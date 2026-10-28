"""In-memory stand-ins for the database, storage and task queue, shared by the job tests."""

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid4

from extraction.pipeline.domain.exceptions import InputMissingError
from extraction.pipeline.domain.jobs import (
    ExtractionEvent,
    ExtractionItem,
    ExtractionRequest,
    ItemContext,
    ObjectRef,
)
from idp_ocr_engines.model import PageImage

START = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.current = START

    def now(self) -> datetime:
        return self.current

    def advance(self, by: timedelta) -> None:
        self.current += by


@dataclass
class Database:
    items: dict[UUID, ExtractionItem] = field(default_factory=dict)
    inbox: set[UUID] = field(default_factory=set)
    events: list[ExtractionEvent] = field(default_factory=list)
    commits: int = 0


class _Items:
    def __init__(self, staged: dict[UUID, ExtractionItem]) -> None:
        self._staged = staged

    def get_for_update(self, item_id: UUID) -> ExtractionItem | None:
        return self._staged.get(item_id)

    def add(self, item: ExtractionItem) -> None:
        self._staged[item.id] = item

    def save(self, item: ExtractionItem) -> None:
        self._staged[item.id] = item


class Units:
    """A unit of work that copies the database and writes back only on commit, like a transaction."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def __call__(self) -> "_Unit":
        return _Unit(self._database)


class _Unit:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._items = copy.deepcopy(database.items)
        self._inbox: set[UUID] = set()
        self._events: list[ExtractionEvent] = []
        self.items = _Items(self._items)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def record_message_once(self, message_id: UUID, message_type: str) -> bool:
        if message_id in self._database.inbox or message_id in self._inbox:
            return False
        self._inbox.add(message_id)
        return True

    def add_event(self, event: ExtractionEvent) -> None:
        self._events.append(event)

    def commit(self) -> None:
        self._database.items = self._items
        self._database.inbox |= self._inbox
        self._database.events += self._events
        self._database.commits += 1


class Tasks:
    def __init__(self) -> None:
        self.sent: list[UUID] = []

    def enqueue(self, item_id: UUID) -> None:
        self.sent.append(item_id)


class Storage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.pages: dict[str, PageImage] = {}

    def read_bytes(self, ref: ObjectRef) -> bytes:
        if (ref.bucket, ref.key) not in self.objects:
            raise InputMissingError
        return self.objects[(ref.bucket, ref.key)][0]

    def write_bytes(self, ref: ObjectRef, data: bytes, content_type: str) -> None:
        self.objects[(ref.bucket, ref.key)] = (data, content_type)

    def save_page(self, ref: ObjectRef, page: PageImage) -> None:
        self.pages[ref.key] = page


def request(*, files: int = 1, item_id: UUID | None = None) -> ExtractionRequest:
    context = ItemContext(tenant_id=uuid4(), correlation_id="corr-1", job_id=uuid4(), item_id=item_id or uuid4())
    return ExtractionRequest(
        context=context,
        document_id=uuid4(),
        files=tuple(ObjectRef(bucket="idp-originals", key=f"acc/doc/file-{n}") for n in range(files)),
        template_id=None,
        options={},
    )
