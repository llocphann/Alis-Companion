#!/usr/bin/env python3
"""Offline, privacy-bounded voice/output evaluation for Aqua and Octo.

Inspired by Mak1zu's *evaluation principle*, not copied from its Go code.
Accepts supplied synthetic / consent-cleared response fixtures; NEVER calls
an LLM, scans chat history, loads a vault, or changes the live Companion.
All output is aggregate; input dialogue is not reproduced in reports.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

MAX_FILE = 512 * 1024
MAX_ROWS = 200
ALLOWED_CHARACTERS = ("aqua", "octo")
ALLOWED_EXPRESSIONS = {
    "idle", "happy", "excited", "thinking", "working",
    "surprised", "sleepy", "sad", "alert",
}
CASE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
INTERNAL_PROTOCOL = re.compile(
    r"(?i)<\s*/?\s*(?:think|tool_call|tool_result|function_call|system|assistant)\b"
    r"|(?:\[\s*/?\s*INST\s*\]|<\|(?:im_start|im_end|assistant|system|tool)[^>]*\|>)"
)
UNVERIFIED_ACTION = re.compile(
    r"(?i)\b(?:i(?:'ve| have) (?:opened|saved|deleted|sent|installed|changed|scheduled)"
    r"|(?:your )?(?:file|setting|appointment) (?:has been|is now) (?:saved|deleted|changed|scheduled))\b"
)
SUPPORT_PHRASES = (
    "as an ai", "i'm happy to help", "i am happy to help",
    "feel free to ask", "how can i assist", "please let me know if",
)
CHARACTER_MISMATCH = {
    "aqua": re.compile(r"(?i)\bi(?: am|'m) octo\b"),
    "octo": re.compile(r"(?i)\bi(?: am|'m) aqua\b"),
}


def load_fixture(path):
    source = Path(path)
    if not source.is_file() or source.stat().st_size > MAX_FILE:
        raise ValueError("invalid_fixture_size")
    binary = source.read_bytes()
    if not binary:
        raise ValueError("empty_fixture")
    rows = []
    for lineno, line in enumerate(binary.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if len(rows) >= MAX_ROWS or len(line) > 4096:
            raise ValueError("fixture_bounds_exceeded")
        item = json.loads(line)
        if not isinstance(item, dict) or set(item) != {"case_id", "character", "response"}:
            raise ValueError("invalid_fixture_fields:" + str(lineno))
        if not isinstance(item["case_id"], str) or not CASE_ID.fullmatch(item["case_id"]):
            raise ValueError("invalid_case_id:" + str(lineno))
        if item["character"] not in ALLOWED_CHARACTERS:
            raise ValueError("invalid_character:" + str(lineno))
        if not isinstance(item["response"], (str, dict)):
            raise ValueError("invalid_response_type:" + str(lineno))
        rows.append(item)
    if not rows or len({(r["case_id"], r["character"]) for r in rows}) != len(rows):
        raise ValueError("empty_or_duplicated_cases")
    return rows, sha256(binary).hexdigest()


def normalize_response(value):
    # A non-JSON fallback is deliberately tracked, not treated as a valid
    # structured-response test. The current AquaMind QML has this fallback.
    if isinstance(value, str):
        try:
            obj = json.loads(value)
        except (ValueError, TypeError):
            return value, "idle", False
    else:
        obj = value
    if not isinstance(obj, dict) or not isinstance(obj.get("text"), str):
        return "", "idle", False
    valid = (set(obj) == {"text", "expression"}
             and isinstance(obj.get("expression"), str)
             and obj["expression"] in ALLOWED_EXPRESSIONS)
    return obj["text"], obj.get("expression"), valid


def evaluate(rows, fixture_hash):
    totals = Counter()
    samples = {c: [] for c in ALLOWED_CHARACTERS}
    per_character = {}
    for item in rows:
        person = item["character"]
        text, expression, schema_valid = normalize_response(item["response"])
        words = WORD.findall(text)
        count = len(words)
        detected = {
            "schema_valid": schema_valid,
            "empty_or_long": not text.strip() or len(text) > 420,
            "controls": bool(CONTROL.search(text)),
            "internal_protocol": bool(INTERNAL_PROTOCOL.search(text)),
            "unsupported_action": bool(UNVERIFIED_ACTION.search(text)),
            "support_phrase": any(p in text.lower() for p in SUPPORT_PHRASES),
            "identity_mismatch": bool(CHARACTER_MISMATCH[person].search(text)),
            "question_ending": text.rstrip().endswith("?"),
            "has_expression": expression in ALLOWED_EXPRESSIONS,
        }
        first = " ".join(w.lower() for w in words[:3])
        samples[person].append((first, count))
        for name, triggered in detected.items():
            if triggered:
                totals[person + "." + name] += 1
        totals[person + ".responses"] += 1
    for person in ALLOWED_CHARACTERS:
        seq = samples[person]
        counts = sorted(w for _, w in seq)
        starters = [s for s, _ in seq if s]
        highest = max(Counter(starters).values(), default=0)
        n = len(seq)
        per_character[person] = {
            "responses": n,
            "schema_valid": totals[person + ".schema_valid"],
            "empty_or_over_420_chars": totals[person + ".empty_or_long"],
            "control_character_cases": totals[person + ".controls"],
            "internal_protocol_cases": totals[person + ".internal_protocol"],
            "unsupported_action_claim_cases": totals[person + ".unsupported_action"],
            "support_bot_phrase_cases": totals[person + ".support_phrase"],
            "wrong_character_claim_cases": totals[person + ".identity_mismatch"],
            "question_ending_cases": totals[person + ".question_ending"],
            "invalid_expression_cases": n - totals[person + ".has_expression"],
            "word_length_median_lower": counts[(n - 1) // 2] if n else None,
            "largest_identical_three_word_opener_count": highest,
        }
    complete = (all(per_character[p]["responses"] >= 1 for p in ALLOWED_CHARACTERS)
                and {r["case_id"] for r in rows if r["character"] == "aqua"} ==
                    {r["case_id"] for r in rows if r["character"] == "octo"})
    return {
        "schema": 1,
        "status": "OFFLINE_FORMAT_METRICS_ONLY" if complete else "INCOMPLETE_CHARACTER_MATRIX",
        "source_fixture_sha256": fixture_hash,
        "scope": "Provided/consent-cleared fixtures only. Not a model test or a proof of voice quality.",
        "characters": per_character,
        "human_review": "REQUIRED",
        "model_invocations": 0,
        "secret_or_chat_history_access": False,
        "comparable_case_ids": complete,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="synthetic or consent-cleared JSONL only, no real chat export")
    parser.add_argument("--output", type=Path, required=True,
                        help="a new aggregate-only JSON file; cannot overwrite")
    args = parser.parse_args()
    destination = args.output.expanduser().resolve()
    if destination.exists():
        parser.error("output_already_exists")
    try:
        items, digest = load_fixture(args.input)
        result = evaluate(items, digest)
    except (UnicodeError, OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, "ALIS_COMPANION_VOICE_EVAL_INCONCLUSIVE:" + str(exc)[:120] + "\n")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("ALIS_COMPANION_VOICE_" + result["status"] + " " + str(destination))
    raise SystemExit(0 if result["comparable_case_ids"] else 2)


if __name__ == "__main__":
    main()
