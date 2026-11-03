---
name: system-diagnostics
description: Inspect Linux system health, CPU load, memory usage, disk space, battery status, and active desktop window.
triggers: ["system-diagnostics", "system health", "kiem tra he thong", "cpu", "ram", "disk", "battery", "chẩn đoán hệ thống"]
command: diag.py
---

# System Diagnostics Skill

Use this skill when the user asks about system performance, CPU load, RAM usage, free disk space, battery health, or desktop environment status.

## Workflow
1. Run `scripts/diag.py` or call the `companion-system.system_info` MCP tool.
2. Summarize CPU load average, memory utilization, `$HOME` disk space, and battery charge clearly.
3. Highlight any resource bottlenecks (e.g. `<10%` free disk or `<15%` available memory).
