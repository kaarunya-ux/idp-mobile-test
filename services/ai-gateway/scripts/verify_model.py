"""Check a model file against models.yaml (standard ML-02), on the model host when a file is placed there.

    python3 scripts/verify_model.py /path/to/Qwen3.6-35B-A3B-UD-IQ4_XS.gguf [--registry models.yaml]

Needs only Python 3 and the file: it does not import the gateway, so it runs on a model VM that has nothing else
installed. It compares the size, then the SHA-256, with the entry the registry lists for that file name. Exit code 0
when they match, 1 when they do not or the file is not in the registry. Reading a 17 GB file takes a minute or two.
(``sha256sum <file>`` gives the same checksum to compare by hand.)
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

_CHUNK = 8 * 1024 * 1024
_ENTRY = re.compile(
    r"^\s*-\s*path:\s*(?P<path>\S+)\s*\n\s*sha256:\s*(?P<sha>[0-9a-f]{64})\s*\n\s*size:\s*(?P<size>\d+)\s*$", re.M
)


def _say(message: str) -> None:
    sys.stdout.write(message + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--registry", type=Path, default=Path(__file__).resolve().parents[1] / "models.yaml")
    args = parser.parse_args()
    try:
        text = args.registry.read_text(encoding="utf-8")
    except OSError:
        _say(f"cannot read the registry {args.registry}")
        return 1
    listed = [m for m in _ENTRY.finditer(text) if Path(m["path"]).name == args.file.name]
    if not listed:
        _say(f"{args.file.name} is not listed in {args.registry}")
        return 1
    expected_sha, expected_size = listed[0]["sha"], int(listed[0]["size"])
    actual_size = args.file.stat().st_size
    if actual_size != expected_size:
        _say(f"SIZE MISMATCH for {args.file.name}: file has {actual_size} bytes, registry says {expected_size}")
        return 1
    actual = _sha256(args.file)
    if actual != expected_sha:
        _say(f"CHECKSUM MISMATCH for {args.file.name}:\n  file     {actual}\n  registry {expected_sha}")
        return 1
    _say(f"OK {args.file.name}: size and SHA-256 match the registry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
