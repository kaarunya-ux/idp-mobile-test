"""The worker processes behind the synchronous route: real processes, a real conversion, and a process that dies."""

import os
import signal
from collections.abc import AsyncIterator
from pathlib import Path

import pikepdf
import pytest

from conversion.conversion.application.services.convert_now import ConvertNowCommand, MergeNowCommand, UploadedFile
from conversion.conversion.domain.exceptions import NeedsReadingError, UnexpectedFailureError
from conversion.sync_worker import ProcessConversions, _convert


def _kill_this_process() -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def _blank_pdf(folder: Path, name: str = "one") -> tuple[UploadedFile, ...]:
    own = folder / name
    own.mkdir()
    upload = own / "upload"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.save(upload)
    return (UploadedFile(path=upload, name=name),)


@pytest.fixture
async def pool() -> AsyncIterator[ProcessConversions]:
    conversions = ProcessConversions(1)
    yield conversions
    conversions.close()


async def test_converts_in_a_worker_process_and_brings_a_business_error_back(
    pool: ProcessConversions, tmp_path: Path
) -> None:
    uploads = _blank_pdf(tmp_path)

    result = await pool.convert(ConvertNowCommand(uploads=uploads, folder=tmp_path, target="PNG", options={}))

    assert result.file.exists()
    assert result.content_type == "image/png"

    again = _blank_pdf(tmp_path, "two")  # a page with no text: to Word it would need reading
    with pytest.raises(NeedsReadingError):
        await pool.convert(ConvertNowCommand(uploads=again, folder=tmp_path, target="DOCX", options={}))

    merged = await pool.merge(MergeNowCommand(uploads=_blank_pdf(tmp_path, "three"), folder=tmp_path))
    assert merged.page_count == 1


async def test_replaces_the_pool_and_keeps_serving_when_a_worker_process_dies(
    pool: ProcessConversions, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads = _blank_pdf(tmp_path)
    monkeypatch.setattr("conversion.sync_worker._convert", _crashes)

    with pytest.raises(UnexpectedFailureError):
        await pool.convert(ConvertNowCommand(uploads=uploads, folder=tmp_path, target="PNG", options={}))

    monkeypatch.setattr("conversion.sync_worker._convert", _convert)
    result = await pool.convert(ConvertNowCommand(uploads=uploads, folder=tmp_path, target="PNG", options={}))
    assert result.file.exists()


def _crashes(_: ConvertNowCommand, __: str | None) -> None:
    _kill_this_process()


def test_turns_an_unexpected_failure_into_the_retryable_business_error(tmp_path: Path) -> None:
    missing = (UploadedFile(path=tmp_path / "missing", name="missing"),)
    command = ConvertNowCommand(uploads=missing, folder=tmp_path, target="PNG", options={})

    with pytest.raises(UnexpectedFailureError):
        _convert(command, "made-up-id")
