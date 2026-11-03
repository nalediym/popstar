// Director's Cut: plays the whole music video in sync with the song, lets you change the edit live, and renders
// outputs/final.mp4 with tools/stitch.ts.
// Run: bun cut/server.ts   (PORT env optional; defaults to a free port. 56101 belongs to the recorder.)
// Data: cut/edl.json (the edit), songs/<song>.timing.json and shots/<song>.json are read fresh on every request,
// because other tools write them while this runs.
import { copyFileSync, existsSync, mkdirSync, realpathSync, renameSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import {
  ALLOWED_MEDIA_DIRS, CAPTION_STYLE, CLONE_LAYOUT, DEFAULT_GLOBALS, GRADE_PRESETS, ROOT, STITCH_VERSION,
  fallbackTiming, probeMedia, rel, safeMediaPath, seedEdl, shotsPathFor, timingPathFor, validateEdl,
  type Edl, type MediaInfo,
} from "../tools/stitch.ts";

const edlPath = resolve(process.env.EDL_PATH || join(import.meta.dir, "edl.json"));
const backupPath = join(import.meta.dir, "edl.backup.json");
const finalPath = join(ROOT, "outputs", "final.mp4");
const thumbsDir = join(ROOT, "outputs", "cut", "thumbs");
const DEFAULT_SONG = "songs/in-the-loop-devagar.mp3";

const noStore = { "cache-control": "no-store" };
const jsonError = (status: number, error: string, extra: Record<string, unknown> = {}) =>
  Response.json({ error, ...extra }, { status, headers: noStore });
const json = (body: unknown, status = 200) => Response.json(body, { status, headers: noStore });

// ---- the EDL on disk ------------------------------------------------------------------------------------------

async function readEdlFile(): Promise<{ edl?: Edl; text?: string; error?: string }> {
  const f = Bun.file(edlPath);
  if (!(await f.exists())) return {};
  const text = await f.text();
  try {
    const v = validateEdl(JSON.parse(text));
    return v.ok ? { edl: v.edl, text } : { error: `cut/edl.json is invalid: ${v.errors.join("; ")}`, text };
  } catch (e) {
    return { error: `cut/edl.json is not valid JSON: ${(e as Error).message}`, text };
  }
}
async function currentSong(): Promise<string> {
  return (await readEdlFile()).edl?.song ?? DEFAULT_SONG;
}
let writeSeq = 0;
/** Atomic write: temp file in the same dir, then rename. The previous version becomes cut/edl.backup.json. */
async function writeEdl(edl: Edl) {
  if (existsSync(edlPath)) copyFileSync(edlPath, backupPath);
  const tmp = `${edlPath}.tmp-${process.pid}-${++writeSeq}`;
  await Bun.write(tmp, JSON.stringify(edl, null, 2) + "\n");
  renameSync(tmp, edlPath);
}

// ---- assets ---------------------------------------------------------------------------------------------------

type Asset = MediaInfo & {
  path: string; group: "scenes" | "previs" | "restyles" | "lipsync" | "looks" | "clips"; label: string;
  bytes: number; mtime: number; song_t0_s: number | null; placeholder: boolean;
};
/** Song time at each known asset's t=0. The lipsync renders used the song's 17-25 s window. */
const KNOWN_SONG_T0: Record<string, number> = {
  "outputs/chorus_lipsync_ia2v.mp4": 17, "outputs/chorus_lipsync_infinitetalk.mp4": 17, "outputs/chorus_lipsync_compare.mp4": 17,
  "outputs/previs/animatic.mp4": 0,
};
const probeCache = new Map<string, MediaInfo | null>();
const SCANS: { dir: string; glob: string; group: (p: string) => Asset["group"] }[] = [
  { dir: "outputs", glob: "**/*.{mp4,mov,m4v,webm}", group: (p) => p.startsWith("outputs/scenes/") ? "scenes" : p.startsWith("outputs/previs/") ? "previs" : /lipsync/i.test(p) ? "lipsync" : "restyles" },
  { dir: "clips", glob: "*.{mp4,mov,m4v,webm}", group: () => "clips" },
  { dir: "reference-master/renders", glob: "**/*.{png,jpg,jpeg,webp}", group: () => "looks" },
  { dir: "reference-master/tests", glob: "*.{png,jpg,jpeg,webp}", group: () => "looks" },
];
const GROUP_ORDER = ["scenes", "previs", "lipsync", "restyles", "looks", "clips"];

async function listAssets(): Promise<{ assets: Asset[]; errors: string[] }> {
  const errors: string[] = [];
  const found: { path: string; group: Asset["group"] }[] = [];
  for (const s of SCANS) {
    const base = join(ROOT, s.dir);
    if (!existsSync(base)) continue;
    try {
      for (const f of new Bun.Glob(s.glob).scanSync({ cwd: base, onlyFiles: true })) {
        const path = `${s.dir}/${f}`;
        if (f.split("/").some((part) => part.startsWith("."))) continue; // dotfiles, partial renders
        if (/\.src\.|\.partial\./.test(f)) continue; // the recorder's and stitch's temp files
        if (!safeMediaPath(path)) continue; // also drops outputs/final.mp4 and outputs/cut/**
        found.push({ path, group: s.group(path) });
      }
    } catch (e) {
      errors.push(`${s.dir}: ${(e as Error).message}`);
    }
  }
  const shots = await readJsonSoft(join(ROOT, shotsPathFor(await currentSong())));
  const edl = (await readEdlFile()).edl;
  const sceneT0 = (id: string) => {
    const fromShots = Array.isArray(shots?.scenes) ? shots.scenes.find((s: { id?: string }) => s?.id === id)?.t0 : undefined;
    return typeof fromShots === "number" ? fromShots : edl?.scenes.find((s) => s.id === id)?.t0 ?? null;
  };
  const assets = await Promise.all(found.map(async ({ path, group }) => {
    const abs = join(ROOT, path);
    let st;
    try { st = statSync(abs); } catch { return null; }
    const key = `${path}|${st.mtimeMs}|${st.size}`;
    if (!probeCache.has(key)) probeCache.set(key, await probeMedia(abs));
    const info = probeCache.get(key);
    if (!info) { errors.push(`${path}: ffprobe could not read it`); return null; }
    let songT0: number | null = KNOWN_SONG_T0[path] ?? null;
    if (group === "scenes") songT0 = sceneT0(path.slice("outputs/scenes/".length).replace(/\.[^.]+$/, ""));
    // The recorder's sidecar: { song_offset_s } = where song t=0 lands in the clip.
    const side = await readJsonSoft(abs.replace(/\.[^.]+$/, ".json"));
    if (side && typeof side.song_offset_s === "number") songT0 = -side.song_offset_s;
    return {
      ...info, path, group, label: path.split("/").pop()!, bytes: st.size, mtime: Math.round(st.mtimeMs),
      song_t0_s: songT0, placeholder: group !== "scenes",
    } satisfies Asset;
  }));
  const list = assets.filter((a): a is Asset => !!a)
    .sort((a, b) => GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group) || a.path.localeCompare(b.path));
  return { assets: list, errors };
}

