#!/usr/bin/env python3
"""Comprehensive AC-F11 validation suite for the natural-language local desktop agent."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
HOST = Path(os.environ.get("ALIS_ROOT", ROOT.parent / "Alis")).resolve()
sys.path.insert(0, str(ROOT / "scripts/aqua"))
sys.path.insert(0, str(HOST / "scripts/ai"))


def run_helper(payload: dict, env: dict[str, str]) -> dict:
    proc = subprocess.run(
        ["/usr/bin/python3", str(ROOT / "scripts/aqua/local_mind.py"), "--host-root", str(HOST)],
        input=json.dumps(payload).encode("utf-8"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=True,
    )
    out = json.loads(proc.stdout.decode("utf-8"))
    assert out.get("ok") is True, f"Helper failed for {payload.get('action')}: {out}"
    return out["result"]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="alis-companion-f11-") as tmp:
        tmp_path = Path(tmp)
        xdg_state = tmp_path / "state"
        xdg_config = tmp_path / "config"
        fake_home = tmp_path / "home"
        desktop = fake_home / "Desktop"
        outside_dir = fake_home / "SecretProject"
        ssh_dir = fake_home / ".ssh"
        for d in (xdg_state, xdg_config, desktop, outside_dir, ssh_dir):
            d.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update({
            "HOME": str(fake_home),
            "XDG_STATE_HOME": str(xdg_state),
            "XDG_CONFIG_HOME": str(xdg_config),
            "ALIS_ROOT": str(HOST),
            "PYTHONDONTWRITEBYTECODE": "1",
        })

        # ------------------------------------------------------------------
        # 0. Pre-upgrade legacy fixture seeding (chat.sqlite3, memory.sqlite3, pocket.json)
        # ------------------------------------------------------------------
        legacy_chat_dir = xdg_state / "alis/aqua"
        legacy_chat_dir.mkdir(parents=True, exist_ok=True)
        env["ALIS_AQUA_HISTORY_DB"] = str(legacy_chat_dir / "chat.sqlite3")
        env["ALIS_AQUA_MEMORY_DB"] = str(legacy_chat_dir / "memory.sqlite3")
        env["ALIS_AQUA_POCKET_FILE"] = str(legacy_chat_dir / "pocket.json")

        # Seed legacy history & memory via existing stores
        hist_res = run_helper(
            {"action": "history_append", "prompt": "Legacy hello", "reply": "Legacy splash!", "model": "legacy:local"},
            env,
        )
        assert hist_res["userMessageId"] == 1 and hist_res["assistantMessageId"] == 2
        mem_res = run_helper(
            {"action": "memory_save", "confirmed": True, "memoryEnabled": True, "id": 0, "text": "Legacy user preference", "scope": "shared", "expiresMs": 0},
            env,
        )
        assert mem_res["saved"] is True and mem_res["id"] == 1

        # ------------------------------------------------------------------
        # 1. Ordinary chat vs Negated / Hypothetical / Quoted guard (VI & EN, Aqua & Octo)
        # ------------------------------------------------------------------
        note_file = desktop / "notes.txt"
        note_file.write_text("Line 1: Initial project note\nLine 2: Important data\n", encoding="utf-8")
        before_bytes = note_file.read_bytes()

        # Ordinary chat should report handled=False so AquaMind routes it to casual AI chat without calling tools
        casual = run_helper({"action": "agent_turn", "prompt": "Hello Aqua, how are you today?", "character": "aqua"}, env)
        assert casual["handled"] is False and casual["intent"] == "chat"

        negated_prompts = [
            ("aqua", "đừng xoá tệp ~/Desktop/notes.txt"),
            ("octo", "không chạy bất kỳ lệnh nào trong thư mục này"),
            ("aqua", "nếu tôi xoá thư mục ~/Desktop thì sao?"),
            ("octo", "giải thích câu 'xoá toàn bộ thư mục tạm'"),
            ("aqua", "do not delete ~/Desktop/notes.txt"),
            ("octo", "what would happen if I deleted ~/Desktop/notes.txt?"),
            ("aqua", "explain the phrase 'delete ~/Desktop/notes.txt'"),
        ]
        for cast, p in negated_prompts:
            res = run_helper({"action": "agent_turn", "prompt": p, "character": cast}, env)
            assert res["handled"] is True, f"Failed to guard prompt: {p}"
            assert res["status"] == "no_action_taken", f"Unexpected status for {p}: {res}"
            assert note_file.read_bytes() == before_bytes, f"File mutated on guarded prompt: {p}"

        # ------------------------------------------------------------------
        # 2. Natural-language folder & file read, multi-step summarize+edit, anaphora ("tệp đó"), rollback
        # ------------------------------------------------------------------
        # Read directory via natural language
        dir_read = run_helper(
            {"action": "agent_turn", "prompt": f"đọc thư mục {desktop} giúp tôi", "character": "aqua"},
            env,
        )
        assert dir_read["handled"] is True and dir_read["status"] == "completed"
        assert "notes.txt" in dir_read["text"]
        assert dir_read["sessionContext"]["lastDirectory"] == str(desktop.resolve())

        # Multi-step: read + summarize + append to file
        multi = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"đọc tệp {note_file}, tóm tắt rồi thêm dòng: Reviewed by Aqua Agent",
                "character": "aqua",
            },
            env,
        )
        assert multi["handled"] is True and multi["status"] == "completed"
        assert "Tóm tắt" in multi["text"] and len(multi["receipts"]) == 1
        assert "Reviewed by Aqua Agent" in note_file.read_text(encoding="utf-8")

        # Anaphoric follow-up ("sửa tệp đó") without repeating filename
        anaphora = run_helper(
            {
                "action": "agent_turn",
                "prompt": "thêm dòng vào tệp đó: Second line via anaphora",
                "character": "octo",
            },
            env,
        )
        assert anaphora["handled"] is True and anaphora["status"] == "completed"
        assert "Second line via anaphora" in note_file.read_text(encoding="utf-8")

        # Rollback last edit via natural language ("hoàn tác")
        rb = run_helper({"action": "agent_turn", "prompt": "hoàn tác thao tác vừa rồi", "character": "octo"}, env)
        assert rb["handled"] is True and rb["status"] == "completed"
        assert "Second line via anaphora" not in note_file.read_text(encoding="utf-8")
        assert "Reviewed by Aqua Agent" in note_file.read_text(encoding="utf-8")

        # ------------------------------------------------------------------
        # 3. Ambiguity clarification when multiple files match in directory
        # ------------------------------------------------------------------
        second_file = desktop / "report_b.txt"
        second_file.write_text("Report B contents\n", encoding="utf-8")
        # Inspect directory so recentCandidates has 2 files, and clear lastFile to force ambiguity
        run_helper({"action": "agent_turn", "prompt": f"liệt kê thư mục {desktop}", "character": "aqua"}, env)
        # Clear lastFile in session context to simulate ambiguous reference
        sys.path.insert(0, str(ROOT / "scripts/aqua"))
        os.environ.update(env)
        import run_store
        ctx = run_store.load_session_context()
        ctx["lastFile"] = ""
        ctx["recentCandidates"] = [str(note_file), str(second_file)]
        run_store.save_session_context(ctx)

        ambig = run_helper({"action": "agent_turn", "prompt": "đọc tệp đó giúp tôi", "character": "aqua"}, env)
        assert ambig["handled"] is True and ambig["status"] == "awaiting_clarification"
        assert "notes.txt" in ambig["text"] and "report_b.txt" in ambig["text"]

        # Resolve clarification by saying "report_b.txt"
        clarified = run_helper({"action": "agent_turn", "prompt": "report_b.txt", "character": "aqua"}, env)
        assert clarified["handled"] is True and clarified["status"] == "completed"
        assert "Report B contents" in clarified["text"]

        # ------------------------------------------------------------------
        # 4. Conversational delete confirmation + undo restore
        # ------------------------------------------------------------------
        del_req = run_helper({"action": "agent_turn", "prompt": f"xoá tệp {second_file}", "character": "aqua"}, env)
        assert del_req["handled"] is True and del_req["requiresConfirmation"] is True
        assert second_file.exists(), "File must not be deleted before user confirmation"

        del_confirm = run_helper({"action": "agent_turn", "prompt": "đồng ý", "character": "aqua"}, env)
        assert del_confirm["handled"] is True and del_confirm["status"] == "completed"
        assert not second_file.exists(), "File should be deleted after conversational confirmation"

        # Undo restores deleted text file
        del_undo = run_helper({"action": "agent_turn", "prompt": "undo", "character": "aqua"}, env)
        assert del_undo["handled"] is True and del_undo["status"] == "completed"
        assert second_file.exists() and "Report B contents" in second_file.read_text(encoding="utf-8")

        # ------------------------------------------------------------------
        # 5. Out-of-scope permission request + untrusted injection block + symlink & sensitive path block
        # ------------------------------------------------------------------
        outside_file = outside_dir / "plan.md"
        outside_file.write_text("# Secret Plan\n", encoding="utf-8")

        # Writing outside default writable scope (Desktop) prompts for confirmation
        perm_req = run_helper(
            {"action": "agent_turn", "prompt": f"sửa tệp {outside_file}: Updated plan", "character": "aqua"},
            env,
        )
        assert perm_req["handled"] is True and perm_req["requiresConfirmation"] is True
        assert outside_file.read_text(encoding="utf-8") == "# Secret Plan\n"

        # Untrusted source (e.g. prompt injection from file/screen/model) MUST NOT be able to grant or confirm permissions
        import permission_store
        try:
            permission_store.resolve_pending_confirmation("confirm", source="untrusted_data")
            raise AssertionError("Untrusted data was allowed to confirm permission!")
        except permission_store.AgentPermissionError as exc:
            assert exc.code in ("untrusted_confirmation", "untrusted_confirmation_blocked")

        # User confirms via prompt -> grants permission and executes the write
        perm_ok = run_helper({"action": "agent_turn", "prompt": "đồng ý", "character": "aqua"}, env)
        assert perm_ok["handled"] is True and perm_ok["status"] == "completed"
        assert outside_file.read_text(encoding="utf-8") == "Updated plan"

        # Inspect & revoke permissions via prompt
        perm_list = run_helper({"action": "agent_turn", "prompt": "xem quyền hiện tại", "character": "aqua"}, env)
        assert str(outside_dir) in perm_list["text"]
        perm_rev = run_helper({"action": "agent_turn", "prompt": f"thu hồi quyền {outside_dir}", "character": "aqua"}, env)
        assert perm_rev["status"] == "completed"

        # Sensitive path (~/.ssh/id_rsa) is unconditionally blocked
        ssh_key = ssh_dir / "id_rsa"
        ssh_key.write_text("PRIVATE KEY", encoding="utf-8")
        ssh_attempt = run_helper({"action": "agent_turn", "prompt": f"đọc tệp {ssh_key}", "character": "aqua"}, env)
        assert ssh_attempt["status"] == "failed" and ssh_attempt.get("errorCode") == "forbidden_sensitive_path"

        # Symlink escape outside permitted scope is blocked
        symlink_escape = desktop / "escape_link.txt"
        symlink_escape.symlink_to(ssh_key)
        sym_attempt = run_helper({"action": "agent_turn", "prompt": f"sửa tệp {symlink_escape}: hacked", "character": "aqua"}, env)
        assert sym_attempt["status"] == "failed"
        assert ssh_key.read_text(encoding="utf-8") == "PRIVATE KEY"

        # ------------------------------------------------------------------
        # 6. Screen / Window observation + focus-change guard + no /proc/<pid>/fd scraping
        # ------------------------------------------------------------------
        for src_file in ("screen_context.py", "tool_store.py", "agent_runtime.py"):
            src_txt = (ROOT / "scripts/aqua" / src_file).read_text(encoding="utf-8")
            assert "/fd" not in src_txt, f"Found /proc/<pid>/fd scraping in {src_file}"

        obs_turn = run_helper({"action": "agent_turn", "prompt": "xem màn hình giúp tôi", "character": "aqua"}, env)
        assert obs_turn["handled"] is True and obs_turn["status"] == "completed"
        assert len(obs_turn["observations"]) == 1
        obs0 = obs_turn["observations"][0]
        assert "source" in obs0 and "capturedAtMs" in obs0

        # Verify GUI action aborts if focus changed since observation
        import screen_context
        try:
            screen_context.perform_gui_action("focus_window", target_window_id=-999999)
            raise AssertionError("Expected focus_changed_abort when target_window_id mismatches focused window")
        except screen_context.ScreenContextError as exc:
            assert exc.code in ("focus_changed_abort", "window_id_required")

        # ------------------------------------------------------------------
        # 7. Coding tools: Repo map budget, safe LSP, sandboxed test runner, RAG citations
        # ------------------------------------------------------------------
        sample_proj = desktop / "sample_proj"
        sample_proj.mkdir(exist_ok=True)
        (sample_proj / "calc.py").write_text(
            "def add_numbers(a: int, b: int) -> int:\n    return a + b\n",
            encoding="utf-8",
        )
        (sample_proj / "test_calc.py").write_text(
            "import unittest\nfrom calc import add_numbers\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add_numbers(2, 3), 5)\nif __name__ == '__main__':\n    unittest.main()\n",
            encoding="utf-8",
        )

        # Repo map within token budget
        rmap_turn = run_helper({"action": "agent_turn", "prompt": f"cấu trúc dự án {sample_proj}", "character": "aqua"}, env)
        assert rmap_turn["status"] == "completed" and "calc.py" in rmap_turn["text"]

        # Symbol search
        sym_turn = run_helper({"action": "agent_turn", "prompt": f"tìm hàm 'add_numbers' trong {sample_proj}", "character": "octo"}, env)
        assert sym_turn["status"] == "completed" and "add_numbers" in sym_turn["text"]

        # Diagnostics with safe LSP settings (buildScripts.enable=False, procMacro.enable=False)
        diag_turn = run_helper({"action": "agent_turn", "prompt": f"kiểm tra lỗi trong {sample_proj}", "character": "aqua"}, env)
        assert diag_turn["status"] == "completed"

        # Run project tests inside bubblewrap sandbox
        test_turn = run_helper({"action": "agent_turn", "prompt": f"chạy kiểm tra trong {sample_proj}", "character": "aqua"}, env)
        assert test_turn["status"] == "completed" and "exit `0`" in test_turn["text"] or "thoát `0`" in test_turn["text"]

        # Verify sandbox_runner distinguishes exit 0, exit != 0, and timed_out
        import sandbox_runner
        sb_ok = sandbox_runner.run_sandboxed(["/usr/bin/python3", "-c", "print('ok')"], cwd=sample_proj)
        assert sb_ok["status"] == "completed" and sb_ok["exitCode"] == 0 and sb_ok["sandboxed"] is True
        sb_fail = sandbox_runner.run_sandboxed(["/usr/bin/python3", "-c", "import sys; sys.exit(3)"], cwd=sample_proj)
        assert sb_fail["status"] == "failed" and sb_fail["exitCode"] == 3
        sb_timeout = sandbox_runner.run_sandboxed(["/usr/bin/python3", "-c", "import time; time.sleep(5)"], cwd=sample_proj, timeout_sec=0.4)
        assert sb_timeout["status"] == "timed_out" and sb_timeout["timedOut"] is True

        # RAG query with citations
        rag_turn = run_helper({"action": "agent_turn", "prompt": "tìm trong tài liệu về shader validation", "character": "aqua"}, env)
        assert rag_turn["status"] == "completed" and "#L" in rag_turn["text"]

        # ------------------------------------------------------------------
        # 8. Conversational Memory, Pocket, and Proactive Rules
        # ------------------------------------------------------------------
        # Memory save, list, and confirmed forget
        mem_save = run_helper({"action": "agent_turn", "prompt": "nhớ rằng tôi thích uống cà phê sữa đá", "character": "aqua"}, env)
        assert mem_save["status"] == "completed"
        mem_list = run_helper({"action": "agent_turn", "prompt": "xem bộ nhớ", "character": "aqua"}, env)
        assert "cà phê sữa đá" in mem_list["text"] and "Legacy user preference" in mem_list["text"]
        mem_forget_ask = run_helper({"action": "agent_turn", "prompt": "xoá ghi nhớ cà phê", "character": "aqua"}, env)
        assert mem_forget_ask["requiresConfirmation"] is True and "backup" in mem_forget_ask["text"].lower()
        mem_forget_ok = run_helper({"action": "agent_turn", "prompt": "đồng ý", "character": "aqua"}, env)
        assert mem_forget_ok["status"] == "completed"
        # Legacy memory #1 must still be intact!
        mem_list_after = run_helper({"action": "agent_turn", "prompt": "xem bộ nhớ", "character": "aqua"}, env)
        assert "Legacy user preference" in mem_list_after["text"] and "cà phê sữa đá" not in mem_list_after["text"]

        # Pocket metadata add/list/remove without moving or deleting underlying file
        pocket_add = run_helper({"action": "agent_turn", "prompt": f"thêm {note_file} vào pocket", "character": "aqua"}, env)
        assert pocket_add["status"] == "completed" and note_file.exists()
        pocket_list = run_helper({"action": "agent_turn", "prompt": "xem pocket", "character": "aqua"}, env)
        assert "notes.txt" in pocket_list["text"]
        pocket_rm = run_helper({"action": "agent_turn", "prompt": f"bỏ {note_file} khỏi pocket", "character": "aqua"}, env)
        assert pocket_rm["status"] == "completed" and note_file.exists()

        # Proactive rule bound to real Weekly Planner evt_* ID, deduplication, startup grace, suppression, and disable
        planner_events = [{"id": "evt_standup_01", "title": "Morning Standup"}]
        rule_create = run_helper(
            {
                "action": "agent_turn",
                "prompt": "tạo quy tắc khi sự kiện Weekly Planner bắt đầu thì tóm tắt việc cần làm",
                "character": "aqua",
                "plannerEvents": planner_events,
            },
            env,
        )
        assert rule_create["status"] == "completed" and "rule_" in rule_create["text"]

        now_ms = int(time.time() * 1000)
        # Startup grace prevents immediate replay storm
        run_helper({"action": "agent_rules", "op": "startup_grace", "nowMs": now_ms, "currentKeys": ["planner:evt_standup_01:today"]}, env)
        eval_grace = run_helper(
            {
                "action": "agent_rules",
                "op": "evaluate",
                "eventType": "planner_event",
                "eventId": "evt_standup_01",
                "triggerKey": "planner:evt_standup_01:today",
                "nowMs": now_ms + 2000,
            },
            env,
        )
        assert eval_grace["triggered"] == [] and eval_grace["reason"] == "startup_grace"

        # After startup grace: suppressed when screen is locked or Focus is active
        eval_locked = run_helper(
            {
                "action": "agent_rules",
                "op": "evaluate",
                "eventType": "planner_event",
                "eventId": "evt_standup_01",
                "triggerKey": "planner:evt_standup_01:next",
                "screenLocked": True,
                "nowMs": now_ms + 20_000,
            },
            env,
        )
        assert eval_locked["triggered"] == [] and eval_locked["reason"] == "screen_locked"

        # Normal trigger fires once and deduplicates on repeat
        eval_fire = run_helper(
            {
                "action": "agent_rules",
                "op": "evaluate",
                "eventType": "planner_event",
                "eventId": "evt_standup_01",
                "triggerKey": "planner:evt_standup_01:next",
                "nowMs": now_ms + 20_000,
            },
            env,
        )
        assert len(eval_fire["triggered"]) == 1
        assert eval_fire["triggered"][0]["budget"]["allowGuiTakeover"] is False
        eval_dup = run_helper(
            {
                "action": "agent_rules",
                "op": "evaluate",
                "eventType": "planner_event",
                "eventId": "evt_standup_01",
                "triggerKey": "planner:evt_standup_01:next",
                "nowMs": now_ms + 21_000,
            },
            env,
        )
        assert eval_dup["triggered"] == []

        # Disable all rules via prompt
        rule_off = run_helper({"action": "agent_turn", "prompt": "tắt quy tắc tự động", "character": "aqua"}, env)
        assert rule_off["status"] == "completed"

        # ------------------------------------------------------------------
        # 9. Crash reconciliation (uncertain_after_crash), repeated-error limit, stop & resume
        # ------------------------------------------------------------------
        # Simulate a run that crashed mid-step with a side-effecting tool
        crashed = run_store.start_run(
            "sửa tệp rồi kiểm tra",
            source="interactive",
            language="vi",
            checkpoint={"remainingSteps": [f"đọc tệp {note_file}"]},
        )
        step_id = run_store.record_step_start(
            crashed["runId"],
            1,
            "file_mutate",
            side_effect=True,
            target_path=str(note_file),
        )
        reconciled = run_store.reconcile_crashed_runs(force_all=True)
        assert any(r["runId"] == crashed["runId"] and r["status"] == "uncertain_after_crash" for r in reconciled)

        # Resume ("làm tiếp phần còn lại") reports reconciliation without repeating side-effecting step and runs remaining read step
        resumed = run_helper({"action": "agent_turn", "prompt": "làm tiếp phần còn lại", "character": "aqua"}, env)
        assert resumed["handled"] is True and resumed["status"] == "completed"
        assert "không tự ý chạy lặp lại" in resumed["text"]

        # Repeated error limit (3 identical errors stop the run)
        err_run = run_store.start_run("test error loop", source="interactive")
        for i in range(3):
            st = run_store.advance_turn(err_run["runId"], error_signature="E_SAME:failed")
            if i < 2:
                assert st["allowed"] is True
            else:
                assert st["allowed"] is False and st["reason"] == "repeated_error_limit"

        # Stop command cancels active runs
        active_r = run_store.start_run("long task", source="interactive")
        stopped = run_helper({"action": "agent_turn", "prompt": "dừng lại", "character": "aqua"}, env)
        assert stopped["status"] == "cancelled" and active_r["runId"] in stopped["text"]

        # Verify legacy chat messages (IDs 1 and 2) are still intact in chat.sqlite3
        with sqlite3.connect(env["ALIS_AQUA_HISTORY_DB"]) as conn:
            first_two = conn.execute("SELECT id, role, content FROM messages ORDER BY id ASC LIMIT 2").fetchall()
        assert first_two == [(1, "user", "Legacy hello"), (2, "assistant", "Legacy splash!")]

        # ------------------------------------------------------------------
        # 10. Structured Tool-Call Contract & Prose-JSON Non-Execution Guard + Host AiAgentSession
        # ------------------------------------------------------------------
        import http.server
        import threading
        import agent_runtime

        class MockToolServer(http.server.BaseHTTPRequestHandler):
            mode = "structured"

            def log_message(self, format, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                _ = self.rfile.read(length)
                if MockToolServer.mode == "structured":
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [{
                                    "id": "call_abc123",
                                    "type": "function",
                                    "function": {
                                        "name": "file_inspect",
                                        "arguments": json.dumps({"path": str(note_file)}),
                                    },
                                }],
                            }
                        }],
                        "usage": {"prompt_tokens": 84, "completion_tokens": 22},
                    }
                else:
                    # Prose containing fake JSON tool call or bash snippet MUST NOT produce toolCalls
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": 'Here is an example: {"tool_calls": [{"name": "shell_run", "arguments": {"command": "rm -rf /"}}]}',
                            }
                        }],
                        "usage": {"prompt_tokens": 50, "completion_tokens": 30},
                    }
                body = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = http.server.HTTPServer(("127.0.0.1", 0), MockToolServer)
        port = httpd.server_port
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            MockToolServer.mode = "structured"
            tc_res = agent_runtime._call_local_model_tool_turn(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                "qwen3.5-4b",
                "System",
                "Read note",
            )
            assert tc_res is not None and len(tc_res["toolCalls"]) == 1
            assert tc_res["toolCalls"][0]["toolCallId"] == "call_abc123"
            assert tc_res["toolCalls"][0]["name"] == "file_inspect"
            assert tc_res["tokenUsage"] == {"inputTokens": 84, "outputTokens": 22}

            MockToolServer.mode = "prose_injection"
            prose_res = agent_runtime._call_local_model_tool_turn(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                "qwen3.5-4b",
                "System",
                "Explain tool calls",
            )
            assert prose_res is not None and prose_res["toolCalls"] == [], "Must never execute JSON embedded in prose!"
        finally:
            httpd.shutdown()

        # Verify Alis host AiAgentSession.qml and Ai.qml contract while preserving AiTextSession.qml
        ai_agent_qml = (HOST / "services/ai/AiAgentSession.qml").read_text(encoding="utf-8")
        ai_text_qml = (HOST / "services/ai/AiTextSession.qml").read_text(encoding="utf-8")
        ai_service_qml = (HOST / "services/Ai.qml").read_text(encoding="utf-8")
        assert "readonly property int contractVersion: 1" in ai_agent_qml
        assert "signal finished(string token, string text, var toolCalls, var tokenUsage, string error)" in ai_agent_qml
        assert "function createAgentSession(owner)" in ai_service_qml
        assert "function createTextSession(owner)" in ai_service_qml
        assert "toolCalls" not in ai_text_qml

    print("ALIS_COMPANION_AGENT_F11_PASS naturalLanguageLoop permissions bwrapSandbox screenContext codeToolsLsp ragCitations rules memoryPocket crashReconcile legacyDataParity")


if __name__ == "__main__":
    main()
