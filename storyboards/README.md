# storyboards/ — the director's boards for "In the Loop (Devagar)"

See the video before it exists. Two boards, four frames, one generator. Dark, monospace, amber.

| file | what it is |
|---|---|
| `in-the-loop-devagar_board.png` / `.pdf` | **Director's storyboard**, 2x2, 2560x1600. One panel per scene: the letterboxed frame with the camera move drawn over it, then SET · LIGHT · WARDROBE · CAMERA (+ post zoom) · EMOTION · TRANSITION IN, a HANDS strip (the move cues on a mini beat timeline of that scene) and the LYRICS sung in that window (italic serif). |
| `in-the-loop-devagar_beatboard.png` | **Beatboard**, 2560x640: one cell per bar (pickup + bars 1–12, widths proportional to time, Σ = 32.00 s), the scene band (INTRO / VERSE / CHORUS / HARMONY), a thin "audio" band with the sections the timing file heard, the beat ruler (taller tick = downbeat), two lyric lanes, the move cues on their beats, and the FX lane: letterbox wipe in (0.00) / out (last beat 31.04), the one warm flash frame (17.34), the punch-ins (22.56, 25.17), dissolves at the other cuts. Amber verticals = scene cuts. |
| `frames/s1.png … s4.png` | The raw frames, 1280x704/720 (her real face, identity lock). |
| `frames/s*_letterbox.png` | The same frames with the 2.39:1 bars burned on (92 px top and bottom at 1280x720), as the final will be. |
| `frames/make_s4_triptych.sh` | The ffmpeg recipe for s4 (see below). |

The generator is `tools/storyboard.py` (Python + Pillow, nothing else).

## What each panel shows

| panel | window | frame | camera glyph | hands (takes[hero-1]) | lyrics in window |
|---|---|---|---|---|---|
| **S1 · Cozy couch intro** | 0.00–6.90 s · pickup + bars 1–2 · INTRO + VERSE IN | cream knit sweater, gold hoops, tan couch, fairy lights, candles, steaming mug; lamp key camera-left | inward corner arrows = slow push-in, dashed box = post-zoom end frame 1.08x | Heart Press 1.68 · Backbeat Snaps 4.29 (small bursts on 2 & 4) | (Devagar, devagar) · Tea is warm… → |
| **S2 · Living-room jam** | 6.90–17.34 s · bars 3–6 · VERSE | burnt-orange ribbed top, laptop glowing with scrolling logs, garland, same living room | wavy line = handheld float (post 1.00→1.03 from the previs) | Palm Roll ~6.90 · Backbeat Snaps 9.51, 14.73 · Shoulder Roll 12.12 | Gave my agent tools… · Hooks before the commit… · Little harness… → |
| **S3 · Holiday stage** | 17.34–25.17 s · bars 7–9 · CHORUS · HOOK | the existing chorus look: burnt-orange silk slip dress, Tuscan-brown velvet, fairy-light bokeh, haze | corner arrows + "↑ LOW ANGLE" tag = low-angle push-in, end frame 1.12x, PUNCH-IN @ 22.56 chip; transition in = cut + warm flash | Body Roll 17.34 · Hair Comb 21.26 · The Loop 22.56 | ← Little harness… · I'm in the loop, devagar |
| **S4 · Girl group of me** | 25.17–32.00 s · bars 10–12 · CHORUS OUT + HARMONIES | the **clones: triptych fallback** (ffmpeg composite of the s3 frame, see below) | dot + curved arrow = quarter orbit feel (post 1.00→1.04 drift) | Heart Press ~25.82 · Reach to Lens ~29.74 · Pull Back ~30.39 · Come Here 31.04 | Every step, eu sei onde está · Plan it, run it… · Keep me in the loop, meu bem |

Times are truncated to 2 decimals the way the repo names downbeats (17.346 → 17.34).

## Glyph legend

