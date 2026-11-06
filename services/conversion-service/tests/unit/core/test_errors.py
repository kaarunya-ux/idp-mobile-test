"""Business errors come back from the synchronous route's worker processes, so each must survive the trip."""

import pickle

from conversion.conversion.domain.exceptions import AiUnavailableError, InvalidOptionError, NeedsReadingError


def test_keeps_its_type_detail_and_attributes_when_it_crosses_a_process() -> None:
    copy = pickle.loads(pickle.dumps(InvalidOptionError("dpi", "must be a number")))  # noqa: S301 - our own bytes

    assert type(copy) is InvalidOptionError
    assert copy.detail == "Option dpi must be a number."
    assert copy.option == "dpi"


def test_keeps_an_error_whose_init_takes_no_arguments_when_it_crosses_a_process() -> None:
    for error in (AiUnavailableError(), NeedsReadingError()):
        copy = pickle.loads(pickle.dumps(error))  # noqa: S301 - our own bytes

        assert type(copy) is type(error)
        assert copy.detail == error.detail
