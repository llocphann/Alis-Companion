#!/usr/bin/env python3
"""Diagnostic helper script for the system-diagnostics Companion skill."""
import os
from pathlib import Path
import platform
import shutil

print(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
try:
    l1, l5, l15 = os.getloadavg()
    print(f"Load average: {l1:.2f}, {l5:.2f}, {l15:.2f}")
except OSError:
    pass
try:
    mem = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            mem[k.strip()] = int(v.strip().split()[0])
    total = mem.get("MemTotal", 0) // 1024
    avail = mem.get("MemAvailable", 0) // 1024
    print(f"Memory: {max(0, total - avail)} MiB used / {total} MiB total ({avail} MiB available)")
except Exception:
    pass
try:
    du = shutil.disk_usage(str(Path.home()))
    print(f"Disk ($HOME): {du.free / (1024**3):.1f} GiB free / {du.total / (1024**3):.1f} GiB total")
except Exception:
    pass
