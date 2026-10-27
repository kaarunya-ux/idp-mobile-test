"""Saving an edit: image compression presets, metadata removal, and splitting by bookmarks."""

import zlib
from collections.abc import Callable
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from conversion.conversion.domain.edit_plan import CompressPreset, Split, SplitMode
from conversion.conversion.domain.exceptions import InvalidPlanError
from conversion.conversion.infrastructure.pdfedit import output

MakePdf = Callable[..., Path]


def _photo_pdf(tmp_path: Path) -> Path:
    """A PDF holding one noisy 2,000 px photo, stored losslessly (Flate), so JPEG makes it much smaller."""
    photo = Image.effect_noise((2000, 1500), 60).convert("RGB")
    path = tmp_path / "photo.pdf"
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(600, 450))
        image = pdf.make_stream(zlib.compress(photo.tobytes()))
        image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        image.Width, image.Height = photo.size
        image.ColorSpace, image.BitsPerComponent, image.Filter = pikepdf.Name.DeviceRGB, 8, pikepdf.Name.FlateDecode
        name = page.add_resource(image, pikepdf.Name.XObject)
        page.Contents = pdf.make_stream(f"q 600 0 0 450 0 0 cm {name} Do Q".encode())
        pdf.save(path)
    return path


@pytest.mark.parametrize(("preset", "max_side"), [(CompressPreset.BALANCED, 2000), (CompressPreset.STRONG, 1600)])
def test_reencodes_large_images_as_smaller_jpegs_when_compressing(
    tmp_path: Path, preset: CompressPreset, max_side: int
) -> None:
    source = _photo_pdf(tmp_path)
    before = source.stat().st_size

    with pikepdf.open(source) as pdf:
        output.compress_images(pdf, preset)
        output.save(pdf, tmp_path / "out.pdf", protect=None, compress=True)

    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        (image,) = pdf.pages[0].get_images().values()
        filters, width = str(image.Filter), int(image.Width)
    assert filters == "/DCTDecode"
    assert width == max_side
    assert (tmp_path / "out.pdf").stat().st_size < before / 2


def test_leaves_images_alone_when_the_preset_is_lossless(tmp_path: Path) -> None:
    source = _photo_pdf(tmp_path)

    with pikepdf.open(source) as pdf:
        output.compress_images(pdf, CompressPreset.LOSSLESS)
        (image,) = pdf.pages[0].get_images().values()
        assert str(image.Filter) == "/FlateDecode"


def test_removes_a_metadata_entry_when_its_new_value_is_null(make_pdf: MakePdf) -> None:
    with pikepdf.open(make_pdf("a.pdf", sizes=[(10, 10)])) as pdf:
        pdf.docinfo["/Author"] = "Someone"

        output.set_metadata(pdf, {"author": None, "title": "New"})

        assert "/Author" not in pdf.docinfo
        assert str(pdf.docinfo["/Title"]) == "New"


def test_starts_a_part_at_each_top_level_bookmark_when_splitting_by_bookmarks(make_pdf: MakePdf) -> None:
    with pikepdf.open(make_pdf("book.pdf", sizes=[(10, 10)] * 5)) as pdf:
        with pdf.open_outline() as outline:
            outline.root.extend([pikepdf.OutlineItem("Part 2", 2), pikepdf.OutlineItem("Part 3", 4)])

        groups = output.page_groups(pdf, Split(mode=SplitMode.BOOKMARKS))

    assert groups == [[0, 1], [2, 3], [4]]


def test_reports_cnv_422_plan_001_when_splitting_by_bookmarks_a_pdf_without_any(make_pdf: MakePdf) -> None:
    with pikepdf.open(make_pdf("plain.pdf", sizes=[(10, 10)] * 2)) as pdf, pytest.raises(InvalidPlanError):
        output.page_groups(pdf, Split(mode=SplitMode.BOOKMARKS))
