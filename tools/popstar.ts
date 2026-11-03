#!/usr/bin/env bun
// popstar: the one command that runs the "make me a pop star in an R&B music video" pipeline.
//   bin/popstar status [--json] [--song <slug>]     stages, receipts, the ONE next step (bun tools/popstar.ts works too)
//   bin/popstar doctor | new | timing | studio | render | qa | open | --help      (docs/cli.md)
// Every command reads popstar.json (the project manifest); without one it falls back to the built-in defaults and says so.
import { existsSync } from "node:fs";
import { paint } from "./popstar/ansi.ts";
import { printDoctor, realDeps, requiredFailures, runDoctor } from "./popstar/doctor.ts";
import { helpText } from "./popstar/help.ts";
import { openTool } from "./popstar/open.ts";
import { abs, defaultManifest, loadManifest, MANIFEST_FILE, pinnedNegative, REPO_ROOT, slugOk, writeManifest, type LoadedManifest } from "./popstar/project.ts";
import { qa } from "./popstar/qa.ts";
import { render, type Fit } from "./popstar/render.ts";
import { computeStatus, printStatus } from "./popstar/status.ts";
import { studio } from "./popstar/studio.ts";
import { timing } from "./popstar/timing.ts";

export type Args = { cmd: string; pos: string[]; flags: Record<string, string | string[] | true>; rest: string[] };
const LIST_FLAGS = new Set(["scene", "uploaded"]);
const BOOL_FLAGS = new Set(["json", "fast", "dry-run", "submit", "no-open", "force", "help"]);

/** `popstar [cmd] [--flag [value]] [positional] [-- rest]`. --scene/--uploaded take every value up to the next flag. */
export function parseArgs(argv: string[]): Args {
  const flags: Args["flags"] = {};
  const pos: string[] = [];
  let rest: string[] = [];
  const set = (key: string, v: string) => {
    if (LIST_FLAGS.has(key)) {
      const cur = flags[key];
      flags[key] = Array.isArray(cur) ? [...cur, v] : [v];
    } else flags[key] = v;
  };
  let cmd = "status";
  let i = 0;
  if (argv.length && !argv[0]!.startsWith("-")) {
    cmd = argv[0]!;
    i = 1;
  }
  for (; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === "--") {
      rest = argv.slice(i + 1);
      break;
    }
    if (a === "-h") {
      flags.help = true;
      continue;
    }
    if (!a.startsWith("--")) {
      pos.push(a);
      continue;
    }
    const eq = a.indexOf("=");
    const key = eq > 0 ? a.slice(2, eq) : a.slice(2);
    if (eq > 0) {
      set(key, a.slice(eq + 1));
      continue;
    }
    if (BOOL_FLAGS.has(key)) {
      flags[key] = true;
      continue;
    }
    if (LIST_FLAGS.has(key)) {
      let n = 0;
      while (i + 1 < argv.length && !argv[i + 1]!.startsWith("-")) {
        set(key, argv[++i]!);
        n++;
      }
      if (!n) flags[key] ??= [];
      continue;
    }
    const next = argv[i + 1];
    if (next !== undefined && !next.startsWith("-")) {
      set(key, next);
      i++;
    } else flags[key] = true;
  }
  return { cmd, pos, flags, rest };
}

const fitOf = (s: string | undefined): Fit => {
  if (s === undefined || s === "pad") return "pad";
  if (s === "crop") return "crop";
  throw new Error(`--fit expects pad or crop, got "${s}"`);
};

