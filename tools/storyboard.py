#!/usr/bin/env python3
"""storyboard.py: the director's storyboard + beatboard for the music video, from the scene plan and the song timing.

    uv run --with pillow python -I tools/storyboard.py [--shots shots/<song>.json] [--timing songs/<song>.timing.json]
        [--looks reference-master/looks.json] [--frames storyboards/frames] [--out storyboards] [--song <slug>]

Reads (every input is optional; what is missing falls back to the canonical plan baked in below, and the board says so):
  shots/<song>.json              scenes[] + takes[] (per-beat cues) from the rnb-director skill
  songs/<song>.timing.json       beats, downbeats, lyric lines (beat_this + Whisper)
  reference-master/looks.json    scene titles, set, light, the lead's wardrobe
  <frames>/s1.png .. s4.png      one raw frame per scene (1280x720-ish)
Writes:
  <frames>/s{n}_letterbox.png    the frame with the 2.39:1 bars burned on
  <out>/<song>_board.png + .pdf  2x2 director's board, 2560x1600
  <out>/<song>_beatboard.png     12-bar strip, 2560x640
Only dependency: Pillow. Fonts: Menlo (mono) + Georgia Italic (lyrics) from /System/Library/Fonts; Pillow's default if missing.
Layout problems (a row that had to shrink, a line that was cut) are printed to stderr as "storyboard: warning".
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- palette, fonts, time formatting
BG = (13, 13, 16, 255)  # #0d0d10
PANEL = (20, 20, 25, 255)
PANEL_EDGE = (44, 44, 52, 255)
TEXT = (232, 232, 238, 255)  # #e8e8ee
DIM = (138, 138, 150, 255)  # #8a8a96
AMBER = (255, 159, 67, 255)  # #ff9f43
ORANGE = (217, 98, 43, 255)  # #d9622b
BROWN = (122, 74, 42, 255)  # #7a4a2a
LIGHT_AMBER = (255, 179, 71, 255)  # #ffb347
CREAM = (243, 226, 199, 255)
INK = (13, 13, 16, 255)
SHADOW = (0, 0, 0, 175)
SECTION_COLOR = {"intro": BROWN, "verse": ORANGE, "chorus": AMBER, "harmony": LIGHT_AMBER}

MENLO = "/System/Library/Fonts/Menlo.ttc"
GEORGIA_ITALIC = "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"
WARNINGS: list[str] = []
_font_missing: set[str] = set()


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"storyboard: warning: {msg}", file=sys.stderr)


def font(size: int, bold: bool = False, serif: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        if serif:
            return ImageFont.truetype(GEORGIA_ITALIC, size)
        return ImageFont.truetype(MENLO, size, index=1 if bold else 0)  # Menlo.ttc: 0 Regular, 1 Bold
    except OSError:
        key = "Georgia Italic" if serif else "Menlo"
        if key not in _font_missing:
            _font_missing.add(key)
            warn(f"{key} not found, using Pillow's default font (labels will look different)")
        try:
            return ImageFont.load_default(size)
        except TypeError:  # Pillow < 10.1
            return ImageFont.load_default()


def fmt_t(t: float) -> str:
    """Truncate to 2 decimals, the way the repo names downbeats (17.346 -> 17.34)."""
    return f"{math.floor(t * 100 + 1e-6) / 100:.2f}"


def with_alpha(c: tuple, a: int) -> tuple:
    return (c[0], c[1], c[2], a)


# ---------------------------------------------------------------- data
@dataclass
class Cue:
    t: float
    text: str
    approx: bool = False
    note: str = ""
    repeat: list[float] = field(default_factory=list)  # extra beats the same move repeats on (Backbeat Snaps)


@dataclass
class Line:
    text: str
    start: float
    end: float
    approx: bool = False


@dataclass
class Scene:
    id: str
    title: str
    t0: float
    t1: float
    section: str  # intro | verse | chorus | harmony (board colour)
    section_label: str
    set: str
    light: str
    wardrobe: str
    camera: str
    post: tuple[float, float] | None
    punch_ins: list[float]
    emotion: str
    cues: list[Cue]
    transition_in: str
    frame: str
    frame_source: str
    tags: list[str] = field(default_factory=list)  # chips drawn on the frame besides the camera move


CANON_DOWNBEATS = [1.68, 4.28, 6.90, 9.50, 12.12, 14.72, 17.34, 19.94, 22.56, 25.16, 27.78, 30.38]
CANON_BAR = 2.61
CANON_DURATION = 32.0
CANON_BPM = 92.0
# The caller's fallback: intro 0.5-3 s, four verse lines over 3-17 s, four chorus lines over 17.3-31 s. All approximate.
CANON_LINES = [
    ("(Devagar, devagar)", 0.5, 3.0),
    ("Tea is warm, the logs are scrolling slow", 3.0, 6.5),
    ("Gave my agent tools and let it go", 6.5, 10.0),
    ("Hooks before the commit, tests before the run", 10.0, 13.5),
    ("Little harness holding everything I've done", 13.5, 17.0),
    ("I'm in the loop, devagar", 17.3, 20.7),
    ("Every step, eu sei onde está", 20.7, 24.1),
    ("Plan it, run it, check it, back again", 24.1, 27.5),
    ("Keep me in the loop, meu bem", 27.5, 31.0),
]


@dataclass
class Timing:
    beats: list[float]
    downbeats: list[float]
    lines: list[Line]
    duration: float
    bpm: float
    source: str  # file path or "canonical fallback"
    approx: bool  # True when everything on it is approximate


def load_timing(path: Path | None) -> Timing:
    if path and path.exists():
        j = json.loads(path.read_text())
        lines = [
            Line(str(l["text"]), float(l["start"]), float(l["end"]), approx=str(l.get("confidence", "low")) != "high")
            for l in j.get("lines", [])
        ]
        beats = [float(b) for b in j.get("beats", [])]
        downbeats = [float(b) for b in j.get("downbeats", [])] or CANON_DOWNBEATS
        if not beats:  # synthesize 4 beats per bar from the downbeats
            beats = sorted({round(d + k * (CANON_BAR / 4), 3) for d in downbeats for k in range(4)})
        return Timing(beats, downbeats, lines, float(j.get("duration_s", CANON_DURATION)), float(j.get("bpm", CANON_BPM)), str(path), False)
    # fallback: canonical downbeats, synthesized beats, approximate lyric times
    beats = sorted({round(d + k * (CANON_BAR / 4), 3) for d in CANON_DOWNBEATS for k in range(4)} | {0.37, 1.03})
    beats = [b for b in beats if b < CANON_DURATION]
    return Timing(beats, CANON_DOWNBEATS, [Line(t, a, b, True) for t, a, b in CANON_LINES], CANON_DURATION, CANON_BPM, "canonical fallback", True)


def find_line(lines: list[Line], needle: str) -> Line | None:
    n = needle.lower()
    return next((l for l in lines if n in l.text.lower()), None)


def beat_after(beats: list[float], t: float, n: int) -> float | None:
    """The n-th beat strictly after t (n=1 -> the next beat)."""
    later = [b for b in beats if b > t + 1e-6]
    return later[n - 1] if len(later) >= n else None


def canonical_scenes(tm: Timing, looks: dict | None) -> list[Scene]:
    """The locked plan from .claude/skills/rnb-director/SKILL.md, with cue times derived from the timing."""
    db = tm.downbeats
    beats = tm.beats
    t0s = [0.0, db[2], db[6], db[9]]  # s2 on bar 3, s3 on bar 7, s4 on bar 10
    t1s = [db[2], db[6], db[9], tm.duration]
    ap = tm.approx

    # cue times
    heart1 = Cue(db[0], "Heart Press", approx=ap, note="bar 1")
    snaps_rep = []
    for d in db:
        if t0s[1] <= d < t1s[1]:
            for n in (1, 3):  # beats 2 and 4 of the bar
                b = beat_after(beats, d, n)
                if b is not None and b < t1s[1]:
                    snaps_rep.append(b)
    snaps = Cue(snaps_rep[0] if snaps_rep else t0s[1], "Backbeat Snaps", approx=ap, note="2 & 4", repeat=snaps_rep[1:])
    jaw = Cue(db[5], "Jaw Trace", approx=True, note="held note")  # the plan gives no time: bar 6, marked ~
    loop_line = find_line(tm.lines, "in the loop")
    loop = Cue(loop_line.start if loop_line else db[8], "Loop Circle", approx=(loop_line.approx if loop_line else True), note='"I\'m in the loop"')
    eu_line = find_line(tm.lines, "eu sei")
    heart2_t = (eu_line.start + 0.4 * (eu_line.end - eu_line.start)) if eu_line else db[9]
    heart2 = Cue(heart2_t, "Heart Press", approx=True, note='"eu sei onde está"')
    keep_line = find_line(tm.lines, "keep me in the loop")
    reach_t = keep_line.start if keep_line else db[11]
    reach = Cue(reach_t, "Reach to Lens", approx=True, note='"Keep me in the loop"')
    pull = Cue(reach_t + 60.0 / tm.bpm, "Pull Back", approx=True, note="the beat after")
    come = Cue(beats[-1] if beats else tm.duration - 0.95, "Come Here", approx=ap, note="final beat")
    all_cues = [heart1, snaps, jaw, loop, heart2, reach, pull, come]

    def cues_in(a: float, b: float) -> list[Cue]:
        return sorted([c for c in all_cues if a <= c.t < b], key=lambda c: c.t)

    L = {s["id"]: s for s in (looks or {}).get("scenes", [])} if looks else {}
    W = {}
    for lk in (looks or {}).get("looks", []):
        if lk.get("character") == "lead":
            W[lk.get("scene")] = lk.get("wardrobe", "")

    def pick(sid: str, key: str, default: str) -> str:
        v = L.get(sid, {}).get(key)
        return str(v) if v else default

    scenes = [
        Scene(
            id="s1-intro", title=pick("s1-intro", "title", "Cozy couch intro").upper(), t0=t0s[0], t1=t1s[0],
            section="intro", section_label="INTRO + VERSE IN",
            set=pick("s1-intro", "set", "a cozy living room at night, tan suede couch, amber fairy lights, candles, a steaming mug of tea") + "; backlit haze silhouette at the start",
            light=pick("s1-intro", "light", "low warm lamp key from camera left, candle glow, deep shadows"),
            wardrobe=W.get("s1-intro") or "an oversized cream knit sweater and small gold hoops",
            camera="slow push-in", post=(1.00, 1.08), punch_ins=[],
            emotion="still, eyes down, breathe", cues=cues_in(t0s[0], t1s[0]),
            transition_in="letterbox wipe in @ 0.00", frame="s1.png", frame_source="",
        ),
        Scene(
            id="s2-verse", title=pick("s2-verse", "title", "Living-room jam").upper(), t0=t0s[1], t1=t1s[1],
            section="verse", section_label="VERSE",
            set=pick("s2-verse", "set", "the same living room, laptop screen glowing with scrolling logs, holiday garland") + "; burnt-orange wash",
            light=pick("s2-verse", "light", "soft warm key, gold bounce, amber rim on her hair"),
            wardrobe=W.get("s2-verse") or "a fitted burnt-orange ribbed top",
            camera="handheld float", post=None, punch_ins=[],
            emotion="eyes down → lift to the lens on the phrase turn; small smile at line ends", cues=cues_in(t0s[1], t1s[1]),
            transition_in="dissolve 10f (same room, new light)", frame="s2.png", frame_source="",
        ),
        Scene(
            id="s3-chorus", title=pick("s3-chorus", "title", "Holiday stage").upper(), t0=t0s[2], t1=t1s[2],
            section="chorus", section_label="CHORUS · HOOK",
            set=pick("s3-chorus", "set", "an intimate late-90s R&B stage, Tuscan brown velvet curtains, fairy-light bokeh, haze") + "; amber",
            light=pick("s3-chorus", "light", "large soft warm key from camera left, gold bounce, amber rim through haze"),
            wardrobe=W.get("s3-chorus") or "a burnt-orange silk slip dress with thin straps and small gold hoop earrings",
            camera="low-angle push-in", post=(1.00, 1.12), punch_ins=[db[8]],
            emotion="straight into the lens; the stillest close-up; slow blink on \"devagar\"", cues=cues_in(t0s[2], t1s[2]),
            transition_in=f"cut + one warm flash frame @ {fmt_t(db[6])}", frame="s3.png", frame_source="",
        ),
        Scene(
            id="s4-harmony", title=pick("s4-harmony", "title", "Girl group of me").upper(), t0=t0s[3], t1=t1s[3],
            section="harmony", section_label="CHORUS OUT + HARMONIES",
            set=pick("s4-harmony", "set", "the same stage, wider, three mic stands"),
            light=pick("s4-harmony", "light", "amber wash, backlit haze, warm specular kickers"),
            wardrobe=W.get("s4-harmony") or "a warm amber satin two-piece; the clones one shade darker",
            camera="quarter orbit feel", post=None, punch_ins=[],
            emotion="girl group of me: the lead holds the gaze, the clones play (Ad-lib Wave, mirrored snaps)", cues=cues_in(t0s[3], t1s[3]),
            transition_in="dissolve 10f (the stage widens)", frame="s4.png", frame_source="",
            tags=["CLONES: TRIPTYCH FALLBACK"],
        ),
    ]
    return scenes


def scenes_from_shots(shots: dict, canon: list[Scene], tm: Timing) -> list[Scene]:
    """Overlay shots/<song>.json on the canonical plan, field by field. Unknown fields keep the canonical value."""
    out: list[Scene] = []
    raw_scenes = shots.get("scenes") or []
    if not raw_scenes:
        return canon
    # hero-1 take cues (kind == "move") override the hand cues
    takes = shots.get("takes") or []
    take = next((t for t in takes if str(t.get("id", "")).startswith("hero-1")), takes[0] if takes else None)
    moves: list[Cue] = []
    if take:
        for c in take.get("cues", []):
            if str(c.get("kind", "move")).lower() == "move" and "t" in c and c.get("text"):
                moves.append(Cue(float(c["t"]), str(c["text"]), approx=bool(c.get("approx", False)), note=str(c.get("note", ""))))
    for i, sc in enumerate(raw_scenes):
        base = canon[min(i, len(canon) - 1)]

        def g(*keys, default=None):
            for k in keys:
                v = sc.get(k)
                if v not in (None, ""):
                    return v
            return default

        t0 = float(g("t0", "start", default=base.t0))
        t1 = float(g("t1", "end", default=base.t1))
        cam = g("camera", default=None)
        camera, post = base.camera, base.post
        punch = list(base.punch_ins)
        if isinstance(cam, dict):
            camera = str(cam.get("move") or cam.get("name") or cam.get("kind") or camera)
            p = cam.get("post")
            if isinstance(p, dict):
                zf = p.get("from", p.get("zoom_from"))
                zt = p.get("to", p.get("zoom_to"))
                post = (float(zf), float(zt)) if zf is not None and zt is not None else post
                pi = p.get("punch_in") or p.get("punch_ins")
                if isinstance(pi, (int, float)):
                    punch = [float(pi)]
                elif isinstance(pi, list):
                    punch = [float(x) for x in pi]
        elif isinstance(cam, str):
            camera = cam
        tr = g("transition", default=None)
        trans_in = base.transition_in
        if isinstance(tr, dict):
            trans_in = str(tr.get("in") or trans_in)
        elif isinstance(tr, str):
            trans_in = tr
        cues = [c for c in moves if t0 <= c.t < t1] if moves else [c for c in base.cues if t0 <= c.t < t1]
        light = g("light", default=base.light)
        emo = g("emotion", default=base.emotion)
        out.append(
            Scene(
                id=str(g("id", default=base.id)), title=str(g("title", default=base.title)).upper(), t0=t0, t1=t1,
                section=str(g("board_section", default=base.section)), section_label=str(g("section", default=base.section_label)).upper(),
                set=str(g("set", default=base.set)) if not isinstance(g("set"), dict) else str(g("set").get("description", base.set)),
                light=str(light) if not isinstance(light, dict) else str(light.get("description", base.light)),
                wardrobe=str(g("wardrobe", default=base.wardrobe)), camera=camera, post=post, punch_ins=punch,
                emotion=str(emo) if not isinstance(emo, dict) else str(emo.get("description", base.emotion)),
                cues=sorted(cues, key=lambda c: c.t), transition_in=trans_in, frame=base.frame, frame_source=base.frame_source, tags=base.tags,
            )
        )
    return out


def glyph_for(camera: str) -> str:
    c = camera.lower()
    if "orbit" in c:
        return "quarter_orbit"
    if "handheld" in c or "float" in c:
        return "handheld_float"
    if "lateral" in c or "slide" in c or "truck" in c:
        return "lateral_slide"
    if "low" in c and ("push" in c or "dolly" in c):
        return "low_angle_push_in"
    if "push" in c or "dolly" in c:
        return "push_in"
    return "hold"


# ---------------------------------------------------------------- drawing helpers
def wrap_text(text: str, f, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split():
            cand = (cur + " " + w).strip()
            if not cur or f.getlength(cand) <= max_w:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def fit_text(text: str, size: int, max_w: float, max_lines: int, bold=False, serif=False, min_size=12, what="text"):
    """Wrap; shrink the font until it fits max_lines (down to min_size); cut with an ellipsis as a last resort."""
    s = size
    while True:
        f = font(s, bold, serif)
        ls = wrap_text(text, f, max_w)
        if len(ls) <= max_lines:
            if s < size:
                warn(f"{what}: shrunk {size}->{s}px to fit {max_lines} line(s): {text[:50]!r}")
            return ls, f, s
        if s <= min_size:
            warn(f"{what}: cut to {max_lines} line(s) at {s}px: {text[:50]!r}")
            ls = ls[:max_lines]
            ls[-1] = ls[-1].rstrip() + "…"
            return ls, f, s
        s -= 1


def arrow(d: ImageDraw.ImageDraw, p0, p1, color, width=3, head=11, shadow=True):
    (x0, y0), (x1, y1) = p0, p1
    ang = math.atan2(y1 - y0, x1 - x0)
    tip = [(x1, y1), (x1 - head * math.cos(ang - 0.55), y1 - head * math.sin(ang - 0.55)), (x1 - head * math.cos(ang + 0.55), y1 - head * math.sin(ang + 0.55))]
    # shorten the shaft so it does not poke through the head
    sx, sy = x1 - (head * 0.6) * math.cos(ang), y1 - (head * 0.6) * math.sin(ang)
    if shadow:
        d.line([(x0, y0), (sx, sy)], fill=SHADOW, width=width + 4)
        d.polygon([(x + 1, y + 1) for x, y in tip], fill=SHADOW)
        d.line(tip + [tip[0]], fill=SHADOW, width=3)
    d.line([(x0, y0), (sx, sy)], fill=color, width=width)
    d.polygon(tip, fill=color)


def arc_points(cx, cy, r, a0, a1, n=24):
    """Points on a circle from angle a0 to a1 (degrees, Pillow convention: 0 = 3 o'clock, clockwise)."""
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def curved_arrow(d, cx, cy, r, a0, a1, color, width=3, head=10, shadow=True):
    pts = arc_points(cx, cy, r, a0, a1)
    if shadow:
        d.line(pts, fill=SHADOW, width=width + 4, joint="curve")
    d.line(pts[:-2], fill=color, width=width, joint="curve")
    arrow(d, pts[-3], pts[-1], color, width=width, head=head, shadow=shadow)


def dashed_rect(d, box, color, width=2, dash=9, gap=6):
    x0, y0, x1, y1 = box
    for (ax, ay, bx, by) in [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]:
        L = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / L, (by - ay) / L
        p = 0.0
        while p < L:
            q = min(p + dash, L)
            d.line([(ax + ux * p, ay + uy * p), (ax + ux * q, ay + uy * q)], fill=color, width=width)
            p = q + gap


def chip(d, x, y, text, size=13, fg=AMBER, bg=(13, 13, 16, 205), outline=None, bold=True, anchor="lt", pad=(8, 5)):
    f = font(size, bold)
    w = f.getlength(text) + 2 * pad[0]
    h = size + 2 * pad[1] + 2
    if anchor == "rt":
        x = x - w
    if anchor == "lb":
        y = y - h
    if anchor == "rb":
        x, y = x - w, y - h
    d.rounded_rectangle([x, y, x + w, y + h], radius=5, fill=bg, outline=outline or with_alpha(fg, 150), width=1)
    d.text((x + pad[0], y + pad[1]), text, font=f, fill=fg)
    return (x, y, x + w, y + h)


def label(d, x, y, text, size=12, color=DIM, bold=True, spacing=1):
    """Small caps label with letter spacing."""
    f = font(size, bold)
    cx = x
    for ch in text.upper():
        d.text((cx, y), ch, font=f, fill=color)
        cx += f.getlength(ch) + spacing
    return cx - x


# ---------------------------------------------------------------- icons (the hand vocabulary + FX)
def icon_kind(name: str) -> str:
    n = name.lower()
    for key, kind in [
        ("heart", "heart"), ("reach", "reach"), ("pull", "pullback"), ("jaw", "jaw"), ("snap", "snap"), ("loop", "loop"),
        ("come here", "comehere"), ("wave", "wave"), ("comb", "comb"), ("frame", "frameface"), ("palm", "palmroll"),
        ("lift", "lift"), ("neck", "jaw"), ("hush", "hush"), ("mic", "hush"), ("shoulder", "comb"), ("lean", "wave"),
    ]:
        if key in n:
            return kind
    return "diamond"


def draw_icon(d, kind: str, x: float, y: float, s: float, color=AMBER, shadow=True):
    """A 1-stroke glyph centred on (x, y), about s px wide."""
    r = s / 2

    def pen(col, w):
        if kind == "heart":
            rr = r * 0.5
            d.ellipse([x - r, y - r * 0.9, x - r + 2 * rr, y - r * 0.9 + 2 * rr], fill=col)
            d.ellipse([x, y - r * 0.9, x + 2 * rr, y - r * 0.9 + 2 * rr], fill=col)
            d.polygon([(x - r, y - r * 0.2), (x + r, y - r * 0.2), (x, y + r)], fill=col)
        elif kind == "reach":  # arrow toward a lens ring
            d.ellipse([x + r * 0.25, y - r * 0.45, x + r * 1.05, y + r * 0.45], outline=col, width=max(2, w - 1))
            arrow(d, (x - r, y), (x + r * 0.15, y), col, width=w, head=7, shadow=False)
        elif kind == "pullback":  # arrow back to the chest (a bar)
            d.line([(x - r, y - r * 0.6), (x - r, y + r * 0.6)], fill=col, width=w)
            arrow(d, (x + r, y), (x - r * 0.65, y), col, width=w, head=7, shadow=False)
        elif kind == "jaw":  # the jawline curve, ear to chin
            pts = arc_points(x, y - r * 0.4, r, 20, 160)
            d.line(pts, fill=col, width=w, joint="curve")
            d.ellipse([pts[-1][0] - 3, pts[-1][1] - 3, pts[-1][0] + 3, pts[-1][1] + 3], fill=col)
        elif kind == "snap":  # starburst
            for k in range(8):
                a = math.radians(k * 45)
                d.line([(x + r * 0.35 * math.cos(a), y + r * 0.35 * math.sin(a)), (x + r * math.cos(a), y + r * math.sin(a))], fill=col, width=w)
        elif kind == "loop":  # a circle drawn in the air, with the arrowhead
            curved_arrow(d, x, y, r * 0.8, 300, 600, col, width=w, head=7, shadow=False)
        elif kind == "comehere":  # the finger curl
            curved_arrow(d, x + r * 0.2, y - r * 0.1, r * 0.7, 180, 420, col, width=w, head=7, shadow=False)
        elif kind == "wave":
            pts = [(x - r + i * (2 * r) / 24, y + r * 0.5 * math.sin(i / 24 * 2 * math.pi * 2)) for i in range(25)]
            d.line(pts, fill=col, width=w, joint="curve")
        elif kind == "comb":  # fingers raking
            for k in range(4):
                ox = x - r * 0.75 + k * r * 0.5
                d.line([(ox + r * 0.35, y - r), (ox, y + r)], fill=col, width=w)
        elif kind == "frameface":  # two corner brackets
            for sx in (-1, 1):
                d.line([(x + sx * r, y - r * 0.9), (x + sx * r, y + r * 0.9)], fill=col, width=w)
                d.line([(x + sx * r, y - r * 0.9), (x + sx * r * 0.45, y - r * 0.9)], fill=col, width=w)
                d.line([(x + sx * r, y + r * 0.9), (x + sx * r * 0.45, y + r * 0.9)], fill=col, width=w)
        elif kind == "palmroll":
            curved_arrow(d, x, y + r * 0.3, r * 0.85, 180, 360, col, width=w, head=7, shadow=False)
        elif kind == "lift":
            arrow(d, (x, y + r), (x, y - r), col, width=w, head=8, shadow=False)
            d.line([(x - r * 0.6, y + r), (x + r * 0.6, y + r)], fill=col, width=w)
        elif kind == "hush":
            d.line([(x, y - r), (x, y + r * 0.3)], fill=col, width=w + 1)
            d.line([(x - r * 0.8, y + r * 0.7), (x + r * 0.8, y + r * 0.7)], fill=col, width=w)
        elif kind == "flash":
            for k in range(8):
                a = math.radians(k * 45 + 22.5)
                L = r if k % 2 == 0 else r * 0.55
                d.line([(x, y), (x + L * math.cos(a), y + L * math.sin(a))], fill=col, width=w)
            d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=col)
        elif kind == "punch":  # a frame with inward corner arrows
            d.rectangle([x - r, y - r, x + r, y + r], outline=col, width=max(1, w - 1))
            for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
                arrow(d, (x + sx * r * 0.95, y + sy * r * 0.95), (x + sx * r * 0.3, y + sy * r * 0.3), col, width=max(1, w - 1), head=5, shadow=False)
        elif kind == "lb_in":  # [ with bars
            d.line([(x - r * 0.3, y - r), (x - r * 0.3, y + r)], fill=col, width=w)
            d.line([(x - r * 0.3, y - r), (x + r * 0.5, y - r)], fill=col, width=w)
            d.line([(x - r * 0.3, y + r), (x + r * 0.5, y + r)], fill=col, width=w)
        elif kind == "lb_out":
            d.line([(x + r * 0.3, y - r), (x + r * 0.3, y + r)], fill=col, width=w)
            d.line([(x + r * 0.3, y - r), (x - r * 0.5, y - r)], fill=col, width=w)
            d.line([(x + r * 0.3, y + r), (x - r * 0.5, y + r)], fill=col, width=w)
        elif kind == "cut":
            d.line([(x, y - r), (x, y + r)], fill=col, width=w)
        elif kind == "dissolve":
            d.rectangle([x - r, y - r * 0.6, x + r * 0.2, y + r * 0.6], outline=col, width=max(1, w - 1))
            d.rectangle([x - r * 0.2, y - r * 0.6, x + r, y + r * 0.6], outline=col, width=max(1, w - 1))
        else:  # diamond
            d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], outline=col, width=w)

    if shadow:
        pen(SHADOW, 5)
    pen(color, 2)


