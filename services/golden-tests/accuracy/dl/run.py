"""Sends the 100 invented driving licence cards to ``POST /api/v1/extract`` and scores every field (correct, wrong ACCEPTED, wrong NEEDS_REVIEW, missing).

    EVAL_EXTRACTION_URL=http://host:8003 python accuracy/dl/run.py [--only D01,D02]

No default address: set ``EVAL_EXTRACTION_URL`` to the base URL of an ``extraction-sync``. ``EVAL_TOKEN``, when set, is sent as
``Authorization: Bearer`` (the local stack needs none); it is never written anywhere. Pictures go to ``cards/`` and the
scored answers to ``results/``, both git-ignored. Then run ``report.py``.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from dl.card_set import build_cards  # noqa: E402
from dl.draw import pictures  # noqa: E402
from dl.score import score_card  # noqa: E402

TIMEOUT_SECONDS = 180.0


def send(client: httpx.Client, paths: list[Path]) -> tuple[int, dict | None, float]:
    """One picture is the front; a second is the back of the same document."""
    started = time.monotonic()
    handles = [path.open("rb") for path in paths]
    try:
        files = {name: (path.name, picture, "image/png") for name, path, picture in zip(("file", "back"), paths, handles, strict=False)}
        reply = client.post("/api/v1/extract", files=files)
    finally:
        for picture in handles:
            picture.close()
    body = reply.json() if reply.headers.get("content-type", "").startswith("application/json") else None
    return reply.status_code, body, round(time.monotonic() - started, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated card ids, for a quick try")
    args = parser.parse_args()
    url = os.environ.get("EVAL_EXTRACTION_URL")
    if not url:
        sys.stderr.write("Set EVAL_EXTRACTION_URL to the base URL of extraction-sync (no default address).\n")
        return 2
    token = os.environ.get("EVAL_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    wanted = set(args.only.split(",")) if args.only else None
    (HERE / "cards").mkdir(exist_ok=True)
    (HERE / "results").mkdir(exist_ok=True)
    rows = []
    with httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=TIMEOUT_SECONDS) as client:
        for spec in build_cards():
            if wanted and spec.card_id not in wanted:
                continue
            paths = []
            for number, picture in enumerate(pictures(spec)):
                paths.append(HERE / "cards" / f"{spec.card_id}{'_back' if number else ''}.png")
                picture.save(paths[-1])
            try:
                status, body, seconds = send(client, paths)
            except httpx.HTTPError as exc:
                status, body, seconds = 0, None, 0.0
                sys.stderr.write(f"{spec.card_id}: {type(exc).__name__}\n")
            fields = score_card(spec.truth(), body if status == 200 else None, spec.group)
            rows.append(
                {
                    "card": spec.card_id,
                    "group": spec.group,
                    "font": spec.font,
                    "layout": spec.layout,
                    "effects": list(spec.effects),
                    "truth": spec.truth(),
                    "http": status,
                    "document_type": (body or {}).get("documentType"),
                    "read_by": (body or {}).get("readBy"),
                    "seconds": seconds,
                    "fields": fields,
                }
            )
            sys.stdout.write(f"{spec.card_id} {spec.group:14} http {status} " + " ".join(f"{k[:4]}={v['outcome'][:7]}" for k, v in fields.items()) + "\n")
    (HERE / "results" / "dl_results.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
