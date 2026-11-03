#!/usr/bin/env python3
"""
photos_scout.py: let the pipeline look at the owner's macOS Photos library for
  (a) identity reference images of the performer (Photos' People faces), and
  (b) taste signals for the music video's looks (favorites, albums, keywords, palettes).

Read-only by default. NOTHING is copied out of the library unless you pass --export.

Run (no install step; uv fetches osxphotos + Pillow into its cache on first use):
  uv run --python 3.12 --with osxphotos --with pillow python -I tools/photos_scout.py stats
  uv run --python 3.12 --with osxphotos --with pillow python -I tools/photos_scout.py identity --person "Name" --n 12
  uv run --python 3.12 --with osxphotos --with pillow python -I tools/photos_scout.py inspire --favorites --n 12
  uv run --python 3.12 --with osxphotos --with pillow python -I tools/photos_scout.py selftest

Every subcommand takes --json and --library. Docs: docs/photos.md.
Exit codes: 0 ok · 1 unexpected error · 2 usage / not found · 3 Photos access blocked (TCC) · 4 selftest failed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD_DIR = ROOT / "build" / "photos"
REFS_DIR = ROOT / "reference-master" / "refs"
LOOKS_JSON = ROOT / "reference-master" / "looks.json"
DEFAULT_PALETTE = ["#7a4a2a", "#d9622b", "#ff9f43", "#ffb347"]
UV_CMD = "uv run --python 3.12 --with osxphotos --with pillow python -I tools/photos_scout.py"
UNKNOWN = "_UNKNOWN_"  # osxphotos' bucket for faces Photos detected but nobody named

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_ACCESS, EXIT_SELFTEST = 0, 1, 2, 3, 4

# contact-sheet theme (dark, warm, monospace)
BG, FG, DIM, ACCENT = (20, 18, 16), (232, 220, 200), (150, 138, 120), (255, 159, 67)
FONT_PATHS = ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Monaco.ttf")


class ScoutError(Exception):
    """A message for the user. Printed on one or a few lines, never as a traceback."""

    def __init__(self, msg: str, code: int = EXIT_ERROR):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- library access (TCC)


def access_fix(lib: Path, err: str) -> str:
    return (
        f"can't read the Photos library at {lib}\n"
        f"  {err}\n"
        "macOS privacy protection (TCC) blocks direct reads of the Photos library. Fix, then re-run:\n"
        "  1. System Settings > Privacy & Security > Full Disk Access: turn ON the app you run this from\n"
        "     (Terminal, iTerm, c11, or the Claude app), then quit and reopen that app.\n"
        "  2. If it still fails: System Settings > Privacy & Security > Photos: give that app Full Access.\n"
        '  3. Or point at a copy of the library: --library "/path/to/Photos Library.photoslibrary"'
    )


def resolve_library(arg: str | None) -> Path:
    """--library wins; else the library Photos opened last; else the system library; else ~/Pictures."""
    if arg:
        return Path(arg).expanduser()
    try:
        from osxphotos.utils import get_last_library_path, get_system_library_path

        for fn in (get_last_library_path, get_system_library_path):
            try:
                v = fn()
            except Exception:
                v = None
            if v:
                return Path(v)
    except Exception:
        pass
    return Path.home() / "Pictures" / "Photos Library.photoslibrary"


def probe_access(lib: Path) -> Path:
    """Read 16 bytes of Photos.sqlite. Under TCC this is where EPERM shows up. Returns the db path."""
    db = lib / "database" / "Photos.sqlite"
    try:
        if not lib.exists():
            raise ScoutError(
                f'no Photos library at "{lib}"\n  pass --library "/path/to/Photos Library.photoslibrary"', EXIT_USAGE
            )
        with open(db, "rb") as fh:
            head = fh.read(16)
    except ScoutError:
        raise
    except OSError as e:  # PermissionError(EPERM) is the TCC signature; EACCES, ENOENT land here too
        raise ScoutError(access_fix(lib, f"{e.__class__.__name__}: {e}"), EXIT_ACCESS) from None
    if not head.startswith(b"SQLite format 3"):
        raise ScoutError(f"{db} is not a SQLite database. Is that a Photos library?", EXIT_USAGE)
    return db


def open_db(lib: Path):
    probe_access(lib)
    try:
        import osxphotos  # noqa: F401
    except ImportError:
        raise ScoutError(f"osxphotos is not installed in this Python. Run it through uv:\n  {UV_CMD} ...", EXIT_USAGE) from None
    try:
        return osxphotos.PhotosDB(dbfile=str(lib))
    except Exception as e:  # osxphotos raises plain OSError/sqlite errors when TCC blocks the WAL/shm files
        msg = f"{e.__class__.__name__}: {e}"
        low = msg.lower()
        if isinstance(e, OSError) or "unable to open" in low or "not permitted" in low or "authorization denied" in low:
            raise ScoutError(access_fix(lib, msg), EXIT_ACCESS) from None
        raise ScoutError(f"osxphotos could not read {lib}:\n  {msg}", EXIT_ERROR) from None


# ---------------------------------------------------------------- candidates (plain rows, testable without Photos)


@dataclass
class Cand:
    uuid: str
    date: dt.datetime  # tz-aware
    is_photo: bool = True
    favorite: bool = False
    screenshot: bool = False
    hidden: bool = False
    shared: bool = False
    overall: float = 0.0  # Photos' overall aesthetic score, about 0..1
    lighting: float = 0.0  # Photos' pleasant_lighting, about -1..1
    face_count: int = 0
    face_name: str | None = None  # whose face, when there is exactly one
    face_quality: float = 0.0  # 0..1
    face_size: float = 0.0  # fraction of the frame
    width: int = 0
    height: int = 0
    persons: list[str] = field(default_factory=list)
    albums: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    local_path: str | None = None  # best file on this Mac (export source)
    preview_path: str | None = None  # a JPEG Photos keeps locally (thumbnails, palettes)
    source_kind: str = "none"  # original | edited | preview | none
    has_original: bool = False  # original file is on this Mac (not iCloud-only)
    score: float = 0.0  # identity composite, filled by rank_identity


def best_local_file(p) -> tuple[str | None, str]:
    for attr, kind in (("path", "original"), ("path_edited", "edited")):
        v = getattr(p, attr, None)
        if v and os.path.exists(v):
            return v, kind
    for d in p.path_derivatives or []:
        if os.path.exists(d):
            return d, "preview"
    return None, "none"


def cand_from_photo(p, person: str | None = None, want_faces: bool = True) -> Cand:
    sc = p.score
    faces: list = []
    if want_faces:
        try:
            faces = [f for f in (p.face_info or []) if not getattr(f, "intrash", False)]
        except Exception:
            faces = []
    face = next((f for f in faces if person and f.name == person), faces[0]) if faces else None
    local, kind = best_local_file(p)
    preview = next((d for d in (p.path_derivatives or []) if os.path.exists(d)), None) or local
    return Cand(
        uuid=p.uuid,
        date=p.date,
        is_photo=bool(p.isphoto),
        favorite=bool(p.favorite),
        screenshot=bool(p.screenshot),
        hidden=bool(p.hidden),
        shared=bool(p.shared),
        overall=float(sc.overall) if sc is not None and sc.overall is not None else 0.0,
        lighting=float(sc.pleasant_lighting) if sc is not None and sc.pleasant_lighting is not None else 0.0,
        face_count=len(faces),
        face_name=(face.name if face is not None else None),
        face_quality=float(face.quality or 0.0) if face is not None else 0.0,
        face_size=float(face.size or 0.0) if face is not None else 0.0,
        width=int(p.width or 0),
        height=int(p.height or 0),
        persons=[n for n in (p.persons or []) if n != UNKNOWN],
        albums=list(p.albums or []),
        keywords=list(p.keywords or []),
        local_path=local,
        preview_path=preview,
        source_kind=kind,
        has_original=bool(p.path),
    )


# ---------------------------------------------------------------- identity selection


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def identity_score(c: Cand, now: dt.datetime) -> float:
    """Portrait-ness for an identity reference. Favorites, recent, well-lit, one big clear face."""
    age_days = max(0.0, (now - c.date).total_seconds() / 86400.0)
    recency = max(0.0, 1.0 - age_days / (3 * 365.0))  # 1 today, 0 three years ago
    s = clamp(c.overall, 0.0, 1.0)
    s += 0.5 * clamp(c.lighting, -1.0, 1.0)
    s += 0.5 * clamp(c.face_quality, 0.0, 1.0)
    s += 0.6 * min(c.face_size, 0.5)
    s += 0.3 if c.favorite else 0.0
    s += 0.2 * recency
    return round(s, 3)


def reject_reason(c: Cand, person: str, since: dt.date | None) -> str | None:
    if not c.is_photo:
        return "video"
    if c.screenshot:
        return "screenshot"
    if c.hidden:
        return "hidden"
    if c.shared:
        return "shared album"
    if c.face_count != 1:
        return "not exactly one face"
    if c.face_name != person:
        return "the face is someone else"
    if since and c.date.date() < since:
        return "before --since"
    if not c.local_path:
        return "no file on this Mac"
    return None


def pick_diverse(ranked: list[Cand], n: int, min_gap_s: float) -> list[Cand]:
    """Greedy: skip shots taken within min_gap_s of an already-chosen one (bursts), then fill up from the skipped."""
    chosen: list[Cand] = []
    skipped: list[Cand] = []
    for c in ranked:
        if len(chosen) >= n:
            break
        if any(abs((c.date - o.date).total_seconds()) < min_gap_s for o in chosen):
            skipped.append(c)
            continue
        chosen.append(c)
    for c in skipped:
        if len(chosen) >= n:
            break
        chosen.append(c)
    chosen.sort(key=lambda c: (-c.score, -c.date.timestamp()))
    return chosen


def rank_identity(
    cands: list[Cand], person: str, n: int, since: dt.date | None = None, now: dt.datetime | None = None, min_gap_s: float = 600
) -> tuple[list[Cand], list[Cand], Counter]:
    """Returns (chosen, whole eligible pool ranked, rejection reasons)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    pool: list[Cand] = []
    rejected: Counter = Counter()
    for c in cands:
        why = reject_reason(c, person, since)
        if why:
            rejected[why] += 1
            continue
        c.score = identity_score(c, now)
        pool.append(c)
    pool.sort(key=lambda c: (-c.score, -c.date.timestamp()))
    return pick_diverse(pool, n, min_gap_s), pool, rejected


