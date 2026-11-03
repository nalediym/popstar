#!/usr/bin/env python3
"""qa_render: automatic QA for AI video renders of one performer (skin lightness, identity, motion).

Usage:
  uv run --python 3.12 --with "opencv-python-headless<5" --with numpy --with pillow \
      python -I tools/qa_render.py <source.mp4|png> <render.mp4|png> \
      [--out build/qa/<name>] [--json] [--frames 8] [--detector auto|haar|mediapipe] [--mp-model PATH]

  Add `--with mediapipe` and `--detector mediapipe --mp-model build/qa/models/blaze_face_short_range.tflite`
  to use MediaPipe's BlazeFace instead of Haar (mediapipe>=1.0 no longer bundles the model; see docs/qa.md).

Checks (the three recurring defects of the pose-restyle pipeline, in order of how often they bite):
  1. SKIN      median CIELAB L* of skin pixels inside the inner-face ellipse, render minus source.
               Positive = the render LIGHTENED her skin. PASS <= +6, WARN <= +12, FAIL above (flags).
               Hue/chroma are reported separately and never fail a stylized render.
  2. IDENTITY  structural similarity (SSIM) of equalized 96x96 grayscale face crops, averaged over frames,
               plus face-box aspect/scale drift. A 0-1 PROXY (same face, same framing), not face recognition.
  3. MOTION    face-centre trajectory correlation (Pearson x/y) + mean normalised distance, with dense
               optical-flow magnitude correlation as a second signal. PASS r >= 0.7 and dist <= 0.06.

Outputs: <out>/report.json (every number, verdict and threshold), <out>/sheet.png (contact sheet with
face crops and skin swatches), one-screen console summary. Exit code 0 PASS, 1 WARN, 2 FAIL.
Frames are sampled at the same timestamps in both files; if durations differ they are mapped proportionally
and the report says so. A still (png) is treated as a 1-frame video.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

VERSION = "0.1.0"
MP_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
                "blaze_face_short_range/float16/1/blaze_face_short_range.tflite")
STILL_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PASS, WARN, FAIL, NA = "PASS", "WARN", "FAIL", "N/A"
RANK = {NA: -1, PASS: 0, WARN: 1, FAIL: 2}


# ---------- media ----------

@dataclass
class Media:
    path: Path
    kind: str          # "video" | "still"
    width: int
    height: int
    fps: float
    frames: int
    duration_s: float

    def as_dict(self):
        return {"path": str(self.path), "kind": self.kind, "width": self.width, "height": self.height,
                "fps": round(self.fps, 3), "frames": self.frames, "duration_s": round(self.duration_s, 3)}


class FrameSource:
    """Random access to frames of a video or a still (a still is a 1-frame video)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.suffix.lower() in STILL_EXT:
            img = cv2.imread(str(self.path), cv2.IMREAD_COLOR)
            if img is None:
                raise SystemExit(f"cannot read image: {self.path}")
            self._img, self._cap = img, None
            h, w = img.shape[:2]
            self.media = Media(self.path, "still", w, h, 0.0, 1, 0.0)
        else:
            cap = cv2.VideoCapture(str(self.path))
            if not cap.isOpened():
                raise SystemExit(f"cannot open video: {self.path}")
            self._img, self._cap = None, cap
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if n <= 0:  # count by decoding (slow, rare)
                n = 0
                while cap.grab():
                    n += 1
            self.media = Media(self.path, "video", w, h, fps, n, (n / fps) if fps else 0.0)
        self._cache: dict[int, np.ndarray] = {}

    def get(self, idx: int) -> np.ndarray | None:
        if self._img is not None:
            return self._img
        if idx in self._cache:
            return self._cache[idx]
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, fr = self._cap.read()
        if not ok:
            return None
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[idx] = fr
        return fr

    def timestamp(self, idx: int) -> float:
        return (idx / self.media.fps) if self.media.fps else 0.0

    def close(self):
        if self._cap is not None:
            self._cap.release()


def sample_plan(src: Media, ren: Media, n: int):
    """Same fractional timestamps in both files. Returns (src_idx, ren_idx, fractions, note)."""
    if src.kind == "still" and ren.kind == "still":
        n = 1
    fr = [(i + 0.5) / n for i in range(n)]
    s_idx = [min(src.frames - 1, int(f * src.frames)) for f in fr]
    r_idx = [min(ren.frames - 1, int(f * ren.frames)) for f in fr]
    note = ""
    if src.kind == "video" and ren.kind == "video":
        if abs(src.duration_s - ren.duration_s) > 1.0 / max(src.fps, ren.fps, 1.0) + 1e-6:
            note = (f"durations differ ({src.duration_s:.2f}s vs {ren.duration_s:.2f}s): "
                    f"frames mapped proportionally, not by absolute time")
    elif src.kind != ren.kind:
        note = "one input is a still: it is compared against every sampled frame of the other"
    return s_idx, r_idx, fr, note


# ---------- faces ----------

@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    W: int
    H: int
    how: str                      # strict | lenient | roi | inherited
    eyes: tuple | None = None     # ((rx, ry), (lx, ly)) in pixels, MediaPipe only

    @property
    def cx(self): return self.x + self.w / 2
    @property
    def cy(self): return self.y + self.h / 2
    @property
    def ncx(self): return self.cx / self.W
    @property
    def ncy(self): return self.cy / self.H
    @property
    def nw(self): return self.w / self.W
    @property
    def nh(self): return self.h / self.H
    @property
    def own(self): return self.how != "inherited"

    def as_dict(self):
        d = {"x": int(self.x), "y": int(self.y), "w": int(self.w), "h": int(self.h), "how": self.how,
             "ncx": round(self.ncx, 4), "ncy": round(self.ncy, 4)}
        if self.eyes:
            d["eyes"] = [[round(v, 1) for v in e] for e in self.eyes]
        return d


def _pick_closest(boxes, cx, cy):
    return min(boxes, key=lambda b: (b[0] + b[2] / 2 - cx) ** 2 + (b[1] + b[3] / 2 - cy) ** 2)


