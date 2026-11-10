"""The model registry: every model the gateway may talk to, declared in ``models.yaml`` (standard ML-01, ML-02).

The registry names each model, its version and quantisation, where it came from, how it runs, the id the server answers
to, the gateway roles it serves and the SHA-256 of every file. Code refers to a model by its registry name, never by a
path or URL. The gateway does not hold the model files (a llama-server on another host does), so it cannot hash them
itself; what it can do is ask each server which model it serves and compare that with the registry
(``verify_served_models``). The checksums are verified where the files are placed, with ``scripts/verify_model.py``.

The file uses a narrow YAML subset (whole-line ``#`` comments, ``key: value``, ``- item`` lists, plain or quoted
values), read here without a YAML library: none is on the approved list (ch. 22.3), as in extraction-service ADR 0001.
"""

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

log = structlog.get_logger(__name__)

type CheckMode = Literal["off", "warn", "strict"]

_SHA256 = re.compile(r"[0-9a-f]{64}")
_ROLES = frozenset({"vl", "smart", "auto", "page"})
_RUNTIMES = frozenset({"llama.cpp"})
_DEVICES = frozenset({"cpu", "gpu"})
_REQUIRED = ("name", "version", "quantization", "source", "runtime", "device", "served_as", "roles", "files")
_CHUNK_BYTES = 8 * 1024 * 1024
_RETRY_SECONDS = 5.0


class ModelRegistryError(RuntimeError):
    """The registry is malformed, or a server serves a model it does not list (with ``strict``)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelSpec:
    name: str
    version: str
    quantization: str
    source: str
    runtime: str
    device: str
    served_as: tuple[str, ...]  # the aliases the server may be started with (llama-server --alias)
    roles: tuple[str, ...]
    files: tuple[ModelFile, ...]

    def matches(self, served: "ServedModel") -> bool:
        """True when the server serves this model. When the server reports the file it loaded (llama-server's
        ``/props``) that file name decides, so a swapped model behind the same alias is caught; otherwise one of the
        ids it lists must be an alias of the model or the base name of one of its files."""
        files = {Path(f.path).name.casefold() for f in self.files}
        if served.file is not None:
            return served.file.casefold() in files
        wanted = {alias.casefold() for alias in self.served_as} | files
        return any(name.casefold() in wanted or Path(name).name.casefold() in wanted for name in served.names)


@dataclass(frozen=True, slots=True)
class ServedModel:
    names: frozenset[str]  # the ids and aliases the server lists
    file: str | None  # the base name of the model file it loaded, when it says so


class ModelRegistry:
    def __init__(self, specs: tuple[ModelSpec, ...]) -> None:
        self._specs = specs
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ModelRegistryError("models.yaml lists the same model name twice")
        roles = [role for s in specs for role in s.roles]
        if len(set(roles)) != len(roles):
            raise ModelRegistryError("models.yaml gives one role to two models")

    @classmethod
    def from_file(cls, path: Path) -> "ModelRegistry":
        path = resolve_registry_path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ModelRegistryError(f"models.yaml could not be read: {path}") from exc
        return cls.from_text(text)

    @classmethod
    def from_text(cls, text: str) -> "ModelRegistry":
        document = _parse(text)
        entries = document.get("models") if isinstance(document, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ModelRegistryError("models.yaml needs a non-empty 'models:' list")
        return cls(tuple(_spec(entry) for entry in entries))

    @property
    def specs(self) -> tuple[ModelSpec, ...]:
        return self._specs

    def for_role(self, role: str) -> ModelSpec | None:
        return next((s for s in self._specs if role in s.roles), None)

    def spec(self, name: str) -> ModelSpec:
        for s in self._specs:
            if s.name == name:
                return s
        raise ModelRegistryError(f"no model named {name} in models.yaml")

    def fingerprint(self) -> str:
        """A short hash of what the registry pins: names, versions, quantisation, ids, roles and checksums. It
        changes when the model changes and is part of the gateway's ``config_hash`` (drift strategies 3 and 4)."""
        content = [
            [
                s.name,
                s.version,
                s.quantization,
                s.served_as,
                list(s.roles),
                [[f.path, f.sha256, f.size] for f in s.files],
            ]
            for s in sorted(self._specs, key=lambda s: s.name)
        ]
        return hashlib.sha256(json.dumps(content).encode()).hexdigest()[:12]


