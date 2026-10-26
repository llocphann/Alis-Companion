#!/usr/bin/env python3
"""Isolated Alis-Companion liquid shader A/A, deliberate negative and A/B captures.

Bakes two independently named QSBs; never edits an installed runtime. GPU
captures require a Wayland Qt/Quickshell/qsb environment and are not FPS tests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / 'modules/abyss/companion/WaterDropletMaterial.frag'
CAPTURE_SIZE = [304, 304]
NEGATIVE_TARGET = 'fragColor=(vec4(film(color)*alpha,alpha)+vec4(pow(hue,vec3(1.0/2.2))*halo,halo))*qt_Opacity;'
NEGATIVE_REPLACEMENT = 'fragColor=vec4(0.95,0.05,0.05,1.0)*qt_Opacity;'
BAD_LOG = ('TypeError:', 'ReferenceError:', 'Binding loop', 'Unable to assign',
           'ShaderEffect: Failed', 'Failed to compile shader', 'Failed to create pipeline')

# Raw ShaderEffect uniform fields match the existing WaterDropletMaterial.frag.
# No independent actor, timeline, shape path, secondary Qt window or IPC.
QML = '''//@ pragma UseQApplication
//@ pragma Env ALIS_STANDALONE_WINDOW=1
import QtQuick
import QtQuick.Window
import Quickshell
Window {
    id: root
    width: 320; height: 320; visible: true; color: "transparent"
    property int currentIndex: 0
    property bool candidateTurn: false
    property int repeatPass: -1 // one retained warm-up, then two measured grabs
    property int readyTicks: 0
    property int framesSinceSwitch: 0
    property bool pending: false
    function advanceCapture() {
        root.readyTicks = 0; root.pending = false
        if (root.repeatPass < 1) { ++root.repeatPass; return }
        root.repeatPass = -1
        if (!root.candidateTurn) { root.candidateTurn = true; return }
        ++root.currentIndex
        if (root.currentIndex === root.cases.length) {
            console.log("ALIS_COMPANION_SHADER_AB_CAPTURE_OK:" + root.cases.length)
            Qt.quit(); return
        }
        root.candidateTurn = false
    }
    onCandidateTurnChanged: { root.framesSinceSwitch = 0; root.readyTicks = 0 }
    onFrameSwapped: root.framesSinceSwitch++
    onSceneGraphError: (error, message) => {
        console.log("ALIS_COMPANION_SHADER_AB_ERROR:scene_graph")
        Qt.exit(6)
    }
    Item {
        id: graphicsProbe
        width: 0; height: 0
        readonly property int api: GraphicsInfo.api
    }
    property var cases: CASES
    readonly property var sample: cases[Math.min(currentIndex, cases.length - 1)]
    readonly property color accentColor: Qt.rgba(sample.accent[0], sample.accent[1], sample.accent[2], 1)
    readonly property color specularColor: Qt.rgba(0.85, 0.94, 1, 1)
    readonly property vector4d animationUniform: Qt.vector4d(sample.shimmer, sample.tip, sample.pulse, sample.effects)
    readonly property vector4d opticsUniform: Qt.vector4d(sample.yaw, sample.variant, sample.gazeX, sample.gazeY)
    readonly property vector4d tierUniform: Qt.vector4d(sample.tier, sample.translucency, 0, 0)
    readonly property vector4d poseUniform: Qt.vector4d(sample.pitch, sample.roll, sample.squash, sample.stretch)
    ShaderEffect {
        id: baseline
        x: 8; y: 8; width: 304; height: 304
        visible: !root.candidateTurn
        property color accent: root.accentColor
        property color specular: root.specularColor
        property vector4d motion: root.animationUniform
        property vector4d optics: root.opticsUniform
        property vector4d rendering: root.tierUniform
        property vector4d pose: root.poseUniform
        fragmentShader: Qt.resolvedUrl("baseline/WaterDropletMaterial.frag.qsb")
    }
    ShaderEffect {
        id: candidate
        x: 8; y: 8; width: 304; height: 304
        visible: root.candidateTurn
        property color accent: root.accentColor
        property color specular: root.specularColor
        property vector4d motion: root.animationUniform
        property vector4d optics: root.opticsUniform
        property vector4d rendering: root.tierUniform
        property vector4d pose: root.poseUniform
        fragmentShader: Qt.resolvedUrl("candidate/WaterDropletMaterial.frag.qsb")
    }
    Timer {
        running: true; repeat: true; interval: 80
        onTriggered: {
            if (root.pending || root.framesSinceSwitch < 1) return
            const expectedApi = Quickshell.env("ALIS_COMPANION_SHADER_AB_GRAPHICS") === "vulkan" ? GraphicsInfo.Vulkan : GraphicsInfo.OpenGL
            if (graphicsProbe.api !== expectedApi) {
                console.log("ALIS_COMPANION_SHADER_AB_ERROR:graphics_api_mismatch:" + graphicsProbe.api)
                Qt.exit(5); return
            }
            const active = root.candidateTurn ? candidate : baseline
            if (active.status === ShaderEffect.Error) {
                console.log("ALIS_COMPANION_SHADER_AB_ERROR:shader_status:" + active.log)
                Qt.exit(3); return
            }
            if (++root.readyTicks < 4) return
            root.pending = true
            const tag = root.candidateTurn ? "candidate" : "baseline"
            const suffix = root.repeatPass < 0 ? "-warmup" : root.repeatPass === 1 ? "-repeat" : ""
            const capturedIndex = root.currentIndex
            active.grabToImage(function(result) {
                const path = Quickshell.env("ALIS_COMPANION_SHADER_AB_OUT") + "/" + tag + "-" + capturedIndex + suffix + ".png"
                if (!result.saveToFile(path)) {
                    console.log("ALIS_COMPANION_SHADER_AB_ERROR:save_failed")
                    Qt.exit(4); return
                }
                root.advanceCapture()
            }, Qt.size(304, 304))
        }
    }
}
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def qsb_flags(executable):
    # Qt 6.4 qsb predates --qt6; produce its equivalent shader variants.
    help_result = subprocess.run([executable, '--help'], capture_output=True, text=True, timeout=10)
    if help_result.returncode:
        raise RuntimeError('qsb_help_unavailable')
    text = help_result.stdout + help_result.stderr
    if '--qt6' in text:
        return ['--qt6']
    if all(option in text for option in ('--glsl', '--hlsl', '--msl')):
        return ['--glsl', '100 es,120,150', '--hlsl', '50', '--msl', '12']
    raise RuntimeError('qsb_missing_required_shader_targets')


def run_checked(command, timeout=30):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('command_failed:' + command[0] + ':exit=' + str(result.returncode)
                           + '\n' + (result.stderr + result.stdout)[-1000:])
    return result.stdout.strip() or result.stderr.strip()


def cases():
    # Shape branches, pose derivatives, tiers, and body material.
    states = [
        ('aqua_idle', 0, 0, 0, 0, 0, 0),
        ('aqua_yaw', 0, 1, .35, 0, 0, 0),
        ('aqua_faceplant', 0, 1, -.3, 1.15, .35, 0),
        ('aqua_back', 0, 0, 3.14, 2.4, -.8, 0),
        ('aqua_tip', 0, 1, .5, .22, -.19, .4),
        ('octo_head', 4, 1, -.45, .7, 0, 0),
        ('cornea', 2, 1, 0, 0, 0, 0),
        ('limb', 1, 0, .35, .55, 0, 0),
        ('foot', 3, 0, 0, 0, 0, 0),
        ('aqua_low_detail', 0, 0, -.25, 0, 0, 0),
        ('aqua_high_detail', 0, 2, .35, 0, 0, 0),
        ('octo_low_detail', 4, 0, -.45, .7, 0, 0),
        ('octo_high_detail', 4, 2, -.45, .7, 0, 0),
    ]
    samples = []
    for name, variant, tier, yaw, pitch, roll, tip in states:
        samples.append(dict(name=name, variant=variant, tier=tier, yaw=yaw,
                            pitch=pitch, roll=roll, tip=tip, shimmer=.3,
                            pulse=.5, effects=1, gazeX=.1, gazeY=-.2,
                            translucency=.16, squash=0, stretch=0,
                            accent=[.05,.59,.84]))
    samples += [dict(samples[0], name='aqua_no_effects', effects=0),
                dict(samples[0], name='aqua_theme_shift', accent=[.8,.13,.44], translucency=.32)]
    return samples


def capture_timeout():
    # Owned nested compositors can pace static-item grabs at about one second.
    # Predeclare a bounded budget for every warm-up + two measured images on
    # both items; this is a capture deadline, never a performance threshold.
    return max(75, len(cases()) * 2 * 3 * 2)


def negative_variant(src):
    if src.count(NEGATIVE_TARGET) != 1:
        raise RuntimeError('negative_control_anchor_not_unique')
    return src.replace(NEGATIVE_TARGET, NEGATIVE_REPLACEMENT)


def rgba_difference(before, after):
    """Exact RGBA bytes, including color values in transparent pixels."""
    if before.size != tuple(CAPTURE_SIZE) or after.size != tuple(CAPTURE_SIZE):
        raise RuntimeError('unexpected_png_dimensions')
    raw_a, raw_b = before.tobytes(), after.tobytes()
    delta = bytes(abs(x - y) for x, y in zip(raw_a, raw_b))
    differing = [index for index in range(0, len(delta), 4)
                 if delta[index] or delta[index + 1] or delta[index + 2] or delta[index + 3]]
    xs = [index // 4 % CAPTURE_SIZE[0] for index in differing]
    ys = [index // 4 // CAPTURE_SIZE[0] for index in differing]
    return dict(changed_pixels=len(differing),
                alpha_changed_pixels=sum(delta[index + 3] != 0 for index in range(0, len(delta), 4)),
                max_channel_delta=max(delta, default=0),
                difference_bbox=([min(xs), min(ys), max(xs) + 1, max(ys) + 1] if differing else None))


def compare_pngs(output, samples):
    from PIL import Image
    results = []
    for index, sample in enumerate(samples):
        images = {}
        for label, suffix in (('baseline', '-warmup'), ('baseline', ''), ('baseline', '-repeat'),
                              ('candidate', '-warmup'), ('candidate', ''), ('candidate', '-repeat')):
            with Image.open(output / ('%s-%d%s.png' % (label, index, suffix))) as file:
                if file.size != tuple(CAPTURE_SIZE):
                    raise RuntimeError('unexpected_png_dimensions')
                images[label + suffix] = file.convert('RGBA')
        before, after = images['baseline'], images['candidate']
        if before.getchannel('A').getextrema()[1] == 0:
            raise RuntimeError('empty_alpha_baseline_capture:' + sample['name'])
        if after.getchannel('A').getextrema()[1] == 0:
            raise RuntimeError('empty_alpha_candidate_capture:' + sample['name'])
        b_repeat = rgba_difference(before, images['baseline-repeat'])
        c_repeat = rgba_difference(after, images['candidate-repeat'])
        cross = rgba_difference(before, after)
        results.append(dict(name=sample['name'],
                            **cross,
                            baseline_repeat_changed_pixels=b_repeat['changed_pixels'],
                            candidate_repeat_changed_pixels=c_repeat['changed_pixels'],
                            baseline_repeat_max_channel_delta=b_repeat['max_channel_delta'],
                            candidate_repeat_max_channel_delta=c_repeat['max_channel_delta'],
                            baseline_repeat_bbox=b_repeat['difference_bbox'],
                            candidate_repeat_bbox=c_repeat['difference_bbox'],
                            baseline_warmup_rgba_sha256=digest(images['baseline-warmup'].tobytes()),
                            candidate_warmup_rgba_sha256=digest(images['candidate-warmup'].tobytes()),
                            baseline_rgba_sha256=digest(before.tobytes()),
                            baseline_repeat_rgba_sha256=digest(images['baseline-repeat'].tobytes()),
                            candidate_rgba_sha256=digest(after.tobytes()),
                            candidate_repeat_rgba_sha256=digest(images['candidate-repeat'].tobytes())))
    baselines = {r['name']: r['baseline_rgba_sha256'] for r in results}
    for name in ('aqua_faceplant', 'octo_head', 'aqua_theme_shift'):
        if baselines[name] == baselines['aqua_idle']:
            raise RuntimeError('shader_baseline_not_responsive:' + name)
    return results


def classify(mode, results):
    if not results or any('baseline_repeat_changed_pixels' not in x or
                          'candidate_repeat_changed_pixels' not in x for x in results):
        return ('INCONCLUSIVE_CAPTURE_DIAGNOSTICS_MISSING', 2)
    if any(x['baseline_repeat_changed_pixels'] > 0 or
           x['candidate_repeat_changed_pixels'] > 0 for x in results):
        return ('INCONCLUSIVE_CAPTURE_VARIANCE', 2)
    differences = sum(x['changed_pixels'] for x in results)
    if mode == 'self':
        return ('PASS_SAME_SOURCE' if differences == 0 else 'INCONCLUSIVE_SELF_CONTROL', 0 if differences == 0 else 2)
    if mode == 'negative':
        return ('PASS_DIFFERENCE_DETECTED' if differences > 0 else 'FAIL_NEGATIVE_UNDETECTED', 0 if differences > 0 else 3)
    return ('PASS_PIXEL_EQUAL' if differences == 0 else 'FAIL_PIXEL_DIFFERENCE', 0 if differences == 0 else 1)


def capture_contract_sha():
    fixture = QML.replace('CASES', json.dumps(cases(), sort_keys=True, separators=(',', ':')))
    return digest(fixture.encode('utf-8'))


def qualified_control(report, baseline_sha, baseline_qsb, qsb_version, graphics):
    comparisons = report.get('comparison', [])
    return (report.get('status') == 'PASS_SAME_SOURCE'
            and report.get('mode') == 'self'
            and report.get('schema') == 3
            and report.get('candidate_source_sha256') == baseline_sha
            and report.get('candidate_qsb_sha256') == baseline_qsb
            and report.get('contract_sha256') == capture_contract_sha()
            and len(comparisons) == len(cases())
            and all(x.get('changed_pixels') == 0
                    and x.get('baseline_repeat_changed_pixels') == 0
                    and x.get('candidate_repeat_changed_pixels') == 0
                    for x in comparisons)
            and report.get('baseline_source_sha256') == baseline_sha
            and report.get('baseline_qsb_sha256') == baseline_qsb
            and report.get('qsb_version') == qsb_version
            and report.get('graphics') == graphics)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('self', 'negative', 'compare'), required=True)
    parser.add_argument('--baseline-source', type=Path, default=PRODUCTION)
    parser.add_argument('--candidate-source', type=Path)
    parser.add_argument('--control-report', type=Path)
    parser.add_argument('--negative-report', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='new, private local directory, never overwritten')
    parser.add_argument('--graphics', choices=('opengl', 'vulkan'), default='opengl')
    parser.add_argument('--diagnostics', action='store_true', help='capture Qt scenegraph logs separately; not a standalone GPU benchmark')
    args = parser.parse_args()
    if args.mode == 'compare' and (not args.candidate_source or not args.control_report or not args.negative_report):
        parser.error('compare requires --candidate-source, --control-report and --negative-report')
    if args.mode == 'negative' and (args.candidate_source or not args.control_report or args.negative_report):
        parser.error('negative requires --control-report only (no --candidate-source)')
    if args.mode == 'self' and (args.candidate_source or args.control_report or args.negative_report):
        parser.error('self mode generates its own identical source and accepts no control reports')
    if not os.environ.get('WAYLAND_DISPLAY') or not os.environ.get('XDG_RUNTIME_DIR'):
        parser.error('real Wayland session required; no false GPU PASS on software/offscreen')
    try:
        from PIL import Image  # noqa: F401 - fail before allocating the evidence directory
    except ImportError:
        parser.error('Python Pillow is required for RGBA validation')
    quickshell = shutil.which('qs') or shutil.which('quickshell')
    qsb = shutil.which('qsb')
    if not qsb and Path('/usr/lib/qt6/bin/qsb').is_file():
        qsb = '/usr/lib/qt6/bin/qsb'
    if not quickshell or not qsb or not shutil.which('dbus-run-session'):
        parser.error('Quickshell, Qt qsb and dbus-run-session are required')
    baseline_source = args.baseline_source.resolve()
    bsrc = baseline_source.read_bytes()
    csrc = (args.candidate_source.resolve().read_bytes() if args.mode == 'compare'
            else negative_variant(bsrc.decode('utf-8')).encode('utf-8') if args.mode == 'negative'
            else bsrc)
    if args.mode != 'self' and bsrc == csrc:
        parser.error('candidate source must differ from baseline source')
    output = args.output.resolve()
    if output.exists():
        parser.error('--output must not exist; refusing to overwrite evidence')
    qsb_version = run_checked([qsb, '--version'])
    selected_flags = qsb_flags(qsb)
    graphics = dict(backend=args.graphics, wayland_display=os.environ['WAYLAND_DISPLAY'],
                    qt_qpa_platform='wayland', qsb_flags=selected_flags)
    started = time.monotonic()
    output.mkdir(mode=0o700, parents=True)
    report = dict(mode=args.mode, schema=3, status='INCONCLUSIVE', contract_sha256=capture_contract_sha(),
                  baseline_source_sha256=digest(bsrc), candidate_source_sha256=digest(csrc),
                  qsb_version=qsb_version, qsb_binary=qsb, graphics=graphics, cases=len(cases()),
                  result_scope='ShaderEffect pixel captures only; no whole-shell GPU time or FPS',
                  diagnostics_enabled=args.diagnostics,
                  capture_deadline_seconds=capture_timeout(),
                  capture_schedule=dict(warmup_per_item=1, measured_per_item=2,
                                        warmup_retained=True, selection='fixed_before_measurement'))
    try:
        with tempfile.TemporaryDirectory(prefix='alis-companion-shader-ab-') as temporary:
            stage = Path(temporary)
            for label, source in (('baseline', bsrc), ('candidate', csrc)):
                folder = stage / label
                folder.mkdir(mode=0o700)
                src = folder / 'WaterDropletMaterial.frag'
                src.write_bytes(source)
                target = folder / 'WaterDropletMaterial.frag.qsb'
                run_checked([qsb, *selected_flags, '-o', str(target), str(src)])
                report[label + '_qsb_sha256'] = digest(target.read_bytes())
                dump = run_checked([qsb, '-d', str(target)])
                if not dump.strip() or 'fragment' not in dump.lower():
                    raise RuntimeError('qsb_dump_missing_fragment_stage:' + label)
                report[label + '_qsb_inspection_sha256'] = digest(dump.encode('utf-8'))
            if args.mode == 'self' and report['baseline_qsb_sha256'] != report['candidate_qsb_sha256']:
                raise RuntimeError('same_source_qsb_not_deterministic')
            if args.mode != 'self':
                control = json.loads(args.control_report.read_text(encoding='utf-8'))
                if not qualified_control(control, report['baseline_source_sha256'],
                                         report['baseline_qsb_sha256'], qsb_version, graphics):
                    raise RuntimeError('control_report_does_not_qualify_current_baseline')
            if args.mode == 'compare':
                neg = json.loads(args.negative_report.read_text(encoding='utf-8'))
                expected_negative_sha = digest(negative_variant(bsrc.decode('utf-8')).encode('utf-8'))
                if (neg.get('status') != 'PASS_DIFFERENCE_DETECTED'
                        or neg.get('mode') != 'negative'
                        or neg.get('schema') != 3
                        or neg.get('contract_sha256') != capture_contract_sha()
                        or neg.get('candidate_source_sha256') != expected_negative_sha
                        or len(neg.get('comparison', [])) != len(cases())
                        or not any(x.get('changed_pixels', 0) > 0 for x in neg.get('comparison', []))
                        or not all(x.get('baseline_repeat_changed_pixels') == 0
                                   and x.get('candidate_repeat_changed_pixels') == 0
                                   for x in neg.get('comparison', []))
                        or neg.get('baseline_source_sha256') != report['baseline_source_sha256']
                        or neg.get('baseline_qsb_sha256') != report['baseline_qsb_sha256']
                        or neg.get('qsb_version') != qsb_version
                        or neg.get('graphics') != graphics):
                    raise RuntimeError('negative_report_does_not_qualify_current_baseline')
            stage.joinpath('shell.qml').write_text(QML.replace('CASES', json.dumps(cases(), sort_keys=True, separators=(',', ':'))), encoding='utf-8')
            env = dict(os.environ)
            for key in ('DISPLAY', 'NIRI_SOCKET', 'ALIS_COMPANIOND',
                        'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QS_CONFIG_PATH', 'QS_CONFIG_NAME'):
                env.pop(key, None)
            for sub in ('config','cache','data','state'):
                (stage / sub).mkdir(mode=0o700)
                env['XDG_' + sub.upper() + '_HOME'] = str(stage / sub)
            env.update(QT_QPA_PLATFORM='wayland', QSG_RHI_BACKEND=args.graphics,
                       QT_QUICK_BACKEND='rhi', ALIS_COMPANION_SHADER_AB_OUT=str(output),
                       ALIS_COMPANION_SHADER_AB_GRAPHICS=args.graphics,
                       QS_NO_RELOAD_POPUP='1')
            if args.diagnostics:
                env.update(QSG_RENDER_TIMING='1', QSG_RHI_PROFILE='1', QSG_RENDERER_DEBUG='render', QSG_INFO='1')
            with (output / 'capture.log').open('x', encoding='utf-8') as log:
                process = subprocess.Popen(['dbus-run-session', '--', quickshell, '--path', str(stage / 'shell.qml')],
                                           cwd=stage, env=env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    try:
                        rc = process.wait(timeout=capture_timeout())
                    except subprocess.TimeoutExpired:
                        rc = 124
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=5)
            log_data = (output / 'capture.log').read_text(encoding='utf-8', errors='replace')
            if rc != 0 or 'ALIS_COMPANION_SHADER_AB_CAPTURE_OK:' + str(len(cases())) not in log_data:
                raise RuntimeError('qml_capture_failed:exit=%d' % rc)
            if any(x in log_data for x in BAD_LOG):
                raise RuntimeError('qml_shader_or_binding_error')
            if digest(baseline_source.read_bytes()) != report['baseline_source_sha256']:
                raise RuntimeError('baseline_source_changed_during_capture')
            if args.mode == 'compare' and digest(args.candidate_source.resolve().read_bytes()) != report['candidate_source_sha256']:
                raise RuntimeError('candidate_source_changed_during_capture')
            report['comparison'] = compare_pngs(output, cases())
            report['status'], exit_code = classify(args.mode, report['comparison'])
            report['elapsed_capture_wall_seconds_not_gpu_time'] = round(time.monotonic() - started, 3)
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as error:
        report['status'] = 'INCONCLUSIVE'
        report['error'] = str(error)[:450]
        exit_code = 2
    (output / 'result.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('ALIS_COMPANION_SHADER_AB_' + report['status'] + ' ' + str(output / 'result.json'))
    raise SystemExit(exit_code)


if __name__ == '__main__':
    main()
