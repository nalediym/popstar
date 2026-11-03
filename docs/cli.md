# `popstar` — the one command for the pipeline

`bin/popstar <command>` (shim for `bun tools/popstar.ts`; `bun tools/popstar.ts status` keeps working exactly as before).
Zero dependencies, Bun + strict TypeScript, modules in `tools/popstar/`. Tests: `bun test tools/popstar.test.ts`.

| command | what it does | exit code |
|---|---|---|
| `status [--json] [--song <slug>]` | the eight stages, their receipts, and the ONE next step (unchanged output) | 0 |
| `doctor [--json] [--fast]` | environment drift (R7): tools, models, Comfy Cloud, fonts, project data; a fix command per ✗ | 1 if a *required* check fails |
| `new <slug> --song <mp3> [--title] [--performer] [--identity] [--negative] [--force]` | writes `popstar.json`, the project manifest (R3) | 2 on bad input |
| `timing [-- <args>]` | `tools/song_timing.py` through uv, then a bpm / beats / lines summary | the script's |
| `studio [--no-open]` | every tool server on a free port + a dark hub page (cards, health, pipeline status, 5 s refresh); Ctrl-C kills them all | 0 |
| `render [--scene <id> …] [--dry-run] [--submit] [--take <id>] [--fit pad\|crop] [--force]` | cut the take per scene to 8n+1 frames, tile the harmony scene, compile the pinned `pose-restyle` graph, write the manifest; cache by content hash (R4) | 1 if a scene failed, 2 on bad flags |
| `render --uploaded <file>=<name> …` / `render --mark <id> --job <prompt_id>` | record a Comfy Cloud upload name / a finished render in `build/state.json` | |
| `qa <source> <render> [-- <args>]` | `tools/qa_render.py` through uv (skin, identity, motion); "QA tool not built yet" if absent | the tool's (0 pass, 1 warn, 2 fail) |
| `open <recorder\|looks\|cut\|storyboard\|previs>` | open the running server, start it, or open the artifact that exists | 1 if nothing to open |
| `--help` | commands + the stage table | 0 |

Every command except `new` reads `popstar.json`. Without one, it falls back to the built-in defaults for
`in-the-loop-devagar` and prints a warning on stderr.

## popstar.json (the manifest, R3)

Every tool should read this instead of hardcoding paths (`import { loadManifest } from "tools/popstar/project.ts"`,
or just `JSON.parse`). Paths are repo-relative.

```json
{
  "version": 1,
  "slug": "in-the-loop-devagar",
  "title": "In the Loop (Devagar)",
  "song": { "file": "songs/in-the-loop-devagar.mp3", "recipe_md": "songs/in-the-loop-devagar.md" },
  "performer": { "name": "Naledi", "identity_lock": "a young Black woman with rich deep brown skin, …", "negative": "blurry, … lightened skin, pale skin, …" },
  "palette": ["#7a4a2a", "#d9622b", "#ff9f43", "#ffb347"],
  "paths": { "timing": "songs/<slug>.timing.json", "shots": "shots/<slug>.json", "looks": "reference-master/looks.json",
             "clips": "clips", "outputs": "outputs", "previs": "previs", "storyboards": "storyboards" },
  "fps": 24,
  "size": [1280, 704]
}
```

`popstar new` fills `performer.negative` from the pinned pose-restyle negative in `workflows/slots.json` and, unless
`--identity` is given, the identity lock of the current performer (it says so). It refuses to overwrite without `--force`.

## doctor

Checks, in order: ffmpeg ≥ 7, ffprobe ≥ 7, bun, uv, python3, Blender at `/Applications/Blender.app/Contents/MacOS/Blender`,
`beat_this` importable (`uv run --python 3.12 --with beat-this python -c "import beat_this"`, skipped with `--fast`),
the mlx-whisper small cache (`~/.cache/huggingface/hub/models--mlx-community--whisper-small-mlx`), Comfy Cloud reachable
(`GET https://cloud.comfy.org/`), `COMFY_API_KEY` set, the stitch fonts (`Menlo.ttc`, `Georgia Italic.ttf`), and the project
data (`popstar.json`, song, recipe, timing, shots, looks, a `clips/hero-*.mp4`).

Required (make it exit 1): ffmpeg, ffprobe, bun, uv, python3, Comfy Cloud reachability, `popstar.json`, the song.
Everything else is optional and printed in amber with the stage it matters for. `--json` gives
`{ ok, required_failures, checks: [{ id, group, label, required, ok, detail, fix? }] }`.

## render

For each scene in `shots/<slug>.json` `scenes[]` (filtered by `--scene`):

1. **Take.** `--take <id>`, else the first `clips/hero-*.mp4` in natural order (`hero-1` < `hero-1-2` < `hero-2`).
   The sync offset comes from the recorder sidecar `clips/<take>.json` (`song_offset_s`, song t=0 inside the take),
   else `shots.sync_offset_s`, else 0.
2. **Cut** `[t0 + offset, t1 + offset)` to exactly **8n+1 frames** at `fps` (the smallest 8n+1 that covers the scene:
   `8·max(1, ceil((ceil(dur·fps) − 1) / 8)) + 1`, so 8 s → 193, 7.82 s → 193, 6.84 s → 169). Start frame
   `round((t0 + offset)·fps)`; clamped to the take with a warning (the largest 8n+1 that still fits). ffmpeg:
   `fps=24, trim=start_frame:end_frame, scale+pad to 1280×704` (`--fit crop` center-crops instead), libx264, no audio.
   The cut is verified with ffprobe and cached in `build/state.json` (`cuts`) by take hash + parameters.
