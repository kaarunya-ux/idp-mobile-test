# Findings from the Aadhaar accuracy runs (written down, not fixed here)

Numbers are from [BASELINE.md](BASELINE.md) (v2, 100 cards). Values quoted are invented.

## v2

1. **Nothing is ever sent to review.** Of the 588 fields on the 98 valid-number cards, 579 were ACCEPTED, 9 had no value (status
   EMPTY) and 0 were NEEDS_REVIEW. 58 fields were wrong and ACCEPTED (10%). The only NEEDS_REVIEW fields of the whole run are the 2
   numbers with a wrong check digit. The service has no way to say "I am not sure" about a blurred or cropped read.
2. **Strong blur is the worst defect:** 68% correct, 38 wrong and ACCEPTED in 20 cards. Glare is next (79%), then crop (88%) and
   angle (90%). Clean cards read 100%.
3. **Address and pincode carry most of the errors** (address 58% correct, 34 wrong ACCEPTED; pincode 81%, 17 wrong ACCEPTED).
   Reads that were wrong and accepted include rn/m (`Marnal` as `Mamal`), cl/d (`Clarmor` as `Clamor`), `O-1` as `0-1`, `Haryana` as
   `Maryana`/`Meryana`/`Naryana`, and invented places (`Dalhousie Park, Delhi`, `Wockhardt`). The model fills in a plausible address.
4. **A cropped address is accepted.** A52, A55 and A58 lost the end of the city name (`Clarp`, `Clarpu`, `Mockaba`) and the
   comma, and were ACCEPTED. A64 (table) returned an address without its pincode.
5. **Gender comes back in a different case 21 times** (`MALE`, `FEMALE` against the printed `Male`, `Female`): correct, but not exact
   (79% exact). Name, address and the others are also a few points lower exact than correct.
6. **The number's mask has two forms:** A38 and A95 returned `XXXX XXXX0333` (no space before the last four). By the strict rule that is wrong.
   If callers accept it, the rule can be relaxed.
7. **Wrong check digit is held back correctly** in both cards (NEEDS_REVIEW, still masked).

## Gaps from run 1

- **"168 of 180 fields":** run 1 had 30 cards with 6 fields each, 180 fields. 28 cards had a valid number: 28 x 6 = 168 fields were
  scored for accuracy. The other 12 fields (2 wrong-check-digit cards x 6) were a separate class and were scored on their own
  rule (all 12 were right). Nothing was dropped. v2 reports them in their own table.
- **"2 fields with no source":** both are the Aadhaar number of the two wrong-check-digit cards (A29 and A30 in run 1). They came
  back as NEEDS_REVIEW and masked with `source` empty: the service sets no read source on a number it holds back for the check
  digit. It is not a read that failed.
- **Why run 1 was so clean:** the cards were sharp, level and well lit. v2 adds the defects that real photos have.