async function readJsonSoft(abs: string) {
  try {
    const f = Bun.file(abs);
    return (await f.exists()) ? JSON.parse(await f.text()) : null;
  } catch {
    return null;
  }
}

/** A requested media path -> an absolute file inside the allowed dirs, or null. Symlinks may not escape. */
function resolveMedia(raw: string | null): string | null {
  const p = safeMediaPath(raw);
  if (!p) return null;
  const abs = join(ROOT, p);
  if (!existsSync(abs)) return null;
  let real: string;
  try { real = realpathSync(abs); } catch { return null; }
  const rootReal = realpathSync(ROOT);
  const ok = ALLOWED_MEDIA_DIRS.some((d) => {
    const dirAbs = join(ROOT, d);
    return existsSync(dirAbs) && real.startsWith(realpathSync(dirAbs) + "/");
  });
  return ok && real.startsWith(rootReal + "/") ? real : null;
}

async function serveFile(req: Request, abs: string, headers: Record<string, string> = {}) {
  const file = Bun.file(abs);
  if (req.method === "HEAD") {
    return new Response(null, { headers: { "content-type": file.type, "content-length": String(file.size), "accept-ranges": "bytes", ...headers } });
  }
  return new Response(file, { headers }); // Bun answers Range requests itself (206), so <video> can seek
}

async function thumb(abs: string, path: string): Promise<string | null> {
  mkdirSync(thumbsDir, { recursive: true });
  const st = statSync(abs);
  const hash = new Bun.CryptoHasher("sha1").update(`${path}|${st.mtimeMs}|${st.size}`).digest("hex").slice(0, 16);
  const out = join(thumbsDir, `${hash}.jpg`);
  if (existsSync(out)) return out;
  const isImage = /\.(png|jpe?g|webp)$/i.test(path);
  let seek: string[] = [];
  if (!isImage) {
    const info = await probeMedia(abs);
    seek = ["-ss", String(Math.min(1, (info?.duration_s ?? 1) * 0.3))];
  }
  const p = Bun.spawn(["ffmpeg", "-y", "-v", "error", "-nostdin", ...seek, "-i", abs, "-frames:v", "1",
    "-vf", "scale=320:-2", "-q:v", "5", out], { stdout: "ignore", stderr: "pipe" });
  return (await p.exited) === 0 && existsSync(out) ? out : null;
}

