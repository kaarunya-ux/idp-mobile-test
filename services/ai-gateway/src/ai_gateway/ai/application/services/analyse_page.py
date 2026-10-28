"""Use case: page-analysis (IDP-03 ch. 4.2). Deep analysis: the whole document and its current result go to the
model, which reports missed or wrong fields. With a free question the ``auto`` model answers (IDP-03 ch. 4.3)."""

from collections.abc import Mapping, Sequence

from ai_gateway.ai.application.answer_models import PageAnalysisAnswer, reader, schema_of
from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.models import Answer, Caller, ImageRef, ModelRequest, ModelRole, Task
from ai_gateway.ai.domain.ports import ImageStorePort, PromptsPort
from ai_gateway.ai.domain.prompt_text import data_block

PROMPT = "page-analysis"


class AnalysePageService:
    def __init__(self, *, runner: ModelCallRunner, images: ImageStorePort, prompts: PromptsPort) -> None:
        self._runner = runner
        self._images = images
        self._prompts = prompts

    async def analyse(
        self, caller: Caller, pages: Sequence[ImageRef], current: Mapping[str, object], question: str | None
    ) -> Answer[PageAnalysisAnswer]:
        prompt = self._prompts.get(PROMPT)
        images = await self._images.load(pages, stack=False)
        text = (
            'Answer with a JSON object: "fields", "summary" and "warnings".\n'
            + data_block("current_result", current)
            + (f"\n{data_block('question', question)}" if question else "")
        )
        request = ModelRequest(
            role=ModelRole.AUTO if question else (prompt.model or ModelRole.VL),
            system=prompt.system,
            text=text,
            images=images,
            schema=schema_of(PageAnalysisAnswer),
            schema_name="page_analysis",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
        )
        return await self._runner.run(caller, Task.PAGE_ANALYSIS, prompt, request, reader(PageAnalysisAnswer))
