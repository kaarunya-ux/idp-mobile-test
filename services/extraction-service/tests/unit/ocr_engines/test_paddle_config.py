from pathlib import Path

import pytest

from idp_ocr_engines.errors import ModelFileError
from idp_ocr_engines.paddle_config import read_post_process


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "inference.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_numbers_and_lists_from_post_process_only(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Global:\n  thresh: 9\nPostProcess:\n  box_thresh: 0.45\n  character_dict:\n  - a\n  - ''''\n  - '#'\n"
        '  - "\\u3042"\n  - \u3000\n  thresh: 0.2\nPreProcess:\n  thresh: 7\n',
    )
    assert read_post_process(path) == {
        "box_thresh": "0.45",
        "character_dict": ["a", "'", "#", "\u3042", "\u3000"],
        "thresh": "0.2",
    }


def test_a_file_without_post_process_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelFileError):
        read_post_process(write(tmp_path, "Global:\n  a: 1\n"))


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelFileError):
        read_post_process(tmp_path / "absent.yml")


def test_a_double_quoted_value_it_cannot_decode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelFileError):
        read_post_process(write(tmp_path, 'PostProcess:\n  character_dict:\n  - "\\q"\n'))
