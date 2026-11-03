# Coach recorder

A localhost recorder for **In the Loop (Devagar)** with a live coaching overlay. It tells you what
to sing, when to sing it, and how to move. It also warns you when the footage won't work for the
AI pass (LTX-2.3, pose-guided).

Nothing on the overlay gets recorded. The saved clip is the raw camera, not mirrored.

## Run it

```bash
bun recorder/server.ts                 # prints: recorder ready: http://localhost:<port>/
PORT=5173 bun recorder/server.ts       # pick a port yourself
```

You can also start the **recorder** entry in `.claude/launch.json` from the browser pane. That
entry pins `PORT=56101`.

Open the printed URL in Chrome, put your earbuds in, and press **C** (or *Turn on camera*). The
page works before you allow the camera, so you can rehearse first.

The server reads two data files fresh on every request, so new versions show up when you reload
or switch back to the tab:

| File | Gives the page | If it's missing |
|---|---|---|
| `songs/in-the-loop-devagar.timing.json` | lyric lines and words, beats, downbeats, sections | no lyrics, beat dots or count-in. Recording still works |
| `shots/in-the-loop-devagar.json` | the move vocabulary and the takes with their cues | a built-in copy of the 7 takes from `docs/capture-list.md`, with no song cues |

To point the server at other files, set `TIMING_PATH=… SHOTS_PATH=… bun recorder/server.ts`.

## The takes workflow

1. **Pick a take** in the setlist on the left, or press **1** to **7**. The clip name fills in
   (`hero-1`, or `hero-1-2` if `clips/hero-1.mp4` already exists). The *♪ song* box follows the
   take, and the panel shows the brief, a setup checklist and the cue sheet.
2. **Set up.** Tick the checklist. Press **G** to show the framing guide.
3. **Rehearse** with **R**. The song and the full overlay run, but nothing records. Use **←** and
   **→** to skip 5 s. Press **R** or **Esc** to stop.
4. **Record** with **Space**. After a 3·2·1 countdown, the song and the overlay start together.
   Song takes stop when the song ends. No-song takes stop at their length. **Space** or **Esc**
   stops early.
5. **Review.** The take plays back with the song under it, lined up by the measured offset, so
   you can check your lip-sync. The card lists every warning that fired, with timestamps.
   **⏎** saves, **⌫** throws the take away, and **Space** records again. If the take isn't saved
   yet, you have to press Space twice.
6. **Receipt.** After saving, the page asks the server to check the file and shows a receipt like
   `✓ hero-1 · 32.0s · 768 frames @24fps · 1280×720 · audio`. Problems get flagged: no audio,
   under 720p, not 24 fps, or a song take that stopped early. The take gets a ✓ in the setlist,
   which this browser remembers. **N** jumps to the next take that isn't done.
7. Tell Claude the names you saved.

The minimum to start is `hero-1`, `turnaround` and `face`.

## What's on the screen

| Element | Where | What it means |
|---|---|---|
| **Lyric line** | top, big | Sing this now. It fills amber word by word. A dimmer line means it's coming up; it appears 1.5 s before you sing it |
| **Next line** | under it, small | The line after this one |
| **3 · 2 · 1 · sing** | under the lyric | Count-in on the beats before the first lyric |
| **● ● ● ●** | top right | The beats of the bar. The big dot is beat 1, and the lit dot pulses on every beat. Below it: bar number, section, seconds left |
| **NEXT 🫶 Heart Press in 4 · 3 · 2 · 1** | bottom | A move is coming. It counts down on the beats and lands on the last count |
| **Move card** | bottom, big | Do this move now: icon, name, how to do it. The card flashes on the beat, and the amber line along its bottom shows how long to hold |
| **Banner** | top center | Direction for this part, for example *CHORUS: lift your eyes to the lens* |
| *Italic line* | under the lyrics | A small face note: *small smile*, *slow blink* |
| **Warning** | bottom, above the move | The footage won't work for the AI. Fix it and keep going (see below) |
| Thin bar | bottom edge | Progress through the take. Ticks mark sections, amber ticks mark moves |
| Dashed box (**G**) | stage | Framing guide: head at the top, waist at the bottom, center line |
| Skeleton (**S**) | stage | What the pose guide sees, drawn faintly |

### Warnings

A warning only appears after its problem has lasted 0.7 s, and then it stays up for at least
1.5 s. Only one shows at a time, highest priority first:

1. **Hand over your mouth, lower it while you sing.** A hand point came within about 0.08
   frame-heights of your mouth while a lyric line was active. During face-touch moves (Jaw Trace,
   Frame the Face, Hair Comb), the radius shrinks to 0.045 so the move itself doesn't trip it.
2. **Hands out of frame.** A wrist or index finger is near the frame edge or hidden while a move
   is active.
3. **Step back, I need you head to waist.** Neither hip is visible. This check only runs on takes
   with the song or with moves, so the close-up `face` take doesn't trigger it.
4. **Too dark, face a lamp or window.** The average brightness of the frame is under 60/255. The
   page samples it every 0.5 s.

Checks 1 to 3 use MediaPipe Pose Landmarker (lite, about 10 fps), which loads from a CDN. If it
can't load, a small *pose checks off* chip appears, and the light check keeps working.

## Keys

Shortcuts don't fire while you're typing in the clip-name box. **Esc** leaves the box.

| Key | Does |
|---|---|
| **Space** | record (3·2·1 first) / stop |
| **R** | rehearse / stop rehearsing |
| **Esc** | stop the rehearsal or take, cancel the countdown, close a card |
| **⏎** / **⌫** | save / discard the take you just recorded |
| **1**–**7**, **N** | pick a take / next take not done yet |
| **←** **→** | skip 5 s back / forward while rehearsing |
| **M** | mirror the preview on/off (the saved clip is never mirrored) |
| **G** | framing guide |
| **S** | pose skeleton |
| **T** | show/hide the setlist |
| **C** | turn on the camera |
| **[** **]** | shift the overlay 25 ms earlier/later, if the beat dots run ahead of what you hear (Bluetooth earbuds) |
| **?** | keys and legend |

## Server routes

| Route | Returns |
|---|---|
| `GET /` | the coach page |
| `GET /song` | the mp3 (supports byte ranges, so the page can seek) |
| `GET /timing`, `GET /shots` | the data files above; `404 {"error": …}` when missing, `500` when the JSON is broken |
| `GET /clips` | `["hero-1.mp4", …]` |
| `GET /clips/<name>.mp4` | one saved clip (plain names only), used by demo mode |
| `POST /save?name=&song_offset=` | writes `clips/<name>.mp4` (H.264/AAC, 24 fps) plus the sidecar `clips/<name>.json` `{song, song_offset_s}` |
| `GET /check?name=<clip>` | `{name, duration_s, frames, fps, width, height, has_audio}` from ffprobe; `404` when missing, `400` for names with `/`, `\` or `..` |

`/save` and `/check` clean names the same way: any character outside letters, digits, `_` and
`-` becomes `_`.

## Troubleshooting

- **"Camera blocked".** Allow camera and mic for `localhost` in Chrome's site settings, then
  press **C**.
- **The song bleeds into the take.** Use earbuds. Wired earbuds are best. With Bluetooth earbuds
  you hear the song about 0.2 s late, so your lips land late in the stitched video too.
- **"exists: saving overwrites"** next to the name. That clip already exists. Pick the take again
  to get the next free name, or type a new one.
- **Try the pose checks without a camera.** Open `/?demo=chunk-01`. That saved clip plays as the
  camera. You can rehearse in this mode, but you can't record.
