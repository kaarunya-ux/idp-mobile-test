"""Use case: convert local files by planning a route and running its steps (IDP-03 ch. 3.3, ConvertDocumentService).

This runs the steps one after another in one process. In step 4 the message consumer reuses it per job item;
splitting steps across worker pools comes with Celery.
"""

import tempfile
from pathlib import Path

from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.application.results import ConvertFileResult
from conversion.conversion.domain.ports import ConverterLookupPort, StepRequest
from conversion.conversion.domain.route_planner import RoutePlanner, RouteRequest


class ConvertFileService:
    """Plans the route, then feeds each step's output to the next in a private working folder.

    Synchronous on purpose: conversions are CPU-bound and run in Celery workers, which are sync end to end
    (standard ch. 15.1). Intermediate files live in a temporary folder that is always removed (FIL-06); only the
    last step writes into ``command.output_dir``.
    """

    def __init__(self, planner: RoutePlanner, converters: ConverterLookupPort) -> None:
        self._planner = planner
        self._converters = converters

    def convert(self, command: ConvertFileCommand) -> ConvertFileResult:
        route = self._planner.get_route(
            RouteRequest(
                source=command.source, target=command.target, pdf_is_scanned=command.pdf_is_scanned, ocr=command.ocr
            )
        )
        files, page_count = command.inputs, 0
        with tempfile.TemporaryDirectory(prefix="conversion-") as scratch:
            for position, step in enumerate(route.steps, start=1):
                is_last = position == len(route.steps)
                step_dir = command.output_dir if is_last else Path(scratch) / f"step-{position}"
                step_dir.mkdir(parents=True, exist_ok=True)
                result = self._converters.get(step.converter).convert(
                    StepRequest(step=step, inputs=files, output_dir=step_dir, options=command.options)
                )
                files = result.outputs
                page_count = page_count or result.page_count  # pages of the original input
        return ConvertFileResult(route=route, outputs=files, page_count=page_count)
