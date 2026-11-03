# Photos scout: identity refs and taste signals from the Mac's Photos library

`tools/photos_scout.py` lets the pipeline (or Naledi) look into the macOS Photos library for two things:

1. **Identity references of the performer**: portraits Photos has already tagged as one person (People faces),
   ranked for "good face reference", with a numbered contact sheet.
2. **Taste signals for the looks**: photos by album, keyword or favorites, with a dominant-colour palette per image,
   one aggregate palette for the set, and a comparison against the project palette in `reference-master/looks.json`.

It is **read-only by default**. Nothing is copied out of the library unless you pass `--export`.
It reads the library through [osxphotos](https://github.com/RhetTbull/osxphotos) (the Photos SQLite database, no Photos.app automation).

## Run

No install step. `uv` fetches osxphotos and Pillow into its cache on the first run (about 10 s), then runs take ~2 s.

```sh
S='uv run --python 3.12 --with osxphotos --with pillow python -I tools/photos_scout.py'

$S stats                                           # counts only: who, what, how many
$S identity --person "Naledi Kekana"               # 12 portraits + build/photos/identity_naledi-kekana.png
$S identity --person "Naledi Kekana" --n 8 --since 2025-01-01
$S identity --person "Naledi Kekana" --export --size 1280     # copies into reference-master/refs/identity/
$S inspire --favorites --n 12                      # build/photos/inspire.png with palettes
$S inspire --album Pinterest                       # any album title from `stats`; --keyword works the same way
$S inspire --album Pinterest --export              # copies into reference-master/refs/inspire/pinterest/
$S selftest                                        # unit tests of the selection + palette logic, no library needed
```

Every subcommand takes `--json` (one JSON object on stdout, progress on stderr), `--library PATH` (another
`.photoslibrary`; default is the library Photos opened last) and `--debug` (tracebacks).

Exit codes: `0` ok, `1` unexpected error, `2` usage or not found (unknown person, album, keyword, library),
`3` Photos access blocked by macOS privacy settings, `4` selftest failed.

## Subcommands

| Subcommand | Options | Output |
|---|---|---|
| `stats` | | library path; photos/videos/trash/hidden/screenshots; originals on this Mac vs iCloud-only; favorites; people (names + photo counts); albums (+counts); keywords (+counts). Counts and names only, never file paths. |
| `identity --person NAME` | `--n 12`, `--since YYYY-MM-DD`, `--min-gap 600`, `--export`, `--size PX`, `--download-missing` | a ranked table, `build/photos/identity_<person>.png`, and with `--export` the files + `manifest.json` in `reference-master/refs/identity/` |
| `inspire` | `--album TITLE`, `--keyword KW`, `--favorites` (default when no filter), `--n 30`, `--export`, `--size PX` | a table with 3 dominant colours per photo, `build/photos/inspire.png`, the set palette vs the project palette, and with `--export` the files + `manifest.json` in `reference-master/refs/inspire/<filter>/` |
| `selftest` | | PASS/FAIL per test on synthetic rows |

`NAME` must be exactly as Photos spells it (take it from `stats`; a partial name gets a "did you mean" hint).

### How `identity` picks portraits

Only photos Photos tagged with that person, then rejected if: a video, a screenshot, hidden, in a shared album,
**not exactly one face**, the single face belongs to someone else, before `--since`, or no file on this Mac.
The rest are scored (shown in the table as `score`):

```
score = overall aesthetic (0..1)          Photos' own curation score
      + 0.5 × pleasant_lighting (-1..1)   Photos' lighting score
      + 0.5 × face quality (0..1)         Photos' face-quality score
      + 0.6 × min(face size, 0.5)         bigger face in frame = more portrait
      + 0.3 if favorite
      + 0.2 × recency                     1.0 today → 0 three years ago
      - 0.4 if only a tiny (<600 px) preview is on this Mac
```

Then a diversity pass: shots taken within `--min-gap` seconds (default 10 min) of an already-chosen one are skipped
(bursts and near-duplicates), and only used to fill up if the pool runs out. Read the sheet: numbers match the table.

### What `inspire` measures

Photos matching the filters (hidden and shared-album photos skipped; screenshots kept, because saved Pinterest pins
are taste), newest first, favorites first. For each: the 3 dominant colours (Pillow median-cut quantize on a 96 px
copy). For the set: all thumbnails are tiled into one mosaic and quantized to 5 colours (the **set palette**), plus

- **warm share**: fraction of the mosaic's pixels in the brown/orange/amber hue band (10°..50°, saturated, not black).
  The project palette (`#7a4a2a #d9622b #ff9f43 #ffb347`) lives at hue 20°..35°.
- **similarity**: for each project colour, its nearest set colour and `1 − distance` ("redmean" weighted RGB, 0..1).
  `mean similarity` is the average. It is a rough number: 0.9+ means the set already lives in the palette,
  0.7 means "same temperature, different saturation", below 0.6 means the set pulls another way.

The palette comes from `reference-master/looks.json` (`palette`), with the four colours above as the fallback.

## Outputs

- `build/photos/identity_<person>.png`, `build/photos/inspire.png`: contact sheets, numbered to match the tables.
  `build/` is gitignored.
- `reference-master/refs/identity/<uuid8>.jpg` + `manifest.json` (identity `--export`),
  `reference-master/refs/inspire/<filter>/<uuid8>.jpg` + `manifest.json` (inspire `--export`).
  Files are always JPEG (HEIC originals are converted; `--size` caps the long edge and never upscales).
  Names are the first 8 characters of the Photos UUID, so re-running overwrites the same photo instead of
  piling up copies; the manifest is rewritten each time. To start over, delete the folder.

`manifest.json`:

```json
{
  "generated": "2026-11-02T22:30:00-05:00",
  "kind": "identity",
  "person": "Naledi Kekana",
  "size": 1280,
  "items": [
    {"n": 1, "uuid": "4841B257-…", "date": "2025-08-17T14:03:11-04:00", "score": 1.61,
     "path": "reference-master/refs/identity/4841b257.jpg", "source": "preview",
     "pixels": "768x1024", "favorite": false, "albums": []}
  ]
}
```

`source` is `original`, `edited`, `preview` (Photos' local ~1024 px derivative, see caveats) or
`original (downloaded from iCloud)`.

## Privacy

- **Read-only by default.** `stats`, `identity` and `inspire` only read the Photos database and the preview files
  Photos already keeps on disk. Nothing is written to the library, ever.
- **Nothing leaves this Mac** from this tool. `--export` copies files into `reference-master/refs/`, which is
  gitignored together with `build/`, `clips/` and `renders/` (see `.gitignore` and `docs/architecture.md`, R5).
  An image reaches Comfy Cloud only when Naledi queues a look in Reference Master that uses it (the render skill
  uploads that one file with `upload_file`).
- Names: `stats` and `--json` print the names Photos has assigned to faces. That is local terminal output. Don't paste
  it into the public README or an issue; the contact sheets are personal photos too, and live under `build/`.
- The tool never deletes, renames or edits anything in the library, and never uses `--export` on its own.
  The pipeline agent runs it read-only and asks before exporting.

### Photos access (TCC)

macOS protects `~/Pictures/Photos Library.photoslibrary`. When the app you run this from is not allowed, the tool
prints the fix and exits with code 3 instead of a stack trace:

```
photos_scout: can't read the Photos library at /Users/…/Photos Library.photoslibrary
  PermissionError: [Errno 1] Operation not permitted: '…/database/Photos.sqlite'
macOS privacy protection (TCC) blocks direct reads of the Photos library. Fix, then re-run:
  1. System Settings > Privacy & Security > Full Disk Access: turn ON the app you run this from
     (Terminal, iTerm, c11, or the Claude app), then quit and reopen that app.
  2. If it still fails: System Settings > Privacy & Security > Photos: give that app Full Access.
  3. Or point at a copy of the library: --library "/path/to/Photos Library.photoslibrary"
```

Verified 2026-11-02: the Claude app's shell reads the library directly (Full Disk Access is on for it).

## How Reference Master picks these up

Reference Master (`reference-master/server.ts` + `looks.json`, skill `.claude/skills/reference-master`) keeps reference
images under `reference-master/refs/`:

- `reference-master/refs/<look_id>/N.ext` are the refs of one look. The tray registers each one in that look's
  `refs` array in `looks.json` as `{path, source_url, role, starred}` with `role` one of `wardrobe`, `mood`, `set`.
  A starred `wardrobe` ref is THE outfit reference.
- `reference-master/refs/identity/` (this tool) is the **identity anchor** folder: portraits of the performer,
  not tied to one look. Use them as `source_frame` for a look when there is no clip frame yet (in `looks.json`
  set `"source_frame": "reference-master/refs/identity/<uuid8>.jpg"`), and as the face reference when a render
  drifts (the skill's identity lock stays in the prompt; the image goes in as the edit source or first frame).
  The server's `/img?path=reference-master/refs/identity/<file>` serves them to the UI like any other ref.
- `reference-master/refs/inspire/<filter>/` are **mood candidates**. To attach one to a look, drag it into that
  look's tray (or copy it to `reference-master/refs/<look_id>/` and add `{"path": …, "source_url": null,
  "role": "mood", "starred": false}` to the look's `refs`). The palette numbers tell you whether a set already
  matches the Tuscan-brown/burnt-orange/amber palette before you spend renders on it.

Nothing in `looks.json` is edited by this tool.

## Caveats

- **iCloud-only originals.** With "Optimize Mac Storage" on, Photos keeps the originals in iCloud and only a
  ~1024 px preview locally (on this library: 6 of 5477 originals are on the Mac). `identity` and `inspire` use the
  previews, and `--export` copies the best file that exists locally (`source: "preview"` in the manifest), so
  `--size 1280` cannot give you more than ~1024 px. For full resolution either
  `--export --download-missing` (asks Photos through PhotoKit to fetch each original; macOS prompts for Automation
  access the first time; it is slow and was **not exercised** in the 2026-11-02 test), or Photos > Settings >
  iCloud > "Download Originals to this Mac" and re-run.
- One face only: group photos with the performer are rejected on purpose; `--n` may come back short on a small
  library. The table says why photos were rejected (`rejected: …`).
- Photos' People names are whatever was typed in Photos. Unnamed face clusters are counted as "unnamed faces" and
  can't be selected by name.
- Favorites are whatever Naledi hearted; on this library they are mostly documents and family snapshots, so for
  taste use `--album Pinterest` (saved pins land there) or a keyword.
- The aesthetic and lighting scores are Photos' own machine-learning scores; they rank, they don't judge.
- Pillow reads JPEG/PNG/WebP and the MPO previews Photos writes; HEIC goes through macOS `sips`. A file neither can
  read shows as an "unreadable" tile instead of stopping the run.
