#!/usr/bin/env python3
"""Offline sequence-runner tests: receipt, stop points, no false GPU PASS."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shader_sequence", ROOT / "scripts/aqua-shader-ab-sequence.py")
seq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seq)
assert seq.BASELINE.exists()
assert seq.HARNESS.exists()
assert seq.BUILDER.exists()

# Fake only the child command boundary, never pretend to have run a GPU frame.
with TemporaryDirectory(prefix="alis-companion-sequence-offline-") as tmp:
    base = Path(tmp)
    candidate = base / "mock-candidate.frag"
    candidate.write_bytes(seq.BASELINE.read_bytes() + b"\n// isolated synthetic candidate\n")
    expected = {"self": "PASS_SAME_SOURCE", "negative": "PASS_DIFFERENCE_DETECTED",
                "compare": "PASS_PIXEL_EQUAL"}
    for stop in (None, "self", "negative", "compare"):
        invoked = []
        result_root = base / ("sequence-" + (stop or "pass"))

        def fake_invoke(cmd, cwd=seq.ROOT):
            mode = cmd[cmd.index("--mode") + 1]
            invoked.append(mode)
            folder = Path(cmd[cmd.index("--output") + 1])
            folder.mkdir()
            status = expected[mode]
            rc = 0
            if mode == stop:
                status = "FAIL_PIXEL_DIFFERENCE" if mode == "compare" else "INCONCLUSIVE"
                rc = 1 if mode == "compare" else 2
            (folder / "result.json").write_text(json.dumps({"status": status}), encoding="utf-8")
            return rc, "synthetic/mock only"

        with patch.object(seq, "git_identity", return_value="a" * 40), \
             patch.object(seq, "invoke", side_effect=fake_invoke):
            rc, receipt = seq.run_chain(result_root, candidate, "opengl", True)
        assert json.loads((result_root / "sequence.json").read_text()) == receipt
        assert receipt["alis_companion_sha"] == "a" * 40
        assert receipt["harness_sha256"] == seq.sha(seq.HARNESS)
        if stop is None:
            assert rc == 0 and receipt["status"] == "G0_CONTROLS_QUALIFIED_AB_PIXELS_EQUAL"
            assert invoked == ["self", "negative", "compare"]
        else:
            assert invoked == ["self", "negative", "compare"][:invoked.index(stop) + 1]
            assert rc == (1 if stop == "compare" else 2)
            assert receipt["status"] == ("REJECTED_PIXEL_DIFFERENCE" if stop == "compare"
                                          else "INCONCLUSIVE")
        try:
            with patch.object(seq, "git_identity", return_value="a" * 40):
                seq.run_chain(result_root, candidate, "opengl", False)
        except ValueError as error:
            assert "output_already_exists" in str(error)
        else:
            raise AssertionError("previous evidence directory was overwritten")

    # A dirty source checkout must fail before creating output or child processes.
    forbidden = base / "forbidden"
    with patch.object(seq, "git_identity", side_effect=RuntimeError("dirty_source_checkout")):
        try:
            seq.run_chain(forbidden, candidate, "opengl", False)
        except RuntimeError as error:
            assert "dirty_source_checkout" in str(error)
        else:
            raise AssertionError("dirty checkout must fail")
    assert not forbidden.exists()
    assert seq.stage_result(base / "nonexistent")["status"] == "MISSING_RECEIPT"
    # A/A mismatch report is summarized in one privacy-bounded uploadable receipt.
    source = base / "synthetic-AA"
    source.mkdir()
    (source / "result.json").write_text(json.dumps({
        "status": "INCONCLUSIVE_SELF_CONTROL",
        "comparison": [
            {"name": "aqua_idle", "changed_pixels": 7,
             "alpha_changed_pixels": 2, "max_channel_delta": 3,
             "difference_bbox": [1, 2, 4, 5], "baseline_repeat_changed_pixels": 0,
             "candidate_repeat_changed_pixels": 0},
            {"name": "aqua_faceplant", "changed_pixels": 3,
             "alpha_changed_pixels": 1, "max_channel_delta": 2,
             "difference_bbox": [2, 3, 4, 6], "baseline_repeat_changed_pixels": 1,
             "candidate_repeat_changed_pixels": 4},
        ],
    }), encoding="utf-8")
    result = seq.stage_result(source)
    assert result["status"] == "INCONCLUSIVE_SELF_CONTROL"
    assert result["diagnostics"]["cross_changed_pixels"] == 10
    assert result["diagnostics"]["baseline_repeat_changed_pixels"] == 1
    assert result["diagnostics"]["candidate_repeat_changed_pixels"] == 4
    assert result["diagnostics"]["worst_cases"][0]["name"] == "aqua_faceplant"


print("ALIS_COMPANION_SHADER_SEQUENCE_OFFLINE_CONTRACT_PASS")
