# Alis-Companion — pre-implementation renderer optimization decision

Original decision: 2026-10-08; updated 2026-10-09. **Status: current technical evidence and G0/G1 runbook, NOT an independent roadmap.** G0 offline harness and G1 resource sampler are implemented and CI-tested; physical G0/G1 qualification remains OPEN. No production shader, compiled release QSB or renderer change is approved by this document. See the [single active Visual/Behavior TODO](../to-do/cloud-bot/ABYSS_WATER_DROPLET_COMPANION.md) for the authoritative checklist.

Source reviewed: Alis-Companion `main@bd5281d4f27a91c611e741d13225b76b3d7dc228`; Alis host `dev@d31e54b9e7444c623f185b4d740344040a39b62a`. Re-pin both immediately before measurement/implementation: prior SHAs are **research snapshots**, not current production or physical-desktop acceptance. This is a technical study subordinate to [the single active Companion TODO](../to-do/cloud-bot/ABYSS_WATER_DROPLET_COMPANION.md); it is **not a second task board**. [Host/API ownership](ALIS_EXTRACTION.md). Alis-Companion owns Aqua/Octo shaders, motions, tests and renderer; Alis owns shared Abyss fields, optional host surfaces, compositor, theme and desktop-wide optimization research.

## Decision and exclusions

**Prefer optimized existing Qt Quick ShaderEffect implicit-volume 3D, with a single actor and existing Blender-authored F-curves.** Do not migrate to standalone 2D sprites, separate 2.5D/3D actors, external Qt Quick 3D/mesh engines, a new render daemon/window, continuous hidden renderers or a new full-screen capture. Do not rewrite all 60 authored Aqua/Octo clips or alter the 5.6-second sequential cast. Portals remain long-distance travel presentation only.

Candidate order: (0) repair measurement / shader A/B oracle; (1) instrument baseline; (2) strict-lossless cost removal in existing shaders; (3) costly Octo paths when profiling warrants; (4) material-only approximation with explicitly approved visual budget if necessary; (5) multi-renderer hybrid **only after measured alternatives fail**. No user-visible quality reduction is allowed to masquerade as strict-lossless.

