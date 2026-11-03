// popstar doctor: environment drift (docs/architecture.md, R7). Every ✗ comes with the exact fix command.
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { BAD, OK, paint, table } from "./ansi.ts";
import { abs, type Manifest } from "./project.ts";
import { run, shellLine, type RunResult } from "./proc.ts";

export type Group = "tools" | "models" | "cloud" | "fonts" | "data";
export type Check = {
  id: string;
  group: Group;
  label: string;
  required: boolean;
  ok: boolean;
  detail: string;
  fix?: string;
  skipped?: boolean;
};

export type DoctorDeps = {
  run: (cmd: string[], timeoutMs?: number) => RunResult;
  exists: (path: string) => boolean;
  reach: (url: string, timeoutMs: number) => Promise<{ ok: boolean; status?: number; error?: string }>;
  env: Record<string, string | undefined>;
  home: string;
};

export const realDeps = (): DoctorDeps => ({
  run: (cmd, timeoutMs) => run(cmd, { timeoutMs }),
  exists: existsSync,
  reach: async (url, timeoutMs) => {
    try {
      const r = await fetch(url, { method: "GET", redirect: "manual", signal: AbortSignal.timeout(timeoutMs) });
      return { ok: true, status: r.status };
    } catch (e) {
      return { ok: false, error: (e as Error).message };
    }
  },
  env: process.env,
  home: homedir(),
});

export type Version = { major: number; minor: number; patch: number; raw: string };

/** The first x.y[.z] in a `--version` banner ("ffmpeg version 9.0.1 Copyright" -> 9.0.1). */
export function parseVersion(text: string): Version | null {
  const m = /(\d+)\.(\d+)(?:\.(\d+))?/.exec(text);
  if (!m) return null;
  return { major: Number(m[1]), minor: Number(m[2]), patch: Number(m[3] ?? 0), raw: m[0] };
}

export const BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender";
export const WHISPER_CACHE = ["hub", "models--mlx-community--whisper-small-mlx"];
export const FONTS = [
  { name: "Menlo.ttc", paths: ["/System/Library/Fonts/Menlo.ttc"] },
  { name: "Georgia Italic.ttf", paths: ["/System/Library/Fonts/Supplemental/Georgia Italic.ttf", "/Library/Fonts/Georgia Italic.ttf"] },
];
export const COMFY_URL = "https://cloud.comfy.org/";
export const BEAT_THIS_CMD = ["uv", "run", "--python", "3.12", "--with", "beat-this", "python", "-c", "import beat_this"];

const versionCheck = (
  d: DoctorDeps,
  id: string,
  cmd: string[],
  min: number | null,
  fix: string,
  required = true,
): Check => {
  const r = d.run(cmd, 20_000);
  const v = r.ok ? parseVersion(`${r.stdout}\n${r.stderr}`) : null;
  if (!r.ok || !v) {
    const why = r.error ? "not installed" : r.timedOut ? "timed out" : `exit ${r.code}`;
    return { id, group: "tools", label: id, required, ok: false, detail: why, fix };
  }
  const ok = min === null || v.major >= min;
  return {
    id,
    group: "tools",
    label: id,
    required,
    ok,
    detail: ok ? v.raw : `${v.raw} (need ≥ ${min})`,
    fix: ok ? undefined : fix,
  };
};

export type DoctorOptions = { fast?: boolean };

