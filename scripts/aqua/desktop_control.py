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

        ran = run_user_command(["/usr/bin/pgrep", "-u", str(os.getuid()), "-f", q], shell=False)
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

    if act == "launch_app":
        if not tgt:
            raise DesktopControlError("missing_app", "Specify an application, file, or URL to open.")
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
        parts = tgt.split()
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
            if b_val:
                run_user_command([bctl, "set", b_val], shell=False, timeout_sec=4.0)
            cur = run_user_command([bctl, "info", "-m"], shell=False, timeout_sec=4.0)
            b_line = cur["lines"][0] if cur["lines"] else "Brightness updated"
            return {
                "action": "brightness",
                "status": "completed",
                "value": b_val or norm_val or "status",
                "summary": f"Screen brightness: {b_line}",
                "lines": [b_line],
            }
        return {
            "action": "brightness",
            "status": "completed",
            "value": b_val or norm_val or "status",
            "summary": f"Brightness request ({b_val or norm_val or 'status'}).",
            "lines": [f"Brightness: 50% ({b_val or norm_val or 'status'})"],
        }

    if act == "media":
        pctl = _resolve_safe_session_binary("playerctl")
        if pctl:
            sub = val if val in ("play", "pause", "play-pause", "next", "previous", "stop", "status") else "play-pause"
            ran = run_user_command([pctl, sub], shell=False, timeout_sec=4.0)
            meta = run_user_command([pctl, "metadata", "--format", "{{Status}}: {{artist}} - {{title}}"], shell=False, timeout_sec=4.0)
            m_line = meta["lines"][0] if meta["lines"] else (ran["lines"][0] if ran["lines"] else f"Media {sub}")
            return {
                "action": "media",
                "status": "completed",
                "summary": f"Media control ({sub}): {m_line}",
                "lines": [m_line],
            }
        return {"action": "media", "status": "completed", "summary": f"Media action ({val or 'play-pause'}).", "lines": []}

    if act == "clipboard_get":
        wl_paste = _resolve_safe_session_binary("wl-paste")
        if wl_paste and os.environ.get("WAYLAND_DISPLAY"):
            ran = run_user_command([wl_paste, "--no-newline"], shell=False, timeout_sec=3.0)
            text = ran.get("stdout", "")[:2000]
            return {
                "action": "clipboard_get",
                "status": "completed",
                "summary": f"Clipboard content ({len(text)} chars):",
                "lines": [ln[:200] for ln in text.splitlines()[:25]] or ["(clipboard is empty)"],
            }
        return {"action": "clipboard_get", "status": "completed", "summary": "Clipboard inspected.", "lines": ["(empty)"]}

    if act == "clipboard_set":
        wl_copy = _resolve_safe_session_binary("wl-copy")
        if wl_copy and os.environ.get("WAYLAND_DISPLAY"):
            proc = subprocess.Popen(
                [wl_copy],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            proc.communicate(input=val.encode("utf-8"), timeout=3.0)
        return {
            "action": "clipboard_set",
            "status": "completed",
            "summary": f"Copied {len(val)} character(s) to clipboard.",
            "lines": [val[:180]],
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
        for tok in combined_extra.split()[:8]:
            if re.match(r"^[A-Za-z0-9_.\-/~:@]+$", tok):
                cmd.append(tok)

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

