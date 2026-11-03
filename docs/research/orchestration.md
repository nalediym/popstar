# Orchestration research: selfie lip-sync clip → AI music video

Researched 2026-11-02, ~21:00–22:30 ET, for the 2026-11-03 12:00 ET deadline. Open weights only.
Labels: **VERIFIED** means a source, the live Comfy Cloud catalog, or a local run shows it.
**INFERRED** means my reasoning, not yet tested. Sources are listed at the bottom as [S1]–[S20].

## Recommendations at a glance

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Hybrid lip-sync | The wiring is valid, but there is a public report of it failing. Run one 30-min spike (3 variants, one batch). If it fails, use pose-only. Your DWPose face points already carry your own lip-sync. |
| 2 | Identity across scenes | Make a hero frame per scene from your own selfie frame (Qwen Image 2.1 Edit). Feed it as first frame at 0.7–1.0. Repeat one character description verbatim. Grade everything with one ffmpeg grade. |
| 3 | Clones from one pose | Tile the source RGB clip ×3 before DWPose, not the skeleton. Anchor with a tiled hero frame. Fallback: an ffmpeg triptych split-screen, which is deterministic and on-genre. |
| 4 | Beats | `beat_this`. Tested locally on the song: 48 beats, 12 downbeats, ~91 BPM. |
| 5 | Pipeline | `shotlist.json` is the single source of truth. A compiler resolves beats to frames, renders go out through `submit_batch`, outputs are cached by shot hash, and `stitch.py` is pure ffmpeg. |
| 6 | Camera | Do camera moves in post (scale `eval=frame` + crop, tested). Skip Blender. Optional trick: zoom the source clip before DWPose so the model renders a real push-in. |

---

## 1. Hybrid lip-sync: pose guide + fixed song-audio latent in one LTX-2.3 graph

### Evidence the graph shape is legal
- **VERIFIED.** The official `LTX-2.3_ICLoRA_Union_Control_Distilled.json` is already an audio+video graph. `LTXAddVideoICLoRAGuide#5012` feeds `LTXVConcatAVLatent#4528.video_latent`, and `LTXVEmptyLatentAudio#3980` feeds `.audio_latent`. After sampling, `LTXVSeparateAVLatent` → `LTXVCropGuides` → decode, and audio is decoded too [S1]. The pose-guide graph therefore already carries an audio latent. Today it is an empty one that gets generated.
- **VERIFIED.** The `video_ltx2_3_ia2v` template fixes the song like this: `LoadAudio → TrimAudioDuration(start_index, duration) → LTXVAudioVAEEncode → SetLatentNoiseMask(mask = SolidMask value 0.0, 1024×1024) → LTXVConcatAVLatent.audio_latent`. The video side is `LTXVImgToVideoInplace` at strength 0.7 in stage 1 and 1.0 in stage 2. Stage 2 reuses the separated audio latent from stage 1 [S3].
- **VERIFIED.** Lightricks' own `LTX-2.3_ICLoRA_DubIt_Two_Stage_Distilled.json` puts an IC-LoRA video guide (`LTXAddVideoICLoRAGuide`) and an *encoded* audio latent (`LTXVAudioVAEEncode → LTXVSetAudioRefTokens`) in the same AV sampling pass. Stage 2 then freezes the audio [S2]. So IC-LoRA guide plus real audio in one pass is a pattern Lightricks ships.
- **VERIFIED.** Your own `workflows/ltx_ic_lora_pose.json`, subgraph `129`, has the same slot: `LTXVEmptyLatentAudio#101 → LTXVConcatAVLatent#119.audio_latent`, with `LTXVAddGuide#115 → #119.video_latent`. The audio VAE is `LTXVAudioVAELoader#126`. `LTXVImgToVideoInplace#198` currently has `bypass=True`.

