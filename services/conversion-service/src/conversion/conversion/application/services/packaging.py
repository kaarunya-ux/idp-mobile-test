"""How a conversion's files leave: as they are, one file only, or one ZIP of all of them (the ``output`` option)."""

import zipfile
from pathlib import Path

from conversion.conversion.domain.exceptions import InvalidOptionError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.values import OutputMode


def package_outputs(files: tuple[Path, ...], output: OutputMode, folder: Path) -> tuple[Path, ...]:
    if output is OutputMode.ZIP and len(files) > 1:
        folder.mkdir()
        archive = folder / f"{files[0].stem.split('-page-')[0]}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for file in files:
                bundle.write(file, arcname=file.name)
        return (archive,)
    if output is OutputMode.SINGLE and len(files) > 1:
        raise InvalidOptionError("output", f"is SINGLE, but this conversion makes {len(files)} files; use ZIP")
    return files


def format_of_file(file: Path) -> Format | None:
    suffix = file.suffix.lower()
    return next((member for member in Format if member.suffix == suffix), None)
