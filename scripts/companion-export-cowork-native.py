#!/usr/bin/env python3
"""Export the two original staged Blender scenes for a private native preview.

This is an offline authoring step, not an installer. Requires the existing bpy
4.3 authoring environment. All eight performances are baked at 60 Hz; this
preview sampling is not a strict lossless replacement for the original curves.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def environment(output):
    """Original neutral studio lights; no desktop capture or borrowed texture."""
    width, height = 256, 128
    pixels = np.zeros((height, width, 4), dtype=np.float32)
    pixels[..., :3] = (.07, .09, .13)
    pixels[..., 3] = 1
    for x, y, w, h, color in [(34, 18, 25, 54, (5, 6, 7)),
                              (162, 22, 13, 49, (2, 3, 5)),
                              (220, 12, 29, 28, (3, 3, 4)),
                              (85, 35, 9, 44, (.6, .8, 1.2))]:
        pixels[y:y+h, x:x+w, :3] = color
    image = bpy.data.images.new("Original cowork preview studio", width, height, float_buffer=True)
    image.pixels.foreach_set(pixels.ravel())
    image.file_format = "HDR"
    image.filepath_raw = str(output / "studio.hdr")
    image.save()


def export(output, smooth_bubbles=False, concept_face=False, smooth_eyes=False):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    authored = json.loads((ROOT / "assets/cowork/authoring.json").read_text())
    receipt = {"schema": 1, "stagedOnly": True, "blender": bpy.app.version_string,
               "bakedHz": 60, "lossless": "NOT_CLAIMED", "smoothBubbleNormals":smooth_bubbles,
               "conceptFace":concept_face, "smoothEyeNormals":smooth_eyes,"characters": {}}
    for character in ("Aqua", "Octo"):
        blend = ROOT / "assets/cowork" / (character + "Laptop.blend")
        digest = hashlib.sha256(blend.read_bytes()).hexdigest()
        assert digest == authored["characters"][character]["blendSha256"]
        clips = json.loads((blend.parent / (character + "LaptopMotion.json")).read_text())
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        scene = bpy.context.scene
        rig = bpy.data.objects[character + " Motion Controls"]
        scene.render.fps = 60
        if concept_face:
            # Delta transforms retain the authored eyeOpen driver and all
            # original keys. Children inherit the same blink and eye volume.
            eyes=[o for o in scene.objects if o.name.startswith("Glossy eye")]
            assert len(eyes)==2
            for eye in eyes:
                eye.delta_scale=(1.4,1.4,1.4)
                eye.delta_location.z=5
            for obj in scene.objects:
                if obj.name.startswith("Preview eye ") and obj.name.endswith(" pupil"):
                    obj.scale=(.58,.23,.64)
                    obj.location.z=.17
        # glTF has no object-visibility channel. Keep the prop geometry in every
        # exported clip; the native fixture's one propVisible gate removes it.
        # Removing these drivers changes this unsaved staging copy only.
        for obj in scene.objects:
            if smooth_bubbles and obj.type=="MESH" and obj.name.startswith("Orbital bubble "):
                for polygon in obj.data.polygons:polygon.use_smooth=True
            if smooth_eyes and obj.type=="MESH" and obj.name.startswith("Glossy eye"):
                for polygon in obj.data.polygons:polygon.use_smooth=True
            if obj.get("alis_companion_original_prop"):
                obj.driver_remove("hide_render")
                obj.driver_remove("hide_viewport")
                obj.hide_render = obj.hide_viewport = False
        # Static original smile geometry retains its parent and local transform.
        for obj in list(scene.objects):
            if obj.type == "CURVE":
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.convert(target="MESH")
        target = output / character.lower()
        target.mkdir()
        scene_receipt = {"blendSha256": digest, "clips": {}}
        for name, clip in clips.items():
            rig.animation_data.action = bpy.data.actions[character + " — " + name]
            scene.frame_start = 1
            frames = clip["duration"] * .06
            assert frames == round(frames)
            scene.frame_end = 1 + round(frames)
            scene.frame_set(1)
            bpy.context.view_layer.update()
            bpy.ops.object.select_all(action="DESELECT")
            for obj in scene.objects:
                if obj.type in ("MESH", "EMPTY"):
                    obj.select_set(True)
            path = target / (name + ".gltf")
            bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLTF_SEPARATE",
                use_selection=True, use_visible=False, use_renderable=False,
                export_animations=True, export_animation_mode="SCENE",
                export_anim_scene_split_object=False, export_bake_animation=True,
                export_force_sampling=True, export_frame_range=True,
                export_optimize_animation_keep_anim_object=True, export_apply=False,
                export_cameras=False, export_lights=False,
                export_draco_mesh_compression_enable=False)
            data = json.loads(path.read_text())
            assert len(data["animations"]) == 1
            scene_receipt["clips"][name] = {
                "duration": clip["duration"], "nodes": len(data["nodes"]),
                "meshes": len(data["meshes"]), "channels": len(data["animations"][0]["channels"]),
                "gltfSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "binSha256": hashlib.sha256(path.with_suffix(".bin").read_bytes()).hexdigest()}
        assert hashlib.sha256(blend.read_bytes()).hexdigest() == digest
        receipt["characters"][character.lower()] = scene_receipt
    environment(output)
    (output / "export.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("COMPANION_NATIVE_EXPORT_PASS " + json.dumps(receipt, separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smooth-bubbles",action="store_true",help="smooth normals in the unsaved native staging copy only")
    parser.add_argument("--concept-face",action="store_true",help="larger raised glossy eyes in the unsaved staging copy; retain authored blinking")
    parser.add_argument("--smooth-eyes",action="store_true",help="smooth outer eye normals in the unsaved staging copy only; retain mesh surface and blink keys")
    args=parser.parse_args()
    export(args.output.resolve(),args.smooth_bubbles,args.concept_face,args.smooth_eyes)