# ---------------------------------------------------------------- frames
def letterbox(src: Image.Image, W=1280, H=720, ratio=2.39) -> Image.Image:
    im = src.convert("RGB")
    s = W / im.width
    im = im.resize((W, max(1, round(im.height * s))), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(im, (0, (H - im.height) // 2))
    bar = (H - round(W / ratio)) // 2
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, W, bar - 1], fill=(0, 0, 0))
    d.rectangle([0, H - bar, W, H], fill=(0, 0, 0))
    return canvas


def load_frame(frames_dir: Path, name: str) -> tuple[Image.Image, bool]:
    p = frames_dir / name
    if p.exists():
        lb = letterbox(Image.open(p))
        lb.save(frames_dir / (p.stem + "_letterbox.png"))
        return lb, True
    warn(f"frame missing: {p}")
    ph = Image.new("RGB", (1280, 720), (24, 24, 30))
    d = ImageDraw.Draw(ph)
    f = font(36, True)
    msg = f"frame missing: {name}"
    d.text(((1280 - f.getlength(msg)) / 2, 330), msg, font=f, fill=(138, 138, 150))
    return letterbox(ph), False


# ---------------------------------------------------------------- the director's board
BOARD_W, BOARD_H = 2560, 1600
FRAME_W, FRAME_H = 704, 396


def draw_camera_glyph(d, sc: Scene, fx, fy, fw, fh):
    """The camera move as a glyph over the frame. Picture area excludes the letterbox bars."""
    bar = round(92 * fh / 720)
    px0, py0, px1, py1 = fx, fy + bar, fx + fw, fy + fh - bar
    kind = glyph_for(sc.camera)
    inset = 22
    if kind in ("push_in", "low_angle_push_in"):
        for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            x = px0 + inset if sx < 0 else px1 - inset
            y = py0 + inset if sy < 0 else py1 - inset
            arrow(d, (x, y), (x - sx * 52, y - sy * 52), AMBER, width=4, head=13)
        if sc.post and sc.post[1] != sc.post[0]:
            z = sc.post[1]
            w, h = (px1 - px0) / z, (py1 - py0) / z
            cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
            dashed_rect(d, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), with_alpha(AMBER, 190), width=2)
            chip(d, cx - w / 2 + 6, cy - h / 2 + 6, f"end frame {z:.2f}x", size=11, pad=(5, 3))
    if kind == "low_angle_push_in":
        chip(d, px0 + 12, py1 - 12, "↑ LOW ANGLE", size=13, anchor="lb")
    elif kind == "handheld_float":
        x0, y, L = px1 - 190, py1 - 34, 150
        pts = [(x0 + i * L / 60, y + 8 * math.sin(i / 60 * 2 * math.pi * 3)) for i in range(61)]
        d.line(pts, fill=SHADOW, width=8, joint="curve")
        d.line(pts, fill=AMBER, width=4, joint="curve")
        chip(d, px1 - 12, y - 14, "HANDHELD FLOAT", size=12, anchor="rb")
    elif kind == "quarter_orbit":
        cx, cy = px1 - 110, py1 - 50
        d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=SHADOW)
        d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=AMBER)
        curved_arrow(d, cx, cy, 40, 190, 290, AMBER, width=4, head=13)
        chip(d, px1 - 12, py1 - 12, "¼ ORBIT · post drift", size=12, anchor="rb")
    elif kind == "lateral_slide":
        arrow(d, (px1 - 200, py1 - 34), (px1 - 40, py1 - 34), AMBER, width=4, head=13)
        chip(d, px1 - 12, py1 - 48, "LATERAL SLIDE", size=12, anchor="rb")
    # camera name chip, top-right of the picture; punch-ins under it
    name = sc.camera.upper()
    if sc.post:
        name += f" · post {sc.post[0]:.2f}→{sc.post[1]:.2f}"
    box = chip(d, px1 - 10, py0 + 10, name, size=13, anchor="rt")
    y = box[3] + 6
    for p in sc.punch_ins:
        box = chip(d, px1 - 10, y, f"PUNCH-IN @ {fmt_t(p)}", size=12, anchor="rt", fg=CREAM)
        y = box[3] + 6
    # extra tags, bottom-left (after the low-angle tag if present)
    ty = py1 - 12 - (34 if kind == "low_angle_push_in" else 0)
    for t in sc.tags:
        box = chip(d, px0 + 12, ty, t, size=12, anchor="lb", fg=CREAM)
        ty = box[1] - 6


