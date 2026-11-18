export const meta = {
  name: 'render-verify',
  description: 'Verify each rendered scene (outputs/scenes/<id>.mp4) against the take it was rendered from, on three lenses: identity kept, skin tone not lightened, motion follows the source. args: { scenes?: ["s1-intro", ...], project?: "/abs/path/to/project" }',
  whenToUse: 'After the scene renders are downloaded (popstar render --mark <id> --job <prompt_id>) and before the stitch, or after re-rendering one scene (pass args.scenes). Opt-in: one scout agent plus one verifier agent per scene.',
  phases: [
    { title: 'Scout', detail: 'find the renders, their source clips, the identity lock and the QA tool' },
    { title: 'Verify', detail: 'one verifier per scene: identity, skin tone, motion (tools/qa_render.py when present)' },
  ],
}

// Inputs. `args` is whatever the caller passed: { scenes?: string[], project?: string }. The script has no filesystem
// access of its own, so every fact about the project is collected by the scout agent and every check is run by a
// verifier agent inside the project. Nothing here re-renders, uploads or submits anything.
const project = args && typeof args.project === 'string' && args.project.trim() ? args.project.trim() : ''
const requested = args && Array.isArray(args.scenes)
  ? Array.from(new Set(args.scenes.map((s) => String(s).trim()).filter(Boolean)))
  : null
const projectLine = project
  ? `The project directory is ${project}; every relative path below is relative to it (cd there first, or use absolute paths).`
  : 'The project directory is the current working directory; every relative path below is relative to it.'

// ------------------------------------------------------------------ Scout: what is there to verify?

const SCOUT_SCHEMA = {
  type: 'object',
  required: ['scenes', 'missing', 'qa_tool', 'identity_lock', 'negative', 'fps'],
  properties: {
    scenes: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'render', 'clip', 'tiled', 'first_frame', 't0', 't1', 'frames', 'title', 'prompt'],
        properties: {
          id: { type: 'string' },
          render: { type: 'string' },
          clip: { type: 'string' },
          tiled: { type: 'boolean' },
          first_frame: { type: 'string' },
          t0: { type: 'number' },
          t1: { type: 'number' },
          frames: { type: 'number' },
          title: { type: 'string' },
          prompt: { type: 'string' },
        },
      },
    },
    missing: { type: 'array', items: { type: 'string' } },
    qa_tool: { type: 'string' },
    identity_lock: { type: 'string' },
    negative: { type: 'string' },
    fps: { type: 'number' },
  },
}

const scoutPrompt = [
  'You are the scout for a render-verification run of the popstar music-video pipeline.',
  projectLine,
  'Collect the facts below without changing any file, then return only the structured result.',
  `1. scenes: every rendered scene, outputs/scenes/<id>.mp4 (the scene id is the file name without .mp4). ${
    requested
      ? `Only these ids matter: ${requested.join(', ')}. Put each of them that has no render in "missing".`
      : 'List all of them; "missing" is then empty.'
  }`,
  '2. For each rendered scene, its inputs from build/render-manifest.json when it exists: the scenes[] entry with the same id has clip (the 8n+1-frame cut of the take the render was made from), tiled, first_frame.file (the look render used as frame 0), t0, t1, frames and output. When the manifest is missing or has no entry for the scene, use build/scenes/<id>.mp4 or build/scenes/<id>.cut.mp4 as the clip when one exists, and leave the rest empty ("" / 0 / false).',
  '3. title and prompt from the shot list: popstar.json -> paths.shots (default shots/<slug>.json), scenes[] by id ("" when absent).',
  '4. identity_lock and negative from popstar.json -> performer (fallback: the shot list\'s identity_lock and negative; "" when neither exists).',
  '5. fps from popstar.json (default 24).',
  '6. qa_tool: "tools/qa_render.py" when that file exists in the project, else "".',
].join('\n')

phase('Scout')
const facts = await agent(scoutPrompt, { label: 'scout', phase: 'Scout', effort: 'low', schema: SCOUT_SCHEMA })
if (!facts) throw new Error('the scout agent returned nothing (stopped or failed); relaunch the workflow')

// Dedupe the scene list (by id) and reconcile it with what was requested.
const seenIds = new Set()
let scenes = (Array.isArray(facts.scenes) ? facts.scenes : []).filter((s) => {
  if (!s || !s.id || !s.render || seenIds.has(s.id)) return false
  seenIds.add(s.id)
  return true
})
const missing = Array.isArray(facts.missing) ? facts.missing.map(String).filter(Boolean) : []
if (requested) {
  scenes = scenes.filter((s) => requested.indexOf(s.id) !== -1)
  for (const id of requested) if (!seenIds.has(id) && missing.indexOf(id) === -1) missing.push(id)
}
if (missing.length) log(`no render yet for: ${missing.join(', ')} (skipped)`)
if (!scenes.length) {
  return {
    summary: `nothing to verify: no rendered scene found${requested ? ` among ${requested.join(', ')}` : ''} (outputs/scenes/<id>.mp4)`,
    table: '',
    scenes: [],
    issues: [],
    missing,
    qa_tool: facts.qa_tool || '',
  }
}
log(`verifying ${scenes.length} scene(s): ${scenes.map((s) => s.id).join(', ')} · QA tool ${facts.qa_tool ? `found (${facts.qa_tool})` : 'not found: visual checks only'}`)