export async function main(argv: string[]): Promise<number> {
  const p = paint;
  const args = parseArgs(argv);
  const root = REPO_ROOT;
  const str = (k: string): string | undefined => (typeof args.flags[k] === "string" ? (args.flags[k] as string) : undefined);
  const list = (k: string): string[] => (Array.isArray(args.flags[k]) ? (args.flags[k] as string[]) : typeof args.flags[k] === "string" ? [args.flags[k] as string] : []);
  const bool = (k: string): boolean => args.flags[k] === true;
  const fail = (msg: string, code = 2): number => {
    console.error(`${p.red("✗")} ${msg}`);
    return code;
  };

  if (bool("help") || args.cmd === "help" || args.cmd === "--help") {
    console.log(helpText());
    return 0;
  }

  if (args.cmd === "new") {
    const slug = args.pos[0];
    if (!slug) return fail("usage: popstar new <slug> --song <mp3> [--title ..] [--performer ..] [--identity ..] [--negative ..] [--force]");
    if (!slugOk(slug)) return fail(`slug "${slug}" must be lowercase letters, digits and dashes (e.g. in-the-loop-devagar)`);
    const song = str("song");
    if (!song) return fail("--song <mp3> is required (the locked song, repo-relative, e.g. songs/<slug>.mp3)");
    if (existsSync(abs(root, MANIFEST_FILE)) && !bool("force")) return fail(`${MANIFEST_FILE} already exists; pass --force to overwrite it`);
    const identity = str("identity");
    const m = defaultManifest(slug, {
      song,
      title: str("title"),
      performer: str("performer"),
      identity_lock: identity,
      negative: str("negative") ?? pinnedNegative(root),
    });
    const file = writeManifest(root, m);
    console.log(p.amber(`\n  NEW PROJECT · ${m.slug}\n`));
    console.log(`  ${p.green("✓")} wrote ${MANIFEST_FILE} (${file})`);
    console.log(`    title      ${m.title}`);
    console.log(`    song       ${m.song.file}${existsSync(abs(root, m.song.file)) ? "" : p.amber("  (not there yet: stage 0)")}`);
    console.log(`    recipe     ${m.song.recipe_md}${existsSync(abs(root, m.song.recipe_md)) ? "" : p.dim("  (write it when the song is locked)")}`);
    console.log(`    performer  ${m.performer.name} · identity lock ${identity ? "set" : p.amber("defaulted to the current performer (pass --identity for someone else)")}`);
    console.log(`    palette    ${m.palette.join(" ")}`);
    console.log(`    paths      timing ${m.paths.timing} · shots ${m.paths.shots} · looks ${m.paths.looks} · clips ${m.paths.clips}/ · outputs ${m.paths.outputs}/`);
    console.log(`    frame      ${m.size[0]}×${m.size[1]} @ ${m.fps} fps`);
    console.log(`\n  ${p.dim("next: popstar doctor · popstar status")}\n`);
    return 0;
  }

  let loaded: LoadedManifest;
  try {
    loaded = loadManifest(root);
  } catch (e) {
    return fail((e as Error).message, 1);
  }
  const m = loaded.manifest;
  if (loaded.warning) console.error(`${p.amber("!")} ${loaded.warning}`);

  switch (args.cmd) {
    case "status": {
      const song = str("song") ?? m.slug;
      printStatus(computeStatus(root, song, m), bool("json"));
      return 0;
    }
    case "doctor": {
      const checks = await runDoctor(root, m, realDeps(), { fast: bool("fast") });
      const bad = requiredFailures(checks);
      if (bool("json")) console.log(JSON.stringify({ ok: bad.length === 0, required_failures: bad.map((c) => c.id), checks }, null, 2));
      else printDoctor(checks, m);
      return bad.length ? 1 : 0;
    }
    case "timing":
      return timing(root, m, args.rest);
    case "studio":
      return studio(root, m, { open: !bool("no-open") });
    case "render": {
      let fit: Fit;
      try {
        fit = fitOf(str("fit"));
      } catch (e) {
        return fail((e as Error).message);
      }
      const markId = str("mark");
      return render(root, m, {
        scenes: list("scene"),
        dryRun: bool("dry-run"),
        submit: bool("submit"),
        take: str("take"),
        fit,
        force: bool("force"),
        uploaded: list("uploaded"),
        mark: markId ? { id: markId, job: str("job") } : undefined,
      });
    }
    case "qa": {
      const [source, rendered] = args.pos;
      if (!source || !rendered) return fail("usage: popstar qa <source.mp4> <render.mp4>");
      return qa(root, source, rendered);
    }
    case "open": {
      const which = args.pos[0];
      if (!which) return fail("usage: popstar open <recorder|looks|cut|storyboard|previs>");
      return openTool(root, m, which);
    }
    default:
      console.error(`${p.red("✗")} unknown command "${args.cmd}"`);
      console.log(helpText());
      return 2;
  }
}

if (import.meta.main) process.exit(await main(process.argv.slice(2)));
