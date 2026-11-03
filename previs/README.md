# Previs: "In the Loop (Devagar)"

A 3D blocking animatic of the whole 32 s video, built headless in Blender from one script. It is a
**guide for the shoot, the looks and the stitch**. It is not footage: the real video is her selfie performance
restyled by LTX, and the camera moves get applied in post (see `camera_plan.json`).

| Output | What it is |
|---|---|
| `outputs/previs/animatic.mp4` | 32.0 s, 854×480, 24 fps, H.264 + the song (AAC). Burn-in: scene marker, SMPTE timecode, frame, lens; top line = section, lyric line, hand cue |
| `previs/stills/s1.png … s4.png` | a clean mid-scene frame per scene, 1280×720 (no burn-in) |
| `previs/in-the-loop-devagar_previs.blend` | the scene: 4 sets, stand-ins, camera rigs bound to markers, song in the sequencer |
| `previs/camera_plan.json` | per scene: frames, cut downbeat, move, focal, dolly/orbit, height, tilt, f-stop, hand cues, wardrobe, and `post_zoom` for the 2D stitch |
| `previs/build_previs.py` | the reusable builder (everything from scratch, `--factory-startup`) |

## Re-run

From the repo root:

```sh
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P previs/build_previs.py -- \
  --song songs/in-the-loop-devagar.mp3 \
  --timing songs/in-the-loop-devagar.timing.json \
  --looks reference-master/looks.json \
  [--shots shots/in-the-loop-devagar.json] \
  --out-blend previs/in-the-loop-devagar_previs.blend \
  --stills-dir previs/stills \
  --animatic outputs/previs/animatic.mp4 --res 854x480
```

- **About 20 s without `--animatic`** (the build itself is ~1.5 s; the 4 stills at 64 samples take 3–5 s each).
  The full run with the animatic took **6 min 51 s** on this M3 (EEVEE, 16 samples: 393 s for the 768 frames, about
  0.5 s a frame). Run that one in the background: `nohup … > /tmp/previs.log 2>&1 &` and tail the log.
- `--timing/--looks/--shots` are optional and read-only. If omitted, the script looks for them at the
  conventional paths next to the song (`songs/<stem>.timing.json`, `shots/<stem>.json`,
  `reference-master/looks.json`). The log says which ones it used, and `camera_plan.json → sources` records it.
- **Re-run whenever** `shots/<song>.json` changes (scene t0/t1, camera move, focal, `takes[].cues`),
  the timing file changes, or a look's wardrobe text changes.
