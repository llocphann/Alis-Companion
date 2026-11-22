#!/usr/bin/env python3
"""Regression tests for Wave 1-3 Alis-Companion security, toolchain, daemon, A2UI & RAG hardening."""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
AQUA_DIR = ROOT / "scripts" / "aqua"
if str(AQUA_DIR) not in sys.path:
    sys.path.insert(0, str(AQUA_DIR))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import a2ui_validator  # noqa: E402
import agent_runtime  # noqa: E402
import companion_daemon  # noqa: E402
import desktop_control  # noqa: E402
import history_store  # noqa: E402
import mcp_client  # noqa: E402
import mcp_transport  # noqa: E402
import native_host_bridge  # noqa: E402
import nexus_rag  # noqa: E402
import rag_store  # noqa: E402
import screen_context  # noqa: E402
import web_tools  # noqa: E402


def _a2ui_env(surface_id: str, components: list[dict], data_model: dict, *, meta: dict | None = None) -> dict:
    cs = {
        "surfaceId": surface_id,
        "catalogId": a2ui_validator.DEFAULT_CATALOG_ID,
        "components": components,
        "dataModel": data_model,
    }
    if meta:
        cs["surfaceMeta"] = meta
    env: dict = {
        "version": a2ui_validator.A2UI_VERSION,
        "createSurface": cs,
    }
    return env


class TestA2uiSecurityAndLogic(unittest.TestCase):
    def test_python_blocks_prototype_pollution_pointers_and_keys(self) -> None:
        self.assertFalse(a2ui_validator._is_valid_pointer("/__proto__/send_prompt"))
        self.assertFalse(a2ui_validator._is_valid_pointer("/a/constructor/prototype"))
        self.assertFalse(a2ui_validator._is_valid_pointer("/prototype/x"))
        self.assertTrue(a2ui_validator._is_valid_pointer("/form/name"))

        bad_surface = _a2ui_env(
            "s_proto",
            [
                {"id": "root", "component": "Column", "children": ["cb1"]},
                {
                    "id": "cb1",
                    "component": "CheckBox",
                    "label": "Pollute",
                    "value": {"path": "/__proto__/send_prompt"},
                },
            ],
            {"__proto__": {"send_prompt": True}, "safe": 1},
        )
        validated, err = a2ui_validator.validate_and_sanitize_surface(bad_surface, tainted=True)
        self.assertIsNone(validated, f"Expected rejection, got {validated} ({err})")

        model = {"safe": 1}
        a2ui_validator.apply_update_data_model(model, "/__proto__/send_prompt", True)
        self.assertEqual(model, {"safe": 1})

    def test_js_a2ui_data_model_blocks_prototype_pollution(self) -> None:
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node not installed")
        js_path = ROOT / "modules" / "abyss" / "companion" / "a2ui" / "A2uiDataModel.js"
        src = js_path.read_text(encoding="utf-8")
        lines = [ln for ln in src.splitlines() if not ln.strip().startswith(".pragma")]
        harness = "\n".join(lines) + """
if (({}).send_prompt !== undefined) process.exit(2);
if (_isValidPointerStr('/__proto__/send_prompt')) process.exit(3);
if (_isValidPointerStr('/constructor/prototype/polluted')) process.exit(4);
const model = { ok: true };
setByPath(model, '/__proto__/send_prompt', true);
setByPath(model, '/constructor/prototype/evil', true);
if (({}).send_prompt !== undefined || ({}).evil !== undefined) process.exit(5);
if (isSafeClientAction('toString')) process.exit(6);
if (!isSafeClientAction('nexus_open')) process.exit(7);
const emptyListSurface = {
  version: A2UI_VERSION,
  createSurface: {
    surfaceId: 's_empty_list',
    catalogId: 'alis-companion-basic-v1',
    components: [
      { id: 'root', component: 'Column', children: ['lst'] },
      { id: 'lst', component: 'List', children: { componentId: 'rowBtn', path: '/items' } },
      { id: 'rowBtn', component: 'Button', child: 'lbl', action: { functionCall: { call: 'openUrl', args: { url: { path: '/url' } } } } },
      { id: 'lbl', component: 'Text', text: { path: '/title' } }
    ],
    dataModel: { items: [] }
  }
};
const v = validateSurface(emptyListSurface);
if (!v || v.createSurface.surfaceId !== 's_empty_list') process.exit(10);
console.log('JS_A2UI_SECURITY_PASS');
"""
        proc = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"node failed: {proc.stderr} {proc.stdout}")
        self.assertIn("JS_A2UI_SECURITY_PASS", proc.stdout)

    def test_slider_non_numeric_and_non_finite_no_value_error(self) -> None:
        for bad_min, bad_max in [("abc", 10), (float("nan"), 10), (0, float("inf")), (True, 10)]:
            surface = _a2ui_env(
                "s_slider_bad",
                [
                    {"id": "root", "component": "Column", "children": ["sl"]},
                    {"id": "sl", "component": "Slider", "min": bad_min, "max": bad_max, "value": 5},
                ],
                {},
            )
            validated, err = a2ui_validator.validate_and_sanitize_surface(surface)
            # Must not crash with ValueError; must either reject or clamp to finite numeric bounds
            if validated is not None:
                sl = [c for c in validated["updateComponents"]["components"] if c["id"] == "sl"][0]
                self.assertIsInstance(sl["min"], (int, float))
                self.assertIsInstance(sl["max"], (int, float))
                self.assertFalse(isinstance(sl["min"], bool))
                self.assertFalse(isinstance(sl["max"], bool))
                self.assertTrue(sl["min"] < sl["max"])
            else:
                self.assertTrue(err)

    def test_empty_list_template_and_bound_choice_tabs_labels(self) -> None:
        surface = _a2ui_env(
            "s_list_tabs",
            [
                {"id": "root", "component": "Column", "children": ["lst", "cp", "tabs"]},
                {"id": "lst", "component": "List", "children": {"componentId": "rowBtn", "path": "/items"}},
                {
                    "id": "rowBtn",
                    "component": "Button",
                    "child": "rowTxt",
                    "action": {"functionCall": {"call": "openUrl", "args": {"url": {"path": "/url"}}}},
                },
                {"id": "rowTxt", "component": "Text", "text": {"path": "/title"}},
                {
                    "id": "cp",
                    "component": "ChoicePicker",
                    "value": {"path": "/selected"},
                    "options": [{"label": {"path": "/optLabel"}, "value": "v1"}],
                },
                {
                    "id": "tabs",
                    "component": "Tabs",
                    "tabs": [{"label": {"path": "/tabLabel"}, "child": "tabBody"}],
                },
                {"id": "tabBody", "component": "Text", "text": "Body"},
            ],
            {"items": [], "selected": ["v1"], "optLabel": "Dynamic Opt", "tabLabel": "Dynamic Tab"},
        )
        validated, err = a2ui_validator.validate_and_sanitize_surface(surface)
        self.assertIsNotNone(validated, f"Rejected valid surface: {err}")
        comps = {c["id"]: c for c in validated["createSurface"]["components"]}
        self.assertEqual(comps["cp"]["options"][0]["label"], {"path": "/optLabel"})
        self.assertEqual(comps["tabs"]["tabs"][0]["label"], {"path": "/tabLabel"})


