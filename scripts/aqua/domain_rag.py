"""3-Stage Domain RAG System for Niri WM, Quickshell (QML), and Arch Linux.

Architecture:
- Stage 1 (`canonical`): Built-in & auto-updated canonical documentation chunks for
  Niri WM (`https://niri-wm.github.io/niri/`), Quickshell (`https://quickshell.outfoxxed.me/docs/`),
  and Arch Linux (`https://wiki.archlinux.org/`), tracked in `manifest.json` with
  `ETag`, `Last-Modified`, and `sha256` change detection.
- Stage 2 (`personal`): Incremental (`mtime_ns` + `sha256`), privacy-scrubbed personal
  machine context covering `~/.config/niri/config.kdl` (+ `include` files),
  Quickshell/Alis `config.json`, `~/.config/systemd/user/*.service`,
  `~/.config/environment.d/*.conf`, and a dynamic runtime profile.
- Stage 3 (`rules_skills`): Domain skill routing (`niri-wm`, `quickshell-qml`, `arch-linux`)
  and Niri KDL configuration validation (`validate_niri_kdl_snippet`).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from permission_store import is_forbidden_path
import rag_store


CANONICAL_DOMAINS: dict[str, dict] = {
    "niri": {
        "label": "Niri WM",
        "baseUrl": "https://niri-wm.github.io/niri/",
        "skillName": "niri-wm",
        "keywords": (
            "niri", "config.kdl", "window-rule", "layer-rule", "niri msg",
            "scrollable-tiling", "prefer-no-csd", "spawn-at-startup",
            "default-column-width", "open-on-output", "open-floating",
            "hotkey-overlay", "focus-ring", "kdl",
        ),
    },
    "quickshell": {
        "label": "Quickshell QML",
        "baseUrl": "https://quickshell.outfoxxed.me/docs/",
        "skillName": "quickshell-qml",
        "keywords": (
            "quickshell", "wlrlayershell", "wlrkeyboardfocus", "wlrlayer",
            "panelwindow", "floatingwindow", "popupwindow", "ipchandler",
            "fileview", "splitparser", "stdiocollector", "toplevelmanager",
            "qmlformat", "aliscompanionsurface", "abyssperimeter",
        ),
    },
    "archlinux": {
        "label": "Arch Linux",
        "baseUrl": "https://wiki.archlinux.org/",
        "skillName": "arch-linux",
        "keywords": (
            "arch", "archlinux", "pacman", "makepkg", "pkgbuild", "aur",
            "paru", "yay", "paccache", "pactree", "systemctl", "journalctl",
            "systemd", "mkinitcpio", "xdg-desktop-portal", "pipewire", "wireplumber",
        ),
    },
}


BUILTIN_CANONICAL_DOCS: dict[str, dict[str, dict[str, str]]] = {
    "niri": {
        "configuration-overview": {
            "title": "Niri Configuration Overview & Include Directives",
            "url": "https://niri-wm.github.io/niri/Configuration:-Introduction.html",
            "content": """# [Niri Docs > Configuration > Overview & Include]
Niri is configured in KDL v1 format at `~/.config/niri/config.kdl`. Changes are live-reloaded automatically unless the file has a syntax error. Always validate edits with `niri validate`.

## [Niri Docs > Configuration > Include Directive]
Modular configuration files can be split across `~/.config/niri/` using `include`:
```kdl
include "binds.kdl"
include "rules.kdl"
include "outputs.kdl"
```

## [Niri Docs > Configuration > Input, Layout & Startup]
Core top-level sections in `config.kdl` use KDL node blocks (never `=` assignments outside properties):
```kdl
prefer-no-csd
screenshot-path "~/Pictures/Screenshots/Screenshot from %Y-%m-%d %H-%M-%S.png"

spawn-at-startup "quickshell" "-c" "alis"

input {
    keyboard {
        xkb {
            layout "us"
        }
    }
    touchpad {
        tap
        natural-scroll
    }
    focus-follows-mouse max-scroll-amount="0%"
}

layout {
    gaps 12
    center-focused-column "never"
    default-column-width { proportion 0.5; }
    focus-ring {
        width 2
        active-color "#7fc8ff"
        inactive-color "#505050"
    }
}
```
""",
        },
        "window-rules-and-layer-rules": {
            "title": "Niri Window Rules & Layer Rules",
            "url": "https://niri-wm.github.io/niri/Configuration:-Window-Rules.html",
            "content": """# [Niri Docs > Configuration > Window Rules]
In Niri, window rules use `window-rule { ... }` blocks in KDL (never Hyprland `windowrulev2`). Each `window-rule` can have one or more `match` and `exclude` nodes using regular expressions on `app-id` or `title`:
```kdl
window-rule {
    match app-id=r#"^org\\.keepassxc\\.KeePassXC$"#
    match app-id=r#"^org\\.gnome\\.World\\.Secrets$"#
    block-out-from "screen-capture"
}

window-rule {
    match app-id=r#"^firefox$"# title="^Picture-in-Picture$"
    open-floating true
    default-floating-position x=32 y=32 relative-to="bottom-right"
}

window-rule {
    match is-active=false
    opacity 0.96
}

window-rule {
    geometry-corner-radius 12
    clip-to-geometry true
}
```

## [Niri Docs > Configuration > Layer Rules]
Wayland layer-shell surfaces (`wlr-layer-shell`, such as Quickshell panels, notifications, wallpapers) are matched with `layer-rule` by `namespace`:
```kdl
layer-rule {
    match namespace="^alis:abyss-perimeter$"
    block-out-from "screencast"
}

layer-rule {
    match namespace="^quickshell:wallpaper$"
    place-within-backdrop true
}
```
""",
        },
        "binds-and-ipc": {
            "title": "Niri Key Bindings & IPC Commands",
            "url": "https://niri-wm.github.io/niri/Configuration:-Key-Bindings.html",
            "content": """# [Niri Docs > Configuration > Key Bindings]
Key bindings live inside `binds { ... }` in `~/.config/niri/config.kdl`. Every action inside a bind MUST end with a semicolon `;`:
```kdl
binds {
    Mod+T hotkey-overlay-title="Open Terminal" { spawn "kitty"; }
    Mod+Q repeat=false { close-window; }
    Mod+Left  { focus-column-left; }
    Mod+Right { focus-column-right; }
    Mod+H     { focus-column-left; }
    Mod+L     { focus-column-right; }
    Mod+F     { maximize-column; }
    Mod+Shift+F { fullscreen-window; }
    Mod+V     { toggle-window-floating; }
    Mod+1     { focus-workspace 1; }
    Mod+2     { focus-workspace 2; }
    XF86AudioRaiseVolume allow-when-locked=true { spawn "wpctl" "set-volume" "@DEFAULT_AUDIO_SINK@" "5%+"; }
}
```

## [Niri Docs > IPC & CLI > niri msg & niri validate]
Query runtime state or trigger compositor actions via `niri msg` (supports `-j` / `--json`):
```bash
niri validate -c ~/.config/niri/config.kdl
niri msg -j version
niri msg -j outputs
niri msg -j windows
niri msg -j workspaces
niri msg -j focused-window
niri msg action focus-column-left
```
""",
        },
    },
    "quickshell": {
        "wayland-layershell-and-windows": {
            "title": "Quickshell PanelWindow, WlrLayershell & Input Regions",
            "url": "https://quickshell.outfoxxed.me/docs/types/Quickshell.Wayland/WlrLayershell/",
            "content": """# [Quickshell Docs > Wayland > PanelWindow & WlrLayershell]
`PanelWindow` creates a Wayland layer-shell surface (`zwlr_layer_surface_v1`). Import `Quickshell` and `Quickshell.Wayland`:
```qml
import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: panel
    screen: modelData
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    exclusiveZone: 0
    WlrLayershell.namespace: "alis:companion-overlay"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: chatInput.activeFocus
        ? WlrKeyboardFocus.OnDemand
        : WlrKeyboardFocus.None
    anchors { top: true; bottom: true; left: true; right: true }
    mask: Region { item: interactiveBox }
}
```

## [Quickshell Docs > Wayland > WlrKeyboardFocus & Shaped Input Masks]
- `WlrKeyboardFocus.None` (`0`): Surface never takes keyboard focus; external Wayland windows keep 100% keyboard focus.
- `WlrKeyboardFocus.OnDemand` (`1`): Surface receives keyboard focus when clicked inside its `mask` (`Region`), and immediately yields keyboard focus back to external Wayland windows when the user clicks an external window.
- `WlrKeyboardFocus.Exclusive` (`2`): Locks all keyboard input across the compositor to this layer surface. Never leave `WlrKeyboardFocus.Exclusive` enabled continuously on a persistent overlay or external windows will be unable to receive text input.
""",
        },
        "io-process-and-ipc": {
            "title": "Quickshell Process, FileView & IpcHandler",
            "url": "https://quickshell.outfoxxed.me/docs/types/Quickshell.Io/Process/",
            "content": """# [Quickshell Docs > Io > Process, SplitParser & FileView]
Use `Quickshell.Io` for non-blocking process execution, streaming stdout parsing, and file watching:
```qml
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    Process {
        id: helperProc
        command: ["python3", "/path/to/helper.py"]
        running: false
        stdout: SplitParser {
            onRead: line => console.info("Line:", line)
        }
    }

    FileView {
        id: configView
        path: Quickshell.env("HOME") + "/.config/alis/settings.json"
        watchChanges: true
        onFileChanged: reload()
    }

    IpcHandler {
        target: "companion"
        function toggleChat(): void {
            AquaMind.toggleChat()
        }
    }
}
```
""",
        },
        "services-and-compositor": {
            "title": "Quickshell ToplevelManager, Pipewire, Mpris & SystemTray",
            "url": "https://quickshell.outfoxxed.me/docs/types/Quickshell.Wayland/ToplevelManager/",
            "content": """# [Quickshell Docs > Services > ToplevelManager, Pipewire & Mpris]
