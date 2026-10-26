"""Local test frontend for OCR engines (Qualcomm EasyOCR, PP-OCRv5, PP-OCRv6) on the repo's card test images.

    python app.py            # listens on 0.0.0.0:5055 (set HOST/PORT to change)

GET /api/ocr/<group>/<name>?engine=easyocr|ppocrv5|ppocrv5_hi|ppocrv6[&template=pan|aadhaar]
    -> {success, template_type, fields: {<name>: {value, status, confidence|reason}}, processing_time_ms}
GET /api/lines/<group>/<name>?engine=...   raw OCR lines + boxes (for the overlay)
GET /api/benchmark                          every engine on every image, scored against ground_truth.json
POST /extract (multipart: file, template_type=pan|aadhaar, engine=easyocr)
    -> same response format + engine (+ document_side_detected for aadhaar); used by the ocr-template-lab React frontend

Environment: HOST (0.0.0.0) PORT (5055) PUBLIC_URL CORS_ORIGINS ("*" or comma list) MAX_UPLOAD_MB (25)
             OCR_MAX_PAGES (4) OCR_PDF_DPI (200) OCR_MODEL_DIR OCR_DEBUG_DIR (opt-in dumps; contain personal data)
"""
import functools
import json
import os
import socket
import time
import uuid

import cv2
import numpy as np
from flask import Flask, abort, jsonify, request, send_file
from PIL import Image

import documents
import pipeline
from contract import build_response
from documents import UploadError
from extractor import extract, load_templates
from ocr_engine import EasyOCR, EngineUnavailable, PPOCRv5Hindi, PPOCRv6, IndraOCR

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
IMAGE_DIRS = {"aadhaar": "aadhaar_test_images", "pan": "pan_test_images"}
EXTS = (".webp", ".jpg", ".jpeg", ".png")
# "ppocrv5" is PP-OCRv5 + the Devanagari recogniser: it reads English and Hindi lines (the frontend contract needs value_hi).
ENGINES = {"easyocr": EasyOCR, "ppocrv5": PPOCRv5Hindi, "ppocrv6": PPOCRv6, "indra": IndraOCR}  # indra: optional, needs `pip install indra-ocr`
DEFAULT_ENGINE = os.environ.get("DEFAULT_ENGINE", "ppocrv5")
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
DEBUG_DIR = os.environ.get("OCR_DEBUG_DIR")

with open(os.path.join(os.path.dirname(__file__), "ground_truth.json")) as _f:
    GROUND_TRUTH = json.load(_f)

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.json.ensure_ascii = False                     # real Devanagari, not \\uXXXX escapes
app.json.mimetype = "application/json; charset=utf-8"
app.config["MAX_CONTENT_LENGTH"] = int(documents.MAX_UPLOAD_MB * 1024 * 1024) + 64 * 1024
_engines = {}


@app.after_request
def cors(resp):
    """Allow the configured frontend origin(s) to call the API, including the preflight request."""
    origin = request.headers.get("Origin")
    if "*" in CORS_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in CORS_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "*")
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def summary(spec):
    """'pan | 4/7 extracted | 4210 ms | signature=failed' for the console (no personal data)."""
    if not spec.get("success"):
        return f"FAILED: {spec['error']['message']}"
    f = spec["fields"]
    bad = [f"{k}={v['status']}" for k, v in f.items() if v["status"] != "extracted"]
    n = sum(v["status"] == "extracted" for v in f.values())
    return f"{spec['template_type']} | {n}/{len(f)} extracted | {spec['processing_time_ms']} ms" + (f" | {', '.join(bad)}" if bad else "")


def error(status, code, message):
    """Error body: {success:false, error:{code,message}}; detail/message mirrored for the frontend's error banner."""
    log(f"{request.remote_addr} {request.method} {request.path} -> {status} {code}")
    return jsonify({"success": False, "error": {"code": code, "message": message},
                    "detail": message, "message": message}), status


@app.errorhandler(UploadError)
def upload_error(e):
    code = {"EMPTY_FILE": "INVALID_FILE", "UNREADABLE_FILE": "INVALID_FILE", "UNSUPPORTED_FORMAT": "INVALID_FILE",
            "PDF_UNSUPPORTED": "INVALID_FILE"}.get(e.code, e.code)
    return error(e.status, code, e.message)


@app.errorhandler(413)
def too_large(e):
    return error(413, "FILE_TOO_LARGE", f"File is larger than {documents.MAX_UPLOAD_MB:g} MB.")


