#!/usr/bin/env python3
"""Compare G1 source-pinned per-PID resource samples for two actor states.

Descriptive only; not a GPU benchmark or a verified optimization claim.
Reject mixed revisions/backends or undersampled comparisons.
"""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median

RESOURCE_METRICS = ("cpu_percent_one_core", "cpu_seconds")
MISSING_GPU = ("gpu_frame_time_ms", "whole_shell_gpu_ms", "gpu_vram_kib",
               "frame_p50_ms", "frame_p95_ms", "frame_p99_ms", "power_w")


def check_sample(path):
    raw = Path(path).read_bytes()
    obj = json.loads(raw)
    if obj.get("schema") != 1 or obj.get("status") != "COLLECTED_RESOURCE_ONLY":
        raise ValueError("unqualified_sample_status")
    if any(not obj.get(k) for k in ("alis_companion_sha", "alis_sha", "backend", "role", "state")):
        raise ValueError("missing_sample_identity")
    sm = obj.get("summary")
    if not isinstance(sm, dict) or sm.get("sample_count", 0) < 3:
        raise ValueError("insufficient_sample_count")
    for name in RESOURCE_METRICS:
        x = sm.get(name)
        if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) or x < 0:
            raise ValueError("invalid_resource_metric:" + name)
    if any(sm.get(k) != "NOT_MEASURED" for k in MISSING_GPU):
        raise ValueError("unverified_gpu_metric_in_proc_only_receipt")
    for name in ("rss", "pss"):
        m = sm.get(name)
        if m != "NOT_MEASURED" and (not isinstance(m, dict) or
                                   any(type(m.get(field)) is not int or m[field] < 0
                                       for field in ("first_kib", "last_kib", "peak_kib"))):
            raise ValueError("invalid_memory_metric:" + name)
    return dict(sha256=sha256(raw).hexdigest(), obj=obj)


def analyze(loaded, reference, observed, min_repeats=3):
    if reference == observed:
        raise ValueError("identical_states")
    if min_repeats < 3:
        raise ValueError("insufficient_repetitions")
    keys = ("alis_companion_sha", "alis_sha", "backend", "role", "duration_target_s",
            "interval_target_s", "warmup_target_s")
    base = loaded[0]["obj"]
    if any(any(s["obj"].get(k) != base.get(k) for k in keys) for s in loaded):
        raise ValueError("mixed_source_or_sampling_protocol")
    counts = {reference: [], observed: []}
    for row in loaded:
        name = row["obj"]["state"]
        if name not in counts:
            raise ValueError("unexpected_state:" + str(name))
        counts[name].append(row)
    if any(len(v) < min_repeats for v in counts.values()):
        raise ValueError("insufficient_paired_state_repetitions")
    if len({x["sha256"] for x in loaded}) != len(loaded):
        raise ValueError("duplicate_receipts")
    def metric(row, key):
        sm = row["obj"]["summary"]
        if key.startswith("rss.") or key.startswith("pss."):
            which, field = key.split(".")
            memory = sm.get(which)
            return memory.get(field) if isinstance(memory, dict) else None
        return sm[key]
    measures = ("cpu_percent_one_core", "cpu_seconds", "rss.peak_kib", "pss.peak_kib")
    result = {}
    for measure in measures:
        before = [metric(r, measure) for r in counts[reference]]
        after = [metric(r, measure) for r in counts[observed]]
        if any(x is None for x in before + after):
            result[measure] = "NOT_MEASURED"
        else:
            mid0, mid1 = median(before), median(after)
            result[measure] = dict(
                reference_median=mid0,
                observed_median=mid1,
                difference_observed_minus_reference=round(mid1-mid0, 5),
                reference_range=[min(before), max(before)],
                observed_range=[min(after), max(after)])
    return dict(schema=1, status="DESCRIPTIVE_RESOURCE_ONLY_NOT_GPU_VALIDATED",
                basis={k:base.get(k) for k in keys},
                reference_state=reference, observed_state=observed,
                repeats={k:len(v) for k,v in counts.items()},
                metrics=result,
                gpu_frame_time_ms="NOT_MEASURED",
                whole_shell_gpu_ms="NOT_MEASURED",
                gpu_vram_kib="NOT_MEASURED",
                safety="No automated state transitions or causal attribution; "
                       "check same machine/driver/power policy and session state manually",
                input_sha256=sorted(x["sha256"] for x in loaded))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-state", required=True)
    parser.add_argument("--observed-state", required=True)
    parser.add_argument("--receipts", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new file; never overwrite evidence")
    args = parser.parse_args()
    target = args.output.expanduser().resolve()
    if target.exists():
        parser.error("output_already_exists")
    try:
        data = [check_sample(p) for p in args.receipts]
        result = analyze(data, args.reference_state, args.observed_state)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, "ALIS_COMPANION_G1_COMPARE_INCONCLUSIVE:" + str(error)[:160] + "\n")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("ALIS_COMPANION_G1_RESOURCE_COMPARISON_ONLY " + str(target))


if __name__ == "__main__":
    main()
