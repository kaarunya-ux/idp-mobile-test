# Findings from the PAN accuracy run (written down, not fixed here)

Numbers are from [BASELINE.md](BASELINE.md) (100 invented cards: 98 valid, 2 with an invalid holder type). Values quoted are invented.

1. **A PAN that breaks its own shape is ACCEPTED.** 17 of 98 PAN numbers were wrong and ACCEPTED (source OCR_AGREE), and all 17 break
   the shape the rule enforces (`AAAHA9999A`): the letter O read as a zero (`0CZPD1124C` for `OCZPD1124C`, P08) or a trailing
   letter read as a digit (`BILPL83010` for `BILPL8301O`, P10; `BMIP08886L` for `BMIPO8886L`, P13; `BOLPD50820` for `BOLPD5082O`, P33).
   A zero in a letter position cannot be a PAN, yet it was not sent to review. The full list is in the baseline.
2. **Names are held, the number is not.** Review rate is 19% overall and 58% under strong blur, mostly name (19%) and father's name
   (30%). Names are never wrong and ACCEPTED (0 of 196 fields), so the check works for names and not for the number.
3. **Strong blur is the worst defect:** 65% correct, 15 wrong and sent to review, 13 missing. Angle follows (75%). Clean cards: 92%.
4. **The date of birth depends on where it is printed.** An earlier drawing put the date of birth above the template's region (it
   starts at 82% of the card height): 82 of 98 were missing, flagged for review with no value, and the AI fallback did not rescue
   them. With the date inside the region, 84% are correct. That first result was the card layout's fault, not a service bug, but it
   shows the PAN template depends on a fixed layout; a real PAN printed differently may fail the same way.
5. **The rule checks the shape only.** The fifth letter of a real PAN is the holder's surname initial; the service does not check it,
   and these cards use the initial, so this set does not test it.
6. **An invalid holder type is held back correctly:** both invalid cards (P99, P100) went to NEEDS_REVIEW.
