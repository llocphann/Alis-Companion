#!/usr/bin/env python3
"""Pure offline guards for source-pinned Alis-Companion dual-QSB visual comparison."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
tool = ROOT / "scripts/aqua-shader-ab.py"
spec = importlib.util.spec_from_file_location("shader_ab", tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# Qt versions differ in qsb CLI sugar; both paths must emit QtQuick variants.
for help_text, expected in (
    ("--qt6 --glsl --hlsl --msl", ["--qt6"]),
    ("--glsl --hlsl --msl", ["--glsl", "100 es,120,150", "--hlsl", "50", "--msl", "12"]),
):
    with patch.object(module.subprocess, "run",
                      return_value=SimpleNamespace(returncode=0, stdout=help_text, stderr="")):
        assert module.qsb_flags("qsb") == expected
for help_text, code in (("unrecognized", 0), ("--qt6", 1)):
    with patch.object(module.subprocess, "run",
                      return_value=SimpleNamespace(returncode=code, stdout=help_text, stderr="")):
        try:
            module.qsb_flags("qsb")
        except RuntimeError:
            pass
        else:
            raise AssertionError("unsupported qsb variant set must fail closed")

assert len(module.cases()) >= 10
assert {"aqua_faceplant", "octo_head", "cornea", "foot", "aqua_theme_shift"} <= {
    state["name"] for state in module.cases()
}
for character_variant in (0, 4):
    assert {state["tier"] for state in module.cases()
            if state["variant"] == character_variant} == {0, 1, 2}, "each character needs all selectable tiers"
assert len({state["name"] for state in module.cases()}) == len(module.cases())
assert module.capture_timeout() == 180
with patch.object(module, 'cases', return_value=[{}]):
    assert module.capture_timeout() == 75
assert 'onFrameSwapped:' in module.QML and 'framesSinceSwitch < 1' in module.QML
assert 'graphics_api_mismatch' in module.QML
assert 'property int repeatPass: -1' in module.QML
assert 'baseline-repeat' in module.QML or 'root.repeatPass === 1' in module.QML
assert module.capture_contract_sha() == module.capture_contract_sha()
assert module.QML.count('fragmentShader: Qt.resolvedUrl(') == 2
assert '"baseline/WaterDropletMaterial.frag.qsb"' in module.QML
assert '"candidate/WaterDropletMaterial.frag.qsb"' in module.QML
assert 'Math.min(currentIndex, cases.length - 1)' in module.QML
# Exercise the actual QML transition function with hard-coded capture order.
# One warm-up per item is fixed in advance; neither pixels nor variance choose
# which frames get measured. Both later captures must still match exactly.
transition = re.search(r'    function advanceCapture\(\) \{[\s\S]*?\n    \}', module.QML).group(0)
script = '''
const vm = require('node:vm');
const root = {currentIndex:0,candidateTurn:false,repeatPass:-1,readyTicks:4,pending:true,cases:[0,1,2]};
let quits = 0;
const api = vm.createContext({root, Qt:{quit:()=>{++quits}},console:{log:()=>{}}});
vm.runInContext(TRANSITION,api);
const seen=[];
while (!quits && seen.length < 25) {
    seen.push([root.currentIndex,root.candidateTurn,root.repeatPass]);
    api.advanceCapture();
    if (root.readyTicks!==0 || root.pending) throw Error('capture state did not settle');
}
process.stdout.write(JSON.stringify({seen,quits,ended:root.currentIndex}));
'''.replace('TRANSITION', json.dumps(transition))
sequence = json.loads(subprocess.check_output(['node', '-e', script], text=True))
assert sequence == dict(seen=[[index, candidate, frame] for index in range(3)
                             for candidate in (False, True) for frame in (-1, 0, 1)],
                        quits=1, ended=3)
original = module.NEGATIVE_TARGET
altered = module.negative_variant(original)
assert original != altered and module.NEGATIVE_REPLACEMENT in altered
for source in ("", original + original):
    try:
        module.negative_variant(source)
    except RuntimeError:
        pass
    else:
        raise AssertionError("non-unique negative control must fail closed")

def steady(changed):
    return {"changed_pixels": changed,
            "baseline_repeat_changed_pixels": 0,
            "candidate_repeat_changed_pixels": 0}

assert module.classify("self", [steady(0)]) == ("PASS_SAME_SOURCE", 0)
assert module.classify("self", [steady(1)]) == ("INCONCLUSIVE_SELF_CONTROL", 2)
assert module.classify("negative", [steady(1)]) == ("PASS_DIFFERENCE_DETECTED", 0)
assert module.classify("negative", [steady(0)]) == ("FAIL_NEGATIVE_UNDETECTED", 3)
assert module.classify("compare", [steady(0)]) == ("PASS_PIXEL_EQUAL", 0)
assert module.classify("compare", [steady(5)]) == ("FAIL_PIXEL_DIFFERENCE", 1)
assert module.classify("self", [dict(steady(0), baseline_repeat_changed_pixels=1)]) == ("INCONCLUSIVE_CAPTURE_VARIANCE", 2)
assert module.classify("compare", [dict(steady(0), candidate_repeat_changed_pixels=1)]) == ("INCONCLUSIVE_CAPTURE_VARIANCE", 2)
assert module.classify("negative", [{"changed_pixels": 1}]) == ("INCONCLUSIVE_CAPTURE_DIAGNOSTICS_MISSING", 2)
assert module.classify("self", []) == ("INCONCLUSIVE_CAPTURE_DIAGNOSTICS_MISSING", 2)

# Deterministic pixel-level diagnostics include RGB even when alpha is zero.
class RawImage:
    size = tuple(module.CAPTURE_SIZE)
    def __init__(self, data):
        self.data = data

    def tobytes(self):
        return self.data

pixels = module.CAPTURE_SIZE[0] * module.CAPTURE_SIZE[1]
a = bytearray(pixels * 4)
b = bytearray(a)
b[0] = 9
b[7] = 4
diag = module.rgba_difference(RawImage(bytes(a)), RawImage(bytes(b)))
assert diag["changed_pixels"] == 2
assert diag["alpha_changed_pixels"] == 1
assert diag["max_channel_delta"] == 9
assert diag["difference_bbox"] == [0, 0, 2, 1]
assert module.rgba_difference(RawImage(bytes(a)), RawImage(bytes(a)))["changed_pixels"] == 0

# Read the filenames actually emitted by QML: label-index-repeat.png.
# Keep the fixture independent of the consumer so a spelling drift fails.
from PIL import Image
with tempfile.TemporaryDirectory() as temporary:
    output = Path(temporary)
    for index, sample in enumerate(module.cases()):
        image = Image.new("RGBA", tuple(module.CAPTURE_SIZE), (index + 1, 80, 130, 255))
        image.putpixel((0, 0), (0, 0, 0, 0))
        for label in ("baseline", "candidate"):
            # Cold readback variance is retained but never substitutes for the
            # two fixed measured frames; it cannot loosen their pixel gate.
            Image.new("RGBA", tuple(module.CAPTURE_SIZE), (0, 0, 0, 0)).save(output / f"{label}-{index}-warmup.png")
            image.save(output / f"{label}-{index}.png")
            image.save(output / f"{label}-{index}-repeat.png")
    comparisons = module.compare_pngs(output, module.cases())
    assert len(comparisons) == len(module.cases())
    assert module.classify("self", comparisons) == ("PASS_SAME_SOURCE", 0)
    (output / "candidate-0-warmup.png").unlink()
    try:
        module.compare_pngs(output, module.cases())
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("warm-up evidence must be retained")
    Image.new("RGBA", tuple(module.CAPTURE_SIZE), (0, 0, 0, 0)).save(output / "candidate-0-warmup.png")
    with Image.open(output / "baseline-0-repeat.png") as image:
        changed = image.convert("RGBA")
    changed.putpixel((0, 0), (9, 0, 0, 0))
    changed.save(output / "baseline-0-repeat.png")
    comparisons = module.compare_pngs(output, module.cases())
    assert comparisons[0]["baseline_repeat_changed_pixels"] == 1
    assert comparisons[0]["baseline_repeat_max_channel_delta"] == 9
    assert comparisons[0]["baseline_repeat_bbox"] == [0, 0, 1, 1]
    assert module.classify("self", comparisons) == ("INCONCLUSIVE_CAPTURE_VARIANCE", 2)
    (output / "candidate-0-repeat.png").unlink()
    try:
        module.compare_pngs(output, module.cases())
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing repeated capture must never qualify")

control = dict(status="PASS_SAME_SOURCE", mode="self", schema=3,
               contract_sha256=module.capture_contract_sha(),
               baseline_source_sha256="baseline-source",
               candidate_source_sha256="baseline-source",
               baseline_qsb_sha256="baseline-qsb",
               candidate_qsb_sha256="baseline-qsb",
               qsb_version="qsb version",
               graphics=dict(backend="opengl"),
               comparison=[dict(name=case["name"], **steady(0)) for case in module.cases()])
assert module.qualified_control(control, "baseline-source", "baseline-qsb",
                                "qsb version", dict(backend="opengl"))
for changed in (dict(control, status="INCONCLUSIVE_SELF_CONTROL"),
                dict(control, baseline_qsb_sha256="other"),
                dict(control, candidate_qsb_sha256="other"),
                dict(control, candidate_source_sha256="other"),
                dict(control, contract_sha256="stale-fixture"),
                dict(control, schema=2),
                dict(control, comparison=[]),
                dict(control, comparison=[dict(name="tampered", **steady(1))] * len(module.cases())),
                dict(control, comparison=[dict(name="unstable", **dict(steady(0), candidate_repeat_changed_pixels=1))] * len(module.cases())),
                dict(control, graphics=dict(backend="vulkan"))):
    assert not module.qualified_control(changed, "baseline-source", "baseline-qsb",
                                        "qsb version", dict(backend="opengl"))

# Invalid/unsupported commands may not allocate evidence directories.
with tempfile.TemporaryDirectory() as root:
    folder = Path(root) / "never-created"
    run = subprocess.run([sys.executable, str(tool), "--mode", "compare",
                          "--output", str(folder)], capture_output=True, text=True)
    assert run.returncode != 0 and "requires" in run.stderr and not folder.exists()
    run = subprocess.run([sys.executable, str(tool), "--mode", "self",
                          "--control-report", str(Path(root) / "forged.json"),
                          "--output", str(folder)], capture_output=True, text=True)
    assert run.returncode != 0 and not folder.exists()

print("ALIS_COMPANION_SHADER_AB_OFFLINE_CONTRACT_PASS")
