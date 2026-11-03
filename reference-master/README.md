# Reference Master

The wardrobe and look department for **In the Loop (Devagar)**. Every scene needs a look per character
(the lead, plus two backup-singer clones in `s4-harmony`). You design the look here; Claude renders it on
Comfy Cloud and writes the images back. The page shows whatever exists and updates itself.

```bash
bun reference-master/server.ts            # prints the URL (any free port)
PORT=56110 bun reference-master/server.ts # fixed port (56101 is the recorder's; it is refused)
```

## The loop

1. **Pick a look** in the left timeline (↑ ↓).
2. **Design it.** Wardrobe, props and notes (the chips add '90s/2000s R&B ideas in the palette). Set and light
   belong to the scene, so editing them changes every look in that scene. Paste or drop Pinterest pins and images
   into **References**, and ★ one to make it THE outfit reference.
3. **Pick an emotion** (1–8). Fine-tune with the 12 sliders; the face preview moves instantly. If the render
   comes back too subtle, push **Intensity** (or **+ bolder**).
4. **Q** queues the look (both passes). Queue as many as you like.
5. **Copy all queued for Claude**, paste it into Claude. Renders land in the image panel within 5 s.

The **NEXT** bar at the top always says the single next thing to do.

| Key | Does |
|---|---|
| ↑ / ↓ | previous / next look |
| 1–8 | apply emotion preset 1–8 |
| S (or ⌘S) | save now (it also autosaves 0.8 s after you stop) |
| Q | queue the look for a full render (wardrobe + expression) |
| ⌘Z / ⇧⌘Z | undo / redo text, slider, preset, chip, ref and copy-to edits |
| ⌘V, drag and drop | add an image or an image/pin link as a reference |
| click an image | zoom (← → to step through, Esc to close) |

## Files

| File | What |
|---|---|
| `looks.json` | the source of truth: scenes, looks, emotion presets, `identity_lock`, `palette` |
| `looks.backup.json` | the previous version, rewritten on every save |
| `queue.json` | render requests: `[{look_id, what, requested_at}]`, one entry per look |
| `refs/<look_id>/<n>.<ext>` | reference images (pins, pastes, drops) |
| `renders/<look_id>/` | Claude's outputs (`wardrobe.png`, `expression.png`) |
| `renders/frames/<clip>_<t>.png` | frames grabbed from `clips/` with **Grab frame** |
| `tests/` | the 2026-11-02 feasibility renders (kept as examples) |

### A look

```jsonc
{
  "id": "s3-chorus.lead", "scene": "s3-chorus", "character": "lead",
  "wardrobe": "…", "props": "", "notes": "",
  "emotion": "soft-smile",                       // a key of "emotions" (the preset it started from)
  "expression": {"rotate_pitch": 0, …, "smile": 1.2}, // RENDER-READY: send these 12 values as-is
  "intensity": 100,                              // %, 0–200; already applied to `expression`
  "expression_base": {…},                        // only when intensity ≠ 100: the values at 100%
  "source_frame": "reference-master/tests/source_frame.png",
  "renders": {"wardrobe": "…png" | null, "expression": "…png" | null},
  "refs": [{"path": "reference-master/refs/s3-chorus.lead/1.jpg", "source_url": "https://…", "role": "wardrobe", "starred": true}],
  "status": "draft"                              // draft | queued | wardrobe-done | done
}
```

`expression` is always `clamp(expression_base × intensity%)` and ready for the ExpressionEditor node, so a renderer
never has to do the intensity math. A starred ref (role `wardrobe`) is the outfit reference for Qwen Image Edit's
`image_2`.

## For Claude (the renderer)

- Read `queue.json`, render each entry (see `.claude/skills/reference-master/SKILL.md` for the verified recipe),
  save to `renders/<look_id>/`, then re-read `looks.json` and change **only** that look's `renders` and `status`
  (`wardrobe-done` / `done`). Remove the handled entry from `queue.json`.
- Overwriting `renders/<look_id>/expression.png` in place is fine: the page reloads images whose file changed.
- If Naledi has unsaved edits when you write, she gets an "updated on disk — reload?" notice. Her autosave merges
  field by field, so neither side's changes are lost.

## Routes

| Route | |
|---|---|
| `GET /` | the page |
| `GET /looks` | looks.json as-is (`ETag` / `x-looks-hash` headers). `?meta=1` → `{hash, looks, files}` (file versions for cache-busting) |
| `PUT /looks` | validate the shape, write atomically (temp file + rename), keep `looks.backup.json`. Optional `If-Match: "<hash>"` → `409` with the disk version on a conflict |
| `GET /img?path=…` | images inside `reference-master/{renders,tests,refs}/` or `clips/` only (traversal, absolute paths and symlinks out are refused) |
| `POST /queue` | `{look_id, what: wardrobe\|expression\|both}` → the queue. Re-queueing a look widens `what` instead of adding a duplicate (no double-paid renders) |
| `GET /queue` · `DELETE /queue?look_id=…` | read · remove an entry |
| `GET /frames?clip=<name>&t=<s>` | ffmpeg frame from `clips/<name>.mp4` → `{path}` |
| `GET /clips` | `[{name, duration}]` for the frame picker |
| `POST /refs` | `{look_id, url, role}` or multipart (`look_id`, `role`, `file`…). Direct image links are saved as-is; page links (pinterest.com/pin/…, pin.it/…) use the page's `og:image`. http(s) only, private/loopback/link-local hosts blocked (checked on every redirect), 15 MB cap, raster images only (sniffed), 10 s timeout per fetch |

The server binds 127.0.0.1 only and refuses foreign `Host` headers (DNS rebinding) and cross-origin writes.

## Slider directions (calibrated on `tests/sheet_chorus_expressions.png`)

- **blink: negative closes the eyes** (−20 shut, −10 heavy lids). Positive opens them wider, so the `devagar`
  preset's `blink 4` keeps her eyes open, exactly like `tests/chorus_devagar.png`.
- **wink** closes her right eye (screen-left). **pupil_x +** looks screen-right. **yaw +** turns screen-right.
  **roll +** tilts clockwise. **pitch −** tips the chin up.
- **pupil_y: negative looks up** (inferred from the node's lid/gaze coupling, not seen in a test render yet).
- The face preview is a schematic stand-in, not a prediction. The render is the truth.
