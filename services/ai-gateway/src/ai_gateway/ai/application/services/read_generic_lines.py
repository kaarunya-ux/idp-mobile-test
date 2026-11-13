"""Use case: generic-lines. generic-read over the lines an OCR engine already read (extraction's ADR 0015).

The model gets the numbered OCR lines, no picture, and answers which lines hold which field: key, label, type and line
numbers, never the text. It writes a fraction of generic-read's answer (the time is in the writing), and the values
stay OCR's exact characters, with OCR's boxes. Text only, so on the text model; thinking off.
"""

from collections.abc import Sequence

from ai_gateway.ai.application.answer_models import GenericLinesAnswer, reader, schema_of
from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.exceptions import AnswerShapeError
from ai_gateway.ai.domain.models import Answer, Caller, ModelRequest, ModelRole, Task
from ai_gateway.ai.domain.ports import PromptsPort
from ai_gateway.ai.domain.prompt_text import data_block

PROMPT = "generic-lines"


class ReadGenericLinesService:
    def __init__(self, *, runner: ModelCallRunner, prompts: PromptsPort) -> None:
        self._runner = runner
        self._prompts = prompts

    async def read(self, caller: Caller, lines: Sequence[tuple[int, str]]) -> Answer[GenericLinesAnswer]:
        """``lines`` are (number, text) from OCR, in reading order."""
        prompt = self._prompts.get(PROMPT)
        known = {number for number, _ in lines}
        text = (
            'Find this document\'s fields. Its OCR lines are in the data block "lines". Answer with a JSON object: '
            '"document_type", then "document_confidence", then "fields".\n'
            + data_block("lines", [{"n": n, "text": line} for n, line in lines])
        )
        request = ModelRequest(
            role=prompt.model or ModelRole.SMART,
            system=prompt.system,
            text=text,
            images=[],
            schema=schema_of(GenericLinesAnswer),
            schema_name="generic_lines",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
            thinking=prompt.thinking,
        )
        parse = reader(GenericLinesAnswer)

        def read(content: str) -> GenericLinesAnswer:
            answer = parse(content)
            unknown = [n for found in answer.fields for n in found.numbers() if n not in known]
            if unknown:
                raise AnswerShapeError(f"line numbers that are not on the document: {unknown[:5]}")
            return answer

        answer = await self._runner.run(caller, Task.GENERIC_LINES, prompt, request, read)
        return Answer(value=answer.value.tidied(), provenance=answer.provenance)
