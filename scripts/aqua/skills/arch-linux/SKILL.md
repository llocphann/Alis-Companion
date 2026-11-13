---
name: arch-linux
description: Arch Linux package management (pacman, makepkg, PKGBUILD, AUR), systemd user services, journalctl diagnostics, and Wayland portal setup.
category: system
version: 1.0.0
triggers: ["arch-linux", "archlinux", "pacman", "makepkg", "pkgbuild", "aur", "systemctl", "journalctl"]
---

# Arch Linux Skill (`arch-linux`)

Use this skill whenever the user asks about Arch Linux package management (`pacman`, `makepkg`, `PKGBUILD`, AUR), `systemd` user/system services, `journalctl` logs, `environment.d`, or Wayland portals (`xdg-desktop-portal`, `pipewire`, `wireplumber`).

## 3-Stage Grounded Workflow
1. **Stage 1 — Canonical Arch Wiki Docs**: Query canonical Arch Wiki documentation (`canonical/archlinux/*.md`) for authoritative flags and maintenance procedures.
2. **Stage 2 — Personal Machine Context**: Inspect the user's `personal/systemd-user-env.md` and `personal/runtime-profile.md` for kernel, distro, active services, and failed user units.
3. **Stage 3 — Strict Arch Linux Safety Rules**:
   - **No Partial Upgrades**: NEVER recommend `pacman -Sy <pkg>` without `-u`. Always use `pacman -S <pkg>` or `pacman -Syu <pkg>`.
   - **Explicit Consent for Privileged Actions**: Never execute `sudo pacman` or destructive `systemctl` operations without explicit user confirmation.
   - **User Service Diagnostics**: Prefer `systemctl --user status <unit>` and `journalctl --user -u <unit> -n 100 -e --no-pager` when diagnosing desktop services like `alis.service`.
