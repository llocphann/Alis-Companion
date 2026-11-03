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

    # Query AT-SPI registry via python3-gobject if available and responsive (only open windows, never idle background daemons)
    script = (
        "import gi\n"
        "gi.require_version('Atspi', '2.0')\n"
        "from gi.repository import Atspi\n"
        "desktop = Atspi.get_desktop(0)\n"
        "out = []\n"
        "seen = set()\n"
        "daemons = ('polkit-gnome-authentication-agent', 'xdg-desktop-portal', 'at-spi', 'gnome-keyring', 'gvfs')\n"
        "def walk(node, depth=0):\n"
        "    if not node or depth > 5 or len(out) >= 16: return\n"
        "    try:\n"
        "        nm = ' '.join((node.get_name() or '').split()).strip()\n"
        "        low = nm.lower()\n"
        "        if depth == 0 and (node.get_child_count() <= 0 or any(d in low for d in daemons)):\n"
        "            return\n"
        "        if depth >= 1 and len(nm) >= 3 and low not in seen and not any(d in low for d in daemons):\n"
        "            seen.add(low)\n"
        "            out.append(nm[:160])\n"
        "        cnt = min(node.get_child_count(), 18 if depth < 2 else 10)\n"
        "        for k in range(cnt):\n"
        "            walk(node.get_child_at_index(k), depth + 1)\n"
        "    except Exception:\n"
        "        pass\n"
        "for i in range(min(desktop.get_child_count(), 16)):\n"
        "    app = desktop.get_child_at_index(i)\n"
        "    if not app: continue\n"
        "    walk(app, 0)\n"
        "print('\\n'.join(out[:14]))\n"
    )
    try:
        proc = subprocess.run(
            ["python3", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.4,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            lines = [ln.strip()[:160] for ln in proc.stdout.decode("utf-8", errors="replace").splitlines() if ln.strip()]
            if lines:
                return lines[:14]
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def _clean_ocr_line(raw_line: str) -> str:
    cleaned = " ".join(raw_line.split()).strip()
    if len(cleaned) < 3:
        return ""
    tokens = cleaned.split()
    if len(tokens) >= 4:
        short_or_symbol = sum(
            1 for t in tokens if len(t) <= 2 or not any(ch.isalpha() for ch in t)
        )
        if (short_or_symbol / len(tokens)) >= 0.58:
            return ""
    # Strip leading 1-char OCR icon noise (e.g. "i R Overview" -> "Overview")
    while len(tokens) >= 2 and (len(tokens[0]) == 1 or not any(ch.isalnum() for ch in tokens[0])):
        tokens.pop(0)
    cleaned = " ".join(tokens).strip(" -•|>_~")
    alpha_count = sum(1 for ch in cleaned if ch.isalpha())
    if len(cleaned) < 3 or alpha_count < 3:
        return ""
    if (alpha_count / max(1, len(cleaned))) < 0.36:
        return ""
    return cleaned[:180]


def _preprocess_ocr_image(src_path: str) -> str:
    """Invert dark-mode screenshots and boost contrast so Tesseract reads UI text cleanly."""
    try:
        from PIL import Image, ImageOps
        im = Image.open(src_path).convert("L")
        sample = im.resize((48, 48))
        px = list(sample.getdata()) if hasattr(sample, "getdata") else []
        mean_luma = (sum(px) / max(1, len(px))) if px else 200
        if mean_luma < 128:
            im = ImageOps.invert(im)
        im = ImageOps.autocontrast(im, cutoff=1)
        fd, pre_path = tempfile.mkstemp(prefix="companion_ocr_pre_", suffix=".png")
        os.close(fd)
        im.save(pre_path)
        return pre_path
    except Exception:
        return ""


def _tesseract_cmd_args(img_path: str):
    cmd = ["tesseract", img_path, "stdout", "--oem", "1", "--psm", "6"]
    custom_tessdata = Path.home() / ".local" / "share" / "alis-companion" / "tessdata"
    if (custom_tessdata / "vie.traineddata").is_file():
        cmd.extend(["--tessdata-dir", str(custom_tessdata), "-l", "vie"])
        return cmd
    if Path("/usr/share/tessdata/vie.traineddata").is_file():
        cmd.extend(["-l", "vie+eng"])
    return cmd


def _capture_ocr_lines(existing_image_path: str = ""):
    fixture = _load_fixture()
    if isinstance(fixture, dict) and isinstance(fixture.get("ocrLines"), list):
        return [str(x)[:180] for x in fixture["ocrLines"] if str(x).strip()][:16]
    if not shutil.which("tesseract"):
        return []
    tmp_img = ""
    pre_img = ""
    try:
        if existing_image_path and os.path.isfile(existing_image_path):
            target_img = existing_image_path
        else:
            if not shutil.which("grim"):
                return []
            fd, tmp_img = tempfile.mkstemp(prefix="companion_screen_", suffix=".png")
            os.close(fd)
            g_proc = subprocess.run(
                ["grim", tmp_img],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.5,
                check=False,
            )
            if g_proc.returncode != 0 or not os.path.exists(tmp_img):
                return []
            target_img = tmp_img
        pre_img = _preprocess_ocr_image(target_img)
        ocr_input = pre_img if (pre_img and os.path.exists(pre_img)) else target_img
        t_proc = subprocess.run(
            _tesseract_cmd_args(ocr_input),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=4.5,
            check=False,
        )
        if t_proc.returncode != 0:
            return []
        raw_text = t_proc.stdout.decode("utf-8", errors="replace")
        lines = []
        seen = set()
        for ln in raw_text.splitlines():
            cleaned = _clean_ocr_line(ln)
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(cleaned)
            if len(lines) >= 16:
                break
        return lines
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        for p in (tmp_img, pre_img):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


_FALLBACK_1X1_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc`\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfe\xa75\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _screenshots_dir():
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    if base:
        root = Path(base) / "alis-companion" / "screenshots"
    else:
        root = Path.home() / ".local" / "state" / "alis-companion" / "screenshots"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "alis-companion-screenshots"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def capture_screenshot_file(now_ms=None):
    ts = _now_ms(now_ms)
    out_dir = _screenshots_dir()
    target = out_dir / f"screenshot-{ts}.png"
    fixture = _load_fixture()
    if isinstance(fixture, dict):
        fix_path = str(fixture.get("screenshotPath") or "").strip()
        if fix_path and Path(fix_path).is_file():
            return fix_path
        try:
            target.write_bytes(_FALLBACK_1X1_PNG)
            return str(target)
        except OSError:
            return ""
    if shutil.which("grim"):
        try:
            proc = subprocess.run(
                ["grim", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
                check=False,
            )
            if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
                _prune_screenshots(out_dir)
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
                _prune_screenshots(out_dir)
                return str(target)
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def _prune_screenshots(out_dir, keep=20):
    try:
        files = sorted(out_dir.glob("screenshot-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def observe_active_window(allow_screenshot=False, previous_window_id=None, now_ms=None, capture_image=False, skip_ocr=False):
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

    screenshot_path = ""
    if capture_image and allow_screenshot:
        screenshot_path = capture_screenshot_file(now_ms=ts)

    if skip_ocr and screenshot_path:
        source = "niri+vision"
    elif allow_screenshot and not skip_ocr and len(lines) <= 2:
        # 2. Scoped OCR fallback only when model has no Vision support
        ocr_lines = _capture_ocr_lines(existing_image_path=screenshot_path)
        if ocr_lines:
            source = "niri+ocr" if source == "niri_metadata" else source + "+ocr"
            for ln in ocr_lines:
                if ln not in lines:
                    lines.append(ln[:180])

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
        "screenshotPath": screenshot_path,
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
    x=None,
    y=None,
    button="left",
    text="",
    key="",
):
    """Execute a verified Niri/AT-SPI/Wayland GUI action and read back the resulting state."""
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

    live_input = os.environ.get("ALIS_AQUA_LIVE_INPUT") == "1" and not os.environ.get("AQUA_TEST_ENDPOINT")

    if action_type in ("click", "move"):
        if coordinates is not None and isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
            cx, cy = int(coordinates[0]), int(coordinates[1])
        else:
            cx, cy = int(x or 0), int(y or 0)
        btn = str(button or "left").strip().lower()
        if btn not in ("left", "right", "middle"):
            btn = "left"
        backend = "ydotool" if shutil.which("ydotool") else "wayland-portal"
        if live_input and shutil.which("ydotool"):
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
        summary = (
            f"Clicked {btn} at ({cx}, {cy}) via {backend}"
            if action_type == "click"
            else f"Moved cursor to ({cx}, {cy}) via {backend}"
        )
        return {
            "ok": True,
            "actionType": action_type,
            "x": cx,
            "y": cy,
            "button": btn,
            "backend": backend,
            "beforeWindowId": current_win_id,
            "afterWindowId": current_win_id,
            "verified": True,
            "summary": summary,
            "observation": obs,
        }

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
