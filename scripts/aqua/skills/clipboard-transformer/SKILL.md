---
name: clipboard-transformer
description: Read Wayland clipboard content (wl-paste), inspect format (code, JSON, URL, logs), and clean, summarize, or translate it.
category: productivity
version: 1.0.0
triggers: ["clipboard-transformer", "xu ly clipboard", "xử lý clipboard", "dich clipboard", "dịch clipboard", "format clipboard", "explain clipboard"]
---

# Clipboard Transformer Skill

Use this skill when the user wants to inspect, format, summarize, translate, or debug whatever text/code/JSON is currently copied in their Wayland clipboard.

## Workflow
1. Read current clipboard contents via `pipewire-mpris-mcp.clipboard_inspect(action="read")` or `companion-system.clipboard_read`.
2. Detect content type (stack trace, JSON/YAML, shell command, URL, or prose) and perform the requested transformation or explanation.
3. Optionally copy the cleaned/transformed output back to the clipboard when requested.
