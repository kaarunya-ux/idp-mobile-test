# Findings from the Aadhaar accuracy run

Written down, not fixed: this PR changes no service code. Run: 2026-11-11 against the team's `extraction-sync`, 30 invented cards.

1. **A wrong address was accepted without review (card A27, blur + slight rotation).** The service returned
   `87, Demo Colony, Testpur, Kamataka 710009` (printed: Karnataka) with status `ACCEPTED`, source `OCR_AGREE`. An `rn` read as `m`
   by the OCR engines, which agreed with each other, and the address field's `SIMILARITY` accept let it through. It is the only
   wrong value in 168 scored fields, but it is the kind that reaches a customer record unreviewed.
2. **The set is easy, so 100% is not proof.** 5 of 6 fields were read correctly on all 28 cards, including low resolution (400 px
   wide) and glare. Only blur combined with rotation produced an error. The next version of the set needs harder defects
   (heavier blur, phone-photo perspective, stains, a card photographed on a pattern, print that is faded) before a change in
   the baseline means much.
3. **Wrong check digit is held back correctly.** Both wrong-check-digit cards came back `NEEDS_REVIEW` with the number still
   masked (`XXXX XXXX` plus the printed last four digits). The other fields on those cards were accepted and correct, as expected.
4. **Values come back as printed, not normalised in case.** Names and genders return in title case (`Anil Testrao`, `Male`), the
   address with its printed commas. The scoring ignores case and punctuation for those fields, as the report says, so this is
   not an error, but a caller that compares exactly must normalise.
5. **Reading route.** 16 of 30 cards were read by OCR alone and 14 needed the AI for some fields. 165 of 180 fields came from
   `OCR_AGREE`, 13 from `AI`, 2 (the held numbers) have no source.