### Evidence it fails, or is fragile
- **VERIFIED (community report, no fix).** HF thread RuneXX/LTX-2.3-Workflows #66 used exactly this wiring: image + union-control-ref0.5 (DWPose/depth) + `LoadAudio → LTXVAudioVAEEncode → SetLatentNoiseMask → LTXVConcatAVLatent`. Image+audio alone lip-synced, and pose alone moved well. Combined, "lipsync completely disappears". They tried IC-LoRA strength 0.3–1.0, guide strength 0.2–1.0, and DWPose with `detect_face` off, and none of it helped. A second user says guides from `LTXVAddGuide`/AddGuideMulti "fight the audio lip sync". The thread has no resolution [S4].
- **VERIFIED (fragility of a2v in general).** In ComfyUI-LTXVideo issue #438, ia2v lips don't move for many users. Anecdotal fixes are putting "lip-syncs" in the prompt, bypassing the prompt enhancer, and raising SolidMask 0.0→0.5 [S5]. Mickmumpitz claims `SetLatentNoiseMask(0)` makes the model treat the audio as "clean", which freezes the mouth. He fixes it with a custom `AudioTimestepOverride` node [S6]. **VERIFIED:** that node is *not* on Comfy Cloud (`search_nodes` returned 0).
- **VERIFIED (levers on Cloud).** `LTXVModalityGuidance` (core) is "strengthening audio-visual sync (e.g. lip-sync)", default `modality_scale` 3.0, and costs one extra forward pass per step. `LTXVDualCFGGuider` gives a separate `audio_cfg`. KJNodes `LTX2AttentionTunerPatch` exposes `audio_to_video_scale` [S7]. Whether any of these beats IC-LoRA dominance is **INFERRED / untested**.

### Why your case may still work where #66 failed (INFERRED)
In #66 the motion video and the TTS audio were *unrelated*, so pose and audio pulled in opposite directions.
Your pose source is *you lip-syncing this same song*, so DWPose's 68 face points (mouth included) already
open and close on the lyrics. Pose and audio agree if they are time-aligned. Rules:
- Keep DWPose `detect_face` **enabled** (your graph has it on). Raise DWPose resolution (704→1024) so the mouth dots aren't sub-pixel.
- Trim the audio with `TrimAudioDuration.start_index = scene.t0 + sync_offset`, the same window as the video chunk.
- You mux the clean song in ffmpeg anyway, so drift in the generated audio is harmless. That means you can safely try SolidMask 0.3–0.5 ("opens the gate", per [S5][S6]).

### Exact spike wiring (edit inside subgraph 129, API-format)
```
LoadAudio(song.mp3) ─► TrimAudioDuration(start_index=t0, duration=len) ─► LTXVAudioVAEEncode(audio_vae=#126)
SolidMask(value=M, width=1024, height=1024) ─────────────────────────────► SetLatentNoiseMask.mask
LTXVAudioVAEEncode ─► SetLatentNoiseMask.samples ─► LTXVConcatAVLatent#119.audio_latent   (delete #101)
LTXVAddGuide#115 (pose video, strength G) ─► #119.video_latent                              (unchanged)
model chain: LoraLoaderModelOnly#195 ─► [LTXVModalityGuidance(modality_scale=S)] ─► KSampler#704.model
prompt: add "she lip-syncs the lyrics, mouth clearly forming each word"   (anecdotal fix from [S5])
```
`from_prompt_id` overrides cannot add nodes. Patch the API JSON of job `650120d9…` with a small Python
script and send the whole graph via `submit_workflow`. This also avoids the saved-workflow revert bug (INFERRED).

**Spike, one `submit_batch`, same 3 s chorus chunk (73 frames), judge mouths by eye, time-box 30 min:**
| variant | M (mask) | G (guide) | S (modality) |
|---|---|---|---|
| A baseline | 0.0 | 1.0 | off |
| B | 0.0 | 1.0 | 3.0 |
| C | 0.4 | 0.7 | 3.0 |

Untested extra lever (INFERRED): `LTXVAddGuide` has an optional `attention_mask` ("per-region conditioning influence") [S7].
A mask that is 0 over the mouth box and 1 elsewhere would leave the mouth to the audio.

