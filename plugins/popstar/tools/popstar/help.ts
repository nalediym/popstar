// popstar --help: the commands and the stage table.
import { paint, table } from "./ansi.ts";

export const STAGES: [string, string, string, string][] = [
  ["0", "SONG", "Comfy Cloud ACE-Step 1.5 (ask Claude)", "songs/<slug>.mp3 + .md (LOCKED)"],
  ["1", "TIMING", "popstar timing", "songs/<slug>.timing.json"],
  ["2", "DIRECT", "skill rnb-director (ask Claude)", "shots/<slug>.json scenes[] + takes[]"],
  ["3", "PREVIS", "previs/build_previs.py (Blender)", "outputs/previs/animatic.mp4"],
  ["4", "LOOKS", "popstar open looks (+ skill reference-master)", "reference-master/renders/<look>/*.png"],
  ["5", "SHOOT", "popstar open recorder", "clips/hero-1.mp4 + sidecar offset"],
  ["6", "RENDER", "popstar render [--submit]", "outputs/scenes/<id>.mp4"],
  ["7", "STITCH", "Director's Cut: popstar open cut", "outputs/final.mp4"],
];

export function helpText(): string {
  const p = paint;
  const L: string[] = [];
  L.push("");
  L.push(`  ${p.amber(p.bold("popstar"))} ${p.dim("· make me a pop star in an R&B music video")}`);
  L.push("");
  L.push(`  ${p.dim("usage:")} popstar [--project <dir>] <command> [options]        ${p.dim("(bin/popstar, bun tools/popstar.ts, or the plugin's copy)")}`);
  L.push("");
  L.push(`  ${p.amber("commands")}`);
  const cmds: [string, string, string][] = [
    ["status", "[--json] [--song <slug>]", "every stage, its receipts, and the ONE next step"],
    ["next", "[--json] [--song <slug>]", "only the ONE next step"],
    ["doctor", "[--json] [--fast]", "ffmpeg/bun/uv/python/Blender, models, Comfy Cloud, fonts, data; exact fixes"],
    ["new <slug>", "--song <mp3> [--title ..] [--performer ..] [--identity ..] [--negative ..] [--force]", "write popstar.json, the project manifest"],
    ["timing", "[-- <song_timing.py args>]", "beats, downbeats, lyric lines via uv"],
    ["studio", "[--no-open]", "start every tool server on a free port + a hub page"],
    ["render", "[--scene <id> ...] [--dry-run] [--submit] [--take <id>] [--fit pad|crop] [--force]", "cut 8n+1 clips, compile pose-restyle graphs, write the manifest"],
    ["", "[--uploaded <file>=<name> ...] [--mark <id> --job <prompt_id>]", "record Comfy Cloud upload names / a finished render"],
    ["qa", "<source.mp4> <render.mp4> [-- <args>]", "skin, identity and motion checks (tools/qa_render.py via uv)"],
    ["open", "<recorder|looks|cut|storyboard|previs>", "open the tool or artifact that exists"],
  ];
  for (const line of table(cmds.map(([c, a, d]) => [c ? p.bold(c) : "", p.dim(a), d]), "    ")) L.push(line);
  L.push("");
  L.push(`  ${p.amber("stages")} ${p.dim("(popstar status shows where you are)")}`);
  for (const line of table([["#", "stage", "tool", "receipt"].map((h) => p.dim(h)), ...STAGES.map(([n, s, t, r]) => [n, p.bold(s), t, p.dim(r)])], "    ")) L.push(line);
  L.push("");
  L.push(`  ${p.dim("project: --project <dir>, else POPSTAR_ROOT, else the nearest popstar.json above the working directory")}`);
  L.push(`  ${p.dim("manifest: popstar.json · graphs: workflows/ (the bundled copy first) · contract: build/render-manifest.json · cache: build/state.json · docs/cli.md")}`);
  L.push("");
  return L.join("\n");
}
