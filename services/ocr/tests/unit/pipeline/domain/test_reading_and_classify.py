from datetime import date

from extraction.pipeline.domain.classify import classify_page
from extraction.pipeline.domain.reading import field_box, read_field, shift_for
from extraction.pipeline.domain.standard_templates import AADHAAR_CARD, PAN_CARD, STANDARD_TEMPLATES
from idp_ocr_engines.model import Box, OcrWord

TODAY = date(2026, 10, 21)


def word(text: str, x: float, y: float, line: int, confidence: float = 0.9) -> OcrWord:
    return OcrWord(
        text=text, box=Box(x=x, y=y, width=0.2, height=0.04), confidence=confidence, line_id=line, block_id=0
    )


def test_classify_picks_the_template_with_enough_keyword_hits() -> None:
    found = classify_page(STANDARD_TEMPLATES, page_text="INCOME TAX DEPARTMENT\nGOVT. OF INDIA", width=860, height=538)
    assert found is not None
    assert found.template.document_type == "PAN_CARD"
    assert found.confidence > 0.5


def test_classify_returns_none_when_nothing_matches() -> None:
    assert classify_page(STANDARD_TEMPLATES, page_text="Invoice 42", width=860, height=538) is None


def test_a_cropped_page_still_classifies_with_lower_confidence() -> None:
    text = "Government of India\nAadhaar"
    normal = classify_page(STANDARD_TEMPLATES, page_text=text, width=860, height=540)
    cropped = classify_page(STANDARD_TEMPLATES, page_text=text, width=860, height=283)
    assert normal is not None
    assert cropped is not None
    assert cropped.confidence == normal.confidence / 2


def test_read_field_picks_the_line_that_passes_the_rule_and_drops_captions() -> None:
    definition = PAN_CARD.fields[1]  # name
    words = [word("/Name", 0.05, 0.52, 0), word("RAMESH SAHANI", 0.05, 0.58, 1), word("Stray", 0.75, 0.58, 2)]
    reading = read_field(definition, words, definition.box, today=TODAY)
    assert reading is not None
    assert reading.text == "RAMESH SAHANI"


def test_read_field_reads_nothing_from_an_empty_region() -> None:
    assert read_field(PAN_CARD.fields[0], [], PAN_CARD.fields[0].box, today=TODAY) is None


def test_the_number_is_cut_out_of_a_longer_line() -> None:
    definition = AADHAAR_CARD.fields[0]
    words = [word("VID: 1234", 0.1, 0.1, 0), word("2345 6789 0124 extra", 0.1, 0.8, 1)]
    reading = read_field(definition, words, definition.box, today=TODAY)
    assert reading is not None
    assert reading.text == "2345 6789 0124"


def test_fields_move_with_their_anchor_and_stay_inside_the_page() -> None:
    anchor = PAN_CARD.anchors[0]
    moved = shift_for(anchor, [word("INCOME TAX DEPARTMENT", 0.03, 0.2, 0)])
    assert moved is not None
    assert moved[1] > 0.05
    box = field_box(PAN_CARD.fields[3], {"title": (0.0, 0.5)})  # dob would leave the page
    assert box.y + box.height <= 1.0
    assert field_box(PAN_CARD.fields[0], {}) == PAN_CARD.fields[0].box


def test_a_missing_anchor_gives_no_shift() -> None:
    assert shift_for(PAN_CARD.anchors[0], [word("Something else", 0.03, 0.1, 0)]) is None


def test_the_name_is_the_line_above_the_date_of_birth_line_wherever_the_card_sits() -> None:
    name = next(f for f in AADHAAR_CARD.fields if f.key == "name")
    words = [
        word("Government of India", 0.1, 0.05, 0),
        word("Town Name", 0.1, 0.20, 1),  # an earlier line that also looks like a name
        word("PRAKASH RATHOD", 0.1, 0.60, 2),
        word("DOB: 01/07/1974", 0.1, 0.65, 3),
        word("Male", 0.1, 0.70, 4),
    ]
    reading = read_field(name, words, name.box, today=TODAY)
    assert reading is not None
    assert reading.text == "PRAKASH RATHOD"


