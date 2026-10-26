#!/usr/bin/env python3
"""Opt-in read-only per-process CPU/PSS/RSS baseline for Alis-Companion G1.

Samples exactly one explicitly supplied, same-UID PID. Never starts, kills,
reconfigures or probes the desktop. Does NOT measure GPU timing or whole-shell
resource savings; these remain NOT_MEASURED pending hardware-specific captures.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATES = ("off", "hidden", "aqua_idle", "aqua_move", "aqua_pitch",
          "octo_idle", "octo_move", "octo_grip", "handoff")
ROLES = ("shell", "companiond", "compositor")
STAT_KEYS = ("pid", "utime", "stime", "num_threads", "starttime", "rss_pages")


def parse_stat(line):
    """Linux /proc/PID/stat: comm may include spaces and right parentheses."""
    if not isinstance(line, str) or ") " not in line or " (" not in line:
        raise ValueError("proc_stat_format")
    pid_text, rest = line.rsplit(") ", 1)
    if not pid_text.split(" (", 1)[0].isdigit():
        raise ValueError("proc_pid_invalid")
    fields = rest.split()
    if len(fields) < 22:
        raise ValueError("proc_stat_fields")
    try:
        value = dict(pid=int(pid_text.split(" (", 1)[0]),
                     utime=int(fields[11]), stime=int(fields[12]),
                     num_threads=int(fields[17]), starttime=int(fields[19]),
                     rss_pages=int(fields[21]))
    except (IndexError, ValueError) as error:
        raise ValueError("proc_stat_types") from error
    if any(value[key] < 0 for key in STAT_KEYS):
        raise ValueError("proc_stat_negative")
    return value


def parse_status(text):
    uid = re.search(r"^Uid:\s+(\d+)", text, re.MULTILINE)
    rss = re.search(r"^VmRSS:\s+(\d+)\s+kB\b", text, re.MULTILINE)
    threads = re.search(r"^Threads:\s+(\d+)", text, re.MULTILINE)
    if not uid:
        raise ValueError("proc_status_uid_missing")
    return dict(uid=int(uid.group(1)),
                vmrss_kib=int(rss.group(1)) if rss else None,
                status_threads=int(threads.group(1)) if threads else None)


def parse_smaps_rollup(text):
    values = {}
    for key in ("Pss", "Rss", "SwapPss", "Private_Clean", "Private_Dirty"):
        match = re.search(r"^" + key + r":\s+(\d+)\s+kB\b", text, re.MULTILINE)
        values[key] = int(match.group(1)) if match else None
    return values


def read_process(pid, proc_root=Path("/proc")):
    base = Path(proc_root) / str(pid)
    before = parse_stat((base / "stat").read_text(encoding="utf-8"))
    status = parse_status((base / "status").read_text(encoding="utf-8"))
    if before["pid"] != pid or status["uid"] != os.geteuid():
        raise RuntimeError("pid_or_uid_mismatch")
    try:
        rollup = parse_smaps_rollup((base / "smaps_rollup").read_text(encoding="utf-8"))
    except (PermissionError, FileNotFoundError):
        rollup = {key: None for key in ("Pss", "Rss", "SwapPss", "Private_Clean", "Private_Dirty")}
    # Guard against PID recycling or process termination within multi-file read.
    after = parse_stat((base / "stat").read_text(encoding="utf-8"))
    if before["starttime"] != after["starttime"] or after["pid"] != pid:
        raise RuntimeError("pid_recycled_during_read")
    return dict(identity=(pid, before["starttime"]), cpu_ticks=after["utime"] + after["stime"],
                rss_kib=rollup["Rss"] if rollup["Rss"] is not None else status["vmrss_kib"],
                pss_kib=rollup["Pss"], swap_pss_kib=rollup["SwapPss"],
                threads=after["num_threads"], starttime=after["starttime"])


def git_sha(path, clean_required=True):
    repo = Path(path).resolve()
    rev = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True, timeout=10)
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
                           capture_output=True, text=True, timeout=15)
    if rev.returncode or dirty.returncode or not re.fullmatch("[0-9a-f]{40}", rev.stdout.strip()):
        raise RuntimeError("unverified_git_revision")
    if clean_required and dirty.stdout.strip():
        raise RuntimeError("dirty_source_revision")
    return rev.stdout.strip()


def summarize(samples, hz):
    if len(samples) < 2 or hz <= 0:
        raise ValueError("insufficient_samples")
    elapsed = samples[-1]["elapsed_s"] - samples[0]["elapsed_s"]
    cpu_ticks = samples[-1]["cpu_ticks"] - samples[0]["cpu_ticks"]
    if elapsed <= 0 or cpu_ticks < 0:
        raise ValueError("invalid_cpu_interval")
    cpu_s = cpu_ticks / hz
    valid_rss = [x["rss_kib"] for x in samples if x["rss_kib"] is not None]
    valid_pss = [x["pss_kib"] for x in samples if x["pss_kib"] is not None]
    def mem_stats(values):
        return (dict(first_kib=values[0], peak_kib=max(values), last_kib=values[-1],
                     growth_kib=values[-1] - values[0]) if len(values) == len(samples)
                else "NOT_MEASURED")
    return dict(elapsed_s=round(elapsed, 4), sample_count=len(samples),
                cpu_seconds=round(cpu_s, 5),
                cpu_percent_one_core=round(100.0 * cpu_s / elapsed, 3),
                thread_count_first=samples[0]["threads"],
                thread_count_max=max(x["threads"] for x in samples),
                rss=mem_stats(valid_rss), pss=mem_stats(valid_pss),
                gpu_frame_time_ms="NOT_MEASURED",
                whole_shell_gpu_ms="NOT_MEASURED",
                gpu_vram_kib="NOT_MEASURED",
                frame_p50_ms="NOT_MEASURED",
                frame_p95_ms="NOT_MEASURED",
                frame_p99_ms="NOT_MEASURED",
                power_w="NOT_MEASURED")


def sample_pid(pid, duration, interval, warmup, proc_root=Path("/proc"),
               sleep=time.sleep, clock=time.monotonic):
    if warmup:
        sleep(warmup)
    first = read_process(pid, proc_root)
    identity = first["identity"]
    started = clock()
    readings = [dict(first, elapsed_s=0.0)]
    steps = math.ceil(duration / interval)
    if steps > 1500:
        raise ValueError("too_many_samples")
    for i in range(1, steps + 1):
        target = started + min(i * interval, duration)
        sleep(max(0.0, target - clock()))
        current = read_process(pid, proc_root)
        if current["identity"] != identity:
            raise RuntimeError("pid_identity_changed")
        readings.append(dict(current, elapsed_s=round(clock() - started, 6)))
    return readings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True, help="explicit live PID, never discovered or modified")
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--state", choices=STATES, required=True)
    parser.add_argument("--backend", choices=("opengl", "vulkan", "software"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--alis-root", type=Path, help="compatible Alis host git checkout; required for qualifying paired baselines")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--warmup", type=float, default=5.0)
    args = parser.parse_args()
    if args.pid <= 1 or args.pid == os.getpid():
        parser.error("must select an existing separate PID")
    if not 2 <= args.duration <= 300 or not .2 <= args.interval <= 5 or not 0 <= args.warmup <= 60:
        parser.error("unsafe sampling bounds")
    if math.ceil(args.duration / args.interval) > 1500:
        parser.error("too many samples")
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error("output_already_exists")
    try:
        own_sha = git_sha(ROOT)
        host_sha = git_sha(args.alis_root) if args.alis_root else None
        first = read_process(args.pid)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(2, "ALIS_COMPANION_G1_PREFLIGHT_INCONCLUSIVE: " + str(error)[:160] + "\n")
    output.mkdir(parents=True, mode=0o700)
    receipt = dict(schema=1, status="INCONCLUSIVE", role=args.role, state=args.state,
                   backend=args.backend, alis_companion_sha=own_sha, alis_sha=host_sha,
                   pid=args.pid, pid_starttime=first["starttime"],
                   duration_target_s=args.duration, interval_target_s=args.interval,
                   warmup_target_s=args.warmup,
                   started_utc=datetime.now(timezone.utc).isoformat(),
                   process_scope="one explicit same-UID PID only",
                   qualifiers="No automatic state transitions; verify actor state before each run",
                   gpu_evidence="NOT_MEASURED")
    result_code = 2
    try:
        readings = sample_pid(args.pid, args.duration, args.interval, args.warmup)
        if readings[0]["starttime"] != first["starttime"]:
            raise RuntimeError("pid_identity_changed_after_preflight")
        hz = os.sysconf("SC_CLK_TCK")
        receipt["summary"] = summarize(readings, hz)
        receipt["samples"] = [{k: v for k, v in x.items() if k != "identity"} for x in readings]
        if git_sha(ROOT) != own_sha or (args.alis_root and git_sha(args.alis_root) != host_sha):
            raise RuntimeError("source_changed_during_sampling")
        receipt["status"] = ("COLLECTED_RESOURCE_ONLY" if host_sha else
                             "PARTIAL_HOST_IDENTITY_NOT_PROVIDED")
        result_code = 0 if host_sha else 2
    except (OSError, RuntimeError, ValueError) as error:
        receipt["status"] = "INCONCLUSIVE"
        receipt["error"] = str(error)[:160]
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "result.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")
    print("ALIS_COMPANION_G1_" + receipt["status"] + " " + str(output / "result.json"))
    raise SystemExit(result_code)


if __name__ == "__main__":
    main()
