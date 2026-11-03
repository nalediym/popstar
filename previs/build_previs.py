"""build_previs.py: 3D previs / blocking animatic for the R&B music video "In the Loop (Devagar)".

Headless, from scratch, reproducible (Blender 5.2):

  /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup -P previs/build_previs.py -- \
      --song songs/in-the-loop-devagar.mp3 \
      [--timing songs/in-the-loop-devagar.timing.json] [--looks reference-master/looks.json] \
      [--shots shots/in-the-loop-devagar.json] \
      --out-blend previs/in-the-loop-devagar_previs.blend --stills-dir previs/stills \
      [--animatic outputs/previs/animatic.mp4] [--res 854x480]

What it builds: four sets far apart on X (S1 couch, S2 living-room jam, S3 stage, S4 girl-group stage), a primitive
mannequin per performer (three in S4), one camera rig per scene bound to a timeline marker at its cut frame
(cut = 1 frame before the downbeat), the song as a sequencer sound strip, an EEVEE look with burn-in stamps, a
mid-scene still per scene, the animatic mp4, and camera_plan.json (the 2D post-move equivalents for the stitch).

Inputs are optional and read-only. If --timing/--shots/--looks are omitted, the conventional paths next to the
song are tried (songs/<stem>.timing.json, shots/<stem>.json, reference-master/looks.json). Without them the
locked director plan below is used.
"""

import argparse
import datetime
import json
import math
import os
import random
import re
import sys
import time

import bmesh
import bpy
import bpy_extras.anim_utils as anim_utils
from mathutils import Euler, Matrix, Vector

FPS = 24
T_START = time.time()


def log(*a):
    print("[previs %6.1fs]" % (time.time() - T_START), *a, flush=True)


# ---------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------

def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="build_previs.py", description=__doc__.split("\n")[0])
    p.add_argument("--song", required=True, help="song mp3 (becomes a sequencer sound strip)")
    p.add_argument("--timing", help="songs/<song>.timing.json: beats, downbeats, lyric lines")
    p.add_argument("--looks", help="reference-master/looks.json: per-scene set/light and wardrobe per look")
    p.add_argument("--shots", help="shots/<song>.json: scenes[] (t0/t1/camera) and takes[].cues[] (hand moves)")
    p.add_argument("--out-blend", required=True)
    p.add_argument("--stills-dir", required=True)
    p.add_argument("--animatic", help="render the full animatic mp4 here (song audio muxed in)")
    p.add_argument("--res", default="854x480", help="animatic resolution WxH")
    p.add_argument("--stills-res", default="1280x720")
    p.add_argument("--camera-plan", help="default: camera_plan.json next to --out-blend")
    p.add_argument("--samples", type=int, default=16, help="EEVEE samples for the animatic")
    p.add_argument("--stills-samples", type=int, default=64)
    p.add_argument("--engine", choices=["eevee", "workbench"], default="eevee",
                   help="workbench = fast fallback if EEVEE cannot render on this machine")
    p.add_argument("--skip-stills", action="store_true", help="don't re-render the four stills")
    p.add_argument("--extra-stills", default="", help="debug: comma list of extra frames to render as stills")
    p.add_argument("--no-auto-inputs", action="store_true", help="don't look for timing/shots/looks next to the song")
    a = p.parse_args(argv)
    for k in ("song", "timing", "looks", "shots", "out_blend", "stills_dir", "animatic", "camera_plan"):
        v = getattr(a, k)
        if v:
            setattr(a, k, os.path.abspath(v))
    if not a.no_auto_inputs:
        song_dir = os.path.dirname(a.song)
        stem = os.path.splitext(os.path.basename(a.song))[0]
        root = os.path.dirname(song_dir)
        guesses = {
            "timing": os.path.join(song_dir, stem + ".timing.json"),
            "shots": os.path.join(root, "shots", stem + ".json"),
            "looks": os.path.join(root, "reference-master", "looks.json"),
        }
        for k, g in guesses.items():
            if not getattr(a, k) and os.path.isfile(g):
                setattr(a, k, g)
    if not a.camera_plan:
        a.camera_plan = os.path.join(os.path.dirname(a.out_blend), "camera_plan.json")
    return a


def parse_res(s):
    w, h = s.lower().split("x")
    return int(w), int(h)


# ---------------------------------------------------------------------------------------------------------------
# Inputs (all optional, parsed tolerantly: other agents own these files)
# ---------------------------------------------------------------------------------------------------------------

DEFAULT_DOWNBEATS = [1.68, 4.28, 6.90, 9.50, 12.12, 14.72, 17.34, 19.94, 22.56, 25.16, 27.78, 30.38]

# The locked director plan (PLAN.md Vision v2 + rnb-director canonical table).
DEFAULT_PLAN = [
    dict(id="s1-intro", title="Cozy couch intro", t0=0.00, t1=6.90, set="living", variant="couch",
         move="slow_push_in", focal=50.0, tag="couch", section="intro + verse in", post=(1.00, 1.08)),
    dict(id="s2-verse", title="Living-room jam", t0=6.90, t1=17.34, set="living", variant="jam",
         move="handheld_float", focal=35.0, tag="jam", section="verse", post=(1.00, 1.03)),
    dict(id="s3-chorus", title="Holiday stage", t0=17.34, t1=25.16, set="stage", variant="solo",
         move="low_angle_push_in", focal=50.0, tag="stage", section="chorus lift (hook)", post=(1.00, 1.12)),
    dict(id="s4-harmony", title="Girl group of me", t0=25.16, t1=32.00, set="stage", variant="trio",
         move="quarter_orbit", focal=35.0, tag="girl group", section="chorus out + harmonies", post=(1.00, 1.04)),
]
MOVE_LABEL = {
    "slow_push_in": "slow push-in", "handheld_float": "handheld float", "low_angle_push_in": "low-angle push-in",
    "quarter_orbit": "quarter orbit", "lateral_slide": "lateral slide", "static": "locked off",
}

def fallback_cues(lines, beats, downbeats, song_end):
    """Hand cues when the shots file has none: the rnb-director canonical plan, anchored to the timing file's
    lyric lines and beats (falls back to fixed times without lyric lines)."""
    def line_with(phrase):
        return next((ln for ln in lines if phrase in ln["text"].lower()), None)

    def phrase_t(phrase, default):
        ln = line_with(phrase)
        if not ln:
            return default
        low = ln["text"].lower()
        return ln["t0"] + (ln["t1"] - ln["t0"]) * low.find(phrase) / max(1, len(low))
    cues = [dict(t=downbeats[0], dur=1.96, move="heart_press", text="Heart Press (bar 1)")]
    snap_db = min(downbeats, key=lambda d: abs(d - 9.5))
    cues.append(dict(t=snap_db, dur=5.0, move="backbeat_snap", text="Backbeat Snaps (2 & 4)"))
    run = line_with("the run")                 # held note at the end of "...tests before the run"
    cues.append(dict(t=(run["t1"] - 0.85) if run else 15.40, dur=1.5, move="jaw_trace", text="Jaw Trace (held note)"))
    cues.append(dict(t=phrase_t("i'm in the loop", 22.24), dur=1.8, move="loop_circle",
                     text="Loop circle: I'm in the loop"))
    cues.append(dict(t=phrase_t("eu sei", 26.25), dur=1.4, move="heart_press", text="Heart Press: eu sei onde esta"))
    final_beat = max(b for b in beats if b < song_end - 0.1)
    come = final_beat - 0.5
    keep = phrase_t("keep me", final_beat - 1.4)
    if come - keep > 1.9:      # room for the full Reach to Lens -> Pull Back -> Come Here
        cues.append(dict(t=keep, dur=0.9, move="reach_to_lens", text="Reach to Lens: keep me in the loop"))
        cues.append(dict(t=keep + 0.95, dur=max(0.3, come - keep - 1.4), move="pull_back", text="Pull Back: meu bem"))
    else:                       # tight: the travel from the reach into the beckon *is* the pull back
        cues.append(dict(t=keep, dur=max(0.55, come - keep - 0.15), move="reach_to_lens",
                         text="Reach to Lens: keep me in the loop"))
    cues.append(dict(t=come, dur=song_end - come, move="come_here", text="Come Here (final beat)"))
    return cues


def load_json(path, what):
    if not path:
        log(f"input {what}: none")
        return None
    if not os.path.isfile(path):
        log(f"input {what}: {path} not found, using defaults")
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        log(f"input {what}: {path}")
        return data
    except Exception as ex:  # half-written by another agent, etc.
        log(f"WARN input {what}: could not parse {path} ({ex}); using defaults")
        return None


def _num(x, keys=("t", "time", "s", "sec", "seconds", "start", "t0")):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        for k in keys:
            if isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool):
                return float(x[k])
    return None


def find_key(obj, names, depth=0):
    """First value under any of `names`, searching dicts/lists breadth-first-ish."""
    if depth > 4:
        return None
    if isinstance(obj, dict):
        for n in names:
            if n in obj:
                return obj[n]
        for v in obj.values():
            r = find_key(v, names, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj[:64]:
            r = find_key(v, names, depth + 1)
            if r is not None:
                return r
    return None


def parse_timing(data):
    out = dict(downbeats=None, beats=None, lines=[], sections=[])
    if not data:
        return out
    sc = data.get("sections") if isinstance(data, dict) else None
    if isinstance(sc, list):
        for s in sc:
            if isinstance(s, dict) and isinstance(s.get("name"), str):
                a = _num(s, ("start", "t0", "start_s"))
                b = _num(s, ("end", "t1", "end_s"))
                if a is not None and b is not None:
                    out["sections"].append(dict(name=s["name"], start=a, end=b))
    db = find_key(data, ["downbeats", "downbeat_times", "downbeats_s", "bars"])
    if isinstance(db, list):
        vals = sorted(v for v in (_num(x) for x in db) if v is not None)
        if len(vals) >= 4:
            out["downbeats"] = vals
    bt = find_key(data, ["beats", "beat_times", "beats_s"])
    if isinstance(bt, list):
        vals = sorted(v for v in (_num(x) for x in bt) if v is not None)
        if len(vals) >= 8:
            out["beats"] = vals
    ln = find_key(data, ["lines", "lyric_lines", "lyrics", "lyrics_lines", "segments"])
    if isinstance(ln, list):
        for x in ln:
            if not isinstance(x, dict):
                continue
            text = next((x[k] for k in ("text", "line", "lyric", "words") if isinstance(x.get(k), str)), None)
            t0 = _num(x, ("t0", "start", "start_s", "t", "begin", "time"))
            t1 = _num(x, ("t1", "end", "end_s", "stop", "finish"))
            if text and t0 is not None:
                out["lines"].append(dict(t0=t0, t1=t1 if t1 is not None else t0 + 2.5, text=text.strip()))
        out["lines"].sort(key=lambda l: l["t0"])
    return out


def beats_from_downbeats(downbeats, t_end):
    """4/4: subdivide each bar; extrapolate the pickup before bar 1 and the tail after the last bar."""
    beats = []
    for a, b in zip(downbeats, downbeats[1:]):
        for k in range(4):
            beats.append(a + (b - a) * k / 4)
    per = (downbeats[-1] - downbeats[-2]) / 4
    t = downbeats[-1]
    while t < t_end:
        beats.append(t)
        t += per
    per0 = (downbeats[1] - downbeats[0]) / 4
    t = downbeats[0] - per0
    while t > 0:
        beats.insert(0, t)
        t -= per0
    return beats


# ---------------------------------------------------------------------------------------------------------------
# Small math / colour helpers
# ---------------------------------------------------------------------------------------------------------------

def srgb(h, a=1.0):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (a,)


def scale_rgb(c, k):
    return (c[0] * k, c[1] * k, c[2] * k, 1.0)


def ease(u):
    """Sine ease-in-out; identical to Blender's SINE + EASE_IN_OUT keyframe interpolation."""
    u = min(1.0, max(0.0, u))
    return 0.5 - 0.5 * math.cos(math.pi * u)


def smooth01(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def lerp(a, b, u):
    return a + (b - a) * u


def vlerp(a, b, u):
    return Vector(a).lerp(Vector(b), u)


KEY_RGB = (1.0, 0.72, 0.45)       # ~2800K soft key
GOLD_RGB = (1.0, 0.76, 0.40)      # gold bounce
AMBER_RGB = (1.0, 0.52, 0.20)     # amber rim
BURNT_RGB = (1.0, 0.40, 0.14)     # burnt-orange wash
FLAME_RGB = (1.0, 0.56, 0.20)
BULB_RGB = (1.0, 0.62, 0.30)

# ---------------------------------------------------------------------------------------------------------------
# Scene reset + collections
# ---------------------------------------------------------------------------------------------------------------


def reset_scene():
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    except Exception as ex:
        log("read_factory_settings failed, clearing by hand:", ex)
        for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
            for d in list(coll):
                coll.remove(d)
    sc = bpy.context.scene
    sc.name = "previs"
    return sc


def new_collection(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    return c


def new_empty(name, coll, loc=(0, 0, 0), rot=(0, 0, 0), parent=None, size=0.08, kind="PLAIN_AXES"):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = kind
    ob.empty_display_size = size
    coll.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    ob.location = loc
    ob.rotation_euler = rot
    return ob


def link_obj(name, data, coll, loc=(0, 0, 0), rot=(0, 0, 0), parent=None):
    ob = bpy.data.objects.new(name, data)
    coll.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    ob.location = loc
    ob.rotation_euler = rot
    return ob


# ---------------------------------------------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------------------------------------------

MATS = {}


def _ensure_nodes(idb):
    """Blender 5.x deprecates use_nodes (node trees become the default); only touch it when there is no tree."""
    if getattr(idb, "node_tree", None) is None:
        idb.use_nodes = True
    return idb.node_tree


def _principled_node(m):
    nt = _ensure_nodes(m)
    b = nt.nodes.get("Principled BSDF")
    if b is None:
        b = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = nt.nodes.get("Material Output") or nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(b.outputs[0], out.inputs["Surface"])
    return b


def principled(name, base, rough=0.5, metal=0.0, spec=0.5, sheen=0.0, sheen_tint=(1, 1, 1, 1), sheen_rough=0.5,
               coat=0.0, coat_rough=0.1, sss=0.0, sss_radius=(1.0, 0.4, 0.25), aniso=0.0, emit=None, emit_k=0.0):
    m = bpy.data.materials.new(name)
    _ensure_nodes(m)
    b = _principled_node(m)
    i = b.inputs
    i["Base Color"].default_value = base
    i["Roughness"].default_value = rough
    i["Metallic"].default_value = metal
    i["Specular IOR Level"].default_value = spec
    i["Sheen Weight"].default_value = sheen
    i["Sheen Tint"].default_value = sheen_tint
    i["Sheen Roughness"].default_value = sheen_rough
    i["Coat Weight"].default_value = coat
    i["Coat Roughness"].default_value = coat_rough
    i["Subsurface Weight"].default_value = sss
    i["Subsurface Radius"].default_value = sss_radius
    i["Subsurface Scale"].default_value = 0.02
    i["Anisotropic"].default_value = aniso
    if emit is not None:
        i["Emission Color"].default_value = emit
        i["Emission Strength"].default_value = emit_k
    m.diffuse_color = base  # Workbench fallback colour
    m.roughness = rough
    return m


def emissive(name, color, strength):
    m = bpy.data.materials.new(name)
    _ensure_nodes(m)
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = color
    e.inputs["Strength"].default_value = strength
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(e.outputs[0], o.inputs["Surface"])
    m.diffuse_color = color
    return m


def sock(sockets, name, typ=None):
    for s in sockets:
        if s.name == name and (typ is None or s.type == typ):
            return s
    raise KeyError(name)


def math_node(nt, op, a=None, b=None, value_b=None):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    if a is not None:
        nt.links.new(a, n.inputs[0])
    if b is not None:
        nt.links.new(b, n.inputs[1])
    elif value_b is not None:
        n.inputs[1].default_value = value_b
    return n.outputs[0]


def logs_screen_material(name):
    """Amber-on-black terminal with log lines; Mapping Z is keyed later so the logs scroll line by line."""
    m = bpy.data.materials.new(name)
    _ensure_nodes(m)
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.name = "LogScroll"
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(mp.outputs["Vector"], sep.inputs[0])
    lines_per_m = 1.0 / 0.0072
    zl = math_node(nt, "MULTIPLY", sep.outputs["Z"], value_b=lines_per_m)
    li = math_node(nt, "FLOOR", zl)
    fr = math_node(nt, "FRACT", zl)
    row = math_node(nt, "LESS_THAN", fr, value_b=0.62)
    w1 = nt.nodes.new("ShaderNodeTexWhiteNoise")
    w1.noise_dimensions = "1D"
    nt.links.new(li, w1.inputs["W"])
    li2 = math_node(nt, "ADD", li, value_b=17.31)
    w2 = nt.nodes.new("ShaderNodeTexWhiteNoise")
    w2.noise_dimensions = "1D"
    nt.links.new(li2, w2.inputs["W"])
    xs = math_node(nt, "ADD", sep.outputs["X"], value_b=0.135)
    indent = math_node(nt, "MULTIPLY", math_node(nt, "FLOOR", math_node(nt, "MULTIPLY", w2.outputs["Value"], value_b=3.0)),
                       value_b=0.014)
    length = math_node(nt, "ADD", math_node(nt, "MULTIPLY", w1.outputs["Value"], value_b=0.21), value_b=0.03)
    right = math_node(nt, "ADD", length, indent)
    c1 = math_node(nt, "GREATER_THAN", xs, indent)
    c2 = math_node(nt, "LESS_THAN", xs, right)
    wx = math_node(nt, "ADD", math_node(nt, "MULTIPLY", xs, value_b=90.0), math_node(nt, "MULTIPLY", li, value_b=3.7))
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.noise_dimensions = "1D"
    nz.inputs["Scale"].default_value = 1.0
    nz.inputs["Detail"].default_value = 0.0
    nt.links.new(wx, nz.inputs["W"])
    words = math_node(nt, "GREATER_THAN", nz.outputs["Fac"], value_b=0.43)
    mask = math_node(nt, "MULTIPLY", math_node(nt, "MULTIPLY", row, c1), math_node(nt, "MULTIPLY", c2, words))
    hl = math_node(nt, "GREATER_THAN", w2.outputs["Value"], value_b=0.82)
    mix_tc = nt.nodes.new("ShaderNodeMix")
    mix_tc.data_type = "RGBA"
    nt.links.new(hl, mix_tc.inputs[0])
    sock(mix_tc.inputs, "A", "RGBA").default_value = (1.0, 0.50, 0.12, 1)
    sock(mix_tc.inputs, "B", "RGBA").default_value = (1.0, 0.86, 0.62, 1)
    mix_bg = nt.nodes.new("ShaderNodeMix")
    mix_bg.data_type = "RGBA"
    nt.links.new(mask, mix_bg.inputs[0])
    sock(mix_bg.inputs, "A", "RGBA").default_value = (0.030, 0.014, 0.006, 1)
    nt.links.new(sock(mix_tc.outputs, "Result", "RGBA"), sock(mix_bg.inputs, "B", "RGBA"))
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 3.2
    nt.links.new(sock(mix_bg.outputs, "Result", "RGBA"), em.inputs["Color"])
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], o.inputs["Surface"])
    m.diffuse_color = (0.6, 0.3, 0.1, 1)
    return m


def steam_material(name):
    m = bpy.data.materials.new(name)
    _ensure_nodes(m)
    try:
        m.surface_render_method = "BLENDED"
    except Exception:
        pass
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])
    mr = nt.nodes.new("ShaderNodeMapRange")  # fade out with height
    mr.inputs["From Min"].default_value = 0.0
    mr.inputs["From Max"].default_value = 0.24
    mr.inputs["To Min"].default_value = 1.0
    mr.inputs["To Max"].default_value = 0.0
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    fade_in = nt.nodes.new("ShaderNodeMapRange")
    fade_in.inputs["From Min"].default_value = 0.0
    fade_in.inputs["From Max"].default_value = 0.03
    nt.links.new(sep.outputs["Z"], fade_in.inputs["Value"])
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.name = "SteamNoise"
    nz.noise_dimensions = "4D"
    nz.inputs["Scale"].default_value = 18.0
    nz.inputs["Detail"].default_value = 2.0
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    a = math_node(nt, "MULTIPLY", mr.outputs[0], fade_in.outputs[0])
    a = math_node(nt, "MULTIPLY", a, math_node(nt, "MULTIPLY", math_node(nt, "SUBTRACT", nz.outputs["Fac"], value_b=0.35),
                                                    value_b=1.6))
    a = math_node(nt, "MULTIPLY", a, value_b=0.9)
    a = math_node(nt, "MAXIMUM", a, value_b=0.0)
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (1.0, 0.86, 0.72, 1)
    em.inputs["Strength"].default_value = 1.4
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(a, mix.inputs["Fac"])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(mix.outputs[0], o.inputs["Surface"])
    m.diffuse_color = (1, 1, 1, 0.2)
    return m