class TestWebToolsSecurity(unittest.TestCase):
    def test_ssrf_fail_closed_nat64_and_proxy_bypass(self) -> None:
        with mock.patch("web_tools.socket.getaddrinfo", side_effect=OSError("dns failure")):
            ok, reason = web_tools.validate_external_url("https://nonexistent.invalid/test")
            self.assertFalse(ok)
            self.assertIn("dns", reason)

        ok_nat64, reason_nat64 = web_tools.validate_external_url("http://[64:ff9b::127.0.0.1]/secret")
        self.assertFalse(ok_nat64)
        self.assertTrue(reason_nat64)

        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}):
            opener = web_tools.build_safe_opener(allow_local=True)
            proxy_handlers = [h for h in opener.handlers if isinstance(h, web_tools.urllib.request.ProxyHandler)]
            self.assertEqual(proxy_handlers, [])

    def test_searxng_rejects_blocked_private_url(self) -> None:
        with self.assertRaises(web_tools.WebToolError) as ctx:
            web_tools._search_searxng("http://169.254.169.254/latest/meta-data", "test query")
        self.assertEqual(ctx.exception.code, "ssrf_blocked")


class TestMcpHardening(unittest.TestCase):
    def test_stdio_session_handles_two_jsonrpc_lines_in_single_chunk(self) -> None:
        script = (
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    req = json.loads(line)\n"
            "    if 'id' not in req: continue\n"
            "    rid = req['id']\n"
            "    if req.get('method') == 'initialize':\n"
            "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':rid,'result':{'protocolVersion':'2025-03-26','capabilities':{}}}) + '\\n')\n"
            "        sys.stdout.flush()\n"
            "    else:\n"
            "        n = json.dumps({'jsonrpc':'2.0','method':'notifications/progress','params':{'p':1}})\n"
            "        r = json.dumps({'jsonrpc':'2.0','id':rid,'result':{'ok':True,'echo':rid}})\n"
            "        sys.stdout.write(n + '\\n' + r + '\\n')\n"
            "        sys.stdout.flush()\n"
        )
        sess = mcp_transport._StdioSession(("test-srv",), "test-srv", [sys.executable, "-u", "-c", script], dict(os.environ))
        try:
            res = sess.exchange("ping", {}, timeout_sec=3.0)
            self.assertEqual(res.get("ok"), True)
        finally:
            sess.close()

    def test_sqlite_mcp_denies_other_xdg_state_dir_and_aborts_runaway_cte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "state"
            other_dir = xdg / "other-app"
            other_dir.mkdir(parents=True)
            other_db = other_dir / "secrets.db"
            conn = sqlite3.connect(str(other_db))
            conn.execute("CREATE TABLE t (x INT)")
            conn.close()

            comp_dir = xdg / "alis-companion"
            comp_dir.mkdir(parents=True)
            comp_db = comp_dir / "ok.db"
            conn2 = sqlite3.connect(str(comp_db))
            conn2.execute("CREATE TABLE t (x INT)")
            conn2.close()

            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                with mock.patch("permission_store.check_permission", return_value={"allowed": False, "state": "denied"}):
                    with self.assertRaises(mcp_client.McpError) as ctx:
                        mcp_client._execute_curated_tool(
                            "sqlite-mcp", "read_query", {"db_path": str(other_db), "sql": "SELECT 1"}
                        )
                    self.assertIn("permission", str(ctx.exception).lower())

                    with self.assertRaises(mcp_client.McpError) as cte_ctx:
                        mcp_client._execute_curated_tool(
                            "sqlite-mcp",
                            "read_query",
                            {
                                "db_path": str(comp_db),
                                "sql": "SELECT count(*) FROM (WITH RECURSIVE c(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM c WHERE x < 500000000) SELECT x FROM c)",
                            },
                        )
                    self.assertIn("interrupted", str(cte_ctx.exception).lower())

    def test_graphify_mcp_enforces_file_read_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "outside_repo"
            target.mkdir()
            (target / "a.py").write_text("def foo(): return 1\n", encoding="utf-8")
            with mock.patch("permission_store.check_permission", return_value={"allowed": False, "state": "denied"}):
                with self.assertRaises(mcp_client.McpError) as ctx:
                    mcp_client._execute_graphify_tool("graph_stats", {"project_path": str(target)}, ROOT)
                self.assertIn("permission", str(ctx.exception).lower())

    def test_call_tool_runs_manifest_integrity_check_before_external_call(self) -> None:
        fake_server = {
            "name": "ext-test-srv",
            "enabled": True,
            "transport": "stdio",
            "command": ["echo"],
            "curated": False,
        }
        with mock.patch("mcp_client.load_mcp_registry", return_value={"servers": [fake_server]}):
            with mock.patch(
                "mcp_client.list_tools",
                side_effect=mcp_client.McpError("rug_pull_detected", "Manifest hash mismatch"),
            ):
                with self.assertRaises(mcp_client.McpError) as ctx:
                    mcp_client.call_tool("ext-test-srv", "danger_tool", {})
                self.assertEqual(ctx.exception.code, "rug_pull_detected")


