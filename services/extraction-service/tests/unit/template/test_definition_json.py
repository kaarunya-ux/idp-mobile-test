"""Template definition JSON (IDP-03 ch. 2.5): the standard templates round-trip; bad definitions list every problem."""

import copy
import json

import pytest

from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES
from extraction.template.domain.definition_json import check_definition, parse_definition, to_json
from extraction.template.domain.exceptions import DefinitionInvalidError


@pytest.mark.parametrize("template", STANDARD_TEMPLATES, ids=lambda t: t.template_id)
def test_keeps_every_value_when_a_standard_template_goes_through_json_and_back(template) -> None:
    data = json.loads(json.dumps(to_json(template)))

    again = parse_definition(data, template_id=template.template_id, version=template.version, scope=template.scope)

    assert again == template


def _pan() -> dict:
    return copy.deepcopy(to_json(STANDARD_TEMPLATES[0]))


@pytest.mark.parametrize(
    ("change", "path"),
    [
        (lambda d: d.update(schemaVersion=2), "schemaVersion"),
        (lambda d: d.update(fields=[]), "fields"),
        (lambda d: d["fields"][0].update(type="CHECKBOX"), "fields[0].type"),
        (lambda d: d["fields"][0]["rule"].update(kind="CHECKSUM"), "fields[0].rule.kind"),
        (lambda d: d["fields"][0]["rule"].update(pattern="[unclosed"), "fields[0].rule.pattern"),
        (lambda d: d["fields"][0]["region"].update(box=[0.5, 0.5]), "fields[0].region.box"),
        (lambda d: d["fields"][0]["region"].update(page=1), "fields[0].region.page"),
        (lambda d: d["fields"][0]["region"].update(relativeTo="nowhere"), "fields[0].region.relativeTo"),
        (lambda d: d["fields"].append(copy.deepcopy(d["fields"][0])), "fields"),
        (lambda d: d.update(tables=[{"key": "t"}]), "tables"),
        (lambda d: d["fields"][3]["rule"].update(max="next week"), "fields[3].rule.max"),
    ],
)
def test_reports_cnv_definition_001_with_the_bad_element_when_a_definition_is_invalid(change, path: str) -> None:
    data = _pan()
    change(data)

    with pytest.raises(DefinitionInvalidError) as caught:
        parse_definition(data, template_id="t", version=1, scope="ACCOUNT")

    assert caught.value.code == "EXT-422-DEFINITION-001"
    assert path in [p for p, _ in caught.value.field_errors]


def test_lists_every_problem_at_once() -> None:
    data = _pan()
    data["schemaVersion"] = 9
    data["fields"][0]["type"] = "PHOTO"

    with pytest.raises(DefinitionInvalidError) as caught:
        parse_definition(data, template_id="t", version=1, scope="ACCOUNT")

    assert {"schemaVersion", "fields[0].type"} <= {p for p, _ in caught.value.field_errors}


def test_refuses_a_definition_for_another_document_type() -> None:
    with pytest.raises(DefinitionInvalidError, match="not valid") as caught:
        check_definition(_pan(), document_type="AADHAAR_CARD")

    assert caught.value.field_errors[0][0] == "documentType"
