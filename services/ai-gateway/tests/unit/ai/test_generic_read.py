"""generic-read: any document's type and fields, tidied, from tidied pictures; and the image store's tidying."""

import io
import json
from typing import Any

import pytest
from PIL import Image as PilImage

from ai_gateway.ai.application.answer_models import GenericTable
from ai_gateway.ai.application.services.read_document import ReadDocumentService
from ai_gateway.ai.domain.exceptions import OutputInvalidError
from ai_gateway.ai.domain.models import ImageRef, Priority
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles
from ai_gateway.ai.infrastructure.s3_images import S3ImageStore
from support.fakes import FakeImages, FakeModel, FakeSlots, caller, runner

FRONT = ImageRef(bucket="idp-derived", key="acct/quick/1/file-1")
BACK = ImageRef(bucket="idp-derived", key="acct/quick/1/file-2")


def _answer(*fields: dict[str, Any], document_type: str | None = "Aadhaar Card") -> str:
    return json.dumps({"document_type": document_type, "document_confidence": 0.94, "fields": list(fields)})


def _field(key: str, value: Any, *, type_: str = "string", confidence: float | None = 0.9) -> dict[str, Any]:
    return {"key": key, "label": key.title(), "type": type_, "value": value, "confidence": confidence}


def _service(model: FakeModel, images: FakeImages | None = None, slots: FakeSlots | None = None) -> ReadDocumentService:
    return ReadDocumentService(
        runner=runner(model, slots=slots), images=images or FakeImages(), prompts=PromptFiles.load()
    )


async def test_returns_the_type_as_a_code_and_each_key_once_when_both_sides_list_it() -> None:
    model = FakeModel(_answer(_field("name", "TEST PERSON"), _field("dob", "01/01/1990"), _field("name", "OTHER")))

    answer = await _service(model).read(caller(Priority.INTERACTIVE), [FRONT, BACK])

    assert answer.value.document_type == "aadhaar_card"
    assert [(f.key, f.value) for f in answer.value.fields] == [("name", "TEST PERSON"), ("dob", "01/01/1990")]
    assert answer.provenance.prompt_version == "generic-read.v1"


async def test_keeps_devanagari_text_as_printed() -> None:
    model = FakeModel(_answer(_field("name_hindi", "परीक्षण व्यक्ति")))

    answer = await _service(model).read(caller(), [FRONT])

    assert answer.value.fields[0].value == "परीक्षण व्यक्ति"


async def test_gives_no_confidence_when_a_value_is_empty() -> None:
    model = FakeModel(_answer(_field("signature", "  "), _field("photo", None, confidence=0.7)))

    answer = await _service(model).read(caller(), [FRONT])

    assert [(f.value, f.confidence) for f in answer.value.fields] == [(None, None), (None, None)]


async def test_returns_no_type_and_no_type_confidence_when_the_model_cannot_tell() -> None:
    model = FakeModel(_answer(document_type=None))

    answer = await _service(model).read(caller(), [FRONT])

    assert (answer.value.document_type, answer.value.document_confidence, answer.value.fields) == (None, None, [])


async def test_returns_a_table_as_headers_and_rows() -> None:
    table = {"headers": ["Item", "Qty", "Amount"], "rows": [["Paper", "2", "40.00"]]}
    model = FakeModel(_answer(_field("line_items", table, type_="table")))

    answer = await _service(model).read(caller(), [FRONT])

    value = answer.value.fields[0].value
    assert isinstance(value, GenericTable)
    assert (value.headers, value.rows) == (table["headers"], table["rows"])


async def test_fails_after_one_repair_when_a_table_field_has_a_text_value() -> None:
    bad = _answer(_field("line_items", "Paper 2 40.00", type_="table"))
    model = FakeModel(bad, bad)

    with pytest.raises(OutputInvalidError):
        await _service(model).read(caller(), [FRONT])
    assert len(model.requests) == 2


async def test_asks_for_tidied_stacked_pictures_with_thinking_off() -> None:
    model, images, slots = FakeModel(_answer()), FakeImages(), FakeSlots()

    await _service(model, images, slots).read(caller(Priority.INTERACTIVE), [FRONT, BACK])

    assert images.loaded == [([FRONT, BACK], True)]
    assert images.options == [{"tidy": True}]
    assert model.requests[0].thinking is False
    assert slots.taken[0][1] is Priority.INTERACTIVE


# --- the image store: tidying and photo rotation --------------------------------------------------------------


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _S3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"Body": _Body(self.objects[Key])}


def _store(objects: dict[str, bytes]) -> S3ImageStore:
    return S3ImageStore(
        _S3(objects), allowed_buckets=["idp-derived"], max_bytes=10_000_000, max_side_px=1280, min_side_px=768
    )  # type: ignore[arg-type]  # a stand-in S3 client


def _card_on_white(width: int, height: int, margin: int) -> bytes:
    page = PilImage.new("RGB", (width + 2 * margin, height + 2 * margin), "white")
    page.paste(PilImage.new("RGB", (width, height), (90, 90, 90)), (margin, margin))
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    return buffer.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    return PilImage.open(io.BytesIO(data)).size


async def test_trims_the_white_margin_and_enlarges_a_small_card_when_tidying() -> None:
    store = _store({"card": _card_on_white(300, 200, margin=100)})

    images = await store.load([ImageRef(bucket="idp-derived", key="card")], stack=False, tidy=True)

    width, height = _size(images[0].data)
    assert max(width, height) == 768
    assert abs(width / height - (300 + 16) / (200 + 16)) < 0.02  # the card and its 8 px pad, nothing more


async def test_keeps_the_margin_and_size_when_not_tidying() -> None:
    store = _store({"card": _card_on_white(300, 200, margin=100)})

    images = await store.load([ImageRef(bucket="idp-derived", key="card")], stack=False)

    assert _size(images[0].data) == (500, 400)


async def test_turns_a_photo_upright_from_its_exif_orientation() -> None:
    photo = PilImage.new("RGB", (400, 300), "gray")
    exif = photo.getexif()
    exif[0x0112] = 6  # rotated 90 degrees: shown upright as 300 x 400
    buffer = io.BytesIO()
    photo.save(buffer, format="JPEG", exif=exif)

    images = await _store({"photo": buffer.getvalue()}).load([ImageRef(bucket="idp-derived", key="photo")], stack=False)

    assert _size(images[0].data) == (300, 400)
