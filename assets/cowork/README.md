# Original 3D laptop staging assets

These two editable Blender 4.3 scenes add eight **new** laptop performances per
character to copies of the original 30-action rigs. The shipped Aqua/Octo scenes,
60 motion exports and liquid shaders remain unchanged. The installer excludes
this directory; no desktop context source or live actor imports this bundle.

`AquaLaptopMotion.json` and `OctoLaptopMotion.json` contain the actual stored
LINEAR F-curve keys, not rendered sprites or a substitute `working` expression.
The shared phases are open, typing loop, thinking loop, agent loop, pause, close,
success and alert. Aqua reaches with exactly two hands; Octo reaches with two
of its four opaque tentacles, including their moving suction cups. A real hinge,
rounded shell, keyboard, trackpad and abstract screen form the original prop.
The preview-only 3D iris/catchlights follow the existing eye-closing drivers.

Re-author into a **new** output directory using the existing authoring cache:

```sh
uv run --offline --python 3.11 --with bpy==4.3.0 --with 'numpy<2' python scripts/companion-author-laptop.py --output /new/owned/bundle
node scripts/test-aqua-laptop-motion.cjs
uv run --offline --python 3.11 --with bpy==4.3.0 --with 'numpy<2' python scripts/companion-verify-laptop.py --output /new/owned/proof --render
```

The verifier reopens both saved scenes, checks exported interpolation, alternating
keyboard motion, unchanged actor volume, four-rim parent transforms, opaque
Octo tentacles and complete prop removal after close. A fixed five-pose render
schedule supports visual inspection. The reflective studio disc is a preview
surface, not a desktop effect. Four-rim geometry is **not** Niri/input acceptance.

Runtime mesh/material integration, theme binding, interrupted motion ownership,
real four-rim visuals/input and GPU costs remain open in the canonical
[Companion TODO](../../to-do/cloud-bot/ABYSS_WATER_DROPLET_COMPANION.md). These
assets do not qualify original G0, strict lossless rendering, resource savings
or reference parity. No private model, desktop screenshot or vault is a fixture.

`CoworkPerformance.js` stages the presentation policy with the existing Behavior
Director and a caller-owned monotonic clock. Opening, looping, close and optional
bounded success/alert cues use epoch tokens. Drag/chat/modal/travel, cast change,
lock/fullscreen, off/quiet, reduced motion, unsupported placement and clock failure
release the prop immediately. Brief context changes use a one-second exit grace;
an interrupted opening reverses its current authored pose. Late or early finished
signals cannot restart an intro. Loop switches blend poses for 140 ms.

This library is a **synthetic staging contract**, not the live actor or an
authorization API. Only the permitted host owner may supply semantics and cues;
AI text cannot call it. No desktop event subscription, permanent timer, inference
or new window is created. Runtime integration still needs one explicit actor
owner and physical acceptance; the library does not authorize rollout.

## Private native 3D preview

The two scripts below export the original staged scene geometry and eight
performances per actor, then display them in one Qt Quick 3D `Loader3D` in an
owned compositor. No installed actor, user setting, model, vault or desktop hook
is touched. The generated meshes, morphs, normals and parent transforms remain
three-dimensional; no sprites or planar falling poses are substituted.

```sh
uv run --offline --python 3.11 --with bpy==4.3.0 --with 'numpy<2' python scripts/companion-export-cowork-native.py --output /new/owned/export
python3 scripts/test-aqua-cowork-native.py --bundle /new/owned/export --output /new/owned/proof --alis-root /clean/Alis
```

The fixed 40-frame schedule checks all paired clips, independent hand/tentacle
motion and cup attachment, four parent rotations, four palettes, opaque Octo
limbs, closed-prop removal and a fully unloaded actor when off. Generated material colors follow the supplied
palette; native transmission is a **prototype mapping**. An original neutral
studio probe supplies reflections. The installed Qt `ProceduralMesh` and
`Timeline` types avoid needing Assimp or downloading a system dependency.

This authoring preview bakes at 60 Hz. It does not replace the exact stored
F-curves, the live SDF renderer or its quality tiers, and cannot establish
lossless parity, concept approval, live theme/input behavior or resource costs.
Buffers/morph arrays and text keyframes are staging data, not an optimized
shipping format. The renderer/event-owner integration remains open.

