"""models.yaml: strict reading, and verification of every model file against its recorded SHA-256 (ML-01, ML-02)."""

import hashlib
import textwrap
from pathlib import Path

import pytest

from idp_ocr_engines.errors import ModelFileError, ModelRegistryError, UnknownModelError
from idp_ocr_engines.registry import ModelRegistry

REPO_REGISTRY = Path(__file__).parents[3] / "models.yaml"


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_registry(directory: Path, *, files: dict[str, bytes], entry: str, extra: str = "") -> Path:
    listing = "\n".join(f"      - path: {name}\n        sha256: {_sha(content)}" for name, content in files.items())
    document = textwrap.dedent(
        """\
        # a comment line
        models:
          - name: demo
            version: "1.0"
            source: test
            runtime: onnxruntime
            device: cpu
            entry: {entry}
            files:
        {listing}
        {extra}
        """
    ).format(entry=entry, listing=listing, extra=extra)
    path = directory / "models.yaml"
    path.write_text(document, encoding="utf-8")
    return path


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "models"
    directory.mkdir()
    (directory / "demo.onnx").write_bytes(b"weights")
    (directory / "demo.yml").write_bytes(b"config")
    return directory


def test_verifies_the_model_when_every_file_matches(tmp_path: Path, models_dir: Path) -> None:
    registry_file = _write_registry(tmp_path, files={"demo.onnx": b"weights", "demo.yml": b"config"}, entry="demo.onnx")

    model = ModelRegistry.from_file(registry_file).verify("demo", models_dir)

    assert model.entry_path == models_dir / "demo.onnx"
    assert model.path_of("demo.yml") == models_dir / "demo.yml"
    assert model.spec.version == "1.0"


def test_refuses_the_model_when_a_file_has_changed(tmp_path: Path, models_dir: Path) -> None:
    registry_file = _write_registry(tmp_path, files={"demo.onnx": b"weights", "demo.yml": b"config"}, entry="demo.onnx")
    (models_dir / "demo.onnx").write_bytes(b"tampered")

    with pytest.raises(ModelFileError, match="checksum"):
        ModelRegistry.from_file(registry_file).verify("demo", models_dir)


def test_refuses_the_model_when_a_file_is_missing(tmp_path: Path, models_dir: Path) -> None:
    registry_file = _write_registry(tmp_path, files={"demo.onnx": b"weights", "demo.yml": b"config"}, entry="demo.onnx")
    (models_dir / "demo.yml").unlink()

    with pytest.raises(ModelFileError, match="missing"):
        ModelRegistry.from_file(registry_file).verify("demo", models_dir)


def test_does_not_leak_the_directory_when_a_file_is_missing(tmp_path: Path, models_dir: Path) -> None:
    registry_file = _write_registry(tmp_path, files={"demo.onnx": b"weights", "demo.yml": b"config"}, entry="demo.onnx")
    (models_dir / "demo.yml").unlink()

    with pytest.raises(ModelFileError) as caught:
        ModelRegistry.from_file(registry_file).verify("demo", models_dir)

    assert str(models_dir) not in str(caught.value)


def test_refuses_an_unknown_name_when_it_is_not_in_the_registry(tmp_path: Path) -> None:
    registry_file = _write_registry(tmp_path, files={"demo.onnx": b"weights"}, entry="demo.onnx")

    with pytest.raises(UnknownModelError):
        ModelRegistry.from_file(registry_file).get_spec("other")


def test_refuses_a_file_lookup_when_the_name_is_not_part_of_the_model(tmp_path: Path, models_dir: Path) -> None:
    registry_file = _write_registry(tmp_path, files={"demo.onnx": b"weights", "demo.yml": b"config"}, entry="demo.onnx")
    model = ModelRegistry.from_file(registry_file).verify("demo", models_dir)

    with pytest.raises(UnknownModelError):
        model.path_of("other.txt")


@pytest.mark.parametrize(
    "document",
    [
        "",
        "models: not-a-list",
        "just text",
        "models:\n  - not-a-mapping",
        "models:\n  - name: x",
        "models:\n  - name: x\n      stray: indent",
        "models:\n  - name\n",
        "models:\n  - name: x\n    files:\n      - path: a\n        sha256: b\n    version: 1\nmodels:",
        "models: [",
    ],
)
def test_refuses_the_registry_when_it_is_malformed(tmp_path: Path, document: str) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ModelRegistryError):
        ModelRegistry.from_file(path)


def test_refuses_the_registry_when_the_file_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(ModelRegistryError):
        ModelRegistry.from_file(tmp_path / "absent.yaml")


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        ("runtime: onnxruntime", "runtime: tensorflow"),
        ("device: cpu", "device: cuda"),
        ("entry: demo.onnx", "entry: other.onnx"),
    ],
)
def test_refuses_the_registry_when_a_field_has_a_value_it_does_not_support(
    tmp_path: Path, replacement: str, reason: str
) -> None:
    registry_file = _write_registry(tmp_path, files={"demo.onnx": b"weights"}, entry="demo.onnx")
    registry_file.write_text(registry_file.read_text(encoding="utf-8").replace(replacement, reason), encoding="utf-8")

    with pytest.raises(ModelRegistryError):
        ModelRegistry.from_file(registry_file)


def test_refuses_the_registry_when_a_checksum_is_not_64_hex_digits(tmp_path: Path) -> None:
    registry_file = _write_registry(tmp_path, files={"demo.onnx": b"weights"}, entry="demo.onnx")
    registry_file.write_text(
        registry_file.read_text(encoding="utf-8").replace(_sha(b"weights"), "ABC123"), encoding="utf-8"
    )

    with pytest.raises(ModelRegistryError, match="SHA-256"):
        ModelRegistry.from_file(registry_file)


def test_refuses_the_registry_when_a_name_appears_twice(tmp_path: Path) -> None:
    single = _write_registry(tmp_path, files={"demo.onnx": b"weights"}, entry="demo.onnx")
    entry = single.read_text(encoding="utf-8").split("models:\n", 1)[1]
    single.write_text("models:\n" + entry + entry, encoding="utf-8")

    with pytest.raises(ModelRegistryError, match="twice"):
        ModelRegistry.from_file(single)


@pytest.mark.parametrize(
    "name",
    [
        "easyocr-detector",
        "easyocr-recognizer",
        "ppocrv5-mobile-det",
        "ppocrv5-mobile-rec",
        "ppocrv5-devanagari-rec",
        "ppocrv6-small-det",
        "ppocrv6-small-rec",
    ],
)
def test_declares_the_model_when_the_repository_registry_is_read(name: str) -> None:
    spec = ModelRegistry.from_file(REPO_REGISTRY).get_spec(name)

    assert spec.runtime == "onnxruntime"
    assert spec.device == "cpu"
    assert spec.entry.endswith(".onnx")
