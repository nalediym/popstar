# popstar (Claude Code plugin)

Make anyone a pop star in a '90s R&B music video. This plugin packages the judgment half of the
[popstar](https://github.com/nalediym/popstar) pipeline (the skills), its CLI, the pinned open-weights Comfy Cloud
graphs and a render-verify workflow, so one command installs it.

| Component | What you get |
|---|---|
| `/popstar` | the pipeline CLI for the current project: `status`, `next`, `doctor`, `new <slug> --song <mp3>` (`/popstar` alone is `status`) |
| `/popstar:popstar-pipeline` | the orchestrator: SONG → TIMING → DIRECT → PREVIS → LOOKS → SHOOT → RENDER → STITCH, each gated by a receipt |
| `/popstar:rnb-director` | the director (late-'90s/2000s R&B grammar): writes `shots/<song>.json`, the single source of truth |
| `/popstar:reference-master` | per-scene looks (wardrobe, set, light, expression) on Comfy Cloud, kept in `reference-master/looks.json` |
| `/popstar:render-verify` | a workflow: one verifier agent per rendered scene (identity kept, skin not lightened, motion follows the take), verdict table out |
| `tools/` | `popstar.ts` (the CLI), `comfy_compile.ts` (fills a pinned graph by slot name), `workflows/` (the six pinned graphs + `slots.json`) |
| `templates/music-video/` | the recipe for a new song and performer, and `shots.schema.json` |

## Install

In Claude Code:

```
/plugin marketplace add nalediym/popstar
/plugin install popstar@popstar-market
```

or in one step: `/plugin install popstar --marketplace nalediym/popstar`. From a shell the same two commands are
`claude plugin marketplace add nalediym/popstar` and `claude plugin install popstar@popstar-market`.

Then connect Comfy Cloud: add the `comfy-cloud` MCP server to Claude Code and sign in with `/mcp`. The MCP is not
bundled with the plugin on purpose: a bundled copy would rename its tools (`mcp__plugin_popstar_comfy-cloud__*`) and a
user-scope entry wins over it anyway.

## Use

Open a project (a checkout of the repo, or an empty directory) and run `/popstar` (= `status`), `/popstar doctor`, or
`/popstar new my-song --song songs/my-song.mp3`. The three skills trigger on their own ("direct this", "make a look",
"what's next", "render the queue"). After the scene renders land in `outputs/scenes/`, run `/popstar:render-verify`,
optionally with `{"scenes": ["s3-chorus"]}` to check one scene, or `{"project": "/abs/path"}` when the session is not in
the project.

## Where data goes

- **The project**: the directory with `popstar.json`, found from `--project`, else `POPSTAR_ROOT`, else the nearest
  `popstar.json` at or above the working directory. The song, timing, shots, looks, clips, `outputs/` and `build/` live
  there; `${CLAUDE_PROJECT_DIR}` in the skills.
- **The plugin** is read-only (`${CLAUDE_PLUGIN_ROOT}`). The compiler reads the pinned graphs from the plugin first
  (`tools/workflows/`), then the project's `workflows/`; `POPSTAR_WORKFLOWS_DIR` overrides both.
- Nothing is written to `${CLAUDE_PLUGIN_DATA}` today.

## What is not in the plugin

The studio tools ship with the repo, not the plugin: the coach recorder, the Reference Master UI, the Director's Cut,
the Blender previs, `tools/song_timing.py`, `tools/validate_shots.py`, `tools/qa_render.py` and `tools/stitch.ts`. Clone
the repo as your project to get the whole pipeline (Bun, uv, ffmpeg ≥ 7, Python 3.12, Blender 5.x for previs;
`/popstar doctor` checks each one and prints the fix).

## Keeping the copies in sync

`plugins/popstar/tools/` mirrors the repo's `tools/popstar.ts`, `tools/popstar/` and `tools/comfy_compile.ts`,
`plugins/popstar/tools/workflows/` mirrors the API graphs and `slots.json` in `workflows/`, and
`plugins/popstar/templates/` mirrors `templates/`. The skills have a single copy: `.claude/skills/<name>` are symlinks
into `plugins/popstar/skills/`. After editing the CLI, the compiler, a graph or the template, re-copy and check:

```bash
cp tools/popstar.ts tools/comfy_compile.ts plugins/popstar/tools/ && cp tools/popstar/*.ts plugins/popstar/tools/popstar/ && cp workflows/*.api.json workflows/slots.json plugins/popstar/tools/workflows/ && cp -r templates/music-video/. plugins/popstar/templates/music-video/
diff -rq tools/popstar plugins/popstar/tools/popstar && diff -q tools/comfy_compile.ts plugins/popstar/tools/comfy_compile.ts && diff -q tools/popstar.ts plugins/popstar/tools/popstar.ts && echo in sync
```

## Validate

```bash
claude plugin validate plugins/popstar --strict   # the plugin
claude plugin validate . --strict                 # the marketplace (.claude-plugin/marketplace.json)
```