def haze_material(name, density, color=(1.0, 0.80, 0.62, 1), anisotropy=0.55):
    m = bpy.data.materials.new(name)
    _ensure_nodes(m)
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    pv = nt.nodes.new("ShaderNodeVolumePrincipled")
    pv.inputs["Color"].default_value = color
    pv.inputs["Density"].default_value = density
    pv.inputs["Anisotropy"].default_value = anisotropy
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(pv.outputs["Volume"], o.inputs["Volume"])
    return m


def build_materials():
    M = MATS
    M["skin"] = principled("skin_deep_brown", srgb("5a3825"), rough=0.40, spec=0.6, coat=0.16, coat_rough=0.32,
                           sss=0.08, sss_radius=(1.0, 0.35, 0.2), sheen=0.15, sheen_tint=(1.0, 0.8, 0.6, 1))
    M["hair"] = principled("hair_copper", srgb("b24a20"), rough=0.5, spec=0.5, sheen=0.7,
                           sheen_tint=(1.0, 0.65, 0.42, 1), sheen_rough=0.35)
    M["gold"] = principled("gold", srgb("e0a84e"), rough=0.22, metal=1.0)
    M["earbud"] = principled("earbud_white", srgb("eeeae4"), rough=0.3)
    M["wall"] = principled("wall_tuscan_plaster", srgb("38231a"), rough=0.92, spec=0.3)
    M["floor"] = principled("floor_dark_wood", srgb("2f1d12"), rough=0.38, coat=0.2)
    M["wood"] = principled("wood_walnut", srgb("3a2416"), rough=0.42, coat=0.25)
    M["suede"] = principled("suede_tan", srgb("8c5f3d"), rough=0.9, spec=0.35, sheen=0.7,
                            sheen_tint=(1.0, 0.86, 0.72, 1), sheen_rough=0.4)
    M["rug"] = principled("rug_rust", srgb("7a3313"), rough=1.0, sheen=0.5, sheen_tint=(1, 0.7, 0.5, 1))
    M["pillow_orange"] = principled("pillow_burnt_orange", srgb("b04a17"), rough=0.85, sheen=0.6)
    M["pillow_cream"] = principled("pillow_cream_knit", srgb("dccbb0"), rough=0.95, sheen=0.7)
    M["wax"] = principled("candle_wax", srgb("ecdcc0"), rough=0.5, sss=0.3, sss_radius=(1.0, 0.7, 0.4))
    M["flame"] = emissive("flame", FLAME_RGB + (1,), 55.0)
    M["bulb"] = emissive("fairy_bulb", BULB_RGB + (1,), 32.0)
    M["bulb_soft"] = emissive("fairy_bulb_soft", BULB_RGB + (1,), 18.0)
    M["shade"] = emissive("lamp_shade_glow", (1.0, 0.66, 0.38, 1), 3.2)
    M["ceramic"] = principled("mug_glaze_burnt_orange", srgb("a84d22"), rough=0.22, coat=0.6, coat_rough=0.1)
    M["tea"] = principled("tea", srgb("3a1a08"), rough=0.08)
    M["steam"] = steam_material("steam")
    M["alu"] = principled("laptop_alu", srgb("3e3a37"), rough=0.35, metal=1.0)
    M["screen"] = logs_screen_material("laptop_logs_amber_terminal")
    M["garland"] = principled("garland_pine", srgb("1f2817"), rough=0.8, sheen=0.4)
    M["ornament_orange"] = principled("ornament_burnt_orange", srgb("c05a1c"), rough=0.18, coat=0.8)
    M["velvet"] = principled("velvet_tuscan_brown", srgb("6e3a1d"), rough=0.92, spec=0.25, sheen=1.0,
                             sheen_tint=(1.0, 0.55, 0.32, 1), sheen_rough=0.32)
    M["velvet_dark"] = principled("velvet_dark", srgb("33180b"), rough=0.95, spec=0.2, sheen=1.0,
                                  sheen_tint=(1.0, 0.5, 0.3, 1), sheen_rough=0.3)
    M["stage_top"] = principled("stage_gloss_wood", srgb("2a170d"), rough=0.2, coat=0.6, coat_rough=0.05)
    M["house_floor"] = principled("house_floor", srgb("140b06"), rough=0.85)
    M["chrome"] = principled("mic_chrome", srgb("8a8580"), rough=0.18, metal=1.0)
    M["black"] = principled("matte_black", srgb("120d0a"), rough=0.6)
    M["grille"] = principled("mic_grille", srgb("b9b3aa"), rough=0.35, metal=1.0)


# Wardrobe ------------------------------------------------------------------------------------------------------

COLOR_WORDS = [  # longest first
    ("burnt orange", "a8481a"), ("burnt-orange", "a8481a"), ("tuscan brown", "6b3f22"), ("dark amber", "a8661c"),
    ("rust", "9c3f16"), ("copper", "b4582a"), ("terracotta", "b0532e"), ("cream", "e4d4ba"), ("ivory", "ece2cf"),
    ("oatmeal", "d6c4a6"), ("camel", "b08354"), ("caramel", "b0702e"), ("tan", "b08a62"), ("cocoa", "4e2e1c"),
    ("chocolate", "3e2416"), ("espresso", "2e1a10"), ("mocha", "5e3d2a"), ("brown", "5a3720"), ("amber", "d2861f"),
    ("honey", "c98a2e"), ("gold", "c9952f"), ("bronze", "9a6a2a"), ("orange", "d0631c"), ("wine", "5e1a1e"),
    ("burgundy", "5a1620"), ("black", "151010"), ("white", "ece6dc"), ("beige", "cdb594"),
    ("light wash", "8fa6bd"), ("denim", "5b6f8a"),
]


def color_from_text(text, default_hex):
    t = (text or "").lower().replace("-", " ")
    best = None
    for word, hx in COLOR_WORDS:
        m = re.search(r"\b%s\b" % re.escape(word), t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), hx)
    return best[1] if best else None


RX_DRESS = r"\b(slip dress|slip|dress|gown|jumpsuit)\b"          # whole words: "slipping" is not a slip
RX_TOP = r"\b(sweater|sweatshirt|pullover|jumper|knit|cardigan|hoodie|turtleneck|top|tee|shirt|blouse|cami|" \
         r"camisole|halter|bralette|bustier|corset|tank|bodysuit)\b"
RX_BOTTOM = r"\b(pants|trousers|cargos?|jeans|denim|leggings|joggers|skirt|shorts)\b"
RX_SET = r"\b(two piece|set|co ord|matching)\b"
FABRICS = ("satin", "silk", "knit", "velvet", "suede", "leather", "ribbed", "cotton", "denim")


def wardrobe_spec(text, default):
    """Free wardrobe text -> mannequin outfit (garment kinds, colours, fabrics), clause by clause."""
    spec = dict(default)
    if not text:
        return spec
    spec["text"] = text
    t = text.lower().replace("-", " ")
    clauses = [c.strip() for c in re.split(r",|;|:|\band\b|\bwith\b", t) if c.strip()]

    def fabric_in(c):
        return next((f for f in FABRICS if re.search(r"\b%s\b" % f, c)), None)
    top_c = next((c for c in clauses if re.search(RX_DRESS, c)), None)
    dress = top_c is not None
    top_c = top_c or next((c for c in clauses if re.search(RX_TOP, c)), None)
    set_c = next((c for c in clauses if re.search(RX_SET, c)), None)
    bot_c = next((c for c in clauses if re.search(RX_BOTTOM, c)), None)
    # garment shape
    if dress:
        spec.update(neckline="cami", sleeves="none", bottom="skirt")
    elif top_c:
        if re.search(r"\b(cami|camisole|halter|strap|straps|strapless|bustier|corset|tank|bralette)\b", t):
            spec.update(neckline="cami", sleeves="none")
        elif re.search(r"\b(sweater|sweatshirt|pullover|jumper|knit|cardigan|hoodie|turtleneck|long sleeve|"
                       r"long sleeved)\b", t):
            spec.update(neckline="crew", sleeves="long")
        else:
            spec.update(neckline="crew", sleeves="none")
    if bot_c:
        spec["bottom"] = "skirt" if re.search(r"\bskirt\b", bot_c) else "pants"
    spec["cropped"] = bool(re.search(r"\bcrop(ped)?\b", t))
    spec["headwrap"] = bool(re.search(r"\b(headwrap|head wrap|turban|head scarf|headscarf)\b", t))
    spec["wide"] = bool(re.search(r"\b(wide leg|palazzo|flare|flared)\b", t))
    spec["oversized"] = bool(re.search(r"\b(oversized|chunky|slouchy)\b", t)) or spec.get("oversized", False)
    # colours: garment clause, else the set clause, else anywhere
    any_col = color_from_text(t, None)
    set_col = color_from_text(set_c, None) if set_c else None
    top_col = (color_from_text(top_c, None) if top_c else None) or set_col or any_col
    if top_col:
        spec["top_hex"] = top_col
    if dress:
        spec["bottom_hex"] = spec["top_hex"]
    elif bot_c:
        bc = color_from_text(bot_c, None)
        if bc:
            spec["bottom_hex"] = bc
        elif re.search(r"\bmatching\b", bot_c) or set_c:
            spec["bottom_hex"] = spec["top_hex"]
    if re.search(r"\b(deeper|darker|one shade darker)\b", t):
        spec["darken_text"] = True
    # fabrics per garment
    spec["fabric_top"] = (fabric_in(top_c) if top_c else None) or (fabric_in(set_c) if set_c else None) \
        or spec.get("fabric_top") or spec.get("fabric", "cotton")
    spec["fabric_bottom"] = (fabric_in(bot_c) if bot_c else None) or (fabric_in(set_c) if set_c else None) \
        or (spec["fabric_top"] if dress else spec.get("fabric_bottom", spec.get("fabric", "cotton")))
    return spec


DEFAULT_WARDROBE = {
    "S1": dict(text="oversized cream knit sweater, cocoa lounge pants", neckline="crew", sleeves="long",
               bottom="pants", top_hex="e4d4ba", bottom_hex="4e2e1c", fabric_top="knit", fabric_bottom="knit",
               oversized=True),
    "S2": dict(text="fitted burnt-orange ribbed top, camel trousers", neckline="crew", sleeves="none",
               bottom="pants", top_hex="a8481a", bottom_hex="9b7048", fabric_top="ribbed", fabric_bottom="cotton"),
    "S3": dict(text="burnt-orange satin slip dress, gold hoops", neckline="cami", sleeves="none", bottom="skirt",
               top_hex="a8481a", bottom_hex="a8481a", fabric_top="satin", fabric_bottom="satin"),
    "S4": dict(text="amber satin cami + wide-leg satin pants (matching set)", neckline="cami", sleeves="none",
               bottom="pants", top_hex="d2861f", bottom_hex="d2861f", fabric_top="satin", fabric_bottom="satin",
               wide=True),
}


def fabric_material(name, hex_color, fabric, darken=1.0):
    base = srgb(hex_color)
    base = (base[0] * darken, base[1] * darken, base[2] * darken, 1.0)
    if fabric in ("satin", "silk"):
        return principled(name, base, rough=0.24, spec=0.75, aniso=0.55, sheen=0.35, sheen_rough=0.3, coat=0.1)
    if fabric in ("knit", "velvet", "suede"):
        return principled(name, base, rough=0.95, spec=0.3, sheen=0.9, sheen_tint=(1, 0.92, 0.85, 1),
                          sheen_rough=0.45)
    if fabric == "leather":
        return principled(name, base, rough=0.35, spec=0.6, coat=0.3)
    return principled(name, base, rough=0.7, spec=0.4, sheen=0.4)


# ---------------------------------------------------------------------------------------------------------------
# Geometry helpers (bmesh, no operators)
# ---------------------------------------------------------------------------------------------------------------

def _mat4(center=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    return (Matrix.Translation(Vector(center)) @ Euler(rot).to_matrix().to_4x4() @
            Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0)))


def _tag(res, mi):
    faces = set()
    for v in res["verts"]:
        faces.update(v.link_faces)
    for f in faces:
        f.material_index = mi


def add_box(bm, size, center=(0, 0, 0), rot=(0, 0, 0), mi=0):
    _tag(bmesh.ops.create_cube(bm, size=1.0, matrix=_mat4(center, rot, size)), mi)


