"""Creating ONNX Runtime sessions and running them with a time limit (ML-03, ML-06, ML-10).

Sessions are created once per process (the caller keeps them) and always on the CPU provider: if a faster device is
ever wanted it is a new, explicit setting, never a silent fallback (ML-10).
"""

import threading
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray
from onnxruntime.capi.onnxruntime_pybind11_state import Fail

from idp_ocr_engines.errors import InferenceTimeoutError

_ERRORS_ONLY = 3  # ONNX Runtime log severity: errors and above


def create_session(model_path: Path, *, threads: int) -> ort.InferenceSession:
    """Load an ONNX model on the CPU provider with ``threads`` intra-op threads."""
    options = ort.SessionOptions()
    options.log_severity_level = _ERRORS_ONLY
    options.intra_op_num_threads = threads
    return ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])


def run_session(
    session: ort.InferenceSession, feeds: Mapping[str, NDArray[np.generic]], *, timeout_seconds: float
) -> list[NDArray[np.generic]]:
    """Run the model; if it is still running after ``timeout_seconds`` it is stopped and the call raises."""
    options = ort.RunOptions()
    timer_fired = threading.Event()

    def stop_run() -> None:
        timer_fired.set()
        options.terminate = True

    timer = threading.Timer(timeout_seconds, stop_run)
    timer.start()
    try:
        return list(session.run(None, dict(feeds), run_options=options))
    except Fail as error:
        if timer_fired.is_set():
            message = "A model run exceeded the inference timeout and was stopped."
            raise InferenceTimeoutError(message) from error
        raise
    finally:
        timer.cancel()
