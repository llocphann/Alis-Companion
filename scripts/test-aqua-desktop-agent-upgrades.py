#!/usr/bin/env python3
"""Regression & integration test suite for the 4 Alis-Companion Desktop Agent pillars.

100% offline, silent, zero-focus, zero-PIL, using isolated XDG dirs and
ephemeral loopback mock HTTP/WebSocket servers cleaned up in finally blocks.
"""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

COMPANION_ROOT = Path(__file__).resolve().parents[1]
AQUA_DIR = COMPANION_ROOT / "scripts" / "aqua"
if str(AQUA_DIR) not in sys.path:
    sys.path.insert(0, str(AQUA_DIR))

_VALID_WAV_BYTES = (
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x40\x00\x00\x00"
    + (b"\x01\x00" * 32)
)


class _IsolatedEnv:
    def __init__(self, *, isolated_flag: str = "1"):
        self._isolated_flag = isolated_flag
        self._tmp = None
        self._saved_env: dict[str, str | None] = {}

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="aqua-upgrades-")
        root = Path(self._tmp.name)
        home = root / "home"
        state = root / "state"
        config = root / "config"
        data = root / "data"
        bin_dir = root / "bin"
        for d in (home, state, config, data, bin_dir):
            d.mkdir(parents=True, exist_ok=True)
        updates = {
            "HOME": str(home),
            "XDG_STATE_HOME": str(state),
            "XDG_CONFIG_HOME": str(config),
            "XDG_DATA_HOME": str(data),
            "ALIS_AQUA_STATE_DIR": str(state / "alis-companion"),
            "ALIS_TEST_ISOLATED": self._isolated_flag,
            "ALIS_AQUA_TEST_NO_SPAWN": self._isolated_flag,
            "AQUA_TEST_ENDPOINT": "",
            "AQUA_TEST_VOICE_TEXT": "",
            "ALIS_TEST_EMBED_ENDPOINT": "",
            "ALIS_TEST_CDP_PORT": "",
            "ALIS_LOCAL_STT_ENDPOINT": "",
            "ALIS_AQUA_SCREEN_FIXTURE": "",
        }
        for k, v in updates.items():
            self._saved_env[k] = os.environ.get(k)
            if v == "":
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return root

    def __exit__(self, *_exc) -> None:
        for k, prev in self._saved_env.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev
        if self._tmp is not None:
            self._tmp.cleanup()


def _write_exec(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -e\n" + body.lstrip(), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


class TestPillar1LiveGuiAndObserve(unittest.TestCase):
    def test_live_gui_disabled_refusal_and_enabled_scroll_drag(self) -> None:
        with _IsolatedEnv(isolated_flag="0") as root:
            import mcp_client
            import native_host_bridge
            import screen_context

            fake_obs = {
                "packageId": "org.kde.dolphin",
                "version": "niri+atspi",
                "headings": ["Dolphin"],
                "elements": [
                    {"markId": 1, "ref": "@e1", "role": "push button", "name": "Save", "x": 120, "y": 80, "width": 80, "height": 30},
                ],
                "observation": {"appId": "org.kde.dolphin", "windowId": 101, "title": "Dolphin", "source": "niri+atspi", "elements": []},
            }
            with mock.patch.object(screen_context, "observe_active_window", return_value=fake_obs):
                refused = screen_context.perform_gui_action(
                    "click", x=50, y=60, allow_live_input=False
                )
                self.assertFalse(refused["ok"])
                self.assertEqual(refused.get("code"), "live_gui_control_disabled")
                self.assertFalse(refused["verified"])

                mcp_refused = mcp_client.call_tool(
                    "computer-use-linux",
                    "mouse_scroll",
                    {"x": 120, "y": 80, "delta": -4, "axis": "vertical", "allowLiveInput": False},
                    companion_root=COMPANION_ROOT,
                )
                self.assertTrue(mcp_refused["isError"])

                with (
                    mock.patch.object(native_host_bridge, "input_pointer_scroll", return_value=(True, "alis-inputd")) as m_sc,
                    mock.patch.object(native_host_bridge, "input_pointer_drag", return_value=(True, "alis-inputd")) as m_dr,
                    mock.patch.object(native_host_bridge, "input_pointer_click", return_value=(True, "alis-inputd")) as m_cl,
                ):
                    sc = screen_context.perform_gui_action("scroll", delta=-3, axis="vertical", allow_live_input=True)
                    self.assertTrue(sc["ok"])
                    self.assertEqual(sc["backend"], "alis-inputd")
                    m_sc.assert_called_once()

                    dr = screen_context.perform_gui_action("drag", x=10, y=20, x2=200, y2=250, allow_live_input=True)
                    self.assertTrue(dr["ok"])
                    m_dr.assert_called_once()

                    dc = mcp_client.call_tool(
                        "computer-use-linux",
                        "mouse_click",
                        {"x": 120, "y": 80, "double": True, "allowLiveInput": True},
                        companion_root=COMPANION_ROOT,
                    )
                    self.assertFalse(dc["isError"])
                    m_cl.assert_called_with(120, 80, button="left", double=True)

                    rc = screen_context.perform_gui_action("right_click", x=30, y=40, allow_live_input=True)
                    self.assertTrue(rc["ok"])
                    m_cl.assert_called_with(30, 40, button="right", double=False)

    def test_screen_observe_schema_and_react_cap(self) -> None:
        with _IsolatedEnv(isolated_flag="1") as root:
            import agent_runtime
            import agent_tool_schemas

            names = {s["function"]["name"] for s in agent_tool_schemas.AGENT_TOOL_SCHEMAS}
            self.assertIn("screen_observe", names)
            self.assertIn("desktop_schedule", names)
            dc_schema = next(s["function"]["parameters"]["properties"] for s in agent_tool_schemas.AGENT_TOOL_SCHEMAS if s["function"]["name"] == "desktop_control")
            for act in ("scroll", "drag", "double_click", "bluetooth_status", "wifi_status", "audio_devices", "mic_mute", "power_profile", "session_power"):
                self.assertIn(act, dc_schema["action"]["enum"])

            fix_path = root / "screen_obs.json"
            fix_path.write_text(
                json.dumps({
                    "appId": "firefox",
                    "windowId": 77,
                    "title": "Docs",
                    "elements": [{"markId": 1, "ref": "@e1", "role": "push button", "name": "Next", "x": 40, "y": 50, "width": 60, "height": 28}],
                }),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"ALIS_AQUA_SCREEN_FIXTURE": str(fix_path)}):
                sctx = agent_runtime.load_session_context()
                out = agent_runtime._execute_llm_tool_call(
                    {"name": "screen_observe", "args": {"allowScreenshot": False}},
                    sctx,
                    COMPANION_ROOT,
                    int(time.time() * 1000),
                )
                self.assertIn("@e1", out)
                self.assertIn("Next", out)