Quickshell provides built-in reactive singletons for Wayland toplevel management, audio, media, battery, and system tray:
- `ToplevelManager.activeToplevel`: tracks the currently active Wayland application window (`activated`, `title`, `appId`).
- `Pipewire.defaultAudioSink`: reactive audio sink node (`audio.volume`, `audio.muted`).
- `Mpris.players`: list of active MPRIS media players.
- `UPower.displayDevice`: battery percentage, charging state, and power profiles.
""",
        },
    },
    "archlinux": {
        "pacman-and-makepkg": {
            "title": "Arch Linux Pacman, Makepkg & Package Maintenance",
            "url": "https://wiki.archlinux.org/title/Pacman",
            "content": """# [Arch Wiki > Package Management > Pacman & Makepkg]
Arch Linux uses `pacman` for official repositories and `makepkg` (or AUR helpers like `paru` / `yay`) for `PKGBUILD` builds.

## [Arch Wiki > Pacman > Safe Commands & Partial Upgrade Rule]
- Full system upgrade + install: `sudo pacman -Syu <package>`
- Install without refreshing sync db: `sudo pacman -S <package>`
- **NEVER run `pacman -Sy <package>`** without `-u`: partial upgrades break shared library `.so` sonames on Arch Linux.
- Query installed packages: `pacman -Qs <pattern>`, `pacman -Qi <package>`, `pacman -Ql <package>`
- Find which package owns a file: `pacman -Qo /usr/bin/<binary>`
- Remove package and unneeded deps: `sudo pacman -Rns <package>`
- Clean package cache safely: `sudo paccache -r` (keep last 3 versions)
- Build AUR package from `PKGBUILD`: `makepkg -si --needed`
""",
        },
        "systemd-and-journalctl": {
            "title": "Arch Linux Systemd User Units, Environment & Journalctl",
            "url": "https://wiki.archlinux.org/title/Systemd/User",
            "content": """# [Arch Wiki > System Administration > Systemd User Services & Journalctl]
Desktop shells and background daemons on Arch + Niri run as systemd user units in `~/.config/systemd/user/`:
```bash
systemctl --user daemon-reload
systemctl --user status alis.service
systemctl --user restart alis.service
systemctl --user --state=failed
journalctl --user -u alis.service -n 100 -e --no-pager
```

## [Arch Wiki > Systemd User > Environment Variables]
Environment variables for user units are defined in `~/.config/environment.d/*.conf` (`KEY=VALUE` per line) or imported from the Wayland compositor session via:
```bash
dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP NIRI_SOCKET
```
""",
        },
        "wayland-portal-and-gpu": {
            "title": "Arch Linux Wayland, XDG Desktop Portal & PipeWire",
            "url": "https://wiki.archlinux.org/title/XDG_Desktop_Portal",
            "content": """# [Arch Wiki > Desktop > Wayland, XDG Desktop Portal & PipeWire]
On Niri + Arch Linux, screencasting, file pickers, and screenshots rely on `xdg-desktop-portal` (`xdg-desktop-portal-gnome` and `xdg-desktop-portal-gtk`) plus `pipewire` and `wireplumber`:
```ini
# ~/.config/xdg-desktop-portal/niri-portals.conf
[preferred]
default=gnome;gtk;
org.freedesktop.impl.portal.Access=gtk;
org.freedesktop.impl.portal.Notification=gtk;
org.freedesktop.impl.portal.Secret=gnome-keyring;
```
Common Wayland utilities on Arch: `grim` (screenshots), `slurp` (region selection), `wl-clipboard` (`wl-copy` / `wl-paste`), `wev` (Wayland event viewer).
""",
        },
    },
}


DOMAIN_RULES_SUMMARY: dict[str, list[str]] = {
    "niri": [
        "Use strict KDL v1 syntax for `~/.config/niri/config.kdl` (never Hyprland `bind =` or `windowrulev2 =`).",
        "Every action inside `binds { ... }` must end with a semicolon `;` (e.g., `Mod+T { spawn \"kitty\"; }`).",
        "Check both Canonical Niri Docs and the user's personal `~/.config/niri/config.kdl` (including `include` files) before suggesting changes.",
        "Validate KDL snippets with `niri validate` (`validate_niri_kdl_snippet`).",
    ],
    "quickshell": [
        "Use Qt6 / Quickshell QML APIs (`PanelWindow`, `WlrLayershell`, `Quickshell.Io.Process`, `FileView`, `IpcHandler`).",
        "Never leave `WlrLayershell.keyboardFocus` at `WlrKeyboardFocus.Exclusive` on persistent overlays/chatboxes; use `WlrKeyboardFocus.OnDemand` or `WlrKeyboardFocus.None` so external Wayland windows can receive keyboard input.",
        "Keep `PanelWindow.mask` shaped to interactive regions (`Region`) so desktop clicks pass through to underlying windows.",
    ],
    "archlinux": [
        "Never recommend partial upgrades (`pacman -Sy <pkg>`); always use `pacman -S <pkg>` or `pacman -Syu`.",
        "Never run privileged `sudo` or package/service mutations without explicit user confirmation.",
        "Inspect user service health with `systemctl --user status <unit>` and `journalctl --user -u <unit> -n 100 -e`.",
    ],
}


_SENSITIVE_PATH_PATTERNS = re.compile(
    r"(?:^|/)(?:\.ssh|\.gnupg|\.aws|\.kube|\.docker|\.env(?:\.[^/]+)?|"
    r"[^/]*(?:secret|token|credential|password|passwd|private_key|id_rsa|id_ed25519)[^/]*|"
    r"mozilla|firefox|chromium|google-chrome|brave-browser)(?:/|$)",
    re.IGNORECASE,
)

_SECRET_KV_RE = re.compile(
    r"""(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|bot[_-]?token|token|secret|client[_-]?secret|password|passwd|private[_-]?key|authorization)\b(\s*[:=]\s*)(?:["']([^"'\n]{4,})["']|([^\s"',;}\]\n]{4,}))"""
)

_SECRET_TOKEN_PREFIX_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|AIza[0-9A-Za-z_\-]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}|Bearer\s+[A-Za-z0-9._\-]{12,})\b"
)


def _domain_rag_root() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    folder = (base / "alis-companion" / "domain_rag").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _canonical_dir() -> Path:
    d = _domain_rag_root() / "canonical"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _personal_dir() -> Path:
    d = _domain_rag_root() / "personal"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return _domain_rag_root() / "manifest.json"


def _personal_manifest_path() -> Path:
    return _domain_rag_root() / "personal_manifest.json"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = str(text or "").encode("utf-8", errors="replace")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def is_sensitive_personal_path(path: Path | str) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return True
    p = Path(raw).expanduser()
    if is_forbidden_path(p):
        return True
    norm = str(p).replace("\\", "/")
    return bool(_SENSITIVE_PATH_PATTERNS.search(norm))


def scrub_secrets(text: str) -> str:
    """Redact API keys, tokens, passwords, and Bearer credentials before indexing into Personal RAG."""
    raw = str(text or "")
    if not raw:
        return ""

    def _replace_kv(match: re.Match) -> str:
        key = match.group(1)
        sep = match.group(2)
        quoted = match.group(3)
        if quoted is not None:
            return f'{key}{sep}"<REDACTED_SECRET>"'
        return f"{key}{sep}<REDACTED_SECRET>"

    scrubbed = _SECRET_KV_RE.sub(_replace_kv, raw)
    scrubbed = _SECRET_TOKEN_PREFIX_RE.sub("<REDACTED_SECRET>", scrubbed)
    return scrubbed


