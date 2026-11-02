"""page-read: a page as blocks, with thinking switched off and a larger picture; over HTTP and against the mock."""

import json

import httpx
import respx

from ai_gateway.ai.application.answer_models import PageTableBlock, PageTextBlock
from ai_gateway.ai.application.services.read_page import ReadPageService
from ai_gateway.ai.domain.models import Image, ImageRef, ModelRequest, ModelRole
from ai_gateway.ai.infrastructure.breaker import CircuitBreaker
from ai_gateway.ai.infrastructure.model_client import OpenAiModelClient
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles
from support.fakes import FakeImages, FakeModel, caller, runner

PAGE = ImageRef(bucket="idp-derived", key="acct/job/item/model-pages/page-0001.png")
ANSWER = {
    "analysis": {"n_blocks": 2, "table_column_counts": [2]},
    "blocks": [
        {"kind": "heading", "text": "Invoice", "align": "center", "size": "large", "bold": True},
        {
            "kind": "table",
            "has_header": True,
            "n_data_rows": 1,
            "n_cols": 2,
            "header": ["A", "B"],
            "rows": [["1", "2"]],
        },
    ],
}


class RecordingImages(FakeImages):
    def __init__(self) -> None:
        super().__init__()
        self.sides: list[int | None] = []

    async def load(self, refs, *, stack, max_side_px=None):
        self.sides.append(max_side_px)
        return await super().load(refs, stack=stack)


async def test_returns_the_pages_blocks_with_thinking_off_and_the_larger_picture() -> None:
    model, images = FakeModel(json.dumps(ANSWER)), RecordingImages()
    service = ReadPageService(runner=runner(model), images=images, prompts=PromptFiles.load(), max_side_px=2048)

    answer = await service.read(caller(), PAGE)

    heading, table = answer.value.blocks
    assert isinstance(heading, PageTextBlock)
    assert (heading.text, heading.align, heading.bold) == ("Invoice", "center", True)
    assert isinstance(table, PageTableBlock)
    assert (table.header, table.rows) == (["A", "B"], [["1", "2"]])
    assert answer.provenance.prompt_version == "page-read.v1"
    assert model.requests[0].thinking is False
    assert images.sides == [2048]


async def test_repairs_once_when_a_block_kind_is_unknown() -> None:
    bad = json.dumps({"analysis": {"n_blocks": 1}, "blocks": [{"kind": "chart", "text": "x"}]})
    model = FakeModel(bad, json.dumps(ANSWER))
    service = ReadPageService(runner=runner(model), images=FakeImages(), prompts=PromptFiles.load(), max_side_px=2048)

    answer = await service.read(caller(), PAGE)

    assert len(answer.value.blocks) == 2
    assert len(model.requests) == 2


@respx.mock
async def test_sends_the_thinking_switch_only_when_the_prompt_sets_it() -> None:
    route = respx.post("http://model.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    client = OpenAiModelClient(
        httpx.AsyncClient(base_url="http://model.test/v1/"),
        models={ModelRole.VL: "vl"},
        breaker=CircuitBreaker(failures=5, window_s=30, reset_after_s=30),
    )
    base = {
        "role": ModelRole.VL,
        "system": "s",
        "text": "t",
        "images": [Image(data=b"x")],
        "schema": {},
        "schema_name": "n",
        "temperature": 0,
        "max_tokens": 8,
    }

    await client.complete(ModelRequest(**base, thinking=False), timeout_s=5)
    await client.complete(ModelRequest(**base), timeout_s=5)

    first, second = (json.loads(call.request.content) for call in route.calls)
    assert first["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in second
