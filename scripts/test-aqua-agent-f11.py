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
        mock_bin = tmp_path / "bin"
        hw_log = tmp_path / "hw_mock.log"
        for d in (xdg_state, xdg_config, desktop, outside_dir, ssh_dir, mock_bin):
            d.mkdir(parents=True, exist_ok=True)

        # Mock wpctl / brightnessctl / playerctl so tests never mutate the user's live audio or display
        wpctl_mock = mock_bin / "wpctl"
        wpctl_mock.write_text(
            f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
log = Path({str(hw_log)!r})
with log.open("a", encoding="utf-8") as f:
    f.write(json.dumps(["wpctl"] + sys.argv[1:]) + "\\n")
if len(sys.argv) > 1 and sys.argv[1] == "get-volume":
    print("Volume: 0.50")
""",
            encoding="utf-8",
        )
        wpctl_mock.chmod(0o755)

        bctl_mock = mock_bin / "brightnessctl"
        bctl_mock.write_text(
            f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
log = Path({str(hw_log)!r})
with log.open("a", encoding="utf-8") as f:
    f.write(json.dumps(["brightnessctl"] + sys.argv[1:]) + "\\n")
if len(sys.argv) > 1 and sys.argv[1] == "info":
    print("backlight,backlight,100,50%,200")
""",
            encoding="utf-8",
        )
        bctl_mock.chmod(0o755)

        env = os.environ.copy()
        env.update({
            "HOME": str(fake_home),
            "XDG_STATE_HOME": str(xdg_state),
            "XDG_CONFIG_HOME": str(xdg_config),
            "ALIS_ROOT": str(HOST),
            "ALIS_AQUA_TEST_NO_SPAWN": "1",
            "ALIS_TEST_ISOLATED": "1",
            "PATH": f"{mock_bin}:{env.get('PATH', '')}",
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
        # 5. Full User Home ($HOME) access + Complete 25+ item listing + Sudo-gated System path access
        # ------------------------------------------------------------------
        outside_file = outside_dir / "plan.md"
        outside_file.write_text("# Secret Plan\n", encoding="utf-8")

        # 5a. Inside $HOME (including subdirectories outside Desktop, symlinks inside $HOME, and dot-dirs):
        # Full read/write/execute access works immediately without confirmation!
        home_write = run_helper(
            {"action": "agent_turn", "prompt": f"sửa tệp {outside_file}: Updated in Home directly", "character": "aqua"},
            env,
        )
        assert home_write["handled"] is True and home_write["status"] == "completed"
        assert outside_file.read_text(encoding="utf-8") == "Updated in Home directly"

        dot_file = ssh_dir / "custom_config"
        dot_file.write_text("Host local-dev\n", encoding="utf-8")
        dot_read = run_helper({"action": "agent_turn", "prompt": f"đọc tệp {dot_file}", "character": "aqua"}, env)
        assert dot_read["status"] == "completed" and "Host local-dev" in dot_read["text"]

        # 5b. Complete listing of >20 items + symlinks inside ~/Downloads, plus follow-up `list files` / `list all`
        downloads_dir = fake_home / "Downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        shared_music = fake_home / "Shared_Documents/Music/Mics"
        shared_music.mkdir(parents=True, exist_ok=True)
        (shared_music / "track01.flac").write_text("audio", encoding="utf-8")
        qobuz_link = downloads_dir / "Qobuz"
        if not qobuz_link.exists():
            qobuz_link.symlink_to(shared_music)
        for idx in range(1, 26):
            (downloads_dir / f"item_{idx:02d}.txt").write_text(f"Item {idx}\n", encoding="utf-8")

        dl_access = run_helper({"action": "agent_turn", "prompt": "access Downloads", "character": "aqua"}, env)
        assert dl_access["handled"] is True and dl_access["status"] == "completed"
        assert "Qobuz" in dl_access["text"], f"Symlink Qobuz missing from listing: {dl_access['text']}"
        for idx in range(1, 26):
            assert f"item_{idx:02d}.txt" in dl_access["text"], f"item_{idx:02d}.txt truncated from access Downloads!"

        # Follow-up `list files` and `list all` must route to agent_turn (never fall through to chat) and list all 26 items
        dl_list_files = run_helper({"action": "agent_turn", "prompt": "list files", "character": "aqua"}, env)
        assert dl_list_files["handled"] is True and dl_list_files["status"] == "completed"
        for idx in range(1, 26):
            assert f"item_{idx:02d}.txt" in dl_list_files["text"], f"item_{idx:02d}.txt missing from 'list files'!"

        dl_list_all = run_helper({"action": "agent_turn", "prompt": "liệt kê tất cả", "character": "aqua"}, env)
        assert dl_list_all["handled"] is True and dl_list_all["status"] == "completed"
        for idx in range(1, 26):
            assert f"item_{idx:02d}.txt" in dl_list_all["text"], f"item_{idx:02d}.txt missing from 'liệt kê tất cả'!"

        # Traversing symlink inside $HOME (`access Qobuz`) works seamlessly
        qobuz_access = run_helper({"action": "agent_turn", "prompt": "access Qobuz", "character": "aqua"}, env)
        assert qobuz_access["handled"] is True and qobuz_access["status"] == "completed"
        assert "track01.flac" in qobuz_access["text"]

        # 5c. Explicit revocation inside $HOME + conversational re-grant + untrusted injection block
        perm_rev = run_helper({"action": "agent_turn", "prompt": f"thu hồi quyền {outside_dir}", "character": "aqua"}, env)
        assert perm_rev["status"] == "completed"

        perm_req = run_helper(
            {"action": "agent_turn", "prompt": f"sửa tệp {outside_file}: Updated after re-grant", "character": "aqua"},
            env,
        )
        assert perm_req["handled"] is True and perm_req["requiresConfirmation"] is True
        assert outside_file.read_text(encoding="utf-8") == "Updated in Home directly"

        import permission_store
        try:
            permission_store.resolve_pending_confirmation("confirm", source="untrusted_data")
            raise AssertionError("Untrusted data was allowed to confirm permission!")
        except permission_store.AgentPermissionError as exc:
            assert exc.code in ("untrusted_confirmation", "untrusted_confirmation_blocked")

        perm_ok = run_helper({"action": "agent_turn", "prompt": "đồng ý", "character": "aqua"}, env)
        assert perm_ok["handled"] is True and perm_ok["status"] == "completed"
        assert outside_file.read_text(encoding="utf-8") == "Updated after re-grant"

        # 5d. System path outside $HOME requires user permission AND sudo password (with history redaction)
        system_dir = tmp_path / "system_etc"
        system_dir.mkdir(parents=True, exist_ok=True)
        system_file = system_dir / "sys_config.conf"
        system_file.write_text("SYSTEM_MODE=default\n", encoding="utf-8")
        env["AQUA_TEST_SUDO_PASSWORD"] = "super_secret_sudo_pw_99"

        sys_req = run_helper(
            {"action": "agent_turn", "prompt": f"đọc tệp {system_file}", "character": "aqua"},
            env,
        )
        assert sys_req["handled"] is True
        assert sys_req["requiresConfirmation"] is True and sys_req["requiresSudoPassword"] is True
        assert sys_req["status"] == "awaiting_sudo_password"

        # Wrong sudo password must be rejected and keep awaiting_sudo_password active
        sys_wrong_pw = run_helper(
            {"action": "agent_turn", "prompt": "wrong_password_123", "character": "aqua"},
            env,
        )
        assert sys_wrong_pw["handled"] is True
        assert sys_wrong_pw["status"] == "awaiting_sudo_password" and sys_wrong_pw["requiresSudoPassword"] is True

        # Correct sudo password executes the system operation and redacts password in chat.sqlite3
        sys_right_pw = run_helper(
            {"action": "agent_turn", "prompt": "[sudo password]", "sudoPassword": "super_secret_sudo_pw_99", "character": "aqua"},
            env,
        )
        assert sys_right_pw["handled"] is True and sys_right_pw["status"] == "completed"
        assert sys_right_pw["requiresSudoPassword"] is False
        assert "SYSTEM_MODE=default" in sys_right_pw["text"]

        # Verify raw sudo password was NEVER stored in chat.sqlite3
        with sqlite3.connect(env["ALIS_AQUA_HISTORY_DB"]) as conn:
            all_msgs = [r[0] for r in conn.execute("SELECT content FROM messages").fetchall()]
        assert not any("super_secret_sudo_pw_99" in m for m in all_msgs), "Plaintext sudo password leaked into chat.sqlite3!"
        assert not any("wrong_password_123" in m for m in all_msgs), "Failed sudo password attempt leaked into chat.sqlite3!"
        assert any("•••••••• (sudo password)" in m for m in all_msgs), "Redacted sudo placeholder missing from chat.sqlite3!"

        # System operation cancellation via 'huỷ'
        sys_edit_req = run_helper(
            {"action": "agent_turn", "prompt": f"sửa tệp {system_file}: HACKED", "character": "aqua"},
            env,
        )
        assert sys_edit_req["requiresSudoPassword"] is True
        sys_cancel = run_helper({"action": "agent_turn", "prompt": "huỷ", "character": "aqua"}, env)
        assert sys_cancel["status"] == "cancelled" and sys_cancel["requiresSudoPassword"] is False
        assert system_file.read_text(encoding="utf-8") == "SYSTEM_MODE=default\n"

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

        # ------------------------------------------------------------------
        # 11. RAG Sources, MCP (Built-in + Stdio JSON-RPC 2.0), and Skills (SKILL.md)
        # ------------------------------------------------------------------
        kb_dir = fake_home / "KnowledgeBase"
        kb_dir.mkdir(parents=True, exist_ok=True)
        (kb_dir / "abyss_guide.md").write_text(
            "# Abyss Fluid Engine\nThe Abyss fluid engine uses signed-distance metaballs and specular rim lighting.\n",
            encoding="utf-8",
        )
        rag_add = run_helper({"action": "rag_action", "op": "add", "path": str(kb_dir), "label": "AbyssKB"}, env)
        assert rag_add["source"]["label"] == "AbyssKB"
        rag_q = run_helper({"action": "rag_action", "op": "query", "query": "signed-distance metaballs"}, env)
        assert any("abyss_guide.md" in s for s in rag_q["snippets"]), rag_q
        rag_turn = run_helper({"action": "agent_turn", "prompt": "/rag search signed-distance metaballs", "character": "aqua"}, env)
        assert rag_turn["handled"] is True and "abyss_guide.md" in rag_turn["text"]

        # MCP built-in + external stdio JSON-RPC 2.0 server
        mcp_list = run_helper({"action": "mcp_action", "op": "list_servers"}, env)
        assert any(s["name"] == "companion-system" for s in mcp_list["servers"])
        mcp_call = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "companion-system", "tool": "system_info", "arguments": {}},
            env,
        )
        assert mcp_call["result"]["isError"] is False and "OS" in mcp_call["result"]["content"][0]["text"] or "os" in mcp_call["result"]["content"][0]["text"]

        # Register stdio MCP server pointing to mcp_client.py --stdio-server
        stdio_srv = run_helper(
            {
                "action": "mcp_action",
                "op": "add_server",
                "name": "local-stdio-test",
                "command": "/usr/bin/python3",
                "args": [str(ROOT / "scripts/aqua/mcp_client.py"), "--stdio-server"],
            },
            env,
        )
        assert stdio_srv["server"]["transport"] == "stdio"
        stdio_tools = run_helper({"action": "mcp_action", "op": "list_tools", "server": "local-stdio-test"}, env)
        assert any(t["name"] == "system_info" for t in stdio_tools["tools"])
        stdio_call = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "local-stdio-test", "tool": "system_info", "arguments": {}},
            env,
        )
        assert stdio_call["result"]["isError"] is False
        mcp_turn = run_helper({"action": "agent_turn", "prompt": "/mcp call local-stdio-test.system_info", "character": "aqua"}, env)
        assert mcp_turn["handled"] is True and "local-stdio-test.system_info" in mcp_turn["text"]
        run_helper({"action": "mcp_action", "op": "remove_server", "name": "local-stdio-test"}, env)

        # Agent Skills (SKILL.md) discovery, execution & lifecycle
        sk_list = run_helper({"action": "skill_action", "op": "list"}, env)
        skill_names = {s["name"] for s in sk_list["skills"]}
        assert {
            "system-diagnostics",
            "code-assistant",
            "rag-researcher",
            "focus-planner",
            "screen-copilot",
            "desktop-organizer",
            "window-workspace-layout",
            "git-daily-brief",
            "system-tuner",
            "clipboard-transformer",
        }.issubset(skill_names), skill_names
        sk_run = run_helper({"action": "skill_action", "op": "run", "name": "system-diagnostics"}, env)
        assert sk_run["result"]["skill"] == "system-diagnostics" and "OS:" in sk_run["result"]["output"]
        sk_tuner = run_helper({"action": "skill_action", "op": "run", "name": "system-tuner"}, env)
        assert sk_tuner["result"]["skill"] == "system-tuner" and "System Health Snapshot:" in sk_tuner["result"]["output"]
        sk_create = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/skill create custom-helper: Custom helper skill | # Workflow\nAlways verify inputs.",
                "character": "aqua",
            },
            env,
        )
        assert sk_create["handled"] is True and "custom-helper" in sk_create["text"]
        sk_del = run_helper({"action": "skill_action", "op": "delete", "name": "custom-helper"}, env)
        assert sk_del["deleted"] is True

        # ------------------------------------------------------------------
        # 12. Chat Threads & Projects (CRUD, Pin, Drag-and-Drop Move/Reorder,
        #     Project Workspace Binding) + Full-Authority Desktop Agent Tools
        # ------------------------------------------------------------------
        proj_ws = fake_home / "Projects/AlisCore"
        proj_ws.mkdir(parents=True, exist_ok=True)
        (proj_ws / "README.md").write_text("# AlisCore Project\n", encoding="utf-8")

        p1 = run_helper(
            {
                "action": "chat_threads",
                "subaction": "create_project",
                "character": "aqua",
                "name": "Alis Core",
                "workspacePath": str(proj_ws),
            },
            env,
        )
        assert len(p1["projects"]) == 1
        proj_id = p1["projects"][0]["id"]
        assert p1["projects"][0]["workspacePath"] == str(proj_ws)

        p2 = run_helper(
            {
                "action": "chat_threads",
                "subaction": "create_project",
                "character": "aqua",
                "name": "Secondary Project",
            },
            env,
        )
        proj2_id = [p["id"] for p in p2["projects"] if p["id"] != proj_id][0]

        # Reorder projects, pin, rename, toggle collapse
        p_move = run_helper(
            {"action": "chat_threads", "subaction": "move_project", "character": "aqua", "projectId": proj2_id, "targetIndex": 0},
            env,
        )
        assert p_move["projects"][0]["id"] == proj2_id
        p_pin = run_helper(
            {"action": "chat_threads", "subaction": "pin_project", "character": "aqua", "projectId": proj_id, "pinned": True},
            env,
        )
        assert p_pin["projects"][0]["id"] == proj_id and p_pin["projects"][0]["pinned"] is True
        p_ren = run_helper(
            {"action": "chat_threads", "subaction": "rename_project", "character": "aqua", "projectId": proj_id, "name": "Alis Core Dev"},
            env,
        )
        assert p_ren["projects"][0]["name"] == "Alis Core Dev"
        p_col = run_helper(
            {"action": "chat_threads", "subaction": "toggle_project_collapsed", "character": "aqua", "projectId": proj_id},
            env,
        )
        assert p_col["projects"][0]["collapsed"] is True

        # Create threads, auto-title on first user message, pin, move into project, reorder
        t1 = run_helper(
            {"action": "chat_threads", "subaction": "create_thread", "character": "aqua", "title": "New Chat"},
            env,
        )
        t1_id = t1["activeThreadId"]
        app1 = run_helper(
            {
                "action": "history_append",
                "character": "aqua",
                "threadId": t1_id,
                "prompt": "Investigate Wayland compositor frame timing",
                "reply": "Checking frame timing telemetry now.",
            },
            env,
        )
        t1_meta = [t for t in app1["threads"] if t["id"] == t1_id][0]
        assert "Investigate Wayland compositor" in t1_meta["title"], t1_meta

        # Move thread into project (drag-and-drop into project)
        t_move = run_helper(
            {
                "action": "chat_threads",
                "subaction": "move_thread",
                "character": "aqua",
                "threadId": t1_id,
                "projectId": proj_id,
                "targetIndex": 0,
            },
            env,
        )
        t1_meta = [t for t in t_move["threads"] if t["id"] == t1_id][0]
        assert t1_meta["projectId"] == proj_id

        # Active project workspace auto-binding during agent_turn (even after session_context is cleared)
        run_store.save_session_context({"lastProject": "", "lastDirectory": ""})
        ws_turn = run_helper(
            {"action": "agent_turn", "prompt": "list files", "character": "aqua", "threadId": t1_id},
            env,
        )
        assert ws_turn["handled"] is True and "README.md" in ws_turn["text"], ws_turn["text"]
        assert ws_turn["activeThreadId"] == t1_id and len(ws_turn["projects"]) >= 2
        assert Path(run_store.load_session_context()["lastProject"]).resolve() == proj_ws.resolve()

        # Full-authority desktop agent telemetry, process management, service status, volume/brightness normalization, and MCP tools
        sys_turn = run_helper(
            {"action": "agent_turn", "prompt": "check cpu and ram usage", "character": "aqua", "threadId": t1_id},
            env,
        )
        assert sys_turn["handled"] is True and "RAM:" in sys_turn["text"], sys_turn["text"]

        proc_turn = run_helper(
            {"action": "agent_turn", "prompt": "top processes", "character": "aqua", "threadId": t1_id},
            env,
        )
        assert proc_turn["handled"] is True and "PID" in proc_turn["text"], proc_turn["text"]

        srv_turn = run_helper(
            {"action": "agent_turn", "prompt": "service status pipewire", "character": "aqua", "threadId": t1_id},
            env,
        )
        assert srv_turn["handled"] is True and srv_turn["status"] == "completed", srv_turn

        vol_turn = run_helper(
            {"action": "agent_turn", "prompt": "tăng âm lượng 10%", "character": "aqua", "threadId": t1_id},
            env,
        )
        assert vol_turn["handled"] is True and vol_turn["status"] == "completed", vol_turn

        bri_turn = run_helper(
            {"action": "agent_turn", "prompt": "giảm độ sáng", "character": "aqua", "threadId": t1_id},
            env,
        )
        assert bri_turn["handled"] is True and bri_turn["status"] == "completed", bri_turn

        import desktop_control
        prev_path = os.environ.get("PATH", "")
        prev_isolated = os.environ.get("ALIS_TEST_ISOLATED")
        os.environ["PATH"] = env["PATH"]
        os.environ["ALIS_TEST_ISOLATED"] = "1"
        try:
            assert desktop_control.manage_desktop("volume", value="up")["value"] == "5%+"
            assert desktop_control.manage_desktop("volume", value="down")["value"] == "5%-"
            assert desktop_control.manage_desktop("volume", value="+10%")["value"] == "10%+"
            assert desktop_control.manage_desktop("brightness", value="up")["value"] == "+10%"
            assert desktop_control.manage_desktop("brightness", value="down")["value"] == "10%-"
        finally:
            os.environ["PATH"] = prev_path
            if prev_isolated is None:
                os.environ.pop("ALIS_TEST_ISOLATED", None)
            else:
                os.environ["ALIS_TEST_ISOLATED"] = prev_isolated
        hw_entries = [json.loads(line) for line in hw_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any(e[:2] == ["wpctl", "set-volume"] and e[-1] == "10%+" for e in hw_entries), hw_entries
        assert any(e[:2] == ["brightnessctl", "set"] and e[-1] == "10%-" for e in hw_entries), hw_entries

        # MCP process_list & git_status + LLM tool call _execute_llm_tool_call for process_manage & git_inspect
        mcp_proc = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "companion-system",
                "tool": "process_list",
                "arguments": {"action": "list", "query": "python"},
            },
            env,
        )
        assert mcp_proc["result"]["isError"] is False and "PID" in mcp_proc["result"]["content"][0]["text"]

        mcp_git = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "companion-system",
                "tool": "git_status",
                "arguments": {"workspace": str(ROOT), "subaction": "status"},
            },
            env,
        )
        assert mcp_git["result"]["isError"] is False and "Git status" in mcp_git["result"]["content"][0]["text"]

        llm_proc_out = agent_runtime._execute_llm_tool_call(
            {"name": "process_manage", "arguments": {"action": "list", "query": "python", "signal": "TERM"}},
            run_store.load_session_context(),
            ROOT,
            int(time.time() * 1000),
        )
        assert "Process list" in llm_proc_out

        llm_git_out = agent_runtime._execute_llm_tool_call(
            {"name": "git_inspect", "arguments": {"workspace": str(ROOT), "action": "status", "args": ""}},
            run_store.load_session_context(),
            ROOT,
            int(time.time() * 1000),
        )
        assert "Git status" in llm_git_out

        # Also verify _execute_llm_tool_call with "args" (dict), JSON string arguments, nested OpenAI "function", and malformed JSON
        llm_args_dict_out = agent_runtime._execute_llm_tool_call(
            {"name": "git_inspect", "args": {"workspace": str(ROOT), "action": "status", "args": ""}},
            run_store.load_session_context(),
            ROOT,
            int(time.time() * 1000),
        )
        assert "Git status" in llm_args_dict_out
        llm_args_json_out = agent_runtime._execute_llm_tool_call(
            {"name": "git_inspect", "arguments": json.dumps({"workspace": str(ROOT), "action": "status", "args": ""})},
            run_store.load_session_context(),
            ROOT,
            int(time.time() * 1000),
        )
        assert "Git status" in llm_args_json_out
        llm_nested_fn_out = agent_runtime._execute_llm_tool_call(
            {"type": "function", "function": {"name": "git_inspect", "arguments": json.dumps({"workspace": str(ROOT), "action": "status", "args": ""})}},
            run_store.load_session_context(),
            ROOT,
            int(time.time() * 1000),
        )
        assert "Git status" in llm_nested_fn_out

        # Malformed/truncated JSON arguments must NOT fall back to {} and execute the tool
        bad_target = proj_ws / "must_not_be_created.txt"
        malformed_out = agent_runtime._execute_llm_tool_call(
            {"name": "file_write", "arguments": '{"op": "edit", "path": "' + str(bad_target) + '", "content": "unterminated'},
            run_store.load_session_context(),
            ROOT,
            int(time.time() * 1000),
        )
        assert "malformed tool arguments JSON" in malformed_out
        assert not bad_target.exists()

        # Verify RAG _collect_doc_chunks mtime-based cache hit, in-place key replacement, and dot-ancestor directory support
        import code_tools
        rag_test_dir = tmp_path / ".local" / "share" / "rag_cache_test"
        rag_test_dir.mkdir(parents=True, exist_ok=True)
        hidden_sub = rag_test_dir / ".git"
        hidden_sub.mkdir(parents=True, exist_ok=True)
        (hidden_sub / "ignored.md").write_text("# Secret Git Internal\nShould be pruned.\n", encoding="utf-8")
        rag_doc = rag_test_dir / "guide.md"
        rag_doc.write_text("# Cache Heading\nInitial cached content alpha beta gamma.\n", encoding="utf-8")
        code_tools._DOC_CHUNK_CACHE.clear()
        chunks_1 = code_tools._collect_doc_chunks(rag_test_dir, extra_dirs=[str(rag_test_dir)])
        assert any("Initial cached content" in c["text"] for c in chunks_1), "Dot-prefixed ancestor (.local) must not cause RAG files to be skipped!"
        assert not any("Should be pruned" in c["text"] for c in chunks_1), "Hidden subdirectory (.git) inside RAG root must still be pruned!"
        cache_keys_1 = len(code_tools._DOC_CHUNK_CACHE)
        assert cache_keys_1 >= 1
        # Mutate returned list in-place -> cached copy must remain uncorrupted
        chunks_1.clear()
        chunks_1b = code_tools._collect_doc_chunks(rag_test_dir, extra_dirs=[str(rag_test_dir)])
        assert any("Initial cached content" in c["text"] for c in chunks_1b)
        # Modify file content & size -> cache must update in-place without growing stale keys
        rag_doc.write_text("# Cache Heading\nUpdated fresh content delta epsilon zeta theta.\n", encoding="utf-8")
        chunks_2 = code_tools._collect_doc_chunks(rag_test_dir, extra_dirs=[str(rag_test_dir)])
        assert any("Updated fresh content" in c["text"] for c in chunks_2)
        assert len(code_tools._DOC_CHUNK_CACHE) == cache_keys_1

        mcp_shell = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "companion-system",
                "tool": "shell_exec",
                "arguments": {"command": "echo FULL_AUTHORITY_OK", "cwd": str(proj_ws)},
            },
            env,
        )
        assert mcp_shell["result"]["isError"] is False and "FULL_AUTHORITY_OK" in mcp_shell["result"]["content"][0]["text"]

        # Thread search, SQL wildcard escaping, duplicate_thread (including >2000 row cap), and clear_thread
        t_search = run_helper(
            {"action": "chat_threads", "subaction": "search_threads", "character": "aqua", "query": "Wayland compositor"},
            env,
        )
        assert t_search["op"] == "search_threads" and t1_id in t_search["matchedThreadIds"]

        # SQL LIKE wildcard "_" must be escaped so it does not match threads without a literal underscore
        t_wildcard = run_helper(
            {"action": "chat_threads", "subaction": "search_threads", "character": "aqua", "query": "___nomatch___"},
            env,
        )
        assert t_wildcard["matchedThreadIds"] == []

        t_dup = run_helper(
            {"action": "chat_threads", "subaction": "duplicate_thread", "character": "aqua", "threadId": t1_id},
            env,
        )
        assert t_dup["op"] == "duplicate_thread" and t_dup["sourceThreadId"] == t1_id
        dup_id = t_dup["threadId"]
        assert dup_id != t1_id and t_dup["activeThreadId"] == dup_id
        assert any("Wayland compositor" in m["content"] for m in t_dup["messages"])
        assert t_dup["hasMore"] is False

        # Verify exact-60 messages -> hasMore is False; 61 messages -> hasMore is True; 2050 messages -> duplicate_thread caps at 2000
        with sqlite3.connect(env["ALIS_AQUA_HISTORY_DB"]) as conn:
            conn.execute("DELETE FROM messages WHERE id IN (SELECT message_id FROM thread_messages WHERE thread_id=?)", (dup_id,))
            conn.execute("DELETE FROM thread_messages WHERE thread_id=?", (dup_id,))
            for i in range(60):
                cur = conn.execute(
                    "INSERT INTO messages(role, content, created_ms, model) VALUES(?,?,?,?)",
                    ("user" if i % 2 == 0 else "assistant", f"Msg {i + 1}", 1000 + i, "fixture"),
                )
                conn.execute("INSERT INTO thread_messages(message_id, thread_id) VALUES(?,?)", (int(cur.lastrowid), dup_id))
        sel_60 = run_helper(
            {"action": "chat_threads", "subaction": "select_thread", "character": "aqua", "threadId": dup_id},
            env,
        )
        assert len(sel_60["messages"]) == 60 and sel_60["hasMore"] is False, "Exact 60 messages with no older rows must have hasMore=False!"

        with sqlite3.connect(env["ALIS_AQUA_HISTORY_DB"]) as conn:
            for i in range(60, 2050):
                cur = conn.execute(
                    "INSERT INTO messages(role, content, created_ms, model) VALUES(?,?,?,?)",
                    ("user" if i % 2 == 0 else "assistant", f"Msg {i + 1}", 1000 + i, "fixture"),
                )
                conn.execute("INSERT INTO thread_messages(message_id, thread_id) VALUES(?,?)", (int(cur.lastrowid), dup_id))
        sel_2050 = run_helper(
            {"action": "chat_threads", "subaction": "select_thread", "character": "aqua", "threadId": dup_id},
            env,
        )
        assert len(sel_2050["messages"]) == 60 and sel_2050["hasMore"] is True
        dup_large = run_helper(
            {"action": "chat_threads", "subaction": "duplicate_thread", "character": "aqua", "threadId": dup_id},
            env,
        )
        dup_large_id = dup_large["threadId"]
        with sqlite3.connect(env["ALIS_AQUA_HISTORY_DB"]) as conn:
            copied_count = conn.execute("SELECT COUNT(*) FROM thread_messages WHERE thread_id=?", (dup_large_id,)).fetchone()[0]
        assert copied_count == 2000, f"Expected duplicate_thread to cap at 2000 rows, got {copied_count}"
        run_helper({"action": "chat_threads", "subaction": "delete_thread", "character": "aqua", "threadId": dup_large_id}, env)

        # Verify thread switch/clear resets pendingSudoOp/pendingClarification and clears lastProject for unassigned threads
        run_store.save_session_context(
            {
                "pendingSudoOp": {"op": "shell", "command": "apt update"},
                "pendingClarification": {"intent": "file_write"},
                "lastProject": str(proj_ws),
            }
        )
        t_clear = run_helper(
            {"action": "chat_threads", "subaction": "clear_thread", "character": "aqua", "threadId": dup_id},
            env,
        )
        assert t_clear["op"] == "clear_thread" and t_clear["threadId"] == dup_id and len(t_clear["messages"]) == 0
        ctx_after_clear = run_store.load_session_context()
        assert ctx_after_clear.get("pendingSudoOp") is None and ctx_after_clear.get("pendingClarification") is None
        # Switch to unassigned default thread -> lastProject must be cleared to ""
        run_store.save_session_context({"pendingSudoOp": {"op": "shell", "command": "id"}, "lastProject": str(proj_ws)})
        run_helper(
            {"action": "chat_threads", "subaction": "select_thread", "character": "aqua", "threadId": "chat_default"},
            env,
        )
        ctx_unassigned = run_store.load_session_context()
        assert ctx_unassigned.get("pendingSudoOp") is None
        assert ctx_unassigned.get("lastProject") == ""

        # Original thread messages remain intact and selecting it restores its project workspace
        t_orig = run_helper(
            {"action": "chat_threads", "subaction": "select_thread", "character": "aqua", "threadId": t1_id},
            env,
        )
        assert any("Wayland compositor" in m["content"] for m in t_orig["messages"])
        assert run_store.load_session_context().get("lastProject") == str(proj_ws)

        # Delete project preserves threads (unassigns projectId) and clears lastProject
        del_proj = run_helper(
            {"action": "chat_threads", "subaction": "delete_project", "character": "aqua", "projectId": proj_id},
            env,
        )
        t1_after_proj_del = [t for t in del_proj["threads"] if t["id"] == t1_id][0]
        assert t1_after_proj_del["projectId"] == ""
        assert run_store.load_session_context().get("lastProject") == ""

        # =========================================================================
        # 17. Hermes Agent Self-Learning, Progressive Disclosure, Patching & /Commands
        # =========================================================================
        import skill_store
        # 17a. Progressive disclosure: Level 0 (metadata), Level 1 (inspect), Level 2 (references/)
        (proj_ws / "main.py").write_text("def run_core():\n    return True\n", encoding="utf-8")
        sk_learned_dir = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/learn {proj_ws}",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert sk_learned_dir["handled"] is True and sk_learned_dir["intent"] == "skill_manage"
        assert "references/source-map.md" in sk_learned_dir["text"] or "source-map.md" in sk_learned_dir["text"]
        assert isinstance(sk_learned_dir.get("skills"), list) and len(sk_learned_dir["skills"]) >= 5

        # Inspect Level 2 reference file created by /learn
        learned_skill_name = "alis-core-workflow"
        ref_res = run_helper(
            {
                "action": "skill_action",
                "subaction": "reference",
                "name": learned_skill_name,
                "reference": "source-map.md",
            },
            env,
        )
        assert ref_res["op"] == "reference" and "main.py" in ref_res["content"]

        # Write a new Level 2 reference doc and read it back; verify path traversal is rejected
        wref_res = run_helper(
            {
                "action": "skill_action",
                "subaction": "write_reference",
                "name": learned_skill_name,
                "reference": "architecture.md",
                "content": "# Architecture Notes\nAlways run unit tests before packaging.",
            },
            env,
        )
        assert wref_res["op"] == "write_reference" and "architecture.md" in wref_res["references"]
        try:
            skill_store.read_skill_reference(learned_skill_name, "../outside.md", companion_root=ROOT)
            raise AssertionError("Path traversal in skill reference should have raised SkillError")
        except skill_store.SkillError as exc:
            assert exc.code == "invalid_reference_path"

        # 17b. Targeted patch_skill (old_text -> new_text + append_note + version bump)
        patch_res = run_helper(
            {
                "action": "skill_action",
                "subaction": "patch",
                "name": learned_skill_name,
                "oldText": "Inspect key files and entrypoints before making edits.",
                "newText": "Inspect key files, references/source-map.md, and entrypoints before making edits.",
                "appendNote": "Always verify imports after refactoring.",
            },
            env,
        )
        assert patch_res["op"] == "patch" and patch_res["skill"]["version"] == "1.0.1"
        inspected_patched = skill_store.inspect_skill(learned_skill_name, companion_root=ROOT)
        assert "references/source-map.md, and entrypoints" in inspected_patched["instructions"]
        assert "Always verify imports after refactoring." in inspected_patched["instructions"]

        # Edge case: patch_skill with non-existent old_text raises skill_patch_mismatch
        try:
            skill_store.patch_skill(learned_skill_name, old_text="NON_EXISTENT_SPAN_XYZ", new_text="replacement", companion_root=ROOT)
            raise AssertionError("Expected SkillError(skill_patch_mismatch)")
        except skill_store.SkillError as exc:
            assert exc.code == "skill_patch_mismatch"

        # Copy-on-write patching of a built-in skill preserves built-in source while creating a user override
        builtin_patch = skill_store.patch_skill(
            "system-diagnostics",
            append_note="Check thermal throttling on ThinkPad sensors when CPU > 85C.",
            companion_root=ROOT,
        )
        assert builtin_patch["skill"]["version"] == "1.0.1"
        assert "Check thermal throttling" in skill_store.inspect_skill("system-diagnostics", companion_root=ROOT)["instructions"]

        # 17c. Natural-language /learn workflow synthesis & direct /<skill-name> slash invocation
        nl_learn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "tự học quy trình release-gate: Kiểm tra git status, chạy test suite và xác nhận version trước khi đóng gói",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert nl_learn["handled"] is True and nl_learn["intent"] == "skill_manage"
        assert any(s["name"] == "release-gate" for s in nl_learn.get("skills", []))

        # Invoke the newly learned skill directly via /release-gate
        direct_skill_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/release-gate",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert direct_skill_turn["handled"] is True and direct_skill_turn["intent"] == "skill_manage"
        assert "release-gate" in direct_skill_turn["text"]

        # 17d. Workflow self-reflection (/workflow) & slash file commands (/access, /edit, /undo)
        wf_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/workflow",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert wf_turn["handled"] is True and wf_turn["intent"] == "skill_manage"
        assert isinstance(wf_turn.get("workflowReflections"), list)

        slash_edit_target = desktop / "slash_cmd_test.txt"
        slash_edit = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/edit {slash_edit_target}: Created via slash command",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert slash_edit["handled"] is True and slash_edit_target.read_text(encoding="utf-8").strip() == "Created via slash command"

        slash_access = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/access {slash_edit_target}",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert slash_access["handled"] is True and "Created via slash command" in slash_access["text"]

        # Bare /rag lists active RAG knowledge sources instead of searching for literal "/rag"
        bare_rag = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/rag",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert bare_rag["handled"] is True and bare_rag["intent"] == "rag_query"
        assert "Active RAG knowledge sources" in bare_rag["text"] or "Nguồn tri thức RAG" in bare_rag["text"]

        # /scan routes to screen_observe
        slash_scan = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/scan",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert slash_scan["handled"] is True and slash_scan["intent"] == "screen_observe"

        # Non-code /plan (e.g. picnic plan) does NOT leak lastDirectory or code diagnostics template
        picnic_plan = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/plan lên kế hoạch cho buổi dã ngoại ngày mai",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert picnic_plan["handled"] is True and picnic_plan["intent"] == "code_task", picnic_plan
        assert "dã ngoại" in picnic_plan["text"].lower(), picnic_plan["text"]
        assert "Workspace:" not in picnic_plan["text"] and "/diagnostics" not in picnic_plan["text"], picnic_plan["text"]

        # Natural-language copy, move, run, and undo without slash commands
        nl_copy_dst = desktop / "nl_copied_file.txt"
        nl_copy = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"hãy sao chép file {slash_edit_target} sang {nl_copy_dst}",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert nl_copy["handled"] is True and nl_copy_dst.exists(), nl_copy

        nl_move_dst = desktop / "nl_moved_file.txt"
        nl_move = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"giúp tôi di chuyển tệp {nl_copy_dst} vào {nl_move_dst}",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert nl_move["handled"] is True and nl_move_dst.exists() and not nl_copy_dst.exists(), nl_move

        nl_undo = run_helper(
            {
                "action": "agent_turn",
                "prompt": "quay lại bước trước",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert nl_undo["handled"] is True and nl_copy_dst.exists(), nl_undo

        # 18. Two-Tier File Editing: Small files (<=24KB & <=300 lines) -> Editor Popup; Large files (>24KB or >300 lines) -> Default System Editor
        small_code_file = desktop / "small_module.py"
        small_code_body = "def compute_total(items):\n    return sum(items)\n"
        small_code_file.write_text(small_code_body, encoding="utf-8")

        # Commanding Companion to edit a small file without inline replacement opens it in Editor Popup without wiping content
        edit_small_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/edit {small_code_file}",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert edit_small_turn["handled"] is True and edit_small_turn["intent"] == "file_task"
        assert edit_small_turn["editorMode"] == "popup"
        assert edit_small_turn["isLargeFile"] is False
        assert edit_small_turn["openedExternalEditor"] is False
        assert edit_small_turn["editorPopup"]["open"] is True
        assert edit_small_turn["editorPopup"]["filePath"] == str(small_code_file)
        assert edit_small_turn["editorPopup"]["fileName"] == "small_module.py"
        assert edit_small_turn["editorPopup"]["content"] == small_code_body
        assert edit_small_turn["editorPopup"]["lineCount"] == 2
        assert edit_small_turn["editorPopup"]["language"] == "py"
        assert small_code_file.read_text(encoding="utf-8") == small_code_body

        # Boundary: 300 lines (<=24KB) is still small -> popup; 301 lines -> external editor
        boundary_300_file = desktop / "boundary_300.py"
        boundary_300_file.write_text("\n".join(f"x_{i} = {i}" for i in range(300)), encoding="utf-8")
        res_300 = run_helper(
            {"action": "agent_file_op", "op": "open_editor", "targetPath": str(boundary_300_file)},
            env,
        )
        assert res_300["editorMode"] == "popup" and res_300["isLargeFile"] is False and res_300["editorPopup"]["open"] is True

        large_lines_file = desktop / "large_305_lines.py"
        large_lines_file.write_text("\n".join(f"line_{i} = {i}" for i in range(305)), encoding="utf-8")
        edit_large_lines_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"edit file {large_lines_file}",
                "character": "aqua",
                "persistHistory": False,
            },
            env,
        )
        assert edit_large_lines_turn["handled"] is True
        assert edit_large_lines_turn["editorMode"] == "external"
        assert edit_large_lines_turn["isLargeFile"] is True
        assert edit_large_lines_turn["openedExternalEditor"] is True
        assert edit_large_lines_turn["editorPopup"]["open"] is False

        # Boundary: > 24KB (even with < 300 lines) -> external editor
        large_bytes_file = desktop / "large_bytes.txt"
        large_bytes_file.write_text(("A" * 260 + "\n") * 100, encoding="utf-8")  # ~26.1 KB, 100 lines
        res_large_bytes = run_helper(
            {"action": "agent_file_op", "op": "open_editor", "targetPath": str(large_bytes_file)},
            env,
        )
        assert res_large_bytes["editorMode"] == "external" and res_large_bytes["isLargeFile"] is True
        assert res_large_bytes["openedExternalEditor"] is True

        # Force external editor handoff on a small file
        res_forced_ext = run_helper(
            {"action": "agent_file_op", "op": "open_editor", "targetPath": str(small_code_file), "forceExternal": True},
            env,
        )
        assert res_forced_ext["editorMode"] == "external" and res_forced_ext["openedExternalEditor"] is True

        # Natural-language Vietnamese & English edit commands without 'file'/'tệp' keyword
        for nl_prompt in (f"hãy sửa {small_code_file}", f"chỉnh sửa {small_code_file}", f"please edit {small_code_file}"):
            nl_edit = run_helper(
                {"action": "agent_turn", "prompt": nl_prompt, "character": "aqua", "persistHistory": False},
                env,
            )
            assert nl_edit["handled"] is True and nl_edit["editorMode"] == "popup", f"Failed for prompt: {nl_prompt} -> {nl_edit}"
            assert nl_edit["editorPopup"]["open"] is True and nl_edit["editorPopup"]["filePath"] == str(small_code_file)

        # System path outside $HOME open_editor with sudo password handoff
        sys_small_file = tmp_path / "etc_mock" / "small_sys.conf"
        sys_small_file.parent.mkdir(parents=True, exist_ok=True)
        sys_small_file.write_text("port = 8080\n", encoding="utf-8")
        sys_edit_req = run_helper(
            {"action": "agent_turn", "prompt": f"/edit {sys_small_file}", "character": "aqua", "persistHistory": False},
            {**env, "AQUA_TEST_SUDO_PASSWORD": "test_sudo_pwd"},
        )
        assert sys_edit_req["requiresSudoPassword"] is True
        sys_edit_done = run_helper(
            {
                "action": "agent_turn",
                "prompt": "[sudo password]",
                "sudoPassword": "test_sudo_pwd",
                "character": "aqua",
                "persistHistory": False,
            },
            {**env, "AQUA_TEST_SUDO_PASSWORD": "test_sudo_pwd"},
        )
        assert sys_edit_done["status"] == "completed"
        assert sys_edit_done.get("editorMode") == "popup"
        assert sys_edit_done.get("editorPopup", {}).get("open") is True
        assert sys_edit_done["editorPopup"]["filePath"] == str(sys_small_file)

        # 19. Storage Directory Quick-Open & Enriched Skills Status for Settings UI
        for kind in ("skills", "builtin_skills", "mcp", "rag", "state"):
            opened_dir = run_helper({"action": "open_storage_path", "kind": kind}, env)
            assert opened_dir["exists"] is True and Path(opened_dir["path"]).is_dir() and opened_dir["kind"] == kind

        st_enriched = run_helper({"action": "agent_status"}, env)
        assert "storagePaths" in st_enriched and Path(st_enriched["storagePaths"]["skillsDir"]).is_dir()
        assert Path(st_enriched["storagePaths"]["builtinSkillsDir"]).is_dir()
        ca_skill = next((s for s in st_enriched["skills"] if s["name"] == "code-assistant"), None)
        rg_skill = next((s for s in st_enriched["skills"] if s["name"] == "release-gate"), None)
        assert ca_skill and ca_skill.get("builtIn") is True and ca_skill.get("folder")
        assert rg_skill and rg_skill.get("builtIn") is False and rg_skill.get("folder")

        # Per-skill Open Folder button passes kind="skills" or "builtin_skills" + custom path=skill["folder"]
        opened_rg_folder = run_helper({"action": "open_storage_path", "kind": "skills", "path": rg_skill["folder"]}, env)
        assert Path(opened_rg_folder["path"]).resolve() == Path(rg_skill["folder"]).resolve()
        opened_ca_folder = run_helper({"action": "open_storage_path", "kind": "builtin_skills", "path": ca_skill["folder"]}, env)
        assert Path(opened_ca_folder["path"]).resolve() == Path(ca_skill["folder"]).resolve()

        # Toggling skill enable/disable preserves folder and path metadata
        toggled_off = run_helper({"action": "skill_action", "op": "disable", "name": "release-gate"}, env)
        assert any(s["name"] == "release-gate" and s["enabled"] is False and s.get("folder") for s in toggled_off["skills"])
        toggled_on = run_helper({"action": "skill_action", "op": "enable", "name": "release-gate"}, env)
        assert any(s["name"] == "release-gate" and s["enabled"] is True and s.get("folder") for s in toggled_on["skills"])

        # 20. Eyes & Hands Curated MCP Servers, Tool Execution, Version Indicator & Update Lifecycle
        mcp_servers_map = {s["name"]: s for s in st_enriched.get("mcpServers", [])}
        assert {
            "computer-use-linux",
            "playwright-mcp",
            "chrome-devtools-mcp",
            "desktop-commander",
            "git-mcp",
            "sqlite-mcp",
            "pipewire-mpris-mcp",
            "companion-system",
        }.issubset(mcp_servers_map.keys()), mcp_servers_map.keys()

        sqlite_tables = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "sqlite-mcp", "tool": "list_tables", "arguments": {}},
            env,
        )
        assert sqlite_tables["result"]["isError"] is False
        pw_media = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "pipewire-mpris-mcp", "tool": "media_control", "arguments": {"action": "status"}},
            env,
        )
        assert pw_media["result"]["isError"] is False

        cul_srv = mcp_servers_map["computer-use-linux"]
        assert cul_srv["version"] == "1.2.0" and cul_srv["latestVersion"] == "1.3.0" and cul_srv["hasUpdate"] is True
        assert cul_srv.get("folder") and Path(cul_srv["folder"]).is_dir()
        opened_mcp_folder = run_helper({"action": "open_storage_path", "kind": "mcp", "path": cul_srv["folder"]}, env)
        assert Path(opened_mcp_folder["path"]).resolve() == Path(cul_srv["folder"]).resolve()

        # Execute Eyes & Hands tools across computer-use-linux, playwright-mcp, chrome-devtools-mcp, desktop-commander
        cul_tools = run_helper({"action": "mcp_action", "op": "list_tools", "server": "computer-use-linux"}, env)
        assert {t["name"] for t in cul_tools["tools"]} >= {"screenshot", "get_accessibility_tree", "mouse_click", "mouse_move", "type_text", "key_press", "window_action"}
        cul_tree = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "computer-use-linux", "tool": "get_accessibility_tree", "arguments": {}},
            env,
        )
        assert cul_tree["result"]["isError"] is False and "Window:" in cul_tree["result"]["content"][0]["text"]

        cul_click = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "computer-use-linux", "tool": "mouse_click", "arguments": {"x": 120, "y": 240, "button": "left"}},
            env,
        )
        assert cul_click["result"]["isError"] is False and "(120, 240)" in cul_click["result"]["content"][0]["text"]
        assert cul_click["result"]["structured"]["x"] == 120 and cul_click["result"]["structured"]["y"] == 240

        cul_move = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "computer-use-linux", "tool": "mouse_move", "arguments": {"x": 300, "y": 400}},
            env,
        )
        assert cul_move["result"]["isError"] is False and "(300, 400)" in cul_move["result"]["content"][0]["text"]

        cul_type = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "computer-use-linux", "tool": "type_text", "arguments": {"text": "hello wayland"}},
            env,
        )
        assert cul_type["result"]["isError"] is False and cul_type["result"]["structured"]["textLength"] == 13

        cul_key = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "computer-use-linux", "tool": "key_press", "arguments": {"key": "Return"}},
            env,
        )
        assert cul_key["result"]["isError"] is False and cul_key["result"]["structured"]["key"] == "Return"

        cul_win = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "computer-use-linux", "tool": "window_action", "arguments": {"action": "inspect_window"}},
            env,
        )
        assert cul_win["result"]["isError"] is False and cul_win["result"]["structured"]["actionType"] == "inspect_window"

        pw_click = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "playwright-mcp", "tool": "browser_click", "arguments": {"element": "button#submit"}},
            env,
        )
        assert pw_click["result"]["isError"] is False and "Playwright" in pw_click["result"]["content"][0]["text"]

        cdt_net = run_helper(
            {"action": "mcp_action", "op": "call_tool", "server": "chrome-devtools-mcp", "tool": "list_network_requests", "arguments": {}},
            env,
        )
        assert cdt_net["result"]["isError"] is False and "DevTools" in cdt_net["result"]["content"][0]["text"]

        dc_exec = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "desktop-commander",
                "tool": "execute_command",
                "arguments": {"command": "echo EYES_AND_HANDS_MCP_OK", "cwd": str(proj_ws)},
            },
            env,
        )
        assert dc_exec["result"]["isError"] is False and "EYES_AND_HANDS_MCP_OK" in dc_exec["result"]["content"][0]["text"]

        dc_read = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "desktop-commander",
                "tool": "read_file_excerpt",
                "arguments": {"path": str(proj_ws / "README.md")},
            },
            env,
        )
        assert dc_read["result"]["isError"] is False and len(dc_read["result"]["structured"]["lines"]) >= 1

        # Legacy mcp_servers.json upgrade parity: pre-existing state with stale latestVersion/hasUpdate=False must surface new catalog updates
        mcp_state_file = Path(env["XDG_STATE_HOME"]) / "alis-companion" / "mcp_servers.json"
        mcp_state_file.write_text(
            json.dumps({
                "version": 1,
                "servers": [
                    {"name": "playwright-mcp", "enabled": True, "version": "0.0.28", "latestVersion": "0.0.28", "hasUpdate": False},
                    {"name": "computer-use-linux", "enabled": True, "version": "1.2.0", "hasUpdate": False},
                ],
            }),
            encoding="utf-8",
        )
        legacy_loaded = run_helper({"action": "agent_status"}, env)
        legacy_mcp_map = {s["name"]: s for s in legacy_loaded["mcpServers"]}
        assert legacy_mcp_map["playwright-mcp"]["latestVersion"] == "0.0.31" and legacy_mcp_map["playwright-mcp"]["hasUpdate"] is True
        assert legacy_mcp_map["computer-use-linux"]["latestVersion"] == "1.3.0" and legacy_mcp_map["computer-use-linux"]["hasUpdate"] is True

        # Check updates & update MCP server via mcp_action and /mcp update slash command
        chk_upd = run_helper({"action": "mcp_action", "op": "check_updates"}, env)
        assert chk_upd["updatesAvailable"] >= 3

        upd_cul = run_helper({"action": "mcp_action", "op": "update_server", "name": "computer-use-linux"}, env)
        assert upd_cul["updated"]["previousVersion"] == "1.2.0"
        assert upd_cul["updated"]["version"] == "1.3.0"
        assert upd_cul["updated"]["hasUpdate"] is False
        cul_after = next(s for s in upd_cul["servers"] if s["name"] == "computer-use-linux")
        assert cul_after["version"] == "1.3.0" and cul_after["hasUpdate"] is False

        slash_upd = run_helper({"action": "agent_turn", "prompt": "/mcp update playwright-mcp", "character": "aqua"}, env)
        assert slash_upd["handled"] is True and "0.0.31" in slash_upd["text"], slash_upd
        pw_after = next(s for s in slash_upd["mcpServers"] if s["name"] == "playwright-mcp")
        assert pw_after["version"] == "0.0.31" and pw_after["hasUpdate"] is False

        slash_chk = run_helper({"action": "agent_turn", "prompt": "/mcp check-updates", "character": "aqua"}, env)
        assert slash_chk["handled"] is True and "desktop-commander" in slash_chk["text"], slash_chk

        # ------------------------------------------------------------------
        # 16. Dual-Model Auto-Routing (UI Action vs Deep Thinking), plusIQ/Qwen3.5 Mode Switching, and MTP Auto-Optimization
        # ------------------------------------------------------------------
        dual_opts = {
            "model": "qwen2.5-vl-3b-instruct-q4_k_m.gguf",
            "actionModel": "qwen2.5-vl-3b-instruct-q4_k_m.gguf",
            "thinkingModel": "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF",
            "autoModelRoutingEnabled": True,
            "mtpOptimizeEnabled": True,
            "thinkingEffort": "high",
        }
        # Fast UI Action prompts (VI + EN) -> actionModel
        act_vi = agent_runtime.classify_prompt_model_role("mở ứng dụng firefox và chụp màn hình", dual_opts)
        assert act_vi["role"] == "action" and act_vi["selectedModel"] == "qwen2.5-vl-3b-instruct-q4_k_m.gguf"
        assert act_vi["dualModelActive"] is True and act_vi["recommendedEffort"] == "off"

        act_en = agent_runtime.classify_prompt_model_role("click the submit button on the active window", dual_opts)
        assert act_en["role"] == "action" and act_en["selectedModel"] == "qwen2.5-vl-3b-instruct-q4_k_m.gguf"

        # Deep Thinking prompts (VI + EN + slash commands) -> thinkingModel (with plusIQ + MTP active)
        think_vi = agent_runtime.classify_prompt_model_role(
            "phân tích chuyên sâu kiến trúc hệ thống, tối ưu thuật toán và tìm nguyên nhân gốc rễ lỗi deadlock",
            dual_opts,
        )
        assert think_vi["role"] == "thinking"
        assert think_vi["selectedModel"] == "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF"
        assert think_vi["isPlusIq"] is True and think_vi["isMtp"] is True and think_vi["mtpActive"] is True
        assert think_vi["inChatDirective"] == "{REASON:xhigh}"
        assert think_vi["enableThinking"] is True
        assert think_vi["repetitionPenalty"] == 1.0 and think_vi["temperature"] == 0.7

        # Max effort on plusIQ model -> {REASON:einstein}
        dual_opts_max = dict(dual_opts, thinkingEffort="max")
        think_max = agent_runtime.classify_prompt_model_role("/plan thiết kế kiến trúc hệ thống multi-agent", dual_opts_max)
        assert think_max["role"] == "thinking" and think_max["inChatDirective"] == "{REASON:einstein}"

        # Philosophical / creative / lateral thinking on plusIQ model -> {REASON:spoon}
        think_spoon = agent_runtime.classify_prompt_model_role(
            "Phân tích triết học và sáng tạo sâu out of the box cho bài toán này",
            dual_opts,
        )
        assert think_spoon["role"] == "thinking" and think_spoon["inChatDirective"] == "{REASON:spoon}"

        # Mixed-intent prompt (< 140 chars) containing both UI word and strong analytical/algorithmic cue routes to "thinking"
        mixed_deep = agent_runtime.classify_prompt_model_role(
            "Click Save and explain why the algorithm deadlocks",
            dual_opts,
        )
        assert mixed_deep["role"] == "thinking" and mixed_deep["selectedModel"] == dual_opts["thinkingModel"]
        mixed_vi = agent_runtime.classify_prompt_model_role(
            "Gỡ lỗi thuật toán khi click chuột vào nút Save",
            dual_opts,
        )
        assert mixed_vi["role"] == "thinking" and mixed_vi["selectedModel"] == dual_opts["thinkingModel"]

        # Slash commands routing check: /rag without query is fast UI status ("action"), /rag <query> is "thinking"
        rag_list_route = agent_runtime.classify_prompt_model_role("/rag", dual_opts)
        assert rag_list_route["role"] == "action"
        rag_query_route = agent_runtime.classify_prompt_model_role("/rag kiến trúc shader của Abyss", dual_opts)
        assert rag_query_route["role"] == "thinking"

        # Single-Model Multi-Mode (DavidAU plusIQ + MTP as sole model):
        # UI Action -> {REASON:imedium} (0 thinking tokens, elevated IQ) + enableThinking=False + MTP repeat_penalty=1.0
        # Deep Thinking -> {REASON:xhigh} + enableThinking=True + MTP repeat_penalty=1.0
        single_plusiq_opts = {
            "model": "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF",
            "actionModel": "",
            "thinkingModel": "",
            "autoModelRoutingEnabled": True,
            "mtpOptimizeEnabled": True,
            "thinkingEffort": "high",
        }
        sp_action = agent_runtime.classify_prompt_model_role("bấm vào nút Save trên cửa sổ", single_plusiq_opts)
        assert sp_action["role"] == "action" and sp_action["dualModelActive"] is False
        assert sp_action["inChatDirective"] == "{REASON:imedium}" and sp_action["enableThinking"] is False
        assert sp_action["mtpActive"] is True and sp_action["repetitionPenalty"] == 1.0 and sp_action["temperature"] == 0.7

        sp_think = agent_runtime.classify_prompt_model_role("explain why this race condition happens step by step", single_plusiq_opts)
        assert sp_think["role"] == "thinking"
        assert sp_think["inChatDirective"] == "{REASON:xhigh}" and sp_think["enableThinking"] is True

        # Turning off MTP optimization disables MTP parameter overrides while preserving routing
        sp_mtp_off = agent_runtime.classify_prompt_model_role(
            "bấm vào nút Save",
            dict(single_plusiq_opts, mtpOptimizeEnabled=False),
        )
        assert sp_mtp_off["isMtp"] is True and sp_mtp_off["mtpOptimizeEnabled"] is False and sp_mtp_off["mtpActive"] is False
        assert sp_mtp_off["repetitionPenalty"] is None and sp_mtp_off["temperature"] is None

        # Verify _call_local_model_tool_turn respects mtp_active=True vs mtp_active=False on an MTP model
        captured_bodies = []

        class MtpBodyCaptureHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                captured_bodies.append(json.loads(self.rfile.read(length).decode("utf-8")))
                resp = {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {}}
                raw = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        mtp_httpd = http.server.HTTPServer(("127.0.0.1", 0), MtpBodyCaptureHandler)
        mtp_port = mtp_httpd.server_port
        mtp_th = threading.Thread(target=mtp_httpd.serve_forever, daemon=True)
        mtp_th.start()
        try:
            agent_runtime._call_local_model_tool_turn(
                f"http://127.0.0.1:{mtp_port}/v1/chat/completions",
                "Qwen3.5-9B-MTP",
                "Sys",
                "Prompt",
                enable_thinking=False,
                in_chat_directive="{REASON:imedium}",
                mtp_active=True,
            )
            assert captured_bodies[-1].get("repetition_penalty") == 1.0
            assert captured_bodies[-1].get("repeat_penalty") == 1.0
            assert captured_bodies[-1].get("temperature") == 0.7
            assert captured_bodies[-1]["messages"][-1]["content"].startswith("{REASON:imedium}\n")

            agent_runtime._call_local_model_tool_turn(
                f"http://127.0.0.1:{mtp_port}/v1/chat/completions",
                "Qwen3.5-9B-MTP",
                "Sys",
                "Prompt",
                enable_thinking=False,
                in_chat_directive="{REASON:imedium}",
                mtp_active=False,
            )
            assert "repetition_penalty" not in captured_bodies[-1]
            assert "repeat_penalty" not in captured_bodies[-1]
            assert captured_bodies[-1].get("temperature") == 0.1
        finally:
            mtp_httpd.shutdown()

        # Auto-discovery from availableModels when actionModel / thinkingModel are empty
        auto_disc = agent_runtime.classify_prompt_model_role(
            "analyze the algorithm complexity and architecture trade-offs",
            {
                "model": "qwen2.5-vl-3b-instruct",
                "actionModel": "",
                "thinkingModel": "",
                "autoModelRoutingEnabled": True,
                "availableModels": [
                    {"name": "qwen2.5-vl-3b-instruct", "capabilities": {"chat": "supported", "reasoning": "unsupported"}},
                    {"name": "deepseek-r1-distill-qwen-7b", "capabilities": {"chat": "supported", "reasoning": "supported"}},
                ],
            },
        )
        assert auto_disc["dualModelActive"] is True
        assert auto_disc["actionModel"] == "qwen2.5-vl-3b-instruct"
        assert auto_disc["thinkingModel"] == "deepseek-r1-distill-qwen-7b"
        assert auto_disc["role"] == "thinking" and auto_disc["selectedModel"] == "deepseek-r1-distill-qwen-7b"

        # Helper action model_route & agent_turn modelRoute metadata
        helper_route = run_helper(
            {
                "action": "model_route",
                "prompt": "/diagnostics",
                "options": dual_opts,
            },
            env,
        )
        assert helper_route["modelRoute"]["role"] == "thinking"
        assert helper_route["modelRoute"]["selectedModel"] == dual_opts["thinkingModel"]

        turn_with_route = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"đọc tệp {note_file}",
                "character": "aqua",
                "options": dual_opts,
            },
            env,
        )
        assert turn_with_route["routedRole"] == "action"
        assert turn_with_route["routedModel"] == dual_opts["actionModel"]
        assert turn_with_route["modelRoute"]["role"] == "action"

        # 25. Interactive Clarification (Codex / Antigravity / Claude Desktop style: 1-3 ideas + 1 custom input)
        clar_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/clarify tối ưu hệ thống",
                "character": "aqua",
            },
            env,
        )
        assert clar_turn["handled"] is True
        assert clar_turn["status"] == "awaiting_clarification"
        assert "clarification" in clar_turn
        clar_meta = clar_turn["clarification"]
        assert clar_meta["kind"] == "task_clarification"
        assert clar_meta["allowCustom"] is True
        assert 1 <= len(clar_meta["options"]) <= 3
        assert len(clar_meta["options"]) == 3
        assert clar_meta["options"][0]["recommended"] is True

        # Select option #1 from the 1-3 ideas
        clar_pick = run_helper(
            {
                "action": "agent_turn",
                "prompt": clar_meta["options"][0]["value"],
                "character": "aqua",
            },
            env,
        )
        assert clar_pick["handled"] is True
        assert clar_pick["status"] == "completed"
        assert clar_pick.get("clarificationResolved", {}).get("mode") == "option"

        # Trigger clarification via underspecified prompt and reply with a custom idea
        clar_vague = run_helper(
            {
                "action": "agent_turn",
                "prompt": "thiết kế giao diện",
                "character": "aqua",
            },
            env,
        )
        assert clar_vague["handled"] is True
        assert clar_vague["status"] == "awaiting_clarification"
        assert len(clar_vague["clarification"]["options"]) == 3

        clar_custom = run_helper(
            {
                "action": "agent_turn",
                "prompt": "Thiết kế thẻ hỏi lại với 3 gợi ý chọn nhanh và 1 ô chat tùy chỉnh",
                "character": "aqua",
            },
            env,
        )
        assert clar_custom["handled"] is True
        assert clar_custom["status"] == "completed"
        assert clar_custom.get("clarificationResolved", {}).get("mode") == "custom"
        assert "Thiết kế thẻ hỏi lại" in clar_custom["text"]

        # 25. Screenshot Capture & Image Reply in Chatbox
        ss_env = dict(env)
        ss_env["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps({
            "windowId": 77,
            "title": "Alis Companion Workspace",
            "appId": "org.alis.companion",
            "lines": ["Alis Companion Active Window", "Screenshot test fixture"],
        })
        ss_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "chụp màn hình giúp tôi",
                "character": "aqua",
            },
            ss_env,
        )
        assert ss_turn["handled"] is True
        assert ss_turn["intent"] == "screen_observe"
        assert isinstance(ss_turn.get("images"), list) and len(ss_turn["images"]) == 1
        assert Path(ss_turn["images"][0]).is_file(), f"Expected captured screenshot file at {ss_turn['images'][0]}"
        assert "Đã chụp màn hình" in ss_turn["text"]

        # 26. YouTube Clean Query Extraction & Direct Video Playback + Auto-Unload Idle LLM
        import web_tools
        yt_clean = web_tools.extract_clean_browser_query(
            "mở trình duyệt, vào youtube và mở bài Everybody loves somebody của Dean Martin",
            engine="youtube",
        )
        assert yt_clean["query"] == "Everybody loves somebody của Dean Martin", f"Unexpected cleaned query: {yt_clean}"
        assert yt_clean["wantsPlay"] is True and yt_clean["engine"] == "youtube"
        yt_env = dict(env)
        yt_env["AQUA_TEST_YOUTUBE_VIDEO_ID"] = "z-2_OstpR5c"
        yt_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "mở trình duyệt, vào youtube và mở bài Everybody loves somebody của Dean Martin",
                "character": "aqua",
            },
            yt_env,
        )
        assert yt_turn["handled"] is True
        assert yt_turn["intent"] == "web_task"
        assert "https://www.youtube.com/watch?v=z-2_OstpR5c" in yt_turn["text"]
        assert "[youtube.com](https://www.youtube.com/watch?v=z-2_OstpR5c)" in yt_turn["text"]
        assert ", vào youtube và mở bài" not in yt_turn["text"]

        unload_res = run_helper(
            {"action": "unload_llm", "model": "lmstudio-qwen3.5"},
            env,
        )
        assert unload_res["unloaded"] is True and isinstance(unload_res["providers"], list)

    print("ALIS_COMPANION_AGENT_F11_PASS naturalLanguageLoop permissions bwrapSandbox screenContext codeToolsLsp ragCitations mcpProtocol skillsSystem hermesSelfLearning slashCommands rules memoryPocket crashReconcile legacyDataParity chatThreadsProjects fullAuthorityDesktopAgent twoTierFileEditor storageAndSkillsSettings dualModelAutoRouting plusIqModeSwitching mtpAutoOptimization interactiveClarification screenshotImageReply")


if __name__ == "__main__":
    main()

