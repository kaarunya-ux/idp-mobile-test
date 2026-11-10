"""Use case: page-layout. A page's structure over the lines an OCR engine already read (ADR 0006).

conversion-service's OCR-first reading of scanned pages: OCR reads every line with its position in a second or two;
the model only says how the lines are organised (headings, paragraphs, lists, tables), by line number, without copying
any text. Its answer is a fraction of page-read's, where the model writes out every word, and the text stays OCR's
exact characters. On the page model (``ModelRole.PAGE``); thinking off.
"""

from collections.abc import Sequence

from ai_gateway.ai.application.answer_models import LayoutTableBlock, PageLayoutAnswer, reader, schema_of
from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.exceptions import AnswerShapeError
from ai_gateway.ai.domain.models import Answer, Caller, ImageRef, ModelRequest, ModelRole, Task
from ai_gateway.ai.domain.ports import ImageStorePort, PromptsPort
from ai_gateway.ai.domain.prompt_text import data_block

PROMPT = "page-layout"


def line_numbers(answer: PageLayoutAnswer) -> list[int]:
    """Every line number the answer uses, in its order (a table's row by row)."""
    numbers: list[int] = []
    for block in answer.blocks:
        if isinstance(block, LayoutTableBlock):
            numbers += [n for row in block.rows for cell in row for n in cell]
        else:
            numbers += block.lines
    return numbers


class ReadLayoutService:
    def __init__(
        self, *, runner: ModelCallRunner, images: ImageStorePort, prompts: PromptsPort, max_side_px: int
    ) -> None:
        self._runner = runner
        self._images = images
        self._prompts = prompts
        self._max_side_px = max_side_px

    async def read(
        self,
        caller: Caller,
        page: ImageRef | None,
        lines: Sequence[tuple[int, str, tuple[float, float, float, float]]],
    ) -> Answer[PageLayoutAnswer]:
        """``lines`` are (number, text, (x, y, width, height)) from OCR. Without ``page`` the model sees no image and
        lays the page out from where the lines sit: a fraction of the time (the image is most of the input)."""
        prompt = self._prompts.get(PROMPT)
        images = await self._images.load([page], stack=False, max_side_px=self._max_side_px) if page else []
        known = {number for number, _, _ in lines}
        listed = [{"n": n, "text": text, "box": [round(v, 3) for v in box]} for n, text, box in lines]
        text = (
            'Lay out this page. Its OCR lines are in the data block "lines". Answer with a JSON object: "blocks".\n'
            + ("" if page else "There is no image this time: judge from where each line sits and its height.\n")
            + data_block("lines", listed)
        )
        request = ModelRequest(
            role=ModelRole.PAGE,
            system=prompt.system,
            text=text,
            images=images,
            schema=schema_of(PageLayoutAnswer),
            schema_name="page_layout",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
            thinking=prompt.thinking,
        )
        parse = reader(PageLayoutAnswer)

        def read(content: str) -> PageLayoutAnswer:
            answer = parse(content)
            unknown = [n for n in line_numbers(answer) if n not in known]
            if unknown:
                raise AnswerShapeError(f"line numbers that are not on the page: {unknown[:5]}")
            return answer

        return await self._runner.run(caller, Task.PAGE_LAYOUT, prompt, request, read)