def add_cyl(bm, r1, r2, depth, center=(0, 0, 0), rot=(0, 0, 0), segs=24, mi=0, caps=True):
    _tag(bmesh.ops.create_cone(bm, cap_ends=caps, cap_tris=False, segments=segs, radius1=r1, radius2=r2,
                               depth=depth, matrix=_mat4(center, rot)), mi)


def add_sphere(bm, radii, center=(0, 0, 0), rot=(0, 0, 0), u=20, v=12, mi=0):
    _tag(bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=v, radius=1.0, matrix=_mat4(center, rot, radii)), mi)


def add_ico(bm, r, center, subdiv=1, mi=0):
    _tag(bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=r, matrix=Matrix.Translation(Vector(center))), mi)


def add_torus(bm, R, r, center=(0, 0, 0), rot=(0, 0, 0), segs=24, rsegs=8, mi=0):
    M = _mat4(center, rot)
    rings = []
    for i in range(segs):
        a = 2 * math.pi * i / segs
        ring = []
        for j in range(rsegs):
            b = 2 * math.pi * j / rsegs
            p = Vector(((R + r * math.cos(b)) * math.cos(a), (R + r * math.cos(b)) * math.sin(a), r * math.sin(b)))
            ring.append(bm.verts.new(M @ p))
        rings.append(ring)
    for i in range(segs):
        for j in range(rsegs):
            f = bm.faces.new((rings[i][j], rings[(i + 1) % segs][j], rings[(i + 1) % segs][(j + 1) % rsegs],
                              rings[i][(j + 1) % rsegs]))
            f.material_index = mi


def add_lathe(bm, rings, segs=32, center=(0, 0, 0), mat_fn=None, caps=True):
    """rings: [(z, rx, ry, y_offset)], z increasing. mat_fn(z_mid) -> material index."""
    cx, cy, cz = center
    vr = []
    for (z, rx, ry, yo) in rings:
        vr.append([bm.verts.new((cx + rx * math.cos(2 * math.pi * k / segs),
                                 cy + yo + ry * math.sin(2 * math.pi * k / segs), cz + z)) for k in range(segs)])
    for i in range(len(vr) - 1):
        mi = mat_fn(0.5 * (rings[i][0] + rings[i + 1][0])) if mat_fn else 0
        for k in range(segs):
            f = bm.faces.new((vr[i][k], vr[i][(k + 1) % segs], vr[i + 1][(k + 1) % segs], vr[i + 1][k]))
            f.material_index = mi
    if caps:
        fb = bm.faces.new(list(reversed(vr[0])))
        fb.material_index = mat_fn(rings[0][0]) if mat_fn else 0
        ft = bm.faces.new(vr[-1])
        ft.material_index = mat_fn(rings[-1][0]) if mat_fn else 0


def limb_rings(length, r0, r1, n=6):
    """Rounded tapered limb along -Z from 0 (radius r0) to -length (radius r1), z increasing order."""
    rings = [(-length - 0.92 * r1, 0.3 * r1, 0.3 * r1, 0), (-length - 0.6 * r1, 0.8 * r1, 0.8 * r1, 0)]
    for i in range(n + 1):
        u = i / n
        z = -length + u * length
        r = lerp(r1, r0, u)
        rings.append((z, r, r, 0))
    rings += [(0.6 * r0, 0.8 * r0, 0.8 * r0, 0), (0.92 * r0, 0.3 * r0, 0.3 * r0, 0)]
    return rings


def bm_object(name, bm, coll, mats, loc=(0, 0, 0), rot=(0, 0, 0), parent=None, smooth=True):
    if smooth:
        for f in bm.faces:
            f.smooth = True
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    return link_obj(name, me, coll, loc, rot, parent)


def box_obj(name, coll, size, loc, mat, rot=(0, 0, 0), bevel=0.0, parent=None):
    bm = bmesh.new()
    add_box(bm, size)
    ob = bm_object(name, bm, coll, [mat], loc, rot, parent, smooth=bevel > 0)
    if bevel > 0:
        md = ob.modifiers.new("bevel", "BEVEL")
        md.width = bevel
        md.segments = 3
        md.limit_method = "NONE"
    return ob


def recenter(ob):
    """Geometry built in world coordinates: move the origin to its centre so the object is tidy to grab."""
    me = ob.data
    if ob.parent is not None or not len(me.vertices):
        return ob
    c = sum((v.co for v in me.vertices), Vector()) / len(me.vertices)
    me.transform(Matrix.Translation(-c))
    ob.location = Vector(ob.location) + c
    return ob


def bulbs_obj(name, coll, points, mat, r=0.011, parent=None, loc=(0, 0, 0)):
    bm = bmesh.new()
    for p in points:
        add_ico(bm, r, p, subdiv=1)
    ob = recenter(bm_object(name, bm, coll, [mat], loc, parent=parent))
    ob.visible_shadow = False
    return ob


def swag_points(x0, x1, z0, z1, sag, y, n, rnd=None, jitter=0.0):
    pts = []
    for i in range(n):
        u = (i + 0.5) / n
        x = lerp(x0, x1, u)
        z = lerp(z0, z1, u) - sag * 4 * u * (1 - u)
        if rnd and jitter:
            x += rnd.uniform(-jitter, jitter)
            z += rnd.uniform(-jitter, jitter)
        pts.append((x, y, z))
    return pts


def curtain_obj(name, coll, width, height, loc, mat, pleat=0.30, depth=0.10, segs_per_pleat=10, rot=(0, 0, 0),
                gather_bottom=0.0):
    bm = bmesh.new()
    nx = max(8, int(width / pleat * segs_per_pleat))
    nz = 8
    grid = []
    for i in range(nx + 1):
        x = -width / 2 + width * i / nx
        col = []
        for k in range(nz + 1):
            z = height * k / nz
            amp = depth * 0.5 * (1.0 + gather_bottom * (1 - k / nz))
            y = amp * math.sin(2 * math.pi * x / pleat) + 0.25 * amp * math.sin(2 * math.pi * x / (pleat * 2.7) + 1.3)
            col.append(bm.verts.new((x, y, z)))
        grid.append(col)
    for i in range(nx):
        for k in range(nz):
            bm.faces.new((grid[i][k], grid[i + 1][k], grid[i + 1][k + 1], grid[i][k + 1]))
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    return link_obj(name, me, coll, loc, rot)


# ---------------------------------------------------------------------------------------------------------------
# Lights
# ---------------------------------------------------------------------------------------------------------------

