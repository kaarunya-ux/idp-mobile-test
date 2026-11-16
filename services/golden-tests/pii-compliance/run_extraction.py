"""PII golden set, extraction part: the model's real values for each card go through extraction's real decision code
(OCR left every field empty, the AI value fills it), and the decision is searched for the raw value where the template's
mask policy says it must be hidden.

Run after run_gateway.py, with extraction's environment (``make pii`` does): ``python run_extraction.py``.
Exit code 1 when a field with a mask policy holds the raw value.
"""

import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # services/
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE.parent))  # services/golden-tests: the shared modules
sys.path.insert(0, str(HERE))
from documents import DOCS  # noqa: E402
from textmatch import leaks  # noqa: E402

sys.path.insert(0, str(REPO / "extraction-service" / "src"))
from extraction.pipeline.domain.decisions import Thresholds, decide_field, decide_with_ai  # noqa: E402
from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES  # noqa: E402

values = json.loads((RESULTS / "gateway_values.json").read_text())
today = date(2026, 11, 7)
thresholds = Thresholds(name_similarity=0.85, review_confidence=0.9)
rows = []
for doc, spec in DOCS.items():
    if spec["kind"] != "card":
        continue
    template = next(t for t in STANDARD_TEMPLATES if t.document_type == spec["type"])
    for field in template.fields:
        ai_value = (values.get(doc) or {}).get(field.key)
        empty = decide_field(field, {"A": None, "B": None}, thresholds=thresholds, today=today)  # OCR read nothing
        decided = decide_with_ai(field, empty, ai_value, confidence=0.8, today=today)
        text = json.dumps(dataclasses.asdict(decided), default=str)
        rows.append(
            {
                "doc": doc,
                "field": field.key,
                "mask_policy": field.mask is not None,
                "ai_value_read": ai_value is not None,
                "status": decided.status.value,
                "marked_masked": decided.masked,
                "raw_value_in_decision": bool(ai_value) and bool(leaks(text, [ai_value])),
                "shown_value": decided.value,
                "ai_reading_kept": decided.readings.get("ai"),
            }
        )
(RESULTS / "extraction_decisions.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
masked_wrong = [r for r in rows if r["mask_policy"] and r["raw_value_in_decision"]]
print(f"extraction: {len(rows)} fields, mask-policy fields holding the raw value: {len(masked_wrong)}")
sys.exit(1 if masked_wrong else 0)
