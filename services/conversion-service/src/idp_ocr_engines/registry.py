"""The model registry: every model the service may run, declared in ``models.yaml`` (ML-01).

Code refers to a model by its registry name, never by a path or URL. Models are baked into the image or placed in the
models directory ahead of time; nothing is downloaded at request time (ML-02). Before a model is loaded, each of its
files is checked against the SHA-256 recorded here, so a corrupt or swapped file stops the worker at start-up.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from idp_ocr_engines.errors import ModelFileError, ModelRegistryError, UnknownModelError
from idp_ocr_engines.simple_yaml import decode_scalar

_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHUNK_BYTES = 1024 * 1024
_RUNTIMES = frozenset({"onnxruntime"})
_DEVICES = frozenset({"cpu"})


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelFile:
    """One file of a model, by path inside the models directory, with its SHA-256."""

    path: str
    sha256: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelSpec:
    """A registry entry: what the model is, where it came from, how it runs and which files make it up."""

    name: str
    version: str
    source: str
    runtime: str
    device: str
    entry: str
    files: tuple[ModelFile, ...]


@dataclass(frozen=True, slots=True)
class VerifiedModel:
    """A model whose files are present and match their checksums, ready to be loaded."""

    spec: ModelSpec
    directory: Path

    @property
    def entry_path(self) -> Path:
        """The file the runtime loads (the ``.onnx`` file)."""
        return self.directory / self.spec.entry

    def path_of(self, file_name: str) -> Path:
        """The path of one of the model's files by its base name, for example ``inference.yml``."""
        for model_file in self.spec.files:
            if Path(model_file.path).name == file_name:
                return self.directory / model_file.path
        message = f"Model '{self.spec.name}' has no file named '{file_name}' in the registry."
        raise UnknownModelError(message)


class ModelRegistry:
    """Looks models up by name and verifies their files."""

    def __init__(self, specs: Mapping[str, ModelSpec]) -> None:
        self._specs = dict(specs)

    @classmethod
    def from_file(cls, registry_file: Path) -> "ModelRegistry":
        """Read and validate ``models.yaml``. Its fixed layout is read by ``parse_registry`` (no YAML library)."""
        try:
            document = parse_registry(registry_file.read_text(encoding="utf-8"))
        except (OSError, ModelRegistryError) as error:
            message = "The model registry file could not be read."
            raise ModelRegistryError(message) from error
        if not isinstance(document, dict) or not isinstance(document.get("models"), list):
            message = "The model registry needs a top-level 'models' list."
            raise ModelRegistryError(message)
        specs: dict[str, ModelSpec] = {}
        for entry in document["models"]:
            spec = _read_spec(entry)
            if spec.name in specs:
                message = f"The model registry lists '{spec.name}' twice."
                raise ModelRegistryError(message)
            specs[spec.name] = spec
        return cls(specs)

    def get_spec(self, name: str) -> ModelSpec:
        """The registry entry called ``name``; raises ``UnknownModelError`` if there is none."""
        try:
            return self._specs[name]
        except KeyError as error:
            message = f"'{name}' is not in the model registry."
            raise UnknownModelError(message) from error

    def verify(self, name: str, models_dir: Path) -> VerifiedModel:
        """Check every file of ``name`` under ``models_dir``; raises ``ModelFileError`` on a missing or altered file."""
        spec = self.get_spec(name)
        for model_file in spec.files:
            path = models_dir / model_file.path
            if not path.is_file():
                message = f"Model '{name}': file '{model_file.path}' is missing."
                raise ModelFileError(message)
            if _sha256_of(path) != model_file.sha256:
                message = f"Model '{name}': file '{model_file.path}' does not match its recorded checksum."
                raise ModelFileError(message)
        return VerifiedModel(spec=spec, directory=models_dir)


# The parsed registry is untyped, so ``Any`` appears only at this boundary; every field is checked (PY-01).
_KEY_VALUE = re.compile(r"(?P<key>[a-z0-9_]+):(?: (?P<value>.+))?")
_ENTRY_INDENT, _FILES_INDENT = 4, 8  # columns of an entry's keys and of a file's keys


def parse_registry(text: str) -> dict[str, Any]:
    """Read the registry layout: ``models:``, then ``- name: ...`` entries whose ``files:`` list holds path/sha256.

    Only that layout is accepted, with ``#`` comment lines allowed. Anything else raises ``ModelRegistryError``.
    """
    models: list[dict[str, Any]] = []
    entry: dict[str, Any] | None = None
    file_item: dict[str, str] | None = None
    seen_header = False
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        where = f"The model registry has an unexpected line {number}."
        if raw == "models:" and not seen_header:
            seen_header = True
        elif not seen_header:
            raise ModelRegistryError(where)
        elif indent == 2 and body.startswith("- "):  # noqa: PLR2004
            entry, file_item = {}, None
            models.append(entry)
            _store(entry, body[2:], where)
        elif entry is not None and indent == _ENTRY_INDENT and body == "files:":
            entry["files"], file_item = [], None
        elif entry is not None and indent == _ENTRY_INDENT:
            _store(entry, body, where)
        elif entry is not None and "files" in entry and indent == _FILES_INDENT - 2 and body.startswith("- "):
            file_item = {}
            entry["files"].append(file_item)
            _store(file_item, body[2:], where)
        elif file_item is not None and indent == _FILES_INDENT:
            _store(file_item, body, where)
        else:
            raise ModelRegistryError(where)
    return {"models": models} if seen_header else {}


def _store(target: dict[str, Any], line: str, where: str) -> None:
    match = _KEY_VALUE.fullmatch(line)
    if match is None or match["value"] is None:
        raise ModelRegistryError(where)
    target[match["key"]] = decode_scalar(match["value"])


def _read_spec(entry: Any) -> ModelSpec:
    if not isinstance(entry, dict):
        message = "Every model in the registry must be a mapping."
        raise ModelRegistryError(message)
    name = _text(entry, "name")
    runtime, device = _text(entry, "runtime"), _text(entry, "device")
    if runtime not in _RUNTIMES or device not in _DEVICES:
        message = f"Model '{name}': runtime must be one of {sorted(_RUNTIMES)} and device one of {sorted(_DEVICES)}."
        raise ModelRegistryError(message)
    files = tuple(_read_file(name, item) for item in _list(entry, "files", name))
    entry_path = _text(entry, "entry")
    if entry_path not in {model_file.path for model_file in files}:
        message = f"Model '{name}': the entry file must also be listed under 'files'."
        raise ModelRegistryError(message)
    return ModelSpec(
        name=name,
        version=_text(entry, "version"),
        source=_text(entry, "source"),
        runtime=runtime,
        device=device,
        entry=entry_path,
        files=files,
    )


def _read_file(model_name: str, item: Any) -> ModelFile:
    if not isinstance(item, dict):
        message = f"Model '{model_name}': every file must be a mapping with 'path' and 'sha256'."
        raise ModelRegistryError(message)
    digest = _text(item, "sha256")
    if not _SHA256.fullmatch(digest):
        message = f"Model '{model_name}': a file has a SHA-256 that is not 64 lower-case hex digits."
        raise ModelRegistryError(message)
    return ModelFile(path=_text(item, "path"), sha256=digest)


def _text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        message = f"The model registry needs a non-empty text value for '{key}'."
        raise ModelRegistryError(message)
    return value


def _list(mapping: Mapping[str, Any], key: str, model_name: str) -> list[Any]:
    value = mapping.get(key)
    if not isinstance(value, list) or not value:
        message = f"Model '{model_name}' needs a non-empty list for '{key}'."
        raise ModelRegistryError(message)
    return value


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
