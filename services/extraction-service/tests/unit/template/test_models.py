"""Template versions: only drafts change, one is published, a retired one comes back, archived ones stay put."""

from datetime import timedelta
from uuid import uuid4

import pytest

from extraction.template.domain.exceptions import (
    DefinitionInvalidError,
    TemplateNotDraftError,
    TemplateNotFoundError,
    TemplateNotTestedError,
    TemplateStateError,
)
from extraction.template.domain.models import DocumentType, Template, VersionState
from support.template_fakes import ACCOUNT, NOW

DEFINITION = {"schemaVersion": 1, "documentType": "PAN_CARD", "fields": []}


def _template() -> Template:
    return Template.create(
        id=uuid4(),
        account_id=ACCOUNT,
        document_type_code="PAN_CARD",
        name="PAN",
        description=None,
        definition=DEFINITION,
        actor="u",
        now=NOW,
    )


def test_starts_with_one_draft_and_no_published_version() -> None:
    template = _template()

    assert [(v.number, v.state) for v in template.versions] == [(1, VersionState.DRAFT)]
    assert template.current_version is None


def test_publishes_with_an_override_reason_then_retires_it_when_the_next_version_is_published() -> None:
    template = _template()
    template.publish(1, actor="u", now=NOW, tested_at=None, override_reason="checked by hand")
    template.new_version(NOW)
    template.publish(2, actor="u", now=NOW, tested_at=NOW - timedelta(hours=1), override_reason=None)

    assert [(v.number, v.state) for v in template.versions] == [(1, VersionState.RETIRED), (2, VersionState.PUBLISHED)]
    assert (template.version(1).publish_reason, template.version(2).publish_reason) == ("checked by hand", None)
    assert template.current_version == 2
    assert template.row_version == 4


@pytest.mark.parametrize("tested_at", [None, NOW - timedelta(hours=25)])
def test_refuses_to_publish_without_a_recent_test_or_a_reason(tested_at) -> None:
    with pytest.raises(TemplateNotTestedError):
        _template().publish(1, actor="u", now=NOW, tested_at=tested_at, override_reason="  ")


def test_restores_a_retired_version_and_retires_the_published_one() -> None:
    template = _template()
    template.publish(1, actor="u", now=NOW, tested_at=None, override_reason="x")
    template.new_version(NOW)
    template.publish(2, actor="u", now=NOW, tested_at=None, override_reason="x")

    template.restore(1, actor="u", now=NOW)

    assert (template.version(1).state, template.version(2).state) == (VersionState.PUBLISHED, VersionState.RETIRED)


def test_changes_only_a_draft_and_only_one_draft_at_a_time() -> None:
    template = _template()
    template.publish(1, actor="u", now=NOW, tested_at=None, override_reason="x")

    with pytest.raises(TemplateNotDraftError):
        template.save_draft(1, DEFINITION, NOW)
    template.new_version(NOW)
    with pytest.raises(TemplateStateError, match="already a draft"):
        template.new_version(NOW)
    with pytest.raises(TemplateStateError, match="only a retired"):
        template.restore(2, actor="u", now=NOW)
    with pytest.raises(TemplateNotFoundError):
        template.version(9)
    with pytest.raises(DefinitionInvalidError):
        template.save_draft(2, {**DEFINITION, "documentType": "AADHAAR_CARD"}, NOW)


def test_refuses_changes_once_archived() -> None:
    template = _template()
    template.archive(NOW)
    template.archive(NOW)

    with pytest.raises(TemplateStateError, match="archived"):
        template.new_version(NOW)


def test_clones_the_published_definition_into_a_new_draft_for_another_account() -> None:
    template = _template()
    template.publish(1, actor="u", now=NOW, tested_at=None, override_reason="x")

    copy = template.clone(id=uuid4(), account_id=uuid4(), actor="v", now=NOW)

    assert (copy.versions[0].state, copy.versions[0].definition) == (VersionState.DRAFT, DEFINITION)
    assert copy.versions[0].definition is not template.versions[0].definition


def test_refuses_a_document_type_with_a_bad_code_category_or_name() -> None:
    with pytest.raises(DefinitionInvalidError) as caught:
        DocumentType(
            code="pan",
            account_id=None,
            name=" ",
            category="SPORTS",
            classifier_hints={},
            default_template_id=None,
            active=True,
        )

    assert {p for p, _ in caught.value.field_errors} == {"code", "category", "name"}
