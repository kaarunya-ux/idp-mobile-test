"""``TemplateCatalogPort``: the published templates a job may use, from ``extraction_db`` (IDP-03 ch. 2.4 and 2.6).

Standard templates plus the account's own; with a ``templateId`` only that one, if the account may use it. Results
are cached per account for ``ttl`` so a worker doesn't query for every page. When the database holds no template
yet (before ``python -m extraction.seed`` has run), the standard templates in code are used.
"""

import time
from collections.abc import Sequence
from uuid import UUID

import structlog
from sqlalchemy import or_, select

from extraction.core.db import SessionFactory
from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES
from extraction.pipeline.domain.template import TemplateDefinition
from extraction.template.domain.definition_json import parse_definition
from extraction.template.domain.exceptions import DefinitionInvalidError
from extraction.template.infrastructure.orm import TemplateOrm, TemplateVersionOrm

log = structlog.get_logger(__name__)


class SqlTemplateCatalog:
    def __init__(self, sessions: SessionFactory, *, ttl_seconds: float = 60.0) -> None:
        self._sessions = sessions
        self._ttl = ttl_seconds
        self._cache: dict[UUID | None, tuple[float, Sequence[TemplateDefinition]]] = {}

    def templates_for(self, account_id: UUID | None, template_id: str | None) -> Sequence[TemplateDefinition]:
        templates = self._published(account_id)
        if template_id is not None:
            chosen = [t for t in templates if t.template_id == template_id]
            if chosen:
                return chosen
            log.warning("template_not_usable", template_id=template_id)  # classify instead of failing the job
        return templates

    def _published(self, account_id: UUID | None) -> Sequence[TemplateDefinition]:
        cached = self._cache.get(account_id)
        if cached is not None and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
        with self._sessions() as session:
            rows = session.execute(
                select(TemplateOrm.id, TemplateOrm.account_id, TemplateVersionOrm.number, TemplateVersionOrm.definition)
                .join(TemplateVersionOrm, TemplateVersionOrm.template_id == TemplateOrm.id)
                .where(
                    TemplateOrm.status == "ACTIVE",
                    TemplateVersionOrm.state == "PUBLISHED",
                    or_(TemplateOrm.account_id.is_(None), TemplateOrm.account_id == account_id),
                )
                .order_by(TemplateOrm.account_id.is_(None), TemplateOrm.name)  # the account's own first
            ).all()
        templates = [t for t in (_parse(*row) for row in rows) if t is not None] or list(STANDARD_TEMPLATES)
        self._cache[account_id] = (time.monotonic(), templates)
        return templates


def _parse(
    template_id: UUID, account_id: UUID | None, number: int, definition: dict[str, object]
) -> TemplateDefinition | None:
    try:
        return parse_definition(
            definition,
            template_id=str(template_id),
            version=number,
            scope="STANDARD" if account_id is None else "ACCOUNT",
        )
    except DefinitionInvalidError:  # stored before a rule tightened: skip it rather than fail every job
        log.error("template_definition_unreadable", template_id=str(template_id), version=number)
        return None
