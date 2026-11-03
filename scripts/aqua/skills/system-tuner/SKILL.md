---
name: system-tuner
description: Audit CPU/RAM/swap pressure, thermal sensors, and top resource-heavy processes to optimize Local LLM & desktop responsiveness.
category: system
version: 1.0.0
triggers: ["system-tuner", "toi uu he thong", "tối ưu hệ thống", "toi uu ram", "tối ưu ram", "tune system", "process audit"]
---

# System Tuner Skill

Use this skill when the system feels slow, RAM/VRAM is constrained during Local LLM inference, or the user wants to find and tame resource-heavy background processes.

## Workflow
1. Inspect CPU load, memory/swap usage, and thermal status via `companion-system.system_info`.
2. List the top CPU and memory-consuming processes via `desktop-commander.list_processes(action="list")`.
3. Recommend safe optimizations (closing idle heavy apps, adjusting Thinking Effort / MTP settings, or terminating stuck processes with confirmation).
