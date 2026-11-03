#!/usr/bin/env python3
"""Song timing for the coach overlay: beats, downbeats, sections and per-line lyric times.

Usage:
  uv run --with librosa --with soundfile --with mlx-whisper python -I tools/song_timing.py \
      songs/in-the-loop-devagar.mp3 songs/in-the-loop-devagar.md -o songs/in-the-loop-devagar.timing.json \
      [--bpm 92] [--model mlx-community/whisper-small-mlx] [--verify] [--debug-dir DIR]

Writes the timing contract the recorder overlay and the shot list read:
  {"song", "duration_s", "bpm", "beats", "downbeats",
   "sections": [{"name", "start", "end"}],
   "lines": [{"text", "start", "end", "section", "confidence", "words": [{"w", "start", "end"}]}]}

Line confidence:
  high  at least 60% of the line's words were heard by Whisper at consistent times
  med   some of its words were heard
  low   none were heard; the time is interpolated between its neighbours (a guess, not a measurement)

--verify cuts 2.5 s of audio at every line start with ffmpeg and transcribes it twice: prompted with that
line, and unprompted as a control (a prompted Whisper happily copies its prompt, so a prompted match alone
proves little). --debug-dir dumps every window transcript, word cluster and alignment decision.
"""
import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
import zlib
from pathlib import Path

import numpy as np

BEAT_SR, BEAT_HOP = 44100, 256  # 5.8 ms analysis frames
ASR_SR = 16000
# Words that never count as evidence on their own: a lone "the" or "is" matches anywhere.
STOP = {"a", "an", "the", "is", "are", "in", "it", "me", "my", "and", "to", "of", "i", "im", "ive",
        "on", "at", "be", "eu", "e", "o"}
SECTION_NAMES = ("intro", "verse", "chorus", "outro")


# ---------- text helpers ----------

