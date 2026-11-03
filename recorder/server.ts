// Coach recorder: serves the coach UI, the song, its timing + shot list, and saves takes into ../clips as .mp4.
// Run: bun recorder/server.ts   (PORT env optional; defaults to a free port)
// Data overrides: TIMING_PATH=<file> SHOTS_PATH=<file> (default to the repo files below).
import { mkdir } from "node:fs/promises";
import { join, relative, resolve } from "node:path";

const root = join(import.meta.dir, "..");
const clipsDir = join(root, "clips");
const song = join(root, "songs", "in-the-loop-devagar.mp3");
const timingPath = resolve(process.env.TIMING_PATH || join(root, "songs", "in-the-loop-devagar.timing.json"));
const shotsPath = resolve(process.env.SHOTS_PATH || join(root, "shots", "in-the-loop-devagar.json"));
await mkdir(clipsDir, { recursive: true });

// The one rule for turning a requested clip name into a file name. /save and /check both use it.
const clipName = (raw: string) => raw.replace(/[^\w-]/g, "_");
const shown = (p: string) => (p.startsWith(root) ? relative(root, p) : p);
const noStore = { "cache-control": "no-store" };
const jsonError = (status: number, error: string, extra: Record<string, unknown> = {}) =>
  Response.json({ error, ...extra }, { status, headers: noStore });

// Timing + shots are written by other tools while this runs, so read them fresh on every request.
async function serveJson(path: string, label: string) {
  const file = Bun.file(path);
  if (!(await file.exists())) return jsonError(404, `${label} file not found: ${shown(path)}`);
  const text = await file.text();
  try {
    JSON.parse(text);
  } catch (e) {
    return jsonError(500, `${label} file is not valid JSON (${shown(path)}): ${(e as Error).message}`);
  }
  return new Response(text, { headers: { "content-type": "application/json; charset=utf-8", ...noStore } });
}

const rate = (r: unknown) => {
  const [n, d] = String(r ?? "").split("/").map(Number);
  return n > 0 && d > 0 ? n / d : 0;
};
const round = (x: number, places = 3) => Math.round(x * 10 ** places) / 10 ** places;

type Stream = { codec_type?: string; width?: number; height?: number; nb_frames?: string; duration?: string; avg_frame_rate?: string; r_frame_rate?: string };

// ffprobe a saved clip -> {name, duration_s, frames, fps, width, height, has_audio}
function probe(name: string, file: string) {
  const ff = Bun.spawnSync([
    "ffprobe", "-v", "error",
    "-show_entries", "stream=codec_type,width,height,nb_frames,duration,avg_frame_rate,r_frame_rate:format=duration",
    "-of", "json", file,
  ]);
  if (ff.exitCode !== 0) return jsonError(500, `ffprobe failed: ${ff.stderr.toString().trim()}`, { name });
  const info = JSON.parse(ff.stdout.toString()) as { streams?: Stream[]; format?: { duration?: string } };
  const streams = info.streams ?? [];
  const v = streams.find((s) => s.codec_type === "video");
  const fps = v ? rate(v.avg_frame_rate) || rate(v.r_frame_rate) : 0;
  const duration = Number(info.format?.duration ?? v?.duration ?? 0) || 0;
  const counted = Number(v?.nb_frames);
  return Response.json({
    name,
    duration_s: round(duration),
    frames: Number.isFinite(counted) && counted > 0 ? counted : Math.round(duration * fps),
    fps: round(fps),
    width: v?.width ?? null,
    height: v?.height ?? null,
    has_audio: streams.some((s) => s.codec_type === "audio"),
  }, { headers: noStore });
}

const server = Bun.serve({
  port: Number(process.env.PORT ?? 0),
  idleTimeout: 255, // seconds: a 32s take can take a while to re-encode inside /save
  maxRequestBodySize: 1024 * 1024 * 1024, // long takes are big; the default 128MB is too tight
  async fetch(req) {
    const url = new URL(req.url);

    if (req.method === "GET" && url.pathname === "/") {
      return new Response(Bun.file(join(import.meta.dir, "index.html")), { headers: noStore });
    }

    if (req.method === "GET" && url.pathname === "/song") {
      const file = Bun.file(song);
      if (!(await file.exists())) return jsonError(404, `song not found: ${shown(song)}`);
      return new Response(file); // Bun answers Range requests itself, so the page can seek
    }

    if (req.method === "GET" && url.pathname === "/timing") return serveJson(timingPath, "timing");
    if (req.method === "GET" && url.pathname === "/shots") return serveJson(shotsPath, "shots");

    if (req.method === "GET" && url.pathname === "/clips") {
      const files = [...new Bun.Glob("*.mp4").scanSync(clipsDir)].sort();
      return Response.json(files, { headers: noStore });
    }

    // A saved clip, for the page's demo mode (?demo=<name>). Only plain names: no dots, no slashes.
    const clipFile = req.method === "GET" && url.pathname.match(/^\/clips\/([\w-]+)\.mp4$/);
    if (clipFile) {
      const file = Bun.file(join(clipsDir, `${clipFile[1]}.mp4`));
      if (!(await file.exists())) return jsonError(404, `no clip clips/${clipFile[1]}.mp4`);
      return new Response(file);
    }

    if (req.method === "GET" && url.pathname === "/check") {
      const raw = url.searchParams.get("name") ?? "";
      if (!raw.trim() || /[/\\]|\.\./.test(raw)) return jsonError(400, `bad clip name: ${JSON.stringify(raw)}`);
      const name = clipName(raw);
      const file = join(clipsDir, `${name}.mp4`);
      if (!(await Bun.file(file).exists())) return jsonError(404, `no clip clips/${name}.mp4`, { name });
      return probe(name, file);
    }

    if (req.method === "POST" && url.pathname === "/save") {
      const name = clipName(url.searchParams.get("name") || `clip-${Date.now()}`);
      const mime = req.headers.get("content-type") ?? "";
      const raw = join(clipsDir, `${name}.${mime.includes("mp4") ? "src.mp4" : "src.webm"}`);
      const out = join(clipsDir, `${name}.mp4`);
      await Bun.write(raw, await req.arrayBuffer());

      // Normalize to H.264/AAC mp4 at constant 24fps so LTX + ffmpeg stitching get a clean input.
      const ff = Bun.spawnSync([
        "ffmpeg", "-y", "-loglevel", "error", "-i", raw,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-movflags", "+faststart", out,
      ]);
      if (ff.exitCode !== 0) {
        return new Response(`ffmpeg failed:\n${ff.stderr.toString()}`, { status: 500 });
      }
      await Bun.file(raw).delete();

      // Sidecar: where song t=0 lands in the clip, so stitching can lay the clean track back on.
      const offset = url.searchParams.get("song_offset");
      if (offset !== null) {
        await Bun.write(join(clipsDir, `${name}.json`), JSON.stringify({
          song: "songs/in-the-loop-devagar.mp3", song_offset_s: Number(offset),
        }, null, 2));
      }
      return Response.json({ saved: `clips/${name}.mp4` });
    }

    return new Response("not found", { status: 404 });
  },
  error(e) {
    return jsonError(500, e.message);
  },
});

async function state(p: string) {
  const file = Bun.file(p);
  if (!(await file.exists())) return "missing, the page runs without it";
  try {
    JSON.parse(await file.text());
    return "ok";
  } catch {
    return "not valid JSON yet, the page runs without it";
  }
}
console.log(`recorder ready: ${server.url}`);
console.log(`  timing: ${shown(timingPath)} (${await state(timingPath)})`);
console.log(`  shots:  ${shown(shotsPath)} (${await state(shotsPath)})`);
