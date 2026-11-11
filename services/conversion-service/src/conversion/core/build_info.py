"""Who is running, and a fingerprint of everything that changes what the service does (prompt drift strategies 1 and 3).

Every log line carries ``service``, ``env``, ``service_version``, ``commit`` and ``config_hash``. Two runs with the same
``config_hash`` should behave the same; a different one says a prompt, threshold or limit changed, before anyone
compares results. Only the settings that change behaviour are hashed (``BEHAVIOUR_SETTINGS``). What may differ by
environment (URLs, credentials, concurrency, timeouts, the log format) is left out on purpose, so a promoted build has
the same hash in dev, pre-prod and prod. No secret is ever hashed or logged.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata

from pydantic import BaseModel

from conversion.core.config import Settings

DISTRIBUTION = "conversion-service"

# Dotted paths into Settings. A path naming a whole group counts every field of it.
BEHAVIOUR_SETTINGS: tuple[str, ...] = (
    "limits",
    "reading_check.enabled",
    "reading_check.languages",
    "reading_check.min_share",
    "reading_check.max_length_ratio",
    "reading_check.min_ocr_words",
    "ai_gateway.render_dpi",
    "ai_gateway.render_max_side_px",
    "ai_gateway.max_pages",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildStamp:
    service: str
    version: str
    environment: str
    commit: str
    config_hash: str

    def log_fields(self) -> dict[str, str]:
        return {
            "service": self.service,
            "env": self.environment,
            "service_version": self.version,
            "commit": self.commit,
            "config_hash": self.config_hash,
        }


def settings_fingerprint(
    settings: BaseModel, paths: Sequence[str] = BEHAVIOUR_SETTINGS, extras: Mapping[str, str] | None = None
) -> str:
    """A short hash of the chosen settings and of ``extras`` (for example the hash of the prompt files)."""
    dumped = settings.model_dump(mode="json")
    picked = {path: _lookup(dumped, path) for path in sorted(paths)}
    payload = json.dumps({"settings": picked, "extras": dict(sorted((extras or {}).items()))}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _lookup(data: Mapping[str, object], path: str) -> object:
    node: object = data
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return "<unset>"
        node = node[part]
    return node


def build_stamp(settings: Settings, extras: Mapping[str, str] | None = None) -> BuildStamp:
    try:
        version = metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:  # running from a source checkout that was never installed
        version = "unknown"
    return BuildStamp(
        service=settings.service_name,
        version=version,
        environment=settings.environment,
        commit=settings.build.commit,
        config_hash=settings_fingerprint(settings, BEHAVIOUR_SETTINGS, extras),
    )
