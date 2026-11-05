// popstar qa <source.mp4> <render.mp4>: run tools/qa_render.py through uv when it exists.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { paint } from "./ansi.ts";
import { runInherit, shellLine } from "./proc.ts";

/** `--with <pkg>` tokens from a script's usage header (its own declared deps), as uv arguments. */
export function parseWith(text: string): string[] {
  const out: string[] = [];
  for (const line of text.split("\n").slice(0, 80)) {
    const re = /--with\s+([\w.\[\]-]+)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(line))) if (m[1] && !out.includes(m[1])) out.push(m[1]);
  }
  return out.flatMap((w) => ["--with", w]);
}

export const QA_TOOL = "tools/qa_render.py";

export function qa(root: string, source: string, render: string): number {
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
  const cmd = ["uv", "run", ...parseWith(readFileSync(tool, "utf8")), "python", "-I", QA_TOOL, source, render];
  console.log(`${p.dim(shellLine(cmd))}`);
  return runInherit(cmd, { cwd: root });
}
