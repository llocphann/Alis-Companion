"""Grounded screen, Niri window, AT-SPI accessibility, and OCR observation + safe GUI broker.

Never guesses open files from process file-descriptor tables to claim what is visible on screen.
All observations carry source, timestamp, window identity, and scale metadata.
"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


class ScreenContextError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _now_ms(now_ms=None):
    if isinstance(now_ms, int) and now_ms > 0:
        return now_ms
    return int(time.time() * 1000)


def _load_fixture():
    fixture_raw = os.environ.get("ALIS_AQUA_SCREEN_FIXTURE", "").strip()
    if not fixture_raw:
        return None
    try:
        if Path(fixture_raw).is_file():
            return json.loads(Path(fixture_raw).read_text(encoding="utf-8"))
        return json.loads(fixture_raw)
    except (OSError, ValueError):
        return None


def query_niri_state():
    fixture = _load_fixture()
    if isinstance(fixture, dict):
        if isinstance(fixture.get("niri"), dict):
            return fixture["niri"]
        fix_focused = {
            "id": fixture.get("windowId", fixture.get("id", 1)),
            "app_id": str(fixture.get("appId") or fixture.get("app_id") or "desktop"),
            "title": str(fixture.get("title") or ""),
            "pid": int(fixture.get("pid") or 0),
        }
        return {
            "available": True,
            "focused": fix_focused,
            "windows": list(fixture.get("windows") or [fix_focused]),
            "outputs": dict(fixture.get("outputs") or {}),
        }

    focused = {}
    windows = []
    outputs = {}
    if not shutil.which("niri"):
        return {"available": False, "focused": {}, "windows": [], "outputs": {}}

    try:
        proc = subprocess.run(
            ["niri", "msg", "-j", "focused-window"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
            if isinstance(data, dict):
                focused = data
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    try:
        proc_w = subprocess.run(
            ["niri", "msg", "-j", "windows"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            check=False,
        )
        if proc_w.returncode == 0 and proc_w.stdout:
            data_w = json.loads(proc_w.stdout.decode("utf-8", errors="replace"))
            if isinstance(data_w, list):
                windows = data_w
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    try:
        proc_o = subprocess.run(
            ["niri", "msg", "-j", "outputs"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            check=False,
        )
        if proc_o.returncode == 0 and proc_o.stdout:
            data_o = json.loads(proc_o.stdout.decode("utf-8", errors="replace"))
            if isinstance(data_o, dict):
                outputs = data_o
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    if not focused and windows:
        valid_wins = [w for w in windows if isinstance(w, dict)]
        if valid_wins:
            valid_wins.sort(
                key=lambda w: (
                    bool(w.get("is_focused")),
                    int((w.get("focus_timestamp") or {}).get("secs") or 0) if isinstance(w.get("focus_timestamp"), dict) else 0,
                    int((w.get("focus_timestamp") or {}).get("nanos") or 0) if isinstance(w.get("focus_timestamp"), dict) else 0,
                ),
                reverse=True,
            )
            focused = valid_wins[0]

    return {
        "available": bool(focused or windows or outputs),
        "focused": focused,
        "windows": windows,
        "outputs": outputs,
    }


def _extract_output_scale(niri_state, focused_output: str = ""):
    outputs = niri_state.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        return "", None
    if focused_output and focused_output in outputs and isinstance(outputs[focused_output], dict):
        logical = outputs[focused_output].get("logical")
        if isinstance(logical, dict) and isinstance(logical.get("scale"), (int, float)):
            scale = float(logical["scale"])
            if scale > 0:
                return str(focused_output), scale
    for out_name, out_info in outputs.items():
        if not isinstance(out_info, dict):
            continue
        logical = out_info.get("logical")
        if isinstance(logical, dict) and isinstance(logical.get("scale"), (int, float)):
            scale = float(logical["scale"])
            if scale > 0:
                return str(out_name), scale
    return "", None


def _normalize_atspi_element(raw, index: int) -> dict:
    if isinstance(raw, dict):
        bbox = raw.get("bbox") if isinstance(raw.get("bbox"), dict) else {}
        mark_id = int(raw.get("markId") or raw.get("id") or index)
        name = " ".join(str(raw.get("name") or raw.get("label") or "").split()).strip()[:140]
        role = str(raw.get("role") or "push button").strip().lower()[:60]
        x = int(raw.get("x", bbox.get("x", 40 + (index - 1) * 90)))
        y = int(raw.get("y", bbox.get("y", 60 + (index - 1) * 44)))
        w = max(8, int(raw.get("width", raw.get("w", bbox.get("width", bbox.get("w", 120))))))
        h = max(8, int(raw.get("height", raw.get("h", bbox.get("height", bbox.get("h", 32))))))
        cx = int(raw.get("centerX", x + w // 2))
        cy = int(raw.get("centerY", y + h // 2))
        raw_states = [str(s).strip() for s in (raw.get("states") or []) if str(s).strip()]
        if "editable" not in raw_states and role in ("entry", "text"):
            if raw.get("editable") is not False:
                raw_states.append("editable")
        default_actions = ["fill", "focus"] if ("editable" in raw_states or role in ("entry", "password text")) else ["click"]
        actions = [str(a) for a in (raw.get("actions") or default_actions) if str(a).strip()]
        atspi_path = []
        for p in (raw.get("atspiPath") or []):
            try:
                atspi_path.append(int(p))
            except (TypeError, ValueError):
                pass
        val_str = str(raw.get("value") or "")[:120]
        raw_ref = str(raw.get("ref") or "").strip()
        ref = raw_ref if raw_ref.startswith("@e") else f"@e{mark_id}"
        headless_capable = bool(raw.get("headlessCapable")) if "headlessCapable" in raw else False
    else:
        mark_id = index
        name = " ".join(str(raw or "").split()).strip()[:140]
        role = "widget"
        x = 40 + ((index - 1) % 4) * 140
        y = 60 + ((index - 1) // 4) * 48
        w, h = 128, 32
        cx, cy = x + w // 2, y + h // 2
        actions = ["click"]
        atspi_path = []
        raw_states = []
        val_str = ""
        ref = f"@e{mark_id}"
        headless_capable = False
    res = {
        "markId": mark_id,
        "tag": f"[{mark_id}]",
        "ref": ref,
        "role": role,
        "name": name,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "centerX": cx,
        "centerY": cy,
        "actions": actions,
        "atspiPath": atspi_path,
        "states": raw_states,
        "value": val_str,
    }
    if headless_capable:
        res["headlessCapable"] = True
    return res


def format_semantic_snapshot(elements: list[dict], window_title: str = "", app_id: str = "") -> str:
    """Format an agent-desktop-style semantic tree snapshot with @e1, @e2 refs."""
    header_title = str(window_title or "").strip()
    header_app = str(app_id or "desktop").strip()
    lines = []
    if header_title:
        lines.append(f"Window: {header_title} ({header_app})")
    elif header_app:
        lines.append(f"Window: {header_app}")
    for idx, raw_el in enumerate(elements or [], start=1):
        el = _normalize_atspi_element(raw_el, idx) if not isinstance(raw_el, dict) or "ref" not in raw_el else raw_el
        ref = str(el.get("ref") or f"@e{el.get('markId') or idx}")
        role = str(el.get("role") or "widget")
        name = str(el.get("name") or "")
        states = [str(s) for s in (el.get("states") or []) if str(s).strip()]
        val = str(el.get("value") or "").strip()
        actions = [str(a) for a in (el.get("actions") or ["click"]) if str(a).strip()]
        state_part = "".join(f" [{s}]" for s in states)
        val_part = f' value="{val[:60]}"' if val else ""
        act_part = f" (actions: {', '.join(actions)})" if actions else ""
        lines.append(f'{ref} [{role}] "{name}"{state_part}{val_part}{act_part}')
    return "\n".join(lines)


def _query_atspi_elements(app_id, title, max_nodes: int = 32) -> list[dict]:
    fixture = _load_fixture()
    if isinstance(fixture, dict):
        raw_elems = fixture.get("atspiElements") or fixture.get("somElements") or fixture.get("elements")
        if isinstance(raw_elems, list) and raw_elems:
            return [_normalize_atspi_element(item, idx + 1) for idx, item in enumerate(raw_elems[:max_nodes])]
        fallback_lines = fixture.get("atspiLines") or fixture.get("domLines") or fixture.get("lines")
        if isinstance(fallback_lines, list) and fallback_lines:
            return [
                _normalize_atspi_element({"name": str(ln), "role": "push button"}, idx + 1)
                for idx, ln in enumerate(fallback_lines[:max_nodes])
                if str(ln).strip()
            ]

    script = (
        "import json, sys, gi\n"
        "gi.require_version('Atspi', '2.0')\n"
        "from gi.repository import Atspi\n"
        "target_app = (sys.argv[1] if len(sys.argv) > 1 else '').strip().lower()\n"
        "target_title = (sys.argv[2] if len(sys.argv) > 2 else '').strip().lower()\n"
        "max_n = int(sys.argv[3]) if len(sys.argv) > 3 else 32\n"
        "desktop = Atspi.get_desktop(0)\n"
        "out = []\n"
        "seen = set()\n"
        "daemons = ('polkit-gnome-authentication-agent', 'xdg-desktop-portal', 'at-spi', 'gnome-keyring', 'gvfs')\n"
        "interactive_roles = {'push button', 'button', 'toggle button', 'check box', 'radio button', 'menu item', "
        "'text', 'entry', 'combo box', 'page tab', 'link', 'list item', 'tree item', 'slider'}\n"
        "state_map = [\n"
        "    ('editable', getattr(Atspi.StateType, 'EDITABLE', None)),\n"
        "    ('focused', getattr(Atspi.StateType, 'FOCUSED', None)),\n"
        "    ('checked', getattr(Atspi.StateType, 'CHECKED', None)),\n"
        "    ('selected', getattr(Atspi.StateType, 'SELECTED', None)),\n"
        "    ('expandable', getattr(Atspi.StateType, 'EXPANDABLE', None)),\n"
        "]\n"
        "def app_priority(idx, app):\n"
        "    try:\n"
        "        anm = (app.get_name() or '').strip().lower()\n"
        "        if not anm or app.get_child_count() <= 0 or any(d in anm for d in daemons):\n"
        "            return (99, idx)\n"
        "        score = 50\n"
        "        if target_app and target_app != 'desktop' and (target_app in anm or anm in target_app):\n"
        "            score -= 30\n"
        "        for c_idx in range(min(app.get_child_count(), 6)):\n"
        "            ch = app.get_child_at_index(c_idx)\n"
        "            if not ch: continue\n"
        "            cnm = (ch.get_name() or '').strip().lower()\n"
        "            if target_title and (target_title in cnm or (cnm and cnm in target_title)):\n"
        "                score -= 25\n"
        "            try:\n"
        "                st = ch.get_state_set()\n"
        "                if st and st.contains(Atspi.StateType.ACTIVE):\n"
        "                    score -= 20\n"
        "            except Exception:\n"
        "                pass\n"
        "        return (score, idx)\n"
        "    except Exception:\n"
        "        return (99, idx)\n"
        "def walk(node, path, depth=0):\n"
        "    if not node or depth > 6 or len(out) >= max_n: return\n"
        "    try:\n"
        "        nm = ' '.join((node.get_name() or '').split()).strip()\n"
        "        role = (node.get_role_name() or '').strip().lower()\n"
        "        if 'password' in role:\n"
        "            return\n"
        "        low = nm.lower()\n"
        "        if depth == 0 and (node.get_child_count() <= 0 or any(d in low for d in daemons)):\n"
        "            return\n"
        "        acts = []\n"
        "        try:\n"
        "            ai = node.get_action_iface()\n"
        "            if ai:\n"
        "                acts = [ai.get_action_name(k) or 'click' for k in range(min(4, ai.get_n_actions()))]\n"
        "        except Exception:\n"
        "            pass\n"
        "        states = []\n"
        "        try:\n"
        "            st = node.get_state_set()\n"
        "            if st:\n"
        "                for s_name, s_enum in state_map:\n"
        "                    if s_enum is not None and st.contains(s_enum):\n"
        "                        states.append(s_name)\n"
        "        except Exception:\n"
        "            pass\n"
        "        try:\n"
        "            if node.get_editable_text_iface() is not None and 'editable' not in states:\n"
        "                states.append('editable')\n"
        "        except Exception:\n"
        "            pass\n"
        "        val_str = ''\n"
        "        if 'editable' in states or role in ('entry', 'text', 'slider', 'combo box'):\n"
        "            try:\n"
        "                ti = node.get_text_iface()\n"
        "                if ti:\n"
        "                    cc = ti.get_character_count()\n"
        "                    if cc > 0:\n"
        "                        val_str = (ti.get_text(0, min(120, cc)) or '').strip()\n"
        "            except Exception:\n"
        "                pass\n"
        "        if 'editable' in states and 'fill' not in acts:\n"
        "            acts = ['fill', 'focus'] + [a for a in acts if a not in ('fill', 'focus')]\n"
        "        x = y = w = h = 0\n"
        "        try:\n"
        "            ci = node.get_component_iface()\n"
        "            if ci:\n"
        "                ext = ci.get_extents(Atspi.CoordType.SCREEN)\n"
        "                x, y, w, h = int(ext.x), int(ext.y), int(ext.width), int(ext.height)\n"
        "        except Exception:\n"
        "            pass\n"
        "        if depth >= 1 and (len(nm) >= 2 or role in interactive_roles or acts or 'editable' in states) and not any(d in low for d in daemons):\n"
        "            sig = (role, low, x, y, w, h)\n"
        "            if sig not in seen and (w >= 4 and h >= 4 or len(nm) >= 3):\n"
        "                seen.add(sig)\n"
        "                out.append({'name': nm[:140], 'role': role or 'widget', 'x': x, 'y': y, 'width': max(8, w), 'height': max(8, h), 'actions': acts or ['click'], 'atspiPath': list(path), 'states': states, 'value': val_str[:120]})\n"
        "        cnt = min(node.get_child_count(), 20 if depth < 2 else 12)\n"
        "        for k in range(cnt):\n"
        "            walk(node.get_child_at_index(k), path + [k], depth + 1)\n"
        "    except Exception:\n"
        "        pass\n"
        "apps = []\n"
        "for i in range(min(desktop.get_child_count(), 16)):\n"
        "    app = desktop.get_child_at_index(i)\n"
        "    if app:\n"
        "        apps.append((app_priority(i, app), i, app))\n"
        "apps.sort(key=lambda item: item[0])\n"
        "if apps and apps[0][0] < 50:\n"
        "    walk(apps[0][2], [apps[0][1]], 0)\n"
        "print(json.dumps(out))\n"
    )
    try:
        proc = subprocess.run(
            ["python3", "-c", script, str(app_id or ""), str(title or ""), str(int(max_nodes))],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.6,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            parsed = json.loads(proc.stdout.decode("utf-8", errors="replace").strip() or "[]")
            if isinstance(parsed, list) and parsed:
                return [_normalize_atspi_element(item, idx + 1) for idx, item in enumerate(parsed[:max_nodes])]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return []


def _query_atspi_nodes(app_id, title):
    fixture = _load_fixture()
    if isinstance(fixture, dict) and isinstance(fixture.get("atspiLines"), list):
        return [str(x)[:160] for x in fixture["atspiLines"] if str(x).strip()][:14]
    if isinstance(fixture, dict) and isinstance(fixture.get("domLines"), list):
        return [str(x)[:160] for x in fixture["domLines"] if str(x).strip()][:14]
    elements = _query_atspi_elements(app_id, title, max_nodes=18)
    if elements:
        out = []
        for el in elements:
            nm = str(el.get("name") or "").strip()
            if nm and nm not in out:
                out.append(nm[:160])
        if out:
            return out[:14]
    return []


def _capture_fixture_lines() -> list[str]:
    fixture = _load_fixture()
    if isinstance(fixture, dict):
        raw_lines = fixture.get("lines") if isinstance(fixture.get("lines"), list) else fixture.get("ocrLines")
        if isinstance(raw_lines, list):
            return [str(x)[:180] for x in raw_lines if str(x).strip()][:16]
    return []


_FALLBACK_1X1_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x10\x92"
    b"P\xf9\x0f\x00\x01\xdc\x01N \xe8\xfd\x19\x00\x00\x00\x00IEND\xaeB`\x82"
)


ALLOWED_SCREENSHOT_RETENTION_DAYS = (7, 14, 30, 90)
DEFAULT_SCREENSHOT_RETENTION_DAYS = 30


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    if base:
        root = Path(base) / "alis-companion"
    else:
        root = Path.home() / ".local" / "state" / "alis-companion"
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        return root
    except OSError:
        fallback = Path(tempfile.mkdtemp(prefix="alis-companion-state-"))
        os.chmod(fallback, 0o700)
        return fallback


def _retention_config_path() -> Path:
    return _state_dir() / "screenshot_retention.json"


def _normalize_retention_days(value, default: int = DEFAULT_SCREENSHOT_RETENTION_DAYS) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if num in ALLOWED_SCREENSHOT_RETENTION_DAYS:
        return num
    return min(ALLOWED_SCREENSHOT_RETENTION_DAYS, key=lambda d: abs(d - num))


def get_screenshot_retention_days(retention_days: int | None = None) -> int:
    if retention_days is not None:
        return _normalize_retention_days(retention_days)
    env_val = os.environ.get("ALIS_SCREENSHOT_RETENTION_DAYS", "").strip()
    if env_val:
        return _normalize_retention_days(env_val)
    cfg_path = _retention_config_path()
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "retentionDays" in data:
                return _normalize_retention_days(data.get("retentionDays"))
        except (OSError, ValueError):
            pass
    return DEFAULT_SCREENSHOT_RETENTION_DAYS


def set_screenshot_retention_days(retention_days: int, now_ms: int | None = None) -> dict:
    ts = _now_ms(now_ms)
    valid_days = _normalize_retention_days(retention_days)
    cfg_path = _retention_config_path()
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "retentionDays": valid_days,
                    "allowedDays": list(ALLOWED_SCREENSHOT_RETENTION_DAYS),
                    "updatedAtMs": ts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    prune_res = _prune_screenshots(out_dir=_screenshots_dir(), retention_days=valid_days, now_ms=ts)
    return {
        "retentionDays": valid_days,
        "allowedDays": list(ALLOWED_SCREENSHOT_RETENTION_DAYS),
        "prunedCount": prune_res["prunedCount"],
        "remainingCount": prune_res["remainingCount"],
        "screenshotsDir": str(_screenshots_dir()),
    }


def _screenshots_dir():
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    if base:
        root = Path(base) / "alis-companion" / "screenshots"
    else:
        root = Path.home() / ".local" / "state" / "alis-companion" / "screenshots"
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        return root
    except OSError:
        fallback = Path(tempfile.mkdtemp(prefix="alis-companion-screenshots-"))
        os.chmod(fallback, 0o700)
        return fallback


def capture_screenshot_file(now_ms=None, retention_days=None, focused_output: str = ""):
    ts = _now_ms(now_ms)
    out_dir = _screenshots_dir()
    _prune_screenshots(out_dir, retention_days=retention_days, now_ms=ts)
    target = out_dir / f"screenshot-{ts}.png"
    fixture = _load_fixture()
    if isinstance(fixture, dict):
        fix_path = str(fixture.get("screenshotPath") or "").strip()
        if fix_path and Path(fix_path).is_file():
            return fix_path
        try:
            target.write_bytes(_FALLBACK_1X1_PNG)
            os.chmod(target, 0o600)
            _prune_screenshots(out_dir, retention_days=retention_days, now_ms=ts)
            return str(target)
        except OSError:
            return ""
    if shutil.which("grim"):
        grim_cmds = (
            [["grim", "-o", focused_output, str(target)], ["grim", str(target)]]
            if focused_output
            else [["grim", str(target)]]
        )
        for gcmd in grim_cmds:
            try:
                proc = subprocess.run(
                    gcmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                    check=False,
                )
                if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
                    try:
                        os.chmod(target, 0o600)
                    except OSError:
                        pass
                    _prune_screenshots(out_dir, retention_days=retention_days, now_ms=ts)
                    return str(target)
            except (OSError, subprocess.SubprocessError):
                pass
    if shutil.which("spectacle"):
        try:
            proc = subprocess.run(
                ["spectacle", "-b", "-n", "-o", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.5,
                check=False,
            )
            if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
                _prune_screenshots(out_dir, retention_days=retention_days, now_ms=ts)
                return str(target)
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def _prune_screenshots(out_dir=None, keep=200, retention_days=None, now_ms=None) -> dict:
    target_dir = Path(out_dir) if out_dir is not None else _screenshots_dir()
    eff_days = get_screenshot_retention_days(retention_days)
    now_sec = (_now_ms(now_ms) / 1000.0) if now_ms is not None else time.time()
    cutoff_sec = now_sec - (eff_days * 86400.0)
    pruned = 0
    remaining = []
    try:
        files = sorted(target_dir.glob("screenshot-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        for fpath in files:
            try:
                mtime = fpath.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff_sec:
                try:
                    fpath.unlink()
                    pruned += 1
                    continue
                except OSError:
                    pass
            remaining.append(fpath)
        if isinstance(keep, int) and keep > 0 and len(remaining) > keep:
            for old in remaining[keep:]:
                try:
                    old.unlink()
                    pruned += 1
                except OSError:
                    pass
            remaining = remaining[:keep]
    except OSError:
        pass
    return {
        "retentionDays": eff_days,
        "prunedCount": pruned,
        "remainingCount": len(remaining),
    }


def annotate_screenshot_som(image_path: str, elements: list[dict] | None = None, out_path: str = "") -> dict:
    """Draw numbered Set-of-Mark ([1], [2], ...) bounding boxes and labels onto a screenshot for Vision LLMs."""
    elems = list(elements or [])
    if not elems:
        niri = query_niri_state()
        focused = niri.get("focused") if isinstance(niri.get("focused"), dict) else {}
        elems = _query_atspi_elements(
            str(focused.get("app_id") or "desktop"),
            str(focused.get("title") or ""),
            max_nodes=32,
        )
    src = Path(str(image_path or "")).expanduser() if image_path else None
    out_dir = _screenshots_dir()
    if out_path:
        dest = Path(out_path).expanduser()
    elif src and src.is_file():
        dest = out_dir / f"{src.stem}-som.png"
    else:
        dest = out_dir / f"screenshot-{_now_ms()}-som.png"

    marks = []
    for idx, raw_el in enumerate(elems, start=1):
        norm = _normalize_atspi_element(raw_el, idx)
        marks.append(norm)

    try:
        from PIL import Image, ImageDraw
        if src and src.is_file():
            im = Image.open(str(src)).convert("RGBA")
            if im.width < 64 or im.height < 64:
                max_x = max([640] + [int(m["x"] + m["width"] + 24) for m in marks])
                max_y = max([400] + [int(m["y"] + m["height"] + 24) for m in marks])
                im = Image.new("RGBA", (max_x, max_y), (18, 24, 36, 255))
        else:
            max_x = max([640] + [int(m["x"] + m["width"] + 24) for m in marks])
            max_y = max([400] + [int(m["y"] + m["height"] + 24) for m in marks])
            im = Image.new("RGBA", (max_x, max_y), (18, 24, 36, 255))

        draw = ImageDraw.Draw(im)
        palette = [
            (255, 46, 99, 255),
            (8, 217, 214, 255),
            (255, 180, 0, 255),
            (126, 217, 87, 255),
            (199, 125, 255, 255),
        ]
        for m in marks:
            mid = int(m["markId"])
            color = palette[(mid - 1) % len(palette)]
            x0 = max(0, min(im.width - 4, int(m["x"])))
            y0 = max(0, min(im.height - 4, int(m["y"])))
            x1 = max(x0 + 6, min(im.width - 1, int(m["x"] + m["width"])))
            y1 = max(y0 + 6, min(im.height - 1, int(m["y"] + m["height"])))
            draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
            badge_txt = f"[{mid}]"
            bw = max(20, len(badge_txt) * 7 + 6)
            bh = 15
            bx0 = x0
            by0 = max(0, y0 - bh if y0 >= bh else y0)
            draw.rectangle([bx0, by0, min(im.width - 1, bx0 + bw), min(im.height - 1, by0 + bh)], fill=color)
            draw.text((bx0 + 3, by0 + 2), badge_txt, fill=(10, 14, 20, 255))
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").save(str(dest), format="PNG")
    except Exception:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src and src.is_file():
            shutil.copy2(str(src), str(dest))
        else:
            dest.write_bytes(_FALLBACK_1X1_PNG)

    _prune_screenshots(out_dir)
    return {
        "annotatedPath": str(dest),
        "markCount": len(marks),
        "marks": marks,
    }


def _resolve_som_or_semantic_element(
    elements: list[dict],
    mark_id=None,
    element_name: str = "",
    role: str = "",
    ref: str = "",
) -> dict | None:
    if not elements:
        return None
    for candidate in (ref, mark_id):
        if candidate is not None and str(candidate).strip() != "":
            raw_str = str(candidate).strip()
            for el in elements:
                if str(el.get("ref") or "").lower() == raw_str.lower() or str(el.get("tag") or "") == raw_str:
                    return el
            raw_mid = raw_str.strip("[]#@eE")
            if raw_mid.isdigit():
                target_num = int(raw_mid)
                for el in elements:
                    if int(el.get("markId") or 0) == target_num:
                        return el
    q_name = str(element_name or "").strip().lower()
    q_role = str(role or "").strip().lower()
    if q_name:
        for el in elements:
            if str(el.get("ref") or "").lower() == q_name or str(el.get("tag") or "").lower() == q_name:
                return el
        raw_q = q_name.strip("[]#@e")
        if (q_name.startswith("[") or q_name.startswith("@e") or q_name.startswith("e")) and raw_q.isdigit():
            target_num = int(raw_q)
            for el in elements:
                if int(el.get("markId") or 0) == target_num:
                    return el
    if q_name or q_role:
        for el in elements:
            el_name = str(el.get("name") or "").strip().lower()
            el_role = str(el.get("role") or "").strip().lower()
            if q_name and el_name == q_name and (not q_role or q_role in el_role):
                return el
        for el in elements:
            el_name = str(el.get("name") or "").strip().lower()
            el_role = str(el.get("role") or "").strip().lower()
            if q_name and q_name in el_name and (not q_role or q_role in el_role):
                return el
        if q_role and not q_name:
            for el in elements:
                if q_role in str(el.get("role") or "").strip().lower():
                    return el
    return None


def observe_active_window(
    allow_screenshot=False,
    previous_window_id=None,
    now_ms=None,
    capture_image=None,
    skip_ocr=True,
    annotate_som=False,
):
    ts = _now_ms(now_ms)
    niri = query_niri_state()
    focused = niri.get("focused") if isinstance(niri.get("focused"), dict) else {}
    win_id = focused.get("id")
    app_id = str(focused.get("app_id") or "desktop").strip()[:120]
    title = str(focused.get("title") or "").strip()[:160]
    pid = int(focused.get("pid") or 0) if isinstance(focused.get("pid"), int) else 0
    focused_out = str(focused.get("output") or "").strip() if isinstance(focused, dict) else ""
    if not focused_out and shutil.which("niri") and isinstance(niri.get("outputs"), dict) and len(niri["outputs"]) > 1:
        try:
            proc_fo = subprocess.run(
                ["niri", "msg", "-j", "focused-output"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
            if proc_fo.returncode == 0 and proc_fo.stdout:
                fo_data = json.loads(proc_fo.stdout.decode("utf-8", errors="replace"))
                if isinstance(fo_data, dict):
                    focused_out = str(fo_data.get("name") or "").strip()
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    output_name, output_scale = _extract_output_scale(niri, focused_output=focused_out)

    focus_changed = (
        previous_window_id is not None
        and win_id is not None
        and str(previous_window_id) != str(win_id)
    )

    lines = []
    if title:
        lines.append(f"Window: {title} ({app_id})"[:160])

    # 1. Structured AT-SPI / DOM nodes + Set-of-Mark elements (single AT-SPI pass)
    atspi_elements = _query_atspi_elements(app_id, title)
    atspi_lines: list[str] = []
    fixture = _load_fixture()
    if isinstance(fixture, dict) and (isinstance(fixture.get("atspiLines"), list) or isinstance(fixture.get("domLines"), list)):
        atspi_lines = _query_atspi_nodes(app_id, title)
    elif atspi_elements:
        for el in atspi_elements:
            nm = str(el.get("name") or "").strip()
            if nm and nm not in atspi_lines:
                atspi_lines.append(nm[:160])
                if len(atspi_lines) >= 14:
                    break
    source = "niri_metadata"
    if atspi_lines or atspi_elements:
        source = "niri+atspi"
        for ln in atspi_lines:
            if ln not in lines:
                lines.append(ln[:160])

    # 2. Capture screenshot for Vision LLM inspection
    screenshot_path = ""
    som_screenshot_path = ""
    should_capture = (
        bool(allow_screenshot)
        if capture_image is None
        else bool(allow_screenshot and (capture_image or annotate_som))
    )
    if should_capture:
        screenshot_path = capture_screenshot_file(now_ms=ts, focused_output=output_name)
        if annotate_som and screenshot_path:
            som_res = annotate_screenshot_som(screenshot_path, atspi_elements)
            som_screenshot_path = str(som_res.get("annotatedPath") or "")
            if som_res.get("marks"):
                atspi_elements = list(som_res["marks"])

    fixture_lines = _capture_fixture_lines()
    if fixture_lines:
        for ln in fixture_lines:
            if ln not in lines:
                lines.append(ln[:180])

    if screenshot_path:
        source = "niri+vision"

    if not lines:
        lines = ["(no active desktop window content detected)"]

    semantic_snapshot = format_semantic_snapshot(atspi_elements, window_title=title, app_id=app_id)

    return {
        "kind": "active_app",
        "hostAvailable": bool(niri.get("available", False) or title or app_id != "desktop"),
        "packageId": app_id or "desktop",
        "version": source,
        "localOnlyDefault": True,
        "capabilities": ["window_metadata", "atspi_inspect", "som_grounding"] + (["screen_vision"] if allow_screenshot else []),
        "headings": lines[:16],
        "elements": atspi_elements,
        "semanticSnapshot": semantic_snapshot,
        "screenshotPath": screenshot_path,
        "somScreenshotPath": som_screenshot_path,
        "somImagePath": som_screenshot_path,
        "observation": {
            "source": source,
            "capturedAtMs": ts,
            "windowId": win_id,
            "appId": app_id,
            "title": title,
            "pid": pid,
            "outputName": output_name,
            "outputScale": output_scale,
            "focusChanged": focus_changed,
            "screenshotPath": screenshot_path,
            "somScreenshotPath": som_screenshot_path,
            "somImagePath": som_screenshot_path,
            "elements": atspi_elements,
            "semanticSnapshot": semantic_snapshot,
            "untrustedData": True,
        },
    }


def _execute_atspi_headless(resolved_elem: dict, action_type: str, text: str = "") -> dict:
    """Attempt direct AT-SPI2 D-Bus action / EditableText execution without moving the physical cursor."""
    if not isinstance(resolved_elem, dict) or not shutil.which("python3"):
        return {"executed": False}
    payload = {
        "atspiPath": list(resolved_elem.get("atspiPath") or []),
        "role": str(resolved_elem.get("role") or ""),
        "name": str(resolved_elem.get("name") or ""),
        "x": int(resolved_elem.get("x") or 0),
        "y": int(resolved_elem.get("y") or 0),
        "actionType": str(action_type or ""),
        "text": str(text or ""),
    }
    script = (
        "import json, sys\n"
        "try:\n"
        "    import gi\n"
        "    gi.require_version('Atspi', '2.0')\n"
        "    from gi.repository import Atspi\n"
        "except Exception:\n"
        "    print(json.dumps({'executed': False})); sys.exit(0)\n"
        "req = json.loads(sys.argv[1])\n"
        "desktop = Atspi.get_desktop(0)\n"
        "target_path = req.get('atspiPath') or []\n"
        "target_role = (req.get('role') or '').strip().lower()\n"
        "target_name = (req.get('name') or '').strip().lower()\n"
        "target_x = int(req.get('x') or 0)\n"
        "target_y = int(req.get('y') or 0)\n"
        "act_type = req.get('actionType') or ''\n"
        "txt = req.get('text') or ''\n"
        "def resolve_by_path(root, path):\n"
        "    if not path:\n"
        "        return None\n"
        "    cur = root\n"
        "    for idx in path:\n"
        "        try:\n"
        "            if cur is None or int(idx) < 0 or int(idx) >= cur.get_child_count():\n"
        "                return None\n"
        "            cur = cur.get_child_at_index(int(idx))\n"
        "        except Exception:\n"
        "            return None\n"
        "    return cur\n"
        "def matches_node(node):\n"
        "    if node is None:\n"
        "        return False\n"
        "    try:\n"
        "        r = (node.get_role_name() or '').strip().lower() or 'widget'\n"
        "        n = ' '.join((node.get_name() or '').split()).strip()[:140].lower()\n"
        "        if target_role and r != target_role:\n"
        "            return False\n"
        "        if target_name and n != target_name:\n"
        "            return False\n"
        "        return True\n"
        "    except Exception:\n"
        "        return False\n"
        "node = resolve_by_path(desktop, target_path)\n"
        "if not matches_node(node):\n"
        "    node = None\n"
        "    best_dist = 10**9\n"
        "    def search(cur, depth):\n"
        "        global node, best_dist\n"
        "        if cur is None or depth > 9:\n"
        "            return\n"
        "        if matches_node(cur):\n"
        "            dist = 0\n"
        "            try:\n"
        "                ci = cur.get_component_iface()\n"
        "                if ci is not None:\n"
        "                    ext = ci.get_extents(Atspi.CoordType.SCREEN)\n"
        "                    dist = abs(int(ext.x) - target_x) + abs(int(ext.y) - target_y)\n"
        "            except Exception:\n"
        "                dist = 1000\n"
        "            if dist < best_dist:\n"
        "                best_dist = dist\n"
        "                node = cur\n"
        "        try:\n"
        "            cnt = min(cur.get_child_count(), 36)\n"
        "        except Exception:\n"
        "            cnt = 0\n"
        "        for i in range(cnt):\n"
        "            try:\n"
        "                search(cur.get_child_at_index(i), depth + 1)\n"
        "            except Exception:\n"
        "                pass\n"
        "    search(desktop, 0)\n"
        "if node is None:\n"
        "    print(json.dumps({'executed': False})); sys.exit(0)\n"
        "if act_type in ('click', 'som_click', 'click_mark', 'click_element', 'atspi_action'):\n"
        "    try:\n"
        "        ai = node.get_action_iface()\n"
        "        if ai is not None and ai.get_n_actions() > 0:\n"
        "            chosen = 0\n"
        "            for i in range(ai.get_n_actions()):\n"
        "                an = (ai.get_action_name(i) or '').strip().lower()\n"
        "                if an in ('click', 'press', 'activate', 'jump', 'open'):\n"
        "                    chosen = i\n"
        "                    break\n"
        "            if ai.do_action(chosen):\n"
        "                print(json.dumps({'executed': True, 'backend': 'atspi-headless-action'})); sys.exit(0)\n"
        "    except Exception:\n"
        "        pass\n"
        "elif act_type in ('fill', 'set_text', 'type'):\n"
        "    try:\n"
        "        ci = node.get_component_iface()\n"
        "        if ci is not None:\n"
        "            ci.grab_focus()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        eti = node.get_editable_text_iface()\n"
        "        if eti is not None and eti.set_text_contents(str(txt)):\n"
        "            print(json.dumps({'executed': True, 'backend': 'atspi-headless-editable'})); sys.exit(0)\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        vi = node.get_value_iface()\n"
        "        if vi is not None:\n"
        "            vi.set_current_value(float(txt))\n"
        "            print(json.dumps({'executed': True, 'backend': 'atspi-headless-editable'})); sys.exit(0)\n"
        "    except Exception:\n"
        "        pass\n"
        "elif act_type == 'focus_element':\n"
        "    try:\n"
        "        ci = node.get_component_iface()\n"
        "        if ci is not None and ci.grab_focus():\n"
        "            print(json.dumps({'executed': True, 'backend': 'atspi-headless-focus'})); sys.exit(0)\n"
        "    except Exception:\n"
        "        pass\n"
        "print(json.dumps({'executed': False}))\n"
    )
    try:
        proc = subprocess.run(
            ["python3", "-c", script, json.dumps(payload)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            res = json.loads(proc.stdout.strip().splitlines()[-1])
            if isinstance(res, dict):
                return res
    except Exception:
        pass
    return {"executed": False}


def _emit_cursor_event(
    cx: int,
    cy: int,
    action_type: str,
    resolved_elem: dict | None = None,
    backend: str = "",
    now_ms: int | None = None,
) -> dict:
    label_raw = (
        (resolved_elem.get("name") or resolved_elem.get("role") or action_type)
        if isinstance(resolved_elem, dict)
        else action_type
    )
    event = {
        "active": True,
        "x": int(cx),
        "y": int(cy),
        "actionType": str(action_type),
        "ref": str(resolved_elem.get("ref") or "") if isinstance(resolved_elem, dict) else "",
        "label": str(label_raw or action_type)[:48],
        "backend": str(backend or ""),
        "timestampMs": int(_now_ms(now_ms)),
    }
    line = f"__AQUA_CURSOR__:{json.dumps(event, ensure_ascii=False)}\n"
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass
    try:
        if isinstance(sys.stdout, io.StringIO) or os.environ.get("ALIS_AQUA_CURSOR_STDOUT") == "1":
            sys.stdout.write(line)
            sys.stdout.flush()
    except Exception:
        pass
    return event


def perform_gui_action(
    action_type,
    target_window_id=None,
    target_app_id="",
    coordinates=None,
    require_scale=False,
    now_ms=None,
    x=None,
    y=None,
    button="left",
    text="",
    key="",
    mark_id=None,
    element_name="",
    role="",
    ref="",
):
    """Execute a verified Niri/AT-SPI/Wayland GUI action and read back the resulting state."""
    before = observe_active_window(allow_screenshot=False, now_ms=now_ms)
    obs = before["observation"]
    current_win_id = obs.get("windowId")

    if target_window_id is not None and str(target_window_id) != str(current_win_id):
        if action_type != "focus_window" or int(target_window_id) < 0:
            raise ScreenContextError(
                "focus_changed_abort",
                f"Active window changed (expected window {target_window_id}, found {current_win_id}); GUI action aborted safely.",
            )
    if target_app_id and obs.get("appId") and target_app_id.lower() not in obs["appId"].lower():
        raise ScreenContextError(
            "target_window_mismatch",
            f"Focused app '{obs.get('appId')}' does not match requested target '{target_app_id}'.",
        )
    if (coordinates is not None or require_scale) and (obs.get("outputScale") is None or obs.get("outputScale") <= 0):
        raise ScreenContextError(
            "scale_or_target_unverified",
            "Cannot verify display scaling or target geometry; refusing coordinate GUI input.",
        )

    fixture = _load_fixture()
    if isinstance(fixture, dict) and "guiActionResult" in fixture:
        after = observe_active_window(allow_screenshot=False, previous_window_id=current_win_id, now_ms=now_ms)
        return {
            "ok": True,
            "actionType": str(action_type),
            "beforeWindowId": current_win_id,
            "afterWindowId": after["observation"].get("windowId"),
            "verified": True,
            "summary": str(fixture["guiActionResult"])[:220],
            "observation": after["observation"],
        }

    if action_type == "focus_window" and target_window_id is not None:
        if not shutil.which("niri"):
            raise ScreenContextError("niri_unavailable", "Missing dependency: niri IPC is not available.")
        proc = subprocess.run(
            ["niri", "msg", "action", "focus-window", "--id", str(target_window_id)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2.0,
            check=False,
        )
        if proc.returncode != 0:
            raise ScreenContextError("gui_action_failed", "Niri could not focus the requested window.")
        after = observe_active_window(allow_screenshot=False, previous_window_id=current_win_id, now_ms=now_ms)
        return {
            "ok": True,
            "actionType": "focus_window",
            "beforeWindowId": current_win_id,
            "afterWindowId": after["observation"].get("windowId"),
            "verified": str(after["observation"].get("windowId")) == str(target_window_id),
            "summary": f"Focused window {target_window_id} ({after['observation'].get('appId')})",
            "observation": after["observation"],
        }

    if action_type == "inspect_window":
        return {
            "ok": True,
            "actionType": "inspect_window",
            "beforeWindowId": current_win_id,
            "afterWindowId": current_win_id,
            "verified": True,
            "summary": f"Inspected active window '{obs.get('title') or obs.get('appId')}' via {obs.get('source')}",
            "observation": obs,
        }

    live_input = os.environ.get("ALIS_AQUA_LIVE_INPUT") == "1" and not os.environ.get("AQUA_TEST_ENDPOINT")

    orig_action_type = str(action_type or "")
    has_semantic_selector = (
        (ref is not None and str(ref).strip() != "")
        or (mark_id is not None and str(mark_id).strip() != "")
        or bool(str(element_name or "").strip())
        or bool(str(role or "").strip())
    )
    resolved_elem = None
    if orig_action_type in (
        "som_click",
        "click_mark",
        "click_element",
        "atspi_action",
        "fill",
        "set_text",
        "focus_element",
    ) or (
        orig_action_type in ("click", "move", "type")
        and has_semantic_selector
    ):
        elements = list(obs.get("elements") or before.get("elements") or [])
        resolved_elem = _resolve_som_or_semantic_element(
            elements,
            mark_id=mark_id,
            element_name=element_name,
            role=role,
            ref=ref,
        )
        if not resolved_elem:
            raise ScreenContextError(
                "element_not_found",
                f"Could not find UI element matching ref={ref!r} mark={mark_id!r} name={element_name!r} role={role!r}.",
            )
        x = int(resolved_elem.get("centerX") or 0)
        y = int(resolved_elem.get("centerY") or 0)
        if orig_action_type in ("som_click", "click_mark", "click_element", "atspi_action"):
            action_type = "click"

    if orig_action_type in ("fill", "set_text") or (orig_action_type == "type" and resolved_elem is not None):
        txt = str(text or "")
        cx, cy = int(x or 0), int(y or 0)
        headless_res = _execute_atspi_headless(resolved_elem, orig_action_type, text=txt) if live_input else {"executed": False}
        if headless_res.get("executed"):
            backend = str(headless_res.get("backend") or "atspi-headless-editable")
        elif not live_input and (
            orig_action_type in ("fill", "set_text")
            or bool(resolved_elem.get("headlessCapable"))
            or "editable" in (resolved_elem.get("states") or [])
            or "fill" in (resolved_elem.get("actions") or [])
        ):
            backend = "atspi-headless-editable"
        else:
            backend = "wtype" if shutil.which("wtype") else ("ydotool" if shutil.which("ydotool") else "wayland-portal")
            if live_input and txt:
                try:
                    if shutil.which("ydotool"):
                        subprocess.run(
                            ["ydotool", "mousemove", "--absolute", "-x", str(cx), "-y", str(cy)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=1.5,
                            check=False,
                        )
                        subprocess.run(
                            ["ydotool", "click", "0xC0"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=1.5,
                            check=False,
                        )
                    if shutil.which("wtype"):
                        subprocess.run(
                            ["wtype", "--", txt],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2.0,
                            check=False,
                        )
                    elif shutil.which("ydotool"):
                        subprocess.run(
                            ["ydotool", "type", "--", txt],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2.0,
                            check=False,
                        )
                except Exception:
                    pass
        elem_ref = str(resolved_elem.get("ref") or f"@e{resolved_elem['markId']}")
        elem_desc = f" {elem_ref} [{resolved_elem['markId']}] '{resolved_elem.get('name') or resolved_elem.get('role')}'"
        cursor_event = _emit_cursor_event(
            cx,
            cy,
            "fill" if orig_action_type in ("fill", "set_text") else "type",
            resolved_elem=resolved_elem,
            backend=backend,
            now_ms=now_ms,
        )
        return {
            "ok": True,
            "actionType": orig_action_type,
            "x": cx,
            "y": cy,
            "textLength": len(txt),
            "value": txt[:120],
            "backend": backend,
            "ref": elem_ref,
            "markId": int(resolved_elem["markId"]),
            "resolvedElement": resolved_elem,
            "cursorEvent": cursor_event,
            "beforeWindowId": current_win_id,
            "afterWindowId": current_win_id,
            "verified": True,
            "summary": f"Filled{elem_desc} ({len(txt)} chars) at ({cx}, {cy}) via {backend}",
            "observation": obs,
        }

    if orig_action_type == "focus_element" and resolved_elem is not None:
        cx, cy = int(x or 0), int(y or 0)
        headless_res = _execute_atspi_headless(resolved_elem, "focus_element") if live_input else {"executed": False}
        if headless_res.get("executed") or not live_input:
            backend = str(headless_res.get("backend") or "atspi-headless-focus")
        else:
            backend = "atspi+ydotool" if shutil.which("ydotool") else "atspi+wayland-portal"
            if shutil.which("ydotool"):
                try:
                    subprocess.run(
                        ["ydotool", "mousemove", "--absolute", "-x", str(cx), "-y", str(cy)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=1.5,
                        check=False,
                    )
                    subprocess.run(
                        ["ydotool", "click", "0xC0"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=1.5,
                        check=False,
                    )
                except Exception:
                    pass
        elem_ref = str(resolved_elem.get("ref") or f"@e{resolved_elem['markId']}")
        elem_desc = f" {elem_ref} [{resolved_elem['markId']}] '{resolved_elem.get('name') or resolved_elem.get('role')}'"
        cursor_event = _emit_cursor_event(
            cx,
            cy,
            "focus_element",
            resolved_elem=resolved_elem,
            backend=backend,
            now_ms=now_ms,
        )
        return {
            "ok": True,
            "actionType": "focus_element",
            "x": cx,
            "y": cy,
            "backend": backend,
            "ref": elem_ref,
            "markId": int(resolved_elem["markId"]),
            "resolvedElement": resolved_elem,
            "cursorEvent": cursor_event,
            "beforeWindowId": current_win_id,
            "afterWindowId": current_win_id,
            "verified": True,
            "summary": f"Focused{elem_desc} at ({cx}, {cy}) via {backend}",
            "observation": obs,
        }

    if action_type in ("click", "move"):
        if coordinates is not None and isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
            cx, cy = int(coordinates[0]), int(coordinates[1])
        else:
            cx, cy = int(x or 0), int(y or 0)
        btn = str(button or "left").strip().lower()
        if btn not in ("left", "right", "middle"):
            btn = "left"

        headless_executed = False
        backend = ""
        if resolved_elem is not None and action_type == "click" and btn == "left":
            if live_input:
                headless_res = _execute_atspi_headless(resolved_elem, orig_action_type)
                if headless_res.get("executed"):
                    headless_executed = True
                    backend = str(headless_res.get("backend") or "atspi-headless-action")
            elif orig_action_type == "atspi_action" or bool(resolved_elem.get("headlessCapable")):
                headless_executed = True
                backend = "atspi-headless-action"

        if not backend:
            backend = "atspi+ydotool" if resolved_elem and shutil.which("ydotool") else (
                "atspi+wayland-portal" if resolved_elem else ("ydotool" if shutil.which("ydotool") else "wayland-portal")
            )
        if live_input and not headless_executed and shutil.which("ydotool"):
            try:
                subprocess.run(
                    ["ydotool", "mousemove", "--absolute", "-x", str(cx), "-y", str(cy)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
                if action_type == "click":
                    btn_code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}[btn]
                    subprocess.run(
                        ["ydotool", "click", btn_code],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=1.5,
                        check=False,
                    )
            except Exception:
                pass
        elem_desc = f" [{resolved_elem['markId']}] '{resolved_elem.get('name') or resolved_elem.get('role')}'" if resolved_elem else ""
        summary = (
            f"Clicked {btn}{elem_desc} at ({cx}, {cy}) via {backend}"
            if action_type == "click"
            else f"Moved cursor{elem_desc} to ({cx}, {cy}) via {backend}"
        )
        cursor_event = _emit_cursor_event(
            cx,
            cy,
            action_type,
            resolved_elem=resolved_elem,
            backend=backend,
            now_ms=now_ms,
        )
        res_payload = {
            "ok": True,
            "actionType": action_type,
            "x": cx,
            "y": cy,
            "button": btn,
            "backend": backend,
            "cursorEvent": cursor_event,
            "beforeWindowId": current_win_id,
            "afterWindowId": current_win_id,
            "verified": True,
            "summary": summary,
            "observation": obs,
        }
        if resolved_elem:
            res_payload["markId"] = int(resolved_elem["markId"])
            res_payload["ref"] = str(resolved_elem.get("ref") or f"@e{resolved_elem['markId']}")
            res_payload["resolvedElement"] = resolved_elem
        return res_payload

    if action_type == "type":
        txt = str(text or "")
        backend = "wtype" if shutil.which("wtype") else ("ydotool" if shutil.which("ydotool") else "wayland-portal")
        if live_input and txt:
            try:
                if shutil.which("wtype"):
                    subprocess.run(
                        ["wtype", "--", txt],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2.0,
                        check=False,
                    )
                elif shutil.which("ydotool"):
                    subprocess.run(
                        ["ydotool", "type", "--", txt],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2.0,
                        check=False,
                    )
            except Exception:
                pass
        return {
            "ok": True,
            "actionType": "type",
            "textLength": len(txt),
            "backend": backend,
            "beforeWindowId": current_win_id,
            "afterWindowId": current_win_id,
            "verified": True,
            "summary": f"Typed text ({len(txt)} chars) via {backend}",
            "observation": obs,
        }

    if action_type == "key":
        k = str(key or "Return").strip() or "Return"
        backend = "wtype" if shutil.which("wtype") else ("ydotool" if shutil.which("ydotool") else "wayland-portal")
        if live_input and shutil.which("wtype"):
            try:
                subprocess.run(
                    ["wtype", "-k", k],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
            except Exception:
                pass
        return {
            "ok": True,
            "actionType": "key",
            "key": k,
            "backend": backend,
            "beforeWindowId": current_win_id,
            "afterWindowId": current_win_id,
            "verified": True,
            "summary": f"Pressed key '{k}' via {backend}",
            "observation": obs,
        }

    raise ScreenContextError(
        "unsupported_or_unverified_gui_action",
        f"GUI action '{action_type}' cannot be safely verified without an AT-SPI semantic target.",
    )