- Other flags: `--skip-stills`, `--samples 16` (animatic), `--stills-samples 64`, `--stills-res 1280x720`,
  `--engine workbench` (a fast fallback if EEVEE ever can't render headless), and `--extra-stills 60,550` (debug frames).

### What the inputs drive
| Input | Used for |
|---|---|
| timing (`downbeats`, `beats`, `lines`, `sections`) | cut frames (each cut lands **1 frame before** its downbeat: `round(t·24) − 1`), the sway on beats, nods on downbeats, hand-cue anchoring to lyric lines, the burn-in |
| shots (`scenes[]`, `takes[].cues[]`, `moves`) | scene times, camera move + focal, `camera.post` slow-push → `post_zoom`, hand moves (`kind: "move"`) mapped to gestures. **Not present at build time**, so the director's canonical plan was used (below) |
| looks (`looks[].wardrobe` per `scene` + `character`) | outfit shape/colour/fabric on each stand-in (slip dress vs. knit vs. two-piece, cropped, wide-leg; the clones a shade darker) |

## Open the .blend and adjust the cameras

Open `previs/in-the-loop-devagar_previs.blend` in Blender 5.2. Scrub the timeline: the **markers** `S1 couch · slow
push-in`, `S2 jam · handheld float`, `S3 stage · low-angle push-in` and `S4 girl group · quarter orbit` are bound
to the cameras, so cuts switch automatically. Drag a marker to move a cut (keep it 1 frame before a downbeat).
Numpad 0 looks through the active camera.

Each scene has a rig in `previs_cameras/<S>_camera`:
- `<S>_cam_rig`: the dolly. Two keyframes (start and end of the scene) with sine ease-in/out. Move the
  object at either keyframe and re-key (I), or edit in the Graph Editor.
- `<S>_cam_target`: what the camera looks at (Track To). Move it to re-frame.
- `<S>_cam`: lens and depth of field (Camera data → Lens / Depth of Field). The focus object is the performer's
  `_eyes` empty.
- S2 handheld: **Noise modifiers** on `S2_cam` rotation/location F-curves (Graph Editor → sidebar → Modifiers).
  Strength = how much shake, Scale = how slow.
- S4 orbit: `S4_orbit_pivot` rotation Z is keyed −45° → +45°. Change the keys to change the arc, or move the
  rig's Y to change the radius.

`camera_plan.json` is written by the script, so hand edits in the .blend don't update it. Re-run the script, or
edit the plan by hand. The per-frame lyric/cue note in the burn-in comes from a Python handler that exists only in
a scripted render; a render from the GUI shows a static note.

## The four scenes

| | Time (frames) | Set | Camera | Light | Stand-in |
|---|---|---|---|---|---|
| **S1 Cozy couch intro** | 0.000–6.875 s (0–164) | living room at night: tan suede couch, burnt-orange + cream pillows, console lamp camera-left, fairy-light swags on the wall, three candles and a steaming mug on the coffee table | slow push-in, 50 mm, eye level, 3.50 → 3.25 m (post 1.00 → 1.08), f/2.8 | lamp practical + soft key from camera left, gold bounce, amber top-back rim | seated; oversized cream knit; eyes down, lifting to the lens by the cut; **Heart Press** on bar 1 |
| **S2 Living-room jam** | 6.875–17.292 s (165–414) | same room + holiday garland with ornaments; laptop at camera-right scrolling amber terminal logs | handheld float (noise) over a 0.52 m lateral slide, 35 mm, 1.8 m, f/2.8 (post 1.00 → 1.03 + small drift) | key camera-left, bounce, rim; laptop glow; lamp and fairy pools on the wall | standing; burnt-orange ribbed top + Tuscan-brown satin cargos; eyes down → lens on the 12.13 s downbeat; **Backbeat Snaps** (9.5–14.5 s), **Jaw Trace** on the held note ~15.3 s |
| **S3 Holiday stage** | 17.292–25.125 s (415–602) | intimate late-'90s stage: Tuscan-brown velvet backdrop and legs, a wall of hanging fairy-light strands, candles, footlights, a chrome mic | low-angle push-in (camera 1.02 m, tilting up 13°), 50 mm **f/1.0** so the strands go to bokeh, 2.73 → 2.46 m (post 1.00 → 1.12) | large soft key camera-left, gold bounce, amber rim + backlight **through haze** (Principled Volume), burnt-orange curtain washes | burnt-orange silk slip; gaze to the lens; **Loop circle** on "I'm in the loop" (22.24 s) |
| **S4 Girl group of me** | 25.125–32.000 s (603–767) | same stage, wider, three mic stands (side-address), more candles | quarter orbit −45° → +45° around the trio, 35 mm, radius 2.9 m, f/2.8 (2D: post 1.00 → 1.04 + drift) | three amber rims + backlights through haze, key, bounce, kicker | V formation: lead at the apex nearest the lens, two clones behind at 45° in the same cropped amber satin halter + wide-leg set, one shade darker, swaying in a cascade (⅛-note offsets); the lead turns to hold the gaze through the orbit; **Heart Press** on "eu sei onde está" (26.25 s), **Reach to Lens** on "Keep me in the loop" (29.74 s), **Come Here** on the final beat (31.05 s); clones do Backbeat Snaps then an Ad-lib Wave |

Hand cues without a shots file follow the `rnb-director` canonical plan, anchored to the timing file's lyric
lines. With a shots file, the hero take's `cues[]` (`kind: "move"`) drive the lead and a `backup` take drives the clones.

## Director's notes from building it
- **The chorus starts later than the scene plan assumes.** The timing file puts "I'm in the loop" at 22.24 s
  (section `chorus` from 22.57 s). S3 "chorus lift" (17.35 s) therefore opens on the last verse line, "Little harness
  holding everything I've done", and "eu sei onde está" (25.5–27.2 s) falls in S4, not S3. The previs follows the
  locked cuts; if the director wants the stage reveal on the hook, the cut to S3 would move to the 22.57 s downbeat.
- The tight finale (Reach to Lens at 29.74 s → Come Here on the 31.05 s final beat) leaves no room for a separate
  Pull Back; the travel from the reach into the beckon plays as the pull back.
- The S3 f/1.0 is exaggerated so the bokeh reads at 480p. In the real video the bokeh comes from the hero frame/prompt.

## Known weaknesses
- Stand-ins are primitive mannequins: no face, no fingers (a curl is a wrist flex), no lip-sync. Use them for
  blocking and framing only.
- The quarter orbit and the lateral slide can't be reproduced by a 2D scale-and-crop on her selfie clip;
  `post_zoom`/`post_pan_x` are the closest post equivalents.
- Gestures are IK keyframes baked per frame. To change one, edit the cue (shots file) and re-run rather than
  keying by hand.

## Blender 5.2 API notes (for whoever edits the script)
Engine id is `BLENDER_EEVEE` (no `_NEXT`); the VSE uses `sequence_editor.strips`; video output needs
`image_settings.media_type = "VIDEO"` before `file_format = "FFMPEG"`; keyframes live in slotted actions (use
`bpy_extras.anim_utils.action_get_channelbag_for_slot`); the compositor is a `CompositorNodeTree` node group set on
`scene.compositing_node_group` (no Composite node; the Glare type is a menu socket). The Homebrew ffmpeg 9.0.1 build
has no `drawtext` filter.
