"""A fixed canary (prompt drift strategy 18): one small invented page read the way conversion reads pages, compared with
its known content. Quick and cheap, so it is a good first check after a deploy or a model restart.

    GOLDEN_ENV=prod python canary.py        prints one JSON line; exit code 1 when the page is not read as it should be

Needs the ai-gateway's environment and the model server. Names, numbers and times only: no document text is printed.
"""

import json
import sys
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1]
REPO = GOLDEN.parent  # services/
sys.path[:0] = [str(GOLDEN), str(Path(__file__).resolve().parent), str(REPO / "ai-gateway" / "src")]
import stamp  # noqa: E402
from ai_gateway.ai.application.answer_models import PageReadAnswer, schema_of  # noqa: E402
from ai_gateway.ai.application.services.read_page import QUESTION  # noqa: E402

import variant  # noqa: E402
from drawing import Sheet  # noqa: E402
from model import ask  # noqa: E402
from textmatch import norm, words_of  # noqa: E402

MIN_RECALL = 0.99


def main() -> int:
    page = Sheet()
    page.heading("Notice of Closure", 30, centre=True)
    page.para("The office will remain closed on Monday for the annual stock audit.")
    prompt = variant.load_prompts().get("page-read")
    result = ask(prompt.system, QUESTION, page.img, schema_of(PageReadAnswer), prompt.max_tokens)
    problems = []
    recall = 0.0
    if not result["ok"]:
        problems.append(f"the model call failed ({result.get('error')})")
    else:
        blocks = json.loads(result["content"])["blocks"]
        got = [w for b in blocks for w in words_of(str(b.get("text") or ""))]
        recall = sum(w in got for w in page.words) / len(page.words)
        if recall < MIN_RECALL:
            problems.append(f"word recall {recall:.2f} < {MIN_RECALL}")
        if not any(b.get("kind") == "heading" and norm(b.get("text")) == norm(page.headings[0]) for b in blocks):
            problems.append("the title is not read as a heading")
    print(
        json.dumps(
            {
                "canary": "ok" if not problems else "FAIL",
                "env": stamp.environment_name(),
                "recall": round(recall, 3),
                "seconds": result["seconds"],
                "tokens_out": result.get("tokens_out"),
                "prompt": f"{prompt.name}.v{prompt.version}",
                "server_fingerprint": result.get("server_fingerprint"),
                "problems": problems,
            }
        )
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
