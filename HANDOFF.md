# HANDOFF — pop-star music video (updated 2026-11-03 ~00:10 ET; model Fable 5.1)

## Resume in 60 seconds
1. Repo: **https://github.com/nalediym/popstar** (public, Apache-2.0). `git pull` first; background builds kept landing after 00:10 and were pushed as they finished.
2. `bun tools/popstar.ts status` → the eight stages and the one next step. `bun tools/popstar.ts doctor` if anything looks off.
3. Read the "In flight" table below: every row marked LANDED is on disk and committed; the rest may have landed after this was written (check `git log`).
4. The next real step is the **shoot**: `bun recorder/server.ts`, take `hero-1`, R to rehearse, Space to record (earbuds in, warm light in front, hands off the mouth). Then one anchored render of s3, then all four scenes, then stitch v1 in `cut/`.
5. Open questions she still owes: Q3 seated or standing performance · Q4 Fable on director/QA agents only or everywhere.

Deadline: **2026-11-03 12:00 ET** (Open Source Higgsfield hackathon). Open weights only. Compute = Comfy Cloud via the `comfy-cloud` MCP.
First command in any new session: `bun tools/popstar.ts status` (stages + the one next step).
Vision + locked decisions: `PLAN.md` (§ Vision v2). Architecture + risks: `docs/architecture.md`. Skills: `.claude/skills/`.

## One line
Naledi records ~32s of herself lip-syncing to an original song; one pipeline turns it into a '90s R&B music video of her:
her real motion (DWPose → LTX-2.3 IC-LoRA), new wardrobe/sets/expressions per scene (Reference Master), clones on the harmonies, cut on the downbeats.

## Verified tonight (receipts in outputs/ and reference-master/tests/)
| What | Evidence | Recipe |
|---|---|---|
| Song, locked | `songs/in-the-loop-devagar.mp3` (ACE-Step 1.5, 32s, ~90.9 BPM real) | `songs/in-the-loop-devagar.md` |
| Pose restyle follows her motion | `outputs/chunk-01_anime-neon_pose.mp4`, `chunk-02_claymation_pose.mp4` + side-by-sides | rerun job `650120d9-3e42-4fbb-8ebc-19e1629c4832` via `submit_workflow from_prompt_id` + overrides `199.file`, `129:211.on_false`, `129:112.text`, `129:704.seed`, `200.image` + `129:198.bypass=false` |
| Lip-sync from a photo + audio | `outputs/chorus_lipsync_ia2v.mp4` (LTX-2.3 ia2v, job `ec3ea736…`), `chorus_lipsync_infinitetalk.mp4` (job `05f64513…`), `chorus_lipsync_compare.mp4` | ia2v overrides: `269.image`, `276.audio`, `340:319.value` prompt, `340:349.value=false`, `340:332.start_index`, `340:331.value` duration, `340:286.noise_seed` |
| Wardrobe + set edit keeps identity | `reference-master/tests/sheet_wardrobe.png` (Qwen Image 2.1 Turbo edit, job `eff061da…`) | `.claude/skills/reference-master/SKILL.md` |
| Editable eyes/mouth/head | `reference-master/tests/sheet_chorus_expressions.png` (ExpressionEditor, job `39a4709f…`) | same skill; use STRONG slider values |
| Beat/downbeat detection | beat_this: 12 downbeats `1.68 4.28 6.90 9.50 12.12 14.72 17.34 19.94 22.56 25.16 27.78 30.38` | `docs/research/orchestration.md` §4 |

Comfy Cloud input names already uploaded (reusable): clip-01 `461682d8…mp4`, her first frame `660bf887…png`, song `ebedfcc8…mp3`, chorus wardrobe render `958a28e5…png`.