def norm(word):
    """Lowercase, strip accents and punctuation: "está," -> "esta", "I'm" -> "im"."""
    word = unicodedata.normalize("NFKD", word)
    word = "".join(c for c in word if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", word)


def display(word):
    return word.strip(" \t\"'“”‘’()[]{},.;:!?¡¿…-")


def syllables(text):
    """Rough syllable count (vowel groups, minus an English silent final e). Only used to weight guesses."""
    total = 0
    for raw in text.split():
        w = norm(raw)
        if not w:
            continue
        n = len(re.findall(r"[aeiouy]+", w))
        if n > 1 and w.endswith("e") and not w.endswith(("le", "ee")) and re.search(r"[^aeiouy]e$", w):
            n -= 1
        total += max(1, n)
    return max(1, total)


def section_name(header):
    h = header.strip().lower()
    for name in SECTION_NAMES:
        if h.startswith(name):
            return name
    if "hook" in h or "chorus" in h:
        return "chorus"
    return "verse"


def parse_lyrics(md_path):
    text = Path(md_path).read_text(encoding="utf-8")
    m = re.search(r"^##\s+Lyrics\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        sys.exit(f"{md_path}: no '## Lyrics' section")
    section, lines = "verse", []
    for raw in m.group(1).splitlines():
        s = raw.strip()
        if not s:
            continue
        header = re.fullmatch(r"\[([^\]]+)\]", s)
        if header:
            section = section_name(header.group(1))
            continue
        words = [w for w in s.split() if norm(w)]
        lines.append({"text": s, "section": section, "words": words})
    if not lines:
        sys.exit(f"{md_path}: '## Lyrics' has no lines")
    return lines


# ---------- beats and downbeats ----------

def find_beats(y, sr, bpm_hint, raw_only=False):
    """librosa beat_track, then a steadiness check. AI-rendered songs hold one tempo, so when the
    detected beats are steady over most of the song we fit one constant-tempo grid through them
    (least squares) and use that: it removes the tracker's frame jitter and repairs stretches where
    the tracker wandered (typically a sparse intro)."""
    import librosa
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=BEAT_HOP)
    tempo, raw = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=BEAT_HOP,
                                         start_bpm=bpm_hint, units="time", trim=False)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=BEAT_HOP)[0]
    loud = np.flatnonzero(librosa.amplitude_to_db(rms, ref=np.max) > -40)
    first_sound = float(librosa.frames_to_time(loud[0], sr=sr, hop_length=BEAT_HOP))
    last_sound = float(librosa.frames_to_time(loud[-1], sr=sr, hop_length=BEAT_HOP))
    raw = raw[(raw >= first_sound - 0.05) & (raw <= last_sound)]
    info = {"librosa_tempo_bpm": round(float(np.atleast_1d(tempo)[0]), 2), "raw_beats": [round(float(b), 3) for b in raw],
            "first_sound_s": round(first_sound, 3), "last_sound_s": round(last_sound, 3)}
    if len(raw) < 8:
        info["mode"] = "raw (too few beats to check)"
        return raw, float(np.median(np.diff(raw))) if len(raw) > 1 else 60.0 / bpm_hint, info

    ibi = np.diff(raw)
    med = float(np.median(ibi))
    steady = np.abs(ibi / med - 1) <= 0.08
    best, cur = (0, 0), None
    for i, ok in enumerate(steady):  # longest run of steady intervals: beats best[0]..best[1]
        cur = ((cur[0] if cur else i), i + 1) if ok else None
        if cur and cur[1] - cur[0] > best[1] - best[0]:
            best = cur
    i0, i1 = best
    if i1 - i0 < 4:
        info["mode"] = "raw (no steady stretch of beats)"
        return raw, med, info
    k = np.arange(i0, i1 + 1)
    period, offset = np.polyfit(k, raw[i0:i1 + 1], 1)
    resid = raw[i0:i1 + 1] - (offset + period * k)
    coverage = float((raw[i1] - raw[i0]) / max(1e-6, last_sound - first_sound))
    unsteady = [round(float(raw[i]), 3) for i in range(len(ibi)) if not steady[i]]
    info.update({"median_ibi_s": round(med, 4), "steady_run_s": [round(float(raw[i0]), 3), round(float(raw[i1]), 3)],
                 "steady_coverage": round(coverage, 3), "grid_resid_rms_ms": round(float(np.sqrt(np.mean(resid ** 2))) * 1000, 1),
                 "grid_resid_max_ms": round(float(np.max(np.abs(resid))) * 1000, 1), "unsteady_intervals_start_at": unsteady})
    if not raw_only and coverage >= 0.7 and np.sqrt(np.mean(resid ** 2)) < 0.03:
        kmin = int(np.ceil((max(0.0, first_sound - 0.05) - offset) / period))
        kmax = int(np.floor((last_sound - offset) / period))
        info["mode"] = "constant-tempo grid fitted to the steady run"
        return offset + period * np.arange(kmin, kmax + 1), float(period), info
    info["mode"] = "raw librosa beats (" + ("--raw-beats" if raw_only else "tempo not steady enough to grid") + ")"
    return raw, med, info


