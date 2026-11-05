// popstar.json: the project manifest every tool reads instead of hardcoding paths (docs/architecture.md, R3).
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";

export type Manifest = {
  version: 1;
  slug: string;
  title: string;
  song: { file: string; recipe_md: string };
  performer: { name: string; identity_lock: string; negative: string };
  palette: string[];
  paths: {
    timing: string;
    shots: string;
    looks: string;
    clips: string;
    outputs: string;
    previs: string;
    storyboards: string;
  };
  fps: number;
  size: [number, number];
};

export const MANIFEST_FILE = "popstar.json";
export const DEFAULT_SLUG = "in-the-loop-devagar";

/** Where this copy of the tools lives (tools/popstar/ -> tools/ -> here): the repo root, or the plugin root when the
 *  tools run from the popstar Claude Code plugin (plugins/popstar/). Bundled files live here; project data never does. */
export const BUNDLE_ROOT = resolve(import.meta.dir, "..", "..");

/** The nearest directory at or above `from` that holds popstar.json, else null. */
export function findProjectRoot(from: string): string | null {
  let dir = resolve(from);
  for (;;) {
    if (existsSync(join(dir, MANIFEST_FILE))) return dir;
    const up = dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
}

/** The project root: the directory that holds popstar.json and the song, shots, looks, clips and outputs (R3).
 *  In order: `explicit` (--project), POPSTAR_ROOT, the nearest popstar.json at or above the working directory, the
 *  bundle root when it is itself a project (a checkout of the repo), else the working directory. `preferCwd` skips
 *  the bundle fallback so that `popstar new` creates the project where it is run, never inside the repo or plugin. */
export function resolveRoot(explicit?: string, preferCwd = false): string {
  if (explicit) return resolve(explicit);
  if (process.env.POPSTAR_ROOT) return resolve(process.env.POPSTAR_ROOT);
  const cwd = process.cwd();
  const found = findProjectRoot(cwd);
  if (found) return found;
  if (!preferCwd && existsSync(join(BUNDLE_ROOT, MANIFEST_FILE))) return BUNDLE_ROOT;
  return cwd;
}

/** The project root for this process (resolveRoot with no overrides), for callers that want a constant. */
export const REPO_ROOT = resolveRoot();

export const IDENTITY_LOCK_DEFAULT =
  "a young Black woman with rich deep brown skin, warm undertones and a luminous dewy sheen, copper-red hair pulled back, small white earbuds";

/** The pinned pose-restyle negative (workflows/slots.json, slot `negative`), as a literal fallback. */
export const NEGATIVE_FALLBACK =
  "blurry, out of focus, low contrast, washed out colors, excessive noise, flickering, distorted proportions, unnatural skin tones, lightened skin, pale skin, incorrect ethnicity, deformed facial features, missing facial features, extra limbs, disfigured hands, wrong hand count, jittery movement, photorealistic, live-action footage, AI artifacts";

export const PALETTE_DEFAULT = ["#7a4a2a", "#d9622b", "#ff9f43", "#ffb347"];

/** The pose-restyle negative from workflows/slots.json when it can be read, else the literal above. */
export function pinnedNegative(root: string): string {
  try {
    const slots = JSON.parse(readFileSync(join(root, "workflows", "slots.json"), "utf8")) as {
      "pose-restyle"?: { slots?: { negative?: { default?: unknown } } };
    };
    const v = slots["pose-restyle"]?.slots?.negative?.default;
    if (typeof v === "string" && v.trim()) return v;
  } catch {
    /* fall through */
  }
  return NEGATIVE_FALLBACK;
}

export type NewProjectOptions = {
  song?: string;
  title?: string;
  performer?: string;
  identity_lock?: string;
  negative?: string;
  palette?: string[];
  fps?: number;
  size?: [number, number];
};

/** A manifest for `slug` from the repo's conventions. With no options it describes the current project. */
export function defaultManifest(slug = DEFAULT_SLUG, o: NewProjectOptions = {}): Manifest {
  const title = o.title ?? (slug === DEFAULT_SLUG ? "In the Loop (Devagar)" : titleFromSlug(slug));
  return {
    version: 1,
    slug,
    title,
    song: { file: o.song ?? `songs/${slug}.mp3`, recipe_md: `songs/${slug}.md` },
    performer: {
      name: o.performer ?? "Naledi",
      identity_lock: o.identity_lock ?? IDENTITY_LOCK_DEFAULT,
      negative: o.negative ?? NEGATIVE_FALLBACK,
    },
    palette: o.palette ?? [...PALETTE_DEFAULT],
    paths: {
      timing: `songs/${slug}.timing.json`,
      shots: `shots/${slug}.json`,
      looks: "reference-master/looks.json",
      clips: "clips",
      outputs: "outputs",
      previs: "previs",
      storyboards: "storyboards",
    },
    fps: o.fps ?? 24,
    size: o.size ?? [1280, 704],
  };
}

export const titleFromSlug = (slug: string): string =>
  slug
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => w[0]!.toUpperCase() + w.slice(1))
    .join(" ");