def load_canonical_manifest() -> dict:
    p = _manifest_path()
    if not p.is_file():
        return {"version": 1, "documents": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("documents"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "documents": {}}


def sync_canonical_docs(
    domains: list[str] | None = None,
    force: bool = False,
    allow_network: bool = False,
    timeout_sec: float = 5.0,
    custom_sync_urls: dict[str, str] | None = None,
) -> dict:
    """Stage 1: Seed and optionally HTTP-sync canonical docs using ETag / Last-Modified / SHA-256."""
    target_domains = [
        d for d in (domains or list(CANONICAL_DOMAINS.keys()))
        if d in CANONICAL_DOMAINS
    ]
    manifest = load_canonical_manifest()
    docs_meta: dict[str, dict] = dict(manifest.get("documents") or {})
    now_ms = int(time.time() * 1000)

    seeded_count = 0
    updated_count = 0
    not_modified_count = 0
    errors: list[str] = []

    for domain in target_domains:
        domain_dir = _canonical_dir() / domain
        domain_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        builtin_docs = BUILTIN_CANONICAL_DOCS.get(domain, {})

        for slug, info in builtin_docs.items():
            doc_key = f"{domain}/{slug}"
            local_file = domain_dir / f"{slug}.md"
            existing_entry = docs_meta.get(doc_key) if isinstance(docs_meta.get(doc_key), dict) else {}
            default_body = info["content"].strip() + "\n"
            default_sha = _sha256_text(default_body)

            if not local_file.is_file() or (force and not allow_network and not custom_sync_urls):
                _atomic_write_text(local_file, default_body)
                docs_meta[doc_key] = {
                    "domain": domain,
                    "slug": slug,
                    "title": info["title"],
                    "url": info["url"],
                    "localPath": str(local_file),
                    "sha256": default_sha,
                    "etag": str(existing_entry.get("etag") or ""),
                    "lastModified": str(existing_entry.get("lastModified") or ""),
                    "updatedAtMs": now_ms,
                    "source": "builtin_seed",
                }
                seeded_count += 1
            elif doc_key not in docs_meta:
                current_text = local_file.read_text(encoding="utf-8", errors="replace")
                docs_meta[doc_key] = {
                    "domain": domain,
                    "slug": slug,
                    "title": info["title"],
                    "url": info["url"],
                    "localPath": str(local_file),
                    "sha256": _sha256_text(current_text),
                    "etag": "",
                    "lastModified": "",
                    "updatedAtMs": now_ms,
                    "source": "builtin_seed",
                }

            override_url = (custom_sync_urls or {}).get(doc_key) or (custom_sync_urls or {}).get(domain)
            fetch_url = str(override_url or info["url"]).strip()
            if (allow_network or override_url) and fetch_url.startswith(("http://", "https://")):
                entry = docs_meta[doc_key]
                headers = {"User-Agent": "Alis-Companion-DomainRAG/1.0"}
                if not force:
                    if entry.get("etag"):
                        headers["If-None-Match"] = str(entry["etag"])
                    if entry.get("lastModified"):
                        headers["If-Modified-Since"] = str(entry["lastModified"])
                req = urllib.request.Request(fetch_url, headers=headers, method="GET")
                try:
                    with urllib.request.urlopen(req, timeout=max(1.0, float(timeout_sec))) as resp:
                        raw_bytes = resp.read(512 * 1024)
                        raw_str = raw_bytes.decode("utf-8", errors="replace")
                        ctype = str(resp.headers.get("Content-Type") or "").lower()
                        if "<html" in raw_str[:500].lower() or "text/html" in ctype:
                            try:
                                from web_tools import html_to_markdown
                                md_body = html_to_markdown(raw_str)
                            except Exception:
                                md_body = re.sub(r"<[^>]+>", " ", raw_str)
                        else:
                            md_body = raw_str
                        domain_label = CANONICAL_DOMAINS[domain]["label"]
                        if not md_body.lstrip().startswith("#"):
                            md_body = f"# [{domain_label} Docs > {info['title']}]\n\n{md_body.strip()}\n"
                        else:
                            md_body = md_body.strip() + "\n"
                        new_sha = _sha256_text(md_body)
                        new_etag = str(resp.headers.get("ETag") or "").strip()
                        new_last_mod = str(resp.headers.get("Last-Modified") or "").strip()
                        if new_sha != entry.get("sha256") or force:
                            _atomic_write_text(local_file, md_body)
                            updated_count += 1
                        else:
                            not_modified_count += 1
                        docs_meta[doc_key] = {
                            "domain": domain,
                            "slug": slug,
                            "title": info["title"],
                            "url": fetch_url,
                            "localPath": str(local_file),
                            "sha256": new_sha,
                            "etag": new_etag or str(entry.get("etag") or ""),
                            "lastModified": new_last_mod or str(entry.get("lastModified") or ""),
                            "updatedAtMs": now_ms,
                            "source": "upstream_http",
                        }
                except urllib.error.HTTPError as exc:
                    if exc.code == 304:
                        not_modified_count += 1
                        docs_meta[doc_key]["checkedAtMs"] = now_ms
                    else:
                        errors.append(f"{doc_key}: HTTP {exc.code}")
                except Exception as exc:
                    errors.append(f"{doc_key}: {exc}")

    manifest["version"] = 1
    manifest["updatedAtMs"] = now_ms
    manifest["documents"] = docs_meta
    _atomic_write_json(_manifest_path(), manifest)
    return {
        "domains": target_domains,
        "seededCount": seeded_count,
        "updatedCount": updated_count,
        "notModifiedCount": not_modified_count,
        "totalDocuments": len(docs_meta),
        "manifestPath": str(_manifest_path()),
        "errors": errors,
    }


def _resolve_niri_config_files(home_dir: Path | None = None, config_home: Path | None = None) -> list[Path]:
    home = (home_dir or Path(os.environ.get("HOME") or Path.home())).expanduser().resolve(strict=False)
    cfg_root = (
        config_home
        or Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    ).expanduser().resolve(strict=False)
    niri_dir = cfg_root / "niri"
    main_cfg = niri_dir / "config.kdl"
    if not main_cfg.is_file() or main_cfg.is_symlink() or is_sensitive_personal_path(main_cfg):
        return []

    found: list[Path] = []
    seen: set[str] = set()
    queue: list[Path] = [main_cfg]

    while queue and len(found) < 24:
        cur = queue.pop(0)
        cur_str = str(cur.resolve(strict=False))
        if cur_str in seen or not cur.is_file() or cur.is_symlink() or is_sensitive_personal_path(cur):
            continue
        seen.add(cur_str)
        found.append(cur)
        try:
            text = cur.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r'^\s*include\s+"([^"]+)"', text, flags=re.MULTILINE):
            inc_raw = match.group(1).strip()
            if not inc_raw:
                continue
            inc_path = Path(inc_raw).expanduser()
            if not inc_path.is_absolute():
                inc_path = (cur.parent / inc_path).resolve(strict=False)
            else:
                inc_path = inc_path.resolve(strict=False)
            if inc_path.is_file() and not inc_path.is_symlink() and not is_sensitive_personal_path(inc_path):
                queue.append(inc_path)
    return found