def test_no_name_is_read_when_there_is_no_date_of_birth_line() -> None:
    name = next(f for f in AADHAAR_CARD.fields if f.key == "name")
    assert read_field(name, [word("PRAKASH RATHOD", 0.1, 0.6, 0)], name.box, today=TODAY) is None


def test_the_date_after_a_dob_label_is_taken_not_another_date_on_the_page() -> None:
    dob = next(f for f in AADHAAR_CARD.fields if f.key == "dob")
    words = [word("Issue Date: 20/02/2013", 0.1, 0.1, 0), word("DOB: 12/07/2002", 0.1, 0.5, 1)]
    reading = read_field(dob, words, dob.box, today=TODAY)
    assert reading is not None
    assert reading.text == "12/07/2002"


def test_the_address_runs_from_its_caption_to_the_pin_code() -> None:
    address = next(f for f in AADHAAR_CARD.fields if f.key == "address")
    words = [word("Address:", 0.1, 0.1, 0), word("S/O: Gopichand Rathod, Ward 3, Pune, 411006", 0.1, 0.2, 1)]
    reading = read_field(address, words, address.box, today=TODAY)
    assert reading is not None
    assert reading.text.endswith("Pune, 411006")


def test_the_address_leaves_out_text_already_read_as_another_field() -> None:
    address = next(f for f in AADHAAR_CARD.fields if f.key == "address")
    words = [
        word("W/O: Ajit Kumar, Ward 1, Pune,", 0.5, 0.2, 0),
        word("SAPNA BAJAJ", 0.0, 0.25, 1),  # the front side's name, on the same rows as the address
        word("DOB: 24/06/1975 Seujpur Mandir, Assam - 786001", 0.5, 0.3, 2),
    ]
    taken = [Box(x=0.0, y=0.24, width=0.3, height=0.06)]
    reading = read_field(address, words, address.box, today=TODAY, skip=taken)
    assert reading is not None
    assert "SAPNA" not in reading.text
    assert reading.text.endswith("786001")


def test_the_english_address_leaves_out_hindi_pieces_and_the_hindi_address_leaves_out_english_ones() -> None:
    english = next(f for f in AADHAAR_CARD.fields if f.key == "address")
    hindi = next(f for f in AADHAAR_CARD.fields if f.key == "address_hi")
    words = [
        word("पत्ता:", 0.0, 0.1, 0),  # the Hindi caption
        word("Address:", 0.5, 0.1, 0),
        word("गोपीचंद राठोड, पुणे,", 0.0, 0.2, 1),
        word("S/O: Gopichand Rathod, Pune, 411006", 0.5, 0.2, 1),
        word("411006", 0.0, 0.3, 2),  # the Hindi column's own PIN piece
    ]
    reading_en = read_field(english, words, english.box, today=TODAY)
    reading_hi = read_field(hindi, words, hindi.box, today=TODAY)
    assert reading_en is not None
    assert reading_hi is not None
    assert reading_en.text.endswith("Gopichand Rathod, Pune, 411006")
    assert reading_hi.text.endswith("411006")
    assert "Gopichand" not in reading_hi.text


def test_an_address_starts_at_its_caption_and_ignores_text_left_of_it_or_above_it() -> None:
    english = next(f for f in AADHAAR_CARD.fields if f.key == "address")
    words = [
        word("Meghna Kumari DOB: 23/10/1997", 0.1, 0.3, 0),  # the front card, left of the back card's address
        word("Address:", 0.55, 0.5, 1),
        word("D/O: Vinod Kumar Singh, Ramnagar, Bihar - 845106", 0.55, 0.6, 2),
        word("A line above the caption, 100001", 0.55, 0.2, 3),
    ]
    reading = read_field(english, words, english.box, today=TODAY)
    assert reading is not None
    assert reading.text == "Vinod Kumar Singh, Ramnagar, Bihar - 845106"  # the D/O marker itself is not part of it


