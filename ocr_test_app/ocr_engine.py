"""Qualcomm EasyOCR (CRAFT detector + CRNN recognizer, ONNX) wrapper.

The exported models are raw networks: this file adds the pre/post-processing that
the EasyOCR python package normally does (letterbox/tiling, CRAFT box extraction,
line merging, CTC decoding).
"""
import os
import json
from dataclasses import dataclass

import cv2
import numpy as np
import onnxruntime as ort

DEFAULT_MODEL_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "models", "qualcomm-EasyOCR",
    "onnx-float", "easyocr-onnx-float"))

DET_H, DET_W = 608, 800          # detector input (fixed by the export)
REC_H, REC_W = 64, 800           # recognizer input (fixed by the export)
PAD_RGB = (0.485, 0.456, 0.406)  # ImageNet mean: becomes 0 after in-graph normalisation

# EasyOCR english_g2 alphabet; index 0 of the 97 logits is the CTC blank.
CHARSET = ("0123456789" + "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ " + "€"
           + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "abcdefghijklmnopqrstuvwxyz")


@dataclass
class Line:
    box: tuple          # x0, y0, x1, y1 in original image pixels
    text: str = ""
    conf: float = 0.0

    def to_dict(self):
        return {"box": [int(v) for v in self.box], "text": self.text, "conf": round(self.conf, 3)}