def aim_rot(loc, target):
    return (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


def add_light(name, coll, kind, loc, target=None, energy=100.0, color=KEY_RGB, size=1.0, size_y=None, spot_deg=40,
              blend=0.5, radius=0.05, cutoff=14.0, shadow=True, volume=1.0, spec=1.0, spread_deg=None):
    ld = bpy.data.lights.new(name, kind)
    if kind == "AREA" and spread_deg is not None:
        ld.spread = math.radians(spread_deg)   # a gridded softbox: keeps the key off the walls
    ld.energy = energy
    ld.color = color
    ld.use_shadow = shadow
    ld.use_custom_distance = True
    ld.cutoff_distance = cutoff
    ld.volume_factor = volume
    ld.specular_factor = spec
    if kind == "AREA":
        ld.shape = "RECTANGLE"
        ld.size = size
        ld.size_y = size_y if size_y is not None else size
    elif kind == "SPOT":
        ld.spot_size = math.radians(spot_deg)
        ld.spot_blend = blend
        ld.shadow_soft_size = radius
    else:
        ld.shadow_soft_size = radius
    ob = link_obj(name, ld, coll, loc, aim_rot(loc, target) if target is not None else (0, 0, 0))
    return ob


def add_noise(id_owner, path, index, strength, scale, phase, depth=0):
    ad = id_owner.animation_data
    cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    fc = cb.fcurves.find(path, index=index)
    md = fc.modifiers.new("NOISE")
    md.strength = strength
    md.scale = scale
    md.phase = phase
    md.depth = depth
    return md


def flicker_light(light_ob, f0, rnd, amount=0.35, scale=2.5):
    ld = light_ob.data
    ld.keyframe_insert("energy", frame=f0)
    add_noise(ld, "energy", 0, ld.energy * amount, scale, rnd.uniform(0, 100))


# ---------------------------------------------------------------------------------------------------------------
# Keyframe baking (Blender 5 slotted actions)
# ---------------------------------------------------------------------------------------------------------------

def bake(owner, path, frames, cols, interp="LINEAR"):
    owner.keyframe_insert(path, frame=frames[0])
    ad = owner.animation_data
    cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    for idx, vals in enumerate(cols):
        fc = cb.fcurves.find(path, index=idx)
        if fc is None:
            fc = cb.fcurves.new(path, index=idx)
        kps = fc.keyframe_points
        if len(kps) < len(frames):
            kps.add(len(frames) - len(kps))
        flat = []
        for f, v in zip(frames, vals):
            flat += (float(f), float(v))
        kps.foreach_set("co", flat)
        for k in kps:
            k.interpolation = interp
        fc.update()


def key_pair(owner, path, f0, v0, f1, v1, interp="SINE", easing="EASE_IN_OUT"):
    setattr(owner, path, v0)
    owner.keyframe_insert(path, frame=f0)
    setattr(owner, path, v1)
    owner.keyframe_insert(path, frame=f1)
    ad = owner.animation_data
    cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    for fc in cb.fcurves:
        if fc.data_path == path:
            for k in fc.keyframe_points:
                k.interpolation = interp
                k.easing = easing


# ---------------------------------------------------------------------------------------------------------------
# Mannequin
# ---------------------------------------------------------------------------------------------------------------

L1, L2, HAND = 0.28, 0.25, 0.17          # upper arm, forearm, hand
HIP_STAND, HIP_SEAT = 0.95, 0.56          # hip-joint height above the root
SHOULDER = {"R": Vector((-0.168, 0.0, 0.425)), "L": Vector((0.168, 0.0, 0.425))}  # in the hips frame
NECK_Z = 0.47


class Rig:
    pass


def build_mannequin(name, coll, root_loc, facing, outfit, skin, seated=False, darken=1.0, rnd=None):
    """Primitive stand-in. Faces -Y in its own frame. Character's right = -X."""
    r = Rig()
    r.name, r.seated, r.facing = name, seated, facing
    r.root_loc = Vector(root_loc)
    r.root = new_empty(f"{name}_root", coll, root_loc, (0, 0, facing), size=0.25, kind="CIRCLE")
    hip_z = HIP_SEAT if seated else HIP_STAND
    r.hip_z = hip_z
    top = fabric_material(f"{name}_top", outfit["top_hex"], outfit.get("fabric_top", "cotton"), darken)
    bottom = fabric_material(f"{name}_bottom", outfit["bottom_hex"], outfit.get("fabric_bottom", "cotton"), darken)
    hair, gold, bud = MATS["hair"], MATS["gold"], MATS["earbud"]
    over = 1.07 if outfit.get("oversized") else 1.0

    # legs (static, parented to root) ---------------------------------------------------------------------------
    skirt = outfit.get("bottom") == "skirt"
    leg_mat = skin if skirt else bottom
    wide = bool(outfit.get("wide")) and not skirt
    for side, sx in (("R", -1), ("L", 1)):
        if seated:
            hip = Vector((sx * 0.095, 0.0, hip_z))
            knee = Vector((sx * 0.105, -0.44, hip_z - 0.02))
            ankle = Vector((sx * 0.105, -0.47, 0.09))
        else:
            hip = Vector((sx * 0.088, 0.0, hip_z))
            knee = Vector((sx * 0.095, -0.012, 0.50))
            ankle = Vector((sx * 0.10, 0.0, 0.08))
        for seg, (a, b, ra, rb) in (("thigh", (hip, knee, 0.083, 0.056)), ("shin", (knee, ankle, 0.054, 0.036))):
            if wide:
                ra, rb = (0.10, 0.095) if seg == "thigh" else (0.095, 0.11)
            bm = bmesh.new()
            ln = (b - a).length
            add_lathe(bm, limb_rings(ln, ra, rb), segs=16)
            rot = (b - a).to_track_quat("-Z", "Y").to_euler()
            mat = leg_mat
            if skirt and seg == "shin":
                mat = skin
            bm_object(f"{name}_{seg}_{side}", bm, coll, [mat], a, rot, parent=r.root)
        bm = bmesh.new()
        add_box(bm, (0.08, 0.23, 0.06), (0, -0.07, 0.0))
        foot = bm_object(f"{name}_foot_{side}", bm, coll, [MATS["black"]], (ankle.x, ankle.y, 0.03), parent=r.root,
                         smooth=False)
        if seated:
            foot.location = (ankle.x, ankle.y - 0.02, 0.03)

    # hips pivot: sway --------------------------------------------------------------------------------------------
    r.hips = new_empty(f"{name}_hips", coll, (0, 0, hip_z), parent=r.root, size=0.15)
    bm = bmesh.new()
    add_lathe(bm, [(-0.13, 0.15, 0.105, 0.0), (-0.06, 0.168, 0.115, 0.0), (0.02, 0.158, 0.108, 0.0)], segs=28)
    bm_object(f"{name}_pelvis", bm, coll, [bottom], parent=r.hips)
    if skirt:
        drop = -(hip_z - (0.36 if not seated else 0.40))
        rings = [(drop, 0.26, 0.21, -0.01), (drop * 0.55, 0.215, 0.165, -0.01), (-0.09, 0.172, 0.122, 0.0),
                 (0.0, 0.162, 0.112, 0.0)]
        if seated:
            rings = [(-0.12, 0.2, 0.3, -0.18), (-0.06, 0.18, 0.18, -0.06), (0.0, 0.162, 0.112, 0.0)]
        bm = bmesh.new()
        add_lathe(bm, rings, segs=32)
        bm_object(f"{name}_skirt", bm, coll, [bottom], parent=r.hips)

    # torso (lathe; material split at the neckline) -----------------------------------------------------------
    neck_line = {"cami": 0.31, "scoop": 0.36, "crew": 0.465}.get(outfit.get("neckline", "crew"), 0.465)
    prof = [(-0.10, 0.160, 0.112, 0.0), (0.00, 0.155, 0.108, 0.0), (0.08, 0.132, 0.093, 0.0),
            (0.17, 0.142, 0.100, -0.006), (0.26, 0.158, 0.114, -0.018), (0.31, 0.162, 0.108, -0.016),
            (0.36, 0.166, 0.100, -0.010), (0.415, 0.168, 0.086, 0.0), (0.45, 0.115, 0.072, 0.0),
            (0.48, 0.058, 0.050, 0.004)]
    crop = (0.035, 0.150) if outfit.get("cropped") else None   # bare midriff band (hips frame)
    for cut in [neck_line] + (list(crop) if crop else []):
        if any(abs(p[0] - cut) < 1e-3 for p in prof):
            continue
        for i in range(len(prof) - 1):   # an exact ring at each material edge
            if prof[i][0] < cut < prof[i + 1][0]:
                u = (cut - prof[i][0]) / (prof[i + 1][0] - prof[i][0])
                prof.insert(i + 1, tuple(lerp(prof[i][j], prof[i + 1][j], u) for j in range(4)))
                break
    prof = [(z, rx * over, ry * over, yo) for (z, rx, ry, yo) in prof]

    def torso_mat(z):
        if z >= neck_line or (crop and crop[0] < z < crop[1]):
            return 0                      # skin
        if crop and z <= crop[0]:
            return 2                      # high-waisted bottom under a cropped top
        return 1                          # top
    bm = bmesh.new()
    add_lathe(bm, prof, segs=36, mat_fn=torso_mat)
    r.torso = bm_object(f"{name}_torso", bm, coll, [skin, top, bottom], parent=r.hips)
    if outfit.get("neckline") == "cami":  # straps
        bm = bmesh.new()
        for sx in (-1, 1):
            add_box(bm, (0.012, 0.20, 0.006), (sx * 0.095, 0.0, 0.425), (0.0, sx * 0.25, 0.0))
        bm_object(f"{name}_straps", bm, coll, [top], parent=r.hips, smooth=False)

    # neck + head (neck pivot: nod) ---------------------------------------------------------------------------
    r.neck = new_empty(f"{name}_neck", coll, (0, 0.004, NECK_Z), parent=r.hips, size=0.1)
    bm = bmesh.new()
    add_lathe(bm, [(-0.02, 0.05, 0.047, 0), (0.05, 0.045, 0.043, 0), (0.10, 0.044, 0.042, 0)], segs=20)
    bm_object(f"{name}_neckmesh", bm, coll, [skin], parent=r.neck)
    hc = Vector((0, -0.012, 0.165))  # head centre relative to the neck pivot
    bm = bmesh.new()
    add_sphere(bm, (0.076, 0.092, 0.110), hc, u=28, v=18)
    add_sphere(bm, (0.058, 0.062, 0.058), hc + Vector((0, -0.03, -0.058)), u=20, v=12)          # jaw / chin
    add_sphere(bm, (0.011, 0.016, 0.023), hc + Vector((0, -0.094, -0.008)), (-0.25, 0, 0), u=10, v=8)  # nose
    for sx in (-1, 1):
        add_sphere(bm, (0.011, 0.02, 0.028), hc + Vector((sx * 0.076, 0.004, -0.006)), u=10, v=8)  # ears
    r.head = bm_object(f"{name}_head", bm, coll, [skin], parent=r.neck)
    # hair: cap with a hairline cut, plus a low bun ("pulled back")
    bm = bmesh.new()
    res = bmesh.ops.create_uvsphere(bm, u_segments=28, v_segments=18, radius=1.0,
                                    matrix=_mat4(hc + Vector((0, 0.006, 0.006)), (0, 0, 0), (0.081, 0.098, 0.116)))

    def keep_hair(v):
        y, z = v.co.y - hc.y, v.co.z - hc.z
        if y > 0.03:                       # back of the head: down to the nape
            return z > -0.07
        return z > 0.006 - 0.55 * y        # hairline high on the forehead, behind the ears at the sides
    bmesh.ops.delete(bm, geom=[v for v in res["verts"] if not keep_hair(v)], context="VERTS")
    add_sphere(bm, (0.05, 0.048, 0.046), hc + Vector((0, 0.098, -0.03)), u=16, v=10)
    bm_object(f"{name}_hair", bm, coll, [hair], parent=r.neck)
    if outfit.get("headwrap"):   # a wrap over the hair in the top's fabric, with a knot at the front-left
        bm = bmesh.new()
        res = bmesh.ops.create_uvsphere(bm, u_segments=28, v_segments=18, radius=1.0,
                                        matrix=_mat4(hc + Vector((0, 0.006, 0.012)), (0, 0, 0), (0.090, 0.106, 0.124)))
        bmesh.ops.delete(bm, geom=[v for v in res["verts"] if not keep_hair(v)], context="VERTS")
        add_sphere(bm, (0.045, 0.04, 0.036), hc + Vector((0.055, -0.045, 0.105)), (0.3, 0.5, 0.0), u=14, v=10)
        bm_object(f"{name}_headwrap", bm, coll, [top], parent=r.neck)
    bm = bmesh.new()
    for sx in (-1, 1):
        add_torus(bm, 0.011, 0.0022, hc + Vector((sx * 0.079, -0.002, -0.046)), (0, math.pi / 2, 0), segs=16,
                  rsegs=6)
    bm_object(f"{name}_hoops", bm, coll, [gold], parent=r.neck)
    bm = bmesh.new()
    for sx in (-1, 1):
        add_sphere(bm, (0.008, 0.008, 0.008), hc + Vector((sx * 0.083, -0.012, -0.012)), u=8, v=6)
    bm_object(f"{name}_earbuds", bm, coll, [bud], parent=r.neck)
    r.eyes = new_empty(f"{name}_eyes", coll, hc + Vector((0, -0.085, 0.012)), parent=r.neck, size=0.03)

    # arms ----------------------------------------------------------------------------------------------------
    r.shoulder, r.elbow, r.wrist = {}, {}, {}
    sleeve_long = outfit.get("sleeves") == "long"
    for side in ("R", "L"):
        sh = new_empty(f"{name}_shoulder_{side}", coll, SHOULDER[side], parent=r.hips, size=0.06)
        bm = bmesh.new()
        add_sphere(bm, (0.046 * over, 0.044 * over, 0.045 * over), (0, 0, -0.005), u=16, v=10)
        bm_object(f"{name}_deltoid_{side}", bm, coll, [top if sleeve_long else skin], parent=sh)
        bm = bmesh.new()
        add_lathe(bm, limb_rings(L1, 0.045 * over, 0.037 * over), segs=16)
        bm_object(f"{name}_upperarm_{side}", bm, coll, [top if sleeve_long else skin], parent=sh)
        el = new_empty(f"{name}_elbow_{side}", coll, (0, 0, -L1), parent=sh, size=0.05)
        bm = bmesh.new()
        add_lathe(bm, limb_rings(L2, 0.037 * over, 0.027 * over), segs=16)
        bm_object(f"{name}_forearm_{side}", bm, coll, [top if sleeve_long else skin], parent=el)
        wr = new_empty(f"{name}_wrist_{side}", coll, (0, 0, -L2), parent=el, size=0.04)
        bm = bmesh.new()
        add_sphere(bm, (0.041, 0.017, 0.084), (0, 0, -0.078), u=16, v=10)
        bm_object(f"{name}_hand_{side}", bm, coll, [skin], parent=wr)
        r.shoulder[side], r.elbow[side], r.wrist[side] = sh, el, wr
    return r


# IK ------------------------------------------------------------------------------------------------------------

def solve_arm(side, target, pole, palm, prev=None):
    """2-bone IK in the hips frame. Returns (shoulder Euler, elbow bend, wrist roll)."""
    S = SHOULDER[side]
    dv = Vector(target) - S
    d = min(max(dv.length, abs(L1 - L2) + 0.03), L1 + L2 - 0.004)
    dn = dv.normalized()
    pole = Vector(pole)
    pp = pole - dn * pole.dot(dn)
    if pp.length < 1e-5:
        pp = Vector((0, 0, -1)) - dn * (-dn.z)
    pp.normalize()
    ca = (L1 * L1 + d * d - L2 * L2) / (2 * L1 * d)
    a = math.acos(min(1.0, max(-1.0, ca)))
    E = S + L1 * (math.cos(a) * dn + math.sin(a) * pp)
    T = S + dn * d
    u = (E - S).normalized()
    w = (T - E).normalized()
    h = u.cross(w)
    if h.length < 1e-5:
        h = u.cross(pp)
    h.normalize()
    y = (-u).cross(h)
    Rs = Matrix((h, y, -u)).transposed()
    bend = u.angle(w)
    Re = Rs @ Matrix.Rotation(bend, 3, "X")
    f = Re @ Vector((0, 0, -1))
    n0 = Re @ Vector((0, 1, 0))
    x0 = Re @ Vector((1, 0, 0))
    pv = Vector(palm)
    dp = pv - f * pv.dot(f)
    roll = math.atan2(-x0.dot(dp), n0.dot(dp)) if dp.length > 1e-6 else 0.0
    eul = Rs.to_euler("XYZ", prev[0]) if prev else Rs.to_euler("XYZ")
    if prev:
        while roll - prev[2] > math.pi:
            roll -= 2 * math.pi
        while roll - prev[2] < -math.pi:
            roll += 2 * math.pi
    return eul, bend, roll


def mirror(v):
    return (-v[0], v[1], v[2])


REST = {
    "stand": dict(tgt=(-0.215, -0.075, -0.035), pole=(-0.25, 1.0, -0.1), palm=(1.0, 0.0, 0.0)),
    "seat": dict(tgt=(-0.12, -0.255, 0.085), pole=(-0.7, 1.0, -0.3), palm=(0.0, 0.0, -1.0)),
}

# Gesture library: right-arm targets (wrist position, hips frame), elbow pole, palm direction.
GESTURES = {
    "heart_press": dict(ain=0.45, aout=0.55, tgt=(-0.10, -0.165, 0.265), pole=(-1.0, 0.0, -1.0), palm=(0.2, 1.0, 0.1)),
    "pull_back": dict(ain=0.40, aout=0.55, tgt=(-0.11, -0.19, 0.25), pole=(-1.0, 0.1, -1.0), palm=(0.2, 1.0, 0.3)),
    "reach_to_lens": dict(ain=0.65, aout=0.55, tgt="camera", reach=0.94, pole=(-0.5, 0.25, -1.0), palm=(0, 0, 1)),
    "come_here": dict(ain=0.45, aout=0.6, tgt=(-0.15, -0.37, 0.245), pole=(-1.0, 0.3, -1.0), palm=(0, 0, 1),
                      curl=True),
    "jaw_trace": dict(ain=0.5, aout=0.5, path=((-0.135, -0.07, 0.455), (-0.07, -0.11, 0.405)),
                      pole=(-0.8, -0.1, -1.0), palm=(1.0, -0.3, 0.0)),
    "backbeat_snap": dict(ain=0.35, aout=0.45, tgt=(-0.31, -0.15, 0.30), pole=(-0.5, 0.3, -1.0),
                          palm=(0.6, -0.6, 0.2), snap=True),
    "loop_circle": dict(ain=0.4, aout=0.5, circle=((-0.12, -0.33, 0.21), 0.075), pole=(-1.0, 0.1, -1.0),
                        palm=(0.0, -1.0, 0.3)),
    "palm_roll": dict(ain=0.4, aout=0.5, tgt=(-0.17, -0.34, 0.20), pole=(-1.0, 0.2, -1.0), palm_from=(0, 0, 1),
                      palm_to=(0, 0, -1)),
    "hair_comb": dict(ain=0.45, aout=0.5, path=((-0.15, -0.06, 0.53), (-0.13, 0.05, 0.58)), pole=(-1.0, -0.3, 0.0),
                      palm=(1.0, 0.0, 0.0)),
    "frame_face": dict(ain=0.45, aout=0.5, tgt=(-0.13, -0.12, 0.45), pole=(-1.0, 0.1, -1.0), palm=(1.0, 0.0, 0.0),
                       both=True),
    "adlib_wave": dict(ain=0.6, aout=0.6, wave=((-0.34, -0.10, 0.86), (-0.08, -0.12, 0.93)), pole=(-1.0, 0.3, 0.2),
                       palm=(0.3, -1.0, 0.0)),
    "hush": dict(ain=0.4, aout=0.5, tgt=(-0.04, -0.17, 0.40), pole=(-1.0, -0.2, -1.0), palm=(1.0, 0.0, 0.0)),
    "vocal_lift": dict(ain=0.4, aout=0.5, path=((-0.20, -0.30, 0.10), (-0.24, -0.34, 0.40)), pole=(-1.0, 0.2, -1.0),
                       palm=(0, 0, 1)),
    "shoulder_brush": dict(ain=0.35, aout=0.45, path=((0.06, -0.12, 0.33), (0.17, -0.15, 0.27)),
                           pole=(-0.5, -0.2, -1.0), palm=(0, 1, 0)),
    "lean_back": dict(ain=0.4, aout=0.4, tgt=(-0.17, -0.17, 0.10), pole=(-1.0, 0.5, -0.5), palm=(0, 0, -1),
                      both=True),
}
GESTURE_LABEL = {
    "heart_press": "Heart Press", "pull_back": "Pull Back", "reach_to_lens": "Reach to Lens", "come_here": "Come Here",
    "jaw_trace": "Jaw Trace", "backbeat_snap": "Backbeat Snaps", "loop_circle": "Loop circle",
    "palm_roll": "Palm Roll", "hair_comb": "Hair Comb", "frame_face": "Frame the Face", "adlib_wave": "Ad-lib Wave",
    "hush": "Hush", "vocal_lift": "Vocal Lift", "shoulder_brush": "Shoulder Brush", "lean_back": "Lean-Back Hands",
    "shoulder_roll": "Shoulder Roll", "body_roll": "Body Roll", "hair_flip": "Hair Flip", "sway": "Sway",
    "ooh_face": "Ooh face",
}


# Body moves are torso / head channels (no arm IK); everything else is an arm gesture from GESTURES.
BODY_MOVES = {"shoulder_roll", "body_roll", "hair_flip", "sway", "ooh_face"}
MOVE_TABLE = [  # first match wins; multi-word keys before their single-word parts
    ("shoulder roll", "shoulder_roll"), ("body roll", "body_roll"), ("hair flip", "hair_flip"), ("ooh", "ooh_face"),
    ("sway", "sway"),
    ("heart", "heart_press"), ("reach", "reach_to_lens"), ("pull back", "pull_back"), ("pull", "pull_back"),
    ("come here", "come_here"), ("come", "come_here"), ("beckon", "come_here"), ("jaw", "jaw_trace"),
    ("snap", "backbeat_snap"), ("palm roll", "palm_roll"), ("roll", "palm_roll"), ("circle", "loop_circle"),
    ("loop", "loop_circle"), ("hair", "hair_comb"), ("comb", "hair_comb"), ("frame", "frame_face"),
    ("wave", "adlib_wave"), ("ad lib", "adlib_wave"), ("adlib", "adlib_wave"), ("hush", "hush"), ("mic", "hush"),
    ("lift", "vocal_lift"), ("shoulder", "shoulder_brush"), ("lean", "lean_back"), ("point", "reach_to_lens"),
]


def classify_move(*names):
    """Map a cue to a previs move. The move id is checked first, then the label, then the free text, so a word
    in the description ("eyes lift") can't override the id ("shoulder-roll")."""
    for n in names:
        if not n:
            continue
        t = str(n).lower().replace("_", " ").replace("-", " ")
        for k, g in MOVE_TABLE:
            if k in t:
                return g
    return None


def side_from_text(text):
    t = (text or "").lower()
    both = bool(re.search(r"\bboth\b", t))
    if re.search(r"\bleft (hand|palm|arm|shoulder)\b", t):
        return "L", both
    if re.search(r"\bright (hand|palm|arm|shoulder)\b", t):
        return "R", both
    return None, both


class Performer:
    """Pose as a pure function of song time; baked to keyframes."""

    def __init__(self, rig, scene_ctx, kind="lead", mirror_arm=False, delay=0.0, sway_amp=2.2, nod_amp=5.0):
        self.rig, self.ctx, self.kind = rig, scene_ctx, kind
        self.mirror_arm = mirror_arm
        self.delay = delay
        self.sway_amp, self.nod_amp = sway_amp, nod_amp
        self.gestures = []   # (t0, t1, name, side)
        root = Matrix.Translation(rig.root_loc) @ Matrix.Rotation(rig.facing, 4, "Z")
        self.hips_world = root @ Matrix.Translation((0, 0, rig.hip_z))
        self.hips_inv = self.hips_world.inverted()

    def add_gesture(self, t0, dur, name, side=None, both=False):
        if side is None:
            if name in ("reach_to_lens", "come_here"):
                # reach with the arm on the camera's side, so the move reads instead of crossing the body
                side = "L" if self.cam_local(t0 + min(dur, 0.6)).x > 0.4 else "R"
            else:
                side = "L" if self.mirror_arm else "R"
        self.gestures.append((t0, t0 + dur, name, side, both))

    def body_moves(self, t):
        """Active torso/head moves: [(name, side, u, weight)], u = progress 0..1."""
        out = []
        for (g0, g1, name, side, both) in self.gestures:
            if name not in BODY_MOVES or t < g0 - 0.3 or t > g1 + 0.3:
                continue
            w = min(smooth01((t - g0 + 0.3) / 0.3), 1.0 - smooth01((t - g1) / 0.3))
            if w > 0:
                out.append((name, side, min(1.0, max(0.0, (t - g0) / max(0.01, g1 - g0))), w))
        return out

    # -- channels ---------------------------------------------------------------------------------------------
    def cam_local(self, t):
        return self.hips_inv @ self.ctx.cam_pos(t)

    def gaze(self, t):
        """(yaw, pitch) of the camera from the head, in the hips frame (radians)."""
        c = self.cam_local(t)
        head = Vector((0, -0.01, NECK_Z + 0.165))
        d = c - head
        yaw = math.atan2(d.x, -d.y)
        pitch = math.atan2(d.z, math.hypot(d.x, d.y))
        return yaw, pitch

    def hips_rot(self, t):
        b = self.ctx.beat_index(t - self.delay)
        roll = math.radians(self.sway_amp) * math.cos(math.pi * b)
        twist = math.radians(self.sway_amp * 0.8) * math.cos(math.pi * b + 0.7)
        if self.kind == "lead_track":  # S4: the lead turns with the orbit to hold the gaze
            yaw, _ = self.gaze(t)
            twist += 0.38 * max(-0.9, min(0.9, yaw))
        pitch = 0.0
        for name, side, u, w in self.body_moves(t):
            sgn = 1.0 if side == "L" else -1.0
            if name == "shoulder_roll":      # one shoulder up-back-down: a torso roll with a little twist
                roll += w * math.radians(5.0) * sgn * math.sin(2 * math.pi * u)
                twist += w * math.radians(4.0) * sgn * (1 - math.cos(2 * math.pi * u)) / 2
            elif name == "body_roll":        # chest leads forward, then settles back
                pitch += w * math.radians(6.0) * math.sin(2 * math.pi * u)
            elif name == "hair_flip":
                twist += w * math.radians(6.0) * sgn * math.sin(math.pi * u)
            elif name == "sway":
                roll *= 1 + 0.6 * w
        return (pitch, roll, twist)

    def neck_rot(self, t):
        nod = 0.0
        for db in self.ctx.downbeats:
            x = t - self.delay - db
            if -0.3 < x < 0.7:
                nod += math.exp(-((x - 0.07) / 0.15) ** 2)
        pitch_down = math.radians(self.nod_amp) * nod
        yaw_c, pitch_c = self.gaze(t)
        look = self.ctx.look_weight(t)            # 0 = eyes down (base), 1 = to the lens
        base_down = math.radians(self.ctx.eyes_down_deg)
        if self.kind == "backup":
            yaw = 0.25 * yaw_c
            pitch = -0.5 * pitch_c + math.radians(2.0)
        else:
            k_yaw = 0.62 if self.kind == "lead_track" else 0.45
            yaw = k_yaw * max(-1.2, min(1.2, yaw_c)) * look
            pitch = lerp(base_down, -0.85 * pitch_c, look)
        roll = 0.0
        for name, side, u, w in self.body_moves(t):
            sgn = 1.0 if side == "L" else -1.0
            if name == "hair_flip":          # head swings away and back, chin up at the top
                yaw += w * math.radians(38.0) * sgn * math.sin(math.pi * u)
                pitch -= w * math.radians(8.0) * math.sin(math.pi * u)
            elif name == "ooh_face":         # no face on the stand-in: a soft head tilt stands in for it
                roll += w * math.radians(7.0) * sgn * math.sin(math.pi * u)
            elif name == "shoulder_roll":
                roll += w * math.radians(4.0) * sgn * math.sin(2 * math.pi * u)
        return (pitch + pitch_down, roll, yaw)

    def arm_state(self, side, t):
        rest = REST["seat" if self.rig.seated else "stand"]
        rt, rp, rpalm = Vector(rest["tgt"]), Vector(rest["pole"]), Vector(rest["palm"])
        if side == "L":
            rt, rp, rpalm = Vector(mirror(rt)), Vector(mirror(rp)), Vector(mirror(rpalm))
        tgt, pole, palm, curl, wsum = rt.copy(), rp.copy(), rpalm.copy(), 0.0, 0.0
        for (g0, g1, name, gside, gboth) in self.gestures:
            g = GESTURES.get(name)
            if g is None or not (gboth or g.get("both") or gside == side):
                continue
            if t < g0 - 0.05 or t > g1 + g["aout"] + 0.05:
                continue
            w_in = smooth01((t - g0) / g["ain"])
            w_out = 1.0 - smooth01((t - g1) / g["aout"])
            w = max(0.0, min(w_in, w_out))
            if w <= 0:
                continue
            u = min(1.0, max(0.0, (t - g0) / max(0.01, g1 - g0)))
            gt, gp, gpalm, gcurl = self.gesture_pose(g, name, side, t, u)
            # arc forward while travelling so the hand never cuts through the body
            bump = 0.07 * math.sin(math.pi * w) if w < 0.999 else 0.0
            gt = gt + Vector((0, -bump, 0))
            tgt = tgt.lerp(gt, w)
            pole = pole.lerp(gp, w)
            palm = palm.lerp(gpalm, w)
            curl = lerp(curl, gcurl, w)
            wsum = max(wsum, w)
        return tgt, pole, palm, curl

    def gesture_pose(self, g, name, side, t, u):
        sgn = -1 if side == "L" else 1   # mirror helper for R-defined data
        def m(v):
            v = Vector(v)
            return Vector((v.x * sgn, v.y, v.z))
        pole = m(g["pole"])
        palm = m(g.get("palm", (0, 0, 1)))
        curl = 0.0
        if g.get("tgt") == "camera":
            c = self.cam_local(t)
            S = SHOULDER[side]
            dirn = (c - S).normalized()
            tgt = S + dirn * (g["reach"] * (L1 + L2))
        elif "circle" in g:
            (cx, cy, cz), rad = g["circle"]
            ph = 2 * math.pi * self.ctx.beat_index(t) / 2.0
            tgt = m((cx + rad * math.cos(ph), cy, cz + rad * math.sin(ph)))
        elif "wave" in g:
            a, b = g["wave"]
            s = 0.5 + 0.5 * math.sin(math.pi * self.ctx.beat_index(t) / 2.0)
            tgt = m(vlerp(a, b, s))
        elif "path" in g:
            a, b = g["path"]
            tgt = m(vlerp(a, b, ease(u)))
        else:
            tgt = m(g["tgt"])
        if "palm_from" in g:
            palm = m(vlerp(g["palm_from"], g["palm_to"], ease(u)))
            if palm.length < 1e-3:
                palm = m((0, -1, 0))
        if g.get("curl"):
            # beckon: a finger-curl (wrist flex) landing on every beat after the hand arrives
            pulse = 0.0
            start = self.gesture_start(name)
            for bt in self.ctx.beats:
                if bt >= start + 0.5:
                    pulse = max(pulse, math.exp(-((t - bt + 0.05) / 0.16) ** 2))
            curl = 0.35 + 0.55 * pulse
        if g.get("snap"):
            pulse = 0.0
            for i, bt in enumerate(self.ctx.beats):
                if self.ctx.is_backbeat(i):
                    x = t - bt
                    pulse = max(pulse, math.exp(-((x + 0.02) / 0.09) ** 2))
            curl = 0.45 * pulse
        return tgt, pole, palm, curl

    def gesture_start(self, name):
        for (g0, g1, n, s, b) in self.gestures:
            if n == name:
                return g0
        return 0.0

    # -- bake -------------------------------------------------------------------------------------------------
    def bake(self, f0, f1):
        frames = list(range(f0, f1 + 1))
        hips = [[], [], []]
        neck = [[], [], []]
        arms = {s: dict(sh=[[], [], []], el=[[], [], []], wr=[[], [], []]) for s in ("R", "L")}
        prev = {"R": None, "L": None}
        for f in frames:
            t = f / FPS
            for i, v in enumerate(self.hips_rot(t)):
                hips[i].append(v)
            for i, v in enumerate(self.neck_rot(t)):
                neck[i].append(v)
            for s in ("R", "L"):
                tgt, pole, palm, curl = self.arm_state(s, t)
                eul, bend, roll = solve_arm(s, tgt, pole, palm, prev[s])
                prev[s] = (eul, bend, roll)
                for i in range(3):
                    arms[s]["sh"][i].append(eul[i])
                arms[s]["el"][0].append(bend)
                arms[s]["el"][1].append(0.0)
                arms[s]["el"][2].append(0.0)
                arms[s]["wr"][0].append(curl)
                arms[s]["wr"][1].append(0.0)
                arms[s]["wr"][2].append(roll)
        r = self.rig
        bake(r.hips, "rotation_euler", frames, hips)
        bake(r.neck, "rotation_euler", frames, neck)
        for s in ("R", "L"):
            bake(r.shoulder[s], "rotation_euler", frames, arms[s]["sh"])
            bake(r.elbow[s], "rotation_euler", frames, arms[s]["el"])
            bake(r.wrist[s], "rotation_euler", frames, arms[s]["wr"])


# ---------------------------------------------------------------------------------------------------------------
# Sets
# ---------------------------------------------------------------------------------------------------------------

def candle(coll, key, idx, loc, h, r, rnd, f0, energy=1.6):
    bm = bmesh.new()
    add_cyl(bm, r, r * 0.97, h, (0, 0, h / 2), segs=20)
    bm_object(f"{key}_candle_{idx}", bm, coll, [MATS["wax"]], loc)
    bm = bmesh.new()
    add_sphere(bm, (0.0065, 0.0065, 0.017), (0, 0, 0), u=10, v=8)
    fl = bm_object(f"{key}_flame_{idx}", bm, coll, [MATS["flame"]], (loc[0], loc[1], loc[2] + h + 0.022))
    fl.visible_shadow = False
    lo = add_light(f"{key}_candle_light_{idx}", coll, "POINT", (loc[0], loc[1], loc[2] + h + 0.045), energy=energy,
                   color=FLAME_RGB, radius=0.012, cutoff=3.5, shadow=True, volume=0.6)
    flicker_light(lo, f0, rnd)
    fl.keyframe_insert("scale", index=2, frame=f0)
    add_noise(fl, "scale", 2, 0.35, 2.0, rnd.uniform(0, 100))
    return lo


def build_couch(coll, key, O):
    S = MATS["suede"]
    cx, cy = O.x, O.y + 1.78
    box_obj(f"{key}_couch_base", coll, (2.10, 0.92, 0.26), (cx, cy, 0.20), S, bevel=0.03)
    for i, x in enumerate((-0.5, 0.5)):
        box_obj(f"{key}_couch_seat_{i}", coll, (0.99, 0.74, 0.15), (cx + x, cy - 0.07, 0.405), S, bevel=0.05)
        box_obj(f"{key}_couch_back_{i}", coll, (0.98, 0.24, 0.46), (cx + x, cy + 0.30, 0.68), S,
                rot=(math.radians(-9), 0, 0), bevel=0.07)
    box_obj(f"{key}_couch_backframe", coll, (2.10, 0.18, 0.62), (cx, cy + 0.40, 0.55), S, bevel=0.04)
    for i, x in enumerate((-1.0, 1.0)):
        box_obj(f"{key}_couch_arm_{i}", coll, (0.22, 0.92, 0.60), (cx + x, cy, 0.37), S, bevel=0.07)
    for i, (x, rz, mat) in enumerate(((-0.70, 0.25, "pillow_orange"), (-0.36, -0.1, "pillow_cream"),
                                      (0.78, -0.3, "pillow_orange"))):
        box_obj(f"{key}_pillow_{i}", coll, (0.42, 0.13, 0.40), (cx + x, cy + 0.12, 0.66), MATS[mat],
                rot=(math.radians(-14), 0, rz), bevel=0.06)
    bm = bmesh.new()
    for x in (-0.95, 0.95):
        for y in (-0.38, 0.38):
            add_cyl(bm, 0.025, 0.02, 0.07, (x, y, 0.035), segs=10)
    bm_object(f"{key}_couch_legs", bm, coll, [MATS["wood"]], (cx, cy, 0))


def build_living_room(key, O, variant, coll, f0, f1, rnd):
    O = Vector(O)
    M = MATS
    box_obj(f"{key}_floor", coll, (9.0, 9.0, 0.04), O + Vector((0, 0.3, -0.02)), M["floor"])
    box_obj(f"{key}_wall_back", coll, (9.0, 0.1, 3.4), O + Vector((0, 2.50, 1.7)), M["wall"])
    box_obj(f"{key}_wall_left", coll, (0.1, 7.0, 3.4), O + Vector((-3.4, -1.0, 1.7)), M["wall"])
    box_obj(f"{key}_wall_right", coll, (0.1, 7.0, 3.4), O + Vector((3.4, -1.0, 1.7)), M["wall"])
    box_obj(f"{key}_ceiling", coll, (9.0, 7.0, 0.1), O + Vector((0, -1.0, 3.0)), M["wall"])
    box_obj(f"{key}_baseboard", coll, (9.0, 0.03, 0.1), O + Vector((0, 2.44, 0.05)), M["wood"])
    box_obj(f"{key}_rug", coll, (2.9, 1.9, 0.012), O + Vector((0.05, 0.85, 0.006)), M["rug"])
    build_couch(coll, key, O)

    # coffee table + candles (+ mug in S1)
    tx, ty = O.x + 0.05, O.y + 0.70
    box_obj(f"{key}_coffee_top", coll, (1.15, 0.60, 0.05), (tx, ty, 0.405), M["wood"], bevel=0.01)
    bm = bmesh.new()
    for x in (-0.52, 0.52):
        for y in (-0.25, 0.25):
            add_box(bm, (0.05, 0.05, 0.38), (x, y, 0.19))
    bm_object(f"{key}_coffee_legs", bm, coll, [M["wood"]], (tx, ty, 0), smooth=False)
    lights = []
    for i, (dx, dy, h, r) in enumerate(((-0.40, 0.05, 0.20, 0.040), (-0.28, -0.08, 0.13, 0.036),
                                        (-0.47, -0.12, 0.09, 0.034))):
        lights.append(candle(coll, key, i, (tx + dx, ty + dy, 0.43), h, r, rnd, f0))
    # steaming mug (front right of the coffee table)
    mx, my, mz = tx + 0.14, ty - 0.12, 0.43
    bm = bmesh.new()
    add_cyl(bm, 0.043, 0.040, 0.095, (0, 0, 0.0475), segs=24)
    for f in bm.faces:
        if f.normal.z > 0.9 and f.calc_center_median().z > 0.09:
            f.material_index = 1
    add_torus(bm, 0.026, 0.0075, (0.052, 0, 0.05), (math.pi / 2, 0, 0), segs=16, rsegs=6)
    mug = bm_object(f"{key}_mug", bm, coll, [M["ceramic"], M["tea"]], (mx, my, mz))
    for k in range(3):
        bm = bmesh.new()
        n = 14
        verts = []
        for i in range(n + 1):
            z = 0.24 * i / n
            off = 0.012 * math.sin(z * 26 + k * 2.1)
            verts.append((bm.verts.new((off - 0.014, 0, z)), bm.verts.new((off + 0.014, 0, z))))
        for i in range(n):
            bm.faces.new((verts[i][0], verts[i][1], verts[i + 1][1], verts[i + 1][0]))
        st = bm_object(f"{key}_steam_{k}", bm, coll, [M["steam"]], (0, 0, 0.09), (0, 0, k * 2.09), parent=mug)
        st.visible_shadow = False
        st.keyframe_insert("rotation_euler", index=2, frame=f0)
        st.rotation_euler[2] += 1.2
        st.keyframe_insert("rotation_euler", index=2, frame=f1)
    nz = M["steam"].node_tree.nodes["SteamNoise"]
    w = nz.inputs["W"]
    w.default_value = 0.0
    w.keyframe_insert("default_value", frame=0)
    w.default_value = 6.0
    w.keyframe_insert("default_value", frame=800)

    # console table behind the couch + table lamp (camera-left practical)
    lx, ly = O.x - 0.80, O.y + 2.33
    box_obj(f"{key}_console", coll, (1.3, 0.22, 0.04), (lx + 0.25, ly, 0.82), M["wood"])
    bm = bmesh.new()
    for x in (-0.6, 0.6):
        for y in (-0.08, 0.08):
            add_box(bm, (0.035, 0.035, 0.80), (x, y, 0.40))
    bm_object(f"{key}_console_legs", bm, coll, [M["wood"]], (lx + 0.25, ly, 0), smooth=False)
    bm = bmesh.new()
    add_sphere(bm, (0.085, 0.085, 0.11), (0, 0, 0.95), u=20, v=12)
    add_cyl(bm, 0.012, 0.012, 0.22, (0, 0, 1.12), segs=10)
    bm_object(f"{key}_lamp_base", bm, coll, [M["ceramic"]], (lx, ly, 0))
    bm = bmesh.new()
    add_cyl(bm, 0.21, 0.14, 0.26, (0, 0, 0), segs=32, caps=False)
    shade = bm_object(f"{key}_lamp_shade", bm, coll, [M["shade"]], (lx, ly, 1.30))
    shade.visible_shadow = False
    lights.append(add_light(f"{key}_lamp_light", coll, "POINT", (lx, ly, 1.28), energy=55, color=(1.0, 0.64, 0.34),
                            radius=0.10, cutoff=9.0, volume=0.4))
    # a dim floor lamp, camera-right back corner
    fx, fy = O.x + 2.05, O.y + 2.05
    bm = bmesh.new()
    add_cyl(bm, 0.15, 0.15, 0.03, (0, 0, 0.015), segs=20)
    add_cyl(bm, 0.012, 0.012, 1.45, (0, 0, 0.75), segs=10)
    bm_object(f"{key}_floorlamp_pole", bm, coll, [M["gold"]], (fx, fy, 0))
    bm = bmesh.new()
    add_cyl(bm, 0.24, 0.17, 0.30, (0, 0, 0), segs=32, caps=False)
    sh2 = bm_object(f"{key}_floorlamp_shade", bm, coll, [M["shade"]], (fx, fy, 1.58))
    sh2.visible_shadow = False
    lights.append(add_light(f"{key}_floorlamp_light", coll, "POINT", (fx, fy, 1.55), energy=26,
                            color=(1.0, 0.62, 0.32), radius=0.12, cutoff=8.0, volume=0.4))

    # fairy-light swags on the back wall
    pts = []
    # swags low enough to sit in the S1 eye-level frame, above the couch back
    swags = [(-2.6, -1.25, 1.52, 1.55, 0.18), (-1.25, 0.15, 1.55, 1.53, 0.21), (0.15, 1.55, 1.53, 1.56, 0.19),
             (1.55, 3.0, 1.56, 1.50, 0.17)]
    for (x0, x1, z0, z1, sag) in swags:
        pts += swag_points(O.x + x0, O.x + x1, z0, z1, sag, O.y + 2.42, 26, rnd, 0.006)
        lights.append(add_light(f"{key}_fairy_wash_{len(lights)}", coll, "POINT",
                                (O.x + (x0 + x1) / 2, O.y + 2.25, (z0 + z1) / 2 - sag * 0.8), energy=7.0,
                                color=BULB_RGB, radius=0.35, cutoff=3.5, shadow=False, volume=0.2))
    bulbs_obj(f"{key}_fairy_bulbs", coll, pts, M["bulb"])

    if variant == "jam":
        # holiday garland over the fairy lights + ornaments + warm bulbs woven in
        bm = bmesh.new()
        gpts = swag_points(O.x - 2.8, O.x + 2.8, 2.24, 2.22, 0.30, O.y + 2.36, 150)
        orn = []
        warm = []
        for i, p in enumerate(gpts):
            rr = rnd.uniform(0.045, 0.065)
            add_ico(bm, rr, (p[0] + rnd.uniform(-0.015, 0.015), p[1] + rnd.uniform(-0.02, 0.02),
                             p[2] + rnd.uniform(-0.02, 0.02)), subdiv=1)
            if i % 9 == 4:
                orn.append((p[0], p[1] - 0.06, p[2] - 0.06))
            if i % 3 == 1:
                warm.append((p[0] + rnd.uniform(-0.03, 0.03), p[1] - 0.065, p[2] + rnd.uniform(-0.04, 0.04)))
        recenter(bm_object(f"{key}_garland", bm, coll, [M["garland"]], (0, 0, 0)))
        bm = bmesh.new()
        for i, p in enumerate(orn):
            add_sphere(bm, (0.035, 0.035, 0.035), p, u=12, v=8, mi=i % 2)
        recenter(bm_object(f"{key}_ornaments", bm, coll, [M["gold"], M["ornament_orange"]], (0, 0, 0)))
        bulbs_obj(f"{key}_garland_bulbs", coll, warm, M["bulb"], r=0.010)
        lights.append(add_light(f"{key}_garland_wash", coll, "AREA", (O.x, O.y + 2.15, 2.0),
                                target=(O.x, O.y + 2.5, 2.1), energy=12, color=BULB_RGB, size=4.0, size_y=0.3,
                                cutoff=4.0, shadow=False, volume=0.2))
        # laptop on a tall plant-stand table at camera-right, screen angled to camera and to her
        sx, sy = O.x + 0.78, O.y + 1.02
        bm = bmesh.new()
        add_cyl(bm, 0.24, 0.24, 0.03, (0, 0, 0.96), segs=28)
        add_cyl(bm, 0.025, 0.025, 0.95, (0, 0, 0.475), segs=10)
        add_cyl(bm, 0.17, 0.17, 0.025, (0, 0, 0.0125), segs=20)
        bm_object(f"{key}_laptop_table", bm, coll, [M["wood"]], (sx, sy, 0))
        lap_rz = math.radians(-32)   # screen faces the lens and turns toward her
        lap = new_empty(f"{key}_laptop", coll, (sx, sy, 0.975), (0, 0, lap_rz), size=0.1)
        bm = bmesh.new()
        add_box(bm, (0.32, 0.22, 0.014), (0, 0, 0.007))
        bm_object(f"{key}_laptop_base", bm, coll, [M["alu"]], parent=lap, smooth=False)
        lid = new_empty(f"{key}_laptop_hinge", coll, (0, 0.11, 0.014), (math.radians(-104 + 90), 0, 0), parent=lap,
                        size=0.05)
        bm = bmesh.new()
        add_box(bm, (0.32, 0.008, 0.215), (0, 0.004, 0.1075))
        bm_object(f"{key}_laptop_lid", bm, coll, [M["alu"]], parent=lid, smooth=False)
        bm = bmesh.new()
        v = [bm.verts.new(p) for p in ((-0.145, 0, -0.092), (0.145, 0, -0.092), (0.145, 0, 0.092), (-0.145, 0, 0.092))]
        bm.faces.new(v)
        scr = bm_object(f"{key}_laptop_screen", bm, coll, [M["screen"]], (0, -0.0015, 0.1075), parent=lid, smooth=False)
        scr.visible_shadow = False
        mp = M["screen"].node_tree.nodes["LogScroll"]
        loc_in = mp.inputs["Location"]
        step = 0.0072
        nsteps = int((f1 - f0) / 8) + 1
        for k in range(nsteps):
            loc_in.default_value[2] = -k * step
            loc_in.keyframe_insert("default_value", index=2, frame=f0 + k * 8)
        ad = M["screen"].node_tree.animation_data
        cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        for fc in cb.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"
        rz = Matrix.Rotation(lap_rz, 3, "Z")
        scr_world = Vector((sx, sy, 0.975)) + rz @ Vector((0, 0.10, 0.12))
        lights.append(add_light(f"{key}_laptop_glow", coll, "AREA", scr_world,
                                target=scr_world + rz @ Vector((0, -1, 0.15)), energy=5.0, color=(1.0, 0.6, 0.25),
                                size=0.3, size_y=0.2, cutoff=3.0, shadow=False, volume=0.2))
    return lights


def mic_stand(coll, key, idx, loc, mic_z, toward):
    """loc = base position; mic head at mic_z pointing toward `toward` (mouth)."""
    bm = bmesh.new()
    add_cyl(bm, 0.14, 0.14, 0.02, (0, 0, 0.01), segs=24)
    add_cyl(bm, 0.011, 0.011, mic_z - 0.08, (0, 0, (mic_z - 0.08) / 2), segs=10)
    bm_object(f"{key}_micstand_{idx}", bm, coll, [MATS["black"]], loc)
    head = Vector((loc[0], loc[1], loc[2] + mic_z))
    d = (Vector(toward) - head).normalized()
    rot = d.to_track_quat("Z", "Y").to_euler()
    bm = bmesh.new()
    add_cyl(bm, 0.017, 0.012, 0.12, (0, 0, -0.03), segs=16)
    add_sphere(bm, (0.026, 0.026, 0.032), (0, 0, 0.05), u=16, v=10, mi=1)
    bm_object(f"{key}_mic_{idx}", bm, coll, [MATS["chrome"], MATS["grille"]], head, rot)


def build_stage(key, O, variant, coll, f0, f1, rnd, performers_xy):
    O = Vector(O)
    M = MATS
    trio = variant == "trio"
    w = 8.0 if trio else 6.2
    lights = []
    box_obj(f"{key}_house_floor", coll, (18.0, 16.0, 0.04), O + Vector((0, -1.0, -0.02)), M["house_floor"])
    box_obj(f"{key}_riser_top", coll, (w, 5.1, 0.04), O + Vector((0, 1.65, 0.28)), M["stage_top"])
    box_obj(f"{key}_riser_body", coll, (w, 5.1, 0.27), O + Vector((0, 1.65, 0.13)), M["velvet_dark"])
    # backdrop + side legs (Tuscan-brown velvet)
    curtain_obj(f"{key}_backdrop", coll, w + 4.0, 6.0, O + Vector((0, 4.30, 0.0)), M["velvet"], pleat=0.30,
                depth=0.12, gather_bottom=0.3)
    for i, sx in enumerate((-1, 1)):
        curtain_obj(f"{key}_leg_{i}", coll, 1.4, 6.0, O + Vector((sx * (w / 2 + 0.1), 2.0, 0.0)), M["velvet"],
                    pleat=0.22, depth=0.16, rot=(0, 0, sx * 0.25))
    # fairy-light strands hanging in front of the backdrop: the bokeh wall
    pts = []
    xs = [-w / 2 + 0.35 + i * 0.30 for i in range(int((w - 0.7) / 0.30) + 1)]
    for x in xs:
        y = 3.85 + rnd.uniform(-0.18, 0.12)
        z = rnd.uniform(0.7, 1.1)
        top = rnd.uniform(4.2, 5.2)
        while z < top:
            pts.append((O.x + x + rnd.uniform(-0.025, 0.025), O.y + y, z))
            z += rnd.uniform(0.09, 0.15)
    bulbs_obj(f"{key}_bokeh_strands", coll, pts, M["bulb"], r=0.012)
    # marquee footlights along the stage lip
    fpts = [(O.x - w / 2 + 0.15 + i * 0.22, O.y - 0.86, 0.315) for i in range(int((w - 0.3) / 0.22) + 1)]
    bulbs_obj(f"{key}_footlights", coll, fpts, M["bulb_soft"], r=0.016)
    lights.append(add_light(f"{key}_foot_wash", coll, "AREA", O + Vector((0, -0.75, 0.33)),
                            target=O + Vector((0, 0.5, 1.6)), energy=40, color=GOLD_RGB, size=w * 0.8, size_y=0.15,
                            cutoff=6.0, shadow=False, volume=0.3))
    # candles: stage-left front cluster, upstage-right cluster
    clusters = [(-2.1 if not trio else -3.0, -0.55), (2.25 if not trio else 3.1, 2.15)]
    if trio:
        clusters.append((-2.6, 2.2))
    ci = 0
    for (cx, cy) in clusters:
        for k in range(5):
            h = rnd.uniform(0.10, 0.34)
            lights.append(candle(coll, key, ci, (O.x + cx + rnd.uniform(-0.22, 0.22), O.y + cy + rnd.uniform(-0.15, 0.15),
                                                0.30), h, rnd.uniform(0.035, 0.05), rnd, f0, energy=1.4))
            ci += 1
    # curtain washes from above: colour as the set
    for i, x in enumerate([-w / 3, 0.0, w / 3]):
        lights.append(add_light(f"{key}_curtain_wash_{i}", coll, "SPOT", O + Vector((x, 2.6, 5.4)),
                                target=O + Vector((x * 1.1, 4.3, 1.4)), energy=1100, color=BURNT_RGB, spot_deg=55,
                                blend=0.9, radius=0.3, cutoff=12.0, shadow=False, volume=0.08))
    # haze
    bm = bmesh.new()
    add_box(bm, (w + 4.0, 11.5, 6.5))
    hz = bm_object(f"{key}_haze", bm, coll, [haze_material(f"{key}_haze", 0.016 if not trio else 0.014)],
                   O + Vector((0, -0.4, 3.2)), smooth=False)
    hz.visible_shadow = False
    # mic stands. Solo: just off her left shoulder, in front. Trio: side-address, beside each singer, so the
    # quarter orbit never parks a stand in front of the lead's gestures.
    for i, (px, py) in enumerate(performers_xy):
        if not trio:
            base = (O.x + px + 0.24, O.y + py - 0.30, 0.30)
        else:
            side = -1 if px <= 0 else 1
            base = (O.x + px + side * 0.40, O.y + py - 0.05, 0.30)
        mic_stand(coll, key, i, base, 1.38, (O.x + px, O.y + py - 0.06, 0.30 + 1.56))
    # amber backlight straight through the haze toward the lens: the glow behind her head
    for i, (px, py) in enumerate(performers_xy[:1] if not trio else performers_xy):
        lights.append(add_light(f"{key}_BACKLIGHT_haze_{i}", coll, "SPOT", O + Vector((px * 1.2, py + 2.6, 3.0)),
                                target=O + Vector((px, py - 1.0, 1.75)), energy=420 if i == 0 else 260,
                                color=AMBER_RGB, spot_deg=24, blend=0.8, radius=0.2, cutoff=12.0, shadow=True,
                                volume=3.0))
    return lights


# ---------------------------------------------------------------------------------------------------------------
# Scene context: timing + camera paths (analytic, mirrored by the keyframes)
# ---------------------------------------------------------------------------------------------------------------

class Ctx:
    def __init__(self, beats, downbeats):
        self.beats, self.downbeats = beats, downbeats
        self.cam_pos = None
        self.look_weight = lambda t: 1.0
        self.eyes_down_deg = 0.0
        self._db_index = {}
        for i, b in enumerate(beats):
            j = min(range(len(downbeats)), key=lambda k: abs(downbeats[k] - b))
            self._db_index[i] = round((b - downbeats[j]) / ((downbeats[1] - downbeats[0]) / 4)) % 4

    def beat_index(self, t):
        B = self.beats
        if t <= B[0]:
            return (t - B[0]) / (B[1] - B[0])
        if t >= B[-1]:
            return len(B) - 1 + (t - B[-1]) / (B[-1] - B[-2])
        lo, hi = 0, len(B) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if B[mid] <= t:
                lo = mid
            else:
                hi = mid
        return lo + (t - B[lo]) / (B[hi] - B[lo])

    def is_backbeat(self, i):
        return self._db_index.get(i, 0) in (1, 3)


# ---------------------------------------------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------------------------------------------

def resolve_plan(shots, downbeats, song_end):
    plan = [dict(p) for p in DEFAULT_PLAN]
    src = "director defaults (PLAN.md Vision v2)"
    scenes = shots.get("scenes") if isinstance(shots, dict) else None
    if isinstance(scenes, list) and scenes:
        scs = sorted([s for s in scenes if isinstance(s, dict) and _num(s.get("t0")) is not None],
                     key=lambda s: s["t0"])
        if scs:
            src = "shots file scenes[]"
            new = []
            living_seen = 0
            for i, s in enumerate(scs):
                base = dict(plan[i]) if len(scs) == len(plan) else {}
                txt = " ".join(str(s.get(k, "")) for k in ("set", "title", "section", "light")).lower()
                if not base:
                    if s.get("harmony"):
                        base = dict(DEFAULT_PLAN[3])
                    elif any(k in txt for k in ("stage", "curtain", "mic")):
                        base = dict(DEFAULT_PLAN[2])
                    else:
                        base = dict(DEFAULT_PLAN[0] if living_seen == 0 else DEFAULT_PLAN[1])
                        living_seen += 1
                base["id"] = s.get("id", base.get("id"))
                base["title"] = s.get("title", base.get("title"))
                base["section"] = s.get("section", base.get("section"))
                base["t0"] = float(s["t0"])
                base["t1"] = float(s.get("t1", base.get("t1", song_end)))
                cam = s.get("camera") or {}
                if cam.get("move") in MOVE_LABEL:
                    base["move"] = cam["move"]
                if isinstance(cam.get("focal_mm"), (int, float)):
                    base["focal"] = float(cam["focal_mm"])
                posts = cam.get("post") or []
                base["post_raw"] = posts
                for p in posts:
                    if p.get("fx") == "slow_push" and isinstance(p.get("from"), (int, float)):
                        base["post"] = (float(p["from"]), float(p.get("to", p["from"])))
                base["set_text"] = s.get("set", "")
                base["light_text"] = s.get("light", "")
                base["emotion"] = s.get("emotion", "")
                base["look_id"] = s.get("look", "")
                new.append(base)
            plan = new
    # snap internal boundaries to downbeats
    for i in range(1, len(plan)):
        t = plan[i]["t0"]
        nb = min(downbeats, key=lambda d: abs(d - t))
        if abs(nb - t) < 0.35:
            plan[i]["t0"] = nb
            plan[i - 1]["t1"] = nb
    plan[-1]["t1"] = min(plan[-1]["t1"], song_end) if plan[-1]["t1"] > 0 else song_end
    return plan, src


def cues_from_shots(shots):
    if not isinstance(shots, dict):
        return None, None
    moves = shots.get("moves") if isinstance(shots.get("moves"), dict) else {}
    takes = shots.get("takes") if isinstance(shots.get("takes"), list) else []
    if not takes:
        return None, None, {}

    def tid(tk):
        return str(tk.get("id", "")).lower()
    # the lead's motion is the FIRST full-song hero take in file order (hero-1), not the one with the most cues
    lead = (next((tk for tk in takes if tk.get("song") and "hero" in tid(tk)), None)
            or next((tk for tk in takes if tk.get("song") and "backup" not in tid(tk)), None) or takes[0])
    backup = next((tk for tk in takes if "backup" in tid(tk)), None)

    def extract(tk):
        out = []
        for c in tk.get("cues") or []:
            if not isinstance(c, dict) or c.get("kind", "move") != "move":
                continue
            t = _num(c.get("t"))
            if t is None:
                continue
            mv = c.get("move", "")
            label = (moves.get(mv) or {}).get("label", "") if isinstance(moves.get(mv), dict) else ""
            g = classify_move(mv, label, c.get("text", ""))
            if g:
                side, both = side_from_text(c.get("text", ""))
                out.append(dict(t=t, dur=float(c.get("dur") or 1.3), move=g, text=label or c.get("text") or mv,
                                side=side, both=both))
            else:
                log(f"  cue {mv!r} at {t} has no previs move; skipped")
        return out
    return extract(lead), (extract(backup) if backup else None), dict(lead_take=tk_id(lead), backup_take=tk_id(backup))


def tk_id(tk):
    return str(tk.get("id", "")) if isinstance(tk, dict) else None


def looks_wardrobe(looks):
    """{scene_key_or_id: {'lead': text, 'backup': text}} from a looks.json of unknown exact shape."""
    out = {}
    scene_info = {}
    if not looks:
        return out, scene_info
    found = []

    def text_of(v):
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            return ", ".join(str(x) for x in v.values() if isinstance(x, str))
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if isinstance(x, str))
        return ""

    def walk(o, ctx_scene=None, depth=0):
        if depth > 6:
            return
        if isinstance(o, dict):
            sid = next((o[k] for k in ("scene", "scene_id") if isinstance(o.get(k), str)), ctx_scene)
            if ("set" in o or "light" in o) and isinstance(o.get("id"), str) and "wardrobe" not in o:
                scene_info[o["id"]] = dict(set=text_of(o.get("set")), light=text_of(o.get("light")))
                sid = o["id"]
            if text_of(o.get("wardrobe")):
                found.append((o, sid))
            for v in o.values():
                walk(v, sid, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, ctx_scene, depth + 1)
    walk(looks)
    for o, sid in found:
        lid = str(o.get("id", ""))
        scene_id = sid or (lid.split(".")[0] if "." in lid else "")
        # role from the character field (or the id suffix after the last "."), never from the scene id:
        # "s4-harmony.lead" is the lead even though the scene is called harmony.
        role_txt = next((str(o[k]) for k in ("character", "role", "who") if o.get(k)), lid.rsplit(".", 1)[-1])
        role = "backup" if any(k in role_txt.lower() for k in ("backup", "clone", "left", "right")) else "lead"
        out.setdefault(scene_id, {})
        out[scene_id].setdefault(role, text_of(o["wardrobe"]))
    return out, scene_info


