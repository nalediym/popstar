#!/usr/bin/env python3
"""Tests for tools/qa_render.py: fast synthetic unit tests, plus two smoke tests on repo media when present.

Run:
  uv run --python 3.12 --with "opencv-python-headless<5" --with numpy --with pillow --with pytest \
      python -I -m pytest tools/test_qa_render.py -q
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_render as Q  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def cfg(*args):
    return Q.build_parser().parse_args(["src", "ren", *args])


# ---------- colour / skin

def test_lab_known_values():
    lab = Q.bgr_to_lab(np.array([[[255, 255, 255]], [[128, 128, 128]], [[0, 0, 255]]], np.uint8))
    assert abs(lab[0, 0, 0] - 100) < 0.5            # white
    assert abs(lab[1, 0, 0] - 53.6) < 1.0           # mid grey
    L, a, b = lab[2, 0]                             # pure red: L 53.2, a 80, b 67
    assert abs(L - 53.2) < 1.5 and a > 70 and b > 55


def fake_face(skin_bgr, size=300, lift=0):
    """Dark background, skin-coloured face ellipse with two black 'eyes'; returns (image, face box)."""
    img = np.full((size, size, 3), 30, np.uint8)
    cv2.ellipse(img, (150, 150), (70, 90), 0, 0, 360, skin_bgr, -1)
    cv2.circle(img, (125, 125), 10, (5, 5, 5), -1)
    cv2.circle(img, (175, 125), 10, (5, 5, 5), -1)
    if lift:
        img = np.clip(img.astype(int) + lift, 0, 255).astype(np.uint8)
    return img, Q.Box(80, 60, 140, 180, size, size, "strict")


def test_skin_lightening_is_measured():
    c = cfg()
    img, box = fake_face((45, 70, 110))             # deep brown, sRGB (110, 70, 45)
    s0 = Q.skin_stats(img, box, c)
    assert s0 and not s0["gate_relaxed"] and 25 < s0["L"] < 45
    img2, _ = fake_face((45, 70, 110), lift=45)     # +45/255 on every channel: a clear whitening
    s1 = Q.skin_stats(img2, box, c)
    dL = s1["L"] - s0["L"]
    assert dL > c.skin_fail
    assert Q.skin_verdict(dL, c) == "FAIL"
    assert Q.skin_verdict(8.0, c) == "WARN"
    assert Q.skin_verdict(-0.6, c) == "PASS"
    assert Q.skin_verdict(-15.0, c) == "PASS"       # darker styles never fail by default
    assert Q.skin_verdict(-15.0, cfg("--skin-dark-warn", "12")) == "WARN"


def test_skin_gate_relaxes_on_stylised_colours():
    c = cfg()
    img, box = fake_face((120, 40, 90))             # purple face: outside the YCrCb skin range
    s = Q.skin_stats(img, box, c)
    assert s and s["gate_relaxed"]


def test_hue_delta_wraps():
    assert Q.hue_delta(10, 350) == 20 and Q.hue_delta(350, 10) == -20


# ---------- identity

def test_ssim_bounds():
    rng = np.random.default_rng(1)
    a = cv2.GaussianBlur(rng.integers(0, 256, (96, 96), dtype=np.uint8), (0, 0), 3)
    assert abs(Q.ssim(a, a) - 1) < 1e-6
    assert Q.ssim(a, rng.integers(0, 256, (96, 96), dtype=np.uint8)) < 0.2
    assert Q.ssim_coarse(a, a) > 0.999


def test_face_crop_shape_and_edge_padding():
    img = np.zeros((200, 300, 3), np.uint8)
    col, g = Q.face_crop(img, Q.Box(-20, -20, 100, 100, 300, 200, "strict"), 96, 0.15)
    assert col.shape == (96, 96, 3) and g.shape == (96, 96)


def test_identity_verdict_defaults():
    c = cfg()
    assert Q.identity_verdict(0.30, c) == "PASS" and Q.identity_verdict(0.13, c) == "WARN"
    assert Q.identity_verdict(0.01, c) == "WARN"                              # never FAIL unless asked
    assert Q.identity_verdict(0.01, cfg("--id-fail", "0.05")) == "FAIL"


# ---------- motion

def test_track_following_with_constant_framing_offset_passes():
    c = cfg()
    t = np.linspace(0, 1, 8)
    src = [(0.5 + 0.05 * math.sin(6 * x), 0.4 + 0.02 * x) for x in t]
    ren = [(x + 0.01, y - 0.04) for x, y in src]
    s = Q.track_stats(src, ren, [0.2] * 8, c)
    assert s["axes"]["x"]["state"] == "moving" and s["axes"]["x"]["r"] > 0.99
    assert s["axes"]["y"]["state"] == "static"
    assert s["dist_centered"] < 1e-6 and abs(s["dist_raw"] - math.hypot(0.01, 0.04)) < 1e-6
    assert Q.track_verdict(s, 0, c)[0] == "PASS"


def test_track_spurious_motion_fails_and_static_pair_passes():
    c = cfg()
    src = [(0.5, 0.4)] * 8
    ren = [(0.5 + 0.05 * math.sin(i), 0.4) for i in range(8)]
    s = Q.track_stats(src, ren, [0.2] * 8, c)
    assert s["axes"]["x"]["state"] == "spurious" and s["axes"]["y"]["state"] == "static"
    v, why = Q.track_verdict(s, 0, c)
    assert v == "FAIL" and any("still" in w for w in why)
    s2 = Q.track_stats(src, src, [0.2] * 8, c)
    assert s2["r"] is None and Q.track_verdict(s2, 0, c)[0] == "PASS"


def test_track_outliers_warn():
    c = cfg()
    pts = [(0.5 + 0.05 * i, 0.4) for i in range(6)]
    s = Q.track_stats(pts, pts, [0.2] * 6, c)
    assert Q.track_verdict(s, 4, c)[0] == "WARN"      # 4 rejected of 10 > outlier_frac


def test_motion_verdict_combines_track_and_flow():
    c = cfg()
    pts = [(0.5 + 0.05 * i, 0.4) for i in range(8)]
    good = Q.track_stats(pts, pts, [0.2] * 8, c)
    assert Q.motion_verdict(good, 0, {"score": 0.9}, c)[0] == "PASS"
    assert Q.motion_verdict(good, 0, {"score": 0.1}, c)[0] == "WARN"          # FAIL-level flow knocks PASS down one level
    assert Q.motion_verdict({"n": 1}, 0, {"score": 0.5}, c)[:2] == ("PASS", "optical_flow")
    assert Q.motion_verdict({"n": 1}, 0, {"score": 0.1}, c)[0] == "FAIL"
    assert Q.motion_verdict({"n": 1}, 0, {"score": None}, c)[0] == "N/A"


def test_reject_offset_outliers_replaces_with_inherited_box():
    c = cfg()

    def row(sx, sy, rx, ry):
        return {"src_box": Q.Box(sx * 1000 - 50, sy * 500 - 50, 100, 100, 1000, 500, "strict"),
                "ren_box": Q.Box(rx * 1000 - 50, ry * 500 - 50, 100, 100, 1000, 500, "roi")}

    rows = [row(0.5, 0.5, 0.5, 0.45) for _ in range(5)] + [row(0.5, 0.5, 0.2, 0.9)]
    assert Q.reject_offset_outliers(rows, c) == 1
    assert rows[-1]["ren_box"].how == "inherited" and rows[-1]["outlier_box"].how == "roi"
    assert all(r["ren_box"].how == "roi" for r in rows[:-1])


def test_pearson_guards():
    assert Q.pearson([1, 2], [1, 2]) is None
    assert Q.pearson([1, 1, 1], [1, 2, 3]) == 0.0
    assert abs(Q.pearson([1, 2, 3], [2, 4, 6]) - 1) < 1e-9


# ---------- sampling / verdict helpers

def test_sample_plan_maps_proportionally_and_says_so():
    s = Q.Media(Path("s"), "video", 1280, 720, 24.0, 73, 73 / 24)
    r = Q.Media(Path("r"), "video", 1280, 704, 24.0, 146, 146 / 24)
    si, ri, fr, note = Q.sample_plan(s, r, 8)
    assert len(si) == len(ri) == 8 and "proportionally" in note
    assert all(abs(b - 2 * a) <= 2 for a, b in zip(si, ri))
    same = Q.Media(Path("r"), "video", 1280, 704, 24.0, 73, 73 / 24)
    assert Q.sample_plan(s, same, 8)[3] == ""
    still = Q.Media(Path("p"), "still", 1280, 720, 0, 1, 0)
    assert Q.sample_plan(still, still, 8)[0] == [0]
    assert "still" in Q.sample_plan(still, s, 8)[3]


def test_verdict_helpers():
    assert Q.worst("PASS", "N/A") == "PASS" and Q.worst("WARN", "FAIL") == "FAIL"
    assert Q.downgrade("PASS") == "WARN" and Q.downgrade("FAIL") == "FAIL"
    assert Q.RANK["FAIL"] == 2 and Q.mark("WARN") == "⚠"


# ---------- smoke tests on repo media (skipped when the private files are absent)

STILL_S = REPO / "reference-master/tests/source_frame.png"
STILL_R = REPO / "reference-master/tests/wardrobe_chorus-stage.png"


@pytest.mark.skipif(not (STILL_S.exists() and STILL_R.exists()), reason="private stills not present")
def test_cli_stills_pair(tmp_path):
    rc = Q.main([str(STILL_S), str(STILL_R), "--out", str(tmp_path), "--detector", "haar"])
    R = json.loads((tmp_path / "report.json").read_text())
    assert rc == 0 and R["overall"]["verdict"] == "PASS"
    assert R["skin"]["verdict"] == "PASS" and R["identity"]["verdict"] == "PASS" and R["motion"]["verdict"] == "N/A"
    assert (tmp_path / "sheet.png").exists()


CLIP = REPO / "clips/chunk-02.mp4"
BRIGHT = REPO / "build/qa/synthetic/chunk-02_claymation_BRIGHT.mp4"


@pytest.mark.skipif(not (CLIP.exists() and BRIGHT.exists()), reason="synthetic render not built (see docs/qa.md)")
def test_cli_synthetic_lightening_fails(tmp_path):
    rc = Q.main([str(CLIP), str(BRIGHT), "--out", str(tmp_path), "--detector", "haar", "--no-sheet"])
    R = json.loads((tmp_path / "report.json").read_text())
    assert rc == 2 and R["skin"]["verdict"] == "FAIL" and R["skin"]["dL_median"] > R["thresholds"]["skin_fail"]
