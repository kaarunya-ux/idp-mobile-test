"""``OfficeConverterPort`` with LibreOffice: Word, Excel, PowerPoint and OpenDocument files to PDF.

The same way conversion-service converts them (its ``office_to_pdf``): one LibreOffice process per document with a
throwaway profile, so runs can be parallel and a damaged profile never carries over; macros stay off (a fresh
profile has LibreOffice's "high" macro security). The real type comes from the content (FIL-03), never a name.
"""

import os
import shutil
import signal
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

import structlog
from filetype import guess_extension

from extraction.pipeline.domain.exceptions import FileRejectedError

log = structlog.get_logger(__name__)

OFFICE_TYPES = frozenset({"doc", "docx", "odt", "xls", "xlsx", "ods", "ppt", "pptx", "odp"})


class LibreOfficeNotInstalledError(RuntimeError):
    """The worker has no LibreOffice: a deployment problem, never a user error."""


class LibreOfficePdfConverter:
    def __init__(self, *, binary: str, timeout: timedelta) -> None:
        self._binary = binary
        self._timeout = timeout

    def to_pdf(self, content: bytes) -> bytes | None:
        kind = guess_extension(content)
        if kind not in OFFICE_TYPES:
            return None
        with tempfile.TemporaryDirectory(prefix="soffice-") as scratch:
            work = Path(scratch)
            source = work / f"document.{kind}"
            source.write_bytes(content)
            self._run(source, profile=work / "profile", out_dir=work / "out")
            produced = work / "out" / "document.pdf"
            if not produced.is_file():  # LibreOffice exits 0 even when it can't open the file
                raise FileRejectedError("The document could not be opened; it may be damaged or protected.")
            log.info("office_document_converted", kind=kind, size=len(content))
            return produced.read_bytes()

    def _run(self, source: Path, *, profile: Path, out_dir: Path) -> None:
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
            raise FileRejectedError(
                f"The document took longer than {self._timeout.total_seconds():.0f} seconds to open."
            ) from exc
