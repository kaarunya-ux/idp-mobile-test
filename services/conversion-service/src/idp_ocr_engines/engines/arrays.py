"""Moving between the pure ``PageImage`` and NumPy arrays, which only the engines need."""

import numpy as np
from numpy.typing import NDArray

from idp_ocr_engines.model import PageImage

_RGB_CHANNELS = 3


def to_rgb_array(image: PageImage) -> NDArray[np.uint8]:
    """The page as a read-only ``height x width x 3`` array of 8-bit RGB."""
    return np.frombuffer(image.rgb, dtype=np.uint8).reshape(image.height, image.width, _RGB_CHANNELS)


def from_rgb_array(array: NDArray[np.uint8], *, page_index: int = 0) -> PageImage:
    """A ``PageImage`` from a ``height x width x 3`` array of 8-bit RGB."""
    height, width, _ = array.shape
    return PageImage(width=width, height=height, rgb=np.ascontiguousarray(array).tobytes(), page_index=page_index)
