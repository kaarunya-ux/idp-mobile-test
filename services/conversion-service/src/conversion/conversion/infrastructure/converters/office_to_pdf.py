"""Word, Excel and PowerPoint -> PDF with LibreOffice (IDP-03 ch. 3.4: ``office_to_pdf``).

Ported from ``services/conversions/python_scripts/word_to_pdf.py`` (Sumukh, Karunya). Each run gets its own
throwaway LibreOffice profile, so runs can happen in parallel and a damaged profile never carries over. Macros
stay off: a fresh profile uses LibreOffice's default "high" macro security, which runs no document macros.
IDP-03 plans a pool of long-running ``unoserver`` instances; one process per document is simpler and comes first.

Most of a run is LibreOffice starting (about 2.5 s), so with ``keep_warm`` (the synchronous route, ADR 0002) one
instance is started ahead of each document, on its own throwaway profile. The document is handed to it and it exits, as
LibreOffice does, and the next one starts behind the answer. When none is ready yet, or the handoff produces nothing,
the document takes the ordinary run: a spare can only make a conversion faster, never fail it.
"""

import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from multiprocessing import util as multiprocessing_util
from pathlib import Path
from uuid import uuid4

import structlog

from conversion.conversion.domain.exceptions import ConversionTimeoutError, UnreadableDocumentError
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.pdf_to_images import open_pdf

log = structlog.get_logger(__name__)


class LibreOfficeNotInstalledError(RuntimeError):
    """The worker has no LibreOffice: a deployment problem, never a user error."""


class LibreOfficePdfConverter:
    """Writes ``<input>.pdf`` from one Office document (DOCX, XLSX or PPTX)."""

    def __init__(self, *, binary: str, timeout: timedelta, keep_warm: bool = False) -> None:
        self._binary = binary
        self._timeout = timeout
        self._keep_warm = keep_warm
        self._spare: _SpareOffice | None = None
        if keep_warm:
            self._start_spare()
            # a worker process skips atexit; this runs when it ends, and asks for the spare current at that moment
            multiprocessing_util.Finalize(self, _stop_spare_of, args=(self,), exitpriority=10)

    @property
    def converter(self) -> Converter:
        return Converter.OFFICE_TO_PDF

    def convert(self, request: StepRequest) -> StepResult:
        (source,) = request.inputs  # one document in; the planner never sends more
        output = request.output_dir / f"{source.stem}.pdf"
        with tempfile.TemporaryDirectory(prefix="soffice-") as scratch:
            work = Path(scratch)
            produced = work / "out" / f"{source.stem}.pdf"
            if not self._handed_to_spare(source, work / "out", produced):
                self._run_libreoffice(source, profile=work / "profile", out_dir=work / "out")
            if not produced.is_file():  # LibreOffice exits 0 even when it can't open the file
                raise UnreadableDocumentError
            shutil.move(produced, output)
        with open_pdf(output) as document:
            page_count = len(document)
        return StepResult(outputs=(output,), page_count=page_count)

    def _start_spare(self) -> None:
        binary = shutil.which(self._binary)
        self._spare = _SpareOffice.started(binary) if binary is not None else None

    def _handed_to_spare(self, source: Path, out_dir: Path, produced: Path) -> bool:
        """Whether a spare made the PDF. It is replaced after every document it took, whatever came of it."""
        spare = self._spare
        if spare is None or not spare.is_ready():
            return False
        try:
            self._run_libreoffice(source, profile=spare.profile, out_dir=out_dir)
        finally:
            spare.stop()
            self._start_spare()
        if not produced.is_file():
            log.info("libreoffice_spare_made_nothing")
            return False
        return True

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
        process = _popen(arguments)
        try:
            process.wait(timeout=self._timeout.total_seconds())
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)  # the whole group: soffice starts a child that would outlive it
            process.wait()
            raise ConversionTimeoutError(self._timeout.total_seconds()) from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class _SpareOffice:
    """A LibreOffice already started and waiting, on a profile only it uses. Its pipe appears when it is ready."""

    process: subprocess.Popen[bytes]
    folder: Path
    pipe: Path

    @classmethod
    def started(cls, binary: str) -> "_SpareOffice":
        folder = Path(tempfile.mkdtemp(prefix="soffice-spare-"))
        name = uuid4().hex
        process = _popen(
            [
                binary,
                f"-env:UserInstallation={(folder / 'profile').as_uri()}",
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--nologo",
                "--nodefault",
                f"--accept=pipe,name={name};urp;",
            ]
        )
        return cls(process=process, folder=folder, pipe=Path(f"/tmp/OSL_PIPE_{os.getuid()}_{name}"))  # noqa: S108 - where LibreOffice puts it

    @property
    def profile(self) -> Path:
        return self.folder / "profile"

    def is_ready(self) -> bool:
        return self.process.poll() is None and self.pipe.exists()

    def stop(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)  # the whole group: soffice starts a child that would outlive it
        self.process.wait()
        shutil.rmtree(self.folder, ignore_errors=True)
        self.pipe.unlink(missing_ok=True)


def _popen(arguments: list[str]) -> subprocess.Popen[bytes]:
    # S603: a fixed argument list and no shell (SEC-08); the only variable parts are our own temp paths.
    # fixed LibreOffice command list, no shell, no user text (S603)
    return subprocess.Popen(  # noqa: S603  # see the line above
        arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


def _stop_spare_of(converter: LibreOfficePdfConverter) -> None:
    if converter._spare is not None:
        converter._spare.stop()