// ------------------------------------------------------------------ Verify: one agent per scene, three lenses each

const LENSES = ['identity', 'skin', 'motion']

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['scene', 'lenses', 'issues', 'evidence', 'fix'],
  properties: {
    scene: { type: 'string' },
    lenses: {
      type: 'array',
      items: {
        type: 'object',
        required: ['lens', 'verdict', 'reason', 'numbers'],
        properties: {
          lens: { type: 'string', enum: LENSES },
          verdict: { type: 'string', enum: ['PASS', 'WARN', 'FAIL', 'N/A'] },
          reason: { type: 'string' },
          numbers: { type: 'string' },
        },
      },
    },
    issues: { type: 'array', items: { type: 'string' } },
    evidence: { type: 'array', items: { type: 'string' } },
    fix: { type: 'string' },
  },
}

function verifyPrompt(s) {
  const qa = facts.qa_tool
  const fps = facts.fps || 24
  const identity = facts.identity_lock || '(none recorded: expect the same person as in the clip and the look)'
  const clip = s.clip || `(not recorded: look for build/scenes/${s.id}.mp4 or build/scenes/${s.id}.cut.mp4; when there is none, compare against the look only and set motion to N/A)`
  return [
    `You verify ONE rendered scene of the popstar music-video pipeline: ${s.id}${s.title ? ` (${s.title})` : ''}.`,
    projectLine,
    `Write only under build/qa/${s.id}/. Never re-render, upload or submit anything.`,
    '',
    `Render: ${s.render}`,
    `Source clip (the take it was rendered from): ${clip}${s.tiled ? ' -- the take was tiled x3 side by side before rendering, so expect three copies of the performer in both files' : ''}`,
    `Look / first frame: ${s.first_frame || '(none)'}`,
    `Song time: ${s.t0}-${s.t1} s · ${s.frames || '?'} frames at ${fps} fps`,
    `Identity lock (what every render must keep): ${identity}`,
    `Negative prompt: ${facts.negative || '(none recorded)'}`,
    `Scene prompt: ${s.prompt || '(not recorded)'}`,
    '',
    'Steps:',
    qa
      ? [
          `1. Run the QA tool (exit 0 PASS, 1 WARN, 2 FAIL; it writes build/qa/${s.id}/report.json and sheet.png; the first run can take a few minutes while uv installs opencv, so allow a 10 minute timeout):`,
          `   uv run --python 3.12 --with "opencv-python-headless<5" --with numpy --with pillow python -I ${qa} "${s.clip}" "${s.render}" --out build/qa/${s.id} --json`,
          '   Read report.json. Thresholds: skin = median CIELAB L* of skin pixels, render minus source: PASS <= +6, WARN <= +12, FAIL above (positive means the render lightened her); identity = SSIM of equalized face crops plus face-box drift, a proxy, not face recognition; motion = face-centre trajectory correlation r >= 0.7 and mean normalised distance <= 0.06 pass. If the tool fails to run or the clip is missing, say so in the reasons and continue with step 2.',
        ].join('\n')
      : '1. This project has no tools/qa_render.py, so there are no measured numbers: judge from frames alone and say so in each reason (numbers: "none").',
    `2. Look with your own eyes: extract 4 frames at matched timestamps from the clip and from the render with ffmpeg (ffmpeg -y -ss <t> -i <file> -frames:v 1 build/qa/${s.id}/frames/<clip|render>-<n>.png, at 5%, 35%, 65% and 95% of the render's duration; if the two durations differ, map the timestamps proportionally and note it). Open every pair with the Read tool, and the look / first frame when there is one.`,
    '3. Judge three lenses, each PASS, WARN or FAIL (N/A only when you had no evidence at all for that lens):',
    '   - identity: the same person as in the clip and the look: face shape, hair (colour and how it is worn), the earbuds, body proportions; no face swap, morphing, extra fingers or an extra person (tiled scenes excepted).',
    '   - skin: the skin is NOT lightened, greyed, ashy or pale compared with the clip and the look; the warm tone and dewy sheen hold in every frame, shadows included. Any lightening is at least WARN; clearly lighter is FAIL.',
    "   - motion: the render follows the performer's actual motion at the same timestamps (head turns, hand gestures, the timing of the moves), with no frozen, looped or drifting stretch; the duration and frame count match the clip (8n+1 frames).",
    '4. Return the structured result only: scene; one entry per lens (lens, verdict, a one-sentence reason, numbers: the measurements you used or "none"); issues: the concrete defects worth fixing, one short specific sentence each, none when all PASS; evidence: the files you wrote or read (report.json, sheet.png, frames); fix: the single change to the prompt, negative, look, first_frame_strength or seed most likely to fix a WARN or FAIL ("" when everything passes).',
  ].join('\n')
}

