#!/usr/bin/env python3
"""Ultra-lightweight stdlib Unix-socket client shim for warm Alis-Companion daemon calls.

Avoids importing local_mind / agent_runtime / SQLite when the persistent daemon
is already warm and matches the current source mtime. Falls back transparently
to `local_mind.py --use-daemon` on cold start or version mismatch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 98304

_CORE_MTIME_FILES = (
    "companion_daemon.py",
    "local_mind.py",
    "agent_runtime.py",
    "a2ui_actions.py",
    "a2ui_builder.py",
    "a2ui_validator.py",
    "desktop_control.py",
    "screen_context.py",
    "scheduler_store.py",
    "cdp_client.py",
    "mcp_client.py",
    "native_host_bridge.py",
)


def _resolve_socket_path() -> Path:
    explicit = str(os.environ.get("ALIS_COMPANION_DAEMON_SOCK") or "").strip()
    if explicit:
        return Path(explicit)
    runtime_dir = str(os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    if runtime_dir:
        base = Path(runtime_dir)
    else:
        base = Path.home() / ".local" / "state" / "quickshell" / "user"
    return base / f"alis-companion-ai-{os.getuid()}.sock"


def _compute_core_source_mtime() -> int:
    newest = 0
    for fname in _CORE_MTIME_FILES:
        try:
            st = (SCRIPT_DIR / fname).stat()
            if st.st_mtime_ns > newest:
                newest = st.st_mtime_ns
        except OSError:
            continue
    return newest


def _fallback_to_local_mind(raw_stdin: bytes, argv: list[str]) -> int:
    cmd = [sys.executable, str(SCRIPT_DIR / "local_mind.py")]
    if "--use-daemon" not in argv:
        cmd.append("--use-daemon")
    cmd.extend(argv)
    proc = subprocess.run(cmd, input=raw_stdin, check=False)
    return int(proc.returncode)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        sys.stdout.write(json.dumps({"ok": False, "error": {"code": "request_too_large", "message": "Companion request exceeded the limit"}}) + "\n")
        return 0
    if not raw.strip() or os.environ.get("ALIS_COMPANION_NO_DAEMON") == "1":
        return _fallback_to_local_mind(raw, args)
    try:
        parsed = json.loads(raw)
    except Exception:
        return _fallback_to_local_mind(raw, args)
    if not isinstance(parsed, dict):
        return _fallback_to_local_mind(raw, args)

    sock_path = _resolve_socket_path()
    if not sock_path.exists():
        return _fallback_to_local_mind(raw, args)

    req_id = str(parsed.get("requestId") or parsed.get("_aqua_request_id") or "").strip()
    if not req_id:
        req_id = f"cli_{int(time.time() * 1000)}_{os.getpid()}"
    parsed["requestId"] = req_id
    parsed["_aqua_request_id"] = req_id
    if "--host-root" in args:
        hr_idx = args.index("--host-root")
        if hr_idx + 1 < len(args) and args[hr_idx + 1]:
            parsed.setdefault("_hostRoot", args[hr_idx + 1])
    parsed.setdefault("_expectedSourceMtime", _compute_core_source_mtime())

    wire = (json.dumps(parsed, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(wire) > MAX_REQUEST_BYTES:
        sys.stdout.write(json.dumps({"ok": False, "error": {"code": "request_too_large", "message": "Companion request exceeded the limit"}}) + "\n")
        return 0

    def _cancel_on_signal(signum: int, _frame: object) -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cs:
                cs.settimeout(1.5)
                cs.connect(str(sock_path))
                cancel_wire = json.dumps({"daemonOp": "cancel_request", "requestId": req_id, "reason": "cancelled_by_signal"}) + "\n"
                cs.sendall(cancel_wire.encode("utf-8"))
        except Exception:
            pass
        raise SystemExit(128 + int(signum))

    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGTERM, _cancel_on_signal)
        signal.signal(signal.SIGINT, _cancel_on_signal)
    except Exception:
        prev_term = None
        prev_int = None

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(240.0)
            s.connect(str(sock_path))
            s.sendall(wire)
            reader = s.makefile("r", encoding="utf-8", errors="replace")
            for line in reader:
                stripped = line.strip()
                if not stripped:
                    continue
                msg = json.loads(stripped)
                if not isinstance(msg, dict):
                    continue
                mtype = msg.get("type")
                if mtype == "stderr":
                    sys.stderr.write(str(msg.get("line") or ""))
                    sys.stderr.flush()
                elif mtype == "stdout":
                    sys.stdout.write(str(msg.get("line") or ""))
                    sys.stdout.flush()
                elif mtype == "result" and isinstance(msg.get("payload"), dict):
                    payload = msg["payload"]
                    err_code = str(((payload.get("error") or {}).get("code") or ""))
                    if err_code == "daemon_version_mismatch":
                        return _fallback_to_local_mind(raw, args)
                    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                    sys.stdout.flush()
                    return 0
                elif "ok" in msg:
                    err_code = str(((msg.get("error") or {}).get("code") or ""))
                    if err_code == "daemon_version_mismatch":
                        return _fallback_to_local_mind(raw, args)
                    sys.stdout.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
                    sys.stdout.flush()
                    return 0
    except OSError:
        return _fallback_to_local_mind(raw, args)
    finally:
        try:
            if prev_term is not None:
                signal.signal(signal.SIGTERM, prev_term)
            if prev_int is not None:
                signal.signal(signal.SIGINT, prev_int)
        except Exception:
            pass
    return _fallback_to_local_mind(raw, args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
