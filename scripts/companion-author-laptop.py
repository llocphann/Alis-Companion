#!/usr/bin/env python3
"""Stage original Blender laptop performances; never modify the shipped rigs.

Use the existing authoring environment:
uv run --offline --python 3.11 --with bpy==4.3.0 --with 'numpy<2' \
    python scripts/companion-author-laptop.py --output /new/owned/directory

The bundle is a design/verification asset, not an installed desktop feature.
No imported artwork, model inference, desktop event source or second actor.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
CHARACTERS = ("Aqua", "Octo")
LOOPS = ("laptop_typing_loop", "laptop_thinking_loop", "laptop_agent_loop", "laptop_pause")


def constant(value):
    return [[0, value], [1, value]]


def definitions(character):
    # Units match the original rig: Blender Z is up, Y is negative depth.
    seated = {"pitch": 6, "eyeOpen": 1, "laptopVisible": 1,
              "laptopHinge": 106, "laptopSlide": 0, "laptopLift": 0,
              "screenPulse": .55}
    if character == "Aqua":
        seated.update(arm0X=20, arm1X=-20, arm0Y=-32, arm1Y=-32, arm0Z=-14, arm1Z=-14)
    else:
        seated.update(laptopReach0=1, laptopReach1=1, laptopTap0=0, laptopTap1=0)
    rest = {key: 1 if key == "eyeOpen" else 0 for key in seated}
    rest["screenPulse"] = .55
    intro = {key: [[0, rest[key]], [.23, rest[key]], [.76, value], [1, value]]
             for key, value in seated.items()}
    intro.update(laptopVisible=[[0, 0], [.12, 0], [.13, 1], [1, 1]],
                 laptopHinge=[[0, 0], [.30, 0], [.73, 109], [.88, 104], [1, 106]],
                 laptopSlide=[[0, 18], [.13, 18], [.58, 0], [1, 0]],
                 laptopLift=[[0, 0], [.24, 5], [.58, 0], [1, 0]],
                 eyeOpen=[[0, 1], [.30, 1], [.35, .12], [.41, 1], [1, 1]])
    outro = {key: [[0, value], [.35, value], [.9, rest[key]], [1, rest[key]]]
             for key, value in seated.items()}
    outro.update(laptopHinge=[[0, 106], [.18, 106], [.62, 0], [1, 0]],
                 laptopSlide=[[0, 0], [.62, 0], [.92, 18], [1, 18]],
                 laptopVisible=[[0, 1], [.92, 1], [.93, 0], [1, 0]])
    durations = dict(laptop_open=1450, laptop_typing_loop=1800,
                     laptop_thinking_loop=3200, laptop_agent_loop=2400,
                     laptop_pause=1800, laptop_close=1200,
                     laptop_success=1100, laptop_alert=1200)
    result = {name: {"duration": duration, "loop": name in LOOPS,
                     "tracks": {key: constant(value) for key, value in seated.items()}}
              for name, duration in durations.items()}
    result["laptop_open"]["tracks"] = intro
    result["laptop_close"]["tracks"] = outro
    typing = result["laptop_typing_loop"]["tracks"]
    agent = result["laptop_agent_loop"]["tracks"]
    # Real independent hands/tentacles strike the keyboard, never a flat scale.
    for i in range(2):
        if character == "Aqua":
            channel, low, high = f"arm{i}Z", -14, -11
        else:
            channel, low, high = f"laptopTap{i}", 0, 3
        typing[channel] = [[0, low], [.12, high if i == 0 else low], [.24, low],
                           [.38, high if i else low], [.50, low],
                           [.62, high if i == 0 else low], [.74, low],
                           [.88, high if i else low], [1, low]]
        agent[channel] = [[0, low], [.20, high if i == 0 else low], [.30, low],
                          [.64, high if i else low], [.75, low], [1, low]]
    thinking = result["laptop_thinking_loop"]["tracks"]
    thinking.update(yaw=[[0, 0], [.25, -7], [.52, 4], [.8, 0], [1, 0]],
                    eyeOpen=[[0, 1], [.28, 1], [.32, .12], [.36, 1], [1, 1]])
    result["laptop_pause"]["tracks"]["eyeOpen"] = [[0, 1], [.62, 1], [.66, .18], [.7, 1], [1, 1]]
    result["laptop_success"]["tracks"].update(
        roll=[[0, 0], [.24, -4], [.5, 4], [.74, -2], [1, 0]],
        screenPulse=[[0, .55], [.26, 1], [.65, .8], [1, .55]])
    result["laptop_alert"]["tracks"].update(
        yaw=[[0, 0], [.25, -9], [.50, 9], [.75, -4], [1, 0]],
        eyeOpen=[[0, 1], [.20, .3], [.36, 1], [1, 1]],
        screenPulse=[[0, .55], [.25, 1], [.50, .55], [.75, 1], [1, .55]])
    return result


def shader_material(name, color, metal=0, roughness=.2, emission=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*color, 1)
    node = mat.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = (*color, 1)
    node.inputs["Metallic"].default_value = metal
    node.inputs["Roughness"].default_value = roughness
    node.inputs["Coat Weight"].default_value = .35
    node.inputs["Emission Color"].default_value = (*color, 1)
    node.inputs["Emission Strength"].default_value = emission
    return mat


def drive(obj, prop, axis, rig, channel, base=0, gain=1):
    f = obj.driver_add(prop, axis) if axis is not None else obj.driver_add(prop)
    var = f.driver.variables.new()
    var.name = "control"
    var.targets[0].id = rig
    var.targets[0].data_path = '["' + channel + '"]'
    f.driver.expression = f"{base!r}+control*{gain!r}"
    return f


def box(name, size, location, material, parent, bevel=.6):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.parent = parent
    obj.location = location
    obj.data.materials.append(material)
    if bevel:
        modifier = obj.modifiers.new("Rounded machined edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 3
        obj.modifiers.new("Weighted surface normals", "WEIGHTED_NORMAL")
    obj["alis_companion_original_prop"] = True
    return obj


def laptop(rig):
    metal = shader_material("Laptop Abyss anodized shell", (.015, .08, .13), .62, .23)
    dark = shader_material("Laptop dark glass", (.005, .018, .025), .15, .14)
    accent = shader_material("Laptop theme accent", (.03, .66, .85), .2, .19, .5)
    root = bpy.data.objects.new("Laptop root", None)
    bpy.context.scene.collection.objects.link(root)
    root.parent = rig
    root.location.y = -46
    drive(root, "location", 1, rig, "laptopSlide", -46)
    drive(root, "location", 2, rig, "laptopLift")
    box("Laptop base", (44, 24, 3), (0, 0, 5), metal, root, 1)
    hinge = bpy.data.objects.new("Laptop hinge", None)
    bpy.context.scene.collection.objects.link(hinge)
    hinge.parent = root
    hinge.location = (0, 12, 7.8)
    drive(hinge, "rotation_euler", 0, rig, "laptopHinge", gain=-math.pi / 180)
    box("Laptop lid", (44, 16, 2.2), (0, -8, 0), metal, hinge, .95)
    box("Laptop screen", (39.6, 12, .35), (0, -8, 1.25), dark, hinge, .55)
    # Abstract light strips are original geometry, not editor content/text.
    for i, width in enumerate((13, 21, 16)):
        box(f"Laptop screen light {i+1}", (width, .7, .15),
            (-5 + width / 5, -4 - i * 2.7, 1.47), accent, hinge, .08)
    box("Laptop back crystal", (5.3, 5.3, .38), (0, -8, -1.3), accent, hinge, .8).rotation_euler.z = math.pi / 4
    for row in range(3):
        for col in range(7):
            box(f"Laptop key {row+1}.{col+1}", (3.9, 2.25, .5),
                (-14.4 + col * 4.8, -1 + row * 3.2, 6.75), dark, root, .35)
    box("Laptop trackpad", (12, 4.3, .2), (0, -8, 6.6), dark, root, .4)
    objects = [obj for obj in bpy.data.objects if obj.get("alis_companion_original_prop")]
    for obj in objects:
        f = drive(obj, "hide_render", None, rig, "laptopVisible")
        f.driver.expression = "control < 0.5"
        f = drive(obj, "hide_viewport", None, rig, "laptopVisible")
        f.driver.expression = "control < 0.5"
    node = accent.node_tree.nodes.get("Principled BSDF")
    f = node.inputs["Emission Strength"].driver_add("default_value")
    var = f.driver.variables.new()
    var.name = "light"
    var.targets[0].id = rig
    var.targets[0].data_path = '["screenPulse"]'
    f.driver.expression = "0.3+light*0.6"
    return objects


def staged_face(character):
    # The desktop already has an expressive face; the older source scenes have
    # plain black eye meshes. Add original 3D iris/highlights for this preview,
    # parented to each eye so the authored eyeOpen driver still closes it.
    iris = shader_material("Preview cyan iris", (.025, .26, .67), .18, .09, .18)
    white = shader_material("Preview eye catchlight", (.85, .97, 1), .05, .07, .35)
    dark = bpy.data.materials["Glossy eyes"]
    for i, eye in enumerate(o for o in bpy.data.objects if o.name.startswith("Glossy eye")):
        for name, location, scale, mat in [
            ("iris", (0, -.84, 0), (.72, .42, .78), iris),
            ("pupil", (0, -1.12, .1), (.39, .23, .46), dark),
            ("main catchlight", (-.26, -1.27, .36), (.19, .07, .17), white),
            ("small catchlight", (.22, -1.23, -.22), (.09, .05, .085), white),
        ]:
            bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12)
            obj = bpy.context.object
            obj.name = f"Preview eye {i+1} {name}"
            obj.parent = eye
            obj.location, obj.scale = location, scale
            obj.data.materials.append(mat)
            for polygon in obj.data.polygons:
                polygon.use_smooth = True
    if character == "Aqua":
        curve = bpy.data.curves.new("Preview Aqua smile", "CURVE")
        curve.dimensions, curve.bevel_depth, curve.bevel_resolution = "3D", .42, 4
        spline = curve.splines.new("BEZIER")
        spline.bezier_points.add(2)
        for point, co in zip(spline.bezier_points, [(-3, -35, -8.5), (0, -36, -10), (3, -35, -8.5)]):
            point.co = co
            point.handle_left_type = point.handle_right_type = "AUTO"
        smile = bpy.data.objects.new("Preview Aqua smile", curve)
        bpy.context.scene.collection.objects.link(smile)
        smile.parent = bpy.data.objects["Liquid body"]
        smile.data.materials.append(dark)


def bezier(points, t):
    weights = ((1-t)**3, 3*(1-t)**2*t, 3*(1-t)*t*t, t**3)
    return Vector(tuple(sum(p[k] * w for p, w in zip(points, weights)) for k in range(3)))


def octo_keyboard(rig):
    # Only the two front tentacles reach; exactly four remain in the scene.
    for i in (0, 1):
        obj = bpy.data.objects[f"Tentacle {i+1}"]
        a = (i + .5) * math.tau / 4
        x, z = math.cos(a), math.sin(a)
        rest = [(10*x, -10, 10*z), (21*x, -23, 21*z),
                (34*x, -20, 26*z), (31*x, -13, 27*z)]
        sign = 1 if i == 0 else -1
        keyboard = [(10*x, -10, 10*z), (sign*19, -18, 20),
                    (sign*16, -27, 38), (sign*14, -27, 42)]
        for channel in ("Reach", "Tap"):
            key = obj.shape_key_add(name="Laptop" + channel)
            key.slider_max = 4
            for j in range(25):
                t = j / 24
                delta = bezier(keyboard, t) - bezier(rest, t) if channel == "Reach" else Vector((0, t*t, 0))
                for k in range(16):
                    v = key.data[j*16+k].co
                    v.x += delta.x
                    v.y -= delta.z
                    v.z += delta.y
            f = key.driver_add("value")
            var = f.driver.variables.new()
            var.name = "value"
            var.targets[0].id = rig
            var.targets[0].data_path = f'["laptop{channel}{i}"]'
            f.driver.expression = "value"
        for n, t in enumerate((.32, .47, .62, .77)):
            cup = bpy.data.objects[f"Suction cup {i+1}.{n+1}"]
            delta = bezier(keyboard, t) - bezier(rest, t)
            for axis, native_axis, gain in ((0, 0, 1), (1, 2, -1), (2, 1, 1)):
                f = next(f for f in cup.animation_data.drivers if f.data_path == "location" and f.array_index == axis)
                for name, channel in (("reachLaptop", "Reach"), ("tapLaptop", "Tap")):
                    var = f.driver.variables.new()
                    var.name = name
                    var.targets[0].id = rig
                    var.targets[0].data_path = f'["laptop{channel}{i}"]'
                f.driver.expression += f"+reachLaptop*{delta[native_axis]*gain!r}"
                if native_axis == 1:
                    f.driver.expression += f"+tapLaptop*{t*t!r}"


def export(rig, character, clips):
    old_channels = {f.data_path[2:-2] for action in bpy.data.actions for f in action.fcurves
                    if f.data_path.startswith('["')}
    channels = old_channels | {channel for clip in clips.values() for channel in clip["tracks"]}
    for channel in channels:
        if channel not in rig:
            rig[channel] = 0.0
    exported = {}
    for name, clip in clips.items():
        action = bpy.data.actions.new(character + " — " + name)
        action.use_fake_user = True
        rig.animation_data.action = action
        frames = clip["duration"] * .06
        for channel in sorted(channels):
            neutral = 1 if channel.startswith("scale") or channel.endswith("Curl") or channel == "eyeOpen" else 0
            for t, value in clip["tracks"].get(channel, constant(neutral)):
                rig[channel] = float(value)
                rig.keyframe_insert(data_path='["' + channel + '"]', frame=1+t*frames, group=name)
        tracks = {}
        for f in action.fcurves:
            for key in f.keyframe_points:
                key.interpolation = "LINEAR"
            channel = f.data_path[2:-2]
            if channel in clip["tracks"]:
                tracks[channel] = [[float(key.co.x-1)/frames, float(key.co.y)] for key in f.keyframe_points]
        exported[name] = dict(duration=clip["duration"], loop=clip["loop"], tracks=tracks)
    return exported


def author(output):
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": 1, "blender": bpy.app.version_string, "stagedOnly": True, "characters": {}}
    for character in CHARACTERS:
        source = ROOT / "assets" / character.lower() / (character + "Motion.blend")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        bpy.ops.wm.open_mainfile(filepath=str(source))
        bpy.context.preferences.filepaths.save_version = 0
        rig = bpy.data.objects[character + " Motion Controls"]
        assert len(bpy.data.actions) == 30
        if character == "Aqua":
            for i, side in enumerate(("Left", "Right")):
                drive(bpy.data.objects[side + " water arm"], "location", 1, rig, f"arm{i}Y", -8)
        else:
            octo_keyboard(rig)
        staged_face(character)
        prop = laptop(rig)
        clips = export(rig, character, definitions(character))
        rig.animation_data.action = bpy.data.actions[character + " — laptop_typing_loop"]
        bpy.context.scene.frame_set(1)
        bpy.context.scene.frame_end = 1 + round(clips["laptop_typing_loop"]["duration"] * .06)
        blend = output / (character + "Laptop.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        curves = output / (character + "LaptopMotion.json")
        curves.write_text(json.dumps(clips, sort_keys=True, separators=(",", ":")) + "\n")
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        receipt["characters"][character] = dict(source=str(source.relative_to(ROOT)),
            sourceSha256=digest, blendSha256=hashlib.sha256(blend.read_bytes()).hexdigest(),
            curvesSha256=hashlib.sha256(curves.read_bytes()).hexdigest(),
            oldClips=30, newClips=8, propMeshes=len(prop))
    (output / "authoring.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("COMPANION_LAPTOP_AUTHORED " + json.dumps(receipt, separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    author(parser.parse_args().output.resolve())
