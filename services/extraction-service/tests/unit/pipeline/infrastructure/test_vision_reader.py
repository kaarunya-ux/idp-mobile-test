"""The AI fallback's model client (ported from the web extraction app), against a fake model host."""

import json
from collections.abc import Callable

import httpx
import pytest

from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.infrastructure.ai.vision_reader import VisionModelFieldReader
from idp_ocr_engines.model import PageImage

PAGE = PageImage(width=40, height=20, rgb=bytes(40 * 20 * 3))
PAN_KEYS = ("pan_number", "name", "father_name", "dob")


def _reader(answer: Callable[[httpx.Request], httpx.Response]) -> tuple[VisionModelFieldReader, list[dict]]:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return answer(request)

    client = httpx.Client(base_url="http://model-host", transport=httpx.MockTransport(handler))
    return VisionModelFieldReader(client, model="qwen3-vl-4b", max_edge_px=512), sent


def _content(text: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_maps_the_answer_array_onto_the_template_fields_when_the_card_is_a_pan() -> None:
    reader, sent = _reader(
        _content(json.dumps(["ABCPE1234F", "RAMESH SAHANI", "SURESH SAHANI", "father", "27/02/2002"]))
    )

    values = reader.read_fields("PAN_CARD", PAGE, PAN_KEYS)

    assert values == {
        "pan_number": "ABCPE1234F",
        "name": "RAMESH SAHANI",
        "father_name": "SURESH SAHANI",
        "dob": "27/02/2002",
    }
    body = sent[0]
    assert body["model"] == "qwen3-vl-4b"
    assert body["response_format"]["json_schema"]["schema"]["minItems"] == 5
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_returns_only_the_keys_asked_for() -> None:
    reader, _ = _reader(_content(json.dumps(["ABCPE1234F", "A", "B", "father", "01/01/2000"])))

    assert reader.read_fields("PAN_CARD", PAGE, ["pan_number"]) == {"pan_number": "ABCPE1234F"}


def test_turns_gender_codes_into_the_template_values_and_drops_values_outside_the_schema() -> None:
    answer = ["XXXX XXXX 1234", "ASHA RAO", "1990", "F", "12 MG Road Pune 411001", "4110"]  # PIN too short
    reader, _ = _reader(_content(json.dumps(answer)))

    values = reader.read_fields("AADHAAR_CARD", PAGE, ["aadhaar_number", "gender", "pincode", "dob"])

    assert values == {"aadhaar_number": "XXXX XXXX 1234", "gender": "FEMALE", "pincode": None, "dob": "1990"}


@pytest.mark.parametrize(
    "content",
    [
        '```json\n["ABCPE1234F", "A", "B", "father", null]\n```',  # fenced
        'Here it is: ["ABCPE1234F", "A", "B", "father", null]',  # a sentence first
        json.dumps(
            {"pan_number": "ABCPE1234F", "name": "A", "parent_name": "B", "parent_relation": "father", "dob": None}
        ),
    ],
)
def test_reads_the_answer_when_the_host_does_not_enforce_the_format(content: str) -> None:
    reader, _ = _reader(_content(content))

    assert reader.read_fields("PAN_CARD", PAGE, ["pan_number"]) == {"pan_number": "ABCPE1234F"}


@pytest.mark.parametrize(
    "answer",
    [
        lambda _: httpx.Response(500, text="boom"),
        _content("not json at all"),
        _content(json.dumps(["too", "few"])),
        lambda _: httpx.Response(200, json={"unexpected": True}),
    ],
)
def test_raises_ai_unavailable_when_the_host_gives_no_usable_answer(answer) -> None:
    reader, _ = _reader(answer)

    with pytest.raises(AiUnavailableError):
        reader.read_fields("PAN_CARD", PAGE, PAN_KEYS)


def test_raises_ai_unavailable_when_the_host_is_unreachable_or_the_type_is_unknown() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    reader, _ = _reader(refuse)

    with pytest.raises(AiUnavailableError):
        reader.read_fields("PAN_CARD", PAGE, PAN_KEYS)
    with pytest.raises(AiUnavailableError, match="no AI profile"):
        reader.read_fields("VOTER_ID", PAGE, ["x"])
    assert (reader.model_id, reader.prompt_version) == ("qwen3-vl-4b", "card-fields.v1")