def draw_hands_strip(d, x, y, w, sc: Scene, tm: Timing, cue_font_size=13):
    """A mini timeline of the scene window with beat ticks, bar numbers and the move cues. Returns its height."""
    lab_w = 92
    label(d, x, y + 4, "HANDS", 12)
    d.text((x, y + 22), f"{fmt_t(sc.t0)}→{fmt_t(sc.t1)}", font=font(11), fill=DIM)
    tx0, tx1 = x + lab_w, x + w - 12
    base = y + 44
    span = max(0.01, sc.t1 - sc.t0)

    def X(t):
        return tx0 + (t - sc.t0) / span * (tx1 - tx0)

    d.line([(tx0, base), (tx1, base)], fill=with_alpha(DIM, 140), width=1)
    for b in tm.beats:
        if sc.t0 - 1e-6 <= b < sc.t1:
            is_db = any(abs(b - dbt) < 1e-3 for dbt in tm.downbeats)
            h = 12 if is_db else 5
            d.line([(X(b), base - h), (X(b), base + (3 if is_db else 2))], fill=with_alpha(AMBER if is_db else DIM, 170 if is_db else 120), width=2 if is_db else 1)
            if is_db:
                n = tm.downbeats.index(min(tm.downbeats, key=lambda v: abs(v - b))) + 1
                d.text((X(b) + 4, base - 15), f"bar {n}", font=font(10), fill=DIM)
    f = font(cue_font_size)
    rows_right = [tx0 - 1e9, tx0 - 1e9]  # right edge of the last label on each row
    for c in sc.cues:
        for rt in c.repeat:  # the repeating snaps: small dim copies
            if sc.t0 <= rt < sc.t1:
                draw_icon(d, icon_kind(c.text), X(rt), base - 24, 12, with_alpha(AMBER, 150), shadow=False)
        cx = X(c.t)
        draw_icon(d, icon_kind(c.text), cx, base - 26, 22, AMBER)
        txt = f"{c.text} · {'~' if c.approx else ''}{fmt_t(c.t)}" + (f"  {c.note}" if c.note else "")
        tw = f.getlength(txt)
        lx = min(max(cx - tw / 2, tx0 - 60), tx1 - tw)
        row = 0 if lx > rows_right[0] + 10 else 1
        if row == 1 and lx <= rows_right[1] + 10:
            row = 0  # both rows busy: accept the overlap on row 0 but say so
            warn(f"{sc.id} hands strip: cue labels overlap near {fmt_t(c.t)}s")
        ly = base + 8 + row * 17
        d.line([(cx, base), (cx, ly - 1)], fill=with_alpha(AMBER, 120), width=1)
        d.text((lx, ly), txt, font=f, fill=TEXT if row == 0 else CREAM)
        rows_right[row] = lx + tw
    if not sc.cues:
        d.text((tx0 + 4, base + 8), "(no hand cues in this window)", font=f, fill=DIM)
    return 44 + 8 + 17 * 2 + 6