// ---- render ---------------------------------------------------------------------------------------------------

let rendering: { started: number } | null = null;

function renderStream(): Response {
  rendering = { started: Date.now() };
  const proc = Bun.spawn(["bun", join(ROOT, "tools", "stitch.ts"), "--edl", edlPath, "--out", finalPath],
    { cwd: ROOT, stdout: "pipe", stderr: "pipe", stdin: "ignore" });
  let gone = false;
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      const enc = new TextEncoder();
      const send = (o: Record<string, unknown>) => {
        if (gone) return;
        try { controller.enqueue(enc.encode(JSON.stringify(o) + "\n")); } catch { gone = true; }
      };
      let receipt: unknown = null;
      const pump = async (stream: ReadableStream<Uint8Array>, kind: "out" | "err") => {
        const dec = new TextDecoder();
        let buf = "";
        const line = (l: string) => {
          if (!l.trim()) return;
          const prog = /^PROGRESS ([\d.]+) (.*)$/.exec(l);
          if (prog) return send({ type: "progress", p: Number(prog[1]), label: prog[2] });
          if (l.startsWith("RECEIPT ")) {
            try { receipt = JSON.parse(l.slice(8)); } catch { /* keep the raw line */ }
            return send({ type: "receipt", receipt });
          }
          send({ type: "log", stream: kind, line: l });
        };
        for await (const chunk of stream as unknown as AsyncIterable<Uint8Array>) {
          buf += dec.decode(chunk, { stream: true });
          let k: number;
          while ((k = buf.indexOf("\n")) >= 0) { line(buf.slice(0, k)); buf = buf.slice(k + 1); }
        }
        line(buf);
      };
      send({ type: "start", edl: rel(edlPath), out: rel(finalPath) });
      await Promise.all([pump(proc.stdout, "out"), pump(proc.stderr, "err")]);
      const code = await proc.exited;
      rendering = null;
      send({ type: "done", code, receipt });
      if (!gone) try { controller.close(); } catch { /* client left */ }
    },
    cancel() { gone = true; }, // the render keeps going if the page closes
  });
  return new Response(body, { headers: { "content-type": "application/x-ndjson; charset=utf-8", ...noStore } });
}

// ---- server ---------------------------------------------------------------------------------------------------

let port = Number(process.env.PORT ?? 0);
if (port === 56101) {
  console.warn("PORT 56101 belongs to the coach recorder; picking a free port instead.");
  port = 0;
}

