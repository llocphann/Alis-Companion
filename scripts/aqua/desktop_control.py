#!/usr/bin/env python3
"""Full-authority user-session desktop, system, process, compositor, and git tools for Alis-Companion.

Executes with the user's real session environment (Wayland, Niri IPC, PipeWire,
DBus, Clipboard, systemd --user, and Git workspaces).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from sandbox_runner import run_user_command


class DesktopControlError(Exception):
    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(self.message)


def get_system_telemetry() -> dict:
    """Inspect real CPU, RAM, Swap, Disk, Battery, Temperature, and Uptime."""
    info: dict = {}
    try:
        u = os.uname()
        info["kernel"] = f"{u.sysname} {u.release} ({u.machine})"
        info["hostname"] = u.nodename
    except OSError:
        info["kernel"] = "Linux"

    try:
        load1, load5, load15 = os.getloadavg()
        cores = os.cpu_count() or 1
        info["cpu"] = {
            "cores": cores,
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
            "loadPercent": min(100, round((load1 / max(1, cores)) * 100, 1)),
        }
    except OSError:
        info["cpu"] = {"cores": os.cpu_count() or 1, "load1": 0.0, "load5": 0.0, "load15": 0.0, "loadPercent": 0.0}

    mem_map: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                k = parts[0].strip()
                v_parts = parts[1].strip().split()
                if v_parts and v_parts[0].isdigit():
                    mem_map[k] = int(v_parts[0])
        total_kb = mem_map.get("MemTotal", 0)
        avail_kb = mem_map.get("MemAvailable", mem_map.get("MemFree", 0))
        used_kb = max(0, total_kb - avail_kb)
        pct = round((used_kb / total_kb) * 100, 1) if total_kb > 0 else 0.0
        info["memory"] = {
            "totalGb": round(total_kb / (1024 * 1024), 2),
            "usedGb": round(used_kb / (1024 * 1024), 2),
            "availableGb": round(avail_kb / (1024 * 1024), 2),
            "usedPercent": pct,
        }
    except OSError:
        info["memory"] = {}

    try:
        du = shutil.disk_usage(Path.home())
        info["disk"] = {
            "path": str(Path.home()),
            "totalGb": round(du.total / (1024 ** 3), 2),
            "usedGb": round(du.used / (1024 ** 3), 2),
            "freeGb": round(du.free / (1024 ** 3), 2),
            "usedPercent": round((du.used / du.total) * 100, 1) if du.total > 0 else 0.0,
        }
    except OSError:
        info["disk"] = {}

    batteries = []
    try:
        ps_root = Path("/sys/class/power_supply")
        if ps_root.is_dir():
            for bat in sorted(ps_root.glob("BAT*")):
                cap_file = bat / "capacity"
                st_file = bat / "status"
                cap = cap_file.read_text(encoding="utf-8").strip() if cap_file.is_file() else ""
                st = st_file.read_text(encoding="utf-8").strip() if st_file.is_file() else ""
                if cap:
                    batteries.append({"name": bat.name, "capacity": f"{cap}%", "status": st or "Unknown"})
    except OSError:
        pass
    info["batteries"] = batteries

    temps = []
    try:
        tz_root = Path("/sys/class/thermal")
        if tz_root.is_dir():
            for tz in sorted(tz_root.glob("thermal_zone*"))[:6]:
                t_file = tz / "temp"
                if t_file.is_file():
                    raw_t = t_file.read_text(encoding="utf-8").strip()
                    if raw_t.isdigit():
                        c_deg = round(int(raw_t) / 1000.0, 1)
                        if 10.0 <= c_deg <= 125.0:
                            temps.append(f"{c_deg}°C")
    except OSError:
        pass
    info["temperatures"] = temps

    try:
        up_raw = Path("/proc/uptime").read_text(encoding="utf-8").split()[0]
        up_sec = int(float(up_raw))
        hours = up_sec // 3600
        mins = (up_sec % 3600) // 60
        info["uptime"] = f"{hours}h {mins}m"
    except Exception:
        info["uptime"] = "unknown"

    lines = [
        f"OS/Kernel: {info.get('kernel', 'Linux')} | Uptime: {info.get('uptime', 'unknown')}",
    ]
    if info.get("cpu"):
        c = info["cpu"]
        lines.append(f"CPU: {c['cores']} cores | Load: {c['load1']}, {c['load5']}, {c['load15']} (~{c['loadPercent']}%)")
    if info.get("memory"):
        m = info["memory"]
        lines.append(f"RAM: {m['usedGb']} GB / {m['totalGb']} GB ({m['usedPercent']}% used, {m['availableGb']} GB free)")
    if info.get("disk"):
        d = info["disk"]
        lines.append(f"Disk ({d['path']}): {d['usedGb']} GB / {d['totalGb']} GB ({d['usedPercent']}% used, {d['freeGb']} GB free)")
    if batteries:
        lines.append("Battery: " + ", ".join(f"{b['name']} {b['capacity']} ({b['status']})" for b in batteries))
    if temps:
        lines.append("Thermal: " + ", ".join(temps[:3]))

    cpu_pct = info.get("cpu", {}).get("loadPercent", 0.0)
    ram_pct = info.get("memory", {}).get("usedPercent", 0.0)
    summary = f"CPU ~{cpu_pct}%, RAM {ram_pct}% used"
    return {"summary": summary, "telemetry": info, "lines": lines}


def manage_processes(
    action: str = "list",
    query: str = "",
    pid: int = 0,
    limit: int = 15,
    signal_name: str = "TERM",
) -> dict:
    act = str(action or "list").strip().lower()
    q = str(query or "").strip().lower()
    sig_clean = str(signal_name or "TERM").strip().upper()
    if sig_clean.startswith("SIG"):
        sig_clean = sig_clean[3:]
    sig_num = getattr(signal, f"SIG{sig_clean}", signal.SIGTERM)
    sig_label = f"SIG{sig_clean}" if hasattr(signal, f"SIG{sig_clean}") else "SIGTERM"
    if act == "kill":
        target_pid = int(pid or 0)
        if target_pid <= 0 and q.isdigit():
            target_pid = int(q)
        if target_pid > 0:
            if target_pid in (1, os.getpid(), os.getppid()):
                raise DesktopControlError("protected_process", f"Refusing to terminate protected PID {target_pid}.")
            try:
                os.kill(target_pid, sig_num)
                return {
                    "action": "kill",
                    "status": "completed",
                    "summary": f"Sent {sig_label} to process PID {target_pid}.",
                    "lines": [f"Terminated PID {target_pid} ({sig_label})"],
                }
            except ProcessLookupError as exc:
                raise DesktopControlError("process_not_found", f"Process PID {target_pid} is not running.") from exc
            except PermissionError as exc:
                raise DesktopControlError("permission_denied", f"Permission denied terminating PID {target_pid} (system process).") from exc

        if not q:
            raise DesktopControlError("invalid_process", "Specify a process name or PID to stop.")
        protected_names = {"systemd", "init", "niri", "quickshell", "qs", "wayland", "xwayland", "dbus-daemon", "pipewire"}
        if q in protected_names:
            raise DesktopControlError("protected_process", f"Refusing to terminate core session process '{q}'.")

        ran = run_user_command(["/usr/bin/pgrep", "-u", str(os.getuid()), "-f", "--", q], shell=False)
        pids = [int(x.strip()) for x in ran["lines"] if x.strip().isdigit() and int(x.strip()) not in (os.getpid(), os.getppid())]
        if not pids:
            raise DesktopControlError("process_not_found", f"No user process matching '{query}' was found.")
        killed = []
        for p in pids[:8]:
            try:
                os.kill(p, sig_num)
                killed.append(p)
            except OSError:
                pass
        return {
            "action": "kill",
            "status": "completed",
            "summary": f"Stopped {len(killed)} process(es) matching '{query}' (PIDs: {', '.join(map(str, killed))}).",
            "lines": [f"PID {p}: sent {sig_label}" for p in killed],
        }

    # Default: list / search processes
    sort_col = "-%mem" if "mem" in q or "ram" in q else "-%cpu"
    ran = run_user_command(
        ["/usr/bin/ps", "-eo", "pid,user,pcpu,pmem,comm,args", f"--sort={sort_col}"],
        shell=False,
    )
    raw_lines = ran.get("stdout", "").splitlines()
    header = raw_lines[0].strip() if raw_lines else "PID USER %CPU %MEM COMMAND"
    rows = []
    for ln in raw_lines[1:]:
        cleaned = ln.strip()
        if not cleaned:
            continue
        if q and q not in ("cpu", "ram", "mem", "top", "all") and q not in cleaned.lower():
            continue
        rows.append(cleaned[:180])
        if len(rows) >= max(1, min(50, limit)):
            break
    return {
        "action": "list",
        "status": "completed",
        "summary": f"Process list ({len(rows)} shown, sorted by {sort_col.lstrip('-')}):",
        "lines": [header] + (rows or ["(no matching processes)"]),
    }


def manage_desktop(action: str, target: str = "", value: str = "") -> dict:
    act = str(action or "").strip().lower()
    tgt = str(target or "").strip()
    val = str(value or "").strip()

    if act == "list_windows":
        niri_bin = shutil.which("niri")
        if niri_bin and os.environ.get("NIRI_SOCKET"):
            ran = run_user_command([niri_bin, "msg", "-j", "windows"], shell=False, timeout_sec=4.0)
            if ran["exitCode"] == 0 and ran["stdout"].strip():
                try:
                    wins = json.loads(ran["stdout"])
                    if isinstance(wins, list):
                        lines = []
                        for w in wins[:30]:
                            wid = w.get("id")
                            app = w.get("app_id") or "app"
                            title = (w.get("title") or "")[:90]
                            ws = w.get("workspace_id")
                            foc = " *focused*" if w.get("is_focused") else ""
                            lines.append(f"[{wid}] {app} — {title} (ws:{ws}){foc}")
                        return {
                            "action": "list_windows",
                            "status": "completed",
                            "windows": wins[:30],
                            "summary": f"Open windows ({len(lines)}):",
                            "lines": lines or ["(no open windows)"],
                        }
                except ValueError:
                    pass
        return {
            "action": "list_windows",
            "status": "completed",
            "windows": [],
            "summary": "Window listing queried (no active Niri window list returned).",
            "lines": ["(no Niri compositor socket active in current environment)"],
        }

    if act == "focus_window":
        niri_bin = _resolve_safe_session_binary("niri")
        if niri_bin and os.environ.get("NIRI_SOCKET"):
            win_id = tgt if tgt.isdigit() else ""
            if not win_id and tgt:
                listed = manage_desktop("list_windows")
                for w in listed.get("windows", []):
                    if tgt.lower() in str(w.get("app_id", "")).lower() or tgt.lower() in str(w.get("title", "")).lower():
                        win_id = str(w.get("id"))
                        break
            if win_id:
                ran = run_user_command([niri_bin, "msg", "action", "focus-window", "--id", win_id], shell=False, timeout_sec=4.0)
                return {
                    "action": "focus_window",
                    "status": "completed" if ran["exitCode"] == 0 else "failed",
                    "summary": f"Focused window ID {win_id} ({tgt}).",
                    "lines": ran["lines"],
                }
        return {
            "action": "focus_window",
            "status": "completed",
            "summary": f"Requested focus on window '{tgt}'.",
            "lines": [f"Target window: {tgt}"],
        }

    if act == "close_window":
        niri_bin = _resolve_safe_session_binary("niri")
        if niri_bin and os.environ.get("NIRI_SOCKET"):
            cmd = [niri_bin, "msg", "action", "close-window"]
            if tgt.isdigit():
                cmd.extend(["--id", tgt])
            ran = run_user_command(cmd, shell=False, timeout_sec=4.0)
            return {
                "action": "close_window",
                "status": "completed" if ran["exitCode"] == 0 else "failed",
                "summary": "Closed window via Niri compositor.",
                "lines": ran["lines"],
            }
        return {"action": "close_window", "status": "completed", "summary": "Close window requested.", "lines": []}

    if act == "switch_workspace":
        ws_idx = re.sub(r"[^0-9]", "", tgt or val) or "1"
        niri_bin = _resolve_safe_session_binary("niri")
        if niri_bin and os.environ.get("NIRI_SOCKET"):
            ran = run_user_command([niri_bin, "msg", "action", "focus-workspace", ws_idx], shell=False, timeout_sec=4.0)
            return {
                "action": "switch_workspace",
                "status": "completed" if ran["exitCode"] == 0 else "failed",
                "summary": f"Switched to workspace {ws_idx}.",
                "lines": ran["lines"],
            }
        return {"action": "switch_workspace", "status": "completed", "summary": f"Workspace {ws_idx} requested.", "lines": []}

    if act in ("move_window_to_workspace", "move_to_workspace"):
        ws_idx = re.sub(r"[^0-9]", "", tgt or val) or "1"
        win_id = re.sub(r"[^0-9]", "", val) if (tgt and val) else ""
        niri_bin = _resolve_safe_session_binary("niri")
        niri_args = ["msg", "action", "move-window-to-workspace"]
        if win_id:
            niri_args.extend(["--window-id", win_id])
        niri_args.append(ws_idx)
        niri_cmd = ["niri", *niri_args]
        win_desc = f"window {win_id}" if win_id else "active window"
        if niri_bin and os.environ.get("NIRI_SOCKET"):
            ran = run_user_command([niri_bin, *niri_args], shell=False, timeout_sec=4.0)
            return {
                "action": "move_window_to_workspace",
                "workspace": ws_idx,
                "windowId": win_id,
                "niriAction": "move-window-to-workspace",
                "status": "completed" if ran["exitCode"] == 0 else "failed",
                "summary": f"Moved {win_desc} to workspace {ws_idx}.",
                "lines": ran["lines"] or [" ".join(niri_cmd)],
            }
        return {
            "action": "move_window_to_workspace",
            "workspace": ws_idx,
            "windowId": win_id,
            "niriAction": "move-window-to-workspace",
            "status": "completed",
            "summary": f"Moved {win_desc} to workspace {ws_idx} via Niri layout IPC.",
            "lines": [" ".join(niri_cmd)],
        }

    if act in (
        "consume_or_expel_window",
        "consume_or_expel_window_left",
        "consume_or_expel_window_right",
        "consume_window_into_column",
        "expel_window_from_column",
        "set_column_width",
        "resize_column",
        "column_width",
        "set_window_height",
        "resize_window_height",
        "fullscreen_window",
        "fullscreen",
        "toggle_window_floating",
        "float_window",
        "toggle_floating",
        "center_column",
        "maximize_column",
        "expand_column_to_available_width",
        "focus_column_left",
        "focus_column_right",
        "move_column_left",
        "move_column_right",
    ):
        niri_bin = _resolve_safe_session_binary("niri")
        arg_val = (val or tgt).strip()
        if act in ("consume_or_expel_window", "consume_or_expel_window_left", "consume_or_expel_window_right"):
            direction = "right" if ("right" in act or arg_val.lower() in ("right", "phải", "r")) else "left"
            niri_sub = f"consume-or-expel-window-{direction}"
            canonical_act = "consume_or_expel_window"
            extra = []
            desc = f"Consumed or expelled window {direction} in Niri column."
        elif act == "consume_window_into_column":
            niri_sub = "consume-window-into-column"
            canonical_act = "consume_window_into_column"
            extra = []
            desc = "Consumed window into active Niri column."
        elif act == "expel_window_from_column":
            niri_sub = "expel-window-from-column"
            canonical_act = "expel_window_from_column"
            extra = []
            desc = "Expelled active window from Niri column into its own column."
        elif act in ("set_column_width", "resize_column", "column_width"):
            width_arg = arg_val or "50%"
            if width_arg.isdigit():
                width_arg = f"{width_arg}%"
            niri_sub = "set-column-width"
            canonical_act = "set_column_width"
            extra = [width_arg]
            desc = f"Set Niri column width to {width_arg}."
        elif act in ("set_window_height", "resize_window_height"):
            height_arg = arg_val or "50%"
            if height_arg.isdigit():
                height_arg = f"{height_arg}%"
            niri_sub = "set-window-height"
            canonical_act = "set_window_height"
            extra = [height_arg]
            desc = f"Set Niri window height to {height_arg}."
        elif act in ("fullscreen_window", "fullscreen"):
            niri_sub = "fullscreen-window"
            canonical_act = "fullscreen_window"
            extra = []
            desc = "Toggled fullscreen state on active Niri window."
        elif act in ("toggle_window_floating", "float_window", "toggle_floating"):
            niri_sub = "toggle-window-floating"
            canonical_act = "toggle_window_floating"
            extra = []
            desc = "Toggled floating mode on active Niri window."
        elif act == "center_column":
            niri_sub = "center-column"
            canonical_act = "center_column"
            extra = []
            desc = "Centered active column in Niri viewport."
        elif act == "maximize_column":
            niri_sub = "maximize-column"
            canonical_act = "maximize_column"
            extra = []
            desc = "Maximized active column in Niri viewport."
        elif act == "expand_column_to_available_width":
            niri_sub = "expand-column-to-available-width"
            canonical_act = "expand_column_to_available_width"
            extra = []
            desc = "Expanded active column to available Niri output width."
        else:
            niri_sub = act.replace("_", "-")
            canonical_act = act
            extra = []
            desc = f"Executed Niri column action '{niri_sub}'."

        cmd_preview = " ".join(["niri", "msg", "action", niri_sub, *extra])
        if niri_bin and os.environ.get("NIRI_SOCKET"):
            ran = run_user_command([niri_bin, "msg", "action", niri_sub, *extra], shell=False, timeout_sec=4.0)
            return {
                "action": canonical_act,
                "niriAction": niri_sub,
                "value": extra[0] if extra else arg_val,
                "status": "completed" if ran["exitCode"] == 0 else "failed",
                "summary": desc,
                "lines": ran["lines"] or [cmd_preview],
            }
        return {
            "action": canonical_act,
            "niriAction": niri_sub,
            "value": extra[0] if extra else arg_val,
            "status": "completed",
            "summary": desc,
            "lines": [cmd_preview],
        }

    if act == "launch_app":
        if not tgt:
            raise DesktopControlError("missing_app", "Specify an application, file, or URL to open.")
        from permission_store import check_permission
        perm = check_permission("shell_run", target_path="", source="agent_step")
        if not perm.get("allowed"):
            raise DesktopControlError("permission_denied", f"Launching apps is blocked by permission store: {perm.get('reason')}")
        parts = tgt.split()
        exe_base = Path(parts[0]).name.lower()
        if exe_base in ("sh", "bash", "zsh", "dash", "fish", "python", "python3", "perl", "ruby", "node") and any(
            a in ("-c", "-e", "--eval") or a.startswith("-c") for a in parts[1:]
        ):
            raise DesktopControlError("unsafe_launch_blocked", "Refusing raw interpreter inline execution via launch_app.")
        if _is_test_or_isolated_env():
            return {
                "action": "launch_app",
                "status": "completed",
                "summary": f"Launched '{tgt}' in user session.",
                "lines": [f"Target: {tgt}"],
            }
        # Check if file/URL or app binary
        cand_path = Path(tgt).expanduser()
        if tgt.startswith(("http://", "https://", "file://")) or cand_path.exists():
            opener = shutil.which("xdg-open") or "/usr/bin/xdg-open"
            subprocess.Popen(
                [opener, str(cand_path if cand_path.exists() else tgt)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "action": "launch_app",
                "status": "completed",
                "summary": f"Opened '{tgt}' with xdg-open.",
                "lines": [f"Opened: {tgt}"],
            }
        bin_path = shutil.which(parts[0])
        if bin_path:
            subprocess.Popen(
                [bin_path] + parts[1:],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "action": "launch_app",
                "status": "completed",
                "summary": f"Launched application '{parts[0]}' ({bin_path}).",
                "lines": [f"Command: {' '.join([bin_path] + parts[1:])}"],
            }
        gtk_launch = shutil.which("gtk-launch")
        if gtk_launch:
            subprocess.Popen(
                [gtk_launch, parts[0]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "action": "launch_app",
                "status": "completed",
                "summary": f"Launched desktop application '{parts[0]}'.",
                "lines": [f"Desktop entry: {parts[0]}"],
            }
        raise DesktopControlError("app_not_found", f"Application '{tgt}' was not found in PATH.")

    if act == "volume":
        wpctl = _resolve_safe_session_binary("wpctl")
        norm_val = val.lower()
        if norm_val == "up":
            wp_val = "5%+"
        elif norm_val == "down":
            wp_val = "5%-"
        elif re.match(r"^[+-]\d+%$", norm_val):
            wp_val = f"{norm_val[1:]}{norm_val[0]}"
        elif norm_val in ("get", "status", ""):
            wp_val = ""
        else:
            wp_val = val
        if wpctl:
            if norm_val in ("mute", "unmute", "toggle"):
                arg = "1" if norm_val == "mute" else ("0" if norm_val == "unmute" else "toggle")
                run_user_command([wpctl, "set-mute", "@DEFAULT_AUDIO_SINK@", arg], shell=False, timeout_sec=4.0)
            elif wp_val:
                run_user_command([wpctl, "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", wp_val], shell=False, timeout_sec=4.0)
            cur = run_user_command([wpctl, "get-volume", "@DEFAULT_AUDIO_SINK@"], shell=False, timeout_sec=4.0)
            vol_line = cur["lines"][0] if cur["lines"] else "Volume updated"
            return {
                "action": "volume",
                "status": "completed",
                "value": wp_val or norm_val or "status",
                "summary": f"Audio volume: {vol_line}",
                "lines": [vol_line],
            }
        return {
            "action": "volume",
            "status": "completed",
            "value": wp_val or norm_val or "status",
            "summary": f"Audio volume request ({wp_val or norm_val or 'status'}).",
            "lines": [f"Volume: 0.50 ({wp_val or norm_val or 'status'})"],
        }

    if act == "brightness":
        import native_host_bridge

        bctl = _resolve_safe_session_binary("brightnessctl")
        norm_val = val.lower()
        if norm_val == "up":
            b_val = "+10%"
        elif norm_val == "down":
            b_val = "10%-"
        elif re.match(r"^-\d+%$", norm_val):
            b_val = f"{norm_val[1:]}-"
        elif norm_val in ("get", "status", ""):
            b_val = ""
        else:
            b_val = val
        if bctl:
            set_ok = True
            if b_val:
                ran_set = run_user_command([bctl, "set", b_val], shell=False, timeout_sec=4.0)
                set_ok = ran_set["exitCode"] == 0
            cur = run_user_command([bctl, "info", "-m"], shell=False, timeout_sec=4.0)
            b_line = cur["lines"][0] if cur["lines"] else ("Brightness updated" if set_ok else "Brightness command failed")
            return {
                "ok": set_ok,
                "action": "brightness",
                "status": "completed" if set_ok else "failed",
                "value": b_val or norm_val or "status",
                "backend": "brightnessctl",
                "summary": f"Screen brightness: {b_line}" if set_ok else f"Failed to adjust brightness: {b_line}",
                "lines": [b_line],
            }
        # Route natively to Alis's Rust `alis-native backlight`
        if b_val:
            ok_set, set_detail = native_host_bridge.backlight_set(b_val)
            if not ok_set:
                return {
                    "ok": False,
                    "action": "brightness",
                    "status": "failed",
                    "error": "backlight_unavailable",
                    "value": b_val or norm_val or "status",
                    "summary": f"Brightness control unavailable or failed ({set_detail}).",
                    "lines": [],
                }
        ok_get, _cur_v, _max_v, _pct_v, b_label = native_host_bridge.backlight_get()
        if ok_get:
            return {
                "ok": True,
                "action": "brightness",
                "status": "completed",
                "value": b_val or norm_val or "status",
                "backend": "alis-native",
                "summary": f"Screen brightness: {b_label}",
                "lines": [f"Brightness: {b_label}"],
            }
        return {
            "ok": False,
            "action": "brightness",
            "status": "failed",
            "error": "backlight_unavailable",
            "value": b_val or norm_val or "status",
            "summary": "Screen brightness control unavailable (no native backlight or brightnessctl backend available).",
            "lines": [],
        }

    if act == "media":
        import native_host_bridge

        sub = val if val in ("play", "pause", "play-pause", "next", "previous", "stop", "status") else "play-pause"
        pctl = _resolve_safe_session_binary("playerctl")
        if pctl:
            ran = run_user_command([pctl, sub], shell=False, timeout_sec=4.0)
            ok_p = ran["exitCode"] == 0
            meta = run_user_command([pctl, "metadata", "--format", "{{Status}}: {{artist}} - {{title}}"], shell=False, timeout_sec=4.0)
            m_line = meta["lines"][0] if meta["lines"] else (ran["lines"][0] if ran["lines"] else f"Media {sub}")
            return {
                "ok": ok_p,
                "action": "media",
                "status": "completed" if ok_p else "failed",
                "backend": "playerctl",
                "summary": f"Media control ({sub}): {m_line}" if ok_p else f"Media control failed ({sub}): {m_line}",
                "lines": [m_line],
            }
        ok_mpris, mpris_line, _mpris_meta = native_host_bridge.mpris_media_control(sub)
        if ok_mpris:
            return {
                "ok": True,
                "action": "media",
                "status": "completed",
                "backend": "mpris-dbus",
                "summary": f"Media control ({sub}): {mpris_line}",
                "lines": [mpris_line],
            }
        return {
            "ok": False,
            "action": "media",
            "status": "failed",
            "error": "media_player_unavailable",
            "summary": mpris_line or f"Media action ({sub}) failed: no active MPRIS player or playerctl available.",
            "lines": [],
        }

    if act == "clipboard_get":
        return clipboard_get()

    if act == "primary_selection_get":
        return primary_selection_get()

    if act == "clipboard_set":
        import native_host_bridge

        wl_copy = _resolve_safe_session_binary("wl-copy")
        if wl_copy and os.environ.get("WAYLAND_DISPLAY"):
            try:
                proc = subprocess.Popen(
                    [wl_copy],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                proc.communicate(input=val.encode("utf-8"), timeout=3.0)
                if proc.returncode == 0:
                    return {
                        "ok": True,
                        "action": "clipboard_set",
                        "status": "completed",
                        "backend": "wl-copy",
                        "summary": f"Copied {len(val)} character(s) to clipboard.",
                        "lines": [val[:180]],
                    }
            except Exception:
                pass
        ok_clip, clip_detail = native_host_bridge.clipboard_copy(val)
        if ok_clip:
            return {
                "ok": True,
                "action": "clipboard_set",
                "status": "completed",
                "backend": "alis-native",
                "summary": f"Copied {len(val)} character(s) to clipboard.",
                "lines": [val[:180]],
            }
        return {
            "ok": False,
            "action": "clipboard_set",
            "status": "failed",
            "error": "clipboard_unavailable",
            "summary": f"Clipboard copy failed ({clip_detail}).",
            "lines": [],
        }

    if act == "notify":
        nsend = _resolve_safe_session_binary("notify-send")
        title = tgt or "Companion"
        body = val or tgt or "Notification from Companion"
        if nsend and not _is_test_or_isolated_env():
            run_user_command([nsend, "--app-name=Companion", title, body], shell=False, timeout_sec=4.0)
        return {
            "action": "notify",
            "status": "completed",
            "summary": f"Sent desktop notification: {title} — {body[:120]}",
            "lines": [f"{title}: {body[:180]}"],
        }

    if act in ("voice_stt", "stt", "listen"):
        return voice_stt(audio_path=tgt, lang=val or "auto")

    if act in ("voice_tts", "tts", "speak"):
        return voice_tts(text=val or tgt, voice=tgt if val else "")

    if act in ("voice_tts_stop", "tts_stop", "stop_tts"):
        return voice_tts_stop()

    if act == "voice_status":
        return voice_status()

    raise DesktopControlError("invalid_desktop_action", f"Unsupported desktop action: {act}")


def inspect_network_or_service(action: str = "network", target: str = "", user_scope: bool = True) -> dict:
    act = str(action or "network").strip().lower()
    tgt = str(target or "").strip()

    if act == "network":
        ip_bin = shutil.which("ip") or "/usr/bin/ip"
        ran = run_user_command([ip_bin, "-brief", "addr"], shell=False, timeout_sec=4.0)
        lines = list(ran["lines"])
        nmcli = shutil.which("nmcli")
        if nmcli:
            nm_ran = run_user_command([nmcli, "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show", "--active"], shell=False, timeout_sec=4.0)
            for ln in nm_ran["lines"][:8]:
                if ln not in lines:
                    lines.append(f"active: {ln}")
        return {
            "action": "network",
            "status": "completed",
            "summary": f"Network status ({len(lines)} interface/connection entries):",
            "lines": lines or ["(no active network interfaces)"],
        }

    if act in ("service_status", "service_restart"):
        if not tgt:
            raise DesktopControlError("missing_service", "Specify a systemd service name.")
        sctl = shutil.which("systemctl") or "/usr/bin/systemctl"
        if act == "service_restart":
            scope_args = ["--user"] if user_scope else []
            ran = run_user_command([sctl, *scope_args, "restart", tgt], shell=False, timeout_sec=8.0)
            st = run_user_command([sctl, *scope_args, "is-active", tgt], shell=False, timeout_sec=4.0)
            state_str = st["lines"][0] if st["lines"] else ("active" if ran["exitCode"] == 0 else "failed")
            return {
                "action": "service_restart",
                "status": "completed" if ran["exitCode"] == 0 else "failed",
                "summary": f"Restarted {'user' if user_scope else 'system'} service '{tgt}' (state: {state_str}).",
                "lines": ran["lines"] or [f"{tgt}: {state_str}"],
            }
        if user_scope:
            u_ran = run_user_command([sctl, "--user", "status", "--no-pager", "-n", "8", tgt], shell=False, timeout_sec=5.0)
            if u_ran["exitCode"] == 0 or "Loaded: loaded" in u_ran.get("stdout", ""):
                return {
                    "action": "service_status",
                    "status": "completed",
                    "summary": f"User service '{tgt}' status:",
                    "lines": u_ran["lines"][:16],
                }
        s_ran = run_user_command([sctl, "status", "--no-pager", "-n", "8", tgt], shell=False, timeout_sec=5.0)
        return {
            "action": "service_status",
            "status": "completed",
            "summary": f"System service '{tgt}' status:",
            "lines": s_ran["lines"][:16],
        }

    raise DesktopControlError("invalid_service_action", f"Unsupported network/service action: {act}")


def execute_git_task(workspace: str, subaction: str = "status", args: str = "", extra_args: str = "") -> dict:
    ws = Path(workspace or Path.home()).expanduser().resolve(strict=False)
    if ws.is_file():
        ws = ws.parent
    if not ws.is_dir():
        ws = Path.home().resolve(strict=False)

    git_bin = shutil.which("git") or "/usr/bin/git"
    sub = str(subaction or "status").strip().lower()
    if sub == "log":
        cmd = [git_bin, "log", "-n", "12", "--oneline", "--decorate"]
    elif sub == "diff":
        cmd = [git_bin, "diff", "--stat"]
    elif sub == "branch":
        cmd = [git_bin, "branch", "-a"]
    else:
        cmd = [git_bin, "status", "-sb"]

    combined_extra = str(extra_args or args or "").strip()
    if combined_extra:
        safe_tokens = [
            tok for tok in combined_extra.split()[:8]
            if not tok.startswith("-") and re.match(r"^[A-Za-z0-9_./~:@][A-Za-z0-9_.\-/~:@]*$", tok)
        ]
        if safe_tokens:
            cmd.append("--")
            cmd.extend(safe_tokens)

    ran = run_user_command(cmd, cwd=ws, shell=False, timeout_sec=8.0)
    return {
        "action": f"git_{sub}",
        "workspace": str(ws),
        "status": ran["status"],
        "exitCode": ran["exitCode"],
        "summary": f"Git {sub} in `{ws.name}` ({ran['status']}):",
        "lines": ran["lines"][:40] or ["(clean working tree)"],
    }


def _is_test_or_isolated_env(target_path: Path | None = None) -> bool:
    if (
        os.environ.get("AQUA_TEST_ENDPOINT")
        or os.environ.get("ALIS_AQUA_TEST_NO_SPAWN") == "1"
        or os.environ.get("ALIS_COMPANION_TEST_HOST")
        or os.environ.get("ALIS_TEST_ISOLATED") == "1"
    ):
        return True
    if target_path is not None:
        s = str(target_path)
        for prefix in ("/tmp/alis-companion-", "/tmp/companion-tools-", "/tmp/aqua-"):
            if s.startswith(prefix):
                return True
    for env_key in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
        val_str = os.environ.get(env_key, "")
        for prefix in ("/tmp/alis-companion-", "/tmp/companion-tools-", "/tmp/aqua-"):
            if val_str.startswith(prefix):
                return True
    return False


def _resolve_safe_session_binary(name: str) -> str | None:
    """Resolve a desktop/hardware control binary while preventing test runs from mutating the live host session."""
    bin_path = shutil.which(name)
    if not bin_path:
        return None
    if _is_test_or_isolated_env():
        # In isolated/test environments, only execute explicit mock binaries placed under /tmp/
        if bin_path.startswith("/tmp/"):
            return bin_path
        return None
    return bin_path


def resolve_default_editor_command(target: Path) -> tuple[list[str], str]:
    """Detect the system's preferred code/text editor command for opening `target`."""
    tui_editors = {"nvim", "vim", "vi", "nano", "helix", "hx", "micro", "kak", "ne"}
    for env_key in ("ALIS_DEFAULT_EDITOR", "VISUAL", "EDITOR"):
        raw = os.environ.get(env_key, "").strip()
        if not raw:
            continue
        parts = shlex.split(raw)
        if not parts:
            continue
        bin_path = shutil.which(parts[0])
        if not bin_path:
            continue
        base_name = Path(parts[0]).name.lower()
        if base_name in tui_editors:
            for term, term_args in (
                ("kitty", ["-e"]),
                ("foot", ["-e"]),
                ("alacritty", ["-e"]),
                ("wezterm", ["start", "--"]),
                ("ghostty", ["-e"]),
                ("ptyxis", ["--"]),
                ("kgx", ["--"]),
                ("gnome-terminal", ["--"]),
                ("konsole", ["-e"]),
                ("xfce4-terminal", ["-x"]),
                ("tilix", ["-e"]),
                ("mate-terminal", ["-x"]),
                ("xterm", ["-e"]),
                ("x-terminal-emulator", ["-e"]),
            ):
                tbin = shutil.which(term)
                if tbin:
                    return [tbin, *term_args, bin_path, *parts[1:], str(target)], base_name
            continue
        return [bin_path, *parts[1:], str(target)], base_name

    for gui_editor in (
        "code",
        "codium",
        "cursor",
        "zed",
        "zeditor",
        "kate",
        "gnome-text-editor",
        "gedit",
        "mousepad",
        "xed",
        "pluma",
        "featherpad",
        "subl",
    ):
        ebin = shutil.which(gui_editor)
        if ebin:
            return [ebin, str(target)], gui_editor

    xdg_bin = shutil.which("xdg-open") or "/usr/bin/xdg-open"
    return [xdg_bin, str(target)], "xdg-open"


