"""Error codes follow the shared format and carry their HTTP status (ERR-05, IDP-00 ch. 7.4)."""

import re

from conversion.core.error_codes import ErrorCode

CODE_FORMAT = re.compile(r"^CNV-\d{3}-[A-Z]+-\d{3}$")


def test_every_code_matches_the_shared_format_when_listed() -> None:
    assert all(CODE_FORMAT.match(code) for code in ErrorCode)


def test_reads_the_http_status_from_the_code_when_asked() -> None:
    assert ErrorCode.ROUTE_NOT_SUPPORTED.http_status == 422