# ---------------------------------------------------------------- palettes


def hex_of(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb[:3])


def rgb_of(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _redmean(a: str, b: str) -> float:
    (r1, g1, b1), (r2, g2, b2) = rgb_of(a), rgb_of(b)
    rm = (r1 + r2) / 2.0
    return math.sqrt((2 + rm / 256) * (r1 - r2) ** 2 + 4 * (g1 - g2) ** 2 + (2 + (255 - rm) / 256) * (b1 - b2) ** 2)


_MAXD = _redmean("#000000", "#ffffff")


def color_distance(a: str, b: str) -> float:
    """Perceptual-ish RGB distance ("redmean"), normalized 0..1."""
    return _redmean(a, b) / _MAXD


def dominant_colors(im, k: int = 3, sample: int = 96) -> list[tuple[str, float]]:
    """The k dominant colors of an image as [(hex, share)], most common first (Pillow median-cut quantize)."""
    from PIL import Image

    small = im.convert("RGB").resize((sample, sample), Image.Resampling.BILINEAR)
    q = small.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)  # [(count, palette index)]
    total = float(sum(c for c, _ in counts)) or 1.0
    out = []
    for count, idx in counts[:k]:
        rgb = tuple(pal[3 * idx : 3 * idx + 3])
        out.append((hex_of(rgb), round(count / total, 3)))
    return out


