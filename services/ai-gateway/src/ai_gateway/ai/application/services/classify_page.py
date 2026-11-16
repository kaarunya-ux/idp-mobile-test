"""Use case: classify (IDP-03 ch. 4.2). Which of the caller's candidate document types a page shows."""

from collections.abc import Sequence

from ai_gateway.ai.application.answer_models import ClassifyAnswer, reader
from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.exceptions import AnswerShapeError
from ai_gateway.ai.domain.models import Answer, Caller, ImageRef, ModelRequest, ModelRole, Task
from ai_gateway.ai.domain.ports import ImageStorePort, PromptsPort
from ai_gateway.ai.domain.prompt_text import data_block

PROMPT = "classify"


class ClassifyPageService:
    def __init__(self, *, runner: ModelCallRunner, images: ImageStorePort, prompts: PromptsPort) -> None:
        self._runner = runner
        self._images = images
        self._prompts = prompts

    async def classify(self, caller: Caller, page: ImageRef, candidates: Sequence[str]) -> Answer[ClassifyAnswer]:
        prompt = self._prompts.get(PROMPT)
        images = await self._images.load([page], stack=False)
        schema = {
            "type": "object",
            "properties": {
                "documentType": {"enum": [*candidates, None]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["documentType", "confidence"],
            "additionalProperties": False,
        }
        text = (
            'Which of the types in the data block "candidates" does this image show? Answer with a JSON object: '
            '"documentType" (one of the candidates, or null) and "confidence".\n' + data_block("candidates", candidates)
        )
        request = ModelRequest(
            role=prompt.model or ModelRole.VL,
            system=prompt.system,
            text=text,
            images=images,
            schema=schema,
            schema_name="classification",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
        )
        parse = reader(ClassifyAnswer)

        def read(content: str) -> ClassifyAnswer:
            answer = parse(content)
            if answer.document_type is not None and answer.document_type not in candidates:
                raise AnswerShapeError("documentType is not one of the candidates")
            return answer

        return await self._runner.run(caller, Task.CLASSIFY, prompt, request, read)