def test_an_address_without_a_caption_is_read_from_the_whole_page() -> None:
    english = next(f for f in AADHAAR_CARD.fields if f.key == "address")
    words = [word("C/O: Zaheer Kadiwala, 402 Faizan Apartment, Mumbai, PIN Code: 400102", 0.1, 0.5, 0)]
    reading = read_field(english, words, english.box, today=TODAY)
    assert reading is not None
    assert reading.text.endswith("400102")


def test_an_address_follows_where_the_pieces_sit_not_the_engines_line_order_and_skips_sideways_text() -> None:
    english = next(f for f in AADHAAR_CARD.fields if f.key == "address")
    sideways = OcrWord(
        text="Download Date", box=Box(x=0.5, y=0.3, width=0.02, height=0.2), confidence=0.9, line_id=0, block_id=0
    )
    words = [
        word("Address:", 0.55, 0.1, 0),
        word("Seujpur, Gabhrupathar, Ward 2,", 0.55, 0.20, 1),  # engine put this line before the one above it
        word("W/O: Ajit Kumar Bajaj,", 0.55, 0.15, 2),
        word("Assam - 786001", 0.55, 0.25, 3),
        sideways,
    ]
    reading = read_field(english, words, english.box, today=TODAY)
    assert reading is not None
    assert reading.text == "W/O: Ajit Kumar Bajaj, Seujpur, Gabhrupathar, Ward 2, Assam - 786001"


def test_a_name_above_the_dob_line_ignores_text_in_another_card_on_the_same_rows() -> None:
    name = next(f for f in AADHAAR_CARD.fields if f.key == "name")
    words = [
        word("Meghna Kumari", 0.1, 0.20, 0),
        word("DOB: 23/10/1997", 0.1, 0.30, 1),
        word("Vinod Kumar Singh", 0.7, 0.25, 2),  # the other card's address, between the two rows in height
    ]
    reading = read_field(name, words, name.box, today=TODAY)
    assert reading is not None
    assert reading.text == "Meghna Kumari"


def test_a_short_caption_is_not_taken_for_sideways_text_but_a_tall_narrow_piece_is() -> None:
    address = next(f for f in AADHAAR_CARD.fields if f.key == "address_hi")
    caption = OcrWord(
        text="पता:", box=Box(x=0.52, y=0.2, width=0.02, height=0.03), confidence=0.9, line_id=0, block_id=0
    )
    body = word("विनोद कुमार, बिहार - 845106", 0.52, 0.25, 1)
    reading = read_field(address, [caption, body], address.box, today=TODAY, page_aspect=3.0)
    assert reading is not None
    assert reading.text.endswith("845106")


def test_tiny_fragments_of_sideways_text_beside_an_address_are_dropped_but_short_real_words_are_kept() -> None:
    english = next(f for f in AADHAAR_CARD.fields if f.key == "address")

    def piece(text: str, y: float, width: float, height: float, line: int) -> OcrWord:
        return OcrWord(
            text=text, box=Box(x=0.55, y=y, width=width, height=height), confidence=0.5, line_id=line, block_id=0
        )

    words = [
        word("Address:", 0.55, 0.10, 0),
        word("W/O: Ajit Kumar Bajaj, Ward No 01, Seujpur,", 0.55, 0.20, 1),
        piece("1M", 0.26, 0.008, 0.026, 2),  # square-ish bits of the "Download Date" text along the edge
        piece("9", 0.30, 0.009, 0.027, 3),
        piece("No", 0.34, 0.040, 0.026, 4),  # a short real word is wider than tall and stays
        word("Assam - 786001", 0.55, 0.40, 5),
    ]
    reading = read_field(english, words, english.box, today=TODAY, page_aspect=3.0)
    assert reading is not None
    assert "1M" not in reading.text
    assert " 9 " not in reading.text
    assert "No" in reading.text