class TestPillar2HardwareAndScheduler(unittest.TestCase):
    def test_hardware_session_controls_and_confirmation_gate(self) -> None:
        with _IsolatedEnv(isolated_flag="1") as root:
            import agent_runtime
            import desktop_control

            bin_dir = root / "bin"
            # Safe /tmp mock binaries only
            _write_exec(
                bin_dir / "bluetoothctl",
                """
if [ "$1" = "show" ]; then
  echo "Controller AA:BB:CC:DD:EE:FF (public)"
  echo "  Powered: yes"
elif [ "$1" = "devices" ] && [ "$2" = "Connected" ]; then
  echo "Device 11:22:33:44:55:66 Sony WH-1000XM5"
elif [ "$1" = "devices" ]; then
  echo "Device 11:22:33:44:55:66 Sony WH-1000XM5"
elif [ "$1" = "power" ]; then
  echo "Changing power $2 succeeded"
elif [ "$1" = "connect" ] || [ "$1" = "disconnect" ]; then
  echo "Connection successful ($1 $2)"
fi
""",
            )
            _write_exec(
                bin_dir / "nmcli",
                """
if [ "$1" = "radio" ] && [ "$2" = "wifi" ] && [ -z "$3" ]; then
  echo "enabled"
elif [ "$1" = "radio" ] && [ "$2" = "wifi" ]; then
  echo "wifi $3 ok"
elif [ "$1" = "-t" ] && [ "$3" = "ACTIVE,SSID,SIGNAL" ]; then
  echo "yes:HomeNet:88"
elif [ "$1" = "-t" ]; then
  echo "*:HomeNet:88:WPA2"
  echo " :OfficeNet:64:WPA2"
else
  read -r _pw || true
  echo "Device successfully activated"
fi
""",
            )
            _write_exec(
                bin_dir / "wpctl",
                """
if [ "$1" = "status" ]; then
  cat <<'EOF'
Audio
 ├─ Sinks:
 │  *   58. Builtin Speaker   [vol: 0.40]
 │      61. USB DAC Headphones [vol: 0.60]
 ├─ Sources:
 │  *   60. Builtin Mic       [vol: 1.00 MUTED]
Video
EOF
elif [ "$1" = "set-default" ]; then
  echo "default set $2"
elif [ "$1" = "set-mute" ]; then
  echo "muted updated"
elif [ "$1" = "get-volume" ]; then
  echo "Volume: 1.00 [MUTED]"
fi
""",
            )
            _write_exec(
                bin_dir / "gdbus",
                """
echo "(<'balanced'>,)"
""",
            )
            _write_exec(bin_dir / "loginctl", 'echo "locked-session-mock"\n')

            with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}):
                bt = desktop_control.manage_desktop("bluetooth_status")
                self.assertTrue(bt["ok"])
                self.assertTrue(bt["powered"])
                self.assertEqual(len(bt["connectedDevices"]), 1)

                btc = desktop_control.manage_desktop("bluetooth_connect", target="Sony WH-1000XM5")
                self.assertTrue(btc["ok"])
                self.assertEqual(btc["mac"], "11:22:33:44:55:66")

                with self.assertRaises(desktop_control.DesktopControlError):
                    desktop_control.manage_desktop("bluetooth_connect", target="--evil-flag")

                wf = desktop_control.manage_desktop("wifi_connect", target="HomeNet", value="TopSecretPassphrase99!")
                self.assertTrue(wf["ok"])
                self.assertNotIn("TopSecretPassphrase99!", wf["summary"])
                self.assertNotIn("TopSecretPassphrase99!", "\n".join(wf["lines"]))

                au = desktop_control.manage_desktop("audio_devices")
                self.assertTrue(au["ok"])
                self.assertEqual(len(au["sinks"]), 2)
                au_set = desktop_control.manage_desktop("audio_set_sink", target="USB DAC")
                self.assertTrue(au_set["ok"])
                self.assertEqual(au_set["deviceId"], "61")

                mic = desktop_control.manage_desktop("mic_mute", value="mute")
                self.assertTrue(mic["ok"])
                self.assertTrue(mic["muted"])

                pp = desktop_control.manage_desktop("power_profile", value="get")
                self.assertTrue(pp["ok"])
                self.assertEqual(pp["profile"], "balanced")

                with self.assertRaises(desktop_control.DesktopControlError):
                    desktop_control.manage_desktop("session_power", value="shutdown")

                # NL "lock screen" MUST gate behind confirmation + A2UI Confirmation Card
                turn = agent_runtime.execute_agent_turn(
                    {"prompt": "lock screen", "a2uiEnabled": True, "persistHistory": False},
                    companion_root=COMPANION_ROOT,
                    host_root=COMPANION_ROOT.parent / "Alis",
                )
                self.assertTrue(turn["requiresConfirmation"])
                self.assertTrue(turn["confirmId"])
                self.assertIsInstance(turn.get("uiSurface"), dict)

                a2ui_res = agent_runtime.handle_a2ui_action(
                    {
                        "eventName": "confirm_action",
                        "surfaceId": turn["uiSurface"]["createSurface"]["surfaceId"],
                        "context": {"confirmId": turn["confirmId"], "decision": "confirm"},
                    },
                    companion_root=COMPANION_ROOT,
                    host_root=COMPANION_ROOT.parent / "Alis",
                )
                self.assertTrue(a2ui_res["ok"])
                self.assertEqual(a2ui_res["resolution"], "confirmed")

    def test_scheduler_store_process_watch_and_a2ui_cancel(self) -> None:
        with _IsolatedEnv(isolated_flag="1"):
            import a2ui_builder
            import agent_runtime
            import local_mind
            import scheduler_store

            now_ms = 1_795_400_000_000
            rem = scheduler_store.create_reminder("Stand up and stretch", delay_minutes=10, now_ms=now_ms)
            self.assertEqual(rem["kind"], "reminder")
            self.assertEqual(rem["status"], "active")

            rec_daily = scheduler_store.create_recurring("Morning brief", "daily@08:30", now_ms=now_ms)
            self.assertEqual(rec_daily["recurrence"], "daily@08:30")
            rec_weekly = scheduler_store.create_recurring("Weekly sync", "weekly@mon,wed@09:00", now_ms=now_ms)
            self.assertEqual(rec_weekly["recurrence"], "weekly@mon,wed@09:00")
            rec_every = scheduler_store.create_recurring("Hydrate", "every:10m", now_ms=now_ms)
            self.assertEqual(rec_every["recurrence"], "every:10m")
            with self.assertRaises(scheduler_store.SchedulerError):
                scheduler_store.create_recurring("Too fast", "every:2m", now_ms=now_ms)

            # Real short-lived subprocess watch
            proc = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                watch = scheduler_store.create_process_watch("Sleep watch", pid=proc.pid, now_ms=now_ms)
                before_poll = scheduler_store.poll_due_jobs(now_ms=now_ms + 1_000)
                self.assertEqual(len(before_poll["fired"]), 0)
            finally:
                proc.terminate()
                proc.wait(timeout=5)

            # Poll after process exit and past reminder+interval due times
            after_poll = scheduler_store.poll_due_jobs(now_ms=now_ms + 15 * 60_000)
            fired_ids = {j["id"] for j in after_poll["fired"]}
            self.assertIn(rem["id"], fired_ids)
            self.assertIn(rec_every["id"], fired_ids)
            self.assertIn(watch["id"], fired_ids)

            # Remaining active jobs should include recurring ones (with advanced dueMs)
            active = scheduler_store.list_jobs()
            every_after = next(j for j in active if j["id"] == rec_every["id"])
            self.assertGreater(every_after["dueMs"], now_ms + 15 * 60_000)

            # Schedule A2UI card + cancel_schedule action + generative boundary check
            surf = a2ui_builder.build_schedule_surface(active, language="en")
            self.assertIsNotNone(surf)
            self.assertLessEqual(len(json.dumps(surf).encode("utf-8")), 6144)

            cancel_res = agent_runtime.handle_a2ui_action(
                {"eventName": "cancel_schedule", "context": {"jobId": rec_daily["id"]}},
                companion_root=COMPANION_ROOT,
                host_root=COMPANION_ROOT.parent / "Alis",
            )
            self.assertTrue(cancel_res["ok"])
            self.assertEqual(cancel_res["job"]["status"], "cancelled")

            gen_blocked = agent_runtime.handle_a2ui_action(
                {"eventName": "cancel_schedule", "surfaceId": "surf_gen_evil", "context": {"jobId": rec_every["id"]}},
                companion_root=COMPANION_ROOT,
                host_root=COMPANION_ROOT.parent / "Alis",
            )
            self.assertFalse(gen_blocked["ok"])
            self.assertEqual(gen_blocked["error"], "privileged_event_forbidden:cancel_schedule")

            lm_list = local_mind.dispatch({"action": "scheduler_action", "op": "list"})
            self.assertTrue(lm_list["ok"])
            lm_poll = local_mind.dispatch({"action": "scheduler_action", "op": "poll_due"})
            self.assertTrue(lm_poll["ok"])
            guard_js = (COMPANION_ROOT / "services" / "AquaHelperReplyGuard.js").read_text(encoding="utf-8")
            self.assertIn('"scheduler_action"', guard_js)


