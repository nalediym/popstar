#!/usr/bin/env bun
/**
 * Reference Master: the localhost look-builder for "In the Loop (Devagar)".
 *
 *   bun reference-master/server.ts            # any free port (printed)
 *   PORT=56110 bun reference-master/server.ts # a fixed port (56101 is reserved for the recorder)
 *
 * The page edits looks.json and queue.json; Claude renders through Comfy Cloud and writes the
 * results back into looks.json. This server only serves files, validates and writes JSON
 * atomically, grabs frames with ffmpeg and fetches reference images.
 */
import { lookup } from "node:dns/promises";
import { mkdir, readdir, readFile, realpath, rename, stat, unlink, writeFile } from "node:fs/promises";
import { isIP } from "node:net";
import { extname, isAbsolute, join, relative, resolve, sep } from "node:path";

const HERE = import.meta.dir;
const ROOT = resolve(HERE, "..");
const P = {
  index: join(HERE, "index.html"),
  looks: join(HERE, "looks.json"),
  backup: join(HERE, "looks.backup.json"),
  queue: join(HERE, "queue.json"),
  frames: join(HERE, "renders", "frames"),
  refs: join(HERE, "refs"),
  clips: join(ROOT, "clips"),
};
/** /img serves images from these folders only. */
const IMG_ROOTS = [join(HERE, "renders"), join(HERE, "tests"), join(HERE, "refs"), join(ROOT, "clips")];
const IMG_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".avif": "image/avif",
};
const RESERVED_PORT = 56101; // the coach recorder preview
const MAX_REF_BYTES = 15 * 1024 * 1024;
const MAX_HTML_BYTES = 5 * 1024 * 1024;
const FETCH_TIMEOUT_MS = 10_000;
const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 ReferenceMaster/1.0";

/** ExpressionEditor (AdvancedLivePortrait) slider ranges. */
const RANGES: Record<string, readonly [number, number]> = {
  rotate_pitch: [-20, 20],
  rotate_yaw: [-20, 20],
  rotate_roll: [-20, 20],
  blink: [-20, 5],
  eyebrow: [-10, 15],
  wink: [0, 25],
  pupil_x: [-15, 15],
  pupil_y: [-15, 15],
  aaa: [-30, 120],
  eee: [-20, 15],
  woo: [-20, 15],
  smile: [-0.3, 1.3],
};
const STATUSES = ["draft", "queued", "wardrobe-done", "done"];
const WHATS = ["wardrobe", "expression", "both"];
const ROLES = ["wardrobe", "mood", "set"];

class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
    public extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

// ---------------------------------------------------------------- helpers

const json = (data: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  });

const hashText = (text: string) => Bun.hash(text).toString(16);
const repoRel = (abs: string) => relative(ROOT, abs).split(sep).join("/");
const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;
const log = (msg: string) => console.log(`${new Date().toLocaleTimeString()}  ${msg}`);
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** True when `p` is strictly inside `root`. */
function inside(root: string, p: string): boolean {
  const r = relative(root, p);
  return r !== "" && r.split(sep)[0] !== ".." && !isAbsolute(r);
}

async function readText(file: string): Promise<string | null> {
  try {
    return await readFile(file, "utf8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
}

/** Write to a temp file in the same folder, then rename over the target. */
async function atomicWrite(file: string, data: string | Uint8Array): Promise<void> {
  const tmp = `${file}.tmp-${process.pid}-${Math.random().toString(36).slice(2, 8)}`;
  await writeFile(tmp, data);
  await rename(tmp, file);
}

/** One writer at a time for looks.json, queue.json and refs/. */
let chain: Promise<unknown> = Promise.resolve();
function withLock<T>(fn: () => Promise<T>): Promise<T> {
  const run = chain.then(fn, fn);
  chain = run.catch(() => undefined);
  return run;
}

/** JSON with 2-space indents, but short all-primitive objects/arrays stay on one line
 *  (so each look's 12 slider values read as one line). */
function pretty(v: unknown, indent = ""): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v) ?? "null";
  const isArr = Array.isArray(v);
  const entries: [string, unknown][] = isArr
    ? (v as unknown[]).map((x, i): [string, unknown] => [String(i), x ?? null])
    : Object.entries(v as Record<string, unknown>).filter(([, x]) => x !== undefined);
  if (!entries.length) return isArr ? "[]" : "{}";
  if (entries.every(([, x]) => x === null || typeof x !== "object")) {
    const one = isArr
      ? `[${entries.map(([, x]) => JSON.stringify(x)).join(", ")}]`
      : `{${entries.map(([k, x]) => `${JSON.stringify(k)}: ${JSON.stringify(x)}`).join(", ")}}`;
    if (indent.length + one.length <= 200) return one;
  }
  const next = `${indent}  `;
  const body = entries.map(([k, x]) => `${next}${isArr ? "" : `${JSON.stringify(k)}: `}${pretty(x, next)}`).join(",\n");
  return isArr ? `[\n${body}\n${indent}]` : `{\n${body}\n${indent}}`;
}