phase('Verify')
const results = await pipeline(scenes, (s) => agent(verifyPrompt(s), { label: s.id, phase: 'Verify', schema: VERDICT_SCHEMA }))

// ------------------------------------------------------------------ The verdict table (plain code, no agent)

const RANK = { 'N/A': 0, PASS: 1, WARN: 2, FAIL: 3 }
const norm = (t) => String(t).toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim()
const cell = (t) => String(t == null ? '' : t).replace(/\|/g, '/').replace(/\s+/g, ' ').trim()
const rows = []
const issueIndex = new Map() // normalized issue text -> { issue, scenes }
const counts = { PASS: 0, WARN: 0, FAIL: 0, ERROR: 0 }
for (let i = 0; i < scenes.length; i++) {
  const s = scenes[i]
  const r = results[i]
  if (!r) {
    counts.ERROR++
    rows.push({ scene: s.id, verdict: 'ERROR', identity: '?', skin: '?', motion: '?', reasons: ['the verifier returned nothing (stopped or failed): relaunch the workflow to retry it'], issues: [], fix: '', evidence: [] })
    continue
  }
  // One verdict per lens: the first entry for a lens wins, repeats are dropped.
  const byLens = {}
  for (const l of Array.isArray(r.lenses) ? r.lenses : []) if (l && LENSES.indexOf(l.lens) !== -1 && !byLens[l.lens]) byLens[l.lens] = l
  const verdicts = LENSES.map((k) => (byLens[k] && RANK[byLens[k].verdict] !== undefined ? byLens[k].verdict : 'N/A'))
  const worst = verdicts.reduce((a, b) => (RANK[b] > RANK[a] ? b : a), 'N/A')
  counts[worst === 'N/A' ? 'ERROR' : worst]++
  // Dedupe the issues within the scene and index them across scenes.
  const seen = new Set()
  const issues = []
  for (const it of Array.isArray(r.issues) ? r.issues : []) {
    const k = norm(it)
    if (!k || seen.has(k)) continue
    seen.add(k)
    issues.push(String(it).trim())
    const e = issueIndex.get(k) || { issue: String(it).trim(), scenes: [] }
    e.scenes.push(s.id)
    issueIndex.set(k, e)
  }
  rows.push({
    scene: s.id,
    verdict: worst,
    identity: verdicts[0],
    skin: verdicts[1],
    motion: verdicts[2],
    reasons: LENSES.map((k) => (byLens[k] ? `${k}: ${byLens[k].reason}${byLens[k].numbers && norm(byLens[k].numbers) !== 'none' ? ` [${byLens[k].numbers}]` : ''}` : `${k}: no verdict`)),
    issues,
    fix: r.fix ? String(r.fix).trim() : '',
    evidence: Array.isArray(r.evidence) ? r.evidence.map(String) : [],
  })
}

const lines = ['| scene | verdict | identity | skin | motion | fix |', '|---|---|---|---|---|---|']
for (const row of rows) lines.push(`| ${cell(row.scene)} | ${cell(row.verdict)} | ${cell(row.identity)} | ${cell(row.skin)} | ${cell(row.motion)} | ${cell(row.fix)} |`)
const issues = Array.from(issueIndex.values())
if (issues.length) {
  lines.push('', '| issue | scenes |', '|---|---|')
  for (const e of issues) lines.push(`| ${cell(e.issue)} | ${cell(e.scenes.join(', '))} |`)
}
const redo = rows.filter((r) => r.verdict !== 'PASS').map((r) => r.scene)
const summary =
  `${rows.length} scene(s) verified: ${counts.PASS} pass, ${counts.WARN} warn, ${counts.FAIL} fail${counts.ERROR ? `, ${counts.ERROR} without a verdict` : ''}` +
  `${missing.length ? `; not rendered yet: ${missing.join(', ')}` : ''}. ` +
  (redo.length
    ? `Re-render ${redo.join(', ')} with the suggested fix, then rerun this workflow with args {"scenes": ${JSON.stringify(redo)}}.`
    : 'Every scene holds identity, skin tone and motion: ready to stitch.')
log(summary)

return { summary, table: lines.join('\n'), scenes: rows, issues, missing, qa_tool: facts.qa_tool || '' }
