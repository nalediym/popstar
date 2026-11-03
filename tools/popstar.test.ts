// bun test tools/popstar.test.ts
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseArgs } from "./popstar.ts";
import { BLENDER, parseVersion, requiredFailures, runDoctor, type DoctorDeps } from "./popstar/doctor.ts";
import { defaultManifest, loadManifest, validateManifest, writeManifest, type Manifest } from "./popstar/project.ts";
import type { RunResult } from "./popstar/proc.ts";
import { parseWith } from "./popstar/qa.ts";
import {
  cutPlan,
  floor8n1,
  framesFor,
  is8n1,
  naturalCompare,
  probe,
  render,
  sceneHash,
  slotsFingerprint,
  staleReason,
  tileFilter,
  type HashParts,
  type RenderManifest,
  type State,
} from "./popstar/render.ts";
import { urlIn } from "./popstar/studio.ts";

// ---------------------------------------------------------------- manifest

describe("popstar.json manifest", () => {
  test("defaults describe the current project", () => {
    const m = defaultManifest();
    expect(m.slug).toBe("in-the-loop-devagar");
    expect(m.song.file).toBe("songs/in-the-loop-devagar.mp3");
    expect(m.paths.shots).toBe("shots/in-the-loop-devagar.json");
    expect(m.size).toEqual([1280, 704]);
    expect(m.fps).toBe(24);
    expect(m.performer.identity_lock).toContain("rich deep brown skin");
    expect(m.performer.negative).toContain("lightened skin");
    expect(validateManifest(m)).toEqual([]);
  });

  test("new slugs get their own paths and a title", () => {
    const m = defaultManifest("my-next-song", { song: "songs/next.mp3", performer: "Someone" });
    expect(m.title).toBe("My Next Song");
    expect(m.song.file).toBe("songs/next.mp3");
    expect(m.song.recipe_md).toBe("songs/my-next-song.md");
    expect(m.paths.timing).toBe("songs/my-next-song.timing.json");
    expect(m.performer.name).toBe("Someone");
  });

  test("validation names what is wrong", () => {
    const errs = validateManifest({ version: 2, slug: "Bad Slug", palette: ["red"], paths: {}, size: [1] });
    expect(errs.some((e) => e.startsWith("version"))).toBe(true);
    expect(errs.some((e) => e.startsWith("slug"))).toBe(true);
    expect(errs.some((e) => e.startsWith("palette"))).toBe(true);
    expect(errs.some((e) => e.includes("paths.shots"))).toBe(true);
    expect(errs.some((e) => e.startsWith("size"))).toBe(true);
    expect(validateManifest(null)).toEqual(["popstar.json must be a JSON object"]);
  });

  test("loadManifest falls back to the defaults with a warning, reads a file, rejects a broken one", () => {
    const dir = mkdtempSync(join(tmpdir(), "popstar-manifest-"));
    try {
      const none = loadManifest(dir);
      expect(none.source).toBe("defaults");
      expect(none.warning).toContain("popstar new");
      const m = defaultManifest("abc", { song: "songs/abc.mp3" });
      writeManifest(dir, m);
      const got = loadManifest(dir);
      expect(got.source).toBe("file");
      expect(got.warning).toBeUndefined();
      expect(got.manifest).toEqual(m);
      writeFileSync(join(dir, "popstar.json"), "{ nope");
      expect(() => loadManifest(dir)).toThrow(/not valid JSON/);
      writeFileSync(join(dir, "popstar.json"), JSON.stringify({ ...m, fps: 0 }));
      expect(() => loadManifest(dir)).toThrow(/fps/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------- frame math

describe("8n+1 frame math", () => {
  test("framesFor picks the smallest 8n+1 that covers the scene", () => {
    expect(framesFor(0, 9.5, 24)).toBe(233); // 228 raw
    expect(framesFor(17.34, 25.16, 24)).toBe(193); // 187.68 raw
    expect(framesFor(25.16, 32, 24)).toBe(169); // 164.16 raw
    expect(framesFor(0, 8, 24)).toBe(193); // exactly 192 -> one spare frame
    expect(framesFor(0, 3, 24)).toBe(73); // 72 raw
    expect(framesFor(0, 73 / 24, 24)).toBe(73); // already 8n+1 stays
    expect(framesFor(0, 0.01, 24)).toBe(9); // never below 9
    expect(() => framesFor(5, 5, 24)).toThrow(/no duration/);
    for (const [a, b] of [[0, 6.9], [6.9, 17.34], [17.34, 25.16], [25.16, 32]]) expect(is8n1(framesFor(a!, b!, 24))).toBe(true);
  });

  test("floor8n1 / is8n1", () => {
    expect(floor8n1(84)).toBe(81);
    expect(floor8n1(81)).toBe(81);
    expect(floor8n1(8)).toBe(0);
    expect(is8n1(1)).toBe(false);
    expect(is8n1(193)).toBe(true);
    expect(is8n1(192)).toBe(false);
  });

  test("cutPlan applies the sync offset and clamps to the take", () => {
    const plain = cutPlan({ t0: 17.34, t1: 25.16, fps: 24, offset: 0, clipFrames: 768 });
    expect(plain).toMatchObject({ startFrame: 416, frames: 193, wanted: 193, clamped: false });
    const shifted = cutPlan({ t0: 17.34, t1: 25.16, fps: 24, offset: 0.5, clipFrames: 768 });
    expect(shifted.startFrame).toBe(428); // (17.34 + 0.5) * 24 = 428.16
    const late = cutPlan({ t0: 25.16, t1: 32, fps: 24, offset: 1.25, clipFrames: 768 });
    expect(late.startFrame).toBe(634); // 26.41 * 24 = 633.84
    expect(late.frames).toBe(129); // 768 - 634 = 134 available -> 129
    expect(late.clamped).toBe(true);
    expect(late.note).toMatch(/take ends early/);
    const tooShort = cutPlan({ t0: 30, t1: 32, fps: 24, offset: 2, clipFrames: 770 });
    expect(tooShort.frames).toBe(0);
    expect(tooShort.note).toMatch(/too short/);
    const early = cutPlan({ t0: 0, t1: 4, fps: 24, offset: -0.5 });
    expect(early.startFrame).toBe(0);
    expect(early.clamped).toBe(true);
    expect(early.frames).toBe(97);
  });

  test("tileFilter at 1280x704 is the one in docs/research/orchestration.md §3", () => {
    const f = tileFilter(1280, 704);
    expect(f).toContain("[a]crop=560:704:(iw-560)/2:0[lead]");
    expect(f).toContain("[b]scale=768:-2,crop=360:ih:(iw-360)/2:0,hflip,pad=360:704:0:704-ih-60[l]");
    expect(f).toContain("[c]scale=768:-2,crop=360:ih:(iw-360)/2:0,pad=360:704:0:704-ih-60[r]");
    expect(f.endsWith("[l][lead][r]hstack=3")).toBe(true);
  });

  test("takes sort naturally", () => {
    expect(["hero-10.mp4", "hero-2.mp4", "hero-1-2.mp4", "hero-1.mp4"].sort(naturalCompare)).toEqual(["hero-1.mp4", "hero-1-2.mp4", "hero-2.mp4", "hero-10.mp4"]);
  });
});

// ---------------------------------------------------------------- hashing + caching

describe("content hash + cache", () => {
  const parts: HashParts = { clip: "c1", frame: null, slots: "s1", graph: "g1" };
  test("sceneHash is stable and sensitive to every part", () => {
    expect(sceneHash(parts)).toBe(sceneHash({ ...parts }));
    expect(sceneHash(parts)).toHaveLength(64);
    for (const k of Object.keys(parts) as (keyof HashParts)[]) expect(sceneHash({ ...parts, [k]: "x" })).not.toBe(sceneHash(parts));
  });
  test("slotsFingerprint ignores upload names and key order", () => {
    const a = slotsFingerprint({ source_video: "a.mp4", seed: 7, prompt: "p", first_frame: "f.png" });
    const b = slotsFingerprint({ prompt: "p", first_frame: "zzz.png", seed: 7, source_video: "other.mp4" });
    expect(a).toBe(b);
    expect(slotsFingerprint({ seed: 8, prompt: "p" })).not.toBe(a);
  });
  test("staleReason explains the verdict", () => {
    const prev = { hash: sceneHash(parts), parts, output: "outputs/scenes/s1.mp4", at: "t" };
    expect(staleReason(undefined, parts, false)).toBe("never rendered");
    expect(staleReason(prev, parts, false)).toMatch(/output missing/);
    expect(staleReason(prev, parts, true)).toBeNull();
    expect(staleReason(prev, { ...parts, slots: "s2" }, true)).toBe("prompt, seed or settings changed");
    expect(staleReason(prev, { ...parts, clip: "c2", frame: "f" }, true)).toBe("the cut clip changed (take, offset or cut); the look frame changed");
  });
});

// ---------------------------------------------------------------- doctor

describe("doctor", () => {
  test("parseVersion reads --version banners", () => {
    expect(parseVersion("ffmpeg version 9.0.1 Copyright (c) 2000-2026")).toMatchObject({ major: 9, minor: 0, patch: 1 });
    expect(parseVersion("uv 0.9.22 (82a6a66b8 2026-01-06)")).toMatchObject({ major: 0, minor: 9, patch: 22 });
    expect(parseVersion("Python 3.14.7")).toMatchObject({ major: 3, minor: 14 });
    expect(parseVersion("1.4.0\n")).toMatchObject({ major: 1, minor: 4, patch: 0 });
    expect(parseVersion("no digits here")).toBeNull();
  });

  const banner: Record<string, string> = {
    ffmpeg: "ffmpeg version 9.0.1 Copyright",
    ffprobe: "ffprobe version 9.0.1 Copyright",
    bun: "1.4.0",
    uv: "uv 0.9.22",
    python3: "Python 3.14.7",
  };
  const ok = (stdout: string): RunResult => ({ ok: true, code: 0, stdout, stderr: "", timedOut: false });
  const deps = (over: Partial<DoctorDeps> = {}, banners = banner): DoctorDeps => ({
    run: (cmd) => (cmd[0] === "uv" && cmd[1] === "run" ? ok("") : banners[cmd[0]!] !== undefined ? ok(banners[cmd[0]!]!) : { ok: false, code: null, stdout: "", stderr: "", timedOut: false, error: "ENOENT" }),
    exists: () => true,
    list: () => ["hero-1.mp4", "hero-1.json"],
    reach: async () => ({ ok: true, status: 200 }),
    env: { COMFY_API_KEY: "comfyui-test" },
    home: "/home/test",
    ...over,
  });
  const m: Manifest = defaultManifest();

  test("a healthy machine has no required failures", async () => {
    const checks = await runDoctor("/repo", m, deps());
    expect(requiredFailures(checks)).toEqual([]);
    expect(checks.every((c) => c.ok)).toBe(true);
    expect(checks.find((c) => c.id === "ffmpeg")?.detail).toBe("9.0.1");
    expect(checks.find((c) => c.id === "blender")?.detail).toBe(BLENDER);
    expect(checks.find((c) => c.id === "hero-take")?.detail).toBe("clips/hero-1.mp4");
    const noTake = await runDoctor("/repo", m, deps({ list: () => [] }));
    expect(noTake.find((c) => c.id === "hero-take")).toMatchObject({ ok: false, required: false, fix: "popstar open recorder   # record hero-1 with the coach" });
  });

  test("--fast skips the beat_this import", async () => {
    let ran = false;
    const checks = await runDoctor("/repo", m, deps({ run: (cmd) => ((ran = ran || cmd.includes("beat-this")), ok(banner[cmd[0]!] ?? "")) }), { fast: true });
    expect(ran).toBe(false);
    expect(checks.find((c) => c.id === "beat_this")).toMatchObject({ ok: true, skipped: true });
  });

  test("old ffmpeg and a missing uv fail with exact fixes", async () => {
    const checks = await runDoctor("/repo", m, deps({}, { ...banner, ffmpeg: "ffmpeg version 6.1.1", uv: undefined as unknown as string }));
    const ff = checks.find((c) => c.id === "ffmpeg")!;
    expect(ff.ok).toBe(false);
    expect(ff.detail).toBe("6.1.1 (need ≥ 7)");
    expect(ff.fix).toBe("brew install ffmpeg");
    const uv = checks.find((c) => c.id === "uv")!;
    expect(uv.ok).toBe(false);
    expect(uv.detail).toBe("not installed");
    expect(uv.fix).toBe("brew install uv");
    expect(requiredFailures(checks).map((c) => c.id)).toEqual(["ffmpeg", "uv"]);
  });

  test("optional items (api key, whisper cache, fonts, blender) never make it fail; offline does", async () => {
    const whisper = join("/home/test", ".cache", "huggingface", "hub", "models--mlx-community--whisper-small-mlx");
    const checks = await runDoctor("/repo", m, deps({ env: {}, exists: (p) => p !== whisper && p !== BLENDER && !p.endsWith("Menlo.ttc") }));
    expect(requiredFailures(checks)).toEqual([]);
    expect(checks.find((c) => c.id === "comfy-api-key")).toMatchObject({ ok: false, required: false });
    expect(checks.find((c) => c.id === "whisper")?.fix).toContain("mlx_whisper");
    expect(checks.find((c) => c.id === "blender")?.fix).toBe("brew install --cask blender");
    expect(checks.find((c) => c.id === "font:Menlo.ttc")?.ok).toBe(false);
    const offline = await runDoctor("/repo", m, deps({ reach: async () => ({ ok: false, error: "ENOTFOUND" }) }));
    expect(requiredFailures(offline).map((c) => c.id)).toEqual(["comfy-cloud"]);
    const noManifest = await runDoctor("/repo", m, deps({ exists: (p) => !p.endsWith("popstar.json") }));
    expect(requiredFailures(noManifest).map((c) => c.id)).toEqual(["manifest"]);
    expect(noManifest.find((c) => c.id === "manifest")?.fix).toContain("popstar new in-the-loop-devagar");
  });
});

// ---------------------------------------------------------------- small helpers

describe("helpers", () => {
  test("parseArgs: command, list flags, values, booleans, rest", () => {
    const a = parseArgs(["render", "--scene", "s1", "s2", "--dry-run", "--take", "hero-1", "--uploaded", "a=b", "--uploaded", "c=d", "--", "x"]);
    expect(a.cmd).toBe("render");
    expect(a.flags.scene).toEqual(["s1", "s2"]);
    expect(a.flags["dry-run"]).toBe(true);
    expect(a.flags.take).toBe("hero-1");
    expect(a.flags.uploaded).toEqual(["a=b", "c=d"]);
    expect(a.rest).toEqual(["x"]);
    expect(parseArgs(["--json"]).cmd).toBe("status");
    expect(parseArgs(["--json"]).flags.json).toBe(true);
    expect(parseArgs(["status", "--song", "abc"]).flags.song).toBe("abc");
    expect(parseArgs(["qa", "a.mp4", "b.mp4"]).pos).toEqual(["a.mp4", "b.mp4"]);
    expect(parseArgs(["new", "slug", "--song=songs/x.mp3"]).flags.song).toBe("songs/x.mp3");
  });
  test("urlIn finds the URL a server prints", () => {
    expect(urlIn("recorder ready: http://localhost:57990/")).toBe("http://localhost:57990/");
    expect(urlIn("\n  Reference Master → http://127.0.0.1:5010/\n  (Ctrl+C to stop)")).toBe("http://127.0.0.1:5010/");
    expect(urlIn("starting…")).toBeNull();
  });
  test("parseWith lifts --python and --with deps from a script header", () => {
    expect(parseWith("# Usage:\n#   uv run --with librosa --with soundfile python -I x.py\n")).toEqual(["--with", "librosa", "--with", "soundfile"]);
    const qaHeader =
      'Usage:\n  uv run --python 3.12 --with "opencv-python-headless<5" --with numpy --with pillow \\\n' +
      "      python -I tools/qa_render.py <source.mp4|png> <render.mp4|png> \\\n      [--out build/qa/<name>] [--json]\n\n" +
      "  MediaPipe is optional: add `--with mediapipe` and `--detector mediapipe`.\n";
    expect(parseWith(qaHeader)).toEqual(["--python", "3.12", "--with", "opencv-python-headless<5", "--with", "numpy", "--with", "pillow"]);
    expect(parseWith("no deps")).toEqual([]);
  });
});

// ---------------------------------------------------------------- end to end: a synthetic take through cut + compile (real ffmpeg)

const haveFfmpeg = Bun.spawnSync(["ffmpeg", "-version"]).exitCode === 0;

describe.skipIf(!haveFfmpeg)("render end to end (ffmpeg)", () => {
  let root = "";
  let m: Manifest;
  const quiet = { log: () => {}, clipboard: () => true };
  const shots = (seed1 = 11) => ({
    version: 1,
    song: "songs/test.mp3",
    timing: "songs/test.timing.json",
    identity_lock: "a performer",
    negative: "ugly",
    takes: [{ id: "hero-1" }],
    scenes: [
      { id: "s1", title: "one", t0: 0, t1: 4, section: "intro", seed: seed1, set: "", light: "", camera: { move: "static" }, emotion: "", prompt: "a performer sings", look: "s1.lead", hero_frame: { strength: 0.7 } },
      { id: "s2", title: "two", t0: 4, t1: 8, section: "chorus", seed: 12, set: "", light: "", camera: { move: "static" }, emotion: "", prompt: "a performer sings again", harmony: true, clones: { mode: "tile_source" } },
      { id: "s3", title: "three", t0: 8, t1: 11.6, section: "chorus", seed: 13, set: "", light: "", camera: { move: "static" }, emotion: "", prompt: "no lock here" },
    ],
  });

  beforeAll(() => {
    root = mkdtempSync(join(tmpdir(), "popstar-e2e-"));
    for (const d of ["clips", "shots", "songs", "reference-master/renders/s1.lead", "outputs/scenes"]) mkdirSync(join(root, d), { recursive: true });
    m = defaultManifest("test", { song: "songs/test.mp3" });
    writeManifest(root, m);
    writeFileSync(join(root, "clips", "hero-1.json"), JSON.stringify({ song: "songs/test.mp3", song_offset_s: 0.5 }));
    writeFileSync(join(root, "reference-master", "looks.json"), JSON.stringify({ looks: [{ id: "s1.lead", renders: { wardrobe: "reference-master/renders/s1.lead/wardrobe.png", expression: null } }] }));
    writeFileSync(join(root, "shots", "test.json"), JSON.stringify(shots()));
  });
  afterAll(() => {
    if (root) rmSync(root, { recursive: true, force: true });
  });

  // The fixture is a test of its own (long timeout): a 12 s synthetic take at 24 fps (288 frames), 1280x720 like the recorder's output.
  test("fixture: a synthetic take and a look render", () => {
    const take = Bun.spawnSync(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24", "-t", "12", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", join(root, "clips", "hero-1.mp4")]);
    expect(take.exitCode).toBe(0);
    const png = Bun.spawnSync(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=orange:size=1280x704", "-frames:v", "1", join(root, "reference-master", "renders", "s1.lead", "wardrobe.png")]);
    expect(png.exitCode).toBe(0);
    expect(probe(join(root, "clips", "hero-1.mp4"))).toMatchObject({ frames: 288, fps: 24 });
  }, 120_000);

  const manifest = () => JSON.parse(readFileSync(join(root, "build", "render-manifest.json"), "utf8")) as RenderManifest;
  const state = () => JSON.parse(readFileSync(join(root, "build", "state.json"), "utf8")) as State;
  const graph = (id: string) => JSON.parse(readFileSync(join(root, "build", "scenes", `${id}.json`), "utf8")) as Record<string, { inputs: Record<string, unknown> }>;

  test("dry run plans without writing", async () => {
    const lines: string[] = [];
    expect(await render(root, m, { dryRun: true, log: (s) => lines.push(s) })).toBe(0);
    expect(existsSync(join(root, "build"))).toBe(false);
    const text = lines.join("\n");
    expect(text).toContain("sync offset +0.5s from clips/hero-1.json");
    expect(text).toContain("dry run");
  }, 60_000);

  test("cuts 8n+1 frames at the offset, tiles the harmony scene, compiles the graphs", async () => {
    expect(await render(root, m, quiet)).toBe(0);
    const man = manifest();
    expect(man.take).toMatchObject({ id: "hero-1", sync_offset_s: 0.5 });
    const [s1, s2, s3] = man.scenes;
    expect(s1).toMatchObject({ start_frame: 12, frames: 97, tiled: false, state: "stale", stale_because: "never rendered" });
    expect(s2).toMatchObject({ start_frame: 108, frames: 97, tiled: true });
    expect(s3).toMatchObject({ start_frame: 204, frames: 81, wanted_frames: 89 }); // 288 - 204 = 84 left -> 81
    expect(s3!.warnings.join(" ")).toMatch(/take ends early/);
    expect(s3!.warnings.join(" ")).toMatch(/identity lock/);
    for (const s of [s1, s2, s3]) {
      const pr = probe(join(root, s!.clip))!;
      expect(pr.frames).toBe(s!.frames);
      expect([pr.width, pr.height]).toEqual([1280, 704]);
      expect(is8n1(pr.frames)).toBe(true);
    }
    // the compiled pose-restyle graphs
    const g1 = graph("s1");
    expect(g1["692"]!.inputs.duration).toBe(0); // trim_duration=0 is mandatory
    expect(g1["199"]!.inputs.file).toBe("s1.mp4"); // placeholder until the upload is recorded
    expect(g1["129:198"]!.inputs.bypass).toBe(false); // use_first_frame
    expect(g1["129:198"]!.inputs.strength).toBe(0.7);
    expect(g1["200"]!.inputs.image).toBe("wardrobe.png");
    expect(g1["129:704"]!.inputs.seed).toBe(11);
    expect(g1["129:211"]!.inputs.on_false).toBe("a performer sings");
    expect(g1["129:112"]!.inputs.text).toBe("ugly");
    expect(g1["68"]!.inputs.filename_prefix).toBe("video/test/s1");
    const g2 = graph("s2");
    expect(g2["129:198"]!.inputs.bypass).toBe(true); // no look for s2
    expect(s1!.first_frame).toMatchObject({ look: "s1.lead", strength: 0.7 });
    expect(s2!.first_frame).toBeNull();
    expect(man.pending_uploads.map((u) => u.file).sort()).toEqual(["build/scenes/s1.mp4", "build/scenes/s2.mp4", "build/scenes/s3.mp4", "reference-master/renders/s1.lead/wardrobe.png"]);
    expect(state().cuts.s1).toBeDefined();
  }, 300_000); // generous: four ffmpeg encodes, and the machine may be rendering previs at the same time

  test("recorded uploads land in the graph; a marked render is fresh until its inputs change", async () => {
    expect(await render(root, m, { ...quiet, uploaded: ["build/scenes/s1.mp4=abc123.mp4", "reference-master/renders/s1.lead/wardrobe.png=def456.png"] })).toBe(0);
    const g1 = graph("s1");
    expect(g1["199"]!.inputs.file).toBe("abc123.mp4");
    expect(g1["200"]!.inputs.image).toBe("def456.png");
    expect(manifest().scenes[0]!.uploads.every((u) => u.name)).toBe(true);
    // the output "arrives"
    writeFileSync(join(root, "outputs", "scenes", "s1.mp4"), readFileSync(join(root, "build", "scenes", "s1.mp4")));
    expect(await render(root, m, { ...quiet, mark: { id: "s1", job: "job-1" } })).toBe(0);
    expect(state().renders.s1).toMatchObject({ job_id: "job-1", output: "outputs/scenes/s1.mp4" });
    expect(await render(root, m, quiet)).toBe(0);
    let man = manifest();
    expect(man.scenes.map((s) => s.state)).toEqual(["fresh", "stale", "stale"]);
    // --submit lists only the stale scenes and copies the contract
    let copied = "";
    expect(await render(root, m, { log: () => {}, submit: true, clipboard: (t) => ((copied = t), true) })).toBe(0);
    expect(copied).toContain("upload_file");
    expect(copied).toContain("--mark s2");
    expect(copied).toContain("--mark s3");
    expect(copied).not.toContain("--mark s1");
    expect(copied).toContain("build/scenes/s2.json");
    // a seed change makes s1 stale again, and says why
    writeFileSync(join(root, "shots", "test.json"), JSON.stringify(shots(99)));
    expect(await render(root, m, quiet)).toBe(0);
    man = manifest();
    expect(man.scenes[0]).toMatchObject({ state: "stale", stale_because: "prompt, seed or settings changed" });
    expect(graph("s1")["129:704"]!.inputs.seed).toBe(99);
    // --scene filters, --force re-renders
    expect(await render(root, m, { ...quiet, scenes: ["s2"], force: true })).toBe(0);
    man = manifest();
    expect(man.scenes.map((s) => s.id)).toEqual(["s2"]);
    expect(man.scenes[0]!.stale_because).toBe("forced (--force)");
    expect(await render(root, m, { ...quiet, scenes: ["nope"] })).toBe(2);
  }, 300_000);
});
