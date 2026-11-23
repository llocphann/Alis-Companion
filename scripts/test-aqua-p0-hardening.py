#!/usr/bin/env python3
"""Offline P0 security, privacy, and correctness hardening suite for Alis-Companion.

Runs in an isolated temporary HOME/XDG sandbox with zero access to or mutation of
the user's real Obsidian vault, config, or credentials.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

COMPANION_ROOT = Path(__file__).resolve().parents[1]
AQUA_DIR = COMPANION_ROOT / "scripts" / "aqua"
if str(AQUA_DIR) not in sys.path:
    sys.path.insert(0, str(AQUA_DIR))


class P0HardeningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aqua-p0-hardening-")
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.cache = self.root / "cache"
        self.config = self.root / "config"
        self.outside = self.root / "outside"
        for d in (self.home, self.state, self.cache, self.config, self.outside):
            d.mkdir(parents=True, exist_ok=True)
        (self.home / "Desktop").mkdir(exist_ok=True)
        (self.home / "Documents").mkdir(exist_ok=True)

        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_STATE_HOME": str(self.state),
                "XDG_CACHE_HOME": str(self.cache),
                "XDG_CONFIG_HOME": str(self.config),
                "ALIS_TEST_ISOLATED": "1",
                "AQUA_TEST_AGENT_ENABLED": "1",
            },
            clear=False,
        )
        self._env_patch.start()
        for var in ("ALIS_AQUA_HISTORY_DB", "ALIS_AQUA_MEMORY_DB", "ALIS_AQUA_SWARM_STATE", "ALIS_TEST_ALLOW_LOCAL_WEB"):
            os.environ.pop(var, None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()

    def test_permission_store_paths_and_revoke_all(self) -> None:
        import permission_store

        ssh_dir = self.home / ".ssh"
        ssh_dir.mkdir(exist_ok=True)
        custom_cfg = ssh_dir / "custom_config"
        custom_cfg.write_text("Host example\n", encoding="utf-8")
        key_file = ssh_dir / "id_ed25519"
        key_file.write_text("PRIVATE KEY\n", encoding="utf-8")
        env_file = self.home / ".env"
        env_file.write_text("API_KEY=secret\n", encoding="utf-8")

        self.assertFalse(permission_store.is_forbidden_path(custom_cfg))
        self.assertTrue(permission_store.is_sensitive_path(custom_cfg))
        self.assertTrue(permission_store.is_forbidden_path(key_file))
        self.assertTrue(permission_store.is_forbidden_path(self.home / ".gnupg" / "secring.gpg"))
        self.assertTrue(permission_store.is_forbidden_path(env_file))
        self.assertTrue(permission_store.is_forbidden_path("/etc/shadow"))

        escape_link = self.home / "Desktop" / "escape_link"
        escape_link.symlink_to("/etc/passwd")
        self.assertTrue(permission_store.is_symlink_raw(escape_link, root_dir=self.home))

        st = permission_store.load_state(now_ms=1000)
        self.assertTrue(st.get("homeFullAccess"))
        res = permission_store.revoke_permission(revoke_all=True, now_ms=2000)
        self.assertFalse(res.get("homeFullAccess"))
        post = permission_store.load_state(now_ms=3000)
        self.assertFalse(post.get("homeFullAccess"))

    def test_sandbox_runner_hardened_bwrap_flags(self) -> None:
        import sandbox_runner

        args = sandbox_runner._build_bwrap_args("/usr/bin/bwrap", str(self.home), allow_network=False)
        for flag in ("--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-net", "--cap-drop"):
            self.assertIn(flag, args, args)
        idx = args.index("--cap-drop")
        self.assertEqual(args[idx + 1], "ALL")

    def test_web_tools_ssrf_validation(self) -> None:
        import web_tools

        blocked_urls = (
            "http://127.0.0.1/status",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/admin",
            "http://[::ffff:127.0.0.1]/metrics",
            "file:///etc/passwd",
        )
        for u in blocked_urls:
            ok, reason = web_tools.validate_external_url(u, allow_local=False)
            self.assertFalse(ok, f"Expected SSRF block for {u}: {reason}")
        ok_ext, _ = web_tools.validate_external_url("https://example.com/docs", allow_local=False)
        self.assertTrue(ok_ext)

    def test_tool_store_rollback_id_confinement(self) -> None:
        import tool_store

        res = tool_store.rollback_file_receipt("../evil")
        self.assertFalse(res.get("ok", True))
        self.assertIn("Invalid", str(res.get("error") or ""))
        with self.assertRaises(tool_store.ToolError):
            tool_store.rollback_file_receipt("../evil", raise_on_missing=True)

    def test_skill_store_reference_and_run_confinement(self) -> None:
        import skill_store

        sk_dir = self.state / "alis-companion" / "skills" / "p0-test-skill"
        (sk_dir / "references").mkdir(parents=True, exist_ok=True)
        (sk_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (sk_dir / "SKILL.md").write_text("---\nname: p0-test-skill\ndescription: P0 test\n---\nInstructions.\n", encoding="utf-8")

        secret_src = self.outside / "secret.txt"
        secret_src.write_text("TOP_SECRET", encoding="utf-8")
        ref_link = sk_dir / "references" / "leak.md"
        ref_link.symlink_to(secret_src)
        with self.assertRaises(skill_store.SkillError):
            skill_store.read_skill_reference("p0-test-skill", "references/leak.md", companion_root=COMPANION_ROOT)

        outside_sh = self.outside / "evil.sh"
        outside_sh.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
        outside_sh.chmod(0o755)
        escaped_script = sk_dir / "scripts" / "evil.sh"
        escaped_script.symlink_to(outside_sh)
        ran = skill_store.run_skill("p0-test-skill", prompt="run test", companion_root=COMPANION_ROOT, workspace=str(self.home))
        self.assertNotIn("pwned", str(ran.get("output") or ""))

    def test_mcp_client_blocks_fallback_and_sensitive_paths(self) -> None:
        import mcp_client

        # Disallow fallback into companion-system / shell_exec from arbitrary enabled server
        with self.assertRaises(mcp_client.McpError):
            mcp_client.call_tool(
                "fetch-mcp",
                "shell_exec",
                {"command": "id"},
                companion_root=COMPANION_ROOT,
                enabled_overrides={"fetch-mcp": True, "companion-system": True},
            )

        ssh_key = self.home / ".ssh" / "id_ed25519"
        ssh_key.parent.mkdir(parents=True, exist_ok=True)
        ssh_key.write_text("PRIVATE", encoding="utf-8")
        with self.assertRaises(mcp_client.McpError):
            mcp_client.call_tool(
                "desktop-commander",
                "read_file_excerpt",
                {"path": str(ssh_key)},
                companion_root=COMPANION_ROOT,
                enabled_overrides={"desktop-commander": True},
            )

        cookies_db = self.home / ".mozilla" / "firefox" / "default" / "cookies.sqlite"
        cookies_db.parent.mkdir(parents=True, exist_ok=True)
        cookies_db.write_bytes(b"")
        with self.assertRaises(mcp_client.McpError):
            mcp_client.call_tool(
                "sqlite-mcp",
                "list_tables",
                {"db_path": str(cookies_db)},
                companion_root=COMPANION_ROOT,
                enabled_overrides={"sqlite-mcp": True},
            )

    def test_domain_rag_scrub_secrets(self) -> None:
        import domain_rag

        raw = (
            'environment { API_KEY "supersecret123" }\n'
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAtest\n-----END RSA PRIVATE KEY-----\n"
            "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c\n"
            "aws=AKIAIOSFODNN7EXAMPLE\n"
            "url=https://admin:hunter2pass@git.example.com/repo.git\n"
        )
        scrubbed = domain_rag.scrub_secrets(raw)
        for leak in ("supersecret123", "MIIEowIBAAKCAQEAtest", "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", "AKIAIOSFODNN7EXAMPLE", "hunter2pass"):
            self.assertNotIn(leak, scrubbed, scrubbed)

    def test_desktop_control_local_stt_and_launch_app_guards(self) -> None:
        import desktop_control
        import permission_store

        # launch_app blocks raw interpreter -c / -e inline execution even in isolated mode
        with self.assertRaises(desktop_control.DesktopControlError) as ctx_inj:
            desktop_control.manage_desktop("launch_app", target="bash -c 'id'")
        self.assertEqual(ctx_inj.exception.code, "unsafe_launch_blocked")

        with self.assertRaises(desktop_control.DesktopControlError):
            desktop_control.manage_desktop("launch_app", target="python3 -c 'print(1)'")

        # launch_app honors revoked shell_run
        permission_store.revoke_permission("tool", "shell_run", now_ms=1000)
        with self.assertRaises(desktop_control.DesktopControlError) as ctx_perm:
            desktop_control.manage_desktop("launch_app", target="firefox")
        self.assertEqual(ctx_perm.exception.code, "permission_denied")

        # voice_stt with configured_provider="local" never invokes cloud HTTP requests
        vdir = desktop_control._voice_state_dir()
        wav = vdir / "voice_input.wav"
        wav.write_bytes(b"RIFF" + b"\x00" * 120)
        with (
            mock.patch.object(desktop_control, "_is_test_or_isolated_env", return_value=False),
            mock.patch.object(
                desktop_control,
                "_load_voice_config_and_keys",
                return_value=({"provider": "local", "language": "en"}, {"groq": "gsk_x", "openai": "sk_x", "gemini": "AIza_x"}),
            ),
            mock.patch.object(desktop_control, "_resolve_safe_session_binary", return_value=None),
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("Cloud STT must not be called when provider=local")),
        ):
            stt_res = desktop_control.voice_stt(audio_path=str(wav))
            self.assertFalse(stt_res.get("ok"))
            self.assertEqual(stt_res.get("status"), "no_speech_detected")

    def test_agent_runtime_guards_reasoning_leak_and_swarm(self) -> None:
        import agent_runtime
        import swarm_store

        # file_mutate blocks op="run"
        blocked_op = agent_runtime._execute_llm_tool_call(
            {"name": "file_mutate", "args": {"op": "run", "path": str(self.home / "Desktop" / "a.txt")}},
            {"lastDirectory": str(self.home / "Desktop")},
            COMPANION_ROOT,
            1000,
        )
        self.assertIn("blocked: unsupported operation 'run'", blocked_op)

        # is_hypothetical_or_negated allows general knowledge while blocking quoted destructive phrase
        self.assertFalse(agent_runtime.is_hypothetical_or_negated("Explain how Raft consensus works"))
        self.assertTrue(agent_runtime.is_hypothetical_or_negated("explain the phrase 'delete ~/Desktop/notes.txt'"))

        # classify_intent routes prose prompts to chat instead of file_task
        self.assertEqual(agent_runtime.classify_intent("Write a haiku about rain", {})["kind"], "chat")
        self.assertEqual(agent_runtime.classify_intent("Summarize how TCP congestion control works", {})["kind"], "chat")

        # _call_chat_completion_text must NOT leak reasoning_content when content is empty
        class _ReasoningOnlyHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                body = json.dumps({
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "INTERNAL CHAIN OF THOUGHT MUST NOT LEAK",
                        }
                    }]
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args) -> None:
                return

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _ReasoningOnlyHandler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            reply = agent_runtime._call_chat_completion_text(
                f"http://127.0.0.1:{srv.server_port}/v1/chat/completions",
                {"model": "qwen3-test", "messages": [{"role": "user", "content": "hi"}]},
                stream_stdout=False,
            )
            self.assertEqual(reply, "")
        finally:
            srv.shutdown()
            srv.server_close()

        # delegate_subagent(role="verifier") without LLM reports skipped + includes toolCount
        sub = agent_runtime.delegate_subagent(
            role="verifier",
            task="Verify migration integrity",
            options={"useSubagentLlm": False},
            companion_root=COMPANION_ROOT,
            now_ms=2000,
        )
        self.assertIn("toolCount", sub)
        self.assertIn("skipped", sub["summary"].lower())

        # run_swarm_mission stops early when swarm state is already stopped by operator
        stopped_st = swarm_store.stop_swarm_state(now_ms=5000, emit=False)
        with mock.patch.object(
            agent_runtime.swarm_store,
            "load_state",
            return_value=stopped_st,
        ):
            st, md = agent_runtime.run_swarm_mission(
                "Audit repository modules",
                {"model": "fixture-model"},
                COMPANION_ROOT,
            )
            self.assertEqual(st.get("status"), "stopped")
            self.assertNotIn("Swarm Completed", md)

    def test_ambient_commentary_and_local_bridge_and_stores(self) -> None:
        import alis_local_bridge
        import ambient_commentary
        import rule_store
        import run_store

        # Malformed truncated JSON fragment never leaks raw '{'
        remark, expr = ambient_commentary._parse_remark_response('{"remark": "hello')
        self.assertEqual(remark, "")

        # Structured snapshot pruning + capturedAtMs
        oversized = {
            "weather": {"temp": "27C", "city": "Can Tho"},
            "nexus": {"notes": ["line " + ("x" * 2000) for _ in range(120)]},
        }
        alis_local_bridge.save_live_snapshot(oversized)
        loaded = alis_local_bridge.load_live_snapshot()
        self.assertIn("capturedAtMs", loaded)
        self.assertIn("weather", loaded)

        # English _format_system + accurate 0 < pct < 1.0 vs 1.0% scaling
        md_one, _ = alis_local_bridge._format_system({"cpuPercent": 1.0, "gpuPercent": 0.25}, {}, "en")
        self.assertIn("**Resources:**", md_one)
        self.assertIn("CPU 1.0%", md_one)
        self.assertIn("GPU 25.0%", md_one)

        # Clause-scoped on/off detection
        intents = alis_local_bridge.detect_all_alis_local_intents("turn off night light and turn on game mode")
        by_act = {it.get("action"): it.get("value") for it in intents}
        self.assertEqual(by_act.get("night_light_set"), "off")
        self.assertEqual(by_act.get("gamemode_set"), "on")

        # rule_store.delete_rule("") must return deleted=False and preserve existing rules
        rule_store.create_or_update_rule("focus_end", "Check inbox", title="Inbox Rule", now_ms=1000)
        del_res = rule_store.delete_rule("", now_ms=1100)
        self.assertFalse(del_res.get("deleted"))
        self.assertEqual(len(rule_store.load_rules_state(now_ms=1200).get("rules", [])), 1)

        # run_store.list_recent_runs returns newly started run
        run_store.start_run("Sample task", now_ms=1500)
        recent = run_store.list_recent_runs(limit=5)
        self.assertTrue(len(recent) >= 1 and recent[0]["goal"] == "Sample task")

    def test_wave1_companion_security_a2ui_and_native_fallbacks(self) -> None:
        import a2ui_validator
        import local_mind
        import mcp_client
        import native_host_bridge
        import permission_store
        import screen_context

        # 1. a2ui_save_pocket allowed for check_permission but forbidden for grant_permission
        chk = permission_store.check_permission("pocket_write", source="a2ui_save_pocket", now_ms=1000)
        self.assertNotEqual(chk.get("reason"), "untrusted_source_blocked")
        with self.assertRaises(permission_store.AgentPermissionError):
            permission_store.grant_permission("tool", "pocket_write", source="a2ui_save_pocket", now_ms=1000)

        # 2. A2UI control-char bypasses, malformed empty-segment pointers, and non-finite numeric fields
        tainted_ev = {
            "version": "1.0",
            "createSurface": {
                "surfaceId": "s_tainted_ctrl",
                "components": [
                    {
                        "id": "root",
                        "component": "Button",
                        "label": "Go",
                        "action": {"event": {"name": "choice_submit", "context": {"pro\x07mpt": "pwn"}}},
                    }
                ],
                "dataModel": {},
            },
        }
        v_surf, err = a2ui_validator.validate_and_sanitize_surface(tainted_ev, tainted=True)
        self.assertIsNone(v_surf)
        self.assertEqual(err, "button_action:tainted_prompt_context_forbidden")

        secret_tf = {
            "version": "1.0",
            "createSurface": {
                "surfaceId": "s_secret_ctrl",
                "components": [
                    {"id": "root", "component": "TextField", "label": "pas\x07sword", "value": {"path": "/safe"}}
                ],
                "dataModel": {},
            },
        }
        v_tf, err_tf = a2ui_validator.validate_and_sanitize_surface(secret_tf)
        self.assertIsNone(v_tf)
        self.assertEqual(err_tf, "password_field_forbidden_in_a2ui")

        for bad_ptr in ("///", "/a//b", "/a/", "/"):
            bad_surf = {
                "version": "1.0",
                "createSurface": {
                    "surfaceId": "s_bad_ptr",
                    "components": [{"id": "root", "component": "Text", "text": {"path": bad_ptr}}],
                    "dataModel": {"secret_note": "keep"},
                },
            }
            v_bad, _ = a2ui_validator.validate_and_sanitize_surface(bad_surf)
            self.assertIsNone(v_bad, f"Expected malformed pointer rejection for {bad_ptr!r}")
            self.assertIsNone(a2ui_validator.get_pointer_value({"a": {"b": 1}}, bad_ptr) if bad_ptr != "/" else None)

        base_dm = {"version": "1.0", "createSurface": {"surfaceId": "s_dm", "components": [{"id": "root", "component": "Text", "text": "ok"}], "dataModel": {"a": 1}}}
        patched_bad = a2ui_validator.apply_update_data_model(base_dm, "/a//b", "x")
        self.assertEqual(patched_bad["createSurface"]["dataModel"], {"a": 1})
        patched_secret = a2ui_validator.apply_update_data_model(base_dm, "/password", "1234")
        self.assertEqual(patched_secret["createSurface"]["dataModel"], {"a": 1})

        nan_surf = {
            "version": "1.0",
            "createSurface": {
                "surfaceId": "s_nan",
                "components": [
                    {"id": "root", "component": "Column", "gap": float("nan"), "children": ["t1"]},
                    {"id": "t1", "component": "Text", "text": "Hi", "maxLines": True},
                ],
                "dataModel": {},
            },
        }
        v_nan, err_nan = a2ui_validator.validate_and_sanitize_surface(nan_surf)
        self.assertIsNotNone(v_nan, err_nan)
        self.assertNotIn("gap", v_nan["createSurface"]["components"][0])
        self.assertNotIn("maxLines", v_nan["createSurface"]["components"][1])

        # 3. Pure-stdlib PNG Set-of-Mark fallback when PIL is absent
        som_out = self.root / "som_out.png"
        with mock.patch.dict(sys.modules, {"PIL": None}):
            res_som = screen_context.annotate_screenshot_som(
                "",
                [{"markId": 1, "name": "Ok", "role": "button", "x": 40, "y": 40, "width": 120, "height": 40}],
                out_path=str(som_out),
            )
            self.assertEqual(res_som["markCount"], 1)
            raw_png = som_out.read_bytes()
            self.assertTrue(raw_png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreater(len(raw_png), len(screen_context._FALLBACK_1X1_PNG))
            # Annotate an existing 8-bit PNG via stdlib decoder
            som_out2 = self.root / "som_out2.png"
            res_som2 = screen_context.annotate_screenshot_som(
                str(som_out),
                [{"markId": 2, "name": "Next", "role": "button", "x": 100, "y": 100, "width": 80, "height": 30}],
                out_path=str(som_out2),
            )
            self.assertEqual(res_som2["markCount"], 1)
            self.assertTrue(som_out2.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

        # 4. mcp_client clipboard_read routes through native_host_bridge
        with mock.patch.object(native_host_bridge, "clipboard_paste", return_value=(True, "native-clip-ok")):
            clip_res = mcp_client.call_tool(
                "companion-system",
                "clipboard_read",
                {},
                companion_root=COMPANION_ROOT,
                enabled_overrides={"companion-system": True},
            )
            self.assertEqual(clip_res["content"][0]["text"], "native-clip-ok")

        # 5. _pick_attachments_dialog handles exit 0 (select), exit 1 (cancel), exit 2 (fallback)
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-1"}, clear=False):
            os.environ.pop("AQUA_TEST_PICK_FILE", None)
            os.environ.pop("AQUA_TEST_PICK_IMAGE", None)
            with mock.patch.object(native_host_bridge, "find_native_dispatch", return_value="/mock/native-dispatch"):
                with mock.patch("shutil.which", side_effect=lambda name: "/mock/zenity" if name == "zenity" else None):
                    p_ok = mock.Mock(returncode=0, stdout=f"{self.home / 'Documents' / 'a.png'}\n", stderr="")
                    with mock.patch("subprocess.run", return_value=p_ok) as m_run:
                        picked = local_mind._pick_attachments_dialog("images")
                        self.assertEqual(len(picked), 1)
                        self.assertIn("file-dialog", m_run.call_args[0][0])

                    p_cancel = mock.Mock(returncode=1, stdout="", stderr="")
                    with mock.patch("subprocess.run", return_value=p_cancel) as m_run:
                        self.assertEqual(local_mind._pick_attachments_dialog("all"), [])
                        self.assertEqual(m_run.call_count, 1)

                    p_err = mock.Mock(returncode=2, stdout="", stderr="portal failed")
                    p_zen = mock.Mock(returncode=0, stdout=f"{self.home / 'Documents' / 'b.txt'}", stderr="")
                    with mock.patch("subprocess.run", side_effect=[p_err, p_zen]) as m_run:
                        picked2 = local_mind._pick_attachments_dialog("all")
                        self.assertEqual(len(picked2), 1)
                        self.assertEqual(m_run.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