class EasyOCR:
    def __init__(self, model_dir=None):
        model_dir = model_dir or os.environ.get("OCR_MODEL_DIR", DEFAULT_MODEL_DIR)
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.det = ort.InferenceSession(os.path.join(model_dir, "detector.onnx"), opts,
                                        providers=["CPUExecutionProvider"])
        self.rec = ort.InferenceSession(os.path.join(model_dir, "recognizer.onnx"), opts,
                                        providers=["CPUExecutionProvider"])

    # ---- detection ---------------------------------------------------------
    def _detect_tile(self, tile_rgb):
        """tile_rgb: float32 HxWx3 in 0..1, at most DET_H x DET_W. Returns boxes in tile px."""
        h, w = tile_rgb.shape[:2]
        canvas = np.empty((DET_H, DET_W, 3), np.float32)
        canvas[:] = PAD_RGB
        canvas[:h, :w] = tile_rgb
        out = self.det.run(None, {"image": canvas.transpose(2, 0, 1)[None]})[0][0]
        text, link = out[..., 0], out[..., 1]
        text_th, link_th, low_text = 0.7, 0.4, 0.4
        seg = np.clip((text > low_text) | (link > link_th), 0, 1).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(seg, connectivity=4)
        boxes = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if area < 10 or text[labels == i].max() < text_th:
                continue
            # CRAFT-style: grow by sqrt(area/box_area)*thickness so the box covers the glyphs
            niter = int(np.sqrt(area * min(bw, bh) / (bw * bh)) * 2)
            x0, y0 = max(0, x - niter), max(0, y - niter)
            x1, y1 = min(out.shape[1], x + bw + niter + 1), min(out.shape[0], y + bh + niter + 1)
            for sy0, sy1 in self._split_rows(text[y:y + bh, x:x + bw] > low_text,
                                             labels[y:y + bh, x:x + bw] == i):
                boxes.append([x0 * 2, max(0, y + sy0 - niter) * 2,
                              x1 * 2, min(out.shape[0], y + sy1 + niter + 1) * 2])  # heatmap is half-res
        return [b for b in boxes if b[0] < w and b[1] < h]

    @staticmethod
    def _split_rows(text_mask, comp_mask):
        """Tight text lines fuse into one tall component; cut it at empty rows of the text map."""
        h = text_mask.shape[0]
        prof = (text_mask & comp_mask).sum(1).astype(float)
        if h < 12 or prof.max() == 0:
            return [(0, h)]
        empty = np.r_[False, prof[1:-1] < 0.12 * prof.max(), False]
        cuts, r = [], 1
        while r < h - 1:
            if empty[r]:
                e = r
                while empty[e + 1]:
                    e += 1
                cuts.append((r + e) // 2)
                r = e + 1
            else:
                r += 1
        spans, start = [], 0
        for c in cuts + [h]:
            if c - start >= 5:
                spans.append((start, c))
                start = c
        if spans and spans[-1][1] != h:
            spans[-1] = (spans[-1][0], h)
        return spans or [(0, h)]

    def detect(self, img_rgb, zoom=None):
        """Scale the image, then tile it with overlap so small text (zoom 2 for narrow
        cards) and tall scans both reach the detector at a legible size."""
        h, w = img_rgb.shape[:2]
        zoom = zoom or (3 if h < 400 and w < 1000 else 2 if h < 700 and w < 1000 else 1)
        zoom = max(1, min(zoom, int(6 * w / DET_W)))  # never enlarge more than 6x (tiny images are noise)
        scale = DET_W * zoom / w
        sw, sh = DET_W * zoom, max(1, round(h * scale))
        scaled = cv2.resize(img_rgb, (sw, sh),
                            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        f = scaled.astype(np.float32) / 255.0
        sx, sy = int(DET_W * 0.75), int(DET_H * 0.75)
        xs = list(range(0, max(1, sw - DET_W + sx), sx))
        ys = list(range(0, max(1, sh - DET_H + sy), sy))
        boxes = []
        for y in ys:
            for x in xs:
                tile = f[y:y + DET_H, x:x + DET_W]
                th, tw = tile.shape[:2]
                for b in self._detect_tile(tile):
                    boxes.append([b[0] + x, b[1] + y, min(b[2], tw) + x, min(b[3], th) + y])
        boxes = self._dedupe(boxes)
        lines = self._merge_lines(boxes)
        return [Line(tuple(int(round(v / scale)) for v in b)) for b in lines]

    @staticmethod
    def _iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-6)

    @staticmethod
    def _dedupe(boxes):
        """Tiles overlap, so a word can be found twice or cut at a tile edge: union boxes
        that overlap by more than half of the smaller one."""
        boxes = [list(b) for b in boxes]
        changed = True
        while changed:
            changed = False
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    a, b = boxes[i], boxes[j]
                    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
                    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
                    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) + 1e-6
                    if ix * iy / small > 0.5:
                        boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                        del boxes[j]
                        changed = True
                        break
                if changed:
                    break
        return boxes

    @staticmethod
    def _merge_lines(boxes):
        """Join word boxes that sit on one text line (gap < ~0.7 line heights)."""
        boxes = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
        rows = []
        for b in boxes:
            cy, h = (b[1] + b[3]) / 2, b[3] - b[1]
            for r in rows:
                rcy = (r["y0"] + r["y1"]) / 2
                rh = r["y1"] - r["y0"]
                if abs(cy - rcy) < 0.4 * max(h, rh):
                    r["boxes"].append(b)
                    r["y0"], r["y1"] = min(r["y0"], b[1]), max(r["y1"], b[3])
                    break
            else:
                rows.append({"y0": b[1], "y1": b[3], "boxes": [b]})
        out = []
        for r in rows:
            bs = sorted(r["boxes"], key=lambda b: b[0])
            cur = list(bs[0])
            for b in bs[1:]:
                h = max(cur[3] - cur[1], b[3] - b[1])
                if b[0] - cur[2] < 0.7 * h:
                    cur = [min(cur[0], b[0]), min(cur[1], b[1]), max(cur[2], b[2]), max(cur[3], b[3])]
                else:
                    out.append(cur)
                    cur = list(b)
            out.append(cur)
        return out

    # ---- recognition -------------------------------------------------------
    def _recognize(self, crop_rgb):
        g = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
        h, w = g.shape
        nw = min(REC_W, max(8, round(w * REC_H / h)))
        g = cv2.resize(g, (nw, REC_H), interpolation=cv2.INTER_CUBIC)
        canvas = np.empty((REC_H, REC_W), np.uint8)
        canvas[:] = g[:, -1:]  # EasyOCR pads by repeating the last column
        canvas[:, :nw] = g
        x = (canvas.astype(np.float32) / 255.0)[None, None]
        logits = self.rec.run(None, {"image": x})[0][0]           # (199, 97)
        e = np.exp(logits - logits.max(-1, keepdims=True))
        p = e / e.sum(-1, keepdims=True)
        idx, conf = p.argmax(-1), p.max(-1)
        chars, confs, prev = [], [], 0
        for i, c in zip(idx, conf):
            if c and i != prev and i != 0:
                chars.append(CHARSET[i - 1])
                confs.append(c)
            prev = i
        text = "".join(chars).strip()
        return text, float(np.exp(np.mean(np.log(np.clip(confs, 1e-6, 1)))) if confs else 0.0)

    @staticmethod
    def _split_tall(img_rgb, lines):
        """CRAFT fuses tightly spaced lines into one box; cut boxes much taller than the
        median line at valleys of the ink row-profile."""
        if len(lines) < 3:
            return lines
        med = float(np.median([l.box[3] - l.box[1] for l in lines]))
        out = []
        for ln in lines:
            x0, y0, x1, y1 = ln.box
            if y1 - y0 < 1.7 * med or y1 - y0 < 24:
                out.append(ln)
                continue
            g = cv2.cvtColor(img_rgb[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
            ink = cv2.threshold(g, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1].sum(1).astype(float)
            ink = np.convolve(ink, np.ones(3) / 3, mode="same")
            valley = ink < 0.25 * np.percentile(ink, 90)
            cuts, r = [], 2
            while r < len(ink) - 2:
                if valley[r]:
                    e = r
                    while e + 1 < len(ink) and valley[e + 1]:
                        e += 1
                    cuts.append((r + e) // 2)
                    r = e + 1
                else:
                    r += 1
            start, spans = 0, []
            for c in cuts + [len(ink)]:
                if c - start >= 0.6 * med:
                    spans.append((start, c))
                    start = c
            if spans and spans[-1][1] != len(ink):
                spans[-1] = (spans[-1][0], len(ink))
            for s0, s1 in spans or [(0, len(ink))]:
                out.append(Line((x0, y0 + s0, x1, y0 + s1)))
        return out

    def read(self, img_rgb):
        h, w = img_rgb.shape[:2]
        lines = self._split_tall(img_rgb, self.detect(img_rgb))
        for ln in lines:
            x0, y0, x1, y1 = ln.box
            pad = int(0.15 * (y1 - y0))
            crop = img_rgb[max(0, y0 - pad):min(h, y1 + pad), max(0, x0 - pad):min(w, x1 + pad)]
            if crop.size:
                ln.text, ln.conf = self._recognize(crop)
        lines = [l for l in lines if l.text]
        lines.sort(key=lambda l: (l.box[1] + l.box[3]) / 2)
        return lines


class EngineUnavailable(RuntimeError):
    pass



MODELS_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "models"))


class PPOCR:
    """PaddlePaddle PP-OCR det + rec ONNX models from Hugging Face (Apache-2.0), run directly on onnxruntime.

    Thresholds come from each model's own det/inference.yml. Subclasses only name the model folder."""

    name, default_dir, env_var = "ppocr", "", "PPOCR_MODEL_DIR"
    aux_dir, aux_env = None, None   # optional second recognizer (e.g. Devanagari); higher-confidence reading wins

    DET_MEAN, DET_STD = np.float32([0.485, 0.456, 0.406]), np.float32([0.229, 0.224, 0.225])  # applied to BGR, as in PaddleOCR

    def __init__(self, model_dir=None):
        model_dir = model_dir or os.environ.get(self.env_var, os.path.join(MODELS_ROOT, self.default_dir))
        try:
            import yaml
            with open(os.path.join(model_dir, "rec", "inference.yml"), encoding="utf8") as f:
                cfg = yaml.safe_load(f)
            with open(os.path.join(model_dir, "det", "inference.yml"), encoding="utf8") as f:
                post = yaml.safe_load(f)["PostProcess"]
            self.thresh, self.box_thresh, self.unclip = post["thresh"], post["box_thresh"], post["unclip_ratio"]
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            self.det = ort.InferenceSession(os.path.join(model_dir, "det", "inference.onnx"), opts,
                                            providers=["CPUExecutionProvider"])
            self.rec = ort.InferenceSession(os.path.join(model_dir, "rec", "inference.onnx"), opts,
                                            providers=["CPUExecutionProvider"])
        except (ImportError, OSError) as e:
            raise EngineUnavailable(f"{self.name} models not available ({e})") from e
        # CTC classes: 0 = blank, 1..N = dictionary, N+1 = space (use_space_char)
        self.chars = ["", *cfg["PostProcess"]["character_dict"], " "]
        self.aux = None
        if self.aux_dir:
            aux = os.environ.get(self.aux_env, os.path.join(MODELS_ROOT, self.aux_dir))
            try:
                with open(os.path.join(aux, "rec", "inference.yml"), encoding="utf8") as f:
                    acfg = yaml.safe_load(f)
                self.aux = (ort.InferenceSession(os.path.join(aux, "rec", "inference.onnx"), opts,
                                                 providers=["CPUExecutionProvider"]),
                            ["", *acfg["PostProcess"]["character_dict"], " "])
            except OSError as e:
                raise EngineUnavailable(f"{self.name} second recognizer not available ({e})") from e

    # ---- detection (DB) ------------------------------------------------------
    def _boxes(self, img_rgb):
        thresh, box_thresh, unclip = self.thresh, self.box_thresh, self.unclip
        h, w = img_rgb.shape[:2]
        s = 960 / max(h, w) if max(h, w) < 960 else min(1.0, 2000 / max(h, w))
        nh, nw = max(32, round(h * s / 32) * 32), max(32, round(w * s / 32) * 32)
        x = cv2.resize(cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR), (nw, nh), interpolation=cv2.INTER_LINEAR)
        x = ((x.astype(np.float32) / 255.0 - self.DET_MEAN) / self.DET_STD).transpose(2, 0, 1)[None]
        prob = self.det.run(None, {"x": x})[0][0, 0]
        cnts, _ = cv2.findContours((prob > thresh).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in cnts:
            (cx, cy), (rw, rh), ang = cv2.minAreaRect(c)
            if min(rw, rh) < 3:
                continue
            mask = np.zeros(prob.shape, np.uint8)
            cv2.fillPoly(mask, [c.reshape(-1, 2)], 1)
            if float(prob[mask > 0].mean()) < box_thresh:
                continue
            d = rw * rh * unclip / (2 * (rw + rh))  # DB unclip, applied to the rotated rectangle
            out.append(((cx * w / nw, cy * h / nh), ((rw + 2 * d) * w / nw, (rh + 2 * d) * h / nh), ang, (w / nw, h / nh)))
        return out

    @staticmethod
    def _crop(img, rect):
        (cx, cy), (rw, rh), ang, _ = rect
        # minAreaRect sizes are in the rotated frame of the (resized) map; anisotropic scale is ~1 here
        if rw < rh:
            rw, rh, ang = rh, rw, ang + 90
        box = cv2.boxPoints(((cx, cy), (rw, rh), ang)).astype(np.float32)
        order = np.argsort(box[:, 0])
        left, right = box[order[:2]], box[order[2:]]
        tl, bl = left[np.argsort(left[:, 1])]
        tr, br = right[np.argsort(right[:, 1])]
        src = np.float32([tl, tr, br, bl])
        wd, ht = max(8, int(round(rw))), max(8, int(round(rh)))
        crop = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, np.float32([[0, 0], [wd, 0], [wd, ht], [0, ht]])),
                                   (wd, ht), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return crop, src

    # ---- recognition (CTC) -----------------------------------------------------
    def _recognize(self, crop_rgb, sess=None, chars=None):
        sess, chars = sess or self.rec, chars or self.chars
        h, w = crop_rgb.shape[:2]
        nw = int(min(3200, max(16, np.ceil(48 * w / h))))
        x = cv2.resize(cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR), (nw, 48), interpolation=cv2.INTER_LINEAR)
        x = ((x.astype(np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None]
        p = sess.run(None, {"x": x})[0][0]                          # (T, classes)
        if abs(float(p[0].sum()) - 1.0) > 1e-3:                      # model may already output probabilities
            e = np.exp(p - p.max(-1, keepdims=True))
            p = e / e.sum(-1, keepdims=True)
        idx, conf = p.argmax(-1), p.max(-1)
        out, confs, prev = [], [], 0
        for i, c in zip(idx, conf):
            if i != prev and i != 0:
                out.append(chars[i])
                confs.append(c)
            prev = i
        text = "".join(out).strip()
        return text, float(np.exp(np.mean(np.log(np.clip(confs, 1e-6, 1)))) if confs else 0.0)

    def read(self, img_rgb):
        lines = []
        for rect in self._boxes(img_rgb):
            crop, quad = self._crop(img_rgb, rect)
            text, conf = self._recognize(crop)
            if self.aux:
                t2, c2 = self._recognize(crop, *self.aux)
                if t2 and c2 > conf:
                    text, conf = t2, c2
            if text:
                x0, y0 = quad.min(0)
                x1, y1 = quad.max(0)
                lines.append(Line((int(x0), int(y0), int(x1), int(y1)), text, conf))
        lines.sort(key=lambda l: (l.box[1] + l.box[3]) / 2)
        return lines


class PPOCRv6(PPOCR):
    """PP-OCRv6 small (det + rec) - English / Chinese / Japanese / Latin scripts."""
    name, default_dir, env_var = "PP-OCRv6", "PaddlePaddle-PP-OCRv6-small", "PPOCRV6_MODEL_DIR"


class PPOCRv5(PPOCR):
    """PP-OCRv5 mobile (det + rec) - Chinese / English / Japanese / pinyin."""
    name, default_dir, env_var = "PP-OCRv5", "PaddlePaddle-PP-OCRv5-mobile", "PPOCRV5_MODEL_DIR"


class PPOCRv5Hindi(PPOCRv5):
    """PP-OCRv5 mobile + the Devanagari recognizer (Hindi / Marathi lines), same detector."""
    name, default_dir, env_var = "PP-OCRv5+Devanagari", "PaddlePaddle-PP-OCRv5-mobile", "PPOCRV5_MODEL_DIR"
    aux_dir, aux_env = "PaddlePaddle-PP-OCRv5-devanagari", "PPOCRV5_DEVANAGARI_DIR"


class IndraOCR:
    """datalab-to/indra-ocr via its own package (`pip install indra-ocr`): detector, script/orientation models and
    recognisers for Latin, Devanagari, Bengali, Cyrillic, Greek and Chinese in one pass.
    Weights are under a modified OpenRAIL-M licence - check it before production use."""

    name, hindi = "indra", True

    def __init__(self, threads=4):
        try:
            import indra_ocr
        except ImportError as e:
            raise EngineUnavailable("indra-ocr is not installed (pip install indra-ocr)") from e
        try:
            self.conv = indra_ocr.load(threads=threads, mode="ocr", path=os.environ.get("INDRA_OCR_MODEL_DIR") or None)
        except Exception as e:  # download / model files missing
            raise EngineUnavailable(f"indra-ocr models could not be loaded ({e})") from e

    def read(self, img_rgb):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "page.png")
            cv2.imwrite(p, cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR))
            lines = []
            for _, page in self.conv.convert(p):
                lines += [Line(tuple(int(round(v)) for v in l.box), l.text.strip(), float(l.conf))
                          for l in page.lines if l.text.strip()]
        lines.sort(key=lambda l: (l.box[1] + l.box[3]) / 2)
        return lines
