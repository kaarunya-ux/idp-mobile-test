"""IDP Web Extraction API: PAN, Aadhaar and Indian driving licence.

    ./start.sh          (settings from .env; see .env.example)

Reads the fields of the document type the caller names, with a vision-language model on a model host
(LLAMA_URL). Test page at /, Swagger at /docs.
"""

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import extract
import llm

log = logging.getLogger("uvicorn.error")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "15")) * 1024 * 1024


# --- Response models: these are what Swagger (/docs) shows the frontend team -------------------

class PanResponse(BaseModel):
    pan_number: str | None = Field(description="10 characters, e.g. 'ABCDE1234F'.")
    name: str | None
    parent_name: str | None = Field(description="Father's or mother's name.")
    parent_relation: str | None = Field(description="'father' or 'mother'.")
    dob: str | None = Field(description="DD/MM/YYYY.")


class AadhaarResponse(BaseModel):
    aadhaar_number: str | None = Field(description="'1234 5678 9012'; masked copies keep their X's.")
    name: str | None
    dob: str | None = Field(description="DD/MM/YYYY, or YYYY when only the year of birth is printed.")
    gender: str | None = Field(description="'M', 'F' or 'O'.")
    address: str | None = Field(description="Whole address on one line, as printed.")
    pincode: str | None = Field(description="6 digits.")


class DlResponse(BaseModel):
    license_number: str | None = Field(description="As printed, with its spaces and dashes.")
    name: str | None
    dob: str | None = Field(description="As printed.")
    address: str | None = Field(description="Whole address on one line, as printed.")
    issue_date: str | None
    validity_nt: str | None = Field(description="Validity for non-transport vehicles.")
    validity_tr: str | None = Field(description="Validity for transport vehicles.")
    blood_group: str | None = Field(description="e.g. 'B+'.")
    organ_donor: str | None = Field(description="'Y' or 'N'.")
    vehicle_classes: str | None = Field(description="Comma-separated codes, e.g. 'MCWG, LMV'.")


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str = Field(description="'ok', or 'model_unavailable' when the model host cannot be reached.")
    model: str | None = Field(description="Model id reported by the model host.")


# Fictional values, in the format the API returns.
EXAMPLES = {
    "pan": {"pan_number": "ABCDE1234F", "name": "RAHUL SHARMA", "parent_name": "SURESH SHARMA",
            "parent_relation": "father", "dob": "15/06/1990"},
    "aadhaar": {"aadhaar_number": "1234 5678 9012", "name": "Priya Verma", "dob": "01/01/1992", "gender": "F",
                "address": "W/O Amit Verma, 12 MG Road, Indiranagar, Bengaluru, Karnataka - 560038",
                "pincode": "560038"},
    "dl": {"license_number": "KA01 20150012345", "name": "RAHUL SHARMA", "dob": "15-06-1990",
           "address": "12 MG Road, Indiranagar, Bengaluru 560038", "issue_date": "10-02-2015",
           "validity_nt": "09-02-2035", "validity_tr": None, "blood_group": "B+", "organ_donor": None,
           "vehicle_classes": "MCWG, LMV"},
}


def _error_doc(description: str, code: str, message: str) -> dict:
    return {"model": ErrorResponse, "description": description,
            "content": {"application/json": {"example": {"error": {"code": code, "message": message}}}}}


ERROR_RESPONSES = {
    400: _error_doc("Empty file or not a readable image (HEIC is not supported).", "INVALID_IMAGE",
                    "Could not read the image. Send a JPEG, PNG or WebP file."),
    413: _error_doc("Image larger than 15 MB.", "IMAGE_TOO_LARGE", "Image is larger than 15 MB."),
    422: _error_doc("No 'file' field in the multipart body.", "INVALID_REQUEST",
                    "Send the image as multipart/form-data in a field named 'file'."),
    500: _error_doc("Unexpected server error.", "INTERNAL_ERROR", "Something went wrong while processing the image."),
    502: _error_doc("The model failed on this image; retry once.", "MODEL_ERROR", "The model service returned an error."),
    503: _error_doc("The model host is down or unreachable.", "MODEL_UNAVAILABLE", "The model service is not reachable."),
    504: _error_doc("The model took too long.", "MODEL_TIMEOUT", "The model took too long to answer."),
}

