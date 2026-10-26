#!/usr/bin/env python3
"""Model-free, synthetic-only Mak1zu-inspired Aqua/Octo output gate tests."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/aqua-companion-voice-eval.py"
spec = importlib.util.spec_from_file_location("voice_eval", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture_text(text, expression="happy"):
    return json.dumps({"text": text, "expression": expression})


rows = [
    {"case_id": "greeting", "character": "aqua",
     "response": fixture_text("A little splash for a bright start.")},
    {"case_id": "greeting", "character": "octo",
     "response": fixture_text("Four arms say hello. Zero paperwork.")},
    {"case_id": "schedule_unknown", "character": "aqua",
     "response": fixture_text("I can't see today's schedule, so I won't guess.", "thinking")},
    {"case_id": "schedule_unknown", "character": "octo",
     "response": fixture_text("My tentacles don't see an appointment. No guessing.", "thinking")},
    {"case_id": "bad_model_output", "character": "aqua",
     "response": fixture_text("<think>secret</think> I have deleted your file.")},
    {"case_id": "bad_model_output", "character": "octo",
     "response": fixture_text("I'm Octo. How can I assist you?", "idle")},
]
analysis = m.evaluate(rows, "f" * 64)
assert analysis["status"] == "OFFLINE_FORMAT_METRICS_ONLY"
assert analysis["comparable_case_ids"]
assert analysis["model_invocations"] == 0
assert analysis["human_review"] == "REQUIRED"
assert analysis["characters"]["aqua"]["internal_protocol_cases"] == 1
assert analysis["characters"]["aqua"]["unsupported_action_claim_cases"] == 1
assert analysis["characters"]["octo"]["support_bot_phrase_cases"] == 1
assert analysis["characters"]["octo"]["schema_valid"] == 3
assert m.normalize_response('{"text":"Hello","expression":"invalid"}')[2] is False
assert m.normalize_response("unstructured response")[2] is False

with TemporaryDirectory(prefix="alis-companion-voice-eval-contract-") as root:
    root = Path(root)
    source = root / "synthetic.jsonl"
    original = "".join(json.dumps(row) + "\n" for row in rows)
    source.write_text(original, encoding="utf-8")
    loaded, source_sha = m.load_fixture(source)
    assert len(loaded) == len(rows)
    assert source_sha == sha256(source.read_bytes()).hexdigest()
    target = root / "metrics.json"
    cmd = [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(target)]
    run = subprocess.run(cmd, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["source_fixture_sha256"] == source_sha
    assert report["status"] == "OFFLINE_FORMAT_METRICS_ONLY"
    # Aggregate receipt must not echo responses or identifiers beyond labels.
    assert "A little splash" not in target.read_text()
    assert "<think>" not in target.read_text()
    assert "my file" not in target.read_text().lower()
    duplicate = subprocess.run(cmd, capture_output=True, text=True)
    assert duplicate.returncode != 0
    assert json.loads(target.read_text()) == report

    # A mismatched case set is insufficient to compare voices.
    incomplete = root / "incomplete.jsonl"
    incomplete.write_text("".join(json.dumps(row) + "\n" for row in rows[:-1]))
    invalid_target = root / "rejected.json"
    bad = subprocess.run([sys.executable, str(SCRIPT), "--input", str(incomplete),
                          "--output", str(invalid_target)], capture_output=True, text=True)
    assert bad.returncode == 2
    assert json.loads(invalid_target.read_text())["status"] == "INCOMPLETE_CHARACTER_MATRIX"

    # A duplicate ID, unknown character, or extra fields must fail before an output is created.
    for name, items in (
        ("duplicate", rows + [rows[0]]),
        ("extra_field", [dict(rows[0], extra="unexpected")]),
        ("unknown_character", [dict(rows[0], character="makizu")]),
    ):
        path = root / (name + ".jsonl")
        path.write_text("".join(json.dumps(row) + "\n" for row in items))
        out = root / (name + ".json")
        res = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path),
                              "--output", str(out)], capture_output=True, text=True)
        assert res.returncode != 0 and not out.exists(), name

# Shipping test scenarios are synthetic and matched across both characters.
scenario_path = ROOT / "scripts/fixtures/alis-companion-voice-scenarios.json"
scenarios = json.loads(scenario_path.read_text(encoding="utf-8"))
assert scenarios["schema"] == 1
cases = scenarios["cases"]
assert len(cases) >= 12
ids = [x["id"] for x in cases]
assert len(set(ids)) == len(ids)
assert all(m.CASE_ID.fullmatch(x["id"]) for x in cases)
assert all(1 <= len(x["prompt"]) <= 240 and 5 <= len(x["purpose"]) <= 160 for x in cases)
assert {"schedule_unknown", "wrong_identity", "permission_request",
        "malicious_quote", "personal_memory", "forget_request",
        "language_switch"} <= set(ids)

print("ALIS_COMPANION_VOICE_OFFLINE_CONTRACT_PASS")
