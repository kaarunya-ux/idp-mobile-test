"""Line and block ids: pieces on one row share a line, close lines that overlap share a block."""

from hypothesis import given
from hypothesis import strategies as st

from idp_ocr_engines.layout import RecognizedPiece, assign_line_and_block_ids
from idp_ocr_engines.model import Box

LINE_HEIGHT = 0.05


def _piece(text: str, x: float, y: float, width: float = 0.2) -> RecognizedPiece:
    return RecognizedPiece(text=text, box=Box(x, y, width, LINE_HEIGHT), confidence=0.9)


def test_returns_nothing_when_there_are_no_pieces() -> None:
    assert assign_line_and_block_ids([]) == ()


def test_puts_pieces_on_one_line_when_their_vertical_centres_line_up() -> None:
    words = assign_line_and_block_ids([_piece("value", 0.5, 0.101), _piece("label", 0.1, 0.1)])

    assert [(word.text, word.line_id) for word in words] == [("label", 0), ("value", 0)]


def test_keeps_two_panels_apart_when_the_pieces_share_a_row_but_are_far_from_each_other() -> None:
    words = assign_line_and_block_ids([_piece("front", 0.05, 0.3, width=0.2), _piece("back", 0.6, 0.3, width=0.2)])

    assert sorted(word.line_id for word in words) == [0, 1]


def test_keeps_a_label_and_its_value_on_one_line_when_a_normal_gap_separates_them() -> None:
    words = assign_line_and_block_ids([_piece("Name", 0.05, 0.3, width=0.1), _piece("ROSHAN", 0.22, 0.3, width=0.2)])

    assert [word.line_id for word in words] == [0, 0]


def test_numbers_lines_from_top_to_bottom_when_pieces_arrive_in_any_order() -> None:
    words = assign_line_and_block_ids(
        [_piece("third", 0.1, 0.5), _piece("first", 0.1, 0.1), _piece("second", 0.1, 0.3)]
    )

    assert [(word.text, word.line_id) for word in words] == [("first", 0), ("second", 1), ("third", 2)]


def test_keeps_close_overlapping_lines_in_one_block_when_they_follow_each_other() -> None:
    words = assign_line_and_block_ids([_piece("a", 0.1, 0.10), _piece("b", 0.1, 0.16), _piece("c", 0.1, 0.22)])

    assert [word.block_id for word in words] == [0, 0, 0]


def test_starts_a_new_block_when_the_gap_is_larger_than_a_line() -> None:
    words = assign_line_and_block_ids([_piece("a", 0.1, 0.10), _piece("b", 0.1, 0.16), _piece("c", 0.1, 0.60)])

    assert [word.block_id for word in words] == [0, 0, 1]


def test_starts_a_new_block_when_the_next_line_does_not_overlap_horizontally() -> None:
    words = assign_line_and_block_ids([_piece("left", 0.05, 0.10, width=0.2), _piece("right", 0.6, 0.16, width=0.2)])

    assert [word.block_id for word in words] == [0, 1]


piece_strategy = st.builds(
    _piece,
    st.just("t"),
    st.floats(min_value=0.0, max_value=0.7),
    st.floats(min_value=0.0, max_value=0.9),
    st.floats(min_value=0.05, max_value=0.3),
)


@given(st.lists(piece_strategy, min_size=1, max_size=30))
def test_gives_every_piece_a_word_with_contiguous_line_ids_when_any_boxes_are_given(
    pieces: list[RecognizedPiece],
) -> None:
    words = assign_line_and_block_ids(pieces)
    line_ids = sorted({word.line_id for word in words})

    assert len(words) == len(pieces)
    assert line_ids == list(range(len(line_ids)))


@given(st.lists(piece_strategy, min_size=1, max_size=30))
def test_gives_the_same_ids_when_the_pieces_arrive_in_a_different_order(pieces: list[RecognizedPiece]) -> None:
    assert assign_line_and_block_ids(pieces) == assign_line_and_block_ids(list(reversed(pieces)))