@app.errorhandler(Exception)
def unexpected(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return error(e.code, e.name.upper().replace(" ", "_"), e.description)
    app.logger.exception("unhandled error")  # stack trace stays in the server log only
    return error(500, "INTERNAL_ERROR", "The document could not be processed. Please try again.")


def ocr(engine):
    if engine not in ENGINES:
        abort(400)
    if engine not in _engines:
        _engines[engine] = ENGINES[engine]()  # may raise EngineUnavailable
    return _engines[engine]


@app.errorhandler(EngineUnavailable)
def engine_unavailable(e):
    return error(503, "ENGINE_UNAVAILABLE", str(e))


def image_path(group, name):
    if group not in IMAGE_DIRS or os.path.basename(name) != name:
        abort(404)
    p = os.path.join(ROOT, IMAGE_DIRS[group], name)
    if not os.path.isfile(p):
        abort(404)
    return p


def load_rgb(path):
    return np.array(Image.open(path).convert("RGB"))


@functools.lru_cache(maxsize=128)
def run(path, engine):
    """OCR once per (image, engine). Returns lines, (h, w), seconds."""
    img = load_rgb(path)
    t = time.time()
    lines = ocr(engine).read(img)
    return lines, img.shape[:2], round(time.time() - t, 2)


def extraction(path, engine, forced=None):
    lines, _, secs = run(path, engine)
    t = time.time()
    res = extract(lines, load_templates(), doc_type=forced or None, img=load_rgb(path))
    return res, int((secs + time.time() - t) * 1000)


def to_spec(res, ms, engine):
    """Internal result -> the API response format (see contract.py)."""
    spec = build_response(res, load_templates()[res["doc_type"]], engine, ms, languages_of(engine)) if res["doc_type"] else None
    return spec or {"success": False, "error": {"code": "DOCUMENT_NOT_RECOGNIZED", "message": res["error"]}}


def norm(v):
    return "".join(str(v or "").upper().split())


def score(path, name, engine):
    """correct = extracted and equal to the truth; wrong = extracted but not the truth (false output);
    missing = nothing extracted."""
    res, _ = extraction(path, engine)
    rows = {}
    for field, truth in GROUND_TRUTH[name].items():
        f = res["fields"].get(field)
        got = f["value"] if f and f["status"] == "ok" else None
        verdict = "missing" if got is None else "correct" if norm(got) == norm(truth) else "wrong"
        rows[field] = {"truth": truth, "got": got, "verdict": verdict}
    return rows


def languages_of(engine):
    e = ocr(engine)
    return ["en", "hi"] if getattr(e, "aux", None) or getattr(e, "hindi", False) else ["en"]


def run_document(pages, engine, template):
    """Preprocess + OCR + extraction for an upload. Returns the response spec."""
    steps = []
    res, ms = pipeline.process(pages, ocr(engine), template, steps.append)
    for m in steps:
        log(f"  {m}")
    if res is None or (not res["matched"] and pipeline.text_ok(res) < 2):
        # nothing of the template was found: a lot of other text means the wrong document, little text means unreadable
        wrong = res is not None and res["nlines"] >= 6
        label = {"pan": "PAN card", "aadhaar": "Aadhaar card"}[template]
        return {"success": False, "error": {
            "code": "WRONG_DOCUMENT_TYPE" if wrong else "UNREADABLE_DOCUMENT",
            "message": f"This does not look like a {label}. Please upload the correct document." if wrong
            else "The document is too blurry to read. Please upload a clearer image."}}
    spec = build_response(res, load_templates()[res["doc_type"]], engine, ms, languages_of(engine))
    if DEBUG_DIR:  # opt-in; the dump contains personal data
        os.makedirs(DEBUG_DIR, exist_ok=True)
        tag = uuid.uuid4().hex[:8]
        json.dump(spec, open(os.path.join(DEBUG_DIR, f"{tag}_response.json"), "w"), indent=2)
        for i, pg in enumerate(pages):
            Image.fromarray(pg).save(os.path.join(DEBUG_DIR, f"{tag}_page{i}.png"))
    return spec


@app.post("/extract")
def api_extract():
    f = request.files.get("file")
    if f is None:
        return error(400, "INVALID_FILE", "No file uploaded (multipart field 'file').")
    templates = load_templates()
    template = (request.form.get("template_type") or "").strip().lower()
    if template not in templates:
        return error(400, "UNSUPPORTED_TEMPLATE", f"template_type must be one of {sorted(templates)}.")
    engine = (request.form.get("engine") or "").strip().lower()
    if engine in ("", "default"):
        engine = DEFAULT_ENGINE
    pages = documents.decode_upload(f.read())   # raises UploadError for empty / oversize / unsupported / corrupt
    if engine == "compare":
        specs = {}
        for e in ENGINES:
            try:
                specs[e] = run_document(pages, e, template)
            except EngineUnavailable:
                pass
        if len(specs) < 2:
            return error(400, "ENGINE_UNAVAILABLE", f"Compare needs at least two working engines; available: {sorted(specs)}.")
        primary = next(iter(specs))
        resp = dict(specs[primary])
        resp["engines"] = specs
        for e, spec in specs.items():
            log(f"{request.remote_addr} /extract {len(pages)}p template={template} engine={e}: {summary(spec)}")
        return jsonify(resp)
    if engine not in ENGINES:
        return error(400, "ENGINE_UNAVAILABLE",
                     f"Engine '{engine}' is not available in this backend. Available: {sorted(ENGINES)}.")
    spec = run_document(pages, engine, template)
    log(f"{request.remote_addr} /extract {len(pages)}p template={template} engine={engine}: {summary(spec)}")
    if not spec.get("success"):
        log(f"{request.remote_addr} /extract -> 422 {spec['error']['code']}")
        return jsonify(dict(spec, detail=spec["error"]["message"], message=spec["error"]["message"])), 422
    return jsonify(spec)


def lan_ip():
    """This machine's address on the local network (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def base_url():
    """PUBLIC_URL env wins; otherwise the address the caller used, so it is right from any device."""
    return os.environ.get("PUBLIC_URL", "").rstrip("/") or request.host_url.rstrip("/")


@app.get("/api/config")
def api_config():
    base = base_url()
    return jsonify({"base_url": base, "extract_endpoint": f"{base}/extract", "lan_ip": lan_ip(),
                    "port": int(os.environ.get("PORT", 5055))})


@app.get("/")
def index():
    return send_file(os.path.join(app.static_folder, "index.html"))


@app.get("/api/images")
def images():
    return jsonify([{"group": g, "name": n}
                    for g, d in IMAGE_DIRS.items()
                    for n in sorted(os.listdir(os.path.join(ROOT, d))) if n.lower().endswith(EXTS)])


@app.get("/api/engines")
def api_engines():
    out = {}
    for e in ENGINES:
        try:
            ocr(e)
            out[e] = {"available": True}
        except EngineUnavailable as ex:
            out[e] = {"available": False, "reason": str(ex)}
    return jsonify(out)


@app.get("/img/<group>/<name>")
def img(group, name):
    ok, buf = cv2.imencode(".png", cv2.cvtColor(load_rgb(image_path(group, name)), cv2.COLOR_RGB2BGR))
    return app.response_class(buf.tobytes(), mimetype="image/png")


@app.get("/api/ocr/<group>/<name>")
def api_ocr(group, name):
    engine = request.args.get("engine", DEFAULT_ENGINE)
    res, ms = extraction(image_path(group, name), engine, request.args.get("template"))
    return jsonify(to_spec(res, ms, engine))


@app.get("/api/lines/<group>/<name>")
def api_lines(group, name):
    lines, (h, w), secs = run(image_path(group, name), request.args.get("engine", DEFAULT_ENGINE))
    return jsonify({"width": w, "height": h, "seconds": secs, "lines": [l.to_dict() for l in lines]})


@app.get("/api/benchmark")
def api_benchmark():
    totals = {}
    for e in ENGINES:
        try:
            ocr(e)
            totals[e] = {"available": True, "correct": 0, "wrong": 0, "missing": 0, "seconds": 0.0, "images": 0}
        except EngineUnavailable as ex:
            totals[e] = {"available": False, "reason": str(ex)}
    rows = []
    for g, d in IMAGE_DIRS.items():
        for n in sorted(os.listdir(os.path.join(ROOT, d))):
            if n not in GROUND_TRUTH:
                continue
            p = image_path(g, n)
            row = {"group": g, "name": n, "engines": {}}
            for e, t in totals.items():
                if not t["available"]:
                    continue
                fields, secs = score(p, n, e), run(p, e)[2]
                row["engines"][e] = {"fields": fields, "seconds": secs}
                t["images"] += 1
                t["seconds"] += secs
                for v in fields.values():
                    t[v["verdict"]] += 1
            rows.append(row)
    for t in totals.values():
        if t["available"]:
            t["seconds"] = round(t["seconds"], 2)
            t["avg_seconds"] = round(t["seconds"] / max(1, t["images"]), 2)
    return jsonify({"totals": totals, "images": rows})


if __name__ == "__main__":
    host, port = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", 5055))
    ip = lan_ip() if host in ("0.0.0.0", "") else host
    print(f"\n  Local:    http://127.0.0.1:{port}\n  Network:  http://{ip}:{port}\n"
          f"  Share this endpoint with other devices on the network:\n      http://{ip}:{port}/extract\n", flush=True)
    app.run(host=host, port=port, debug=False)