| glyph | meaning |
|---|---|
| four amber arrows pointing in from the corners | push-in (slow dolly in). The dashed inner rectangle is the post-zoom end frame (`post 1.00→1.08` = the crop at the end of the scene). |
| the same + `↑ LOW ANGLE` tag | low-angle push-in (the chorus lift) |
| small wavy line, bottom-right | handheld float (Martel warmth; done in post as drift) |
| dot with a curved arrow around it | quarter orbit (feel; post drift) |
| horizontal arrow | lateral slide |
| `PUNCH-IN @ t` chip | a beat-hit punch-in in the stitch |
| `CLONES: TRIPTYCH FALLBACK` chip | the frame is a composite, not a render |
| heart · starburst · jaw arc · circle-arrow · arrow-to-ring · arrow-to-bar · finger curl · wave · rake · S-curve · shoulder roll | Heart Press · Backbeat Snaps (small = every 2 & 4) · Jaw Trace · Loop Circle ("The Loop") · Reach to Lens · Pull Back · Come Here · Ad-lib Wave · Hair Comb · Body Roll · Shoulder Roll |
| star · framed corner arrows · `[` · `]` · two overlapping boxes · bar | warm flash frame · punch-in · letterbox wipe in · letterbox wipe out · dissolve 10f · cut |
| `~` before a time | approximate (see below) |
| `←` / `→` on a lyric | the line started in the previous scene / continues into the next |

Palette: bg `#0d0d10`, text `#e8e8ee`, dim `#8a8a96`, amber `#ff9f43`, burnt orange `#d9622b`, Tuscan brown `#7a4a2a` (intro), light amber `#ffb347` (harmony). Fonts: Menlo (`/System/Library/Fonts/Menlo.ttc`) for everything, Georgia Italic for the lyrics; if either is missing the script says so on stderr and uses Pillow's default.

## Regenerate

```sh
# both boards + the letterboxed frames (inputs are optional; defaults shown)
uv run --with pillow python -I tools/storyboard.py \
  --shots shots/in-the-loop-devagar.json --timing songs/in-the-loop-devagar.timing.json \
  --frames storyboards/frames --out storyboards

# s4 again from s3 (ffmpeg 9)
zsh storyboards/frames/make_s4_triptych.sh            # s3.png -> s4.png
```