class TestPillar3SemanticRagAndCdp(unittest.TestCase):
    def test_warm_semantic_hybrid_rag_zero_lexical_overlap(self) -> None:
        def _vec_for(text: str) -> list[float]:
            low = text.lower()
            # Map unrelated lexical words to identical semantic axes!
            canine = 1.0 if any(w in low for w in ("golden", "retriever", "grooming", "canine", "puppy")) else 0.05
            finance = 1.0 if any(w in low for w in ("budget", "ledger", "invoice", "quarterly")) else 0.05
            return [canine, finance, 0.05, 0.05]

        class _EmbedHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                inputs = req.get("input") or []
                if isinstance(inputs, str):
                    inputs = [inputs]
                data = [{"index": idx, "embedding": _vec_for(t)} for idx, t in enumerate(inputs)]
                body = json.dumps({"data": data}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _EmbedHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with _IsolatedEnv(isolated_flag="1") as root:
                import knowledge_index
                import nexus_rag
                import rag_store

                ep = f"http://127.0.0.1:{srv.server_address[1]}/v1/embeddings"
                knowledge_index._WARM_EMBED_CACHE.clear()
                with mock.patch.dict(os.environ, {"ALIS_TEST_EMBED_ENDPOINT": ep, "ALIS_TEST_EMBED_MODEL": "test-bge-small"}):
                    ep1, m1 = knowledge_index.probe_warm_embedding_model()
                    ep2, m2 = knowledge_index.probe_warm_embedding_model()
                    self.assertEqual((ep1, m1), (ep, "test-bge-small"))
                    self.assertEqual((ep1, m1), (ep2, m2))

                    # 1. Nexus RAG recall with zero lexical overlap ("canine" vs "Golden Retriever")
                    vault = root / "nexus_vault"
                    (vault / "Pets").mkdir(parents=True)
                    (vault / "Finance").mkdir(parents=True)
                    (vault / "Pets" / "dog_care.md").write_text(
                        "---\ntitle: Dog Manual\ntags: [animal]\n---\n# Bathing\nBrush your golden retriever coat weekly.\n",
                        encoding="utf-8",
                    )
                    (vault / "Finance" / "ledger.md").write_text(
                        "# Invoice Summary\nQuarterly budget ledger reconciliation.\n",
                        encoding="utf-8",
                    )
                    nx = nexus_rag.search_nexus_knowledge(
                        "canine puppy guide",
                        vault_path=str(vault),
                        top_k=3,
                        include_collections=False,
                    )
                    self.assertTrue(nx.get("semanticUsed"))
                    self.assertEqual(nx.get("embedModel"), "test-bge-small")
                    self.assertTrue(nx["citations"])
                    self.assertEqual(nx["citations"][0]["relPath"], "Pets/dog_care.md")

                    # Check nexus_embeddings float16 persistence
                    conn = sqlite3.connect(nx["dbPath"])
                    cnt = conn.execute("SELECT COUNT(*) FROM nexus_embeddings").fetchone()[0]
                    conn.close()
                    self.assertGreaterEqual(cnt, 2)

                    # 2. rag_store.query_hybrid_rag recall with zero lexical overlap
                    chunks = [
                        {"source": "doc_finance.md", "heading": "Budget", "text": "Quarterly invoice and ledger sheet."},
                        {"source": "doc_pets.md", "heading": "Grooming", "text": "Brush golden retriever fur carefully."},
                    ]
                    ranked = rag_store.query_hybrid_rag("canine wellness", chunks)
                    self.assertTrue(ranked)
                    self.assertEqual(ranked[0][1]["source"], "doc_pets.md")

                    conn2 = sqlite3.connect(str(rag_store._rag_db_path()))
                    cnt2 = conn2.execute("SELECT COUNT(*) FROM rag_embeddings").fetchone()[0]
                    conn2.close()
                    self.assertGreaterEqual(cnt2, 2)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_cdp_rfc6455_client_and_honest_live_browser_errors(self) -> None:
        ws_magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        state_box = {"url": "https://example.org/", "navCount": 0, "evals": []}
        server_port_holder = {"port": 0}

        class _CdpHandler(socketserver.StreamRequestHandler):
            def handle(self):
                buf = bytearray()
                while b"\r\n\r\n" not in buf:
                    chunk = self.connection.recv(4096)
                    if not chunk:
                        return
                    buf.extend(chunk)
                end = buf.find(b"\r\n\r\n")
                hdr_txt = bytes(buf[:end]).decode("latin-1")
                del buf[: end + 4]
                lines = hdr_txt.split("\r\n")
                req_line = lines[0]
                if "GET /json/list " in req_line:
                    port = server_port_holder["port"]
                    payload = json.dumps([
                        {
                            "id": "page-1",
                            "type": "page",
                            "title": "Mock Page",
                            "url": state_box["url"],
                            "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/1",
                        }
                    ]).encode("utf-8")
                    self.connection.sendall(
                        f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode("ascii")
                        + payload
                    )
                    return
                if "Upgrade: websocket" in hdr_txt or "upgrade: websocket" in hdr_txt:
                    ws_key = ""
                    for ln in lines[1:]:
                        if ln.lower().startswith("sec-websocket-key:"):
                            ws_key = ln.split(":", 1)[1].strip()
                    accept = base64.b64encode(hashlib.sha1((ws_key + ws_magic).encode("ascii")).digest()).decode("ascii")
                    self.connection.sendall(
                        (
                            "HTTP/1.1 101 Switching Protocols\r\n"
                            "Upgrade: websocket\r\n"
                            "Connection: Upgrade\r\n"
                            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                        ).encode("ascii")
                    )

                    def recv_exact(n: int) -> bytes:
                        while len(buf) < n:
                            c = self.connection.recv(4096)
                            if not c:
                                raise EOFError()
                            buf.extend(c)
                        out = bytes(buf[:n])
                        del buf[:n]
                        return out

                    while True:
                        try:
                            b0, b1 = recv_exact(2)
                        except EOFError:
                            return
                        opcode = b0 & 0x0F
                        if opcode == 0x8:
                            return
                        masked = bool(b1 & 0x80)
                        length = b1 & 0x7F
                        if length == 126:
                            length = struct.unpack("!H", recv_exact(2))[0]
                        elif length == 127:
                            length = struct.unpack("!Q", recv_exact(8))[0]
                        mkey = recv_exact(4) if masked else b""
                        raw = recv_exact(length)
                        if masked:
                            raw = bytes(b ^ mkey[i & 3] for i, b in enumerate(raw))
                        req = json.loads(raw.decode("utf-8"))
                        mid = req["id"]
                        method = req["method"]
                        params = req.get("params") or {}
                        if method == "Page.navigate":
                            state_box["url"] = str(params.get("url") or state_box["url"])
                            state_box["navCount"] += 1
                            res_obj = {"frameId": "main-frame"}
                        elif method == "Runtime.evaluate":
                            expr = str(params.get("expression") or "")
                            state_box["evals"].append(expr[:80])
                            if "performance.getEntriesByType" in expr:
                                val = [{"name": f"{state_box['url']}app.js", "initiatorType": "script", "durationMs": 12, "transferSize": 2048}]
                            elif "el.click" in expr:
                                val = {"ok": True, "url": state_box["url"], "tag": "button", "label": "Submit Order"}
                            elif "elements =" in expr:
                                val = {
                                    "url": state_box["url"],
                                    "title": "Live CDP Example",
                                    "text": "Hello from CDP",
                                    "elements": [{"ref": "e1", "tagRef": "[e1]", "tag": "button", "role": "button", "id": "submit", "name": "", "label": "Submit Order", "href": "", "value": "", "placeholder": "", "selector": "#submit"}],
                                }
                            else:
                                val = {"answer": 42}
                            res_obj = {"result": {"type": "object", "value": val}}
                        else:
                            res_obj = {}
                        resp_bytes = json.dumps({"id": mid, "result": res_obj}).encode("utf-8")
                        hdr = bytearray([0x81])
                        if len(resp_bytes) < 126:
                            hdr.append(len(resp_bytes))
                        else:
                            hdr.append(126)
                            hdr.extend(struct.pack("!H", len(resp_bytes)))
                        self.connection.sendall(bytes(hdr) + resp_bytes)

        class _ReuseThreadingTCPServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        cdp_srv = _ReuseThreadingTCPServer(("127.0.0.1", 0), _CdpHandler)
        server_port_holder["port"] = cdp_srv.server_address[1]
        t = threading.Thread(target=cdp_srv.serve_forever, daemon=True)
        t.start()
        try:
            with _IsolatedEnv(isolated_flag="1"):
                import cdp_client
                import mcp_client
                import native_host_bridge
                import web_tools

                with mock.patch.dict(os.environ, {"ALIS_TEST_CDP_PORT": str(cdp_srv.server_address[1])}):
                    targets = cdp_client.probe_cdp_targets()
                    self.assertEqual(len(targets), 1)
                    nav = cdp_client.cdp_navigate(f"http://127.0.0.1:{cdp_srv.server_address[1]}/page")
                    self.assertTrue(nav["ok"])
                    with self.assertRaises(cdp_client.CdpError):
                        cdp_client.cdp_navigate("http://169.254.169.254/latest/meta-data/")

                    snap = mcp_client.call_tool("playwright-mcp", "browser_snapshot", {}, companion_root=COMPANION_ROOT)
                    self.assertFalse(snap["isError"])
                    self.assertTrue(snap["structured"].get("cdpConnected"))

                    clk = mcp_client.call_tool("playwright-mcp", "browser_click", {"element": "[e1]"}, companion_root=COMPANION_ROOT)
                    self.assertFalse(clk["isError"])
                    self.assertTrue(clk["structured"].get("cdpConnected"))

                    net = mcp_client.call_tool("chrome-devtools-mcp", "list_network_requests", {}, companion_root=COMPANION_ROOT)
                    self.assertFalse(net["isError"])
                    self.assertFalse(net["structured"]["simulated"])
                    self.assertEqual(net["structured"]["count"], 1)

                    ev = mcp_client.call_tool("chrome-devtools-mcp", "evaluate_script", {"expression": "1+41"}, companion_root=COMPANION_ROOT)
                    self.assertFalse(ev["isError"])
                    self.assertEqual(ev["structured"]["value"], {"answer": 42})

                # Honest live failure when CDP unreachable and element missing in non-isolated runtime
                with (
                    mock.patch.object(native_host_bridge, "is_test_or_isolated_env", return_value=False),
                    mock.patch.object(cdp_client, "probe_cdp_targets", return_value=[]),
                ):
                    with self.assertRaises(web_tools.WebToolError):
                        web_tools.interact_dom_element("click", target="#missing-live-btn")
                    live_err = mcp_client.call_tool("playwright-mcp", "browser_click", {"element": "#missing-live-btn"}, companion_root=COMPANION_ROOT)
                    self.assertTrue(live_err["isError"])
                    dev_err = mcp_client.call_tool("chrome-devtools-mcp", "list_network_requests", {}, companion_root=COMPANION_ROOT)
                    self.assertTrue(dev_err["isError"])
        finally:
            cdp_srv.shutdown()
            cdp_srv.server_close()


class TestPillar4OfflineSttAndModularRuntime(unittest.TestCase):
    def test_local_http_stt_diagnostics_and_reexports(self) -> None:
        seen_paths: list[str] = []

        class _SttHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                seen_paths.append(self.path)
                if self.path == "/health":
                    body = b'{"status":"ok"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                seen_paths.append(self.path)
                self.rfile_last = raw
                body = json.dumps({"text": "offline loopback transcription works"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _SttHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with _IsolatedEnv(isolated_flag="1") as root:
                import a2ui_actions
                import agent_runtime
                import agent_tool_schemas
                import companion_daemon
                import desktop_control
                import local_mind

                wav_p = root / "state" / "alis-companion" / "voice" / "sample.wav"
                wav_p.parent.mkdir(parents=True, exist_ok=True)
                wav_p.write_bytes(_VALID_WAV_BYTES)

                base_url = f"http://127.0.0.1:{srv.server_address[1]}"
                st = desktop_control.voice_status(local_stt_endpoint=base_url)
                self.assertTrue(st["offlineSttReady"])
                self.assertEqual(st["offlineSttBackend"], "local-http-stt")
                self.assertIn("cloudSttAvailable", st)
                self.assertIn("ttsReady", st)
                self.assertIn("installHint", st)

                stt_res = desktop_control.voice_stt(
                    audio_path=str(wav_p),
                    local_stt_endpoint=base_url,
                    local_only=True,
                )
                self.assertTrue(stt_res["ok"])
                self.assertEqual(stt_res["sttBackend"], "local-http-stt")
                self.assertEqual(stt_res["text"], "offline loopback transcription works")

                with self.assertRaises(desktop_control.DesktopControlError):
                    desktop_control.voice_stt(
                        audio_path=str(wav_p),
                        local_stt_endpoint="https://evil.example.com/v1/audio/transcriptions",
                        local_only=True,
                    )

                lm_status = local_mind.dispatch({"action": "agent_status", "localSttEndpoint": base_url})
                self.assertIn("voiceStatus", lm_status)
                self.assertTrue(lm_status["voiceStatus"]["offlineSttReady"])

                # Modular extraction & backwards-compatible re-exports + prewarm list
                self.assertIs(agent_runtime.handle_a2ui_action, a2ui_actions.handle_a2ui_action)
                self.assertIs(agent_runtime.AGENT_TOOL_SCHEMAS, agent_tool_schemas.AGENT_TOOL_SCHEMAS)
                self.assertIs(agent_runtime.CORE_AGENT_TOOL_NAMES, agent_tool_schemas.CORE_AGENT_TOOL_NAMES)
                self.assertIs(agent_runtime.search_agent_tool_schemas, agent_tool_schemas.search_agent_tool_schemas)
                prewarmed = set(companion_daemon._prewarm_modules())
                for mod in ("agent_tool_schemas", "scheduler_store", "cdp_client", "a2ui_actions"):
                    self.assertIn(mod, prewarmed)
        finally:
            srv.shutdown()
            srv.server_close()


class TestP2SecurityDaemonAndUxUpgrades(unittest.TestCase):
    def test_security_gates_allowliveinput_taint_and_confirm_failure(self) -> None:
        with _IsolatedEnv(isolated_flag="1") as root:
            import a2ui_actions
            import agent_runtime
            import desktop_control
            import mcp_client

            cul_tools = {t["name"]: t for t in mcp_client.CURATED_TOOLS["computer-use-linux"]}
            for name in ("mouse_scroll", "mouse_drag"):
                self.assertNotIn("allowLiveInput", cul_tools[name]["inputSchema"]["properties"])

            self.assertTrue(
                agent_runtime._is_privileged_taint_sink(
                    "desktop_control", {"action": "bluetooth_toggle", "value": "off"}
                )
            )

            seen_live_flags: list[bool] = []

            def _fake_call_mcp(server_name, tool_name, arguments=None, **_kwargs):
                args_d = dict(arguments or {})
                seen_live_flags.append(bool(args_d.get("allowLiveInput")))
                return {
                    "ok": True,
                    "server": server_name,
                    "tool": tool_name,
                    "content": [{"type": "text", "text": "ok"}],
                    "structured": {},
                }

            tctx = agent_runtime._tctx()
            prev_live = tctx.live_gui_control
            try:
                tctx.live_gui_control = False
                with mock.patch.object(mcp_client, "call_tool", side_effect=_fake_call_mcp):
                    agent_runtime._execute_llm_tool_call(
                        {
                            "function": {
                                "name": "mcp_call",
                                "arguments": json.dumps(
                                    {
                                        "server": "computer-use-linux",
                                        "tool": "mouse_scroll",
                                        "arguments": {"delta": 3, "allowLiveInput": True},
                                    }
                                ),
                            }
                        },
                        {"sessionId": "chat_test_sec"},
                        COMPANION_ROOT,
                        1_700_000_000_000,
                    )
                    tctx.live_gui_control = True
                    agent_runtime._execute_llm_tool_call(
                        {
                            "function": {
                                "name": "mcp_call",
                                "arguments": json.dumps(
                                    {
                                        "server": "computer-use-linux",
                                        "tool": "mouse_drag",
                                        "arguments": {"x": 10, "y": 10, "x2": 30, "y2": 30, "allowLiveInput": False},
                                    }
                                ),
                            }
                        },
                        {"sessionId": "chat_test_sec"},
                        COMPANION_ROOT,
                        1_700_000_001_000,
                    )
                self.assertEqual(seen_live_flags, [False, True])

                # Turning BT / Wi-Fi off via desktop_control tool must gate behind confirmation
                with mock.patch.object(desktop_control, "manage_desktop") as m_desk:
                    bt_out = agent_runtime._execute_llm_tool_call(
                        {"function": {"name": "desktop_control", "arguments": json.dumps({"action": "bluetooth_toggle", "value": "off"})}},
                        {"sessionId": "chat_test_sec"},
                        COMPANION_ROOT,
                        1_700_000_002_000,
                    )
                    wifi_out = agent_runtime._execute_llm_tool_call(
                        {"function": {"name": "desktop_control", "arguments": json.dumps({"action": "wifi_toggle", "value": "off"})}},
                        {"sessionId": "chat_test_sec"},
                        COMPANION_ROOT,
                        1_700_000_003_000,
                    )
                    m_desk.assert_not_called()
                self.assertIn("Confirmation required", bt_out)
                self.assertIn("Confirmation required", wifi_out)

                # Failed tainted escalation confirm reports failure (never silently succeeds)
                fresh_now = int(time.time() * 1000)
                conf_fail = agent_runtime.create_pending_confirmation(
                    "tainted_tool_escalation",
                    "run_shell",
                    "Confirm run_shell",
                    {"tool": "run_shell", "args": {"command": "false"}, "taintSources": ["web_search"]},
                    now_ms=fresh_now,
                )
                with mock.patch.object(agent_runtime, "_execute_llm_tool_call", return_value="[run_shell error: exit code 1]"):
                    fail_res = a2ui_actions.handle_a2ui_action(
                        {
                            "eventName": "confirm_action",
                            "surfaceId": "surf_confirm_fail",
                            "context": {"confirmId": conf_fail["confirmId"], "decision": "confirm", "sessionId": "chat_test_sec"},
                        },
                        companion_root=COMPANION_ROOT,
                        host_root=COMPANION_ROOT.parent / "Alis",
                    )
                self.assertFalse(fail_res["ok"])
                self.assertEqual(fail_res["resolution"], "failed")
                self.assertEqual(fail_res["expression"], "sad")

                # Confirmed live GUI escalation grants 5m guiApprovedUntilMs
                conf_gui = agent_runtime.create_pending_confirmation(
                    "tainted_tool_escalation",
                    "gui_action",
                    "Confirm gui_action",
                    {"tool": "gui_action", "args": {"action": "click", "x": 40, "y": 50}, "taintSources": ["screen"]},
                    now_ms=fresh_now + 100,
                )
                with mock.patch.object(agent_runtime, "_execute_llm_tool_call", return_value='{"ok": true}'):
                    ok_res = a2ui_actions.handle_a2ui_action(
                        {
                            "eventName": "confirm_action",
                            "surfaceId": "surf_confirm_gui",
                            "sessionId": "chat_test_sec",
                            "liveGuiControl": True,
                            "context": {"confirmId": conf_gui["confirmId"], "decision": "confirm", "sessionId": "chat_test_sec"},
                        },
                        companion_root=COMPANION_ROOT,
                        host_root=COMPANION_ROOT.parent / "Alis",
                    )
                self.assertTrue(ok_res["ok"])
                self.assertEqual(ok_res["resolution"], "confirmed")
                ctx_after = agent_runtime.load_session_context()
                self.assertGreater(int(ctx_after.get("guiApprovedUntilMs") or 0), fresh_now)
            finally:
                tctx.live_gui_control = prev_live

    def test_lock_tts_embeddings_and_daemon_parity(self) -> None:
        lm_unload_calls: list[str] = []

        class _LmStudioHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                if self.path == "/api/v0/models":
                    payload = {
                        "data": [
                            {"id": "qwen2.5-7b", "type": "llm", "state": "loaded"},
                            {"id": "nomic-embed-text", "type": "embeddings", "state": "loaded"},
                        ]
                    }
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                lm_unload_calls.append(str(req.get("instance_id") or req.get("model") or ""))
                body = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _LmStudioHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with _IsolatedEnv(isolated_flag="1") as root:
                import companion_daemon
                import desktop_control
                import local_mind

                # session_power lock tries alis ipc lock activate first
                seen_cmds: list[list[str]] = []
                alis_mock = root / "bin" / "alis"
                _write_exec(alis_mock, "exit 0\n")

                def _fake_which(name: str):
                    if name == "alis":
                        return str(alis_mock)
                    if name == "whisper-cli":
                        return str(root / "bin" / "whisper-cli")
                    return None

                orig_run = desktop_control.run_user_command

                def _spy_run(cmd, **kwargs):
                    seen_cmds.append(list(cmd))
                    return orig_run(cmd, **kwargs)

                with mock.patch("shutil.which", side_effect=_fake_which), mock.patch.object(
                    desktop_control, "run_user_command", side_effect=_spy_run
                ):
                    lock_res = desktop_control.manage_desktop("session_power", "", "lock")
                    self.assertTrue(lock_res["ok"], lock_res)
                    self.assertEqual(seen_cmds[0][:4], [str(alis_mock), "ipc", "lock", "activate"])

                    # whisper-cli without a .ggml model is NOT reported ready
                    with mock.patch.object(desktop_control, "_is_test_or_isolated_env", return_value=False), \
                         mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}, clear=False):
                        st = desktop_control.voice_status()
                    self.assertFalse(st["offlineSttReady"])
                    self.assertEqual(st["offlineSttBackend"], "none")
                    self.assertFalse(st["sttAvailable"])

                    # localOnly=True refuses falling back to edge-tts when offline engines are absent
                    tts_res = desktop_control.voice_tts("offline test", local_only=True)
                    self.assertFalse(tts_res["ok"])
                    self.assertEqual(tts_res["error"], "offline_tts_unavailable")
                    self.assertTrue(tts_res["localOnly"])

                # unload_llm_action skips loaded embeddings when includeEmbeddings is False
                base_url = f"http://127.0.0.1:{srv.server_address[1]}"
                lms_log = root / "lms_unloads.log"
                lms_mock = root / "bin" / "lms"
                _write_exec(lms_mock, f'echo "$*" >> "{lms_log}"\nexit 0\n')

                def _which_lms(name: str):
                    return str(lms_mock) if name == "lms" else None

                clean_env = {"AQUA_TEST_NO_EXTERNAL_EDITOR": "", "ALIS_TEST_HARNESS": "", "AQUA_TEST_ENDPOINT": "", "AI_FIXTURE_URL": ""}
                with mock.patch("shutil.which", side_effect=_which_lms), mock.patch.dict(os.environ, clean_env, clear=False):
                    unloaded = local_mind.unload_llm_action({"lmstudioUrl": base_url})
                    self.assertTrue(unloaded["unloaded"])
                    self.assertEqual(lms_log.read_text(encoding="utf-8").splitlines(), ["unload qwen2.5-7b"])

                    lms_log.write_text("", encoding="utf-8")
                    unloaded_all = local_mind.unload_llm_action(
                        {"lmstudioUrl": base_url, "includeEmbeddings": True}
                    )
                    self.assertTrue(unloaded_all["unloaded"])
                    self.assertEqual(
                        set(lms_log.read_text(encoding="utf-8").splitlines()),
                        {"unload qwen2.5-7b", "unload nomic-embed-text"},
                    )

                # Daemon sourceMtime mismatch check & daemon_call.py shim
                sock_path = root / "state" / "alis-companion" / "daemon.sock"
                srv_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                srv_sock.bind(str(sock_path))
                srv_sock.listen(4)
                stop_flag = threading.Event()

                expected_mtime = companion_daemon._compute_core_source_mtime()

                def _serve_once():
                    while not stop_flag.is_set():
                        srv_sock.settimeout(0.1)
                        try:
                            conn, _ = srv_sock.accept()
                        except socket.timeout:
                            continue
                        with conn:
                            reader = conn.makefile("r", encoding="utf-8", errors="replace")
                            line = reader.readline()
                            if not line.strip():
                                continue
                            req = json.loads(line)
                            if req.get("_expectedSourceMtime") and req["_expectedSourceMtime"] != expected_mtime:
                                rep = {
                                    "ok": False,
                                    "error": {"code": "daemon_version_mismatch", "message": "stale"},
                                    "sourceMtime": expected_mtime,
                                }
                            else:
                                rep = {"ok": True, "pong": True, "sourceMtime": expected_mtime}
                            wire = (json.dumps({"type": "result", "payload": rep}) + "\n").encode("utf-8")
                            conn.sendall(wire)

                st_thr = threading.Thread(target=_serve_once, daemon=True)
                st_thr.start()
                try:
                    mismatch = companion_daemon.call_via_daemon(
                        {"action": "ping", "_expectedSourceMtime": 123},
                        sock_path=sock_path,
                        auto_start=False,
                        timeout=5.0,
                    )
                    self.assertFalse(mismatch["ok"])
                    self.assertEqual((mismatch.get("error") or {}).get("code"), "daemon_version_mismatch")

                    env = dict(os.environ, ALIS_COMPANION_DAEMON_SOCK=str(sock_path))
                    shim_proc = subprocess.run(
                        [sys.executable, str(AQUA_DIR / "daemon_call.py")],
                        input=json.dumps({"action": "ping"}) + "\n",
                        text=True,
                        capture_output=True,
                        env=env,
                        timeout=10,
                    )
                    self.assertEqual(shim_proc.returncode, 0, shim_proc.stderr)
                    self.assertTrue(json.loads(shim_proc.stdout).get("pong"))
                finally:
                    stop_flag.set()
                    st_thr.join(timeout=2)
                    srv_sock.close()
        finally:
            srv.shutdown()
            srv.server_close()

    def test_gui_scroll_drag_focus_and_cdp_fixes(self) -> None:
        with _IsolatedEnv(isolated_flag="1"):
            import cdp_client
            import native_host_bridge
            import screen_context

            # scroll without coordinates must NOT emit (0, 0) cursorEvent
            with mock.patch.object(native_host_bridge, "has_native_vpointer", return_value=True), \
                 mock.patch.object(native_host_bridge, "input_pointer_scroll", return_value={"ok": True}):
                scr = screen_context.perform_gui_action("scroll", delta=-3, allow_live_input=True)
                self.assertTrue(scr["ok"])
                self.assertIsNone(scr.get("cursorEvent"))

            # right_click and double_click preserve specific actionType
            with mock.patch.object(native_host_bridge, "has_native_vpointer", return_value=True), \
                 mock.patch.object(native_host_bridge, "input_pointer_click", return_value={"ok": True}):
                rc = screen_context.perform_gui_action("right_click", x=120, y=240, allow_live_input=True)
                dc = screen_context.perform_gui_action("double_click", x=120, y=240, allow_live_input=True)
                self.assertEqual(rc["cursorEvent"]["actionType"], "right_click")
                self.assertEqual(dc["cursorEvent"]["actionType"], "double_click")

            # drag resolves semantic element coordinates and focus_element falls back to click when AT-SPI focus fails
            fake_elem = {
                "ref": "e1",
                "name": "Handle",
                "role": "push button",
                "centerX": 120,
                "centerY": 130,
                "bbox": [100, 120, 40, 20],
            }
            fake_obs = {
                "observation": {
                    "source": "atspi",
                    "capturedAtMs": 1_795_515_000_000,
                    "windowId": 7,
                    "appId": "app",
                    "title": "App",
                    "pid": 1234,
                    "outputName": "eDP-1",
                    "outputScale": 1.0,
                    "elements": [fake_elem],
                },
                "windowId": 7,
                "appId": "app",
                "title": "App",
                "elements": [fake_elem],
            }
            with mock.patch.dict(os.environ, {"ALIS_AQUA_LIVE_INPUT": "1"}), \
                 mock.patch.object(screen_context, "observe_active_window", return_value=fake_obs), \
                 mock.patch.object(screen_context, "query_niri_state", return_value={"focusedWindow": {"id": 7, "appId": "app", "geometry": {"x": 0, "y": 0, "width": 1000, "height": 800}}, "elements": [fake_elem]}), \
                 mock.patch.object(native_host_bridge, "has_native_vpointer", return_value=True), \
                 mock.patch.object(native_host_bridge, "input_pointer_drag", return_value=(True, "ok")) as m_drag, \
                 mock.patch.object(native_host_bridge, "input_pointer_click", return_value=(True, "ok")) as m_click, \
                 mock.patch.object(screen_context, "_execute_atspi_headless", return_value={"executed": False}):
                dr = screen_context.perform_gui_action("drag", ref="e1", x2=300, y2=320, allow_live_input=True)
                self.assertTrue(dr["ok"])
                m_drag.assert_called_once()
                self.assertEqual((dr["cursorEvent"]["x"], dr["cursorEvent"]["y"]), (300, 320))

                fc = screen_context.perform_gui_action("focus_element", ref="e1", allow_live_input=True)
                self.assertTrue(fc["ok"])
                m_click.assert_called_once()

            # CDP target selector prefers non-devtools page target & setter supports React controlled inputs
            ws = cdp_client.select_page_target(
                [
                    {"type": "page", "url": "devtools://devtools/bundled/inspector.html", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/dev"},
                    {"type": "background_page", "url": "chrome-extension://ext/bg.html", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/bg"},
                    {"type": "page", "url": "https://example.org/app", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/app"},
                ]
            )
            self.assertEqual(ws, "ws://127.0.0.1:9222/app")
            src = Path(cdp_client.__file__).read_text(encoding="utf-8")
            self.assertIn("HTMLInputElement.prototype", src)
            self.assertIn("HTMLTextAreaElement.prototype", src)

    def test_scheduler_multiprocess_nlp_and_lsp(self) -> None:
        with _IsolatedEnv(isolated_flag="1"):
            import agent_runtime
            import code_tools
            import desktop_control
            import scheduler_store

            now_ms = 1_795_500_000_000
            proc = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                watch_by_name = scheduler_store.create_process_watch(
                    "Watch sleep", process_name="sleep", now_ms=now_ms
                )
                self.assertIsNone(watch_by_name["watchPid"])
                self.assertEqual(watch_by_name["watchName"], "sleep")

                watch_pid = scheduler_store.create_process_watch("Recycled pid watch", pid=proc.pid, now_ms=now_ms)
                self.assertEqual(watch_pid["watchPid"], proc.pid)
                summary = scheduler_store.schedule_summary()
                self.assertGreaterEqual(summary["activeProcessWatchCount"], 2)

                # Simulate PID reuse where /proc/<pid>/stat starttime no longer matches recorded start time
                with mock.patch.object(scheduler_store, "_pid_starttime", return_value="99999999"):
                    polled = scheduler_store.poll_due_jobs(now_ms=now_ms + 1_000)
                fired_ids = {j["id"] for j in polled["fired"]}
                self.assertIn(watch_pid["id"], fired_ids)
            finally:
                proc.terminate()
                proc.wait(timeout=5)

            # python3.14 must not extract PID 14
            with mock.patch.object(scheduler_store, "_matching_pids_for_name", return_value=[os.getpid()]):
                turn_pw = agent_runtime.execute_agent_turn(
                    {"prompt": "watch process python3.14", "threadId": "t_sched_nlp"},
                    COMPANION_ROOT,
                    COMPANION_ROOT.parent / "Alis",
                )
            self.assertEqual(turn_pw["status"], "completed")
            pw_jobs = [j for j in scheduler_store.list_jobs() if j["watchName"] == "python3.14"]
            self.assertEqual(len(pw_jobs), 1)
            self.assertIsNone(pw_jobs[0]["watchPid"])

            # Combined hours + minutes and weekly weekday parsing
            turn_hm = agent_runtime.execute_agent_turn(
                {"prompt": "remind me in 1 hour 30 minutes to stretch", "threadId": "t_sched_nlp"},
                COMPANION_ROOT,
                COMPANION_ROOT.parent / "Alis",
            )
            self.assertEqual(turn_hm["status"], "completed")
            hm_jobs = [j for j in scheduler_store.list_jobs() if "stretch" in (j.get("title") or "").lower()]
            self.assertEqual(len(hm_jobs), 1)
            delta_min = (int(hm_jobs[0]["dueMs"]) - int(hm_jobs[0]["createdMs"])) / 60_000.0
            self.assertAlmostEqual(delta_min, 90.0, delta=2.0)

            turn_wk = agent_runtime.execute_agent_turn(
                {"prompt": "schedule every monday at 09:00 weekly sync", "threadId": "t_sched_nlp"},
                COMPANION_ROOT,
                COMPANION_ROOT.parent / "Alis",
            )
            self.assertEqual(turn_wk["status"], "completed")
            wk_jobs = [j for j in scheduler_store.list_jobs() if str(j.get("recurrence") or "").startswith("weekly@mon@09:00")]
            self.assertEqual(len(wk_jobs), 1)

            # Multi-word Wi-Fi SSID parsing
            seen_wifi: list[tuple[str, str]] = []

            def _fake_manage(action: str, target: str = "", value: str = ""):
                seen_wifi.append((action, target))
                return {"ok": True, "action": action, "target": target, "summary": f"Connected to {target}"}

            with mock.patch.object(desktop_control, "manage_desktop", side_effect=_fake_manage):
                agent_runtime.execute_agent_turn(
                    {"prompt": "connect wifi My Home Cafe 5G", "threadId": "t_sched_nlp"},
                    COMPANION_ROOT,
                    COMPANION_ROOT.parent / "Alis",
                )
            self.assertIn(("wifi_connect", "My Home Cafe 5G"), seen_wifi)

            # code_inspect delegates to agent_runtime.query_lsp
            with mock.patch.object(agent_runtime, "query_lsp", return_value={"ok": True, "symbols": [{"name": "MainFn"}]}) as m_lsp:
                lsp_out = agent_runtime._execute_llm_tool_call(
                    {
                        "function": {
                            "name": "code_inspect",
                            "arguments": json.dumps({"action": "symbols", "workspace": str(COMPANION_ROOT)}),
                        }
                    },
                    {"sessionId": "t_sched_nlp"},
                    COMPANION_ROOT,
                    now_ms,
                )
            m_lsp.assert_called_once()
            self.assertIn("MainFn", lsp_out)

    def test_react_sse_streaming_commentary_and_config_parity(self) -> None:
        state = {"mode": "sse", "commentary_prompts": []}

        class _SseHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body_raw = self.rfile.read(length).decode("utf-8")
                req_json = json.loads(body_raw)
                if self.path == "/v1/chat/completions" and "messages" in req_json:
                    msgs_txt = json.dumps(req_json["messages"], ensure_ascii=False)
                    if "critique" in msgs_txt.lower() or "vietnamese" in msgs_txt.lower() or "remark" in msgs_txt.lower():
                        state["commentary_prompts"].append(msgs_txt)
                    if state["mode"] == "sse":
                        chunks = [
                            b'data: {"choices":[{"delta":{"reasoning_content":"Inspecting "}}]}\n\n',
                            b'data: {"choices":[{"delta":{"reasoning_content":"tools"}}]}\n\n',
                            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"system_info","arguments":"{\\"scope\\":"}}]}}]}\n\n',
                            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"all\\"}"}}]}}]}\n\n',
                            b"data: [DONE]\n\n",
                        ]
                        payload = b"".join(chunks)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                        return
                    plain = json.dumps(
                        {"choices": [{"message": {"role": "assistant", "content": "Ghi ch\u00fa nh\u1ecf: M\u1ecdi th\u1ee9 \u0111ang \u1ed5n."}}]}
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(plain)))
                    self.end_headers()
                    self.wfile.write(plain)
                else:
                    self.send_response(404)
                    self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _SseHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with _IsolatedEnv(isolated_flag="1"):
                import agent_runtime
                import ambient_commentary

                base_url = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
                sse_res = agent_runtime._call_local_model_tool_turn(
                    base_url,
                    "qwen-test",
                    "sys",
                    "check system",
                    tools=[{"type": "function", "function": {"name": "system_info"}}],
                    provider="openai",
                )
                self.assertIsNotNone(sse_res)
                self.assertEqual(sse_res["rawMessage"].get("reasoning_content"), "Inspecting tools")
                self.assertEqual(sse_res["toolCalls"][0]["name"], "system_info")
                self.assertEqual(sse_res["toolCalls"][0]["args"], {"scope": "all"})

                state["mode"] = "json"
                json_res = agent_runtime._call_local_model_tool_turn(
                    base_url,
                    "qwen-test",
                    "sys",
                    "check system",
                    tools=[{"type": "function", "function": {"name": "system_info"}}],
                    provider="openai",
                )
                self.assertIsNotNone(json_res)
                self.assertIn("Ghi ch\u00fa", json_res["text"])

                ac_res = ambient_commentary.generate_mascot_commentary(
                    {
                        "endpoint": base_url,
                        "provider": "openai",
                        "model": "qwen-test",
                        "language": "auto",
                        "allowContextualFallback": True,
                        "lastUserPrompt": "Ch\u1ebf \u0111\u1ed9 n\u00e0y ho\u1ea1t \u0111\u1ed9ng sao?",
                        "desktopContext": {"focusedApp": "code", "focusedTitle": "main.py - Alis Project"},
                    }
                )
                self.assertTrue(ac_res.get("ok"))
                self.assertFalse(ac_res.get("skipped"))
                self.assertTrue(any("vietnamese" in p.lower() for p in state["commentary_prompts"]) or bool(ac_res.get("remark")))

                alis_root = (
                    Path(os.environ["ALIS_ROOT"])
                    if os.environ.get("ALIS_ROOT")
                    else (
                        COMPANION_ROOT
                        if (COMPANION_ROOT / "defaults" / "config.json").is_file()
                        else (COMPANION_ROOT.parent / "Alis")
                    )
                )
                cfg_json = json.loads((alis_root / "defaults" / "config.json").read_text(encoding="utf-8"))
                mind_cfg = cfg_json["abyss"]["companionMind"]
                self.assertEqual(mind_cfg.get("sttLanguage"), "auto")
                self.assertEqual(mind_cfg.get("ambientLanguage"), "auto")
                cfg_qml = (alis_root / "modules" / "common" / "Config.qml").read_text(encoding="utf-8")
                self.assertIn('property string sttLanguage: "auto"', cfg_qml)
                self.assertIn('property string ambientLanguage: "auto"', cfg_qml)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
