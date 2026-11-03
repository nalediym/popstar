# workflows/: pinned Comfy Cloud graphs

Every generation step is a committed API-format ComfyUI graph here, plus `slots.json`, which names the inputs a
pipeline fills. `tools/comfy_compile.ts` writes values in by name and prints a complete graph to submit.

This replaces the two things that only worked on Naledi's account tonight: re-running past job ids (`from_prompt_id`),
and hand-typed flattened subgraph ids like `129:211`, which move when Comfy updates a template. This is the fix for
risk R1 in `docs/architecture.md`.

| graph | what it does | output | pinned from (2026-11-02) | template |
|---|---|---|---|---|
| `pose-restyle` | **Main renderer.** Restyles a performance clip into a scene while following her motion. LTX-2.3 22B distilled + IC-LoRA union control, guided by a DWPose skeleton of the clip, with an optional hero first frame. | node 68, video | job `650120d9…`: chunk-01, anime neon | `video_ltx2_3_ic_lora`, with the depth (MoGe) guide swapped for DWPose |
| `lipsync-ia2v` | Image + a section of the song → singing video. LTX-2.3 22B dev + distilled LoRA, two stages with a 2x latent upscale. The song is the audio latent. | node 341, video | job `ec3ea736…`: chorus test | `video_ltx2_3_ia2v` |
| `lipsync-infinitetalk` | Image + vocal → singing video. Wan2.1 InfiniteTalk 14B 480p, 848x480 at 25 fps. | node 325, video | job `05f64513…`: chorus comparison | `templates-wan2_1_infinitetalk_music` |
| `song-acestep` | Tags + lyrics → song. ACE-Step 1.5 XL Turbo, mp3 out. | node 111, audio | job `408b363c…`: In the Loop (Devagar) | `audio_ace_step1_5_xl_turbo` |
| `look-wardrobe` | One frame → new wardrobe, set and light, keeping her face, pose and framing. Qwen Image 2.1 Turbo edit. | node 461, image | job `eff061da…`: chorus stage look | `image_qwen_image_2_1_turbo_image_edit` |
| `look-expression` | One image → new expression (head, eyes, mouth). LoadImage → ExpressionEditor → SaveImage. | node 3, image | job `39a4709f…`, cut from 4 editors to 1 | hand-built |

Full job ids, Cloud workflow ids and what changed from each template are in each graph's `provenance` in `slots.json`.
`ltx_ic_lora_pose.json` is the editor (save-format) copy of pose-restyle, for opening on the canvas. The compiler
doesn't use it.

## Use it

1. **Upload inputs first.** Call `upload_file` (comfy-cloud MCP) for each clip, image or audio file, and use the
   **returned name** (a content hash like `ecb50ea3….mp4`), never the local filename.
2. **Compile by slot name.**
   ```sh
   bun tools/comfy_compile.ts --list                     # all graphs
   bun tools/comfy_compile.ts pose-restyle --list        # its slots and pinned defaults
   bun tools/comfy_compile.ts pose-restyle --inputs scene-2.json --set seed=7 --out build/scene-2.json
   bun tools/comfy_compile.ts pose-restyle --set source_video="<clip name>" \
     --set first_frame="<frame name>" --set use_first_frame=true --set first_frame_strength=0.8 --set trim_duration=0
   bun tools/comfy_compile.ts look-expression --preset devagar --set image="<image name>" \
     --set output_prefix=reference-master/chorus_devagar
   ```
   Later values win: pinned defaults < `--preset` < `--inputs` (a JSON object `{"slot": value}`) < `--set`. These
   are all errors, and nothing gets printed: an unknown slot, a wrong type, an out-of-range or off-list value, or a
   slot map that no longer matches its graph (the error names the slot and the missing node or input).
   `scene-2.json` looks like this (strings shortened here):
   ```json
   {"source_video": "ecb50ea3….mp4", "prompt": "A young Black woman with deep brown skin … claymation …",
    "negative": "blurry, out of focus, …", "seed": 7}
   ```
3. **Check for free, then submit** the compiled JSON with the comfy-cloud MCP. Pass the whole object, not a string:
   first `submit_workflow({workflow: <build/scene-2.json>, dry_run: true})` should return `validated`, then make the
   same call without `dry_run`. **That second call spends GPU credits.** For several scenes, send one `submit_batch`.
   Then `wait_for_job` / `get_output`.

## Slots

`default` is the value in the pinned graph. Uploads are content-hash names in Naledi's workspace.

