// popstar render: cut the hero take per scene (8n+1 frames), tile the harmony scene, compile the pinned
// pose-restyle graph per scene, and write build/render-manifest.json (the contract) + build/state.json (the
// cache: a scene whose content hash matches a recorded successful render is skipped; docs/architecture.md R4).
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { CompileError, checkSlots, compile, format, loadGraph, loadSlots, WORKFLOWS_DIR } from "../comfy_compile.ts";
import { BAD, OK, paint, table } from "./ansi.ts";
import { abs, type Manifest } from "./project.ts";
import { run, shellLine } from "./proc.ts";

export const GRAPH = "pose-restyle";

// ------------------------------------------------------------------ shot list (templates/music-video/shots.schema.json)

export type Scene = {
  id: string;
  title?: string;
  t0: number;
  t1: number;
  section?: string;
  seed?: number;
  prompt?: string;
  negative?: string;
  look?: string;
  hero_frame?: { source_t?: number; look?: string; strength?: number };
  harmony?: boolean;
  clones?: { mode?: string; count?: number; backup_scale?: number; fallback?: string };
  lipsync?: string;
};
export type Shots = {
  version?: number;
  song?: string;
  timing?: string;
  sync_offset_s?: number;
  identity_lock?: string;
  negative?: string;
  takes?: { id: string }[];
  scenes?: Scene[];
};

// ------------------------------------------------------------------ frame math

const round6 = (x: number): number => Math.round(x * 1e6) / 1e6;

/** LTX wants 8n+1 frames: the smallest 8n+1 that covers [t0, t1) at `fps` (orchestration.md §5; min 9). */
export function framesFor(t0: number, t1: number, fps: number): number {
  const raw = Math.ceil(round6((t1 - t0) * fps));
  if (!(raw > 0)) throw new Error(`scene has no duration: t0=${t0} t1=${t1}`);
  return 8 * Math.max(1, Math.ceil((raw - 1) / 8)) + 1;
}
export const is8n1 = (n: number): boolean => n >= 9 && (n - 1) % 8 === 0;
/** The largest 8n+1 that fits in `n` frames (0 when fewer than 9). */
export const floor8n1 = (n: number): number => (n < 9 ? 0 : 8 * Math.floor((n - 1) / 8) + 1);

export type CutPlan = { startFrame: number; frames: number; wanted: number; clamped: boolean; note?: string };

/** Where the scene starts in the take (song t0 + sync offset) and how many frames to cut, clamped to the take. */
export function cutPlan(a: { t0: number; t1: number; fps: number; offset: number; clipFrames?: number }): CutPlan {
  const wanted = framesFor(a.t0, a.t1, a.fps);
  let startFrame = Math.round(round6((a.t0 + a.offset) * a.fps));
  let note: string | undefined;
  let clamped = false;
  if (startFrame < 0) {
    note = `scene starts ${(-startFrame / a.fps).toFixed(2)}s before the take; cutting from frame 0`;
    startFrame = 0;
    clamped = true;
  }
  let frames = wanted;
  if (a.clipFrames !== undefined) {
    const avail = a.clipFrames - startFrame;
    if (avail < wanted) {
      frames = floor8n1(avail);
      clamped = true;
      note = frames
        ? `take ends early: ${avail} frames left, cutting ${frames} (wanted ${wanted})`
        : `take too short: ${Math.max(0, avail)} frames after frame ${startFrame} (wanted ${wanted})`;
    }
  }
  return { startFrame, frames, wanted, clamped, note };
}

// ------------------------------------------------------------------ ffmpeg

/** The ×3 tile from docs/research/orchestration.md §3 (lead center at full scale, backups 0.6× raised, left one mirrored). */
export function tileFilter(w: number, h: number): string {
  const side = 2 * Math.round((w * 0.28125) / 2); // 360 at 1280
  const lead = w - 2 * side; // 560
  const scaleW = 2 * Math.round((w * 0.6) / 2); // 768
  const raise = Math.round((h * 60) / 704); // 60
  return (
    `[0:v]split=3[a][b][c];[a]crop=${lead}:${h}:(iw-${lead})/2:0[lead];` +
    `[b]scale=${scaleW}:-2,crop=${side}:ih:(iw-${side})/2:0,hflip,pad=${side}:${h}:0:${h}-ih-${raise}[l];` +
    `[c]scale=${scaleW}:-2,crop=${side}:ih:(iw-${side})/2:0,pad=${side}:${h}:0:${h}-ih-${raise}[r];` +
    `[l][lead][r]hstack=3`
  );
}

