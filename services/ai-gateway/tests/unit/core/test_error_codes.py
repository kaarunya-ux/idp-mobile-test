"""Error codes keep the shared format and their HTTP status (ERR-05)."""

import re

from ai_gateway.core.error_codes import ErrorCode


def test_every_code_has_the_shared_format_when_listed() -> None:
    for code in ErrorCode:
        assert re.fullmatch(r"AIG-[1-5][0-9]{2}-[A-Z]+-[0-9]{3}", code.value)


def test_returns_the_status_from_the_code_when_asked() -> None:
    assert ErrorCode.CAP_REACHED.http_status == 429
    assert ErrorCode.OUTPUT_INVALID.http_status == 502
    assert ErrorCode.BUSY.http_status == 503
