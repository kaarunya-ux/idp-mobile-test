"""The last steps of an edit: metadata, image compression, splitting, and saving (with a password if asked)."""

import io
from collections.abc import Mapping
from pathlib import Path

import pikepdf
from PIL import Image

from conversion.conversion.domain.edit_plan import CompressPreset, Protect, Split, SplitMode
from conversion.conversion.domain.exceptions import InvalidPlanError

_DOCINFO = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords"}
_JPEG_QUALITY = {CompressPreset.BALANCED: 75, CompressPreset.STRONG: 50}
_STRONG_MAX_SIDE_PX, _SMALL_IMAGE_PX = 1600, 300


def set_metadata(pdf: pikepdf.Pdf, changes: Mapping[str, str | None]) -> None:
    for field, value in changes.items():
        key = _DOCINFO[field]
        if value is None:
            if key in pdf.docinfo:
                del pdf.docinfo[key]
        else:
            pdf.docinfo[key] = value


def compress_images(pdf: pikepdf.Pdf, preset: CompressPreset) -> None:
    """Re-encode large RGB and grey images as JPEG when that makes them smaller; others are left alone."""
    quality = _JPEG_QUALITY.get(preset)
    if quality is None:
        return
    done: set[tuple[int, int]] = set()
    for page in pdf.pages:
        for image in page.get_images().values():
            if not isinstance(image, pikepdf.Stream) or image.objgen in done:
                continue
            done.add(image.objgen)
            _recompress(image, quality, shrink=preset is CompressPreset.STRONG)


def _recompress(image: pikepdf.Stream, quality: int, *, shrink: bool) -> None:
    if "/SMask" in image or image.get("/BitsPerComponent") != 8:  # noqa: PLR2004 - 8-bit images only
        return
    if image.get("/ColorSpace") not in (pikepdf.Name.DeviceRGB, pikepdf.Name.DeviceGray):
        return
    try:
        picture = pikepdf.PdfImage(image).as_pil_image()
    except (pikepdf.PdfError, NotImplementedError, ValueError):
        return  # an encoding Pillow can't read: leave it as it is
    if max(picture.size) < _SMALL_IMAGE_PX:
        return
    if shrink:
        picture.thumbnail((_STRONG_MAX_SIDE_PX, _STRONG_MAX_SIDE_PX), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    picture.convert("RGB" if picture.mode != "L" else "L").save(buffer, format="JPEG", quality=quality)
    if len(buffer.getvalue()) >= len(image.read_raw_bytes()):
        return
    image.write(buffer.getvalue(), filter=pikepdf.Name.DCTDecode)
    image.Width, image.Height = picture.size
    if "/DecodeParms" in image:
        del image.DecodeParms


def page_groups(pdf: pikepdf.Pdf, split: Split | None) -> list[list[int]]:
    """The page indexes of each output file."""
    count = len(pdf.pages)
    if split is None:
        return [list(range(count))]
    match split.mode:
        case SplitMode.RANGES:
            return [split_range.select(count) for split_range in split.ranges]
        case SplitMode.EVERY:
            return [list(range(start, min(start + split.every, count))) for start in range(0, count, split.every)]
        case SplitMode.BOOKMARKS:
            return _by_bookmarks(pdf, count)


def _by_bookmarks(pdf: pikepdf.Pdf, count: int) -> list[list[int]]:
    with pdf.open_outline() as outline:
        pages = [page for item in outline.root if (page := _page_of(pdf, item)) is not None]
    if not pages:
        raise InvalidPlanError("The PDF has no bookmarks to split by.")
    starts = sorted({0, *pages})
    ends = [*starts[1:], count]
    return [list(range(start, end)) for start, end in zip(starts, ends, strict=True) if end > start]


def _page_of(pdf: pikepdf.Pdf, item: pikepdf.OutlineItem) -> int | None:
    destination = item.destination
    if isinstance(destination, pikepdf.Array) and len(destination) > 0:
        try:
            return pdf.pages.index(pikepdf.Page(destination[0]))
        except (ValueError, pikepdf.PdfError):
            return None
    return None


def save(pdf: pikepdf.Pdf, path: Path, *, protect: Protect | None, compress: bool) -> None:
    encryption: pikepdf.Encryption | bool = False
    if protect is not None:
        encryption = pikepdf.Encryption(
            user=protect.user_password,
            owner=protect.owner_password,
            R=6,  # AES-256
            allow=pikepdf.Permissions(
                extract=protect.allow_copy, print_lowres=protect.allow_print, print_highres=protect.allow_print
            ),
        )
    pdf.save(
        path,
        encryption=encryption,
        compress_streams=True,
        recompress_flate=compress,
        object_stream_mode=pikepdf.ObjectStreamMode.generate if compress else pikepdf.ObjectStreamMode.preserve,
    )
