// Pipeline status: which stages are done, what each produced, and the ONE next action.
// This is the original tools/popstar.ts logic, kept byte-for-byte in its output.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import type { Manifest } from "./project.ts";

export type Stage = { n: number; name: string; done: boolean; detail: string; next: string };
export type StatusResult = { song: string; stages: Stage[]; next: Stage | null };

/** Compute the eight stages for `song`. The manifest only supplies file paths when its slug is `song`. */
export function computeStatus(root: string, song: string, manifest?: Manifest): StatusResult {
  const p = (...parts: string[]) => join(root, ...parts);
  const has = (rel: string) => existsSync(p(rel));
  const readJson = (rel: string): any => {
    try {
      return JSON.parse(readFileSync(p(rel), "utf8"));
    } catch {
      return null;
    }
  };
  const list = (rel: string, re: RegExp) => {
    try {
      return readdirSync(p(rel)).filter((f) => re.test(f)).sort();
    } catch {
      return [];
    }
  };
  const age = (rel: string) => {
    try {
      const mins = Math.round((Date.now() - statSync(p(rel)).mtimeMs) / 60000);
      return mins < 60 ? `${mins}m ago` : `${Math.round(mins / 60)}h ago`;
    } catch {
      return "";
    }
  };

  const m = manifest && manifest.slug === song ? manifest : undefined;
  const songFile = m?.song.file ?? `songs/${song}.mp3`;
  const recipeFile = m?.song.recipe_md ?? `songs/${song}.md`;
  const timingFile = m?.paths.timing ?? `songs/${song}.timing.json`;
  const shotsFile = m?.paths.shots ?? `shots/${song}.json`;
  const looksFile = m?.paths.looks ?? "reference-master/looks.json";
  const clipsDir = m?.paths.clips ?? "clips";
  const outputsDir = m?.paths.outputs ?? "outputs";
  const previsDir = m?.paths.previs ?? "previs";

  const stages: Stage[] = [];
  const add = (s: Stage) => stages.push(s);

  // 0 SONG
  const recipe = has(recipeFile) ? readFileSync(p(recipeFile), "utf8") : "";
  add({
    n: 0, name: "SONG", done: has(songFile) && /LOCKED/.test(recipe),
    detail: has(songFile) ? `${songFile}${/LOCKED/.test(recipe) ? " (locked)" : " (not locked)"}` : "no song yet",
    next: "Make a song with ACE-Step 1.5 on Comfy Cloud, listen, and lock it.",
  });

  // 1 TIMING
  const timing = readJson(timingFile);
  const lowLines = timing?.lines?.filter((l: any) => l.confidence === "low").length ?? 0;
  add({
    n: 1, name: "TIMING", done: !!timing?.beats?.length && !!timing?.lines?.length,
    detail: timing
      ? `${timing.beats?.length ?? 0} beats · ${timing.downbeats?.length ?? 0} downbeats · ${timing.lines?.length ?? 0} lines (${lowLines} low-confidence) · ${timing.bpm ?? "?"} BPM`
      : "no timing file",
    next: `uv run --with librosa --with soundfile --with mlx-whisper python -I tools/song_timing.py ${songFile} ${recipeFile} -o ${timingFile}`,
  });

  // 2 DIRECT
  const shots = readJson(shotsFile);
  let validator = "validator not found";
  if (shots && has("tools/validate_shots.py")) {
    const v = Bun.spawnSync(["python3", "-I", p("tools/validate_shots.py"), p(shotsFile), p(timingFile)]);
    validator = v.exitCode === 0 ? "valid" : `INVALID: ${v.stdout.toString().trim().split("\n")[0] || v.stderr.toString().trim().split("\n")[0]}`;
  }
  const scenes: any[] = shots?.scenes ?? [];
  add({
    n: 2, name: "DIRECT", done: !!shots && scenes.length > 0 && validator === "valid",
    detail: shots ? `${shots.takes?.length ?? 0} takes · ${scenes.length} scenes · ${validator}` : "no shot list",
    next: scenes.length ? "Fix the shot list until tools/validate_shots.py passes." : "Ask Claude to direct it (skill rnb-director): fill scenes[] in the shot list.",
  });

  // 3 PREVIS
  const animatic = `${outputsDir}/previs/animatic.mp4`;
  add({
    n: 3, name: "PREVIS", done: has(animatic) && has(`${previsDir}/camera_plan.json`),
    detail: has(animatic) ? `${animatic} (${age(animatic)})` : "no animatic",
    next: `Block it: /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P ${previsDir}/build_previs.py -- (see ${previsDir}/README.md)`,
  });

  // 4 LOOKS
  const looks = readJson(looksFile);
  const lookList: any[] = looks?.looks ?? [];
  const doneLooks = lookList.filter((l) => l.status === "done").length;
  add({
    n: 4, name: "LOOKS", done: lookList.length > 0 && doneLooks === lookList.length,
    detail: looks ? `${doneLooks}/${lookList.length} looks rendered · queue: ${(readJson("reference-master/queue.json") ?? []).length ?? 0}` : "no looks file",
    next: "Open Reference Master (bun reference-master/server.ts), design the looks, queue them, then ask Claude to \"render the queue\".",
  });

  // 5 SHOOT
  const takeIds: string[] = (shots?.takes ?? []).map((t: any) => t.id);
  const clips = list(clipsDir, /\.mp4$/);
  const recorded = takeIds.filter((id) => clips.some((c) => c === `${id}.mp4` || c.startsWith(`${id}-`)));
  const heroes = recorded.filter((id) => id.startsWith("hero"));
  add({
    n: 5, name: "SHOOT", done: heroes.length > 0,
    detail: takeIds.length ? `${recorded.length}/${takeIds.length} takes recorded${recorded.length ? ` (${recorded.join(", ")})` : ""}` : `${clips.length} clips in ${clipsDir}/`,
    next: "Open the coach recorder (bun recorder/server.ts), pick take hero-1, press R to rehearse, then Space to record.",
  });

  // 6 RENDER
  const rendered = scenes.filter((s) => has(`${outputsDir}/scenes/${s.id}.mp4`)).map((s) => s.id);
  add({
    n: 6, name: "RENDER", done: scenes.length > 0 && rendered.length === scenes.length,
    detail: scenes.length ? `${rendered.length}/${scenes.length} scenes rendered` : "needs scenes[] first",
    next: "Ask Claude to render the scenes (skill popstar-pipeline, stage 6).",
  });

  // 7 STITCH
  const final = `${outputsDir}/final.mp4`;
  add({
    n: 7, name: "STITCH", done: has(final),
    detail: has(final) ? `${final} (${age(final)})` : "no final video",
    next: "Ask Claude to stitch the final cut (skill popstar-pipeline, stage 7).",
  });

  const firstOpen = stages.find((s) => !s.done);
  return { song, stages, next: firstOpen ?? null };
}

/** The classic status screen (always colored, exactly as before). */
export function printStatus(r: StatusResult, asJson: boolean, log: (s: string) => void = console.log): void {
  if (asJson) {
    log(JSON.stringify({ song: r.song, stages: r.stages, next: r.next }, null, 2));
    return;
  }
  const amber = (s: string) => `\x1b[38;5;215m${s}\x1b[0m`;
  const dim = (s: string) => `\x1b[2m${s}\x1b[0m`;
  const firstOpen = r.next;
  log(amber(`\n  POP-STAR PIPELINE · ${r.song}\n`));
  for (const s of r.stages) {
    const mark = s.done ? "\x1b[32m✓\x1b[0m" : s === firstOpen ? amber("▶") : dim("·");
    log(`  ${mark} ${String(s.n)} ${s.name.padEnd(7)} ${s.done ? s.detail : dim(s.detail)}`);
  }
  log(firstOpen ? `\n  ${amber("NEXT:")} ${firstOpen.next}\n` : `\n  ${amber("DONE.")} Watch outputs/final.mp4 🎤\n`);
}
