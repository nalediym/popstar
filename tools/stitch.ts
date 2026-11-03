#!/usr/bin/env bun
/**
 * Director's Cut renderer: pipeline stage 7 (STITCH). It turns cut/edl.json into outputs/final.mp4.
 *
 *   bun tools/stitch.ts [--edl cut/edl.json] [--out outputs/final.mp4] [--captions auto|png|drawtext|off] [--keep]
 *
 * The edit grammar (.claude/skills/popstar-pipeline/SKILL.md, Stage 7):
 *   - Cuts land 1 frame before the downbeat (globals.cut_lead_frames).
 *   - Dissolves are xfade=fade, centered on the cut.
 *   - A warm flash sits on the chorus downbeat.
 *   - The post camera is scale(eval=frame, t-expression) plus crop, never zoompan.
 *   - One amber grade; a 2.39:1 letterbox wipes in at 0 and out on the last beat.
 *   - Lyric captions, and the clean song as the only audio, shifted by sync_offset_s.
 *
 * It is deterministic. The frame plan is integer, every ffmpeg expression runs on output time, and x264 is
 * deterministic by design. Nothing time- or random-based reaches the file, so the same EDL and the same
 * sources give the same bytes (the receipt prints an md5).
 *
 * The browser preview (cut/index.html) runs the same math live. The shared numbers (grade presets, clone layout,
 * caption style) live HERE and reach the page through GET /presets. The page mirrors the frame plan in its
 * <script id="cut-core"> block, and the tests compare the two.
 *
 * Two ffmpeg 9.0.1 facts it relies on (both checked on this machine, 2026-11-02):
 *   - After `scale=...:eval=frame`, `crop` keeps a stale `iw`/`ih`, so "(iw-W)/2" drifts the zoom center
 *     off to the bottom-right. Crop x/y therefore repeat the scale's own size expression.
 *   - In scale's eval=frame expressions, `n` is off by one (the first frame sees n=1). Expressions use `t`.
 *   - The Homebrew build has no drawtext (no libfreetype). Captions are rendered to PNG with ImageMagick and
 *     overlaid. --captions drawtext is used only if the filter exists.
 */
import { existsSync, mkdirSync, readdirSync, renameSync, rmSync, statSync } from "node:fs";
import { basename, dirname, extname, join, posix, relative, resolve } from "node:path";

export const ROOT = resolve(import.meta.dir, "..");
export const STITCH_VERSION = "1.0.0";

// ---------------------------------------------------------------------------------------------------------------
// Shared look constants (served to the page by GET /presets, so preview and render agree)
// ---------------------------------------------------------------------------------------------------------------

/** A grade as CSS-filter numbers: filter: sepia() saturate() contrast() brightness(), then a warm multiply
 *  overlay (color at warm_opacity). The ffmpeg chain is derived from these same numbers (gradeFilters). */
export type Grade = {
  label: string; sepia: number; saturate: number; contrast: number; brightness: number;
  warm: string; warm_opacity: number;
};
export const GRADE_PRESETS: Record<string, Grade> = {
  amber_soft: { label: "amber soft (the house look)", sepia: 0.22, saturate: 1.15, contrast: 1.06, brightness: 0.98, warm: "#ffb066", warm_opacity: 0.16 },
  amber_deep: { label: "amber deep (glow-up)", sepia: 0.3, saturate: 1.22, contrast: 1.1, brightness: 0.96, warm: "#ff9f43", warm_opacity: 0.22 },
  burnt_orange: { label: "burnt orange", sepia: 0.26, saturate: 1.28, contrast: 1.06, brightness: 0.95, warm: "#e0702c", warm_opacity: 0.2 },
  tuscan_brown: { label: "tuscan brown", sepia: 0.42, saturate: 0.95, contrast: 1.08, brightness: 0.92, warm: "#b07a4a", warm_opacity: 0.22 },
  none: { label: "no grade", sepia: 0, saturate: 1, contrast: 1, brightness: 1, warm: "#ffffff", warm_opacity: 0 },
};

/** Harmony clones, triptych mode. The lead is the full frame with soft side edges. Two backups sit behind it:
 *  0.6x size, the left one mirrored, darker, soft-masked, eye line level with the lead's. */
export const CLONE_LAYOUT = {
  bg_dark: 0.3, // the backdrop is the same frame dimmed (a dim stage), so no hard black edges show
  side_scale: 0.6, // backup size relative to the frame
  side_dark: 0.6, // backup brightness multiplier
  side_cx: [0.2, 0.8] as [number, number], // backup centers, fraction of frame width
  side_top: 0.16, // backup top edge, fraction of frame height (keeps the eye line at about 40%)
  flip_left: true, // the left backup is mirrored, so the pair is symmetric around the lead
  side_mask: { h_feather: 0.18, v_top: 0.1, v_bottom: 0.25 }, // linear alpha ramps, fractions of the backup size
  lead_mask: { core: 0.14, ramp: 0.12 }, // opaque while |x - W/2| <= core*W, then 0 after another ramp*W
};

export const CAPTION_STYLE = {
  font_css: 'Georgia, "Times New Roman", serif',
  font_files: [
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
  ],
  fade_frames: 6, // fade in and out, frames
  box_h: 120, // caption strip height in px at 720p, scaled with the frame
  shadow: { dy: 2, sigma: 3, opacity: 0.85 }, // CSS: text-shadow 0 2px 6px rgba(0,0,0,.85)
  max_width: 0.92, // of the frame width; longer lines shrink
  char_w: 0.5, // width estimate per char, in em (both renderers use it to shrink long lines)
};

export const VIDEO_EXT = [".mp4", ".mov", ".m4v", ".webm"];
export const IMAGE_EXT = [".png", ".jpg", ".jpeg", ".webp"];
/** The only places a scene source (and GET /media) may come from. Repo-relative, trailing slash. */
export const ALLOWED_MEDIA_DIRS = ["outputs/", "clips/", "reference-master/renders/", "reference-master/tests/"];
/** Never a source: our own outputs. */
export const FORBIDDEN_SOURCES = ["outputs/cut/", "outputs/final.mp4"];

// ---------------------------------------------------------------------------------------------------------------
// EDL types + defaults
// ---------------------------------------------------------------------------------------------------------------

export type Source = { path: string; in_s: number; rate: number; placeholder: boolean; loop: "loop" | "hold" };
export type CameraFx = "slow_push" | "drift" | "none";
export type Scene = {
  id: string; title: string; t0: number; t1: number;
  source: Source;
  camera_post: { fx: CameraFx; from: number; to: number };
  punch_in_at: number[];
  transition_in: { type: "cut" | "dissolve"; frames: number };
  clones: { on: boolean; mode: "triptych" };
  grade_override: string | null;
  note?: string;
};
export type Globals = {
  cut_lead_frames: number;
  letterbox: { on: boolean; ratio: number; wipe_in_s: number; wipe_out_s: number; wipe_dur_s: number };
  captions: { on: boolean; y: number; size: number; color: string };
  grade: { preset: string; on: boolean };
  flash: { on: boolean; at: number[]; frames: number; color: string; opacity: number };
  beat_punch: { on: boolean; amount: number; decay_s: number };
  punch: { amount: number; decay_s: number };
};
export type Edl = {
  version: 1; song: string; duration_s: number; fps: number; size: [number, number]; sync_offset_s: number;
  globals: Globals; scenes: Scene[];
};

export const DEFAULT_GLOBALS: Globals = {
  cut_lead_frames: 1,
  letterbox: { on: true, ratio: 2.39, wipe_in_s: 0, wipe_out_s: 31.685, wipe_dur_s: 1 },
  captions: { on: true, y: 0.935, size: 34, color: "#ffbe6b" },
  grade: { preset: "amber_soft", on: true },
  flash: { on: true, at: [17.34], frames: 2, color: "#ffd9a0", opacity: 0.85 },
  beat_punch: { on: false, amount: 0.04, decay_s: 0.15 },
  punch: { amount: 0.1, decay_s: 0.35 },
};

// ---------------------------------------------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------------------------------------------

const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x));
const r4 = (x: number) => Math.round(x * 1e4) / 1e4;
/** A number for an ffmpeg expression: fixed point, no exponent, sign-safe when wrapped in parentheses. */
export const fx = (x: number) => {
  const s = (Math.abs(x) < 1e-9 ? 0 : x).toFixed(6).replace(/\.?0+$/, "");
  return s === "-0" ? "0" : s;
};
const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const hex01 = (hex: string): [number, number, number] => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  const v = m ? parseInt(m[1], 16) : 0xffffff;
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
};
const isHex = (s: unknown) => typeof s === "string" && /^#[0-9a-f]{6}$/i.test(s);
export const rel = (abs: string) => relative(ROOT, abs).split("\\").join("/");

