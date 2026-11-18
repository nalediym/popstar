// popstar qa <source.mp4> <render.mp4> [-- args]: run tools/qa_render.py through uv when it exists.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { paint } from "./ansi.ts";
import { runInherit, shellLine } from "./proc.ts";

/** The uv arguments a script's usage header declares for itself: `--python X` and every `--with <pkg>` ("quoted specs" too),
 *  read from its `uv run …` command line (plus `\` continuation lines) only, never from the prose around it. */
export function parseWith(text: string): string[] {
  const lines = text.split("\n").slice(0, 80);
  const start = lines.findIndex((l) => /\buv run\b/.test(l));
  if (start < 0) return [];
  let cmd = "";
  for (let i = start; i < lines.length; i++) {
    const l = lines[i]!.replace(/^\s*#?\s*/, "");
    cmd += ` ${l.replace(/\\\s*$/, "")}`;
    if (!/\\\s*$/.test(lines[i]!)) break;
  }
  const out: string[] = [];
  const seen = new Set<string>();
  const py = /--python\s+("([^"]+)"|'([^']+)'|([^\s"'`]+))/.exec(cmd);
  if (py) out.push("--python", py[2] ?? py[3] ?? py[4]!);
  const re = /--with\s+("([^"]+)"|'([^']+)'|([^\s"'`]+))/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(cmd))) {
    const spec = m[2] ?? m[3] ?? m[4] ?? "";
    if (spec && !seen.has(spec)) {
      seen.add(spec);
      out.push("--with", spec);
    }
  }
  return out;
}

export const QA_TOOL = "tools/qa_render.py";

export function qa(root: string, source: string, render: string, extra: string[] = []): number {
  const p = paint;
  const tool = join(root, QA_TOOL);
  if (!existsSync(tool)) {
    console.log(`${p.amber("!")} QA tool not built yet (${QA_TOOL})`);
    return 1;
  }
  for (const f of [source, render])
    if (!existsSync(f)) {
      console.error(`${p.red("✗")} no such file: ${f}`);
      return 2;
    }
  const cmd = ["uv", "run", ...parseWith(readFileSync(tool, "utf8")), "python", "-I", QA_TOOL, source, render, ...extra];
  console.log(`${p.dim(shellLine(cmd))}`);
  return runInherit(cmd, { cwd: root });
}
