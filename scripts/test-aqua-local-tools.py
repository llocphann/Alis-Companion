#!/usr/bin/env python3
"""Verify typed read-only metadata tools, conditional RAG/routing, and confirmed edits."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(os.environ.get("ALIS_COMPANION_SOURCE", Path(__file__).resolve().parents[1]))
HOST = Path(os.environ.get("ALIS_ROOT", ROOT))


def call_mind(env, payload):
    proc = subprocess.run(
        ["python3", str(ROOT / "scripts/aqua/local_mind.py"), "--host-root", str(HOST)],
        input=(json.dumps(payload) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=True,
        timeout=8,
    )
    raw = proc.stdout.decode("utf-8").strip()
    return raw, json.loads(raw)


def verify_guard(action, raw):
    node_code = """
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const pub = vm.createContext({});
vm.runInContext(fs.readFileSync("services/AquaReplyGuard.js", "utf8").replace(/^\\.pragma library\\s*/, ""), pub);
const ctx = vm.createContext({ PublicReply: pub, Number, Array, Object, JSON, Math });
const src = fs.readFileSync("services/AquaHelperReplyGuard.js", "utf8")
    .replace(/^\\.pragma library\\s*/, "")
    .replace(/^\\.import\\s+.*$/m, "");
vm.runInContext(src, ctx);
const res = ctx.parse(process.argv[1], process.argv[2], 0);
assert.equal(res.ok, true, "Guard rejected valid helper output for " + process.argv[1]);
"""
    subprocess.run(
        ["node", "-e", node_code, action, raw],
        cwd=str(ROOT),
        check=True,
        timeout=5,
    )


with tempfile.TemporaryDirectory(prefix="companion-tools-") as tmp:
    private = Path(tmp)
    env = os.environ.copy()
    env["XDG_STATE_HOME"] = str(private / "state")
    env["XDG_CONFIG_HOME"] = str(private / "config")
    env["ALIS_NEXUS_VAULT"] = "__DISABLED__"

    # 1. Read-only typed metadata tools
    raw, host_meta = call_mind(env, {"action": "tool_inspect", "kind": "host_metadata"})
    assert host_meta["ok"] is True and host_meta["result"]["kind"] == "host_metadata"
    assert host_meta["result"]["localOnlyDefault"] is True
    verify_guard("tool_inspect", raw)

    raw, repo_meta = call_mind(env, {"action": "tool_inspect", "kind": "repo_metadata", "target": "companion"})
    assert repo_meta["ok"] is True and repo_meta["result"]["packageId"] == "companion"
    verify_guard("tool_inspect", raw)

    raw, doc_meta = call_mind(env, {"action": "tool_inspect", "kind": "doc_metadata", "target": "README.md"})
    assert doc_meta["ok"] is True and doc_meta["result"]["kind"] == "doc_metadata"
    assert len(doc_meta["result"]["headings"]) >= 1
    verify_guard("tool_inspect", raw)

    for bad_path in ("../README.md", "/etc/passwd", "scripts/install.py"):
        _, rejected = call_mind(env, {"action": "tool_inspect", "kind": "doc_metadata", "target": bad_path})
        assert rejected["ok"] is False and rejected["error"]["code"] == "invalid_target"

    # 2. Conditional RAG & Reflex/Brain routing cost/privacy gate
    raw, blocked_route = call_mind(env, {
        "action": "tool_route",
        "prompt": "Explain Companion architecture",
        "localOnly": True,
        "isLocalModel": False,
        "docOptIn": True,
    })
    assert blocked_route["ok"] is True and blocked_route["result"]["route"] == "blocked"
    assert blocked_route["result"]["reason"] == "local_only_required"
    verify_guard("tool_route", raw)

    raw, reflex_route = call_mind(env, {
        "action": "tool_route",
        "prompt": "Hello there!",
        "localOnly": True,
        "isLocalModel": True,
        "docOptIn": True,
    })
    assert reflex_route["ok"] is True and reflex_route["result"]["route"] == "reflex"
    assert reflex_route["result"]["ragUsed"] is False
    verify_guard("tool_route", raw)

    raw, unconsented_rag = call_mind(env, {
        "action": "tool_route",
        "prompt": "How does Companion shader validation and portal travel work?",
        "localOnly": True,
        "isLocalModel": True,
        "docOptIn": False,
    })
    assert unconsented_rag["ok"] is True and unconsented_rag["result"]["ragUsed"] is False
    assert unconsented_rag["result"]["reason"] == "doc_consent_disabled"
    verify_guard("tool_route", raw)

    raw, brain_rag = call_mind(env, {
        "action": "tool_route",
        "prompt": "How does Companion shader validation and package architecture work?",
        "localOnly": True,
        "isLocalModel": True,
        "docOptIn": True,
    })
    assert brain_rag["ok"] is True and brain_rag["result"]["route"] == "brain"
    assert brain_rag["result"]["ragUsed"] is True and 1 <= len(brain_rag["result"]["snippets"]) <= 3
    verify_guard("tool_route", raw)

    # 3. Allowlisted user-confirmed edits, timeout, cancel, idempotence, and rollback
    for bad_edit in (
        {"action": "tool_propose", "field": "shellCommand", "beforeValue": "a", "afterValue": "b"},
        {"action": "tool_propose", "field": "personality", "beforeValue": "calm", "afterValue": "hacked"},
    ):
        _, bad_res = call_mind(env, bad_edit)
        assert bad_res["ok"] is False

    now = 1_700_000_000_000
    raw, prop1 = call_mind(env, {
        "action": "tool_propose",
        "field": "personality",
        "beforeValue": "calm",
        "afterValue": "lively",
        "nowMs": now,
    })
    assert prop1["ok"] is True and prop1["result"]["status"] == "pending"
    verify_guard("tool_propose", raw)
    pid1 = prop1["result"]["proposalId"]

    # Unconfirmed execution must be refused
    _, unconfirmed = call_mind(env, {"action": "tool_confirm", "proposalId": pid1, "confirmed": False, "nowMs": now + 100})
    assert unconfirmed["ok"] is False and unconfirmed["error"]["code"] == "explicit_consent_required"

    # Confirmed within deadline succeeds and produces a receipt
    raw, confirmed = call_mind(env, {"action": "tool_confirm", "proposalId": pid1, "confirmed": True, "nowMs": now + 500})
    assert confirmed["ok"] is True and confirmed["result"]["idempotent"] is False
    assert confirmed["result"]["afterValue"] == "lively" and confirmed["result"]["rolledBack"] is False
    verify_guard("tool_confirm", raw)
    rid1 = confirmed["result"]["receiptId"]

    # Repeated confirmation is idempotent
    raw, idem_confirm = call_mind(env, {"action": "tool_confirm", "proposalId": pid1, "confirmed": True, "nowMs": now + 600})
    assert idem_confirm["ok"] is True and idem_confirm["result"]["idempotent"] is True
    assert idem_confirm["result"]["receiptId"] == rid1
    verify_guard("tool_confirm", raw)

    # Cancellation of a fresh proposal blocks subsequent confirmation
    _, prop2 = call_mind(env, {
        "action": "tool_propose",
        "field": "dialogueTone",
        "beforeValue": "gentle",
        "afterValue": "playful",
        "nowMs": now + 1000,
    })
    pid2 = prop2["result"]["proposalId"]
    raw, cancelled = call_mind(env, {"action": "tool_cancel", "proposalId": pid2})
    assert cancelled["ok"] is True and cancelled["result"]["cancelled"] is True
    verify_guard("tool_cancel", raw)
    _, confirm_cancelled = call_mind(env, {"action": "tool_confirm", "proposalId": pid2, "confirmed": True, "nowMs": now + 1100})
    assert confirm_cancelled["ok"] is False and confirm_cancelled["error"]["code"] == "proposal_cancelled"

    # Timeout expiration blocks confirmation after TTL
    _, prop3 = call_mind(env, {
        "action": "tool_propose",
        "field": "familiarity",
        "beforeValue": "neutral",
        "afterValue": "warm",
        "nowMs": now + 2000,
    })
    pid3 = prop3["result"]["proposalId"]
    _, confirm_expired = call_mind(env, {
        "action": "tool_confirm",
        "proposalId": pid3,
        "confirmed": True,
        "nowMs": now + 2000 + 120_001,
    })
    assert confirm_expired["ok"] is False and confirm_expired["error"]["code"] == "proposal_expired"

    # Rollback requires explicit confirmation and is idempotent
    _, unconfirmed_rb = call_mind(env, {"action": "tool_rollback", "receiptId": rid1, "confirmed": False})
    assert unconfirmed_rb["ok"] is False and unconfirmed_rb["error"]["code"] == "explicit_consent_required"

    raw, rb1 = call_mind(env, {"action": "tool_rollback", "receiptId": rid1, "confirmed": True})
    assert rb1["ok"] is True and rb1["result"]["rolledBack"] is True and rb1["result"]["idempotent"] is False
    assert rb1["result"]["beforeValue"] == "calm"
    verify_guard("tool_rollback", raw)

    raw, rb2 = call_mind(env, {"action": "tool_rollback", "receiptId": rid1, "confirmed": True})
    assert rb2["ok"] is True and rb2["result"]["rolledBack"] is True and rb2["result"]["idempotent"] is True
    verify_guard("tool_rollback", raw)

    raw, receipts = call_mind(env, {"action": "tool_receipts"})
    assert receipts["ok"] is True and len(receipts["result"]["receipts"]) == 1
    assert receipts["result"]["receipts"][0]["rolledBack"] is True
    verify_guard("tool_receipts", raw)

    # 4. Desktop Agent file operations (access, edit, copy, cut, and 2x-confirmed delete)
    agent_env = env.copy()
    agent_env["HOME"] = str(private)
    (private / "Desktop").mkdir(parents=True, exist_ok=True)

    raw, edited = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "edit",
        "targetPath": "agent_note.txt",
        "mode": "write",
        "content": "Hello from Companion Agent\nSecond line",
    })
    assert edited["ok"] is True and edited["result"]["status"] == "completed"
    verify_guard("agent_file_op", raw)
    note_file = private / "Desktop" / "agent_note.txt"
    assert note_file.is_file() and "Hello from Companion Agent" in note_file.read_text(encoding="utf-8")

    raw, accessed = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "access",
        "targetPath": str(note_file),
    })
    assert accessed["ok"] is True and accessed["result"]["status"] == "completed"
    assert any("Hello from Companion Agent" in ln for ln in accessed["result"]["lines"])
    verify_guard("agent_file_op", raw)

    copy_file = private / "Desktop" / "agent_copy.txt"
    raw, copied = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "copy",
        "targetPath": str(note_file),
        "destPath": str(copy_file),
    })
    assert copied["ok"] is True and copy_file.is_file()
    verify_guard("agent_file_op", raw)

    moved_file = private / "Desktop" / "agent_moved.txt"
    raw, moved = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "cut",
        "targetPath": str(copy_file),
        "destPath": str(moved_file),
    })
    assert moved["ok"] is True and not copy_file.exists() and moved_file.is_file()
    verify_guard("agent_file_op", raw)

    # Delete must require 2 confirmations before removing the file
    raw, del_step0 = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "delete",
        "targetPath": str(moved_file),
        "confirmStep": 0,
        "nowMs": now + 3000,
    })
    assert del_step0["ok"] is True and del_step0["result"]["status"] == "confirm_1_required"
    assert del_step0["result"]["confirmStage"] == 1 and moved_file.is_file()
    verify_guard("agent_file_op", raw)

    # Skipping step 1 directly to step 2 must be rejected
    _, skip_step1 = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "delete",
        "targetPath": str(moved_file),
        "confirmStep": 2,
        "nowMs": now + 3100,
    })
    assert skip_step1["ok"] is False and skip_step1["error"]["code"] == "second_confirmation_required"
    assert moved_file.is_file()

    # Step 1 confirmation advances to stage 2 without deleting yet
    raw, del_step1 = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "delete",
        "targetPath": str(moved_file),
        "confirmStep": 1,
        "nowMs": now + 3200,
    })
    assert del_step1["ok"] is True and del_step1["result"]["status"] == "confirm_2_required"
    assert del_step1["result"]["confirmStage"] == 2 and moved_file.is_file()
    verify_guard("agent_file_op", raw)

    # Step 2 (2nd confirmation) deletes the file
    raw, del_step2 = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "delete",
        "targetPath": str(moved_file),
        "confirmStep": 2,
        "nowMs": now + 3300,
    })
    assert del_step2["ok"] is True and del_step2["result"]["status"] == "completed"
    assert not moved_file.exists()
    verify_guard("agent_file_op", raw)

    # 5. Desktop Agent script execution (op == "run")
    script_file = private / "Desktop" / "hello_agent.sh"
    script_file.write_text("#!/usr/bin/env bash\necho 'Full Access Script OK'\n", encoding="utf-8")
    raw, ran_script = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "run",
        "targetPath": str(script_file),
    })
    assert ran_script["ok"] is True and ran_script["result"]["status"] == "completed"
    assert any("Full Access Script OK" in ln for ln in ran_script["result"]["lines"])
    verify_guard("agent_file_op", raw)

    # 6. Backend QoL Upgrades: SSE streamDelta, Diff Preview & Receipt Rollback, Primary Selection, Stoppable TTS, Search Turn Snippets, and Fine-Tune Export
    import contextlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import io
    import sys
    import threading
    from unittest import mock

    if str(ROOT / "scripts/aqua") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts/aqua"))
    os.environ.update(agent_env)
    import agent_runtime
    import desktop_control
    import history_store
    import learning_store
    import tool_store

    # 6a. Real-time SSE token streaming (streamDelta)
    class _SseHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"choices": [{"delta": {"content": "Streaming "}}]},
                {"choices": [{"delta": {"content": "token "}}]},
                {"choices": [{"delta": {"content": "verification!"}}]},
            ]
            for ch in chunks:
                self.wfile.write(f"data: {json.dumps(ch)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    sse_srv = ThreadingHTTPServer(("127.0.0.1", 0), _SseHandler)
    sse_thr = threading.Thread(target=sse_srv.serve_forever, daemon=True)
    sse_thr.start()
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            full_sse = agent_runtime._call_chat_completion_text(
                f"http://127.0.0.1:{sse_srv.server_port}/v1/chat/completions",
                {"model": "test-sse", "messages": [{"role": "user", "content": "hi"}]},
                stream_stdout=True,
            )
        assert full_sse == "Streaming token verification!", f"Unexpected full_sse: {full_sse}"
        streamed_lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
        assert [item["streamDelta"] for item in streamed_lines] == ["Streaming ", "token ", "verification!"]
    finally:
        sse_srv.shutdown()
        sse_srv.server_close()

    class _JsonFallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.dumps({"choices": [{"message": {"content": "Non-SSE fallback reply"}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    json_srv = ThreadingHTTPServer(("127.0.0.1", 0), _JsonFallbackHandler)
    json_thr = threading.Thread(target=json_srv.serve_forever, daemon=True)
    json_thr.start()
    try:
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            fb_txt = agent_runtime._call_chat_completion_text(
                f"http://127.0.0.1:{json_srv.server_port}/v1/chat/completions",
                {"model": "test-json-fb", "messages": [{"role": "user", "content": "hi"}]},
                stream_stdout=True,
            )
        assert fb_txt == "Non-SSE fallback reply"
        fb_lines = [json.loads(ln) for ln in buf_out.getvalue().splitlines() if ln.strip()]
        assert fb_lines == [{"streamDelta": "Non-SSE fallback reply"}]
        assert "__AQUA_STEP__:" in buf_err.getvalue()
    finally:
        json_srv.shutdown()
        json_srv.server_close()

    # 6b. Unified diff preview (+X / -Y) and 1-click file receipt rollback
    diff_target = private / "Desktop" / "diff_rollback_test.txt"
    _, cr_diff = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "create",
        "targetPath": str(diff_target),
        "content": "line 1\nline 2\nline 3\n",
        "nowMs": now + 4000,
    })
    assert cr_diff["ok"] is True
    cr_res = cr_diff["result"]
    assert cr_res["addedLines"] == 3 and cr_res["removedLines"] == 0
    assert "+line 1" in cr_res["diffPreview"]
    create_rcpt_id = cr_res["receiptId"]

    _, ed_diff = call_mind(agent_env, {
        "action": "agent_file_op",
        "op": "edit",
        "targetPath": str(diff_target),
        "content": "line 1\nline 2 modified\nline 3\nline 4 added\n",
        "nowMs": now + 4100,
    })
    assert ed_diff["ok"] is True
    ed_res = ed_diff["result"]
    assert ed_res["addedLines"] == 2 and ed_res["removedLines"] == 1
    assert "-line 2" in ed_res["diffPreview"] and "+line 2 modified" in ed_res["diffPreview"]
    edit_rcpt_id = ed_res["receiptId"]

    # Rollback the edit via CLI --rollback-file-receipt
    rb_cli = subprocess.run(
        ["python3", str(ROOT / "scripts/aqua/local_mind.py"), "--rollback-file-receipt", edit_rcpt_id],
        env=agent_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    rb_cli_out = json.loads(rb_cli.stdout)
    assert rb_cli_out["ok"] is True and rb_cli_out["existedBefore"] is True
    assert diff_target.read_text(encoding="utf-8") == "line 1\nline 2\nline 3\n"

    # Line-range replace_lines and unified diff apply_patch
    rl_res = tool_store.agent_file_op({
        "op": "replace_lines",
        "targetPath": str(diff_target),
        "startLine": 2,
        "endLine": 2,
        "content": "line 2 range-replaced",
        "nowMs": now + 4150,
    })
    assert rl_res["addedLines"] == 1 and rl_res["removedLines"] == 1
    assert "line 2 range-replaced" in diff_target.read_text(encoding="utf-8")

    ap_res = tool_store.agent_file_op({
        "op": "apply_patch",
        "targetPath": str(diff_target),
        "content": "@@ -1,3 +1,3 @@\n line 1\n-line 2 range-replaced\n+line 2 patched\n line 3\n",
        "nowMs": now + 4160,
    })
    assert ap_res["addedLines"] == 1 and ap_res["removedLines"] == 1
    assert diff_target.read_text(encoding="utf-8") == "line 1\nline 2 patched\nline 3\n"

    # Rollback the creation receipt (removes newly created file)
    rb_create = tool_store.rollback_file_receipt(create_rcpt_id)
    assert rb_create["ok"] is True and rb_create["existedBefore"] is False
    assert not diff_target.exists()
    assert tool_store.rollback_file_receipt("rcpt_nonexistent0000")["ok"] is False

    # 6c. Wayland Primary Selection & Stoppable TTS
    with mock.patch("shutil.which", return_value="/usr/bin/wl-paste"), mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["/usr/bin/wl-paste", "--primary", "--no-newline"],
            returncode=0,
            stdout="Highlighted Wayland Selection Text",
            stderr="",
        )
        prim = desktop_control.primary_selection_get()
        assert prim["ok"] is True and prim["source"] == "primary"
        assert prim["text"] == "Highlighted Wayland Selection Text"

    def _mock_wl_paste_fallback(cmd, *args, **kwargs):
        if "--primary" in cmd or "primary" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Clipboard Fallback Text", stderr="")

    with mock.patch("shutil.which", return_value="/usr/bin/wl-paste"), mock.patch("subprocess.run", side_effect=_mock_wl_paste_fallback):
        prim_fb = desktop_control.primary_selection_get()
        assert prim_fb["ok"] is True and prim_fb["source"] == "clipboard"
        assert prim_fb["text"] == "Clipboard Fallback Text"

    dummy_tts = subprocess.Popen(["sleep", "30"])
    try:
        desktop_control._tts_pid_file().write_text(json.dumps({"pid": dummy_tts.pid}), encoding="utf-8")
        stop_cli = subprocess.run(
            ["python3", str(ROOT / "scripts/aqua/local_mind.py"), "--stop-tts"],
            env=agent_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        stop_res = json.loads(stop_cli.stdout)
        assert stop_res["ok"] is True and stop_res["stopped"] is True and stop_res["pid"] == dummy_tts.pid
        dummy_tts.wait(timeout=3)
        assert not desktop_control._tts_pid_file().exists()
        idle_stop = desktop_control.voice_tts_stop()
        assert idle_stop["ok"] is True and idle_stop["stopped"] is False
    finally:
        if dummy_tts.poll() is None:
            dummy_tts.kill()

    # 6d. Search snippets & matchedTurnIndex in history_store
    created_thr = history_store.execute_thread_action({"op": "create_thread", "title": "Kernel Audit Thread", "character": "aqua"})
    tid_search = created_thr["threadId"]
    history_store.append_exchange("Check Wayland compositor latency", "Wayland frame pacing is 165Hz with zero dropped frames.", thread_id=tid_search)
    matched_list = history_store.search_threads("165Hz")
    assert len(matched_list) >= 1
    m_item = next(item for item in matched_list if item["id"] == tid_search)
    assert m_item["matchedTurnIndex"] == 1
    assert m_item["matchedRole"] == "assistant"
    assert "165Hz" in m_item["matchedSnippet"] and len(m_item["matchedSnippet"]) <= 120
    title_only = history_store.search_threads("Kernel Audit")
    assert any(item["id"] == tid_search and item["matchedTurnIndex"] == -1 for item in title_only)
    empty_q = history_store.search_threads("")
    assert all("matchedTurnIndex" in item and "matchedSnippet" in item and "matchedRole" in item for item in empty_q)

    # 6e. Fine-Tuning Dataset Exporter (ShareGPT & ChatML JSONL + run_store trajectory reconstruction)
    import run_store
    import skill_store
    run_store.cancel_active_runs(now_ms=now + 5000)
    completed_run = run_store.start_run("Lower brightness by 10%", source="interactive", language="en", now_ms=now + 5010)
    step_b = run_store.record_step_start(completed_run["runId"], 1, "desktop_brightness", side_effect=True, now_ms=now + 5015)
    run_store.record_step_finish(
        step_b,
        status="completed",
        detail={"action": "brightness", "status": "completed", "value": "10%-", "summary": "Screen brightness decreased by 10%."},
    )
    step_synth = run_store.record_step_start(completed_run["runId"], 2, "llm_synthesize", side_effect=False, now_ms=now + 5018)
    run_store.record_step_finish(
        step_synth,
        status="completed",
        detail={"status": "completed", "summary": "Synthesized final response."},
    )
    finished_run = run_store.finish_run(
        completed_run["runId"],
        status="completed",
        now_ms=now + 5020,
        final_reply="I lowered the screen brightness by 10%.",
    )
    assert finished_run["checkpoint"]["finalReply"] == "I lowered the screen brightness by 10%."

    skill_store.auto_reflect_run(
        prompt="Lower brightness by 10%",
        status="completed",
        summary="I lowered the screen brightness by 10%.",
        steps=[
            {"toolName": "desktop_brightness", "status": "completed"},
            {"toolName": "llm_synthesize", "status": "completed"},
        ],
    )
    refls_loaded = skill_store._load_reflections()
    assert not any(" ->  ->" in str(r.get("lesson", "")) for r in refls_loaded)

    leaked_run = run_store.start_run("/fetch https://empty-audit-fixture.example.org/blank", source="interactive", now_ms=now + 5030)
    run_store.finish_run(leaked_run["runId"], status="completed", now_ms=now + 5035, final_reply="Leaked fixture reply")

    failed_run = run_store.start_run("Failed run should not export", source="interactive", now_ms=now + 5040)
    run_store.finish_run(failed_run["runId"], status="failed", now_ms=now + 5045, final_reply="Failed reply")

    learning_store.record_trajectory(
        "Inspect Niri workspaces",
        "Here are your active Niri workspaces.",
        tool_calls=[{"name": "desktop_control", "arguments": {"action": "list_windows"}}],
        success=True,
        rating=2,
        preferences={"tone": "concise"},
    )
    learning_store.record_trajectory(
        "Bad trajectory that failed",
        "Should be excluded",
        success=True,
        rating=-1,
    )
    ft_cli = subprocess.run(
        ["python3", str(ROOT / "scripts/aqua/local_mind.py"), "--export-finetune"],
        env=agent_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    ft_res = json.loads(ft_cli.stdout)
    assert ft_res["ok"] is True and ft_res["count"] >= 3
    exported_lines = [json.loads(ln) for ln in Path(ft_res["path"]).read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert all("conversations" in rec for rec in exported_lines)
    assert not any("Should be excluded" in json.dumps(rec) for rec in exported_lines)
    assert not any("empty-audit-fixture.example.org" in json.dumps(rec) for rec in exported_lines)
    assert not any("Failed run should not export" in json.dumps(rec) for rec in exported_lines)
    assert not any("llm_synthesize" in json.dumps(rec) for rec in exported_lines)
    brightness_rec = next(
        rec for rec in exported_lines
        if any(c.get("from") == "human" and c.get("value") == "Lower brightness by 10%" for c in rec["conversations"])
    )
    assert any(c.get("from") == "function_call" and "desktop_brightness" in c.get("value", "") for c in brightness_rec["conversations"])
    assert any(c.get("from") == "observation" and "Screen brightness decreased" in c.get("value", "") for c in brightness_rec["conversations"])
    assert brightness_rec["conversations"][-1] == {"from": "gpt", "value": "I lowered the screen brightness by 10%."}

    chatml_res = learning_store.export_finetune_dataset(format="chatml")
    assert chatml_res["ok"] is True and chatml_res["format"] == "chatml"
    chatml_lines = [json.loads(ln) for ln in Path(chatml_res["path"]).read_text(encoding="utf-8").splitlines() if ln.strip()]
    chatml_brightness = next(
        rec for rec in chatml_lines
        if any(m.get("role") == "user" and m.get("content") == "Lower brightness by 10%" for m in rec["messages"])
    )
    assert any(m.get("role") == "tool" and m.get("name") == "desktop_brightness" for m in chatml_brightness["messages"])

    # 6f. Agent-Desktop semantic snapshot & Companion Virtual Cursor guard compatibility
    import screen_context
    prev_sf = os.environ.get("ALIS_AQUA_SCREEN_FIXTURE")
    os.environ["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps({
        "windowId": 42,
        "title": "Companion Editor",
        "appId": "org.alis.Editor",
        "elements": [
            {"id": 1, "role": "push button", "name": "Save", "bbox": {"x": 40, "y": 20, "w": 60, "h": 24}, "atspiPath": [0, 0], "headlessCapable": True},
            {"id": 2, "role": "entry", "name": "Search...", "bbox": {"x": 120, "y": 20, "w": 160, "h": 24}, "atspiPath": [0, 1], "states": ["editable"], "actions": ["click", "fill", "focus"]},
        ],
    })
    try:
        obs_sc = screen_context.observe_active_window(allow_screenshot=False)
        assert '@e1 [push button] "Save"' in obs_sc["semanticSnapshot"]
        assert '@e2 [entry] "Search..." [editable]' in obs_sc["semanticSnapshot"]
        fill_sc = screen_context.perform_gui_action("fill", ref="@e2", text="hello")
        assert fill_sc["backend"] == "atspi-headless-editable" and fill_sc["ref"] == "@e2"
        assert fill_sc["cursorEvent"]["x"] == 200 and fill_sc["cursorEvent"]["y"] == 32
        verify_guard("agent_file_op", f"__AQUA_CURSOR__:{json.dumps(fill_sc['cursorEvent'])}\n{raw}")
    finally:
        if prev_sf is None:
            os.environ.pop("ALIS_AQUA_SCREEN_FIXTURE", None)
        else:
            os.environ["ALIS_AQUA_SCREEN_FIXTURE"] = prev_sf

    # 6g. Bundled skills (ponytail, docx, pdf, xlsx) & code_tools.read_file / convert_or_create_document
    import code_tools
    import skill_store

    discovered_skills = {s["name"]: s for s in skill_store.list_skills(ROOT)}
    assert "ponytail" in discovered_skills, f"Missing ponytail in {list(discovered_skills)}"
    assert "docx" in discovered_skills, f"Missing docx in {list(discovered_skills)}"
    assert "pdf" in discovered_skills, f"Missing pdf in {list(discovered_skills)}"
    assert "xlsx" in discovered_skills, f"Missing xlsx in {list(discovered_skills)}"
    assert "The best code is the code you never wrote" in discovered_skills["ponytail"]["instructions"]
    assert "Zero-Formula-Error" in discovered_skills["xlsx"]["instructions"]

    assert skill_store.match_skills("/ponytail ultra", ROOT)[0]["name"] == "ponytail"
    assert skill_store.match_skills("Điền biểu mẫu TTSP_BIEUMAU.docx", ROOT)[0]["name"] == "docx"
    assert skill_store.match_skills("Extract pdf tables with pdftotext", ROOT)[0]["name"] == "pdf"
    assert skill_store.match_skills("Phân tích bảng tính excel metrics.xlsx", ROOT)[0]["name"] == "xlsx"

    # Test convert_or_create_document & read_file on .docx (paragraphs + Markdown table order)
    sample_md = (
        "# Quarterly Engineering Report\n\n"
        "Introductory paragraph before table.\n\n"
        "| Module | Status | Lines |\n"
        "| --- | --- | --- |\n"
        "| ponytail | active | 42 |\n"
        "| docx | ready | 108 |\n\n"
        "Closing summary paragraph."
    )
    gen_docx_path = private / "generated_report.docx"
    created_docx = code_tools.convert_or_create_document(sample_md, gen_docx_path, title="Quarterly Engineering Report")
    assert created_docx["ok"] is True and gen_docx_path.is_file()

    read_docx = code_tools.read_file(gen_docx_path)
    assert read_docx["ok"] is True and read_docx["format"] == "docx"
    assert "# Quarterly Engineering Report" in read_docx["text"]
    assert "| Module | Status | Lines |" in read_docx["text"]
    assert "| --- | --- | --- |" in read_docx["text"]
    assert "| ponytail | active | 42 |" in read_docx["text"]
    assert len(read_docx["tables"]) == 1
    assert read_docx["tables"][0][0] == ["Module", "Status", "Lines"]
    assert read_docx["tables"][0][1] == ["ponytail", "active", "42"]
    # Table cells must not be duplicated as standalone paragraphs
    assert "ponytail" not in read_docx["paragraphs"]

    # Test convert_or_create_document & read_file on .pdf (including Vietnamese Unicode round-trip & pageCount)
    vi_pdf_md = (
        "# Biểu Mẫu Thực Tập\n\n"
        "Họ và tên: Nguyễn Văn A\n\n"
        "| Module | Status | Lines |\n"
        "| --- | --- | --- |\n"
        "| ponytail | active | 42 |\n"
    )
    gen_pdf_path = private / "generated_report.pdf"
    created_pdf = code_tools.convert_or_create_document(vi_pdf_md, gen_pdf_path, title="Biểu Mẫu Thực Tập")
    assert created_pdf["ok"] is True and gen_pdf_path.is_file()

    read_pdf = code_tools.read_file(gen_pdf_path)
    assert read_pdf["ok"] is True and read_pdf["format"] == "pdf"
    assert read_pdf.get("pageCount", 0) >= 1, read_pdf
    assert "Biểu Mẫu Thực Tập" in read_pdf["text"], read_pdf["text"]
    assert "Nguyễn Văn A" in read_pdf["text"], read_pdf["text"]
    assert "| Module | Status | Lines |" in read_pdf["text"], read_pdf["text"]

    # Test convert_or_create_document & read_file on .xlsx (worksheets, Markdown tables, dynamic formulas & zero formula errors)
    sample_xlsx_md = (
        "### Sheet: Q1_Budget\n"
        "| Category | Jan | Feb | Total |\n"
        "| --- | --- | --- | --- |\n"
        "| Compute | 1200 | 1500 | =SUM(B2:C2) |\n"
        "| Storage | 300 | 450 | =SUM(B3:C3) |\n"
        "| Grand Total | =SUM(B2:B3) | =SUM(C2:C3) | =SUM(D2:D3) |\n"
    )
    gen_xlsx_path = private / "generated_budget.xlsx"
    created_xlsx = code_tools.convert_or_create_document(sample_xlsx_md, gen_xlsx_path, title="Q1 Budget Workbook")
    assert created_xlsx["ok"] is True and gen_xlsx_path.is_file()

    read_xlsx = code_tools.read_file(gen_xlsx_path)
    assert read_xlsx["ok"] is True and read_xlsx["format"] == "xlsx", read_xlsx
    assert "### Sheet: Q1_Budget" in read_xlsx["text"], read_xlsx["text"]
    assert "2700 (=SUM(B2:C2))" in read_xlsx["text"], read_xlsx["text"]
    assert "3450 (=SUM(D2:D3))" in read_xlsx["text"], read_xlsx["text"]
    assert len(read_xlsx.get("sheets") or []) == 1
    assert len(read_xlsx.get("formulas") or []) == 5
    assert read_xlsx.get("formulaErrors") == [], read_xlsx.get("formulaErrors")

    # Test agent_file_op edit + rollback on .docx and .xlsx preserves valid OOXML archive
    docx_edit_res = tool_store.agent_file_op({
        "op": "edit",
        "targetPath": str(gen_docx_path),
        "content": "# Updated Title\n\nUpdated paragraph.",
        "mode": "write",
        "nowMs": now + 9000,
    })
    assert docx_edit_res["editorMode"] == "popup" and docx_edit_res["isLargeFile"] is False, docx_edit_res
    assert "# Updated Title" in code_tools.read_file(gen_docx_path)["text"]
    rb_docx_res = tool_store.rollback_file_receipt(docx_edit_res["receiptId"])
    assert rb_docx_res["ok"] is True and rb_docx_res["existedBefore"] is True, rb_docx_res
    restored_docx = code_tools.read_file(gen_docx_path)
    assert "# Quarterly Engineering Report" in restored_docx["text"], restored_docx
    assert "| ponytail | active | 42 |" in restored_docx["text"], restored_docx

    # Edge cases: missing file, invalid path & forbidden path
    assert code_tools.read_file(private / "does_not_exist.docx")["error"] == "file_not_found"
    assert code_tools.read_file("")["error"] == "invalid_path"
    assert code_tools.read_file("bad\x00path.docx")["error"] == "forbidden_path"
    assert code_tools.convert_or_create_document("test", "bad\x00out.pdf")["error"] == "forbidden_path"

print("COMPANION_LOCAL_TOOLS_PASS readOnlyMetadata routingCostGate confirmedReceipts timeoutCancel idempotentRollback desktopAgentFileOps2xDelete qolUpgrades agentDesktopSemanticRefsAndVirtualCursor ponytailDocxPdfXlsxSkills")