class HaarDetector:
    """OpenCV Haar cascades (ship with opencv-python < 5). No downloads, no keypoints."""
    name = "haar"
    FILES = ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml",
             "haarcascade_frontalface_alt.xml")

    def __init__(self, cascade_dir: str | None = None):
        d = cascade_dir or getattr(getattr(cv2, "data", None), "haarcascades", "")
        self.cascades = []
        for f in self.FILES:
            p = os.path.join(d, f)
            if os.path.exists(p):
                c = cv2.CascadeClassifier(p)
                if not c.empty():
                    self.cascades.append((f.replace("haarcascade_frontalface_", "").replace(".xml", ""), c))
        if not self.cascades:
            raise RuntimeError(
                f"no Haar cascades found in {d!r}. OpenCV 5 wheels dropped them: run with "
                "--with \"opencv-python-headless<5\" or pass --haar-dir pointing at haarcascade_*.xml files")
        self.detail = f"cascades={[n for n, _ in self.cascades]}"

    @staticmethod
    def _gray(frame):
        return cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))

    def detect_strict(self, frame) -> list[tuple]:
        """Full-frame, conservative. Returns [(x, y, w, h)] of the first cascade that finds anything."""
        g = self._gray(frame)
        h, w = g.shape
        ms = max(24, min(h, w) // 10)
        for _, c in self.cascades:
            b = c.detectMultiScale(g, scaleFactor=1.1, minNeighbors=5, minSize=(ms, ms))
            if len(b):
                return [tuple(int(v) for v in bb) for bb in b]
        return []

    def detect_in_roi(self, frame, roi, ref_size) -> list[tuple]:
        """Lenient search inside roi=(x0,y0,x1,y1) for a face about ref_size px wide."""
        x0, y0, x1, y1 = roi
        g = self._gray(frame)[y0:y1, x0:x1]
        if g.size == 0:
            return []
        lo, hi = max(16, int(ref_size * 0.5)), max(24, int(ref_size * 1.6))
        for mn in (3, 1):
            for _, c in self.cascades:
                b = c.detectMultiScale(g, scaleFactor=1.05, minNeighbors=mn, minSize=(lo, lo), maxSize=(hi, hi))
                if len(b):
                    return [(int(bb[0]) + x0, int(bb[1]) + y0, int(bb[2]), int(bb[3])) for bb in b]
        return []


class MediaPipeDetector:
    """MediaPipe Tasks FaceDetector (BlazeFace short range). Needs the .tflite model file on disk.
    Gives eye keypoints, which the identity crop uses to align to the eye line."""
    name = "mediapipe"

    def __init__(self, model_path: str):
        import mediapipe as mp  # noqa: F401  (import error -> caller falls back)
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        self._mp = mp
        opts = vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.IMAGE, min_detection_confidence=0.4)
        self._det = vision.FaceDetector.create_from_options(opts)
        self.detail = f"model={model_path}"

    def _run(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        res = self._det.detect(img)
        out = []
        H, W = bgr.shape[:2]
        for d in res.detections:
            bb = d.bounding_box
            eyes = None
            if d.keypoints and len(d.keypoints) >= 2:
                eyes = ((d.keypoints[0].x * W, d.keypoints[0].y * H), (d.keypoints[1].x * W, d.keypoints[1].y * H))
            out.append(((int(bb.origin_x), int(bb.origin_y), int(bb.width), int(bb.height)), eyes))
        return out

    def detect_strict(self, frame):
        self._last_eyes = {}
        out = []
        for box, eyes in self._run(frame):
            out.append(box)
            self._last_eyes[box] = eyes
        return out

    def detect_in_roi(self, frame, roi, ref_size):
        x0, y0, x1, y1 = roi
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return []
        out = []
        for (x, y, w, h), eyes in self._run(crop):
            box = (x + x0, y + y0, w, h)
            out.append(box)
            self._last_eyes[box] = ((eyes[0][0] + x0, eyes[0][1] + y0), (eyes[1][0] + x0, eyes[1][1] + y0)) if eyes else None
        return out

    def eyes_for(self, box):
        return getattr(self, "_last_eyes", {}).get(box)


def make_detector(choice: str, mp_model: str | None, haar_dir: str | None):
    """Returns (detector, notes). auto = mediapipe if importable AND its model file exists, else haar."""
    notes = []
    if choice in ("auto", "mediapipe"):
        model = mp_model or os.environ.get("QA_MP_FACE_MODEL") or "build/qa/models/blaze_face_short_range.tflite"
        try:
            import mediapipe  # noqa: F401
            have_mp = True
        except Exception as e:  # pragma: no cover - depends on env
            have_mp = False
            if choice == "mediapipe":
                notes.append(f"mediapipe import failed ({e.__class__.__name__}): falling back to Haar")
        if have_mp and os.path.exists(model):
            try:
                return MediaPipeDetector(model), notes
            except Exception as e:  # pragma: no cover
                notes.append(f"mediapipe detector failed to start ({e}): falling back to Haar")
        elif have_mp:
            msg = (f"mediapipe is installed but its face model is not at {model}. mediapipe>=1.0 ships no model; "
                   f"fetch it once with: curl -L -o {model} {MP_MODEL_URL}  (then --mp-model {model}). Using Haar")
            notes.append(msg)
        elif choice == "auto":
            notes.append("mediapipe not installed: using OpenCV Haar cascades (box centre alignment, no eye keypoints)")
    return HaarDetector(haar_dir), notes


def _to_box(b, W, H, how, eyes=None):
    return Box(float(b[0]), float(b[1]), float(b[2]), float(b[3]), W, H, how, eyes)


def _roi_around(cx, cy, w, h, scale, W, H):
    return (int(max(0, cx - w * scale / 2)), int(max(0, cy - h * scale / 2)),
            int(min(W, cx + w * scale / 2)), int(min(H, cy + h * scale / 2)))


def _plausible(cand, ref_ncx, ref_ncy, ref_nw, W, H, gate_dist, size_lo, size_hi):
    """Is a candidate pixel box (x,y,w,h) a plausible match for a face at the reference normalised position?"""
    x, y, w, h = cand
    d = math.hypot((x + w / 2) / W - ref_ncx, (y + h / 2) / H - ref_ncy)
    ratio = (w / W) / max(ref_nw, 1e-6)
    return d <= gate_dist * ref_nw and size_lo <= ratio <= size_hi


def locate_source(det, frame, prev: Box | None, cfg) -> Box | None:
    H, W = frame.shape[:2]
    boxes = det.detect_strict(frame)
    if boxes:
        if prev is not None:
            ok = [b for b in boxes if _plausible(b, prev.ncx, prev.ncy, prev.nw, W, H, cfg.gate_dist * 2, 0.4, 2.5)]
            b = _pick_closest(ok, prev.cx, prev.cy) if ok else max(boxes, key=lambda b: b[2] * b[3])
        else:
            b = max(boxes, key=lambda b: b[2] * b[3])
        return _to_box(b, W, H, "strict", getattr(det, "eyes_for", lambda _b: None)(b))
    if prev is not None:  # hands over the face, head turned: look near the last known position
        roi = _roi_around(prev.cx, prev.cy, prev.w, prev.h, cfg.roi_scale * 1.25, W, H)
        boxes = det.detect_in_roi(frame, roi, prev.w)
        if boxes:
            b = _pick_closest(boxes, prev.cx, prev.cy)
            return _to_box(b, W, H, "lenient", getattr(det, "eyes_for", lambda _b: None)(b))
    return None


def locate_render(det, frame, src: Box | None, prev: Box | None, cfg) -> Box | None:
    """strict full-frame (gated by the source box) -> source-guided ROI -> inherit the source box."""
    H, W = frame.shape[:2]
    ref = src if src is not None else prev
    boxes = det.detect_strict(frame)
    if boxes:
        if ref is None:
            b = max(boxes, key=lambda b: b[2] * b[3])
            return _to_box(b, W, H, "strict", getattr(det, "eyes_for", lambda _b: None)(b))
        ok = [b for b in boxes if _plausible(b, ref.ncx, ref.ncy, ref.nw, W, H, cfg.gate_dist, 0.5, 2.0)]
        if ok:
            b = _pick_closest(ok, ref.ncx * W, ref.ncy * H)
            return _to_box(b, W, H, "strict", getattr(det, "eyes_for", lambda _b: None)(b))
    if ref is None:
        return None
    cx, cy, w, h = ref.ncx * W, ref.ncy * H, ref.nw * W, ref.nh * H
    roi = _roi_around(cx, cy, w, h, cfg.roi_scale, W, H)
    boxes = det.detect_in_roi(frame, roi, w)
    if boxes:
        b = _pick_closest(boxes, cx, cy)
        return _to_box(b, W, H, "roi", getattr(det, "eyes_for", lambda _b: None)(b))
    if src is None:
        return None
    return Box(cx - w / 2, cy - h / 2, w, h, W, H, "inherited")


# ---------- colour ----------

def bgr_to_lab(bgr_u8: np.ndarray) -> np.ndarray:
    """True CIELAB (L* 0..100, a*/b* signed) from 8-bit sRGB, via OpenCV's float path."""
    return cv2.cvtColor(bgr_u8.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)


def lab_to_bgr_u8(L, a, b) -> tuple:
    px = np.array([[[L, a, b]]], dtype=np.float32)
    bgr = cv2.cvtColor(px, cv2.COLOR_LAB2BGR)[0, 0]
    return tuple(int(round(float(np.clip(v, 0, 1)) * 255)) for v in bgr)


def skin_mask(crop_bgr: np.ndarray, cfg) -> tuple[np.ndarray, bool]:
    """Inner-face ellipse AND YCrCb skin gate. Returns (mask, relaxed). relaxed=True when the chroma gate
    rejected almost everything (flat stylised colours) and only the ellipse + luma limits were kept."""
    h, w = crop_bgr.shape[:2]
    ell = np.zeros((h, w), np.uint8)
    cv2.ellipse(ell, (int(w * 0.5), int(h * 0.52)), (int(w * cfg.ellipse_rx), int(h * cfg.ellipse_ry)), 0, 0, 360, 255, -1)
    ycc = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2YCrCb)
    Y, Cr, Cb = ycc[..., 0], ycc[..., 1], ycc[..., 2]
    luma_ok = (Y >= cfg.y_min) & (Y <= cfg.y_max)
    chroma_ok = (Cr >= cfg.cr_lo) & (Cr <= cfg.cr_hi) & (Cb >= cfg.cb_lo) & (Cb <= cfg.cb_hi)
    m = (ell > 0) & luma_ok & chroma_ok
    ell_n = int((ell > 0).sum())
    if ell_n and m.sum() < cfg.min_gate_frac * ell_n:
        return ((ell > 0) & luma_ok), True
    return m, False


