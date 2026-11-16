"""Upload validation and decoding. Content is sniffed, the filename extension is never trusted."""
import io
import os

import numpy as np
from PIL import Image, ImageOps

MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", 25))
MAX_PAGES = int(os.environ.get("OCR_MAX_PAGES", 4))
PDF_DPI = int(os.environ.get("OCR_PDF_DPI", 200))
MAX_PIXELS = 60_000_000          # reject decompression bombs / absurd scans
ALLOWED = {"JPEG", "PNG", "WEBP", "TIFF", "BMP"}

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class UploadError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def decode_upload(data):
    """bytes -> list of RGB pages (numpy HxWx3). EXIF orientation is applied. Raises UploadError."""
    if not data:
        raise UploadError("EMPTY_FILE", "The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise UploadError("FILE_TOO_LARGE", f"File is larger than {MAX_UPLOAD_MB:g} MB.", 413)
    if data[:5] == b"%PDF-":
        return _pdf_pages(data)
    try:
        im = Image.open(io.BytesIO(data))
        fmt = im.format
        if fmt not in ALLOWED:
            raise UploadError("UNSUPPORTED_FORMAT", f"Unsupported image format '{fmt}'. Use PDF, PNG, JPG, TIFF or WEBP.", 415)
        pages = []
        for i in range(getattr(im, "n_frames", 1)):
            if i >= MAX_PAGES:
                break
            im.seek(i)
            pages.append(np.array(ImageOps.exif_transpose(im.copy()).convert("RGB")))
        return pages
    except UploadError:
        raise
    except Exception:
        raise UploadError("UNREADABLE_FILE", "The file is not a readable image or PDF.", 415)


def _pdf_pages(data):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise UploadError("PDF_UNSUPPORTED", "PDF support needs the 'pypdfium2' package on the backend.", 415)
    try:
        pdf = pdfium.PdfDocument(data)
        pages = []
        for i in range(min(len(pdf), MAX_PAGES)):
            pages.append(np.array(pdf[i].render(scale=PDF_DPI / 72).to_pil().convert("RGB")))
        if not pages:
            raise ValueError
        return pages
    except Exception:
        raise UploadError("UNREADABLE_FILE", "The PDF could not be opened (corrupt or password protected).", 415)