export type Fit = "pad" | "crop";
const X264 = ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart"];

export function cutCommand(a: {
  input: string;
  output: string;
  startFrame: number;
  frames: number;
  fps: number;
  width: number;
  height: number;
  fit: Fit;
}): string[] {
  const fit =
    a.fit === "crop"
      ? [`scale=${a.width}:${a.height}:force_original_aspect_ratio=increase`, `crop=${a.width}:${a.height}`]
      : [`scale=${a.width}:${a.height}:force_original_aspect_ratio=decrease`, `pad=${a.width}:${a.height}:(ow-iw)/2:(oh-ih)/2:color=black`];
  const vf = [`fps=${a.fps}`, `trim=start_frame=${a.startFrame}:end_frame=${a.startFrame + a.frames}`, "setpts=PTS-STARTPTS", ...fit, "setsar=1"].join(",");
  return ["ffmpeg", "-y", "-loglevel", "error", "-i", a.input, "-vf", vf, "-frames:v", String(a.frames), "-r", String(a.fps), "-an", ...X264, a.output];
}

export function tileCommand(input: string, output: string, w: number, h: number, fps: number, image: boolean): string[] {
  const tail = image ? ["-frames:v", "1", output] : ["-r", String(fps), "-an", ...X264, output];
  return ["ffmpeg", "-y", "-loglevel", "error", "-i", input, "-filter_complex", tileFilter(w, h), ...tail];
}

export type Probe = { frames: number; fps: number; width: number; height: number; duration: number };

export function probe(file: string): Probe | null {
  const r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=width,height,nb_read_frames,nb_frames,avg_frame_rate:format=duration", "-of", "json", file], { timeoutMs: 60_000 });
  if (!r.ok) return null;
  try {
    const j = JSON.parse(r.stdout) as { streams?: { width?: number; height?: number; nb_frames?: string; nb_read_frames?: string; avg_frame_rate?: string }[]; format?: { duration?: string } };
    const v = j.streams?.[0];
    if (!v) return null;
    const [n, d] = String(v.avg_frame_rate ?? "0/1").split("/").map(Number);
    const fps = n && d ? n / d : 0;
    const duration = Number(j.format?.duration ?? 0) || 0;
    const counted = Number(v.nb_read_frames ?? v.nb_frames);
    return { frames: counted > 0 ? counted : Math.round(duration * fps), fps, width: v.width ?? 0, height: v.height ?? 0, duration };
  } catch {
    return null;
  }
}

// ------------------------------------------------------------------ hashing + state (R4)

export const sha256File = (file: string): string => createHash("sha256").update(readFileSync(file)).digest("hex");
export const sha256Text = (text: string): string => createHash("sha256").update(text).digest("hex");

/** What a render depends on: the cut clip, the look frame (or none), the slot values, the pinned graph. */
export type HashParts = { clip: string; frame: string | null; slots: string; graph: string };
export const sceneHash = (p: HashParts): string => sha256Text(JSON.stringify([p.clip, p.frame, p.slots, p.graph]));

export type RenderRecord = { hash: string; parts: HashParts; job_id?: string; output: string; at: string };
export type State = {
  version: 1;
  renders: Record<string, RenderRecord>;
  uploads: Record<string, { name: string; path: string; at: string }>;
  cuts: Record<string, { key: string; at: string }>;
};

/** Why a scene must render again (null = fresh). Names the part that changed. */
export function staleReason(prev: RenderRecord | undefined, now: HashParts, outputExists: boolean): string | null {
  if (!prev) return "never rendered";
  if (!outputExists) return `output missing (${prev.output})`;
  if (prev.hash === sceneHash(now)) return null;
  const words: Record<keyof HashParts, string> = {
    clip: "the cut clip changed (take, offset or cut)",
    frame: "the look frame changed",
    slots: "prompt, seed or settings changed",
    graph: "the pinned graph changed",
  };
  const changed = (Object.keys(words) as (keyof HashParts)[]).filter((k) => prev.parts[k] !== now[k]);
  return changed.map((k) => words[k]).join("; ") || "inputs changed";
}

export const statePath = (root: string): string => join(root, "build", "state.json");

export function loadState(root: string): State {
  try {
    const s = JSON.parse(readFileSync(statePath(root), "utf8")) as Partial<State>;
    return { version: 1, renders: s.renders ?? {}, uploads: s.uploads ?? {}, cuts: s.cuts ?? {} };
  } catch {
    return { version: 1, renders: {}, uploads: {}, cuts: {} };
  }
}

