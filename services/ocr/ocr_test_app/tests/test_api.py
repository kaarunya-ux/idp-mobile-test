"""Tests for the /extract backend.

    python -m unittest discover -s tests -v                 # everything (real model inference, ~3 min)
    OCR_SKIP_INFERENCE=1 python -m unittest discover -s tests   # only the tests that need no model

Classes named *Unit* never run OCR. Classes named *Inference* run the real EasyOCR ONNX models on the
repo's test images (pan_test_images/, aadhaar_test_images/) and compare against values read from the images.
"""
import io
import os
import sys
import unittest

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as A  # noqa: E402
import contract  # noqa: E402
import extractor  # noqa: E402

ROOT = A.ROOT
PAN = os.path.join(ROOT, "pan_test_images")
AAD = os.path.join(ROOT, "aadhaar_test_images")
SKIP_INFERENCE = bool(os.environ.get("OCR_SKIP_INFERENCE"))
PAN_KEYS = ["pan_number", "name", "father_name", "dob", "photo", "signature", "qr_code"]
AADHAAR_KEYS = ["name", "dob_or_yob", "gender", "aadhaar_number", "enrolment_number", "photo", "qr_code",
                "care_of", "address", "pincode"]
FIELD_KEYS = {"value_en", "value_hi", "status", "reason"}  # + optional "confidence"
DEFAULT_ENGINE = A.DEFAULT_ENGINE


def post(client, data, filename="card.png", **form):
    body = {k: v for k, v in form.items() if v is not None}
    if data is not None:
        body["file"] = (io.BytesIO(data), filename)
    return client.post("/extract", data=body, content_type="multipart/form-data")


def png(arr):
    b = io.BytesIO()
    Image.fromarray(arr).save(b, "PNG")
    return b.getvalue()


def read(path):
    with open(path, "rb") as f:
        return f.read()


def check_contract(tc, j, template, keys, engine=DEFAULT_ENGINE):
    tc.assertTrue(j["success"])
    tc.assertEqual(j["template_type"], template)
    tc.assertEqual(j["engine"], engine)
    tc.assertIsInstance(j["processing_time_ms"], int)
    tc.assertEqual(sorted(j["fields"]), sorted(keys))
    for k, f in j["fields"].items():
        tc.assertEqual(set(f) - {"confidence"}, FIELD_KEYS, k)
        tc.assertIn(f["status"], ("extracted", "failed", "not_available"), k)
        for v in (f["value_en"], f["value_hi"], f["reason"]):
            tc.assertIsInstance(v, str, k)                    # never null
        if "confidence" in f:
            tc.assertIsInstance(f["confidence"], float, k)
            tc.assertTrue(0 < f["confidence"] <= 1, k)
        if f["status"] == "extracted":
            tc.assertEqual(f["reason"], "", k)
            if k in ("photo", "signature", "qr_code"):
                tc.assertEqual(f["value_en"], "Detected", k)
            else:
                tc.assertTrue(f["value_en"] or f["value_hi"], k + " extracted but empty")
        else:  # failed / not_available never carry a value and always explain themselves
            tc.assertEqual((f["value_en"], f["value_hi"]), ("", ""), k)
            tc.assertTrue(f["reason"], k)
            tc.assertNotIn("confidence", f, k)