**2026-10-09 policy update:** the pinned inventory below predates the restored three-level preference. Explicit Quality now activates tier 2, while existing defaults and automatic profiles keep tier 0/1 costs. Fresh baselines must include both characters at tier 2. The oracle retains one fixed warm-up and two strict measured readbacks per item; its original-shader GPU control remains INCONCLUSIVE. Exact receipts and install scope are in the [active visual TODO](../to-do/cloud-bot/ABYSS_WATER_DROPLET_COMPANION.md#three-rendering-tiers-and-fixed-capture-readiness--2026-10-09).

No actual GPU/CPU/PSS/VRAM/frame-time gain has been measured for this research. No hypothetical percentages are baseline results.

## Source-grounded findings and existing optimizations

| Source | Observed implementation | Implication |
| --- | --- | --- |
| `modules/abyss/companion/WaterDropletMaterial.frag` (blob `adead3c2`) | Front interface has analytic path for `abs(pose.x)<0.00001`; pitched bodies use 24/32/40 coarse samples plus 7 refinements for tier 0/1/2. `backInterface` uses 10/18/28 samples plus 3/5/7 refinements. Material also does Fresnel, internal/reflected light, volume attenuation and optional bubble rays (0/18/48 candidates by tier). | Count shader operations, but do **not** assert actual executed work, CPU saving or whole-GPU benefit without GPU capture. Preserve front/back geometry and medium appearance. |
| `modules/abyss/companion/OctoTentacle.frag` (blob `60d7032`) | Front ray tests eight curved tapered sections; `field()` traverses eight segments; `normalAt()` performs 6 field evaluations via finite differences; back tracing can execute up to 12 field steps + 3 refinements. | Potentially intensive *per-fragment* work, but each tentacle has a bounded painted region and tier-0 uniform. Profile actual pixels/overdraw before algebraic or analytic-normal changes. |
| `modules/abyss/companion/OctoTentacles.qml` (blob `a6f7767`) | A four-tentacle octopus body; when Aqua is pulled, two front and two rear tentacles are instantiated by gated Loaders. Clip projections/Bezier controls feed ShaderEffects; UI owns z-order. | Preserve 4 arms, 16 cups, opaque body-facing overlap and 3D coil around Aqua. Avoid new timers and geometry rebuilds for faux-2.5D layers. |
| `modules/abyss/companion/WaterDropletBody.qml` (blob `6be24fc`) | Logical body is 76x92. Torso keeps selected quality, tiny limbs/orbit droplets already use tier 0. Reflection ShaderEffectSource is 76x82, live only while visible/grounded/detailed. Hidden motion gates and software fallback already exist. | No duplicate optimization credit for tier-0 limbs, existing visibility gates or reduced floor capture; an extra offscreen capture would be a possible regression. Source texture dimensions do not bound all driver VRAM allocations. |
| `modules/abyss/companion/AquaPreferences.js` (blob `c34f118`) | Actual user choices performance/quality produce tier 0 or 1; tier-2 shader branch is not presently selected by this policy. | Never attribute hypothetical tier-2 cost to normal production. |
| `modules/abyss/companion/AquaMotion.qml` (blob `f8e31e2`) | Single local loop clock and shared finite progress; visibility/motion eligibility affects animation weight. | Switching whole body instances by Loader or visibility may reset phase/weight; keep one motion owner. |
| `scripts/build-aqua-material-candidate.py` (blob `184e318`) | Existing isolated source candidate skips radius, distance and exponentials for bubbles whose projection is outside the closed ray segment. | Best early low-risk **experiment**, not a proven optimization: compile separately, verify finite/NaN and boundary semantics, image parity and GPU time. |
| `scripts/validate.py` (blob `7af3d22`) | `make test ALIS_ROOT=...` builds Rust and runs selected component/regression tests inside a private host overlay, plus QML format checks. | **It does not automatically dispatch every legacy render-cost, spatial-volume or shader-parity script.** Include explicit opt-in renderer checks in the gate; a skip for missing Wayland is not a GPU PASS. |

**Critical test-oracle gap (BLOCKER 0).** `scripts/aqua-verify-lossless-render.py` compares historical and current *WaterDropletBody.qml* and hashes those QML sources, but stages the current `WaterDropletMaterial.frag.qsb` into the baseline directory (with other current dependencies). Its reference captures do **not** independently pin a historical shader binary, so success cannot certify that a **changed shader** matches the old shader. `--self-control` is a useful capture qualification only. Before shader optimization, implement an isolated source- and binary-pinned dual-material A/B harness; do not weaken the comparator to obtain a PASS. Historical identical-source mismatch remains an explicit possible INCONCLUSIVE outcome.

**Other baseline trap.** A `.frag` text edit does not change the shipped `.frag.qsb` automatically. Candidate source, compiled QSB, build flags, qsb/Qt version and active runtime package must each be hashed/verified; test a deliberate shader perturbation to prove the oracle catches a change. Do not commit source-only changes that leave stale QSB bytes in the installed payload. Qt's QSB packages can contain multiple graphics-backend shader variants; capture backend identity and preserve supported targets.

## Measurement design: isolated Companion and real host

### B0 — pin identity and ensure valid signals

- Record immutable Alis-Companion and compatible Alis host commit SHAs, manifest/payload SHA, compiled QSB hashes, kernel/compositor, Qt + Quickshell, GPU vendor/driver, backend (OpenGL/Vulkan), power profile, scale/DPR, refresh, output topology and display mode. No default-branch or historic Alis source may silently substitute for AlisCompanion.
- A control/measurement harness must demonstrate it can distinguish identical-source PASS from a deliberate known shader perturbation FAIL and a missing/inconclusive graphics capture. A screenshot or fixture only proves its own bounded case, never physical pointer, performance or full-output acceptance.
- GPU timing: collect Qt scene-graph timing (`QSG_RENDER_TIMING=1`) and batching (`QSG_RENDERER_DEBUG=render`) as diagnostic signals; where supported, enable timestamp sampling (`QSG_RHI_PROFILE=1`) and an appropriate GPU profiler/frame capture. Qt RHI GPU timestamps may be unavailable on some backends/drivers and usually describe command buffers, **not** the time spent solely inside one shader. Disentangle by controlled A/B variants; do not equate FPS, GPU utilization %, scene-graph CPU duration, or draw count with Companion GPU milliseconds.
- Measure *two separate costs*: (a) bounded isolated per-actor shader/render workload under identical synthetic motion, plus (b) **incremental end-to-end Alis shell cost** in a compatible live host. The latter must include compositor effects and floor capture, and compare disabled, enabled-but-hidden, and visible across real scene states.

### B1 — reproducible state matrix

Minimum paired scenarios: Companion OFF, configured but invisible, Aqua idle, walk/run, fly/jump, strongly pitched faceplant/backside fall, Aqua on each of four edges/corners, Octo idle, moving tentacles, Octo/Aqua grip-pull and sequential handoff, theme retint, translucency boundaries, quality tier 0 and tier 1, detailed effects toggles, reduced motion, loss of graphics API/software fallback, fullscreen/modal policy, resize/scale/output switch and restart/suspend as real-session gates.

Use deterministic finite motion phases and explicitly qualified clips. For image A/B, capture before/after from independently staged baseline/candidate shader source **and distinct QSB** under identical uniforms/theme, background, resolution, alpha state, orientation and present-frame readiness. Include shader-only captures and a final host composite; preserve transparent pixels and premultiplication. An unchanged PNG does not prove an unchanged algorithm for all phases.

### G1 tooling preflight — read-only resource sampler (2026-10-09)

The repository now owns [`scripts/aqua-g1-resource-sample.py`](../scripts/aqua-g1-resource-sample.py), its [offline parser/identity tests](../scripts/test-aqua-g1-resource-contract.py), [`scripts/aqua-g1-compare.py`](../scripts/aqua-g1-compare.py) and [multi-run comparison tests](../scripts/test-aqua-g1-compare-contract.py). **These scripts do not start, stop, move, show or hide the actor, compositor or any user process.** A future local chatbot must first identify the correct Quickshell PID and manually establish each target state in a compatible Alis host, without changing presentation policy for the sake of tests.

The sampler pins the **current clean Alis-Companion and Alis source revisions**, reads only `/proc/<explicit_pid>/stat`, `status` and (when readable) `smaps_rollup`, requires a process belonging to the user's UID, and rejects PID recycling/mid-sample changes. Sampled values: process CPU ticks converted using OS clock tick rate, CPU usage normalized to one logical core, observed thread count, process RSS and PSS (when supported). Every receipt labels **GPU frame time, scene/compositor GPU time, VRAM, frame p50/p95/p99 and power `NOT_MEASURED`**. The tool is not a driver profiler, GPU benchmark or measurement of total Alis resource consumption. Never attribute all of Quickshell CPU to Companion without paired OFF/hidden/visible evidence.

The comparison tool requires at least **three independent captures per actor state** with the same commit SHAs, role, backend, nominal duration, sampling interval and warmup. It publishes descriptive medians/ranges for CPU and RSS/PSS only. It does *not* pair separate machines, infer statistical significance, provide GPU percentages, assert CPU savings, or validate state truth automatically.

**Example local-chatbot commands after G0 has qualified and the correct PID/state have been confirmed manually:**

```bash
cd /path/to/Alis-Companion
# Select the *actual* stable Quickshell PID; never guess, kill, or restart it.
# Set QS_PID using the local host's explicit process inspection.
# Take at least 3 separate captures with the host holding the stated state.
root="$HOME/.local/state/alis-companion/g1-$(date +%Y%m%d-%H%M%S)"
for i in 1 2 3; do
  # Confirm Aqua is disabled/OFF before each recording.
  python3 scripts/aqua-g1-resource-sample.py --pid "$QS_PID" --role shell \
    --state off --backend opengl --alis-root /path/to/Alis \
    --duration 30 --interval 1 --warmup 5 --output "$root/off-$i"
  # The local chatbot must explicitly enable Aqua and observe idle before recording;
  # NEVER use script timing as a proxy for the confirmed visible state.
  python3 scripts/aqua-g1-resource-sample.py --pid "$QS_PID" --role shell \
    --state aqua_idle --backend opengl --alis-root /path/to/Alis \
    --duration 30 --interval 1 --warmup 5 --output "$root/aqua-idle-$i"
done
python3 scripts/aqua-g1-compare.py --reference-state off --observed-state aqua_idle \
  --receipts "$root"/off-*/result.json "$root"/aqua-idle-*/result.json \
  --output "$root/off-vs-aqua-idle.json"
```

This example is **not** a turnkey state controller: the required OFF→Aqua change is deliberately left to a local, observed UI/IPC sequence and must happen before each corresponding sample. If the state cannot be confirmed or a PID changes, label that pass `INCONCLUSIVE` and do not include it in comparisons. To assess Octo or airborne modes, record separate labeled samples; comparisons on unchanged source establish *workload overhead*, not *optimized candidate performance*. For production claims, G1 additionally requires independently sourced GPU frame-time instrumentation, calibrated A/A drift, whole-shell scene comparison and p95/p99 frame pacing with an actual Wayland/Niri integration check.

### B2 — paired repeatability, reporting and attribution

Warm both variants equivalently; run at least 5 A/B paired repetitions per key motion state (randomized AB/BA order), with enough frames for stable p50/p95/p99 and confidence intervals. Record per-state sample count, median/p95/p99 frame interval and GPU-time signal (with API/scope named), CPU time/wakeups, PSS/RSS, GPU allocation/residency when observable, draw submissions/scenegraph batches, startup-to-first-presented-frame and idle power if measurable. Separate idle/off and visible averages by observed duty cycle, not guesses.

Do not run heavy profiling/logging in the primary timing sample; use a separate instrumented pass. Run negative controls (no actor / static actor, same-renderer A/A) to expose drift and overhead. Record aborted/unsupported metrics as NOT_MEASURED or INCONCLUSIVE, never zero.

## Candidate queue and controlled experiments

| ID | Experiment | Why | Fidelity risk | Gate |
| --- | --- | --- | --- | --- |
| E0 | Build the shader-aware dual-QSB capture + timing harness; verify A/A and deliberate-difference controls | Eliminates false lossless claims | None to production | Mandatory first |
| E1 | Existing bubble-ray segment-gating candidate, from `build-aqua-material-candidate.py` | Small bounded change with visible upstream candidate; avoids part of work for bubbles outside ray interval | Low-to-medium; floating-point boundaries, NaN, divergence, early-outs need tests | Tier-1/effects-on image oracle, GPU timing; reject if not measurably better |
| E2 | Reduce repeat coordinate transforms/uniform-invariant work inside `field()`, `modelPoint()`, `normalAt()`, and reflection paths; inspect optimized shader bytecode first | `field` is called repeatedly in front/back tracing and normals | Medium: reordered float operations may shift normals/pixels | Per-state A/B, angle extremes, silhouette, reflection and strict exact-color oracle |
| E3 | `backInterface` interval/bracketing improvements or mathematically equivalent bounded special cases | Repeated refraction/exiting work, particularly tilted body | High: steps have optical significance and different materials share path | Precise front/back path + near-tangent/high-pitch optical regression; no blind step reduction |
| E4 | Octo shader: share per-fragment curve-segment math or test analytically derived per-segment normals with controlled boundaries | Six finite-difference field probes with eight-segment evaluation are candidates | High: joins/cups/occlusion may shift | Octo close-up + all four rims + grip z-order + α=opaque interior |
| E5 | Specialized shader packages for eye, foot, sphere or volume only **if E1–E4 and profiler warrant** | Uniform-driven branches might block compiler optimization | Medium-high: more QSBs, driver pipelines and first-use hitch | Variant-count, QSB size, compile/pipeline cache, first-frame and package audit |
| E6 | Material-only precomputed lighting/thickness / minimal 2.5D inside existing actor | Optional fallback if proven 3D savings inadequate | Intentionally different pixels for changing pitch/theme | Explicit maintainer approval for a visual budget and measurable end-to-end savings |
| E7 | Dynamic per-animation 2D/2.5D/3D renderer swap | Last resort, not approved for production | Very high; pose/input/reload/VRAM/latency | Separate architecture RFC only if E0–E6 do not achieve the pre-agreed goal |

Do not implement candidates in parallel. Each is separately source-pinned, measured, reviewed and either promoted or rejected before the next. Candidate E1 is **not** automatically the largest hotspot; execution priority after E0 must be revisited using measured E0/B1 evidence.

### Specific hard constraints for source changes

- Do not remove live themed refraction, transparency, glossy cornea, soft reflections, 3D volume through pitch/yaw/roll, optical bubble detail, 4 opaque Octo tentacles/16 cups or handoff/grip behavior simply to improve numbers.
- Any replacement must preserve the public QML/IPC contract, actor bounds, event timing, surface attachment, input passthrough and fallback. The optimization scope must not extend into Alis-owned `AbyssField.frag` by accident.
- Avoid extra ShaderEffectSource/layer captures, sprite atlases, hidden parallel renderers, scene/item proliferation and CPU-side Bezier reconstruction by default. Test driver-specific changes rather than assuming a shorter GLSL shader compiles to fewer instructions.
- Beware `Shape`/fallback shader updates; a lower GPU arithmetic count that increases QML binding/geometry churn is not a win.
- There is no general proof that analytic derivatives of the rounded droplet, tapered tubes or interface distances produce byte-identical pixels. Such candidates are numerical experiments, not pre-approved strict-lossless replacements.

## Acceptance gates before production rollout

**G0 / tooling:** Alis-Companion render oracle captures independently pinned baseline and candidate QSB. Same-source control behaves, deliberate shader difference is detected; binary/source/host/backend identification is complete. Existing source-only regression checks retained.

**G1 / baseline:** Comparable source-pinned Companion and shell measurements exist for idle, ordinary animation and spatial/Octo worst-case; estimate workload fraction and ambient variability. Decide *before candidate measurement* what gain is material relative to instrument noise and resource tradeoffs (an initial signal is >=10% isolated Companion median GPU-time improvement **and** above 2x A/A noise, but do not treat this arbitrary screening target as a verified guarantee).

**G2 / visual:** All required variants/poses are verified with the new shader-aware oracle. Strict-lossless means exact approved oracle equivalence on specified captures and no new visual behavior; if a controlled visual difference is accepted instead, log explicit owner approval and label it **quality/performance tradeoff**, not strict-lossless. Check RGB, alpha, edge, theme, 3D roll, eyes, droplets, caustics, Octo grips and scene composite. INCONCLUSIVE cannot be promoted.

**G3 / engineering:** Targeted source-independent tests, all Alis-Companion validator checks (`make test ALIS_ROOT=/path/to/Alis`; optionally `--require-clean` after commit), and explicit legacy volume/spatial/cast/render-cost tests where available, with real Wayland rather than SKIP. Shader source and binary have correct packaged hashes. No new runtime loader/daemon/material error, p95/p99 hitch, CPU/RAM/VRAM regression beyond measured noise, startup penalty or idle wakeups.

**G4 / desktop:** Maintainer-observed live Niri/Quickshell, four rims/corners, supported shared surfaces, popup/chat/drag/input, dynamic themes, multiple outputs/DPR/hotplug, sleep-resume and fullscreen/permission precedence. A private QML scene proves neither live host interactivity nor desktop-safe Region.

**G5 / decision:** Publish exact baseline and candidate SHA + metrics + raw artifact provenance + acceptance status + gains **for Companion and whole desktop separately**. Promote only if improvement exceeds noise and useful target without regressing more important metrics; otherwise **stop, retain single 3D renderer, and consider Alis's independently owned full-screen/Abyss optimizations**. Maintain a clean rollback to the last known-good Alis-Companion release through the existing immutable release/current-link design; no mandatory install/restart during tests.

## Repository-only verification result — 2026-10-09

- **Verified completed CI on Alis-Companion `main@3937c2e369bf403bc9aa4a2a71f8f05641759e5a`:** [GitHub Actions run 37815092922](https://github.com/llocphann/Alis-Companion/actions/runs/37815092922) **SUCCESS**. The `offline-contracts` job completed Python AST parsing, source/control negative tests, orchestrated fail-closed receipts and the E1 builder's no-overwrite check. The `compile-shaders` job installed Qt 6.4.2 `qsb` and independently compiled/dumped baseline, deliberately mutated negative, and source-only bubble-ray candidate; the negative QSB hash differed from baseline.
- **Actual porting issue observed/fixed:** Ubuntu 24.04 Qt 6.4.2 `qsb` rejects `--qt6` (CI run 37814896189 failed). Tooling now checks `qsb --help` and selects `--glsl '100 es,120,150' --hlsl 50 --msl 12` when `--qt6` is absent. The corrected fallback passed on runner SHA above. A separate local Qt/Quickshell 6.11 + actual Wayland GPU run is still required.
- **Never conflate these results:** offline CI proves syntax, Python control flow and a Qt 6.4 portable compilation check **only**. It does *not* prove active QtQuick GPU shader rendering, visual pixel parity, G1 GPU/CPU/RAM metrics, real desktop input or Alis-Companion package integration. Status stays **G0 hardware qualification OPEN / G1 OPEN / E1 production NOT APPROVED**.
- The one-command local chatbot runner records source hashes, status per stage and a local `sequence.json` receipt; it stops at the first inconclusive proof or candidate color mismatch. Evidence must be read on the exact tested checkout before allowing any shader change. Do not run an indeterminate action twice without inspecting its existing receipt.

**Latest additional CI confirmation (2026-10-09):** [Alis-Companion `main@e0c35447c48333f1ec82e553db7189e91363a036` / workflow run 37815357323](https://github.com/llocphann/Alis-Companion/actions/runs/37815357323) completed both offline jobs **SUCCESS**. Qt 6.4.2 independently compiled and inspected five source-only shader packages: baseline liquid `WaterDropletMaterial.frag`, a deliberately changed negative control, the unshipped bubble-ray candidate, the existing `OctoTentacle.frag` and `WaterDropletContact.frag`. The runner printed the five QSB SHA-256 digests; its negative QSB differs from the baseline. The separate Python contract job also passed. This expands portable source compilation coverage, **not** validated runtime GPU or visual acceptance.

### First local G0 receipt — same-source GPU captures are not yet deterministic (2026-10-09)

The maintainer provided a **bounded `sequence.json` receipt** from Alis-Companion `main@51873887f32a5ac667550a5a9051d1cec0537769`. Its A/A stage compiled the same baseline source SHA-256 (`776cbb3ae2dd97c0fc6b493980c14bbcb6ba153ebf348120ecedcb89c8e6db0d`) and the same binary QSB SHA-256 (`b15d54e8e0e3b54b91986cf5478b4e0f5bd503f1423bfca65c6207320c83d125`) on both sides, **but reported `INCONCLUSIVE_SELF_CONTROL`, exit 2**. The orchestrator stopped before deliberate-negative or E1 A/B tests. This is **NOT evidence that the bubble-ray candidate has any visual mismatch or GPU cost**. The old sequence contains no per-case alpha/diff statistics, so no root cause was proven. A Python Pillow `Image.getdata()` deprecation warning was observed, but it is unrelated to a demonstrated shader mismatch; its API usage has now been replaced.

**Fail-closed instrumentation response, not a hidden tolerance:** G0 script now saves two independent captures **of each of the same baseline and candidate ShaderEffect items** at each of the 12 frozen uniform states (48 images total). Versioned control report schema 3 requires both baseline-repeat and candidate-repeat to be **exact RGBA pixel-identical** before cross-material A/B status may pass. Failure is distinguished as `INCONCLUSIVE_CAPTURE_VARIANCE` (an individual item fluctuated), `INCONCLUSIVE_SELF_CONTROL` (both same-item repeats stable but two A/A instances differ), or `INCONCLUSIVE` for capture/readiness errors. All pixel channels and alpha remain compared without tolerance, including transparent RGB. Reported per-case counts, bounds, largest channel difference and repeat-image hashes are also summarized in the private `sequence.json` (top five cases), making future single-file result uploads diagnosable.

**CI qualification for this diagnostic change:** [GitHub Actions run 37816467318](https://github.com/llocphann/Alis-Companion/actions/runs/37816467318) succeeded on Alis-Companion `main@143abcfca0ad07499bb7b83af393d718e4e5de7f` with Python AST, strict-RGBA/unstable-repeat fixture checks, sequence summary and mock stop logic, and Qt 6.4.2 shader compilation. **This is not proof that the previously inconclusive Wayland A/A now passes.** The report schema change intentionally invalidates older A/A/negative controls; do **not** reuse stale reports.

**Next local chatbot action when ready:** run a fresh `scripts/aqua-shader-ab-sequence.py` invocation on its then-current clean, SHA-pinned Alis-Companion checkout, inspect `sequence.json` first, and if still inconclusive inspect `aa/result.json` and the recorded local PNG pairs. Do not discard differences as GPU nondeterminism without confirming same-item repeats, and do not skip A/A to force E1. Production shader remains unchanged.

## G0 implementation checkpoint and manual GPU qualification — 2026-10-08

**Current implementation:** `scripts/aqua-shader-ab.py` is committed as an **isolated, independently compiled** two-`.qsb` ShaderEffect pixel comparison harness, with `scripts/test-aqua-shader-ab-contract.py` integrated into `scripts/validate.py`. It does **not** touch the product `.frag`, shipped `.qsb`, Blender animation, loader, installed release, or Alis host. Unit/offline classification and fail-closed checks are possible without a GPU. **G0 is NOT YET QUALIFIED** until actual A/A and deliberately altered A/B captures succeed on the maintainer's real Wayland + Qt/Quickshell + `qsb` session.

The harness stages two different QSB locations in one temporary, private Quickshell window; each is produced with `qsb --qt6` from an explicitly hashed shader source. It samples 12 material/pose/theme combinations, compares unmodified RGBA pixels, checks for non-empty baseline captures and records QSB hashes, qsb version, backend/Wayland handle, Qt logs and failure categories. It requires a prior **qualifying A/A** result before negative testing, and requires **both** qualifying A/A and deliberately different-shader control results before real candidate comparisons. Comparison is **shader-only** (not full WaterDropletBody composition, no 60-clip claim, no live compositor input proof). A successful single QML capture is not a frame-time benchmark.

### One-command local chatbot qualification (preferred)

The future local chatbot can run the **single, read-only-to-production** command below from a clean Alis-Companion checkout after activating a supported Wayland session. It runs A/A → deliberate negative → independently baked bubble-ray candidate A/B, stops at the first invalid stage, and keeps evidence private. It neither installs a release nor changes the Alis-Companion runtime.

```bash
python3 scripts/aqua-shader-ab-sequence.py \
  --output "$HOME/.local/state/alis-companion/shader-ab/$(date +%Y%m%d-%H%M%S)" \
  --graphics opengl --diagnostics
```

Review `sequence.json` and per-stage `result.json` before accepting any conclusions. A nonzero exit is **not** permission to weaken image checks or run the candidate in production. Exit 1 denotes observed candidate pixel differences; exit 2 denotes an inconclusive stage; exit 0 proves only the captured shader samples are equal **with both controls qualified**. Graphics API mismatch, lack of frames queued for presenting, empty alpha, identical images across deliberately changed poses/theme, stale controls and missing dependencies fail closed. Actual whole-session/GPU performance remains G1, not part of this G0 sequence.

### Exact interactive GPU qualification commands

Run from a current clean Alis-Companion checkout with `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, `qs`, `qsb`, `dbus-run-session` and Python Pillow available. Prefer Alis-Companion's ordinary source checkout, *not* an installed runtime or the historical Alis Companion copy. Use a new evidence directory for each invocation; the command never overwrites existing captures.

```bash
set -euo pipefail
run="$HOME/.local/state/alis-companion/shader-ab/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$run"
python3 scripts/test-aqua-shader-ab-contract.py
python3 scripts/aqua-shader-ab.py --mode self --output "$run/aa" --diagnostics
python3 scripts/aqua-shader-ab.py --mode negative \
    --control-report "$run/aa/result.json" --output "$run/negative"
python3 scripts/build-aqua-material-candidate.py "$run/bubble-candidate.frag"
python3 scripts/aqua-shader-ab.py --mode compare \
    --candidate-source "$run/bubble-candidate.frag" \
    --control-report "$run/aa/result.json" \
    --negative-report "$run/negative/result.json" \
    --output "$run/ab"
printf 'Shader A/B reports: %s\n' "$run"
```

**Interpret reports literally.** `PASS_SAME_SOURCE` is required for A/A; `PASS_DIFFERENCE_DETECTED` is required for the deliberately perturbed shader. `PASS_PIXEL_EQUAL` on a candidate applies **only** to the tested 12 shader samples, on that backend/compiler build. `FAIL_PIXEL_DIFFERENCE` means a deviation was detected; it is *not* a runtime crash. Any `INCONCLUSIVE` (missing QSB variants, differing compiler outputs on A/A, capture timeout, missing Wayland, empty image, dependency error, source drift or invalid control report) blocks promotion. `capture.log` and `result.json` are private/local diagnostic data and should not be published unreviewed.

The built-in negative mutation changes the final body-fragment output only to demonstrate visible pixel detection. `--diagnostics` records `QSG_RENDER_TIMING`, `QSG_RHI_PROFILE`, `QSG_RENDERER_DEBUG=render` and `QSG_INFO` diagnostics separately from later repeatable timing experiments. GPU timestamps may be unsupported; Qt timings are not isolated per-material shader time. The `elapsed_capture_wall_seconds_not_gpu_time` report field **must never** be interpreted as a GPU speedup.

**Next after G0 is proven:** obtain a pinned, repeatable G1 baseline in the real Alis-Companion-enabled Alis host (OFF / hidden / idle / moving / pitched / Octo grip, p50/p95/p99 GPU frame, CPU, PSS/VRAM when observable). Only then investigate E1 bubble-ray's candidate as a performance optimization. A doc-only write, contract test pass or synthetic pixel capture does not itself satisfy G1 or real Niri acceptance.

## Tooling and references

- Alis-Companion build and isolated tests: [Makefile](../Makefile), [scripts/validate.py](../scripts/validate.py), [scripts/aqua-verify-lossless-render.py](../scripts/aqua-verify-lossless-render.py), [scripts/build-aqua-material-candidate.py](../scripts/build-aqua-material-candidate.py), [scripts/test-companion-volume.py](../scripts/test-companion-volume.py), [scripts/test-companion-cast.py](../scripts/test-companion-cast.py), [scripts/test-aqua-spatial-volume.py](../scripts/test-aqua-spatial-volume.py), [scripts/test-aqua-render-cost-contract.py](../scripts/test-aqua-render-cost-contract.py).
- Qt documentation: [ShaderEffect QSB and uniform semantics](https://doc.qt.io/qt-6/qml-qtquick-shadereffect.html); [QSB baking and variant inspection](https://doc.qt.io/qt-6/qtshadertools-qsb.html); [scene-graph batching, timing and debug](https://doc.qt.io/qt-6/qtquick-visualcanvas-scenegraph-renderer.html); [GPU RHI timestamp prerequisites](https://doc.qt.io/qt-6/qquickgraphicsconfiguration.html); [ShaderEffectSource memory/performance](https://doc.qt.io/qt-6/qml-qtquick-shadereffectsource.html).
- The [Alis strict-lossless audit](https://github.com/llocphann/Alis/blob/dev/docs/optimization/STRICT_LOSSLESS_GPU_RAM_CPU_AUDIT.md) already has a profile-first Aqua/Octo caution (historical R32.5); future **Companion** findings belong here/Alis-Companion, while full-desktop shared render-graph findings stay in that audit.

**Next authorized work if implementation is requested:** implement only G0/B0 dual-QSB controls and baseline instrumentation in test tooling. No production shader, QML actor, feature, or Alis host change until that evidence is available.
