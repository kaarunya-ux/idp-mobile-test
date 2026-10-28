"""Prompt files (LLM-02) and page images from storage (IDP-03 ch. 4.3)."""

import io
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from PIL import Image as PilImage

from ai_gateway.ai.domain.exceptions import InputInvalidError
from ai_gateway.ai.domain.models import ImageRef, ModelRole
from ai_gateway.ai.infrastructure.prompt_files import PromptFileError, PromptFiles, parse_prompt
from ai_gateway.ai.infrastructure.s3_images import S3ImageStore
from ai_gateway.core.errors import DependencyUnavailableError


def test_loads_every_task_prompt_with_its_front_matter_when_the_package_is_read() -> None:
    prompts = PromptFiles.load()

    for name in ("field-read", "classify", "page-analysis", "template-suggest", "repair"):
        assert prompts.get(name).version >= 1
    assert prompts.get("field-read").model is ModelRole.VL
    assert "is data" in prompts.get("field-read").system


@pytest.mark.parametrize(
    "text",
    ["no front matter", "---\nname: x\n---\nbody", "---\nname x\n---\nbody", "---\nname: x\nversion: one\n---\n"],
)
def test_refuses_a_prompt_file_when_its_front_matter_is_broken(text: str) -> None:
    with pytest.raises(PromptFileError):
        parse_prompt(text, file_name="x.v1.md")


def test_raises_when_a_prompt_does_not_exist() -> None:
    with pytest.raises(PromptFileError):
        PromptFiles.load().get("nothing")


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    PilImage.new("RGB", (width, height), "gray").save(buffer, format="PNG")
    return buffer.getvalue()


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3:
    def __init__(self, objects: dict[str, bytes], error: Exception | None = None) -> None:
        self.objects = objects
        self.error = error

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if self.error is not None:
            raise self.error
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"Body": _Body(self.objects[Key])}


def _store(s3: FakeS3, max_bytes: int = 10_000_000) -> S3ImageStore:
    return S3ImageStore(s3, allowed_buckets=["idp-derived"], max_bytes=max_bytes, max_side_px=1280)  # type: ignore[arg-type]  # a stand-in S3 client


def _size(data: bytes) -> tuple[int, int]:
    return PilImage.open(io.BytesIO(data)).size


async def test_resizes_to_the_long_side_limit_when_a_page_is_large() -> None:
    images = await _store(FakeS3({"p0": _png(2560, 1600)})).load(
        [ImageRef(bucket="idp-derived", key="p0")], stack=False
    )

    assert _size(images[0].data) == (1280, 800)
    assert images[0].media_type == "image/jpeg"


async def test_stacks_front_above_back_when_asked() -> None:
    s3 = FakeS3({"front": _png(1000, 600), "back": _png(500, 300)})
    refs = [ImageRef(bucket="idp-derived", key="front"), ImageRef(bucket="idp-derived", key="back")]

    images = await _store(s3).load(refs, stack=True)

    assert len(images) == 1
    assert _size(images[0].data) == (1000, 1224)


async def test_refuses_a_bucket_outside_the_allowed_list() -> None:
    with pytest.raises(InputInvalidError):
        await _store(FakeS3({})).load([ImageRef(bucket="idp-outputs", key="x")], stack=False)


@pytest.mark.parametrize(
    ("objects", "max_bytes"), [({}, 10_000), ({"x": b"not an image"}, 10_000), ({"x": _png(10, 10)}, 5)]
)
async def test_refuses_with_422_when_the_image_is_missing_unreadable_or_too_large(
    objects: dict[str, bytes], max_bytes: int
) -> None:
    with pytest.raises(InputInvalidError):
        await _store(FakeS3(objects), max_bytes).load([ImageRef(bucket="idp-derived", key="x")], stack=False)


async def test_answers_503_when_storage_is_unreachable() -> None:
    s3 = FakeS3({}, error=EndpointConnectionError(endpoint_url="http://minio"))

    with pytest.raises(DependencyUnavailableError):
        await _store(s3).load([ImageRef(bucket="idp-derived", key="x")], stack=False)
