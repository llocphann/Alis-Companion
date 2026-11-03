---
name: window-workspace-layout
description: Inspect open Niri/Wayland windows across workspaces, focus apps, or arrange coding/research workspace layouts.
category: desktop-hands
version: 1.0.0
triggers: ["window-workspace-layout", "sap xep cua so", "sắp xếp cửa sổ", "bo cuc cua so", "bố cục cửa sổ", "workspace layout", "arrange windows"]
---

# Window & Workspace Layout Skill

Use this skill when the user wants to inspect all open Wayland/Niri windows, switch or organize workspaces, or launch/focus a multi-window workflow (e.g. Coding, Research, Media).

## Workflow
1. Query open windows and workspace assignments via `computer-use-linux.window_action(action="list_windows")`.
2. Summarize active windows grouped by workspace and application ID.
3. Focus, switch workspace, or launch requested applications cleanly using `computer-use-linux.window_action`.
