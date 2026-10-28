"""The template designer's samples and test runs (IDP-03 ch. 2.9).

A sample is a document the designer uploaded (through document-service), with the values a person says are right.
A worker prepares it once: the first page as a PNG and the OCR words, in ``idp-templates`` (IDP-00 ch. 7.7), so
suggestions and test runs don't render it again. A test run reads every ready sample with one template version
and scores it; publishing needs a passing run on that exact definition from the last 24 hours.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from extraction.template.domain.exceptions import DefinitionInvalidError

MAX_SAMPLES_PER_VERSION = 20  # IDP-03 ch. 2.9: test runs of up to 20 samples need no quota
MAX_FILES_PER_SAMPLE = 2  # a card's two sides
SAMPLE_BUCKETS = frozenset({"idp-originals", "idp-templates"})
_UNSAFE_KEY = re.compile(r"(^/)|(\.\.)|(\\)|[\x00-\x1f\x7f]")
_EXPECTED_LENGTH = 400


class SampleStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class TestRunStatus(StrEnum):
    __test__ = False  # not a pytest test class

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True, kw_only=True)
class SampleFile:
    bucket: str
    key: str


@dataclass(slots=True, kw_only=True)
class Sample:
    id: UUID
    template_id: UUID
    version: int
    files: tuple[SampleFile, ...]
    expected: dict[str, str]
    status: SampleStatus
    created_at: datetime
    page_key: str | None = None  # idp-templates: the prepared first page
    words_key: str | None = None  # idp-templates: the OCR words, JSON
    error_code: str | None = None


@dataclass(slots=True, kw_only=True)
class TestRun:
    __test__ = False

    id: UUID
    template_id: UUID
    version: int
    status: TestRunStatus
    definition_hash: str  # the definition it tested; a later edit makes the run stale
    samples: list[dict[str, Any]]  # [{sampleId, expected}]
    created_at: datetime
    report: dict[str, Any] | None = None
    finished_at: datetime | None = None
    error_code: str | None = None


def definition_hash(definition: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def check_sample_files(files: list[SampleFile], *, account_id: UUID | None) -> None:
    """Samples come from the account's own uploads (SEC-05). Standard templates (no account) are platform staff's."""
    problems = []
    if not 1 <= len(files) <= MAX_FILES_PER_SAMPLE:
        problems.append(("files", f"1 to {MAX_FILES_PER_SAMPLE} files"))
    for index, found in enumerate(files):
        if found.bucket not in SAMPLE_BUCKETS:
            problems.append((f"files[{index}].bucket", f"one of {', '.join(sorted(SAMPLE_BUCKETS))}"))
        if not 1 <= len(found.key) <= 1024 or _UNSAFE_KEY.search(found.key):  # noqa: PLR2004
            problems.append((f"files[{index}].key", "a safe relative key"))
        elif account_id is not None and not found.key.startswith(f"{account_id}/"):
            problems.append((f"files[{index}].key", "a file of your own account"))
    if problems:
        raise DefinitionInvalidError(problems)


def check_expected(expected: dict[str, str], field_keys: set[str]) -> None:
    problems = [(f"expected.{key}", "not a field of this version") for key in expected if key not in field_keys]
    problems += [
        (f"expected.{key}", f"at most {_EXPECTED_LENGTH} characters")
        for key, value in expected.items()
        if len(value) > _EXPECTED_LENGTH
    ]
    if problems:
        raise DefinitionInvalidError(problems)


@dataclass(frozen=True, slots=True, kw_only=True)
class Bundle:
    """The mobile template bundle (IDP-03 ch. 2.10): what the phone needs to read documents offline."""

    version: str
    body: dict[str, Any] = field(default_factory=dict)