export function saveState(root: string, s: State): void {
  mkdirSync(join(root, "build"), { recursive: true });
  writeFileSync(statePath(root), `${JSON.stringify(s, null, 2)}\n`);
}

// ------------------------------------------------------------------ takes, looks

/** hero-1 < hero-1-2 < hero-2 < hero-10: digits compare as numbers, the extension is ignored, a prefix sorts first. */
export function naturalCompare(a: string, b: string): number {
  const ax = a.replace(/\.[a-z0-9]+$/i, "").split(/(\d+)/).filter((s) => s !== "");
  const bx = b.replace(/\.[a-z0-9]+$/i, "").split(/(\d+)/).filter((s) => s !== "");
  for (let i = 0; i < Math.min(ax.length, bx.length); i++) {
    const x = ax[i]!;
    const y = bx[i]!;
    if (x === y) continue;
    const nx = /^\d+$/.test(x);
    const ny = /^\d+$/.test(y);
    if (nx && ny) return Number(x) - Number(y);
    return x < y ? -1 : 1;
  }
  return ax.length - bx.length;
}

export type Take = { id: string; file: string; offset: number; offsetSource: string };

/** The chosen take: --take <id>, else the first clips/hero-*.mp4 in natural order. Offset from its sidecar, else the shot list. */
export function findTake(root: string, m: Manifest, shots: Shots, wanted?: string): Take | null {
  const dir = abs(root, m.paths.clips);
  let names: string[] = [];
  try {
    names = readdirSync(dir).filter((f) => /^hero-.*\.mp4$/.test(f)).sort(naturalCompare);
  } catch {
    return null;
  }
  const name = wanted ? (names.includes(`${wanted}.mp4`) ? `${wanted}.mp4` : existsSync(join(dir, `${wanted}.mp4`)) ? `${wanted}.mp4` : null) : (names[0] ?? null);
  if (!name) return null;
  const id = name.replace(/\.mp4$/, "");
  const sidecar = join(dir, `${id}.json`);
  if (existsSync(sidecar)) {
    try {
      const j = JSON.parse(readFileSync(sidecar, "utf8")) as { song_offset_s?: number; sync_offset_s?: number };
      const v = j.song_offset_s ?? j.sync_offset_s;
      if (typeof v === "number" && Number.isFinite(v)) return { id, file: `${m.paths.clips}/${name}`, offset: v, offsetSource: `${m.paths.clips}/${id}.json` };
    } catch {
      /* fall through */
    }
  }
  if (typeof shots.sync_offset_s === "number") return { id, file: `${m.paths.clips}/${name}`, offset: shots.sync_offset_s, offsetSource: `${m.paths.shots} sync_offset_s` };
  return { id, file: `${m.paths.clips}/${name}`, offset: 0, offsetSource: "none (0)" };
}

export type LookFrame = { look: string; file: string };

/** The Reference Master render for the scene's look, when one exists on disk. */
export function findLookFrame(root: string, m: Manifest, scene: Scene): LookFrame | null {
  const look = scene.look ?? scene.hero_frame?.look;
  if (!look) return null;
  let entry: { renders?: { wardrobe?: string | null; expression?: string | null } } | undefined;
  try {
    const looks = JSON.parse(readFileSync(abs(root, m.paths.looks), "utf8")) as { looks?: { id: string; renders?: { wardrobe?: string | null; expression?: string | null } }[] };
    entry = looks.looks?.find((l) => l.id === look);
  } catch {
    /* no looks file */
  }
  const candidates = [entry?.renders?.expression, entry?.renders?.wardrobe, `reference-master/renders/${look}/expression.png`, `reference-master/renders/${look}/wardrobe.png`];
  for (const c of candidates) if (c && existsSync(abs(root, c))) return { look, file: c };
  return null;
}

// ------------------------------------------------------------------ the manifest (contract)

export type Upload = { slot: "source_video" | "first_frame"; file: string; sha256: string | null; name: string | null };
export type SceneEntry = {
  id: string;
  title: string;
  section: string;
  t0: number;
  t1: number;
  seed: number;
  start_frame: number;
  frames: number;
  wanted_frames: number;
  harmony: boolean;
  tiled: boolean;
  clip: string;
  clip_sha256: string | null;
  first_frame: { look: string; file: string; sha256: string | null; strength: number } | null;
  inputs_file: string;
  graph_file: string;
  uploads: Upload[];
  hash: string | null;
  parts: HashParts | null;
  state: "fresh" | "stale" | "planned" | "error";
  stale_because: string | null;
  output: string;
  warnings: string[];
};
export type RenderManifest = {
  version: 1;
  project: string;
  generated_at: string;
  dry_run: boolean;
  graph: string;
  graph_file: string;
  graph_sha256: string;
  fps: number;
  size: [number, number];
  fit: Fit;
  take: { id: string; file: string; sha256: string | null; sync_offset_s: number; offset_source: string } | null;
  scenes: SceneEntry[];
  pending_uploads: { file: string; slot: string; scenes: string[] }[];
};