def skin_stats(frame: np.ndarray, box: Box, cfg) -> dict | None:
    x0, y0 = int(max(0, box.x)), int(max(0, box.y))
    x1, y1 = int(min(box.W, box.x + box.w)), int(min(box.H, box.y + box.h))
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    mask, relaxed = skin_mask(crop, cfg)
    n = int(mask.sum())
    if n < 20:
        return None
    lab = bgr_to_lab(crop)[mask]
    L, a, b = (float(np.median(lab[:, i])) for i in range(3))
    hue = math.degrees(math.atan2(b, a)) % 360.0
    chroma = math.hypot(a, b)
    return {"L": L, "a": a, "b": b, "hue": hue, "chroma": chroma, "pixels": n,
            "coverage": n / float(mask.size), "gate_relaxed": relaxed, "swatch_bgr": lab_to_bgr_u8(L, a, b)}


def frame_L(frame: np.ndarray) -> float:
    small = cv2.resize(frame, (160, max(1, int(160 * frame.shape[0] / frame.shape[1]))), interpolation=cv2.INTER_AREA)
    return float(np.median(bgr_to_lab(small)[..., 0]))


def hue_delta(h_ren: float, h_src: float) -> float:
    return ((h_ren - h_src + 180.0) % 360.0) - 180.0


# ---------- identity ----------

def face_crop(frame: np.ndarray, box: Box, size: int, margin: float):
    """Square crop around the face box (+margin), aligned to the eye line when eye keypoints exist.
    Returns (colour crop, equalised gray crop), both size x size."""
    img = frame
    cx, cy = box.cx, box.cy
    if box.eyes is not None:
        (rx, ry), (lx, ly) = box.eyes
        ang = math.degrees(math.atan2(ly - ry, lx - rx))
        M = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
        img = cv2.warpAffine(frame, M, (box.W, box.H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    side = max(box.w, box.h) * (1 + 2 * margin)
    x0, y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
    x1, y1 = int(round(cx + side / 2)), int(round(cy + side / 2))
    pad = max(0, -x0, -y0, x1 - box.W, y1 - box.H)
    if pad:
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REFLECT)
        x0, y0, x1, y1 = x0 + pad, y0 + pad, x1 + pad, y1 + pad
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return None, None
    color = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    gray = cv2.equalizeHist(cv2.cvtColor(color, cv2.COLOR_BGR2GRAY))
    return color, gray


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM of two equal-size uint8 grayscale images (7x7 Gaussian window, sigma 1.5)."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    k, s = (7, 7), 1.5
    mu_a, mu_b = cv2.GaussianBlur(a, k, s), cv2.GaussianBlur(b, k, s)
    saa = cv2.GaussianBlur(a * a, k, s) - mu_a ** 2
    sbb = cv2.GaussianBlur(b * b, k, s) - mu_b ** 2
    sab = cv2.GaussianBlur(a * b, k, s) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + C1) * (2 * sab + C2)) / ((mu_a ** 2 + mu_b ** 2 + C1) * (saa + sbb + C2))
    return float(np.clip(m.mean(), -1.0, 1.0))