def find_downbeats(y, sr, beats):
    """4/4. The brief's cue is the phase with the most low-frequency onset energy (kick on 1). That cue is
    fooled by grooves that hit hardest on beat 3 or on 2 and 4 (pagode surdo, half-time R&B), so it is
    checked against two more: the snare/clap backbeat (mid/high onsets) marks beats 2 and 4, which leaves
    two candidates for beat 1, and the harmony (chords change at the bar line) picks between them when it
    clearly prefers one. Every score is reported."""
    import librosa
    bf = librosa.time_to_frames(beats, sr=sr, hop_length=BEAT_HOP)
    S = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=2048, hop_length=BEAT_HOP, n_mels=128, fmin=20, fmax=sr / 2)
    mf = librosa.mel_frequencies(n_mels=128, fmin=20, fmax=sr / 2)
    SdB = librosa.power_to_db(S, ref=np.max)

    def onsets_at_beats(lo, hi):
        env = librosa.onset.onset_strength(S=SdB[(mf >= lo) & (mf < hi)], sr=sr, hop_length=BEAT_HOP)
        return np.array([env[max(0, f - 2):f + 3].max() for f in bf])

    low = onsets_at_beats(20, 150)
    backbeat = onsets_at_beats(800, 6000)
    chroma = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=sr, hop_length=BEAT_HOP)
    seg = librosa.util.sync(chroma, bf, aggregate=np.mean)  # column j+1 = the beat-j..j+1 segment
    seg = seg / (np.linalg.norm(seg, axis=0, keepdims=True) + 1e-9)
    change = (1 - (seg[:, 1:] * seg[:, :-1]).sum(axis=0))[:len(beats)]  # harmony change entering beat j

    idx = np.arange(len(beats))

    def by_phase(v):
        return np.array([float(v[idx % 4 == p].mean()) for p in range(4)])

    L, B, H = by_phase(low), by_phase(backbeat), by_phase(change)
    spec_phase = int(np.argmax(L))
    pair = max([(0, 2), (1, 3)], key=lambda pr: B[pr[0]] + B[pr[1]])
    other = (1, 3) if pair == (0, 2) else (0, 2)
    pair_margin = (B[pair[0]] + B[pair[1]]) / max(1e-9, B[other[0]] + B[other[1]])
    if pair_margin < 1.08:
        phase, why = spec_phase, "low-frequency onsets (backbeat unclear)"
    else:
        a, b = sorted(other, key=lambda p: -H[p])
        if H[a] >= 1.25 * H[b]:
            phase, why = a, "harmony changes, among the two non-backbeat phases"
        else:
            phase, why = max(other, key=lambda p: L[p]), "low-frequency onsets, among the two non-backbeat phases"
    info = {"phase": phase, "chosen_by": why, "low_freq_onset_by_phase": L.round(3).tolist(),
            "low_freq_only_phase": spec_phase, "backbeat_onset_by_phase": B.round(3).tolist(),
            "backbeat_phases": list(pair), "backbeat_margin": round(float(pair_margin), 3),
            "harmony_change_by_phase": H.round(4).tolist()}
    return beats[phase::4], info


# ---------- lyrics: windowed Whisper ----------