const server = Bun.serve({
  port,
  idleTimeout: 255, // seconds; renders stream progress, but give slow machines room
  async fetch(req) {
    const url = new URL(req.url);
    const path = url.pathname;
    const get = req.method === "GET" || req.method === "HEAD";

    if (get && path === "/") {
      return new Response(Bun.file(join(import.meta.dir, "index.html")), { headers: { "content-type": "text/html; charset=utf-8", ...noStore } });
    }

    if (get && path === "/song") {
      const song = await currentSong();
      const abs = join(ROOT, song);
      if (!existsSync(abs)) return jsonError(404, `song not found: ${song}`);
      return serveFile(req, abs, noStore);
    }

    if (get && path === "/timing") {
      const song = await currentSong();
      const tp = timingPathFor(song);
      const f = Bun.file(join(ROOT, tp));
      const md = await Bun.file(join(ROOT, song.replace(/\.[^.]+$/, ".md"))).text().catch(() => null);
      const edl = (await readEdlFile()).edl;
      const fb = (why: string) => fallbackTiming(song, edl?.duration_s ?? 32, md, why);
      if (!(await f.exists())) return jsonError(404, `timing file not found: ${tp}`, { fallback: fb(`${tp} is missing`) });
      const text = await f.text();
      try { JSON.parse(text); } catch (e) {
        return jsonError(500, `timing file is not valid JSON (${tp}): ${(e as Error).message}`, { fallback: fb(`${tp} is not valid JSON yet`) });
      }
      return new Response(text, { headers: { "content-type": "application/json; charset=utf-8", ...noStore } });
    }

    if (get && path === "/shots") {
      const sp = shotsPathFor(await currentSong());
      const f = Bun.file(join(ROOT, sp));
      if (!(await f.exists())) return jsonError(404, `shot list not found: ${sp}`);
      const text = await f.text();
      try { JSON.parse(text); } catch (e) { return jsonError(500, `shot list is not valid JSON (${sp}): ${(e as Error).message}`); }
      return new Response(text, { headers: { "content-type": "application/json; charset=utf-8", ...noStore } });
    }

    if (get && path === "/edl") {
      const cur = await readEdlFile();
      if (cur.error) return jsonError(500, cur.error, { backup: existsSync(backupPath) ? rel(backupPath) : null });
      if (cur.edl) return json(cur.edl);
      const seed = await seedEdl(); // first run: write the canonical seed
      await writeEdl(seed);
      return json(seed);
    }

    if (get && path === "/edl/seed") return json(await seedEdl()); // a fresh seed, not written ("start over")

    if (req.method === "PUT" && path === "/edl") {
      let body: unknown;
      try { body = await req.json(); } catch (e) { return jsonError(400, `body is not JSON: ${(e as Error).message}`); }
      const v = validateEdl(body);
      if (!v.ok) return jsonError(400, "EDL rejected", { errors: v.errors });
      await writeEdl(v.edl);
      return json({ ok: true, saved: rel(edlPath), backup: rel(backupPath), warnings: v.warnings, saved_at: new Date().toISOString() });
    }

    if (get && path === "/assets") {
      const { assets, errors } = await listAssets();
      return json({ assets, errors, groups: GROUP_ORDER });
    }

    if (get && path === "/media") {
      const raw = url.searchParams.get("path");
      const abs = resolveMedia(raw);
      if (!abs) return jsonError(raw && safeMediaPath(raw) ? 404 : 400, `not an allowed media path: ${JSON.stringify(raw)}`, { allowed: ALLOWED_MEDIA_DIRS });
      return serveFile(req, abs, { "cache-control": "no-cache" });
    }

    if (get && path === "/thumb") {
      const raw = url.searchParams.get("path");
      const abs = resolveMedia(raw);
      if (!abs) return jsonError(400, `not an allowed media path: ${JSON.stringify(raw)}`);
      const out = await thumb(abs, raw!);
      if (!out) return jsonError(500, `could not make a thumbnail for ${raw}`);
      return new Response(Bun.file(out), { headers: { "content-type": "image/jpeg", "cache-control": "max-age=3600" } });
    }

    if (get && path === "/presets") {
      return json({ grade: GRADE_PRESETS, clones: CLONE_LAYOUT, captions: CAPTION_STYLE, defaults: DEFAULT_GLOBALS, stitch_version: STITCH_VERSION });
    }

    if (get && path === "/status") {
      const song = await currentSong();
      const fin = existsSync(finalPath) ? statSync(finalPath) : null;
      return json({
        edl: rel(edlPath), song, timing: existsSync(join(ROOT, timingPathFor(song))) ? timingPathFor(song) : null,
        shots: existsSync(join(ROOT, shotsPathFor(song))) ? shotsPathFor(song) : null,
        final: fin ? { path: rel(finalPath), bytes: fin.size, mtime: Math.round(fin.mtimeMs) } : null,
        rendering: rendering ? { since_s: Math.round((Date.now() - rendering.started) / 1000) } : null,
      });
    }

    if (req.method === "POST" && path === "/render") {
      if (rendering) return jsonError(409, "a render is already running", { since_s: Math.round((Date.now() - rendering.started) / 1000) });
      const cur = await readEdlFile();
      if (!cur.edl) return jsonError(400, cur.error ?? "no EDL yet: open the page first");
      return renderStream();
    }

    if (get && path === "/final") {
      if (!existsSync(finalPath)) return jsonError(404, "no render yet: press Render final");
      const headers: Record<string, string> = { ...noStore };
      if (url.searchParams.has("download")) headers["content-disposition"] = 'attachment; filename="in-the-loop-devagar_directors-cut.mp4"';
      return serveFile(req, finalPath, headers);
    }

    return jsonError(404, `no route ${req.method} ${path}`);
  },
  error(e) {
    return jsonError(500, e.message);
  },
});

const song = await currentSong();
const state = (p: string) => (existsSync(join(ROOT, p)) ? "ok" : "missing, the page runs without it");
console.log(`director's cut ready: ${server.url}`);
console.log(`  edl:    ${rel(edlPath)} (${existsSync(edlPath) ? "ok" : "seeded on first load"})`);
console.log(`  timing: ${timingPathFor(song)} (${state(timingPathFor(song))})`);
console.log(`  shots:  ${shotsPathFor(song)} (${state(shotsPathFor(song))})`);
