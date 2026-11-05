"""What the gateway works with: tasks, callers, image references, prompts sent and answers received (IDP-03 ch. 4)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class Task(StrEnum):
    FIELD_READ = "field-read"
    CLASSIFY = "classify"
    PAGE_ANALYSIS = "page-analysis"
    TEMPLATE_SUGGEST = "template-suggest"
    PAGE_READ = "page-read"  # a whole page as blocks, for conversion-service's scanned routes
    GENERIC_READ = "generic-read"  # any document, every field it shows: extraction's synchronous path (ADR 0003)


class Priority(StrEnum):
    """Which waiting queue a call joins (IDP-03 ch. 4.5): PRIORITY is served 3:1 over STANDARD.

    INTERACTIVE is a person waiting on an open HTTP request (the synchronous path, ADR 0003): it is served before
    both queued lanes, may use the slots kept for it, and waits only briefly before it is told to go the queued way.
    """

    INTERACTIVE = "INTERACTIVE"
    PRIORITY = "PRIORITY"
    STANDARD = "STANDARD"


class Plan(StrEnum):
    """Which daily cap applies (IDP-03 ch. 4.5)."""

    GUEST = "GUEST"
    PERSONAL = "PERSONAL"
    CORPORATE = "CORPORATE"


class ModelRole(StrEnum):
    """The model server's three models (IDP-03 ch. 4.1). The names they are sent as come from settings."""

    VL = "vl"  # vision
    SMART = "smart"  # text only
    AUTO = "auto"  # open-ended questions


@dataclass(frozen=True, slots=True, kw_only=True)
class Caller:
    """Whose call this is. ``account_id`` comes from the body and is used only for caps (IDP-03 ch. 4.2)."""

    client_id: str  # the calling service, from its token
    account_id: UUID
    priority: Priority
    plan: Plan


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageRef:
    """A page image: in storage (``bucket`` and ``key``), or sent in the request (``data``, the synchronous lane,
    which keeps nothing in storage: ADR 0004)."""

    bucket: str = ""
    key: str = ""
    data: bytes | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Image:
    """A page ready to send: JPEG bytes, already resized to the long-side limit."""

    data: bytes
    media_type: str = "image/jpeg"


@dataclass(frozen=True, slots=True, kw_only=True)
class Prompt:
    """One versioned prompt file (``prompts/<task>.v<n>.md``, LLM-02)."""

    name: str  # e.g. "field-read"
    version: int
    system: str
    temperature: float
    max_tokens: int
    model: ModelRole | None = None  # None for a prompt that is part of another call (the repair instruction)
    thinking: bool | None = None  # sent on every call when set: a server's own default must never decide it

    @property
    def label(self) -> str:
        return f"{self.name}.v{self.version}"


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelRequest:
    """One chat completion: the system prompt, the user turn (text and images), and the answer's JSON schema."""

    role: ModelRole
    system: str
    text: str
    images: Sequence[Image]
    schema: Mapping[str, Any]
    schema_name: str
    temperature: float
    max_tokens: int
    follow_up: Sequence[tuple[str, str]] = ()  # (role, text) turns after the first user turn: the repair attempt
    thinking: bool | None = None  # hybrid models' reasoning mode, when the prompt sets it


@dataclass(frozen=True, slots=True, kw_only=True)
class Tokens:
    input: int = 0
    output: int = 0

    def __add__(self, other: "Tokens") -> "Tokens":
        return Tokens(input=self.input + other.input, output=self.output + other.output)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelReply:
    model: str  # the name the server answered with
    content: str
    tokens: Tokens


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Returned with every answer (IDP-03 ch. 4.2)."""

    model: str
    prompt_version: str
    latency_ms: int
    tokens: Tokens


@dataclass(frozen=True, slots=True, kw_only=True)
class Answer[T]:
    value: T
    provenance: Provenance


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldSpec:
    """One field to read. ``label``, ``type`` and ``rule`` help a generic prompt; standard card types ignore them."""

    key: str
    label: str | None = None
    type: str | None = None
    rule: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldReadQuery:
    document_type: str | None
    pages: Sequence[ImageRef]
    fields: Sequence[FieldSpec]
    readings: Mapping[str, Mapping[str, str | None]] = field(default_factory=dict)  # key -> engine slot -> text


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldValue:
    key: str
    value: str | None
    confidence: float | None = None
    reason: str | None = None
