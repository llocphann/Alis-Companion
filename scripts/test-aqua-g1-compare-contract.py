#!/usr/bin/env python3
"""Offline receipt-cohort analysis: no live desktop, mocks, or hardware claims."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("g1compare", ROOT / "scripts/aqua-g1-compare.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def receipt(state, run):
    cpu = 1.0 + run if state == "off" else 2.0 + run
    return dict(schema=1, status="COLLECTED_RESOURCE_ONLY",
                alis_companion_sha="a"*40, alis_sha="b"*40,
                backend="opengl", role="shell", state=state,
                duration_target_s=30.0, interval_target_s=1.0, warmup_target_s=5.0,
                summary=dict(sample_count=31, cpu_percent_one_core=cpu,
                             cpu_seconds=cpu/100*30,
                             rss=dict(first_kib=100, last_kib=105, peak_kib=100+run),
                             pss=dict(first_kib=60, last_kib=65, peak_kib=70+run),
                             **{x:"NOT_MEASURED" for x in m.MISSING_GPU}))

with TemporaryDirectory() as root:
    base = Path(root)
    paths = []
    for state in ("off", "aqua_idle"):
        for run in range(3):
            path = base / f"{state}-{run}.json"
            path.write_text(json.dumps(receipt(state, run)), encoding="utf-8")
            paths.append(path)
    sources = [m.check_sample(x) for x in paths]
    diff = m.analyze(sources, "off", "aqua_idle")
    assert diff["status"] == "DESCRIPTIVE_RESOURCE_ONLY_NOT_GPU_VALIDATED"
    assert diff["metrics"]["cpu_percent_one_core"]["difference_observed_minus_reference"] == 1
    assert diff["metrics"]["pss.peak_kib"]["difference_observed_minus_reference"] == 0
    assert diff["gpu_frame_time_ms"] == "NOT_MEASURED"
    assert diff["repeats"] == dict(off=3, aqua_idle=3)

    target = base / "summary.json"
    proc = subprocess.run([sys.executable, str(ROOT/"scripts/aqua-g1-compare.py"),
                           "--reference-state", "off", "--observed-state", "aqua_idle",
                           "--receipts", *(str(p) for p in paths),
                           "--output", str(target)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(target.read_text()) == diff
    proc2 = subprocess.run([sys.executable, str(ROOT/"scripts/aqua-g1-compare.py"),
                            "--reference-state", "off", "--observed-state", "aqua_idle",
                            "--receipts", *(str(p) for p in paths),
                            "--output", str(target)], capture_output=True, text=True)
    assert proc2.returncode != 0 and target.exists()

    def should_reject(new_rows, reason):
        try:
            m.analyze(new_rows, "off", "aqua_idle")
        except ValueError as err:
            assert reason in str(err), err
        else:
            raise AssertionError("should reject " + reason)

    should_reject(sources[:-1], "insufficient_paired")
    should_reject(sources + [sources[0]], "duplicate_receipts")
    altered = json.loads(paths[5].read_text())
    altered["backend"] = "vulkan"
    changed = base / "mixed.json"
    changed.write_text(json.dumps(altered), encoding="utf-8")
    should_reject(sources[:5]+[m.check_sample(changed)], "mixed_source")
    altered["backend"] = "opengl"
    altered["summary"]["gpu_frame_time_ms"] = 5.0
    changed.write_text(json.dumps(altered), encoding="utf-8")
    try:
        m.check_sample(changed)
    except ValueError as err:
        assert "unverified_gpu_metric" in str(err)
    else:
        raise AssertionError("fake GPU samples must be rejected")

print("ALIS_COMPANION_G1_COMPARE_OFFLINE_CONTRACT_PASS")
