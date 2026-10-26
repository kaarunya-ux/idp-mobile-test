"""In-memory stand-ins for the database, storage, inspector, task queue and clock, so the use cases are tested
without any service running. The real adapters are tested against real servers in tests/integration."""

import copy
import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from uuid import UUID, uuid4

import pytest

from conversion.conversion.domain.events import ConversionEvent, ItemContext
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionItem, ConversionRequest
from conversion.conversion.domain.ports import UploadedObject
from conversion.conversion.domain.values import InputFile, ObjectRef, WorkerPool

START = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
STALE_AFTER = timedelta(minutes=12)
OUTPUTS_BUCKET = "idp-outputs"


class FixedClock:
    def __init__(self) -> None:
        self.current = START

    def now(self) -> datetime:
        return self.current

    def advance(self, by: timedelta) -> None:
        self.current += by


@dataclass
class Database:
    """What the fake unit of work commits to. ``events`` are the outbox rows, in order."""

    items: dict[UUID, ConversionItem] = field(default_factory=dict)
    inbox: set[UUID] = field(default_factory=set)
    events: list[ConversionEvent] = field(default_factory=list)
    fail_next_commit: Exception | None = None


class _Items:
    def __init__(self, staged: dict[UUID, ConversionItem]) -> None:
        self._staged = staged

    def get_for_update(self, item_id: UUID) -> ConversionItem | None:
        return self._staged.get(item_id)

    def add(self, item: ConversionItem) -> None:
        self._staged[item.id] = item

    def save(self, item: ConversionItem) -> None:
        self._staged[item.id] = item


class InMemoryUnitOfWork:
    """Works on copies; only ``commit`` writes them back, so a missing commit loses everything like a rollback."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._staged = copy.deepcopy(database.items)
        self._inbox: set[UUID] = set()
        self._events: list[ConversionEvent] = []
        self.items = _Items(self._staged)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def record_message_once(self, message_id: UUID, message_type: str) -> bool:
        if message_id in self._database.inbox or message_id in self._inbox:
            return False
        self._inbox.add(message_id)
        return True

    def add_event(self, event: ConversionEvent) -> None:
        self._events.append(event)

    def commit(self) -> None:
        if self._database.fail_next_commit is not None:
            error, self._database.fail_next_commit = self._database.fail_next_commit, None
            raise error
        self._database.items = self._staged
        self._database.inbox |= self._inbox
        self._database.events.extend(self._events)


class FakeTaskQueue:
    def __init__(self) -> None:
        self.sent: list[tuple[UUID, WorkerPool]] = []

    def enqueue(self, item_id: UUID, pool: WorkerPool) -> None:
        self.sent.append((item_id, pool))


class FolderStorage:
    """Objects are files under ``root/<bucket>/<key>``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.uploaded: dict[str, str] = {}  # key -> content type
        self.fail_with: Exception | None = None

    def put(self, ref: ObjectRef, source: Path) -> None:
        target = self.root / ref.bucket / ref.key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def download(self, ref: ObjectRef, destination: Path) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        shutil.copyfile(self.root / ref.bucket / ref.key, destination)

    def upload(self, source: Path, ref: ObjectRef, content_type: str) -> UploadedObject:
        self.put(ref, source)
        self.uploaded[ref.key] = content_type
        data = source.read_bytes()
        return UploadedObject(size=len(data), sha256=hashlib.sha256(data).hexdigest())


class FakeInspector:
    def __init__(self) -> None:
        self.scanned = False

    def is_scanned_pdf(self, path: Path) -> bool:
        return self.scanned

    def count_pages(self, path: Path, file_format: Format) -> int | None:
        return 1 if file_format in {Format.PDF, Format.PNG, Format.JPEG} else None


MakeRequestOf = Callable[..., ConversionRequest]


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def database() -> Database:
    return Database()


@pytest.fixture
def units(database: Database) -> Callable[[], InMemoryUnitOfWork]:
    return lambda: InMemoryUnitOfWork(database)


@pytest.fixture
def task_queue() -> FakeTaskQueue:
    return FakeTaskQueue()


@pytest.fixture
def storage(tmp_path: Path) -> FolderStorage:
    return FolderStorage(tmp_path / "minio")


@pytest.fixture
def inspector() -> FakeInspector:
    return FakeInspector()


@pytest.fixture
def conversion_request() -> MakeRequestOf:
    """``conversion_request(Format.PNG, Format.PDF, inputs=2, options={...})``: one item's request."""

    def make(
        source: Format = Format.PNG,
        target: Format = Format.PDF,
        *,
        inputs: int = 1,
        options: dict[str, Any] | None = None,
        item_id: UUID | None = None,
    ) -> ConversionRequest:
        context = ItemContext(tenant_id=uuid4(), correlation_id="corr-1", job_id=uuid4(), item_id=item_id or uuid4())
        files = tuple(
            InputFile(ref=ObjectRef(bucket="idp-originals", key=f"acc/doc/file-{n}"), format=source)
            for n in range(inputs)
        )
        return ConversionRequest(
            context=context, inputs=files, target=target, options=options or {}, output_prefix="acc/job/item"
        )

    return make
