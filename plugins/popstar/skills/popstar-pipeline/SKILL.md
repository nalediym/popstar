---
name: popstar-pipeline
description: >-
  Run the whole "make me a pop star in an R&B music video" pipeline end to end, or tell Naledi exactly where it
  stands and the one next step. Chains the stages SONG → TIMING → DIRECT → PREVIS → LOOKS → SHOOT → RENDER → STITCH,
  each with its own tool or skill, gated by receipts. Use for "make the video", "what's next", "where are we",
  "run the pipeline", "make another one for a new song", or at the start of a session in a popstar project.
---

# Pop-star pipeline

One source of truth (`shots/<song>.json`), one stage at a time, receipts before moving on.
Start every run with `popstar status`: it prints each stage, its artifacts and the next action.

## Where things live
- The project is `${CLAUDE_PROJECT_DIR}`, the directory with `popstar.json`; every path below is relative to it. It is
  expected to be a checkout of https://github.com/nalediym/popstar (or made from the template), because the studio tools
  the stages call (`recorder/`, `reference-master/`, `cut/`, `previs/`, `tools/*.py`, `tools/stitch.ts`) ship with the repo.
- The CLI and the pinned Comfy Cloud graphs also ship inside this plugin, so they work in any project. Below, `popstar`
  means `bun ${CLAUDE_PLUGIN_ROOT}/tools/popstar.ts --project "${CLAUDE_PROJECT_DIR}"`, and the compiler is
  `bun ${CLAUDE_PLUGIN_ROOT}/tools/comfy_compile.ts` (graphs in `${CLAUDE_PLUGIN_ROOT}/tools/workflows/`; `<graph> --list`
  prints every slot with its pinned default). In a checkout of the repo without the plugin, the same tools are
  `bun tools/popstar.ts` and `bun tools/comfy_compile.ts`, with the graphs in `workflows/` (see `workflows/README.md`).
- Compute is Comfy Cloud through the `comfy-cloud` MCP (connect it with `/mcp`); it is not bundled here on purpose.

| # | Stage | Tool / skill | Output (receipt) | Gate before the next stage |
|---|---|---|---|---|
| 0 | SONG | Comfy Cloud `audio_ace_step1_5_xl_turbo` (open weights) | `songs/<song>.mp3` + recipe `.md` | Naledi listens and says "lock it" |
| 1 | TIMING | `popstar timing` (wraps `tools/song_timing.py`: beats, downbeats, lyric lines) | `songs/<song>.timing.json` | lines spot-checked by ear/Whisper |
| 2 | DIRECT | skill `rnb-director` | `shots/<song>.json` scenes[] + takes[] | `tools/validate_shots.py` passes |
| 3 | PREVIS | `previs/build_previs.py` (headless Blender) | `outputs/previs/animatic.mp4`, `previs/camera_plan.json` | Naledi watches the animatic |
| 4 | LOOKS | skill `reference-master` + tool `reference-master/` | `reference-master/renders/<look>/*.png` | a look sheet per scene approved, skin true |
| 5 | SHOOT | coach recorder `recorder/` (overlay cues) | `clips/<take>.mp4` + sidecar offset | `/check` receipt: 24fps, frames, audio |
| 6 | RENDER | `popstar render`: LTX-2.3 IC-LoRA pose (Comfy Cloud), hero frame per scene | `outputs/scenes/<scene>.mp4` | each scene follows her motion; identity + skin hold (`/popstar:render-verify`) |
| 7 | STITCH | ffmpeg: trim, grade, post camera, beat FX, clones, captions, clean song | `outputs/final.mp4` | Naledi watches it; screenshot receipts |

## Rules
- Open weights only. No partner/paid API nodes (no `LtxApi*`, Kling, Veo, Suno…).
- Never advance past a gate on "should work". Run it, look at it, show her.
- One question at a time; recommend first. Tell her the single next action, not the whole list.
- Downstream invalidation: changing `scenes[]` t0/t1 → re-run previs and sync the Reference Master scene times;
  changing a look → re-render that scene only; changing a take → re-render every scene.
- Comfy Cloud gotchas: subgraph `slot_overrides` often fail, so use flattened `"<instance>:<node>"` `input_overrides`.
  Saved-workflow runs revert LoadImage/bypass edits, so rerun with `from_prompt_id`.
  Always write the skin-tone identity lock into prompts.

## Stage 6 (render), current recipe (portable: pinned graphs, see "Where things live")
`popstar render` cuts the hero take per scene, tiles the harmony scene, compiles the pinned pose-restyle graph for every
stale scene into `build/scenes/` and writes `build/render-manifest.json`; `popstar render --submit` prints the MCP
contract (upload → `--uploaded` names → dry-run → `submit_batch` → download → `--mark`). Per scene, that is:
cut the take at `[t0 + sync_offset, t1 + sync_offset)` to exactly 8n+1 frames, tile ×3 first if `harmony`,
`upload_file` it, then compile and submit:
```
bun ${CLAUDE_PLUGIN_ROOT}/tools/comfy_compile.ts pose-restyle --set source_video=<uploaded name> --set trim_duration=0 \
  --set first_frame=<look render name> --set use_first_frame=true --set first_frame_strength=0.8 \
  --set prompt="<scene prompt>" --set negative="<negative>" --set seed=<scene seed> --out build/<scene>.json
```
then `submit_workflow` with that JSON (all scenes in one `submit_batch`). **`trim_duration=0` is mandatory**: the pinned
graph's Video Slice node defaults to 5 s and silently caps longer scenes. Hybrid lip-sync (audio latent) is a spike only;
see `docs/research/orchestration.md` §1.
Receipts: run the workflow `/popstar:render-verify` before stitching (one verifier agent per rendered scene: identity
kept, skin not lightened, motion follows the take; it uses `tools/qa_render.py` when the project has it, and takes
`{"scenes": [...]}` to re-check only what was re-rendered).

## Stage 7 (stitch), the edit grammar
Cuts 1 frame before downbeats · 8–12 frame dissolves between rooms · one warm flash frame at the chorus downbeat ·
post camera from `scenes[].camera.post` (scale eval=frame + crop; never zoompan) · one amber grade over everything ·
2.39:1 letterbox wipe in at 0 and out on the last beat · lyric captions from timing lines · mux the clean song,
shifted by `sync_offset_s`.

## New song? Use the template
`templates/music-video/README.md` (bundled copy: `${CLAUDE_PLUGIN_ROOT}/templates/music-video/README.md`) walks through the
whole thing for any song + performer; `popstar new <slug> --song <mp3>` writes its `popstar.json`.
