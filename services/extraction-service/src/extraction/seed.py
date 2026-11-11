"""Store the standard templates and document types: ``python -m extraction.seed`` (after ``alembic upgrade head``).

Safe to run again: what is stored is kept. A standard template the service ships a newer version of (its ``version``
in ``standard_templates``) is published as that version and the old one retired, but only while every version stored
was the seed's own: a template a platform admin changed is never touched. Each standard template keeps a fixed id
(derived from its name), so results and environments agree on it.
"""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from extraction.core.build_info import build_stamp
from extraction.core.config import get_settings
from extraction.core.db import SessionFactory, create_db_engine, create_session_factory
from extraction.core.ids import new_id
from extraction.core.logging import configure_logging
from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES
from extraction.pipeline.domain.template import TemplateDefinition
from extraction.template.domain.definition_json import to_json
from extraction.template.infrastructure.orm import DocumentTypeOrm, TemplateOrm, TemplateVersionOrm

log = structlog.get_logger(__name__)

_NAMESPACE = uuid.UUID("8a4f1c2e-6b1d-4e0a-9c3f-2d7b5e6a1f00")  # standard template ids: uuid5(namespace, id)
_SEEDER = "extraction-service seed"
_DOCUMENT_TYPES = {
    "PAN_CARD": ("PAN card", "ID"),
    "AADHAAR_CARD": ("Aadhaar card", "ID"),
    "DRIVING_LICENCE": ("Indian driving licence", "ID"),
}


def standard_template_id(code_name: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, code_name)


def seed(sessions: SessionFactory) -> int:
    """Insert what's missing; return how many rows were added."""
    added = 0
    now = datetime.now(UTC)
    with sessions.begin() as session:
        existing_types = set(session.scalars(select(DocumentTypeOrm.code).where(DocumentTypeOrm.account_id.is_(None))))
        for code, (name, category) in _DOCUMENT_TYPES.items():
            if code not in existing_types:
                session.add(
                    DocumentTypeOrm(
                        id=new_id(),
                        account_id=None,
                        code=code,
                        name=name,
                        category=category,
                        classifier_hints={},
                        default_template_id=None,
                        active=True,
                    )
                )
                added += 1
        for template in STANDARD_TEMPLATES:
            template_id = standard_template_id(template.template_id)
            stored = session.get(TemplateOrm, template_id)
            if stored is not None:
                added += _upgrade(stored, template, now)
                continue
            row = TemplateOrm(
                id=template_id,
                account_id=None,
                document_type_code=template.document_type,
                name=_DOCUMENT_TYPES.get(template.document_type, (template.template_id, ""))[0],
                description=None,
                current_version=template.version,
                status="ACTIVE",
                created_by=_SEEDER,
                version=1,
                created_at=now,
                updated_at=now,
            )
            row.versions.append(
                TemplateVersionOrm(
                    id=new_id(),
                    number=template.version,
                    state="PUBLISHED",
                    definition=to_json(template),
                    created_at=now,
                    published_at=now,
                    published_by=_SEEDER,
                    publish_reason="Standard template shipped with the service",
                )
            )
            session.add(row)
            added += 1
    return added


def _upgrade(stored: TemplateOrm, template: TemplateDefinition, now: datetime) -> int:
    """Publish the shipped version over the stored one when it is newer and nobody but the seed wrote any version.
    Returns 1 when a version was added."""
    current = stored.current_version
    if current is None or template.version <= current or any(v.published_by != _SEEDER for v in stored.versions):
        return 0
    for version in stored.versions:
        if version.state == "PUBLISHED":
            version.state = "RETIRED"
    number = max(v.number for v in stored.versions) + 1
    stored.versions.append(
        TemplateVersionOrm(
            id=new_id(),
            number=number,
            state="PUBLISHED",
            definition=to_json(template),
            created_at=now,
            published_at=now,
            published_by=_SEEDER,
            publish_reason=f"Standard template version {template.version} shipped with the service",
        )
    )
    stored.current_version = number
    stored.version += 1
    stored.updated_at = now
    log.info("standard_template_upgraded", template=template.template_id, version=number)
    return 1


def main() -> None:
    settings = get_settings()
    configure_logging(settings.logging, build_stamp(settings))
    sessions = create_session_factory(create_db_engine(settings.require_db(), application_name=settings.service_name))
    log.info("seed_finished", added=seed(sessions))


if __name__ == "__main__":
    main()
