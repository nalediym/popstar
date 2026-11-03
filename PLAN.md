# Glitch Karaoke — plan (locked 2026-11-02, 20:00 ET)

Deadline: **2026-11-03 12:00 ET** (Open Source Higgsfield). Context: `hackathon-context.json`.

## The thing
Lip-sync to an AI-made song on camera. At the song's dramatic peaks, LTX restyles you
into a new style and the lyric line slams on screen. The rest of the time you're in one calm style.

## Pipeline
1. **Song** — ACE-Step 1.5 via native ComfyUI nodes (`TextEncodeAceStepAudio1.5`,
   `EmptyAceStep1.5LatentAudio`). We write the lyrics ourselves. Original, so no IP risk.
2. **Timing** — find 3–5 dramatic peaks (loudness/onset) and align the lyrics to time (Whisper).
3. **Record** — the user lip-syncs to the track for ~16s.
4. **Restyle** — chunk the clip at the peaks. LTX video-to-video on **Comfy Cloud** with pose/depth
   guidance (IC-LoRA via `LTXVAddGuide`), so the restyle follows your motion. One style per chunk.
5. **Stitch** — ffmpeg: concat chunks + glitch flash at each cut + burned-in lyric caption + clean track.

Interface: one command (`glitch my-clip.mp4`). A web page only once the reel looks good.

## Vision v2: "makes you into a pop star in an R&B music video" (grilled 2026-11-02, ~20:50 ET)
Supersedes "The thing" and steps 2–5 above where they conflict.
1. **Record-then-render.** You record ~32s, one command renders the video. Not a live filter.
2. **Lip-sync = hybrid (C).** One LTX pass: your DWPose skeleton (motion) + the song audio latent (mouth).
   One spike to prove it. Fallback: pose-only restyle (your own lip-sync performance).
3. **Scenes, not art-style flips.** One scene per song section + ffmpeg beat-synced zooms/flashes.
   Mood (soft, seductive, cozy holiday; Tuscan brown / burnt orange / amber) decides sets, light and props.
4. **Harmony → clones.** Harmony sections hand-tagged in the lyrics; render by tiling your skeleton ×3
   (lead + 2 backup-you's). Fallback: composite 3 renders.
5. **Full 32s song, 4 × 8s scenes**, cuts snapped to beats (92 BPM ≈ 0.65s per beat). Clones in the last scene only.
6. **Only three smart rules:** harmony → clones · beat hits → zoom/flash · lyric lines → captions.
7. **Director brain:** a repo skill that plays a '90s/2000s R&B music video director. It writes the shot list
   (camera, light, emotion, hand choreography per beat), which is the single source of truth for render + stitch.
   The template makes it reusable for any song + clip.

## Constraints
- Open weights only. **No `LtxApi25*` / partner API nodes** (keeps Open Weights + LTX bonus eligible).
- Compute = Comfy Cloud, NYC Build Night workspace, via the `comfy-cloud` MCP.
- ffmpeg is 9.0.1 (bumped unplanned) — check flags if stitching breaks.

## Done
1. One command turns a ~16s lip-sync clip into a reel with 3–5 visibly different style switches on the peaks.
2. The restyled chunks move in sync with the source (not stills), and lyric captions land on the peaks.
3. Workflow JSON + script + LICENSE + setup steps committed.

## Status (2026-11-02, ~20:35 ET)
- ✅ **Song locked**: `songs/in-the-loop-devagar.mp3` (ACE-Step 1.5 XL Turbo, 32s, 92 BPM). Recipe + lyrics in `songs/in-the-loop-devagar.md`.
  Loudness: intro 0–3s, verse 3–16s, chorus 17–31s (hits ~23.5 / 25 / 27.5s).
- ✅ **Restyle smoke test passed**: LTX 2.3 IC-LoRA + DWPose pose guide works on Comfy Cloud (chunk-01 anime, chunk-02 claymation in `outputs/`).
  Run chunks by re-running job `650120d9-3e42-4fbb-8ebc-19e1629c4832` with `submit_workflow from_prompt_id` + overrides
  (`199.file`, `129:211.on_false` prompt, `129:704.seed`). Don't use the saved workflow; Cloud reverts LoadImage + bypass.
  Every style prompt must describe her as "a young Black woman with deep brown skin", or the model lightens her skin.

- 🧪 **Lip-sync alternative tested**: `video_ltx2_3_ia2v` (photo + song → singing video), `outputs/chorus_lipsync_ia2v.mp4`.
  Job `ec3ea736-497d-4e79-a33b-243fde61b18a`. Overrides must use flattened ids: prompt `340:319.value`,
  prompt-enhance off `340:349.value=false`, audio start `340:332.start_index`, duration `340:331.value`, seed `340:286.noise_seed`.
  Images go on `269.image`, audio on `276.audio` (slot_overrides fail on this template).

## First step next session
Record ~16s lip-syncing to the locked song (recorder needs to play the track). Then: peak timing → chunk → restyle → stitch.

## Shiny box (not now)
- Real-time webcam preview filter
- User sings for real (vocal mix)
