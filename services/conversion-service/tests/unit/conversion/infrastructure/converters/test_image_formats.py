"""PNG <-> JPEG: rotation applied, DPI and transparency handled, bad input reported with a code."""

from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, ImageCms

from conversion.conversion.domain.exceptions import LimitExceededError, UnreadableImageError
from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.options import ConversionOptions
from conversion.conversion.domain.ports import StepRequest
from conversion.conversion.domain.route import Converter
from conversion.conversion.infrastructure.converters.image_formats import PillowImageFormatConverter

R = Representation
MakeImage = Callable[..., Path]
MakeRequest = Callable[..., StepRequest]
converter = PillowImageFormatConverter(max_pixels=10_000_000)


def _to(target: R, source: R, inputs: list[Path], make_request: MakeRequest, **options: int) -> list[Path]:
    request = make_request(Converter.IMAGE_FORMATS, source, target, inputs, ConversionOptions(**options))
    return list(converter.convert(request).outputs)


def test_writes_one_png_per_jpeg_with_identical_pixels_when_converting(
    make_image: MakeImage, make_request: MakeRequest
) -> None:
    photo = make_image("photo.jpg", size=(64, 32), color=(10, 120, 200))

    (output,) = _to(R.PNG, R.JPEG, [photo], make_request)

    assert output.name == "photo.png"
    with Image.open(photo) as before, Image.open(output) as after:
        assert after.format == "PNG"
        assert after.convert("RGB").tobytes() == before.convert("RGB").tobytes()  # PNG keeps exactly what JPEG decoded


def test_turns_the_photo_upright_and_swaps_dpi_when_the_phone_tagged_a_quarter_turn(
    make_image: MakeImage, make_request: MakeRequest
) -> None:
    photo = make_image("phone.jpg", size=(40, 20), orientation=6, dpi=(300, 150))

    (output,) = _to(R.PNG, R.JPEG, [photo], make_request)

    with Image.open(output) as image:
        assert image.size == (20, 40)
        assert tuple(round(v) for v in image.info["dpi"]) == (150, 300)


def test_lays_transparency_on_white_when_the_target_is_jpeg(make_image: MakeImage, make_request: MakeRequest) -> None:
    clear = make_image("logo.png", mode="RGBA", color=(0, 0, 0, 0))

    (output,) = _to(R.JPEG, R.PNG, [clear], make_request, jpeg_quality=95)

    with Image.open(output) as image:
        assert image.mode == "RGB"
        pixel = image.getpixel((5, 5))
        assert isinstance(pixel, tuple)
        assert all(channel > 245 for channel in pixel)


def test_converts_cmyk_to_rgb_when_saving_png(make_image: MakeImage, make_request: MakeRequest) -> None:
    print_ready = make_image("print.jpg", mode="CMYK", color=(0, 255, 255, 0))

    (output,) = _to(R.PNG, R.JPEG, [print_ready], make_request)

    with Image.open(output) as image:
        assert image.mode == "RGB"


def test_converts_every_input_when_given_several(make_image: MakeImage, make_request: MakeRequest) -> None:
    inputs = [make_image(f"p{i}.png") for i in range(3)]

    outputs = _to(R.JPEG, R.PNG, inputs, make_request)

    assert [o.name for o in outputs] == ["p0.jpg", "p1.jpg", "p2.jpg"]


def test_reports_cnv_422_file_001_when_the_file_is_not_an_image(tmp_path: Path, make_request: MakeRequest) -> None:
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"this is not an image")

    with pytest.raises(UnreadableImageError) as caught:
        _to(R.JPEG, R.PNG, [junk], make_request)

    assert caught.value.code == "CNV-422-FILE-001"


def test_reports_cnv_422_file_001_when_the_image_is_truncated(make_image: MakeImage, make_request: MakeRequest) -> None:
    photo = make_image("cut.jpg", size=(200, 200))
    photo.write_bytes(photo.read_bytes()[:400])

    with pytest.raises(UnreadableImageError):
        _to(R.PNG, R.JPEG, [photo], make_request)


def test_refuses_with_cnv_422_limit_001_when_the_image_has_too_many_pixels(
    make_image: MakeImage, make_request: MakeRequest
) -> None:
    big = make_image("big.png", size=(50, 50))
    request = make_request(Converter.IMAGE_FORMATS, R.PNG, R.JPEG, [big])

    with pytest.raises(LimitExceededError) as caught:
        PillowImageFormatConverter(max_pixels=1000).convert(request)

    assert caught.value.code == "CNV-422-LIMIT-001"


def test_reports_cnv_422_file_001_when_the_pixel_data_is_cut_short(
    make_image: MakeImage, make_request: MakeRequest
) -> None:
    picture = make_image("cut.png", size=(300, 300), mode="RGB", color=(1, 2, 3))
    data = picture.read_bytes()
    picture.write_bytes(data[: len(data) // 2])  # the header survives; the pixel data doesn't

    with pytest.raises(UnreadableImageError):
        _to(R.JPEG, R.PNG, [picture], make_request)


def test_refuses_when_pillow_detects_a_decompression_bomb(
    make_image: MakeImage, make_request: MakeRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    bomb = make_image("bomb.png", size=(50, 50))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)  # Pillow refuses at open above 2x this

    with pytest.raises(LimitExceededError):
        _to(R.JPEG, R.PNG, [bomb], make_request)


def test_converts_palette_images_to_rgb_when_the_target_is_jpeg(
    make_image: MakeImage, make_request: MakeRequest
) -> None:
    palette = make_image("icon.png", mode="P", color=3)

    (output,) = _to(R.JPEG, R.PNG, [palette], make_request)

    with Image.open(output) as image:
        assert image.mode == "RGB"


def test_keeps_the_colour_profile_when_converting(tmp_path: Path, make_request: MakeRequest) -> None:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    photo = tmp_path / "srgb.jpg"
    Image.new("RGB", (20, 20), (90, 90, 90)).save(photo, icc_profile=profile)

    (output,) = _to(R.PNG, R.JPEG, [photo], make_request)

    with Image.open(output) as image:
        assert image.info.get("icc_profile") == profile


def test_lets_a_missing_input_surface_as_a_bug_when_the_file_does_not_exist(
    tmp_path: Path, make_request: MakeRequest
) -> None:
    with pytest.raises(FileNotFoundError):  # our own mistake, never reported to the user as a bad file
        _to(R.PNG, R.JPEG, [tmp_path / "missing.jpg"], make_request)