export type RenderOptions = {
  scenes?: string[];
  dryRun?: boolean;
  submit?: boolean;
  take?: string;
  fit?: Fit;
  force?: boolean;
  uploaded?: string[]; // file=name
  mark?: { id: string; job?: string };
  log?: (s: string) => void;
  clipboard?: (text: string) => boolean;
};

const readJson = (file: string): unknown => {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return null;
  }
};

const pbcopy = (text: string): boolean => {
  try {
    return Bun.spawnSync(["pbcopy"], { stdin: Buffer.from(text) }).exitCode === 0;
  } catch {
    return false;
  }
};

/** Slot values for the pinned pose-restyle graph. Upload names fall back to the local basename until recorded. */
export function slotValues(a: {
  m: Manifest;
  shots: Shots;
  scene: Scene;
  clipName: string;
  frame: { name: string; strength: number } | null;
}): Record<string, unknown> {
  const { m, shots, scene } = a;
  const [width, height] = m.size;
  const values: Record<string, unknown> = {
    source_video: a.clipName,
    trim_start: 0,
    trim_duration: 0, // mandatory: the pinned 5 s would cap the scene (workflows/README.md)
    prompt: scene.prompt ?? "",
    negative: scene.negative ?? shots.negative ?? m.performer.negative,
    seed: scene.seed ?? 42,
    fps: m.fps,
    width,
    height,
    output_prefix: `video/${m.slug}/${scene.id}`,
  };
  if (a.frame) {
    values.first_frame = a.frame.name;
    values.use_first_frame = true;
    values.first_frame_strength = a.frame.strength;
  }
  return values;
}

/** The part of the slot values that identifies a render (upload names are not identity: the file hashes are). */
export const slotsFingerprint = (values: Record<string, unknown>): string => {
  const { source_video: _a, first_frame: _b, ...rest } = values;
  return sha256Text(JSON.stringify(rest, Object.keys(rest).sort()));
};

export function submitContract(m: Manifest, man: RenderManifest, stale: SceneEntry[]): string {
  const L: string[] = [];
  L.push(`POPSTAR RENDER · ${m.slug} · ${stale.length} scene(s) to render via the comfy-cloud MCP · contract: build/render-manifest.json`);
  L.push("Comfy Cloud documents no plain HTTPS API for upload/submit/poll (MCP guides setup-auth + output-downloads: the");
  L.push("hosted session holds an OAuth bearer or X-API-Key; upload_file / get_output emit the commands), so Claude runs this:");
  L.push("");
  L.push("1. UPLOAD each file with upload_file (run the emitted curl; keep the returned content-hash name):");
  const uploads = new Map<string, string[]>();
  for (const s of stale) for (const u of s.uploads) if (!u.name) uploads.set(`${u.file} → ${u.slot}`, [...(uploads.get(`${u.file} → ${u.slot}`) ?? []), s.id]);
  if (!uploads.size) L.push("   (every upload is already recorded)");
  for (const [k, ids] of uploads) L.push(`   ${k} of ${ids.join(", ")}`);
  L.push("2. RECORD the names (this recompiles the graphs with them):");
  const recs = [...new Set(stale.flatMap((s) => s.uploads.filter((u) => !u.name).map((u) => `--uploaded ${u.file}=<name>`)))];
  L.push(`   bin/popstar render ${recs.length ? recs.join(" ") : "(nothing to record)"}`);
  L.push("3. PRE-FLIGHT for free: submit_workflow({ workflow: <graph JSON as an object>, dry_run: true }) must say validated:");
  for (const s of stale) L.push(`   ${s.graph_file}   (${s.id}: ${s.frames} frames = 8·${(s.frames - 1) / 8}+1, seed ${s.seed}${s.first_frame ? `, first frame ${s.first_frame.look}` : ""}${s.tiled ? ", tiled ×3" : ""})`);
  L.push("4. SUBMIT (spends credits): submit_batch with every graph above (or submit_workflow one at a time), then wait_for_batch / wait_for_job.");
  L.push("5. DOWNLOAD each output with get_output (run the emitted curl) to its path, then record the receipt:");
  for (const s of stale) L.push(`   ${s.output}   → bin/popstar render --mark ${s.id} --job <prompt_id>`);
  L.push(`Receipts per scene: ${m.fps} fps · 8n+1 frames · identity + skin hold · the motion follows the take. Then: bin/popstar status`);
  return L.join("\n");
}