def scene_key_match(d, plan_item, idx):
    if not d:
        return None
    for k in (plan_item.get("look_id", "").split(".")[0], plan_item.get("id")):
        if k and k in d:
            return d[k]
    for k, v in d.items():
        if k and (k.lower().startswith("s%d" % (idx + 1)) or k.lower().startswith("scene%d" % (idx + 1))):
            return v
    return None


def build(args):
    sc = reset_scene()
    prefs = bpy.context.preferences
    prefs.view.timecode_style = "SMPTE"
    rnd = random.Random(1103)

    timing = parse_timing(load_json(args.timing, "timing"))
    shots = load_json(args.shots, "shots")
    looks = load_json(args.looks, "looks")

    # --- song + frame range -----------------------------------------------------------------------------------
    sc.render.fps, sc.render.fps_base = FPS, 1.0
    sc.frame_start = 0
    se = sc.sequence_editor_create()
    strips = se.strips if hasattr(se, "strips") else se.sequences
    snd = strips.new_sound("song", args.song, 1, 0)
    names = {p.identifier for p in snd.bl_rna.properties}
    if {"left_handle", "right_handle"} <= names:
        song_frames = int(snd.right_handle - snd.left_handle)
    elif "duration" in names:
        song_frames = int(snd.duration)
    else:
        song_frames = int(snd.frame_final_duration)
    song_end = song_frames / FPS
    downbeats = timing["downbeats"] or DEFAULT_DOWNBEATS
    beats = list(timing["beats"] or beats_from_downbeats(downbeats, song_end))
    per = beats[-1] - beats[-2]
    while beats[-1] + per < song_end + 0.7:      # keep the grid going to the very end (sway, beckon)
        beats.append(beats[-1] + per)
    lines = timing["lines"]
    log(f"song {args.song}: {song_frames} frames = {song_end:.3f}s; downbeats from "
        f"{'timing file' if timing['downbeats'] else 'beat_this defaults'}; {len(beats)} beats; {len(lines)} lyric lines")

    plan, plan_src = resolve_plan(shots, downbeats, song_end)
    log("scene plan from", plan_src)
    total_frames = int(round(min(song_end, plan[-1]["t1"]) * FPS))
    sc.frame_end = total_frames - 1
    for i, p in enumerate(plan):
        p["key"] = "S%d" % (i + 1)
        p["start_frame"] = 0 if i == 0 else int(round(p["t0"] * FPS)) - 1
    for i, p in enumerate(plan):
        p["end_frame"] = plan[i + 1]["start_frame"] - 1 if i + 1 < len(plan) else sc.frame_end
        p["marker"] = f"{p['key']} {p['tag']} · {MOVE_LABEL.get(p['move'], p['move'])}"

    # --- cues ---------------------------------------------------------------------------------------------------
    lead_cues, backup_cues, take_info = cues_from_shots(shots)
    if lead_cues:
        cue_src = f"shots file takes[{take_info.get('lead_take')}].cues"
        if backup_cues:
            cue_src += f" + takes[{take_info.get('backup_take')}] for the clones"
    else:
        cue_src = "director canonical plan (fallback)"
    if not lead_cues:
        lead_cues = fallback_cues(lines, timing["beats"] or beats, downbeats, song_end)
    log(f"hand cues from {cue_src}: " + ", ".join(f"{c['move']}@{c['t']:.2f}" for c in lead_cues))

    wardrobe_by_scene, look_scene_info = looks_wardrobe(looks)

    build_materials()
    sets_root = new_collection("previs_sets")
    cams_coll = new_collection("previs_cameras")
    contexts = {}
    performers = []
    all_cams = {}

    for i, p in enumerate(plan):
        key = p["key"]
        O = Vector((100.0 * i, 0.0, 0.0))
        coll = new_collection(f"{key}_{p['tag'].replace(' ', '_')}", sets_root)
        f0, f1 = p["start_frame"], p["end_frame"]
        t0, t1 = f0 / FPS, (f1 + 1) / FPS
        ctx = Ctx(beats, downbeats)
        contexts[key] = ctx
        wd = scene_key_match(wardrobe_by_scene, p, i) or {}
        dflt = DEFAULT_WARDROBE.get(key, DEFAULT_WARDROBE["S2"])
        if p["set"] == "stage" and p["variant"] == "solo":
            dflt = DEFAULT_WARDROBE["S3"]
        if p["variant"] == "trio":
            dflt = DEFAULT_WARDROBE["S4"]
        if p["variant"] == "couch":
            dflt = DEFAULT_WARDROBE["S1"]
        outfit = wardrobe_spec(wd.get("lead", ""), dflt)
        backup_outfit = wardrobe_spec(wd.get("backup", wd.get("lead", "")), dflt)
        p["wardrobe_lead"] = outfit.get("text", "")
        p["wardrobe_backup"] = backup_outfit.get("text", "") if p["variant"] == "trio" else ""
        p["wardrobe_src"] = "looks.json" if wd else "director defaults"
        info = look_scene_info.get(p.get("id", ""), {})
        p.setdefault("set_text", info.get("set", ""))
        p.setdefault("light_text", info.get("light", ""))

        # ---- set + performers + camera, per variant ----------------------------------------------------------
        if p["set"] == "living":
            seated = p["variant"] == "couch"
            lights = build_living_room(key, O, p["variant"], coll, f0, f1, rnd)
            if seated:
                lead_loc = O + Vector((0.30, 1.72, 0.0))
            else:
                lead_loc = O + Vector((0.08, 1.10, 0.0))
            lead = build_mannequin(f"{key}_lead", coll, lead_loc, 0.0, outfit, MATS["skin"], seated=seated)
            rigs = [(lead, "lead")]
            face = lead_loc + Vector((0, -0.01, lead.hip_z + NECK_Z + 0.165))
        else:
            trio = p["variant"] == "trio"
            if trio:
                pos = [(0.0, -0.28), (-0.95, 0.62), (0.95, 0.62)]
            else:
                pos = [(0.0, 0.0)]
            lights = build_stage(key, O, p["variant"], coll, f0, f1, rnd, pos)
            rigs = []
            for k, (px, py) in enumerate(pos):
                is_backup = k > 0
                nm = f"{key}_lead" if k == 0 else (f"{key}_backup_L" if px < 0 else f"{key}_backup_R")
                rg = build_mannequin(nm, coll, O + Vector((px, py, 0.30)), 0.0,
                                     backup_outfit if is_backup else outfit, MATS["skin"],
                                     darken=0.72 if is_backup else 1.0)
                rigs.append((rg, "backup" if is_backup else ("lead_track" if trio else "lead")))
            lead = rigs[0][0]
            face = lead.root_loc + Vector((0, -0.01, lead.hip_z + NECK_Z + 0.165))

        # ---- key / bounce / rim (soft warm key from camera-left, gold bounce, amber rim) -----------------------
        if p["set"] == "living":
            key_loc = face + Vector((-1.55, -1.45, 0.55))
            lights.append(add_light(f"{key}_KEY", coll, "AREA", key_loc, target=face + Vector((0, 0, -0.08)),
                                    energy=145 if p["variant"] == "couch" else 160, color=KEY_RGB, size=1.5,
                                    cutoff=10.0, volume=0.25, spread_deg=55))
            lights.append(add_light(f"{key}_BOUNCE_gold", coll, "AREA", face + Vector((-0.1, -1.0, -0.85)),
                                    target=face, energy=16, color=GOLD_RGB, size=1.0, size_y=0.45, cutoff=6.0,
                                    shadow=False, volume=0.1, spread_deg=70))
            lights.append(add_light(f"{key}_RIM_amber", coll, "SPOT", face + Vector((0.75, 0.55, 1.15)),
                                    target=face + Vector((0, 0.05, -0.25)), energy=260, color=AMBER_RGB,
                                    spot_deg=38, blend=0.7, radius=0.15, cutoff=8.0, volume=0.6))
        else:
            trio = p["variant"] == "trio"
            key_loc = face + Vector((-1.75, -1.75, 0.55))
            lights.append(add_light(f"{key}_KEY", coll, "AREA", key_loc, target=face + Vector((0, 0, -0.08)),
                                    energy=280 if not trio else 340, color=KEY_RGB, size=1.8 if not trio else 2.6,
                                    cutoff=12.0, volume=0.15, spread_deg=55 if not trio else 70))
            lights.append(add_light(f"{key}_BOUNCE_gold", coll, "AREA", face + Vector((0.0, -1.15, -1.25)),
                                    target=face, energy=30, color=GOLD_RGB, size=1.3, size_y=0.5, cutoff=6.0,
                                    shadow=False, volume=0.05, spread_deg=70))
            for k, (rg, kind) in enumerate(rigs):
                fpos = rg.root_loc + Vector((0, 0, rg.hip_z + NECK_Z + 0.165))
                sx = 0.55 if k != 1 else -0.55
                lights.append(add_light(f"{key}_RIM_amber_{k}", coll, "SPOT", fpos + Vector((sx, 1.55, 1.75)),
                                        target=fpos + Vector((0, 0.0, -0.35)), energy=1000 if k == 0 else 700,
                                        color=AMBER_RGB, spot_deg=30, blend=0.6, radius=0.12, cutoff=12.0,
                                        volume=2.5))
            lights.append(add_light(f"{key}_KICKER", coll, "SPOT", face + Vector((-1.4, 1.4, 1.4)),
                                    target=face + Vector((0, 0, -0.2)), energy=320, color=BURNT_RGB, spot_deg=32,
                                    blend=0.7, radius=0.15, cutoff=10.0, volume=1.0))

        # ---- camera rig ---------------------------------------------------------------------------------------
        ccoll = new_collection(f"{key}_camera", cams_coll)
        move = p["move"]
        lens = p.get("focal", 50.0)
        cam_d = bpy.data.cameras.new(f"{key}_cam")
        cam_d.lens = lens
        cam_d.sensor_width = 36.0
        cam_d.sensor_fit = "HORIZONTAL"
        cam_d.clip_start = 0.05
        cam_d.clip_end = 60.0
        cam_d.dof.use_dof = True
        cam_d.dof.focus_object = lead.eyes
        cam_d.dof.aperture_blades = 0
        info = dict(height_m=None, slide_m=0.0, orbit_deg=0.0)
        if p["set"] == "living":
            if p["variant"] == "couch":
                target = O + Vector((0.12, 1.62, 0.78))
                d0, d1 = 3.40, 3.40 / (p["post"][1] if p["post"][1] > 0 else 1.08)
                eye_h = 1.14
                dirn = Vector((-0.06, -1.0, 0.0)).normalized()
                fstop = 2.8
            else:
                target = O + Vector((0.08, 1.10, 1.42))
                d0 = d1 = 1.80
                eye_h = 1.50
                dirn = Vector((0.0, -1.0, 0.0))
                fstop = 2.8
        else:
            if p["variant"] == "solo":
                target = O + Vector((0.0, -0.02, 1.62))
                d0 = 2.58
                d1 = d0 / (p["post"][1] if p["post"][1] > 0 else 1.12)
                eye_h = 1.02
                dirn = Vector((0.07, -1.0, 0.0)).normalized()
                fstop = 1.0     # exaggerated so the fairy-light wall reads as bokeh in a 480p animatic
            else:
                target = O + Vector((0.0, 0.05, 1.42))
                d0 = d1 = 2.60
                eye_h = 1.45
                dirn = Vector((0.0, -1.0, 0.0))
                fstop = 2.8
        # Generic: honour the move from the shots file even if it differs from the set's default.
        rig = new_empty(f"{key}_cam_rig", ccoll, size=0.2, kind="CUBE")
        aim = new_empty(f"{key}_cam_aim", ccoll, parent=rig, size=0.1)
        tgt = new_empty(f"{key}_cam_target", ccoll, target, size=0.15, kind="SPHERE")
        con = aim.constraints.new("TRACK_TO")
        con.target = tgt
        con.track_axis = "TRACK_NEGATIVE_Z"
        con.up_axis = "UP_Y"
        cam = link_obj(f"{key}_cam", cam_d, ccoll, parent=aim)
        cam_d.dof.aperture_fstop = fstop

        def flat(v):
            return Vector((v.x, v.y, 0.0))

        if move == "quarter_orbit":
            pivot_xy = O + Vector((0.0, 0.18, 0.0))
            piv = new_empty(f"{key}_orbit_pivot", ccoll, (pivot_xy.x, pivot_xy.y, eye_h), size=0.3, kind="CIRCLE")
            rig.parent = piv
            R = d0 + 0.28
            rig.location = (0.0, -R, 0.0)
            a0, a1 = math.radians(-45), math.radians(45)
            key_pair(piv, "rotation_euler", f0, Euler((0, 0, a0)), f1, Euler((0, 0, a1)))
            info["orbit_deg"] = 90.0

            def cam_pos(t, piv_loc=Vector((pivot_xy.x, pivot_xy.y, eye_h)), R=R, a0=a0, a1=a1, f0=f0, f1=f1):
                a = lerp(a0, a1, ease((t * FPS - f0) / max(1, f1 - f0)))
                return piv_loc + Matrix.Rotation(a, 3, "Z") @ Vector((0, -R, 0))
        else:
            if move in ("handheld_float", "lateral_slide"):
                side = Vector((1.0, 0.0, 0.0))
                base = target + dirn * d0
                p0 = Vector((base.x - 0.26, base.y, eye_h))
                p1 = Vector((base.x + 0.26, base.y, eye_h))
                key_pair(rig, "location", f0, p0, f1, p1)
                key_pair(tgt, "location", f0, target - side * 0.07, f1, target + side * 0.07)
                info["slide_m"] = 0.52
            elif move == "static":
                p0 = p1 = Vector((target.x + dirn.x * d0, target.y + dirn.y * d0, eye_h))
                rig.location = p0
            else:  # push-ins
                p0 = Vector((target.x + dirn.x * d0, target.y + dirn.y * d0, eye_h))
                p1 = Vector((target.x + dirn.x * d1, target.y + dirn.y * d1, eye_h + (0.02 if move == "low_angle_push_in" else 0.0)))
                key_pair(rig, "location", f0, p0, f1, p1)

            def cam_pos(t, p0=p0, p1=p1, f0=f0, f1=f1):
                return p0.lerp(p1, ease((t * FPS - f0) / max(1, f1 - f0)))
        if move == "handheld_float":
            cam.keyframe_insert("rotation_euler", frame=f0)
            cam.keyframe_insert("location", frame=f0)
            for idx, (st, scl) in enumerate(((0.010, 46.0), (0.012, 38.0), (0.005, 52.0))):
                add_noise(cam, "rotation_euler", idx, st, scl, rnd.uniform(0, 200), depth=1)
            for idx, (st, scl) in enumerate(((0.016, 60.0), (0.008, 64.0), (0.012, 44.0))):
                add_noise(cam, "location", idx, st, scl, rnd.uniform(0, 200), depth=1)
        ctx.cam_pos = cam_pos
        all_cams[key] = cam
        m = sc.timeline_markers.new(p["marker"], frame=f0)
        m.camera = cam
        pf = ctx.cam_pos(t0)
        pl = ctx.cam_pos(t1)
        subj = face
        # small eye light beside the lens: a catchlight + a little lift so dark skin doesn't fall to mud
        add_light(f"{key}_EYELIGHT", coll, "AREA", pf + Vector((0.0, 0.0, 0.14)), target=face, energy=40 if
                  p["set"] == "living" else 60, color=KEY_RGB, size=0.3, cutoff=8.0, shadow=False, volume=0.0,
                  spread_deg=25)
        p["camera_object"] = cam.name
        p["dolly_start_m"] = round((pf - subj).length, 3)
        p["dolly_end_m"] = round((pl - subj).length, 3)
        p["height_m"] = round(eye_h, 3)
        p["aperture_fstop"] = fstop
        p["slide_m"] = info["slide_m"]
        p["orbit_deg"] = info["orbit_deg"]
        tilt = math.degrees(math.atan2(target.z - eye_h, (flat(target) - flat(pf)).length))
        p["tilt_deg"] = round(tilt, 1)

        # ---- eyes-down → lens, per scene ---------------------------------------------------------------------
        if p["variant"] == "couch":
            ctx.eyes_down_deg = 14.0
            ctx.look_weight = lambda t, a=t0 + 2.6, b=t1 - 0.6: smooth01((t - a) / max(0.1, b - a))
        elif p["variant"] == "jam":
            turn = min((d for d in downbeats if d > t0 + 3.5), default=t0 + 4.0)
            ctx.eyes_down_deg = 10.0
            ctx.look_weight = lambda t, a=turn - 0.4, b=turn + 0.5: smooth01((t - a) / (b - a))
        else:
            ctx.eyes_down_deg = 0.0
            ctx.look_weight = lambda t: 1.0

        # ---- performers: sway, nods, gestures, bake ----------------------------------------------------------
        for k, (rg, kind) in enumerate(rigs):
            if kind == "backup":
                left = rg.root_loc.x < (O.x)
                pf_ = Performer(rg, ctx, kind="backup", mirror_arm=left, delay=0.163 if left else 0.326,
                                sway_amp=2.8, nod_amp=4.0)
                bcues = backup_cues or []
                if bcues:
                    for c in bcues:   # the flanks mirror each other, whatever hand the take says
                        if t0 <= c["t"] < t1:
                            pf_.add_gesture(c["t"], c["dur"], c["move"], "L" if left else "R", both=c.get("both", False))
                else:
                    last_db = max(d for d in downbeats if d < t1)
                    pf_.add_gesture(t0 + 0.3, max(0.5, last_db - t0 - 0.9), "backbeat_snap", "L" if left else "R")
                    pf_.add_gesture(last_db, max(0.6, t1 - last_db - 0.3), "adlib_wave", "L" if left else "R")
            else:
                pf_ = Performer(rg, ctx, kind=kind, sway_amp=1.2 if rg.seated else 2.2,
                                nod_amp=4.0 if rg.seated else 5.5)
                for c in lead_cues:
                    if t0 <= c["t"] < t1 - 0.2:
                        pf_.add_gesture(c["t"], min(c["dur"], t1 - c["t"]), c["move"], side=c.get("side"),
                                        both=c.get("both", False))
            performers.append((key, pf_))
            pf_.bake(max(0, f0 - 2), min(sc.frame_end, f1 + 2))
        p["cues"] = [dict(t=round(c["t"], 2), dur=round(c["dur"], 2), move=c["move"],
                          label=GESTURE_LABEL.get(c["move"], c["move"]),
                          hand=("both" if c.get("both") else {"L": "left", "R": "right"}.get(c.get("side"), "")))
                     for c in lead_cues if t0 <= c["t"] < t1 - 0.2]
        log(f"{key} built: {p['title']} frames {f0}-{f1}, camera {move} {lens:.0f}mm, "
            f"{len(rigs)} performer(s), {len(lights)} lights, cues {[c['move'] for c in p['cues']]}")

    sc.camera = all_cams[plan[0]["key"]]

    # --- world, colour, compositor ------------------------------------------------------------------------------
    world = bpy.data.worlds.new("previs_world_warm_brown")
    _ensure_nodes(world)
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.010, 0.0058, 0.0036, 1)
    bg.inputs["Strength"].default_value = 1.0
    sc.world = world
    vs = sc.view_settings
    vs.view_transform = "AgX"
    try:
        vs.look = "AgX - Punchy"
    except Exception:
        pass
    vs.exposure = -0.9
    setup_compositor(sc)
    return sc, plan, dict(beats=beats, downbeats=downbeats, lines=lines, sections=timing["sections"], plan_src=plan_src, cue_src=cue_src,
                          song_end=song_end, total_frames=total_frames, sound=snd)