### pose-restyle

| slot | node.input | type | default | notes |
|---|---|---|---|---|
| `source_video` | `199.file` | string | her upload | Performer clip: upload_file it, pass the returned name. Its DWPose skeleton drives the motion. Cut it at 24 fps (= fps). |
| `trim_start` | `692.start_time` | number | `0` | Seconds into the clip where the render starts. |
| `trim_duration` | `692.duration` | number 0.. | `5` | Seconds of clip to render; 0 = the whole clip. The pinned 5 caps renders at 5 s: fine for the 73-frame test chunks, too short for 8 s scenes. Use 0 with a clip pre-cut to 8n+1 frames. |
| `first_frame` | `200.image` | string | her upload | Hero/look frame (upload_file name). Used only when use_first_frame=true. |
| `use_first_frame` | `129:198.bypass` (inverted) | boolean | `false` | true = hold frame 0 to first_frame (writes bypass=false on LTXVImgToVideoInplace 129:198). |
| `first_frame_strength` | `129:198.strength` | number 0..1 | `1` | How hard frame 0 follows first_frame (0.7-1.0 used). |
| `prompt` | `129:211.on_false` | string | (629-char text) | Scene prompt (the If/Else branch used while prompt enhance is off). Keep the identity + skin-tone lock in it. |
| `negative` | `129:112.text` | string | (344-char text) | Negative prompt. |
| `seed` | `129:704.seed` | integer 0.. | `42` | KSampler seed. |
| `pose_strength` | `129:115.strength` | number 0..10 | `1` | IC-LoRA pose guide strength (union control is built for 1.0). |
| `pose_resolution` | `901.resolution` | integer 64..16384 | `704` | DWPose detection resolution in px. |
| `fps` | `129:114.value` | integer 1.. | `24` | Frame rate of the latent, audio and output. Match the clip. |
| `width` | `129:113.value` | integer 64.. | `1280` | Output width. Keep it a multiple of 32. |
| `height` | `129:98.value` | integer 64.. | `704` | Output height. Keep it a multiple of 32. |
| `output_prefix` | `68.filename_prefix` | string | `"video/ltx2.3_ic_lora"` | Output filename prefix on Comfy Cloud. |

### lipsync-ia2v

| slot | node.input | type | default | notes |
|---|---|---|---|---|
| `image` | `269.image` | string | her upload | Performer/look still (upload_file name). |
| `audio` | `276.audio` | string | her upload | The song (upload_file name). audio_start + duration pick the section. |
| `audio_start` | `340:332.start_index` | number | `17` | Seconds into the song where the clip starts. |
| `duration` | `340:331.value` | number 0.. | `8` | Clip length in seconds. Frames = duration*fps + 1, so keep duration*fps a multiple of 8 (8 s at 24 fps = 193 frames). |
| `prompt` | `340:319.value` | string | (406-char text) | Prompt. Keep the identity + skin-tone lock in it. |
| `negative` | `340:314.text` | string | (58-char text) | Negative prompt. |
| `prompt_enhance` | `340:349.value` | boolean | `false` | true = rewrite the prompt with the Gemma enhancer first. |
| `image_strength` | `340:325.strength` | number 0..1 | `0.7` | Stage-1 strength of the still on frame 0 (stage 2 stays at 1.0). |
| `seed` | `340:286.noise_seed` | integer 0.. | `11` | Stage-1 noise seed. |
| `refine_seed` | `340:285.noise_seed` | integer 0.. | `42` | Stage-2 (upscaled refine) noise seed. |
| `fps` | `340:323.value` | integer 1.. | `24` | Frame rate. |
| `width` | `340:330.value` | integer 64.. | `1280` | Output width. Stage 1 samples at half size. |
| `height` | `340:324.value` | integer 64.. | `720` | Output height. Stage 1 samples at half size. |
| `output_prefix` | `341.filename_prefix` | string | `"video/LTX_2.3_ia2v"` | Output filename prefix on Comfy Cloud. |

### lipsync-infinitetalk

