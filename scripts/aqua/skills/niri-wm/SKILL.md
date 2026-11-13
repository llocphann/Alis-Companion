---
name: niri-wm
description: Authoritative KDL configuration, window-rule, layer-rule, keybindings, and IPC guidance for Niri Wayland compositor.
category: desktop
version: 1.0.0
triggers: ["niri", "niri-wm", "config.kdl", "window-rule", "layer-rule", "niri msg", "scrollable-tiling", "kdl"]
---

# Niri WM Skill (`niri-wm`)

Use this skill whenever the user asks about Niri compositor configuration (`~/.config/niri/config.kdl` and `include` files), `window-rule`, `layer-rule`, `binds`, `outputs`, `layout`, or `niri msg` IPC.

## 3-Stage Grounded Workflow
1. **Stage 1 — Canonical Niri Docs**: Query canonical Niri KDL documentation (`canonical/niri/*.md`) to verify exact KDL v1 node names and properties.
2. **Stage 2 — Personal Machine Context**: Inspect the user's actual `~/.config/niri/config.kdl` and any `include "..."` files indexed in `personal/niri-personal-config.md` plus active outputs in `personal/runtime-profile.md`.
3. **Stage 3 — Strict KDL Rules & Validation**:
   - **NEVER use Hyprland or Sway syntax** (`windowrulev2 =`, `bind =`, `exec-once =`) in Niri.
   - Every action inside `binds { ... }` MUST end with a semicolon `;`, e.g. `Mod+T { spawn "kitty"; }`.
   - Window rules use `window-rule { match app-id=r#"..."#; open-floating true; }`.
   - Layer-shell rules use `layer-rule { match namespace="^alis:..."; }`.
   - Validate all generated or modified KDL snippets with `niri validate -c <file>` (`domain_rag.validate_niri_kdl_snippet`).