# ---------- motion ----------

def pearson(a, b) -> float | None:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or len(a) != len(b):
        return None
    if a.std() < 1e-9 or b.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def flow_magnitude(prev: np.ndarray, nxt: np.ndarray, width: int = 160) -> tuple[float, np.ndarray]:
    """Mean Farneback flow magnitude (px at `width` px wide) and a 16x9 magnitude grid."""
    def small(f):
        h = max(9, int(round(width * f.shape[0] / f.shape[1])))
        return cv2.cvtColor(cv2.resize(f, (width, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    p, n = small(prev), small(nxt)
    if p.shape != n.shape:
        n = cv2.resize(n, (p.shape[1], p.shape[0]))
    flow = cv2.calcOpticalFlowFarneback(p, n, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    mag = np.hypot(flow[..., 0], flow[..., 1])
    grid = cv2.resize(mag, (16, 9), interpolation=cv2.INTER_AREA)
    return float(mag.mean()), grid


# ---------- verdicts ----------

def worst(*vs):
    return max(vs, key=lambda v: RANK[v])


def mark(v):
    return {PASS: "✓", WARN: "⚠", FAIL: "✗", NA: "·"}[v]


def skin_verdict(dL_median, cfg):
    if dL_median is None:
        return NA
    if dL_median > cfg.skin_fail:
        return FAIL
    if dL_median > cfg.skin_warn:
        return WARN
    if dL_median < -cfg.skin_dark_warn:
        return WARN
    return PASS


def identity_verdict(score, cfg):
    if score is None:
        return NA
    if score < cfg.id_fail:
        return FAIL
    if score < cfg.id_warn:
        return WARN
    return PASS


def motion_verdict(track, flow, cfg):
    """track: dict with n, r, mean_dist (r may be None when both axes are static). flow: dict with corr."""
    basis = None
    if track and track["n"] >= cfg.min_track:
        r, d = track["r"], track["mean_dist"]
        basis = "face_track"
        if (r is None or r >= cfg.motion_r) and d <= cfg.motion_dist:
            v = PASS
        elif (r is None or r >= cfg.motion_r_warn) and d <= cfg.motion_dist_warn:
            v = WARN
        else:
            v = FAIL
        # the second signal can only pull a PASS down to WARN, never up
        if v == PASS and flow and flow.get("corr") is not None and flow["corr"] < cfg.flow_warn:
            v, basis = WARN, "face_track+flow"
        return v, basis
    if flow and flow.get("corr") is not None:
        c = flow["corr"]
        return (PASS if c >= cfg.flow_pass else WARN if c >= cfg.flow_warn else FAIL), "optical_flow"
    return NA, "none"


# ---------- sheet ----------

def _font(size):
    from PIL import ImageFont
    for p in ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # old Pillow
        return ImageFont.load_default()


COLORS = {PASS: (63, 185, 80), WARN: (210, 153, 34), FAIL: (248, 81, 73), NA: (139, 148, 158)}
BOX_COLORS = {"strict": (63, 185, 80), "lenient": (63, 185, 80), "roi": (210, 153, 34), "inherited": (248, 81, 73)}
BG, FG, DIM, PANEL = (13, 17, 23), (230, 237, 243), (139, 148, 158), (22, 27, 34)


def _thumb(frame_bgr, box, col_w, col_h):
    from PIL import Image, ImageDraw
    h, w = frame_bgr.shape[:2]
    s = min(col_w / w, col_h / h)
    tw, th = max(1, int(w * s)), max(1, int(h * s))
    im = Image.fromarray(cv2.cvtColor(cv2.resize(frame_bgr, (tw, th), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB))
    if box is not None:
        d = ImageDraw.Draw(im)
        d.rectangle([box.x * s, box.y * s, (box.x + box.w) * s, (box.y + box.h) * s], outline=BOX_COLORS[box.how], width=2)
    return im


def render_sheet(path: Path, R: dict, rows: list, src_name: str, ren_name: str, cfg):
    from PIL import Image, ImageDraw
    N = max(1, len(rows))
    COL, GUT, PAD, TH = 200, 92, 12, 112
    CROP = cfg.crop_size
    W = GUT + N * COL + PAD * 2
    f_title, f_chip, f_small, f_tiny = _font(20), _font(16), _font(13), _font(12)
    header_h, row_gap = 92, 10
    H = (PAD + header_h + (TH + 16 + row_gap) * 2 + (CROP + 18 + row_gap) + (64 + 18 + row_gap) + 78 + PAD)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # header
    y = PAD
    d.text((PAD, y), f"qa_render  {src_name}  →  {ren_name}", font=f_title, fill=FG)
    y += 28
    x = PAD
    sk, idn, mo = R["skin"], R["identity"], R["motion"]
    chips = [
        (sk["verdict"], f"SKIN {sk['verdict']}  ΔL* {_fmt(sk['dL_median'], '+.1f')}"),
        (idn["verdict"], f"IDENTITY {idn['verdict']}  {_fmt(idn['ssim_mean'], '.2f')} (proxy)"),
        (mo["verdict"], f"MOTION {mo['verdict']}  r={_fmt(mo['track'].get('r'), '.2f')} d={_fmt(mo['track'].get('mean_dist'), '.3f')} flow={_fmt(mo['flow'].get('corr'), '.2f')}"),
        (R["overall"]["verdict"], f"OVERALL {R['overall']['verdict']}"),
    ]
    for v, txt in chips:
        tw = d.textlength(txt, font=f_chip)
        d.rounded_rectangle([x, y, x + tw + 20, y + 26], radius=6, fill=PANEL, outline=COLORS[v], width=2)
        d.text((x + 10, y + 4), txt, font=f_chip, fill=COLORS[v])
        x += tw + 32
    y += 34
    det = R["detector"]
    samp = R["sampling"]
    line = (f"{N} frames · detector {det['backend']} · boxes: green=own strict, amber=own (source-guided ROI), "
            f"red=inherited from source{(' · ' + samp['note']) if samp['note'] else ''}")
    d.text((PAD, y), line, font=f_small, fill=DIM)
    y = PAD + header_h

    def label(yy, txt, hh):
        d.text((PAD, yy + hh / 2 - 8), txt, font=f_chip, fill=DIM)

    # row: source
    label(y, "SOURCE", TH)
    for i, r in enumerate(rows):
        x = PAD + GUT + i * COL
        if r["src_frame"] is not None:
            t = _thumb(r["src_frame"], r["src_box"], COL - 8, TH)
            im.paste(t, (x, y + (TH - t.height) // 2))
        d.text((x, y + TH + 2), f"t={r['t_src']:.2f}s  f{r['src_idx']}" + ("" if r["src_box"] else "  no face"),
               font=f_tiny, fill=DIM if r["src_box"] else COLORS[WARN])
    y += TH + 16 + row_gap
    # row: render
    label(y, "RENDER", TH)
    for i, r in enumerate(rows):
        x = PAD + GUT + i * COL
        if r["ren_frame"] is not None:
            t = _thumb(r["ren_frame"], r["ren_box"], COL - 8, TH)
            im.paste(t, (x, y + (TH - t.height) // 2))
        how = r["ren_box"].how if r["ren_box"] else "no face"
        d.text((x, y + TH + 2), f"t={r['t_ren']:.2f}s  f{r['ren_idx']}  {how}", font=f_tiny,
               fill=BOX_COLORS.get(how, COLORS[WARN]))
    y += TH + 16 + row_gap
    # row: face crops
    label(y, "FACE", CROP)
    for i, r in enumerate(rows):
        x = PAD + GUT + i * COL
        for j, key in enumerate(("src_crop", "ren_crop")):
            c = r.get(key)
            xx = x + j * (CROP + 6)
            if c is not None:
                im.paste(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB)), (xx, y))
                dd = ImageDraw.Draw(im)
                m = cfg.crop_margin
                # the inner-face ellipse the skin stats sampled (scaled into the crop's margin)
                k = 1.0 / (1 + 2 * m)
                dd.ellipse([xx + CROP * (0.5 - cfg.ellipse_rx * k), y + CROP * (0.52 - cfg.ellipse_ry * k),
                            xx + CROP * (0.5 + cfg.ellipse_rx * k), y + CROP * (0.52 + cfg.ellipse_ry * k)],
                           outline=(88, 166, 255), width=1)
            else:
                d.rectangle([xx, y, xx + CROP - 1, y + CROP - 1], fill=PANEL, outline=DIM)
        s = r.get("ssim")
        d.text((x, y + CROP + 2), f"ssim {_fmt(s, '.2f')}" if s is not None else "ssim –", font=f_tiny, fill=DIM)
    y += CROP + 18 + row_gap
    # row: skin swatches
    SW, SH = CROP, 64
    label(y, "SKIN L*", SH)
    for i, r in enumerate(rows):
        x = PAD + GUT + i * COL
        for j, key in enumerate(("src_skin", "ren_skin")):
            st = r.get(key)
            xx = x + j * (SW + 6)
            if st is not None:
                b, g, rr = st["swatch_bgr"]
                d.rectangle([xx, y, xx + SW - 1, y + SH - 1], fill=(rr, g, b))
                txt = f"L* {st['L']:.1f}"
                tc = (0, 0, 0) if st["L"] > 55 else (255, 255, 255)
                d.text((xx + 6, y + 6), txt, font=f_chip, fill=tc)
                d.text((xx + 6, y + 28), f"h {st['hue']:.0f}° C {st['chroma']:.0f}" + ("*" if st["gate_relaxed"] else ""),
                       font=f_tiny, fill=tc)
            else:
                d.rectangle([xx, y, xx + SW - 1, y + SH - 1], fill=PANEL, outline=DIM)
        dl = r.get("dL")
        if dl is not None:
            v = PASS if dl <= cfg.skin_warn else WARN if dl <= cfg.skin_fail else FAIL
            d.text((x, y + SH + 2), f"ΔL* {dl:+.1f}", font=f_tiny, fill=COLORS[v])
    y += SH + 18 + row_gap
    # footer
    t = R["thresholds"]
    foot = [
        f"skin: median ΔL* {_fmt(sk['dL_median'], '+.1f')} (mean {_fmt(sk['dL_mean'], '+.1f')}), L* {_fmt(sk['L_source_median'], '.1f')} → "
        f"{_fmt(sk['L_render_median'], '.1f')}, Δhue {_fmt(sk['dhue_median'], '+.0f')}°, Δchroma {_fmt(sk['dchroma_median'], '+.1f')}, "
        f"whole-frame ΔL* {_fmt(sk['frame_dL_median'], '+.1f')}   [warn > +{t['skin_warn']}, fail > +{t['skin_fail']}]"
        + ("   * = chroma gate relaxed (stylised colours)" if sk["gate_relaxed_frames"] else ""),
        f"identity: mean SSIM {_fmt(idn['ssim_mean'], '.3f')} (min {_fmt(idn['ssim_min'], '.3f')}), scale drift {_fmt(idn['scale_drift'], '.2f')}x, "
        f"aspect drift {_fmt(idn['aspect_drift'], '.3f')}  [warn < {t['id_warn']}, fail < {t['id_fail']}]  PROXY: equalised-crop SSIM, not face recognition",
        f"motion ({mo['basis']}): track n={mo['track'].get('n')} r_x {_fmt(mo['track'].get('r_x'), '.2f')} r_y {_fmt(mo['track'].get('r_y'), '.2f')} "
        f"dist {_fmt(mo['track'].get('mean_dist'), '.3f')}  flow corr {_fmt(mo['flow'].get('corr'), '.2f')} spatial {_fmt(mo['flow'].get('spatial_corr'), '.2f')}  "
        f"[pass r ≥ {t['motion_r']} & dist ≤ {t['motion_dist']}]  render boxes: {mo['render_box_sources']}",
    ]
    for ln in foot:
        d.text((PAD, y), ln, font=f_small, fill=DIM)
        y += 20
    im.save(str(path))


def _fmt(v, spec):
    return "–" if v is None else format(v, spec)


# ---------- main ----------

def build_parser():
    p = argparse.ArgumentParser(description="QA an AI render against its source clip: skin lightness, identity, motion.")
    p.add_argument("source")
    p.add_argument("render")
    p.add_argument("--out", help="output dir (default build/qa/<render stem>)")
    p.add_argument("--json", action="store_true", help="print report.json to stdout (summary goes to stderr)")
    p.add_argument("--frames", type=int, default=8, help="frames to sample from each file (default 8)")
    p.add_argument("--detector", choices=("auto", "haar", "mediapipe"), default="auto")
    p.add_argument("--mp-model", help="path to blaze_face_short_range.tflite (env QA_MP_FACE_MODEL)")
    p.add_argument("--haar-dir", help="directory with haarcascade_*.xml (default: cv2.data.haarcascades)")
    p.add_argument("--no-sheet", action="store_true")
    g = p.add_argument_group("thresholds")
    g.add_argument("--skin-warn", type=float, default=6.0, help="ΔL* above this is WARN (default +6)")
    g.add_argument("--skin-fail", type=float, default=12.0, help="ΔL* above this is FAIL (default +12)")
    g.add_argument("--skin-dark-warn", type=float, default=12.0, help="ΔL* below -this is WARN (much darker)")
    g.add_argument("--id-warn", type=float, default=0.30, help="identity proxy below this is WARN")
    g.add_argument("--id-fail", type=float, default=0.15, help="identity proxy below this is FAIL")
    g.add_argument("--motion-r", type=float, default=0.7, help="face-track correlation needed to PASS")
    g.add_argument("--motion-dist", type=float, default=0.06, help="mean normalised face distance allowed to PASS")
    g.add_argument("--motion-r-warn", type=float, default=0.4, help="correlation needed to stay at WARN (below: FAIL)")
    g.add_argument("--motion-dist-warn", type=float, default=0.10, help="distance allowed to stay at WARN")
    g.add_argument("--flow-pass", type=float, default=0.5, help="flow-magnitude correlation for PASS when the face track is unusable")
    g.add_argument("--flow-warn", type=float, default=0.2, help="flow correlation below this pulls PASS down to WARN / is FAIL without a track")
    g.add_argument("--min-track", type=int, default=4, help="own render detections needed to trust the face track")
    g.add_argument("--motion-min-std", type=float, default=0.005, help="source axis std (normalised) below which that axis is 'static'")
    g.add_argument("--flow-step", type=int, default=2, help="frames between the two images each flow is computed on; 0 = consecutive samples")
    a = p.add_argument_group("detection / masks")
    a.add_argument("--roi-scale", type=float, default=2.0, help="source-guided ROI = this × the source box")
    a.add_argument("--gate-dist", type=float, default=0.75, help="a strict render detection must be within this × box width of the source face")
    a.add_argument("--ellipse-rx", type=float, default=0.33, help="inner-face ellipse semi-axis, fraction of box width")
    a.add_argument("--ellipse-ry", type=float, default=0.40, help="inner-face ellipse semi-axis, fraction of box height")
    a.add_argument("--cr-lo", type=int, default=133)
    a.add_argument("--cr-hi", type=int, default=173)
    a.add_argument("--cb-lo", type=int, default=77)
    a.add_argument("--cb-hi", type=int, default=127)
    a.add_argument("--y-min", type=int, default=40, help="drop near-black pixels (eyes, nostrils) from the skin mask")
    a.add_argument("--y-max", type=int, default=245, help="drop speculars from the skin mask")
    a.add_argument("--min-gate-frac", type=float, default=0.10, help="if the chroma gate keeps less than this of the ellipse, relax it")
    a.add_argument("--crop-size", type=int, default=96)
    a.add_argument("--crop-margin", type=float, default=0.15)
    return p


THRESHOLD_KEYS = ("skin_warn", "skin_fail", "skin_dark_warn", "id_warn", "id_fail", "motion_r", "motion_dist",
                  "motion_r_warn", "motion_dist_warn", "flow_pass", "flow_warn", "min_track", "motion_min_std",
                  "flow_step", "roi_scale", "gate_dist", "ellipse_rx", "ellipse_ry", "cr_lo", "cr_hi", "cb_lo",
                  "cb_hi", "y_min", "y_max", "min_gate_frac", "crop_size", "crop_margin")


def analyse(cfg) -> tuple[dict, list]:
    t0 = time.time()
    notes = []
    src, ren = FrameSource(cfg.source), FrameSource(cfg.render)
    det, dnotes = make_detector(cfg.detector, cfg.mp_model, cfg.haar_dir)
    notes += dnotes
    s_idx, r_idx, fracs, snote = sample_plan(src.media, ren.media, cfg.frames)
    if snote:
        notes.append(snote)

    rows = []
    prev_s = prev_r = None
    for si, ri, f in zip(s_idx, r_idx, fracs):
        sf, rf = src.get(si), ren.get(ri)
        row = {"frac": f, "src_idx": si, "ren_idx": ri, "t_src": src.timestamp(si), "t_ren": ren.timestamp(ri),
               "src_frame": sf, "ren_frame": rf, "src_box": None, "ren_box": None}
        if sf is not None:
            row["src_box"] = locate_source(det, sf, prev_s, cfg)
            prev_s = row["src_box"] or prev_s
        if rf is not None:
            row["ren_box"] = locate_render(det, rf, row["src_box"], prev_r, cfg)
            if row["ren_box"] is not None and row["ren_box"].own:
                prev_r = row["ren_box"]
        sb, rb = row["src_box"], row["ren_box"]
        if sb is not None and rb is not None:
            row["src_skin"], row["ren_skin"] = skin_stats(sf, sb, cfg), skin_stats(rf, rb, cfg)
            if row["src_skin"] and row["ren_skin"]:
                row["dL"] = row["ren_skin"]["L"] - row["src_skin"]["L"]
                row["dhue"] = hue_delta(row["ren_skin"]["hue"], row["src_skin"]["hue"])
                row["dchroma"] = row["ren_skin"]["chroma"] - row["src_skin"]["chroma"]
            row["src_crop"], sg = face_crop(sf, sb, cfg.crop_size, cfg.crop_margin)
            row["ren_crop"], rg = face_crop(rf, rb, cfg.crop_size, cfg.crop_margin)
            if sg is not None and rg is not None:
                row["ssim"] = ssim(sg, rg)
        if sf is not None and rf is not None:
            row["frame_dL"] = frame_L(rf) - frame_L(sf)
        rows.append(row)

    # ----- skin
    dls = [r["dL"] for r in rows if r.get("dL") is not None]
    skin = {
        "verdict": NA, "n_frames": len(dls),
        "dL_median": _med(dls), "dL_mean": _mean(dls),
        "L_source_median": _med([r["src_skin"]["L"] for r in rows if r.get("src_skin")]),
        "L_render_median": _med([r["ren_skin"]["L"] for r in rows if r.get("ren_skin")]),
        "dhue_median": _med([r["dhue"] for r in rows if r.get("dhue") is not None]),
        "dchroma_median": _med([r["dchroma"] for r in rows if r.get("dchroma") is not None]),
        "chroma_source_median": _med([r["src_skin"]["chroma"] for r in rows if r.get("src_skin")]),
        "chroma_render_median": _med([r["ren_skin"]["chroma"] for r in rows if r.get("ren_skin")]),
        "frame_dL_median": _med([r["frame_dL"] for r in rows if r.get("frame_dL") is not None]),
        "gate_relaxed_frames": sum(1 for r in rows for k in ("src_skin", "ren_skin") if r.get(k) and r[k]["gate_relaxed"]),
        "per_frame": [{"src_idx": r["src_idx"], "ren_idx": r["ren_idx"],
                       "L_source": _r(r["src_skin"]["L"]) if r.get("src_skin") else None,
                       "L_render": _r(r["ren_skin"]["L"]) if r.get("ren_skin") else None,
                       "dL": _r(r.get("dL")), "dhue": _r(r.get("dhue")), "dchroma": _r(r.get("dchroma")),
                       "coverage_source": _r(r["src_skin"]["coverage"], 3) if r.get("src_skin") else None,
                       "coverage_render": _r(r["ren_skin"]["coverage"], 3) if r.get("ren_skin") else None,
                       "gate_relaxed": bool((r.get("src_skin") or {}).get("gate_relaxed") or (r.get("ren_skin") or {}).get("gate_relaxed"))}
                      for r in rows],
    }
    skin["verdict"] = skin_verdict(skin["dL_median"], cfg)
    if skin["gate_relaxed_frames"]:
        notes.append(f"skin chroma gate relaxed on {skin['gate_relaxed_frames']} face(s): stylised colours fell outside the "
                     "YCrCb skin range, so the whole inner-face ellipse was measured")

    # ----- identity
    ss = [r["ssim"] for r in rows if r.get("ssim") is not None]
    pairs = [(r["src_box"], r["ren_box"]) for r in rows if r["src_box"] and r["ren_box"]]
    aspect = [abs((rb.w / rb.h) - (sb.w / sb.h)) for sb, rb in pairs]
    scale = [abs(math.log((rb.nw) / max(sb.nw, 1e-6))) for sb, rb in pairs]
    inherited = sum(1 for r in rows if r["ren_box"] and r["ren_box"].how == "inherited")
    identity = {
        "verdict": NA, "n_frames": len(ss), "ssim_mean": _mean(ss), "ssim_min": (min(ss) if ss else None),
        "aspect_drift": _mean(aspect), "scale_drift": (math.exp(_mean(scale)) if scale else None),
        "inherited_boxes": inherited,
        "note": "proxy: SSIM of equalised 96x96 face crops aligned to the box centre (eye line with MediaPipe). "
                "It rewards same face + same framing and is lowered by style changes; it is not face recognition.",
        "per_frame": [{"src_idx": r["src_idx"], "ren_idx": r["ren_idx"], "ssim": _r(r.get("ssim"), 3),
                       "render_box": r["ren_box"].how if r["ren_box"] else None} for r in rows],
    }
    identity["verdict"] = identity_verdict(identity["ssim_mean"], cfg)
    if det.name == "haar" and pairs:
        identity["aspect_note"] = "Haar boxes are square, so aspect drift is always 0 with this detector"

    # ----- motion: face track
    trk = [r for r in rows if r["src_box"] and r["ren_box"] and r["ren_box"].own]
    sx, sy = [r["src_box"].ncx for r in trk], [r["src_box"].ncy for r in trk]
    rx, ry = [r["ren_box"].ncx for r in trk], [r["ren_box"].ncy for r in trk]
    track = {"n": len(trk), "n_pairs": len(pairs), "r_x": None, "r_y": None, "r": None, "mean_dist": None, "static_axes": []}
    if len(trk) >= 3:
        rs = []
        for axis, (a, b) in (("x", (sx, rx)), ("y", (sy, ry))):
            if float(np.std(a)) < cfg.motion_min_std:
                track["static_axes"].append(axis)
                track[f"r_{axis}"] = None
            else:
                track[f"r_{axis}"] = pearson(a, b)
                rs.append(track[f"r_{axis}"])
        track["r"] = _mean(rs) if rs else None
    if trk:
        track["mean_dist"] = _mean([math.hypot(a - b, c - d) for a, b, c, d in zip(sx, rx, sy, ry)])
    if src.media.kind == "still":
        track = {**track, "n": 0}

    # ----- motion: optical flow
    flow = {"corr": None, "spatial_corr": None, "n": 0, "mean_mag_source": None, "mean_mag_render": None, "step": cfg.flow_step}
    if src.media.kind == "video" and ren.media.kind == "video":
        ms, mr, sp = [], [], []
        for k in range(len(rows)):
            if cfg.flow_step > 0:
                a_s, b_s = _flow_pair_idx(rows[k]["src_idx"], cfg.flow_step, src.media.frames)
                a_r, b_r = _flow_pair_idx(rows[k]["ren_idx"], cfg.flow_step, ren.media.frames)
            else:
                if k + 1 >= len(rows):
                    break
                a_s, b_s = rows[k]["src_idx"], rows[k + 1]["src_idx"]
                a_r, b_r = rows[k]["ren_idx"], rows[k + 1]["ren_idx"]
            fs = (src.get(a_s), src.get(b_s))
            fr_ = (ren.get(a_r), ren.get(b_r))
            if any(f is None for f in fs + fr_):
                continue
            m1, g1 = flow_magnitude(*fs)
            m2, g2 = flow_magnitude(*fr_)
            ms.append(m1)
            mr.append(m2)
            c = pearson(g1.flatten(), g2.flatten())
            if c is not None:
                sp.append(c)
        flow.update({"n": len(ms), "corr": pearson(ms, mr), "spatial_corr": _mean(sp),
                     "mean_mag_source": _mean(ms), "mean_mag_render": _mean(mr),
                     "series_source": [_r(v, 3) for v in ms], "series_render": [_r(v, 3) for v in mr]})
    box_sources = {k: sum(1 for r in rows if r["ren_box"] and r["ren_box"].how == k) for k in ("strict", "roi", "inherited")}
    box_sources["none"] = sum(1 for r in rows if r["ren_box"] is None)
    if src.media.kind == "still":
        mv, basis = NA, "n/a: source is a still"
    else:
        mv, basis = motion_verdict(track, flow, cfg)
        if track["n"] < cfg.min_track and basis == "optical_flow":
            notes.append(f"face track unusable ({track['n']} own render detections < {cfg.min_track}); "
                         "motion verdict rests on optical flow only")
    if inherited:
        notes.append(f"{inherited}/{len(rows)} render frames had no detectable face near the source face: the source "
                     "box was inherited for skin/identity and those frames are excluded from the face track")
    motion = {"verdict": mv, "basis": basis, "track": {k: (_r(v, 4) if isinstance(v, float) else v) for k, v in track.items()},
              "flow": {k: (_r(v, 4) if isinstance(v, float) else v) for k, v in flow.items()},
              "render_box_sources": box_sources,
              "trajectory": [{"src_idx": r["src_idx"], "ren_idx": r["ren_idx"],
                              "source": [_r(r["src_box"].ncx, 4), _r(r["src_box"].ncy, 4)] if r["src_box"] else None,
                              "render": [_r(r["ren_box"].ncx, 4), _r(r["ren_box"].ncy, 4)] if r["ren_box"] else None,
                              "render_box": r["ren_box"].how if r["ren_box"] else None} for r in rows]}

    overall = worst(skin["verdict"], identity["verdict"], motion["verdict"])
    report = {
        "tool": "qa_render", "version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": src.media.as_dict(), "render": ren.media.as_dict(),
        "sampling": {"n": len(rows), "fractions": [_r(f, 4) for f in fracs], "source_indices": s_idx, "render_indices": r_idx,
                     "source_timestamps_s": [_r(src.timestamp(i), 3) for i in s_idx],
                     "render_timestamps_s": [_r(ren.timestamp(i), 3) for i in r_idx], "note": snote},
        "detector": {"backend": det.name, "detail": det.detail},
        "thresholds": {k: getattr(cfg, k) for k in THRESHOLD_KEYS},
        "skin": _round_tree(skin), "identity": _round_tree(identity), "motion": motion,
        "overall": {"verdict": overall, "exit_code": max(0, RANK[overall])},
        "notes": notes, "elapsed_s": round(time.time() - t0, 2),
    }
    src.close()
    ren.close()
    return report, rows


def _flow_pair_idx(idx, step, total):
    if idx + step < total:
        return idx, idx + step
    return max(0, idx - step), idx


def _med(v):
    return float(np.median(v)) if len(v) else None


def _mean(v):
    return float(np.mean(v)) if len(v) else None


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def _round_tree(o):
    if isinstance(o, float):
        return round(o, 4)
    if isinstance(o, dict):
        return {k: _round_tree(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round_tree(v) for v in o]
    return o


def summary_lines(R: dict, out_dir: Path | None) -> list[str]:
    sk, idn, mo, t = R["skin"], R["identity"], R["motion"], R["thresholds"]
    name = f"{Path(R['source']['path']).name} → {Path(R['render']['path']).name}"
    L = [f"qa_render  {name}   ({R['sampling']['n']} frames, {R['detector']['backend']}, {R['elapsed_s']}s)"]
    L.append(f"{mark(sk['verdict'])} SKIN      ΔL* {_fmt(sk['dL_median'], '+.1f')} median (mean {_fmt(sk['dL_mean'], '+.1f')}); "
             f"L* {_fmt(sk['L_source_median'], '.1f')} → {_fmt(sk['L_render_median'], '.1f')}; Δhue {_fmt(sk['dhue_median'], '+.0f')}°, "
             f"Δchroma {_fmt(sk['dchroma_median'], '+.1f')}; whole frame ΔL* {_fmt(sk['frame_dL_median'], '+.1f')}   "
             f"[warn > +{t['skin_warn']:g}, fail > +{t['skin_fail']:g}]")
    L.append(f"{mark(idn['verdict'])} IDENTITY  {_fmt(idn['ssim_mean'], '.2f')} proxy (SSIM of aligned equalised crops, min {_fmt(idn['ssim_min'], '.2f')}); "
             f"scale drift {_fmt(idn['scale_drift'], '.2f')}×; {idn['inherited_boxes']} inherited box(es)   "
             f"[warn < {t['id_warn']:g}, fail < {t['id_fail']:g}]  NOTE: not face recognition")
    tr, fl = mo["track"], mo["flow"]
    L.append(f"{mark(mo['verdict'])} MOTION    {mo['basis']}: r={_fmt(tr.get('r'), '.2f')} (x {_fmt(tr.get('r_x'), '.2f')}, y {_fmt(tr.get('r_y'), '.2f')}) "
             f"dist {_fmt(tr.get('mean_dist'), '.3f')} on {tr.get('n')} own boxes; flow corr {_fmt(fl.get('corr'), '.2f')} "
             f"(spatial {_fmt(fl.get('spatial_corr'), '.2f')}); boxes {mo['render_box_sources']}   "
             f"[pass r ≥ {t['motion_r']:g} & dist ≤ {t['motion_dist']:g}]")
    for n in R["notes"]:
        L.append(f"  note: {n}")
    ov = R["overall"]["verdict"]
    where = f"  → {out_dir}/report.json, sheet.png" if out_dir else ""
    L.append(f"{mark(ov)} OVERALL {ov}{where}")
    return L


def main(argv=None) -> int:
    cfg = build_parser().parse_args(argv)
    out_dir = Path(cfg.out) if cfg.out else Path("build/qa") / Path(cfg.render).stem
    out_dir.mkdir(parents=True, exist_ok=True)
    R, rows = analyse(cfg)
    (out_dir / "report.json").write_text(json.dumps(R, indent=2, ensure_ascii=False) + "\n")
    if not cfg.no_sheet:
        render_sheet(out_dir / "sheet.png", R, rows, Path(cfg.source).name, Path(cfg.render).name, cfg)
    lines = summary_lines(R, out_dir)
    if cfg.json:
        print(json.dumps(R, indent=2, ensure_ascii=False))
        print("\n".join(lines), file=sys.stderr)
    else:
        print("\n".join(lines))
    return R["overall"]["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