/** Run every check. Nothing here writes; `deps` can be mocked in tests. */
export async function runDoctor(root: string, m: Manifest, d: DoctorDeps, o: DoctorOptions = {}): Promise<Check[]> {
  const checks: Check[] = [];

  // tools
  checks.push(versionCheck(d, "ffmpeg", ["ffmpeg", "-version"], 7, "brew install ffmpeg"));
  checks.push(versionCheck(d, "ffprobe", ["ffprobe", "-version"], 7, "brew install ffmpeg"));
  checks.push(versionCheck(d, "bun", ["bun", "--version"], null, "curl -fsSL https://bun.sh/install | bash"));
  checks.push(versionCheck(d, "uv", ["uv", "--version"], null, "brew install uv"));
  checks.push(versionCheck(d, "python3", ["python3", "--version"], 3, "brew install python@3.12"));
  checks.push({
    id: "blender",
    group: "tools",
    label: "Blender",
    required: false,
    ok: d.exists(BLENDER),
    detail: d.exists(BLENDER) ? BLENDER : `not at ${BLENDER} (previs stage)`,
    fix: d.exists(BLENDER) ? undefined : "brew install --cask blender",
  });

  // models
  if (o.fast) {
    checks.push({ id: "beat_this", group: "models", label: "beat_this", required: false, ok: true, skipped: true, detail: "skipped (--fast)" });
  } else {
    const r = d.run(BEAT_THIS_CMD, 240_000);
    checks.push({
      id: "beat_this",
      group: "models",
      label: "beat_this",
      required: false,
      ok: r.ok,
      detail: r.ok ? "imports via uv (python 3.12)" : r.timedOut ? "timed out (first run downloads torch; run the fix once)" : (r.stderr.trim().split("\n").pop() ?? "import failed"),
      fix: r.ok ? undefined : shellLine(BEAT_THIS_CMD),
    });
  }
  const whisper = join(d.home, ".cache", "huggingface", ...WHISPER_CACHE);
  const whisperOk = d.exists(whisper);
  checks.push({
    id: "whisper",
    group: "models",
    label: "mlx-whisper small",
    required: false,
    ok: whisperOk,
    detail: whisperOk ? "cached in ~/.cache/huggingface" : "not cached (timing stage downloads it)",
    fix: whisperOk
      ? undefined
      : `uv run --with mlx-whisper python -c "import mlx_whisper; mlx_whisper.transcribe('${m.song.file}', path_or_hf_repo='mlx-community/whisper-small-mlx')"`,
  });

  // cloud
  const reach = await d.reach(COMFY_URL, 8_000);
  checks.push({
    id: "comfy-cloud",
    group: "cloud",
    label: "Comfy Cloud",
    required: true,
    ok: reach.ok,
    detail: reach.ok ? `${COMFY_URL} answers (HTTP ${reach.status})` : `unreachable: ${reach.error ?? "no response"}`,
    fix: reach.ok ? undefined : "check the network (VPN? captive portal?), then: curl -I https://cloud.comfy.org/",
  });
  const key = d.env.COMFY_API_KEY;
  checks.push({
    id: "comfy-api-key",
    group: "cloud",
    label: "COMFY_API_KEY",
    required: false,
    ok: !!key,
    detail: key ? `set (${key.slice(0, 8)}…)` : "not set: fine, the comfy-cloud MCP signs in with OAuth; only the local stdio MCP needs a key",
    fix: key ? undefined : "export COMFY_API_KEY=comfyui-…   # optional; mint one at https://cloud.comfy.org (workspace API keys)",
  });

  // fonts (stitch captions)
  for (const f of FONTS) {
    const found = f.paths.find((p) => d.exists(p));
    checks.push({
      id: `font:${f.name}`,
      group: "fonts",
      label: f.name,
      required: false,
      ok: !!found,
      detail: found ?? `not found in ${f.paths.join(" or ")} (stitch captions)`,
      fix: found ? undefined : `open -a "Font Book" && ls /System/Library/Fonts/Supplemental | grep -i '${f.name.split(" ")[0]}'   # ships with macOS; or set STITCH_FONT`,
    });
  }

  // data
  const data: { id: string; label: string; rel: string; required: boolean; fix: string }[] = [
    { id: "manifest", label: "popstar.json", rel: "popstar.json", required: true, fix: `popstar new ${m.slug} --song ${m.song.file} --title '${m.title}'` },
    { id: "song", label: "song", rel: m.song.file, required: true, fix: "stage 0: make the song on Comfy Cloud (ACE-Step 1.5), then lock it in the recipe .md" },
    { id: "recipe", label: "song recipe", rel: m.song.recipe_md, required: false, fix: `write ${m.song.recipe_md} with '## Lyrics' and the word LOCKED` },
    { id: "timing", label: "timing", rel: m.paths.timing, required: false, fix: "popstar timing" },
    { id: "shots", label: "shots", rel: m.paths.shots, required: false, fix: "ask Claude to direct it (skill rnb-director): it writes scenes[] + takes[]" },
    { id: "looks", label: "looks", rel: m.paths.looks, required: false, fix: "popstar open looks   # Reference Master designs the looks" },
  ];
  for (const x of data) {
    const ok = d.exists(abs(root, x.rel));
    checks.push({ id: x.id, group: "data", label: x.label, required: x.required, ok, detail: ok ? x.rel : `${x.rel} missing`, fix: ok ? undefined : x.fix });
  }
  const hero = heroClip(root, m, d.exists);
  checks.push({
    id: "hero-take",
    group: "data",
    label: "hero take",
    required: false,
    ok: !!hero,
    detail: hero ?? `no ${m.paths.clips}/hero-*.mp4 yet`,
    fix: hero ? undefined : "popstar open recorder   # record hero-1 with the coach",
  });

  return checks;
}

function heroClip(root: string, m: Manifest, exists: (p: string) => boolean): string | null {
  const dir = abs(root, m.paths.clips);
  if (!exists(dir)) return null;
  try {
    const names = Array.from(new Bun.Glob("hero-*.mp4").scanSync(dir)).sort();
    return names.length ? `${m.paths.clips}/${names[0]}` : null;
  } catch {
    return null;
  }
}

export const requiredFailures = (checks: Check[]): Check[] => checks.filter((c) => c.required && !c.ok);

export function printDoctor(checks: Check[], m: Manifest, log: (s: string) => void = console.log): void {
  const p = paint;
  log(p.amber(`\n  POP-STAR DOCTOR · ${m.slug}\n`));
  const groups: Record<Group, string> = { tools: "tools", models: "models", cloud: "cloud", fonts: "fonts (stitch)", data: "project data" };
  for (const [g, title] of Object.entries(groups) as [Group, string][]) {
    const rows = checks.filter((c) => c.group === g);
    if (!rows.length) continue;
    log(`  ${p.dim(title)}`);
    const cells = rows.map((c) => {
      const mark = c.ok ? p.green(OK) : c.required ? p.red(BAD) : p.amber(BAD);
      const label = c.ok ? c.label : c.required ? p.bold(c.label) : c.label;
      const detail = c.ok ? (c.skipped ? p.dim(c.detail) : c.detail) : p.dim(c.detail) + (c.required ? "" : p.dim(" · optional"));
      return [mark, label, detail];
    });
    for (const line of table(cells, "  ")) log(line);
    for (const c of rows) if (!c.ok && c.fix) log(`      ${p.amber("fix:")} ${c.fix}`);
    log("");
  }
  const bad = requiredFailures(checks);
  const optionalBad = checks.filter((c) => !c.required && !c.ok).length;
  if (bad.length) log(`  ${p.red(`${bad.length} required check(s) failed:`)} ${bad.map((c) => c.label).join(", ")}\n`);
  else log(`  ${p.green("ready.")} ${optionalBad ? p.dim(`${optionalBad} optional item(s) to fix when their stage comes.`) : p.dim("every check passed.")}\n`);
}
