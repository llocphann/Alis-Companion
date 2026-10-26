#!/usr/bin/env python3
"""Run the fail-closed Alis-Companion A/A -> deliberate-negative -> candidate chain.

No runtime install, remote access, live-desktop configuration change or GPU
speedup claim. All logs and RGBA captures remain in a newly created local dir.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts/aqua-shader-ab.py"
BUILDER = ROOT / "scripts/build-aqua-material-candidate.py"
BASELINE = ROOT / "modules/abyss/companion/WaterDropletMaterial.frag"
EXIT_INCONCLUSIVE = 2


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_identity(root):
    rev = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                         capture_output=True, text=True, timeout=10)
    clean = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                           capture_output=True, text=True, timeout=15)
    if rev.returncode or clean.returncode or len(rev.stdout.strip()) != 40:
        raise RuntimeError("unverified_git_checkout")
    if clean.stdout.strip():
        raise RuntimeError("dirty_source_checkout")
    return rev.stdout.strip()


def stage_result(directory):
    report = Path(directory) / "result.json"
    if not report.is_file():
        return {"status": "MISSING_RECEIPT", "path": str(report)}
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "UNREADABLE_RECEIPT", "path": str(report)}
    rows = data.get("comparison", [])
    rows = rows if isinstance(rows, list) else []
    useful = []
    for value in rows:
        if not isinstance(value, dict):
            continue
        name = value.get("name", "")
        if not isinstance(name, str) or len(name) > 80:
            continue
        keys = ("changed_pixels", "alpha_changed_pixels", "max_channel_delta",
                "baseline_repeat_changed_pixels", "candidate_repeat_changed_pixels")
        if not all(type(value.get(key, 0)) is int and value.get(key, 0) >= 0 for key in keys):
            continue
        useful.append(dict(name=name, **{key: value.get(key, 0) for key in keys},
                           difference_bbox=value.get("difference_bbox"),
                           baseline_repeat_bbox=value.get("baseline_repeat_bbox"),
                           candidate_repeat_bbox=value.get("candidate_repeat_bbox")))
    # Bounded aggregate diagnostics: no PNGs, user paths or frame contents copied.
    diagnostic = dict(cases_reported=len(useful),
                      cross_changed_pixels=sum(x["changed_pixels"] for x in useful),
                      baseline_repeat_changed_pixels=sum(x["baseline_repeat_changed_pixels"] for x in useful),
                      candidate_repeat_changed_pixels=sum(x["candidate_repeat_changed_pixels"] for x in useful),
                      worst_cases=sorted(useful, key=lambda x: (
                          x["baseline_repeat_changed_pixels"] +
                          x["candidate_repeat_changed_pixels"] + x["changed_pixels"]),
                          reverse=True)[:5])
    return {"status": data.get("status", "UNRECOGNIZED_RECEIPT"), "path": str(report),
            "baseline_source_sha256": data.get("baseline_source_sha256"),
            "baseline_qsb_sha256": data.get("baseline_qsb_sha256"),
            "candidate_source_sha256": data.get("candidate_source_sha256"),
            "candidate_qsb_sha256": data.get("candidate_qsb_sha256"),
            "diagnostics": diagnostic}


def invoke(command, cwd=ROOT):
    try:
        finished = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                  timeout=180, check=False)
        return finished.returncode, (finished.stdout + finished.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "bounded_subprocess_timeout"


def run_chain(output, candidate, graphics, diagnostics):
    # Source identity is frozen before allocating any evidence paths.
    revision = git_identity(ROOT)
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError("output_already_exists")
    if not BASELINE.is_file() or not HARNESS.is_file() or not BUILDER.is_file():
        raise RuntimeError("required_alis_companion_sources_missing")
    if candidate is not None:
        candidate = Path(candidate).expanduser().resolve()
        if not candidate.is_file():
            raise RuntimeError("candidate_source_missing")
        if sha(candidate) == sha(BASELINE):
            raise RuntimeError("identical_explicit_candidate")
    output.mkdir(parents=True, mode=0o700)
    receipt = {
        "schema": 1, "alis_companion_sha": revision, "harness_sha256": sha(HARNESS),
        "baseline_source_sha256": sha(BASELINE), "graphics": graphics,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "INCONCLUSIVE",
        "scope": "synthetic shader captures; no whole-shell benchmark or physical acceptance",
        "stages": {},
    }
    try:
        if candidate is None:
            candidate = output / "bubble-candidate.frag"
            rc, log = invoke([sys.executable, str(BUILDER), str(candidate)])
            if rc:
                receipt["stages"]["candidate_build"] = {"returncode": rc, "output_tail": log}
                return EXIT_INCONCLUSIVE, receipt
        receipt["candidate_source_sha256"] = sha(candidate)
        if receipt["baseline_source_sha256"] == receipt["candidate_source_sha256"]:
            receipt["stages"]["candidate_build"] = {"status": "IDENTICAL_CANDIDATE"}
            return EXIT_INCONCLUSIVE, receipt
        stages = [
            ("aa", "self", "PASS_SAME_SOURCE"),
            ("negative", "negative", "PASS_DIFFERENCE_DETECTED"),
            ("ab", "compare", "PASS_PIXEL_EQUAL"),
        ]
        for label, mode, expected in stages:
            command = [sys.executable, str(HARNESS), "--mode", mode,
                       "--graphics", graphics, "--output", str(output / label)]
            if mode == "self":
                if diagnostics:
                    command.append("--diagnostics")
            elif mode == "negative":
                command += ["--control-report", str(output / "aa" / "result.json")]
            else:
                command += ["--candidate-source", str(candidate),
                            "--control-report", str(output / "aa" / "result.json"),
                            "--negative-report", str(output / "negative" / "result.json")]
            code, log = invoke(command)
            data = stage_result(output / label)
            receipt["stages"][label] = {**data, "returncode": code, "output_tail": log}
            if code != 0 or data["status"] != expected:
                receipt["status"] = ("REJECTED_PIXEL_DIFFERENCE" if label == "ab" and
                                     data["status"] == "FAIL_PIXEL_DIFFERENCE"
                                     else "INCONCLUSIVE")
                return (1 if receipt["status"] == "REJECTED_PIXEL_DIFFERENCE" else EXIT_INCONCLUSIVE), receipt
        receipt["status"] = "G0_CONTROLS_QUALIFIED_AB_PIXELS_EQUAL"
        return 0, receipt
    finally:
        receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "sequence.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                                               encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new local folder; may not already exist")
    parser.add_argument("--candidate-source", type=Path,
                        help="optional source; without this, generate the existing bubble-ray candidate")
    parser.add_argument("--graphics", choices=("opengl", "vulkan"), default="opengl")
    parser.add_argument("--diagnostics", action="store_true", help="Qt debug logs only on A/A, not GPU benchmark")
    args = parser.parse_args()
    try:
        code, receipt = run_chain(args.output, args.candidate_source, args.graphics, args.diagnostics)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(EXIT_INCONCLUSIVE, "ALIS_COMPANION_SHADER_AB_SEQUENCE_INCONCLUSIVE:" + str(error)[:200] + "\n")
    print("ALIS_COMPANION_SHADER_AB_SEQUENCE_" + receipt["status"] +
          " report=" + str(Path(args.output).expanduser().resolve() / "sequence.json"))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