// ------------------------------------------------------------------ the command

export async function render(root: string, m: Manifest, o: RenderOptions = {}): Promise<number> {
  const log = o.log ?? console.log;
  const p = paint;
  const fit: Fit = o.fit ?? "pad";
  const [width, height] = m.size;
  const state = loadState(root);
  let stateDirty = false;

  // --uploaded file=name: remember Comfy Cloud upload names by file content.
  for (const spec of o.uploaded ?? []) {
    const eq = spec.indexOf("=");
    if (eq <= 0) {
      log(`${p.red(BAD)} --uploaded expects <file>=<name>, got "${spec}"`);
      return 2;
    }
    const file = spec.slice(0, eq);
    const name = spec.slice(eq + 1);
    const full = abs(root, file);
    if (!existsSync(full)) {
      log(`${p.red(BAD)} --uploaded: no such file ${file}`);
      return 2;
    }
    state.uploads[sha256File(full)] = { name, path: file, at: new Date().toISOString() };
    stateDirty = true;
    log(`${p.green(OK)} recorded upload ${file} → ${name}`);
  }

  // --mark <id> --job <prompt_id>: a successful render landed in outputs/scenes/<id>.mp4.
  if (o.mark) {
    const man = readJson(join(root, "build", "render-manifest.json")) as RenderManifest | null;
    const entry = man?.scenes.find((s) => s.id === o.mark!.id);
    if (!entry || !entry.hash || !entry.parts) {
      log(`${p.red(BAD)} --mark ${o.mark.id}: no such scene in build/render-manifest.json (run popstar render first)`);
      return 2;
    }
    const output = abs(root, entry.output);
    if (!existsSync(output)) {
      log(`${p.red(BAD)} --mark ${o.mark.id}: ${entry.output} is not there yet (download it with get_output first)`);
      return 2;
    }
    const pr = probe(output);
    const record: RenderRecord = { hash: entry.hash, parts: entry.parts, job_id: o.mark.job, output: entry.output, at: new Date().toISOString() };
    state.renders[o.mark.id] = record;
    saveState(root, state);
    const receipt = pr ? `${pr.frames} frames @ ${pr.fps} fps · ${pr.width}×${pr.height}${is8n1(pr.frames) ? "" : p.amber(" · not 8n+1")}` : "(ffprobe failed)";
    log(`${p.green(OK)} ${o.mark.id} recorded as rendered${o.mark.job ? ` (job ${o.mark.job})` : ""}: ${entry.output} · ${receipt}`);
    return 0;
  }

  // The shot list.
  const shotsFile = abs(root, m.paths.shots);
  const shots = readJson(shotsFile) as Shots | null;
  const all = shots?.scenes ?? [];
  if (!shots || !all.length) {
    if (stateDirty) saveState(root, state);
    log(p.amber(`\n  RENDER · ${m.slug}\n`));
    log(`  ${p.dim("nothing to render yet. The render stage needs:")}`);
    log(`  ${shots ? p.amber("▶") : p.red(BAD)} ${m.paths.shots} with scenes[] ${shots ? "(exists, but scenes[] is empty)" : "(missing)"} — ask Claude to direct it (skill rnb-director)`);
    log(`  ${p.dim("·")} ${m.paths.clips}/hero-*.mp4 — record hero-1 with the coach: popstar open recorder`);
    log(`  ${p.dim("·")} Reference Master renders per look (optional first frames): popstar open looks`);
    log(`  ${p.dim("·")} the pinned graph workflows/${GRAPH}.api.json ${existsSync(join(WORKFLOWS_DIR, `${GRAPH}.api.json`)) ? p.green(OK) : p.red("missing")}`);
    log(`\n  ${p.dim("Then: popstar render --dry-run · popstar render · popstar render --submit")}\n`);
    return 0;
  }
  const wantedIds = o.scenes?.length ? new Set(o.scenes) : null;
  if (wantedIds) {
    const unknown = [...wantedIds].filter((id) => !all.some((s) => s.id === id));
    if (unknown.length) {
      log(`${p.red(BAD)} unknown scene(s): ${unknown.join(", ")}. Known: ${all.map((s) => s.id).join(", ")}`);
      return 2;
    }
  }
  const scenes = all.filter((s) => !wantedIds || wantedIds.has(s.id));

  // The take.
  const take = findTake(root, m, shots, o.take);
  const takeFull = take ? abs(root, take.file) : null;
  const takeProbe = takeFull ? probe(takeFull) : null;
  if (take && !takeProbe) {
    log(`${p.red(BAD)} ffprobe cannot read ${take.file}`);
    return 1;
  }
  if (!take && !o.dryRun) {
    log(p.amber(`\n  RENDER · ${m.slug}\n`));
    log(`  ${p.red(BAD)} no hero take in ${m.paths.clips}/ (hero-*.mp4). Record hero-1 with the coach: popstar open recorder`);
    log(`  ${p.dim("popstar render --dry-run shows the plan without a take.")}\n`);
    return 0;
  }

  // The pinned graph.
  const graphs = loadSlots();
  const entry = graphs[GRAPH];
  if (!entry) {
    log(`${p.red(BAD)} workflows/slots.json has no "${GRAPH}" graph`);
    return 1;
  }
  const graph = loadGraph(entry);
  const graphFile = join(WORKFLOWS_DIR, entry.file);
  const graphSha = sha256File(graphFile);
  const { errors: slotErrors, warnings: slotWarnings } = checkSlots(entry, graph);
  if (slotErrors.length) {
    log(`${p.red(BAD)} workflows/slots.json does not match ${entry.file}:\n  ${slotErrors.join("\n  ")}`);
    return 1;
  }
  for (const w of slotWarnings) log(`${p.amber("!")} comfy_compile: ${w}`);

  const buildDir = join(root, "build", "scenes");
  if (!o.dryRun) mkdirSync(buildDir, { recursive: true });
  const takeSha = !o.dryRun && takeFull ? sha256File(takeFull) : null;
  const uploadName = (file: string, sha: string | null): string | null => (sha && state.uploads[sha]?.name) || null;

  log(p.amber(`\n  RENDER · ${m.slug} · ${scenes.length}/${all.length} scene(s)${o.dryRun ? " · dry run" : ""}\n`));
  log(
    `  ${p.dim("take:")} ${take ? `${take.file} (${takeProbe!.frames} frames @ ${takeProbe!.fps} fps, ${takeProbe!.width}×${takeProbe!.height}) · sync offset ${take.offset >= 0 ? "+" : ""}${take.offset}s from ${take.offsetSource}` : p.dim("none yet (offset 0, no clamping)")}`,
  );
  log(`  ${p.dim("graph:")} ${GRAPH} (workflows/${entry.file}, ${Object.keys(graph).length} nodes) · ${width}×${height} @ ${m.fps} fps · fit ${fit}\n`);

  const entries: SceneEntry[] = [];
  let failed = 0;
  for (const scene of scenes) {
    const warnings: string[] = [];
    const harmony = scene.harmony === true;
    const tiled = harmony && (scene.clones?.mode ?? "tile_source") === "tile_source";
    const plan = cutPlan({ t0: scene.t0, t1: scene.t1, fps: m.fps, offset: take?.offset ?? 0, clipFrames: takeProbe?.frames });
    if (plan.note) warnings.push(plan.note);
    if (!scene.prompt) warnings.push("scene has no prompt");
    else if (m.performer.identity_lock && !scene.prompt.includes(m.performer.identity_lock)) warnings.push("prompt lacks the identity lock verbatim (skin-drift risk)");
    const look = findLookFrame(root, m, scene);
    const clipRel = `build/scenes/${scene.id}.mp4`;
    const frameRel = look ? (tiled ? `build/scenes/${scene.id}.frame.png` : look.file) : null;
    const strength = tiled ? 1 : (scene.hero_frame?.strength ?? 0.8);
    const e: SceneEntry = {
      id: scene.id,
      title: scene.title ?? scene.id,
      section: scene.section ?? "",
      t0: scene.t0,
      t1: scene.t1,
      seed: scene.seed ?? 42,
      start_frame: plan.startFrame,
      frames: plan.frames,
      wanted_frames: plan.wanted,
      harmony,
      tiled,
      clip: clipRel,
      clip_sha256: null,
      first_frame: look && frameRel ? { look: look.look, file: frameRel, sha256: null, strength } : null,
      inputs_file: `build/scenes/${scene.id}.inputs.json`,
      graph_file: `build/scenes/${scene.id}.json`,
      uploads: [],
      hash: null,
      parts: null,
      state: "planned",
      stale_because: null,
      output: `${m.paths.outputs}/scenes/${scene.id}.mp4`,
      warnings,
    };
    entries.push(e);
    if (o.dryRun) {
      // Validate the slot values against the pinned graph without writing anything.
      try {
        compile(entry, graph, slotValues({ m, shots, scene, clipName: basename(clipRel), frame: e.first_frame ? { name: basename(e.first_frame.file), strength } : null }));
      } catch (err) {
        if (!(err instanceof CompileError)) throw err;
        e.state = "error";
        e.warnings.push(`compile: ${err.message}`);
        failed++;
      }
      continue;
    }
    if (plan.frames === 0) {
      e.state = "error";
      failed++;
      continue;
    }
    // 1. cut (cached by take hash + cut parameters)
    const cutFull = join(buildDir, `${scene.id}.mp4`);
    const rawFull = tiled ? join(buildDir, `${scene.id}.cut.mp4`) : cutFull;
    const cutKey = JSON.stringify([takeSha, plan.startFrame, plan.frames, m.fps, width, height, fit, tiled]);
    const cached = state.cuts[scene.id]?.key === cutKey && existsSync(cutFull);
    if (!cached) {
      const c = run(cutCommand({ input: takeFull!, output: rawFull, startFrame: plan.startFrame, frames: plan.frames, fps: m.fps, width, height, fit }), { timeoutMs: 300_000 });
      if (!c.ok) {
        e.state = "error";
        e.warnings.push(`ffmpeg cut failed: ${c.stderr.trim() || c.error || `exit ${c.code}`}`);
        failed++;
        continue;
      }
      if (tiled) {
        const t = run(tileCommand(rawFull, cutFull, width, height, m.fps, false), { timeoutMs: 300_000 });
        if (!t.ok) {
          e.state = "error";
          e.warnings.push(`ffmpeg tile failed: ${t.stderr.trim() || t.error || `exit ${t.code}`}`);
          failed++;
          continue;
        }
      }
      const got = probe(cutFull);
      if (!got || got.frames !== plan.frames) {
        e.state = "error";
        e.warnings.push(`cut has ${got?.frames ?? "?"} frames, wanted ${plan.frames}`);
        failed++;
        continue;
      }
      state.cuts[scene.id] = { key: cutKey, at: new Date().toISOString() };
      stateDirty = true;
    }
    e.clip_sha256 = sha256File(cutFull);
    // 2. the look frame (tiled too for a harmony scene, so the three faces start as the same person)
    if (look && e.first_frame) {
      const src = abs(root, look.file);
      if (tiled) {
        const t = run(tileCommand(src, abs(root, e.first_frame.file), width, height, m.fps, true), { timeoutMs: 120_000 });
        if (!t.ok) {
          e.state = "error";
          e.warnings.push(`ffmpeg frame tile failed: ${t.stderr.trim() || t.error || `exit ${t.code}`}`);
          failed++;
          continue;
        }
      }
      e.first_frame.sha256 = sha256File(abs(root, e.first_frame.file));
    }
    // 3. compile the graph with the upload names we know (else the local basenames, listed as pending uploads)
    const clipName = uploadName(clipRel, e.clip_sha256) ?? basename(clipRel);
    const frameName = e.first_frame ? (uploadName(e.first_frame.file, e.first_frame.sha256) ?? basename(e.first_frame.file)) : null;
    e.uploads.push({ slot: "source_video", file: clipRel, sha256: e.clip_sha256, name: uploadName(clipRel, e.clip_sha256) });
    if (e.first_frame) e.uploads.push({ slot: "first_frame", file: e.first_frame.file, sha256: e.first_frame.sha256, name: uploadName(e.first_frame.file, e.first_frame.sha256) });
    const values = slotValues({ m, shots, scene, clipName, frame: e.first_frame && frameName ? { name: frameName, strength } : null });
    try {
      const compiled = compile(entry, graph, values);
      writeFileSync(join(root, e.inputs_file), `${JSON.stringify(values, null, 2)}\n`);
      writeFileSync(join(root, e.graph_file), `${format(compiled)}\n`);
    } catch (err) {
      if (!(err instanceof CompileError)) throw err;
      e.state = "error";
      e.warnings.push(`compile: ${err.message}`);
      failed++;
      continue;
    }
    // 4. hash + cache verdict
    e.parts = { clip: e.clip_sha256, frame: e.first_frame?.sha256 ?? null, slots: slotsFingerprint(values), graph: graphSha };
    e.hash = sceneHash(e.parts);
    const reason = o.force ? "forced (--force)" : staleReason(state.renders[scene.id], e.parts, existsSync(abs(root, e.output)));
    e.state = reason ? "stale" : "fresh";
    e.stale_because = reason;
  }

  // The manifest.
  const pending = new Map<string, { file: string; slot: string; scenes: string[] }>();
  for (const e of entries) for (const u of e.uploads) if (!u.name) {
    const k = `${u.slot}:${u.file}`;
    const cur = pending.get(k) ?? { file: u.file, slot: u.slot, scenes: [] };
    cur.scenes.push(e.id);
    pending.set(k, cur);
  }
  const manifest: RenderManifest = {
    version: 1,
    project: m.slug,
    generated_at: new Date().toISOString(),
    dry_run: !!o.dryRun,
    graph: GRAPH,
    graph_file: `workflows/${entry.file}`,
    graph_sha256: graphSha,
    fps: m.fps,
    size: [width, height],
    fit,
    take: take ? { id: take.id, file: take.file, sha256: takeSha, sync_offset_s: take.offset, offset_source: take.offsetSource } : null,
    scenes: entries,
    pending_uploads: [...pending.values()],
  };
  if (!o.dryRun) {
    mkdirSync(join(root, "build"), { recursive: true });
    writeFileSync(join(root, "build", "render-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
    if (stateDirty) saveState(root, state);
  }

  // The table.
  const rows: string[][] = [["", "scene", "song time", "take frame", "frames", "x3", "first frame", "hash", "state"]];
  for (const e of entries) {
    const mark = e.state === "error" ? p.red(BAD) : e.state === "fresh" ? p.green(OK) : e.state === "stale" ? p.amber("▶") : p.dim("·");
    rows.push([
      mark,
      e.id,
      `${e.t0.toFixed(2)}→${e.t1.toFixed(2)}`,
      String(e.start_frame),
      `${e.frames}${e.frames !== e.wanted_frames ? p.amber(`/${e.wanted_frames}`) : ""}`,
      e.tiled ? "×3" : "",
      e.first_frame ? `${e.first_frame.look} @${e.first_frame.strength}` : p.dim("none"),
      e.hash ? e.hash.slice(0, 8) : p.dim("—"),
      e.state === "fresh" ? p.green("fresh (skip)") : e.state === "stale" ? `stale: ${e.stale_because}` : e.state === "error" ? p.red("error") : p.dim("planned"),
    ]);
  }
  for (const line of table(rows)) log(line);
  for (const e of entries) for (const w of e.warnings) log(`      ${e.state === "error" ? p.red(BAD) : p.amber("!")} ${e.id}: ${w}`);
  log("");
  if (o.dryRun) {
    const first = entries[0];
    if (take && first) {
      log(`  ${p.dim("would run, per scene (shown for " + first.id + "):")}`);
      log(`  ${p.dim(shellLine(cutCommand({ input: take.file, output: first.clip, startFrame: first.start_frame, frames: first.frames, fps: m.fps, width, height, fit })))}`);
      if (first.tiled) log(`  ${p.dim(shellLine(tileCommand(`build/scenes/${first.id}.cut.mp4`, first.clip, width, height, m.fps, false)))}`);
      log(`  ${p.dim(`bun tools/comfy_compile.ts ${GRAPH} --inputs ${first.inputs_file} --out ${first.graph_file}`)}`);
    }
    log(`  ${p.dim("dry run: nothing written. Next: popstar render (cuts + compiles), then popstar render --submit.")}\n`);
    return failed ? 1 : 0;
  }
  const stale = entries.filter((e) => e.state === "stale");
  const fresh = entries.filter((e) => e.state === "fresh");
  log(`  ${p.dim("wrote")} build/render-manifest.json · ${entries.length - failed} graph(s) in build/scenes/ · ${fresh.length} fresh, ${stale.length} to render${failed ? p.red(`, ${failed} failed`) : ""}`);
  if (manifest.pending_uploads.length) log(`  ${p.dim("pending uploads:")} ${manifest.pending_uploads.map((u) => u.file).join(", ")}`);
  if (!o.submit) {
    log(`  ${p.dim("next: popstar render --submit (prints the MCP contract and copies it to the clipboard)")}\n`);
    return failed ? 1 : 0;
  }
  if (!stale.length) {
    log(`\n  ${p.green(OK)} every scene is fresh; nothing to submit. (--force re-renders them.)\n`);
    return failed ? 1 : 0;
  }
  const text = submitContract(m, manifest, stale);
  log("");
  for (const line of text.split("\n")) log(`  ${line}`);
  const copied = (o.clipboard ?? pbcopy)(text);
  log(`\n  ${copied ? p.green(OK) + " copied to the clipboard" : p.amber("!") + " pbcopy unavailable"}: paste it to Claude, who runs it through the comfy-cloud MCP.\n`);
  return failed ? 1 : 0;
}
