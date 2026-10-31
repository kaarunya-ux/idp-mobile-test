"""Error codes keep the shared format and the HTTP status they embed (ERR-05)."""

import re

import pytest

from idox_conversion.core.error_codes import ErrorCode
from idox_conversion.core.errors import DomainError

_FORMAT = re.compile(r"CNV-\d{3}-[A-Z]+-\d{3}")


@pytest.mark.parametrize("code", list(ErrorCode))
def test_code_has_the_shared_format(code: ErrorCode) -> None:
    assert _FORMAT.fullmatch(code.value)


def test_http_status_is_the_second_part_of_the_code() -> None:
    assert ErrorCode.ROUTE_UNSUPPORTED.http_status == 422
    assert ErrorCode.TIMEOUT.http_status == 504


def test_codes_from_idp_03_are_unchanged() -> None:
    assert ErrorCode.ROUTE_UNSUPPORTED.value == "CNV-422-ROUTE-001"
    assert ErrorCode.PDF_ENCRYPTED.value == "CNV-422-PDF-001"
    assert ErrorCode.TIMEOUT.value == "CNV-504-TIMEOUT-001"


def test_domain_error_carries_its_detail() -> None:
    error = DomainError("no route")

    assert error.detail == "no route"
    assert str(error) == "no route"