class TestNativeBridgeAndHonestErrors(unittest.TestCase):
    def test_stdlib_dhash_from_ppm_without_pil(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp) / "grad.ppm"
            pixels = bytearray()
            for _y in range(8):
                for x in range(9):
                    v = max(0, 255 - x * 28)
                    pixels.extend((v, v, v))
            ppm.write_bytes(b"P6\n9 8\n255\n" + bytes(pixels))
            h = native_host_bridge.compute_dhash_from_image_file(str(ppm))
            self.assertEqual(len(h), 16)
            self.assertEqual(h, "ffffffffffffffff")

    def test_desktop_control_honest_failure_when_backends_missing(self) -> None:
        with mock.patch("desktop_control.shutil.which", return_value=None):
            with mock.patch("native_host_bridge.backlight_get", return_value=(False, 0, 0, 0, "no_backlight")):
                with mock.patch("native_host_bridge.backlight_set", return_value=(False, "no_backlight")):
                    res = desktop_control.manage_desktop("brightness", "get", "")
                    self.assertEqual(res.get("status"), "failed")
                    self.assertNotIn("50%", str(res.get("message", "")))

            with mock.patch("native_host_bridge.clipboard_copy", return_value=(False, "no_wayland")):
                res_clip = desktop_control.manage_desktop("clipboard_set", "hello", "")
                self.assertEqual(res_clip.get("status"), "failed")
                self.assertNotIn("Copied", str(res_clip.get("message", "")))

            with mock.patch("native_host_bridge.mpris_media_control", return_value=(False, "no_mpris", {})):
                res_media = desktop_control.manage_desktop("media", "play-pause", "")
                self.assertEqual(res_media.get("status"), "failed")

    def test_screen_context_honest_failure_in_live_input_when_no_backend(self) -> None:
        with mock.patch.dict(os.environ, {"ALIS_AQUA_LIVE_INPUT": "1"}, clear=False):
            with mock.patch("screen_context.shutil.which", return_value=None):
                with mock.patch("native_host_bridge.input_type", return_value=(False, "no_inputd")):
                    res = screen_context.perform_gui_action("type", text="hello")
                    self.assertFalse(res.get("ok"))
                    self.assertFalse(res.get("verified"))
                    self.assertEqual(res.get("backend"), "unavailable")

                res_click = screen_context.perform_gui_action("click", x=10, y=20)
                self.assertFalse(res_click.get("ok"))
                self.assertFalse(res_click.get("verified"))
                self.assertEqual(res_click.get("backend"), "unavailable")