## In flight when this was written (background builds; verify each when it lands, then commit it)
| Build | Produces | Verify by |
|---|---|---|
| Coach recorder workflow | `songs/*.timing.json`, `shots/in-the-loop-devagar.json`, `recorder/index.html` overlay (karaoke, beat HUD, move cards, pose warnings), `tools/song_timing.py`, `tools/validate_shots.py` | open the recorder in the browser pane with `?demo=test-clip`, watch cues fire on beats; `python3 -I tools/validate_shots.py …` |
| ~~Reference Master~~ **LANDED** | `reference-master/{server.ts,index.html,looks.json,README.md}`: refs tray, intensity slider, 8 emotion presets, undo, copy-for-Claude; 49/49 UI tests. 25 Pinterest refs wired into `looks.json` with her wardrobe lines; launch entry `reference-master` (port 56110) | `bun reference-master/server.ts`. Note: `blink` must be NEGATIVE to close eyes (−10 heavy lids, −20 shut); the devagar preset's blink 4 does not close them |
| ~~Photos scout~~ **LANDED** | `tools/photos_scout.py` (`stats`, `identity --person "Naledi Kekana"`, `inspire --album Pinterest`, `--export` opt-in), `docs/photos.md`. Library readable from this app (FDA on); originals are iCloud-only so exports are ~1024 px previews unless `--download-missing` or Photos → Download Originals | `uv run --python 3.12 --with osxphotos --with pillow python -I tools/photos_scout.py identity --person "Naledi Kekana" --export` to drop 12 identity refs into `reference-master/refs/identity/` |
| ~~Hermes/NousCon~~ **LANDED** | `docs/research/hermes-nouscon.md`: NousCon was an exhibition (Oct 30, NYC); Hermes Agent is MIT, skills = the same SKILL.md format. Borrow: vision-checked render gate (`<scene>.qa.json`), skill-learning closeout (patch Pitfalls from receipts, staged for approval), bounded taste memory, SOUL.md per role. Not: hosted-API generation, zero-human video, nested delegation | read §4 before building the examiner |
| ~~Reuse inventory~~ **LANDED** | `docs/research/reuse-inventory.md`: nWave practices to borrow (expectation charter per scene, examiner after render, walking skeleton first) and top reuse from `~/Projects/claude-music-video` (songmap, skia caption overlays, lipsync envelope) | read it before building captions or the hybrid spike |
| ~~Blender previs~~ **LANDED** | `previs/build_previs.py` (headless Blender 5.2, EEVEE, ~7 min), `outputs/previs/animatic.mp4` (32.0 s, cuts 1 frame before downbeats, verified), `previs/camera_plan.json` (post_zoom/post_pan per scene for stitch), `previs/README.md`, `.blend` + stills | the committed animatic predates the shot list + new wardrobe by minutes; a re-run (README command) realigns it. **Director note:** timing.json puts the chorus at 22.57 s, so the locked S3 (17.34–25.16) opens on the last verse line; decide whether S3 should start at 19.94 or 22.56 before rendering |
| ~~Comfy graph pinning (R1)~~ **LANDED** | `workflows/*.api.json` (6 graphs), `workflows/slots.json`, `tools/comfy_compile.ts` (`--list`, `--set`, `--preset`) | equivalence test vs job `ce7dccfd…` passed (0 diffs); all 6 dry-run validated. **Always pass `trim_duration=0` to pose-restyle** (pinned default 5 s caps scenes). InfiniteTalk rounds up to 81-frame windows; trim in stitch. Wardrobe width/height only apply with `custom_size=true`. |
| Storyboards | `storyboards/*_board.png`, `*_beatboard.png`, `.pdf`, `tools/storyboard.py` | look at the boards; s1/s2 frames rendered on Cloud, s4 is a triptych composite |
| Director's Cut | `cut/{server.ts,index.html,edl.json}`, `tools/stitch.ts`, `outputs/final.mp4` (placeholders) | play it against the song; render; ffprobe 32s/24fps/audio |

## Not started
R2 (scene times: one source of truth), R3 (`popstar.json` manifest, no hardcoded paths), plugin packaging (`plugins/popstar` + marketplace.json, format in the research report), Asset Doctor / gap filler (missing shots → generated inserts; archive light leaks), unified Studio (one server, tabs), **the hero take**, **the 4 scene renders**, **stitch v1 with real renders**, hybrid lip-sync spike, LICENSES.md audit (LivePortrait face-detector weights may be non-commercial).

## Licenses (docs/LICENSES.md, audited 2026-11-02 ~23:40)
Open Weights bonus: eligible (all weights downloadable, no partner nodes). Commercial blocker: **Qwen-Image 2.1 Turbo**
(wardrobe edits) is research-only; swap to the Apache-2.0 Qwen-Image-Edit (test in flight → `workflows/look-wardrobe-apache.api.json`).
LTX-2.3 = LTX-2 Community License (free under $10M revenue, keep notices). Gemma = Gemma Terms (ship a NOTICE). Everything
else MIT/Apache. Light leaks are CC-BY 3.0 (credit Jeffrey Beach / Beachfront Productions). Repo license recommendation: Apache-2.0 + NOTICE.
Taste refs: `docs/inspiration/pinterest.md` (synthesis) + `build/pinterest-refs/` (25 pins, staged; move into `reference-master/refs/pinterest/` after the RM build lands, because its tests wipe `refs/`).

## Gotchas (each cost time tonight)
- Subgraph `slot_overrides` fail on Cloud templates → use flattened `"<instance>:<node>"` `input_overrides` (the error names the id).
- Running a *saved* workflow reverted LoadImage + bypass edits → rerun from a working job id, or submit full API JSON.
- Models lighten her skin unless every prompt has the identity lock verbatim ("rich deep brown skin, warm undertones, luminous dewy sheen…") and the negative has "lightened skin, pale skin".
- Whisper on the full song hallucinates; use ≤8s windows with `initial_prompt`.
- zsh: `cp/mv/rm` are `-i` aliases (use `/bin/…`); unquoted vars don't word-split. Port 56101 = recorder preview.
- LTX frame counts must be 8n+1. ffmpeg `zoompan` shakes; use `scale eval=frame` + `crop`.
- This machine's ffmpeg 9.0.1 has **no `drawtext`/`subtitles`/`ass`** filters: captions are Pillow-rendered PNGs composited with `overlay=enable='between(t,a,b)'`.

## Next, in order (after the builds land)
1. Tool freeze. 2. Record `hero-1` with the coach (earbuds, warm front light, hands off the mouth). 3. One anchored render: scene s3 with its Reference Master look as first frame; check identity holds 8s. 4. Render all 4 scenes (`submit_batch`). 5. Stitch v1 in the Director's Cut. 6. Only then: clones, hybrid spike, plugin.

## Open questions (grill in progress)
Freeze tools after the in-flight builds? · GitHub public/private + name · seated or standing performance · Fable on director/QA agents only, or everywhere.
