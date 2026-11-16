"""Use case: template-suggest (IDP-03 ch. 4.2, F-TP-02). Proposes fields and tables for a sample page from the page
image and its OCR words; a person then adjusts the boxes in the template editor."""

from collections.abc import Mapping, Sequence

from ai_gateway.ai.application.answer_models import TemplateSuggestAnswer, reader, schema_of
from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.models import Answer, Caller, ImageRef, ModelRequest, ModelRole, Task
from ai_gateway.ai.domain.ports import ImageStorePort, PromptsPort
from ai_gateway.ai.domain.prompt_text import data_block

PROMPT = "template-suggest"


class SuggestTemplateService:
    def __init__(self, *, runner: ModelCallRunner, images: ImageStorePort, prompts: PromptsPort) -> None:
        self._runner = runner
        self._images = images
        self._prompts = prompts

    async def suggest(
        self, caller: Caller, page: ImageRef, ocr_words: Sequence[Mapping[str, object]]
    ) -> Answer[TemplateSuggestAnswer]:
        prompt = self._prompts.get(PROMPT)
        images = await self._images.load([page], stack=False)
        text = 'Answer with a JSON object: "fields" and "tables".\n' + data_block("ocr_words", list(ocr_words))
        request = ModelRequest(
            role=prompt.model or ModelRole.VL,
            system=prompt.system,
            text=text,
            images=images,
            schema=schema_of(TemplateSuggestAnswer),
            schema_name="template_suggestion",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
        )
        return await self._runner.run(caller, Task.TEMPLATE_SUGGEST, prompt, request, reader(TemplateSuggestAnswer))
