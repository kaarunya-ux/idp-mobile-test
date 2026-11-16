"""Turns the latest golden results into the numbers the drift tools compare (prompt drift strategies 9, 12 and 13).

Quality is reported per class (digital, scan, hostile pages; cards), not as one average. Performance is normalised by
the work done: seconds per page, seconds per megapixel and tokens per page or card, so a different document mix does not
look like drift.
"""

import json
import statistics
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1]
CONVERSION = GOLDEN / "prompt-injection" / "conversion" / "results"
EXTRACTION = GOLDEN / "prompt-injection" / "extraction" / "results"


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def load(results: Path, name: str) -> object:
    path = results / name
    if not path.exists():
        raise SystemExit(f"{path} is missing: run the suite first (make injection-conversion / injection-extraction)")
    return json.loads(path.read_text())


def conversion(results: Path = CONVERSION) -> dict[str, dict[str, object]]:
    rows = [r for r in load(results, "golden_results.json") if r.get("valid")]  # type: ignore[union-attr]
    classes: dict[str, dict[str, object]] = {}
    for kind in sorted({r["kind"] for r in rows}):
        group = [r for r in rows if r["kind"] == kind]
        seconds = [r["seconds"] for r in group]
        classes[f"conversion/{kind}"] = {
            "pages": len(group),
            "word_recall": _mean([r["word_recall"] for r in group]),
            "cell_match": _mean([r["cell_match"] for r in group if r["cell_match"] is not None]),
            "heading_recall": _mean([r["heading_recall"] for r in group]),
            "seconds_per_page_median": _median(seconds),
            "seconds_per_page_p95": _p95(seconds),
            "seconds_per_megapixel_median": _median([r["seconds"] / r["megapixels"] for r in group if r.get("megapixels")]),
            "tokens_per_page_median": _median([r["tokens_out"] for r in group if r.get("tokens_out")]),
        }
    return classes


def extraction(results: Path = EXTRACTION) -> dict[str, dict[str, object]]:
    rows = load(results, "extraction_cards.json")
    out: dict[str, dict[str, object]] = {}
    for hints in (False, True):
        group = [r for r in rows if r["hints"] == hints]  # type: ignore[union-attr]
        normal = [r for r in group if r["case"] == "normal"]
        hostile = [r for r in group if r["case"] != "normal"]
        out[f"extraction/{'with' if hints else 'without'}_hints"] = {
            "cards": len(group),
            "normal_accuracy": _mean([r["accuracy"] for r in normal]),
            "hostile_obeyed": sum(bool(r["obeyed"]) for r in hostile),
            "seconds_per_card_median": _median([r["seconds"] for r in group if "seconds" in r]),
            "seconds_per_card_p95": _p95([r["seconds"] for r in group if "seconds" in r]),
            "tokens_per_card_median": _median([r["tokens_out"] for r in group if r.get("tokens_out")]),
        }
    return out


def summarise() -> dict[str, dict[str, object]]:
    return {**conversion(), **extraction()}


def stamps() -> dict[str, object]:
    return {"conversion": load(CONVERSION, "stamp.json"), "extraction": load(EXTRACTION, "stamp.json")}


def statuses() -> dict[str, object]:
    return {"conversion": load(CONVERSION, "status.json"), "extraction": load(EXTRACTION, "status.json")}
