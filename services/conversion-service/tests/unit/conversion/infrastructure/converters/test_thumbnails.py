"""Image thumbnails for PREVIEW: upright, shrunk to fit, never enlarged, JPEG even from a transparent PNG."""

from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from conversion.conversion.domain.exceptions import LimitExceededError
from conversion.conversion.domain.values import ThumbnailSpec
from conversion.conversion.infrastructure.converters.thumbnails import PillowThumbnailer

MakeImage = Callable[..., Path]
SPEC = ThumbnailSpec(max_side_px=100, jpeg_quality=75)
thumbnailer = PillowThumbnailer(max_pixels=10_000_000)


def _size_and_format(path: Path) -> tuple[tuple[int, int], str | None]:
    with Image.open(path) as image:
        return image.size, image.format


@pytest.mark.parametrize(
    ("size", "orientation", "expected"),
    [
        ((400, 200), None, (100, 50)),  # shrunk to fit, shape kept
        ((400, 200), 6, (50, 100)),  # the phone's quarter-turn tag applied
        ((60, 30), None, (60, 30)),  # never enlarged
    ],
)
def test_writes_an_upright_jpeg_that_fits_the_box_when_an_image_is_given(
    make_image: MakeImage, tmp_path: Path, size: tuple[int, int], orientation: int | None, expected: tuple[int, int]
) -> None:
    output = tmp_path / "thumb.jpg"

    thumbnailer.thumbnail(make_image("photo.jpg", size=size, orientation=orientation), output, SPEC)

    assert _size_and_format(output) == (expected, "JPEG")


def test_lays_a_transparent_image_on_white_when_it_becomes_a_jpeg(make_image: MakeImage, tmp_path: Path) -> None:
    output = tmp_path / "thumb.jpg"

    thumbnailer.thumbnail(make_image("logo.png", size=(50, 50), mode="RGBA", color=(0, 0, 0, 0)), output, SPEC)

    with Image.open(output) as image:
        assert image.getpixel((25, 25)) == (255, 255, 255)


def test_refuses_with_cnv_422_limit_001_when_the_image_has_too_many_pixels(
    make_image: MakeImage, tmp_path: Path
) -> None:
    with pytest.raises(LimitExceededError):
        PillowThumbnailer(max_pixels=1_000_000).thumbnail(
            make_image("huge.png", size=(2000, 1000)), tmp_path / "t.jpg", SPEC
        )
