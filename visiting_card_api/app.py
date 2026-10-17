"""Visiting-card extraction API.

Run from the repo root:  ./visiting_card_api/start.sh
Starts llama-server (Qwen3-VL) on 127.0.0.1:8081 unless one is already running there,
warms it up, then serves the API and test page on 0.0.0.0:8000.
"""

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import model

log = logging.getLogger("uvicorn.error")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "15")) * 1024 * 1024


# --- Response models: these are what Swagger (/docs) shows the frontend team -------------------

class CardData(BaseModel):
    name: str | None = Field(description="Person's full name as printed. null if no person's name is on the card.")
    email: str | None = Field(description="Person's email as printed. null if none.")
    country_code: str | None = Field(description="Country calling code of `phone`, e.g. '+244'. null if no code is printed.")
    phone: str | None = Field(description="One number as printed without its country code, mobile preferred, never fax. null if none.")
    address: str | None = Field(description="Full postal address on one line, parts joined by ', '. null if none.")


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str = Field(description="'ok', or 'model_unavailable' when the model server is down.")
    model: str


SUCCESS_EXAMPLE = {"name": "Jane Doe", "email": "jane.doe@example.com",
                   "country_code": "+44", "phone": "20 7946 0958", "address": None}


def _error_doc(description: str, code: str, message: str) -> dict:
    return {"model": ErrorResponse, "description": description,
            "content": {"application/json": {"example": {"error": {"code": code, "message": message}}}}}


ERROR_RESPONSES = {
    400: _error_doc("Empty file or not a readable image (HEIC is not supported).", "INVALID_IMAGE",
                    "Could not read the image. Send a JPEG, PNG or WebP file."),
    413: _error_doc("Image larger than 15 MB.", "IMAGE_TOO_LARGE", "Image is larger than 15 MB."),
    422: _error_doc("No 'file' field in the multipart body.", "INVALID_REQUEST",
                    "Send the image as multipart/form-data in a field named 'file'."),
    502: _error_doc("The model failed on this image; retry once.", "MODEL_ERROR", "Model server returned 500: ..."),
    503: _error_doc("The model server is down.", "MODEL_UNAVAILABLE", "Model server is not reachable: ..."),
    504: _error_doc("The model took over 2 minutes.", "MODEL_TIMEOUT", "The model took too long to answer."),
}

API_DESCRIPTION = """
Send a photo of a visiting card; get back the person's **name, email, phone (with country code separate) and address** as JSON.

* One request = one extraction: `POST /api/v1/visiting-card/extract` with the image as multipart field `file`.
* HTTP 200 returns exactly five keys: `name`, `email`, `country_code`, `phone`, `address`. A field not printed on the card is `null`.
* Any other status returns `{"error": {"code": ..., "message": ...}}`.
* Photograph the card lying on a desk; the API finds and crops the card itself.
* Typical time 4-5 s per card; requests are handled one at a time, so allow a 30 s client timeout.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient(base_url=model.LLAMA_URL, timeout=httpx.Timeout(120, connect=5))
    proc = None
    try:
        if await model.is_healthy(client):
            log.info("Using llama-server already running at %s", model.LLAMA_URL)
        else:
            log.info("Starting llama-server with %s", model.MODEL_PATH.name)
            proc = model.start_server()
            await model.wait_until_healthy(client, proc)
        t = time.perf_counter()
        try:
            await model.warm_up(client)
            log.info("Model warm (%.1fs). Ready.", time.perf_counter() - t)
        except model.ModelError as e:
            log.warning("Warm-up failed, first request will be slower: %s", e.message)
        app.state.client = client
        yield
    finally:
        await client.aclose()
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


app = FastAPI(title="Visiting Card Extraction API", version="1.0.0", description=API_DESCRIPTION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(model.ModelError)
async def model_error_handler(request: Request, e: model.ModelError):
    return error(e.status, e.code, e.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, e: RequestValidationError):
    return error(422, "INVALID_REQUEST", "Send the image as multipart/form-data in a field named 'file'.")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(model.ROOT / "static/index.html")


@app.get("/health", response_model=HealthResponse, summary="Is the model up?")
async def health():
    ready = await model.is_healthy(app.state.client)
    return {"status": "ok" if ready else "model_unavailable", "model": model.MODEL_NAME}


@app.post(
    "/api/v1/visiting-card/extract",
    response_model=None,
    summary="Extract name, email, phone and address from a visiting card photo",
    responses={200: {"model": CardData, "content": {"application/json": {"example": SUCCESS_EXAMPLE}}},
               **ERROR_RESPONSES},
)
async def extract_card(
    file: UploadFile = File(..., description="Visiting card photo: JPEG, PNG or WebP, up to 15 MB. Not HEIC."),
    # Internal: our test page and test scripts add ?debug=true to also get timings. Hidden from Swagger.
    debug: bool = Query(False, include_in_schema=False),
):
    start = time.perf_counter()
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        return error(400, "INVALID_IMAGE", "The uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        return error(413, "IMAGE_TOO_LARGE", f"Image is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")

    image_url, width, height, cropped = await asyncio.to_thread(model.prepare_image, raw)
    prep_ms = round((time.perf_counter() - start) * 1000)
    data, stats = await model.extract(app.state.client, image_url)
    total_ms = round((time.perf_counter() - start) * 1000)

    request_id = uuid.uuid4().hex[:12]
    log.info("extract %s %s %dx%d crop=%s %dms prep=%dms read=%s/%sms wrote=%s/%sms %s",
             request_id, file.filename, width, height, cropped, total_ms, prep_ms,
             stats["prompt_tokens"], stats["prompt_ms"], stats["output_tokens"], stats["output_ms"], data)
    if not debug:
        return data
    return {
        **data,
        "meta": {
            "request_id": request_id,
            "model": model.MODEL_NAME,
            "processing_ms": total_ms,
            "image": {"width": width, "height": height, "cropped_to_card": cropped},
            "timings": {"image_prep_ms": prep_ms, **stats},
        },
    }
