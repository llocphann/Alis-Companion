#!/usr/bin/env python3
"""G1 sampler logic tests: fixture /proc only, no live PID or GPU probes."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("g1", ROOT / "scripts/aqua-g1-resource-sample.py")
g1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g1)


def stat(pid=1456, start=991, user=120, system=30, rss=100):
    fields = ["S"] + ["0"] * 21
    fields[11], fields[12] = str(user), str(system)
    fields[17], fields[19], fields[21] = "4", str(start), str(rss)
    return f"{pid} (alis-companion (with) spaces) " + " ".join(fields)


assert g1.parse_stat(stat()) == dict(pid=1456, utime=120, stime=30,
                                   num_threads=4, starttime=991, rss_pages=100)
for bad in ("", "abc", "1456 (missing tail) ", "1 (thing) S 0"):
    try:
        g1.parse_stat(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid stat must not be parsed")
assert g1.parse_status("Name:\tqs\nUid:\t1000\t1000\t1000\t1000\n"
                       "Threads:\t4\nVmRSS:\t124 kB\n") == dict(
                           uid=1000, vmrss_kib=124, status_threads=4)
assert g1.parse_smaps_rollup("Rss: 200 kB\nPss: 80 kB\nSwapPss: 0 kB\n") == dict(
    Pss=80, Rss=200, SwapPss=0, Private_Clean=None, Private_Dirty=None)

with tempfile.TemporaryDirectory(prefix="g1-proc-fixture-") as folder:
    proc = Path(folder)
    target = proc / "1456"
    target.mkdir()
    (target / "stat").write_text(stat())
    (target / "status").write_text("Uid:\t" + str(os.geteuid()) + "\nVmRSS:\t123 kB\nThreads:\t4\n")
    (target / "smaps_rollup").write_text("Rss: 222 kB\nPss: 111 kB\nSwapPss: 0 kB\n")
    snapshot = g1.read_process(1456, proc)
    assert snapshot["identity"] == (1456, 991)
    assert snapshot["cpu_ticks"] == 150
    assert snapshot["rss_kib"] == 222 and snapshot["pss_kib"] == 111
    assert snapshot["threads"] == 4
    (target / "smaps_rollup").unlink()
    fallback = g1.read_process(1456, proc)
    assert fallback["rss_kib"] == 123 and fallback["pss_kib"] is None
    (target / "status").write_text("Uid:\t" + str(os.geteuid() + 1) + "\nVmRSS:\t123 kB\n")
    try:
        g1.read_process(1456, proc)
    except RuntimeError as error:
        assert "pid_or_uid_mismatch" in str(error)
    else:
        raise AssertionError("reading a foreign UID must fail")

readings = [
    dict(identity=(1456, 991), starttime=991, cpu_ticks=100, rss_kib=100, pss_kib=80, threads=3),
    dict(identity=(1456, 991), starttime=991, cpu_ticks=115, rss_kib=120, pss_kib=87, threads=3),
    dict(identity=(1456, 991), starttime=991, cpu_ticks=140, rss_kib=105, pss_kib=90, threads=4),
]
clock_state = [0.0]
def fake_sleep(duration):
    clock_state[0] += duration
def fake_clock():
    return clock_state[0]
with patch.object(g1, "read_process", side_effect=readings):
    samples = g1.sample_pid(1456, duration=2, interval=1, warmup=0,
                            sleep=fake_sleep, clock=fake_clock)
assert len(samples) == 3
assert [x["elapsed_s"] for x in samples] == [0, 1, 2]
summary = g1.summarize(samples, hz=100)
assert summary["cpu_seconds"] == .4
assert summary["cpu_percent_one_core"] == 20
assert summary["rss"] == dict(first_kib=100, peak_kib=120, last_kib=105, growth_kib=5)
assert summary["pss"]["peak_kib"] == 90
assert summary["gpu_frame_time_ms"] == "NOT_MEASURED"
assert summary["frame_p99_ms"] == "NOT_MEASURED"
samples[1]["pss_kib"] = None
assert g1.summarize(samples, 100)["pss"] == "NOT_MEASURED"
try:
    g1.summarize(samples[:1], 100)
except ValueError:
    pass
else:
    raise AssertionError("single sample is not a baseline")
with patch.object(g1, "read_process", side_effect=[readings[0], dict(readings[1], identity=(1456, 992))]):
    try:
        g1.sample_pid(1456, duration=1, interval=1, warmup=0,
                      sleep=fake_sleep, clock=fake_clock)
    except RuntimeError as error:
        assert "pid_identity_changed" in str(error)
    else:
        raise AssertionError("PID recycling must fail closed")

# Invalid CLI must not allocate an output directory.
with tempfile.TemporaryDirectory() as folder:
    destination = Path(folder) / "forbidden"
    run = subprocess.run([sys.executable, str(ROOT / "scripts/aqua-g1-resource-sample.py"),
                          "--pid", "-1", "--role", "shell", "--state", "aqua_idle",
                          "--backend", "opengl", "--output", str(destination)],
                         capture_output=True, text=True)
    assert run.returncode != 0 and not destination.exists()

print("ALIS_COMPANION_G1_RESOURCE_OFFLINE_CONTRACT_PASS")