| slot | node.input | type | default | notes |
|---|---|---|---|---|
| `image` | `284.image` | string | her upload | Performer/look still (upload_file name). Center-cropped to width x height. |
| `audio` | `125.audio` | string | her upload | Exactly the section to sing (upload_file name). This graph does not trim the audio: cut it first. |
| `num_frames` | `330:337.num_frames` | integer 1..10000 | `200` | Frames requested at 25 fps. Output rounds up to whole 81-frame windows (9 overlap): 200 gave 225 frames = 9 s, so trim in stitch. |
| `prompt` | `330:332.positive_prompt` | string | (334-char text) | Prompt. Keep the identity + skin-tone lock in it. |
| `negative` | `330:332.negative_prompt` | string | (293-char text) | Negative prompt. |
| `seed` | `330:333.seed` | integer 0.. | `11` | Sampler seed. |
| `width` | `330:340.width` | integer 16..16384 | `848` | Video width (divisible by 16). |
| `height` | `330:340.height` | integer 16..16384 | `480` | Video height (divisible by 16). |
| `output_prefix` | `325.filename_prefix` | string | `"video/ComfyUI"` | Output filename prefix on Comfy Cloud. |

### song-acestep

| slot | node.input | type | default | notes |
|---|---|---|---|---|
| `tags` | `94.tags` | string | (244-char text) | Style tags: genre, instruments, vocal, mood, BPM. |
| `lyrics` | `94.lyrics` | string | (331-char text) | Lyrics with [Intro] / [Verse] / [Chorus] section tags. |
| `duration` | `94.duration` + `98.seconds` | number 1..1000 | `32` | Song length in seconds (written to the text encoder and the empty latent). |
| `bpm` | `94.bpm` | integer 10..300 | `92` | Tempo. |
| `keyscale` | `94.keyscale` | string | `"Bb major"` | Key; one of the 34 the node lists (C major … B minor). |
| `timesignature` | `94.timesignature` | string | `"4"` | Beats per bar: 2, 3, 4 or 6. |
| `language` | `94.language` | string | `"en"` | Lyrics language code (en, pt, ...). |
| `seed` | `109.value` | integer 0.. | `11` | Seed. One Int node feeds both the sampler and the encoder. |
| `output_prefix` | `111.filename_prefix` | string | `"audio/ACE_Step1.5_xl_turbo"` | Output filename prefix on Comfy Cloud. |

### look-wardrobe

| slot | node.input | type | default | notes |
|---|---|---|---|---|
| `image` | `470.image` | string | her upload | Source frame (upload_file name). A neutral, lips-closed frame works best. |
| `prompt` | `459:484.on_false` + `459:500.prompt` | string | (640-char text) | Edit instruction: keep identity, pose and framing; change wardrobe, set and light. Also written to the enhancer input 459:500.prompt, as the template expects. Keep the identity + skin-tone lock in it. |
| `prompt_enhance` | `459:484.switch` | boolean | `false` | true = rewrite the instruction with the Qwen3.5 enhancer first. |
| `seed` | `459:504.noise_seed` | integer 0.. | `11` | Noise seed. |
| `custom_size` | `459:468.switch` | boolean | `false` | false = output size follows the input image, rounded to /32 (1280x720 in, 1280x704 out); true = use width x height. |
| `width` | `459:456.width` | integer 16.. | `1280` | Used only with custom_size=true. |
| `height` | `459:456.height` | integer 16.. | `720` | Used only with custom_size=true. |
| `output_prefix` | `461.filename_prefix` | string | `"Qwen_image_2.1"` | Output filename prefix on Comfy Cloud. |

### look-expression

| slot | node.input | type | default | notes |
|---|---|---|---|---|
| `image` | `1.image` | string | her upload | Image to edit (upload_file name), e.g. a look-wardrobe output. |
| `rotate_pitch` / `rotate_yaw` / `rotate_roll` | `2.<same>` | number -20..20 | `0` / `0` / `0` | Head nod / turn / tilt. |
| `blink` | `2.blink` | number -20..5 | `0` | Eyelids. |
| `eyebrow` | `2.eyebrow` | number -10..15 | `4` | Eyebrows. |
| `wink` | `2.wink` | number 0..25 | `0` | Wink. |
| `pupil_x` / `pupil_y` | `2.<same>` | number -15..15 | `0` / `0` | Gaze left-right / up-down. |
| `aaa` | `2.aaa` | number -30..120 | `0` | Mouth open ('aaa'). |
| `eee` / `woo` | `2.<same>` | number -20..15 | `0` / `0` | Mouth wide ('eee') / round ('woo'). |
| `smile` | `2.smile` | number -0.3..1.3 | `1.2` | Smile. Strong values read best (1.0-1.2). |
| `sample_parts` | `2.sample_parts` | string | `"OnlyExpression"` | OnlyExpression, OnlyRotation, OnlyMouth, OnlyEyes or All. Use All whenever a rotate_* is non-zero, or the head won't turn. |
| `output_prefix` | `3.filename_prefix` | string | `"reference-master/expression"` | Output filename prefix, e.g. `reference-master/<look_id>_<preset>`. |

