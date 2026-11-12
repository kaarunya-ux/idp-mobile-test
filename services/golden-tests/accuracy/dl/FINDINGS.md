# Findings from the driving licence accuracy run (written down, not fixed here)

Numbers are from [BASELINE.md](BASELINE.md) (100 invented cards: 49 sent as one side-by-side picture, 49 as a front and a back
picture, plus 2 whose number has a short serial). Only the number, name and address are scored. Values quoted are invented.

1. **Address is the weakest field: 10% correct, 57 wrong and ACCEPTED, 31 missing.** 72 of 294 fields are wrong and ACCEPTED.
2. **Side by side: the front's header is read into the address.** In 33 of the 57 wrong addresses the value starts with
   `Transport Department - Union of India (TEST SAMPLE)` and was ACCEPTED (for example D01, D03), because the service reads rows across both
   sides of one picture. The other 24 are misreads, for example D21 `1/10, BEND COLONY, LALMER, TELANGANA 410005` for `1/10, DEMO COLONY, LALMOR, TELANGANA 410009` (three words and the pincode wrong).
3. **Front and back as two pictures: the back is barely used.** The address came back missing on 29 of the 49 two-picture cards
   (against 2 of 49 side by side). The endpoint accepts the `back` picture, but the results do not show it reading it for a
   licence. Observed, not diagnosed.
4. **Letters OCR confuses are accepted in names:** 15 names wrong and ACCEPTED, for example `LALIT DOUBAL` for `LALIT DOLWAL`,
   `DELNAZ MORNANI` for `DILNAZ MORNANI`, `CLARIBEL OORANI` for `CLARIBEL OLWANI`, `KAMAL LORHADE` for `KAMAL LOMHADE`.
5. **Strong blur:** 17% correct, 29 wrong and ACCEPTED, 9 sent to review (review rate 38%). Every other defect sits at 58-67%, and
   even the 10 clean cards only reach 67% because of the address.
6. **The number keeps its space.** 81% correct but 47% exact: in 33 cases the service returned `TN32 20160419911` where the
   template says spaces are removed (expected `TN3220160419911`). The value is right; the form is not what the template promises.
7. **A short-serial number is held back correctly:** both invalid cards (D99, D100) went to NEEDS_REVIEW. D99's address was still
   wrong and ACCEPTED (the header problem above).
8. **Review is rare outside the number:** 9% of fields overall, and 0% of addresses, the field that is wrong most often.
