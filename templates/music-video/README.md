# Template: make anyone a pop star in an R&B music video

Record yourself lip-syncing for ~30 seconds and get back a music video of you: same moves, new world, AI-restyled
with open-weights models on Comfy Cloud. This folder is the reusable recipe; "In the Loop (Devagar)" is the first
song built with it.

**Always start with:** `bun tools/popstar.ts status --song <song>`. It shows every stage and the one next step.

## The eight stages

| # | Stage | Do this | Get this |
|---|---|---|---|
| 0 | **Song** | Ask Claude for a song (ACE-Step 1.5 XL Turbo on Comfy Cloud). Write lyrics with a `[Chorus]` and, if you want clones, a `[harmony]` tag. Listen, then lock it. | `songs/<song>.mp3` + `songs/<song>.md` (recipe, lyrics, seed) |
| 1 | **Timing** | `uv run --with librosa --with soundfile --with mlx-whisper python -I tools/song_timing.py songs/<song>.mp3 songs/<song>.md -o songs/<song>.timing.json` | beats, downbeats, lyric line times |
| 2 | **Direct** | Ask Claude to direct it (skill `rnb-director`). It writes scenes and coaching cues. | `shots/<song>.json` (schema: `shots.schema.json` here) |
| 3 | **Previs** | Block it in Blender: `previs/build_previs.py` (headless). Watch the animatic with the song. | `outputs/previs/animatic.mp4`, `previs/camera_plan.json` |
| 4 | **Looks** | `bun reference-master/server.ts`: wardrobe, props, Pinterest refs and the emotion (eyes/mouth sliders) per scene and character. Queue renders, then tell Claude "render the queue" (skill `reference-master`). | hero frame per scene |
| 5 | **Shoot** | `bun recorder/server.ts`: pick a take, **R** to rehearse with the overlay (lyrics, beat, move cards, pose warnings), **Space** to record. | `clips/<take>.mp4` + offset sidecar |
| 6 | **Render** | Ask Claude to render the scenes (skill `popstar-pipeline`). Each scene follows your real motion (DWPose → LTX-2.3 IC-LoRA). | `outputs/scenes/<scene>.mp4` |
| 7 | **Stitch** | Ask Claude to stitch: dissolves, grade, post camera, beat FX, clones, captions, the clean song. | `outputs/final.mp4` |

## What to film (any song)
Earbuds in · warm light from the front · camera at eye level, head to waist · both hands in frame · hands off your
mouth while you sing. Takes: 2 hero takes of the whole song, one "backup singer" take for the clones, plus short
reference takes (turnaround, hands, shoulders, face). The coach recorder walks you through each one.

## Swap the vibe
The director skill holds the house style ('90s/2000s R&B: warm, close, held; soft key, gold bounce, amber rim;
one camera move per shot; the hook sung straight into the lens). For a different genre, give Claude a new
palette and reference directors and ask it to re-direct; every downstream stage reads the new shot list.

## Rules that keep it working
- Open weights only (no paid partner API nodes).
- Every prompt carries the performer's identity lock verbatim, including their skin tone, or the models drift
  (they lightened a dark-skinned performer in testing).
- Change scenes → re-run previs; change a look → re-render that scene; re-record → re-render everything.
