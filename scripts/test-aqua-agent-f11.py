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

        # 27. Proposal 1: Multi-Step ReAct / Agentic Tool Loop & LLM Observation Synthesis
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import wave
        import zipfile

        class _MockReActHandler(BaseHTTPRequestHandler):
            calls = []

            def log_message(self, format, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                type(self).calls.append(body)
                msgs = body.get("messages") or []
                tool_msgs = [m for m in msgs if m.get("role") == "tool"]
                user_texts = " ".join(str(m.get("content") or "") for m in msgs if m.get("role") == "user")

                if "INFINITE_TOOL_LOOP" in user_texts:
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "Checking status again...",
                                "tool_calls": [{
                                    "id": f"call_inf_{len(tool_msgs) + 1}",
                                    "type": "function",
                                    "function": {
                                        "name": "system_status",
                                        "arguments": json.dumps({"section": "all"}),
                                    },
                                }],
                            },
                            "finish_reason": "tool_calls",
                        }]
                    }
                elif not body.get("tools"):
                    # Observation synthesis call
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "Synthesized Answer: System health and tool observations are clean.",
                            },
                            "finish_reason": "stop",
                        }]
                    }
                elif len(tool_msgs) == 0:
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "Step 1: I will inspect the project README first.",
                                "tool_calls": [{
                                    "id": "call_step_1",
                                    "type": "function",
                                    "function": {
                                        "name": "access_file",
                                        "arguments": json.dumps({"path": str(proj_ws / "README.md")}),
                                    },
                                }],
                            },
                            "finish_reason": "tool_calls",
                        }]
                    }
                elif len(tool_msgs) == 1:
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "Step 2: Now I will check system telemetry after reading the file.",
                                "tool_calls": [{
                                    "id": "call_step_2",
                                    "type": "function",
                                    "function": {
                                        "name": "system_status",
                                        "arguments": json.dumps({"section": "all"}),
                                    },
                                }],
                            },
                            "finish_reason": "tool_calls",
                        }]
                    }
                else:
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "ReAct Multi-Step Synthesis Complete: inspected README.md and verified system status.",
                            },
                            "finish_reason": "stop",
                        }]
                    }

                raw_out = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw_out)))
                self.end_headers()
                self.wfile.write(raw_out)

        mock_server = ThreadingHTTPServer(("127.0.0.1", 0), _MockReActHandler)
        mock_thread = threading.Thread(target=mock_server.serve_forever, daemon=True)
        mock_thread.start()
        mock_endpoint = f"http://127.0.0.1:{mock_server.server_port}/v1/chat/completions"
        try:
            react_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "Coordinate a multi-step check on the workspace and system for me",
                    "character": "aqua",
                    "endpoint": mock_endpoint,
                    "model": "qwen3.5-react-test",
                    "useModelTools": True,
                    "maxReactSteps": 5,
                },
                env,
            )
            assert react_turn["handled"] is True
            assert react_turn["intent"] == "llm_tool_turn"
            assert react_turn["reactStepCount"] == 2, f"Expected 2 ReAct steps, got {react_turn}"
            assert len(react_turn["reactSteps"]) == 2
            assert react_turn["reactSteps"][0]["name"] == "access_file"
            assert react_turn["reactSteps"][1]["name"] == "system_status"
            assert "ReAct Multi-Step Synthesis Complete" in react_turn["text"]
            with sqlite3.connect(xdg_state / "alis-companion" / "agent_runs.sqlite3") as rconn:
                react_run_rows = rconn.execute(
                    "SELECT run_id FROM agent_runs WHERE goal LIKE ?",
                    ("%Coordinate a multi-step check%",),
                ).fetchall()
            assert len(react_run_rows) == 1 and react_run_rows[0][0] == react_turn["runId"], (
                f"Expected exactly 1 agent_runs row for ReAct turn, got {react_run_rows}"
            )

            # Test maxReactSteps loop boundary guard
            react_bounded = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "INFINITE_TOOL_LOOP check",
                    "character": "aqua",
                    "endpoint": mock_endpoint,
                    "model": "qwen3.5-react-test",
                    "useModelTools": True,
                    "maxReactSteps": 2,
                },
                env,
            )
            assert react_bounded["handled"] is True
            assert react_bounded["reactStepCount"] == 2
            assert len(react_bounded["reactSteps"]) == 2

            # Test LLM synthesis of deterministic tool output when synthesizeWithLlm=True (must synthesize ONCE, not twice)
            synth_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "/system",
                    "character": "aqua",
                    "endpoint": mock_endpoint,
                    "model": "qwen3.5-react-test",
                    "synthesizeWithLlm": True,
                },
                env,
            )
            assert synth_turn["handled"] is True
            assert synth_turn["text"].count("Synthesized Answer:") == 1, f"Expected single synthesis in {synth_turn['text']}"

            synth_rag_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "/rag glslangValidator",
                    "character": "aqua",
                    "endpoint": mock_endpoint,
                    "model": "qwen3.5-react-test",
                    "synthesizeWithLlm": True,
                },
                env,
            )
            assert synth_rag_turn["handled"] is True
            assert synth_rag_turn["text"].count("Synthesized Answer:") == 1, f"Duplicate LLM synthesis in /rag: {synth_rag_turn['text']}"
        finally:
            mock_server.shutdown()
            mock_server.server_close()

        # 28. Proposal 3: Niri Advanced Window/Column Layout & Interactive Browser DOM Snapshot
        niri_ws_move = run_helper(
            {
                "action": "agent_turn",
                "prompt": "move window to workspace 3",
                "character": "aqua",
            },
            env,
        )
        assert niri_ws_move["handled"] is True and niri_ws_move["intent"] == "desktop_control"
        assert "workspace" in niri_ws_move["text"].lower() or "3" in niri_ws_move["text"]

        niri_win_id_move = run_helper(
            {
                "action": "agent_turn",
                "prompt": "move window 5 to workspace 3",
                "character": "aqua",
            },
            env,
        )
        assert niri_win_id_move["handled"] is True and niri_win_id_move["intent"] == "desktop_control", (
            f"Expected desktop_control for 'move window 5 to workspace 3', got {niri_win_id_move}"
        )
        assert "3" in niri_win_id_move["text"] and "path_not_found" not in niri_win_id_move["text"]
        import desktop_control
        direct_ws_move = desktop_control.manage_desktop(action="move_window_to_workspace", target="3", value="5")
        assert direct_ws_move["workspace"] == "3" and direct_ws_move["windowId"] == "5", (
            f"Expected workspace='3' and windowId='5', got {direct_ws_move}"
        )

        niri_col_width = run_helper(
            {
                "action": "agent_turn",
                "prompt": "set column width 60%",
                "character": "aqua",
            },
            env,
        )
        assert niri_col_width["handled"] is True and niri_col_width["intent"] == "desktop_control"
        assert "60%" in niri_col_width["text"]

        for niri_act, niri_val in (
            ("consume_or_expel_window", "left"),
            ("consume_window_into_column", ""),
            ("expel_window_from_column", ""),
            ("set_window_height", "75%"),
            ("fullscreen_window", ""),
            ("toggle_window_floating", ""),
            ("center_column", ""),
            ("maximize_column", ""),
            ("expand_column_to_available_width", ""),
        ):
            niri_mcp = run_helper(
                {
                    "action": "mcp_action",
                    "op": "call_tool",
                    "server": "computer-use-linux",
                    "tool": "window_action",
                    "arguments": {"action": niri_act, "value": niri_val},
                },
                env,
            )
            assert niri_mcp["result"]["isError"] is False, f"Failed Niri action {niri_act}: {niri_mcp}"

        # Interactive Browser DOM Snapshot & [e1], [e2] Element Actions
        sample_html = """
        <html>
          <head><title>Alis Portal Login</title></head>
          <body>
            <h1>Welcome to Alis Portal</h1>
            <p>Sign in or browse documentation below.</p>
            <a id="docs-link" href="https://example.org/docs">Read Documentation</a>
            <input id="user-input" name="username" type="text" placeholder="Enter username" />
            <button id="login-btn" type="submit">Sign In Now</button>
          </body>
        </html>
        """
        dom_snap = web_tools.capture_dom_snapshot(sample_html)
        assert dom_snap["title"] == "Alis Portal Login"
        assert len(dom_snap["elements"]) == 3
        assert dom_snap["elements"][0]["ref"] == "e1" and dom_snap["elements"][0]["role"] == "link"
        assert dom_snap["elements"][1]["ref"] == "e2" and dom_snap["elements"][1]["role"] == "textbox"
        assert dom_snap["elements"][2]["ref"] == "e3" and dom_snap["elements"][2]["role"] == "button"
        assert any("[e2]" in line for line in dom_snap["domSnapshot"])

        dom_link_click = web_tools.interact_dom_element("click", "e1")
        assert dom_link_click["ok"] is True
        assert "-> https://example.org/docs" in dom_link_click["summary"], (
            f"Expected link click summary to include navigated URL, got {dom_link_click}"
        )

        dom_fill = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "playwright-mcp",
                "tool": "browser_fill",
                "arguments": {"element": "[e2]", "text": "llocphann"},
            },
            env,
        )
        assert dom_fill["result"]["isError"] is False
        assert dom_fill["result"]["structured"]["formValues"].get("e2") == "llocphann"

        dom_click_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "click [e3]",
                "character": "aqua",
            },
            env,
        )
        assert dom_click_turn["handled"] is True
        assert dom_click_turn["intent"] == "web_task"
        assert "e3" in dom_click_turn["text"]

        # 29. Proposal 4: Semantic AT-SPI Grounding & Set-of-Mark (SoM) Visual Grounding
        som_env = dict(env)
        som_env["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps({
            "windowId": 88,
            "title": "GTK Settings Dialog",
            "appId": "org.gnome.Settings",
            "lines": ["Appearance", "Dark Style", "Apply Changes"],
            "elements": [
                {"id": 1, "role": "push button", "name": "Apply Changes", "bbox": {"x": 100, "y": 60, "w": 80, "h": 28}},
                {"id": 2, "role": "toggle button", "name": "Dark Style", "bbox": {"x": 20, "y": 60, "w": 64, "h": 28}},
            ],
        })
        som_obs = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/som",
                "character": "aqua",
            },
            som_env,
        )
        assert som_obs["handled"] is True
        assert som_obs["intent"] == "screen_observe"
        assert isinstance(som_obs.get("images"), list) and len(som_obs["images"]) == 1
        assert som_obs["images"][0].endswith("-som.png"), f"Expected -som.png annotated image, got {som_obs['images']}"
        assert Path(som_obs["images"][0]).is_file(), f"SoM annotated PNG missing: {som_obs}"
        assert "[1]" in som_obs["text"] and "Apply Changes" in som_obs["text"]

        som_mcp_shot = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "computer-use-linux",
                "tool": "som_screenshot",
                "arguments": {},
            },
            som_env,
        )
        assert som_mcp_shot["result"]["isError"] is False
        assert "SoM Annotated Screenshot:" in som_mcp_shot["result"]["content"][0]["text"]
        assert som_mcp_shot["result"]["structured"]["observation"]["title"] == "GTK Settings Dialog"

        som_click_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "click [1]",
                "character": "aqua",
            },
            som_env,
        )
        assert som_click_turn["handled"] is True
        assert som_click_turn["intent"] == "screen_observe"
        assert "140" in som_click_turn["text"] and "74" in som_click_turn["text"]

        som_mcp_click = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "computer-use-linux",
                "tool": "som_click",
                "arguments": {"elementQuery": "Dark Style"},
            },
            som_env,
        )
        assert som_mcp_click["result"]["isError"] is False
        assert som_mcp_click["result"]["structured"]["x"] == 52
        assert som_mcp_click["result"]["structured"]["y"] == 74

        # 30. Proposal 5: Multi-Format Document Parsing (PDF/DOCX/XLSX/ODT/CSV/HTML/JSON) & SQLite FTS5 Hybrid RAG
        import rag_store
        docs_dir = fake_home / "multi_format_docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # CSV
        csv_file = docs_dir / "quarterly_metrics.csv"
        csv_file.write_text("quarter,revenue_usd,active_users\nQ1,125000,4800\nQ2,168000,6200\n", encoding="utf-8")

        # JSON
        json_file = docs_dir / "deployment_spec.json"
        json_file.write_text(json.dumps({"service": "alis-companion-rag", "fts5Hybrid": True, "port": 9091}), encoding="utf-8")

        # HTML
        html_file = docs_dir / "architecture_guide.html"
        html_file.write_text("<html><head><title>Hybrid RAG Architecture</title></head><body><h1>SQLite FTS5 BM25</h1><p>Combines Porter stemming BM25 with character 3-gram semantic similarity.</p></body></html>", encoding="utf-8")

        # DOCX (valid OOXML zip with word/document.xml)
        docx_file = docs_dir / "executive_brief.docx"
        with zipfile.ZipFile(docx_file, "w") as zf:
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>Project Alis-Companion Executive Brief</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Wayland Niri tiling window orchestration and Set-of-Mark visual grounding.</w:t></w:r></w:p>'
                '</w:body></w:document>',
            )

        # XLSX (valid OOXML zip with xl/sharedStrings.xml and xl/worksheets/sheet1.xml)
        xlsx_file = docs_dir / "budget_forecast.xlsx"
        with zipfile.ZipFile(xlsx_file, "w") as zf:
            zf.writestr(
                "xl/sharedStrings.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<si><t>Department</t></si><si><t>GPU Cluster Allocation</t></si><si><t>Approved</t></si>'
                '</sst>',
            )
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>98500</v></c></row></sheetData>'
                '</worksheet>',
            )

        # ODT (valid OpenDocument zip with content.xml)
        odt_file = docs_dir / "security_policy.odt"
        with zipfile.ZipFile(odt_file, "w") as zf:
            zf.writestr(
                "content.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                '<office:body><office:text>'
                '<text:p>Bubblewrap Sandbox Isolation Policy for Linux Desktop Agents.</text:p>'
                '</office:text></office:body></office:document-content>',
            )

        # PDF (valid PDF stream with text operator Tj)
        pdf_file = docs_dir / "vector_manual.pdf"
        pdf_file.write_bytes(
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"4 0 obj\n<< /Length 74 >>\nstream\nBT /F1 12 Tf 72 712 Td (Alis Companion Offline Voice Piper Whisper Manual) Tj ET\nendstream\nendobj\n%%EOF\n"
        )

        assert "168000" in rag_store.extract_document_markdown(csv_file)
        assert "alis-companion-rag" in rag_store.extract_document_markdown(json_file)
        assert "SQLite FTS5 BM25" in rag_store.extract_document_markdown(html_file)
        assert "Executive Brief" in rag_store.extract_document_markdown(docx_file)
        assert "GPU Cluster Allocation" in rag_store.extract_document_markdown(xlsx_file)
        assert "Bubblewrap Sandbox Isolation Policy" in rag_store.extract_document_markdown(odt_file)
        assert "Offline Voice Piper Whisper Manual" in rag_store.extract_document_markdown(pdf_file)

        rag_add_multi = run_helper(
            {"action": "rag_action", "op": "add", "path": str(docs_dir), "label": "MultiFormatDocs"},
            env,
        )
        assert any(s["label"] == "MultiFormatDocs" for s in rag_add_multi["sources"])

        rag_docx_q = run_helper(
            {"action": "rag_action", "op": "query", "query": "Executive Brief Set-of-Mark visual grounding"},
            env,
        )
        assert rag_docx_q["ragUsed"] is True
        assert rag_docx_q.get("fts5Used") is True
        assert rag_docx_q.get("retrievalMode") == "hybrid_fts5_bm25_semantic"
        assert any("executive_brief.docx" in (s.get("file", "") if isinstance(s, dict) else str(s)) for s in rag_docx_q["snippets"]), f"Expected DOCX hit in {rag_docx_q}"

        rag_xlsx_q = run_helper(
            {"action": "rag_action", "op": "query", "query": "GPU Cluster Allocation 98500"},
            env,
        )
        assert rag_xlsx_q["ragUsed"] is True
        assert any("budget_forecast.xlsx" in (s.get("file", "") if isinstance(s, dict) else str(s)) for s in rag_xlsx_q["snippets"]), f"Expected XLSX hit in {rag_xlsx_q}"

        # 31. Proposal 6: Multi-File Attachments / @file Context & Local Offline Voice STT/TTS
        import agent_runtime
        att_resolved = agent_runtime.resolve_file_attachments_and_mentions(
            prompt=f"Please compare @{csv_file.name} with the executive brief",
            attached_files=[str(docx_file)],
            session_ctx={"lastDirectory": str(docs_dir)},
            companion_root=ROOT,
        )
        assert len(att_resolved["files"]) == 2
        resolved_names = [f["name"] for f in att_resolved["files"]]
        assert "executive_brief.docx" in resolved_names and "quarterly_metrics.csv" in resolved_names
        assert "Executive Brief" in att_resolved["contextMarkdown"]
        assert "168000" in att_resolved["contextMarkdown"]

        # Binary/image-only PDF without extractable text must NOT leak raw binary/NUL bytes into contextMarkdown
        binary_pdf = docs_dir / "scanned_image_only.pdf"
        binary_pdf.write_bytes(b"%PDF-1.4\n1 0 obj\nstream\n\x00\xff\xfeBINARY_GARBAGE_STREAM\x00\nendstream\n%%EOF\n")
        att_bin = agent_runtime.resolve_file_attachments_and_mentions(
            prompt="Inspect @scanned_image_only.pdf",
            attached_files=[],
            session_ctx={"lastDirectory": str(docs_dir)},
            companion_root=ROOT,
        )
        assert "\x00" not in att_bin["contextMarkdown"]
        assert "BINARY_GARBAGE_STREAM" not in att_bin["contextMarkdown"]
        assert "No extractable text found" in att_bin["contextMarkdown"]

        # Test agent_turn with @file mention and attachedFiles
        file_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"tóm tắt file {docx_file}",
                "attachedFiles": [str(csv_file)],
                "character": "aqua",
            },
            env,
        )
        assert file_turn["handled"] is True
        assert len(file_turn.get("attachedFiles") or []) >= 1

        # Test Local Offline Voice STT/TTS
        wav_path = tmp_path / "sample_voice.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 1600)

        voice_env = dict(env)
        voice_env["AQUA_TEST_VOICE_TRANSCRIPT"] = "Kiểm tra trạng thái hệ thống bằng giọng nói"

        v_status = run_helper({"action": "voice_action", "op": "status"}, voice_env)
        assert v_status["ok"] is True and "sttBackend" in v_status and "ttsBackend" in v_status

        v_mcp_status = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "pipewire-mpris-mcp",
                "tool": "voice_status",
                "arguments": {},
            },
            voice_env,
        )
        assert v_mcp_status["result"]["isError"] is False
        assert "sttBackend" in v_mcp_status["result"]["structured"]

        v_stt = run_helper(
            {"action": "voice_action", "op": "stt", "audioPath": str(wav_path), "language": "vi"},
            voice_env,
        )
        assert v_stt["ok"] is True
        assert v_stt["transcript"] == "Kiểm tra trạng thái hệ thống bằng giọng nói"
        assert v_stt["sttBackend"] == "fixture-whisper"

        stt_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/listen",
                "audioPath": str(wav_path),
                "character": "aqua",
            },
            voice_env,
        )
        assert stt_turn["handled"] is True and stt_turn["intent"] == "voice_task"
        assert "fixture-whisper" in stt_turn["text"]

        # Confirm /voice slash command has been removed in favor of the dedicated Voice button
        assert agent_runtime.classify_intent("/voice", {})["kind"] == "chat"
        assert agent_runtime.classify_intent("/voice speak test", {})["kind"] == "chat"

        import desktop_control
        assert desktop_control._clean_stt_transcript("[BLANK_AUDIO]") == ""
        assert desktop_control._clean_stt_transcript("Hello, can you hear me?Hello, can you hear me?") == "Hello, can you hear me?"
        unfinalized_wav = tmp_path / "unfinalized.wav"
        raw_wav = bytearray(wav_path.read_bytes())
        raw_wav[4:8] = b"\xff\xff\xff\xff"
        raw_wav[40:44] = b"\xff\xff\xff\xff"
        unfinalized_wav.write_bytes(bytes(raw_wav))
        desktop_control._repair_wav_header_if_needed(unfinalized_wav)
        repaired_bytes = unfinalized_wav.read_bytes()
        assert repaired_bytes[4:8] != b"\xff\xff\xff\xff" and repaired_bytes[40:44] != b"\xff\xff\xff\xff"

        # Long transcripts (>400 chars) must keep summary <= 400 chars so AquaHelperReplyGuard.parse succeeds
        long_voice_env = dict(env)
        long_transcript = "Xin chào Aqua, đây là câu nói thử nghiệm rất dài " * 12
        long_voice_env["AQUA_TEST_VOICE_TRANSCRIPT"] = long_transcript.strip()
        v_stt_long = run_helper(
            {"action": "voice_action", "op": "stt", "audioPath": str(wav_path), "language": "vi"},
            long_voice_env,
        )
        assert v_stt_long["ok"] is True
        assert v_stt_long["transcript"] == long_transcript.strip()
        assert len(v_stt_long["summary"]) <= 400, f"Expected summary <= 400 chars, got {len(v_stt_long['summary'])}"

        v_tts = run_helper(
            {"action": "voice_action", "op": "tts", "text": "Xin chào, tôi là trợ lý Alis Companion."},
            voice_env,
        )
        assert v_tts["ok"] is True and v_tts["spoken"] is True
        assert v_tts["ttsBackend"] == "fixture-piper"

        tts_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/tts Xin chào từ Alis Companion",
                "character": "aqua",
            },
            voice_env,
        )
        assert tts_turn["handled"] is True and tts_turn["intent"] == "voice_task"

        voice_speak_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/speak Hello from unified voice command",
                "character": "aqua",
            },
            voice_env,
        )
        assert voice_speak_turn["handled"] is True and voice_speak_turn["intent"] == "voice_task"
        assert "Hello from unified voice command" in voice_speak_turn["text"]
        assert "/speak" not in voice_speak_turn["text"]

        # 32. Graphify (graphify-mcp + graphify skill), /som click routing, and tool schema deduplication
        schema_names = [t["function"]["name"] for t in agent_runtime.AGENT_TOOL_SCHEMAS]
        assert "browser_interact" not in schema_names, f"Expected browser_interact removed from {schema_names}"
        assert "voice_io" not in schema_names, f"Expected voice_io removed from {schema_names}"
        assert "mcp_call" in schema_names

        som_click_intent = agent_runtime.classify_intent("/som click 1", {})
        assert som_click_intent["kind"] == "gui_action" and som_click_intent.get("subkind") == "som_click", (
            f"Expected /som click 1 to route to gui_action (som_click), got {som_click_intent}"
        )
        som_click_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/som click 1",
                "character": "aqua",
            },
            som_env,
        )
        assert som_click_turn["handled"] is True and som_click_turn["intent"] == "gui_action", (
            f"Expected /som click 1 turn to execute gui_action, got {som_click_turn}"
        )
        assert som_click_turn["status"] == "completed" and "140" in som_click_turn["text"], (
            f"Expected /som click 1 with som_env to succeed at (140, 74), got {som_click_turn}"
        )

        # Verify sqlite-mcp describe_table & read_query on existing SQLite DB (regression for missing import re)
        sq_desc = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "sqlite-mcp",
                "tool": "describe_table",
                "arguments": {"db_path": env["ALIS_AQUA_HISTORY_DB"], "table_name": "messages"},
            },
            env,
        )
        assert sq_desc["result"]["isError"] is False
        assert any(c["name"] == "content" for c in sq_desc["result"]["structured"]["columns"])
        sq_read = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "sqlite-mcp",
                "tool": "read_query",
                "arguments": {"db_path": env["ALIS_AQUA_HISTORY_DB"], "query": "SELECT id, role FROM messages LIMIT 2"},
            },
            env,
        )
        assert sq_read["result"]["isError"] is False and sq_read["result"]["structured"]["rowCount"] >= 1

        # Verify graphify-mcp server & all 10 tools + mcp.json synchronization
        mcp_list_after = run_helper({"action": "mcp_action", "op": "list"}, env)
        server_names = [s["name"] for s in mcp_list_after["servers"]]
        assert "graphify-mcp" in server_names, f"Expected graphify-mcp in {server_names}"
        assert "companion-filesystem" not in server_names
        assert "companion-git" not in server_names
        assert "companion-rag" not in server_names

        mcp_cfg_json = Path(env["XDG_CONFIG_HOME"]) / "alis-companion" / "mcp.json"
        assert mcp_cfg_json.is_file(), f"Expected mcp.json synced at {mcp_cfg_json}"
        mcp_cfg_data = json.loads(mcp_cfg_json.read_text(encoding="utf-8"))
        assert "graphify-mcp" in mcp_cfg_data.get("mcpServers", {}), f"Missing graphify-mcp in {mcp_cfg_data}"
        assert "playwright-mcp" in mcp_cfg_data.get("mcpServers", {}), f"Missing playwright-mcp in {mcp_cfg_data}"

        graphify_tools_res = run_helper(
            {"action": "mcp_action", "op": "tools", "server": "graphify-mcp"},
            env,
        )
        graphify_tool_names = [t["name"] for t in graphify_tools_res["tools"]]
        for expected_gtool in (
            "query_graph",
            "get_node",
            "get_neighbors",
            "get_community",
            "god_nodes",
            "graph_stats",
            "shortest_path",
            "list_prs",
            "get_pr_impact",
            "triage_prs",
        ):
            assert expected_gtool in graphify_tool_names, f"Missing {expected_gtool} in {graphify_tool_names}"

        g_stats_call = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "graphify-mcp",
                "tool": "graph_stats",
                "arguments": {"projectPath": str(ROOT)},
            },
            env,
        )
        assert g_stats_call["result"]["isError"] is False
        assert g_stats_call["result"]["structured"]["nodes"] > 0

        # Empty/corrupted graph.json must gracefully fall back to AST synthesis
        empty_graph_dir = sample_proj / "graphify-out"
        empty_graph_dir.mkdir(parents=True, exist_ok=True)
        (empty_graph_dir / "graph.json").write_text('{"nodes": [], "links": []}', encoding="utf-8")
        g_empty_fallback = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "graphify-mcp",
                "tool": "graph_stats",
                "arguments": {"projectPath": str(sample_proj)},
            },
            env,
        )
        assert g_empty_fallback["result"]["structured"]["nodes"] > 0, (
            f"Expected AST synthesis fallback when graph.json has 0 nodes, got {g_empty_fallback}"
        )

        # Write a multi-hop graph.json to test get_node not-found, DFS query_graph, and BFS multi-hop shortest_path
        (empty_graph_dir / "graph.json").write_text(
            json.dumps({
                "nodes": [
                    {"id": "n_alpha", "label": "AlphaService", "source_file": "alpha.py", "file_type": "code", "community": 0},
                    {"id": "n_beta", "label": "BetaRouter", "source_file": "beta.py", "file_type": "code", "community": 0},
                    {"id": "n_gamma", "label": "GammaStore", "source_file": "gamma.py", "file_type": "code", "community": 1},
                    {"id": "n_isolated", "label": "IsolatedNode", "source_file": "iso.py", "file_type": "code", "community": 2},
                ],
                "links": [
                    {"source": "n_alpha", "target": "n_beta", "relation": "routes_to", "confidence": "EXTRACTED"},
                    {"source": "n_beta", "target": "n_gamma", "relation": "persists_in", "confidence": "EXTRACTED"},
                ],
            }),
            encoding="utf-8",
        )
        g_not_found = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "graphify-mcp",
                "tool": "get_node",
                "arguments": {"label": "NonExistentSymbolXYZ999", "projectPath": str(sample_proj)},
            },
            env,
        )
        assert g_not_found["result"]["structured"]["node"] is None
        assert "not found in graph" in g_not_found["result"]["content"][0]["text"]

        g_sp_2hop = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "graphify-mcp",
                "tool": "shortest_path",
                "arguments": {"source": "AlphaService", "target": "GammaStore", "projectPath": str(sample_proj)},
            },
            env,
        )
        assert len(g_sp_2hop["result"]["structured"]["hops"]) == 2
        assert "2 hop(s)" in g_sp_2hop["result"]["content"][0]["text"]

        g_sp_unreach = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "graphify-mcp",
                "tool": "shortest_path",
                "arguments": {"source": "AlphaService", "target": "IsolatedNode", "projectPath": str(sample_proj)},
            },
            env,
        )
        assert g_sp_unreach["result"]["structured"]["hops"] == []
        assert "No path found" in g_sp_unreach["result"]["content"][0]["text"]

        g_dfs_call = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "graphify-mcp",
                "tool": "query_graph",
                "arguments": {"question": "AlphaService", "mode": "dfs", "token_budget": 300, "projectPath": str(sample_proj)},
            },
            env,
        )
        assert "Graphify DFS Query" in g_dfs_call["result"]["content"][0]["text"]

        g_god_call = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "graphify-mcp",
                "tool": "god_nodes",
                "arguments": {"top_n": 5, "projectPath": str(ROOT)},
            },
            env,
        )
        assert g_god_call["result"]["isError"] is False
        assert "God Nodes" in g_god_call["result"]["content"][0]["text"]

        g_query_call = run_helper(
            {
                "action": "mcp_action",
                "op": "call_tool",
                "server": "graphify-mcp",
                "tool": "query_graph",
                "arguments": {"question": "mcp_client", "projectPath": str(ROOT)},
            },
            env,
        )
        assert g_query_call["result"]["isError"] is False
        assert "Graphify BFS Query" in g_query_call["result"]["content"][0]["text"]

        # Verify graphify skill & references
        skills_after = run_helper({"action": "skill_action", "op": "list"}, env)
        graphify_skill = next((s for s in skills_after["skills"] if s["name"] == "graphify"), None)
        assert graphify_skill is not None, "Expected built-in graphify skill in skill_store"
        assert len(graphify_skill.get("references") or []) >= 8, f"Expected >=8 reference files in graphify skill: {graphify_skill}"

        graphify_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/graphify mcp_client",
                "character": "aqua",
            },
            env,
        )
        assert graphify_turn["handled"] is True
        assert "Graphify Knowledge Graph" in graphify_turn["text"] or "God Nodes" in graphify_turn["text"]

        # 33. Anti-hallucination (@file missing warning, Vietnamese RAG stopwords, rulesText & /rule sync, temperature & systemPrompt propagation)
        att_missing = agent_runtime.resolve_file_attachments_and_mentions(
            prompt="Hãy đọc @definitely_missing_ghost_file_999.py và giải thích",
            attached_files=[],
            session_ctx={"lastDirectory": str(docs_dir)},
            companion_root=ROOT,
        )
        assert "definitely_missing_ghost_file_999.py" in (att_missing.get("missingMentions") or []), f"Expected missing mention in {att_missing}"
        assert "### Missing Mentioned File(s)" in att_missing.get("contextMarkdown", "")
        assert "Do NOT fabricate" in att_missing.get("contextMarkdown", "")

        rule_add_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/rule Always verify line numbers with read_file before citing code.",
                "character": "aqua",
            },
            env,
        )
        assert rule_add_turn["handled"] is True
        assert "Always verify line numbers with read_file before citing code." in rule_add_turn.get("agentRules", {}).get("rulesText", "")

        rule_remove_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/rule remove Always verify line numbers",
                "character": "aqua",
            },
            env,
        )
        assert rule_remove_turn["handled"] is True
        assert "Always verify line numbers with read_file before citing code." not in rule_remove_turn.get("agentRules", {}).get("rulesText", "")

        rules_set_res = run_helper(
            {
                "action": "agent_rules",
                "op": "set_rules_text",
                "rulesText": "- Rule Alpha\n- Rule Beta",
            },
            env,
        )
        assert "rulesText" in rules_set_res and "defaultRulesText" in rules_set_res
        assert "- Rule Alpha" in rules_set_res["rulesText"] and "- Rule Beta" in rules_set_res["rulesText"]

        # Verify temperature (0.35 and 0.0), custom systemPrompt, and rulesText propagation to LLM request body
        _MockReActHandler.calls = []
        prop_server = ThreadingHTTPServer(("127.0.0.1", 0), _MockReActHandler)
        prop_thread = threading.Thread(target=prop_server.serve_forever, daemon=True)
        prop_thread.start()
        prop_endpoint = f"http://127.0.0.1:{prop_server.server_port}/v1/chat/completions"
        try:
            prop_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "/system",
                    "character": "aqua",
                    "endpoint": prop_endpoint,
                    "model": "qwen3.5-prop-test",
                    "synthesizeWithLlm": True,
                    "temperature": 0.35,
                    "systemPrompt": "CUSTOM_COMPANION_SYSTEM_PROMPT_TOKEN_42",
                    "rulesText": "- Rule Alpha\n- Rule Beta",
                },
                env,
            )
            assert prop_turn["handled"] is True
            assert len(_MockReActHandler.calls) >= 1
            sent_body = _MockReActHandler.calls[-1]
            assert abs(float(sent_body.get("temperature", -1.0)) - 0.35) < 1e-6, f"Expected temperature 0.35 in {sent_body}"
            sys_msgs = " ".join(str(m.get("content") or "") for m in (sent_body.get("messages") or []) if m.get("role") == "system")
            assert "CUSTOM_COMPANION_SYSTEM_PROMPT_TOKEN_42" in sys_msgs, f"Expected custom systemPrompt in {sys_msgs}"
            assert "- Rule Alpha" in sys_msgs and "- Rule Beta" in sys_msgs, f"Expected rulesText in {sys_msgs}"

            _MockReActHandler.calls = []
            zero_temp_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "/system",
                    "character": "aqua",
                    "endpoint": prop_endpoint,
                    "model": "qwen3.5-prop-test",
                    "synthesizeWithLlm": True,
                    "temperature": 0.0,
                },
                env,
            )
            assert zero_temp_turn["handled"] is True
            assert len(_MockReActHandler.calls) >= 1
            zero_body = _MockReActHandler.calls[-1]
            assert float(zero_body.get("temperature", -1.0)) == 0.0, f"Expected temperature 0.0 preserved in {zero_body}"
        finally:
            prop_server.shutdown()
            prop_server.server_close()

        rule_reset_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/rule reset",
                "character": "aqua",
            },
            env,
        )
        assert rule_reset_turn["handled"] is True
        assert "Ground every file, directory, or code claim" in rule_reset_turn.get("agentRules", {}).get("rulesText", "")

        vn_rag_noise = rag_store.query_hybrid_rag(
            "là có và các những một để với",
            [{"source": "doc.md", "heading": "Intro", "text": "Đây là một tài liệu có các phần để đọc."}],
        )
        assert vn_rag_noise == []

        # 34. Clean web search query extraction, English planning intent, deep webpage reading (fetch_url), and LLM synthesis
        import web_tools
        cleaned_party_q = web_tools.clean_search_query("Plan a party for me tomorrow. You can search the internet.", for_plan=True)
        assert "you can search the internet" not in cleaned_party_q.lower(), f"Expected meta instruction stripped, got {cleaned_party_q!r}"
        assert "for me" not in cleaned_party_q.lower(), f"Expected 'for me' stripped, got {cleaned_party_q!r}"
        assert "party" in cleaned_party_q.lower() and "tomorrow" in cleaned_party_q.lower(), f"Expected core party query preserved, got {cleaned_party_q!r}"

        compound_bday_q = web_tools.clean_search_query(
            "Can you help me plan a birthday dinner this weekend? Feel free to browse the web and check ideas for me.",
            for_plan=True,
        )
        assert "feel free to browse" not in compound_bday_q.lower() and "can you" not in compound_bday_q.lower(), (
            f"Expected compound meta-clause and modal prefix stripped, got {compound_bday_q!r}"
        )
        assert "birthday dinner this weekend" in compound_bday_q.lower(), f"Expected topic preserved, got {compound_bday_q!r}"

        inline_topic_q = web_tools.clean_search_query(
            "Plan a birthday party for my friend tomorrow. Search online for games and food ideas.",
            for_plan=True,
        )
        assert "search online for" not in inline_topic_q.lower(), f"Expected inline 'Search online for' stripped, got {inline_topic_q!r}"
        assert "games and food ideas" in inline_topic_q.lower(), f"Expected inline sub-topic preserved, got {inline_topic_q!r}"

        vi_party_q = agent_runtime._extract_plan_search_query(
            "Lên kế hoạch cho một buổi tiệc ngày mai, bạn có thể tìm trên mạng và tổng hợp lại cho tôi.",
            "vi",
        )
        assert "buổi tiệc" in vi_party_q.lower() and "một tiệc" not in vi_party_q.lower(), (
            f"Expected 'buổi tiệc' preserved without corrupting to 'một tiệc', got {vi_party_q!r}"
        )
        assert "tìm trên mạng" not in vi_party_q.lower() and "tổng hợp" not in vi_party_q.lower(), (
            f"Expected Vietnamese compound meta-clause stripped, got {vi_party_q!r}"
        )

        party_intent = agent_runtime.classify_intent("Plan a party for me tomorrow. You can search the internet.", {})
        assert party_intent["kind"] == "code_task" and party_intent.get("subkind") == "plan" and party_intent.get("language") == "en", (
            f"Expected English planning intent, got {party_intent}"
        )
        lead_in_party_intent = agent_runtime.classify_intent("You can search the internet and plan a party for me tomorrow.", {})
        assert lead_in_party_intent["kind"] == "code_task" and lead_in_party_intent.get("subkind") == "plan", (
            f"Expected compound lead-in planning intent, got {lead_in_party_intent}"
        )

        party_web_env = dict(env)
        party_web_env["AQUA_TEST_WEB_FIXTURE"] = json.dumps({
            "results": [
                {
                    "title": "JS Only Shell App",
                    "url": "https://js-shell.example.net/app",
                    "snippet": "Interactive app requiring JavaScript.",
                },
                {
                    "title": "How to Throw a Last-Minute Party Checklist",
                    "url": "https://example.org/party-checklist",
                    "snippet": "Step-by-step checklist for hosting a great party tomorrow with minimal stress.",
                },
                {
                    "title": "Easy Party Finger Foods and Drinks Guide",
                    "url": "https://partyguide.example.com/menu-ideas",
                    "snippet": "Quick snacks, mocktails, and playlists you can prepare in 2 hours.",
                },
            ],
            "pages": {
                "https://js-shell.example.net/app": {
                    "title": "JS Only Shell App",
                    "headings": ["Loading..."],
                    "lines": [],
                },
                "https://example.org/party-checklist": {
                    "title": "How to Throw a Last-Minute Party Checklist",
                    "headings": ["24-Hour Timeline", "Shopping List"],
                    "lines": [
                        "Send digital invites via group text right away and confirm headcount.",
                        "Focus on 3 crowd-pleasing finger foods, 1 signature drink, and ice.",
                        "Set up lighting and a 3-hour upbeat playlist 1 hour before guests arrive.",
                    ],
                },
                "https://partyguide.example.com/menu-ideas": {
                    "title": "Easy Party Finger Foods and Drinks Guide",
                    "headings": ["Finger Foods", "Drinks"],
                    "lines": [
                        "Charcuterie board, sliders, and tortilla chips with guacamole take 20 minutes.",
                        "Sparkling citrus punch works for both alcoholic and non-alcoholic guests.",
                    ],
                },
            },
        })

        _MockReActHandler.calls = []
        plan_server = ThreadingHTTPServer(("127.0.0.1", 0), _MockReActHandler)
        plan_thread = threading.Thread(target=plan_server.serve_forever, daemon=True)
        plan_thread.start()
        plan_endpoint = f"http://127.0.0.1:{plan_server.server_port}/v1/chat/completions"
        try:
            party_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "Plan a party for me tomorrow. You can search the internet.",
                    "character": "aqua",
                    "endpoint": plan_endpoint,
                    "model": "lmstudio-qwen3.5-plan-test",
                    "thinkingEffort": "max",
                    "synthesizeWithLlm": True,
                },
                party_web_env,
            )
            assert party_turn["handled"] is True and party_turn["intent"] == "code_task", f"Unexpected party_turn: {party_turn}"
            act_steps = " | ".join(party_turn.get("activitySteps") or [])
            assert "Searched web:" in act_steps, f"Expected Searched web step in {act_steps}"
            assert "Read webpage: example.org" in act_steps and "Read webpage: partyguide.example.com" in act_steps, (
                f"Expected JS-only empty-body page skipped so both real webpages are read, got {act_steps}"
            )
            assert "Synthesized response" in act_steps, f"Expected Synthesized response step in {act_steps}"
            assert "📚 **Sources:**" in party_turn["text"], f"Expected Sources footer in {party_turn['text']}"
            assert "https://example.org/party-checklist" in party_turn["text"]
            assert _MockReActHandler.calls and _MockReActHandler.calls[-1].get("max_tokens", 0) >= 1600, (
                f"Expected max_tokens >= 1600 for max effort, got {_MockReActHandler.calls[-1] if _MockReActHandler.calls else None}"
            )
        finally:
            plan_server.shutdown()
            plan_server.server_close()

        # Multi-clause voice YouTube/browser query extraction & concise open_browser confirmation
        spoken_yt_1 = "Okay, and now I want you to open the internet and open YouTube, then search everybody loves somebody."
        assert web_tools.clean_search_query(spoken_yt_1) == "everybody loves somebody"
        spoken_yt_1_info = web_tools.extract_clean_browser_query(spoken_yt_1, engine="youtube")
        assert spoken_yt_1_info["query"] == "everybody loves somebody", f"Unexpected spoken_yt_1_info: {spoken_yt_1_info}"
        assert spoken_yt_1_info["engine"] == "youtube" and spoken_yt_1_info["wantsPlay"] is False

        spoken_yt_2 = "Well, can you open the browser, go to YouTube, and play Fly Me To The Moon?"
        spoken_yt_2_info = web_tools.extract_clean_browser_query(spoken_yt_2, engine="youtube")
        assert spoken_yt_2_info["query"] == "Fly Me To The Moon", f"Unexpected spoken_yt_2_info: {spoken_yt_2_info}"
        assert spoken_yt_2_info["engine"] == "youtube" and spoken_yt_2_info["wantsPlay"] is True

        spoken_yt_3 = "Okay, mở trình duyệt vào YouTube rồi tìm kiếm Chúng Ta Của Hiện Tại."
        spoken_yt_3_info = web_tools.extract_clean_browser_query(spoken_yt_3, engine="youtube")
        assert spoken_yt_3_info["query"] == "Chúng Ta Của Hiện Tại", f"Unexpected spoken_yt_3_info: {spoken_yt_3_info}"
        assert spoken_yt_3_info["engine"] == "youtube" and spoken_yt_3_info["wantsPlay"] is False

        launch_env = web_tools._build_desktop_launch_env()
        assert "XDG_DATA_DIRS" in launch_env and "/var/lib/flatpak/exports/share" in launch_env["XDG_DATA_DIRS"]

        spoken_turn_1 = run_helper(
            {
                "action": "agent_turn",
                "prompt": spoken_yt_1,
                "character": "aqua",
                "synthesizeWithLlm": True,
            },
            env,
        )
        assert spoken_turn_1["handled"] is True and spoken_turn_1["intent"] == "web_task"
        assert spoken_turn_1["synthesized"] is False
        assert "https://www.youtube.com/results?search_query=everybody+loves+somebody" in spoken_turn_1["text"]
        assert "Okay%2C+and+now" not in spoken_turn_1["text"]
        assert "Related search results (Web Search Eyes)" not in spoken_turn_1["text"]
        assert "\n\n" not in spoken_turn_1["text"].strip(), f"Expected concise 1-sentence confirmation, got: {spoken_turn_1['text']!r}"

        spoken_turn_2 = run_helper(
            {
                "action": "agent_turn",
                "prompt": spoken_yt_2,
                "character": "aqua",
                "synthesizeWithLlm": True,
            },
            yt_env,
        )
        assert spoken_turn_2["handled"] is True and spoken_turn_2["intent"] == "web_task"
        assert spoken_turn_2["synthesized"] is False
        assert "https://www.youtube.com/watch?v=z-2_OstpR5c" in spoken_turn_2["text"]
        assert "Fly Me To The Moon" in spoken_turn_2["text"]
        assert "Related search results" not in spoken_turn_2["text"]

        spoken_turn_3 = run_helper(
            {
                "action": "agent_turn",
                "prompt": spoken_yt_3,
                "character": "aqua",
                "synthesizeWithLlm": True,
            },
            env,
        )
        assert spoken_turn_3["handled"] is True and spoken_turn_3["intent"] == "web_task"
        assert spoken_turn_3["synthesized"] is False
        assert "https://www.youtube.com/results?search_query=Ch%C3%BAng+Ta+C%E1%BB%A7a+Hi%E1%BB%87n+T%E1%BA%A1i" in spoken_turn_3["text"]
        assert "**Chúng Ta Của Hiện Tại**" in spoken_turn_3["text"]
        assert "Kết quả liên quan" not in spoken_turn_3["text"]

        # Verify song titles starting with filler/action words and "Play ... on YouTube" routing are preserved intact
        os.environ["ALIS_TEST_HARNESS"] = "1"
        edge_cases = [
            ("Play everybody loves somebody on YouTube.", "everybody loves somebody", "youtube", True),
            ("Okay, mở trình duyệt vào YouTube rồi tìm kiếm Hãy Trao Cho Anh.", "Hãy Trao Cho Anh", "youtube", False),
            ("Open YouTube and search Play With Fire.", "Play With Fire", "youtube", False),
            ("Open YouTube and search Hello by Adele.", "Hello by Adele", "youtube", False),
            ("Open YouTube and search Now And Then Beatles.", "Now And Then Beatles", "youtube", False),
            ("Okay, open the internet, find YouTube, and search everybody loves somebody.", "everybody loves somebody", "youtube", False),
            ("Okay, open the browser and search YouTube for everybody loves somebody.", "everybody loves somebody", "youtube", False),
            ("Can you go to YouTube and play a song called Fly Me To The Moon?", "Fly Me To The Moon", "youtube", True),
            ("Search everybody loves somebody on YouTube again.", "everybody loves somebody", "youtube", False),
            ("Search Everybody loves somebody - Dean Martin on Youtube", "Everybody loves somebody - Dean Martin", "youtube", False),
            ("Mở trình duyệt vào YouTube tìm bài hát Chiều hôm ấy lại nhé.", "Chiều hôm ấy", "youtube", False),
            ("Open youtube.com and search Everybody loves somebody", "Everybody loves somebody", "youtube", False),
            ("Open Everybody loves somebody on YouTube", "Everybody loves somebody", "youtube", False),
        ]
        for ec_prompt, ec_q, ec_eng, ec_play in edge_cases:
            ec_info = web_tools.extract_clean_browser_query(ec_prompt)
            assert ec_info["query"] == ec_q, f"Expected {ec_q!r} for {ec_prompt!r}, got {ec_info}"
            assert ec_info["engine"] == ec_eng and ec_info["wantsPlay"] is ec_play, f"Unexpected engine/play for {ec_prompt!r}: {ec_info}"
            ec_opened = web_tools.open_in_browser(ec_prompt, engine=ec_eng, play_media=ec_play)
            assert ec_opened["cleanQuery"] == ec_q, f"Double-clean corrupted {ec_prompt!r}: {ec_opened}"
            assert ec_opened["dryRun"] is True and ec_opened["opened"] is False

        # Verify Flatpak document-portal directory provisioning and bwrap error detection
        fake_rt = Path(d) / "fake_xdg_runtime"
        (fake_rt / "app" / "org.example.CustomBrowser").mkdir(parents=True, exist_ok=True)
        fp_apps = web_tools._ensure_flatpak_doc_portal_dirs({"XDG_RUNTIME_DIR": str(fake_rt)})
        assert "app.zen_browser.zen" in fp_apps and "org.example.CustomBrowser" in fp_apps
        assert (fake_rt / "doc" / "by-app" / "app.zen_browser.zen").is_dir()
        assert (fake_rt / "doc" / "by-app" / "org.example.CustomBrowser").is_dir()
        assert web_tools._run_browser_opener(
            ["/usr/bin/python3", "-c", "import sys; sys.stderr.write('bwrap: Can\\'t find source path /run/user/1000/doc/by-app/app.zen_browser.zen\\n')"],
            dict(os.environ),
            wait_sec=0.5,
        ) is False
        assert web_tools._run_browser_opener(
            ["/usr/bin/python3", "-c", "import sys; sys.stderr.write('[2] Sandbox: CanCreateUserNamespace() clone() failure: EPERM\\n')"],
            dict(os.environ),
            wait_sec=0.5,
        ) is True
        # Ensure forked grandchild that emits a benign warning first and a bwrap error 300ms later is not cut off prematurely
        assert web_tools._run_browser_opener(
            [
                "/usr/bin/python3",
                "-c",
                "import os, sys, time\n"
                "pid = os.fork()\n"
                "if pid > 0:\n"
                "    os._exit(0)\n"
                "sys.stderr.write('[2] Sandbox: CanCreateUserNamespace() clone() failure: EPERM\\n')\n"
                "sys.stderr.flush()\n"
                "time.sleep(0.32)\n"
                "sys.stderr.write('bwrap: Can\\'t find source path /run/user/1000/doc/by-app/app.zen_browser.zen\\n')\n"
                "sys.stderr.flush()\n",
            ],
            dict(os.environ),
            wait_sec=0.8,
        ) is False

        spoken_turn_4 = run_helper(
            {
                "action": "agent_turn",
                "prompt": "Play everybody loves somebody on YouTube.",
                "character": "aqua",
                "synthesizeWithLlm": True,
            },
            yt_env,
        )
        assert spoken_turn_4["handled"] is True and spoken_turn_4["intent"] == "web_task"
        assert spoken_turn_4["synthesized"] is False
        assert "https://www.youtube.com/watch?v=z-2_OstpR5c" in spoken_turn_4["text"]

        # 35. Audit regressions: stderr __AQUA_STEP__ emission, extensionless @mentions, 0-result web guard, RAG score threshold & user-source isolation
        import io
        stderr_buf = io.StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr_buf
            agent_runtime._reset_activity_steps()
            agent_runtime._emit_activity_step("Searched web: audit verification", kind="web_search")
        finally:
            sys.stderr = old_stderr
        assert "__AQUA_STEP__:" in stderr_buf.getvalue(), f"Expected __AQUA_STEP__: written to sys.stderr, got {stderr_buf.getvalue()!r}"
        assert "Searched web: audit verification" in stderr_buf.getvalue()

        extless_missing = agent_runtime.resolve_file_attachments_and_mentions(
            prompt="Check @Makefile, @Dockerfile, @PKGBUILD, and @README in this directory.",
            attached_files=[],
            session_ctx={"lastDirectory": str(Path(d) / "empty_extless_dir")},
            companion_root=Path(d) / "empty_extless_dir",
        )
        for extless_name in ("Makefile", "Dockerfile", "PKGBUILD", "README"):
            assert extless_name in (extless_missing.get("missingMentions") or []), (
                f"Expected extensionless @{extless_name} in missingMentions, got {extless_missing}"
            )

        empty_web_env = dict(env)
        empty_web_env["AQUA_TEST_WEB_FIXTURE"] = json.dumps({
            "results": [],
            "pages": {
                "https://empty-audit-fixture.example.org/blank": {
                    "ok": True,
                    "url": "https://empty-audit-fixture.example.org/blank",
                    "title": "Blank Page",
                    "headings": [],
                    "lines": [],
                    "links": [],
                }
            },
        })
        _MockReActHandler.calls = []
        zero_web_server = ThreadingHTTPServer(("127.0.0.1", 0), _MockReActHandler)
        zero_web_thread = threading.Thread(target=zero_web_server.serve_forever, daemon=True)
        zero_web_thread.start()
        zero_web_endpoint = f"http://127.0.0.1:{zero_web_server.server_port}/v1/chat/completions"
        try:
            zero_search_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "/web search xyznonexistentquery987654321",
                    "character": "aqua",
                    "endpoint": zero_web_endpoint,
                    "model": "lmstudio-qwen3.5-plan-test",
                    "synthesizeWithLlm": True,
                },
                empty_web_env,
            )
            assert zero_search_turn["handled"] is True and zero_search_turn["intent"] == "web_task"
            assert zero_search_turn["synthesized"] is False, f"Expected no LLM synthesis when web search returns 0 results: {zero_search_turn}"
            assert len(_MockReActHandler.calls) == 0, f"Expected 0 LLM calls on empty web search, got {len(_MockReActHandler.calls)}"

            # Empty fetch_url page (where mcp_client returns Title/URL header with 0 lines/headings) must also skip LLM synthesis
            zero_fetch_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "/fetch https://empty-audit-fixture.example.org/blank",
                    "character": "aqua",
                    "endpoint": zero_web_endpoint,
                    "model": "lmstudio-qwen3.5-plan-test",
                    "synthesizeWithLlm": True,
                },
                empty_web_env,
            )
            assert zero_fetch_turn["handled"] is True and zero_fetch_turn["intent"] == "web_task"
            assert zero_fetch_turn["synthesized"] is False, f"Expected no LLM synthesis when fetch_url returns 0 lines/headings: {zero_fetch_turn}"
            assert len(_MockReActHandler.calls) == 0, f"Expected 0 LLM calls on empty fetch_url, got {len(_MockReActHandler.calls)}"

            zero_plan_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "Plan a party for me tomorrow. You can search the internet.",
                    "character": "aqua",
                    "endpoint": zero_web_endpoint,
                    "model": "lmstudio-qwen3.5-plan-test",
                    "synthesizeWithLlm": True,
                },
                empty_web_env,
            )
            assert zero_plan_turn["handled"] is True and zero_plan_turn["intent"] == "code_task"
            assert len(_MockReActHandler.calls) >= 1
            zero_plan_sys = " ".join(
                str(m.get("content") or "")
                for m in (_MockReActHandler.calls[-1].get("messages") or [])
                if m.get("role") == "system"
            )
            assert "[domain.com]" not in zero_plan_sys, f"Expected offline planning system prompt without [domain.com] citation instruction: {zero_plan_sys}"
            assert "Do NOT invent or cite any domain names" in zero_plan_sys

            # Verify screen_observe VLM prompt injects AT-SPI & OCR ground-truth text alongside screenshot
            _MockReActHandler.calls = []
            vlm_turn = run_helper(
                {
                    "action": "agent_turn",
                    "prompt": "/som",
                    "character": "aqua",
                    "endpoint": zero_web_endpoint,
                    "model": "lmstudio-qwen3.5-plan-test",
                    "synthesizeWithLlm": True,
                },
                som_env,
            )
            assert vlm_turn["handled"] is True and vlm_turn["intent"] == "screen_observe"
            assert len(_MockReActHandler.calls) >= 1
            vlm_msgs_dump = json.dumps(_MockReActHandler.calls[-1].get("messages") or [])
            assert "AT-SPI & OCR Ground-Truth UI Text" in vlm_msgs_dump, f"Expected AT-SPI & OCR ground-truth in VLM prompt: {vlm_msgs_dump}"
            assert "Apply Changes" in vlm_msgs_dump
            assert "[1]" in vlm_turn["text"] and "Apply Changes" in vlm_turn["text"], f"Expected SoM elements preserved after VLM synthesis: {vlm_turn['text']}"
        finally:
            zero_web_server.shutdown()
            zero_web_server.server_close()

        # Verify single-stem 0.65 noise is filtered out by min_score=1.0 even on short chunks with trigram overlap
        stem_noise = rag_store.query_hybrid_rag(
            "compax",
            [{"source": "builtin.md", "heading": "Overview", "text": "Companion mode"}],
        )
        assert stem_noise == [], f"Expected single-stem short-chunk match filtered out by min_score=1.0, got {stem_noise}"

        # Verify user RAG source isolation: when user RAG sources exist, unrelated queries with 0 user hits do NOT fall back to built-in Alis docs
        import code_tools
        isolated_user_dir = Path(d) / "isolated_rag_dir"
        isolated_user_dir.mkdir(parents=True, exist_ok=True)
        (isolated_user_dir / "user_cooking_notes.md").write_text("# Sourdough Recipe\nMix flour, water, and starter for fermentation.\n", encoding="utf-8")
        isolated_rag = code_tools.retrieve_rag(
            "package manifest lifecycle",
            companion_root=ROOT,
            extra_dirs=[isolated_user_dir],
        )
        assert isolated_rag["totalMatches"] == 0 and isolated_rag["snippets"] == [], (
            f"Expected 0 built-in Alis doc hits for unrelated query when user RAG sources exist, got {isolated_rag}"
        )

        # Verify voice_tts auto-selects Vietnamese voice (-v vi / vi-VN-HoaiMyNeural) and English voice (en-US-AvaNeural)
        import desktop_control
        vi_tts = desktop_control.voice_tts("Xin chào thế giới hôm nay", voice="default", out_path=str(Path(d) / "vi_test.wav"))
        assert vi_tts["ok"] is True and vi_tts["voice"] == "vi" and vi_tts.get("neuralVoice") == "vi-VN-HoaiMyNeural", (
            f"Expected voice='vi' and neuralVoice='vi-VN-HoaiMyNeural' for Vietnamese text, got {vi_tts}"
        )
        en_tts = desktop_control.voice_tts("Hello world today", voice="default", out_path=str(Path(d) / "en_test.wav"))
        assert en_tts["ok"] is True and en_tts.get("neuralVoice") == "en-US-AvaNeural", (
            f"Expected neuralVoice='en-US-AvaNeural' for English text, got {en_tts}"
        )

        # Verify modality-matching in agent_runtime: text turn (fromVoice=False) -> text only; voice turn (fromVoice=True) -> voice spoken first, transcript after
        text_turn_res = run_helper({"action": "agent_turn", "prompt": "/skills list", "fromVoice": False}, som_env)
        assert text_turn_res.get("handled") is True and text_turn_res.get("voiceReply", {}).get("spoken") is False, (
            f"Expected text-originated agent_turn to not auto-speak, got {text_turn_res.get('voiceReply')}"
        )
        voice_turn_res = run_helper({"action": "agent_turn", "prompt": "/skills list", "fromVoice": True}, som_env)
        assert (
            voice_turn_res.get("handled") is True
            and voice_turn_res.get("voiceReply", {}).get("spoken") is True
            and voice_turn_res.get("voiceReply", {}).get("transcriptAfterVoice") is True
        ), f"Expected voice-originated agent_turn to speak first and mark transcriptAfterVoice=True, got {voice_turn_res.get('voiceReply')}"

        # Verify export_finetune returns both path and outputPath, and helper actions work end-to-end
        ft_res = run_helper({"action": "export_finetune", "format": "sharegpt"}, som_env)
        assert ft_res["ok"] is True and ft_res.get("path") and ft_res.get("outputPath") == ft_res.get("path"), (
            f"Expected export_finetune to return matching path and outputPath: {ft_res}"
        )
        tts_stop_res = run_helper({"action": "voice_tts_stop"}, som_env)
        assert tts_stop_res["ok"] is True and "stopped" in tts_stop_res, f"Unexpected voice_tts_stop reply: {tts_stop_res}"

        pocket_save_res = run_helper(
            {
                "action": "pocket_save_item",
                "content": "# Saved Assistant Turn\nUse `qs ipc` to inspect Quickshell state.",
                "title": "turn_1",
            },
            som_env,
        )
        assert pocket_save_res.get("saved") is True and pocket_save_res.get("path", "").endswith(".md"), (
            f"Expected pocket_save_item to save snippet markdown and add to Pocket: {pocket_save_res}"
        )
        assert any(it.get("path") == pocket_save_res["path"] for it in pocket_save_res.get("items", []))

        import agent_runtime
        special_mentions_res = agent_runtime.resolve_file_attachments_and_mentions(
            "Summarize @selection and @clipboard and @screen and @pocket and @git-diff please",
            attached_files=[],
            session_ctx={"primarySelection": "Selected line from terminal", "lastDirectory": str(ROOT)},
            companion_root=ROOT,
        )
        assert special_mentions_res["missingMentions"] == [], (
            f"Special @mentions must not be treated as missing disk files: {special_mentions_res}"
        )
        for expected_hdr in (
            "Highlighted Selection (`@selection`)",
            "Clipboard (`@clipboard`)",
            "Active Screen / Windows (`@screen`)",
            "Pocket Items (`@pocket`)",
            "Workspace Git Diff (`@git-diff`",
        ):
            assert expected_hdr in special_mentions_res["contextMarkdown"], (
                f"Expected {expected_hdr} in resolved @mention contextMarkdown: {special_mentions_res['contextMarkdown']}"
            )

        # Verify QML QoL wirings and dead-code cleanup across Companion QML surfaces
        comp_src = Path(os.environ.get("ALIS_COMPANION_SOURCE", str(ROOT)))
        mind_qml = (ROOT / "services/AquaMind.qml").read_text(encoding="utf-8")
        cloud_qml = (ROOT / "modules/abyss/companion/AquaTalkCloud.qml").read_text(encoding="utf-8")
        sidebar_qml = (ROOT / "modules/abyss/companion/AquaChatSidebar.qml").read_text(encoding="utf-8")
        pocket_qml = (ROOT / "modules/abyss/companion/AquaPocketPane.qml").read_text(encoding="utf-8")
        cfg_path = comp_src / "modules/settings/CompanionConfig.qml"
        if not cfg_path.is_file():
            cfg_path = ROOT / "optional/alis-companion/modules/settings/CompanionConfig.qml"
        cfg_qml = cfg_path.read_text(encoding="utf-8")
        chat_view_qml = (ROOT / "modules/abyss/companion/AquaCompanionChatView.qml").read_text(encoding="utf-8")

        for required_token in (
            "conversationPinned",
            "toggleConversationPinned",
            "stopVoiceTts",
            "rollbackFileReceipt",
            "fileRollbackBusy",
            "attachPrimarySelection",
            "pendingContextText",
            "savePocketItem",
            "exportFinetuneDataset",
            "searchedThreadMatches",
            'payload?.type === "stream_delta"',
            "agentGlobalStop",
        ):
            assert required_token in mind_qml, f"Missing {required_token} in AquaMind.qml"
        for removed_dead_fn in (
            "function transcribeVoiceInput(",
            "function toggleVoiceRecording(",
            "function pickAndAttachImage(",
            "function clearAttachedImages(",
            "function clearThread(",
            "function inspectToolMetadata(",
            "function captureActiveAppContext(",
            "function proposePreferenceEdit(",
            "function confirmToolProposal(",
            "function cancelToolProposal(",
            "function poseClarification(",
        ):
            assert removed_dead_fn not in mind_qml, f"Dead function still present in AquaMind.qml: {removed_dead_fn}"

        for required_cloud_token in (
            "aquaPinChatButton",
            "aquaStopTtsButton",
            "aquaScrollToBottomButton",
            "aquaBubbleCopyCode-",
            "aquaBubbleOpenCodeEditor-",
            "aquaBubbleDiffUndoPill-",
            "aquaChatBubbleEdit-",
            "aquaChatBubbleRegen-",
            "aquaChatBubbleTts-",
            "aquaChatBubblePocket-",
            "pendingJumpThreadId",
            "mentionCatalog",
            "userPromptHistory",
            "function scrollByPage(",
        ):
            assert required_cloud_token in cloud_qml, f"Missing {required_cloud_token} in AquaTalkCloud.qml"
        assert "function choiceLabel(" not in cloud_qml and "actorVisualRight" not in cloud_qml
        assert "signal threadTurnSelected(" in sidebar_qml and "aquaThreadSnippet-" in sidebar_qml
        assert "aquaPocketSendToChat-" in pocket_qml
        assert "companionExportFinetuneButton" not in cfg_qml and "companionExportFinetunePathNotice" not in cfg_qml
        assert "companionQualityControl" not in cfg_qml and "aquaContextAccessGrid" not in cfg_qml
        assert "function scrollByPage(" in chat_view_qml

        # Verify standalone Companion /swarm (Board / Members / Graph) + Popup Auto-Expansion
        import aqua.swarm_store as swarm_store
        swarm_pane_qml = (ROOT / "modules/abyss/companion/AquaSwarmPane.qml").read_text(encoding="utf-8")
        for swarm_tok in ("aquaSwarmPane", "aquaSwarmBoardList", "aquaSwarmMembersList", "aquaSwarmGraphCanvas", '"board"', '"members"', '"graph"', "prefillChatRequested"):
            assert swarm_tok in swarm_pane_qml, f"Missing {swarm_tok} in AquaSwarmPane.qml"
        for forbidden_swarm_input in ("StyledTextInput", "aquaSwarmBoardInput", "aquaSwarmMemberBriefInput"):
            assert forbidden_swarm_input not in swarm_pane_qml, (
                f"Swarm Right Sidebar must share Chatbox input and not render {forbidden_swarm_input}"
            )
        for cloud_swarm_tok in ("swarmOpen", "expandedSwarmWidth", "expandedSwarmHeight", "AquaSwarmPane", "aquaSwarmSidebarToggle", "aquaSwarmRightSidebar"):
            assert cloud_swarm_tok in cloud_qml, f"Missing {cloud_swarm_tok} in AquaTalkCloud.qml"
        assert "aquaUnifiedSwarmTab" not in cloud_qml, "Swarm should not be a separate top mode tab"
        assert "aquaNewChatButton" not in cloud_qml, "Top-right New Chat button should be replaced by Swarm Right Sidebar toggle"
        old_xdg_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(fake_home / "swarm-test-state")
        try:
            s_state = swarm_store.start_swarm_mission(
                "Audit shell memory and verify edgebar layout",
                [
                    {"role": "researcher", "task": "Inspect memory footprint"},
                    {"role": "verifier", "task": "Validate layout invariants"},
                ],
                emit=False,
            )
            assert s_state["swarmId"] and len(s_state["members"]) == 3
            s_state = swarm_store.record_worker_completion(
                s_state,
                "researcher-1",
                {"summary": "Memory check complete.", "toolCount": 2, "elapsedMs": 120},
                emit=False,
            )
            assert len(s_state["board"]) >= 3 and len(s_state["graph"]["nodes"]) == 3
        finally:
            if old_xdg_state is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = old_xdg_state

        # ------------------------------------------------------------------
        # 39. Web Search Relevance Filtering, Evening Party Plan + Clarification,
        #     Surgical Scope & Clean-Code Reversion, Screen Vision (No OCR),
        #     and Bubble Action Button Non-Overlap
        # ------------------------------------------------------------------
        party_prompt = "lên kế hoạch cho 1 buổi tiệc lúc 8 giờ tối vào ngày mai (hãy sử dụng internet để tổng hợp thông tin cho tôi)"
        cleaned_party_q = web_tools.clean_search_query(party_prompt, for_plan=True)
        assert "sử dụng internet" not in cleaned_party_q.lower() and "tổng hợp thông tin" not in cleaned_party_q.lower(), (
            f"Meta-instruction was not stripped from search query: {cleaned_party_q}"
        )
        extracted_party_q = agent_runtime._extract_plan_search_query(party_prompt, "vi")
        assert "buổi tiệc" in extracted_party_q and "8 giờ" not in extracted_party_q and "sử dụng internet" not in extracted_party_q.lower(), (
            f"Expected clean party search query, got: {extracted_party_q!r}"
        )

        # Verify Unicode URL normalization never leaves raw non-ASCII bytes that break urllib
        encoded_vn_url = web_tools.normalize_url("https://pasgo.vn/blog/kế-hoạch-tổ-chức-tiệc-tối?q=tiệc vui")
        assert all(ord(ch) < 128 for ch in encoded_vn_url), f"Non-ASCII characters remained in normalized URL: {encoded_vn_url}"

        # Verify search relevance filter drops studocu/scribd homework dumps and diacritic mismatches ('Hối tiếc' vs 'tiệc')
        raw_noisy_results = [
            {
                "title": "CNTT1118 - Buổi 01 - Internet và Thương mại điện tử",
                "url": "https://www.studocu.vn/vn/document/dai-hoc-kinh-te-quoc-dan/cntt/12345",
                "snippet": "Bài tập buổi 1 sử dụng internet để tổng hợp thông tin",
            },
            {
                "title": "Nói lời Hối tiếc bằng tiếng Anh - IELTS Speaking",
                "url": "https://ieltsgeneral.info/hoi-tiec-tieng-anh",
                "snippet": "Cách diễn đạt sự hối tiếc trong buổi nói chuyện",
            },
            {
                "title": "Kinh nghiệm lập kế hoạch tổ chức buổi tiệc tối ấm cúng tại nhà",
                "url": "https://pasgo.vn/blog/to-chuc-buoi-tiec-toi",
                "snippet": "Hướng dẫn chi tiết lên thực đơn, chuẩn bị âm nhạc và kịch bản cho buổi tiệc tối",
            },
        ]
        filtered_party_results = web_tools._filter_and_rank_search_results(raw_noisy_results, "buổi tiệc", limit=5)
        assert len(filtered_party_results) == 1 and "pasgo.vn" in filtered_party_results[0]["url"], (
            f"Expected studocu & diacritic-mismatch ('Hối tiếc') to be filtered out, got: {filtered_party_results}"
        )

        # Verify evening party plan generates 20:00 evening timeline + attaches follow-up clarification options + custom input
        party_plan_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/plan {party_prompt}",
                "character": "aqua",
            },
            env,
        )
        assert party_plan_turn["handled"] is True and party_plan_turn["intent"] == "code_task"
        assert "20:00" in party_plan_turn["text"] and "08:30 – Buổi sáng" not in party_plan_turn["text"], (
            f"Evening party plan should use 20:00 evening schedule instead of morning picnic: {party_plan_turn['text']}"
        )
        assert isinstance(party_plan_turn.get("clarification"), dict), f"Missing proactive clarification on party plan: {party_plan_turn}"
        assert party_plan_turn["clarification"].get("allowCustom") is True
        assert 1 <= len(party_plan_turn["clarification"].get("options") or []) <= 3
        assert party_plan_turn["clarification"]["options"][0].get("recommended") is True
        assert party_plan_turn["clarification"]["originalPrompt"].lower().startswith("lên kế hoạch cho"), (
            f"plan_clarification originalPrompt must preserve planning prefix: {party_plan_turn['clarification']}"
        )

        # Replying '1' or clicking an option chip on plan_clarification routes back to code_task (plan) without duplicating goal
        party_plan_followup = run_helper(
            {
                "action": "agent_turn",
                "prompt": "1",
                "character": "aqua",
            },
            env,
        )
        assert party_plan_followup["handled"] is True and party_plan_followup["intent"] == "code_task", (
            f"Follow-up '1' on party plan should route back to code_task plan: {party_plan_followup}"
        )
        assert "20:00" in party_plan_followup["text"] and "1 buổi tiệc lúc 8 giờ tối vào ngày mai — 1 buổi tiệc" not in party_plan_followup["text"], (
            f"Follow-up party plan should not duplicate goal text: {party_plan_followup['text']}"
        )

        # Verify Vietnamese core nouns ('sách', 'du lịch', 'thời tiết') are not stripped by _SEARCH_STOPWORDS
        vn_book_results = [
            {
                "title": "Top 10 cuốn sách hay nên đọc năm nay",
                "url": "https://fahasa.com/blog/top-10-cuon-sach-hay",
                "snippet": "Danh sách những cuốn sách kinh điển đáng đọc nhất",
            },
            {
                "title": "Trang chủ cửa hàng trực tuyến",
                "url": "https://example.com/home",
                "snippet": "Mua sắm trực tuyến giá rẻ",
            },
        ]
        filtered_books = web_tools._filter_and_rank_search_results(vn_book_results, "sách hay", limit=5)
        assert len(filtered_books) == 1 and "fahasa.com" in filtered_books[0]["url"], (
            f"'sách' must not be stripped as a stopword: {filtered_books}"
        )

        # Verify natural-language internet synthesis prompt routes to web_task and general web search mentioning 'github' uses web_search
        nl_internet_cls = agent_runtime.classify_intent("gợi ý thực đơn tối nay (hãy sử dụng internet để tổng hợp thông tin cho tôi)", {})
        assert nl_internet_cls["kind"] == "web_task" and nl_internet_cls["subkind"] == "search", (
            f"Expected web_task search for natural-language internet synthesis prompt, got: {nl_internet_cls}"
        )

        # Verify surgical-scope cross-component impact clarification (both explicit prompt & sibling-file symbol analysis)
        cross_impact_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"chỉnh sửa file {note_file} nhưng thay đổi này ảnh hưởng các thành phần khác",
                "character": "aqua",
            },
            env,
        )
        assert cross_impact_turn["status"] == "awaiting_clarification"
        assert isinstance(cross_impact_turn.get("clarification"), dict)
        assert cross_impact_turn["clarification"].get("allowCustom") is True
        assert len(cross_impact_turn["clarification"].get("options") or []) == 3

        # Sibling-file symbol reference detection without typing 'ảnh hưởng' in prompt
        sib_dir = fake_home / "surgical_scope_pkg"
        sib_dir.mkdir(parents=True, exist_ok=True)
        sib_utils = sib_dir / "utils.py"
        sib_app = sib_dir / "app.py"
        sib_utils.write_text("def compute_party_budget(guests):\n    return guests * 250000\n", encoding="utf-8")
        sib_app.write_text("from utils import compute_party_budget\nprint(compute_party_budget(10))\n", encoding="utf-8")
        sib_impact_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/agent edit {sib_utils}: def renamed_budget_helper(guests):\n    return guests * 300000",
                "character": "aqua",
            },
            env,
        )
        assert sib_impact_turn["status"] == "awaiting_clarification", (
            f"Breaking a function referenced by sibling app.py must pause for surgical-scope clarification: {sib_impact_turn}"
        )
        assert "app.py" in sib_impact_turn["text"] and "compute_party_budget" in sib_impact_turn["text"]
        assert sib_utils.read_text(encoding="utf-8").startswith("def compute_party_budget"), (
            "Target file must not be mutated before user resolves cross-component clarification"
        )

        # Clear pending clarification and verify no-op edit on existing file is automatically reverted/cleaned up
        noop_target = fake_home / "noop_clean_test.txt"
        noop_target.write_text("clean line 1\n", encoding="utf-8")
        noop_edit_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/agent edit {noop_target}: clean line 1",
                "character": "aqua",
            },
            env,
        )
        assert noop_edit_turn["handled"] is True and noop_edit_turn["status"] == "completed"
        assert noop_edit_turn.get("addedLines") == 0 and noop_edit_turn.get("removedLines") == 0
        assert noop_edit_turn.get("receiptId") == "", f"No-op edit receipt should be rolled back and cleared: {noop_edit_turn}"
        assert "0 dòng thay đổi" in noop_edit_turn["text"] or "no effective diff" in noop_edit_turn["text"]

        # Verify syntax-breaking edit on a valid Python file is automatically reverted/cleaned up
        syntax_target = fake_home / "valid_module.py"
        valid_py_src = "def greet_user(name):\n    return f'Hello {name}'\n"
        syntax_target.write_text(valid_py_src, encoding="utf-8")
        broken_edit_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/agent edit {syntax_target}: def greet_user(name\n    return",
                "character": "aqua",
            },
            env,
        )
        assert broken_edit_turn["handled"] is True and broken_edit_turn["status"] == "completed"
        assert broken_edit_turn.get("receiptId") == "", f"Syntax-breaking edit receipt must be rolled back: {broken_edit_turn}"
        assert syntax_target.read_text(encoding="utf-8") == valid_py_src, (
            "Syntax-breaking edit was not rolled back to original valid Python source"
        )
        assert "lỗi cú pháp" in broken_edit_turn["text"] or "syntax error" in broken_edit_turn["text"]

        # Verify legacy 4-line agent_rules.json migrates to 5-line DEFAULT_RULES_TEXT while preserving custom edits
        import rule_store
        rules_file = Path(env["XDG_STATE_HOME"]) / "alis-companion" / "agent_rules.json"
        legacy_4line = next(iter(rule_store.LEGACY_DEFAULT_RULES_TEXTS))
        rules_file.write_text(
            json.dumps({"schemaVersion": 1, "rulesText": legacy_4line, "updatedAtMs": 1700000000000}),
            encoding="utf-8",
        )
        migrated_status = run_helper({"action": "agent_rules", "op": "list"}, env)
        assert "Surgical Scope & Clean Code" in migrated_status["rulesText"], (
            f"Legacy 4-line default rulesText did not migrate to include Surgical Scope & Clean Code: {migrated_status}"
        )
        custom_rules_str = "- Custom user rule: always reply in haiku."
        rules_file.write_text(
            json.dumps({"schemaVersion": 1, "rulesText": custom_rules_str, "updatedAtMs": 1700000000000}),
            encoding="utf-8",
        )
        preserved_status = run_helper({"action": "agent_rules", "op": "list"}, env)
        assert preserved_status["rulesText"] == custom_rules_str, (
            f"Custom user rulesText must never be overwritten by migration: {preserved_status}"
        )
        run_helper({"action": "agent_rules", "op": "reset_rules_text"}, env)

        # Verify curated MCP server auto-resolution and argument normalization in mcp_client.call_tool
        import mcp_client
        mcp_norm_res = mcp_client.call_tool(
            "web-search-mcp",
            "read_file_excerpt",
            {"query": str(noop_target)},
            companion_root=ROOT,
            enabled_overrides={"web-search-mcp": True, "desktop-commander": True},
        )
        assert mcp_norm_res["isError"] is False and mcp_norm_res["server"] == "desktop-commander", (
            f"Expected curated server auto-resolution and query->path normalization, got: {mcp_norm_res}"
        )

        # Verify screen_observe ('what can you see on my screen') captures screenshot for Vision and removes live Tesseract OCR
        import screen_context
        assert not hasattr(screen_context, "_capture_ocr_lines"), "Live Tesseract OCR helper _capture_ocr_lines must be removed"
        vision_screen_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "what can you see on my screen",
                "character": "aqua",
            },
            ss_env,
        )
        assert vision_screen_turn["handled"] is True and vision_screen_turn["intent"] == "screen_observe"
        assert isinstance(vision_screen_turn.get("images"), list) and len(vision_screen_turn["images"]) == 1
        assert Path(vision_screen_turn["images"][0]).is_file()
        assert "niri+vision" in vision_screen_turn["text"]

        # Verify bubbleCopyBtn and bubbleHoverActionsRow non-overlap layout in AquaTalkCloud.qml
        assert "readonly property bool fitsActionClusterOutside" in cloud_qml
        assert "readonly property real actionFooterHeight: fitsActionClusterOutside ? 0 : 22" in cloud_qml
        assert "bubbleHoverActionsRow.x - width - 4" in cloud_qml

        # Verify natural-language $HOME file search ("find the file named 01_Core.css for me" & VI equivalent)
        obsidian_snippets = fake_home / "Shared_Documents" / "Obsidian-Vault" / ".obsidian" / "snippets"
        obsidian_snippets.mkdir(parents=True, exist_ok=True)
        core_css = obsidian_snippets / "01_Core.css"
        core_css.write_text(":root {\n  --cc-p00: #0a0e10;\n  --cc-p01: #0c1113;\n}\n", encoding="utf-8")

        find_en_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "find the file named 01_Core.css for me",
                "character": "aqua",
            },
            env,
        )
        assert find_en_turn["handled"] is True and find_en_turn["intent"] == "file_task"
        assert find_en_turn["status"] == "completed"
        assert str(core_css.resolve()) in find_en_turn["text"], f"Expected {core_css} in reply: {find_en_turn['text']}"
        assert "--cc-p00: #0a0e10" in find_en_turn["text"]
        assert any("Searched for 01_Core.css" in s for s in find_en_turn.get("activitySteps", [])), (
            f"Expected 'Searched for 01_Core.css' step in {find_en_turn.get('activitySteps')}"
        )

        find_vi_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "tìm file 01_Core.css giúp tôi",
                "character": "aqua",
            },
            env,
        )
        assert find_vi_turn["handled"] is True and find_vi_turn["intent"] == "file_task"
        assert find_vi_turn["status"] == "completed"
        assert str(core_css.resolve()) in find_vi_turn["text"]

        # Verify Vietnamese '<filename> ở đâu?' and 'thư mục <name> nằm ở đâu' and quoted filenames with spaces
        find_vi_where_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "01_Core.css ở đâu?",
                "character": "aqua",
            },
            env,
        )
        assert find_vi_where_turn["handled"] is True and find_vi_where_turn["intent"] == "file_task"
        assert str(core_css.resolve()) in find_vi_where_turn["text"]

        find_vi_folder_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "thư mục snippets nằm ở đâu",
                "character": "aqua",
            },
            env,
        )
        assert find_vi_folder_turn["handled"] is True and find_vi_folder_turn["intent"] == "file_task"
        assert str(obsidian_snippets.resolve()) in find_vi_folder_turn["text"]

        spaced_note = fake_home / "Documents" / "My Notes 2026.md"
        spaced_note.parent.mkdir(parents=True, exist_ok=True)
        spaced_note.write_text("# My 2026 Notes\nSpaced filename test.\n", encoding="utf-8")
        find_spaced_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "find the file named 'My Notes 2026.md' for me",
                "character": "aqua",
            },
            env,
        )
        assert find_spaced_turn["handled"] is True and find_spaced_turn["intent"] == "file_task"
        assert str(spaced_note.resolve()) in find_spaced_turn["text"]
        assert "Spaced filename test." in find_spaced_turn["text"]

        # Verify symlinked project folder inside Obsidian-Vault is traversed and preferred by search_files_in_home
        real_repo_dir = fake_home / "Shared_Documents" / "Repos" / "SampleRepo"
        real_repo_dir.mkdir(parents=True, exist_ok=True)
        symlink_target_file = real_repo_dir / "SymlinkedWidget.qml"
        symlink_target_file.write_text("import QtQuick\nItem {}\n", encoding="utf-8")
        vault_projects_dir = fake_home / "Shared_Documents" / "Obsidian-Vault" / "10_Projects"
        vault_projects_dir.mkdir(parents=True, exist_ok=True)
        vault_symlink = vault_projects_dir / "SampleRepo"
        if not vault_symlink.exists():
            vault_symlink.symlink_to(real_repo_dir)
        find_sym_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "find file SymlinkedWidget.qml",
                "character": "aqua",
            },
            env,
        )
        assert find_sym_turn["handled"] is True and find_sym_turn["intent"] == "file_task"
        assert "SymlinkedWidget.qml" in find_sym_turn["text"] and "import QtQuick" in find_sym_turn["text"]

        # Verify _emit_activity_step never creates duplicate 'Thought for Xs ›' entries
        agent_runtime._reset_activity_steps()
        agent_runtime._emit_activity_step("Thought for 12s ›", kind="step", status="completed")
        agent_runtime._emit_activity_step("Thought for 38s ›", kind="llm_think", status="completed")
        thought_labels = [s["label"] for s in agent_runtime._ACTIVE_RUN_STEPS if s["label"].startswith("Thought for ")]
        assert thought_labels == ["Thought for 38s ›"], f"Expected single deduplicated Thought step, got {thought_labels}"

        find_missing_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "find the file named nonexistent_xyz_999.css for me",
                "character": "aqua",
            },
            env,
        )
        assert find_missing_turn["handled"] is True and find_missing_turn["intent"] == "file_task"
        assert find_missing_turn["status"] == "completed"
        assert "nonexistent_xyz_999.css" in find_missing_turn["text"]

        # Verify legacy broken '**🖥 Alis' stub migration in history_store.py
        db_conn = sqlite3.connect(env["ALIS_AQUA_HISTORY_DB"])
        cur_u = db_conn.execute(
            "INSERT INTO messages(role, content, created_ms, model) VALUES('user', 'find the file named 01_Core.css for me', 1775445330106, 'lmstudio:qwen/qwen3.6-35b-a3b')"
        )
        u_row_id = cur_u.lastrowid
        cur_a = db_conn.execute(
            "INSERT INTO messages(role, content, created_ms, model) VALUES('assistant', '**🖥 Alis', 1775445330107, 'lmstudio:qwen/qwen3.6-35b-a3b')"
        )
        a_row_id = cur_a.lastrowid
        db_conn.execute("INSERT OR REPLACE INTO thread_messages(message_id, thread_id) VALUES(?, 'chat_default')", (u_row_id,))
        db_conn.execute("INSERT OR REPLACE INTO thread_messages(message_id, thread_id) VALUES(?, 'chat_default')", (a_row_id,))
        db_conn.commit()
        db_conn.close()

        loaded_after_repair = run_helper({"action": "history", "threadId": "chat_default", "limit": 20}, env)
        repaired_rows = [r for r in loaded_after_repair["messages"] if r["id"] == a_row_id]
        assert len(repaired_rows) == 1, f"Expected repaired row id={a_row_id} in history"
        assert repaired_rows[0]["content"] != "**🖥 Alis", f"Broken stub was not repaired: {repaired_rows[0]}"
        assert "01_Core.css" in repaired_rows[0]["content"] and str(core_css.resolve()) in repaired_rows[0]["content"], (
            f"Expected grounded path in repaired content: {repaired_rows[0]['content']}"
        )

        # Verify separator-flexible folder search ("find the folder named TTSP_BIEUMAU" -> "TTSP.Bieumau.TTBDNVSP.2026")
        ttsp_dir = fake_home / "Shared_Documents" / "Obsidian-Vault" / "40_Academics" / "Templates" / "TTSP.Bieumau.TTBDNVSP.2026"
        ttsp_dir.mkdir(parents=True, exist_ok=True)
        (ttsp_dir / "guide.txt").write_text("TTSP template guide\n", encoding="utf-8")
        find_ttsp_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "find the folder named TTSP_BIEUMAU",
                "character": "aqua",
            },
            env,
        )
        assert find_ttsp_turn["handled"] is True and find_ttsp_turn["intent"] == "file_task"
        assert "TTSP.Bieumau.TTBDNVSP.2026" in find_ttsp_turn["text"], (
            f"Expected separator-flexible folder match in reply: {find_ttsp_turn['text']}"
        )

        # Verify history_store sep_repair_max_id watermark repairs historical missed separator-flexible rows once
        db_conn2 = sqlite3.connect(env["ALIS_AQUA_HISTORY_DB"])
        cur_u2 = db_conn2.execute(
            "INSERT INTO messages(role, content, created_ms, model) VALUES('user', 'find the folder named TTSP_BIEUMAU', 1775445330200, 'lmstudio:qwen/qwen3.6-35b-a3b')"
        )
        u2_id = cur_u2.lastrowid
        cur_a2 = db_conn2.execute(
            "INSERT INTO messages(role, content, created_ms, model) VALUES('assistant', ?, 1775445330201, 'lmstudio:qwen/qwen3.6-35b-a3b')",
            (f"Could not find any file or folder matching `TTSP_BIEUMAU` in `{fake_home}`.",),
        )
        a2_id = cur_a2.lastrowid
        db_conn2.execute("INSERT OR REPLACE INTO thread_messages(message_id, thread_id) VALUES(?, 'chat_default')", (u2_id,))
        db_conn2.execute("INSERT OR REPLACE INTO thread_messages(message_id, thread_id) VALUES(?, 'chat_default')", (a2_id,))
        db_conn2.commit()
        db_conn2.close()

        loaded_sep_repair = run_helper({"action": "history", "threadId": "chat_default", "limit": 30}, env)
        sep_rows = [r for r in loaded_sep_repair["messages"] if r["id"] == a2_id]
        assert len(sep_rows) == 1 and "TTSP.Bieumau.TTBDNVSP.2026" in sep_rows[0]["content"], (
            f"Expected historical TTSP_BIEUMAU row to be repaired on history load: {sep_rows}"
        )
        db_conn3 = sqlite3.connect(env["ALIS_AQUA_HISTORY_DB"])
        wm_val = db_conn3.execute("SELECT value FROM state_kv WHERE key='sep_repair_max_id'").fetchone()
        db_conn3.close()
        assert wm_val and int(wm_val[0]) >= a2_id, f"Expected sep_repair_max_id watermark >= {a2_id}, got {wm_val}"

        # Verify follow-up screenshot query ("what you can see in the screen shot") reuses previous screenshot without capturing a new one
        for shot_prompt in (
            "what you can see in the screen shot",
            "describe the screenshot in detail",
            "bạn thấy gì trong ảnh chụp",
        ):
            followup_shot_intent = agent_runtime.classify_intent(shot_prompt, {})
            assert followup_shot_intent["kind"] == "screen_observe", f"Failed classify_intent for {shot_prompt}: {followup_shot_intent}"
            role_res = agent_runtime.classify_prompt_model_role(shot_prompt, dual_opts)
            assert role_res["role"] == "action", f"Failed classify_prompt_model_role for {shot_prompt}: {role_res}"
        no_new_cap = screen_context.observe_active_window(allow_screenshot=True, capture_image=False)
        assert no_new_cap["screenshotPath"] == "", (
            f"observe_active_window(allow_screenshot=True, capture_image=False) must not capture a new screenshot: {no_new_cap}"
        )

        # Verify _ensure_lmstudio_runtime_ready and local_mind._ensure_lmstudio_model_ready recreate .lmstudio/.internal/temp, guard GTT/OOM, and pin loaded model IDs
        import local_mind
        fake_lms_dir = fake_home / ".lmstudio"
        fake_lms_dir.mkdir(parents=True, exist_ok=True)
        old_home_env = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            assert agent_runtime._ensure_lmstudio_runtime_ready() is True
            assert (fake_lms_dir / ".internal" / "temp").is_dir()
            shutil.rmtree(fake_lms_dir / ".internal" / "temp")
            assert local_mind._ensure_lmstudio_model_ready("qwen3.6-35b-a3b-mtp") is True
            assert (fake_lms_dir / ".internal" / "temp").is_dir()
            mem_mb, gtt_mb = agent_runtime._get_system_and_gpu_memory_mb()
            assert isinstance(mem_mb, int) and mem_mb > 0
            assert isinstance(gtt_mb, int) and gtt_mb >= 0
            fake_log_dir = fake_lms_dir / "server-logs" / "2026-11"
            fake_log_dir.mkdir(parents=True, exist_ok=True)
            fake_log_file = fake_log_dir / "2026-11-07.1.log"
            fake_log_file.write_text(
                "[DEBUG] llama_server: initializing ...\n"
                "[DEBUG] W common_fit_params: failed to fit params to free device memory: n_gpu_layers already set by user to 33, abort\n",
                encoding="utf-8",
            )
            assert agent_runtime._check_lmstudio_log_recent_fit_or_oom_error() == "gpu_fit_failed"
            assert local_mind._check_lmstudio_log_recent_fit_or_oom_error() == "gpu_fit_failed"
            fake_log_file.write_text(
                "[DEBUG] llama_server: initializing ...\n"
                "[DEBUG] W common_fit_params: failed to fit params to free device memory\n"
                "[DEBUG] I srv  llama_server: model loaded\n",
                encoding="utf-8",
            )
            assert agent_runtime._check_lmstudio_log_recent_fit_or_oom_error() == ""
            assert local_mind._check_lmstudio_log_recent_fit_or_oom_error() == ""
            # Timestamped log lines (and un-timestamped partial prefix lines at the start of a 24KB slice) older than since_ts must be ignored
            fake_log_file.write_text(
                "partial line at start of 24KB slice: DeviceLostError\n"
                "[2026-11-08 12:00:00][DEBUG] W common_fit_params: failed to fit params to free device memory: n_gpu_layers already set by user to 33, abort\n"
                "[2026-11-08 13:05:00][INFO] [LM STUDIO SERVER] Running HTTP server on port 1234\n",
                encoding="utf-8",
            )
            future_since = time.mktime(time.strptime("2026-11-08 13:00:00", "%Y-%m-%d %H:%M:%S"))
            assert agent_runtime._check_lmstudio_log_recent_fit_or_oom_error(future_since) == ""
            assert local_mind._check_lmstudio_log_recent_fit_or_oom_error(future_since) == ""
            past_since = time.mktime(time.strptime("2026-11-08 11:00:00", "%Y-%m-%d %H:%M:%S"))
            assert agent_runtime._check_lmstudio_log_recent_fit_or_oom_error(past_since) == "gpu_fit_failed"
            assert local_mind._check_lmstudio_log_recent_fit_or_oom_error(past_since) == "gpu_fit_failed"

            # Verify requesting an unloaded model ID when another model is 'loaded' pins _LAST_LMSTUDIO_LOADED_MODEL to the loaded model
            class _MockLmsV0Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    pass

                def do_GET(self):
                    payload = {
                        "data": [
                            {"id": "unloaded-fast-4b", "type": "llm", "state": "not-loaded"},
                            {"id": "qwen/qwen3.6-35b-a3b:2", "type": "vlm", "state": "loaded"},
                        ]
                    }
                    raw = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

            lms_v0_srv = http.server.HTTPServer(("127.0.0.1", 0), _MockLmsV0Handler)
            lms_v0_thread = threading.Thread(target=lms_v0_srv.serve_forever, daemon=True)
            lms_v0_thread.start()
            mock_v0_url = f"http://127.0.0.1:{lms_v0_srv.server_port}/api/v0/models"
            try:
                agent_runtime._LAST_LMSTUDIO_ERROR = ""
                assert agent_runtime._ensure_lmstudio_runtime_ready(
                    "http://127.0.0.1:1234/v1/chat/completions",
                    "lmstudio-unloaded-fast-4b",
                    v0_url=mock_v0_url,
                ) is True
                assert agent_runtime._LAST_LMSTUDIO_LOADED_MODEL == "qwen/qwen3.6-35b-a3b:2", (
                    f"Expected loaded model ID pinned, got {agent_runtime._LAST_LMSTUDIO_LOADED_MODEL!r}"
                )
                assert local_mind._ensure_lmstudio_model_ready(
                    "lmstudio-unloaded-fast-4b",
                    v0_url=mock_v0_url,
                ) is True
                assert local_mind._LAST_LMSTUDIO_LOADED_MODEL == "qwen/qwen3.6-35b-a3b:2", (
                    f"Expected local_mind loaded model ID pinned, got {local_mind._LAST_LMSTUDIO_LOADED_MODEL!r}"
                )
                agent_runtime._LAST_LMSTUDIO_LOADED_MODEL = ""
                local_mind._LAST_LMSTUDIO_LOADED_MODEL = ""
            finally:
                lms_v0_srv.shutdown()
                lms_v0_srv.server_close()
        finally:
            if old_home_env is not None:
                os.environ["HOME"] = old_home_env
        ar_src = (ROOT / "scripts/aqua/agent_runtime.py").read_text(encoding="utf-8")
        lm_src = (ROOT / "scripts/aqua/local_mind.py").read_text(encoding="utf-8")
        assert "/api/v1/models/load" not in ar_src
        assert "/api/v1/models/load" not in lm_src

        # Verify AquaMind.qml duplicate 'Thought for' prevention, screenshot follow-up routing parity, and AquaTalkCloud.qml auto-scroll
        mind_qml_latest = (ROOT / "services/AquaMind.qml").read_text(encoding="utf-8")
        cloud_qml_latest = (ROOT / "modules/abyss/companion/AquaTalkCloud.qml").read_text(encoding="utf-8")
        assert "property int workThinkingDeciseconds: 0" in mind_qml_latest
        assert "!st.startsWith(\"Thought for \")" in mind_qml_latest
        assert "if (busy || draining || agentTurnWorker.running || agentFileWorker.running)" in mind_qml_latest
        assert "find the file" in mind_qml_latest
        assert "screen\\s*shot" in mind_qml_latest
        assert "what you can see" in mind_qml_latest
        assert "trong anh chup" in mind_qml_latest
        assert "function scrollToLatestMessage(): void" in cloud_qml_latest
        assert "function _snapTranscriptToBottom(): void" in cloud_qml_latest
        assert "id: scrollToBottomSettleTimer" in cloud_qml_latest

        # Verify screenshot auto-delete retention (7 / 14 / 30 / 90 days) and age-based pruning
        shots_dir = screen_context._screenshots_dir()
        for existing_png in shots_dir.glob("screenshot-*.png"):
            existing_png.unlink(missing_ok=True)
        now_sec = time.time()
        now_ms_test = int(now_sec * 1000)
        shot_5d = shots_dir / "screenshot-1001.png"
        shot_10d = shots_dir / "screenshot-1002.png"
        shot_20d = shots_dir / "screenshot-1003.png"
        shot_45d = shots_dir / "screenshot-1004.png"
        shot_100d = shots_dir / "screenshot-1005.png"
        for f, age_days in (
            (shot_5d, 5),
            (shot_10d, 10),
            (shot_20d, 20),
            (shot_45d, 45),
            (shot_100d, 100),
        ):
            f.write_bytes(screen_context._FALLBACK_1X1_PNG)
            mtime = now_sec - (age_days * 86400)
            os.utime(f, (mtime, mtime))

        ret_90 = run_helper({"action": "screenshot_retention", "retentionDays": 90, "nowMs": now_ms_test}, env)
        assert ret_90["retentionDays"] == 90 and ret_90["prunedCount"] == 1 and ret_90["remainingCount"] == 4
        assert not shot_100d.exists() and shot_45d.exists()

        ret_30 = run_helper({"action": "screenshot_retention", "retentionDays": 30, "nowMs": now_ms_test}, env)
        assert ret_30["retentionDays"] == 30 and ret_30["prunedCount"] == 1 and ret_30["remainingCount"] == 3
        assert not shot_45d.exists() and shot_20d.exists()

        ret_14 = run_helper({"action": "screenshot_retention", "retentionDays": 14, "nowMs": now_ms_test}, env)
        assert ret_14["retentionDays"] == 14 and ret_14["prunedCount"] == 1 and ret_14["remainingCount"] == 2
        assert not shot_20d.exists() and shot_10d.exists()

        ret_7 = run_helper({"action": "screenshot_retention", "retentionDays": 7, "nowMs": now_ms_test}, env)
        assert ret_7["retentionDays"] == 7 and ret_7["prunedCount"] == 1 and ret_7["remainingCount"] == 1
        assert not shot_10d.exists() and shot_5d.exists()

        st_ret = run_helper({"action": "agent_status", "screenshotRetentionDays": 30, "nowMs": now_ms_test}, env)
        assert st_ret["screenshotRetentionDays"] == 30 and st_ret["screenshotRetention"]["retentionDays"] == 30

        # Verify explicit user temperature override in classify_prompt_model_role
        mtp_route_override = agent_runtime.classify_prompt_model_role(
            "analyze system architecture",
            {
                **dual_opts,
                "thinkingModel": "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP",
                "mtpOptimizeEnabled": True,
                "temperature": 1.25,
                "hasTemperatureOverride": True,
            },
        )
        assert mtp_route_override["mtpActive"] is True and mtp_route_override["temperature"] == 1.25, mtp_route_override

        # 39. 3-Stage Domain RAG System (Niri WM, Quickshell QML, Arch Linux)
        import domain_rag
        import http.server
        import threading

        # Stage 1: Seed canonical docs & verify manifest + HTTP conditional ETag/304/200 sync
        os.environ["XDG_STATE_HOME"] = env["XDG_STATE_HOME"]
        os.environ["XDG_CONFIG_HOME"] = env["XDG_CONFIG_HOME"]
        os.environ["HOME"] = env["HOME"]
        seed_res = domain_rag.sync_canonical_docs(allow_network=False)
        assert seed_res["totalDocuments"] >= 9, f"Expected >= 9 canonical docs seeded, got {seed_res}"
        manifest_s1 = domain_rag.load_canonical_manifest()
        for dom_expect in ("niri", "quickshell", "archlinux"):
            assert any(v["domain"] == dom_expect for v in manifest_s1["documents"].values()), f"Missing domain {dom_expect}"

        canon_srv_state = {"etag": '"etag-v1"', "body": "# Niri Keybindings Upstream\n\nMod+Shift+E quits Niri cleanly.\n", "hits_304": 0}

        class _CanonDocHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                inm = self.headers.get("If-None-Match", "")
                if inm == canon_srv_state["etag"]:
                    canon_srv_state["hits_304"] += 1
                    self.send_response(304)
                    self.end_headers()
                    return
                data = canon_srv_state["body"].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("ETag", canon_srv_state["etag"])
                self.send_header("Last-Modified", "Wed, 06 Nov 2026 07:00:00 GMT")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):  # noqa: A002
                pass

        canon_httpd = http.server.HTTPServer(("127.0.0.1", 0), _CanonDocHandler)
        canon_port = canon_httpd.server_port
        canon_thread = threading.Thread(target=canon_httpd.serve_forever, daemon=True)
        canon_thread.start()
        custom_urls = {"niri/configuration-overview": f"http://127.0.0.1:{canon_port}/Configuration.md"}
        try:
            sync_1 = domain_rag.sync_canonical_docs(domains=["niri"], force=False, allow_network=False, custom_sync_urls=custom_urls)
            assert sync_1["updatedCount"] >= 1, sync_1
            # Second sync with unchanged ETag must hit 304 Not Modified and increment notModifiedCount
            sync_2 = domain_rag.sync_canonical_docs(domains=["niri"], force=False, allow_network=False, custom_sync_urls=custom_urls)
            assert canon_srv_state["hits_304"] >= 1 and sync_2["notModifiedCount"] >= 1, sync_2
            # Update upstream ETag and content -> must fetch 200 and update sha256
            canon_srv_state["etag"] = '"etag-v2"'
            canon_srv_state["body"] = "# Niri Keybindings Upstream v2\n\nMod+Ctrl+Space toggles floating window focus.\n"
            sync_3 = domain_rag.sync_canonical_docs(domains=["niri"], force=False, allow_network=False, custom_sync_urls=custom_urls)
            assert sync_3["updatedCount"] >= 1, sync_3
        finally:
            canon_httpd.shutdown()
            canon_httpd.server_close()

        # Stage 2: Personal context indexing + recursive include + secret scrubbing + incremental cache
        niri_cfg_dir = Path(env["XDG_CONFIG_HOME"]) / "niri"
        niri_cfg_dir.mkdir(parents=True, exist_ok=True)
        (niri_cfg_dir / "config.kdl").write_text(
            'input {\n    keyboard {\n        xkb {\n            layout "us"\n        }\n    }\n}\ninclude "binds.kdl"\n',
            encoding="utf-8",
        )
        (niri_cfg_dir / "binds.kdl").write_text(
            'binds {\n    Mod+Shift+Return { spawn "ghostty"; }\n    // secret OPENAI_API_KEY="sk-1234567890abcdef1234567890abcdef"\n}\n',
            encoding="utf-8",
        )
        alis_cfg_dir = Path(env["XDG_CONFIG_HOME"]) / "alis"
        alis_cfg_dir.mkdir(parents=True, exist_ok=True)
        (alis_cfg_dir / "config.json").write_text(
            json.dumps({"ai": {"temperature": 0.4, "apiKey": "ghp_1234567890abcdef1234567890abcdef1234"}}),
            encoding="utf-8",
        )
        systemd_user_dir = Path(env["XDG_CONFIG_HOME"]) / "systemd" / "user"
        systemd_user_dir.mkdir(parents=True, exist_ok=True)
        (systemd_user_dir / "alis.service").write_text(
            "[Unit]\nDescription=Alis Shell\n[Service]\nExecStart=/usr/bin/qs -c alis\n",
            encoding="utf-8",
        )

        pers_1 = domain_rag.sync_personal_context(force=False)
        assert pers_1["changed"] is True and pers_1["watchedFileCount"] >= 4, (
            f"Expected >= 4 personal files indexed (including binds.kdl), got {pers_1}"
        )
        assert any("binds.kdl" in p for p in pers_1["niriConfigFiles"]), pers_1
        # Verify secrets were scrubbed from stored personal markdown
        for md_file in (Path(env["XDG_STATE_HOME"]) / "alis-companion" / "domain_rag" / "personal").rglob("*.md"):
            md_txt = md_file.read_text(encoding="utf-8")
            assert "sk-1234567890abcdef1234567890abcdef" not in md_txt, f"Raw OpenAI key leaked in {md_file}"
            assert "ghp_1234567890abcdef1234567890abcdef1234" not in md_txt, f"Raw GitHub token leaked in {md_file}"
        # Second sync without file changes must detect changed == False via mtime_ns + sha256 cache
        pers_2 = domain_rag.sync_personal_context(force=False)
        assert pers_2["changed"] is False and pers_2["watchedFileCount"] >= 4, f"Expected incremental cache unchanged, got {pers_2}"

        # Stage 3: Domain Skills, KDL validation, and automatic agent_turn domainRag context injection
        assert domain_rag.validate_niri_kdl_snippet('window-rule {\n    open-floating true\n}')["valid"] is True
        assert domain_rag.validate_niri_kdl_snippet('window-rule {\n    open-floating true\n')["valid"] is False
        assert domain_rag.validate_niri_kdl_snippet('windowrulev2 = float, class:^(kitty)$')["valid"] is False

        sk_list = run_helper({"action": "skill_action", "op": "list"}, env)["skills"]
        sk_names = {s["name"] for s in sk_list}
        assert {"niri-wm", "quickshell-qml", "arch-linux"}.issubset(sk_names), f"Missing domain skills in {sk_names}"

        sk_niri_run = run_helper(
            {"action": "skill_action", "op": "run", "name": "niri-wm", "prompt": "Mod+Shift+Return ghostty window-rule"},
            env,
        )["result"]
        assert "ghostty" in sk_niri_run["output"] and "Stage 1" in sk_niri_run["output"], sk_niri_run

        rag_turn_auto = run_helper(
            {
                "action": "agent_turn",
                "prompt": "What terminal is bound to Mod+Shift+Return in my Niri config.kdl?",
                "character": "aqua",
            },
            env,
        )
        assert "domainRag" in rag_turn_auto and "niri" in rag_turn_auto["domainRag"]["domains"], rag_turn_auto
        assert "ghostty" in rag_turn_auto.get("attachedContextMarkdown", ""), rag_turn_auto
        assert "Core Domain Identity" in rag_turn_auto.get("attachedContextMarkdown", ""), rag_turn_auto
        assert "NEVER Lua-based" in rag_turn_auto.get("attachedContextMarkdown", ""), rag_turn_auto

        # Verify Auto-Route badges (Domain RAG, Deep Thinking, Fast Chat, UI Action) & effort scaling
        r_cap = agent_runtime.classify_task_type(
            "do you have knowledge of Niri, Arch Linux and Quickshell now",
            {"thinkingEffort": "max", "model": "qwen3.5-9b-plusiq"},
        )
        assert r_cap["role"] == "thinking" and r_cap["roleLabel"] == "Domain RAG" and r_cap["category"] == "domain_rag", r_cap
        assert r_cap["recommendedEffort"] == "off" and r_cap["enableThinking"] is False, r_cap

        r_lookup = agent_runtime.classify_prompt_model_role(
            "How do I configure a floating window-rule in Niri config.kdl?",
            {"thinkingEffort": "max", "model": "qwen3.5-9b-plusiq"},
        )
        assert r_lookup["role"] == "thinking" and r_lookup["roleLabel"] == "Domain RAG" and r_lookup["category"] == "domain_rag", r_lookup
        assert r_lookup["recommendedEffort"] == "low" and r_lookup["enableThinking"] is False, r_lookup

        r_deep = agent_runtime.classify_prompt_model_role(
            "Explain why the lock-free ring buffer algorithm deadlocks step by step",
            {"thinkingEffort": "high", "model": "qwen3.5-9b-plusiq"},
        )
        assert r_deep["role"] == "thinking" and r_deep["roleLabel"] == "Deep Thinking" and r_deep["enableThinking"] is True, r_deep

        r_chat = agent_runtime.classify_prompt_model_role("hey there, how is your day going?")
        assert r_chat["role"] == "action" and r_chat["roleLabel"] == "Fast Chat" and r_chat["category"] == "fast_chat", r_chat

        r_ui = agent_runtime.classify_prompt_model_role("click the Save button on the current window")
        assert r_ui["role"] == "action" and r_ui["roleLabel"] == "UI Action" and r_ui["category"] == "desktop_ui_action", r_ui

        # Verify capability query & follow-up option click ("— Explain a concept or command")
        cap_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "do you have knowledge of Niri, Arch Linux and Quickshell now",
                "character": "aqua",
            },
            env,
        )
        assert cap_turn["handled"] is True and cap_turn["intent"] == "rag_query", cap_turn
        assert cap_turn["modelRoute"]["roleLabel"] == "Domain RAG", cap_turn
        assert "3-Stage Domain RAG" in cap_turn["text"] and "Qt6/QML" in cap_turn["text"], cap_turn
        assert not cap_turn["text"].rstrip().endswith("2."), cap_turn["text"]

        concept_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "do you have knowledge of Niri, Arch Linux and Quickshell now — Explain a concept or command",
                "character": "aqua",
            },
            env,
        )
        assert concept_turn["handled"] is True and concept_turn["intent"] == "rag_query", concept_turn
        assert "infinite horizontal strip of columns" in concept_turn["text"], concept_turn["text"]
        assert "never Lua-based" in concept_turn["text"] and "pacman -Syu" in concept_turn["text"], concept_turn["text"]

        # Verify "can you check my configuration" inspects live Stage 2 configs and sanitizes stale 01_Core.css in run_store SQLite session_context
        stale_ctx = run_store.load_session_context()
        stale_ctx["lastFile"] = "/home/llocphann/Shared_Documents/Obsidian-Vault/.obsidian/snippets/01_Core.css"
        stale_ctx["lastDirectory"] = "/home/llocphann/Shared_Documents/Obsidian-Vault/.obsidian/snippets"
        stale_ctx["recentCandidates"] = ["/home/llocphann/Shared_Documents/Obsidian-Vault/.obsidian/snippets/01_Core.css"]
        run_store.save_session_context(stale_ctx)

        cfg_check_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "can you check my configuration",
                "character": "aqua",
            },
            env,
        )
        assert cfg_check_turn["handled"] is True and cfg_check_turn["intent"] == "rag_query", cfg_check_turn
        assert cfg_check_turn["modelRoute"]["roleLabel"] == "Domain RAG", cfg_check_turn
        assert "01_Core.css" not in cfg_check_turn["text"], cfg_check_turn["text"]
        assert "config.kdl" in cfg_check_turn["text"] and "binds.kdl" in cfg_check_turn["text"], cfg_check_turn["text"]
        assert "1. Check Niri config (~/.config/niri/config.kdl) (Recommended)" in cfg_check_turn["text"], cfg_check_turn["text"]
        assert "2. Check Quickshell / Alis config" in cfg_check_turn["text"], cfg_check_turn["text"]
        assert "3. Check Arch Linux & systemd user services" in cfg_check_turn["text"], cfg_check_turn["text"]
        assert "01_Core.css" not in str(cfg_check_turn.get("sessionContext", {}).get("lastFile") or ""), cfg_check_turn
        assert ".obsidian" not in str(cfg_check_turn.get("sessionContext", {}).get("lastDirectory") or ""), cfg_check_turn
        assert not any("01_Core.css" in str(c) for c in (cfg_check_turn.get("sessionContext", {}).get("recentCandidates") or [])), cfg_check_turn

        # Verify clicking follow-up config options routes to single-domain config inspection
        cfg_niri_opt = run_helper(
            {
                "action": "agent_turn",
                "prompt": "can you check my configuration — Check Niri config (~/.config/niri/config.kdl)",
                "character": "aqua",
            },
            env,
        )
        assert cfg_niri_opt["handled"] is True and cfg_niri_opt["intent"] == "rag_query", cfg_niri_opt
        assert "Niri WM Configuration" in cfg_niri_opt["text"] and "Quickshell / Alis Configuration" not in cfg_niri_opt["text"], cfg_niri_opt["text"]

        cfg_qs_opt = run_helper(
            {
                "action": "agent_turn",
                "prompt": "do you have knowledge of Niri, Arch Linux and Quickshell now — Inspect Quickshell / Alis config",
                "character": "aqua",
            },
            env,
        )
        assert cfg_qs_opt["handled"] is True and cfg_qs_opt["intent"] == "rag_query", cfg_qs_opt
        assert "Quickshell / Alis Configuration" in cfg_qs_opt["text"] and "Niri WM Configuration" not in cfg_qs_opt["text"], cfg_qs_opt["text"]

        # 39. Agent-Desktop AT-SPI2 Semantic Snapshot (@e1/@e2), Headless Action/Editable Execution & Companion Virtual Cursor
        import contextlib
        import io
        import screen_context as sc_mod

        ad_fixture = {
            "windowId": 99,
            "title": "Settings — Alis Desktop",
            "appId": "org.alis.Settings",
            "lines": ["Appearance", "Filter settings"],
            "elements": [
                {
                    "id": 1,
                    "role": "push button",
                    "name": "New",
                    "bbox": {"x": 100, "y": 50, "w": 60, "h": 28},
                    "atspiPath": [0, 1, 2],
                    "states": ["sensitive"],
                    "headlessCapable": True,
                },
                {
                    "id": 2,
                    "role": "entry",
                    "name": "Filter chats...",
                    "bbox": {"x": 200, "y": 50, "w": 180, "h": 28},
                    "atspiPath": [0, 1, 3],
                    "states": ["editable", "focusable"],
                    "value": "",
                    "actions": ["click", "fill", "focus"],
                    "headlessCapable": True,
                },
            ],
        }
        prev_fix = os.environ.get("ALIS_AQUA_SCREEN_FIXTURE")
        os.environ["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps(ad_fixture)
        try:
            obs_ad = sc_mod.observe_active_window(allow_screenshot=False)
            assert "semanticSnapshot" in obs_ad and "semanticSnapshot" in obs_ad["observation"], obs_ad
            snap_txt = obs_ad["semanticSnapshot"]
            assert "Window: Settings — Alis Desktop (org.alis.Settings)" in snap_txt, snap_txt
            assert '@e1 [push button] "New"' in snap_txt, snap_txt
            assert '@e2 [entry] "Filter chats..." [editable]' in snap_txt, snap_txt
            assert obs_ad["elements"][0]["ref"] == "@e1" and obs_ad["elements"][0]["atspiPath"] == [0, 1, 2]
            assert obs_ad["elements"][1]["ref"] == "@e2" and "editable" in obs_ad["elements"][1]["states"]

            buf_out = io.StringIO()
            with contextlib.redirect_stdout(buf_out):
                act_click = sc_mod.perform_gui_action("atspi_action", ref="@e1")
                act_click_default_xy = sc_mod.perform_gui_action("click", ref="@e1", x=0, y=0)
                act_fill = sc_mod.perform_gui_action("fill", ref="@e2", text="dark theme")
                act_focus = sc_mod.perform_gui_action("focus_element", ref="@e2")
            streamed_lines = [ln for ln in buf_out.getvalue().splitlines() if ln.startswith("__AQUA_CURSOR__:")]
            assert len(streamed_lines) == 4, streamed_lines
            assert act_click["backend"] == "atspi-headless-action" and act_click["ref"] == "@e1", act_click
            assert act_click["cursorEvent"]["x"] == 130 and act_click["cursorEvent"]["y"] == 64, act_click
            assert act_click["cursorEvent"]["ref"] == "@e1" and act_click["cursorEvent"]["label"] == "New", act_click
            assert act_click_default_xy["x"] == 130 and act_click_default_xy["y"] == 64 and act_click_default_xy["ref"] == "@e1", act_click_default_xy
            assert act_fill["backend"] == "atspi-headless-editable" and act_fill["ref"] == "@e2", act_fill
            assert act_fill["value"] == "dark theme" and act_fill["cursorEvent"]["actionType"] == "fill", act_fill
            assert act_focus["backend"] == "atspi-headless-focus" and act_focus["cursorEvent"]["actionType"] == "focus_element", act_focus

            # Verify _execute_llm_tool_call for desktop_control (action="click" & action_type="fill") and screen_observe semantic-first
            tool_ctx: dict = {}
            dc_click_out = agent_runtime._execute_llm_tool_call(
                {"name": "desktop_control", "arguments": {"action": "click", "ref": "@e1", "x": 0, "y": 0}},
                tool_ctx,
                ROOT,
                1700000900000,
            )
            assert "130, 64" in dc_click_out and tool_ctx.get("_lastCursorEvent", {}).get("ref") == "@e1", (dc_click_out, tool_ctx)
            dc_fill_out = agent_runtime._execute_llm_tool_call(
                {"name": "desktop_control", "arguments": {"action_type": "fill", "ref": "@e2", "text": "via action_type"}},
                tool_ctx,
                ROOT,
                1700000901000,
            )
            assert "@e2" in dc_fill_out and tool_ctx.get("_lastCursorEvent", {}).get("actionType") == "fill", (dc_fill_out, tool_ctx)
            so_out = agent_runtime._execute_llm_tool_call(
                {"name": "screen_observe", "arguments": {"allowScreenshot": True}},
                tool_ctx,
                ROOT,
                1700000902000,
            )
            assert '@e1 [push button] "New"' in so_out and '@e2 [entry] "Filter chats..."' in so_out, so_out
        finally:
            if prev_fix is None:
                os.environ.pop("ALIS_AQUA_SCREEN_FIXTURE", None)
            else:
                os.environ["ALIS_AQUA_SCREEN_FIXTURE"] = prev_fix

        ad_env = dict(env)
        ad_env["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps(ad_fixture)
        ref_click_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "click @e1",
                "character": "aqua",
            },
            ad_env,
        )
        assert ref_click_turn["handled"] is True, ref_click_turn
        assert ref_click_turn.get("ref") == "@e1", ref_click_turn
        assert isinstance(ref_click_turn.get("cursorEvent"), dict), ref_click_turn
        assert ref_click_turn["cursorEvent"]["x"] == 130 and ref_click_turn["cursorEvent"]["y"] == 64, ref_click_turn

        ref_atspi_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "atspi_action @e1",
                "character": "aqua",
            },
            ad_env,
        )
        assert ref_atspi_turn["handled"] is True and ref_atspi_turn.get("ref") == "@e1", ref_atspi_turn
        assert ref_atspi_turn.get("cursorEvent", {}).get("backend") == "atspi-headless-action", ref_atspi_turn

        ref_fill_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": "fill @e2 'hello world'",
                "character": "aqua",
            },
            ad_env,
        )
        assert ref_fill_turn["handled"] is True, ref_fill_turn
        assert ref_fill_turn.get("ref") == "@e2", ref_fill_turn
        assert isinstance(ref_fill_turn.get("cursorEvent"), dict) and ref_fill_turn["cursorEvent"]["actionType"] == "fill", ref_fill_turn

        # 47. Ponytail (/ponytail lite|full|ultra), DOCX, PDF, and XLSX skills & document tools
        import code_tools
        import skill_store

        all_skill_names = {s["name"] for s in skill_store.list_skills(ROOT)}
        assert {"ponytail", "docx", "pdf", "xlsx"}.issubset(all_skill_names), all_skill_names

        pt_default = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/ponytail refactor config loader",
                "character": "aqua",
            },
            env,
        )
        assert pt_default["handled"] is True and pt_default["intent"] == "skill_manage", pt_default
        assert pt_default.get("skill") == "ponytail" and pt_default.get("ponytailMode") == "full", pt_default
        assert "The best code is the code you never wrote" in pt_default["text"], pt_default
        assert "Ponytail Receipt (`full`)" in pt_default["text"], pt_default

        pt_lite = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/ponytail lite add helper",
                "character": "aqua",
            },
            env,
        )
        assert pt_lite["handled"] is True and pt_lite.get("ponytailMode") == "lite", pt_lite
        assert "Ponytail Receipt (`lite`)" in pt_lite["text"], pt_lite

        pt_ultra = run_helper(
            {
                "action": "agent_turn",
                "prompt": "/ponytail ultra build microservice factory",
                "character": "aqua",
            },
            env,
        )
        assert pt_ultra["handled"] is True and pt_ultra.get("ponytailMode") == "ultra", pt_ultra
        assert "Ponytail Receipt (`ultra`)" in pt_ultra["text"], pt_ultra
        assert pt_ultra.get("sessionContext", {}).get("ponytailMode") == "ultra", pt_ultra

        # Create a .docx with headings + table via convert_or_create_document and inspect via agent_turn & /docx
        bieu_mau_docx = docs_dir / "TTSP_BIEUMAU.docx"
        code_tools.convert_or_create_document(
            "# Biểu Mẫu Thực Tập\n\nHọ và tên sinh viên: Nguyễn Văn A\n\n| Tiêu chí | Điểm tối đa | Kết quả |\n| --- | --- | --- |\n| Chuyên cần | 20 | 19 |\n| Báo cáo | 80 | 76 |\n",
            bieu_mau_docx,
            title="Biểu Mẫu Thực Tập",
        )
        docx_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"đọc file {bieu_mau_docx}",
                "character": "aqua",
            },
            env,
        )
        assert docx_turn["handled"] is True and docx_turn["intent"] == "file_task", docx_turn
        assert "Biểu Mẫu Thực Tập" in docx_turn["text"], docx_turn
        assert "| Tiêu chí | Điểm tối đa | Kết quả |" in docx_turn["text"], docx_turn
        assert "| Chuyên cần | 20 | 19 |" in docx_turn["text"], docx_turn

        slash_docx = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/docx {bieu_mau_docx}",
                "character": "aqua",
            },
            env,
        )
        assert slash_docx["handled"] is True and slash_docx.get("skill") == "docx", slash_docx
        assert "TTSP_BIEUMAU.docx" in slash_docx["text"] and "| Chuyên cần | 20 | 19 |" in slash_docx["text"], slash_docx

        slash_pdf = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/pdf {pdf_file}",
                "character": "aqua",
            },
            env,
        )
        assert slash_pdf["handled"] is True and slash_pdf.get("skill") == "pdf", slash_pdf
        assert "Offline Voice Piper Whisper Manual" in slash_pdf["text"], slash_pdf

        # Create an .xlsx with formulas via convert_or_create_document and inspect via agent_turn & /xlsx
        bang_diem_xlsx = docs_dir / "bang_diem.xlsx"
        code_tools.convert_or_create_document(
            "### Sheet: BangDiem\n| Sinh viên | Giữa kỳ | Cuối kỳ | Tổng điểm |\n| --- | --- | --- | --- |\n| Nguyễn Văn A | 40 | 55 | =SUM(B2:C2) |\n| Trần Thị B | 38 | 52 | =SUM(B3:C3) |\n",
            bang_diem_xlsx,
            title="Bảng Điểm Thực Tập",
        )
        xlsx_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"đọc file {bang_diem_xlsx}",
                "character": "aqua",
            },
            env,
        )
        assert xlsx_turn["handled"] is True and xlsx_turn["intent"] == "file_task", xlsx_turn
        assert "### Sheet: BangDiem" in xlsx_turn["text"], xlsx_turn
        assert "95 (=SUM(B2:C2))" in xlsx_turn["text"], xlsx_turn
        assert "0 formula error(s)" in xlsx_turn["text"], xlsx_turn

        slash_xlsx = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"/xlsx {bang_diem_xlsx}",
                "character": "aqua",
            },
            env,
        )
        assert slash_xlsx["handled"] is True and slash_xlsx.get("skill") == "xlsx", slash_xlsx
        assert "bang_diem.xlsx" in slash_xlsx["text"] and "95 (=SUM(B2:C2))" in slash_xlsx["text"], slash_xlsx

        # Natural-language conversion from CSV -> XLSX (preserving source file and building valid OOXML .xlsx)
        conv_xlsx_target = docs_dir / "quarterly_metrics_converted.xlsx"
        conv_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": f"chuyển đổi file {csv_file} sang {conv_xlsx_target}",
                "character": "aqua",
            },
            env,
        )
        assert conv_turn["handled"] is True and conv_turn["intent"] == "file_task", conv_turn
        assert csv_file.is_file(), "Source CSV must not be deleted during conversion"
        assert conv_xlsx_target.is_file(), "Target XLSX must be created"
        conv_readback = code_tools.read_file(conv_xlsx_target)
        assert conv_readback["ok"] is True and conv_readback["format"] == "xlsx", conv_readback
        assert "168000" in conv_readback["text"], conv_readback

        # 48. Single-Model Autonomous Subagent Orchestration, 5 Efficiency-Gain Criteria & Strict English Status Strings
        import agent_runtime
        import tool_store

        # 48a. Small/normal or simple sequential single-target prompts do NOT unnecessarily spawn subagents
        for normal_p in (
            "Xin chào Aqua",
            "tăng âm lượng lên 60%",
            f"đọc file {csv_file}",
            "mở file README.md rồi tóm tắt ngắn gọn",
            "kiểm tra file config.kdl của niri xem có lỗi không",
            "/skills list",
            "Đừng xoá hay phân tích rồi tạo báo cáo nhé",
        ):
            scale_norm = agent_runtime.assess_task_scale_for_subagents(normal_p)
            assert scale_norm["shouldDecompose"] is False and scale_norm["efficiencyJustified"] is False, (
                f"Normal/simple sequential prompt should NOT decompose into subagents: {normal_p} -> {scale_norm}"
            )
            assert scale_norm["criteria"] == [] and scale_norm["subagentCriteria"] == [], scale_norm

        # 48b. Prompts matching the 5 Efficiency-Gain Criteria decompose into sequential specialist subagents with English status labels
        large_vi_prompt = (
            "kiểm tra cấu hình niri và quickshell hiện tại trên toàn bộ repo, sau đó phân tích kiến trúc rồi tạo báo cáo tổng hợp và kiểm chứng chéo kết quả"
        )
        scale_large = agent_runtime.assess_task_scale_for_subagents(large_vi_prompt)
        assert scale_large["shouldDecompose"] is True and scale_large["efficiencyJustified"] is True, scale_large
        assert scale_large["scale"] == "large", scale_large
        for expected_crit in ("cross_verification", "parallel_discovery", "context_isolation", "cognitive_separation"):
            assert expected_crit in scale_large["criteria"], (expected_crit, scale_large)
        assert len(scale_large["matchedReasons"]) >= 3, scale_large
        assert len(scale_large["subtasks"]) >= 3, scale_large
        for st_item in scale_large["subtasks"]:
            lbl = st_item["statusLabel"]
            assert lbl.startswith("🤖 Subagent [") and "Đang" not in lbl, (
                f"Subagent status label must be strictly in English, got: {lbl}"
            )

        # Verify privilege_isolation criterion specifically (untrusted external URL/doc + local config/file action)
        scale_priv = agent_runtime.assess_task_scale_for_subagents(
            "đọc tài liệu ngoài từ https://example.com/guide rồi sửa cấu hình local và kiểm chứng chéo"
        )
        assert scale_priv["shouldDecompose"] is True and scale_priv["efficiencyJustified"] is True, scale_priv
        assert "privilege_isolation" in scale_priv["criteria"], scale_priv
        assert "cross_verification" in scale_priv["criteria"], scale_priv

        # 48c. delegate_subagent runs in an isolated context window on the single pinned model, uses parallel discovery, enforces privilege isolation, caps summary <= 1200 chars, and uses English status
        agent_runtime._LAST_LMSTUDIO_LOADED_MODEL = "qwen3.5-9b-uncensored-hauhaucs-aggressive"
        sub_res_1 = tool_store.delegate_subagent(
            role="researcher",
            task="Inspect Niri & Quickshell configuration docs and summarize key bindings",
            context_hint="Focus on Mod+Return and window rules",
            subagent_index=1,
            subagent_total=2,
            requested_tools=["edit_file", "shell_run"],
        )
        assert sub_res_1["ok"] is True and sub_res_1["role"] == "researcher", sub_res_1
        assert sub_res_1["isolatedContext"] is True and sub_res_1["singleModelPinned"] is True, sub_res_1
        assert sub_res_1["privilegeIsolated"] is True and sub_res_1["readOnlyRole"] is True, sub_res_1
        assert sub_res_1["parallelDiscoveryUsed"] is True, sub_res_1
        assert "edit_file" in sub_res_1["blockedTools"] and "shell_run" in sub_res_1["blockedTools"], sub_res_1
        assert sub_res_1["model"] == "qwen3.5-9b-uncensored-hauhaucs-aggressive", sub_res_1
        assert sub_res_1["enableThinking"] is False and sub_res_1["temperature"] == 0.2, sub_res_1
        assert sub_res_1["messagesCount"] == 2, sub_res_1
        assert 0 < len(sub_res_1["summary"]) <= 1200, len(sub_res_1["summary"])
        assert "🤖 Subagent [Researcher 1/2]: Gathering context..." in sub_res_1["statusLabel"], sub_res_1

        sub_res_ver = tool_store.delegate_subagent(
            role="verifier",
            task="Independently cross-verify generated report and configuration syntax",
            subagent_index=2,
            subagent_total=2,
        )
        assert sub_res_ver["ok"] is True and sub_res_ver["role"] == "verifier", sub_res_ver
        assert "🤖 Subagent [Verifier 2/2]: Cross-verifying output..." in sub_res_ver["statusLabel"], sub_res_ver

        # Verify Vietnamese accidental status input is sanitized to English
        sanitized_prog = agent_runtime._emit_subagent_progress(
            "researcher", 1, 2, "Đang thu thập dữ liệu...", status="running"
        )
        assert sanitized_prog["summary"] == "Gathering context..." and "Đang" not in sanitized_prog["current"], sanitized_prog

        # 48d. End-to-end agent_turn with large multi-phase prompt populates subagentRuns & subagentCount
        multi_turn = run_helper(
            {
                "action": "agent_turn",
                "prompt": large_vi_prompt,
                "autoSubagentDecompose": True,
                "character": "aqua",
            },
            env,
        )
        assert multi_turn.get("subagentCount", 0) >= 3 and len(multi_turn.get("subagentRuns", [])) >= 3, multi_turn
        assert multi_turn.get("taskScale", {}).get("efficiencyJustified") is True, multi_turn.get("taskScale")
        assert all(
            r.get("isolatedContext") is True
            and r.get("singleModelPinned") is True
            and r.get("privilegeIsolated") is True
            and len(r.get("summary", "")) <= 1200
            and str(r.get("statusLabel", "")).startswith("🤖 Subagent [")
            for r in multi_turn["subagentRuns"]
        ), multi_turn["subagentRuns"]

        # 48e. Regression: all subagent role tools execute in _execute_llm_tool_call (never returning '[unknown tool: ...]') and voice_tts accepts engine & wait
        import desktop_control

        sub_ctx = {
            "lastDirectory": str(docs_dir),
            "lastProject": str(docs_dir),
            "_activeTurnOptions": {
                "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
                "model": "qwen3.5-9b-uncensored-hauhaucs-aggressive",
                "useSubagentLlm": False,
            },
        }
        sub_tool_test_file = docs_dir / "subagent_tool_target.txt"
        sub_tool_test_doc = docs_dir / "subagent_doc.md"
        sub_tool_test_html = docs_dir / "subagent_doc.html"
        for tc_check in (
            {"name": "write_file", "args": {"path": str(sub_tool_test_file), "content": "alpha\nbeta\n"}},
            {"name": "read_file", "args": {"path": str(sub_tool_test_file)}},
            {"name": "edit_file", "args": {"path": str(sub_tool_test_file), "content": "alpha\nbeta\ngamma\n"}},
            {"name": "code_search", "args": {"workspace": str(docs_dir), "query": "gamma"}},
            {"name": "domain_rag", "args": {"query": "niri window rules"}},
            {"name": "system_status", "args": {}},
            {"name": "inspect_user_configuration", "args": {"query": "check niri config"}},
            {"name": "create_document", "args": {"path": str(sub_tool_test_doc), "content": "# Subagent Doc\n\n Verified content."}},
            {"name": "convert_document", "args": {"sourcePath": str(sub_tool_test_doc), "destPath": str(sub_tool_test_html)}},
            {"name": "run_command", "args": {"command": "echo subagent_cmd_ok", "cwd": str(docs_dir)}},
            {"name": "delegate_subagent", "args": {"role": "verifier", "task": "Verify sub_tool_test_file content"}},
        ):
            tc_out = agent_runtime._execute_llm_tool_call(tc_check, sub_ctx, ROOT, 1700009999000)
            assert "[unknown tool:" not in tc_out and len(tc_out.strip()) > 0, (tc_check["name"], tc_out)
        assert agent_runtime._ACTIVE_SUBAGENT_RUNS[-1]["endpoint"] == "http://127.0.0.1:1234/v1/chat/completions", (
            agent_runtime._ACTIVE_SUBAGENT_RUNS[-1]
        )

        tts_vi_edge = desktop_control.voice_tts(
            "Xin chào, đây là phản hồi bằng giọng nói tiếng Việt tự nhiên.",
            language="vi",
            engine="edge-tts",
            wait=False,
        )
        assert tts_vi_edge["ok"] is True and tts_vi_edge["neuralVoice"] == "vi-VN-HoaiMyNeural", tts_vi_edge
        assert tts_vi_edge["requestedEngine"] == "edge-tts" and tts_vi_edge["waited"] is False, tts_vi_edge

        tts_en_edge = desktop_control.voice_tts(
            "Hello, this is a natural English neural voice response.",
            language="en",
            engine="edge-tts",
            wait=True,
        )
        assert tts_en_edge["ok"] is True and tts_en_edge["neuralVoice"] == "en-US-AvaNeural", tts_en_edge
        assert tts_en_edge["requestedEngine"] == "edge-tts" and tts_en_edge["waited"] is True, tts_en_edge

    print("ALIS_COMPANION_AGENT_F11_PASS naturalLanguageLoop permissions bwrapSandbox screenContext codeToolsLsp ragCitations mcpProtocol skillsSystem hermesSelfLearning slashCommands rules memoryPocket crashReconcile legacyDataParity chatThreadsProjects fullAuthorityDesktopAgent twoTierFileEditor storageAndSkillsSettings dualModelAutoRouting plusIqModeSwitching mtpAutoOptimization interactiveClarification screenshotImageReply reactMultiStepLoop niriWindowColumnLayout browserDomSnapshot somVisualGrounding multiFormatFts5HybridRag fileAttachmentsAndOfflineVoice graphifyMcpAndSkill antiHallucinationAndRulesSync deepWebSearchAndPlanSynthesis qmlQolAndDeadCodeCleanup homeFileSearchAndAutoScroll screenshotRetentionAndTemperatureOverride threeStageDomainRag agentDesktopSemanticRefsAndVirtualCursor ponytailDocxPdfXlsxSkills singleModelSubagentOrchestration")


if __name__ == "__main__":
    main()


