---
name: reference-master
description: >-
  Design, render and edit per-scene looks for the music video (wardrobe, props and set, light, and an
  emotion with editable eyes, mouth and head) on Comfy Cloud, keeping reference-master/looks.json in sync.
  Use when Naledi says "Reference Master", "render the queue", "make a look", "wardrobe for scene X",
  "change her expression, smile or eyes", pastes a Reference Master render instruction, or adds
  Pinterest or other reference images.
---

# Reference Master

The wardrobe and look department of the music video. Every scene needs a **look** for each character in it
(the lead, plus two backup-singer clones in the harmony scene). A look is: wardrobe, props, the scene's set and light,
and an emotion stored as 12 Expression Editor slider values. The rendered look becomes the scene's reference
and first frame for the LTX video pass.

## Files
The project is `${CLAUDE_PROJECT_DIR}`, the directory with `popstar.json`; every path below is relative to it. The tool
(`reference-master/server.ts`) ships with the repo (https://github.com/nalediym/popstar), not with this plugin; the two
pinned look graphs do ship here (see "Portable graphs" below).
- `reference-master/looks.json`: scenes, looks, emotion presets, `identity_lock`. The source of truth. Naledi edits it in the tool.
- `reference-master/queue.json`: render requests from the tool (`{look_id, what: wardrobe|expression|both}`).
- `reference-master/refs/<look_id>/`: reference images (pasted Pinterest pins, drag-and-drop). A starred `role: "wardrobe"` ref is THE outfit reference.
- `reference-master/renders/<look_id>/`: your outputs (`wardrobe.png`, `expression.png`).
- `reference-master/tests/`: the 2026-11-02 feasibility renders (keep as examples).
- Tool: `bun reference-master/server.ts` (or the `reference-master` entry in `.claude/launch.json`).

## Render recipe (verified 2026-11-02 on Comfy Cloud, open weights only)
1. **Source frame.** Use `look.source_frame`; for a clip moment, extract with ffmpeg (or the tool's `/frames`).
   Prefer a neutral, lips-closed frame (the `face` take). A pursed mid-lip-sync mouth fights smiles.
   Upload with `upload_file`, then run the returned curl and keep the `name`.
2. **Wardrobe and set.** `run_template` `image_qwen_image_2_1_turbo_image_edit`, `input_overrides` only
   (its `slot_overrides` fail with "no proxyWidget mapping"):
   ```json
   {"470": {"image": "<uploaded name>"},
    "459:484": {"on_false": "<PROMPT>"}, "459:500": {"prompt": "<PROMPT>"},
    "459:456": {"width": 1280, "height": 720}, "459:504": {"noise_seed": 11}}
   ```
   PROMPT = "Keep {identity_lock}, her exact pose, hand position and camera framing. Change only her wardrobe and
   the set. She now wears {wardrobe}{, props}. The background becomes {scene.set}. Lighting: {scene.light}."
   Output (node 461) is 1280×704. Takes about 20s. Identity, skin tone, hair and earbuds held in the test. The hand pose was not perfectly kept.
   **Wardrobe reference image (Pinterest ref): NOT verified yet.** The template exposes `images.image_2` with no
   loader wired. It needs a structural edit (get_template → add LoadImage → submit_workflow) plus "dress her in the
   outfit from image 2" in the prompt. Test it once before promising it.
   Portable alternative (works on any account, same inputs): compile the pinned `look-wardrobe` graph and `submit_workflow` it:
   `bun ${CLAUDE_PLUGIN_ROOT}/tools/comfy_compile.ts look-wardrobe --set image=<uploaded name> --set prompt="<PROMPT>" --set seed=11 --out build/looks/<look_id>.wardrobe.json`
3. **Expression.** Upload the wardrobe render, then `submit_workflow` with an API graph per look:
   `LoadImage → ExpressionEditor → SaveImage`. Several looks can share one graph (one Editor + SaveImage pair each).
   ExpressionEditor inputs: the 12 sliders, plus `src_ratio 1`, `sample_ratio 1`, `crop_factor 1.7`, `src_image ["<load id>", 0]`,
   `sample_parts "OnlyExpression"`. Use `"All"` whenever any `rotate_*` is non-zero, or the head won't move.
   Slider ranges: pitch/yaw/roll −20..20, blink −20..5, eyebrow −10..15, wink 0..25, pupil_x/y −15..15,
   aaa −30..120, eee −20..15, woo −20..15, smile −0.3..1.3.
   **Use strong values.** smile 0.7 on a pursed mouth barely shows; smile 1.2 + eyebrow 4 reads clearly.
   Verified presets: soft-smile (smile 1.2, eyebrow 4) · devagar (blink 4, smile 0.6, roll 6, pitch −4, pupil_y −6, All) ·
   sultry-gaze (pupil_x 8, pupil_y −3, eyebrow −3, smile 0.4, yaw 8, All) · playful-wink (wink 18, smile 1.0, eyebrow 2).
   The same presets are `--preset` names of the pinned `look-expression` graph:
   `bun ${CLAUDE_PLUGIN_ROOT}/tools/comfy_compile.ts look-expression --preset devagar --set image=<uploaded name> --set output_prefix=reference-master/<look_id>_devagar --out build/looks/<look_id>.devagar.json`
4. **Write back.** Download each output (`get_output` gives `/api/s/<id>?raw=1` shortlinks; curl them) into
   `reference-master/renders/<look_id>/`. Re-read `looks.json` fresh, change ONLY that look's `renders` and `status`
   (`wardrobe-done` / `done`), and write it back. Never overwrite her text or slider edits. Remove the handled entries from `queue.json`.
   Then show her a sheet (ffmpeg hstack of before/after face crops).

## Look design (the R&B director's eye; see docs/research/rnb-video-language.md)
- Palette: Tuscan brown, burnt orange, orange, warm amber. One color wash per phrase.
- Skin: always "rich deep brown skin with warm undertones and a luminous dewy sheen". Never let a render lighten her.
  If it does, add the line again and re-run; don't ship it.
- Light: large soft warm key + gold bounce + amber rim through haze; candlelight; fairy-light bokeh. Restraint is the seduction.
- Wardrobe ideas ('90s/2000s R&B, in palette): silk slip dress + gold hoops; oversized cream knit; fitted ribbed top + satin cargos;
  matching girl-group satin sets for the clones, a shade darker than the lead's.
- Emotion per section: intro = still, eyes down; verse = soft smile at line ends, devagar blink; chorus = eyes to lens,
  soft smile or sultry gaze; harmony = playful wink for the clones, the lead holds the gaze.

## Portable graphs
`${CLAUDE_PLUGIN_ROOT}/tools/workflows/` holds `look-wardrobe.api.json` and `look-expression.api.json` with their slot map
(`slots.json`); `bun ${CLAUDE_PLUGIN_ROOT}/tools/comfy_compile.ts <graph> --list` prints every slot, range and pinned default.
In a checkout of the repo without the plugin the same files are `workflows/` and `bun tools/comfy_compile.ts`.
Always `submit_workflow({workflow: <compiled JSON>, dry_run: true})` first (free), then without `dry_run` (spends credits).

## Pinterest
There is no connector that reads boards. Checked 2026-11-02: the plugin directory only has pin-creation tools
(InsightPins, Tailwind), and the MCP registry has nothing. References come in through the tool's refs tray: paste a pin
link (the server reads the pin page's og:image), paste a direct image link, or drag and drop.

## Gotchas
- zsh doesn't word-split unquoted variables (use `bash -c` or arrays). `cp`/`mv`/`rm` are interactive aliases: use `/bin/cp`, `/bin/mv`, `/bin/rm`.
- Template `slot_overrides` often fail on subgraph templates; the error message names the flattened `"<instance>:<node>"` id to use instead.
- Port 56101 belongs to the coach recorder preview.