def looks_like_loop(text):
    """Whisper's music hallucinations: 'oh, oh, oh, ...', 'da da da ...'."""
    if not text:
        return False
    toks = [norm(t) for t in text.split() if norm(t)]
    run = best = 1
    for a, b in zip(toks, toks[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    ratio = len(text) / max(1, len(zlib.compress(text.encode("utf-8"))))
    return best >= 4 or ratio > 2.4


def transcribe_windows(y16, model, prompt, win, hop, tag, first_id=0):
    """Overlapping short windows (Whisper on a whole song loops and hallucinates). Word times are offset
    by the window start. The unprompted control pass runs without the no-speech skip: its job is to say
    what it hears, and its words only ever count when they agree with the prompted pass or the lyrics."""
    import mlx_whisper
    dur = len(y16) / ASR_SR
    starts = list(np.arange(0.0, max(0.0, dur - win) + 1e-6, hop))
    if starts[-1] + win < dur - 0.25:
        starts.append(dur - win)
    gates = {} if prompt else {"no_speech_threshold": None, "logprob_threshold": None}
    windows, words = [], []
    for n, s in enumerate(starts):
        wi = first_id + n
        e = min(dur, s + win)
        r = mlx_whisper.transcribe(y16[int(s * ASR_SR):int(e * ASR_SR)].astype(np.float32), path_or_hf_repo=model,
                                   word_timestamps=True, condition_on_previous_text=False, language="en",
                                   initial_prompt=prompt, temperature=0.0, verbose=None, **gates)
        text = r.get("text", "").strip()
        loop = looks_like_loop(text)
        windows.append({"pass": tag, "start": round(float(s), 2), "end": round(float(e), 2), "text": text,
                        "dropped_as_loop": loop})
        if loop:
            continue
        for segm in r.get("segments", []):
            for w in segm.get("words", []):
                ws, we = float(w["start"]) + s, float(w["end"]) + s
                words.append({"word": w["word"].strip(), "tok": norm(w["word"]), "start": ws, "end": we,
                              "p": float(w.get("probability", 0.0)), "win": wi, "pass": tag,
                              # a first word glued to the window start, a last word cut by the window end, or a
                              # word smeared over 2 s has an unreliable time; it still counts as a hearing
                              "clamped": (s > 0 and ws - s < 0.08) or we - ws > 2.0,
                              "truncated": (e < dur and e - we < 0.08) or we - ws > 2.0})
    return windows, [w for w in words if w["tok"]]


def similar(a, b):
    """Same word as heard by two passes: 'loop' ~ 'loo' ~ 'looped', 'holding' ~ 'holdin'."""
    if a == b:
        return True
    if min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def cluster_words(words):
    """Dedupe overlapping windows: the same word heard by several windows at about the same time is one
    word. Its time is the median over the hearings that were not clipped; its support is how many windows
    heard it. A prompted Whisper copies its prompt into unclear singing ("devagar, devagar" at every window
    start), so a prompted word is strong evidence only when the unprompted pass heard a similar word at the
    same time, or when several windows heard it with high probability. Weaker clusters are kept for
    bridging a gap next to a strong match (see align_lines)."""
    clusters = []
    for w in sorted(words, key=lambda w: w["start"]):
        w_end = max(w["end"], w["start"] + 0.15)
        for c in reversed(clusters[-24:]):
            if (c["tok"] == w["tok"] and w["win"] not in c["wins"] and w["start"] < c["hi"] + 0.05
                    and c["lo"] < w_end + 0.05 and abs(w["start"] - c["anchor"]) <= 0.7):
                c["members"].append(w)
                c["wins"].add(w["win"])
                c["lo"], c["hi"] = min(c["lo"], w["start"]), max(c["hi"], w_end)
                break
        else:
            clusters.append({"tok": w["tok"], "members": [w], "wins": {w["win"]}, "lo": w["start"], "hi": w_end,
                             "anchor": w["start"]})
    plain_words = [w for w in words if w["pass"] == "plain"]
    out = []
    for c in clusters:
        m = c["members"]
        starts = [x["start"] for x in m if not x["clamped"]] or [x["start"] for x in m]
        ends = [x["end"] for x in m if not x["truncated"]] or [x["end"] for x in m]
        p = max(x["p"] for x in m)
        support = len(c["wins"])
        start = float(np.median(starts))
        end = max(float(np.median(ends)), start + 0.05)
        prompted = any(x["pass"] == "prompted" for x in m)
        heard_plain = any(similar(w["tok"], c["tok"]) and w["start"] < end + 0.4 and start < w["end"] + 0.4
                          for w in plain_words)
        clean = any(not x["clamped"] and not x["truncated"] for x in m)
        if heard_plain and (support >= 2 or (p >= 0.75 and clean)) and p >= 0.3:
            evidence = "strong" if prompted else ("strong" if p >= 0.75 else "weak")
        elif prompted and support >= 2 and p >= 0.85:
            evidence = "strong"
        elif support >= 2 and p >= 0.3:
            evidence = "weak"
        else:
            evidence = "none"
        out.append({"tok": c["tok"], "word": m[0]["word"], "start": start, "end": end, "p": round(p, 3),
                    "support": support, "passes": sorted({x["pass"] for x in m}), "heard_plain": heard_plain,
                    "evidence": evidence})
    return sorted(out, key=lambda c: c["start"])


def align_lines(lines, clusters):
    """Lyric words vs strongly heard words with difflib.SequenceMatcher on normalized tokens. Stopwords are
    masked for this pass (a lone "is" would otherwise win a tie and block the real match next to it).
    Then gaps are bridged word by word: an unmatched lyric word right next to a matched word of its line
    is matched to a heard cluster of the same word (weak evidence is enough here) that sits between its
    neighbours in time."""
    lyr = [(li, wi, norm(w)) for li, line in enumerate(lines) for wi, w in enumerate(line["words"])]
    strong = [c for c in clusters if c["evidence"] == "strong"]
    a_toks = [t if t not in STOP else f"#a{k}" for k, (_, _, t) in enumerate(lyr)]
    b_toks = [c["tok"] if c["tok"] not in STOP else f"#b{k}" for k, c in enumerate(strong)]
    sm = difflib.SequenceMatcher(None, a_toks, b_toks, autojunk=False)
    matched, blocks = {}, []
    for blk in sm.get_matching_blocks():
        if blk.size == 0:
            continue
        blocks.append({"lyric": " ".join(lyr[blk.a + k][2] for k in range(blk.size)),
                       "heard_at": round(strong[blk.b]["start"], 2), "how": "difflib"})
        for k in range(blk.size):
            li, wi, _ = lyr[blk.a + k]
            matched[(li, wi)] = strong[blk.b + k]
    usable = [c for c in clusters if c["evidence"] in ("strong", "weak")]
    used = {id(c) for c in matched.values()}
    grew = True
    while grew:
        grew = False
        for li, line in enumerate(lines):
            for wi, word in enumerate(line["words"]):
                if (li, wi) in matched:
                    continue
                prev = next((matched[(li, k)] for k in range(wi - 1, -1, -1) if (li, k) in matched), None)
                nxt = next((matched[(li, k)] for k in range(wi + 1, len(line["words"])) if (li, k) in matched), None)
                if not ((li, wi - 1) in matched or (li, wi + 1) in matched):
                    continue
                lo = prev["end"] - 0.15 if prev else (nxt["start"] - 2.0)
                hi = nxt["start"] + 0.05 if nxt else (prev["end"] + 2.0)
                tok = norm(word)
                cands = [c for c in usable if id(c) not in used and c["tok"] == tok and lo <= c["start"] <= hi]
                if cands:
                    best = max(cands, key=lambda c: (c["support"], c["p"]))
                    matched[(li, wi)] = best
                    used.add(id(best))
                    blocks.append({"lyric": tok, "heard_at": round(best["start"], 2), "how": "bridge"})
                    grew = True
    for li, line in enumerate(lines):
        hits = sorted(((wi, matched[(li, wi)]) for wi in range(len(line["words"])) if (li, wi) in matched),
                      key=lambda x: x[0])
        # A sung line is compact: drop a matched word that sits far from the rest of its line.
        limit = max(6.0, 0.9 * syllables(" ".join(line["words"])))
        while len(hits) > 1 and max(c["end"] for _, c in hits) - min(c["start"] for _, c in hits) > limit:
            med = float(np.median([c["start"] for _, c in hits]))
            hits.remove(max(hits, key=lambda h: abs(h[1]["start"] - med)))
        line["hits"] = hits
        if hits:
            frac = len(hits) / len(line["words"])
            line["confidence"] = "high" if frac >= 0.6 else "med"
            line["start"] = min(c["start"] for _, c in hits)
            line["end"] = max(c["end"] for _, c in hits)
        else:
            line["confidence"] = "low"
    # Matched lines must come in lyric order without overlapping; demote whichever has less evidence.
    changed = True
    while changed:
        changed = False
        anchored = [i for i, l in enumerate(lines) if l["confidence"] != "low"]
        for a, b in zip(anchored, anchored[1:]):
            if lines[b]["start"] <= lines[a]["end"] - 0.05:
                loser = a if len(lines[a]["hits"]) < len(lines[b]["hits"]) else b
                lines[loser].update(confidence="low", hits=[])
                changed = True
                break
    return blocks


def singing_rate(lines):
    """Seconds per syllable over the heard stretch of each heard line (default 0.4)."""
    rates = []
    for line in lines:
        if line["confidence"] != "low" and len(line["hits"]) >= 2:
            first, last = line["hits"][0][0], line["hits"][-1][0]
            syl = syllables(" ".join(line["words"][first:last + 1]))
            rates.append((line["end"] - line["start"]) / syl)
    return float(np.clip(np.median(rates), 0.2, 0.7)) if rates else 0.4


def interpolate(lines, t_begin, t_end, floors=None):
    """Spread each run of unheard lines over the gap between its heard neighbours, by syllable count.
    A heard neighbour whose own first or last words went unheard still sings them, so the gap is
    shrunk by those words at the measured singing rate. floors {line index: t} pins the first line of a
    section to start at its section start (a run that crosses a section start is split there)."""
    floors = floors or {}
    rate = singing_rate(lines)
    n, i = len(lines), 0
    while i < n:
        if lines[i]["confidence"] != "low":
            i += 1
            continue
        j = i
        while j < n and lines[j]["confidence"] == "low" and not (j > i and j in floors):
            j += 1
        a, b = t_begin, t_end
        if i > 0:
            prev = lines[i - 1]
            tail = prev["hits"][-1][0] + 1 if prev["hits"] else len(prev["words"])
            a = prev["end"] + (rate * syllables(" ".join(prev["words"][tail:])) if tail < len(prev["words"]) else 0)
        if j < n and j in floors and lines[j]["confidence"] == "low":
            b = floors[j]
        elif j < n:
            nxt = lines[j]
            b = nxt["start"] - rate * (syllables(" ".join(nxt["words"][:nxt["hits"][0][0]])) if nxt["hits"][0][0] else 0)
        if i in floors:
            a = max(a, floors[i])
        a, b = min(a, t_end), max(min(b, t_end), min(a, t_end))
        need = 0.15 * (j - i)
        if b - a < need:  # no room (lines missing from the audio?): pack them in just before b, in order
            a = max(lines[i - 1]["start"] + 0.05 if i > 0 else 0.0, b - need)
            b = max(b, a + need)
        weights = [syllables(" ".join(l["words"])) for l in lines[i:j]]
        cum = np.concatenate([[0], np.cumsum(weights)]) / sum(weights)
        span = max(0.0, b - a)
        gap = min(0.2, span / (4 * (j - i) + 1))
        for k, line in enumerate(lines[i:j]):
            line["start"] = a + span * cum[k] + (gap if (i + k) > 0 else 0.0)
            line["end"] = min(max(line["start"] + 0.1, a + span * cum[k + 1] - gap), max(b, line["start"] + 0.02))
        i = j


def snap_to_downbeat(t, downbeats, beat_s):
    """Bar line at or before t, except a pickup: a line that starts at most 1.25 beats before a bar line
    belongs to that bar (Whisper also tends to start a first word early), so the section starts there."""
    after = [d for d in downbeats if d > t + 1e-6]
    if after and after[0] - t <= 1.25 * beat_s:
        return float(after[0])
    before = [d for d in downbeats if d <= t + 1e-6]
    return float(before[-1]) if before else 0.0


def loudness_change(y, sr, downbeats, beat_s):
    """Mean loudness (dB) in the bar after each downbeat minus the bar before it."""
    import librosa
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=BEAT_HOP)[0]
    db = librosa.amplitude_to_db(rms, ref=np.max(rms))
    t = librosa.frames_to_time(np.arange(len(db)), sr=sr, hop_length=BEAT_HOP)
    bar = 4 * beat_s
    out = {}
    for d in downbeats:
        after, before = db[(t >= d) & (t < d + bar)], db[(t >= d - bar) & (t < d)]
        out[float(d)] = float(after.mean() - before.mean()) if len(after) and len(before) else 0.0
    return out


def build_sections(lines, downbeats, beat_s, duration, change):
    """Section i starts on the bar line of its first line (snap_to_downbeat). When that first line was not
    heard its time is only a guess, so the start comes from the audio instead: of the bar lines within a
    bar of the guess, the one where the loudness changes most (arrangements change at section starts)."""
    sections, how = [], []
    for li, line in enumerate(lines):
        if sections and sections[-1]["name"] == line["section"]:
            continue
        if not sections:
            start, why = 0.0, "song start"
        elif line["confidence"] != "low":
            start, why = snap_to_downbeat(line["start"], downbeats, beat_s), "first line heard; its bar line"
        else:
            near = [d for d in downbeats if abs(d - line["start"]) <= 4 * beat_s + 1e-6] or \
                   [snap_to_downbeat(line["start"], downbeats, beat_s)]
            start = max(near, key=lambda d: abs(change.get(float(d), 0.0)))
            why = f"first line not heard; bar line with the biggest loudness change near {line['start']:.2f}s " \
                  f"({', '.join(f'{d:.2f}s: {change.get(float(d), 0.0):+.1f} dB' for d in near)})"
        if sections and start <= sections[-1]["start"]:
            start, why = line["start"], why + "; collided with previous section, used the line time"
        sections.append({"name": line["section"], "start": float(start), "first_line": li})
        how.append({"section": line["section"], "start": round(float(start), 3), "why": why})
    for a, b in zip(sections, sections[1:]):
        a["end"] = b["start"]
    sections[-1]["end"] = duration
    return sections, how


# ---------- verification ----------

def verify(song, lines, model, clip_s=2.5):
    """Cut clip_s seconds at each line start; transcribe prompted with the line and unprompted (control)."""
    import mlx_whisper
    report = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, line in enumerate(lines):
            clip = Path(tmp) / f"line{i}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{line['start']:.3f}", "-t", str(clip_s), "-i", song,
                            "-ac", "1", "-ar", str(ASR_SR), str(clip)], check=True)
            heard = {}
            for tag, prompt in (("prompted", line["text"]), ("control", None)):
                r = mlx_whisper.transcribe(str(clip), path_or_hf_repo=model, word_timestamps=True, language="en",
                                           condition_on_previous_text=False, initial_prompt=prompt,
                                           temperature=0.0, verbose=None)
                heard[tag] = [(w["word"].strip(), float(w.get("probability", 0.0)))
                              for s in r.get("segments", []) for w in s.get("words", [])]
            lyr = [norm(w) for w in line["words"]]
            first = [t for t in lyr[:2]]
            p_toks = [norm(w) for w, _ in heard["prompted"]][:4]
            prompted_ok = bool(first) and (first[0] in p_toks[:3] or (len(first) > 1 and first[1] in p_toks))
            content = {t for t in lyr[:4] if t not in STOP} or set(lyr[:2])
            control_toks = {norm(w) for w, _ in heard["control"]}
            control_ok = bool(content & control_toks)
            verdict = ("confirmed" if prompted_ok and control_ok else
                       "prompted only (could be prompt-copy)" if prompted_ok else
                       "control only" if control_ok else "not heard")
            report.append({"line": line["text"], "claimed_start": round(line["start"], 2), "confidence": line["confidence"],
                           "heard_prompted": " ".join(f"{w}({p:.2f})" for w, p in heard["prompted"][:8]),
                           "heard_control": " ".join(f"{w}({p:.2f})" for w, p in heard["control"][:8]),
                           "verdict": verdict})
    return report


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("song")
    ap.add_argument("lyrics_md")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--bpm", type=float, default=92.0, help="tempo hint for the beat tracker (the tag)")
    ap.add_argument("--model", default="mlx-community/whisper-small-mlx")
    ap.add_argument("--window", type=float, default=6.0, help="ASR window seconds")
    ap.add_argument("--hop", type=float, default=2.0, help="ASR window hop seconds")
    ap.add_argument("--raw-beats", action="store_true", help="keep librosa's beats as detected, no tempo grid")
    ap.add_argument("--verify", action="store_true", help="spot-check every line start (prints a report)")
    ap.add_argument("--verify-model", help="Whisper model for --verify (default: --model)")
    ap.add_argument("--debug-dir", help="dump windows, clusters and alignment here")
    ap.add_argument("--song-field", help="value for the json 'song' field (default: the song path as given)")
    args = ap.parse_args()

    import librosa

    lines = parse_lyrics(args.lyrics_md)
    y, sr = librosa.load(args.song, sr=BEAT_SR, mono=True)
    duration = len(y) / sr

    beats, beat_s, beat_info = find_beats(y, sr, args.bpm, raw_only=args.raw_beats)
    downbeats, db_info = find_downbeats(y, sr, beats)
    bpm = 60.0 / beat_s

    prompt = ". ".join(re.sub(r"[()\[\]]", "", l["text"]).strip(" .") for l in lines) + "."
    y16 = librosa.resample(y, orig_sr=sr, target_sr=ASR_SR)
    windows, words = transcribe_windows(y16, args.model, prompt, args.window, args.hop, "prompted")
    plain_windows, plain_words = transcribe_windows(y16, args.model, None, args.window, args.hop, "plain",
                                                    first_id=len(windows))
    windows, words = windows + plain_windows, words + plain_words
    clusters = cluster_words(words)
    blocks = align_lines(lines, clusters)
    interpolate(lines, 0.0, beat_info["last_sound_s"])  # first guess for unheard lines
    raw_sections, section_how = build_sections(lines, downbeats, beat_s, duration,
                                               loudness_change(y, sr, downbeats, beat_s))
    interpolate(lines, 0.0, beat_info["last_sound_s"],  # again, inside the section bounds
                floors={s["first_line"]: s["start"] for s in raw_sections[1:]})
    sections = [{"name": s["name"], "start": round(s["start"], 3), "end": round(s["end"], 3)} for s in raw_sections]

    out_lines = []
    for line in lines:
        item = {"text": line["text"], "start": round(line["start"], 3), "end": round(line["end"], 3),
                "section": line["section"], "confidence": line["confidence"]}
        if line["hits"]:
            item["words"] = [{"w": display(line["words"][wi]), "start": round(c["start"], 3), "end": round(c["end"], 3)}
                             for wi, c in line["hits"]]
        out_lines.append(item)
    timing = {"song": args.song_field or args.song, "duration_s": round(duration, 3), "bpm": round(bpm, 2),
              "beats": [round(float(b), 3) for b in beats], "downbeats": [round(float(d), 3) for d in downbeats],
              "sections": sections, "lines": out_lines}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(timing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    starts = [l["start"] for l in out_lines]
    problems = []
    if any(b <= a for a, b in zip(starts, starts[1:])):
        problems.append("line starts are not strictly increasing")
    if any(l["start"] < 0 or l["end"] > duration for l in out_lines):
        problems.append("a line falls outside the song")
    print(f"wrote {args.out}: {len(beats)} beats at {bpm:.2f} bpm ({beat_info['mode']}; librosa said "
          f"{beat_info['librosa_tempo_bpm']}), downbeat phase {db_info['phase']} by {db_info['chosen_by']} "
          f"(low-frequency cue alone: phase {db_info['low_freq_only_phase']})")
    for s, h in zip(sections, section_how):
        print(f"  section {s['name']:<6} {s['start']:6.2f} - {s['end']:6.2f}  ({h['why']})")
    for l in out_lines:
        print(f"  {l['confidence']:<4} {l['start']:6.2f} - {l['end']:6.2f}  {l['text']}")
    for p in problems:
        print(f"  PROBLEM: {p}")

    report = verify(args.song, lines, args.verify_model or args.model) if args.verify else None
    if report:
        print("spot checks (2.5 s from each line start):")
        for r in report:
            print(f"  {r['claimed_start']:6.2f} [{r['confidence']}] {r['line']!r}\n"
                  f"        prompted: {r['heard_prompted'] or '-'}\n        control:  {r['heard_control'] or '-'}\n"
                  f"        -> {r['verdict']}")
    if args.debug_dir:
        d = Path(args.debug_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "debug.json").write_text(json.dumps({
            "beats": beat_info, "downbeats": db_info, "sections": section_how, "windows": windows,
            "clusters": clusters, "blocks": blocks,
            "verify": report}, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