// ---------------------------------------------------------------- looks.json shape

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => v !== null && typeof v === "object" && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isPathOrNull = (v: unknown) => v === null || v === undefined || isStr(v);

function checkExpression(e: unknown, at: string, errs: string[], ranged = true): void {
  if (!isObj(e)) {
    errs.push(`${at} must be an object with the 12 slider values`);
    return;
  }
  for (const [k, [lo, hi]] of Object.entries(RANGES)) {
    const v = e[k];
    if (!isNum(v)) errs.push(`${at}.${k} must be a number`);
    else if (ranged && (v < lo || v > hi)) errs.push(`${at}.${k} = ${v} is outside ${lo}..${hi}`);
  }
}

/** Returns a list of problems; empty means the document is OK to write. Unknown extra fields are kept. */
function validateLooks(d: unknown): string[] {
  const errs: string[] = [];
  if (!isObj(d)) return ["the document must be a JSON object"];
  if (!isNum(d.version)) errs.push("version must be a number");
  if (!Array.isArray(d.palette) || !d.palette.every((c) => isStr(c) && /^#[0-9a-f]{3,8}$/i.test(c)))
    errs.push("palette must be an array of hex colors");
  if (!isStr(d.identity_lock)) errs.push("identity_lock must be a string");

  const sceneIds = new Set<string>();
  if (!Array.isArray(d.scenes)) errs.push("scenes must be an array");
  else
    d.scenes.forEach((s: unknown, i) => {
      const at = `scenes[${i}]`;
      if (!isObj(s)) return void errs.push(`${at} must be an object`);
      if (!isStr(s.id) || !s.id) errs.push(`${at}.id must be a non-empty string`);
      else if (sceneIds.has(s.id)) errs.push(`${at}.id "${s.id}" is duplicated`);
      else sceneIds.add(s.id);
      for (const f of ["title", "set", "light"]) if (!isStr(s[f])) errs.push(`${at}.${f} must be a string`);
      for (const f of ["start", "end"]) if (!isNum(s[f])) errs.push(`${at}.${f} must be a number (seconds)`);
      if (!Array.isArray(s.characters) || !s.characters.every(isStr))
        errs.push(`${at}.characters must be an array of names`);
    });

  const lookIds = new Set<string>();
  if (!Array.isArray(d.looks)) errs.push("looks must be an array");
  else
    d.looks.forEach((l: unknown, i) => {
      const at = `looks[${i}]`;
      if (!isObj(l)) return void errs.push(`${at} must be an object`);
      if (!isStr(l.id) || !l.id) errs.push(`${at}.id must be a non-empty string`);
      else if (lookIds.has(l.id)) errs.push(`${at}.id "${l.id}" is duplicated`);
      else lookIds.add(l.id);
      if (!isStr(l.scene) || !sceneIds.has(l.scene)) errs.push(`${at}.scene must name one of the scenes`);
      if (!isStr(l.character) || !l.character) errs.push(`${at}.character must be a non-empty string`);
      for (const f of ["wardrobe", "props", "emotion", "notes"]) if (!isStr(l[f])) errs.push(`${at}.${f} must be a string`);
      checkExpression(l.expression, `${at}.expression`, errs);
      if (l.expression_base !== undefined) checkExpression(l.expression_base, `${at}.expression_base`, errs, false);
      if (l.intensity !== undefined && !(isNum(l.intensity) && l.intensity >= 0 && l.intensity <= 200))
        errs.push(`${at}.intensity must be a percentage 0..200`);
      if (!isPathOrNull(l.source_frame)) errs.push(`${at}.source_frame must be a path or null`);
      if (!isObj(l.renders)) errs.push(`${at}.renders must be an object {wardrobe, expression}`);
      else
        for (const f of ["wardrobe", "expression"])
          if (!isPathOrNull(l.renders[f])) errs.push(`${at}.renders.${f} must be a path or null`);
      if (!STATUSES.includes(l.status as string)) errs.push(`${at}.status must be one of ${STATUSES.join(" / ")}`);
      if (l.refs !== undefined) {
        if (!Array.isArray(l.refs)) errs.push(`${at}.refs must be an array`);
        else
          l.refs.forEach((r: unknown, j) => {
            const rat = `${at}.refs[${j}]`;
            if (!isObj(r) || !isStr(r.path)) return void errs.push(`${rat} must be {path, source_url, role}`);
            if (!isPathOrNull(r.source_url)) errs.push(`${rat}.source_url must be a string or null`);
            if (!ROLES.includes(r.role as string)) errs.push(`${rat}.role must be one of ${ROLES.join(" / ")}`);
            if (r.starred !== undefined && typeof r.starred !== "boolean") errs.push(`${rat}.starred must be true/false`);
          });
      }
    });

  if (!isObj(d.emotions)) errs.push("emotions must be an object of presets");
  else
    for (const [id, e] of Object.entries(d.emotions)) {
      if (!isObj(e) || !isStr(e.label)) {
        errs.push(`emotions.${id} needs a label`);
        continue;
      }
      checkExpression(e.expression, `emotions.${id}.expression`, errs);
    }
  return errs;
}

async function loadLooks(): Promise<{ text: string; doc: Obj; hash: string }> {
  const text = await readText(P.looks);
  if (text === null) throw new HttpError(500, "reference-master/looks.json is missing");
  try {
    return { text, doc: JSON.parse(text) as Obj, hash: hashText(text) };
  } catch (e) {
    throw new HttpError(422, `looks.json on disk is not valid JSON: ${errMsg(e)}`, { hash: hashText(text) });
  }
}

/** Writes looks.json atomically and keeps the previous version in looks.backup.json. */
async function writeLooks(doc: unknown, prev: string | null): Promise<{ hash: string; changed: boolean }> {
  const out = `${pretty(doc)}\n`;
  if (prev === out) return { hash: hashText(out), changed: false };
  if (prev !== null) await atomicWrite(P.backup, prev);
  await atomicWrite(P.looks, out);
  return { hash: hashText(out), changed: true };
}

/** Lexical allow-list check for /img (realpath is checked again before serving). */
function safeImagePath(p: unknown): string | null {
  if (!isStr(p) || !p || p.includes("\0") || p.includes("\\") || isAbsolute(p)) return null;
  const abs = resolve(ROOT, p);
  if (!IMG_ROOTS.some((r) => inside(r, abs))) return null;
  if (!IMG_TYPES[extname(abs).toLowerCase()]) return null;
  return abs;
}

/** mtime+size per image the doc points at, so the page can reload images that changed in place. */
async function fileVersions(doc: Obj): Promise<Record<string, string | null>> {
  const paths = new Set<string>();
  for (const l of Array.isArray(doc.looks) ? doc.looks : []) {
    if (!isObj(l)) continue;
    if (isStr(l.source_frame)) paths.add(l.source_frame);
    if (isObj(l.renders)) for (const k of ["wardrobe", "expression"]) if (isStr(l.renders[k])) paths.add(l.renders[k]);
    if (Array.isArray(l.refs)) for (const r of l.refs) if (isObj(r) && isStr(r.path)) paths.add(r.path);
  }
  const out: Record<string, string | null> = {};
  await Promise.all(
    [...paths].map(async (p) => {
      const abs = safeImagePath(p);
      out[p] = null;
      if (!abs) return;
      try {
        const st = await stat(abs);
        if (st.isFile()) out[p] = `${Math.round(st.mtimeMs).toString(36)}${st.size.toString(36)}`;
      } catch {
        /* missing file → null */
      }
    }),
  );
  return out;
}

// ---------------------------------------------------------------- routes: looks, img, queue

async function getLooks(url: URL): Promise<Response> {
  const text = await readText(P.looks);
  if (text === null) throw new HttpError(500, "reference-master/looks.json is missing");
  const hash = hashText(text);
  if (!url.searchParams.has("meta")) {
    return new Response(text, {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        etag: `"${hash}"`,
        "x-looks-hash": hash,
      },
    });
  }
  let doc: Obj;
  try {
    doc = JSON.parse(text) as Obj;
  } catch (e) {
    return json({ error: `looks.json on disk is not valid JSON: ${errMsg(e)}`, hash }, 422);
  }
  return json({ hash, looks: doc, files: await fileVersions(doc) });
}

async function putLooks(req: Request): Promise<Response> {
  const body = await req.text();
  let doc: unknown;
  try {
    doc = JSON.parse(body);
  } catch (e) {
    throw new HttpError(400, `body is not valid JSON: ${errMsg(e)}`);
  }
  const errors = validateLooks(doc);
  if (errors.length) {
    const more = errors.length > 1 ? ` (+${errors.length - 1} more)` : "";
    return json({ error: `invalid looks.json: ${errors[0]}${more}`, details: errors.slice(0, 40) }, 400);
  }
  const ifMatch = (req.headers.get("if-match") ?? "").replace(/^W\//, "").replaceAll('"', "").trim();
  return withLock(async () => {
    const cur = await readText(P.looks);
    if (ifMatch && ifMatch !== "*" && cur !== null && hashText(cur) !== ifMatch) {
      let looks: unknown = null;
      try {
        looks = JSON.parse(cur);
      } catch {
        /* invalid on disk: the page offers an overwrite */
      }
      return json({ error: "conflict: looks.json changed on disk", hash: hashText(cur), looks }, 409);
    }
    const { hash, changed } = await writeLooks(doc, cur);
    if (changed) log(`saved looks.json (${(doc as { looks: unknown[] }).looks.length} looks)`);
    return json({ ok: true, hash, changed });
  });
}

async function getImg(req: Request, url: URL): Promise<Response> {
  const p = url.searchParams.get("path") ?? "";
  const abs = safeImagePath(p);
  if (!abs)
    throw new HttpError(
      403,
      "refused: /img only serves .png .jpg .webp .gif .avif inside reference-master/renders, reference-master/tests, reference-master/refs or clips",
    );
  let real: string;
  try {
    real = await realpath(abs);
  } catch {
    throw new HttpError(404, `not found: ${p}`);
  }
  const roots = await Promise.all(IMG_ROOTS.map((r) => realpath(r).catch(() => r)));
  if (!roots.some((r) => inside(r, real))) throw new HttpError(403, "refused: that path resolves outside the allowed folders");
  const st = await stat(real);
  if (!st.isFile()) throw new HttpError(404, `not a file: ${p}`);
  const etag = `"${Math.round(st.mtimeMs).toString(36)}-${st.size.toString(36)}"`;
  const headers = {
    "content-type": IMG_TYPES[extname(abs).toLowerCase()] ?? "application/octet-stream",
    "cache-control": "no-cache",
    etag,
    "x-content-type-options": "nosniff",
  };
  if (req.headers.get("if-none-match") === etag) return new Response(null, { status: 304, headers });
  if (req.method === "HEAD") return new Response(null, { headers: { ...headers, "content-length": String(st.size) } });
  return new Response(Bun.file(real), { headers });
}

type QueueEntry = { look_id: string; what: string; requested_at: string };

async function readQueue(): Promise<QueueEntry[]> {
  const text = await readText(P.queue);
  if (text === null || !text.trim()) return [];
  let q: unknown;
  try {
    q = JSON.parse(text);
  } catch (e) {
    throw new HttpError(500, `queue.json is not valid JSON: ${errMsg(e)}`);
  }
  if (!Array.isArray(q)) throw new HttpError(500, "queue.json must be a JSON array");
  return q as QueueEntry[];
}
const writeQueue = (q: QueueEntry[]) => atomicWrite(P.queue, `${pretty(q)}\n`);

async function postQueue(req: Request): Promise<Response> {
  const b = (await req.json().catch(() => null)) as Obj | null;
  const lookId = b?.look_id;
  const what = b?.what;
  if (!isStr(lookId) || !lookId) throw new HttpError(400, "look_id is required");
  if (!isStr(what) || !WHATS.includes(what)) throw new HttpError(400, `what must be one of ${WHATS.join(" / ")}`);
  const { doc } = await loadLooks();
  const looks = Array.isArray(doc.looks) ? doc.looks : [];
  if (!looks.some((l) => isObj(l) && l.id === lookId)) throw new HttpError(404, `no look "${lookId}" in looks.json`);
  return withLock(async () => {
    const q = await readQueue();
    const requested_at = new Date().toISOString();
    // One entry per look: re-queueing widens `what` instead of paying for a duplicate render.
    const hit = q.find((e) => e.look_id === lookId);
    if (hit) {
      hit.what = hit.what === what ? what : "both";
      hit.requested_at = requested_at;
    } else q.push({ look_id: lookId, what, requested_at });
    await writeQueue(q);
    log(`queued ${lookId} (${hit?.what ?? what}); ${q.length} in queue`);
    return json(q);
  });
}

async function deleteQueue(url: URL): Promise<Response> {
  const lookId = url.searchParams.get("look_id");
  if (!lookId) throw new HttpError(400, "use DELETE /queue?look_id=<id>");
  return withLock(async () => {
    const q = await readQueue();
    const next = q.filter((e) => e.look_id !== lookId);
    if (next.length !== q.length) {
      await writeQueue(next);
      log(`unqueued ${lookId}; ${next.length} in queue`);
    }
    return json(next);
  });
}

// ---------------------------------------------------------------- routes: clips + frames

const durations = new Map<string, { mtime: number; d: number | null }>();
async function clipDuration(file: string): Promise<number | null> {
  try {
    const st = await stat(file);
    const hit = durations.get(file);
    if (hit && hit.mtime === st.mtimeMs) return hit.d;
    const proc = Bun.spawn(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file], {
      stdout: "pipe",
      stderr: "ignore",
    });
    const out = await new Response(proc.stdout).text();
    await proc.exited;
    const n = Number.parseFloat(out);
    const d = Number.isFinite(n) ? Math.round(n * 1000) / 1000 : null;
    durations.set(file, { mtime: st.mtimeMs, d });
    return d;
  } catch {
    return null;
  }
}

async function getClips(): Promise<Response> {
  let names: string[] = [];
  try {
    names = (await readdir(P.clips)).filter((n) => /^[\w-]+\.mp4$/.test(n)).sort();
  } catch {
    /* no clips/ yet */
  }
  const clips = await Promise.all(
    names.map(async (n) => ({ name: n.slice(0, -4), duration: await clipDuration(join(P.clips, n)) })),
  );
  return json(clips);
}

async function getFrames(url: URL): Promise<Response> {
  const clip = url.searchParams.get("clip") ?? "";
  const tRaw = (url.searchParams.get("t") ?? "").trim();
  if (!/^[\w-]+$/.test(clip)) throw new HttpError(400, "clip must be a clip name like chunk-01 (letters, digits, _ and -)");
  if (!/^(\d+(\.\d*)?|\.\d+)$/.test(tRaw)) throw new HttpError(400, "t must be a time in seconds, like 1.0");
  const t = Number(tRaw);
  const src = join(P.clips, `${clip}.mp4`);
  if (!(await stat(src).then((s) => s.isFile(), () => false))) throw new HttpError(404, `clips/${clip}.mp4 not found`);
  const dur = await clipDuration(src);
  if (dur !== null && t >= dur) throw new HttpError(422, `t = ${t}s is past the end of ${clip}.mp4 (${dur}s long)`);

  const tag = t.toFixed(3).replace(/\.?0+$/, "");
  await mkdir(P.frames, { recursive: true });
  const out = join(P.frames, `${clip}_${tag}.png`);
  const tmp = join(P.frames, `.${clip}_${tag}.${process.pid}.tmp.png`);
  const proc = Bun.spawn(
    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", String(t), "-i", src, "-frames:v", "1", "-update", "1", tmp],
    { stdin: "ignore", stdout: "ignore", stderr: "pipe" },
  );
  const killer = setTimeout(() => proc.kill(), 20_000);
  const [code, err] = await Promise.all([proc.exited, new Response(proc.stderr).text()]);
  clearTimeout(killer);
  const ok = code === 0 && (await stat(tmp).then((s) => s.size > 0, () => false));
  if (!ok) {
    await unlink(tmp).catch(() => undefined);
    const why = err.trim().split("\n").slice(-2).join(" ") || `ffmpeg exit ${code}`;
    throw new HttpError(422, `ffmpeg could not grab a frame at ${t}s from ${clip}.mp4: ${why}`);
  }
  await rename(tmp, out);
  log(`frame ${clip} @ ${t}s → ${repoRel(out)}`);
  return json({ path: repoRel(out), clip, t });
}

// ---------------------------------------------------------------- routes: refs (Pinterest / image links / uploads)

function ipv4Blocked(ip: string): boolean {
  const p = ip.split(".").map(Number);
  if (p.length !== 4 || p.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return true;
  const [a, b, c] = p as [number, number, number, number];
  return (
    a === 0 || a === 10 || a === 127 || a >= 224 || // this-net, private, loopback, multicast/reserved
    (a === 100 && b >= 64 && b <= 127) || // CGNAT
    (a === 169 && b === 254) || // link-local
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 192 && b === 0 && c === 0) ||
    (a === 198 && (b === 18 || b === 19)) // benchmarking
  );
}

function expandV6(ip: string): number[] | null {
  let s = ip.toLowerCase().split("%")[0] ?? "";
  const v4 = /(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(s);
  if (v4) {
    const [a, b, c, d] = v4.slice(1).map(Number) as [number, number, number, number];
    s = `${s.slice(0, v4.index)}${((a << 8) | b).toString(16)}:${((c << 8) | d).toString(16)}`;
  }
  const halves = s.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
  if (fill < 0) return null;
  const parts = [...head, ...Array<string>(fill).fill("0"), ...tail];
  if (parts.length !== 8) return null;
  const nums = parts.map((h) => (/^[0-9a-f]{1,4}$/.test(h) ? Number.parseInt(h, 16) : Number.NaN));
  return nums.some((n) => Number.isNaN(n)) ? null : nums;
}

function ipv6Blocked(ip: string): boolean {
  const h = expandV6(ip);
  if (!h) return true;
  const v4 = (hi: number, lo: number) => `${hi >> 8}.${hi & 255}.${lo >> 8}.${lo & 255}`;
  const a = h[0] as number;
  if (h.every((n) => n === 0)) return true; // ::
  if (h.slice(0, 7).every((n) => n === 0) && h[7] === 1) return true; // ::1
  if (h.slice(0, 5).every((n) => n === 0) && (h[5] === 0xffff || h[5] === 0)) return ipv4Blocked(v4(h[6] as number, h[7] as number));
  if (a === 0x64 && h[1] === 0xff9b) return ipv4Blocked(v4(h[6] as number, h[7] as number)); // NAT64
  if (a === 0x2002) return ipv4Blocked(v4(h[1] as number, h[2] as number)); // 6to4
  return (a & 0xfe00) === 0xfc00 || (a & 0xffc0) === 0xfe80 || (a & 0xff00) === 0xff00; // ULA, link-local, multicast
}

/** http(s) only, no credentials, and the host must resolve to public addresses only. */
async function assertPublicUrl(raw: string): Promise<URL> {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    throw new HttpError(400, "that isn't a valid link");
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") throw new HttpError(400, "only http(s) links are allowed");
  if (u.username || u.password) throw new HttpError(400, "links with a username or password are not allowed");
  const host = u.hostname.replace(/^\[|\]$/g, "").replace(/\.$/, "").toLowerCase();
  if (!host || host === "localhost" || /\.(localhost|local|internal|home\.arpa)$/.test(host))
    throw new HttpError(400, `blocked: ${host || "that host"} is a local address`);
  let addrs: { address: string; family: number }[];
  if (isIP(host)) addrs = [{ address: host, family: isIP(host) }];
  else {
    try {
      addrs = await lookup(host, { all: true, verbatim: true });
    } catch {
      throw new HttpError(400, `couldn't find ${host} (DNS)`);
    }
  }
  if (!addrs.length) throw new HttpError(400, `couldn't find ${host}`);
  for (const a of addrs)
    if (a.family === 6 ? ipv6Blocked(a.address) : ipv4Blocked(a.address))
      throw new HttpError(400, `blocked: ${host} points to a private or local address`);
  return u;
}

/** Fetches with manual redirects so every hop is re-checked. */
async function fetchPublic(raw: string, accept: string): Promise<{ res: Response; url: URL }> {
  let next = raw;
  for (let hop = 0; hop < 6; hop++) {
    const url = await assertPublicUrl(next);
    let res: Response;
    try {
      res = await fetch(url, {
        redirect: "manual",
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        headers: { "user-agent": UA, accept, "accept-language": "en-US,en;q=0.8" },
      });
    } catch (e) {
      const name = e instanceof Error ? e.name : "";
      throw new HttpError(
        504,
        name === "TimeoutError" || name === "AbortError"
          ? `timed out after ${FETCH_TIMEOUT_MS / 1000}s fetching ${url.host}`
          : `couldn't fetch ${url.host}: ${errMsg(e)}`,
      );
    }
    const loc = res.headers.get("location");
    if (res.status >= 300 && res.status < 400 && loc) {
      await res.body?.cancel().catch(() => undefined);
      next = new URL(loc, url).toString();
      continue;
    }
    if (!res.ok) {
      await res.body?.cancel().catch(() => undefined);
      throw new HttpError(502, `${url.host} answered HTTP ${res.status}`);
    }
    return { res, url };
  }
  throw new HttpError(502, "too many redirects");
}

/** Reads a body up to `max` bytes. Images over the cap fail; pages are truncated (og tags live in <head>). */
async function readCapped(res: Response, max: number, what: string, truncate = false): Promise<Uint8Array> {
  const len = Number(res.headers.get("content-length") ?? 0);
  if (!truncate && len > max) {
    await res.body?.cancel().catch(() => undefined);
    throw new HttpError(413, `that ${what} is ${mb(len)}; the cap is ${mb(max)}`);
  }
  if (!res.body) return new Uint8Array(0);
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (total + value.byteLength > max) {
        await reader.cancel().catch(() => undefined);
        if (!truncate) throw new HttpError(413, `that ${what} is over the ${mb(max)} cap`);
        chunks.push(value.subarray(0, max - total));
        total = max;
        break;
      }
      chunks.push(value);
      total += value.byteLength;
    }
  } catch (e) {
    if (e instanceof HttpError) throw e;
    throw new HttpError(504, `downloading the ${what} failed or timed out (${errMsg(e)})`);
  }
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.byteLength;
  }
  return out;
}

