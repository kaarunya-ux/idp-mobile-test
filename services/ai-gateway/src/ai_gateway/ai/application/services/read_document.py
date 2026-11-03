"""Use case: generic-read. Any document, any type, any country: its type and every field it shows, in one model call.

For extraction-service's synchronous path (ADR 0003), when no template matched the document. The pages are its
prepared page images (a card's front and back, or a PDF's first two pages); two are stacked into one picture, so the
model reads them as one document and lists each field once. Pictures are tidied (white margin trimmed, small ones
enlarged) as the benchmarked web extraction app did. Thinking is switched off: the answer is wanted in seconds.
"""

from collections.abc import Sequence

from ai_gateway.ai.application.answer_models import GenericReadAnswer, reader, schema_of
from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.models import Answer, Caller, ImageRef, ModelRequest, ModelRole, Task
from ai_gateway.ai.domain.ports import ImageStorePort, PromptsPort

PROMPT = "generic-read"
QUESTION = (
    "Read this document. Answer with a JSON object: "
    '"document_type", then "document_confidence", then "fields" (every field printed on it).'
)


class ReadDocumentService:
    def __init__(self, *, runner: ModelCallRunner, images: ImageStorePort, prompts: PromptsPort) -> None:
        self._runner = runner
        self._images = images
        self._prompts = prompts

    async def read(self, caller: Caller, pages: Sequence[ImageRef]) -> Answer[GenericReadAnswer]:
        prompt = self._prompts.get(PROMPT)
        images = await self._images.load(pages, stack=True, tidy=True)
        request = ModelRequest(
            role=prompt.model or ModelRole.VL,
            system=prompt.system,
            text=QUESTION,
            images=images,
            schema=schema_of(GenericReadAnswer),
            schema_name="generic_read",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
            thinking=prompt.thinking,
        )
        parse = reader(GenericReadAnswer)
        answer = await self._runner.run(caller, Task.GENERIC_READ, prompt, request, parse)
        return Answer(value=answer.value.tidied(), provenance=answer.provenance)