class TestDaemonContextVarAndEscalation(unittest.TestCase):
    def test_turn_state_contextvar_isolation_across_threads(self) -> None:
        def worker(idx: int) -> tuple[str, int]:
            tok = agent_runtime._TURN_STATE_VAR.set(agent_runtime._TurnState())
            try:
                agent_runtime._CURRENT_TURN_THREAD_ID = f"thread_{idx}"
                agent_runtime._ACTIVE_RUN_STEPS = [{"step": idx}]
                threading.Event().wait(0.03)
                return agent_runtime._CURRENT_TURN_THREAD_ID, agent_runtime._ACTIVE_RUN_STEPS[0]["step"]
            finally:
                agent_runtime._TURN_STATE_VAR.reset(tok)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(4)))
        self.assertEqual(results, [(f"thread_{i}", i) for i in range(4)])

    def test_daemon_lock_uses_file_handle_not_posixpath(self) -> None:
        src = (AQUA_DIR / "companion_daemon.py").read_text(encoding="utf-8")
        self.assertNotIn("lock_path.fileno()", src)
        self.assertIn("lock_fh.fileno()", src)

    def test_a2ui_form_submit_multifield_and_patch_failure_and_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "state"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                created = history_store.execute_thread_action({"action": "create", "title": "Test A2UI"})
                tid = created["activeThreadId"]
                form_surf = _a2ui_env(
                    "surf_form_1",
                    [
                        {"id": "root", "component": "Column", "children": ["btn"]},
                        {"id": "btn", "component": "Button", "child": "t", "action": {"event": {"name": "form_submit"}}},
                        {"id": "t", "component": "Text", "text": "Submit"},
                    ],
                    {"name": "", "email": ""},
                    meta={"sourceKind": "generative", "tainted": True},
                )
                history_store.append("assistant", "Form", thread_id=tid, ui_surface=form_surf)

                res = agent_runtime.handle_a2ui_action(
                    {
                        "threadId": tid,
                        "surfaceId": "surf_form_1",
                        "eventName": "form_submit",
                        "context": {"values": {"/name": "Alice", "/email": "alice@example.com"}},
                    },
                    ROOT,
                    ROOT.parent / "Alis",
                )
                self.assertTrue(res.get("ok"), res)
                self.assertEqual(len(res.get("dataPatch") or []), 2)

                msgs = history_store.load(thread_id=tid)
                saved_surf = msgs[-1].get("uiSurface") or {}
                cs_saved = saved_surf.get("createSurface") or {}
                dm = cs_saved.get("dataModel") or {}
                self.assertEqual(dm.get("name"), "Alice")
                self.assertEqual(dm.get("email"), "alice@example.com")
                self.assertEqual((cs_saved.get("surfaceMeta") or {}).get("sourceKind"), "generative")
                self.assertTrue((cs_saved.get("surfaceMeta") or {}).get("tainted"))

                with mock.patch("history_store.patch_surface_by_id", return_value=None):
                    fail_res = agent_runtime.handle_a2ui_action(
                        {
                            "threadId": tid,
                            "surfaceId": "surf_form_1",
                            "eventName": "form_submit",
                            "context": {"values": {"/name": "Bob"}},
                        },
                        ROOT,
                        ROOT.parent / "Alis",
                    )
                    self.assertFalse(fail_res.get("ok"))

                import permission_store
                pending = permission_store.create_pending_confirmation(
                    "tainted_tool_escalation",
                    "get_time",
                    "Run get_time",
                    payload={"tool": "get_time", "args": {}},
                )
                conf_id = pending["confirmId"]
                esc_surf = _a2ui_env(
                    "surf_esc_1",
                    [
                        {"id": "root", "component": "Column", "children": ["b"]},
                        {"id": "b", "component": "Button", "child": "l", "action": {"event": {"name": "confirm_action"}}},
                        {"id": "l", "component": "Text", "text": "Approve"},
                    ],
                    {
                        "actionKind": "tainted_tool_escalation",
                        "status": "pending",
                        "resolved": False,
                        "confirmId": conf_id,
                    },
                )
                history_store.append("assistant", "Confirm", thread_id=tid, ui_surface=esc_surf)
                with mock.patch(
                    "agent_runtime._execute_llm_tool_call",
                    return_value="Executed approved get_time",
                ) as exec_mock:
                    esc_res = agent_runtime.handle_a2ui_action(
                        {
                            "threadId": tid,
                            "surfaceId": "surf_esc_1",
                            "eventName": "confirm_action",
                            "context": {"decision": "confirm", "confirmId": conf_id},
                        },
                        ROOT,
                        ROOT.parent / "Alis",
                    )
                    self.assertTrue(esc_res.get("ok"), esc_res)
                    exec_mock.assert_called_once()
                    self.assertIn("Executed approved get_time", str(esc_res.get("text") or ""))