def setup_compositor(sc):
    try:
        tree = bpy.data.node_groups.new("previs_comp", "CompositorNodeTree")
        tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = tree.nodes.new("CompositorNodeRLayers")
        go = tree.nodes.new("NodeGroupOutput")
        gl = tree.nodes.new("CompositorNodeGlare")
        for val in ("Bloom", "BLOOM", "Fog Glow", "FOG_GLOW"):
            try:
                gl.inputs["Type"].default_value = val
                break
            except Exception:
                continue
        gl.inputs["Threshold"].default_value = 2.0
        gl.inputs["Strength"].default_value = 0.22
        gl.inputs["Size"].default_value = 0.45
        try:
            gl.inputs["Quality"].default_value = "Medium"
        except Exception:
            pass
        tree.links.new(rl.outputs["Image"], gl.inputs["Image"])
        tree.links.new(gl.outputs["Image"], go.inputs[0])
        sc.compositing_node_group = tree
        sc.render.use_compositing = True
        log("compositor: glare", gl.inputs["Type"].default_value)
    except Exception as ex:
        log("WARN compositor skipped:", ex)


# ---------------------------------------------------------------------------------------------------------------
# Render settings, stamp, handler
# ---------------------------------------------------------------------------------------------------------------

def set_engine(sc, engine, samples, stills):
    if engine == "workbench":
        sc.render.engine = "BLENDER_WORKBENCH"
        sh = sc.display.shading
        sh.light = "STUDIO"
        sh.color_type = "MATERIAL"
        sh.show_shadows = True
        return
    sc.render.engine = "BLENDER_EEVEE"
    e = sc.eevee
    e.taa_render_samples = samples
    e.use_raytracing = True
    try:
        e.ray_tracing_options.resolution_scale = "2" if stills else "4"
    except Exception:
        pass
    e.use_shadows = True
    e.shadow_ray_count = 1
    e.shadow_step_count = 6 if stills else 4
    e.shadow_resolution_scale = 1.0 if stills else 0.5
    e.volumetric_tile_size = "8" if stills else "8"
    e.volumetric_samples = 64 if stills else 32
    e.use_volumetric_shadows = True
    e.volumetric_shadow_samples = 16 if stills else 8
    e.volumetric_start = 0.1
    e.volumetric_end = 30.0
    e.bokeh_max_size = 100.0
    e.use_bokeh_jittered = False


