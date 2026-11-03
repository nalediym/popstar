#!/usr/bin/env python3
"""Validate a shot list against the song's timing file.

Usage:
  python3 -I tools/validate_shots.py shots/in-the-loop-devagar.json songs/in-the-loop-devagar.timing.json
  python3 -I tools/validate_shots.py SHOTS TIMING --sheet     # also print every take's cue sheet

Checks the data contract that the coach recorder, the director and the render stages share:
  * required keys and types: version, song, timing, moves, takes, scenes
  * moves: label, a one-sentence imperative "how", exactly one emoji as the icon
  * takes: id (unique, safe as a clip name), title, song, duration_s, brief, setup, cues
  * cues: t, dur, kind (move | direction | emote), text, optional detail; "move" only on move cues, and defined
  * song takes: every cue t sits on a beat from timing.beats (within 0.03 s; exact copies are expected)
  * every cue fits inside its take: 0 <= t and t + dur <= duration_s
  * move cues never overlap inside a take
  * face-touch moves (Jaw Trace, Hair Comb, Frame the Face, ...) stay off sung lines, except a line's
    last second (the held note): the lip-sync needs to see the mouth
  * a cue that quotes a lyric from a low-confidence line says "timing approximate" in its detail
  * scenes is a list (scene objects get a light check once the director fills them)
Exit code: 0 valid (warnings may print), 1 contract errors, 2 an input file can't be read.
Python standard library only.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

BEAT_TOL = 0.03        # contract: a song cue t must be within this of a beat
EXACT_TOL = 0.0005     # closer than this counts as an exact copy of the beat
EPS = 1e-6             # float slack for t + dur comparisons
HELD_S = 1.0           # a face-touch move may overlap only a line's held last note: its final HELD_S,
HELD_FRAC = 0.3        # ...or HELD_FRAC of the line if that is shorter (short chorus lines are all words)
LYRIC_NEAR_S = 1.5     # a cue quoting a lyric should sit within this of that line
APPROX = "timing approximate"
KINDS = ("move", "direction", "emote")
TOP_REQUIRED = ("version", "song", "timing", "moves", "takes", "scenes")
TOP_OPTIONAL = ("sync_offset_s", "identity_lock", "negative")
TAKE_KEYS = ("id", "title", "song", "duration_s", "brief", "setup", "cues")
CUE_KEYS = ("t", "dur", "kind", "move", "text", "detail")
MOVE_KEYS = ("label", "how", "icon")
SCENE_REQUIRED = ("id", "title", "t0", "t1", "section", "seed", "set", "light", "camera", "emotion", "prompt")
CAMERA_MOVES = ("static", "slow_push_in", "handheld_float", "low_angle_push_in", "quarter_orbit", "lateral_slide")
# Hand-near-face moves, matched on "<id> <label>". Words, so "hair-flip" and "ooh-face" don't count.
FACE_TOUCH = re.compile(r"\b(jaw|comb|frame|neck|cheek|chin|mic|hush|lips?)\b", re.I)
NOT_A_VERB = {"you", "your", "she", "her", "the", "a", "an", "this", "it", "they", "we", "i"}


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def fmt(x):
    return f"{x:.3f}".rstrip("0").rstrip(".") if is_num(x) else repr(x)


# ---------------------------------------------------------------- emoji: one grapheme, stdlib only
def _is_modifier(cp):
    return cp in (0xFE0E, 0xFE0F, 0x20E3) or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F


def _is_pictograph(cp):
    return (0x1F000 <= cp <= 0x1FAFF or 0x2190 <= cp <= 0x21FF or 0x2300 <= cp <= 0x23FF
            or 0x2460 <= cp <= 0x27BF or 0x2900 <= cp <= 0x2BFF or cp in (0x00A9, 0x00AE, 0x203C, 0x2049,
            0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299))


def single_emoji(s):
    """True when s is one emoji: one pictograph plus modifiers, ZWJ-joined parts, a flag pair or a keycap."""
    if not isinstance(s, str) or not s or len(s) > 16 or s != s.strip():
        return False
    keycap = "⃣" in s
    bases, joined, flag_half = 0, False, False
    for ch in s:
        cp = ord(ch)
        if cp == 0x200D:
            joined = True
            continue
        if _is_modifier(cp):
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:            # regional indicators pair up into one flag
            if not flag_half and not joined:
                bases += 1
            flag_half = not flag_half
            joined = False
            continue
        if not (_is_pictograph(cp) or (keycap and ch in "0123456789#*")):
            return False
        if not joined:
            bases += 1
        joined = False
    return bases == 1


# ---------------------------------------------------------------- lyrics
def norm_words(s):
    s = s.lower().replace("’", "'").replace("‘", "'")
    return " ".join(re.sub(r"[^\w' ]+", " ", s).split())


def quoted(text):
    """Fragments inside “curly” or "straight" quotes."""
    return [a or b for a, b in re.findall(r"“([^”]+)”|\"([^\"]+)\"", text)]


def gap_to(t, line):
    return 0.0 if line["start"] <= t <= line["end"] else min(abs(t - line["start"]), abs(t - line["end"]))


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}" if where else msg)

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}" if where else msg)


# ---------------------------------------------------------------- timing file
def load_json(path, label):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        print(f"ERROR: can't read the {label} file {path}: {e.strerror or e}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"ERROR: the {label} file {path} is not valid JSON: {e.msg} at line {e.lineno}, column {e.colno}",
              file=sys.stderr)
        sys.exit(2)


def check_timing(T, rep):
    """Returns a normalized view of the timing file: beats, downbeats, lines, duration."""
    out = {"beats": [], "downbeats": [], "lines": [], "duration": None, "song": None}
    if not isinstance(T, dict):
        rep.err("timing", "the file must be a JSON object")
        return out
    beats = T.get("beats")
    if not isinstance(beats, list) or not beats or not all(is_num(b) for b in beats):
        rep.err("timing.beats", "must be a non-empty list of numbers (seconds); song cues can't be checked")
    elif any(b2 <= b1 for b1, b2 in zip(beats, beats[1:])):
        rep.err("timing.beats", "must be strictly increasing")
    else:
        out["beats"] = list(beats)
    downs = T.get("downbeats", [])
    if isinstance(downs, list) and all(is_num(d) for d in downs):
        out["downbeats"] = sorted(downs)
    else:
        rep.warn("timing.downbeats", "should be a list of numbers; bar numbers are skipped")
    if is_num(T.get("duration_s")) and T["duration_s"] > 0:
        out["duration"] = float(T["duration_s"])
    else:
        rep.warn("timing.duration_s", "missing or not a positive number")
    out["song"] = T.get("song") if isinstance(T.get("song"), str) else None
    lines = T.get("lines")
    if not isinstance(lines, list):
        rep.warn("timing.lines", "missing; the lyric checks (face-touch, timing approximate) are skipped")
        return out
    for i, ln in enumerate(lines):
        ok = (isinstance(ln, dict) and isinstance(ln.get("text"), str) and is_num(ln.get("start"))
              and is_num(ln.get("end")) and ln["end"] > ln["start"])
        if not ok:
            rep.warn(f"timing.lines[{i}]", "needs text, start and end (end > start); line skipped")
            continue
        conf = ln.get("confidence")
        if conf not in ("high", "med", "low"):
            rep.warn(f"timing.lines[{i}]", f"confidence {conf!r} is not high|med|low; treated as low")
            conf = "low"
        out["lines"].append({"i": i, "text": ln["text"], "start": float(ln["start"]), "end": float(ln["end"]),
                             "confidence": conf, "norm": f" {norm_words(ln['text'])} "})
    return out


def nearest(seq, x):
    """Index of the value in sorted seq nearest to x."""
    lo, hi = 0, len(seq)
    while lo < hi:
        mid = (lo + hi) // 2
        if seq[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return 0
    if lo == len(seq):
        return len(seq) - 1
    return lo if seq[lo] - x < x - seq[lo - 1] else lo - 1


def bar_beat(tm, t):
    """'bar.beat' on the song clock from downbeats, e.g. '9.1'; pickup beats are bar 0."""
    B, D = tm["beats"], tm["downbeats"]
    if not B or not D:
        return ""
    k = nearest(B, t)
    down_idx = [nearest(B, d) for d in D]
    bar = sum(1 for d in down_idx if d <= k)
    if bar == 0:
        return f"0.{4 - (down_idx[0] - k) + 1}"
    return f"{bar}.{k - down_idx[bar - 1] + 1}"


# ---------------------------------------------------------------- shots file
def check_moves(moves, rep):
    if not isinstance(moves, dict):
        rep.err("moves", "must be an object of {move-id: {label, how, icon}}")
        return {}
    good = {}
    for mid, m in moves.items():
        where = f"moves.{mid}"
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", mid):
            rep.warn(where, "move ids read best as kebab-case (like heart-press)")
        if not isinstance(m, dict):
            rep.err(where, "must be an object with label, how and icon")
            continue
        for k in MOVE_KEYS:
            if not isinstance(m.get(k), str) or not m[k].strip():
                rep.err(where, f"needs a non-empty string {k!r}")
        for k in m:
            if k not in MOVE_KEYS:
                rep.warn(where, f"unknown key {k!r} (the contract has label, how, icon)")
        how = m.get("how")
        if isinstance(how, str) and how.strip():
            h = how.strip()
            if re.search(r"[.!?…][\"”’)\]]*\s+\S", h):
                rep.err(where, f"'how' must be one sentence: {h!r}")
            if not re.search(r"[.!?][\"”’)]*$", h):
                rep.warn(where, "'how' should end like a sentence (. ! ?)")
            if len(h) > 140:
                rep.warn(where, f"'how' is {len(h)} characters; keep it short enough to read mid-take")
            first = re.split(r"[\s,]+", h, maxsplit=1)[0].lower()
            if first in NOT_A_VERB:
                rep.warn(where, f"'how' should start with a verb (imperative), not {first!r}")
        icon = m.get("icon")
        if isinstance(icon, str) and icon and not single_emoji(icon):
            rep.err(where, f"'icon' must be exactly one emoji, got {icon!r}")
        good[mid] = m
    return good


def check_cue(c, where, take, moves, tm, rep):
    if not isinstance(c, dict):
        rep.err(where, "must be an object")
        return None
    bad = False
    for k in c:
        if k not in CUE_KEYS:
            rep.warn(where, f"unknown key {k!r}")
    t, dur, kind = c.get("t"), c.get("dur"), c.get("kind")
    if not is_num(t):
        rep.err(where, "t must be a number (seconds)")
        bad = True
    if not is_num(dur) or dur <= 0:
        rep.err(where, "dur must be a number > 0 (seconds)")
        bad = True
    if kind not in KINDS:
        rep.err(where, f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
        bad = True
    if not isinstance(c.get("text"), str) or not c["text"].strip():
        rep.err(where, "text must be a non-empty string")
    if "detail" in c and not isinstance(c["detail"], str):
        rep.err(where, "detail must be a string when present")
    elif isinstance(c.get("detail"), str) and not c["detail"].strip():
        rep.warn(where, "detail is empty; drop the key instead")
    if kind == "move":
        mid = c.get("move")
        if not isinstance(mid, str) or not mid:
            rep.err(where, "a move cue needs \"move\": \"<move-id>\"")
            bad = True
        elif mid not in moves:
            rep.err(where, f"move {mid!r} is not defined in moves")
    elif "move" in c:
        rep.err(where, f"\"move\" only belongs on kind \"move\" cues, this one is {kind!r}")
    if bad:
        return None
    dur_s = take.get("duration_s")
    if t < 0:
        rep.err(where, f"t={fmt(t)} is before the take starts")
    if is_num(dur_s):
        if t >= dur_s:
            rep.err(where, f"t={fmt(t)} is at or after the end of the take ({fmt(dur_s)}s)")
        elif t + dur > dur_s + EPS:
            rep.err(where, f"ends at {fmt(t + dur)}s, after the take's {fmt(dur_s)}s; shorten dur to {fmt(dur_s - t)}")
    if take.get("song") is True and tm["beats"]:
        B = tm["beats"]
        b = B[nearest(B, t)]
        off = abs(t - b)
        if off > BEAT_TOL:
            rep.err(where, f"t={fmt(t)} is not on a beat: nearest beat is {fmt(b)} ({off:.3f}s away)")
        elif off > EXACT_TOL:
            rep.warn(where, f"t={fmt(t)} is {off:.3f}s off beat {fmt(b)}; copy the beat value exactly")
    return c


def check_lyrics(c, where, take, moves, tm, rep):
    """Face-touch moves stay off sung lines; low-confidence lyric quotes say 'timing approximate'."""
    lines = tm["lines"]
    if not lines or take.get("song") is not True:
        return
    t, end = c["t"], c["t"] + c["dur"]
    if c["kind"] == "move" and c.get("move") in moves:
        m = moves[c["move"]]
        if FACE_TOUCH.search(f"{c['move']} {m.get('label', '')}"):
            for ln in lines:
                a, b = max(t, ln["start"]), min(end, ln["end"])
                held = min(HELD_S, HELD_FRAC * (ln["end"] - ln["start"]))
                if b - a > EPS and a < ln["end"] - held - EPS:
                    rep.err(where, f"{m.get('label', c['move'])} brings a hand to the face during the sung line "
                            f"{ln['text']!r} ({fmt(a)}-{fmt(b)}s, line {fmt(ln['start'])}-{fmt(ln['end'])}s). "
                            f"Put it in a gap between lines or on the held last note (from {fmt(ln['end'] - held)}s)")
    detail = c.get("detail") if isinstance(c.get("detail"), str) else ""
    for frag in quoted(c["text"]):
        key = f" {norm_words(frag)} "
        if not key.strip():
            continue
        hits = [ln for ln in lines if key in ln["norm"]]
        if not hits:
            continue  # not a lyric (for example a quoted "ooh")
        ln = min(hits, key=lambda x: gap_to(t, x))
        if gap_to(t, ln) > LYRIC_NEAR_S:
            rep.warn(where, f"quotes {frag!r} but sits {gap_to(t, ln):.2f}s away from that line "
                     f"({fmt(ln['start'])}-{fmt(ln['end'])}s)")
        if ln["confidence"] == "low" and APPROX not in detail.lower():
            rep.err(where, f"quotes {frag!r} from a low-confidence line ({ln['text']!r}); "
                    f"add \"detail\": \"{APPROX}\"")


def check_take(take, i, moves, tm, rep, seen_ids):
    if not isinstance(take, dict):
        rep.err(f"takes[{i}]", "must be an object")
        return None
    tid = take.get("id")
    where = f"takes[{i}] {tid!r}" if isinstance(tid, str) else f"takes[{i}]"
    if not isinstance(tid, str) or not tid.strip():
        rep.err(where, "needs a non-empty string id")
    else:
        if tid in seen_ids:
            rep.err(where, "duplicate take id")
        seen_ids.add(tid)
        if not re.fullmatch(r"[\w-]+", tid):
            rep.warn(where, "the recorder saves clips as clips/<id>.mp4 and replaces anything but letters, digits, _ and -")
    for k in take:
        if k not in TAKE_KEYS:
            rep.warn(where, f"unknown key {k!r}")
    if not isinstance(take.get("title"), str) or not take["title"].strip():
        rep.err(where, "title must be a non-empty string")
    if not isinstance(take.get("song"), bool):
        rep.err(where, "song must be true or false")
    if not is_num(take.get("duration_s")) or take["duration_s"] <= 0:
        rep.err(where, "duration_s must be a number > 0")
    elif take.get("song") is True and tm["duration"] is not None and abs(take["duration_s"] - tm["duration"]) > 0.5:
        rep.warn(where, f"a song take lasts {fmt(take['duration_s'])}s but the song is {fmt(tm['duration'])}s")
    if not isinstance(take.get("brief"), str):
        rep.err(where, "brief must be a string")
    elif not take["brief"].strip():
        rep.warn(where, "brief is empty")
    setup = take.get("setup")
    if not isinstance(setup, list) or not all(isinstance(s, str) and s.strip() for s in setup):
        rep.err(where, "setup must be a list of non-empty strings")
    cues = take.get("cues")
    if not isinstance(cues, list):
        rep.err(where, "cues must be a list")
        return {"take": take, "where": where, "cues": []}
    if not cues:
        rep.warn(where, "has no cues")
    good = []
    for j, c in enumerate(cues):
        label = ""
        if isinstance(c, dict):
            label = f" (t={fmt(c.get('t'))} {c.get('kind')}{' ' + str(c.get('move')) if c.get('kind') == 'move' else ''})"
        cw = f"{where} cues[{j}]{label}"
        ok = check_cue(c, cw, take, moves, tm, rep)
        if ok is not None:
            check_lyrics(ok, cw, take, moves, tm, rep)
            good.append((j, ok))
    ts = [c["t"] for _, c in good]
    if ts != sorted(ts):
        rep.warn(where, "cues are not sorted by t (the recorder sorts them, but people read this file)")
    mv = sorted(((j, c) for j, c in good if c["kind"] == "move"), key=lambda x: x[1]["t"])
    for (ja, a), (jb, b) in zip(mv, mv[1:]):
        if a["t"] + a["dur"] > b["t"] + EPS:
            rep.err(where, f"move cues overlap: cues[{ja}] {a.get('move')} at {fmt(a['t'])}s runs to "
                    f"{fmt(a['t'] + a['dur'])}s, past cues[{jb}] {b.get('move')} at {fmt(b['t'])}s")
    return {"take": take, "where": where, "cues": [c for _, c in good]}


def check_scenes(scenes, tm, rep):
    if not isinstance(scenes, list):
        rep.err("scenes", "must be a list ([] until the director fills it)")
        return
    ids = set()
    for i, s in enumerate(scenes):
        where = f"scenes[{i}]"
        if not isinstance(s, dict):
            rep.err(where, "must be an object")
            continue
        where = f"scenes[{i}] {s.get('id')!r}"
        for k in SCENE_REQUIRED:
            if k not in s:
                rep.err(where, f"missing {k!r}")
        if s.get("id") in ids:
            rep.err(where, "duplicate scene id")
        ids.add(s.get("id"))
        t0, t1 = s.get("t0"), s.get("t1")
        if is_num(t0) and is_num(t1):
            if t1 <= t0:
                rep.err(where, "t1 must be after t0")
            D = tm["downbeats"]
            for name, x in (("t0", t0), ("t1", t1)):
                on_down = D and abs(D[nearest(D, x)] - x) <= BEAT_TOL
                at_edge = (name == "t0" and abs(x) <= BEAT_TOL) or (
                    name == "t1" and tm["duration"] is not None and abs(x - tm["duration"]) <= BEAT_TOL)
                if D and not on_down and not at_edge:
                    rep.warn(where, f"{name}={fmt(x)} is not on a downbeat")
        elif "t0" in s or "t1" in s:
            rep.err(where, "t0 and t1 must be numbers")
        if "seed" in s and not (isinstance(s["seed"], int) and not isinstance(s["seed"], bool)):
            rep.err(where, "seed must be an integer")
        cam = s.get("camera")
        if "camera" in s and (not isinstance(cam, dict) or "move" not in cam):
            rep.err(where, "camera must be an object with a move")
        elif isinstance(cam, dict) and cam.get("move") not in CAMERA_MOVES:
            rep.warn(where, f"camera.move {cam.get('move')!r} is not one of {', '.join(CAMERA_MOVES)}")


def resolve_like_repo(rel, shots_path):
    p = Path(rel)
    if p.is_absolute():
        return p
    for base in (Path.cwd(), shots_path.resolve().parent.parent):
        if (base / p).exists():
            return base / p
    return None


def check_file(S, shots_path, timing_path, tm, rep):
    if not isinstance(S, dict):
        rep.err("", "the shot list must be a JSON object")
        return {}, []
    for k in TOP_REQUIRED:
        if k not in S:
            rep.err("", f"missing top-level key {k!r}")
    for k in S:
        if k not in TOP_REQUIRED + TOP_OPTIONAL:
            rep.warn("", f"unknown top-level key {k!r}")
    if "version" in S and (S["version"] != 1 or isinstance(S["version"], bool)):
        rep.err("version", f"must be 1, got {S['version']!r}")
    for k in ("song", "timing"):
        if k in S and (not isinstance(S[k], str) or not S[k].strip()):
            rep.err(k, "must be a non-empty path string")
    if isinstance(S.get("song"), str):
        if tm["song"] and S["song"] != tm["song"]:
            rep.warn("song", f"{S['song']!r} differs from the timing file's song {tm['song']!r}")
        if resolve_like_repo(S["song"], shots_path) is None:
            rep.warn("song", f"{S['song']!r} not found from here or the repo root")
    if isinstance(S.get("timing"), str):
        found = resolve_like_repo(S["timing"], shots_path)
        if found is None:
            rep.warn("timing", f"{S['timing']!r} not found from here or the repo root")
        elif found.resolve() != Path(timing_path).resolve():
            rep.warn("timing", f"points at {S['timing']!r}, but you validated against {timing_path}")
    if "sync_offset_s" in S and not is_num(S["sync_offset_s"]):
        rep.err("sync_offset_s", "must be a number")
    for k in ("identity_lock", "negative"):
        if k in S and not isinstance(S[k], str):
            rep.err(k, "must be a string")
    moves = check_moves(S.get("moves"), rep) if "moves" in S else {}
    takes = S.get("takes")
    checked = []
    if "takes" in S:
        if not isinstance(takes, list) or not takes:
            rep.err("takes", "must be a non-empty list")
        else:
            seen = set()
            for i, tk in enumerate(takes):
                r = check_take(tk, i, moves, tm, rep, seen)
                if r:
                    checked.append(r)
    if "scenes" in S:
        check_scenes(S["scenes"], tm, rep)
    used = {c.get("move") for r in checked for c in r["cues"] if c["kind"] == "move"}
    unused = sorted(set(moves) - used)
    if unused:
        rep.warn("moves", f"defined but never cued: {', '.join(unused)}")
    return moves, checked


# ---------------------------------------------------------------- output
def line_at(tm, t):
    for ln in tm["lines"]:
        if ln["start"] <= t < ln["end"]:
            return f"{ln['text']} [{ln['confidence']}]"
    return "-"


def print_sheet(checked, moves, tm):
    for r in checked:
        take = r["take"]
        song = take.get("song") is True
        print(f"\n{take.get('id')}  ({'song' if song else 'no song'}, {fmt(take.get('duration_s'))}s)")
        print(f"  {'t':>7} {'to':>7} {'bar':>5}  {'kind':<9} {'what':<44} {'sung now' if song else ''}")
        for c in sorted(r["cues"], key=lambda c: c["t"]):
            what = moves.get(c.get("move"), {}).get("label", c.get("move")) if c["kind"] == "move" else c["text"]
            if c["kind"] == "move" and c["text"].strip().lower() != str(what).lower():
                what = f"{what}: {c['text']}"
            if c.get("detail") and APPROX in c["detail"].lower():
                what += " (~)"
            what = what if len(what) <= 44 else what[:43] + "…"
            print(f"  {c['t']:7.3f} {c['t'] + c['dur']:7.3f} {bar_beat(tm, c['t']) if song else '':>5}  "
                  f"{c['kind']:<9} {what:<44} {line_at(tm, c['t']) if song else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shots", help="shots/<song>.json")
    ap.add_argument("timing", help="songs/<song>.timing.json")
    ap.add_argument("--sheet", action="store_true", help="print every take's cue sheet with bar.beat and the sung line")
    args = ap.parse_args()
    rep = Report()
    T = load_json(args.timing, "timing")
    S = load_json(args.shots, "shots")
    tm = check_timing(T, rep)
    moves, checked = check_file(S, Path(args.shots), args.timing, tm, rep)

    if args.sheet:
        print_sheet(checked, moves, tm)
        print()
    for r in checked:
        cues = r["cues"]
        n = {k: sum(1 for c in cues if c["kind"] == k) for k in KINDS}
        take = r["take"]
        print(f"  {str(take.get('id')):<14} {'song' if take.get('song') is True else 'no song':<8} "
              f"{fmt(take.get('duration_s')):>5}s  {len(cues):>3} cues: {n['move']} moves, "
              f"{n['direction']} directions, {n['emote']} emotes")
    for w in rep.warnings:
        print(f"WARNING {w}")
    for e in rep.errors:
        print(f"ERROR   {e}")
    total = sum(len(r["cues"]) for r in checked)
    if rep.errors:
        print(f"FAIL: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s) in {args.shots}")
        return 1
    print(f"OK: {args.shots} matches the contract: {len(checked)} takes, {total} cues, {len(moves)} moves"
          f"{f', {len(rep.warnings)} warning(s)' if rep.warnings else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
