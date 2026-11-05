"""Use case: page-read. A whole page picture as blocks (headings, paragraphs, lists, tables), for conversion-service's
scanned routes (scan or photo to Word, Excel or CSV).

The reading is Sumukh's model-first approach from ``services/conversions``: the model first writes an analysis (how
many blocks, how wide each table is), then the blocks, so it commits to the structure before transcribing. Thinking is
switched off on every call: a server restarted with it on spent the whole output budget reasoning. The page is sent at
``MODEL__PAGE_READ_MAX_SIDE_PX``, larger than a card needs, because small print must stay readable.
"""

from ai_gateway.ai.application.answer_models import PageReadAnswer, reader, schema_of
from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.models import Answer, Caller, ImageRef, ModelRequest, ModelRole, Task
from ai_gateway.ai.domain.ports import ImageStorePort, PromptsPort

PROMPT = "page-read"
QUESTION = (
    "Count the paragraphs, then extract every block on this page, from the first line to the last line. "
    'Answer with a JSON object: "analysis" first, then "blocks".'
)


class ReadPageService:
    def __init__(
        self, *, runner: ModelCallRunner, images: ImageStorePort, prompts: PromptsPort, max_side_px: int
    ) -> None:
        self._runner = runner
        self._images = images
        self._prompts = prompts
        self._max_side_px = max_side_px

    async def read(self, caller: Caller, page: ImageRef) -> Answer[PageReadAnswer]:
        prompt = self._prompts.get(PROMPT)
        images = await self._images.load([page], stack=False, max_side_px=self._max_side_px)
        request = ModelRequest(
            role=ModelRole.PAGE,  # its own model when one is set up (MODEL__PAGE__BASE_URL)
            system=prompt.system,
            text=QUESTION,
            images=images,
            schema=schema_of(PageReadAnswer),
            schema_name="page_read",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
            thinking=prompt.thinking,
        )
        return await self._runner.run(caller, Task.PAGE_READ, prompt, request, reader(PageReadAnswer))
