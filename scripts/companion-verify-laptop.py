#!/usr/bin/env python3
"""Reopen staged 3D laptop assets; verify actual geometry, then optional preview.

Use the cached bpy 4.3 authoring environment. The original desktop rigs are
read-only and no model/user vault/desktop content is used.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]


def linear(keys, t):
    for a, b in zip(keys, keys[1:]):
        if t <= b[0]:
            return a[1] + (b[1]-a[1])*(t-a[0])/(b[0]-a[0])
    return keys[-1][1]


def set_phase(rig, character, clips, name, phase):
    rig.animation_data.action = bpy.data.actions[character + " — " + name]
    frame = 1 + clips[name]["duration"] * .06 * phase
    bpy.context.scene.frame_set(math.floor(frame), subframe=frame % 1)
    bpy.context.view_layer.update()


def tip(obj):
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    value = evaluated.matrix_world @ (sum((v.co for v in mesh.vertices[-16:]), Vector())/16)
    evaluated.to_mesh_clear()
    return value


def studio():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 20
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x = 384
    scene.render.resolution_y = 384
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    scene.camera.location = (62, -205, 83)
    scene.camera.data.ortho_scale = 138
    scene.camera.rotation_euler = (Vector((0, -12, 33)) - scene.camera.location).to_track_quat("-Z", "Y").to_euler()
    for obj in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for location, energy, size, color in [((-55, -70, 110), 85000, 75, (.65, .91, 1)),
                                         ((65, -20, 80), 55000, 55, (.5, .75, 1)),
                                         ((5, 60, 100), 90000, 70, (.35, .65, 1))]:
        bpy.ops.object.light_add(type="AREA", location=location)
        light = bpy.context.object
        light.data.energy, light.data.size, light.data.color = energy, size, color
        light.rotation_euler = (Vector((0, 0, 25)) - light.location).to_track_quat("-Z", "Y").to_euler()
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.08, .15, .25, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .7
    # A bounded reflective water disc is for visual inspection only.
    mat = bpy.data.materials.new("Preview water contact")
    mat.use_nodes = True
    node = mat.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = (.008, .065, .12, 1)
    node.inputs["Metallic"].default_value = .6
    node.inputs["Roughness"].default_value = .14
    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=60, depth=.6, location=(0, -8, -1))
    bpy.context.object.data.materials.append(mat)


def verify(bundle, output, render):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    receipt = {"schema": 1, "blender": bpy.app.version_string,
               "stagedOnly": True, "physicalAcceptance": "OPEN", "characters": {}}
    authored = json.loads((bundle / "authoring.json").read_text())
    for character in ("Aqua", "Octo"):
        blend = bundle / (character + "Laptop.blend")
        curves = bundle / (character + "LaptopMotion.json")
        clips = json.loads(curves.read_text())
        assert hashlib.sha256(blend.read_bytes()).hexdigest() == authored["characters"][character]["blendSha256"]
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        rig = bpy.data.objects[character + " Motion Controls"]
        body = bpy.data.objects["Liquid body" if character == "Aqua" else "Octo liquid head"]
        assert len(bpy.data.actions) == 38
        original = ROOT / authored["characters"][character]["source"]
        assert hashlib.sha256(original.read_bytes()).hexdigest() == authored["characters"][character]["sourceSha256"]
        error, samples = 0, 0
        for name, clip in clips.items():
            action = bpy.data.actions[character + " — " + name]
            for channel, keys in clip["tracks"].items():
                f = next(f for f in action.fcurves if f.data_path == '["' + channel + '"]')
                assert len(f.keyframe_points) == len(keys)
                assert all(k.interpolation == "LINEAR" for k in f.keyframe_points)
                for i in range(101):
                    t = i/100
                    error = max(error, abs(f.evaluate(1+t*clip["duration"]*.06)-linear(keys, t)))
                    samples += 1
        assert error < .0001
        prop = [o for o in bpy.data.objects if o.get("alis_companion_original_prop")]
        assert len(prop) == 29
        set_phase(rig, character, clips, "laptop_open", 0)
        assert all(o.hide_render for o in prop)
        set_phase(rig, character, clips, "laptop_open", 1)
        assert not any(o.hide_render for o in prop)
        hinge = bpy.data.objects["Laptop hinge"]
        assert abs(hinge.rotation_euler.x + math.radians(106)) < .0001
        if character == "Aqua":
            hands = [bpy.data.objects[side + " water arm"] for side in ("Left", "Right")]
            assert len([o for o in bpy.data.objects if " water foot" in o.name]) == 2
            assert len([o for o in bpy.data.objects if " water arm" in o.name]) == 2
            positions = [o.matrix_world.translation.copy() for o in hands]
            assert all(-48 < p.y < -34 and 7 < p.z < 16 for p in positions), positions
            set_phase(rig, character, clips, "laptop_typing_loop", .12)
            travel = (hands[0].matrix_world.translation - positions[0]).length
            still = (hands[1].matrix_world.translation - positions[1]).length
        else:
            arms = [bpy.data.objects[f"Tentacle {i+1}"] for i in range(4)]
            cups = [o for o in bpy.data.objects if o.name.startswith("Suction cup ")]
            assert len(arms) == 4 and len(cups) == 16
            assert not any(" water foot" in o.name or " water arm" in o.name for o in bpy.data.objects)
            positions = [tip(o) for o in arms[:2]]
            assert all(-50 < p.y < -34 and 4 < p.z < 14 for p in positions), positions
            cup_before = cups[0].matrix_world.translation.copy()
            set_phase(rig, character, clips, "laptop_typing_loop", .12)
            travel = (tip(arms[0]) - positions[0]).length
            still = (tip(arms[1]) - positions[1]).length
            assert (cups[0].matrix_world.translation-cup_before).length > .1
            node = next(n for n in arms[0].data.materials[0].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
            assert node.inputs["Transmission Weight"].default_value < .051
            assert node.inputs["Alpha"].default_value == 1
        assert travel > 2.9 and still < .001, (travel, still)
        assert abs(body.matrix_world.determinant() - 1) < .00001
        # Same parent rotates the actual geometry and prop on all four rims.
        set_phase(rig, character, clips, "laptop_typing_loop", 0)
        points = [bpy.data.objects["Laptop base"].matrix_world.translation.copy(), body.matrix_world.translation.copy()]
        rim_errors = []
        for angle in (0, 90, 180, 270):
            rig.rotation_euler.y = math.radians(angle)
            bpy.context.view_layer.update()
            expected = Matrix.Rotation(math.radians(angle), 4, "Y")
            for obj, start in zip((bpy.data.objects["Laptop base"], body), points):
                rim_errors.append((obj.matrix_world.translation - expected @ start).length)
        rig.rotation_euler.y = 0
        bpy.context.view_layer.update()
        assert max(rim_errors) < .0001
        set_phase(rig, character, clips, "laptop_close", 1)
        assert all(o.hide_render for o in prop)
        assert abs(hinge.rotation_euler.x) < .00001
        info = dict(animations=8, preservedActions=30, verifiedSamples=samples,
                    maximumCurveError=error, keyboardPositions=[list(p) for p in positions],
                    alternatingTapTravel=travel, otherHandTravel=still, maximumRimGeometryError=max(rim_errors),
                    blendSha256=hashlib.sha256(blend.read_bytes()).hexdigest(),
                    curveSha256=hashlib.sha256(curves.read_bytes()).hexdigest())
        if render:
            studio()
            schedule = [("laptop_open", .55), ("laptop_typing_loop", 0),
                        ("laptop_typing_loop", .12), ("laptop_thinking_loop", .3), ("laptop_close", .65)]
            info["captures"] = []
            for index, (name, phase) in enumerate(schedule):
                set_phase(rig, character, clips, name, phase)
                image = output / f"{character.lower()}-{index}-{name}.png"
                bpy.context.scene.render.filepath = str(image)
                bpy.ops.render.render(write_still=True)
                info["captures"].append(dict(file=image.name, clip=name, phase=phase,
                    sha256=hashlib.sha256(image.read_bytes()).hexdigest()))
        receipt["characters"][character] = info
    (output / "blender-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("COMPANION_LAPTOP_BLENDER_PASS " + json.dumps(receipt, separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "assets/cowork")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    verify(args.bundle.resolve(), args.output.resolve(), args.render)