class UnitContractTests(unittest.TestCase):
    """No OCR: status mapping rules."""

    def spec(self, name="name", meta=None, doc="pan", side=None, **r):
        base = {"status": "ok", "value": "X", "value_hi": "", "conf": 0.9, "reason": "", "absent": False}
        return contract.field_spec(name, meta or {"required": True}, {**base, **r}, doc, side)

    def test_extracted_has_both_languages_and_confidence(self):
        f = self.spec(value="RAHUL KUMAR", value_hi="राहुल कुमार")
        self.assertEqual(f, {"value_en": "RAHUL KUMAR", "value_hi": "राहुल कुमार", "status": "extracted",
                             "reason": "", "confidence": 0.9})

    def test_hindi_only_counts_as_extracted(self):
        f = self.spec(status="missing", value=None, value_hi="राहुल कुमार", conf_hi=0.8)
        self.assertEqual((f["status"], f["value_en"], f["value_hi"]), ("failed", "", ""))  # needs status ok from hindi.py

    def test_visual_and_aadhaar_number_formatting(self):
        self.assertEqual(self.spec("photo", value="")["value_en"], "Detected")
        self.assertEqual(self.spec("aadhaar_number", doc="aadhaar", value="612487298241")["value_en"], "6124 8729 8241")
        self.assertEqual(self.spec("aadhaar_number", doc="aadhaar", value="XXXX XXXX 2222")["value_en"], "XXXX XXXX 2222")

    def test_rejected_value_is_dropped_with_friendly_reason(self):
        f = self.spec(status="rejected", value="BAD", reason="Verhoeff checksum failed")
        self.assertEqual((f["value_en"], f["status"], f["reason"]), ("", "failed", "Aadhaar checksum invalid"))

    def test_back_fields_not_available_on_front_only(self):
        f = self.spec("address", {"required": True, "side": "back"}, "aadhaar", "front",
                      status="missing", value=None, reason="address label not found")
        self.assertEqual((f["status"], f["reason"]), ("not_available", "Back side not provided"))

    def test_missing_required_is_failed_optional_is_not_available(self):
        m = dict(status="missing", value=None, reason="")
        self.assertEqual(self.spec(meta={"required": True}, **m)["status"], "failed")
        self.assertEqual(self.spec("care_of", {"required": False}, "aadhaar", **m)["reason"], "Not printed on this card")
        self.assertEqual(self.spec("qr_code", {"required": False}, "pan", **m)["reason"], "Older PAN without QR")

    def test_masked_aadhaar_and_checksum(self):
        self.assertIsNone(extractor.validate("aadhaar", "XXXX XXXX 1234"))
        self.assertIsNone(extractor.validate("aadhaar", "612487298241"))
        self.assertIsNotNone(extractor.validate("aadhaar", "612487298242"))  # one digit off
        self.assertEqual(extractor.fix_pan_positional("FCLPMOOO1F"), "FCLPM0001F")

    def test_address_cleanup(self):
        import hindi
        self.assertEqual(hindi.clean_pin_and_care_of(["S/O: Ramesh Kumar,", "12 Gandhi Nagar, Lucknow", "PIN Code: 226001,"]),
                         "12 Gandhi Nagar, Lucknow")
        self.assertEqual(hindi.clean_pin_and_care_of(["आत्मज: रमेश कुमार, मकान 12", "लखनऊ, उत्तर प्रदेश", "226001"]),
                         "मकान 12 लखनऊ, उत्तर प्रदेश")


