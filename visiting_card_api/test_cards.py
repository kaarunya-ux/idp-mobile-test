"""Send every image in a folder to the running API and print what came back.

    .venv/bin/python visiting_card_api/test_cards.py [images_dir] [--mode MODE] [--api URL]

MODE is how the image is prepared before upload:
  raw                  send the file untouched; the API crops to the card itself (default)
  1600 / 1280 / 1024   shrink so the long edge is at most that many pixels
  crop                 crop to the card here (card_crop.py); falls back to the whole photo
The 1600/1280/1024/crop modes were for choosing the image size before the API cropped on its own.
Writes the full responses to visiting_card_api/logs/test_<mode>.json.
"""

import argparse
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from card_crop import crop_to_card  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("images", nargs="?", default="images")
ap.add_argument("--mode", default="raw")
ap.add_argument("--api", default="http://127.0.0.1:8000")
args = ap.parse_args()
url = args.api + "/api/v1/visiting-card/extract?debug=true"
out = HERE / "logs" / f"test_{args.mode}.json"


def prepare(path: Path) -> tuple[bytes, str]:
    if args.mode == "raw":
        return path.read_bytes(), ""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    note = ""
    if args.mode == "crop":
        img, cropped = crop_to_card(img)
        note = "" if cropped else " (no crop)"
    else:
        edge = int(args.mode)
        img.thumbnail((edge, edge), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue(), note


results = []
for path in sorted(p for p in Path(args.images).iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}):
    data, note = prepare(path)
    r = httpx.post(url, files={"file": (path.name, data, "image/jpeg")}, timeout=180)
    raw_body = r.json()
    # Saved in one shape (success/data/meta/error) so score.py reads old and new runs alike.
    if r.status_code == 200:
        meta = raw_body.pop("meta")
        body = {"success": True, "data": raw_body, "meta": meta}
    else:
        body = {"success": False, "error": raw_body.get("error")}
    results.append({"file": path.name, "note": note.strip(), "response": body})
    if body.get("success"):
        d, m = body["data"], body["meta"]
        t = m["timings"]
        print(f"{path.name} {m['processing_ms'] / 1000:5.1f}s  {m['image']['width']}x{m['image']['height']}"
              f"  read {t['prompt_tokens']} tok {t['prompt_ms'] / 1000:.1f}s  wrote {t['output_tokens']} tok"
              f" {t['output_ms'] / 1000:.1f}s{note}{'' if m['image'].get('cropped_to_card', True) else ' (API: no crop)'}")
        for k in ("name", "email", "country_code", "phone", "address"):
            print(f"    {k:12} {d.get(k)}")
    else:
        print(f"{path.name}  ERROR {body.get('error')}")
    sys.stdout.flush()

ok = [r["response"]["meta"]["processing_ms"] for r in results if r["response"].get("success")]
if ok:
    print(f"\n[{args.mode}] {len(ok)}/{len(results)} succeeded, avg {sum(ok) / len(ok) / 1000:.1f}s, "
          f"min {min(ok) / 1000:.1f}s, max {max(ok) / 1000:.1f}s")
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"Full responses: {out}")