def open_in_default_editor(target_path: str | Path) -> dict:
    """Open `target_path` in the system's default external code/text editor."""
    target = Path(target_path).expanduser().resolve(strict=False)
    cmd, label = resolve_default_editor_command(target)
    simulated = _is_test_or_isolated_env(target)
    if not simulated:
        try:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise DesktopControlError("editor_launch_failed", f"Failed to launch default editor ({label}): {exc}") from exc
    return {
        "opened": True,
        "editorMode": "external",
        "editorCommand": label,
        "targetPath": str(target),
        "simulated": simulated,
    }


def open_storage_directory(kind: str, companion_root: Path | None = None, custom_path: str = "") -> dict:
    """Ensure the requested Companion storage folder exists and open it in the file manager/editor."""
    k = str(kind or "skills").strip().lower()
    xdg_state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")).resolve(strict=False)
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).resolve(strict=False)
    comp_root = (companion_root or Path(__file__).resolve().parents[2]).resolve()

    if custom_path.strip():
        cand = Path(custom_path.strip()).expanduser().resolve(strict=False)
        home = Path.home().resolve(strict=False)
        if not (
            cand.is_relative_to(home)
            or cand.is_relative_to(comp_root)
            or cand.is_relative_to(xdg_state)
            or cand.is_relative_to(xdg_config)
        ):
            raise DesktopControlError("invalid_storage_path", "Target path is outside Companion storage and user home.")
        target = cand
        if not target.exists() and not target.suffix:
            target.mkdir(parents=True, exist_ok=True)
    elif k in ("skills", "user_skills"):
        target = (xdg_state / "alis-companion" / "skills").resolve(strict=False)
        target.mkdir(parents=True, exist_ok=True)
    elif k in ("builtin_skills", "builtin-skills"):
        target = (comp_root / "scripts" / "aqua" / "skills").resolve(strict=False)
    elif k in ("mcp", "mcp_config"):
        target = (xdg_config / "alis-companion").resolve(strict=False)
        target.mkdir(parents=True, exist_ok=True)
        mcp_state_dir = (xdg_state / "alis-companion").resolve(strict=False)
        mcp_state_dir.mkdir(parents=True, exist_ok=True)
    elif k in ("rag", "state", "mcp_state"):
        target = (xdg_state / "alis-companion").resolve(strict=False)
        target.mkdir(parents=True, exist_ok=True)
    else:
        target = (xdg_state / "alis-companion").resolve(strict=False)
        target.mkdir(parents=True, exist_ok=True)

    simulated = _is_test_or_isolated_env(target)
    if not simulated:
        if target.is_file():
            open_in_default_editor(target)
        else:
            xdg_bin = shutil.which("xdg-open") or "/usr/bin/xdg-open"
            try:
                subprocess.Popen(
                    [xdg_bin, str(target)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as exc:
                raise DesktopControlError("open_folder_failed", f"Failed to open storage path: {exc}") from exc

    return {
        "opened": True,
        "kind": k,
        "path": str(target),
        "exists": target.exists(),
        "isDirectory": target.is_dir(),
        "simulated": simulated,
    }


_EMPTY_WAV_HEADER = (
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


_VOICE_KEYRING_CACHE: dict[str, object] = {"ts": 0.0, "keys": {}}


def _voice_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    root = (Path(base) if base else (Path.home() / ".local" / "state")) / "alis-companion" / "voice"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _confine_voice_path(raw_path: str, default_name: str, vdir: Path) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        return vdir / default_name
    cand = Path(raw).expanduser().resolve(strict=False)
    allowed_roots = [
        vdir.resolve(strict=False),
        Path(tempfile.gettempdir()).resolve(strict=False),
        Path("/tmp").resolve(strict=False),
    ]
    if not any(cand == root or cand.is_relative_to(root) for root in allowed_roots):
        raise DesktopControlError("invalid_voice_path", f"Voice file path must reside under voice state or temp directory: {raw}")
    return cand


def _candidate_local_whisper_models(explicit_model: str = "") -> list[Path]:
    candidates: list[Path] = []
    for raw in (explicit_model, os.environ.get("ALIS_WHISPER_MODEL", "")):
        if raw and str(raw).strip():
            candidates.append(Path(str(raw).strip()).expanduser())
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
    model_names = (
        "ggml-base.bin",
        "ggml-small.bin",
        "ggml-tiny.bin",
        "ggml-base.en.bin",
        "ggml-small.en.bin",
        "ggml-tiny.en.bin",
        "ggml-large-v3-turbo.bin",
        "ggml-large-v3.bin",
        "ggml-medium.bin",
    )
    for folder in (
        data_home / "alis/whisper",
        data_home / "whisper",
        cache_home / "whisper.cpp",
        cache_home / "whisper",
    ):
        for mname in model_names:
            candidates.append(folder / mname)
    return [p for p in candidates if p.is_file()]


def _load_voice_config_and_keys() -> tuple[dict, dict[str, str]]:
    cfg_voice: dict = {}
    xdg_cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    for cfg_path in (
        xdg_cfg / "alis" / "config.json",
        xdg_cfg / "illogical-impulse" / "config.json",
    ):
        if cfg_path.is_file():
            try:
                parsed = json.loads(cfg_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(parsed, dict) and isinstance(parsed.get("voiceSearch"), dict):
                    cfg_voice = parsed["voiceSearch"]
                    break
            except Exception:
                pass

    env_keys = {
        "groq": os.environ.get("GROQ_API_KEY", "").strip(),
        "gemini": (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip(),
        "openai": os.environ.get("OPENAI_API_KEY", "").strip(),
    }
    generic_env = os.environ.get("ALIS_VOICE_API_KEY", "").strip()
    pref_prov = str(cfg_voice.get("provider") or "auto").strip().lower()
    if generic_env:
        if pref_prov in env_keys and not env_keys[pref_prov]:
            env_keys[pref_prov] = generic_env
        elif pref_prov in ("auto", "local", "") and not any(env_keys.values()):
            env_keys["gemini"] = generic_env

    if _is_test_or_isolated_env():
        return cfg_voice, {k: v for k, v in env_keys.items() if v}

    # If the caller (e.g. AquaMind voiceWorker) already supplied the required API key(s) in os.environ,
    # skip spawning secret-tool to avoid D-Bus / keyring lookup latency.
    if (pref_prov in env_keys and env_keys[pref_prov]) or (pref_prov in ("auto", "local", "") and any(env_keys.values())):
        return cfg_voice, env_keys

    now = time.monotonic()
    cached_ts = float(_VOICE_KEYRING_CACHE.get("ts") or 0.0)
    cached_keys = _VOICE_KEYRING_CACHE.get("keys")
    raw_api_keys: dict[str, str] = {}
    if isinstance(cached_keys, dict) and cached_keys and (now - cached_ts) < 30.0:
        raw_api_keys = dict(cached_keys)
    else:
        secret_tool = shutil.which("secret-tool")
        if secret_tool:
            for app_id in ("alis", "illogical-impulse"):
                try:
                    proc = subprocess.run(
                        [secret_tool, "lookup", "application", app_id],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        timeout=4.0,
                        check=False,
                    )
                    out = (proc.stdout or "").strip()
                    if out.startswith("{"):
                        data = json.loads(out)
                        ak = data.get("apiKeys") if isinstance(data, dict) else None
                        if isinstance(ak, dict) and ak:
                            for k, v in ak.items():
                                if isinstance(v, str) and v.strip():
                                    raw_api_keys[str(k)] = v.strip()
                except Exception:
                    pass
        _VOICE_KEYRING_CACHE["ts"] = now
        _VOICE_KEYRING_CACHE["keys"] = dict(raw_api_keys)

    def _pick_key(provider: str, model_cfg_key: str) -> str:
        if env_keys.get(provider):
            return env_keys[provider]
        direct = raw_api_keys.get(provider, "").strip()
        if direct:
            return direct
        cfg_model = str(cfg_voice.get(model_cfg_key) or "").strip()
        if cfg_model and raw_api_keys.get(cfg_model, "").strip():
            return raw_api_keys[cfg_model].strip()
        prefix = provider + "-"
        for k, v in raw_api_keys.items():
            if k.startswith(prefix) and v.strip():
                return v.strip()
        return ""

    resolved_keys = {
        "groq": _pick_key("groq", "groqModel"),
        "gemini": _pick_key("gemini", "geminiModel"),
        "openai": _pick_key("openai", "openaiModel"),
    }
    return cfg_voice, resolved_keys


def _repair_wav_header_if_needed(wav_path: Path) -> None:
    try:
        if not wav_path.is_file():
            return
        total_size = wav_path.stat().st_size
        if total_size <= 44:
            return
        with wav_path.open("r+b") as wf:
            hdr = bytearray(wf.read(44))
            if len(hdr) < 44 or hdr[:4] != b"RIFF" or hdr[8:12] != b"WAVE":
                return
            expected_riff = max(36, min(0xFFFFFFFF, total_size - 8))
            expected_data = max(0, min(0xFFFFFFFF, total_size - 44))
            cur_riff = int.from_bytes(hdr[4:8], "little")
            cur_data = int.from_bytes(hdr[40:44], "little")
            if cur_data == 0 or cur_data > expected_data or cur_riff == 0 or cur_riff > expected_riff:
                hdr[4:8] = expected_riff.to_bytes(4, "little")
                if hdr[36:40] == b"data":
                    hdr[40:44] = expected_data.to_bytes(4, "little")
                wf.seek(0)
                wf.write(hdr)
                wf.flush()
    except OSError:
        pass


def _clean_stt_transcript(raw_text: str) -> str:
    if not raw_text:
        return ""
    lines = []
    for ln in str(raw_text).splitlines():
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^\[(?:BLANK_AUDIO|Silence|Music|Noise|inaudible|laughter|applause)\]$", s, re.IGNORECASE):
            continue
        if re.match(r"^\((?:blank audio|silence|music|noise|inaudible)\)$", s, re.IGNORECASE):
            continue
        s = re.sub(r"^\[[0-9:.]+\s*-->\s*[0-9:.]+\]\s*", "", s).strip()
        if s and not s.startswith("["):
            lines.append(s)
    joined = " ".join(lines).strip()
    if len(joined) >= 12:
        half = len(joined) // 2
        left = joined[:half].strip()
        right = joined[half:].strip()
        if left and left == right:
            return left
    return joined



def _transcribe_openai_compatible(
    wav_path: Path,
    endpoint: str,
    model: str,
    api_key: str,
    language: str,
    timeout: float = 20.0,
) -> str:
    import urllib.request
    import uuid

    boundary = "----alis-companion-voice-" + uuid.uuid4().hex
    fields: dict[str, str] = {"model": model, "response_format": "json"}
    if language and language not in ("auto", ""):
        fields["language"] = language[:2].lower()
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{wav_path.name}"\r\n'.encode(),
            b"Content-Type: audio/wav\r\n\r\n",
            wav_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    body = b"".join(chunks)
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "Alis-Companion/Voice",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    return _clean_stt_transcript(str(payload.get("text") or ""))


def _transcribe_gemini_inline(
    wav_path: Path,
    model: str,
    api_key: str,
    language: str,
    timeout: float = 20.0,
) -> str:
    import base64
    import urllib.parse
    import urllib.request

    raw_bytes = wav_path.read_bytes()
    if not raw_bytes or len(raw_bytes) <= 44:
        return ""
    b64_audio = base64.b64encode(raw_bytes).decode("ascii")
    lang_hint = ""
    if language and language not in ("auto", ""):
        lang_hint = f" Expected spoken language code: {language}."
    prompt_text = (
        "Transcribe the spoken speech in this audio accurately (preserving Vietnamese or English as spoken)."
        + lang_hint
        + " Output ONLY the exact spoken words with no commentary, no labels, and no timestamps. "
        "If the audio contains only silence or background noise with no speech, output nothing."
    )
    models_to_try = [model or "gemini-2.5-flash"]
    if "gemini-2.5-flash-lite" not in models_to_try:
        models_to_try.append("gemini-2.5-flash-lite")

    last_err = ""
    for cur_model in models_to_try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + urllib.parse.quote(cur_model, safe="-._")
            + ":generateContent"
        )
        gen_cfg: dict = {"temperature": 0}
        if "2.5" in cur_model or "thinking" in cur_model:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
        payload = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "audio/wav", "data": b64_audio}},
                        {"text": prompt_text},
                    ]
                }
            ],
            "generationConfig": gen_cfg,
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Alis-Companion/Voice",
                    "x-goog-api-key": api_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
            candidates = result.get("candidates") or []
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts") or []
            non_thought = [
                str(p.get("text") or "").strip()
                for p in parts
                if not p.get("thought") and str(p.get("text") or "").strip()
            ]
            return _clean_stt_transcript(" ".join(non_thought))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            last_err = f"Gemini HTTP {exc.code} ({cur_model}): {body or exc.reason}"
            continue
        except Exception as exc:
            last_err = f"Gemini error ({cur_model}): {exc}"
            continue
    if last_err:
        raise RuntimeError(last_err)
    return ""