class UnitRequestTests(unittest.TestCase):
    """No OCR: request validation, error shape, CORS."""

    @classmethod
    def setUpClass(cls):
        cls.c = A.app.test_client()
        cls.good = png(np.full((100, 160, 3), 200, np.uint8))

    def err(self, r, status, code):
        j = r.get_json()
        self.assertEqual(r.status_code, status, j)
        self.assertFalse(j["success"])
        self.assertEqual(j["error"]["code"], code)
        self.assertTrue(j["error"]["message"] and j["detail"])  # detail is what the React banner shows
        self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_no_file(self):
        self.err(post(self.c, None, template_type="pan"), 400, "INVALID_FILE")

    def test_empty_file(self):
        self.err(post(self.c, b"", template_type="pan"), 400, "INVALID_FILE")

    def test_bad_template(self):
        self.err(post(self.c, self.good, template_type="passport"), 400, "UNSUPPORTED_TEMPLATE")
        self.err(post(self.c, self.good), 400, "UNSUPPORTED_TEMPLATE")

    def test_unsupported_engines(self):
        for e in ("doctr", "tesseract", "ppocrv4"):
            self.err(post(self.c, self.good, template_type="pan", engine=e), 400, "ENGINE_UNAVAILABLE")

    def test_garbage_and_extension_is_not_trusted(self):
        self.err(post(self.c, b"this is not an image", "card.jpg", template_type="pan"), 415, "INVALID_FILE")
        self.err(post(self.c, b"%PDF-1.4 broken", "card.png", template_type="pan"), 415, "INVALID_FILE")

    def test_unsupported_format(self):
        b = io.BytesIO()
        Image.new("RGB", (20, 20)).save(b, "GIF")
        self.err(post(self.c, b.getvalue(), "card.png", template_type="pan"), 415, "INVALID_FILE")

    def test_oversize(self):
        old = A.documents.MAX_UPLOAD_MB
        A.documents.MAX_UPLOAD_MB = 0.00005  # ~52 bytes
        try:
            self.err(post(self.c, self.good, template_type="pan"), 413, "FILE_TOO_LARGE")
        finally:
            A.documents.MAX_UPLOAD_MB = old

    def test_cors_preflight_and_headers(self):
        r = self.c.options("/extract", headers={"Origin": "http://10.0.3.40:5173",
                                                "Access-Control-Request-Method": "POST",
                                                "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("POST", r.headers["Access-Control-Allow-Methods"])
        r = post(self.c, b"", template_type="pan")  # errors carry CORS headers too, or the browser hides them
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")

    def test_decode_applies_exif_and_reads_multipage_tiff_and_pdf(self):
        arr = np.zeros((40, 80, 3), np.uint8)
        ex = Image.Exif()
        ex[0x0112] = 6  # rotate 90 cw when displayed
        b = io.BytesIO()
        Image.fromarray(arr).save(b, "JPEG", exif=ex)
        self.assertEqual(A.documents.decode_upload(b.getvalue())[0].shape[:2], (80, 40))
        b = io.BytesIO()
        Image.fromarray(arr).save(b, "TIFF", save_all=True, append_images=[Image.fromarray(arr)])
        self.assertEqual(len(A.documents.decode_upload(b.getvalue())), 2)
        b = io.BytesIO()
        Image.fromarray(arr).save(b, "PDF")
        self.assertEqual(len(A.documents.decode_upload(b.getvalue())), 1)


@unittest.skipIf(SKIP_INFERENCE, "OCR_SKIP_INFERENCE set")
class InferencePanTests(unittest.TestCase):
    """Real EasyOCR inference on PAN images."""

    @classmethod
    def setUpClass(cls):
        cls.c = A.app.test_client()

    def run_pan(self, data, **kw):
        r = post(self.c, data, template_type="pan", **kw)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        check_contract(self, j, "pan", PAN_KEYS, kw.get("engine", DEFAULT_ENGINE))
        return j["fields"]

    def values(self, f):
        return {k: f[k]["value_en"] for k in ("pan_number", "name", "father_name", "dob")}

    def test_clear_real_pan(self):
        f = self.run_pan(read(os.path.join(PAN, "1aea9462babd9fb2fd84f7a2f81f5ccf.webp")))
        self.assertEqual(self.values(f), {"pan_number": "OMRPS9699G", "name": "ROSHAN SAHANI",
                                          "father_name": "RAMESH SAHANI", "dob": "27/02/2002"})
        self.assertEqual(f["qr_code"]["status"], "not_available")  # PAN QR is optional: absent -> not_available

    def test_english_names_with_default_engine(self):
        f = self.run_pan(read(os.path.join(PAN, "synthetic_pan_02_long_names.webp")))  # no engine field: backend default
        self.assertEqual(f["name"]["value_en"], "PRIYADARSHINI VENKATESWARAN")
        self.assertEqual(f["father_name"]["value_en"], "RAMAKRISHNAN IYER")

    def test_blur_and_low_contrast(self):
        for n, want in (("synthetic_pan_04_blur.webp", "NEHA SHARMA"), ("synthetic_pan_05_dark_noise.webp", "TARUN PATEL")):
            self.assertEqual(self.run_pan(read(os.path.join(PAN, n)))["name"]["value_en"], want)

    def test_rotated_and_perspective_photos(self):
        for n, want in (("synthetic_pan_03_rotated.webp", "CDEPT4096G"), ("synthetic_pan_06_perspective.webp", "FKLPD8153C")):
            f = self.run_pan(read(os.path.join(PAN, n)))
            self.assertEqual(f["pan_number"]["value_en"], want)
            self.assertEqual(f["dob"]["status"], "extracted")

    def test_turned_card_and_exif_phone_photo(self):
        src = np.array(Image.open(os.path.join(PAN, "synthetic_pan_01_clean.webp")).convert("RGB"))
        for k in (1, 2, 3):  # 90 / 180 / 270 degrees
            f = self.run_pan(png(np.rot90(src, k).copy()))
            self.assertEqual(f["pan_number"]["value_en"], "ABCPK1234E", f"rot {k * 90}")
        ex = Image.Exif()
        ex[0x0112] = 6  # sensor stored sideways, tag says "rotate 90 cw to view"
        b = io.BytesIO()
        Image.fromarray(np.rot90(src, 1).copy()).save(b, "JPEG", exif=ex)
        self.assertEqual(self.run_pan(b.getvalue())["name"]["value_en"], "ARAVIND KUMAR")

    def test_card_small_in_large_photo(self):
        src = np.array(Image.open(os.path.join(PAN, "synthetic_pan_01_clean.webp")).convert("RGB"))
        bg = np.full((3000, 4000, 3), (110, 85, 60), np.uint8)
        cw = 1800
        card = cv2.resize(src, (cw, int(src.shape[0] * cw / src.shape[1])))
        bg[900:900 + card.shape[0], 1100:1100 + cw] = card
        f = self.run_pan(png(bg))
        self.assertEqual(self.values(f)["pan_number"], "ABCPK1234E")
        self.assertEqual(f["name"]["value_en"], "ARAVIND KUMAR")

    def test_pdf_upload(self):
        b = io.BytesIO()
        Image.open(os.path.join(PAN, "synthetic_pan_01_clean.webp")).convert("RGB").save(b, "PDF", resolution=150)
        self.assertEqual(self.run_pan(b.getvalue())["pan_number"]["value_en"], "ABCPK1234E")

    def test_partial_success_is_honest(self):
        f = self.run_pan(read(os.path.join(PAN, "synthetic_pan_07_low_res.webp")))  # 480x300: PAN number unreadable
        for k, v in f.items():
            self.assertNotEqual((v["status"], v["value_en"]), ("extracted", ""), k) if k in ("name", "dob") else None
        self.assertEqual(f["name"]["value_en"], "VIKRAM SINGH RATHORE")
        if f["pan_number"]["status"] != "extracted":  # must say so rather than invent a number
            self.assertEqual(f["pan_number"]["status"], "failed")

    def test_non_card_image_is_rejected_not_invented(self):
        noise = np.random.RandomState(0).randint(0, 255, (400, 600, 3), dtype=np.uint8)
        r = post(self.c, png(noise), template_type="pan")
        self.assertEqual(r.status_code, 422)
        self.assertIn(r.get_json()["error"]["code"], ("UNREADABLE_DOCUMENT", "WRONG_DOCUMENT_TYPE"))

    def test_aadhaar_uploaded_as_pan_is_wrong_document(self):
        r = post(self.c, read(os.path.join(AAD, "e3db3076cbf416d7c82d6bef8c5ef030.webp")), template_type="pan")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (422, "WRONG_DOCUMENT_TYPE"))


@unittest.skipIf(SKIP_INFERENCE, "OCR_SKIP_INFERENCE set")
class InferenceAadhaarTests(unittest.TestCase):
    """Real EasyOCR inference on Aadhaar images."""

    @classmethod
    def setUpClass(cls):
        cls.c = A.app.test_client()

    def run_aadhaar(self, name, engine=None):
        r = post(self.c, read(os.path.join(AAD, name)), template_type="aadhaar", engine=engine)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        j = r.get_json()
        check_contract(self, j, "aadhaar", AADHAAR_KEYS, engine or DEFAULT_ENGINE)
        self.assertEqual(j["languages"], ["en"] if engine == "ppocrv6" else ["en", "hi"])
        return j

    def test_front_only(self):
        j = self.run_aadhaar("74b878dcb675f764b87dfbb799818481.webp", engine="ppocrv6")  # default engine misses this card's number
        self.assertEqual(j["document_side_detected"], "front")
        f = j["fields"]
        self.assertEqual((f["name"]["value_en"], f["dob_or_yob"]["value_en"], f["gender"]["value_en"], f["aadhaar_number"]["value_en"]),
                         ("Jasjot Singh", "08/01/2000", "Male", "5282 7048 2687"))
        for k in ("enrolment_number", "care_of", "address", "pincode"):  # back-side fields: not a failure
            self.assertEqual(f[k]["status"], "not_available", k)
            self.assertEqual(f[k]["reason"], "Back side not provided", k)

    def test_front_and_back_in_one_image(self):
        j = self.run_aadhaar("e3db3076cbf416d7c82d6bef8c5ef030.webp")
        self.assertEqual(j["document_side_detected"], "both")
        f = j["fields"]
        self.assertEqual(f["pincode"]["value_en"], "400102")
        self.assertEqual(f["enrolment_number"]["value_en"], "2722/41021/31687")
        self.assertEqual(f["care_of"]["value_en"], "C/O Zaheer Kadiwala")
        self.assertNotIn("400102", f["address"]["value_en"])  # address is one string without the PIN
        self.assertNotIn("Zaheer", f["address"]["value_en"])  # ... and without the care-of lead-in
        self.assertIn("Jogeshwari", f["address"]["value_en"])

    def test_two_panels_english_and_hindi_text(self):
        j = self.run_aadhaar("9a0167ef10d1a58334994e590fe66f59.webp")  # Marathi + English panels
        f = j["fields"]
        self.assertEqual(f["pincode"]["value_en"], "411006")
        self.assertEqual(f["aadhaar_number"]["value_en"], "8538 7535 4900")
        self.assertEqual(f["dob_or_yob"]["value_en"], "01/07/1974")

    def test_hindi_values_are_real_devanagari(self):
        import unicodedata
        j = self.run_aadhaar("masked_sample_front_back.webp")
        f = j["fields"]
        self.assertEqual((f["name"]["value_en"], f["name"]["value_hi"]), ("RAHUL KUMAR", "राहुल कुमार"))
        self.assertEqual(f["aadhaar_number"]["value_en"], "XXXX XXXX 2222")
        self.assertEqual(f["pincode"]["value_en"], "226001")
        self.assertEqual(f["care_of"]["value_en"], "S/O SURESH KUMAR")
        for k, v in f.items():
            self.assertEqual(v["value_hi"], unicodedata.normalize("NFC", v["value_hi"]), k)
            self.assertFalse(any("A" <= c.upper() <= "Z" for c in v["value_hi"]) and k != "care_of", k)  # no English in value_hi

    def test_marathi_card_splits_the_two_scripts(self):
        j = self.run_aadhaar("9a0167ef10d1a58334994e590fe66f59.webp")
        f = j["fields"]
        self.assertEqual((f["gender"]["value_en"], f["gender"]["value_hi"]), ("Male", "पुरुष"))
        self.assertEqual(f["name"]["value_hi"], "प्रकाश गोपीचंद राठोड")
        self.assertEqual(f["care_of"]["value_en"], "S/O Gopichand Rathod")
        self.assertNotIn("411006", f["address"]["value_en"])
        self.assertFalse(any("\u0900" <= c <= "\u097f" for c in f["address"]["value_en"]))  # no Devanagari in value_en

    def test_response_is_utf8_without_escapes(self):
        r = post(self.c, read(os.path.join(AAD, "masked_sample_front_back.webp")), template_type="aadhaar")
        self.assertEqual(r.headers["Content-Type"], "application/json; charset=utf-8")
        self.assertIn("राहुल कुमार", r.get_data(as_text=True))

    def test_unreadable_name_is_failed_not_guessed(self):
        j = self.run_aadhaar("219d9bd4252cf9df619cb60394f34dc3.webp")  # text too small for the recognizer
        n = j["fields"]["name"]
        if n["status"] != "extracted":
            self.assertEqual((n["value_en"], n["status"]), ("", "failed"))


if __name__ == "__main__":
    unittest.main()
