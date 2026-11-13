---
name: quickshell-qml
description: Quickshell & Qt6 QML development rules for PanelWindow, WlrLayershell, WlrKeyboardFocus, Region masks, Process, FileView, and IpcHandler.
category: desktop
version: 1.0.0
triggers: ["quickshell", "quickshell-qml", "wlrlayershell", "panelwindow", "wlrkeyboardfocus", "fileview", "ipchandler", "alis shell"]
---

# Quickshell QML Skill (`quickshell-qml`)

Use this skill whenever the user asks about Quickshell QML components, `PanelWindow`, `WlrLayershell`, `WlrKeyboardFocus`, `Region` input masks, `Quickshell.Io.Process`, `FileView`, `IpcHandler`, or Alis/Companion UI surfaces.

## 3-Stage Grounded Workflow
1. **Stage 1 — Canonical Quickshell Docs**: Query canonical Quickshell documentation (`canonical/quickshell/*.md`) for exact `Quickshell`, `Quickshell.Wayland`, `Quickshell.Io`, and `Quickshell.Services` APIs.
2. **Stage 2 — Personal Machine Context**: Check the user's active Alis/Quickshell configuration in `personal/quickshell-alis-config.md`.
3. **Stage 3 — Strict Quickshell & Wayland Rules**:
   - **Keyboard Focus Safety (`WlrKeyboardFocus`)**: Never leave `WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive` active continuously on a non-modal overlay or chatbox, or external Wayland windows will be blocked from receiving keyboard input. Use `WlrKeyboardFocus.OnDemand` while an input is focused/hovered and `WlrKeyboardFocus.None` when unfocused.
   - **Shaped Input Masks (`mask: Region`)**: Always constrain `PanelWindow.mask` to actual interactive child items (`Region { item: ... }`) so pointer clicks on the rest of the screen pass through to normal windows.
   - **Syntax Verification**: Validate modified `.qml` files with `/usr/lib/qt6/bin/qmlformat`.