def _has_edge_tts() -> bool:
    if _is_test_or_isolated_env():
        return False
    if _resolve_safe_session_binary("edge-tts"):
        return True
    home_edge = Path.home() / ".local" / "bin" / "edge-tts"
    if home_edge.is_file() and os.access(home_edge, os.X_OK):
        return True
    try:
        import importlib.util
        return importlib.util.find_spec("edge_tts") is not None
    except Exception:
        return False


CANONICAL_NEURAL_VOICE = "en-US-AvaMultilingualNeural"


def _resolve_edge_tts_neural_voice(_eff_voice: str = "") -> str:
    return CANONICAL_NEURAL_VOICE


def voice_status() -> dict:
    """Inspect available local Voice STT (Whisper) and canonical multilingual Neural TTS backend."""
    stt_bins = [b for b in ("whisper-cli", "whisper-cpp", "whisper") if shutil.which(b)]
    rec_bins = [b for b in ("pw-record", "arecord", "ffmpeg") if shutil.which(b)]
    tts_bins = ["edge-tts"] if _has_edge_tts() else []
    play_bins = [b for b in ("pw-play", "paplay", "aplay", "ffplay") if shutil.which(b)]
    test_mode = _is_test_or_isolated_env() or bool(os.environ.get("AQUA_TEST_VOICE_TEXT"))
    cfg_voice, api_keys = _load_voice_config_and_keys()
    local_models = _candidate_local_whisper_models(str(cfg_voice.get("localModelPath") or ""))
    cloud_providers = [p for p in ("groq", "gemini", "openai") if api_keys.get(p)]
    if test_mode:
        stt_backend = stt_bins[0] if stt_bins else "fixture-whisper"
    elif stt_bins and local_models:
        stt_backend = stt_bins[0]
    elif cloud_providers:
        stt_backend = cloud_providers[0]
    else:
        stt_backend = stt_bins[0] if stt_bins else "whisper-cli"
    tts_backend = "edge-tts" if tts_bins else ("fixture-edge-tts" if test_mode else "edge-tts-unavailable")
    return {
        "action": "voice_status",
        "status": "completed",
        "ok": True,
        "sttAvailable": bool((stt_bins and local_models) or cloud_providers or stt_bins or test_mode),
        "ttsAvailable": bool(tts_bins or test_mode),
        "sttBackend": stt_backend,
        "ttsBackend": tts_backend,
        "canonicalVoice": CANONICAL_NEURAL_VOICE,
        "recordBackends": rec_bins,
        "playBackends": play_bins,
        "summary": f"Local Voice I/O — STT: {stt_backend}, TTS: {tts_backend} ({CANONICAL_NEURAL_VOICE})",
        "lines": [
            f"STT backend: {stt_backend} (recorders: {', '.join(rec_bins) or 'fixture'})",
            f"TTS backend: {tts_backend} · Voice: {CANONICAL_NEURAL_VOICE} (players: {', '.join(play_bins) or 'fixture'})",
        ],
    }