const ENTITIES: Record<string, string> = { amp: "&", quot: '"', apos: "'", "#39": "'", lt: "<", gt: ">" };
const decodeEntities = (s: string) =>
  s.replace(/&(amp|quot|apos|#39|lt|gt);|&#x([0-9a-f]+);|&#(\d+);/gi, (m, n?: string, hex?: string, dec?: string) =>
    n ? (ENTITIES[n.toLowerCase()] ?? m) : String.fromCodePoint(Number.parseInt(hex ?? dec ?? "0", hex ? 16 : 10)),
  );

/** The pin page's og:image (or twitter:image) as an absolute URL. */
async function ogImage(html: string, base: URL): Promise<string | null> {
  const found = new Map<number, string>();
  const take = (rank: number, attr: string) => ({
    element(el: { getAttribute(name: string): string | null }) {
      const v = el.getAttribute(attr);
      if (v && !found.has(rank)) found.set(rank, v);
    },
  });
  await new HTMLRewriter()
    .on('meta[property="og:image:secure_url"]', take(1, "content"))
    .on('meta[property="og:image"]', take(2, "content"))
    .on('meta[name="og:image"]', take(3, "content"))
    .on('meta[property="og:image:url"]', take(4, "content"))
    .on('meta[name="twitter:image"]', take(5, "content"))
    .on('meta[name="twitter:image:src"]', take(6, "content"))
    .on('meta[property="twitter:image"]', take(7, "content"))
    .on('link[rel="image_src"]', take(8, "href"))
    .transform(new Response(html))
    .text();
  for (const rank of [...found.keys()].sort((a, b) => a - b)) {
    try {
      return new URL(decodeEntities((found.get(rank) ?? "").trim()), base).toString();
    } catch {
      /* try the next candidate */
    }
  }
  return null;
}

function sniffImage(b: Uint8Array): string | null {
  const ascii = (from: number, to: number) => String.fromCharCode(...b.subarray(from, to));
  if (b.length >= 8 && b[0] === 0x89 && ascii(1, 4) === "PNG") return "png";
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "jpg";
  if (b.length >= 6 && ascii(0, 4) === "GIF8") return "gif";
  if (b.length >= 12 && ascii(0, 4) === "RIFF" && ascii(8, 12) === "WEBP") return "webp";
  if (b.length >= 12 && ascii(4, 8) === "ftyp" && ["avif", "avis"].includes(ascii(8, 12))) return "avif";
  return null;
}

async function imageBody(res: Response, type: string): Promise<Uint8Array> {
  if (type.includes("svg")) {
    await res.body?.cancel().catch(() => undefined);
    throw new HttpError(415, "SVG isn't accepted; use a PNG, JPEG or WebP image");
  }
  return readCapped(res, MAX_REF_BYTES, "image");
}

/** A direct image link is downloaded as-is; a page link (pinterest.com/pin/…, pin.it/…) → its og:image. */
async function fetchRefImage(raw: string): Promise<{ bytes: Uint8Array; source_url: string }> {
  const link = raw.trim();
  if (!link) throw new HttpError(400, "paste a Pinterest pin or image link first");
  const { res, url } = await fetchPublic(link, "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.9,text/html;q=0.8,*/*;q=0.5");
  const type = (res.headers.get("content-type") ?? "").toLowerCase();
  if (type.startsWith("image/")) return { bytes: await imageBody(res, type), source_url: link };
  if (type.includes("html")) {
    const html = new TextDecoder().decode(await readCapped(res, MAX_HTML_BYTES, "page", true));
    const img = await ogImage(html, url);
    if (!img)
      throw new HttpError(422, "that page has no og:image. Open the pin, right-click the picture → Copy Image, then paste it here");
    const second = await fetchPublic(img, "image/avif,image/webp,image/png,image/jpeg,image/*");
    const t2 = (second.res.headers.get("content-type") ?? "").toLowerCase();
    if (!t2.startsWith("image/")) {
      await second.res.body?.cancel().catch(() => undefined);
      throw new HttpError(415, `the page's og:image isn't an image (${t2 || "no content type"})`);
    }
    return { bytes: await imageBody(second.res, t2), source_url: link };
  }
  await res.body?.cancel().catch(() => undefined);
  throw new HttpError(415, `that link is ${type || "an unknown type"}, not an image or a web page`);
}

async function postRefs(req: Request): Promise<Response> {
  const ct = req.headers.get("content-type") ?? "";
  let lookId = "";
  let role = "wardrobe";
  const items: { bytes: Uint8Array; source_url: string | null }[] = [];
  const assertLook = async () => {
    if (!/^\w[\w.-]*$/.test(lookId)) throw new HttpError(400, "look_id is required");
    if (!ROLES.includes(role)) throw new HttpError(400, `role must be one of ${ROLES.join(" / ")}`);
    const { doc } = await loadLooks();
    const looks = Array.isArray(doc.looks) ? doc.looks : [];
    if (!looks.some((l) => isObj(l) && l.id === lookId)) throw new HttpError(404, `no look "${lookId}" in looks.json`);
  };

  if (ct.startsWith("multipart/form-data")) {
    const form = await req.formData();
    lookId = String(form.get("look_id") ?? "");
    role = String(form.get("role") ?? "wardrobe");
    await assertLook();
    const files = form.getAll("file").filter((f): f is File => typeof f === "object" && f !== null && "arrayBuffer" in f);
    if (!files.length) throw new HttpError(400, 'the upload has no image (form field "file")');
    for (const f of files) {
      if (f.size > MAX_REF_BYTES) throw new HttpError(413, `${f.name || "that image"} is ${mb(f.size)}; the cap is 15 MB`);
      items.push({ bytes: new Uint8Array(await f.arrayBuffer()), source_url: null });
    }
  } else {
    const b = (await req.json().catch(() => null)) as Obj | null;
    lookId = String(b?.look_id ?? "");
    role = String(b?.role ?? "wardrobe");
    await assertLook();
    items.push(await fetchRefImage(String(b?.url ?? "")));
  }

  const typed = items.map((it) => {
    const ext = sniffImage(it.bytes);
    if (!ext) throw new HttpError(415, "that isn't a PNG, JPEG, WebP, GIF or AVIF image");
    return { ...it, ext };
  });

  return withLock(async () => {
    const { text, doc } = await loadLooks();
    const look = (Array.isArray(doc.looks) ? doc.looks : []).find((l): l is Obj => isObj(l) && l.id === lookId);
    if (!look) throw new HttpError(404, `no look "${lookId}" in looks.json`);
    const dir = join(P.refs, lookId);
    if (!inside(P.refs, dir)) throw new HttpError(400, "bad look_id");
    await mkdir(dir, { recursive: true });
    let n = (await readdir(dir)).reduce((m, f) => Math.max(m, Number(/^(\d+)\./.exec(f)?.[1] ?? 0)), 0);
    const added: Obj[] = [];
    for (const it of typed) {
      n += 1;
      const file = join(dir, `${n}.${it.ext}`);
      await atomicWrite(file, it.bytes);
      added.push({ path: repoRel(file), source_url: it.source_url, role, starred: false });
    }
    look.refs = [...(Array.isArray(look.refs) ? look.refs : []), ...added];
    const { hash } = await writeLooks(doc, text);
    log(`refs +${added.length} → ${lookId} (${added.map((a) => a.path).join(", ")})`);
    return json({ ok: true, added, hash, looks: doc });
  });
}

// ---------------------------------------------------------------- router + server

const LOCAL_HOST = /^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i;
const KNOWN = new Set(["/", "/index.html", "/looks", "/img", "/queue", "/frames", "/clips", "/refs"]);

async function route(req: Request): Promise<Response> {
  const url = new URL(req.url);
  // Refuse DNS-rebinding (foreign Host) and cross-site writes (foreign Origin).
  if (!LOCAL_HOST.test(req.headers.get("host") ?? "")) return json({ error: "Reference Master only answers on localhost" }, 403);
  const m = req.method;
  if (m !== "GET" && m !== "HEAD") {
    const origin = req.headers.get("origin");
    let local = true;
    if (origin) {
      try {
        local = LOCAL_HOST.test(new URL(origin).host);
      } catch {
        local = false;
      }
    }
    if (!local) return json({ error: "cross-origin request refused" }, 403);
  }
  switch (`${m} ${url.pathname}`) {
    case "GET /":
    case "GET /index.html": {
      const page = await readText(P.index); // read fresh each time so edits show on reload
      if (page === null) throw new HttpError(500, "reference-master/index.html is missing");
      return new Response(page, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
    }
    case "GET /looks":
      return getLooks(url);
    case "PUT /looks":
      return putLooks(req);
    case "GET /img":
    case "HEAD /img":
      return getImg(req, url);
    case "GET /queue":
      return json(await readQueue());
    case "POST /queue":
      return postQueue(req);
    case "DELETE /queue":
      return deleteQueue(url);
    case "GET /frames":
      return getFrames(url);
    case "GET /clips":
      return getClips();
    case "POST /refs":
      return postRefs(req);
    case "GET /favicon.ico":
      return new Response(null, { status: 204 });
  }
  return KNOWN.has(url.pathname)
    ? json({ error: `${m} is not allowed on ${url.pathname}` }, 405)
    : json({ error: `no route ${url.pathname}` }, 404);
}

function start(port: number) {
  return Bun.serve({
    hostname: "127.0.0.1",
    port,
    idleTimeout: 60, // ref fetches can take up to ~20s (page + image)
    maxRequestBodySize: 64 * 1024 * 1024,
    async fetch(req) {
      try {
        return await route(req);
      } catch (e) {
        if (e instanceof HttpError) return json({ error: e.message, ...e.extra }, e.status);
        console.error(e);
        return json({ error: errMsg(e) }, 500);
      }
    },
  });
}

let want = Number.parseInt(process.env.PORT ?? "", 10);
if (!Number.isInteger(want) || want < 0 || want > 65535) want = 0;
if (want === RESERVED_PORT) {
  console.warn(`PORT ${RESERVED_PORT} belongs to the coach recorder; picking a free port instead.`);
  want = 0;
}
let server: ReturnType<typeof start>;
try {
  server = start(want);
} catch (e) {
  if (want === 0 || !/EADDRINUSE|in use/i.test(`${(e as NodeJS.ErrnoException).code ?? ""} ${errMsg(e)}`)) throw e;
  console.warn(`port ${want} is busy; picking a free port instead.`);
  server = start(0);
}
console.log(`\n  Reference Master → http://127.0.0.1:${server.port}/\n  (Ctrl+C to stop)\n`);
