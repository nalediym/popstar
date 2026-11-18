// popstar timing: run tools/song_timing.py through uv and summarize the timing contract it wrote.
import { existsSync, readFileSync } from "node:fs";
import { paint } from "./ansi.ts";
import { abs, type Manifest } from "./project.ts";
import { runInherit, shellLine } from "./proc.ts";

export const timingCommand = (m: Manifest, extra: string[] = []): string[] => [
  "uv", "run", "--with", "librosa", "--with", "soundfile", "--with", "mlx-whisper",
  "python", "-I", "tools/song_timing.py", m.song.file, m.song.recipe_md, "-o", m.paths.timing, ...extra,
];

type Timing = {
  bpm?: number;
  duration_s?: number;
  beats?: number[];
  downbeats?: number[];
  sections?: { name: string; start: number; end: number }[];
  lines?: { text: string; start: number; end: number; confidence?: string }[];
};

export function summarizeTiming(t: Timing): string[] {
  const lines = t.lines ?? [];
  const low = lines.filter((l) => l.confidence === "low").length;
  const out = [
    `bpm        ${t.bpm ?? "?"}${t.duration_s ? `   (${t.duration_s}s)` : ""}`,
    `beats      ${t.beats?.length ?? 0}`,
    `downbeats  ${t.downbeats?.length ?? 0}${t.downbeats?.length ? `   ${t.downbeats.map((d) => d.toFixed(2)).join(" ")}` : ""}`,
    `sections   ${(t.sections ?? []).map((s) => `${s.name} ${s.start.toFixed(1)}–${s.end.toFixed(1)}`).join(" · ") || "none"}`,
    `lines      ${lines.length}${lines.length ? ` (${low} low-confidence)` : ""}`,
  ];
  for (const l of lines) out.push(`  ${l.start.toFixed(2).padStart(6)}–${l.end.toFixed(2).padEnd(6)} ${l.confidence === "low" ? paint.dim(l.text) : l.text}`);
  return out;
}

export function timing(root: string, m: Manifest, extra: string[]): number {
  const song = abs(root, m.song.file);
  const recipe = abs(root, m.song.recipe_md);
  for (const [what, f] of [["song", song], ["recipe", recipe]] as const)
    if (!existsSync(f)) {
      console.error(`${paint.red("✗")} ${what} missing: ${f}`);
      return 1;
    }
  const cmd = timingCommand(m, extra);
  console.log(paint.amber(`\n  TIMING · ${m.slug}\n`));
  console.log(`  ${paint.dim(shellLine(cmd))}\n`);
  const code = runInherit(cmd, { cwd: root });
  if (code !== 0) {
    console.error(`\n${paint.red("✗")} song_timing.py exited ${code}`);
    return code;
  }
  const file = abs(root, m.paths.timing);
  if (!existsSync(file)) {
    console.error(`${paint.red("✗")} no ${m.paths.timing} written`);
    return 1;
  }
  const t = JSON.parse(readFileSync(file, "utf8")) as Timing;
  console.log(`\n  ${paint.green("✓")} ${m.paths.timing}\n`);
  for (const l of summarizeTiming(t)) console.log(`  ${l}`);
  console.log("");
  return 0;
}