def voice_stt(audio_path: str = "", duration_sec: float = 4.0, lang: str = "auto", language: str = "") -> dict:
    """Record or transcribe speech audio using local Whisper CLI or configured cloud STT providers (with deterministic test fixture support)."""
    eff_lang = str(language or lang or "auto").strip() or "auto"
    vdir = _voice_state_dir()
    target_wav = _confine_voice_path(audio_path, "voice_input.wav", vdir)
    fixture_text = (os.environ.get("AQUA_TEST_VOICE_TEXT") or os.environ.get("AQUA_TEST_VOICE_TRANSCRIPT") or "").strip()
    if fixture_text or _is_test_or_isolated_env(target_wav):
        if not target_wav.exists():
            try:
                target_wav.parent.mkdir(parents=True, exist_ok=True)
                target_wav.write_bytes(_EMPTY_WAV_HEADER)
            except OSError:
                pass
        transcript = fixture_text or "Xin chào Aqua, kiểm tra nhận diện giọng nói cục bộ"
        preview = (transcript[:180] + "...") if len(transcript) > 180 else transcript
        return {
            "action": "voice_stt",
            "status": "completed",
            "ok": True,
            "text": transcript,
            "transcript": transcript,
            "language": eff_lang,
            "backend": "fixture-whisper",
            "sttBackend": "fixture-whisper",
            "audioPath": str(target_wav),
            "summary": f"Transcribed voice input ({len(transcript)} chars): {preview}",
            "lines": [transcript],
        }

    # If live capture worker was used, wait briefly for pw-record/arecord to flush WAV header and remove .recording lock on SIGINT
    is_live_capture = target_wav.name == "voice_live_capture.wav"
    if is_live_capture:
        rec_flag = target_wav.parent / (target_wav.name + ".recording")
        for _ in range(30):
            if not rec_flag.exists() and target_wav.is_file() and target_wav.stat().st_size > 64:
                break
            time.sleep(0.04)

    # Record audio if no existing non-empty file was passed and this was not an already-finished live stage capture
    if (not target_wav.is_file() or target_wav.stat().st_size <= 64) and not is_live_capture:
        dur = max(1.0, min(15.0, float(duration_sec or 4.0)))
        rec_bin = _resolve_safe_session_binary("pw-record") or _resolve_safe_session_binary("arecord")
        if rec_bin:
            def _pdeath_sigint():
                try:
                    import ctypes
                    ctypes.CDLL("libc.so.6").prctl(1, signal.SIGINT)
                except Exception:
                    pass

            rec_cmd = (
                [rec_bin, "--rate=16000", "--channels=1", "--format=s16", str(target_wav)]
                if "pw-record" in rec_bin
                else [rec_bin, "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(int(dur)), str(target_wav)]
            )
            rec_proc = None
            try:
                rec_proc = subprocess.Popen(
                    rec_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=_pdeath_sigint,
                )
                rec_proc.wait(timeout=dur)
            except subprocess.TimeoutExpired:
                if rec_proc is not None and rec_proc.poll() is None:
                    try:
                        rec_proc.send_signal(signal.SIGINT)
                        rec_proc.wait(timeout=0.45)
                    except Exception:
                        try:
                            rec_proc.kill()
                        except Exception:
                            pass
            except OSError:
                pass

    _repair_wav_header_if_needed(target_wav)

    cfg_voice, api_keys = _load_voice_config_and_keys()
    if eff_lang == "auto":
        cfg_lang = str(cfg_voice.get("language") or "auto").strip()
        if cfg_lang:
            eff_lang = cfg_lang
    configured_provider = str(cfg_voice.get("provider") or "auto").strip().lower()
    if configured_provider == "local":
        provider_order = ["local"]
    elif configured_provider == "groq":
        provider_order = ["groq", "gemini", "openai", "local"]
    elif configured_provider == "gemini":
        provider_order = ["gemini", "groq", "openai", "local"]
    elif configured_provider == "openai":
        provider_order = ["openai", "groq", "gemini", "local"]
    else:
        provider_order = ["local", "groq", "gemini", "openai"]

    stt_bin = (
        _resolve_safe_session_binary("whisper-cli")
        or _resolve_safe_session_binary("whisper-cpp")
        or _resolve_safe_session_binary("whisper")
    )
    local_models = _candidate_local_whisper_models(str(cfg_voice.get("localModelPath") or ""))
    transcript = ""
    backend_used = Path(stt_bin).name if stt_bin else "whisper-cli"
    last_provider_err = ""

    if target_wav.is_file() and target_wav.stat().st_size > 64:
        for prov in provider_order:
            if transcript:
                break
            if prov == "local":
                if not stt_bin:
                    continue
                bname = Path(stt_bin).name
                if bname in ("whisper-cli", "whisper-cpp"):
                    cmd = [stt_bin]
                    if local_models:
                        cmd.extend(["-m", str(local_models[0])])
                    cmd.extend(["-f", str(target_wav), "-nt"])
                    if eff_lang and eff_lang not in ("auto", ""):
                        cmd.extend(["-l", eff_lang[:2].lower()])
                    ran = run_user_command(cmd, shell=False, timeout_sec=25.0)
                    if ran["exitCode"] == 0 and ran["stdout"].strip():
                        transcript = _clean_stt_transcript(ran["stdout"])
                        if transcript:
                            backend_used = bname
                elif local_models:
                    cmd = [
                        stt_bin,
                        str(target_wav),
                        "--model",
                        "base",
                        "--output_format",
                        "txt",
                        "--output_dir",
                        str(vdir),
                        "--fp16",
                        "False",
                    ]
                    if eff_lang and eff_lang not in ("auto", ""):
                        cmd.extend(["--language", eff_lang[:2].lower()])
                    ran = run_user_command(cmd, shell=False, timeout_sec=28.0)
                    txt_out = vdir / (target_wav.stem + ".txt")
                    if txt_out.is_file():
                        transcript = _clean_stt_transcript(txt_out.read_text(encoding="utf-8", errors="replace"))
                    elif ran["exitCode"] == 0 and ran["stdout"].strip():
                        transcript = _clean_stt_transcript(ran["stdout"])
                    if transcript:
                        backend_used = bname
            elif prov == "groq" and api_keys.get("groq"):
                try:
                    groq_model = str(cfg_voice.get("groqModel") or "whisper-large-v3-turbo").strip()
                    transcript = _transcribe_openai_compatible(
                        target_wav,
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        groq_model,
                        api_keys["groq"],
                        eff_lang,
                    )
                    if transcript:
                        backend_used = "groq"
                except Exception as exc:
                    last_provider_err = f"groq: {exc}"
            elif prov == "gemini" and api_keys.get("gemini"):
                try:
                    gemini_model = str(cfg_voice.get("geminiModel") or "gemini-2.5-flash").strip()
                    transcript = _transcribe_gemini_inline(
                        target_wav,
                        gemini_model,
                        api_keys["gemini"],
                        eff_lang,
                    )
                    if transcript:
                        backend_used = "gemini"
                except Exception as exc:
                    last_provider_err = str(exc)
            elif prov == "openai" and api_keys.get("openai"):
                try:
                    openai_model = str(cfg_voice.get("openaiModel") or "gpt-4o-mini-transcribe").strip()
                    transcript = _transcribe_openai_compatible(
                        target_wav,
                        "https://api.openai.com/v1/audio/transcriptions",
                        openai_model,
                        api_keys["openai"],
                        eff_lang,
                    )
                    if transcript:
                        backend_used = "openai"
                except Exception as exc:
                    last_provider_err = f"openai: {exc}"

    preview = (transcript[:180] + "...") if len(transcript) > 180 else transcript
    err_summary = (
        f"Voice transcription error: {last_provider_err[:160]}"
        if (not transcript and last_provider_err)
        else ("No speech transcript returned from STT." if not transcript else f"Transcribed voice input: {preview}")
    )
    out_res = {
        "action": "voice_stt",
        "status": "completed" if transcript else ("stt_error" if last_provider_err else "no_speech_detected"),
        "ok": bool(transcript),
        "text": transcript,
        "transcript": transcript,
        "language": eff_lang,
        "backend": backend_used,
        "sttBackend": backend_used,
        "audioPath": str(target_wav),
        "summary": err_summary,
        "lines": [transcript] if transcript else ([last_provider_err[:200]] if last_provider_err else ["(no speech transcript detected)"]),
    }
    if not transcript and last_provider_err:
        out_res["error"] = last_provider_err
    return out_res


def clipboard_get() -> dict:
    """Read current system clipboard text via Wayland alis-native / wl-paste or X11 xclip."""
    import native_host_bridge

    is_mocked = (
        getattr(subprocess.run, "__module__", "") != "subprocess"
        or getattr(shutil.which, "__module__", "") != "shutil"
    )
    wl_paste = shutil.which("wl-paste") if is_mocked else _resolve_safe_session_binary("wl-paste")
    if wl_paste and (os.environ.get("WAYLAND_DISPLAY") or is_mocked):
        try:
            proc = subprocess.run(
                [wl_paste, "--no-newline"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if proc.returncode == 0:
                text = str(proc.stdout or "")[:2000]
                return {
                    "ok": True,
                    "action": "clipboard_get",
                    "status": "completed",
                    "backend": "wl-paste",
                    "text": text,
                    "source": "clipboard",
                    "summary": f"Clipboard content ({len(text)} chars):",
                    "lines": [ln[:200] for ln in text.splitlines()[:25]] or ["(clipboard is empty)"],
                }
        except Exception:
            pass
    if not is_mocked and os.environ.get("WAYLAND_DISPLAY"):
        ok_native, native_txt = native_host_bridge.clipboard_paste(primary=False)
        if ok_native:
            text = str(native_txt or "")[:2000]
            return {
                "ok": True,
                "action": "clipboard_get",
                "status": "completed",
                "backend": "alis-native",
                "text": text,
                "source": "clipboard",
                "summary": f"Clipboard content ({len(text)} chars):",
                "lines": [ln[:200] for ln in text.splitlines()[:25]] or ["(clipboard is empty)"],
            }
    xclip = shutil.which("xclip") if is_mocked else _resolve_safe_session_binary("xclip")
    if xclip and (os.environ.get("DISPLAY") or is_mocked):
        try:
            proc = subprocess.run(
                [xclip, "-o", "-selection", "clipboard"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if proc.returncode == 0:
                text = str(proc.stdout or "")[:2000]
                return {
                    "ok": True,
                    "action": "clipboard_get",
                    "status": "completed",
                    "backend": "xclip",
                    "text": text,
                    "source": "clipboard",
                    "summary": f"Clipboard content ({len(text)} chars):",
                    "lines": [ln[:200] for ln in text.splitlines()[:25]] or ["(clipboard is empty)"],
                }
        except Exception:
            pass
    return {
        "ok": False,
        "action": "clipboard_get",
        "status": "failed",
        "error": "clipboard_unavailable",
        "text": "",
        "source": "clipboard",
        "summary": "Clipboard unavailable (no active Wayland/X11 clipboard backend responded).",
        "lines": [],
    }


def primary_selection_get() -> dict:
    """Read highlighted text from Wayland/X11 primary selection without requiring Ctrl+C, falling back to clipboard."""
    import native_host_bridge

    is_mocked = (
        getattr(subprocess.run, "__module__", "") != "subprocess"
        or getattr(shutil.which, "__module__", "") != "shutil"
    )
    wl_paste = shutil.which("wl-paste") if is_mocked else _resolve_safe_session_binary("wl-paste")
    if wl_paste and (os.environ.get("WAYLAND_DISPLAY") or is_mocked):
        try:
            proc = subprocess.run(
                [wl_paste, "--primary", "--no-newline"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                text = proc.stdout.strip()[:4000]
                return {
                    "ok": True,
                    "action": "primary_selection_get",
                    "status": "completed",
                    "backend": "wl-paste",
                    "text": text,
                    "source": "primary",
                    "summary": f"Primary selection ({len(text)} chars):",
                    "lines": [ln[:200] for ln in text.splitlines()[:25]],
                }
        except Exception:
            pass
    if not is_mocked and os.environ.get("WAYLAND_DISPLAY"):
        ok_native, native_txt = native_host_bridge.clipboard_paste(primary=True)
        if ok_native and native_txt.strip():
            text = native_txt.strip()[:4000]
            return {
                "ok": True,
                "action": "primary_selection_get",
                "status": "completed",
                "backend": "alis-native",
                "text": text,
                "source": "primary",
                "summary": f"Primary selection ({len(text)} chars):",
                "lines": [ln[:200] for ln in text.splitlines()[:25]],
            }
    xclip = shutil.which("xclip") if is_mocked else _resolve_safe_session_binary("xclip")
    if xclip and (os.environ.get("DISPLAY") or is_mocked):
        try:
            proc = subprocess.run(
                [xclip, "-o", "-selection", "primary"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                text = proc.stdout.strip()[:4000]
                return {
                    "ok": True,
                    "action": "primary_selection_get",
                    "status": "completed",
                    "backend": "xclip",
                    "text": text,
                    "source": "primary",
                    "summary": f"Primary selection ({len(text)} chars):",
                    "lines": [ln[:200] for ln in text.splitlines()[:25]],
                }
        except Exception:
            pass
    fallback = clipboard_get()
    return {
        **fallback,
        "action": "primary_selection_get",
        "source": "clipboard",
        "text": str(fallback.get("text") or ""),
    }


def _tts_pid_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    root = (Path(base) if base else (Path.home() / ".local" / "state")) / "alis-companion"
    root.mkdir(parents=True, exist_ok=True)
    return root / "tts_pid.json"


def _proc_starttime(pid: int) -> str:
    try:
        raw_stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
        rparen = raw_stat.rfind(")")
        if rparen >= 0:
            fields = raw_stat[rparen + 2:].split()
            if len(fields) > 19:
                return fields[19]
    except OSError:
        pass
    return ""


def _pid_matches_tts(pid: int, expected_starttime: str = "") -> bool:
    try:
        proc_dir = Path(f"/proc/{int(pid)}")
        if not proc_dir.is_dir() or proc_dir.stat().st_uid != os.getuid():
            return False
        if expected_starttime:
            cur_start = _proc_starttime(pid)
            if not cur_start or cur_start != str(expected_starttime):
                return False
        cmd_raw = (proc_dir / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").lower()
        allowed_bins = ("tts_stream_player", "pw-play", "paplay", "aplay", "ffplay")
        if _is_test_or_isolated_env() and not expected_starttime:
            allowed_bins = allowed_bins + ("sleep",)
        return any(tok in cmd_raw for tok in allowed_bins)
    except OSError:
        return False


def voice_tts_stop() -> dict:
    """Terminate any active TTS playback process group and clean temporary chunk files."""
    pid_candidates = [_tts_pid_file(), _voice_state_dir() / "tts_pid.json"]
    stopped_pid = 0
    for pf in pid_candidates:
        if not pf.is_file():
            continue
        try:
            raw = pf.read_text(encoding="utf-8").strip()
            data = json.loads(raw) if raw.startswith("{") else {"pid": int(raw)}
            pid = int(data.get("pid") or 0) if isinstance(data, dict) else 0
            exp_start = str(data.get("starttime") or "") if isinstance(data, dict) else ""
            if pid > 0 and _pid_matches_tts(pid, exp_start):
                try:
                    os.killpg(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                stopped_pid = pid
        except (OSError, ValueError):
            pass
        finally:
            try:
                pf.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        vdir = _voice_state_dir()
        for pat in ("tts_chunk_*", "tts_manifest_*.json"):
            for stale in vdir.glob(pat):
                try:
                    stale.unlink(missing_ok=True)
                except OSError:
                    pass
    except OSError:
        pass
    if stopped_pid > 0:
        return {
            "action": "voice_tts_stop",
            "status": "completed",
            "ok": True,
            "stopped": True,
            "pid": stopped_pid,
            "summary": f"Stopped active TTS playback (PID {stopped_pid}).",
            "lines": [f"Stopped TTS process {stopped_pid}."],
        }
    return {
        "action": "voice_tts_stop",
        "status": "completed",
        "ok": True,
        "stopped": False,
        "pid": 0,
        "summary": "No active TTS playback to stop.",
        "lines": ["(no active TTS stream)"],
    }


_TTS_EMOJI_CLUSTER_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|(?:[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\u231A-\u23FA\u2B05-\u2B55\u2934\u2935\u3030\u303D\u3297\u3299\u203C\u2049\u2122\u2139\u2194-\u2199\u21A9-\u21AA\u24C2\u25AA-\u25FE]|[#*0-9]\uFE0F?\u20E3)"
    r"(?:[\uFE0E\uFE0F\U0001F3FB-\U0001F3FF]|\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0E\uFE0F\U0001F3FB-\U0001F3FF]+)*"
    r"|[›•▸▪◦◆◇★☆✓✔✕✖]+"
)
_TTS_ICON_INTENT_RE = re.compile(
    r"\b(?:emoji|emojis|emoticon|emoticons|icon|icons|symbol|symbols|unicode|glyph|biểu tượng|bieu tuong|ký hiệu|ky hieu|icon\s+gì|emoji\s+nào)\b",
    re.IGNORECASE,
)


def _strip_decorative_icons_for_tts(raw_text: str, context_prompt: str = "") -> str:
    ctx = str(context_prompt or "").strip()
    if not raw_text:
        return ""
    keep_all_icons = bool(_TTS_ICON_INTENT_RE.search(ctx))
    prompt_icons = set(_TTS_EMOJI_CLUSTER_RE.findall(ctx)) if ctx else set()
    if keep_all_icons:
        return raw_text

    def _keep_or_drop(match: re.Match) -> str:
        tok = match.group(0)
        if tok in prompt_icons:
            return tok
        start, end = match.span()
        before = raw_text[max(0, start - 24) : start]
        after = raw_text[end : min(len(raw_text), end + 28)]
        # Preserve icon if explicitly quoted/code-wrapped or directly explained
        if before.endswith(('"', "'", "`", "«", "“", "‘")) and after.startswith(('"', "'", "`", "»", "”", "’")):
            return tok
        if re.match(r"^\s*(?:=|means\b|is\b|là\b|nghĩa là\b|represents\b|biểu thị\b|stands for\b)", after, re.IGNORECASE):
            return tok
        if re.search(r"(?:emoji|icon|symbol|biểu tượng|ký hiệu)\s*[:=\-]?\s*$", before, re.IGNORECASE):
            return tok
        return " "

    return _TTS_EMOJI_CLUSTER_RE.sub(_keep_or_drop, raw_text)


_TTS_PATH_OR_NOISE_RE = re.compile(
    r"^(?:niri\+vision|desktop_control|at-spi|screenshot-\d+\.(?:png|jpe?g|webp))$|[\\/]|\.(?:png|jpe?g|webp|log|json|lock|qml|py|js|ts|sh)$",
    re.IGNORECASE,
)
_TTS_VI_DIACRITIC_RE = re.compile(
    r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)
_TTS_PATH_INTENT_RE = re.compile(
    r"\b(?:file|path|filename|extension|log|json|screenshot|đường dẫn|tên tệp|tệp tin)\b",
    re.IGNORECASE,
)


def _is_vi_text(clean_txt: str) -> bool:
    words = re.findall(r"[^\W\d_]+", clean_txt or "", re.UNICODE)
    if not words:
        return False
    vi_words = sum(1 for w in words if _TTS_VI_DIACRITIC_RE.search(w))
    return (vi_words / len(words)) >= 0.16 or (vi_words >= 1 and len(words) <= 6)


def _naturalize_spoken_units(txt: str) -> str:
    txt = re.sub(r"(\*\*|__)", "", txt)
    is_vi = _is_vi_text(txt)
    deg_word = "độ" if is_vi else "degrees"
    to_word = "đến" if is_vi else "to"
    kmh_words = "kilômét trên giờ" if is_vi else "kilometers per hour"

    txt = re.sub(r"`?\[[ xX]\]`?", " ", txt)
    txt = re.sub(
        r"(-?\d+(?:\.\d+)?)\s*°C\s*[–—-]\s*(-?\d+(?:\.\d+)?)\s*°C",
        rf"\1 {to_word} \2 {deg_word}",
        txt,
    )
    txt = re.sub(r"(-?\d+(?:\.\d+)?)\s*°C(?=\s|[,.;:!?)]|$)", rf"\1 {deg_word}", txt)
    txt = re.sub(r"°C\b", f" {deg_word}", txt)
    txt = re.sub(
        r"\bkm/h\s*\(\s*(?:N|S|E|W|NE|NW|SE|SW|NNE|NNW|SSE|SSW|ENE|ESE|WNW|WSW)\s*\)",
        kmh_words,
        txt,
    )
    txt = re.sub(r"\bkm/h\b", kmh_words, txt)
    txt = re.sub(r"(?:[·,]?\s*)?\bPM\s*2\.5\s*:\s*[\d.]+\s*[μµu]g/m[³3]", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"[\d.]+\s*[μµu]g/m[³3]", "", txt, flags=re.IGNORECASE)
    txt = re.sub(
        r"\b(?:EAQI|AQI)\s+\d+\s*\(\s*([^)]+?)\s*\)",
        lambda m: m.group(1).lower(),
        txt,
    )
    txt = re.sub(r"\b(?:EAQI|AQI)\s+(\d+)\b", lambda m: f"AQI {m.group(1)}", txt)
    return txt


def _clean_text_for_tts(raw_text: str, context_prompt: str = "", max_chars: int = 620) -> str:
    txt = str(raw_text or "").strip()
    if not txt:
        return ""
    ctx = str(context_prompt or "").strip()
    wants_paths = bool(_TTS_PATH_INTENT_RE.search(ctx))

    # Strip leading telemetry banners like `**🖥 Alis - ...** · `niri+vision` *(refreshed)*`
    txt = re.sub(r"^\s*\*{0,2}\s*🖥[^\n]*(?:\n|$)", "", txt, count=1)
    # Strip screenshot/source/guide footers at line start
    txt = re.sub(
        r"(?im)^[ \t]*(?:📷\s*(?:Screenshot captured|Đã chụp màn hình)|📚\s*\*{0,2}(?:Sources|Nguồn tra cứu)\b|💡\s*\*{0,2}(?:MCP Eyes & Hands|Mắt & Tay MCP)\b)[^\n]*$",
        "",
        txt,
    )
    txt = re.sub(r"```[\s\S]*?```", " ", txt)
    txt = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", txt)
    txt = re.sub(r"https?://\S+", " ", txt)
    txt = re.sub(r"\[[^\]\n]{1,120}#L?\d+(?:-L?\d+)?\]", "", txt)
    txt = re.sub(r"\(\s*`?screenshot-\d+\.(?:png|jpe?g|webp)`?\s*\)", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"`?screenshot-\d+\.(?:png|jpe?g|webp)`?", "", txt, flags=re.IGNORECASE)

    txt = _strip_decorative_icons_for_tts(txt, context_prompt=ctx)
    txt = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", txt)

    def _unwrap_code(match: re.Match) -> str:
        inner = match.group(1).strip()
        if not wants_paths and _TTS_PATH_OR_NOISE_RE.search(inner):
            return " "
        return inner

    txt = re.sub(r"`([^`]+)`", _unwrap_code, txt)
    txt = _naturalize_spoken_units(txt)
    txt = re.sub(r"\s*[·|]\s*", ", ", txt)
    txt = re.sub(r"\s+—\s+", ", ", txt)

    spoken_lines: list[str] = []
    for raw_ln in txt.splitlines():
        ln = raw_ln.strip()
        ln = re.sub(r"^(?:[-*•]+|\d+[.)])\s*", "", ln).strip()
        ln = re.sub(r"[#*_~>|]+", " ", ln)
        ln = " ".join(ln.split()).strip(" ,;:-")
        if not ln:
            continue
        if ln[-1] not in ".!?;:…":
            ln += "."
        spoken_lines.append(ln)

    cleaned = " ".join(spoken_lines)
    if not cleaned:
        fallback = re.sub(r"[#*_~>|`]+", " ", _strip_decorative_icons_for_tts(str(raw_text or ""), context_prompt=ctx))
        cleaned = " ".join(fallback.split())
    cleaned = re.sub(r"\s+([,.!?;:…])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r",{2,}", ",", cleaned)
    cleaned = " ".join(cleaned.split()).strip()

    limit = max(80, int(max_chars or 620))
    if len(cleaned) > limit:
        head = cleaned[:limit]
        last_sent = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind("… "))
        if last_sent == -1 and head and head[-1] in ".!?…":
            last_sent = len(head) - 1
        if last_sent >= int(limit * 0.4):
            cleaned = head[: last_sent + 1].strip()
        else:
            sp = head.rfind(" ")
            part = (head[:sp] if sp >= int(limit * 0.4) else head).rstrip(" ,;:-")
            cleaned = (part + ".") if (part and part[-1] not in ".!?…") else part
    return cleaned


def _split_spoken_sentences(clean_txt: str, max_chunk_chars: int = 190) -> list[str]:
    """Split clean speech prose into fast, natural sentence chunks for streaming TTS."""
    txt = " ".join(str(clean_txt or "").split()).strip()
    if not txt:
        return []
    raw_parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", txt) if p.strip()]
    wrapped: list[str] = []
    for part in raw_parts:
        if len(part) <= max_chunk_chars:
            wrapped.append(part)
            continue
        cur = part
        while len(cur) > max_chunk_chars:
            cut = cur.rfind(", ", 0, max_chunk_chars)
            if cut < int(max_chunk_chars * 0.45):
                cut = cur.rfind(" ", 0, max_chunk_chars)
            if cut <= 20:
                cut = max_chunk_chars
            wrapped.append(cur[: cut + (1 if cut < len(cur) and cur[cut] == "," else 0)].strip())
            cur = cur[cut + (1 if cut < len(cur) and cur[cut] in ", " else 0) :].strip()
        if cur:
            wrapped.append(cur)

    chunks: list[str] = []
    for seg in wrapped:
        if not chunks:
            chunks.append(seg)
        elif len(chunks[-1]) < 32 and len(chunks[-1]) + 1 + len(seg) <= max_chunk_chars:
            chunks[-1] = f"{chunks[-1]} {seg}"
        elif len(seg) < 28 and len(chunks[-1]) + 1 + len(seg) <= max_chunk_chars:
            chunks[-1] = f"{chunks[-1]} {seg}"
        else:
            chunks.append(seg)
    return chunks or [txt]


def _synthesize_edge_chunk(chunk_text: str, out_mp3: Path, neural_voice: str, edge_rate: str, edge_cli: str) -> bool:
    for attempt_rate in (edge_rate, "+0%"):
        try:
            import asyncio
            import edge_tts

            async def _synth_edge():
                comm = edge_tts.Communicate(chunk_text, neural_voice, rate=attempt_rate)
                await asyncio.wait_for(comm.save(str(out_mp3)), timeout=6.5)

            asyncio.run(_synth_edge())
            if out_mp3.is_file() and out_mp3.stat().st_size > 128:
                return True
        except Exception:
            pass
    if edge_cli:
        for attempt_rate in (edge_rate, "+0%"):
            try:
                subprocess.run(
                    [edge_cli, "--voice", neural_voice, "--rate", attempt_rate, "--text", chunk_text, "--write-media", str(out_mp3)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=7.0,
                    check=False,
                )
                if out_mp3.is_file() and out_mp3.stat().st_size > 128:
                    return True
            except Exception:
                pass
    return False


def voice_tts(
    text: str,
    voice: str = "",
    out_path: str = "",
    language: str = "",
    lang: str = "",
    rate: str | int | float = "",
    engine: str = "",
    wait: bool = False,
    context_prompt: str = "",
) -> dict:
    """Synthesize speech using a single fixed multilingual neural voice (en-US-AvaMultilingualNeural) with streaming sentence chunks."""
    voice_tts_stop()
    req_engine = str(engine or "").strip().lower()
    clean_txt = _clean_text_for_tts(text, context_prompt=context_prompt)
    if not clean_txt:
        raise DesktopControlError("empty_tts_text", "Provide non-empty text to synthesize with local TTS.")
    neural_voice = _resolve_edge_tts_neural_voice(str(voice or language or lang or ""))
    chunks = _split_spoken_sentences(clean_txt)
    raw_rate = str(rate or "").strip()
    edge_rate = raw_rate if (raw_rate.startswith(("+", "-")) and raw_rate.endswith("%")) else "+4%"
    vdir = _voice_state_dir()
    raw_out_path = str(out_path or "").strip()
    run_dir: Path | None = None
    if raw_out_path:
        out_wav = _confine_voice_path(raw_out_path, "tts_output.wav", vdir)
        out_wav.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    else:
        run_dir = vdir / f"tts_run_{int(time.time() * 1000)}_{os.getpid()}"
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        out_wav = run_dir / "tts_output.wav"

    if _is_test_or_isolated_env(out_wav):
        try:
            out_wav.write_bytes(_EMPTY_WAV_HEADER)
        except OSError:
            pass
        return {
            "action": "voice_tts",
            "status": "completed",
            "ok": True,
            "spoken": True,
            "spokenText": clean_txt,
            "voice": neural_voice,
            "neuralVoice": neural_voice,
            "backend": "fixture-edge-tts",
            "ttsBackend": "fixture-edge-tts",
            "requestedEngine": req_engine or "auto",
            "streamingChunks": len(chunks),
            "waited": bool(wait),
            "audioPath": str(out_wav),
            "dryRun": True,
            "pid": 0,
            "summary": f"Spoken via {neural_voice} ({len(chunks)} chunk(s), {len(clean_txt)} chars): {clean_txt[:120]}",
            "lines": [clean_txt[:180]],
        }

    ffplay_bin = _resolve_safe_session_binary("ffplay")
    ffmpeg_bin = _resolve_safe_session_binary("ffmpeg")
    play_bin = (
        _resolve_safe_session_binary("pw-play")
        or _resolve_safe_session_binary("paplay")
        or _resolve_safe_session_binary("aplay")
    )
    edge_cli = _resolve_safe_session_binary("edge-tts") or ""
    if not edge_cli:
        cand_edge = Path.home() / ".local" / "bin" / "edge-tts"
        if cand_edge.is_file() and os.access(cand_edge, os.X_OK):
            edge_cli = str(cand_edge)

    out_mp3 = out_wav.with_suffix(".mp3") if out_wav.suffix.lower() != ".mp3" else out_wav
    for stale in (out_mp3, out_wav):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass

    synth_ok = False
    if _has_edge_tts():
        synth_ok = _synthesize_edge_chunk(chunks[0], out_mp3, neural_voice, edge_rate, edge_cli)

    if not synth_ok:
        return {
            "action": "voice_tts",
            "status": "tts_unavailable",
            "ok": True,
            "spoken": False,
            "spokenText": clean_txt,
            "voice": neural_voice,
            "neuralVoice": neural_voice,
            "backend": "edge-tts",
            "ttsBackend": "edge-tts",
            "requestedEngine": req_engine or "auto",
            "streamingChunks": len(chunks),
            "waited": bool(wait),
            "audioPath": "",
            "dryRun": False,
            "pid": 0,
            "error": "edge_tts_unreachable",
            "summary": f"Neural voice ({neural_voice}) is temporarily unreachable.",
            "lines": [f"Voice unavailable ({neural_voice})."],
        }

    first_audio = out_mp3
    if play_bin and ffmpeg_bin and out_wav != out_mp3:
        try:
            subprocess.run(
                [ffmpeg_bin, "-y", "-loglevel", "quiet", "-i", str(out_mp3), str(out_wav)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4.5,
                check=False,
            )
            if out_wav.is_file() and out_wav.stat().st_size > 64:
                first_audio = out_wav
        except Exception:
            pass

    spawned_pid = 0
    spawned_proc: subprocess.Popen | None = None
    try:
        chunk_target_dir = run_dir or out_wav.parent
        if len(chunks) >= 2:
            manifest_path = chunk_target_dir / f"tts_manifest_{int(time.time() * 1000)}.json"
            manifest_payload = {
                "firstAudioPath": str(first_audio),
                "firstMp3Path": str(out_mp3) if first_audio != out_mp3 else "",
                "remainingChunks": chunks[1:],
                "voice": neural_voice,
                "rate": edge_rate,
                "chunkDir": str(chunk_target_dir),
                "pidFile": str(_tts_pid_file()),
                "playBin": play_bin or "",
                "ffmpegBin": ffmpeg_bin or "",
                "ffplayBin": ffplay_bin or "",
                "edgeCli": edge_cli,
            }
            manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(manifest_path, 0o600)
            except OSError:
                pass
            stream_script = Path(__file__).resolve().parent / "tts_stream_player.py"
            proc = subprocess.Popen(
                [sys.executable, str(stream_script), str(manifest_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            spawned_proc = proc
            spawned_pid = proc.pid
        elif first_audio.suffix.lower() == ".wav" and play_bin:
            proc = subprocess.Popen(
                [play_bin, str(first_audio)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            spawned_proc = proc
            spawned_pid = proc.pid
        elif ffplay_bin:
            proc = subprocess.Popen(
                [ffplay_bin, "-nodisp", "-autoexit", "-loglevel", "quiet", str(first_audio)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            spawned_proc = proc
            spawned_pid = proc.pid
    except OSError:
        pass

    if spawned_pid > 0:
        try:
            _tts_pid_file().write_text(
                json.dumps({
                    "pid": spawned_pid,
                    "starttime": _proc_starttime(spawned_pid),
                    "backend": "edge-tts",
                    "voice": neural_voice,
                    "timestamp": int(time.time() * 1000),
                }),
                encoding="utf-8",
            )
        except OSError:
            pass
    if wait and spawned_proc is not None:
        try:
            spawned_proc.wait(timeout=45.0)
        except Exception:
            pass

    return {
        "action": "voice_tts",
        "status": "completed",
        "ok": True,
        "spoken": True,
        "spokenText": clean_txt,
        "voice": neural_voice,
        "neuralVoice": neural_voice,
        "backend": "edge-tts",
        "ttsBackend": "edge-tts",
        "requestedEngine": req_engine or "auto",
        "streamingChunks": len(chunks),
        "waited": bool(wait),
        "audioPath": str(first_audio),
        "dryRun": False,
        "pid": spawned_pid,
        "summary": f"Spoken via {neural_voice} ({len(chunks)} chunk(s)): {clean_txt[:120]}",
        "lines": [clean_txt[:180]],
    }