export const slugOk = (slug: string): boolean => /^[a-z0-9][a-z0-9-]*$/.test(slug);

/** Problems with a parsed manifest; empty means usable. */
export function validateManifest(x: unknown): string[] {
  const errs: string[] = [];
  const isObj = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
  const str = (v: unknown) => typeof v === "string" && v.length > 0;
  if (!isObj(x)) return ["popstar.json must be a JSON object"];
  if (x.version !== 1) errs.push("version must be 1");
  if (!str(x.slug) || !slugOk(x.slug as string)) errs.push("slug must be lowercase letters, digits and dashes");
  if (!str(x.title)) errs.push("title must be a non-empty string");
  if (!isObj(x.song) || !str(x.song.file) || !str(x.song.recipe_md)) errs.push("song needs {file, recipe_md}");
  if (!isObj(x.performer) || !str(x.performer.name) || !str(x.performer.identity_lock) || typeof x.performer.negative !== "string")
    errs.push("performer needs {name, identity_lock, negative}");
  if (!Array.isArray(x.palette) || !x.palette.every((c) => typeof c === "string" && /^#[0-9a-f]{3,8}$/i.test(c)))
    errs.push("palette must be an array of hex colors");
  const keys: (keyof Manifest["paths"])[] = ["timing", "shots", "looks", "clips", "outputs", "previs", "storyboards"];
  if (!isObj(x.paths)) errs.push("paths must be an object");
  else for (const k of keys) if (!str(x.paths[k])) errs.push(`paths.${k} must be a repo-relative path`);
  if (typeof x.fps !== "number" || !(x.fps > 0)) errs.push("fps must be a positive number");
  if (!Array.isArray(x.size) || x.size.length !== 2 || !x.size.every((n) => Number.isInteger(n) && n > 0))
    errs.push("size must be [width, height]");
  return errs;
}

export type LoadedManifest = { manifest: Manifest; source: "file" | "defaults"; warning?: string; file: string };

/** Read <root>/popstar.json; without one, fall back to the conventions for the default song and say so. */
export function loadManifest(root: string): LoadedManifest {
  const file = join(root, MANIFEST_FILE);
  if (!existsSync(file)) {
    return {
      manifest: defaultManifest(),
      source: "defaults",
      warning: `no ${MANIFEST_FILE} in ${root}: using the built-in defaults for ${DEFAULT_SLUG}. Create one with: popstar new <slug> --song <mp3>`,
      file,
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(file, "utf8"));
  } catch (e) {
    throw new Error(`${MANIFEST_FILE} is not valid JSON: ${(e as Error).message}`);
  }
  const errs = validateManifest(parsed);
  if (errs.length) throw new Error(`${MANIFEST_FILE} is invalid:\n  ${errs.join("\n  ")}`);
  return { manifest: parsed as Manifest, source: "file", file };
}

export function writeManifest(root: string, m: Manifest): string {
  const file = join(root, MANIFEST_FILE);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(m, null, 2)}\n`);
  return file;
}

/** Repo-relative -> absolute (absolute paths pass through). */
export const abs = (root: string, rel: string): string => (isAbsolute(rel) ? rel : join(root, rel));
