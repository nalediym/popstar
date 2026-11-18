---
name: popstar
description: >-
  The popstar pipeline CLI for the current project: status (every stage, its receipts, the ONE next step), next (only
  the next step), doctor (tools, models, Comfy Cloud and data checks with the exact fix for each miss) and
  new <slug> --song <mp3> (write popstar.json, the project manifest). /popstar alone is status.
argument-hint: "[status|next|doctor|new <slug> --song <mp3>] [--json]"
disable-model-invocation: true
allowed-tools: Bash(bun ${CLAUDE_PLUGIN_ROOT}/tools/popstar.ts *)
---

# /popstar

Run the CLI bundled with this plugin against the project in `${CLAUDE_PROJECT_DIR}`, exactly like this (no `cd`, no
extra flags; the arguments of this invocation are passed through as given):

```bash
bun ${CLAUDE_PLUGIN_ROOT}/tools/popstar.ts --project "${CLAUDE_PROJECT_DIR}" $ARGUMENTS
```

With no arguments it runs `status`. Then act on what it printed:

- **status / next**: show the output as it came (it is short), then say the ONE next step in one line and name what
  does it: directing → `/popstar:rnb-director`, looks → `/popstar:reference-master`, everything else (song, timing,
  previs, shoot, render, stitch) → `/popstar:popstar-pipeline`. Do not start that step in this turn unless asked to.
- **doctor**: exit code 1 means a required check failed. List each failed check with its `fix` line verbatim and stop;
  do not run the fixes. Comfy Cloud is reached through the `comfy-cloud` MCP, which the person connects with `/mcp`;
  it is not bundled with this plugin on purpose (a bundled copy would rename its tools and lose to a user-scope entry).
- **new**: writes `popstar.json` into the project directory, never into the plugin. Only run it when `new` is in the
  arguments of this invocation. If the manifest already exists the command refuses unless `--force` was passed: report
  that and stop; never add `--force` yourself.
- Any other command (`timing`, `studio`, `render`, `qa`, `open`, `--help`) is passed through too, for example
  `/popstar render --dry-run`. The gated stages are better run through `/popstar:popstar-pipeline`, which knows the
  receipts each one needs.

## Where things live

- **The project** (`${CLAUDE_PROJECT_DIR}`): `popstar.json` plus the song, timing, shots, looks, clips, `outputs/`
  and `build/`. Everything the pipeline writes goes here. The CLI finds the project from `--project`, else
  `POPSTAR_ROOT`, else the nearest `popstar.json` at or above the working directory.
- **This plugin, read-only** (`${CLAUDE_PLUGIN_ROOT}`): `tools/popstar.ts` (the CLI), `tools/comfy_compile.ts` (fills
  the pinned Comfy Cloud graphs by slot name; `<graph> --list` prints the slots), `tools/workflows/` (the six pinned
  open-weights graphs + `slots.json`), `templates/music-video/` (the recipe for a new song and performer, and the
  shot-list schema).
- **Plugin-private state**: `${CLAUDE_PLUGIN_DATA}`, unused today.
- The studio tools the later stages call (coach recorder, Reference Master, Director's Cut, previs, the timing and QA
  scripts) ship with the repo, not the plugin: a project is expected to be a checkout of
  https://github.com/nalediym/popstar, or made from the template with `/popstar new`.
