"""Grounded screen, Niri window, AT-SPI accessibility, and OCR observation + safe GUI broker.

Never guesses open files from process file-descriptor tables to claim what is visible on screen.
All observations carry source, timestamp, window identity, and scale metadata.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
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
    if isinstance(fixture, dict) and "niri" in fixture:
        return fixture["niri"]

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

    return {
        "available": bool(focused or windows or outputs),
        "focused": focused,
        "windows": windows,
        "outputs": outputs,
    }


def _extract_output_scale(niri_state):
    outputs = niri_state.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        return "", None
    for out_name, out_info in outputs.items():
        if not isinstance(out_info, dict):
            continue
        logical = out_info.get("logical")
        if isinstance(logical, dict) and isinstance(logical.get("scale"), (int, float)):
            scale = float(logical["scale"])
            if scale > 0:
                return str(out_name), scale
    return "", None


def _query_atspi_nodes(app_id, title):
    fixture = _load_fixture()
    if isinstance(fixture, dict) and isinstance(fixture.get("atspiLines"), list):
        return [str(x)[:160] for x in fixture["atspiLines"] if str(x).strip()][:14]
    if isinstance(fixture, dict) and isinstance(fixture.get("domLines"), list):
        return [str(x)[:160] for x in fixture["domLines"] if str(x).strip()][:14]

    # Query AT-SPI registry via python3-gobject if available and responsive
    script = (
        "import gi\n"
        "gi.require_version('Atspi', '2.0')\n"
        "from gi.repository import Atspi\n"
        "desktop = Atspi.get_desktop(0)\n"
        "out = []\n"
        "for i in range(min(desktop.get_child_count(), 16)):\n"
        "    app = desktop.get_child_at_index(i)\n"
        "    if not app: continue\n"
        "    aname = (app.get_name() or '').strip()\n"
        "    for j in range(min(app.get_child_count(), 6)):\n"
        "        win = app.get_child_at_index(j)\n"
        "        if not win: continue\n"
        "        wname = (win.get_name() or '').strip()\n"
        "        if wname: out.append(f'{aname}: {wname}')\n"
        "print('\\n'.join(out[:12]))\n"
    )
    try:
        proc = subprocess.run(
            ["python3", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.2,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            lines = [ln.strip()[:160] for ln in proc.stdout.decode("utf-8", errors="replace").splitlines() if ln.strip()]
            if lines:
                return lines[:12]
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def _capture_ocr_lines():
    fixture = _load_fixture()
    if isinstance(fixture, dict) and isinstance(fixture.get("ocrLines"), list):
        return [str(x)[:160] for x in fixture["ocrLines"] if str(x).strip()][:14]
    if not shutil.which("grim") or not shutil.which("tesseract"):
        return []
    fd, tmp_img = tempfile.mkstemp(prefix="companion_screen_", suffix=".png")
    os.close(fd)
    try:
        g_proc = subprocess.run(
            ["grim", tmp_img],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.5,
            check=False,
        )
        if g_proc.returncode != 0 or not os.path.exists(tmp_img):
            return []
        t_proc = subprocess.run(
            ["tesseract", tmp_img, "stdout", "--psm", "6"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=4.0,
            check=False,
        )
        if t_proc.returncode != 0:
            return []
        raw_text = t_proc.stdout.decode("utf-8", errors="replace")
        lines = []
        for ln in raw_text.splitlines():
            cleaned = " ".join(ln.split()).strip()
            if len(cleaned) >= 2:
                lines.append(cleaned[:160])
            if len(lines) >= 14:
                break
        return lines
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        if os.path.exists(tmp_img):
            try:
                os.unlink(tmp_img)
            except OSError:
                pass


def observe_active_window(allow_screenshot=False, previous_window_id=None, now_ms=None):
    ts = _now_ms(now_ms)
    niri = query_niri_state()
    focused = niri.get("focused") if isinstance(niri.get("focused"), dict) else {}
    win_id = focused.get("id")
    app_id = str(focused.get("app_id") or "desktop").strip()[:120]
    title = str(focused.get("title") or "").strip()[:160]
    pid = int(focused.get("pid") or 0) if isinstance(focused.get("pid"), int) else 0
    output_name, output_scale = _extract_output_scale(niri)

    focus_changed = (
        previous_window_id is not None
        and win_id is not None
        and str(previous_window_id) != str(win_id)
    )

    lines = []
    if title:
        lines.append(f"Window: {title} ({app_id})"[:160])

    # 1. Structured AT-SPI / DOM nodes
    atspi_lines = _query_atspi_nodes(app_id, title)
    source = "niri_metadata"
    if atspi_lines:
        source = "niri+atspi"
        for ln in atspi_lines:
            if ln not in lines:
                lines.append(ln[:160])

    # 2. Scoped OCR fallback only when permitted and needed
    if allow_screenshot and len(lines) <= 2:
        ocr_lines = _capture_ocr_lines()
        if ocr_lines:
            source = "niri+ocr" if source == "niri_metadata" else source + "+ocr"
            for ln in ocr_lines:
                if ln not in lines:
                    lines.append(ln[:160])

    if not lines:
        lines = ["(no active desktop window content detected)"]

    return {
        "kind": "active_app",
        "hostAvailable": bool(niri.get("available", False) or title or app_id != "desktop"),
        "packageId": app_id or "desktop",
        "version": source,
        "localOnlyDefault": True,
        "capabilities": ["window_metadata", "atspi_inspect"] + (["screen_ocr"] if allow_screenshot else []),
        "headings": lines[:16],
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
            "untrustedData": True,
        },
    }


def perform_gui_action(
    action_type,
    target_window_id=None,
    target_app_id="",
    coordinates=None,
    require_scale=False,
    now_ms=None,
):
    """Execute a verified Niri/AT-SPI GUI action and read back the resulting state."""
    before = observe_active_window(allow_screenshot=False, now_ms=now_ms)
    obs = before["observation"]
    current_win_id = obs.get("windowId")

    if target_window_id is not None and str(target_window_id) != str(current_win_id):
        if action_type != "focus_window" or int(target_window_id) < 0 or current_win_id is not None:
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

    raise ScreenContextError(
        "unsupported_or_unverified_gui_action",
        f"GUI action '{action_type}' cannot be safely verified without an AT-SPI semantic target.",
    )