def scene_lines(sc: Scene, lines: list[Line], max_n=3) -> list[tuple[Line, bool, bool]]:
    """Lyric lines that overlap the scene window, with (starts earlier, ends later) flags; at most max_n by overlap."""
    hits = []
    for l in lines:
        ov = min(l.end, sc.t1) - max(l.start, sc.t0)
        if ov > 0.05:
            hits.append((ov, l))
    hits.sort(key=lambda h: -h[0])
    keep = sorted(hits[:max_n], key=lambda h: h[1].start)
    if len(hits) > max_n:
        dropped = ", ".join(f"{h[1].text[:24]!r} ({h[0]:.2f}s)" for h in hits[max_n:])
        print(f"storyboard: {sc.id}: lyric row shows {max_n} of {len(hits)} overlapping lines; dropped the briefest: {dropped}", file=sys.stderr)
    return [(l, l.start < sc.t0 - 0.05, l.end > sc.t1 + 0.05) for _, l in keep]


def draw_lyrics_row(d, x, y, w, sc: Scene, tm: Timing) -> int:
    label(d, x, y + 4, "LYRICS", 12)
    if tm.approx:
        d.text((x, y + 22), "~ approx", font=font(11), fill=DIM)
    rows = scene_lines(sc, tm.lines)
    fy = y + 2
    mono = font(12)
    for l, before, after in rows:
        mark = "←" if before else " "
        tstr = f"{mark} {'~' if l.approx else ' '}{fmt_t(l.start)}"
        d.text((x + 92, fy + 6), tstr, font=mono, fill=DIM)
        ls, f, _ = fit_text(l.text + ("  →" if after else ""), 20, w - 92 - 96, 1, serif=True, min_size=15, what=f"{sc.id} lyric")
        d.text((x + 92 + 92, fy), ls[0], font=f, fill=CREAM)
        fy += 26
    if not rows:
        d.text((x + 92, fy + 4), "(no lyric in this window)", font=font(13), fill=DIM)
        fy += 26
    return fy - y + 4