Add `--video` to the proof command to retain two short native 3D review movies
and their original PNG frames. The movies are review artifacts; they are not
sprite assets, a GPU timing measurement or lossless-renderer evidence.

Add `--performance` to also connect the dormant controller to the single native
actor using fixed synthetic events and one caller-owned clock. The 48 additional
frames cover opening, typing/agent/thinking switches, alert, drag release, stale
callbacks, reversal of a partly opened lid, close and off for both characters.
Native transforms and morph weights crossfade from a captured pose; quaternion
rotation uses the shortest arc. No extra animation clock is introduced. Checks
run after painting and inspect actual positions, scales, rotations and weights
at transition boundaries. Their numeric pose bound is a staging geometry check,
not a G0 pixel tolerance or proof of lossless rendering.

This option supplies no real desktop event bridge, permission flow, live actor
integration or cost measurement. It changes only generated private previews.

## Abyss optical study

Export with `--smooth-bubbles`, then capture with `--optics` to add eight fixed
blue/amber/purple/green poses. `--performance` can be combined with it. The
unsaved Blender export smooths only the original orbital bubble normals; it
never changes the saved scenes, mesh shape or authored keys. The proof checks
the actual radial normals of all sixteen bubbles.

The optional `abyss` material profile uses a smooth reflective coat, bounded
transmission with IOR/volume attenuation, a separate smaller 3D inner core,
dark glossy irises and theme-linked emission. Core geometry is shared with the
original body and follows its parent; it is hidden in the studio profile and
unloads with the single actor. Octo limbs retain alpha 1 and zero transmission.
The lighting study reduces overexposure and keeps the theme hue across layers.

This is an optical **proposal**, not Blender/Cycles parity, a fluid simulation,
concept approval or the installed liquid shader. Extra core/material costs are
unmeasured. Do not ship it or infer GPU/resource savings from private captures.

Optional `--concept-face` on the Blender exporter raises and enlarges both
glossy eye volumes, with wider pupils that leave a smaller lower iris crescent.
Delta transforms preserve the original eye-closing driver; all four 3D eye
layers retain the eye as their parent. The optical proof adds one closed-blink
pose per character and inspects actual transforms/parenting. This unsaved study
does not change the original `.blend` files or claim concept approval.

Optional `--smooth-eyes` smooths the two outer glossy eye meshes in the unsaved
export. An offline comparison with a prior export using the same other options
checks exact oriented Float32 surface/UV/morph data, materials, parents and all
animation keys despite glTF vertex merging. Only outer eye normals may differ:
`python3 scripts/test-aqua-cowork-native.py --bundle /new/smooth-export --reference-bundle /preserved/face-export --output /new/owned/audit`.
This command opens no compositor and establishes no native visual, shipping,
concept, G0/G1 or resource acceptance.

## Native water contact proposal

Add `--contact` to the native proof command to create a dormant 3D water receiver
and three shared torus ripples in each generated actor. A fixed radial mesh
fades the receiver to zero alpha at its outer edge. Its normal, reflection
receiver box and mirrored camera follow the supporting rim. Only subject
geometry casts into the local probe; only the water receives it. The probe uses
512 × 512 cube faces and captures on the caller's explicit request, with no
permanent timer or `EveryFrame` mode. This option requires Qt Quick 3D 6.12.

The 22 additional fixed captures include both characters, four rims, four
palettes, advancing wave phase, reflection off, an empty-probe control and
complete actor unload. The control keeps the visible body, camera and surface
unchanged while disabling subject reflection casting. A fixed water-region
paint check requires an actual body contribution; probe flags alone do not
qualify reflection. It can be combined with `--optics --performance`.

This remains a private native material/geometry study. It is not a shipping
planar-reflection provider, input/occlusion acceptance, concept parity, fluid
simulation or a G0 lossless comparison. Cube-map artifacts and optical fidelity
still need visual review; surface/probe/shadow costs are unmeasured. No installed
package, saved Blender asset, production shader or user preference is changed.