def stamp(sc, on, font=12):
    r = sc.render
    r.use_stamp = on
    for k in ("use_stamp_date", "use_stamp_render_time", "use_stamp_filename", "use_stamp_scene",
              "use_stamp_camera", "use_stamp_memory", "use_stamp_hostname", "use_stamp_frame_range",
              "use_stamp_sequencer_strip"):
        setattr(r, k, False)
    r.use_stamp_marker = True
    r.use_stamp_time = True
    r.use_stamp_frame = True
    r.use_stamp_lens = True
    r.use_stamp_note = True
    r.use_stamp_labels = True
    r.stamp_font_size = font
    r.stamp_foreground = (1.0, 0.90, 0.76, 1.0)
    r.stamp_background = (0.0, 0.0, 0.0, 0.55)


def make_note_fn(plan, meta):
    lines = meta["lines"]
    secs = meta.get("sections") or []

    def note(frame):
        t = frame / FPS
        p = next((q for q in plan if q["start_frame"] <= frame <= q["end_frame"]), plan[-1])
        sec = next((s["name"] for s in secs if s["start"] <= t < s["end"]), p["section"])
        parts = [f"PREVIS  {p['key']} {p['title']}", sec]
        ln = next((l for l in lines if l["t0"] - 0.1 <= t <= l["t1"] + 0.1), None)
        if ln:
            parts.append('"%s"' % ln["text"][:48])
        for c in p.get("cues", []):
            if c["t"] - 0.1 <= t <= c["t"] + 1.6:
                parts.append("CUE " + c["label"])
                break
        return "  |  ".join(parts)[:118]
    return note


