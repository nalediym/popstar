# Taste profile from Naledi's Pinterest (scanned 2026-11-02 ~23:30 ET)

Read through Claude in Chrome on her own account; boards are private. This file holds the *synthesis* only.
The 25 saved pins + manifest (URLs, boards, roles) are staged in `build/pinterest-refs/` (gitignored) and move to
`reference-master/refs/pinterest/` once the Reference Master build is done (its tests wipe `refs/`).

## What she saves (the signals)
| Board / section | Pins | What it says |
|---|---|---|
| Ensemble Inspiration → **90s babysitter – winter** | 29 | The video's wardrobe brief, in her own words: oversized brown/rust knits, cream turtlenecks, tucked vintage sweatshirts, high-waist light denim, overalls, long camel wool coats, beanies, chunky sneakers, layered tees over long sleeves, flat-lay "outfit + books + coffee + camera" collages. Cozy, tactile, warm neutrals with one pop of rust. |
| Ensemble → **IG Model** | 87 | The glam register: red sequin/satin mini + heels, faux-leather trench, white turtleneck + knit cap editorial, black-wall studio poses, hand-on-hip confidence. |
| Ensemble → **Africa** | 17 | Headwraps and draped wax-print/ankara, a red strapless column dress, bold print maxi dresses. Heritage glam. |
| Art → **Modeling inspiration** | 43 | Expression sheets ("many pictures of a woman making faces"), posing grids, red lip + black dress + gold earrings, Rihanna. She studies *faces and poses*: Reference Master's expression sliders are on-brief. |
| Art → **Makeup Art** / **Makeup** | 22 / 31 | Glossy brown-nude lips, bronze skin sheen, defined brows, voluminous '90s curls, big hoops, a red lip for glam. |
| **Hair Technique** | 133 | '90s volume, slicked edges, kinky-straight styles, red-copper tones (matches her current hair). |
| **Vibes** | 24 | Warm *window daylight*, plants, mirror selfies, bodies in motion (pole, aerial, painting), affirmations ("I will boss up…"), Black women artists, a cartoon girl in a bonnet with a big shirt ("living the life"). Cozy, sunlit, self-possessed. |
| **105 Edgeworth** (her place) | 55 | Living room, lighting, office, kitchen sections: the real cozy-apartment set reference. |
| Home feed | | Saturated reds and burnt oranges, amber-lit interiors, glossy lips, batik textiles, flowers, headwrap portraits. |

## Director's read
1. **Palette confirmed.** Tuscan brown / burnt orange / amber is exactly what she pins. Add **cream** and **light-wash denim blue** as the neutrals that make the oranges sing.
2. **Light: she loves daylight, not only candlelight.** Vibes and Edgeworth are warm window light with plants. Scenes 1–2 should offer a **golden-hour window** option (soft daylight key from camera left, plants, warm wood) alongside the night-candles version. Decide at Reference Master time; render both looks for s1 (20 s each).
3. **Wardrobe per scene (from her own boards)**
   - s1 intro (cozy couch): oversized rust or cream knit, beanie optional, bare feet, mug. (90s babysitter)
   - s2 verse (living-room jam): tucked vintage brown sweatshirt + high-waist light denim, or cream turtleneck + camel coat draped. (90s babysitter)
   - s3 chorus (stage): the glam switch: red/burnt-orange satin or sequin slip + gold hoops + glossy brown lip + '90s volume curls. (IG Model, Makeup)
   - s4 harmony (girl group): lead in the red slip; the two backup-hers in matching amber **ankara/wax-print** wraps or headwraps a shade darker. (Africa) This makes the clones read as *hers*, not generic backup singers.
4. **Expressions:** she collects expression sheets, so the Reference Master emotion presets should be a *sheet* per look (4–6 faces) rather than one face. Cheap: one graph, several editors.
5. **Motion:** Vibes has bodies in motion (pole, aerial, painting) and mirror selfies. A mirror-selfie framing for the verse (phone in hand, soft daylight) is on-taste and matches a seated/selfie capture.
6. **Affirmation text:** she pins "I will boss up…" cards. The lyric captions could use that register: a plain sans card, white on black, one line, for the hook. Director X letterbox + a text card = era-correct and hers.

## What NOT to take
Fisheye/strobe uptempo grammar, cold blue washes, high-fashion sterility. Nothing in her boards wants that.

## Next
- Save ~20 candidate pins into `reference-master/refs/` (needs her OK; they're other people's images used as private moodboard refs).
- Fold the wardrobe lines above into `reference-master/looks.json` once the Reference Master build lands, and add the golden-hour s1 variant.
- `tools/photos_scout.py inspire --favorites` adds her own photos to the same taste pool.