class TestRagAndHistorySearch(unittest.TestCase):
    def test_nexus_rag_cjk_symbols_and_filter_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "notes").mkdir(parents=True)
            (vault / "notes" / "cpp.md").write_text(
                "---\ntags: [dev]\n---\n# C++ window-rule Guide\n日本語メモ: テスト設計\n",
                encoding="utf-8",
            )
            xdg = Path(tmp) / "state"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                res_cpp = nexus_rag.search_nexus_knowledge("C++ window-rule", vault_path=str(vault), top_k=5)
                self.assertGreaterEqual(len(res_cpp.get("citations") or []), 1, res_cpp)

                res_cjk = nexus_rag.search_nexus_knowledge("日本語メモ", vault_path=str(vault), top_k=5)
                self.assertGreaterEqual(len(res_cjk.get("citations") or []), 1, res_cjk)

                res_filter = nexus_rag.search_nexus_knowledge("", vault_path=str(vault), folder="notes", tag="dev", top_k=5)
                self.assertGreaterEqual(len(res_filter.get("citations") or []), 1, res_filter)

    def test_history_search_vietnamese_diacritic_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "state"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                created = history_store.execute_thread_action({"action": "create", "title": "VN Thread"})
                tid = created["activeThreadId"]
                history_store.append("user", "Lập báo cáo hiệu năng hệ thống", thread_id=tid)
                found = history_store.search_threads("bao cao hieu nang")
                self.assertTrue(any(t["id"] == tid for t in found), found)

    def test_rag_store_persists_digest_across_cold_resync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "state"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
                chunks = [{"id": "c1", "source": "a.md", "heading": "C++ Guide", "text": "日本語メモ テスト設計"}]
                rag_store._LAST_INDEX_SIGNATURE = None
                self.assertTrue(rag_store.sync_fts5_chunks(chunks))
                sig = rag_store._LAST_INDEX_SIGNATURE
                self.assertTrue(sig)
                rag_store._LAST_INDEX_SIGNATURE = None
                self.assertTrue(rag_store.sync_fts5_chunks(chunks))
                self.assertEqual(rag_store._LAST_INDEX_SIGNATURE, sig)
                hits = rag_store.query_hybrid_rag("日本語メモ", chunks)
                self.assertGreaterEqual(len(hits), 1, f"CJK hybrid RAG query returned empty: {hits}")

    def test_p7_pam_dhash_and_real_alis_theme_jpg_resize(self) -> None:
        # 1. Pure PAM P7 RGBA 9x8 horizontal gradient -> all bits 1 (ffffffffffffffff)
        pixels = bytearray()
        for _y in range(8):
            for x in range(9):
                v = max(0, 255 - x * 28)
                pixels.extend((v, v, v, 255))
        pam_hdr = b"P7\nWIDTH 9\nHEIGHT 8\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n"
        self.assertEqual(native_host_bridge.compute_dhash_from_ppm_bytes(pam_hdr + bytes(pixels)), "ffffffffffffffff")

        # 2. Real alis-theme resize on a JPEG gradient (proves P7 PAM path works end-to-end)
        if native_host_bridge.find_native_binary("alis-theme"):
            with tempfile.TemporaryDirectory() as tmp:
                from aqua_png_stdlib import Image

                png_p = Path(tmp) / "grad.png"
                jpg_p = Path(tmp) / "grad.jpg"
                im = Image.new("RGBA", (36, 32), (0, 0, 0, 255))
                for y in range(32):
                    for x in range(36):
                        v = max(0, 255 - int(x * 7))
                        im.putpixel((x, y), (v, v, v, 255))
                im.save(png_p)
                ok_jpg, _ = native_host_bridge.image_resize(png_p, "36x32!", jpg_p, quality=95)
                self.assertTrue(ok_jpg and jpg_p.is_file())
                dh_jpg = native_host_bridge.compute_dhash_from_image_file(jpg_p)
                self.assertEqual(len(dh_jpg), 16, f"JPEG dHash via alis-theme P7 failed: {dh_jpg!r}")
                self.assertEqual(dh_jpg, "ffffffffffffffff")

    def test_a2ui_open_editor_and_save_pocket_real_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "state"
            home_dir = Path(tmp) / "home"
            home_dir.mkdir()
            note = home_dir / "sample.txt"
            note.write_text("hello editor and pocket\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"XDG_STATE_HOME": str(xdg), "HOME": str(home_dir), "AQUA_TEST_NO_EXTERNAL_EDITOR": "1"},
            ):
                created = history_store.execute_thread_action({"action": "create", "title": "Handlers"})
                tid = created["activeThreadId"]
                # Trusted card for open_editor
                open_surf = _a2ui_env(
                    "surf_open_1",
                    [
                        {"id": "root", "component": "Column", "children": ["b"]},
                        {"id": "b", "component": "Button", "child": "t", "action": {"event": {"name": "open_editor", "context": {"path": str(note)}}}},
                        {"id": "t", "component": "Text", "text": "Open"},
                    ],
                    {},
                )
                history_store.append("assistant", "Open", thread_id=tid, ui_surface=open_surf)
                res_open = agent_runtime.handle_a2ui_action(
                    {
                        "threadId": tid,
                        "surfaceId": "surf_open_1",
                        "eventName": "open_editor",
                        "context": {"path": str(note)},
                    },
                    ROOT,
                    ROOT.parent / "Alis",
                )
                self.assertTrue(res_open.get("ok"), res_open)
                self.assertEqual(Path(res_open.get("targetPath") or "").resolve(), note.resolve())

                # save_pocket with text clip -> creates real Pocket entry in pocket.sqlite3
                res_clip = agent_runtime.handle_a2ui_action(
                    {
                        "threadId": tid,
                        "surfaceId": "surf_open_1",
                        "eventName": "save_pocket",
                        "context": {"text": "Saved snippet body", "label": "snippet"},
                    },
                    ROOT,
                    ROOT.parent / "Alis",
                )
                self.assertTrue(res_clip.get("ok"), res_clip)

                # save_pocket with file path denied when file_read permission is refused
                with mock.patch.object(agent_runtime, "check_permission", return_value={"allowed": False, "state": "denied"}):
                    res_denied = agent_runtime.handle_a2ui_action(
                        {
                            "threadId": tid,
                            "surfaceId": "surf_open_1",
                            "eventName": "save_pocket",
                            "context": {"path": str(note)},
                        },
                        ROOT,
                        ROOT.parent / "Alis",
                    )
                    self.assertFalse(res_denied.get("ok"), res_denied)
                    self.assertEqual(res_denied.get("error"), "permission_denied")

                # save_pocket with file path allowed when file_read permission succeeds
                with mock.patch.object(agent_runtime, "check_permission", return_value={"allowed": True, "state": "granted"}):
                    res_file = agent_runtime.handle_a2ui_action(
                        {
                            "threadId": tid,
                            "surfaceId": "surf_open_1",
                            "eventName": "save_pocket",
                            "context": {"path": str(note)},
                        },
                        ROOT,
                        ROOT.parent / "Alis",
                    )
                    self.assertTrue(res_file.get("ok"), res_file)

    def test_web_tools_invalid_port_and_loopback_redirect_ssrf_guard(self) -> None:
        ok, reason = web_tools.validate_external_url("http://example.com:99999/test")
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_url")

        # Loopback-only redirect must reject jumping to AWS metadata / RFC1918
        ok_meta, reason_meta = web_tools.validate_external_url(
            "http://169.254.169.254/latest/meta-data", allow_local=True, allow_loopback_only=True
        )
        self.assertFalse(ok_meta)
        self.assertTrue(reason_meta)

        ok_loop, _ = web_tools.validate_external_url(
            "http://127.0.0.1:8888/search", allow_local=True, allow_loopback_only=True
        )
        self.assertTrue(ok_loop)

    def test_js_a2ui_resolve_context_and_slider_bounds(self) -> None:
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node not installed")
        js_path = ROOT / "modules" / "abyss" / "companion" / "a2ui" / "A2uiDataModel.js"
        src = js_path.read_text(encoding="utf-8")
        lines = [ln for ln in src.splitlines() if not ln.strip().startswith(".pragma")]
        harness = "\n".join(lines) + """
const ctxSpec = JSON.parse('{"__proto__":{"polluted":true},"ok":"yes"}');
const resolved = resolveContext(ctxSpec, {}, null);
if (resolved.ok !== 'yes' || resolved.polluted !== undefined || ({}).polluted !== undefined) process.exit(2);
if (_sanitizeDataModelValue(NaN, 0) !== null || _sanitizeDataModelValue(Infinity, 0) !== null) process.exit(3);
const badSliders = [
  { min: '', max: 10, step: 1 },
  { min: 20, max: 10, step: 1 },
  { min: 0, max: 10, step: 0 }
];
for (const b of badSliders) {
  const env = {
    version: A2UI_VERSION,
    createSurface: {
      surfaceId: 's_sl',
      catalogId: 'alis-companion-basic-v1',
      components: [
        { id: 'root', component: 'Column', children: ['sl'] },
        Object.assign({ id: 'sl', component: 'Slider', value: { path: '/v' } }, b)
      ],
      dataModel: { v: 5 }
    }
  };
  if (validateSurface(env) !== null) process.exit(4);
}
console.log('JS_A2UI_EDGE_PASS');
"""
        proc = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"node failed: {proc.stderr} {proc.stdout}")
        self.assertIn("JS_A2UI_EDGE_PASS", proc.stdout)

    def test_lmstudio_watchdog_propagates_oom_to_caller_contextvar(self) -> None:
        tok = agent_runtime._TURN_STATE_VAR.set(agent_runtime._TurnState())
        try:
            with mock.patch.object(agent_runtime, "_kill_runaway_lmstudio_workers"):
                with mock.patch.object(agent_runtime, "_get_system_and_gpu_memory_mb", return_value=(4000, 100)):
                    with mock.patch.object(agent_runtime, "_check_lmstudio_log_recent_fit_or_oom_error", return_value="gpu_oom"):
                        ev = agent_runtime._start_lmstudio_gtt_watchdog()
                        try:
                            threading.Event().wait(0.35)
                        finally:
                            ev.set()
            self.assertEqual(agent_runtime._LAST_LMSTUDIO_ERROR, "gpu_oom")
        finally:
            agent_runtime._TURN_STATE_VAR.reset(tok)

    def test_graphify_pr_number_non_numeric_does_not_crash(self) -> None:
        res = mcp_client._execute_graphify_tool("get_pr_impact", {"pr_number": "not-a-number"}, ROOT)
        self.assertFalse(res.get("isError"))
        self.assertEqual((res.get("structured") or {}).get("prNumber"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
