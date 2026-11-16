"""Builders for small test files, made on the fly so the repo holds no binary fixtures (TST-06)."""

from collections.abc import Callable
from pathlib import Path

import img2pdf
import pikepdf
import pytest
from PIL import Image

from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.options import ConversionOptions
from conversion.conversion.domain.ports import StepRequest
from conversion.conversion.domain.route import Converter, Step

EXIF_ORIENTATION = 0x0112
type MakeImage = Callable[..., Path]
type MakePdf = Callable[..., Path]


@pytest.fixture
def make_image(tmp_path: Path) -> MakeImage:
    """``make_image("photo.jpg", size=(40, 20), mode="RGB", orientation=6, dpi=(300, 150))``"""

    def make(
        name: str,
        *,
        size: tuple[int, int] = (40, 20),
        mode: str = "RGB",
        color: tuple[int, ...] | int = 128,
        orientation: int | None = None,
        dpi: tuple[int, int] | None = None,
    ) -> Path:
        path = tmp_path / name
        image = Image.new(mode, size, color)
        save: dict[str, object] = {}
        if orientation is not None:
            exif = Image.Exif()
            exif[EXIF_ORIENTATION] = orientation
            save["exif"] = exif.tobytes()
        if dpi is not None:
            save["dpi"] = dpi
        image.save(path, **save)
        return path

    return make


@pytest.fixture
def make_pdf(tmp_path: Path, make_image: MakeImage) -> MakePdf:
    """A PDF with one page per size, in points at 72 dpi: ``make_pdf("doc.pdf", sizes=[(200, 100)] * 3)``."""

    def make(name: str, *, sizes: list[tuple[int, int]], password: str | None = None) -> Path:
        pages = [make_image(f"{name}-src-{i}.png", size=size, dpi=(72, 72)) for i, size in enumerate(sizes)]
        path = tmp_path / name
        path.write_bytes(img2pdf.convert([str(page) for page in pages]))
        if password is not None:
            with pikepdf.open(path, allow_overwriting_input=True) as pdf:
                pdf.save(path, encryption=pikepdf.Encryption(user=password, owner=password + "-owner"))
        return path

    return make


type MakeRequest = Callable[..., StepRequest]


@pytest.fixture
def make_request(tmp_path: Path) -> MakeRequest:
    """``make_request(Converter.X, Representation.A, Representation.B, [inputs], options=...)``; output in tmp/out."""

    def make(
        converter: Converter,
        source: Representation,
        target: Representation,
        inputs: list[Path],
        options: ConversionOptions | None = None,
    ) -> StepRequest:
        output_dir = tmp_path / "out"
        output_dir.mkdir(exist_ok=True)
        return StepRequest(
            step=Step(converter=converter, source=source, target=target),
            inputs=tuple(inputs),
            output_dir=output_dir,
            options=options or ConversionOptions(),
        )

    return make


type MakeTextPdf = Callable[..., Path]


@pytest.fixture
def make_text_pdf(tmp_path: Path) -> MakeTextPdf:
    """``make_text_pdf("doc.pdf", ["page one text", "page two text"], size=(300, 200), form_field="name")``:
    one page per string, written in Helvetica so the text is real (searchable, replaceable). ``form_field`` adds
    a text field of that name on the first page."""

    def make(name: str, texts: list[str], *, size: tuple[int, int] = (300, 200), form_field: str | None = None) -> Path:
        pdf = pikepdf.new()
        font = pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
        for text in texts:
            page = pdf.add_blank_page(page_size=size)
            page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
            page.Contents = pdf.make_stream(b"BT /F1 12 Tf 20 100 Td (" + text.encode("cp1252") + b") Tj ET")
        if form_field is not None:
            widget = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Annot,
                    Subtype=pikepdf.Name.Widget,
                    FT=pikepdf.Name.Tx,
                    T=pikepdf.String(form_field),
                    Rect=[20, 20, 200, 40],
                    P=pdf.pages[0].obj,
                    DA=pikepdf.String("/Helv 10 Tf 0 g"),
                )
            )
            pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([widget]))
            pdf.Root.AcroForm = pikepdf.Dictionary(
                Fields=pikepdf.Array([widget]),
                DA=pikepdf.String("/Helv 10 Tf 0 g"),
                DR=pikepdf.Dictionary(Font=pikepdf.Dictionary(Helv=font)),
            )
        path = tmp_path / name
        pdf.save(path)
        return path

    return make