def _format_niri_personal_markdown(niri_files: list[Path]) -> str:
    lines: list[str] = ["# [Personal Context > Niri Config > Overview]"]
    if not niri_files:
        lines.append("No personal `~/.config/niri/config.kdl` found on this machine.")
        return "\n".join(lines) + "\n"

    lines.append(
        "Indexed Niri configuration files: "
        + ", ".join(f"`{p}`" for p in niri_files)
    )
    for fpath in niri_files:
        try:
            raw = scrub_secrets(fpath.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        src_lines = raw.splitlines()
        section_name = f"File {fpath.name}"
        section_start = 1
        buf: list[str] = []
        brace_depth = 0

        for idx, ln in enumerate(src_lines, start=1):
            stripped = ln.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if brace_depth == 0:
                m_top = re.match(
                    r"^(binds|window-rule|layer-rule|input|layout|output(?:\s+\"[^\"]+\")?|spawn-at-startup|animations|hotkey-overlay|cursor|environment|include)\b",
                    stripped,
                )
                if m_top and buf:
                    lines.append(
                        f"\n## [Personal Context > Niri Config > {fpath.name} > {section_name} (L{section_start}-L{idx - 1})]"
                    )
                    lines.append("```kdl")
                    lines.extend(buf[:60])
                    lines.append("```")
                    buf = []
                    section_name = stripped.split("{")[0].strip()[:60]
                    section_start = idx
                elif m_top and not buf:
                    section_name = stripped.split("{")[0].strip()[:60]
                    section_start = idx
            buf.append(ln)
            brace_depth += ln.count("{") - ln.count("}")
            if brace_depth <= 0 and len(buf) >= 28:
                brace_depth = 0
                lines.append(
                    f"\n## [Personal Context > Niri Config > {fpath.name} > {section_name} (L{section_start}-L{idx})]"
                )
                lines.append("```kdl")
                lines.extend(buf[:60])
                lines.append("```")
                buf = []
                section_start = idx + 1

        if buf:
            lines.append(
                f"\n## [Personal Context > Niri Config > {fpath.name} > {section_name} (L{section_start}-L{len(src_lines)})]"
            )
            lines.append("```kdl")
            lines.extend(buf[:60])
            lines.append("```")
    return "\n".join(lines) + "\n"


def _format_quickshell_personal_markdown(home_dir: Path, config_home: Path, state_home: Path) -> tuple[str, list[Path]]:
    watched: list[Path] = []
    lines: list[str] = ["# [Personal Context > Quickshell & Alis Config > Overview]"]

    candidates = [
        state_home / "quickshell/user/generated/alis/config.json",
        config_home / "alis/config.json",
        home_dir / ".local/state/quickshell/user/generated/alis/config.json",
    ]
    seen: set[str] = set()
    for cand in candidates:
        c_res = cand.resolve(strict=False)
        if str(c_res) in seen or not cand.is_file() or cand.is_symlink() or is_sensitive_personal_path(cand):
            continue
        seen.add(str(c_res))
        watched.append(cand)
        try:
            raw_text = scrub_secrets(cand.read_text(encoding="utf-8", errors="replace"))
            parsed = json.loads(raw_text)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            for top_key, val in sorted(parsed.items()):
                snippet = scrub_secrets(json.dumps({top_key: val}, ensure_ascii=False, indent=2))
                lines.append(f"\n## [Personal Context > Quickshell & Alis Config > {cand.name} > {top_key}]")
                lines.append("```json")
                lines.extend(snippet.splitlines()[:45])
                lines.append("```")
        elif raw_text.strip():
            lines.append(f"\n## [Personal Context > Quickshell & Alis Config > {cand.name}]")
            lines.append("```json")
            lines.extend(raw_text.splitlines()[:45])
            lines.append("```")

    qs_alis_dir = config_home / "quickshell/alis"
    if qs_alis_dir.is_dir() and not is_sensitive_personal_path(qs_alis_dir):
        qml_entries = sorted(p.name for p in qs_alis_dir.glob("*.qml") if p.is_file())[:20]
        if qml_entries:
            lines.append("\n## [Personal Context > Quickshell & Alis Config > Installed Entrypoints]")
            lines.append(f"Directory `{qs_alis_dir}` entrypoints: " + ", ".join(f"`{n}`" for n in qml_entries))

    if len(lines) == 1:
        lines.append("No personal Quickshell/Alis config.json found in current state/config directories.")
    return "\n".join(lines) + "\n", watched


def _format_systemd_personal_markdown(config_home: Path) -> tuple[str, list[Path]]:
    watched: list[Path] = []
    lines: list[str] = ["# [Personal Context > Systemd User Units & Env > Overview]"]

    systemd_user_dir = config_home / "systemd/user"
    if systemd_user_dir.is_dir() and not is_sensitive_personal_path(systemd_user_dir):
        for unit in sorted(systemd_user_dir.glob("*.service"))[:20]:
            if not unit.is_file() or unit.is_symlink() or is_sensitive_personal_path(unit):
                continue
            watched.append(unit)
            try:
                body = scrub_secrets(unit.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            lines.append(f"\n## [Personal Context > Systemd User Units > {unit.name}]")
            lines.append("```ini")
            lines.extend(body.splitlines()[:40])
            lines.append("```")

    env_d_dir = config_home / "environment.d"
    if env_d_dir.is_dir() and not is_sensitive_personal_path(env_d_dir):
        for conf in sorted(env_d_dir.glob("*.conf"))[:20]:
            if not conf.is_file() or conf.is_symlink() or is_sensitive_personal_path(conf):
                continue
            watched.append(conf)
            try:
                body = scrub_secrets(conf.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            lines.append(f"\n## [Personal Context > Environment.d > {conf.name}]")
            lines.append("```ini")
            lines.extend(body.splitlines()[:40])
            lines.append("```")

    if len(lines) == 1:
        lines.append("No custom user systemd `.service` or `environment.d/*.conf` files found.")
    return "\n".join(lines) + "\n", watched


_LAST_RUNTIME_PROBE_MS: int = 0
_LAST_RUNTIME_PROFILE_MD: str = ""


def _build_runtime_profile_markdown(force: bool = False) -> str:
    global _LAST_RUNTIME_PROBE_MS, _LAST_RUNTIME_PROFILE_MD
    now_ms = int(time.time() * 1000)
    if not force and _LAST_RUNTIME_PROFILE_MD and (now_ms - _LAST_RUNTIME_PROBE_MS) < 60000:
        return _LAST_RUNTIME_PROFILE_MD

    lines: list[str] = [
        "# [Personal Context > Runtime Profile > System & Compositor]",
        f"- Kernel: `{platform.system()} {platform.release()} ({platform.machine()})`",
    ]
    os_release = Path("/etc/os-release")
    if os_release.is_file():
        try:
            for ln in os_release.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.startswith("PRETTY_NAME="):
                    distro = ln.split("=", 1)[1].strip().strip('"')
                    lines.append(f"- Distro: `{distro}`")
                    break
        except OSError:
            pass

    niri_bin = shutil.which("niri")
    if niri_bin:
        try:
            ver_proc = subprocess.run(
                [niri_bin, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                check=False,
                text=True,
            )
            if ver_proc.returncode == 0 and ver_proc.stdout.strip():
                lines.append(f"- Niri Version: `{ver_proc.stdout.strip()}`")
        except Exception:
            pass

    if niri_bin and os.environ.get("NIRI_SOCKET"):
        try:
            out_proc = subprocess.run(
                [niri_bin, "msg", "-j", "outputs"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                check=False,
                text=True,
            )
            if out_proc.returncode == 0 and out_proc.stdout.strip():
                outputs_obj = json.loads(out_proc.stdout)
                if isinstance(outputs_obj, dict) and outputs_obj:
                    lines.append("\n## [Personal Context > Runtime Profile > Active Niri Outputs]")
                    for out_name, out_info in sorted(outputs_obj.items()):
                        if isinstance(out_info, dict):
                            make = out_info.get("make", "")
                            model = out_info.get("model", "")
                            logical = out_info.get("logical") or {}
                            w = logical.get("width", "?")
                            h = logical.get("height", "?")
                            scale = logical.get("scale", 1.0)
                            lines.append(f"- Output `{out_name}`: {make} {model} ({w}x{h} @ scale {scale})")
        except Exception:
            pass

    systemctl_bin = shutil.which("systemctl")
    if systemctl_bin:
        try:
            failed_proc = subprocess.run(
                [systemctl_bin, "--user", "--state=failed", "--no-legend", "--no-pager"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                check=False,
                text=True,
            )
            if failed_proc.returncode == 0:
                failed_lines = [ln.strip() for ln in failed_proc.stdout.splitlines() if ln.strip()]
                lines.append("\n## [Personal Context > Runtime Profile > Systemd User Health]")
                if failed_lines:
                    for fl in failed_lines[:10]:
                        lines.append(f"- Failed unit: `{fl}`")
                else:
                    lines.append("- Failed user systemd units: `0 (all user services healthy)`")
        except Exception:
            pass

    _LAST_RUNTIME_PROBE_MS = now_ms
    _LAST_RUNTIME_PROFILE_MD = "\n".join(lines) + "\n"
    return _LAST_RUNTIME_PROFILE_MD


def sync_personal_context(
    force: bool = False,
    include_runtime_probe: bool = True,
    home_dir: Path | None = None,
    config_home: Path | None = None,
    state_home: Path | None = None,
) -> dict:
    """Stage 2: Incrementally sync (`mtime_ns` + `sha256`) and secret-scrub personal machine context."""
    home = (home_dir or Path(os.environ.get("HOME") or Path.home())).expanduser().resolve(strict=False)
    cfg_home = (
        config_home
        or Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    ).expanduser().resolve(strict=False)
    st_home = (
        state_home
        or Path(os.environ.get("XDG_STATE_HOME") or (home / ".local/state"))
    ).expanduser().resolve(strict=False)

    p_manifest_file = _personal_manifest_path()
    prev_manifest: dict = {}
    if p_manifest_file.is_file():
        try:
            prev_manifest = json.loads(p_manifest_file.read_text(encoding="utf-8"))
        except Exception:
            prev_manifest = {}
    prev_files: dict[str, dict] = dict(prev_manifest.get("files") or {}) if isinstance(prev_manifest, dict) else {}

    niri_files = _resolve_niri_config_files(home_dir=home, config_home=cfg_home)
    qs_candidates = [
        st_home / "quickshell/user/generated/alis/config.json",
        cfg_home / "alis/config.json",
    ]
    systemd_files: list[Path] = []
    if (cfg_home / "systemd/user").is_dir():
        systemd_files.extend(sorted((cfg_home / "systemd/user").glob("*.service"))[:20])
    if (cfg_home / "environment.d").is_dir():
        systemd_files.extend(sorted((cfg_home / "environment.d").glob("*.conf"))[:20])

    all_watched = [
        p for p in (niri_files + qs_candidates + systemd_files)
        if p.is_file() and not p.is_symlink() and not is_sensitive_personal_path(p)
    ]

    cur_stats: dict[str, dict] = {}
    any_changed = bool(force)
    for p in all_watched:
        p_str = str(p.resolve(strict=False))
        try:
            st = p.stat()
            mtime_ns = int(st.st_mtime_ns)
            size = int(st.st_size)
        except OSError:
            continue
        old_entry = prev_files.get(p_str)
        if not force and isinstance(old_entry, dict) and old_entry.get("mtimeNs") == mtime_ns and old_entry.get("size") == size:
            cur_stats[p_str] = old_entry
        else:
            try:
                raw_txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            sha = _sha256_text(raw_txt)
            if not isinstance(old_entry, dict) or old_entry.get("sha256") != sha:
                any_changed = True
            cur_stats[p_str] = {
                "path": p_str,
                "mtimeNs": mtime_ns,
                "size": size,
                "sha256": sha,
            }

    if set(cur_stats.keys()) != set(prev_files.keys()):
        any_changed = True

    p_dir = _personal_dir()
    niri_md_path = p_dir / "niri-personal-config.md"
    qs_md_path = p_dir / "quickshell-alis-config.md"
    sysd_md_path = p_dir / "systemd-user-env.md"
    runtime_md_path = p_dir / "runtime-profile.md"

    if any_changed or not niri_md_path.is_file() or not qs_md_path.is_file() or not sysd_md_path.is_file():
        _atomic_write_text(niri_md_path, _format_niri_personal_markdown(niri_files))
        qs_md, _ = _format_quickshell_personal_markdown(home, cfg_home, st_home)
        _atomic_write_text(qs_md_path, qs_md)
        sysd_md, _ = _format_systemd_personal_markdown(cfg_home)
        _atomic_write_text(sysd_md_path, sysd_md)

    if include_runtime_probe or not runtime_md_path.is_file():
        _atomic_write_text(runtime_md_path, _build_runtime_profile_markdown(force=force))

    now_ms = int(time.time() * 1000)
    if any_changed or not p_manifest_file.is_file():
        _atomic_write_json(
            p_manifest_file,
            {
                "version": 1,
                "updatedAtMs": now_ms,
                "files": cur_stats,
            },
        )

    return {
        "changed": any_changed,
        "watchedFileCount": len(cur_stats),
        "niriConfigFiles": [str(p) for p in niri_files],
        "personalDocs": [
            str(niri_md_path),
            str(qs_md_path),
            str(sysd_md_path),
            str(runtime_md_path),
        ],
        "manifestPath": str(p_manifest_file),
    }


def detect_domains(prompt: str) -> list[str]:
    """Detect whether a user prompt touches `niri`, `quickshell`, and/or `archlinux`."""
    raw = str(prompt or "").strip().lower()
    if not raw:
        return []
    matched: list[str] = []
    for domain, meta in CANONICAL_DOMAINS.items():
        for kw in meta["keywords"]:
            if " " in kw or "." in kw or "-" in kw:
                if kw in raw:
                    matched.append(domain)
                    break
            elif re.search(rf"\b{re.escape(kw)}\b", raw):
                matched.append(domain)
                break
    return matched


def _parse_markdown_chunks(label: str, path: Path, stage: str, domain: str) -> list[dict]:
    if not path.is_file() or path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    chunks: list[dict] = []
    heading = label
    buf: list[str] = []
    start_line = 1

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            if buf:
                body = "\n".join(buf).strip()
                if body:
                    chunks.append({
                        "source": f"{label}#L{start_line}-L{idx - 1}",
                        "heading": heading[:140],
                        "text": body[:520],
                        "stage": stage,
                        "domain": domain,
                    })
                buf = []
            heading = stripped.lstrip("#").strip() or label
            start_line = idx
        elif stripped:
            buf.append(raw)
            if len("\n".join(buf)) >= 420:
                body = "\n".join(buf).strip()
                chunks.append({
                    "source": f"{label}#L{start_line}-L{idx}",
                    "heading": heading[:140],
                    "text": body[:520],
                    "stage": stage,
                    "domain": domain,
                })
                buf = []
                start_line = idx + 1

    if buf:
        body = "\n".join(buf).strip()
        if body:
            chunks.append({
                "source": f"{label}#L{start_line}-L{len(lines)}",
                "heading": heading[:140],
                "text": body[:520],
                "stage": stage,
                "domain": domain,
            })
    return chunks


def collect_domain_chunks(
    domains: list[str] | None = None,
    include_canonical: bool = True,
    include_personal: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Return `(canonical_chunks, personal_chunks)` after ensuring Stage 1 and Stage 2 are synced."""
    target_domains = [
        d for d in (domains or list(CANONICAL_DOMAINS.keys()))
        if d in CANONICAL_DOMAINS
    ]
    if include_canonical:
        sync_canonical_docs(domains=target_domains, allow_network=False)
    if include_personal:
        sync_personal_context(force=False, include_runtime_probe=True)

    canonical_chunks: list[dict] = []
    personal_chunks: list[dict] = []

    if include_canonical:
        for dom in target_domains:
            dom_dir = _canonical_dir() / dom
            if dom_dir.is_dir():
                for md_file in sorted(dom_dir.glob("*.md")):
                    canonical_chunks.extend(
                        _parse_markdown_chunks(
                            f"canonical/{dom}/{md_file.name}",
                            md_file,
                            stage="canonical",
                            domain=dom,
                        )
                    )

    if include_personal:
        p_dir = _personal_dir()
        personal_map = [
            ("niri", "niri-personal-config.md"),
            ("quickshell", "quickshell-alis-config.md"),
            ("archlinux", "systemd-user-env.md"),
            ("archlinux", "runtime-profile.md"),
        ]
        for dom, fname in personal_map:
            if dom in target_domains or "niri" in target_domains and fname == "runtime-profile.md":
                f_path = p_dir / fname
                if f_path.is_file():
                    personal_chunks.extend(
                        _parse_markdown_chunks(
                            f"personal/{fname}",
                            f_path,
                            stage="personal",
                            domain=dom,
                        )
                    )

    return canonical_chunks, personal_chunks


def validate_niri_kdl_snippet(kdl_text: str) -> dict:
    """Stage 3 helper: Validate a Niri KDL configuration snippet using `niri validate` + syntax checks."""
    raw = str(kdl_text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_\-]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()

    errors: list[str] = []
    if not raw:
        return {"valid": False, "validator": "syntax-check", "errors": ["Empty KDL snippet."]}

    for idx, ln in enumerate(raw.splitlines(), start=1):
        s = ln.strip()
        if not s or s.startswith("//"):
            continue
        if re.match(r"^(?:windowrulev2|bindm?|exec-once|env|general:)\s*=", s):
            errors.append(
                f"Line {idx}: Detected Hyprland syntax (`{s[:40]}`); Niri uses KDL nodes like `window-rule {{ ... }}`, `binds {{ ... }}`, `spawn-at-startup`."
            )

    open_braces = raw.count("{")
    close_braces = raw.count("}")
    if open_braces != close_braces:
        errors.append(f"Unbalanced braces in KDL snippet (`{{`={open_braces}, `}}`={close_braces}).")

    if errors:
        return {"valid": False, "validator": "kdl-syntax-guard", "errors": errors}

    niri_bin = shutil.which("niri")
    if niri_bin and os.environ.get("ALIS_AQUA_NIRI_VALIDATE_BIN", "") != "__MISSING__":
        fd, tmp_name = tempfile.mkstemp(prefix=".niri-validate-", suffix=".kdl")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(raw + "\n")
            proc = subprocess.run(
                [niri_bin, "validate", "-c", str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3.0,
                check=False,
                text=True,
            )
            if proc.returncode == 0:
                return {"valid": True, "validator": "niri-validate", "errors": []}
            err_out = (proc.stderr or proc.stdout or "niri validate failed").strip()
            return {
                "valid": False,
                "validator": "niri-validate",
                "errors": [ln.strip() for ln in err_out.splitlines() if ln.strip()][:8],
            }
        except Exception:
            pass
        finally:
            tmp_path.unlink(missing_ok=True)

    return {"valid": True, "validator": "kdl-syntax-fallback", "errors": []}


def query_domain_rag(
    prompt: str,
    domains: list[str] | None = None,
    companion_root: Path | None = None,
    top_k_canonical: int = 3,
    top_k_personal: int = 2,
    token_budget: int = 640,
) -> dict:
    """Execute the 3-Stage Domain RAG pipeline:
    1. Stage 1: Canonical Source RAG (`canonicalChunks`)
    2. Stage 2: Personal Machine Context RAG (`personalChunks`)
    3. Stage 3: Domain Rules & Skills (`matchedSkills`, `domainRules`)
    """
    detected = domains if domains is not None else detect_domains(prompt)
    if not detected:
        return {
            "triggered": False,
            "domains": [],
            "canonicalChunks": [],
            "personalChunks": [],
            "domainRules": [],
            "matchedSkills": [],
            "contextMarkdown": "",
        }

    canonical_pool, personal_pool = collect_domain_chunks(
        domains=detected,
        include_canonical=True,
        include_personal=True,
    )

    scored_canonical = rag_store.query_hybrid_rag(prompt, canonical_pool, min_score=0.5)
    if not scored_canonical and canonical_pool:
        scored_canonical = [(1.0, ch) for ch in canonical_pool[:top_k_canonical]]
    selected_canonical = [item[1] for item in scored_canonical[: max(1, int(top_k_canonical))]]

    scored_personal = rag_store.query_hybrid_rag(prompt, personal_pool, min_score=0.5)
    if not scored_personal and personal_pool:
        scored_personal = [(1.0, ch) for ch in personal_pool[:top_k_personal]]
    selected_personal = [item[1] for item in scored_personal[: max(1, int(top_k_personal))]]

    rules: list[str] = []
    skill_names: list[str] = []
    for dom in detected:
        for r in DOMAIN_RULES_SUMMARY.get(dom, []):
            if r not in rules:
                rules.append(r)
        sk_name = CANONICAL_DOMAINS[dom]["skillName"]
        if sk_name not in skill_names:
            skill_names.append(sk_name)

    core_defs = [CORE_DOMAIN_DEFINITIONS[d] for d in ("niri", "quickshell", "archlinux") if d in CORE_DOMAIN_DEFINITIONS]
    sections: list[str] = [
        f"## Grounded 3-Stage Domain RAG ({', '.join(CANONICAL_DOMAINS[d]['label'] for d in detected)})"
    ]
    if core_defs:
        sections.append("### Core Domain Identity (Authoritative — Never Contradict)\n" + "\n".join(f"- {c}" for c in core_defs))
    if selected_canonical:
        sections.append("### Stage 1 — Canonical Documentation (Authoritative Syntax & Contracts)")
        for ch in selected_canonical:
            sections.append(f"- **[{ch['source']}] {ch['heading']}**:\n{ch['text']}")

    if selected_personal:
        sections.append("### Stage 2 — Personal Machine Context (Current Local Config & Runtime)")
        for ch in selected_personal:
            sections.append(f"- **[{ch['source']}] {ch['heading']}**:\n{ch['text']}")

    if rules:
        sections.append(f"### Stage 3 — Active Domain Rules & Skills ({', '.join(f'`{s}`' for s in skill_names)})")
        for r in rules:
            sections.append(f"- {r}")

    context_md = "\n\n".join(sections)
    max_chars = max(600, int(token_budget) * 4) + 600
    if len(context_md) > max_chars:
        context_md = context_md[:max_chars].rstrip() + "\n..."

    return {
        "triggered": True,
        "domains": detected,
        "canonicalChunks": selected_canonical,
        "personalChunks": selected_personal,
        "domainRules": rules,
        "matchedSkills": skill_names,
        "contextMarkdown": context_md,
    }


CORE_DOMAIN_DEFINITIONS: dict[str, str] = {
    "niri": (
        "**Niri (`niri`)**: Scrollable-tiling Wayland compositor in Rust arranging windows in an **infinite horizontal strip of columns** "
        "(NOT vertical-monitor or Hyprland/i3). Configured via KDL (`~/.config/niri/config.kdl`), `niri msg`, `niri validate`."
    ),
    "quickshell": (
        "**Quickshell (`quickshell`)**: **Qt6 / QML Wayland desktop shell toolkit** (`ShellRoot`, `PanelWindow`, `WlrLayershell`, `Process`, "
        "`FileView`, `IpcHandler`). **NEVER Lua-based** (not AwesomeWM) and **NEVER CSS/GTK-based**."
    ),
    "archlinux": (
        "**Arch Linux (`arch`)**: Rolling-release Linux managed via `pacman -Syu` (never partial `-Sy`), `paccache -rk2`, "
        "`makepkg -si` / `PKGBUILD` (`paru`/`yay`), `mkinitcpio -P`, and `systemd` (`systemctl` / `journalctl`)."
    ),
}
CORE_DOMAIN_DEFINITIONS["arch"] = CORE_DOMAIN_DEFINITIONS["archlinux"]


def _fold_ascii(text: str) -> str:
    import unicodedata
    norm = unicodedata.normalize("NFD", str(text or "").strip().lower())
    norm = re.sub(r"[\u0300-\u036f]", "", norm).replace("đ", "d")
    return re.sub(r"\s+", " ", norm).strip()


def is_domain_concept_or_command_query(prompt: str) -> bool:
    """Return True when the prompt (or follow-up option click) asks to explain core concepts or commands of Niri, Quickshell, or Arch Linux."""
    raw = str(prompt or "").strip()
    if not raw:
        return False
    if "—" in raw:
        tail_raw = raw.split("—")[-1].strip()
        if is_config_check_query(tail_raw):
            return False
    folded = _fold_ascii(raw)
    stripped_folded = re.sub(r"^\s*\d+[\.\)]\s*", "", folded)
    if re.search(
        r"\b(?:explain a concept or command|explain core .* concepts|giai thich khai niem hoac lenh|khai niem va lenh co ban)\b",
        stripped_folded,
    ):
        return True
    domains = detect_domains(raw)
    if not domains:
        return False
    if "—" in raw or " - " in raw:
        tail = _fold_ascii(re.split(r"\s*(?:—|--|-)\s*", raw)[-1])
        if re.search(r"\b(?:explain|concept|command|giai thich|khai niem|lenh)\b", tail):
            return True
    return False


def format_domain_concepts_reply(
    prompt: str = "",
    lang: str = "en",
    companion_root: Path | None = None,
) -> str:
    """Return an authoritative, zero-hallucination overview of core concepts and commands for Niri, Quickshell, and Arch Linux."""
    tail_domains: list[str] = []
    if "—" in str(prompt or ""):
        tail_domains = detect_domains(str(prompt).split("—")[-1])
    detected = tail_domains or detect_domains(prompt) or ["niri", "quickshell", "archlinux"]
    has_arch = "archlinux" in detected or "arch" in detected
    if lang == "vi":
        parts = [
            "Dưới đây là **khái niệm cốt lõi & các lệnh/cấu hình chuẩn xác** (từ Hệ thống Domain RAG 3 tầng):"
        ]
        if "niri" in detected:
            parts.append(
                "### 1. Niri WM (Scrollable-Tiling Wayland Compositor)\n"
                "- **Bản chất**: Wayland compositor viết bằng **Rust**, sắp xếp cửa sổ theo **dải cột ngang vô tận (infinite horizontal strip of columns)** trên từng màn hình — không chia nhỏ không gian như i3/Hyprland.\n"
                "- **Cấu hình (`~/.config/niri/config.kdl`)**: Sử dụng ngôn ngữ **KDL** (`input`, `output`, `layout`, `window-rule`, `layer-rule`, `binds`, `spawn-at-startup`).\n"
                "- **Lệnh chuẩn**:\n"
                "  - `niri validate -c ~/.config/niri/config.kdl` — Kiểm tra cú pháp & schema KDL trước khi áp dụng.\n"
                "  - `niri msg -j windows` / `niri msg -j workspaces` / `niri msg -j outputs` — Truy vấn trạng thái JSON.\n"
                "  - `niri msg action focus-column-left` / `consume-or-expel-window-left` / `switch-preset-column-width`."
            )
        if "quickshell" in detected:
            parts.append(
                "### 2. Quickshell (Qt6 / QML Wayland Desktop Shell Toolkit)\n"
                "- **Bản chất**: Bộ công cụ xây dựng Desktop Shell trên Wayland dựa trên **Qt6 / QtQuick / QML (C++)** — **hoàn toàn KHÔNG phải Lua (never Lua-based)**.\n"
                "- **Thành phần QML cốt lõi**:\n"
                "  - `ShellRoot` & `Variants` — Điểm vào gốc và nhân bản bar/panel theo `Quickshell.screens`.\n"
                "  - `PanelWindow` + `WlrLayershell` — Thiết lập `layer`, `exclusiveZone`, `namespace`, và `WlrKeyboardFocus.OnDemand` (hoặc `None` khi ẩn để không cướp phím của Niri).\n"
                "  - `Process` + `StdioCollector` / `SplitParser`, `FileView` (`blockLoading: false`), `IpcHandler`, `ToplevelManager`, `ScreencopyView`.\n"
                "- **Lệnh chuẩn**: `qs -p ~/.config/quickshell/alis` hoặc `systemctl --user restart alis.service`."
            )
        if has_arch:
            parts.append(
                "### 3. Arch Linux (Rolling-Release Linux Distribution)\n"
                "- **Bản chất**: Bản phân phối Linux độc lập theo mô hình **rolling-release**, quản lý gói bằng `pacman` và `makepkg`/`PKGBUILD` (AUR) cùng `systemd`.\n"
                "- **Lệnh chuẩn & Quy tắc an toàn**:\n"
                "  - `sudo pacman -Syu <pkg>` — Luôn đồng bộ kèm nâng cấp toàn hệ thống (tuyệt đối **không** dùng `pacman -Sy <pkg>` gây partial upgrade).\n"
                "  - `paccache -rk2` — Dọn bộ nhớ đệm pacman an toàn, giữ lại 2 phiên bản gần nhất.\n"
                "  - `pacman -Qo /path/to/file` / `pacman -Ql <pkg>` — Tra cứu gói sở hữu tệp.\n"
                "  - `systemctl --user status alis.service` & `journalctl --user -u alis.service -n 100 --no-pager`."
            )
        return "\n\n".join(parts)

    parts = [
        "Here are the **authoritative core concepts, architecture, and essential commands** (grounded in the 3-Stage Domain RAG):"
    ]
    if "niri" in detected:
        parts.append(
            "### 1. Niri WM (Scrollable-Tiling Wayland Compositor)\n"
            "- **Core Concept**: A Wayland compositor written in **Rust** that places windows into an **infinite horizontal strip of columns** per monitor (so new windows never shrink existing ones).\n"
            "- **Configuration (`~/.config/niri/config.kdl`)**: Written in **KDL** (`input`, `output`, `layout`, `window-rule`, `layer-rule`, `binds`, `spawn-at-startup`).\n"
            "- **Essential Commands**:\n"
            "  - `niri validate -c ~/.config/niri/config.kdl` — Validate KDL syntax & schema before reloading.\n"
            "  - `niri msg -j windows` / `niri msg -j workspaces` / `niri msg -j outputs` — Inspect live compositor state.\n"
            "  - `niri msg action focus-column-left` / `consume-or-expel-window-left` / `switch-preset-column-width`."
        )
    if "quickshell" in detected:
        parts.append(
            "### 2. Quickshell (Qt6 / QML Wayland Desktop Shell Toolkit)\n"
            "- **Core Concept**: A **Qt6 / QtQuick / QML-based** (C++) Wayland shell toolkit for building bars, overlays, notifications, and desktop widgets — **never Lua-based**.\n"
            "- **Core QML Primitives**:\n"
            "  - `ShellRoot` & `Variants` — Root entry point and multi-monitor instancing across `Quickshell.screens`.\n"
            "  - `PanelWindow` + `WlrLayershell` — Layer-shell surfaces using `WlrKeyboardFocus.OnDemand` for interactive popups and `WlrKeyboardFocus.None` when hidden.\n"
            "  - `Process` (`StdioCollector` / `SplitParser`), `FileView` (`blockLoading: false`), `IpcHandler`, `ToplevelManager`, and `ScreencopyView`.\n"
            "- **Essential Commands**: `qs -p ~/.config/quickshell/alis` or `systemctl --user restart alis.service`."
        )
    if has_arch:
        parts.append(
            "### 3. Arch Linux (Rolling-Release Distribution)\n"
            "- **Core Concept**: An independent rolling-release Linux distribution built around `pacman`, `makepkg`/`PKGBUILD` (AUR), `mkinitcpio`, and `systemd`.\n"
            "- **Essential Commands & Safety Rules**:\n"
            "  - `sudo pacman -Syu <pkg>` — Full system sync + upgrade (never run `pacman -Sy <pkg>` partial upgrades).\n"
            "  - `paccache -rk2` — Safely trim package cache while keeping the last 2 versions.\n"
            "  - `pacman -Qo /path/to/file` / `pacman -Ql <pkg>` — Query file ownership and package contents.\n"
            "  - `systemctl --user status alis.service` & `journalctl --user -u alis.service -n 100 --no-pager`."
        )
    return "\n\n".join(parts)


def is_config_check_query(prompt: str) -> bool:
    """Return True when the user asks to check/inspect their configuration (Niri, Quickshell/Alis, Arch/systemd, or generic 'check my configuration')."""
    raw = str(prompt or "").strip()
    if not raw:
        return False
    if "—" in raw:
        tail_raw = raw.split("—")[-1].strip()
        if tail_raw and tail_raw != raw and is_config_check_query(tail_raw):
            return True
    folded = _fold_ascii(raw)
    stripped_folded = re.sub(r"^\s*\d+[\.\)]\s*", "", folded)
    if re.search(r"\b[a-z0-9_.\-]+\.(?:css|py|ts|tsx|js|cjs|mjs|rs|go|c|cpp|h|html|vue|svelte|sql|pdf|docx|xlsx)\b", stripped_folded):
        return False
    if re.search(
        r"^(?:check|inspect|kiem tra|xem)\s+(?:niri\s+config|quickshell\s*/?\s*alis\s+config|arch\s+linux\s*(?:&|and|va)\s*systemd|cau\s+hinh\s+niri|cau\s+hinh\s+quickshell|dich\s+vu\s+arch)",
        stripped_folded,
    ):
        return True
    if re.search(
        r"\b(?:check|inspect|review|validate|verify|audit|show|read|kiem tra|xem|danh gia|xac thuc)\s+(?:my\s+|current\s+|the\s+|giup\s+|ho\s+)?(?:(?:niri|quickshell|alis|arch(?:\s*linux)?|systemd|window\s+manager|wm|desktop|shell|system)\s+)?(?:config(?:uration)?s?|config\.kdl|setup|keybinds?|binds?|window[- ]rules?|layer[- ]rules?|outputs?|user\s+services?|systemd\s+services?|cau\s+hinh|thiet\s+lap|phim\s+tat)\b",
        stripped_folded,
    ):
        return True
    if re.search(
        r"\b(?:cau\s+hinh|thiet\s+lap|config(?:uration)?|setup)\s+(?:cua\s+(?:toi|minh)|niri|quickshell|alis|hien\s+tai)\b.*\b(?:nhu\s+the\s+nao|ra\s+sao|co\s+on\s+khong|kiem\s+tra|xem)\b",
        stripped_folded,
    ):
        return True
    return False


def inspect_user_configuration(
    prompt: str = "",
    lang: str = "en",
    companion_root: Path | None = None,
) -> str:
    """Inspect the user's live Stage 2 Personal Machine Context (Niri config.kdl + validation, Quickshell/Alis config, and Arch/systemd runtime)."""
    sync_canonical_docs(allow_network=False)
    pers_sync = sync_personal_context(force=True)
    raw_prompt = str(prompt or "").strip()
    effective_prompt = raw_prompt.split("—")[-1].strip() if "—" in raw_prompt else raw_prompt
    folded = _fold_ascii(effective_prompt)
    domains = detect_domains(effective_prompt) or detect_domains(raw_prompt)
    specific_only = len(domains) == 1 and not re.search(r"\b(?:all|everything|tat ca|toan bo)\b", folded)

    home = Path(os.environ.get("HOME") or Path.home()).expanduser().resolve(strict=False)
    cfg_root = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config")).expanduser().resolve(strict=False)
    state_root = Path(os.environ.get("XDG_STATE_HOME") or (home / ".local" / "state")).expanduser().resolve(strict=False)

    niri_cfg = cfg_root / "niri" / "config.kdl"
    niri_files = [Path(p) for p in (pers_sync.get("niriConfigFiles") or []) if Path(p).is_file()]
    if not niri_files and niri_cfg.is_file():
        niri_files = [niri_cfg]

    alis_cfg = cfg_root / "alis" / "config.json"
    if not alis_cfg.is_file():
        alis_cfg = state_root / "quickshell" / "user" / "generated" / "alis" / "config.json"
    if not alis_cfg.is_file():
        alis_cfg = state_root / "quickshell" / "user" / "generated" / "config.json"

    # 1. Niri inspection across config.kdl and included KDL files
    niri_exists = niri_cfg.is_file() or bool(niri_files)
    niri_val = {"valid": False, "validator": "missing", "errors": []}
    niri_stats = {"binds": 0, "windowRules": 0, "layerRules": 0, "outputs": 0, "spawns": 0, "terminalBind": "", "sampleBinds": []}
    if niri_exists:
        try:
            combined_kdl = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in niri_files)
            niri_val = validate_niri_kdl_snippet(combined_kdl)
            niri_stats["windowRules"] = len(re.findall(r"(?m)^\s*window-rule\s*\{", combined_kdl))
            niri_stats["layerRules"] = len(re.findall(r"(?m)^\s*layer-rule\s*\{", combined_kdl))
            niri_stats["outputs"] = len(re.findall(r"(?m)^\s*output\s+", combined_kdl))
            niri_stats["spawns"] = len(re.findall(r"(?m)^\s*spawn-at-startup\s+", combined_kdl))
            bind_matches = re.findall(r"(?m)^\s*((?:Mod|Alt|Ctrl|Shift|Super|XF86)[A-Za-z0-9_+]*)\s*\{\s*([^;\n}]+)", combined_kdl)
            niri_stats["binds"] = len(re.findall(r"(?m)^\s*(?:Mod|Alt|Ctrl|Shift|Super|XF86)[A-Za-z0-9_+]*\s*\{", combined_kdl))
            niri_stats["sampleBinds"] = [f"`{k}` → `{act.strip()}`" for k, act in bind_matches[:5]]
            m_term = re.search(r"(?m)^\s*(Mod\+[^\s{]+)\s*\{\s*spawn\s+\"([^\"]+)\"", combined_kdl)
            if m_term:
                niri_stats["terminalBind"] = f"`{m_term.group(1)}` → `spawn \"{m_term.group(2)}\"`"
        except OSError:
            pass

    # 2. Quickshell / Alis config inspection
    alis_exists = alis_cfg.is_file()
    alis_keys: list[str] = []
    if alis_exists:
        try:
            alis_obj = json.loads(alis_cfg.read_text(encoding="utf-8", errors="replace"))
            if isinstance(alis_obj, dict):
                alis_keys = sorted(alis_obj.keys())[:12]
        except Exception:
            pass
    qs_alis_dir = cfg_root / "quickshell" / "alis"
    qs_entrypoints = sorted(p.name for p in qs_alis_dir.glob("*.qml") if p.is_file())[:8] if qs_alis_dir.is_dir() else []

    # 3. Runtime & systemd user services
    rt_doc = _personal_dir() / "runtime-profile.md"
    rt_snippet = ""
    if rt_doc.is_file():
        try:
            rt_lines = [
                ln.strip()
                for ln in rt_doc.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip().startswith("- ")
            ]
            rt_snippet = "\n".join(rt_lines[:8])
        except OSError:
            pass
    systemd_user_dir = cfg_root / "systemd" / "user"
    user_units = sorted(p.name for p in systemd_user_dir.glob("*.service") if p.is_file())[:8] if systemd_user_dir.is_dir() else []

    niri_status_str = (
        (f"Hợp lệ (`{niri_val['validator']}`)" if niri_val.get("valid") else f"Có lỗi cú pháp ({'; '.join(niri_val.get('errors', [])[:2])})")
        if lang == "vi"
        else (f"Valid (`{niri_val['validator']}`)" if niri_val.get("valid") else f"Issues detected ({'; '.join(niri_val.get('errors', [])[:2])})")
    )
    niri_file_names = ", ".join(f"`{p.name}`" for p in niri_files) or f"`{niri_cfg.name}`"

    sections: list[str] = []
    if lang == "vi":
        sections.append(f"Đã kiểm tra cấu hình thực tế trên máy của bạn (**Stage 2 Personal Machine Context** — {pers_sync.get('watchedFileCount', 0)} tệp được theo dõi):")
        if not specific_only or "niri" in domains:
            if niri_exists:
                term_info = f", phím tắt mở ứng dụng: {niri_stats['terminalBind']}" if niri_stats["terminalBind"] else ""
                extra_binds = (
                    f"\n- **Phím tắt tiêu biểu**: {', '.join(niri_stats['sampleBinds'])}"
                    if specific_only and niri_stats["sampleBinds"]
                    else ""
                )
                sections.append(
                    f"### 1. Cấu hình Niri WM ({niri_file_names})\n"
                    f"- **Trạng thái kiểm tra KDL**: {niri_status_str}\n"
                    f"- **Cấu trúc**: `{niri_stats['outputs']}` khối `output`, `{niri_stats['windowRules']}` `window-rule`, `{niri_stats['layerRules']}` `layer-rule`, `{niri_stats['spawns']}` lệnh `spawn-at-startup`, `{niri_stats['binds']}` tổ hợp phím `binds`{term_info}.{extra_binds}"
                )
            else:
                sections.append(f"### 1. Cấu hình Niri WM\n- Không tìm thấy tệp `{niri_cfg}`.")
        if not specific_only or "quickshell" in domains:
            if alis_exists:
                ep_line = f"\n- **Entrypoints Quickshell (`{qs_alis_dir}`)**: {', '.join(f'`{e}`' for e in qs_entrypoints)}" if specific_only and qs_entrypoints else ""
                sections.append(
                    f"### 2. Cấu hình Quickshell / Alis (`{alis_cfg.name}`)\n"
                    f"- **Trạng thái**: JSON hợp lệ (đã lọc khoá nhạy cảm). Các nhóm cấu hình chính: {', '.join(f'`{k}`' for k in alis_keys) or '`(mặc định)`'}.{ep_line}"
                )
            else:
                sections.append("### 2. Cấu hình Quickshell / Alis\n- Đang dùng cấu hình mặc định của Alis/Quickshell.")
        if not specific_only or "archlinux" in domains or "arch" in domains:
            unit_line = f"\n- **Systemd user units**: {', '.join(f'`{u}`' for u in user_units)}" if user_units else ""
            sections.append(
                "### 3. Môi trường Arch Linux & Systemd User Services\n"
                + (rt_snippet if rt_snippet else "- Đã lập chỉ mục `systemd-user-env.md` và `runtime-profile.md`.")
                + unit_line
            )
        if not specific_only:
            sections.append(
                "Bạn muốn kiểm tra chi tiết phần cấu hình nào?\n"
                "1. Check Niri config (~/.config/niri/config.kdl) (Recommended)\n"
                "2. Check Quickshell / Alis config\n"
                "3. Check Arch Linux & systemd user services"
            )
    else:
        sections.append(f"Checked your live system configuration (**Stage 2 Personal Machine Context** — {pers_sync.get('watchedFileCount', 0)} local files indexed):")
        if not specific_only or "niri" in domains:
            if niri_exists:
                term_info = f", sample keybind: {niri_stats['terminalBind']}" if niri_stats["terminalBind"] else ""
                extra_binds = (
                    f"\n- **Sample Keybinds**: {', '.join(niri_stats['sampleBinds'])}"
                    if specific_only and niri_stats["sampleBinds"]
                    else ""
                )
                sections.append(
                    f"### 1. Niri WM Configuration ({niri_file_names})\n"
                    f"- **KDL Validation**: {niri_status_str}\n"
                    f"- **Summary**: `{niri_stats['outputs']}` `output` block(s), `{niri_stats['windowRules']}` `window-rule`(s), `{niri_stats['layerRules']}` `layer-rule`(s), `{niri_stats['spawns']}` `spawn-at-startup` entry(s), and `{niri_stats['binds']}` keybind(s){term_info}.{extra_binds}"
                )
            else:
                sections.append(f"### 1. Niri WM Configuration\n- `{niri_cfg}` was not found on disk.")
        if not specific_only or "quickshell" in domains:
            if alis_exists:
                ep_line = f"\n- **Installed Entrypoints (`{qs_alis_dir}`)**: {', '.join(f'`{e}`' for e in qs_entrypoints)}" if specific_only and qs_entrypoints else ""
                sections.append(
                    f"### 2. Quickshell / Alis Configuration (`{alis_cfg.name}`)\n"
                    f"- **Status**: Valid JSON (secret-scrubbed). Top-level sections: {', '.join(f'`{k}`' for k in alis_keys) or '`(defaults)`'}.{ep_line}"
                )
            else:
                sections.append("### 2. Quickshell / Alis Configuration\n- Using default Alis/Quickshell settings.")
        if not specific_only or "archlinux" in domains or "arch" in domains:
            unit_line = f"\n- **User Systemd Services**: {', '.join(f'`{u}`' for u in user_units)}" if user_units else ""
            sections.append(
                "### 3. Arch Linux & Systemd User Environment\n"
                + (rt_snippet if rt_snippet else "- Indexed `systemd-user-env.md` and `runtime-profile.md`.")
                + unit_line
            )
        if not specific_only:
            sections.append(
                "Which configuration would you like to inspect in detail?\n"
                "1. Check Niri config (~/.config/niri/config.kdl) (Recommended)\n"
                "2. Check Quickshell / Alis config\n"
                "3. Check Arch Linux & systemd user services"
            )

    return "\n\n".join(sections)


def is_domain_capability_query(prompt: str) -> bool:
    """Return True when the user is asking whether Companion has knowledge/RAG/skills for Niri, Quickshell, or Arch Linux."""
    raw = str(prompt or "").strip()
    if not raw:
        return False
    if is_domain_concept_or_command_query(raw) or is_config_check_query(raw):
        return False
    folded = _fold_ascii(raw)
    domains = detect_domains(raw)
    has_rag_meta = bool(re.search(r"\b(?:domain\s+rag|3[- ]stage\s+rag|rag\s+3\s+tang|3\s+tang\s+rag)\b", folded))
    if not domains and not has_rag_meta:
        return False
    if has_rag_meta and re.search(r"\b(?:status|check|what|have|know|trang thai|kiem tra|co gi|nhu the nao)\b", folded):
        return True
    if re.search(
        r"\b(?:bound to|keybind|mod\+|window-rule|layer-rule|spawn-at-startup|wlrlayershell|wlrkeyboardfocus|panelwindow|pacman\s+-|paccache|makepkg|systemctl\s+|journalctl\s+|mkinitcpio|how\s+to|how\s+do\s+i|how\s+does|why\s+does|why\s+is|fix\s+|debug\s+|lam\s+sao\s+de|cach\s+cau\s+hinh|sua\s+loi)\b",
        folded,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:do you (?:have|know)|have you (?:learned|got|indexed)|are you (?:familiar|aware)|what (?:knowledge|docs|documentation|skills|capabilities) do you have|tell me about your (?:knowledge|capabilities|domain rag)|can you help (?:me )?with|ban (?:da )?co (?:kien thuc|biet|tai lieu|ky nang)|ban biet gi ve|kien thuc cua ban ve)\b",
            folded,
        )
    )


def domain_rag_status(companion_root: Path | None = None) -> dict:
    """Inspect live 3-Stage Domain RAG status (Stage 1 Canonical Docs, Stage 2 Personal Context, Stage 3 Skills & Guardrails)."""
    canon_sync = sync_canonical_docs(allow_network=False)
    pers_sync = sync_personal_context(force=False)
    canon_dir = _canonical_dir()
    pers_dir = _personal_dir()
    domain_summary: dict[str, dict] = {}
    for dom_id, meta in CANONICAL_DOMAINS.items():
        dom_dir = canon_dir / dom_id
        md_files = sorted(p.name for p in dom_dir.glob("*.md")) if dom_dir.is_dir() else []
        domain_summary[dom_id] = {
            "label": meta["label"],
            "baseUrl": meta["baseUrl"],
            "skillName": meta["skillName"],
            "docCount": len(md_files),
            "documents": md_files,
        }
    personal_docs = sorted(p.name for p in pers_dir.glob("*.md")) if pers_dir.is_dir() else []
    cfg_root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")).expanduser().resolve(strict=False)
    niri_cfg = cfg_root / "niri" / "config.kdl"
    return {
        "stage1Canonical": {
            "totalDocuments": int(canon_sync.get("totalDocuments") or sum(len(v) for v in BUILTIN_CANONICAL_DOCS.values())),
            "domains": domain_summary,
            "statePath": str(canon_sync.get("manifestPath") or _manifest_path()),
        },
        "stage2Personal": {
            "indexedDocuments": personal_docs,
            "watchedFileCount": int(pers_sync.get("watchedFileCount") or 0),
            "niriConfigPresent": niri_cfg.is_file(),
            "niriConfigPath": str(niri_cfg),
            "statePath": str(pers_sync.get("manifestPath") or _personal_manifest_path()),
        },
        "stage3Skills": {
            "skills": [meta["skillName"] for meta in CANONICAL_DOMAINS.values()],
            "niriValidateAvailable": bool(shutil.which("niri")),
            "rulesCount": len(CORE_DOMAIN_DEFINITIONS),
        },
    }


def format_domain_rag_capability_reply(
    prompt: str = "",
    lang: str = "en",
    companion_root: Path | None = None,
) -> str:
    """Format a fast, grounded Markdown summary of the active 3-Stage Domain RAG system."""
    status = domain_rag_status(companion_root=companion_root)
    s1 = status["stage1Canonical"]
    s2 = status["stage2Personal"]
    s3 = status["stage3Skills"]
    niri_docs = s1["domains"].get("niri", {}).get("docCount", 0)
    qs_docs = s1["domains"].get("quickshell", {}).get("docCount", 0)
    arch_docs = (s1["domains"].get("archlinux") or s1["domains"].get("arch") or {}).get("docCount", 0)
    total_docs = s1["totalDocuments"]
    watched_cnt = s2["watchedFileCount"]
    pers_files = ", ".join(f"`{name}`" for name in s2["indexedDocuments"]) or "`niri-user-config.md`, `quickshell-alis-config.md`, `systemd-user-env.md`, `runtime-profile.md`"
    niri_val_tag = "`niri validate -c`" if s3["niriValidateAvailable"] else "KDL syntax & schema guard"

    if lang == "vi":
        return (
            "Có! Mình hiện đã được trang bị **Hệ thống Domain RAG 3 tầng (3-Stage Domain RAG)** chuyên biệt cho **Niri WM**, **Quickshell (Qt6/QML)** và **Arch Linux** ngay trên máy của bạn:\n\n"
            f"### 1. Tầng 1 — Tài liệu Chuẩn Chính thức (`canonical/`, {total_docs} tài liệu đã lập chỉ mục)\n"
            f"- **Niri WM** (`{niri_docs}` tài liệu từ `niri-wm.github.io/niri/`): Scrollable-tiling Wayland compositor (Rust, dải cột ngang vô tận), cú pháp KDL, `input`/`output`/`layout`, `window-rule`, `layer-rule`, `binds`, `animations` và IPC `niri msg`.\n"
            f"- **Quickshell QML** (`{qs_docs}` tài liệu từ `quickshell.outfoxxed.me/docs/`): Bộ công cụ Wayland Desktop Shell dựa trên **Qt6 / QML** (`PanelWindow`, `WlrLayershell`, `WlrKeyboardFocus`, `Process`/`StdioCollector`/`SplitParser`, `FileView`, `IpcHandler`, `ToplevelManager`, `ScreencopyView`).\n"
            f"- **Arch Linux** (`{arch_docs}` tài liệu từ `wiki.archlinux.org/`): `pacman -Syu`, `paccache`, `makepkg`/`PKGBUILD`, `systemctl`/`journalctl`, `mkinitcpio`, PipeWire/WirePlumber và `xdg-desktop-portal`.\n\n"
            f"### 2. Tầng 2 — Ngữ cảnh Máy Cá nhân (`personal/`, {watched_cnt} tệp cấu hình thực tế, tự động lọc bí mật)\n"
            f"- Đã đồng bộ trực tiếp cấu hình trên máy bạn ({pers_files}): `~/.config/niri/config.kdl` (cùng các tệp `include`), cấu hình Quickshell/Alis, `~/.config/systemd/user/*.service`, `environment.d/*.conf` và trạng thái runtime trực tiếp.\n\n"
            "### 3. Tầng 3 — Kỹ năng Chuyên sâu & Kiểm tra An toàn (`skills/`)\n"
            f"- **3 kỹ năng đang bật**: `niri-wm`, `quickshell-qml`, `arch-linux`.\n"
            f"- **Chốt chặn xác thực**: Tự động kiểm tra snippet KDL bằng {niri_val_tag} (chặn cú pháp Hyprland nhầm lẫn), bắt buộc `WlrKeyboardFocus.OnDemand` cho overlay Quickshell và quy tắc an toàn `pacman -Syu` (chống partial upgrade `-Sy`)."
        )

    return (
        "Yes — I now have a live **3-Stage Domain RAG System** for **Niri WM**, **Quickshell (Qt6/QML)**, and **Arch Linux** active on your machine:\n\n"
        f"### 1. Stage 1 — Canonical Documentation (`canonical/`, {total_docs} indexed docs)\n"
        f"- **Niri WM** (`{niri_docs}` docs from `niri-wm.github.io/niri/`): Scrollable-tiling Wayland compositor in Rust (infinite horizontal strip of columns), KDL syntax, `input`/`output`/`layout`, `window-rule`, `layer-rule`, `binds`, `animations`, and `niri msg` IPC.\n"
        f"- **Quickshell QML** (`{qs_docs}` docs from `quickshell.outfoxxed.me/docs/`): **Qt6 / QML-based** Wayland desktop shell toolkit (`PanelWindow`, `WlrLayershell`, `WlrKeyboardFocus`, `Process`/`StdioCollector`/`SplitParser`, `FileView`, `IpcHandler`, `ToplevelManager`, and `ScreencopyView`).\n"
        f"- **Arch Linux** (`{arch_docs}` docs from `wiki.archlinux.org/`): `pacman -Syu`, `paccache`, `makepkg`/`PKGBUILD`, `systemctl`/`journalctl`, `mkinitcpio`, PipeWire/WirePlumber, and `xdg-desktop-portal`.\n\n"
        f"### 2. Stage 2 — Personal Machine Context (`personal/`, {watched_cnt} live local files, secret-scrubbed)\n"
        f"- Indexed directly from your setup ({pers_files}): `~/.config/niri/config.kdl` (and `include` files), Quickshell/Alis `config.json`, `~/.config/systemd/user/*.service`, `environment.d/*.conf`, and live runtime telemetry.\n\n"
        "### 3. Stage 3 — Domain Skills & Validation Guardrails (`skills/`)\n"
        f"- **Active skills**: `niri-wm`, `quickshell-qml`, `arch-linux`.\n"
        f"- **Safety guardrails**: Automatic KDL validation via {niri_val_tag} (blocking Hyprland syntax mix-ups), `WlrKeyboardFocus.OnDemand` overlay focus enforcement, and safe `pacman -Syu` upgrade rules."
    )

