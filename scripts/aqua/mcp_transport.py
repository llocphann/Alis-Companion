#!/usr/bin/env python3
"""Real MCP JSON-RPC 2.0 session pool (stdio + Streamable HTTP/SSE + rug-pull integrity).

Replaces one-shot process spawning with pooled persistent stdio sessions that drain
stderr asynchronously, respect Streamable HTTP session headers, and pin external
server tool manifest hashes against mid-session schema tampering.
"""
from __future__ import annotations

import atexit
from collections import deque
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from web_tools import build_safe_opener, validate_external_url

MCP_PROTOCOL_VERSION = "2024-11-05"
_SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "XDG_RUNTIME_DIR",
    "XDG_STATE_HOME",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "WAYLAND_DISPLAY",
    "DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
    "TERM",
)


class McpTransportError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = str(code)
        self.message = str(message or code)
        super().__init__(self.message)


def _hashes_state_file() -> Path:
    explicit = os.environ.get("ALIS_AQUA_MCP_HASHES_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "alis-companion" / "mcp_tool_hashes.json"


def resolve_server_command(command: str) -> str:
    cmd_str = str(command or "").strip()
    if not cmd_str:
        return ""
    if not Path(cmd_str).is_absolute():
        resolved_bin = shutil.which(cmd_str)
        if not resolved_bin:
            for cand in (
                Path.home() / ".local/bin" / cmd_str,
                Path.home() / ".local/share/uv/tools/graphifyy/bin" / cmd_str,
            ):
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
        if resolved_bin:
            return resolved_bin
    return cmd_str


def build_safe_subprocess_env(extra_env: dict | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for k, v in (extra_env or {}).items():
        val = str(v)
        if val.startswith("secret-tool://") and os.environ.get("ALIS_TEST_ISOLATED") != "1":
            attr_parts = val[len("secret-tool://"):].split("/")
            st_bin = shutil.which("secret-tool")
            if st_bin and len(attr_parts) >= 2 and len(attr_parts) % 2 == 0:
                try:
                    res = subprocess.run(
                        [st_bin, "lookup"] + attr_parts,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        timeout=1.5,
                        check=False,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        val = res.stdout.strip()
                except Exception:
                    pass
        env[str(k)] = val
    return env


class _StdioSession:
    def __init__(self, key: tuple, server_name: str, cmd: list[str], env: dict[str, str]):
        self.key = key
        self.server_name = server_name
        self.cmd = cmd
        self.env = env
        self.lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self._stderr_thread: threading.Thread | None = None
        self._req_id = 0
        self.initialized = False
        self.last_used = time.monotonic()

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _drain_stderr(self, pipe) -> None:
        try:
            for line in iter(pipe.readline, ""):
                txt = line.strip()
                if txt:
                    self.stderr_tail.append(txt[:240])
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def ensure_started(self, timeout_sec: float = 6.0) -> None:
        if self.is_alive() and self.initialized:
            return
        self.close()
        try:
            proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            raise McpTransportError("server_spawn_failed", f"Failed to start MCP server '{self.server_name}': {exc}") from exc
        self.proc = proc
        if proc.stderr is not None:
            th = threading.Thread(target=self._drain_stderr, args=(proc.stderr,), daemon=True)
            th.start()
            self._stderr_thread = th
        deadline = time.monotonic() + max(1.0, float(timeout_sec))
        self._req_id += 1
        init_id = self._req_id
        self._write_msg({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "alis-companion", "version": "1.0.0"},
            },
        })
        self._read_reply(init_id, "initialize", deadline)
        self._write_msg({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        self.initialized = True
        self.last_used = time.monotonic()

    def _write_msg(self, msg: dict) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def _read_reply(self, expected_id: int, method: str, deadline: float) -> dict:
        assert self.proc is not None and self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            ready, _, _ = select.select([fd], [], [], min(0.5, remaining))
            if not ready:
                if self.proc.poll() is not None:
                    break
                continue
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("id") == expected_id:
                if "error" in parsed and isinstance(parsed["error"], dict):
                    raise McpTransportError("mcp_rpc_error", str(parsed["error"].get("message") or "MCP RPC error"))
                return parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
        raise McpTransportError("mcp_timeout", f"MCP server '{self.server_name}' timed out or closed on '{method}'.")

    def exchange(self, method: str, params: dict | None = None, timeout_sec: float = 6.0) -> dict:
        with self.lock:
            for attempt in range(2):
                try:
                    self.ensure_started(timeout_sec=timeout_sec)
                    self._req_id += 1
                    rid = self._req_id
                    self._write_msg({
                        "jsonrpc": "2.0",
                        "id": rid,
                        "method": method,
                        "params": params or {},
                    })
                    res = self._read_reply(rid, method, time.monotonic() + max(0.5, float(timeout_sec)))
                    self.last_used = time.monotonic()
                    return res
                except McpTransportError as exc:
                    if exc.code == "mcp_rpc_error":
                        raise
                    self.close()
                    if attempt == 1:
                        raise
                except (BrokenPipeError, OSError):
                    self.close()
                    if attempt == 1:
                        raise McpTransportError("mcp_timeout", f"MCP server '{self.server_name}' pipe closed on '{method}'.")
            raise McpTransportError("mcp_timeout", f"MCP server '{self.server_name}' failed on '{method}'.")

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        self.initialized = False
        if proc is None:
            return
        pid = proc.pid
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=0.8)
            except Exception:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass


_POOL_LOCK = threading.Lock()
_STDIO_POOL: dict[tuple, _StdioSession] = {}
_HTTP_SESSION_IDS: dict[tuple[str, str], str] = {}
_IDLE_TTL_SEC = 120.0


def _evict_idle_locked() -> None:
    now = time.monotonic()
    stale = [k for k, sess in _STDIO_POOL.items() if not sess.is_alive() or (now - sess.last_used) > _IDLE_TTL_SEC]
    for k in stale:
        sess = _STDIO_POOL.pop(k, None)
        if sess is not None:
            sess.close()


def close_all_sessions() -> None:
    with _POOL_LOCK:
        sessions = list(_STDIO_POOL.values())
        _STDIO_POOL.clear()
        _HTTP_SESSION_IDS.clear()
    for sess in sessions:
        sess.close()


atexit.register(close_all_sessions)


def get_pooled_stdio_pid(server_name: str) -> int | None:
    with _POOL_LOCK:
        for (sname, *_), sess in _STDIO_POOL.items():
            if sname == server_name and sess.is_alive() and sess.proc is not None:
                return int(sess.proc.pid)
    return None


def stdio_rpc_exchange(server: dict, method: str, params: dict | None = None, timeout_sec: float = 6.0) -> dict:
    cmd_str = resolve_server_command(str(server.get("command") or ""))
    if not cmd_str:
        raise McpTransportError("invalid_server_config", f"MCP server '{server.get('name', '')}' has no command.")
    args = [str(a) for a in (server.get("args") or [])]
    env = build_safe_subprocess_env(server.get("env") if isinstance(server.get("env"), dict) else None)
    env_sig = hashlib.sha256(json.dumps(sorted(env.items())).encode("utf-8")).hexdigest()[:16]
    key = (str(server.get("name") or ""), cmd_str, tuple(args), env_sig)

    with _POOL_LOCK:
        _evict_idle_locked()
        sess = _STDIO_POOL.get(key)
        if sess is None:
            sess = _StdioSession(key, str(server.get("name") or cmd_str), [cmd_str] + args, env)
            _STDIO_POOL[key] = sess

    return sess.exchange(method, params=params, timeout_sec=timeout_sec)


def _parse_sse_rpc_response(raw_text: str, expected_id: int) -> dict | None:
    data_buf: list[str] = []
    events: list[str] = []
    for ln in raw_text.splitlines():
        if not ln.strip():
            if data_buf:
                events.append("\n".join(data_buf))
                data_buf = []
            continue
        if ln.startswith("data:"):
            data_buf.append(ln[5:].lstrip())
    if data_buf:
        events.append("\n".join(data_buf))

    for ev in events:
        try:
            obj = json.loads(ev)
        except ValueError:
            continue
        if isinstance(obj, dict) and (obj.get("id") == expected_id or "result" in obj or "error" in obj):
            return obj
    return None


def http_rpc_exchange(server: dict, method: str, params: dict | None = None, timeout_sec: float = 6.0) -> dict:
    url = str(server.get("url") or "").strip()
    sname = str(server.get("name") or "")
    if not url.startswith(("http://", "https://")):
        raise McpTransportError("invalid_server_config", f"MCP server '{sname}' has an invalid HTTP URL.")
    parsed_host = (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    explicit_loopback = parsed_host in ("127.0.0.1", "localhost", "::1")
    ok_url, reason = validate_external_url(url, allow_local=explicit_loopback)
    if not ok_url:
        raise McpTransportError("ssrf_blocked", f"MCP server '{sname}' HTTP target blocked ({reason}).")

    req_id = 1
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    sess_key = (sname, url)
    with _POOL_LOCK:
        sid = _HTTP_SESSION_IDS.get(sess_key, "")
    if sid:
        headers["Mcp-Session-Id"] = sid

    req = urllib.request.Request(url, data=payload, headers=headers)
    opener = build_safe_opener(allow_local=explicit_loopback)
    try:
        with opener.open(req, timeout=timeout_sec) as resp:
            resp_sid = str(resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id") or "").strip()
            if resp_sid:
                with _POOL_LOCK:
                    _HTTP_SESSION_IDS[sess_key] = resp_sid
            ctype = str(resp.headers.get("Content-Type") or "").lower()
            raw_text = resp.read(256 * 1024).decode("utf-8", errors="replace")

        parsed = (
            _parse_sse_rpc_response(raw_text, req_id)
            if ("text/event-stream" in ctype or raw_text.lstrip().startswith(("event:", "data:")))
            else json.loads(raw_text)
        )
        if not isinstance(parsed, dict):
            raise ValueError("Invalid MCP HTTP payload")
        if "error" in parsed and isinstance(parsed["error"], dict):
            raise McpTransportError("mcp_rpc_error", str(parsed["error"].get("message") or "MCP HTTP RPC error"))
        return parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
    except McpTransportError:
        raise
    except Exception as exc:
        raise McpTransportError("mcp_http_failed", f"MCP HTTP request to '{sname}' failed: {exc}") from exc


def compute_tool_manifest_hash(tools: list[dict]) -> str:
    canonical = sorted(
        (
            {
                "name": str(t.get("name") or ""),
                "description": str(t.get("description") or ""),
                "inputSchema": t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {},
            }
            for t in (tools or [])
            if isinstance(t, dict) and t.get("name")
        ),
        key=lambda t: t["name"],
    )
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def verify_tool_manifest_integrity(
    server_name: str,
    tools: list[dict],
    *,
    auto_approve: bool = False,
) -> tuple[bool, str]:
    """Pin tool schema digest on first discovery; detect unexpected schema changes (rug-pull)."""
    sname = str(server_name or "").strip()
    if not sname or not tools:
        return True, ""
    digest = compute_tool_manifest_hash(tools)
    hfile = _hashes_state_file()
    hfile.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_file = hfile.parent / f".{hfile.name}.lock"

    with open(lock_file, "a+", encoding="utf-8") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(hfile.read_text(encoding="utf-8")) if hfile.is_file() else {}
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            prev = str(data.get(sname) or "")
            if not prev or auto_approve:
                data[sname] = digest
                fd, tmp_name = tempfile.mkstemp(prefix=".mcp-hash-", dir=str(hfile.parent))
                tmp_p = Path(tmp_name)
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(json.dumps(data, indent=2, sort_keys=True))
                        fh.flush()
                    os.replace(tmp_p, hfile)
                except OSError:
                    tmp_p.unlink(missing_ok=True)
                return True, digest
            if prev != digest:
                return False, f"{prev[:12]}->{digest[:12]}"
            return True, digest
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