API_DESCRIPTION = """
Send a photo or scan of an **Indian identity document** to the endpoint for its type; get its fields back as JSON.

* `POST /api/v1/pan/extract`, `POST /api/v1/aadhaar/extract`, `POST /api/v1/dl/extract`
* Body: multipart/form-data with the image in `file`. For Aadhaar and DL, the back side can go in `back`, in the
  same request; both sides are read together into one result.
* HTTP 200 returns the fields of that type; every key is always present, `null` when not printed or not readable.
* Any other status returns `{"error": {"code": ..., "message": ...}}`.
* The API trims the white margin itself. JPEG, PNG or WebP up to 15 MB; allow a 30 s client timeout.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = llm.make_client()
    app.state.client = client
    app.state.model_name = await llm.model_name(client)
    if app.state.model_name:
        log.info("Model host %s (%s). Ready.", llm.LLAMA_URL, app.state.model_name)
    else:
        # Start anyway: requests return 503 MODEL_UNAVAILABLE until the model host is up.
        log.warning("Model host %s is not reachable yet.", llm.LLAMA_URL)
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(title="IDP Web Extraction API", version="1.0.0", description=API_DESCRIPTION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(llm.ModelError)
async def model_error_handler(request: Request, e: llm.ModelError):
    return error(e.status, e.code, e.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, e: RequestValidationError):
    return error(422, "INVALID_REQUEST", "Send the image as multipart/form-data in a field named 'file'.")


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, e: Exception):
    """Anything unforeseen still answers in the API's error format; the traceback goes to the log."""
    log.exception("Unhandled error on %s", request.url.path)
    return error(500, "INTERNAL_ERROR", "Something went wrong while processing the image.")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/health", response_model=HealthResponse, summary="Is the model host up?")
async def health():
    ready = await llm.is_healthy(app.state.client)
    if ready:
        # Refreshed on every healthy check: the model host is swapped between models during testing.
        app.state.model_name = await llm.model_name(app.state.client) or app.state.model_name
    return {"status": "ok" if ready else "model_unavailable", "model": app.state.model_name}


async def _run(doc_type: str, file: UploadFile, back: UploadFile | None, debug: bool):
    start = time.perf_counter()
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    raw_back = await back.read(MAX_UPLOAD_BYTES + 1) if back is not None else None
    for data_, which in ((raw, "uploaded file"), (raw_back, "back image")):
        if data_ is None:
            continue
        if not data_:
            return error(400, "INVALID_IMAGE", f"The {which} is empty.")
        if len(data_) > MAX_UPLOAD_BYTES:
            return error(413, "IMAGE_TOO_LARGE", f"The {which} is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")

    url, size = await asyncio.to_thread(extract.prepare, raw, raw_back)
    prep_ms = round((time.perf_counter() - start) * 1000)
    fields, stats = await extract.extract(app.state.client, doc_type, url)
    total_ms = round((time.perf_counter() - start) * 1000)
    # Document type, timings and which fields were found, never their values or the file name:
    # both can hold personal data.
    log.info("extract-%s %s sides=%d %dx%d %dms prep=%dms %s found=%s", doc_type, uuid.uuid4().hex[:12],
             1 if raw_back is None else 2, size[0], size[1], total_ms, prep_ms, stats,
             ",".join(k for k, v in fields.items() if v) or "none")
    if not debug:
        return fields
    return {**fields, "meta": {"model": app.state.model_name, "processing_ms": total_ms,
                               "image": {"width": size[0], "height": size[1]},
                               "timings": {"image_prep_ms": prep_ms, **stats}}}


def _add_route(doc_type: str, label: str, model: type[BaseModel], two_sided: bool):
    responses = {200: {"model": model, "content": {"application/json": {"example": EXAMPLES[doc_type]}}},
                 **ERROR_RESPONSES}
    file_doc = (f"Photo or scan of the {label}" + (" (the front, when `back` is also sent)" if two_sided else "")
                + ": JPEG, PNG or WebP, up to 15 MB.")

    if two_sided:
        async def handler(
            file: UploadFile = File(..., description=file_doc),
            back: UploadFile | None = File(None, description="Optional: the back side of the same card, in the "
                                                             "same request. Both sides are read into one result."),
            # Internal: the test page adds ?debug=true to also get timings. Hidden from Swagger.
            debug: bool = Query(False, include_in_schema=False),
        ):
            return await _run(doc_type, file, back, debug)
    else:
        async def handler(
            file: UploadFile = File(..., description=file_doc),
            debug: bool = Query(False, include_in_schema=False),
        ):
            return await _run(doc_type, file, None, debug)

    handler.__name__ = f"extract_{doc_type}"
    app.post(f"/api/v1/{doc_type}/extract", response_model=None, summary=f"Extract the fields of a {label}",
             responses=responses)(handler)


_add_route("pan", "PAN card", PanResponse, two_sided=False)
_add_route("aadhaar", "Aadhaar card", AadhaarResponse, two_sided=True)
_add_route("dl", "Indian driving licence", DlResponse, two_sided=True)