Inputs, in order of trust: `songs/<song>.timing.json` (beats, downbeats, sections, lyric lines) → `shots/<song>.json`
(`scenes[]` overlays the plan field by field; `takes[hero-1].cues` of kind `move` are the HANDS cues; `--take hero-2`
to board the safety take) → `previs/camera_plan.json` (post zooms) → `reference-master/looks.json` (scene titles, set,
light, the lead's wardrobe). Anything missing falls back to the canonical table in `.claude/skills/rnb-director/SKILL.md`,
baked into the script, and the board header says which sources were used. With no timing file at all the script uses the
plan's downbeats (1.68 4.28 … 30.38, bar ≈ 2.61 s) and the caller's approximate line times (intro 0.5–3 s, verse lines over
3–17 s, chorus lines over 17.3–31 s) and marks everything `~`.

The script prints `storyboard: warning:` lines for anything it had to shrink or cut, so a clean run means no overflow.
`frames/s{n}.png` missing → a "frame missing" placeholder panel, and the board still builds.

## Where the frames came from

All frames start from her uploaded first frame on Comfy Cloud (`660bf887…png`) through the pinned `look-wardrobe`
graph (`workflows/look-wardrobe.api.json`, Qwen-Image-2.1 Turbo edit, 8 steps), compiled by slot name:

```sh
bun tools/comfy_compile.ts look-wardrobe \
  --set image=660bf887ec0c56650a8f7b35b720ab99669054611b973146f27dd032d393faf5.png \
  --set prompt="<identity lock> + wardrobe + set + light" --set seed=<n> --out build/storyboard/sX.json
# then comfy-cloud MCP: submit_workflow({workflow: <that JSON>}) → wait_for_job → get_output → curl the shortlink (keep ?raw=1)
```

Every prompt starts with the identity lock verbatim: *"Keep the same young Black woman: her exact face, rich deep brown
skin with warm undertones and a luminous dewy sheen, copper-red hair pulled back, small white earbuds, her exact pose,
hand position and camera framing. Change only her wardrobe and the set."*

| frame | source | job (prompt_id) | seed | note |
|---|---|---|---|---|
| s1 | Comfy Cloud, look-wardrobe | `5a19c16f-3d80-44b0-b6cd-7d695dc688c6` | 11 | cream knit sweater + gold hoops; couch, fairy lights, candles, steaming mug; lamp key camera-left. Skin tone true (checked). |
| s2 | Comfy Cloud, look-wardrobe | `33b6e37b-74cf-4a7f-9332-30ab86d28078` | 13 | burnt-orange ribbed top; "replace the entire background with" the living room + laptop logs + garland. The first attempt (`635909ab-ecc1-447e-b3a5-1b7c3a118a91`, seed 12, "the same living room") kept the source's office background (kept as `frames/s2_attempt1-office.png`), so it was re-run once with the stronger set instruction; the compiled graphs are in `build/storyboard/` (gitignored). |
| s3 | existing look render | `eff061da-91b0-4e5b-ab16-b8f6680b0647` (Reference Master) | 11 | `reference-master/tests/wardrobe_chorus-stage.png`, copied as `frames/s3.png`. |
| s4 | ffmpeg triptych of s3 | — | — | **clones: triptych fallback**, `frames/make_s4_triptych.sh`: the lead full size in the centre with feathered edges, two backups at 0.6 scale behind-left/right (left hflipped so the flanks mirror each other), darkened 25 %, gblur 1.4, edges feathered; a blurred, darkened copy of the stage as the backdrop; a faint amber vignette (≤ 28 % at the corners). V formation per the director's rules. Not a render. |

`look-wardrobe` has **no `negative` slot** (the pinned graph's `TextEncodeQwenImage21.negative_prompt` is `""` and not
exposed in `slots.json`), so a lightened-skin re-run would have to go through the prompt; none was needed: s1 and s2
both keep her deep brown skin.

## What is approximate (the `~` marks)

- **Lyric lines**: every line in `timing.json` is `confidence: low` except *"Little harness holding everything I've done"*
  (16.38) and *"I'm in the loop, devagar"* (22.24), which Whisper heard with word timings. Low-confidence lines carry `~`.
- **Hand cues**: the coach cues in `shots/…json` marked `timing approximate` (Palm Roll 6.90, Heart Press 25.82, Reach to
  Lens 29.74, Pull Back 30.39) carry `~`; the rest sit on detected beats.
- **If `timing.json` is missing** everything is approximate: the fallback downbeats and line times above, all marked `~`.
- **Downbeats**: the boards use `timing.json` (beat_this): 1.686 4.296 6.906 9.516 12.126 14.736 17.346 19.956 22.566
  25.176 27.786 30.396. The SKILL table quotes 4.28 / 9.50 / 14.72 / 19.94 / 25.16 / 30.38 for six of them (≤ 0.016 s off),
  so the board reads 25.17 where the table says 25.16.

## Caveats worth a director's glance

- **The hook vocal lands two bars after the chorus cut.** The scene plan cuts to the stage on 17.34, but the song's chorus
  vocal (*"I'm in the loop"*) starts at 22.24 and `timing.json`'s own sections put the chorus at 22.57–32.00; the last verse
  line (*"Little harness…"*) is sung 16.38–21.16, i.e. over the first half of S3. The beatboard's thin "audio" band and the
  `←` on S3's lyric row show this. The canonical table's *"Heart Press on 'eu sei onde está'"* therefore falls in S4
  (25.82), not S3.
- **`shots/…json` had no `scenes[]` yet** when these boards were generated (the director agent was still writing it), so
  scene set/light/camera/transition come from the canonical table; the HANDS cues come from `takes[hero-1]`.
- **Wardrobe drift.** The s1/s2 frames wear the brief's wardrobe (cream knit sweater; burnt-orange ribbed top). While this
  was being built `reference-master/looks.json` was rewritten (briefly missing) and now says a rust-brown chunky knit (s1)
  and a brown sweatshirt + denim (s2). The WARDROBE row follows `looks.json` when it exists; the caption under each frame
  says what was actually rendered. Re-render s1/s2 with the compile command above once the looks lock.
- **Post zooms** come from `previs/camera_plan.json` when present (it derives them from the dolly ratio): S1 1.00→1.08,
  S2 1.00→1.03 (under the handheld float), S3 1.00→1.12, S4 1.00→1.04 (under the orbit drift). Without it: the canonical
  1.00→1.08 (S1), 1.00→1.12 (S3), none for S2/S4. The previs' own `hand_cues` differ from `takes[hero-1]` (it mixes the
  takes); the boards follow hero-1, the take that will be shot first.
- **S4 is a composite**, not a render: a stand-in for `clones.mode: tile_source` and labelled as such on the panel.
- **Privacy**: the frames carry her face. `storyboards/` is **not** in `.gitignore` (unlike `reference-master/tests/`);
  decide before the repo goes public.