def bars_label(sc: Scene, tm: Timing) -> str:
    db = tm.downbeats
    first = next((i + 1 for i, v in enumerate(db) if v >= sc.t0 - 1e-3), None)
    last = max([i + 1 for i, v in enumerate(db) if v < sc.t1 - 1e-3] or [0])
    if first is None:
        return "after bar 12"
    pre = "pickup + " if sc.t0 < db[0] - 1e-3 else ""
    return f"{pre}bars {first}–{last}" if last > first else f"{pre}bar {first}"


def draw_panel(d, x, y, w, h, sc: Scene, tm: Timing, frame: Image.Image):
    d.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=PANEL, outline=PANEL_EDGE, width=1)
    pad = 16
    cx, cy, iw = x + pad, y + pad, w - 2 * pad
    # header: S3 · HOLIDAY STAGE · 17.34–25.16s · bars 7–9 · [CHORUS]
    sid = sc.id.split("-")[0].upper()
    fb, fr = font(22, True), font(19)
    d.text((cx, cy), sid, font=fb, fill=AMBER)
    hx = cx + fb.getlength(sid) + 10
    head = f"· {sc.title} · {fmt_t(sc.t0)}–{fmt_t(sc.t1)}s · {bars_label(sc, tm)}"
    d.text((hx, cy + 2), head, font=fr, fill=TEXT)
    col = SECTION_COLOR.get(sc.section, AMBER)
    chip(d, cx + iw, cy, sc.section_label, size=13, fg=INK, bg=col, outline=col, anchor="rt")
    # frame + glyphs
    fx, fy = cx, cy + 40
    d.rectangle([fx - 1, fy - 1, fx + FRAME_W, fy + FRAME_H], outline=(60, 60, 70, 255), width=1)
    d._image.paste(frame.resize((FRAME_W, FRAME_H), Image.LANCZOS), (fx, fy))
    draw_camera_glyph(d, sc, fx, fy, FRAME_W, FRAME_H)
    d.text((fx, fy + FRAME_H + 6), sc.frame_source, font=font(11), fill=DIM)
    # right column rows
    rx = fx + FRAME_W + 22
    rw = cx + iw - rx
    ry = fy - 2
    cam = sc.camera
    if sc.post:
        cam += f" · post zoom {sc.post[0]:.2f}→{sc.post[1]:.2f}"
    else:
        cam += " · no post zoom (drift only)" if glyph_for(sc.camera) in ("handheld_float", "quarter_orbit") else ""
    for p in sc.punch_ins:
        cam += f" · punch-in on {fmt_t(p)}"
    rows = [("SET", sc.set, 3), ("LIGHT", sc.light, 2), ("WARDROBE", sc.wardrobe, 2), ("CAMERA", cam, 2), ("EMOTION", sc.emotion, 2), ("TRANSITION IN", sc.transition_in, 1)]
    for name, val, maxl in rows:
        label(d, rx, ry, name, 11)
        ry += 15
        ls, f, s = fit_text(val, 16, rw, maxl, min_size=13, what=f"{sc.id} {name}")
        for ln in ls:
            d.text((rx, ry), ln, font=f, fill=TEXT)
            ry += s + 4
        ry += 7
    col_bottom = ry
    # hands strip, lyrics
    sy = max(fy + FRAME_H + 24, col_bottom + 6)
    hh = draw_hands_strip(d, cx, sy, iw, sc, tm)
    ly = sy + hh + 8
    lh = draw_lyrics_row(d, cx, ly, iw, sc, tm)
    bottom = ly + lh
    if bottom > y + h - 8:
        warn(f"{sc.id}: panel content overflows by {bottom - (y + h - 8)}px")


