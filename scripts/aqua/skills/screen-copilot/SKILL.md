---
name: screen-copilot
description: Capture active window or screen via Vision VLM, explain on-screen UI/errors/logs, and suggest immediate actions.
category: desktop-eyes
version: 1.0.0
triggers: ["screen-copilot", "phan tich man hinh", "phân tích màn hình", "doc loi tren man hinh", "đọc lỗi trên màn hình", "diagnose screen", "explain screen"]
---

# Screen Copilot Skill

Use this skill when the user wants Companion to look at the currently open desktop window or terminal, read errors/dialogs/code on screen via the local Vision LLM (`niri+vision`), and provide actionable troubleshooting or explanation.

## Workflow
1. Capture the active Wayland/Niri window using `computer-use-linux.screenshot` (`skipOcr=True` when VLM is active).
2. Inspect the visual contents, window title, and focused UI elements directly with the Vision model.
3. Highlight any visible error messages, stack traces, build failures, or UI blockers and propose concrete next steps.
