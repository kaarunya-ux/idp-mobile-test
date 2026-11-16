"""Download the OCR models extraction-service's worker needs and check them against ``models.yaml``.

    python3 fetch_models.py [target folder]          # default: ./models

Only the models the engine slots use (PP-OCRv6 small and PP-OCRv5 mobile with Devanagari, plus the page-orientation
classifier); all Apache-2.0, from Hugging Face (``source`` in models.yaml). Every file's SHA-256 must match
models.yaml, or the file is removed and the script fails: the worker would refuse it anyway (ML-01). Standard library
only, so it runs before anything is installed. Files already present and correct are not downloaded again.
"""

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

REGISTRY = Path(__file__).resolve().parent.parent / "extraction-service" / "models.yaml"
BASE = "https://huggingface.co/PaddlePaddle/{repo}/resolve/main/{name}"
REPOS = {  # models.yaml folder -> Hugging Face repository
    "PaddlePaddle-PP-OCRv5-mobile/det": "PP-OCRv5_mobile_det_onnx",
    "PaddlePaddle-PP-OCRv5-mobile/rec": "PP-OCRv5_mobile_rec_onnx",
    "PaddlePaddle-PP-OCRv5-devanagari/rec": "devanagari_PP-OCRv5_mobile_rec_onnx",
    "PaddlePaddle-PP-OCRv6-small/det": "PP-OCRv6_small_det_onnx",
    "PaddlePaddle-PP-OCRv6-small/rec": "PP-OCRv6_small_rec_onnx",
    "PaddlePaddle-PP-LCNet-doc-ori": "PP-LCNet_x1_0_doc_ori_onnx",
}
_FILE = re.compile(r"- path: (\S+)\s+sha256: ([0-9a-f]{64})")


def expected() -> dict[str, str]:
    """path -> sha256, for every file of the models above."""
    found = dict(_FILE.findall(REGISTRY.read_text(encoding="utf-8")))
    return {path: digest for path, digest in found.items() if path.rsplit("/", 1)[0] in REPOS}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(target: Path) -> int:
    failures = 0
    for relative, digest in sorted(expected().items()):
        folder, name = relative.rsplit("/", 1)
        destination = target / relative
        if destination.is_file() and sha256(destination) == digest:
            print(f"ok       {relative}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = BASE.format(repo=REPOS[folder], name=name)
        print(f"download {relative}")
        urllib.request.urlretrieve(url, destination)  # noqa: S310 - a fixed https URL above
        if sha256(destination) != digest:
            destination.unlink()
            failures += 1
            print(f"MISMATCH {relative}: upstream changed; ask the team for the benchmarked files", file=sys.stderr)
    return failures


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "models"
    sys.exit(1 if fetch(folder) else 0)
