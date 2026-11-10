"""Greedy CTC decoding, shared by every recogniser (EasyOCR's CRNN and the PP-OCR recognisers).

A recogniser outputs, for each time step, a score for every class. Class 0 is the blank; class ``i`` is
``charset[i]``. The text is the most likely class at each step with repeats collapsed and blanks dropped; the
confidence is the geometric mean of the winning probabilities, a ranking signal and not a calibrated probability.
"""

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

_BLANK = 0
_ROW_SUM_TOLERANCE = 1e-3
_FLOOR = 1e-6  # keeps the log finite when a probability underflows to 0


def to_probabilities(scores: NDArray[np.floating]) -> NDArray[np.float64]:
    """Softmax over the class axis, unless the rows already sum to one (some exports include the softmax)."""
    values = scores.astype(np.float64)
    if abs(float(values[0].sum()) - 1.0) <= _ROW_SUM_TOLERANCE:
        return values
    shifted = np.exp(values - values.max(axis=-1, keepdims=True))
    return shifted / shifted.sum(axis=-1, keepdims=True)


def decode_ctc_greedy(scores: NDArray[np.floating], charset: Sequence[str]) -> tuple[str, float]:
    """Decode a ``(time steps, classes)`` score matrix into ``(text, confidence)``; ``charset[0]`` is the blank."""
    probabilities = to_probabilities(scores)
    best = probabilities.argmax(axis=-1)
    best_probability = probabilities.max(axis=-1)
    characters: list[str] = []
    kept: list[float] = []
    previous = _BLANK
    for index, probability in zip(best.tolist(), best_probability.tolist(), strict=True):
        if index not in {_BLANK, previous}:
            characters.append(charset[index])
            kept.append(probability)
        previous = index
    text = "".join(characters).strip()
    if not kept:
        return text, 0.0
    return text, float(np.exp(np.mean(np.log(np.clip(kept, _FLOOR, 1.0)))))