def mosaic(images: list, tile: int = 64):
    """All thumbnails tiled into one image: the set's pixels, for the aggregate palette and warm share."""
    from PIL import Image

    if not images:
        return Image.new("RGB", (tile, tile), (0, 0, 0))
    cols = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / cols)
    m = Image.new("RGB", (cols * tile, rows * tile))
    for i, im in enumerate(images):
        m.paste(im.convert("RGB").resize((tile, tile), Image.Resampling.BILINEAR), ((i % cols) * tile, (i // cols) * tile))
    return m


def warm_share(im, hue_deg=(10.0, 50.0), min_s: float = 0.25, min_v: float = 0.15) -> float:
    """Fraction of pixels in the brown/orange/amber hue band (the project palette lives at hue 20..35 deg)."""
    from PIL import Image

    hsv = im.convert("RGB").resize((96, 96), Image.Resampling.BILINEAR).convert("HSV")
    px = list(hsv.getdata())
    lo, hi = hue_deg[0] / 360 * 255, hue_deg[1] / 360 * 255
    warm = sum(1 for h, s, v in px if lo <= h <= hi and s >= min_s * 255 and v >= min_v * 255)
    return round(warm / len(px), 3)


def compare_palettes(agg: list[tuple[str, float]], project: list[str]) -> dict:
    """For each project color: the nearest color of the set and a 0..1 similarity (1 = identical)."""
    rows = []
    for pc in project:
        best = min(agg, key=lambda a: color_distance(pc, a[0])) if agg else None
        d = color_distance(pc, best[0]) if best else 1.0
        rows.append({"project": pc, "nearest": best[0] if best else None, "similarity": round(1.0 - d, 3)})
    mean = round(sum(r["similarity"] for r in rows) / len(rows), 3) if rows else 0.0
    return {"per_color": rows, "mean_similarity": mean}


def project_palette() -> list[str]:
    try:
        pal = json.loads(LOOKS_JSON.read_text()).get("palette")
        if isinstance(pal, list) and all(isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in pal) and pal:
            return [c.lower() for c in pal]
    except Exception:
        pass
    return list(DEFAULT_PALETTE)


# ---------------------------------------------------------------- images, sheets, export


def load_image(path: str, max_px: int = 1024):
    """Open any still Photos keeps on disk as an RGB image; HEIC and friends go through macOS sips."""
    from PIL import Image, ImageDraw, ImageOps

    try:
        im = Image.open(path)
        im.load()
        im = ImageOps.exif_transpose(im) or im
        return im.convert("RGB")
    except Exception:
        pass
    try:
        tmp = Path(tempfile.mkdtemp(prefix="photos_scout_")) / "preview.jpg"
        subprocess.run(
            ["/usr/bin/sips", "-s", "format", "jpeg", "-Z", str(max_px), str(path), "--out", str(tmp)], check=True, capture_output=True
        )
        return Image.open(tmp).convert("RGB")
    except Exception:
        ph = Image.new("RGB", (480, 480), (60, 50, 44))
        ImageDraw.Draw(ph).text((16, 16), "unreadable:\n" + os.path.basename(path), fill=FG, font=font(14))
        return ph


def write_jpeg(src: str, out: Path, size: int | None = None, quality: int = 95) -> tuple[int, int]:
    """Write src as a JPEG at out; --size caps the long edge (never upscales). HEIC falls back to sips."""
    from PIL import Image, ImageOps

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        im = Image.open(src)
        im.load()
        im = ImageOps.exif_transpose(im) or im
        im = im.convert("RGB")
        if size and max(im.size) > size:
            im = ImageOps.contain(im, (size, size), Image.Resampling.LANCZOS)
        im.save(out, "JPEG", quality=quality, optimize=True)
        return im.size
    except Exception:
        cmd = ["/usr/bin/sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality)]
        if size:
            cmd += ["-Z", str(size)]
        subprocess.run(cmd + [str(src), "--out", str(out)], check=True, capture_output=True)
        with Image.open(out) as im2:
            return im2.size


def font(size: int):
    from PIL import ImageFont

    for p in FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def contact_sheet(
    out: Path,
    title: str,
    cells: list[dict],
    cols: int,
    thumb: int = 300,
    caption_h: int = 56,
    footer: Callable | None = None,
    footer_h: int = 0,
) -> Path:
    """cells: [{"image": PIL image, "lines": [str, ...], "swatches": [hex, ...] | None}], numbered in order."""
    from PIL import Image, ImageDraw, ImageOps

    pad, title_h = 14, 44
    rows = max(1, math.ceil(len(cells) / cols))
    sw_h = 18 if any(c.get("swatches") for c in cells) else 0
    cell_h = thumb + sw_h + caption_h
    W = pad + cols * (thumb + pad)
    H = title_h + rows * (cell_h + pad) + pad + footer_h
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f_title, f_num, f_cap = font(17), font(15), font(12)
    d.text((pad, 13), title, fill=FG, font=f_title)
    for i, c in enumerate(cells):
        x = pad + (i % cols) * (thumb + pad)
        y = title_h + (i // cols) * (cell_h + pad)
        t = ImageOps.contain(c["image"].convert("RGB"), (thumb, thumb), Image.Resampling.LANCZOS)
        ox, oy = x + (thumb - t.width) // 2, y + (thumb - t.height) // 2
        sheet.paste(t, (ox, oy))
        label = f"#{i + 1}"
        tw = d.textlength(label, font=f_num)
        d.rectangle([ox, oy, ox + tw + 12, oy + 22], fill=ACCENT)
        d.text((ox + 6, oy + 3), label, fill=BG, font=f_num)
        yy = y + thumb + 3
        if c.get("swatches"):
            w = thumb // len(c["swatches"])
            for j, hx in enumerate(c["swatches"]):
                d.rectangle([x + j * w, yy, x + (j + 1) * w - 2, yy + sw_h - 4], fill=rgb_of(hx))
            yy += sw_h
        for k, line in enumerate(c.get("lines", [])[:3]):
            d.text((x, yy + k * 16), line, fill=FG if k == 0 else DIM, font=f_cap)
    if footer:
        footer(d, pad, H - footer_h, W)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def palette_footer(agg: list[tuple[str, float]], match: dict, warm: float) -> Callable:
    def draw(d, pad: int, y: int, W: int) -> None:
        f, f2 = font(12), font(13)
        d.text((pad, y), f"set palette: warm share {warm:.0%}, similarity to the project palette {match['mean_similarity']:.2f}", fill=FG, font=f2)
        y += 24
        x = pad
        for hx, share in agg:
            d.rectangle([x, y, x + 72, y + 30], fill=rgb_of(hx))
            d.text((x, y + 34), f"{hx} {share:.0%}", fill=DIM, font=f)
            x += 84
        x += 24
        px = x
        for row in match["per_color"]:
            d.rectangle([x, y, x + 72, y + 30], fill=rgb_of(row["project"]))
            d.text((x, y + 34), f"{row['project']} {row['similarity']:.2f}", fill=DIM, font=f)
            x += 84
        d.text((pad, y + 52), "left: this set (share)   right: project palette from looks.json (similarity to nearest set color)", fill=DIM, font=f)
        del px

    return draw


FOOTER_H = 24 + 30 + 34 + 18 + 12


def export_items(items: list[Cand], dest: Path, size: int | None, meta: dict, download_missing: bool = False, db=None) -> tuple[list[dict], list[str]]:
    """Copy each item's best local file (as JPEG) into dest and write manifest.json. Idempotent per uuid."""
    dest.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    notes: list[str] = []
    for i, c in enumerate(items, 1):
        src, kind = c.local_path, c.source_kind
        if download_missing and not c.has_original and db is not None:
            got = fetch_original(db, c.uuid)
            if got:
                src, kind = got, "original (downloaded from iCloud)"
            else:
                notes.append(f"#{i} {c.uuid[:8]}: iCloud download failed, kept the local {c.source_kind}")
        if not src:
            notes.append(f"#{i} {c.uuid[:8]}: no file on this Mac, skipped")
            continue
        out = dest / f"{c.uuid[:8].lower()}.jpg"
        w, h = write_jpeg(src, out, size)
        manifest.append(
            {
                "n": i,
                "uuid": c.uuid,
                "date": c.date.isoformat(),
                "score": c.score,
                "path": rel(out),
                "source": kind,
                "pixels": f"{w}x{h}",
                "favorite": c.favorite,
                "albums": c.albums,
            }
        )
    doc = {"generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"), **meta, "size": size, "items": manifest}
    (dest / "manifest.json").write_text(json.dumps(doc, indent=2) + "\n")
    return manifest, notes


def fetch_original(db, uuid: str) -> str | None:
    """Opt-in: ask Photos (PhotoKit) to download an iCloud-only original. Needs Automation permission; can be slow."""
    try:
        from osxphotos import ExportOptions, PhotoExporter

        p = db.get_photo(uuid)
        tmp = Path(tempfile.mkdtemp(prefix="photos_scout_dl_"))
        res = PhotoExporter(p).export(str(tmp), options=ExportOptions(download_missing=True, use_photokit=True, overwrite=True, timeout=180))
        files = list(getattr(res, "exported", None) or [])
        return files[0] if files and os.path.exists(files[0]) else None
    except Exception:
        return None


# ---------------------------------------------------------------- helpers


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise ScoutError(f'--since must be YYYY-MM-DD, not "{s}"', EXIT_USAGE) from None


def stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def table(headers: list[str], rows: list[list[Any]], right: set[int] = frozenset()) -> str:
    cells = [[str(v) for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    fmt = lambda r: "  ".join((v.rjust(w) if i in right else v.ljust(w)) for i, (v, w) in enumerate(zip(r, widths)))
    return "\n".join([fmt(headers)] + [fmt(r) for r in cells])


def say(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- subcommands


def cmd_stats(db, lib: Path, args) -> dict:
    photos = db.photos()  # excludes the trash
    n_photo = sum(1 for p in photos if p.isphoto)
    n_video = sum(1 for p in photos if p.ismovie)
    n_local = sum(1 for p in photos if p.path)
    people = {k: v for k, v in db.persons_as_dict.items() if k != UNKNOWN}
    out = {
        "library": str(lib),
        "items": len(photos),
        "photos": n_photo,
        "videos": n_video,
        "in_trash": len(db.photos(intrash=True)),
        "hidden": sum(1 for p in photos if p.hidden),
        "screenshots": sum(1 for p in photos if p.screenshot),
        "favorites": sum(1 for p in photos if p.favorite),
        "originals_on_this_mac": n_local,
        "icloud_only": len(photos) - n_local,
        "people": [{"name": k, "photos": v} for k, v in sorted(people.items(), key=lambda kv: (-kv[1], kv[0]))],
        "unnamed_faces_photos": db.persons_as_dict.get(UNKNOWN, 0),
        "albums": [{"name": k, "photos": v} for k, v in sorted(db.albums_as_dict.items(), key=lambda kv: (-kv[1], kv[0]))],
        "keywords": [{"name": k, "photos": v} for k, v in sorted(db.keywords_as_dict.items(), key=lambda kv: (-kv[1], kv[0]))],
    }
    if not args.json:
        j = lambda xs: " · ".join(f"{x['name']} {x['photos']}" for x in xs) or "none"
        print(f"library     {out['library']}")
        print(f"items       {out['items']} ({n_photo} photos, {n_video} videos) · {out['in_trash']} in trash (not counted) · {out['hidden']} hidden · {out['screenshots']} screenshots")
        print(f"originals   {n_local} on this Mac · {out['icloud_only']} iCloud-only (Photos keeps ~1024px previews locally; see docs/photos.md)")
        print(f"favorites   {out['favorites']}")
        print(f"people      {j(out['people'])} · unnamed faces in {out['unnamed_faces_photos']} photos")
        print(f"albums      {j(out['albums'])}")
        print(f"keywords    {j(out['keywords'])}")
    return out


def cmd_identity(db, lib: Path, args) -> dict:
    person = args.person
    names = sorted(k for k in db.persons_as_dict if k != UNKNOWN)
    if person not in names:
        close = [n for n in names if person.lower() in n.lower()]
        hint = f" Did you mean: {', '.join(close)}?" if close else f" Names Photos knows: {', '.join(names) or 'none'}."
        raise ScoutError(f'no person named "{person}" in Photos (take the name from `stats`).{hint}', EXIT_USAGE)
    since = parse_date(args.since)
    say(f"scanning {person}'s photos ...")
    photos = db.photos(persons=[person], movies=False)
    cands = [cand_from_photo(p, person) for p in photos]
    chosen, pool, rejected = rank_identity(cands, person, args.n, since, min_gap_s=args.min_gap)
    if not chosen:
        why = ", ".join(f"{k} {v}" for k, v in rejected.most_common())
        raise ScoutError(f"no usable portrait of {person} (rejected: {why or 'nothing tagged'})", EXIT_USAGE)

    cells, rows = [], []
    for i, c in enumerate(chosen):
        im = load_image(c.preview_path or c.local_path)
        alb = c.albums[0] if c.albums else "-"
        cells.append(
            {
                "image": im,
                "lines": [
                    f"#{i + 1}  {c.date:%Y-%m-%d}" + ("  fav" if c.favorite else ""),
                    f"score {c.score:.2f}  light {c.lighting:+.2f}  face {c.face_quality:.2f}",
                    f"{im.width}x{im.height} {c.source_kind}  {alb}",
                ],
            }
        )
        rows.append(
            [i + 1, f"{c.date:%Y-%m-%d}", f"{c.score:.2f}", f"{c.overall:.2f}", f"{c.lighting:+.2f}", f"{c.face_quality:.2f}", f"{c.face_size:.0%}",
             "fav" if c.favorite else "", f"{im.width}x{im.height} {c.source_kind}", alb]
        )
    sheet = BUILD_DIR / f"identity_{slug(person)}.png"
    cols = 4 if len(chosen) > 6 else max(1, len(chosen))
    contact_sheet(sheet, f"identity  {person}  {len(chosen)} of {len(pool)} candidates  {stamp()}", cells, cols=cols, thumb=300)

    exported, notes = None, []
    if args.export:
        exported, notes = export_items(chosen, REFS_DIR / "identity", args.size, {"kind": "identity", "person": person}, args.download_missing, db)
    previews = sum(1 for c in chosen if not c.has_original)
    if previews:
        notes.append(
            f"{previews} of {len(chosen)} are iCloud-only: the local file is Photos' ~1024px preview, not the original. "
            "Full-res: --export --download-missing (asks Photos to fetch them), or Photos > Settings > iCloud > Download Originals."
        )
    out = {
        "person": person,
        "candidates": len(pool),
        "tagged": len(cands),
        "rejected": dict(rejected),
        "since": since.isoformat() if since else None,
        "selected": [
            {"n": i + 1, "uuid": c.uuid, "date": c.date.isoformat(), "score": c.score, "overall": c.overall, "lighting": c.lighting,
             "face_quality": c.face_quality, "face_size": c.face_size, "favorite": c.favorite, "source": c.source_kind,
             "has_original": c.has_original, "pixels": f"{c.width}x{c.height}", "albums": c.albums}
            for i, c in enumerate(chosen)
        ],
        "sheet": rel(sheet),
        "exported": exported,
        "notes": notes,
    }
    if not args.json:
        print(f"{person}: {len(cands)} tagged, {len(pool)} usable portraits (one face, no screenshots/hidden/shared), showing {len(chosen)}")
        print(table(["#", "date", "score", "overall", "light", "faceQ", "face%", "fav", "local file", "album"], rows, right={0, 2, 3, 4, 5, 6}))
        if rejected:
            print("rejected: " + ", ".join(f"{k} {v}" for k, v in rejected.most_common()))
        print(f"sheet: {rel(sheet)}")
        if exported is not None:
            print(f"exported {len(exported)} -> {rel(REFS_DIR / 'identity')}/ (manifest.json)")
        else:
            print("nothing copied (add --export to copy them into reference-master/refs/identity/)")
        for n in notes:
            print(f"note: {n}")
    return out


def cmd_inspire(db, lib: Path, args) -> dict:
    photos = list(db.photos(movies=False))
    filters: list[str] = []
    if args.album:
        titles = {t.lower(): t for t in db.albums_as_dict}
        if args.album.lower() not in titles:
            raise ScoutError(f'no album "{args.album}". Albums: {", ".join(sorted(db.albums_as_dict)) or "none"}', EXIT_USAGE)
        want = titles[args.album.lower()]
        photos = [p for p in photos if want in (p.albums or [])]
        filters.append(f"album={want}")
    if args.keyword:
        kws = {k.lower(): k for k in db.keywords_as_dict}
        if args.keyword.lower() not in kws:
            raise ScoutError(f'no keyword "{args.keyword}". Keywords: {", ".join(sorted(db.keywords_as_dict)) or "none"}', EXIT_USAGE)
        want = kws[args.keyword.lower()]
        photos = [p for p in photos if want in (p.keywords or [])]
        filters.append(f"keyword={want}")
    if args.favorites or not filters:
        if not filters:
            say("no filter given: using --favorites")
        photos = [p for p in photos if p.favorite]
        filters.append("favorites")
    cands = [cand_from_photo(p, want_faces=False) for p in photos if not p.hidden and not p.shared]
    cands = [c for c in cands if c.preview_path]
    cands.sort(key=lambda c: (-int(c.favorite), -c.date.timestamp()))  # newest first
    chosen = cands[: args.n]
    if not chosen:
        raise ScoutError(f"no photos match {' + '.join(filters)}", EXIT_USAGE)

    images, cells, rows, items = [], [], [], []
    for i, c in enumerate(chosen):
        im = load_image(c.preview_path)
        pal = dominant_colors(im, 3)
        images.append(im)
        alb = c.albums[0] if c.albums else "-"
        cells.append(
            {
                "image": im,
                "swatches": [h for h, _ in pal],
                "lines": [f"#{i + 1}  {c.date:%Y-%m-%d}" + ("  fav" if c.favorite else ""), " ".join(h for h, _ in pal), f"{alb}" + ("  screenshot" if c.screenshot else "")],
            }
        )
        rows.append([i + 1, f"{c.date:%Y-%m-%d}", "fav" if c.favorite else "", alb, " ".join(f"{h} {s:.0%}" for h, s in pal)])
        items.append({"n": i + 1, "uuid": c.uuid, "date": c.date.isoformat(), "favorite": c.favorite, "screenshot": c.screenshot,
                      "albums": c.albums, "keywords": c.keywords, "palette": [{"hex": h, "share": s} for h, s in pal], "source": c.source_kind})
    m = mosaic(images)
    agg = dominant_colors(m, 5, sample=128)
    warm = warm_share(m)
    proj = project_palette()
    match = compare_palettes(agg, proj)
    sheet = BUILD_DIR / "inspire.png"
    cols = 4 if len(chosen) <= 12 else 6
    thumb = 280 if cols == 4 else 210
    contact_sheet(sheet, f"inspire  {' + '.join(filters)}  {len(chosen)} of {len(cands)}  {stamp()}", cells, cols=cols, thumb=thumb,
                  caption_h=54, footer=palette_footer(agg, match, warm), footer_h=FOOTER_H)

    exported, notes = None, []
    name = "+".join(slug(f.split("=", 1)[-1]) for f in filters)
    if args.export:
        exported, notes = export_items(chosen, REFS_DIR / "inspire" / name, args.size, {"kind": "inspire", "filters": filters}, False, db)
    out = {
        "filters": filters,
        "matched": len(cands),
        "shown": len(chosen),
        "items": items,
        "aggregate_palette": [{"hex": h, "share": s} for h, s in agg],
        "warm_share": warm,
        "project_palette": proj,
        "match": match,
        "sheet": rel(sheet),
        "exported": exported,
        "notes": notes,
    }
    if not args.json:
        print(f"inspire {' + '.join(filters)}: {len(cands)} photos, showing {len(chosen)} (newest first)")
        print(table(["#", "date", "fav", "album", "dominant colors"], rows, right={0}))
        print("set palette:     " + "  ".join(f"{h} {s:.0%}" for h, s in agg) + f"   warm share {warm:.0%}")
        print("project palette: " + "  ".join(f"{r['project']}->{r['nearest']} {r['similarity']:.2f}" for r in match["per_color"]) + f"   mean similarity {match['mean_similarity']:.2f}")
        print(f"sheet: {rel(sheet)}")
        if exported is not None:
            print(f"exported {len(exported)} -> {rel(REFS_DIR / 'inspire' / name)}/ (manifest.json)")
        else:
            print(f"nothing copied (add --export to copy them into reference-master/refs/inspire/{name}/)")
        for n in notes:
            print(f"note: {n}")
    return out


# ---------------------------------------------------------------- selftest (synthetic rows, no Photos library needed)


def run_selftest(args) -> int:
    from PIL import Image

    base = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)

    def mk(uuid: str, days_ago: float, **kw) -> Cand:
        d = dict(face_count=1, face_name="Ada", local_path="/x", preview_path="/x", overall=0.5, face_quality=0.5, face_size=0.2)
        d.update(kw)
        return Cand(uuid=uuid, date=base - dt.timedelta(days=days_ago), **d)

    def t_filters():
        cands = [
            mk("good", 10, overall=0.8),
            mk("shot", 10, screenshot=True),
            mk("hide", 10, hidden=True),
            mk("two", 10, face_count=2),
            mk("other", 10, face_name="Bob"),
            mk("vid", 10, is_photo=False),
            mk("old", 900),
            mk("nofile", 10, local_path=None),
        ]
        chosen, pool, rej = rank_identity(cands, "Ada", 12, since=dt.date(2025, 1, 1), now=base)
        assert [c.uuid for c in chosen] == ["good"], chosen
        assert rej == Counter({"screenshot": 1, "hidden": 1, "not exactly one face": 1, "the face is someone else": 1, "video": 1, "before --since": 1, "no file on this Mac": 1}), rej

    def t_preferences():
        fav, plain = mk("fav", 100, favorite=True), mk("plain", 100)
        lit, dark = mk("lit", 100, lighting=0.4), mk("dark", 100, lighting=-0.4)
        new, old = mk("new", 30), mk("old", 1000)
        big, small = mk("big", 100, face_size=0.4), mk("small", 100, face_size=0.05)
        for a, b in ((fav, plain), (lit, dark), (new, old), (big, small)):
            assert identity_score(a, base) > identity_score(b, base), (a.uuid, b.uuid)

    def t_diversity():
        burst = [mk(f"b{i}", 10 + i / 86400, overall=0.9 - i * 0.01) for i in range(3)]  # seconds apart
        other = mk("otherday", 40, overall=0.6)
        chosen, _, _ = rank_identity(burst + [other], "Ada", 2, now=base)
        assert {c.uuid for c in chosen} == {"b0", "otherday"}, [c.uuid for c in chosen]
        chosen, _, _ = rank_identity(burst + [other], "Ada", 4, now=base)
        assert len(chosen) == 4 and chosen[0].uuid == "b0", [c.uuid for c in chosen]

    def t_dominant():
        im = Image.new("RGB", (100, 100), (255, 159, 67))
        im.paste((0, 0, 255), (60, 0, 100, 100))
        pal = dominant_colors(im, 3)
        top, share = pal[0]
        assert all(abs(a - b) <= 8 for a, b in zip(rgb_of(top), (255, 159, 67))), pal
        assert 0.5 <= share <= 0.7, pal
        assert abs(pal[1][1] - 0.4) < 0.08 and rgb_of(pal[1][0])[2] > 200, pal

    def t_palette_match():
        warm_imgs = [Image.new("RGB", (50, 50), rgb_of("#ff9f43")), Image.new("RGB", (50, 50), rgb_of("#7a4a2a"))]
        m = mosaic(warm_imgs)
        assert warm_share(m) > 0.9, warm_share(m)
        agg = dominant_colors(m, 5)
        match = compare_palettes(agg, DEFAULT_PALETTE)
        assert match["mean_similarity"] > 0.8, match
        blue = mosaic([Image.new("RGB", (50, 50), (20, 40, 220))])
        assert warm_share(blue) < 0.05
        bm = compare_palettes(dominant_colors(blue, 3), DEFAULT_PALETTE)
        assert bm["mean_similarity"] < 0.6, bm
        assert color_distance("#000000", "#ffffff") == 1.0 and color_distance("#ff9f43", "#ff9f43") == 0.0

    def t_sheet_and_jpeg():
        tmp = Path(tempfile.mkdtemp(prefix="photos_scout_selftest_"))
        imgs = [Image.new("RGB", (400, 300), (200, 100, 50)), Image.new("RGB", (300, 400), (50, 100, 200)), Image.new("RGB", (100, 100), (90, 90, 90))]
        cells = [{"image": im, "lines": ["#", "two", "three"], "swatches": ["#ff9f43", "#7a4a2a", "#ffb347"]} for im in imgs]
        agg = [("#ff9f43", 0.5), ("#7a4a2a", 0.5)]
        p = contact_sheet(tmp / "sheet.png", "selftest", cells, cols=2, thumb=120, footer=palette_footer(agg, compare_palettes(agg, DEFAULT_PALETTE), 0.9), footer_h=FOOTER_H)
        assert p.exists() and p.stat().st_size > 1000
        with Image.open(p) as s:
            assert s.size[0] == 14 + 2 * (120 + 14) and s.size[1] > 300, s.size
        src = tmp / "big.png"
        Image.new("RGB", (2000, 1000), (1, 2, 3)).save(src)
        assert write_jpeg(str(src), tmp / "out.jpg", 1280) == (1280, 640)
        assert write_jpeg(str(src), tmp / "out2.jpg", None) == (2000, 1000)
        assert write_jpeg(str(src), tmp / "out3.jpg", 4000) == (2000, 1000)  # never upscales

    def t_access_message():
        if os.geteuid() == 0:
            return  # root ignores file modes
        tmp = Path(tempfile.mkdtemp(prefix="photos_scout_selftest_")) / "Fake.photoslibrary"
        (tmp / "database").mkdir(parents=True)
        dbf = tmp / "database" / "Photos.sqlite"
        dbf.write_bytes(b"SQLite format 3\0")
        dbf.chmod(0)
        try:
            probe_access(tmp)
            raise AssertionError("expected ScoutError")
        except ScoutError as e:
            assert e.code == EXIT_ACCESS and "Full Disk Access" in str(e) and "PermissionError" in str(e), str(e)
        finally:
            dbf.chmod(0o600)
        try:
            probe_access(tmp.parent / "Nope.photoslibrary")
            raise AssertionError("expected ScoutError")
        except ScoutError as e:
            assert e.code == EXIT_USAGE and "--library" in str(e)
        dbf.write_bytes(b"not a database")
        try:
            probe_access(tmp)
            raise AssertionError("expected ScoutError")
        except ScoutError as e:
            assert e.code == EXIT_USAGE and "SQLite" in str(e)

    def t_slug():
        assert slug("Naledi Kekana") == "naledi-kekana" and slug("  WhatsApp / 2025 ") == "whatsapp-2025" and slug("!!!") == "x"
        assert parse_date(None) is None and parse_date("2025-01-01") == dt.date(2025, 1, 1)
        try:
            parse_date("yesterday")
            raise AssertionError("expected ScoutError")
        except ScoutError as e:
            assert e.code == EXIT_USAGE

    tests = [t_filters, t_preferences, t_diversity, t_dominant, t_palette_match, t_sheet_and_jpeg, t_access_message, t_slug]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {e.__class__.__name__}: {e}")
            if args.debug:
                import traceback

                traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return EXIT_OK if not failed else EXIT_SELFTEST


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print a JSON object instead of the table")
    common.add_argument("--library", metavar="PATH", help="a .photoslibrary to read (default: the library Photos opened last)")
    common.add_argument("--debug", action="store_true", help="show Python tracebacks")
    ap = argparse.ArgumentParser(
        prog="photos_scout.py",
        description="Look at the owner's macOS Photos library for identity references and taste signals. Read-only unless --export.",
        epilog=f"run through uv:  {UV_CMD} <subcommand> ...",
    )
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="subcommand")
    sub.add_parser("stats", parents=[common], help="library path, counts, people, albums, keywords, favorites (counts only)")
    s = sub.add_parser("identity", parents=[common], help="portraits of one person for identity references, with a contact sheet")
    s.add_argument("--person", required=True, help='a name from `stats` (Photos\' People), e.g. "Naledi Kekana"')
    s.add_argument("--n", type=int, default=12, help="how many (default 12)")
    s.add_argument("--since", metavar="YYYY-MM-DD", help="ignore photos taken before this date")
    s.add_argument("--min-gap", type=float, default=600, metavar="SECONDS", help="prefer shots this far apart in time (default 600)")
    s.add_argument("--export", action="store_true", help="copy them into reference-master/refs/identity/ with a manifest.json")
    s.add_argument("--size", type=int, metavar="PX", help="with --export: cap the long edge (never upscales)")
    s.add_argument("--download-missing", action="store_true", help="with --export: ask Photos to fetch iCloud-only originals (Automation permission prompt; slow)")
    s = sub.add_parser("inspire", parents=[common], help="taste signals: photos by album / keyword / favorites with palettes and a contact sheet")
    s.add_argument("--album", help="album title from `stats`")
    s.add_argument("--keyword", help="keyword from `stats`")
    s.add_argument("--favorites", action="store_true", help="favorites only (the default when no filter is given)")
    s.add_argument("--n", type=int, default=30, help="how many (default 30), newest first")
    s.add_argument("--export", action="store_true", help="copy them into reference-master/refs/inspire/<filter>/ with a manifest.json")
    s.add_argument("--size", type=int, metavar="PX", help="with --export: cap the long edge (never upscales)")
    sub.add_parser("selftest", parents=[common], help="unit-test the selection and palette logic on synthetic rows (no library needed)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "selftest":
            return run_selftest(args)
        lib = resolve_library(args.library)
        db = open_db(lib)
        result = {"stats": cmd_stats, "identity": cmd_identity, "inspire": cmd_inspire}[args.cmd](db, lib, args)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        return EXIT_OK
    except ScoutError as e:
        print(f"photos_scout: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        if getattr(args, "debug", False):
            raise
        print(f"photos_scout: unexpected {e.__class__.__name__}: {e}\n  re-run with --debug for the traceback", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