def resolve_registry_path(path: Path) -> Path:
    """A relative ``models.yaml`` is looked for in the working directory, then next to the service's source tree. A
    service started by systemd or a process manager does not always have the service folder as its working directory,
    and a registry that is silently not found would also change ``config_hash`` (it would hash as "none")."""
    if path.is_absolute():
        return path
    for root in (Path.cwd(), Path(__file__).resolve().parents[4]):
        if (root / path).exists():
            return root / path
    return path


def sha256_of(path: Path) -> str:
    """The SHA-256 of a file, read in chunks (the model files are tens of gigabytes)."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ verifying what a server serves
async def served_model(client: httpx.AsyncClient, base_url: str, api_key: str | None = None) -> ServedModel | None:
    """What an OpenAI-style server says it serves, or ``None`` when it can't be asked.

    ``/models`` lists the ids (llama-server lists only the alias it was started with, ``--alias``, which differs from
    host to host). llama-server's ``/props`` at the server root names the model file it loaded: the stable identity."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    base = base_url.rstrip("/")
    try:
        response = await client.get(f"{base}/models", headers=headers)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    names: set[str] = set()
    for entry in [*(body.get("data") or []), *(body.get("models") or [])] if isinstance(body, dict) else []:
        if isinstance(entry, dict):
            names.update(str(entry[key]) for key in ("id", "name", "model") if entry.get(key))
            names.update(str(alias) for alias in entry.get("aliases") or [])
    file = None
    try:
        props = (await client.get(f"{base.removesuffix('/v1')}/props", headers=headers)).json()
    except (httpx.HTTPError, ValueError):
        props = None  # not llama-server, or /props is off: the ids above are all there is
    if isinstance(props, dict):
        if props.get("model_alias"):
            names.add(str(props["model_alias"]))
        if props.get("model_path"):
            file = Path(str(props["model_path"])).name
            names.add(str(props["model_path"]))
    return ServedModel(names=frozenset(names), file=file)