def draw_legend_item(d, x, y, kind, text, size=13):
    draw_icon(d, kind, x + 11, y + 9, 18, AMBER)
    d.text((x + 28, y), text, font=font(size), fill=DIM)
    return x + 28 + font(size).getlength(text) + 28


def render_board(scenes: list[Scene], tm: Timing, frames_dir: Path, out_png: Path, out_pdf: Path, song: str, frame_notes: dict[str, str]):
    board = Image.new("RGBA", (BOARD_W, BOARD_H), BG)
    d = ImageDraw.Draw(board, "RGBA")
    # title
    tf = font(34, True)
    title = song.replace("-", " ").upper()
    d.text((30, 22), title, font=tf, fill=AMBER)
    sub = f"director's storyboard · {tm.duration:.1f}s · {tm.bpm:.0f} BPM · {len(tm.downbeats)} bars · {len(scenes)} scenes · 2.39:1 letterbox · cuts on downbeats"
    d.text((30 + tf.getlength(title) + 24, 34), sub, font=font(17), fill=DIM)
    right = f"hackathon R&B music video · generated {date.today().isoformat()} · timing: {Path(tm.source).name if tm.source != 'canonical fallback' else 'canonical fallback (~)'}"
    rf = font(14)
    d.text((BOARD_W - 30 - rf.getlength(right), 40), right, font=rf, fill=DIM)
    d.line([(30, 84), (BOARD_W - 30, 84)], fill=with_alpha(AMBER, 140), width=1)
    # 2x2 panels
    pw, ph, gx, gy = 1230, 690, 40, 28
    x0s, y0s = [30, 30 + pw + gx], [112, 112 + ph + gy]
    for i, sc in enumerate(scenes[:4]):
        frame, ok = load_frame(frames_dir, sc.frame)
        sc.frame_source = ("frame · " + frame_notes.get(sc.id, sc.frame)) if ok else "frame · MISSING"
        draw_panel(d, x0s[i % 2], y0s[i // 2], pw, ph, sc, tm, frame)
    # legend
    ly = 112 + 2 * ph + gy + 14
    lx = 30
    label(d, lx, ly + 2, "GLYPHS", 11)
    lx += 90
    f = font(13)
    # push-in glyph in miniature
    for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
        arrow(d, (lx + 11 + sx * 11, ly + 9 + sy * 9), (lx + 11 + sx * 3, ly + 9 + sy * 2), AMBER, width=2, head=5, shadow=False)
    d.text((lx + 30, ly), "corner arrows = push-in (dashed box = post-zoom end frame; ↑ tag = low angle)", font=f, fill=DIM)
    lx += 30 + f.getlength("corner arrows = push-in (dashed box = post-zoom end frame; ↑ tag = low angle)") + 28
    pts = [(lx + i * 22 / 20, ly + 9 + 5 * math.sin(i / 20 * 2 * math.pi * 2)) for i in range(21)]
    d.line(pts, fill=AMBER, width=2, joint="curve")
    d.text((lx + 30, ly), "wave = handheld float", font=f, fill=DIM)
    lx += 30 + f.getlength("wave = handheld float") + 28
    d.ellipse([lx + 8, ly + 9, lx + 13, ly + 14], fill=AMBER)
    curved_arrow(d, lx + 10.5, ly + 11.5, 10, 190, 290, AMBER, width=2, head=5, shadow=False)
    d.text((lx + 30, ly), "curved arrow = ¼ orbit", font=f, fill=DIM)
    lx += 30 + f.getlength("curved arrow = ¼ orbit") + 28
    arrow(d, (lx, ly + 9), (lx + 22, ly + 9), AMBER, width=2, head=6, shadow=False)
    d.text((lx + 30, ly), "horizontal arrow = lateral slide", font=f, fill=DIM)
    lx += 30 + f.getlength("horizontal arrow = lateral slide") + 28
    d.text((lx, ly), "~ = approximate time    ← → = lyric continues from / into the next scene", font=f, fill=DIM)
    ly2 = ly + 24
    lx = 30 + 90
    for kind, text in [("heart", "Heart Press"), ("snap", "Backbeat Snaps"), ("jaw", "Jaw Trace"), ("loop", "Loop Circle"), ("reach", "Reach to Lens"), ("pullback", "Pull Back"), ("comehere", "Come Here"), ("wave", "Ad-lib Wave")]:
        lx = draw_legend_item(d, lx, ly2, kind, text)
    src = "frames · " + " · ".join(f"{k.split('-')[0].upper()}: {v}" for k, v in frame_notes.items())
    sf = font(12)
    d.text((BOARD_W - 30 - sf.getlength(src), ly2 + 3), src, font=sf, fill=DIM)
    out = board.convert("RGB")
    out.save(out_png)
    out.save(out_pdf, "PDF", resolution=150.0)


# ---------------------------------------------------------------- the beatboard
BEAT_W, BEAT_H = 2560, 640


def render_beatboard(scenes: list[Scene], tm: Timing, out_png: Path, song: str):
    im = Image.new("RGBA", (BEAT_W, BEAT_H), BG)
    d = ImageDraw.Draw(im, "RGBA")
    L, R = 70, BEAT_W - 70
    pps = (R - L) / tm.duration

    def X(t):
        return L + t * pps

    tf = font(26, True)
    title = song.replace("-", " ").upper() + " · BEATBOARD"
    d.text((L, 18), title, font=tf, fill=AMBER)
    bar_s = (tm.downbeats[-1] - tm.downbeats[0]) / (len(tm.downbeats) - 1) if len(tm.downbeats) > 1 else CANON_BAR
    sub = f"{tm.duration:.2f}s · {tm.bpm:.2f} BPM · bar ≈ {bar_s:.2f}s · {len(tm.beats)} beats · {len(tm.downbeats)} downbeats · 24 fps"
    d.text((L + tf.getlength(title) + 22, 27), sub, font=font(15), fill=DIM)
    note = "bars start on the detected downbeats; the pickup is the time before bar 1; bar 12 runs to the end of the song" if not tm.approx else "~ canonical fallback: downbeats from the plan, lyric times approximate"
    nf = font(13)
    d.text((R - nf.getlength(note), 30), note, font=nf, fill=DIM)

    # bar header cells
    y_bar, h_bar = 76, 46
    edges = [0.0] + list(tm.downbeats) + [tm.duration]
    cells = [(edges[i], edges[i + 1], ("PICKUP" if i == 0 else f"BAR {i}")) for i in range(len(edges) - 1)]
    total = sum(b - a for a, b, _ in cells)

    def section_at(t):
        for sc in scenes:
            if sc.t0 <= t < sc.t1:
                return sc.section
        return scenes[-1].section

    for a, b, name in cells:
        col = SECTION_COLOR.get(section_at(a + 0.01), AMBER)
        d.rectangle([X(a) + 1, y_bar, X(b) - 1, y_bar + h_bar], fill=with_alpha(col, 42))
        d.rectangle([X(a) + 1, y_bar, X(b) - 1, y_bar + 5], fill=col)
        d.text((X(a) + 8, y_bar + 10), name, font=font(14, True), fill=TEXT)
        d.text((X(a) + 8, y_bar + 28), f"{fmt_t(a)}s", font=font(12), fill=DIM)
    d.text((X(tm.duration) + 6, y_bar + 10), f"{fmt_t(tm.duration)}s", font=font(12, True), fill=AMBER)
    d.text((X(tm.duration) + 6, y_bar + 28), f"Σ {total:.2f}s", font=font(11), fill=DIM)

    # scene / section band
    y_sec = y_bar + h_bar + 8
    for sc in scenes:
        col = SECTION_COLOR.get(sc.section, AMBER)
        d.rectangle([X(sc.t0) + 1, y_sec, X(sc.t1) - 1, y_sec + 26], fill=col)
        txt = f"{sc.id.split('-')[0].upper()} · {sc.section_label} · {sc.title} · {fmt_t(sc.t0)}–{fmt_t(sc.t1)}"
        ls, f, _ = fit_text(txt, 13, X(sc.t1) - X(sc.t0) - 16, 1, bold=True, min_size=10, what="beatboard scene band")
        d.text((X(sc.t0) + 8, y_sec + 6), ls[0], font=f, fill=INK)

    # beat ruler
    y_rule = y_sec + 26 + 30
    d.line([(X(0), y_rule), (X(tm.duration), y_rule)], fill=with_alpha(DIM, 160), width=1)
    for b in tm.beats:
        is_db = any(abs(b - v) < 1e-3 for v in tm.downbeats)
        d.line([(X(b), y_rule - (14 if is_db else 6)), (X(b), y_rule + 2)], fill=with_alpha(AMBER if is_db else DIM, 200 if is_db else 130), width=2 if is_db else 1)
    d.text((X(0) - 2, y_rule - 30), "0.00", font=font(11), fill=DIM)
    d.text((X(tm.duration) - 30, y_rule - 30), fmt_t(tm.duration), font=font(11), fill=DIM)
    d.text((X(0) + 6, y_rule + 4), "beats · taller = downbeat", font=font(10), fill=DIM)

    # lyric lanes (two, alternating)
    y_lyr, lane_h = y_rule + 22, 56
    lf = font(18, serif=True)
    for i, l in enumerate(tm.lines):
        lane = i % 2
        ly = y_lyr + lane * (lane_h + 4)
        col = SECTION_COLOR.get(section_at(l.start), AMBER)
        d.rounded_rectangle([X(l.start), ly, max(X(l.end), X(l.start) + 6), ly + lane_h], radius=5, fill=with_alpha(col, 40), outline=with_alpha(col, 120), width=1)
        avail = X(tm.duration) - X(l.start) - 8
        wrap_w = max(X(l.end) - X(l.start) - 8, min(avail, 380))
        ls = wrap_text(l.text, lf, wrap_w)
        if len(ls) > 2:
            ls, lf2, _ = fit_text(l.text, 18, wrap_w, 2, serif=True, min_size=14, what="beatboard lyric")
        else:
            lf2 = lf
        for k, ln in enumerate(ls[:2]):
            d.text((X(l.start) + 7, ly + 5 + k * 23), ln, font=lf2, fill=CREAM)
        tlab = f"{'~' if l.approx else ''}{fmt_t(l.start)}"
        d.text((X(l.start) + 7 + lf2.getlength(ls[0]) + 8, ly + 9), tlab, font=font(10), fill=DIM)

    # cue lane
    y_cue = y_lyr + 2 * lane_h + 4 + 14
    base = y_cue + 36
    d.text((X(0), y_cue - 2), "MOVES", font=font(11, True), fill=DIM)
    d.line([(X(0), base), (X(tm.duration), base)], fill=with_alpha(DIM, 110), width=1)
    cf = font(12)
    rows_right = [-1e9, -1e9]
    all_cues = sorted([c for sc in scenes for c in sc.cues], key=lambda c: c.t)
    for c in all_cues:
        for rt in c.repeat:
            draw_icon(d, icon_kind(c.text), X(rt), base - 20, 12, with_alpha(AMBER, 150), shadow=False)
        cx = X(c.t)
        draw_icon(d, icon_kind(c.text), cx, base - 22, 22, AMBER)
        txt = f"{c.text} {'~' if c.approx else ''}{fmt_t(c.t)}"
        tw = cf.getlength(txt)
        lx = min(max(cx - tw / 2, X(0)), X(tm.duration) - tw)
        row = 0 if lx > rows_right[0] + 8 else 1
        if row == 1 and lx <= rows_right[1] + 8:
            row = 0
            warn(f"beatboard: cue labels overlap near {fmt_t(c.t)}s")
        ly = base + 6 + row * 16
        d.line([(cx, base), (cx, ly)], fill=with_alpha(AMBER, 120), width=1)
        d.text((lx, ly), txt, font=cf, fill=TEXT if row == 0 else CREAM)
        rows_right[row] = lx + tw

    # FX lane: letterbox in/out, flash, punch-ins, transitions at the scene cuts
    y_fx = base + 44
    fx_base = y_fx + 30
    d.text((X(0), y_fx - 4), "FX · CUTS", font=font(11, True), fill=DIM)
    d.line([(X(0), fx_base), (X(tm.duration), fx_base)], fill=with_alpha(DIM, 110), width=1)
    fx_items: list[tuple[float, str, str]] = [(0.0, "lb_in", "letterbox wipe in"), (tm.beats[-1] if tm.beats else tm.duration, "lb_out", "letterbox wipe out")]
    for sc in scenes[1:]:
        ti = sc.transition_in.lower()
        if "flash" in ti:
            fx_items.append((sc.t0, "flash", f"cut + warm flash"))
        elif "dissolve" in ti:
            fx_items.append((sc.t0, "dissolve", "dissolve 10f"))
        else:
            fx_items.append((sc.t0, "cut", "cut"))
    for sc in scenes:
        for p in sc.punch_ins:
            fx_items.append((p, "punch", "punch-in"))
    if scenes[-1].t0 not in [p for sc in scenes for p in sc.punch_ins]:
        fx_items.append((scenes[-1].t0, "punch", "punch-in"))  # the s4 cut lands with a punch (plan: 22.56 and 25.16)
    rows_right = [-1e9, -1e9]
    for t, kind, text in sorted(fx_items, key=lambda i: i[0]):
        cx = X(t)
        draw_icon(d, kind, cx, fx_base - 18, 20, AMBER if kind in ("flash", "punch") else CREAM)
        txt = f"{text} {fmt_t(t)}"
        tw = cf.getlength(txt)
        lx = min(max(cx - tw / 2, X(0)), X(tm.duration) - tw)
        row = 0 if lx > rows_right[0] + 8 else 1
        ly = fx_base + 6 + row * 16
        d.line([(cx, fx_base), (cx, ly)], fill=with_alpha(AMBER, 120), width=1)
        d.text((lx, ly), txt, font=cf, fill=TEXT)
        rows_right[row] = lx + tw

    # scene boundaries, full height of the lanes
    for sc in scenes[1:]:
        x = X(sc.t0)
        d.line([(x, y_bar - 6), (x, fx_base + 40)], fill=with_alpha(AMBER, 200), width=2)
    for t in (0.0, tm.duration):
        d.line([(X(t), y_bar - 6), (X(t), fx_base + 40)], fill=with_alpha(DIM, 160), width=1)

    # legend
    y_leg = fx_base + 56
    label(d, L, y_leg + 2, "LEGEND", 11)
    lx = L + 90
    for kind, text in [("heart", "Heart Press"), ("snap", "Backbeat Snaps (small = every 2 & 4)"), ("jaw", "Jaw Trace"), ("loop", "Loop Circle"), ("reach", "Reach to Lens"), ("pullback", "Pull Back"), ("comehere", "Come Here")]:
        lx = draw_legend_item(d, lx, y_leg, kind, text, 12)
    lx = L + 90
    y_leg2 = y_leg + 24
    for kind, text in [("flash", "one warm flash frame"), ("punch", "punch-in (post)"), ("lb_in", "letterbox wipe in"), ("lb_out", "letterbox wipe out"), ("dissolve", "dissolve 10f"), ("cut", "cut")]:
        lx = draw_legend_item(d, lx, y_leg2, kind, text, 12)
    d.line([(lx + 4, y_leg2 + 1), (lx + 4, y_leg2 + 17)], fill=AMBER, width=2)
    d.text((lx + 14, y_leg2), "amber vertical = scene cut (1 frame before the downbeat)   ~ = approximate time", font=font(12), fill=DIM)
    if tm.lines and not tm.approx:
        aud = "lyric times: timing.json (Whisper); ~ = low-confidence line"
        d.text((R - font(12).getlength(aud), y_leg), aud, font=font(12), fill=DIM)
    im.convert("RGB").save(out_png)
    return total


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="director's storyboard + beatboard (Pillow only)")
    ap.add_argument("--shots", default=None, help="shots/<song>.json (optional)")
    ap.add_argument("--timing", default=None, help="songs/<song>.timing.json (optional)")
    ap.add_argument("--looks", default="reference-master/looks.json", help="scene titles/sets/wardrobe (optional)")
    ap.add_argument("--frames", default="storyboards/frames", help="dir with s1.png..s4.png")
    ap.add_argument("--out", default="storyboards", help="output dir")
    ap.add_argument("--song", default="in-the-loop-devagar", help="song slug (names the outputs and default inputs)")
    ap.add_argument("--frame-notes", default=None, help="JSON {scene id: provenance text} printed under each frame")
    a = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    shots_p = Path(a.shots) if a.shots else root / "shots" / f"{a.song}.json"
    timing_p = Path(a.timing) if a.timing else root / "songs" / f"{a.song}.timing.json"
    looks_p = Path(a.looks) if a.looks else None
    frames_dir = Path(a.frames)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    tm = load_timing(timing_p)
    print(f"storyboard: timing from {tm.source}" + (" (everything approximate)" if tm.approx else f" · {len(tm.beats)} beats, {len(tm.lines)} lines"), file=sys.stderr)
    looks = json.loads(looks_p.read_text()) if looks_p and looks_p.exists() else None
    scenes = canonical_scenes(tm, looks)
    if shots_p.exists():
        try:
            scenes = scenes_from_shots(json.loads(shots_p.read_text()), scenes, tm)
            print(f"storyboard: scenes from {shots_p}", file=sys.stderr)
        except (ValueError, KeyError, TypeError) as e:
            warn(f"could not read {shots_p} ({e}); using the canonical plan")
    else:
        print(f"storyboard: {shots_p} not found; using the canonical plan from rnb-director/SKILL.md", file=sys.stderr)

    default_notes = {
        "s1-intro": "Comfy Cloud look-wardrobe (Qwen-Image-2.1 edit) · job 5a19c16f · seed 11",
        "s2-verse": "Comfy Cloud look-wardrobe (Qwen-Image-2.1 edit) · job 635909ab · seed 12",
        "s3-chorus": "existing look render · reference-master/tests/wardrobe_chorus-stage.png (job eff061da)",
        "s4-harmony": "ffmpeg triptych from the s3 frame (clones: triptych fallback) · storyboards/frames/make_s4_triptych.sh",
    }
    notes = {**default_notes, **(json.loads(a.frame_notes) if a.frame_notes else {})}

    board_png = out_dir / f"{a.song}_board.png"
    board_pdf = out_dir / f"{a.song}_board.pdf"
    beat_png = out_dir / f"{a.song}_beatboard.png"
    render_board(scenes, tm, frames_dir, board_png, board_pdf, a.song, notes)
    total = render_beatboard(scenes, tm, beat_png, a.song)
    print(f"storyboard: wrote {board_png}, {board_pdf}, {beat_png}; beatboard cells sum to {total:.2f}s", file=sys.stderr)
    for sc in scenes:
        cues = ", ".join(f"{c.text}@{'~' if c.approx else ''}{fmt_t(c.t)}" for c in sc.cues) or "-"
        print(f"  {sc.id:11s} {fmt_t(sc.t0)}–{fmt_t(sc.t1)}  {sc.camera:20s} cues: {cues}", file=sys.stderr)
    if WARNINGS:
        print(f"storyboard: {len(WARNINGS)} warning(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