### Fallbacks, in order
1. **Pose-only restyle** (already working). Mouth comes from your DWPose face points. First check `outputs/chunk-0*.mp4` for visible mouth motion.
2. **Wan2.2 Animate** (`video_wan2_2_14B_animate`, or `video_wan_animate2` on Cloud, both open weights): "precise motion and expression replication" from the driving video [S8]. The paper targets "motion and facial expressions" [S9]. The lips are copied from your selfie performance and you mux the song. INFERRED to beat option 1 on mouth fidelity, but untested and slower.
3. **Edit-level hybrid:** cut between ia2v close-ups (verified lip-sync) on lyric lines and pose-guided wide or hands shots. This is standard music-video grammar and needs zero model risk.

## 2. Identity across 4 separately rendered scenes
- **Anchor on the real person (INFERRED, strongest lever).** For each scene, grab the selfie frame at `t0`. Restyle it into the scene's set with an open-weights editor. **VERIFIED on Cloud:** `image_qwen_image_2_1_image_edit` ("latest open-weight Qwen-Image"), `image_qwen_image_edit_2511_int8`, `image_flux3_klein_image_edit` [S7]. The hero frame then has *your pose at t0*, so it doesn't fight the skeleton.
- Feed the hero frame to `LTXVImgToVideoInplace#198` (set `bypass=false`) at **0.7–1.0**. **VERIFIED:** ia2v uses 0.7 in stage 1 and 1.0 in stage 2 [S3]. The union workflow has the same `bypass_i2v` toggle on `LTXVImgToVideoConditionOnly` [S1].
- **Character description verbatim** in every prompt and every edit prompt (**VERIFIED** as Comfy Cloud's own guidance, "repeating ONE fixed character description verbatim"). Keep your PLAN's skin-tone line ("a young Black woman with deep brown skin…"), because without it the model lightens skin.
- **Seeds:** fix one per scene in the shot list, for reproducibility and cheap re-renders. Seeds don't carry identity *across* scenes (INFERRED).
- **IC-LoRA strength:** keep union at 1.0 (designed for full strength). 0.5–0.8 trades structure for freedom [S10][S11]. It controls structure, not identity.
- `LTXAddVideoICLoRAGuide.frame_idx = -1` places a single-image reference "outside the generated frame range… for attention conditioning" (**VERIFIED** tooltip [S7]). That is meant for reference-trained IC-LoRAs, so don't rely on it with union (INFERRED).
- **Glue in post (INFERRED):** one shared ffmpeg grade (curves/eq/vignette, Tuscan amber) on all scenes hides residual drift.

## 3. Three people from one pose (lead + 2 backup clones)
- **VERIFIED (closest evidence):** a community LTX-2.3 pose IC-LoRA (DWPose 133-kp) reports "multi-character pose tracking works when 2-person reference videos are used" [S12]. Multi-person pose-driven dance is an active research topic [S13]. **No source tests a duplicated skeleton.**
- **Do (INFERRED):** tile the **RGB source clip** before upload, so the existing in-graph `DWPreprocessor` detects 3 people. No graph change is needed. The RGB only feeds DWPose, so black padding is fine. Lead is a full-scale 560 px center strip, and the backups are 0.6×, mirrored on the left, and raised (further back). The filter runs as written (tested on a 1280×704 source, output 1280×704):
  ```
  ffmpeg -i chunk_1280x704.mp4 -filter_complex "[0]split=3[a][b][c];[a]crop=560:704:(iw-560)/2:0[lead];\
  [b]scale=768:-2,crop=360:ih:(iw-360)/2:0,hflip,pad=360:704:0:704-ih-60[l];\
  [c]scale=768:-2,crop=360:ih:(iw-360)/2:0,pad=360:704:0:704-ih-60[r];[l][lead][r]hstack=3" -c:a copy tiled.mp4
  ```
  Also tile the hero frame the same way and use it as first frame at 1.0. Otherwise the 3 faces drift into 3 different people.
- **Expect (INFERRED):** backup faces at 0.6× of 1280×704 come out soft. That's fine for dim, soft-focus backup singers, and their mouths match because they share your face points.
- **Deterministic fallback:** an ffmpeg triptych of one lead render (`crop` 3 strips → side ones `hflip` + darker grade → `hstack`). It is the classic '90s R&B split-screen look, costs zero GPU, and can't fail.

## 4. Beat and downbeat detection
- **Pick `beat_this` (CPJKU, ISMIR 2024, MIT code and weights).** It does beats and downbeats in one call, with no madmom/DBN needed [S14][S15]. **VERIFIED locally just now:**
  ```
  uv run --python 3.12 --with beat-this --with soundfile python -c "from beat_this.inference import File2Beats; \
  b,d=File2Beats(checkpoint_path='final0',device='cpu',dbn=False)('songs/in-the-loop-devagar.mp3'); print(b,d)"
  ```
  Results: 48 beats, 12 downbeats, median **90.9 BPM** (not exactly 92, so use detected times, not a grid). It took about 18 s on CPU after a one-time 80 MB checkpoint download.
  Downbeats: `1.68 4.28 6.90 9.50 12.12 14.72 17.34 19.94 22.56 25.16 27.78 30.38` (bar ≈ 2.61 s). Your chorus hits (23.5/25/27.5) sit near the 22.56/25.16/27.78 downbeats.
- Use Python 3.12 via uv. System Python is 3.14 and torch wheels lag there (INFERRED).
- **Not:** madmom (PyPI build needs Python<3.10, numpy<1.20; you'd install from git) [S15]. librosa `beat_track` gives beats but no downbeats, and aubio gives beats/onsets only (INFERRED from their APIs). Keep librosa's `onset_strength` if you want per-beat intensity, to decide which beats get a flash.
- **Snap rules:** scene cuts go on downbeats, punch-ins on downbeats, flashes on the strongest-onset beats only (≤1 per bar, or it gets noisy). Frame index = `round(t*24)`.

## 5. Pipeline shape
- **VERIFIED (negative):** no public repo does the whole LLM-shot-list → ComfyUI batch → beat-synced ffmpeg chain. Pieces exist: BeatSync-Engine and leocodeio/synctest (librosa + PySceneDetect) [S16][S17]. The shape below is INFERRED best practice:
```
director skill ─► shotlist.json (SSOT, hand-editable)
  └─ compile.py: validate · beats→seconds→frames (8n+1) · build per-shot API JSON · hash(shot)
       └─ render: submit_batch (hero frames) ─► submit_batch (video shots) ─► wait_for_batch ─► get_output
            └─ cache outputs/shots/<hash>.mp4 (re-render only changed hashes)
                 └─ stitch.py: trim each to [t0,t1) · grade · beat FX · clones · captions · concat · mux clean song
```
- Hand-choreography cues can't be *prompted* when pose drives motion. Your recorded hands are the hands, so the shot list's `hands[]` cues must drive the **recorder's on-screen cue cards** (INFERRED, and important).
- LTX frame counts must be 8n+1 (**VERIFIED**, LTXVAddGuide tooltip [S7]). Render `ceil8(dur*24)+1` and trim in stitch. ia2v's default 9 s → 217 frames shows ~9 s is in range [S3]. Test one 193-frame render before batching 4, since cost scales faster than linear (INFERRED).
- Song-to-clip **sync offset** (recorder latency) is one number in the shot list and is applied everywhere.

### Proposed `shotlist.json` (scenes snapped to the detected downbeats, 3 bars each)
```json
{
  "version": 1,
  "song": { "file": "songs/in-the-loop-devagar.mp3", "fps": 24, "bpm_detected": 90.9,
            "beats_file": "build/beats.json", "lyrics_file": "build/lyrics_aligned.json" },
  "source_clip": "clips/take-01.mp4",
  "sync_offset_s": 0.00,
  "character": {
    "description": "a young Black woman with deep brown skin and copper-red hair pulled back, small gold hoops",
    "negative": "pc game, cartoon, lighter skin, extra people"
  },
  "look": { "palette": ["tuscan brown", "burnt orange", "warm amber"], "grade": "amber_soft_v1" },
  "render_defaults": {
    "engine": "ltx23_pose", "lipsync": "pose_face",
    "width": 1280, "height": 704, "ic_lora_strength": 1.0, "guide_strength": 1.0,
    "i2v_strength": 0.8, "audio_mask": 0.0, "modality_scale": null
  },
  "scenes": [
    { "id": "s1_intro_verse", "t0": 0.00, "t1": 9.50, "section": "intro+verse", "frames": 233, "seed": 4101,
      "set": "cozy living room, string lights, rain on the window", "lighting": "warm practical lamps, low key",
      "emotion": "soft, inviting, eyes to lens", "prompt_extra": "slow sway, close medium shot",
      "hero_frame": { "source_t": 0.40, "edit_prompt": "same woman in an oversized knit sweater on a velvet couch" },
      "camera": { "model": "static", "post": [ { "fx": "slow_push", "from": 1.0, "to": 1.08 } ] },
      "hands": [ { "beat": 4, "cue": "palm to chest" }, { "beat": 8, "cue": "fingertips trace jaw" } ],
      "fx": [ { "on": "downbeats", "type": "punch_in", "amount": 0.06 } ],
      "harmony": false },
    { "id": "s2_verse_b", "t0": 9.50, "t1": 17.34, "section": "verse", "frames": 193, "seed": 4102,
      "set": "kitchen at night, steam from mugs", "lighting": "single amber key, hard rim",
      "emotion": "playful, smirk on 'harness'", "camera": { "model": "static", "post": [] },
      "hands": [ { "beat": 18, "cue": "point to lens" } ],
      "fx": [ { "on": "beats:strong", "type": "flash", "frames": 2 } ], "harmony": false },
    { "id": "s3_chorus_a", "t0": 17.34, "t1": 25.16, "section": "chorus", "frames": 193, "seed": 4103,
      "lipsync": "hybrid", "audio_mask": 0.4, "modality_scale": 3.0,
      "set": "rooftop, holiday city bokeh", "lighting": "orange sodium + teal fill",
      "emotion": "belting, eyes closed on the long note",
      "camera": { "model": "source_zoom", "post": [ { "fx": "punch_in", "at": [22.56], "amount": 0.15 } ] },
      "hands": [ { "beat": 30, "cue": "both arms open" } ], "harmony": false },
    { "id": "s4_chorus_b_harmony", "t0": 25.16, "t1": 32.00, "section": "chorus", "frames": 169, "seed": 4104,
      "set": "studio cyc, amber gradient", "lighting": "three soft spots",
      "emotion": "confident, unified", "camera": { "model": "static", "post": [] },
      "hands": [ { "beat": 40, "cue": "synchronized snap" } ],
      "harmony": true, "clones": { "mode": "tile_source", "count": 3, "backup_scale": 0.6, "fallback": "triptych" } }
  ],
  "captions": { "style": "lower_third_serif", "lines_from": "lyrics_file" },
  "output": { "dir": "outputs/run-{shotlist_hash}", "final": "outputs/final.mp4" }
}
```
Fields left out of a scene inherit `render_defaults`. `beat` indexes into `beats.json`, and the compiler resolves beat → seconds → frame.

## 6. Camera moves: post vs prompt vs Blender
- **Post (recommended).** The pose skeleton is drawn in pixel space, so framing follows your selfie. Prompted moves fight the guide (INFERRED; consistent with "output follows the person's motion" in your runs).
- **VERIFIED:** the Cloud camera LoRAs are `ltx-2-19b-lora-camera-control-{dolly-in,out,left,right,jib-up,down,static}`. They are LTX-2 **19B**, so INFERRED incompatible with 2.3 22B. A `LTX2.3-22B_IC-LoRA-Cameraman_v1` exists, but it would be a second IC-LoRA, and docs advise against mixing IC-LoRA control types [S11].
- **Avoid `zoompan` on video.** Shake bug trac #4298 is still open in 2025 [S18]. **VERIFIED locally (ffmpeg 9.0.1):** `scale … eval=frame` with a `t` expression + per-frame `crop` gives smooth punch-ins (frame sizes went 1472×808 → 1312×722 over 6 frames, with constant 1280×704 out). The compiler generates Z from the beat list:
  ```
  Z="(1+0.15*(between(t,22.56,22.96)*exp(-(t-22.56)/0.12)+between(t,25.16,25.56)*exp(-(t-25.16)/0.12)))"
  -vf "scale=w='trunc(1280*$Z/2)*2':h='trunc(704*$Z/2)*2':eval=frame,crop=1280:704:(in_w-1280)/2:(in_h-704)/2,\
       eq=brightness='if(between(t,22.56,22.64),0.35,0)':eval=frame"
  ```
  The max zoom is 1.15, so render at 1280×704 and accept the slight softening, or crop-zoom ≤10% for sharpness [S19].
- **Model-baked moves (optional, INFERRED, one test):** apply the push-in to the *source clip* before DWPose. The skeleton grows, and LTX renders a real dolly with a consistent background, which beats a 2D crop on a hero shot.
- **Blender: overkill for this deadline (INFERRED).** Beat-keyed ffmpeg expressions already cover punch, push, shake and whip. Blender previz adds a tool and an export step and gives nothing the shot list can't express. Revisit only for a 3D lyric-typography layer after the video works.

## Sources
- [S1] Lightricks, LTX-2.3 IC-LoRA Union Control example: https://github.com/Lightricks/ComfyUI-LTXVideo/blob/master/example_workflows/2.3/LTX-2.3_ICLoRA_Union_Control_Distilled.json (repo: https://github.com/Lightricks/ComfyUI-LTXVideo)
- [S2] Lightricks, Dub-It two-stage example: https://github.com/Lightricks/ComfyUI-LTXVideo/blob/master/example_workflows/2.3/LTX-2.3_ICLoRA_DubIt_Two_Stage_Distilled.json
- [S3] Comfy template `video_ltx2_3_ia2v`: https://cloud.comfy.org/templates/video_ltx2_3_ia2v.json
- [S4] HF RuneXX/LTX-2.3-Workflows #66 (motion control + input audio → lipsync lost): https://huggingface.co/RuneXX/LTX-2.3-Workflows/discussions/66
- [S5] ComfyUI-LTXVideo issue #438 (ia2v lips not moving): https://github.com/Lightricks/ComfyUI-LTXVideo/issues/438
- [S6] Mickmumpitz, LTX 2.3 vid2vid lip sync (SetLatentNoiseMask gate, AudioTimestepOverride): https://mickmumpitz.ai/posts/free-workflow-2-155607570
- [S7] Comfy Cloud live catalog via `comfy-cloud` MCP `get_node`/`search_nodes`/`search_templates`/`search_models`, 2026-11-03 (LTXVModalityGuidance, LTXVDualCFGGuider, LTX2AttentionTunerPatch, LTXVAddGuide, LTXAddVideoICLoRAGuide, Qwen/FLUX edit templates, camera LoRAs)
- [S8] Comfy template Wan2.2 Animate: https://cloud.comfy.org/templates/video_wan2_2_14B_animate.json · Wan Animate 2: https://www.comfy.org/workflows/video_wan_animate2
- [S9] Wan-Animate paper: https://arxiv.org/abs/2509.14055
- [S10] LTX docs, Union Control: https://docs.ltx.io/open-source-model/feature-guides/structural-control/union-control
- [S11] LTX docs, LoRAs (IC-LoRA at 1.0; don't mix control types): https://docs.ltx.io/open-source-model/usage-guides/lo-ra
- [S12] SyFeee LTX-2.3 pose IC-LoRA card (2-person works): https://huggingface.co/SyFeee/ltx2.3-chinese-drama-iclora-pose
- [S13] "Do You Guys Want to Dance" (multi-person dance generation): https://arxiv.org/abs/2401.13363
- [S14] Beat This! paper: https://arxiv.org/abs/2407.21658
- [S15] beat_this repo: https://github.com/CPJKU/beat_this · PyPI: https://pypi.org/project/beat-this/
- [S16] BeatSync-Engine (merserk): https://github.com/merserk
- [S17] leocodeio/synctest: https://awesome.ecosyste.ms/projects/github.com%2Fleocodeio%2Fsynctest
- [S18] FFmpeg trac #4298 zoompan shake (2025 activity): https://ffmpeg.org/pipermail/ffmpeg-trac/2025-March/072904.html
- [S19] FFmpeg filters, `scale` (eval=frame, t): https://ffmpeg.org/ffmpeg-filters.html#scale-1
- [S20] Sync-LoRA for LTX-2.3 (video-only edited-first-frame + motion; alt identity tool): https://huggingface.co/SagiPolaczek/LTX-2.3-Sync-LoRA