async def verify_served_models(
    registry: ModelRegistry,
    servers: Mapping[str, tuple[str, str | None]],
    client: httpx.AsyncClient,
    mode: CheckMode,
    *,
    wait_seconds: float = 0.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[str]:
    """Ask each role's server which model it serves and compare with the registry. ``servers`` maps a role to its
    base URL and API key. Returns the roles that serve a different model. A server that cannot be asked is only
    logged: the breaker already handles a model that is down. With ``strict`` a mismatch stops the start-up
    (``ModelRegistryError``)."""
    if mode == "off":
        return []
    mismatched: list[str] = []
    for role, (base_url, api_key) in sorted(servers.items()):
        spec = registry.for_role(role)
        if spec is None:
            continue
        served = await served_model(client, base_url, api_key)
        waited = 0.0
        while served is None and waited < wait_seconds:
            await sleep(_RETRY_SECONDS)
            waited += _RETRY_SECONDS
            served = await served_model(client, base_url, api_key)
        if served is None:
            log.warning("model_registry_unverified", role=role, expected=spec.name)
        elif spec.matches(served):
            log.info(
                "model_registry_verified",
                role=role,
                model=spec.name,
                version=spec.version,
                quantization=spec.quantization,
            )
        else:
            mismatched.append(role)
            log.error("model_registry_mismatch", role=role, expected=spec.name, served=_summary(served))
    if mismatched and mode == "strict":
        raise ModelRegistryError(
            f"the model servers for {', '.join(mismatched)} do not serve the models in models.yaml"
        )
    return mismatched


def _summary(served: ServedModel) -> list[str]:
    """What to log about a server that serves another model: names and the file, never paths of the host."""
    return sorted({Path(name).name for name in served.names} | ({served.file} if served.file else set()))


# ------------------------------------------------------------------ reading the registry
def _spec(entry: Any) -> ModelSpec:
    if not isinstance(entry, dict):
        raise ModelRegistryError("each model in models.yaml must be a list of 'key: value' lines")
    missing = [key for key in _REQUIRED if key not in entry]
    if missing:
        raise ModelRegistryError(f"model {entry.get('name', '?')} lacks: {', '.join(missing)}")
    roles, files = entry["roles"], entry["files"]
    served = tuple(entry["served_as"]) if isinstance(entry["served_as"], list) else (entry["served_as"],)
    if not (isinstance(roles, list) and roles and set(roles) <= _ROLES):
        raise ModelRegistryError(f"model {entry['name']}: roles must be some of {sorted(_ROLES)}")
    if entry["runtime"] not in _RUNTIMES or entry["device"] not in _DEVICES:
        raise ModelRegistryError(
            f"model {entry['name']}: runtime must be one of {sorted(_RUNTIMES)}, device one of {sorted(_DEVICES)}"
        )
    if not (isinstance(files, list) and files):
        raise ModelRegistryError(f"model {entry['name']}: files must list at least one file")
    return ModelSpec(
        name=entry["name"],
        version=entry["version"],
        quantization=entry["quantization"],
        source=entry["source"],
        runtime=entry["runtime"],
        device=entry["device"],
        served_as=served,
        roles=tuple(roles),
        files=tuple(_file(entry["name"], f) for f in files),
    )


def _file(model: str, item: Any) -> ModelFile:
    if not (isinstance(item, dict) and {"path", "sha256", "size"} <= set(item)):
        raise ModelRegistryError(f"model {model}: each file needs path, sha256 and size")
    if not _SHA256.fullmatch(str(item["sha256"])):
        raise ModelRegistryError(f"model {model}: {item['path']} has no valid SHA-256 (64 lowercase hex characters)")
    if not str(item["size"]).isdigit():
        raise ModelRegistryError(f"model {model}: {item['path']} has a size that is not a number of bytes")
    return ModelFile(path=str(item["path"]), sha256=str(item["sha256"]), size=int(item["size"]))


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":  # noqa: PLR2004
        return text[1:-1]
    return text


def _parse(text: str) -> Any:
    """The YAML subset the registry uses: nested mappings, lists of scalars or of mappings, and scalars."""
    lines = [
        (len(raw) - len(raw.lstrip(" ")), raw.strip())
        for raw in text.splitlines()
        if raw.strip() and not raw.lstrip().startswith("#")
    ]
    if not lines:
        raise ModelRegistryError("models.yaml is empty")
    value, end = _block(lines, 0, lines[0][0])
    if end != len(lines):
        raise ModelRegistryError("models.yaml has lines this reader does not understand")
    return value


def _block(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[Any, int]:
    if lines[i][1].startswith("- "):
        items: list[Any] = []
        while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
            rest = lines[i][1][2:].strip()
            if ":" in rest and not rest.startswith(("'", '"')):
                lines[i] = (indent + 2, rest)  # "- key: v" starts a mapping two columns in
                item, i = _block(lines, i, indent + 2)
            else:
                item, i = _scalar(rest), i + 1
            items.append(item)
        return items, i
    mapping: dict[str, Any] = {}
    while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
        key, sep, value = lines[i][1].partition(":")
        if not sep:
            raise ModelRegistryError(f"models.yaml line is not 'key: value': {lines[i][1][:40]}")
        if value.strip():
            mapping[key.strip()], i = _scalar(value), i + 1
        else:
            i += 1
            if i >= len(lines) or lines[i][0] <= indent:
                raise ModelRegistryError(f"models.yaml: '{key.strip()}' has no value")
            mapping[key.strip()], i = _block(lines, i, lines[i][0])
    return mapping, i
