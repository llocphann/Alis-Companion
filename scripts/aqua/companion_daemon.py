#!/usr/bin/env python3
"""Persistent per-user Unix socket AI runtime daemon for Alis-Companion.

Keeps heavyweight Python modules, SQLite helpers, FTS5/vector indexes, and MCP
session pools warm across turns while isolating each request's stdout/stderr
streams (`__AQUA_STEP__`, `__AQUA_PROGRESS__`, `__AQUA_SWARM__`, `__AQUA_CURSOR__`)
and cancellation tokens. Automatically exits after idle timeout.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_IDLE_SEC = 900
MAX_REQUEST_BYTES = 98304


def resolve_socket_path() -> Path:
    explicit = str(os.environ.get("ALIS_COMPANION_DAEMON_SOCK") or "").strip()
    if explicit:
        return Path(explicit)
    runtime_dir = str(os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    if runtime_dir:
        base = Path(runtime_dir)
    else:
        base = Path.home() / ".local" / "state" / "quickshell" / "user"
    return base / f"alis-companion-ai-{os.getuid()}.sock"


_STREAM_LOCAL = threading.local()
_MUX_LOCK = threading.Lock()
_ACTIVE_REQUESTS_LOCK = threading.Lock()
_ACTIVE_REQUESTS: dict[str, threading.Event] = {}
_ACTIVE_STREAM_SINKS: dict[str, tuple[Callable[[str], None] | None, Callable[[str], None] | None]] = {}
_TOTAL_REQUESTS = 0
_DAEMON_START_TIME = 0.0
_SHUTDOWN_EVENT = threading.Event()


def _resolve_active_sink(stream_kind: str) -> Callable[[str], None] | None:
    attr = "stderr_cb" if stream_kind == "stderr" else "stdout_cb"
    cb = getattr(_STREAM_LOCAL, attr, None)
    if cb is not None:
        return cb
    req_id = str(getattr(threading.current_thread(), "_aqua_request_id", "") or "")
    if req_id:
        with _ACTIVE_REQUESTS_LOCK:
            pair = _ACTIVE_STREAM_SINKS.get(req_id)
            if pair is not None:
                return pair[0] if stream_kind == "stderr" else pair[1]
    # Fallback for child worker threads spawned inside a single active request
    with _ACTIVE_REQUESTS_LOCK:
        if len(_ACTIVE_STREAM_SINKS) == 1:
            pair = next(iter(_ACTIVE_STREAM_SINKS.values()))
            return pair[0] if stream_kind == "stderr" else pair[1]
    return None


class _ThreadAwareStreamMux:
    """Line-buffered stream proxy routing writes to the originating request socket."""

    def __init__(self, orig_stream: Any, stream_kind: str) -> None:
        self._orig = orig_stream
        self._kind = stream_kind
        self._buf_local = threading.local()

    @property
    def encoding(self) -> str:
        return getattr(self._orig, "encoding", "utf-8")

    def isatty(self) -> bool:
        return False

    def write(self, data: str | bytes) -> int:
        text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)
        if not text:
            return 0
        cb = _resolve_active_sink(self._kind)
        if cb is None:
            return self._orig.write(text)
        buf = getattr(self._buf_local, "buf", "") + text
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            try:
                cb(line + "\n")
            except Exception:
                pass
        self._buf_local.buf = buf
        return len(text)

    def flush(self) -> None:
        cb = _resolve_active_sink(self._kind)
        buf = getattr(self._buf_local, "buf", "")
        if cb is not None and buf:
            self._buf_local.buf = ""
            try:
                cb(buf)
            except Exception:
                pass
        elif cb is None:
            try:
                self._orig.flush()
            except Exception:
                pass


@contextmanager
def _bind_request_stream_sinks(
    req_id: str,
    stderr_cb: Callable[[str], None] | None,
    stdout_cb: Callable[[str], None] | None,
    cancel_ev: threading.Event,
    stream_stdout: bool,
):
    prev_stderr = getattr(_STREAM_LOCAL, "stderr_cb", None)
    prev_stdout = getattr(_STREAM_LOCAL, "stdout_cb", None)
    cur_thread = threading.current_thread()
    prev_cancel = getattr(cur_thread, "_aqua_cancel_event", None)
    prev_req_id = getattr(cur_thread, "_aqua_request_id", None)
    prev_json_rpc = getattr(cur_thread, "_aqua_json_rpc_stdout", None)

    _STREAM_LOCAL.stderr_cb = stderr_cb
    _STREAM_LOCAL.stdout_cb = stdout_cb
    cur_thread._aqua_cancel_event = cancel_ev
    cur_thread._aqua_request_id = req_id
    cur_thread._aqua_json_rpc_stdout = not bool(stream_stdout)
    with _ACTIVE_REQUESTS_LOCK:
        _ACTIVE_REQUESTS[req_id] = cancel_ev
        _ACTIVE_STREAM_SINKS[req_id] = (stderr_cb, stdout_cb)
    try:
        yield
    finally:
        # Flush any buffered partial line before clearing sinks
        for stream_obj in (sys.stderr, sys.stdout):
            if isinstance(stream_obj, _ThreadAwareStreamMux):
                stream_obj.flush()
        _STREAM_LOCAL.stderr_cb = prev_stderr
        _STREAM_LOCAL.stdout_cb = prev_stdout
        cur_thread._aqua_cancel_event = prev_cancel
        cur_thread._aqua_request_id = prev_req_id
        if prev_json_rpc is None:
            try:
                delattr(cur_thread, "_aqua_json_rpc_stdout")
            except AttributeError:
                pass
        else:
            cur_thread._aqua_json_rpc_stdout = prev_json_rpc
        with _ACTIVE_REQUESTS_LOCK:
            _ACTIVE_REQUESTS.pop(req_id, None)
            _ACTIVE_STREAM_SINKS.pop(req_id, None)


def _install_stream_muxes() -> None:
    with _MUX_LOCK:
        if not isinstance(sys.stderr, _ThreadAwareStreamMux):
            sys.stderr = _ThreadAwareStreamMux(sys.stderr, "stderr")  # type: ignore[assignment]
        if not isinstance(sys.stdout, _ThreadAwareStreamMux):
            sys.stdout = _ThreadAwareStreamMux(sys.stdout, "stdout")  # type: ignore[assignment]


def _prewarm_modules() -> list[str]:
    loaded: list[str] = []
    for mod_name in (
        "local_mind",
        "context_policy",
        "thinking_router",
        "prompt_assembler",
        "knowledge_index",
        "nexus_rag",
        "permission_store",
        "run_store",
        "mcp_transport",
        "mcp_client",
        "agent_runtime",
    ):
        try:
            __import__(mod_name)
            loaded.append(mod_name)
        except Exception:
            pass
    return loaded


def _verify_peer_uid(conn: socket.socket) -> bool:
    if hasattr(socket, "SO_PEERCRED"):
        try:
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", creds)
            return int(uid) == os.getuid()
        except Exception:
            return False
    return True


def _handle_daemon_op(op: str, payload: dict, sock_path: Path, prewarmed: list[str]) -> dict:
    if op == "ping":
        with _ACTIVE_REQUESTS_LOCK:
            active_cnt = len(_ACTIVE_REQUESTS)
        return {
            "ok": True,
            "result": {
                "alive": True,
                "pid": os.getpid(),
                "socket": str(sock_path),
                "activeRequests": active_cnt,
            },
        }
    if op == "status":
        with _ACTIVE_REQUESTS_LOCK:
            active_ids = list(_ACTIVE_REQUESTS.keys())
        return {
            "ok": True,
            "result": {
                "alive": True,
                "pid": os.getpid(),
                "socket": str(sock_path),
                "uptimeSec": round(max(0.0, time.monotonic() - _DAEMON_START_TIME), 2),
                "totalRequests": _TOTAL_REQUESTS,
                "activeRequests": len(active_ids),
                "activeRequestIds": active_ids,
                "loadedModules": list(prewarmed),
            },
        }
    if op == "cancel_request":
        target_id = str(payload.get("requestId") or "").strip()
        reason = str(payload.get("reason") or "cancelled_by_daemon_client")
        cancelled_reqs: list[str] = []
        with _ACTIVE_REQUESTS_LOCK:
            if target_id:
                ev = _ACTIVE_REQUESTS.get(target_id)
                if ev is not None:
                    ev.set()
                    cancelled_reqs.append(target_id)
            else:
                for rid, ev in _ACTIVE_REQUESTS.items():
                    ev.set()
                    cancelled_reqs.append(rid)
        cancelled_runs: list[str] = []
        try:
            import run_store
            cancelled_runs = list(run_store.cancel_active_runs(reason=reason) or [])
        except Exception:
            pass
        return {
            "ok": True,
            "result": {
                "cancelled": True,
                "cancelledRequestIds": cancelled_reqs,
                "cancelledRunIds": cancelled_runs,
            },
        }
    if op == "shutdown":
        _SHUTDOWN_EVENT.set()
        return {"ok": True, "result": {"shuttingDown": True, "pid": os.getpid()}}
    return {"ok": False, "error": {"code": "invalid_daemon_op", "message": f"Unknown daemonOp: {op}"}}


def _serve_client(conn: socket.socket, sock_path: Path, prewarmed: list[str], touch_fn: Callable[[], None]) -> None:
    global _TOTAL_REQUESTS
    conn_lock = threading.Lock()

    def _send_obj(obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
        raw = line.encode("utf-8", errors="replace")
        with conn_lock:
            conn.sendall(raw)

    try:
        if not _verify_peer_uid(conn):
            _send_obj({"ok": False, "error": {"code": "permission_denied", "message": "Peer UID mismatch"}})
            return
        conn.settimeout(300.0)
        buf = bytearray()
        while len(buf) <= MAX_REQUEST_BYTES + 1:
            chunk = conn.recv(4096)
            if not chunk:
                break
            nl_idx = chunk.find(b"\n")
            if nl_idx != -1:
                buf.extend(chunk[:nl_idx])
                break
            buf.extend(chunk)
        if len(buf) > MAX_REQUEST_BYTES:
            _send_obj({"ok": False, "error": {"code": "request_too_large", "message": "Companion request exceeded the limit"}})
            return
        if not buf.strip():
            return
        touch_fn()
        try:
            parsed = json.loads(buf.decode("utf-8", errors="strict"))
        except Exception:
            _send_obj({"ok": False, "error": {"code": "invalid_request", "message": "Invalid JSON request"}})
            return
        if not isinstance(parsed, dict):
            _send_obj({"ok": False, "error": {"code": "invalid_request", "message": "Request must be a JSON object"}})
            return

        daemon_op = str(parsed.get("daemonOp") or "").strip().lower()
        if daemon_op:
            _send_obj(_handle_daemon_op(daemon_op, parsed, sock_path, prewarmed))
            return

        import local_mind

        _TOTAL_REQUESTS += 1
        req_id = str(parsed.get("requestId") or f"req_{int(time.time() * 1000)}_{_TOTAL_REQUESTS}")
        cancel_ev = threading.Event()
        stream_stdout = bool(parsed.get("streamStdout"))

        def _stderr_cb(line: str) -> None:
            _send_obj({"type": "stderr", "line": line})

        def _stdout_cb(line: str) -> None:
            _send_obj({"type": "stdout", "line": line})

        with _bind_request_stream_sinks(req_id, _stderr_cb, _stdout_cb, cancel_ev, stream_stdout):
            try:
                res = local_mind.dispatch(parsed)
                envelope = {"ok": True, "result": res}
            except local_mind.journal_errors() as exc:
                envelope = {"ok": False, "error": {"code": exc.code, "message": str(exc)[:160]}}
            except OSError:
                envelope = {"ok": False, "error": {"code": "file_unavailable", "message": "Configured local file is unavailable"}}
            except (ValueError, TypeError, KeyError, UnicodeError):
                envelope = {"ok": False, "error": {"code": "invalid_request", "message": "Invalid companion data"}}

        touch_fn()
        _send_obj({"type": "result", "payload": envelope})
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def run_daemon(sock_path: Path | None = None, idle_sec: int | None = None) -> int:
    """Start the Unix-domain companion daemon and block until idle or shutdown."""
    global _DAEMON_START_TIME
    _SHUTDOWN_EVENT.clear()
    _DAEMON_START_TIME = time.monotonic()
    target_sock = Path(sock_path) if sock_path else resolve_socket_path()
    target_sock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    eff_idle = max(
        1,
        int(idle_sec if idle_sec is not None else (os.environ.get("ALIS_COMPANION_DAEMON_IDLE_SEC") or DEFAULT_IDLE_SEC)),
    )

    _install_stream_muxes()
    prewarmed = _prewarm_modules()

    try:
        target_sock.unlink(missing_ok=True)
    except OSError:
        pass

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    last_active = time.monotonic()
    active_lock = threading.Lock()
    workers: list[threading.Thread] = []

    def _touch() -> None:
        nonlocal last_active
        with active_lock:
            last_active = time.monotonic()

    try:
        srv.bind(str(target_sock))
        os.chmod(target_sock, 0o600)
        srv.listen(32)
        srv.settimeout(min(1.0, max(0.1, eff_idle / 3.0)))

        while not _SHUTDOWN_EVENT.is_set():
            try:
                conn, _ = srv.accept()
                _touch()
                t = threading.Thread(
                    target=_serve_client,
                    args=(conn, target_sock, prewarmed, _touch),
                    daemon=True,
                )
                t.start()
                workers = [w for w in workers if w.is_alive()]
                workers.append(t)
            except TimeoutError:
                with _ACTIVE_REQUESTS_LOCK:
                    busy = len(_ACTIVE_REQUESTS) > 0
                with active_lock:
                    idle_elapsed = time.monotonic() - last_active
                if not busy and idle_elapsed >= eff_idle:
                    break
            except OSError:
                break
    finally:
        for w in workers:
            if w.is_alive():
                w.join(timeout=1.5)
        try:
            srv.close()
        except OSError:
            pass
        try:
            target_sock.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


def _raw_socket_call(payload: dict, sock_path: Path, timeout: float = 2.0) -> dict | None:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(sock_path))
        s.sendall(line.encode("utf-8"))
        reader = s.makefile("r", encoding="utf-8", errors="replace")
        resp_line = reader.readline()
        if not resp_line:
            return None
        return json.loads(resp_line)


def daemon_status(sock_path: Path | None = None, timeout: float = 0.6) -> dict:
    target = Path(sock_path) if sock_path else resolve_socket_path()
    if not target.exists():
        return {"alive": False, "socket": str(target)}
    try:
        resp = _raw_socket_call({"daemonOp": "status"}, target, timeout=timeout)
        if isinstance(resp, dict) and resp.get("ok") and isinstance(resp.get("result"), dict):
            return dict(resp["result"])
    except Exception:
        pass
    return {"alive": False, "socket": str(target)}


def is_daemon_running(sock_path: Path | None = None, timeout: float = 0.4) -> bool:
    target = Path(sock_path) if sock_path else resolve_socket_path()
    if not target.exists():
        return False
    try:
        resp = _raw_socket_call({"daemonOp": "ping"}, target, timeout=timeout)
        return bool(isinstance(resp, dict) and resp.get("ok") and (resp.get("result") or {}).get("alive"))
    except Exception:
        return False


def shutdown_daemon(sock_path: Path | None = None, timeout: float = 1.5) -> bool:
    target = Path(sock_path) if sock_path else resolve_socket_path()
    if not target.exists():
        return False
    stopped = False
    try:
        resp = _raw_socket_call({"daemonOp": "shutdown"}, target, timeout=timeout)
        stopped = bool(isinstance(resp, dict) and resp.get("ok"))
    except Exception:
        pass
    deadline = time.monotonic() + min(1.5, timeout)
    while target.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
    return stopped


def cancel_daemon_request(request_id: str = "", reason: str = "cancelled_by_user", sock_path: Path | None = None) -> dict | None:
    target = Path(sock_path) if sock_path else resolve_socket_path()
    if not target.exists():
        return None
    try:
        return _raw_socket_call(
            {"daemonOp": "cancel_request", "requestId": request_id, "reason": reason},
            target,
            timeout=2.0,
        )
    except Exception:
        return None


def ensure_daemon_running(sock_path: Path | None = None, timeout: float = 5.0) -> bool:
    target = Path(sock_path) if sock_path else resolve_socket_path()
    if is_daemon_running(target):
        return True
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = target.with_suffix(".lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            if is_daemon_running(target):
                return True
            env = dict(os.environ)
            env["ALIS_COMPANION_DAEMON_SOCK"] = str(target)
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--daemon"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
            deadline = time.monotonic() + max(0.5, float(timeout))
            while time.monotonic() < deadline:
                if is_daemon_running(target, timeout=0.35):
                    return True
                time.sleep(0.04)
        finally:
            fcntl.flock(lock_path.fileno(), fcntl.LOCK_UN)
    return False


def call_via_daemon(
    payload: dict,
    sock_path: Path | None = None,
    *,
    auto_start: bool = True,
    timeout: float = 240.0,
    on_stderr: Callable[[str], None] | None = None,
    on_stdout: Callable[[str], None] | None = None,
) -> dict:
    """Send a companion dispatch payload through the Unix-socket daemon and stream events."""
    target = Path(sock_path) if sock_path else resolve_socket_path()
    if auto_start and not is_daemon_running(target):
        if not ensure_daemon_running(target):
            raise RuntimeError("daemon_unavailable")

    wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    raw = wire.encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        return {"ok": False, "error": {"code": "request_too_large", "message": "Companion request exceeded the limit"}}

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(target))
        s.sendall(raw)
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
                err_line = str(msg.get("line") or "")
                if on_stderr is not None:
                    on_stderr(err_line)
                else:
                    sys.stderr.write(err_line)
                    sys.stderr.flush()
            elif mtype == "stdout":
                out_line = str(msg.get("line") or "")
                if on_stdout is not None:
                    on_stdout(out_line)
                else:
                    sys.stdout.write(out_line)
                    sys.stdout.flush()
            elif mtype == "result" and isinstance(msg.get("payload"), dict):
                return msg["payload"]
            elif "ok" in msg:
                return msg
    raise RuntimeError("daemon_eof")


def run_daemon_cli(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    idle_sec: int | None = None
    sock_path: Path | None = None
    if "--idle-sec" in args:
        idx = args.index("--idle-sec")
        if idx + 1 < len(args):
            try:
                idle_sec = int(args[idx + 1])
            except ValueError:
                pass
    if "--socket" in args:
        idx = args.index("--socket")
        if idx + 1 < len(args):
            sock_path = Path(args[idx + 1])
    return run_daemon(sock_path=sock_path, idle_sec=idle_sec)


if __name__ == "__main__":
    raise SystemExit(run_daemon_cli(sys.argv[1:]))