# ---------------------------------------------------------------------------------------------------------------
# camera_plan.json
# ---------------------------------------------------------------------------------------------------------------

def write_camera_plan(path, plan, meta, args, engine_used):
    scenes = []
    for p in plan:
        move = p["move"]
        notes = {
            "slow_push_in": "Seated on the tan suede couch; slow eye-level push-in, held. Lamp key camera-left, "
                            "candles and a steaming mug in the foreground, fairy lights on the back wall.",
            "handheld_float": "Standing in the same living room; gentle handheld float (noise on the camera) over a "
                              "slow lateral slide. Laptop at camera-right glows with scrolling amber logs; garland.",
            "low_angle_push_in": "Chorus lift: low-angle push-in on the stage, f/1.4 so the fairy-light wall goes to "
                                 "bokeh; amber rim through haze, velvet curtains washed burnt orange.",
            "quarter_orbit": "Harmony: lead at the V apex nearest the lens, two darker-wardrobe clones behind at 45°; "
                             "90° orbit around the trio (in 2D, fake it with a slow drift + zoom).",
        }.get(move, "")
        post = {"from": round(p["post"][0], 3), "to": round(p["post"][1], 3)}
        item = dict(
            id=p.get("id"), key=p["key"], title=p["title"], marker=p["marker"], camera_object=p["camera_object"],
            section=p.get("section", ""),
            start_s=round(p["start_frame"] / FPS, 4), end_s=round((p["end_frame"] + 1) / FPS, 4),
            start_frame=p["start_frame"], end_frame=p["end_frame"],
            cut_downbeat_s=(round(p["t0"], 3) if p["start_frame"] > 0 else None),
            camera_move=move, focal_start_mm=p.get("focal"), focal_end_mm=p.get("focal"),
            dolly_start_m=p["dolly_start_m"], dolly_end_m=p["dolly_end_m"], orbit_deg=p["orbit_deg"],
            slide_m=p["slide_m"], height_m=p["height_m"], tilt_deg=p["tilt_deg"], aperture_fstop=p["aperture_fstop"],
            notes=notes, post_zoom=post,
            hand_cues=p.get("cues", []),
            wardrobe=dict(lead=p.get("wardrobe_lead", ""), backup=p.get("wardrobe_backup", ""),
                          source=p.get("wardrobe_src", "")),
        )
        if move == "handheld_float":
            item["post_pan_x"] = {"from": -0.015, "to": 0.015}
            item["post_shake"] = {"amount_px_at_1280": 3, "period_s": 1.8}
        if move == "quarter_orbit":
            item["post_pan_x"] = {"from": -0.03, "to": 0.03}
        if p.get("post_raw"):
            item["post_from_shots"] = p["post_raw"]
        scenes.append(item)
    doc = dict(
        version=1,
        song=os.path.relpath(args.song, os.path.dirname(os.path.dirname(path))),
        fps=FPS, frames=meta["total_frames"], duration_s=round(meta["total_frames"] / FPS, 3),
        cut_rule="each cut lands 1 frame before its downbeat: start_frame = round(t*24) - 1",
        downbeats_s=[round(d, 3) for d in meta["downbeats"]],
        sources=dict(scene_plan=meta["plan_src"], hand_cues=meta["cue_src"],
                     timing=args.timing or None, shots=args.shots or None, looks=args.looks or None),
        post_zoom_note="post_zoom = scale-and-crop push for the 2D stitch (scale eval=frame + crop, never zoompan); "
                       "it matches the previs dolly ratio dolly_start_m/dolly_end_m for push-ins.",
        render=dict(engine=engine_used, blender=bpy.app.version_string),
        generated_at=datetime.datetime.now().isoformat(timespec="seconds"),
        scenes=scenes,
    )
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    log("wrote", path)


# ---------------------------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------------------------

def render_still(sc, frame, path):
    sc.frame_set(frame)
    cam = None
    for m in sorted(sc.timeline_markers, key=lambda m: m.frame):
        if m.frame <= frame and m.camera:
            cam = m.camera
    if cam:
        sc.camera = cam
    sc.render.filepath = path
    t = time.time()
    bpy.ops.render.render(write_still=True)
    log(f"still frame {frame} -> {path} ({time.time() - t:.1f}s)")


def main():
    args = parse_args()
    log("Blender", bpy.app.version_string)
    sc, plan, meta = build(args)
    engine_used = "BLENDER_EEVEE" if args.engine == "eevee" else "BLENDER_WORKBENCH"

    # save the .blend (paths relative so the file can move with the repo). The per-frame lyric/cue note is a
    # Python handler that lives only in this run, so the saved file carries a static note instead.
    os.makedirs(os.path.dirname(args.out_blend), exist_ok=True)
    set_engine(sc, args.engine, args.samples, stills=False)
    w, h = parse_res(args.res)
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    stamp(sc, True, font=max(11, int(h / 36)))
    sc.render.stamp_note_text = "PREVIS  In the Loop (Devagar)  (lyric + cue burn-in: re-run build_previs.py)"
    try:
        bpy.context.preferences.filepaths.save_version = 0   # no .blend1 backups next to the deliverable
    except Exception:
        pass
    bpy.ops.wm.save_as_mainfile(filepath=args.out_blend, compress=True)
    try:
        bpy.ops.file.make_paths_relative()
        bpy.ops.wm.save_mainfile()
    except Exception as ex:
        log("WARN make_paths_relative:", ex)
    log("saved", args.out_blend)
    write_camera_plan(args.camera_plan, plan, meta, args, engine_used)

    note_fn = make_note_fn(plan, meta)

    def _note_handler(scene, depsgraph=None):
        try:
            scene.render.stamp_note_text = note_fn(scene.frame_current)
        except Exception:
            pass
    bpy.app.handlers.frame_change_pre.append(_note_handler)

    # stills: a clean mid-scene frame per scene
    if not args.skip_stills:
        os.makedirs(args.stills_dir, exist_ok=True)
        set_engine(sc, args.engine, args.stills_samples, stills=True)
        sw, sh = parse_res(args.stills_res)
        sc.render.resolution_x, sc.render.resolution_y = sw, sh
        stamp(sc, False)
        sc.render.image_settings.media_type = "IMAGE"
        sc.render.image_settings.file_format = "PNG"
        sc.render.image_settings.color_mode = "RGB"
        for p in plan:
            mid = (p["start_frame"] + p["end_frame"]) // 2
            render_still(sc, mid, os.path.join(args.stills_dir, p["key"].lower() + ".png"))
        for fr in [int(x) for x in args.extra_stills.split(",") if x.strip()]:
            render_still(sc, fr, os.path.join(args.stills_dir, f"debug_{fr:04d}.png"))

    if args.animatic:
        os.makedirs(os.path.dirname(args.animatic), exist_ok=True)
        set_engine(sc, args.engine, args.samples, stills=False)
        w, h = parse_res(args.res)
        sc.render.resolution_x, sc.render.resolution_y = w, h
        stamp(sc, True, font=max(11, int(h / 36)))
        ims = sc.render.image_settings
        ims.media_type = "VIDEO"
        ims.file_format = "FFMPEG"
        ff = sc.render.ffmpeg
        ff.format = "MPEG4"
        ff.codec = "H264"
        ff.constant_rate_factor = "HIGH"
        ff.ffmpeg_preset = "GOOD"
        ff.gopsize = 24
        ff.audio_codec = "AAC"
        ff.audio_bitrate = 192
        ff.audio_channels = "STEREO"
        ff.audio_mixrate = 48000
        sc.render.filepath = args.animatic
        sc.frame_set(sc.frame_start)
        t = time.time()
        bpy.ops.render.render(animation=True)
        log(f"animatic -> {args.animatic} ({time.time() - t:.1f}s for {sc.frame_end - sc.frame_start + 1} frames)")
    log("done")


if __name__ == "__main__":
    main()
