"""What the synchronous extract accepts (ADR 0009): one PDF alone, or a JPEG or PNG front and optionally its back.

The type comes from the content, never from a file name or a declared type (FIL-03). Sizes are checked before
anything is stored (FIL-05). Whether the content really opens is ai-gateway's check: it decodes the pictures.
"""

from collections.abc import Sequence

from extraction.quick.domain.exceptions import FileTooLargeError, FileTypeUnsupportedError, FileUnreadableError
from extraction.quick.domain.models import FileKind, UploadLimits

_SIGNATURES = (
    (b"%PDF-", FileKind.PDF),
    (b"\xff\xd8\xff", FileKind.JPEG),
    (b"\x89PNG\r\n\x1a\n", FileKind.PNG),
)
_MIB = 1024 * 1024


def kind_of(content: bytes) -> FileKind | None:
    return next((kind for signature, kind in _SIGNATURES if content.startswith(signature)), None)


def check_uploads(front: bytes, back: bytes | None, limits: UploadLimits) -> Sequence[FileKind]:
    """The kind of each file, front first. Raises when a file is empty, too large or of a kind not accepted."""
    files = [("file", front)] + ([("back", back)] if back is not None else [])
    kinds: list[FileKind] = []
    for name, content in files:
        if not content:
            raise FileUnreadableError(f"The {name} is empty.")
        if len(content) > limits.max_file_bytes:
            raise FileTooLargeError(f"The {name} is larger than {limits.max_file_bytes // _MIB} MB.")
        kind = kind_of(content)
        if kind is None:
            raise FileTypeUnsupportedError(f"The {name} is not a PDF, JPG or PNG file.")
        kinds.append(kind)
    if sum(len(content) for _, content in files) > limits.max_total_bytes:
        raise FileTooLargeError(f"The files together are larger than {limits.max_total_bytes // _MIB} MB.")
    if len(kinds) > 1 and FileKind.PDF in kinds:
        raise FileTypeUnsupportedError("A PDF is sent alone; a back side goes with a JPG or PNG front.")
    return kinds
