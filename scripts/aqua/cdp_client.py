#!/usr/bin/env python3
"""Pure-stdlib loopback-only Chrome DevTools Protocol (CDP) RFC 6455 WebSocket client.

Connects exclusively to loopback (`127.0.0.1`, `localhost`, `::1`), bypasses system
HTTP proxies, and implements RFC 6455 framing + JSON-RPC over WebSocket for live
Playwright/Chrome DevTools browser automation without external Python packages.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import os
import socket
import struct
import time
from typing import Any
import urllib.parse
import urllib.request

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MSG_SEQ = itertools.count(1)


class CdpError(Exception):
    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(self.message)


def _is_loopback_host(host: str) -> bool:
    return str(host or "").strip("[]").lower() in ("127.0.0.1", "localhost", "::1")


def _resolve_cdp_port(port: int | None = None) -> int:
    if port is not None:
        return int(port)
    for key in ("ALIS_TEST_CDP_PORT", "ALIS_CDP_PORT"):
        raw = os.environ.get(key, "").strip()
        if raw.isdigit() and 1 <= int(raw) <= 65535:
            return int(raw)
    return 9222


def _parse_loopback_ws_url(ws_url: str) -> tuple[str, int, str]:
    raw = str(ws_url or "").strip()
    if not raw:
        raise CdpError("missing_cdp_url", "CDP WebSocket URL is empty.")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme.lower() != "ws":
        raise CdpError("unsafe_cdp_url", f"Refusing non-ws CDP scheme '{parsed.scheme}'.")
    host = (parsed.hostname or "").strip("[]")
    if not _is_loopback_host(host):
        raise CdpError("unsafe_cdp_url", f"Refusing non-loopback CDP host '{host}'.")
    port = parsed.port or 9222
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return host, int(port), path


def probe_cdp_targets(port: int | None = None, timeout: float = 0.6) -> list[dict[str, Any]]:
    """Return loopback CDP page targets from `http://127.0.0.1:<port>/json/list`."""
    import native_host_bridge

    if port is None and not os.environ.get("ALIS_TEST_CDP_PORT") and native_host_bridge.is_test_or_isolated_env():
        return []
    eff_port = _resolve_cdp_port(port)
    url = f"http://127.0.0.1:{eff_port}/json/list"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as resp:
            raw = resp.read(262144).decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    targets: list[dict[str, Any]] = []
    for item in data[:20]:
        if not isinstance(item, dict):
            continue
        ws = str(item.get("webSocketDebuggerUrl") or "").strip()
        if ws:
            try:
                _parse_loopback_ws_url(ws)
            except CdpError:
                continue
        targets.append(item)
    return targets


def select_page_target(targets: list[dict[str, Any]]) -> str:
    """Select the best live browser tab target (preferring normal type=='page' tabs over devtools/extensions)."""
    if not isinstance(targets, list):
        return ""
    for t in targets:
        if not isinstance(t, dict) or t.get("type") != "page":
            continue
        ws = str(t.get("webSocketDebuggerUrl") or "").strip()
        u = str(t.get("url") or "").strip().lower()
        if ws and not u.startswith(("devtools://", "chrome-extension://", "chrome://")):
            return ws
    for t in targets:
        if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return str(t["webSocketDebuggerUrl"]).strip()
    for t in targets:
        if isinstance(t, dict) and t.get("webSocketDebuggerUrl"):
            return str(t["webSocketDebuggerUrl"]).strip()
    return ""


def _resolve_ws_url(ws_url: str = "", port: int | None = None, timeout: float = 0.6) -> str:
    if str(ws_url or "").strip():
        _parse_loopback_ws_url(ws_url)
        return str(ws_url).strip()
    targets = probe_cdp_targets(port=port, timeout=timeout)
    if not targets:
        eff_port = _resolve_cdp_port(port)
        raise CdpError(
            "cdp_unavailable",
            f"No live Chrome/Chromium CDP target found on 127.0.0.1:{eff_port}. Start browser with --remote-debugging-port={eff_port}.",
        )
    chosen = select_page_target(targets)
    if chosen:
        return chosen
    raise CdpError("cdp_unavailable", "CDP targets did not expose a loopback webSocketDebuggerUrl.")


def _recv_exact(sock: socket.socket, buf: bytearray, n: int) -> bytes:
    while len(buf) < n:
        chunk = sock.recv(max(4096, n - len(buf)))
        if not chunk:
            raise CdpError("cdp_eof", "CDP WebSocket connection closed unexpectedly.")
        buf.extend(chunk)
    out = bytes(buf[:n])
    del buf[:n]
    return out


def _send_ws_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    length = len(payload)
    mask_key = os.urandom(4)
    hdr = bytearray([0x80 | (opcode & 0x0F)])
    if length < 126:
        hdr.append(0x80 | length)
    elif length <= 0xFFFF:
        hdr.append(0x80 | 126)
        hdr.extend(struct.pack("!H", length))
    else:
        hdr.append(0x80 | 127)
        hdr.extend(struct.pack("!Q", length))
    hdr.extend(mask_key)
    masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
    sock.sendall(bytes(hdr) + masked)


def _recv_ws_frame(sock: socket.socket, buf: bytearray) -> tuple[bool, int, bytes]:
    b0, b1 = _recv_exact(sock, buf, 2)
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    is_masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, buf, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, buf, 8))[0]
    if length > 4 * 1024 * 1024:
        raise CdpError("cdp_frame_too_large", "Refusing CDP frame exceeding 4 MiB.")
    mask_key = _recv_exact(sock, buf, 4) if is_masked else b""
    payload = _recv_exact(sock, buf, length)
    if is_masked:
        payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
    return fin, opcode, payload


def _recv_ws_message(sock: socket.socket, buf: bytearray) -> tuple[int, bytes]:
    chunks: list[bytes] = []
    msg_opcode = -1
    while True:
        fin, opcode, payload = _recv_ws_frame(sock, buf)
        if opcode == 0x8:  # Close
            raise CdpError("cdp_closed", "CDP WebSocket closed by peer.")
        if opcode == 0x9:  # Ping -> Pong
            _send_ws_frame(sock, 0xA, payload[:125])
            continue
        if opcode == 0xA:  # Pong
            continue
        if opcode in (0x1, 0x2):
            msg_opcode = opcode
            chunks.append(payload)
            if fin:
                return msg_opcode, b"".join(chunks)
        elif opcode == 0x0:  # Continuation
            chunks.append(payload)
            if fin:
                return (msg_opcode if msg_opcode != -1 else 0x1), b"".join(chunks)


def cdp_send_command(
    ws_url: str,
    method: str,
    params: dict[str, Any] | None = None,
    timeout: float = 4.0,
) -> dict[str, Any]:
    """Execute one CDP JSON-RPC request over a loopback RFC 6455 WebSocket."""
    host, port, path = _parse_loopback_ws_url(ws_url)
    msg_id = next(_MSG_SEQ)
    key_b64 = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(hashlib.sha1((key_b64 + _WS_MAGIC).encode("ascii")).digest()).decode("ascii")

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        req_lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key_b64}",
            "Sec-WebSocket-Version: 13",
            "",
            "",
        ]
        sock.sendall("\r\n".join(req_lines).encode("ascii"))

        buf = bytearray()
        hdr_end = -1
        while hdr_end == -1:
            chunk = sock.recv(4096)
            if not chunk:
                raise CdpError("cdp_handshake_failed", "EOF during CDP WebSocket handshake.")
            buf.extend(chunk)
            if len(buf) > 32768:
                raise CdpError("cdp_handshake_failed", "Oversized HTTP upgrade response.")
            hdr_end = buf.find(b"\r\n\r\n")

        raw_hdr = bytes(buf[:hdr_end]).decode("latin-1", errors="replace")
        del buf[: hdr_end + 4]
        lines = raw_hdr.split("\r\n")
        if not lines or " 101 " not in lines[0]:
            raise CdpError("cdp_handshake_failed", f"CDP upgrade rejected: {lines[0] if lines else 'empty'}")
        headers: dict[str, str] = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if headers.get("sec-websocket-accept", "").strip() != expected_accept:
            raise CdpError("cdp_handshake_failed", "Invalid Sec-WebSocket-Accept from CDP endpoint.")

        req_payload = json.dumps(
            {"id": msg_id, "method": str(method), "params": params if isinstance(params, dict) else {}}
        ).encode("utf-8")
        _send_ws_frame(sock, 0x1, req_payload)

        while True:
            _op, frame_bytes = _recv_ws_message(sock, buf)
            try:
                msg = json.loads(frame_bytes.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == msg_id:
                if isinstance(msg.get("error"), dict):
                    err_msg = str(msg["error"].get("message") or msg["error"])
                    raise CdpError("cdp_command_error", err_msg)
                res = msg.get("result")
                return res if isinstance(res, dict) else {"value": res}
    finally:
        try:
            _send_ws_frame(sock, 0x8, b"")
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


def cdp_evaluate(
    expression: str,
    *,
    ws_url: str = "",
    port: int | None = None,
    timeout: float = 4.0,
) -> dict[str, Any]:
    target_ws = _resolve_ws_url(ws_url, port=port)
    res = cdp_send_command(
        target_ws,
        "Runtime.evaluate",
        {"expression": str(expression or ""), "returnByValue": True, "awaitPromise": True},
        timeout=timeout,
    )
    val_obj = (res.get("result") or {}) if isinstance(res.get("result"), dict) else {}
    if isinstance(res.get("exceptionDetails"), dict):
        ex_txt = str(res["exceptionDetails"].get("text") or "JS evaluation error")
        raise CdpError("cdp_js_exception", ex_txt)
    return {
        "ok": True,
        "wsUrl": target_ws,
        "type": str(val_obj.get("type") or ""),
        "value": val_obj.get("value"),
        "raw": res,
    }


def cdp_navigate(
    url: str,
    *,
    ws_url: str = "",
    port: int | None = None,
    timeout: float = 6.0,
) -> dict[str, Any]:
    import web_tools

    target_url = str(url or "").strip()
    ok_url, reason = web_tools.validate_external_url(
        target_url,
        allow_local=bool(os.environ.get("ALIS_TEST_CDP_PORT")),
        allow_loopback_only=True,
    )
    if not ok_url:
        raise CdpError("ssrf_blocked", f"Refusing unsafe navigation URL ({reason}): {target_url}")
    target_ws = _resolve_ws_url(ws_url, port=port)
    nav_res = cdp_send_command(target_ws, "Page.navigate", {"url": target_url}, timeout=timeout)
    if nav_res.get("errorText"):
        raise CdpError("cdp_navigate_failed", str(nav_res["errorText"]))
    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        try:
            st_ev = cdp_evaluate("document.readyState", ws_url=target_ws, timeout=1.0)
            rs = st_ev.get("value")
            if not isinstance(rs, str) or rs == "complete":
                break
        except Exception:
            break
        time.sleep(0.08)
    return {
        "ok": True,
        "url": target_url,
        "frameId": str(nav_res.get("frameId") or ""),
        "wsUrl": target_ws,
    }


_INTERACTIVE_SELECTOR = (
    'a,button,input:not([type="hidden"]),select,textarea,[contenteditable="true"],'
    '[role="button"],[role="link"],[role="textbox"],[role="combobox"],[role="tab"],[role="checkbox"],[role="switch"]'
)

_SNAPSHOT_JS = """(() => {
  const maxN = %d;
  const raw = Array.from(document.querySelectorAll('""" + _INTERACTIVE_SELECTOR + """'));
  const nodes = raw.filter(el => {
    try {
      if (el.disabled || el.type === 'hidden') return false;
      if (el.offsetParent !== null) return true;
      const st = window.getComputedStyle ? getComputedStyle(el) : null;
      return Boolean(st && st.position === 'fixed' && st.display !== 'none' && st.visibility !== 'hidden');
    } catch (_e) { return true; }
  }).slice(0, maxN);
  const elements = nodes.map((el, i) => {
    const tag = (el.tagName || 'div').toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : (tag === 'button' ? 'button' : (tag === 'input' || tag === 'textarea' ? 'textbox' : tag)));
    const lbl = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || el.name || el.id || '').trim().slice(0, 80);
    return {
      ref: 'e' + (i + 1),
      tagRef: '[e' + (i + 1) + ']',
      tag,
      role,
      id: el.id || '',
      name: el.name || '',
      label: lbl,
      href: el.href || '',
      value: (el.value !== undefined && el.value !== null) ? String(el.value).slice(0, 80) : '',
      placeholder: el.placeholder || '',
      selector: el.id ? ('#' + el.id) : (el.name ? (tag + '[name="' + el.name + '"]') : tag)
    };
  });
  return {
    url: String(location.href || ''),
    title: String(document.title || ''),
    text: String((document.body && document.body.innerText) || '').slice(0, 4000),
    elements
  };
})()"""


def cdp_snapshot(
    *,
    ws_url: str = "",
    port: int | None = None,
    max_elements: int = 40,
    timeout: float = 4.0,
) -> dict[str, Any]:
    import web_tools

    lim = max(1, min(60, int(max_elements)))
    ev = cdp_evaluate(_SNAPSHOT_JS % lim, ws_url=ws_url, port=port, timeout=timeout)
    data = ev.get("value") if isinstance(ev.get("value"), dict) else {}
    cur_url = str(data.get("url") or "about:blank")
    cur_title = str(data.get("title") or "CDP Page")
    raw_text = str(data.get("text") or "")
    elems = [e for e in (data.get("elements") or []) if isinstance(e, dict)][:lim]
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()][:35]
    snap_lines = web_tools._format_dom_snapshot_lines(elems, cur_url)
    state = {
        "url": cur_url,
        "title": cur_title,
        "headings": lines[:6],
        "lines": lines,
        "elements": elems,
        "domSnapshot": snap_lines,
        "formValues": {},
        "cdpConnected": True,
    }
    web_tools._save_browser_dom_state(state)
    return state


def cdp_interact(
    action: str,
    target: str,
    value: str = "",
    *,
    ws_url: str = "",
    port: int | None = None,
    timeout: float = 4.0,
) -> dict[str, Any]:
    act = str(action or "click").strip().lower()
    tgt = str(target or "").strip()
    val = str(value or "")
    js = f"""(() => {{
  const act = {json.dumps(act)};
  const tgt = {json.dumps(tgt)};
  const val = {json.dumps(val)};
  const nodes = Array.from(document.querySelectorAll({json.dumps(_INTERACTIVE_SELECTOR)})).filter(el => {{
    try {{
      if (el.disabled || el.type === 'hidden') return false;
      if (el.offsetParent !== null) return true;
      const st = window.getComputedStyle ? getComputedStyle(el) : null;
      return Boolean(st && st.position === 'fixed' && st.display !== 'none' && st.visibility !== 'hidden');
    }} catch (_e) {{ return true; }}
  }}).slice(0, 60);
  let el = null;
  const mRef = tgt.replace(/^\\[?#?/, '').replace(/\\]$/, '').toLowerCase();
  const refIdx = mRef.startsWith('e') ? parseInt(mRef.slice(1), 10) : (/^\\d+$/.test(mRef) ? parseInt(mRef, 10) : NaN);
  if (!Number.isNaN(refIdx) && refIdx >= 1 && refIdx <= nodes.length) el = nodes[refIdx - 1];
  if (!el && tgt) {{ try {{ el = document.querySelector(tgt); }} catch (_e) {{}} }}
  if (!el && tgt) {{
    const low = tgt.toLowerCase();
    el = nodes.find(n => ((n.getAttribute('aria-label') || n.innerText || n.name || n.id || n.placeholder || '').toLowerCase().includes(low)));
  }}
  if (!el) return {{ ok: false, error: 'element_not_found' }};
  if (act === 'fill' || act === 'type' || act === 'select') {{
    el.focus && el.focus();
    const tag = (el.tagName || '').toLowerCase();
    if (el.isContentEditable) {{
      el.textContent = val;
    }} else {{
      const proto = tag === 'textarea'
        ? window.HTMLTextAreaElement && window.HTMLTextAreaElement.prototype
        : (tag === 'select'
            ? window.HTMLSelectElement && window.HTMLSelectElement.prototype
            : window.HTMLInputElement && window.HTMLInputElement.prototype);
      const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && typeof desc.set === 'function') desc.set.call(el, val);
      else el.value = val;
    }}
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }} else {{
    el.click && el.click();
  }}
  return {{
    ok: true,
    url: String(location.href || ''),
    tag: (el.tagName || '').toLowerCase(),
    label: String(el.getAttribute('aria-label') || el.innerText || el.id || el.name || tgt).trim().slice(0, 80)
  }};
}})()"""
    ev = cdp_evaluate(js, ws_url=ws_url, port=port, timeout=timeout)
    res = ev.get("value") if isinstance(ev.get("value"), dict) else {}
    ok_res = bool(res.get("ok"))
    return {
        "ok": ok_res,
        "action": act,
        "target": tgt,
        "value": val,
        "url": str(res.get("url") or ""),
        "cdpConnected": True,
        "element": {"label": str(res.get("label") or tgt), "tag": str(res.get("tag") or "")},
        "summary": (
            f"CDP {act} on '{res.get('label') or tgt}'"
            if ok_res
            else f"CDP {act} could not find element '{tgt}'."
        ),
    }


def cdp_list_network(
    *,
    ws_url: str = "",
    port: int | None = None,
    timeout: float = 4.0,
) -> dict[str, Any]:
    js = """(() => performance.getEntriesByType('resource').slice(-40).map(e => ({
  name: String(e.name || ''),
  initiatorType: String(e.initiatorType || ''),
  durationMs: Math.round(Number(e.duration || 0)),
  transferSize: Number(e.transferSize || 0)
})))()"""
    ev = cdp_evaluate(js, ws_url=ws_url, port=port, timeout=timeout)
    reqs = ev.get("value") if isinstance(ev.get("value"), list) else []
    return {
        "ok": True,
        "cdpConnected": True,
        "requests": reqs,
        "count": len(reqs),
        "lines": [f"{r.get('initiatorType','req')}: {r.get('name','')} ({r.get('durationMs',0)}ms)" for r in reqs[:15]],
    }
