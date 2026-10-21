"""Word, Excel and PowerPoint -> PDF with LibreOffice (IDP-03 ch. 3.4: ``office_to_pdf``).

Ported from ``services/conversions/python_scripts/word_to_pdf.py`` (Sumukh, Karunya). Each run gets its own
throwaway LibreOffice profile, so runs can happen in parallel and a damaged profile never carries over. Macros
stay off: a fresh profile uses LibreOffice's default "high" macro security, which runs no document macros.
IDP-03 plans a pool of long-running ``unoserver`` instances; one process per document is simpler and comes first.
"""

import os
import shutil
import signal
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from conversion.conversion.domain.exceptions import ConversionTimeoutError, UnreadableDocumentError
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.pdf_to_images import open_pdf


class LibreOfficeNotInstalledError(RuntimeError):
    """The worker has no LibreOffice: a deployment problem, never a user error."""


class LibreOfficePdfConverter:
    """Writes ``<input>.pdf`` from one Office document (DOCX, XLSX or PPTX)."""

    def __init__(self, *, binary: str, timeout: timedelta) -> None:
        self._binary = binary
        self._timeout = timeout

    @property
    def converter(self) -> Converter:
        return Converter.OFFICE_TO_PDF

    def convert(self, request: StepRequest) -> StepResult:
        (source,) = request.inputs  # one document in; the planner never sends more
        output = request.output_dir / f"{source.stem}.pdf"
        with tempfile.TemporaryDirectory(prefix="soffice-") as scratch:
            work = Path(scratch)
            self._run_libreoffice(source, profile=work / "profile", out_dir=work / "out")
            produced = work / "out" / f"{source.stem}.pdf"
            if not produced.is_file():  # LibreOffice exits 0 even when it can't open the file
                raise UnreadableDocumentError
            shutil.move(produced, output)
        with open_pdf(output) as document:
            page_count = len(document)
        return StepResult(outputs=(output,), page_count=page_count)

    def _run_libreoffice(self, source: Path, *, profile: Path, out_dir: Path) -> None:
        binary = shutil.which(self._binary)
        if binary is None:
            raise LibreOfficeNotInstalledError(f"'{self._binary}' was not found on this worker")
        arguments = [
            binary,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source),
        ]
        # S603: a fixed argument list and no shell (SEC-08); the only variable parts are our own temp paths.
        process = subprocess.Popen(  # noqa: S603
            arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        try:
            process.wait(timeout=self._timeout.total_seconds())
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)  # the whole group: soffice starts a child that would outlive it
            process.wait()
            raise ConversionTimeoutError(self._timeout.total_seconds()) from exc