3. **Harmony** (`scene.harmony` and `clones.mode` = `tile_source`, the default): the cut is tiled ×3 with the filter in
   `docs/research/orchestration.md` §3 (lead at full scale in the center, backups at 0.6×, raised, the left one
   mirrored), and the look frame is tiled the same way (`build/scenes/<id>.frame.png`) and held at strength 1.0.
4. **First frame.** When a Reference Master render exists for `scene.look` (or `hero_frame.look`): `looks.json`
   `renders.expression`, then `renders.wardrobe`, then `reference-master/renders/<look>/{expression,wardrobe}.png`.
   Sets `first_frame`, `use_first_frame=true`, `first_frame_strength` (`hero_frame.strength`, default 0.8).
5. **Compile** the pinned graph with `tools/comfy_compile.ts`: `source_video`, `trim_start=0`, **`trim_duration=0`**
   (mandatory; the pinned 5 s would cap the scene), `prompt`, `negative` (scene → shot list → manifest), `seed`, `fps`,
   `width`, `height`, `output_prefix=video/<slug>/<id>`. Written to `build/scenes/<id>.json`, the slot values to
   `build/scenes/<id>.inputs.json`. A prompt without the identity lock verbatim gets a warning (skin-drift risk).
6. **Hash + verdict.** `hash = sha256(clip sha256, look-frame sha256 | null, slot fingerprint (no upload names),
   pinned graph sha256)`. A scene is **fresh** (skipped) when `build/state.json` `renders[id].hash` matches and
   `outputs/scenes/<id>.mp4` exists; otherwise **stale** with the reason (never rendered, output missing, the cut clip
   changed, the look frame changed, prompt/seed/settings changed, the pinned graph changed). `--force` re-renders.

`build/render-manifest.json` is the contract: the take (file, sha256, offset), and per scene `clip`, `clip_sha256`,
`first_frame`, `inputs_file`, `graph_file`, `uploads[]` (slot, file, sha256, recorded name or null), `hash`, `parts`,
`state`, `stale_because`, `output`, `warnings[]`, plus `pending_uploads[]`.

`--dry-run` prints the plan (frames, start frames, looks, the commands it would run) and writes nothing; it works
offline and without a take (offset 0, no clamping). With no `scenes[]` yet it prints what the stage needs and exits 0.

### Submission: the Comfy Cloud MCP contract

**No plain HTTPS API is documented.** The comfy-cloud MCP guide (`get_prompting_guide`, topic `setup-auth`) says the
hosted session "carries ONE credential … either an OAuth bearer token … or a Comfy Cloud API key (`X-API-Key: comfyui-…`)",
that the local stdio mode "reads `COMFY_API_KEY` from the environment", and (topic `output-downloads`) that the server
"never writes to local disk": `get_output` returns a short-lived `/api/s/<id>` shortlink plus a curl command and
`upload_file` "emits an upload command that POSTs to `cloud.comfy.org`". Submitting and polling exist only as MCP tools
(`submit_workflow`, `submit_batch`, `wait_for_job`, `get_output`). So `--submit` does not talk to the cloud itself; it prints
exactly what Claude must do through the MCP and copies it to the clipboard (`pbcopy`):

1. `upload_file` each pending file (the cut clip, the look frame), run the emitted curl, keep the returned name.
2. `bin/popstar render --uploaded build/scenes/<id>.mp4=<name> …` records the names by file hash and recompiles the graphs.
3. `submit_workflow({ workflow: <build/scenes/<id>.json>, dry_run: true })` → `validated` (free).
4. `submit_batch` the stale graphs (credits), `wait_for_batch` / `wait_for_job`.
5. `get_output` → `outputs/scenes/<id>.mp4`, then `bin/popstar render --mark <id> --job <prompt_id>` records the receipt;
   `popstar status` shows RENDER progress.

Recorded upload names are reused on the next `render`, so re-rendering a scene with a new seed uploads nothing new.

## studio

Starts `recorder/server.ts`, `reference-master/server.ts` and `cut/server.ts` (whichever exist; a missing one shows a
"not built yet" card), each with `PORT=<free port>` in its own process group, waits for the URL each prints, then serves
the hub on another free port and opens it (`--no-open` to skip). Port 56101 (the recorder preview in `.claude/launch.json`)
is never handed out. `/api/state` returns the cards (url, pid, health from a `GET /` with a 1.5 s timeout, last log lines)
and `status --json`; the page re-fetches it every 5 s. SIGINT/SIGTERM kill every child's process group (TERM, then KILL).

## open

`recorder` / `looks` / `cut`: if the tool is already listening on its `.claude/launch.json` port, open that URL; else start
its server on a free port, open it, and keep it running until Ctrl-C; "not built yet" if the server file is missing.
`storyboard`: the first `storyboards/*.pdf`, else `*_board.png`. `previs`: `outputs/previs/animatic.mp4` (prints the
Blender command if missing).

## timing, qa

`timing` runs `uv run --with librosa --with soundfile --with mlx-whisper python -I tools/song_timing.py <song> <recipe>
-o <timing>` (extra args after `--`) and prints bpm, beats, downbeats, sections and every line with its confidence.
`qa` lifts `--python` and `--with` from the usage header of `tools/qa_render.py`, so its own declared deps are used.

## Caveats

- `render` needs a `clips/hero-*.mp4` for anything but `--dry-run`; the scene times must come from `shots/<slug>.json`
  (the single source of truth, R2). Scenes[] is written by the `rnb-director` skill.
- The cut drops audio (the pose guide only reads the picture; stitch muxes the clean song). Use `--fit crop` if the
  2 % side bars from scale+pad bother the first-frame match.
- `--mark` only records; it does not verify identity or skin tone. Run `popstar qa` on each render for that.
- The hub's health check is a plain `GET /`; a server that answers but is broken still shows green.