Presets (`--preset`) are the four editors of job 39a4709f, each verified on her chorus look:

| preset | sets (all other sliders 0) |
|---|---|
| `soft-smile` | smile 1.2, eyebrow 4 · OnlyExpression (the defaults) |
| `devagar` | blink 4, smile 0.6, rotate_roll 6, rotate_pitch -4, pupil_y -6 · All |
| `sultry-gaze` | pupil_x 8, pupil_y -3, eyebrow -3, smile 0.4, rotate_yaw 8 · All |
| `playful-wink` | wink 18, smile 1, eyebrow 2 · OnlyExpression |

## Gotchas

- **Skip the saved-workflow path** (`save_workflow` → `run_saved_workflow`). On Cloud it reverts LoadImage and bypass
  edits. Submit the compiled API graph with `submit_workflow`.
- **`slot_overrides` fail on these subgraph templates** ("no proxyWidget mapping"). So the slot map writes the
  flattened `<instance>:<inner>` ids (`129:211`) for you. Those ids belong to these pinned graphs: don't re-derive
  them from a newer copy of the template.
- **LTX frame counts must be 8n+1.** pose-restyle renders the frames inside the trim window: the DWPose frame count
  sets the latent length, and `LTXVAddGuide` crops a guide that isn't 8n+1 frames. Pre-cut clips to 8n+1 frames at
  24 fps (73 = 3.04 s, 121 = 5.04 s, 193 = 8.04 s) and set `trim_duration=0`. The pinned 5 caps every render at 5 s.
  lipsync-ia2v makes duration*fps + 1 frames, so keep duration*fps a multiple of 8.
- **Upload with `upload_file` first, then pass the returned name.** The pinned defaults point at uploads in Naledi's
  workspace. Other accounts can't count on them, so always set `source_video` / `first_frame` / `image` / `audio`.
- **Put the identity + skin-tone lock in every prompt** ("a young Black woman with deep brown skin …"), or the model
  lightens her skin.
- look-wardrobe: `width`/`height` do nothing unless `custom_size=true`. Without it, the output follows the input image.
- lipsync-infinitetalk doesn't trim the audio and runs at 25 fps. Upload only the section, and expect the output to
  round up to whole windows.
- Seeds above 2^53-1 can't stay exact in JSON, so the compiler refuses them.
- API format is keyed by node id, and ComfyUI reads every top-level key as a node. Don't add notes or provenance keys to
  `*.api.json`; that information lives in `slots.json` and here.

## Receipts (2026-11-02)

- **Equivalence.** Compiling pose-restyle with chunk-02's four values (`source_video`, `prompt`, `negative`, `seed=7`),
  read from job `ce7dccfd-40f7-4325-b4f7-134541a46f9b`'s stored graph, gives a deep-equal copy of that graph: 35/35
  nodes, 0 differences, the same canonical sha256 `378d2b538cbd28ca…`. The result is the same via `--inputs` and via
  `--set` alone. Control: the uncompiled defaults differ from it in exactly the 3 inputs the run changed (clip,
  prompt, seed; the negative didn't change).
- **Cloud pre-flight.** `submit_workflow(dry_run: true)` on all six compiled default graphs returned `validated`.
  Nothing was submitted and no GPU time was spent. look-wardrobe came back with 4 advisories: its Qwen model filenames
  aren't in the MCP's bundled node index. That index lags the cloud: job eff061da ran with exactly these names.
- **Generalization.** Each look-expression preset compiles to exactly the inputs of its editor in job 39a4709f.

## Re-pin or add a graph

1. Run it once on Cloud, then call `get_job_status(<prompt_id>, include_workflow: true)`.
2. Save the returned `workflow` object, pretty-printed and with no extra keys, as `workflows/<name>.api.json`.
3. Add `<name>` to `slots.json` with `file`, `what`, `output`, `provenance` (job id, template, date) and `slots`. Each
   slot takes `node`, `input`, `type` (`string` / `integer` / `number` / `boolean`), optional `invert` / `also` /
   `min` / `max` / `enum`, plus `default` and `desc`.
4. `bun tools/comfy_compile.ts <name> --list` checks the map against the graph. A missing node or input is an error
   that names the slot; a default that drifted from the graph is a warning.
5. Dry-run the compiled default graph.
