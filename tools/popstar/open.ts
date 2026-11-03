// popstar open <tool>: open the file or URL that exists (a running server, a started one, or a rendered artifact).
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { paint } from "./ansi.ts";
import { abs, type Manifest } from "./project.ts";
import { freePort, health, killGroup, openUrl, portListening, startTool, TOOLS } from "./studio.ts";

export const OPENABLE = ["recorder", "looks", "cut", "storyboard", "previs"] as const;
export type Openable = (typeof OPENABLE)[number];
const ALIASES: Record<string, Openable> = { "reference-master": "looks", rm: "looks", look: "looks", animatic: "previs", boards: "storyboard", storyboards: "storyboard", edit: "cut" };

export const normalizeTool = (s: string): Openable | null => {
  const k = s.toLowerCase();
  if ((OPENABLE as readonly string[]).includes(k)) return k as Openable;
  return ALIASES[k] ?? null;
};

/** A port pinned for this tool in .claude/launch.json (the recorder preview), if any. */
export function launchPort(root: string, key: string): number | null {
  try {
    const j = JSON.parse(readFileSync(join(root, ".claude", "launch.json"), "utf8")) as { configurations?: { name?: string; port?: number; runtimeArgs?: string[] }[] };
    const file = TOOLS.find((t) => t.key === key)?.file;
    const c = j.configurations?.find((x) => x.name === key || (file && x.runtimeArgs?.some((a) => a.endsWith(file))));
    return typeof c?.port === "number" ? c.port : null;
  } catch {
    return null;
  }
}

export async function openTool(root: string, m: Manifest, which: string, log: (s: string) => void = console.log): Promise<number> {
  const p = paint;
  const tool = normalizeTool(which);
  if (!tool) {
    log(`${p.red("✗")} unknown tool "${which}". One of: ${OPENABLE.join(", ")}`);
    return 2;
  }
  if (tool === "storyboard") {
    const dir = abs(root, m.paths.storyboards);
    const files = existsSync(dir) ? readdirSync(dir).sort() : [];
    const pick = files.find((f) => f.endsWith(".pdf")) ?? files.find((f) => /_board\.png$/.test(f)) ?? files.find((f) => f.endsWith(".png"));
    if (!pick) {
      log(`${p.amber("!")} storyboards not built yet (${m.paths.storyboards}/*_board.png or .pdf). The storyboard build (tools/storyboard.py) is in flight.`);
      return 1;
    }
    const file = join(dir, pick);
    log(`${p.green("✓")} opening ${m.paths.storyboards}/${pick}`);
    return openUrl(file) ? 0 : 1;
  }
  if (tool === "previs") {
    const file = abs(root, `${m.paths.outputs}/previs/animatic.mp4`);
    if (!existsSync(file)) {
      log(`${p.amber("!")} animatic not built yet (${m.paths.outputs}/previs/animatic.mp4). Block it:`);
      log(`  /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P ${m.paths.previs}/build_previs.py --`);
      return 1;
    }
    log(`${p.green("✓")} opening ${m.paths.outputs}/previs/animatic.mp4`);
    return openUrl(file) ? 0 : 1;
  }
  const def = TOOLS.find((t) => t.key === tool)!;
  // Already running on its pinned preview port?
  const pinned = launchPort(root, tool);
  if (pinned && portListening(pinned)) {
    const url = `http://localhost:${pinned}/`;
    if ((await health(url)) === "up") {
      log(`${p.green("✓")} ${def.name} is already up on its .claude/launch.json port: ${url}`);
      return openUrl(url) ? 0 : 1;
    }
  }
  if (!existsSync(join(root, def.file))) {
    log(`${p.amber("!")} ${def.name} not built yet (${def.file}).`);
    return 1;
  }
  const port = await freePort();
  const r = startTool(root, def, port);
  log(`${p.amber("▶")} starting ${def.name} (${def.file}) on port ${port}…`);
  const url = await r.ready;
  if (r.state === "exited" || !url) {
    log(`${p.red("✗")} ${def.name} exited (${r.exitCode}): ${r.log.slice(-3).join(" | ")}`);
    return 1;
  }
  log(`${p.green("✓")} ${def.name} → ${url}   ${p.dim("(Ctrl-C stops it)")}`);
  openUrl(url);
  return new Promise<number>((resolve) => {
    const stop = () => {
      killGroup(r.child, "SIGTERM");
      setTimeout(() => {
        killGroup(r.child, "SIGKILL");
        resolve(0);
      }, 500);
    };
    process.on("SIGINT", stop);
    process.on("SIGTERM", stop);
    r.child.on("exit", (code) => {
      log(`${p.dim(`${def.name} exited (${code})`)}`);
      resolve(code ?? 0);
    });
  });
}