/** Repo-relative media path check shared by the EDL validator and GET /media. Returns the cleaned path or null. */
export function safeMediaPath(p: unknown, opts: { allowForbidden?: boolean } = {}): string | null {
  if (typeof p !== "string" || !p || p.length > 400 || p.includes("\0") || p.includes("\\")) return null;
  if (p.startsWith("/") || /^[a-z]+:/i.test(p)) return null;
  const parts = p.split("/");
  if (parts.some((s) => s === ".." || s === "." || s === "")) return null;
  const norm = posix.normalize(p);
  if (norm !== p) return null;
  if (!ALLOWED_MEDIA_DIRS.some((d) => norm.startsWith(d))) return null;
  if (!opts.allowForbidden && FORBIDDEN_SOURCES.some((d) => norm === d || norm.startsWith(d))) return null;
  const ext = extname(norm).toLowerCase();
  if (![...VIDEO_EXT, ...IMAGE_EXT].includes(ext)) return null;
  return norm;
}
export const isImagePath = (p: string) => IMAGE_EXT.includes(extname(p).toLowerCase());

// ---------------------------------------------------------------------------------------------------------------
// Media probe (shared with the server's GET /assets)
// ---------------------------------------------------------------------------------------------------------------

export type MediaInfo = {
  kind: "video" | "image"; duration_s: number | null; width: number; height: number;
  fps: number | null; frames: number | null; has_audio: boolean;
};
const rate = (r: unknown) => {
  const [n, d] = String(r ?? "").split("/").map(Number);
  return n > 0 && d > 0 ? n / d : 0;
};
export async function probeMedia(abs: string): Promise<MediaInfo | null> {
  const p = Bun.spawn(["ffprobe", "-v", "error", "-show_entries",
    "stream=codec_type,codec_name,width,height,nb_frames,duration,avg_frame_rate,r_frame_rate:format=duration,format_name",
    "-of", "json", abs], { stdout: "pipe", stderr: "pipe", stdin: "ignore" });
  const [out, code] = [await new Response(p.stdout).text(), await p.exited];
  if (code !== 0) return null;
  type St = { codec_type?: string; width?: number; height?: number; nb_frames?: string; duration?: string; avg_frame_rate?: string; r_frame_rate?: string };
  let info: { streams?: St[]; format?: { duration?: string; format_name?: string } };
  try { info = JSON.parse(out); } catch { return null; }
  const v = (info.streams ?? []).find((s) => s.codec_type === "video");
  if (!v || !v.width || !v.height) return null;
  const image = isImagePath(abs) || /_pipe|image2/.test(info.format?.format_name ?? "");
  if (image) return { kind: "image", duration_s: null, width: v.width, height: v.height, fps: null, frames: null, has_audio: false };
  const fps = rate(v.avg_frame_rate) || rate(v.r_frame_rate) || 24;
  const frames = Number(v.nb_frames);
  const dur = Number.isFinite(frames) && frames > 0 ? frames / fps : Number(v.duration ?? info.format?.duration ?? 0);
  return {
    kind: "video", duration_s: r4(dur), width: v.width, height: v.height, fps: r4(fps),
    frames: Number.isFinite(frames) && frames > 0 ? frames : Math.round(dur * fps),
    has_audio: (info.streams ?? []).some((s) => s.codec_type === "audio"),
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Timing: songs/<song>.timing.json, or an approximate fallback (shared with GET /timing's 404 body)
// ---------------------------------------------------------------------------------------------------------------

export type Line = { text: string; start: number; end: number; section?: string; confidence?: string };
export type Timing = {
  song: string; duration_s: number; bpm?: number; beats: number[]; downbeats: number[];
  sections: { name: string; start: number; end: number }[]; lines: Line[];
  approximate: boolean; source: string; warning?: string;
};

/** Downbeats measured for "In the Loop (Devagar)" (beat_this, 2026-11-02). Used only when timing.json is missing. */
export const FALLBACK_DOWNBEATS = [1.68, 4.28, 6.9, 9.5, 12.12, 14.72, 17.34, 19.94, 22.56, 25.16, 27.78, 30.38];
/** Where each lyric section roughly sits (seconds) when the real line times are missing. */
const FALLBACK_WINDOWS: Record<string, [number, number]> = { intro: [0.5, 3], verse: [3, 17], chorus: [17.3, 31] };

export const timingPathFor = (song: string) => song.replace(/\.(mp3|wav|m4a|flac|aac)$/i, "") + ".timing.json";
export const shotsPathFor = (song: string) => "shots/" + basename(song).replace(/\.(mp3|wav|m4a|flac|aac)$/i, "") + ".json";
const lyricsPathFor = (song: string) => song.replace(/\.(mp3|wav|m4a|flac|aac)$/i, "") + ".md";

/** Parse "## Lyrics" from the song recipe: [Section] headers, then lines until the next "## ". */
export function parseLyrics(md: string): { section: string; text: string }[] {
  const out: { section: string; text: string }[] = [];
  const at = md.search(/^##\s+Lyrics\s*$/im);
  if (at < 0) return out;
  let section = "verse";
  for (const raw of md.slice(at).split("\n").slice(1)) {
    const line = raw.trim();
    if (/^##\s/.test(line)) break;
    if (!line) continue;
    const head = /^\[([^\]]+)\]$/.exec(line);
    if (head) { section = head[1].trim().toLowerCase(); continue; }
    out.push({ section, text: line });
  }
  return out;
}

export function fallbackTiming(song: string, duration: number, md: string | null, why: string): Timing {
  const db = FALLBACK_DOWNBEATS.filter((d) => d < duration);
  const bars = db.slice(1).map((d, i) => d - db[i]);
  const bar = bars.length ? bars.reduce((a, b) => a + b, 0) / bars.length : 2.61;
  const beats: number[] = [];
  for (let t = db[0] - bar / 4; t > 0.05; t -= bar / 4) beats.unshift(r4(t));
  db.forEach((d, i) => {
    const span = i + 1 < db.length ? db[i + 1] - d : bar;
    for (let k = 0; k < 4; k++) { const t = d + (span * k) / 4; if (t < duration - 0.01) beats.push(r4(t)); }
  });
  const lyr = md ? parseLyrics(md) : [];
  const bySection = new Map<string, string[]>();
  for (const l of lyr) bySection.set(l.section, [...(bySection.get(l.section) ?? []), l.text]);
  const lines: Line[] = [];
  const known = [...bySection.keys()].filter((s) => FALLBACK_WINDOWS[s]);
  const unknown = [...bySection.keys()].filter((s) => !FALLBACK_WINDOWS[s]);
  const place = (section: string, texts: string[], a: number, b: number) => {
    const step = (b - a) / texts.length;
    texts.forEach((text, k) => lines.push({
      text, section, confidence: "approx",
      start: r4(a + k * step), end: r4(a + (k + 1) * step - Math.min(0.15, step * 0.1)),
    }));
  };
  for (const s of known) place(s, bySection.get(s)!, ...FALLBACK_WINDOWS[s]);
  if (unknown.length) {
    const a = Math.min(duration - 1, Math.max(0.5, ...lines.map((l) => l.end))), b = duration - 0.5;
    if (b > a) unknown.forEach((s, i) => place(s, bySection.get(s)!, a + ((b - a) * i) / unknown.length, a + ((b - a) * (i + 1)) / unknown.length));
  }
  lines.sort((x, y) => x.start - y.start);
  const sections = known.map((s) => ({ name: s, start: FALLBACK_WINDOWS[s][0], end: FALLBACK_WINDOWS[s][1] }));
  return {
    song, duration_s: duration, bpm: r4(240 / bar), beats, downbeats: db, sections, lines,
    approximate: true, source: "fallback (built-in downbeats + recipe lyrics at approximate times)", warning: why,
  };
}

async function readJson(abs: string): Promise<unknown | undefined> {
  const f = Bun.file(abs);
  if (!(await f.exists())) return undefined;
  try { return JSON.parse(await f.text()); } catch { return null; }
}

/** The real timing.json if present and sane, else the approximate fallback. Never throws. */
export async function loadTiming(song: string, duration: number): Promise<Timing> {
  const tp = timingPathFor(song);
  const j = (await readJson(join(ROOT, tp))) as Partial<Timing> | null | undefined;
  const md = await Bun.file(join(ROOT, lyricsPathFor(song))).text().catch(() => null);
  if (j === undefined) return fallbackTiming(song, duration, md, `${tp} is missing`);
  if (j === null || !Array.isArray(j.beats) || !Array.isArray(j.downbeats)) {
    return fallbackTiming(song, duration, md, `${tp} is not valid timing JSON yet`);
  }
  const nums = (a: unknown[]) => a.filter(isNum).map(r4).sort((x, y) => x - y);
  const lines = (Array.isArray(j.lines) ? j.lines : [])
    .filter((l): l is Line => !!l && typeof l.text === "string" && isNum(l.start) && isNum(l.end) && l.end > l.start)
    .map((l) => ({ text: l.text, start: r4(l.start), end: r4(l.end), section: l.section, confidence: l.confidence }));
  return {
    song, duration_s: isNum(j.duration_s) ? j.duration_s : duration, bpm: isNum(j.bpm) ? j.bpm : undefined,
    beats: nums(j.beats), downbeats: nums(j.downbeats),
    sections: Array.isArray(j.sections) ? j.sections : [], lines, approximate: false, source: tp,
  };
}

// ---------------------------------------------------------------------------------------------------------------
// The frame plan. MIRRORED in cut/index.html <script id="cut-core">; change both together.
// ---------------------------------------------------------------------------------------------------------------

export type SegPlan = {
  i: number; id: string;
  start: number; end: number; // frames this scene renders, including dissolve overlap: [start, end)
  cut: number; sceneEnd: number; // the cut frame and the frame where the next scene cuts in
  xIn: number; xStart: number; // dissolve-in frames (0 = hard cut) and the frame where it starts
};
export type Plan = { fps: number; W: number; H: number; total: number; barH: number; segs: SegPlan[]; flashes: { start: number; frames: number }[] };

/** A musical time to a frame, landing cut_lead_frames early (so the new image is settled on the beat). */
export const leadFrame = (t: number, fps: number, lead: number, total: number) =>
  t <= 0 ? 0 : clamp(Math.round(t * fps) - lead, 0, total);

export function planEdl(edl: Edl): Plan {
  const F = edl.fps, [W, H] = edl.size, total = Math.round(edl.duration_s * F), L = edl.globals.cut_lead_frames;
  const sc = edl.scenes, n = sc.length;
  const c = sc.map((s, i) => (i === 0 ? 0 : leadFrame(s.t0, F, L, total)));
  const e = c.map((_, i) => (i === n - 1 ? total : c[i + 1]));
  // Dissolve lengths, clamped so a dissolve never eats a whole scene or overlaps its neighbour.
  const x = sc.map((s, i) => {
    if (s.transition_in?.type !== "dissolve") return 0;
    const N = Math.max(0, Math.round(s.transition_in.frames || 0));
    if (i === 0) return Math.min(N, Math.max(0, e[0] - c[0] - 1)); // fade in from black
    return Math.min(N, Math.max(0, Math.min(e[i - 1] - c[i - 1], e[i] - c[i]) - 2));
  });
  const half = (i: number) => (i === 0 ? 0 : Math.floor(x[i] / 2)); // dissolves are centered on the cut
  const segs = sc.map((s, i) => ({
    i, id: s.id,
    start: i === 0 ? 0 : c[i] - half(i),
    end: i === n - 1 ? total : x[i + 1] > 0 ? c[i + 1] - half(i + 1) + x[i + 1] : e[i],
    cut: c[i], sceneEnd: e[i], xIn: x[i], xStart: i === 0 ? 0 : c[i] - half(i),
  }));
  const fl = edl.globals.flash;
  const flashes = fl.on ? fl.at.map((t) => ({ start: leadFrame(t, F, L, total), frames: Math.max(1, Math.round(fl.frames)) })) : [];
  const lb = edl.globals.letterbox;
  const barH = lb.on && lb.ratio > 0 ? Math.max(0, Math.round((H - W / lb.ratio) / 2)) : 0;
  return { fps: F, W, H, total, barH, segs, flashes };
}

/** Event time (punch, beat punch) snapped like a cut: the frame it lands on, in seconds. */
const leadTime = (t: number, edl: Edl) =>
  leadFrame(t, edl.fps, edl.globals.cut_lead_frames, Math.round(edl.duration_s * edl.fps)) / edl.fps;

/** Letterbox progress 0..1 at output time t (smoothstep in, smoothstep out). Same formula as the page. */
export function letterboxP(g: Globals, t: number, duration: number) {
  const lb = g.letterbox;
  if (!lb.on) return 0;
  const ss = (v: number) => { const u = clamp(v, 0, 1); return u * u * (3 - 2 * u); };
  const dIn = Math.max(1e-3, lb.wipe_dur_s);
  const dOut = Math.max(1e-3, Math.min(lb.wipe_dur_s, duration - lb.wipe_out_s));
  return ss((t - lb.wipe_in_s) / dIn) * (1 - ss((t - lb.wipe_out_s) / dOut));
}

// ---------------------------------------------------------------------------------------------------------------
// Validation + seed (shared with PUT /edl and first-run GET /edl)
// ---------------------------------------------------------------------------------------------------------------

type Obj = Record<string, unknown>;
const isObj = (x: unknown): x is Obj => !!x && typeof x === "object" && !Array.isArray(x);

export function validateEdl(input: unknown): { ok: true; edl: Edl; warnings: string[] } | { ok: false; errors: string[] } {
  const errors: string[] = [], warnings: string[] = [];
  if (!isObj(input)) return { ok: false, errors: ["the EDL must be a JSON object"] };
  const o = input;
  if (o.version !== 1) errors.push("version must be 1");
  const song = typeof o.song === "string" ? o.song : "";
  if (!/^songs\/[\w.\-() ]+\.(mp3|wav|m4a|flac|aac)$/i.test(song) || song.includes("..")) errors.push(`song must be a file in songs/ (got ${JSON.stringify(o.song)})`);
  const fps = o.fps;
  if (!isNum(fps) || !Number.isInteger(fps) || fps < 12 || fps > 60) errors.push("fps must be an integer 12..60");
  const size = o.size as unknown;
  if (!Array.isArray(size) || size.length !== 2 || !size.every((v) => isNum(v) && Number.isInteger(v) && v % 2 === 0 && v >= 160 && v <= 3840)) errors.push("size must be [even width, even height], 160..3840");
  const duration = o.duration_s;
  if (!isNum(duration) || duration <= 0 || duration > 600) errors.push("duration_s must be 0..600 seconds");
  const offset = o.sync_offset_s ?? 0;
  if (!isNum(offset) || Math.abs(offset) > 5) errors.push("sync_offset_s must be a number, |x| <= 5");

  // Globals: missing fields take defaults; present fields must have the right type.
  const gIn = isObj(o.globals) ? o.globals : {};
  const g = structuredClone(DEFAULT_GLOBALS) as Globals;
  const merge = (key: keyof Globals) => {
    const v = gIn[key];
    if (v === undefined) return;
    if (key === "cut_lead_frames") { if (isNum(v) && Number.isInteger(v) && v >= 0 && v <= 3) g.cut_lead_frames = v; else errors.push("globals.cut_lead_frames must be 0..3"); return; }
    if (!isObj(v)) { errors.push(`globals.${key} must be an object`); return; }
    const target = g[key] as Obj;
    for (const [k, val] of Object.entries(v)) {
      if (!(k in target)) continue; // unknown keys are dropped
      const want = target[k];
      if (typeof want === "boolean" ? typeof val !== "boolean" : typeof want === "number" ? !isNum(val) : typeof want === "string" ? typeof val !== "string" : Array.isArray(want) ? !Array.isArray(val) || !val.every(isNum) : false) {
        errors.push(`globals.${key}.${k} has the wrong type`);
        continue;
      }
      target[k] = Array.isArray(val) ? [...val].map(r4).sort((a, b) => a - b) : isNum(val) ? r4(val) : val;
    }
  };
  (Object.keys(DEFAULT_GLOBALS) as (keyof Globals)[]).forEach(merge);
  if (g.letterbox.ratio < 1 || g.letterbox.ratio > 4) errors.push("globals.letterbox.ratio must be 1..4");
  if (g.letterbox.wipe_dur_s < 0 || g.letterbox.wipe_dur_s > 10) errors.push("globals.letterbox.wipe_dur_s must be 0..10");
  if (g.captions.y < 0.05 || g.captions.y > 0.99) errors.push("globals.captions.y must be 0.05..0.99");
  if (g.captions.size < 10 || g.captions.size > 120) errors.push("globals.captions.size must be 10..120");
  if (!isHex(g.captions.color)) errors.push("globals.captions.color must be #rrggbb");
  if (!(g.grade.preset in GRADE_PRESETS)) errors.push(`globals.grade.preset must be one of ${Object.keys(GRADE_PRESETS).join(", ")}`);
  if (!isHex(g.flash.color)) errors.push("globals.flash.color must be #rrggbb");
  if (g.flash.frames < 1 || g.flash.frames > 24) errors.push("globals.flash.frames must be 1..24");
  if (g.flash.opacity < 0 || g.flash.opacity > 1) errors.push("globals.flash.opacity must be 0..1");
  if (g.beat_punch.amount < 0 || g.beat_punch.amount > 0.5) errors.push("globals.beat_punch.amount must be 0..0.5");
  if (g.punch.amount < 0 || g.punch.amount > 1) errors.push("globals.punch.amount must be 0..1");
  if (g.beat_punch.decay_s <= 0 || g.punch.decay_s <= 0) errors.push("punch decay_s must be > 0");

  const scenesIn = Array.isArray(o.scenes) ? o.scenes : [];
  if (!scenesIn.length || scenesIn.length > 64) errors.push("scenes must be a list of 1..64 scenes");
  const ids = new Set<string>();
  const scenes: Scene[] = [];
  scenesIn.forEach((s: unknown, i: number) => {
    const at = `scenes[${i}]`;
    if (!isObj(s)) { errors.push(`${at} must be an object`); return; }
    const id = typeof s.id === "string" ? s.id : "";
    if (!/^[a-z0-9][a-z0-9_-]{0,47}$/i.test(id)) errors.push(`${at}.id must be a short slug (got ${JSON.stringify(s.id)})`);
    if (ids.has(id)) errors.push(`${at}.id ${id} is used twice`);
    ids.add(id);
    const src = isObj(s.source) ? s.source : {};
    const path = safeMediaPath(src.path);
    if (!path) errors.push(`${at}.source.path must be a video or image under ${ALLOWED_MEDIA_DIRS.join(", ")} (got ${JSON.stringify(src.path)})`);
    const cam = isObj(s.camera_post) ? s.camera_post : {};
    const camFx = (cam.fx ?? "none") as CameraFx;
    if (!["slow_push", "drift", "none"].includes(camFx)) errors.push(`${at}.camera_post.fx must be slow_push, drift or none`);
    const from = isNum(cam.from) ? cam.from : 1, to = isNum(cam.to) ? cam.to : from;
    if (from < 1 || from > 3 || to < 1 || to > 3) errors.push(`${at}.camera_post from/to must be 1..3 (1 = full frame)`);
    const tr = isObj(s.transition_in) ? s.transition_in : {};
    const trType = tr.type ?? "cut";
    if (trType !== "cut" && trType !== "dissolve") errors.push(`${at}.transition_in.type must be cut or dissolve`);
    const trFrames = isNum(tr.frames) ? Math.round(tr.frames) : 0;
    if (trFrames < 0 || trFrames > 48) errors.push(`${at}.transition_in.frames must be 0..48`);
    const inS = isNum(src.in_s) ? src.in_s : 0, rt = isNum(src.rate) ? src.rate : 1;
    if (inS < 0 || inS > 3600) errors.push(`${at}.source.in_s must be >= 0`);
    if (rt < 0.1 || rt > 4) errors.push(`${at}.source.rate must be 0.1..4`);
    const loop = src.loop === "hold" ? "hold" : "loop";
    const punches = Array.isArray(s.punch_in_at) ? s.punch_in_at : [];
    if (!punches.every(isNum)) errors.push(`${at}.punch_in_at must be a list of seconds`);
    const go = s.grade_override ?? null;
    if (go !== null && !(typeof go === "string" && go in GRADE_PRESETS)) errors.push(`${at}.grade_override must be null or one of ${Object.keys(GRADE_PRESETS).join(", ")}`);
    if (!isNum(s.t0) || !isNum(s.t1)) errors.push(`${at} needs numeric t0 and t1`);
    scenes.push({
      id, title: typeof s.title === "string" ? s.title.slice(0, 80) : id, t0: r4(Number(s.t0)), t1: r4(Number(s.t1)),
      source: { path: path ?? String(src.path), in_s: r4(inS), rate: r4(rt), placeholder: src.placeholder !== false, loop },
      camera_post: { fx: camFx, from: r4(from), to: r4(to) },
      punch_in_at: (punches.filter(isNum) as number[]).map(r4).sort((a, b) => a - b),
      transition_in: { type: trType === "dissolve" ? "dissolve" : "cut", frames: trFrames },
      clones: { on: isObj(s.clones) ? s.clones.on === true : false, mode: "triptych" },
      grade_override: (go as string | null),
      ...(typeof s.note === "string" && s.note ? { note: s.note.slice(0, 400) } : {}),
    });
  });
  if (!errors.length && isNum(duration)) {
    const eps = 5e-4;
    scenes.forEach((s, i) => {
      if (i === 0 && Math.abs(s.t0) > eps) errors.push(`the first scene must start at 0 (got ${s.t0})`);
      if (i > 0 && Math.abs(s.t0 - scenes[i - 1].t1) > eps) errors.push(`scene ${s.id} starts at ${s.t0} but ${scenes[i - 1].id} ends at ${scenes[i - 1].t1}: scenes must butt together`);
      if (s.t1 - s.t0 < 0.25) errors.push(`scene ${s.id} is shorter than 0.25 s`);
      if (i === scenes.length - 1 && Math.abs(s.t1 - duration) > eps) errors.push(`the last scene must end at duration_s ${duration} (got ${s.t1})`);
      const out = s.punch_in_at.filter((p) => p < s.t0 - eps || p >= s.t1);
      if (out.length) warnings.push(`scene ${s.id}: punch-ins outside the scene are ignored (${out.join(", ")})`);
    });
  }
  if (errors.length) return { ok: false, errors };
  const edl: Edl = {
    version: 1, song, duration_s: r4(duration as number), fps: fps as number, size: size as [number, number],
    sync_offset_s: r4(offset as number), globals: g, scenes,
  };
  return { ok: true, edl, warnings };
}

/** The canonical scene plan (rnb-director, locked 2026-11-02), with the best placeholder per scene. */
export async function seedEdl(): Promise<Edl> {
  const song = "songs/in-the-loop-devagar.mp3";
  const songInfo = await probeSongDuration(join(ROOT, song));
  const duration = songInfo ?? 32;
  const timing = await loadTiming(song, duration);
  const lastBeat = [...timing.beats].filter((b) => b < duration - 0.05).pop() ?? duration - 0.5;
  const animatic = "outputs/previs/animatic.mp4";
  // Use the animatic only when it is complete: readable and long enough to cover s1 (previs may be mid-write).
  const anim = existsSync(join(ROOT, animatic)) ? await probeMedia(join(ROOT, animatic)) : null;
  const hasAnimatic = !!anim && anim.kind === "video" && (anim.duration_s ?? 0) >= 6.9;
  // The lipsync renders were made from the song's 17-25 s window, so source t=0 is song t=17.0.
  // (Checked by audio cross-correlation: infinitetalk 17.00 s, corr 0.99; ia2v 17.03 s, re-synthesized audio.)
  const LIPSYNC_SONG_T0 = 17.0;
  const scenes: Scene[] = [
    {
      id: "s1-intro", title: "Intro: couch at night", t0: 0, t1: 6.9,
      source: hasAnimatic
        ? { path: animatic, in_s: 0, rate: 1, placeholder: true, loop: "hold" }
        : { path: "reference-master/tests/wardrobe_chorus-stage.png", in_s: 0, rate: 1, placeholder: true, loop: "hold" },
      camera_post: { fx: "slow_push", from: 1, to: 1.08 }, punch_in_at: [],
      transition_in: { type: "cut", frames: 0 }, clones: { on: false, mode: "triptych" }, grade_override: null,
      note: hasAnimatic ? "Previs animatic, its own segment (song-aligned, so in_s = t0)." : "Look still (chorus-stage wardrobe) until outputs/scenes/s1-intro.mp4 lands. The letterbox wipes in over it.",
    },
    {
      id: "s2-verse", title: "Verse: living room, laptop glow", t0: 6.9, t1: 17.34,
      source: { path: "outputs/chunk-02_claymation_pose.mp4", in_s: 0, rate: 0.5, placeholder: true, loop: "loop" },
      camera_post: { fx: "drift", from: 1.04, to: 1.08 }, punch_in_at: [],
      transition_in: { type: "dissolve", frames: 10 }, clones: { on: false, mode: "triptych" }, grade_override: null,
      note: "3 s claymation restyle at half speed, looping. Stand-in for the handheld float.",
    },
    {
      id: "s3-chorus", title: "Chorus lift: the glow-up", t0: 17.34, t1: 25.16,
      source: { path: "outputs/chorus_lipsync_ia2v.mp4", in_s: r4(17.34 - LIPSYNC_SONG_T0), rate: 1, placeholder: true, loop: "hold" },
      camera_post: { fx: "slow_push", from: 1, to: 1.12 }, punch_in_at: [22.56],
      transition_in: { type: "cut", frames: 0 }, clones: { on: false, mode: "triptych" }, grade_override: null,
      note: "ia2v lipsync of the song's 17-25 s window: in_s = t0 - 17.0 keeps her mouth on the song. Hard cut plus the warm flash.",
    },
    {
      id: "s4-harmony", title: "Harmonies: girl group of me", t0: 25.16, t1: r4(duration),
      source: { path: "outputs/chorus_lipsync_infinitetalk.mp4", in_s: 0, rate: 1, placeholder: true, loop: "hold" },
      camera_post: { fx: "drift", from: 1.06, to: 1.1 }, punch_in_at: [],
      transition_in: { type: "dissolve", frames: 10 }, clones: { on: true, mode: "triptych" }, grade_override: null,
      note: "infinitetalk lipsync (17-25 s window, so the mouth is not on these lines yet), as an ffmpeg triptych.",
    },
  ];
  // If the director's shot list has scenes, take its cut points (single source of truth for times).
  const shots = (await readJson(join(ROOT, shotsPathFor(song)))) as { scenes?: { id?: string; t0?: number; t1?: number; title?: string }[] } | null | undefined;
  if (shots && Array.isArray(shots.scenes) && shots.scenes.length === scenes.length) {
    shots.scenes.forEach((s, i) => {
      if (isNum(s.t0) && isNum(s.t1)) { scenes[i].t0 = r4(s.t0); scenes[i].t1 = r4(s.t1); }
      if (typeof s.title === "string" && s.title) scenes[i].title = s.title;
    });
    scenes[0].t0 = 0; scenes[scenes.length - 1].t1 = r4(duration);
    for (let i = 1; i < scenes.length; i++) scenes[i].t0 = scenes[i - 1].t1;
  }
  const edl: Edl = {
    version: 1, song, duration_s: r4(duration), fps: 24, size: [1280, 720], sync_offset_s: 0,
    globals: { ...structuredClone(DEFAULT_GLOBALS), letterbox: { ...DEFAULT_GLOBALS.letterbox, wipe_out_s: r4(lastBeat) } },
    scenes,
  };
  const v = validateEdl(edl);
  if (!v.ok) throw new Error("seed EDL failed validation: " + v.errors.join("; "));
  return v.edl;
}

async function probeSongDuration(abs: string): Promise<number | null> {
  if (!existsSync(abs)) return null;
  const p = Bun.spawn(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", abs], { stdout: "pipe", stderr: "ignore" });
  const out = (await new Response(p.stdout).text()).trim();
  await p.exited;
  const d = Number(out);
  return Number.isFinite(d) && d > 0 ? r4(d) : null;
}

// ---------------------------------------------------------------------------------------------------------------
// ffmpeg pieces
// ---------------------------------------------------------------------------------------------------------------

/** CSS filter math (sRGB, clamped after each step) as ffmpeg RGB filters. */
export function gradeFilters(gr: Grade): string[] {
  const a = gr.sepia, s = gr.saturate, k = 1 - a;
  const ident = a === 0 && s === 1 && gr.contrast === 1 && gr.brightness === 1 && gr.warm_opacity === 0;
  if (ident) return [];
  const ccm = (m: number[]) =>
    `colorchannelmixer=rr=${fx(m[0])}:rg=${fx(m[1])}:rb=${fx(m[2])}:gr=${fx(m[3])}:gg=${fx(m[4])}:gb=${fx(m[5])}:br=${fx(m[6])}:bg=${fx(m[7])}:bb=${fx(m[8])}`;
  const out: string[] = [];
  if (a > 0) out.push(ccm([0.393 + 0.607 * k, 0.769 - 0.769 * k, 0.189 - 0.189 * k, 0.349 - 0.349 * k, 0.686 + 0.314 * k, 0.168 - 0.168 * k, 0.272 - 0.272 * k, 0.534 - 0.534 * k, 0.131 + 0.869 * k]));
  if (s !== 1) out.push(ccm([0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s, 0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s, 0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]));
  const w = hex01(gr.warm), o = gr.warm_opacity;
  const mul = w.map((c) => (1 - o + o * c) * gr.brightness); // multiply overlay folded into brightness
  const lut = (m: number) => `'clip((val/maxval-0.5)*${fx(gr.contrast)}+0.5,0,1)*${fx(m)}*maxval'`;
  out.push(`lutrgb=r=${lut(mul[0])}:g=${lut(mul[1])}:b=${lut(mul[2])}`);
  return out;
}

/** Camera scale S(T) as an ffmpeg expression in absolute output time T. Same formula as the page. */
function cameraExpr(edl: Edl, scene: Scene, T: string, beats: number[]): string | null {
  const cp = scene.camera_post, g = edl.globals;
  const parts: string[] = [];
  if (cp.fx !== "none" && (cp.from !== 1 || cp.to !== 1)) {
    parts.push(`(${fx(cp.from)}+(${fx(cp.to - cp.from)})*clip((${T}-${fx(scene.t0)})/${fx(scene.t1 - scene.t0)},0,1))`);
  }
  const bump = (times: number[], amount: number, decay: number) => {
    const terms = times.map((p) => `gte(${T},${fx(p)})*exp(-max(${T}-${fx(p)},0)/${fx(decay)})`);
    return terms.length && amount > 0 ? `(1+${fx(amount)}*(${terms.join("+")}))` : null;
  };
  const punches = scene.punch_in_at.filter((p) => p >= scene.t0 && p < scene.t1).map((p) => leadTime(p, edl));
  const pb = bump(punches, g.punch.amount, g.punch.decay_s);
  if (pb) parts.push(pb);
  if (g.beat_punch.on) {
    const lo = scene.t0 - 1, hi = scene.t1 + 1;
    const bb = bump(beats.map((b) => b + edl.sync_offset_s).filter((b) => b >= lo && b <= hi).map((b) => leadTime(b, edl)), g.beat_punch.amount, g.beat_punch.decay_s);
    if (bb) parts.push(bb);
  }
  return parts.length ? parts.join("*") : null;
}

type Probed = MediaInfo & { abs: string; rel: string };

/** One scene -> WxH@fps frames [seg.start, seg.end): source timing, clones, camera, grade. */
function segmentJob(edl: Edl, plan: Plan, seg: SegPlan, src: Probed, beats: number[], masks: { side: string; lead: string } | null) {
  const scene = edl.scenes[seg.i], F = edl.fps, W = plan.W, H = plan.H;
  const len = seg.end - seg.start, tStart = seg.start / F;
  const args: string[] = [];
  const chain: string[] = [];
  if (src.kind === "image") {
    args.push("-loop", "1", "-framerate", String(F), "-t", fx((len + 2) / F), "-i", src.abs);
  } else {
    const r = scene.source.rate;
    // Source time at output time t: in_s + (t - t0) * rate. Below 0 it holds the first frame; past the end it
    // loops (-stream_loop) or holds the last frame (tpad clone). Same rule as the page's sourceTime().
    const s0 = scene.source.in_s + (tStart - scene.t0) * r;
    const sEnd = s0 + (len / F) * r;
    const loop = scene.source.loop === "loop" && sEnd > (src.duration_s ?? 0) - 1e-6;
    if (loop) args.push("-stream_loop", "-1", "-t", fx(Math.max(0, sEnd) + 1));
    args.push("-i", src.abs);
    chain.push(`setpts='(PTS-STARTPTS)/${fx(r)}+(${fx(-s0 / r)})/TB'`, `fps=${F}:start_time=0`);
  }
  chain.push("tpad=stop_mode=clone:stop=-1", `trim=end_frame=${len}`, "setpts=PTS-STARTPTS",
    "setsar=1", `scale=${W}:${H}:force_original_aspect_ratio=increase:flags=lanczos`, `crop=${W}:${H}`, "format=gbrp");
  const graph: string[] = [`[0:v]${chain.join(",")}[norm]`];
  let last = "norm";

  if (scene.clones.on && masks) {
    const K = CLONE_LAYOUT;
    const sw = 2 * Math.round((W * K.side_scale) / 2), sh = 2 * Math.round((H * K.side_scale) / 2);
    const top = Math.round(H * K.side_top);
    const lx = Math.round(W * K.side_cx[0] - sw / 2), rx = Math.round(W * K.side_cx[1] - sw / 2);
    const dark = `colorchannelmixer=rr=${fx(K.side_dark)}:gg=${fx(K.side_dark)}:bb=${fx(K.side_dark)}`;
    args.push("-loop", "1", "-framerate", String(F), "-t", fx((len + 2) / F), "-i", masks.side);
    args.push("-loop", "1", "-framerate", String(F), "-t", fx((len + 2) / F), "-i", masks.lead);
    graph.push(
      `[1:v]format=gray,split[ms1][ms2]`, `[2:v]format=gray[ml]`,
      `[${last}]split=4[cb][cl][cr][cc]`,
      `[cb]colorchannelmixer=rr=${fx(K.bg_dark)}:gg=${fx(K.bg_dark)}:bb=${fx(K.bg_dark)}[cbk]`,
      `[cl]scale=${sw}:${sh}:flags=bicubic${K.flip_left ? ",hflip" : ""},${dark}[cl1]`, `[cl1][ms1]alphamerge[cl2]`,
      `[cr]scale=${sw}:${sh}:flags=bicubic,${dark}[cr1]`, `[cr1][ms2]alphamerge[cr2]`,
      `[cc][ml]alphamerge[cc2]`,
      `[cbk][cl2]overlay=x=${lx}:y=${top}:format=gbrp[o1]`,
      `[o1][cr2]overlay=x=${rx}:y=${top}:format=gbrp[o2]`,
      `[o2][cc2]overlay=x=0:y=0:format=gbrp[clo]`,
    );
    last = "clo";
  }

  // Post camera: S(T) with T = segment time t + segment start. Crop repeats the scale's size expression
  // (crop's own iw/ih go stale after eval=frame; see the header).
  const T = `(t+${fx(tStart)})`;
  const S = cameraExpr(edl, scene, T, beats);
  if (S) {
    const sw = `(2*round(${W}*${S}/2))`, sh = `(2*round(${H}*${S}/2))`;
    const mx = `((${sw}-${W})/2)`, my = `((${sh}-${H})/2)`;
    const u = `clip((${T}-${fx(scene.t0)})/${fx(scene.t1 - scene.t0)},0,1)`;
    const x = scene.camera_post.fx === "drift" ? `floor(${mx}*(1+0.8*(2*${u}-1)))` : mx;
    graph.push(`[${last}]scale=w='${sw}':h='${sh}':eval=frame:flags=bicubic,crop=w=${W}:h=${H}:x='${x}':y='${my}'[cam]`);
    last = "cam";
  }

  const preset = scene.grade_override ?? (edl.globals.grade.on ? edl.globals.grade.preset : "none");
  const post = [...gradeFilters(GRADE_PRESETS[preset] ?? GRADE_PRESETS.none),
    "scale=out_color_matrix=bt709:out_range=tv", "format=yuv420p", "setsar=1"];
  if (seg.i === 0 && seg.xIn > 0) post.push(`fade=t=in:s=0:n=${seg.xIn}`); // dissolve into the first scene = from black
  graph.push(`[${last}]${post.join(",")}[vout]`);
  return { args, graph: graph.join(";"), len, preset };
}

function captionPng(text: string, size: number, color: string, W: number, boxH: number, font: string, out: string) {
  const fitted = Math.max(10, Math.min(size, Math.floor((W * CAPTION_STYLE.max_width) / Math.max(1, text.length * CAPTION_STYLE.char_w))));
  // ImageMagick treats % as an escape and a leading @ as "read a file". Neutralize both.
  const safe = text.replace(/\\/g, "\\\\").replace(/%/g, "%%").replace(/^@/, "\\@");
  const sh = CAPTION_STYLE.shadow;
  return ["magick", "-size", `${W}x${boxH}`, "xc:none", "-font", font, "-pointsize", String(fitted), "-gravity", "center",
    "(", "+clone", "-fill", `rgba(0,0,0,${sh.opacity})`, "-annotate", `+0+${sh.dy}`, safe, "-blur", `0x${sh.sigma}`, ")",
    "-composite", "-fill", color, "-annotate", "+0+0", safe, `PNG32:${out}`];
}

async function run(cmd: string[], onStdoutLine?: (l: string) => void): Promise<{ code: number; stderr: string }> {
  const p = Bun.spawn(cmd, { stdout: "pipe", stderr: "pipe", stdin: "ignore" });
  const errText = new Response(p.stderr).text();
  const dec = new TextDecoder();
  let buf = "";
  for await (const chunk of p.stdout as unknown as AsyncIterable<Uint8Array>) {
    if (!onStdoutLine) continue;
    buf += dec.decode(chunk, { stream: true });
    let k: number;
    while ((k = buf.indexOf("\n")) >= 0) { onStdoutLine(buf.slice(0, k).replace(/\r$/, "")); buf = buf.slice(k + 1); }
  }
  if (onStdoutLine && buf) onStdoutLine(buf);
  return { code: await p.exited, stderr: await errText };
}
const tail = (s: string, n = 25) => s.trim().split("\n").slice(-n).join("\n");

export async function ffmpegFilters(): Promise<Set<string>> {
  const p = Bun.spawn(["ffmpeg", "-hide_banner", "-filters"], { stdout: "pipe", stderr: "ignore" });
  const out = await new Response(p.stdout).text();
  await p.exited;
  const names = new Set<string>();
  for (const line of out.split("\n")) { const m = /^\s*[T.][S.]?\s+(\S+)\s+\S+->\S+/.exec(line) ?? /^\s*\S{2,3}\s+(\S+)\s+\S*->\S*/.exec(line); if (m) names.add(m[1]); }
  return names;
}
const has = (cmd: string) => Bun.which(cmd) !== null;

// ---------------------------------------------------------------------------------------------------------------
// render()
// ---------------------------------------------------------------------------------------------------------------

export type Receipt = {
  path: string; duration_s: number; fps: number; width: number; height: number; has_audio: boolean; bytes: number;
  frames: number; video_codec: string; pix_fmt: string; audio: { codec: string; sample_rate: number; channels: number; duration_s: number } | null;
  md5: string; elapsed_s: number; captions: string; timing: string; placeholders: string[]; warnings: string[];
  stitch_version: string; ffmpeg: string;
};

export async function render(opts: { edlPath: string; outPath: string; captions?: string; keep?: boolean },
  log: (line: string) => void = console.log): Promise<Receipt> {
  const t0 = performance.now();
  const step = (p: number, label: string) => log(`PROGRESS ${p.toFixed(3)} ${label}`);
  const warnings: string[] = [];
  for (const c of ["ffmpeg", "ffprobe"]) if (!has(c)) throw new Error(`${c} is not on PATH (brew install ffmpeg)`);

  // 1. EDL
  const raw = await readJson(opts.edlPath);
  if (raw === undefined) throw new Error(`EDL not found: ${opts.edlPath}`);
  if (raw === null) throw new Error(`EDL is not valid JSON: ${opts.edlPath}`);
  const v = validateEdl(raw);
  if (!v.ok) throw new Error("EDL is invalid:\n  - " + v.errors.join("\n  - "));
  const edl = v.edl;
  warnings.push(...v.warnings);
  const plan = planEdl(edl);
  const F = edl.fps, W = plan.W, H = plan.H;
  log(`edl ${rel(opts.edlPath)}: ${edl.scenes.length} scenes, ${plan.total} frames (${edl.duration_s}s @ ${F}fps), ${W}x${H}`);

  // 2. Preflight: filters, song, sources
  const filters = await ffmpegFilters();
  const need = ["xfade", "concat", "overlay", "scale", "crop", "fps", "setpts", "tpad", "trim", "colorchannelmixer", "lutrgb", "alphamerge", "drawbox", "color", "split", "hflip", "format", "fade", "geq", "adelay", "atrim", "apad", "aresample", "asetpts"];
  const missing = need.filter((f) => !filters.has(f));
  if (missing.length) throw new Error(`this ffmpeg lacks filters: ${missing.join(", ")}`);
  const verOut = Bun.spawnSync(["ffmpeg", "-version"]).stdout.toString().split("\n")[0] ?? "";
  const ffver = /ffmpeg version (\S+)/.exec(verOut)?.[1] ?? "unknown";
  const songAbs = join(ROOT, edl.song);
  if (!existsSync(songAbs)) throw new Error(`song not found: ${edl.song}`);
  const sources: Probed[] = [];
  const problems: string[] = [];
  for (const s of edl.scenes) {
    const abs = join(ROOT, s.source.path);
    const info = existsSync(abs) ? await probeMedia(abs) : null;
    if (!info) problems.push(`scene ${s.id}: source ${existsSync(abs) ? "is unreadable" : "not found"}: ${s.source.path}`);
    else sources.push({ ...info, abs, rel: s.source.path });
  }
  if (problems.length) throw new Error(problems.join("\n") + "\npick another source in the page (or fix the path in the EDL)");
  edl.scenes.forEach((s, i) => {
    const d = sources[i].duration_s;
    if (sources[i].kind === "video" && d && s.source.in_s >= d) warnings.push(`scene ${s.id}: in_s ${s.source.in_s} is past the source end (${d}s)`);
  });
  const timing = await loadTiming(edl.song, edl.duration_s);
  if (timing.approximate) warnings.push(`timing: ${timing.warning}; captions use approximate line times`);
  step(0.04, "preflight ok");

  // 3. Scratch dir, one per render (outputs/cut/render-<pid>/), so two renders never trample each other.
  //    Removed on success unless --keep; kept on failure for debugging. Old leftovers (>6 h) are swept.
  const cutDir = join(ROOT, "outputs", "cut");
  mkdirSync(cutDir, { recursive: true });
  for (const d of readdirSync(cutDir)) {
    if (!/^render-\d+$/.test(d)) continue;
    try { if (Date.now() - statSync(join(cutDir, d)).mtimeMs > 6 * 3600e3) rmSync(join(cutDir, d), { recursive: true, force: true }); } catch { /* raced */ }
  }
  const scratch = join(cutDir, `render-${process.pid}`);
  rmSync(scratch, { recursive: true, force: true });
  mkdirSync(scratch, { recursive: true });

  // 4. Clone masks (one PNG each, same alpha ramps as the page's canvas gradients)
  let masks: { side: string; lead: string } | null = null;
  if (edl.scenes.some((s) => s.clones.on)) {
    const K = CLONE_LAYOUT;
    const sw = 2 * Math.round((W * K.side_scale) / 2), sh = 2 * Math.round((H * K.side_scale) / 2);
    masks = { side: join(scratch, "mask_side.png"), lead: join(scratch, "mask_lead.png") };
    const m = K.side_mask, l = K.lead_mask;
    const side = `255*clip(min((X+0.5)/(${fx(m.h_feather)}*W),(W-X-0.5)/(${fx(m.h_feather)}*W)),0,1)*clip(min((Y+0.5)/(${fx(m.v_top)}*H),(H-Y-0.5)/(${fx(m.v_bottom)}*H)),0,1)`;
    const lead = `255*clip((${fx(l.core + l.ramp)}*W-abs(X+0.5-W/2))/(${fx(l.ramp)}*W),0,1)`;
    for (const [expr, size, out] of [[side, `${sw}x${sh}`, masks.side], [lead, `${W}x${H}`, masks.lead]] as const) {
      const r = await run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "lavfi", "-i", `color=c=black:s=${size}:d=1`,
        "-vf", `format=gray,geq=lum='${expr}'`, "-frames:v", "1", out]);
      if (r.code !== 0) throw new Error("clone mask failed:\n" + tail(r.stderr));
    }
  }

  // 5. Captions
  const capCfg = edl.globals.captions;
  let capMode = (opts.captions ?? "auto").toLowerCase();
  const font = CAPTION_STYLE.font_files.find((f) => existsSync(f));
  if (!capCfg.on) capMode = "off";
  if (capMode === "auto") capMode = has("magick") && font ? "png" : filters.has("drawtext") && font ? "drawtext" : "off";
  if (capMode === "drawtext" && !filters.has("drawtext")) { warnings.push("captions: this ffmpeg has no drawtext filter (no libfreetype); used PNG captions instead"); capMode = has("magick") ? "png" : "off"; }
  if (capMode === "png" && !has("magick")) { warnings.push("captions: ImageMagick (magick) not found; captions skipped"); capMode = "off"; }
  if (capMode !== "off" && !font) { warnings.push("captions: no italic serif font file found; captions skipped"); capMode = "off"; }
  if (capCfg.on && capMode === "off" && !warnings.some((w) => w.startsWith("captions"))) warnings.push("captions: skipped");
  const fd = CAPTION_STYLE.fade_frames;
  const boxH = 2 * Math.round((CAPTION_STYLE.box_h * H) / 720 / 2);
  const capSize = Math.round((capCfg.size * H) / 720);
  const capY = clamp(Math.round(capCfg.y * H - boxH / 2), 0, H - boxH);
  const caps = capMode === "off" ? [] : timing.lines
    .map((l) => ({ text: l.text.trim(), a: Math.round((l.start + edl.sync_offset_s) * F), b: Math.round((l.end + edl.sync_offset_s) * F) }))
    .map((c) => ({ ...c, a: clamp(c.a, 0, plan.total), b: clamp(c.b, 0, plan.total) }))
    .filter((c) => c.text && c.b - c.a >= 2);
  if (capMode === "png") {
    await Promise.all(caps.map(async (c, k) => {
      const out = join(scratch, `cap_${String(k).padStart(2, "0")}.png`);
      const r = await run(captionPng(c.text, capSize, capCfg.color, W, boxH, font!, out));
      if (r.code !== 0) throw new Error(`caption ${k} (${c.text}) failed:\n` + tail(r.stderr));
      (c as typeof c & { png?: string }).png = out;
    }));
  } else if (capMode === "drawtext") {
    await Promise.all(caps.map((c, k) => Bun.write(join(scratch, `cap_${String(k).padStart(2, "0")}.txt`), c.text)));
  }
  step(0.1, `captions: ${capMode}${caps.length ? ` (${caps.length} lines)` : ""}`);

  // 6. Segments
  const segFiles: string[] = [];
  for (const seg of plan.segs) {
    const scene = edl.scenes[seg.i];
    const job = segmentJob(edl, plan, seg, sources[seg.i], timing.beats, masks);
    const out = join(scratch, `seg_${seg.i}_${scene.id}.mp4`);
    await Bun.write(join(scratch, `seg_${seg.i}.graph.txt`), job.graph.split(";").join(";\n"));
    const t = performance.now();
    const r = await run(["ffmpeg", "-y", "-v", "error", "-nostdin", ...job.args, "-filter_complex", job.graph, "-map", "[vout]",
      "-frames:v", String(job.len), "-c:v", "libx264", "-preset", "veryfast", "-crf", "10", "-pix_fmt", "yuv420p",
      "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-an", out]);
    if (r.code !== 0) throw new Error(`scene ${scene.id} failed (graph in ${rel(join(scratch, `seg_${seg.i}.graph.txt`))}):\n` + tail(r.stderr));
    const got = (await probeMedia(out))?.frames ?? 0;
    if (got !== job.len) throw new Error(`scene ${scene.id}: rendered ${got} frames, the plan needs ${job.len}`);
    segFiles.push(out);
    log(`scene ${scene.id}: frames ${seg.start}-${seg.end - 1} (${job.len}), ${sources[seg.i].kind} ${scene.source.path}` +
      `${scene.clones.on ? ", clones" : ""}, grade ${job.preset}, ${((performance.now() - t) / 1000).toFixed(1)}s`);
    step(0.1 + (0.55 * (seg.i + 1)) / plan.segs.length, `scene ${seg.i + 1}/${plan.segs.length} ${scene.id}`);
  }

  // 7. Final pass: chain the scenes, then flash, letterbox, captions; the song is the only audio.
  const args: string[] = [];
  segFiles.forEach((f) => args.push("-i", f));
  let idx = segFiles.length;
  const capInputs: number[] = [];
  if (capMode === "png") for (const c of caps) { args.push("-loop", "1", "-framerate", String(F), "-t", fx(c.b / F + 0.5), "-i", (c as typeof c & { png: string }).png); capInputs.push(idx++); }
  const songIdx = idx++;
  args.push("-i", songAbs);
  const gph: string[] = segFiles.map((_, i) => `[${i}:v]fps=${F},setsar=1,format=yuv420p[s${i}]`);
  let cur = "s0";
  plan.segs.slice(1).forEach((seg) => {
    const outL = `x${seg.i}`;
    if (seg.xIn > 0) gph.push(`[${cur}][s${seg.i}]xfade=transition=fade:duration=${fx(seg.xIn / F)}:offset=${fx(seg.xStart / F)}[${outL}]`);
    else gph.push(`[${cur}][s${seg.i}]concat=n=2:v=1:a=0,settb=1/${F}[${outL}]`); // concat outputs 1/1000000; xfade needs both sides on 1/fps
    cur = outL;
  });
  const fin: string[] = [];
  const half = 0.25 / F; // enable windows pad half-ish a frame so float t never misses a frame edge
  const fl = edl.globals.flash;
  for (const f of plan.flashes) fin.push(`drawbox=x=0:y=0:w=iw:h=ih:color=0x${fl.color.slice(1)}@${fx(fl.opacity)}:t=fill:enable='between(t,${fx(f.start / F - half)},${fx((f.start + f.frames - 1) / F + half)})'`);
  gph.push(`[${cur}]${fin.length ? fin.join(",") : "null"}[vf]`);
  cur = "vf";
  if (plan.barH > 0) {
    const lb = edl.globals.letterbox, bh = plan.barH;
    const dIn = Math.max(1e-3, lb.wipe_dur_s), dOut = Math.max(1e-3, Math.min(lb.wipe_dur_s, edl.duration_s - lb.wipe_out_s));
    const P = `(st(0,clip((t-${fx(lb.wipe_in_s)})/${fx(dIn)},0,1))*0+st(1,clip((t-${fx(lb.wipe_out_s)})/${fx(dOut)},0,1))*0+ld(0)*ld(0)*(3-2*ld(0))*(1-ld(1)*ld(1)*(3-2*ld(1))))`;
    gph.push(`color=c=black:s=${W}x${bh}:r=${F}[lbt]`, `color=c=black:s=${W}x${bh}:r=${F}[lbb]`,
      `[${cur}][lbt]overlay=x=0:y='-${bh}+round(${bh}*${P})':eval=frame:shortest=1[lb1]`,
      `[lb1][lbb]overlay=x=0:y='${H}-round(${bh}*${P})':eval=frame:shortest=1[lb2]`);
    cur = "lb2";
  }
  caps.forEach((c, k) => {
    const a = c.a / F, b = c.b / F, d = fd / F;
    const en = `enable='between(t,${fx(a - half)},${fx(b - 1 / F + half)})'`;
    if (capMode === "png") {
      gph.push(`[${capInputs[k]}:v]format=rgba,fade=t=in:st=${fx(a)}:d=${fx(d)}:alpha=1,fade=t=out:st=${fx(b - d)}:d=${fx(d)}:alpha=1[c${k}]`,
        `[${cur}][c${k}]overlay=x=0:y=${capY}:eof_action=pass:${en}[v${k}]`);
    } else {
      // drawtext (only on builds with libfreetype; untested on this machine's 9.0.1, which lacks it).
      // textfile + expansion=none sidesteps text escaping entirely; only the paths are quoted.
      const tf = join(scratch, `cap_${String(k).padStart(2, "0")}.txt`).replace(/'/g, "");
      const alpha = `'clip((t-${fx(a)})/${fx(d)},0,1)*clip((${fx(b)}-t)/${fx(d)},0,1)'`;
      gph.push(`[${cur}]drawtext=fontfile='${font!.replace(/'/g, "")}':textfile='${tf}':expansion=none:fontsize=${capSize}:fontcolor=0x${capCfg.color.slice(1)}:` +
        `x=(w-text_w)/2:y=${Math.round(capCfg.y * H)}-text_h/2:shadowcolor=black@${fx(CAPTION_STYLE.shadow.opacity)}:shadowx=0:shadowy=${CAPTION_STYLE.shadow.dy}:alpha=${alpha}:${en}[v${k}]`);
    }
    cur = `v${k}`;
  });
  gph.push(`[${cur}]format=yuv420p[vout]`);
  const off = edl.sync_offset_s;
  const shift = off > 0 ? `adelay=delays=${Math.round(off * 1000)}:all=1,` : off < 0 ? `atrim=start=${fx(-off)},asetpts=PTS-STARTPTS,` : "";
  gph.push(`[${songIdx}:a]aresample=48000,asetpts=PTS-STARTPTS,${shift}apad=whole_dur=${fx(edl.duration_s)},atrim=end=${fx(edl.duration_s)},asetpts=PTS-STARTPTS[aout]`);
  const graph = gph.join(";");
  await Bun.write(join(scratch, "final.graph.txt"), gph.join(";\n"));
  mkdirSync(dirname(opts.outPath), { recursive: true });
  const tmp = join(dirname(opts.outPath), `.${basename(opts.outPath, extname(opts.outPath))}.partial-${process.pid}.mp4`);
  let lastP = 0;
  const r = await run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats", ...args,
    "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-map_metadata", "-1", "-frames:v", String(plan.total),
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-profile:v", "high",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
    "-metadata", "title=In the Loop (Devagar): Director's Cut", tmp], (line) => {
    const m = /^frame=(\d+)/.exec(line);
    if (m) {
      const p = Number(m[1]) / plan.total;
      if (p - lastP >= 0.1 || p >= 1) { lastP = p; step(0.65 + 0.33 * Math.min(1, p), `final pass frame ${m[1]}/${plan.total}`); }
    }
  });
  if (r.code !== 0) { rmSync(tmp, { force: true }); throw new Error(`final pass failed (graph in ${rel(join(scratch, "final.graph.txt"))}):\n` + tail(r.stderr)); }
  renameSync(tmp, opts.outPath); // atomic: the page never plays a half-written file

  // 8. Receipt
  const probe = Bun.spawnSync(["ffprobe", "-v", "error", "-show_entries",
    "stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,pix_fmt,sample_rate,channels,duration:format=duration,size",
    "-of", "json", opts.outPath]);
  const info = JSON.parse(probe.stdout.toString()) as { streams: Record<string, string | number>[]; format: Record<string, string> };
  const vs = info.streams.find((s) => s.codec_type === "video")!, as = info.streams.find((s) => s.codec_type === "audio");
  const hasher = new Bun.CryptoHasher("md5");
  hasher.update(await Bun.file(opts.outPath).arrayBuffer());
  if (!opts.keep) rmSync(scratch, { recursive: true, force: true });
  else log(`kept the scratch files in ${rel(scratch)}`);
  const receipt: Receipt = {
    path: rel(opts.outPath), duration_s: r4(Number(info.format.duration)), fps: r4(rate(vs.r_frame_rate)),
    width: Number(vs.width), height: Number(vs.height), has_audio: !!as, bytes: statSync(opts.outPath).size,
    frames: Number(vs.nb_frames), video_codec: String(vs.codec_name), pix_fmt: String(vs.pix_fmt),
    audio: as ? { codec: String(as.codec_name), sample_rate: Number(as.sample_rate), channels: Number(as.channels), duration_s: r4(Number(as.duration)) } : null,
    md5: hasher.digest("hex"), elapsed_s: r4((performance.now() - t0) / 1000), captions: capMode,
    timing: timing.approximate ? `approximate (${timing.warning})` : timing.source,
    placeholders: edl.scenes.filter((s) => s.source.placeholder).map((s) => s.id), warnings,
    stitch_version: STITCH_VERSION, ffmpeg: ffver,
  };
  step(1, "done");
  return receipt;
}

// ---------------------------------------------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------------------------------------------

if (import.meta.main) {
  const argv = process.argv.slice(2);
  const opt = (name: string) => { const i = argv.indexOf(`--${name}`); return i >= 0 ? argv[i + 1] : undefined; };
  if (argv.includes("--help") || argv.includes("-h")) {
    console.log("bun tools/stitch.ts [--edl cut/edl.json] [--out outputs/final.mp4] [--captions auto|png|drawtext|off] [--keep]\n" +
      "       bun tools/stitch.ts --seed [--edl cut/edl.json]   (write the canonical seed EDL)");
    process.exit(0);
  }
  const edlPath = resolve(opt("edl") ?? join(ROOT, "cut", "edl.json"));
  if (argv.includes("--seed")) {
    const edl = await seedEdl();
    await Bun.write(edlPath, JSON.stringify(edl, null, 2) + "\n");
    console.log(`seeded ${rel(edlPath)} (${edl.scenes.length} scenes)`);
    process.exit(0);
  }
  try {
    const receipt = await render({ edlPath, outPath: resolve(opt("out") ?? join(ROOT, "outputs", "final.mp4")), captions: opt("captions"), keep: argv.includes("--keep") });
    for (const w of receipt.warnings) console.log(`warning: ${w}`);
    console.log(`final: ${receipt.path}  ${receipt.duration_s}s  ${receipt.fps}fps  ${receipt.width}x${receipt.height}  ` +
      `audio ${receipt.has_audio ? "yes" : "NO"}  ${(receipt.bytes / 1e6).toFixed(2)} MB  md5 ${receipt.md5}  (${receipt.elapsed_s}s)`);
    console.log("RECEIPT " + JSON.stringify(receipt));
  } catch (e) {
    console.error("ERROR " + (e as Error).message);
    process.exit(1);
  }
}
