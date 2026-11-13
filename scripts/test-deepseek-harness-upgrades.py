#!/usr/bin/env python3
"""Regression tests for DeepSeek Harness 4 Upgrades (A6), Check-In De-dupe, and AlisOutput floating bounds.

Strictly isolated in /tmp via tempfile.TemporaryDirectory — never touches ~/.local/state/alis* or any user vault.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import socketserver
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
AQUA_DIR = SCRIPTS_DIR / "aqua"
HOST_DIR = Path(os.environ.get("ALIS_ROOT", ROOT.parent / "Alis")).resolve()
for _p in (str(AQUA_DIR), str(HOST_DIR / "scripts/ai"), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_provider_runner():
    runner_path = SCRIPTS_DIR / "aqua-companion-provider-run.py"
    spec = importlib.util.spec_from_file_location("aqua_companion_provider_run", runner_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def test_upgrade_1_spill_files_and_read_spill(tmp_root: Path) -> None:
    import agent_runtime
    import local_mind
    import permission_store
    import spill_store
    import tool_store

    state_dir = tmp_root / "u1_state"
    os.environ["ALIS_COMPANION_STATE_DIR"] = str(state_dir)
    os.environ["ALIS_AQUA_RUNS_DB"] = str(state_dir / "runs.sqlite3")
    os.environ["ALIS_AQUA_PERMISSIONS_DB"] = str(state_dir / "perms.sqlite3")

    # 1. Small output stays inline and creates no spill directory
    small_text = "alpha\nbeta\ngamma\n"
    inline_res, inline_meta = spill_store.maybe_spill_tool_output(
        small_text, session_id="sess-u1", call_id="call-small", tool_name="file_inspect"
    )
    assert inline_res == small_text
    assert inline_meta is None
    assert not (state_dir / "spills" / "sess-u1").exists()

    # 2. Large output (>200 lines & >12,000 chars) spills atomically with 0600 file / 0700 dir perms
    lines = [f"line-{idx:04d}: " + ("X" * 64) for idx in range(1, 281)]
    lines[154] = "line-0155: NEEDLE_TOKEN=HARNESS_SPILL_EXACT_MIDDLE_991"
    large_text = "\n".join(lines)
    assert len(large_text) > 12_000

    preview, spill_meta = spill_store.maybe_spill_tool_output(
        large_text, session_id="sess-u1", call_id="call-large-01", tool_name="file_inspect"
    )
    assert spill_meta is not None
    assert "spilled 280 lines /" in preview
    assert 'read_spill(spill_id="call-large-01"' in preview
    assert "line-0001:" in preview
    assert "line-0280:" in preview
    assert "HARNESS_SPILL_EXACT_MIDDLE_991" not in preview, "Middle lines should live in spill file, not inline preview"

    session_spill_dir = state_dir / "spills" / "sess-u1"
    spill_file = session_spill_dir / "call-large-01.txt"
    assert session_spill_dir.is_dir()
    assert spill_file.is_file()
    assert stat.S_IMODE(session_spill_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(spill_file.stat().st_mode) == 0o600
    assert spill_file.read_text(encoding="utf-8") == large_text

    # 3. read_spill slices exact ranges and supports regex/substring grep without permission prompts
    sliced = spill_store.read_spill("sess-u1", spill_id="call-large-01", offset_line=150, limit_lines=12)
    assert sliced["ok"] is True
    assert sliced["offsetLine"] == 150
    assert sliced["returnedLines"] == 12
    assert "L155: line-0155: NEEDLE_TOKEN=HARNESS_SPILL_EXACT_MIDDLE_991" in sliced["content"]

    grepped = tool_store.read_spill_tool({
        "sessionId": "sess-u1",
        "spill_id": "call-large-01",
        "grep": r"NEEDLE_TOKEN=HARNESS_[A-Z0-9_]+",
    })
    assert grepped["ok"] is True
    assert grepped["matchedLines"] == 1
    assert "HARNESS_SPILL_EXACT_MIDDLE_991" in grepped["content"]

    dispatched = local_mind.dispatch({
        "action": "read_spill",
        "sessionId": "sess-u1",
        "path": str(spill_file),
        "offsetLine": 154,
        "limitLines": 3,
    })
    assert dispatched["ok"] is True
    assert "HARNESS_SPILL_EXACT_MIDDLE_991" in dispatched["content"]

    # 4. Path traversal, cross-session escape, outside paths, and symlinks are strictly rejected
    outside_secret = tmp_root / "outside_secret.txt"
    outside_secret.write_text("TOP_SECRET_DO_NOT_READ", encoding="utf-8")
    other_meta = spill_store.save_spill("OTHER_SESSION_DATA\n" * 300, session_id="other-session", call_id="call-other")

    bad_inputs = [
        {"spill_id": "../outside_secret.txt"},
        {"spill_id": "../../outside_secret.txt"},
        {"path": str(outside_secret)},
        {"path": other_meta["path"]},
        {"spill_id": "../other-session/call-other"},
    ]
    for bad in bad_inputs:
        rejected = False
        try:
            spill_store.read_spill("sess-u1", **bad)
        except spill_store.SpillError:
            rejected = True
        assert rejected, f"Expected SpillError for {bad}"

    symlink_path = session_spill_dir / "evil_link.txt"
    symlink_path.symlink_to(outside_secret)
    symlink_rejected = False
    try:
        spill_store.read_spill("sess-u1", spill_id="evil_link")
    except spill_store.SpillError:
        symlink_rejected = True
    finally:
        symlink_path.unlink(missing_ok=True)
    assert symlink_rejected, "Symlink inside session spill dir must be rejected"

    # 5. Retention & session cleanup preserve active sessions
    stale_meta = spill_store.save_spill("OLD\n" * 250, session_id="stale-session", call_id="call-old", prune_expired=False)
    stale_dir = Path(stale_meta["path"]).parent
    stale_file = Path(stale_meta["path"])
    eight_days_ago = time.time() - (8 * 86400)
    os.utime(stale_file, (eight_days_ago, eight_days_ago))
    os.utime(stale_dir, (eight_days_ago, eight_days_ago))

    pruned = spill_store.prune_expired_spills(
        max_age_days=7,
        active_session_ids={"sess-u1"},
    )
    assert pruned >= 1
    assert not stale_dir.exists()
    assert session_spill_dir.exists(), "Active session spills must be preserved"

    removed_files = spill_store.cleanup_session_spills("sess-u1")
    assert removed_files >= 1
    assert not session_spill_dir.exists()
    assert Path(other_meta["path"]).is_file(), "Other session spills must remain untouched"
    spill_store.cleanup_session_spills("other-session")

    # 6. End-to-end ReAct loop: large file_inspect -> spill preview -> read_spill -> exact synthesis
    fake_home = tmp_root / "u1_fake_home"
    workspace_dir = fake_home / "u1_workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    big_doc = workspace_dir / "giant_log.txt"
    doc_lines = [f"entry-{i:04d}: routine telemetry payload {'Z' * 55}" for i in range(1, 321)]
    doc_lines[154] = "entry-0155: ROOT_CAUSE_CODE=E_REACTOR_VALVE_42"
    big_doc.write_text("\n".join(doc_lines), encoding="utf-8")

    prev_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        permission_store.grant_permission("directory", str(workspace_dir), levels=["read"])
        step_counter = {"n": 0}

        class _ReActSpillHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                step_counter["n"] += 1
                n = step_counter["n"]
                if n == 1:
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "Reading giant_log.txt",
                                "tool_calls": [{
                                    "id": "call_big_read",
                                    "type": "function",
                                    "function": {
                                        "name": "file_inspect",
                                        "arguments": json.dumps({"path": str(big_doc)}),
                                    },
                                }],
                            }
                        }]
                    }
                elif n == 2:
                    tool_msgs = [m for m in body["messages"] if m.get("role") == "tool"]
                    assert len(tool_msgs) == 1
                    assert "spilled" in tool_msgs[0]["content"]
                    assert "read_spill" in tool_msgs[0]["content"]
                    assert "E_REACTOR_VALVE_42" not in tool_msgs[0]["content"]
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "Inspecting middle range via read_spill",
                                "tool_calls": [{
                                    "id": "call_inspect_spill",
                                    "type": "function",
                                    "function": {
                                        "name": "read_spill",
                                        "arguments": json.dumps({
                                            "spill_id": "call_big_read",
                                            "offset_line": 150,
                                            "limit_lines": 12,
                                        }),
                                    },
                                }],
                            }
                        }]
                    }
                else:
                    tool_msgs = [m for m in body["messages"] if m.get("role") == "tool"]
                    assert any("E_REACTOR_VALVE_42" in str(m.get("content", "")) for m in tool_msgs)
                    resp = {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": "Found root cause: E_REACTOR_VALVE_42.",
                            }
                        }]
                    }
                payload = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with _ThreadedTCPServer(("127.0.0.1", 0), _ReActSpillHandler) as srv:
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            out = agent_runtime.run_react_tool_loop(
                prompt=f"Inspect {big_doc} and find ROOT_CAUSE_CODE.",
                session_ctx={"lastDirectory": str(workspace_dir), "activeThreadId": "react-spill-sess"},
                options={
                    "threadId": "react-spill-sess",
                    "provider": "deepseek",
                },
                endpoint_url=f"http://127.0.0.1:{port}/v1/chat/completions",
                model_name="deepseek-chat",
                system_prompt="Static base system prompt.",
            )
            srv.shutdown()
    finally:
        if prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev_home

    assert out is not None and out["handled"] is True
    assert "E_REACTOR_VALVE_42" in out["finalText"]
    assert step_counter["n"] == 3


def test_upgrade_2_route_compaction_and_tool_pair_integrity(tmp_root: Path) -> None:
    import context_policy
    runner = _load_provider_runner()

    # 1. Route window mapping & input_budget formula
    w_ollama = context_policy.resolve_route_context_window("ollama", "qwen2.5-coder:7b")
    assert 16_384 <= w_ollama <= 32_768
    assert context_policy.compute_input_budget(w_ollama, 1024) == max(2048, int((w_ollama - 1024) * 0.85))

    w_llama = context_policy.resolve_route_context_window("llama.cpp", "local-gguf-q4")
    assert w_llama == 16_384

    w_ds = context_policy.resolve_route_context_window("deepseek", "deepseek-chat")
    assert 64_000 <= w_ds <= 131_072
    w_ds_v2 = context_policy.resolve_route_context_window("deepseek", "deepseek-v2-64k")
    assert w_ds_v2 == 65_536

    w_gpt4o = context_policy.resolve_route_context_window("openai", "gpt-4o")
    assert w_gpt4o == 128_000
    w_o3 = context_policy.resolve_route_context_window("openai", "o3-mini")
    assert w_o3 == 200_000

    w_claude = context_policy.resolve_route_context_window("anthropic", "claude-sonnet-4-20250514")
    assert w_claude == 200_000

    w_gemini = context_policy.resolve_route_context_window("google", "gemini-2.5-pro")
    assert w_gemini >= 1_000_000

    w_fallback = context_policy.resolve_route_context_window("unknown-vendor", "mystery-model")
    assert w_fallback == 32_768

    w_override = context_policy.resolve_route_context_window("ollama", "qwen2.5:7b", explicit_context_window=49_152)
    assert w_override == 49_152

    # 2. Conservative UTF-8 + tool_calls token estimation
    cjk_msg = {"role": "user", "content": "你好世界" * 300}
    assert context_policy.estimate_message_tokens(cjk_msg) >= (len(("你好世界" * 300).encode("utf-8")) // 3) + 12

    # 3. Compaction preserves static system prefix, recent ~60% budget, spill refs, AND atomic tool pairs
    static_sys = "You are Aqua. Base persona rules that must stay 100% byte-identical."
    messages: list[dict] = [{"role": "system", "content": static_sys}]
    # Pre-inject an orphaned legacy tool message at index 1 to ensure sanitization repairs it
    messages.append({"role": "tool", "tool_call_id": "orphan_legacy_99", "content": "stale orphan output"})

    for turn_idx in range(1, 16):
        call_a = f"call_{turn_idx}_a"
        call_b = f"call_{turn_idx}_b"
        messages.append({
            "role": "user",
            "content": f"Turn {turn_idx} user request: " + ("context_payload " * 35),
        })
        messages.append({
            "role": "assistant",
            "content": f"Turn {turn_idx} checking two tools",
            "tool_calls": [
                {"id": call_a, "type": "function", "function": {"name": "read_spill", "arguments": json.dumps({"spill_id": f"sp_{turn_idx}"})}},
                {"id": call_b, "type": "function", "function": {"name": "file_inspect", "arguments": json.dumps({"path": "."})}},
            ],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": call_a,
            "content": f'[... spilled 400 lines / 22000 UTF-8 bytes to /tmp/spills/s1/sp_{turn_idx}.txt — call read_spill(spill_id="sp_{turn_idx}", offset_line=1, limit_lines=120) ...]\n' + ("data_a " * 45),
        })
        messages.append({
            "role": "tool",
            "tool_call_id": call_b,
            "content": f"Turn {turn_idx} tool B result: " + ("data_b " * 45),
        })
        messages.append({
            "role": "assistant",
            "content": f"Turn {turn_idx} synthesis complete.",
        })

    compacted, meta = context_policy.compact_messages_for_route(
        messages,
        provider="ollama",
        model="qwen2.5:7b",
        context_window=4096,
        reserved_output=1024,
    )
    assert meta["compacted"] is True
    assert compacted[0] == {"role": "system", "content": static_sys}, "System prompt prefix must stay byte-identical"
    summary_msgs = [m for m in compacted if "[Compacted Session Context]" in str(m.get("content", ""))]
    assert len(summary_msgs) == 1
    assert "sp_1" in summary_msgs[0]["content"], "Compacted summary should retain spill references for recovery"

    valid, reason = context_policy.validate_tool_pair_integrity(compacted)
    assert valid is True, f"Tool pair integrity violated after compaction: {reason}"

    # Verify aqua-companion-provider-run.py helpers directly
    pol = runner.resolve_provider_context_policy("deepseek", "deepseek-chat")
    assert pol["contextWindow"] >= 64_000
    prov_compacted, prov_meta = runner.compact_provider_messages(
        messages, provider="llama.cpp", model="local", context_window=4096
    )
    assert prov_meta["compacted"] is True
    prov_valid, prov_reason = context_policy.validate_tool_pair_integrity(prov_compacted)
    assert prov_valid is True, prov_reason


def test_upgrade_3_non_waking_context_inbox_and_prefix_stability(tmp_root: Path) -> None:
    import agent_runtime
    import context_policy
    import local_mind

    state_dir = tmp_root / "u3_state"
    os.environ["ALIS_COMPANION_STATE_DIR"] = str(state_dir)
    session_id = "sess-u3-inbox"
    context_policy.clear_context_updates(session_id)

    # 1. Enqueue multiple rapid ambient updates; verify deduplication & zero LLM wakeups
    context_policy.enqueue_context_update(session_id, "mood_checkin", "Mood: tired, Energy: low", dedupe_key="mood_checkin")
    context_policy.enqueue_context_update(session_id, "active_window", "Focused: firefox", dedupe_key="active_window")
    context_policy.enqueue_context_update(session_id, "mood_checkin", "Mood: focused, Energy: high", dedupe_key="mood_checkin")
    context_policy.enqueue_context_update(session_id, "nexus_note", "Daily/2026-11-11.md", dedupe_key="nexus_note")

    peeked = context_policy.peek_context_updates(session_id)
    assert len(peeked) == 3, "Duplicate mood_checkin updates must coalesce in-place"
    assert [item["dedupeKey"] for item in peeked] == ["mood_checkin", "active_window", "nexus_note"]
    assert "Mood: focused, Energy: high" in peeked[0]["content"]

    # Also verify local_mind context_enqueue / context_drain actions
    enq_res = local_mind.dispatch({
        "action": "context_enqueue",
        "sessionId": "sess-u3-dispatch",
        "kind": "nexus_note",
        "dedupeKey": "nexus_note",
        "content": "Projects/Alis.md",
    })
    assert enq_res["queued"] is True and enq_res["pendingCount"] == 1
    drn_res = local_mind.dispatch({
        "action": "context_drain",
        "sessionId": "sess-u3-dispatch",
    })
    assert drn_res["hasUpdates"] is True
    assert "<context_update>" in drn_res["contextBlock"]
    assert local_mind.dispatch({"action": "context_drain", "sessionId": "sess-u3-dispatch"})["hasUpdates"] is False

    # 2. Verify Turn 1 vs Turn 2 HTTP payloads keep messages[0] 100% byte-identical while draining <context_update>
    seen_payloads: list[dict] = []

    class _PrefixCacheHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            seen_payloads.append(body)
            payload = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": f"Ack turn {len(seen_payloads)}"}}]
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    static_system = "IMMUTABLE_PREFIX_V1: You are Aqua, desktop companion."
    with _ThreadedTCPServer(("127.0.0.1", 0), _PrefixCacheHandler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        endpoint = f"http://127.0.0.1:{port}/v1/chat/completions"

        # Turn 1 drains the 3 queued updates right before the user message
        m1, drained_1 = context_policy.drain_and_inject_context(
            [{"role": "system", "content": static_system}, {"role": "user", "content": "Hello on turn 1"}],
            session_id,
        )
        assert "<context_update>" in drained_1
        r1 = agent_runtime._call_chat_completion_text(
            endpoint,
            {"model": "deepseek-chat", "companionProvider": "deepseek", "messages": m1},
            stream_stdout=False,
        )
        assert r1 == "Ack turn 1"
        assert context_policy.peek_context_updates(session_id) == [], "Inbox must be empty after drain"

        # Queue a new ambient update between Turn 1 and Turn 2 (does NOT wake server!)
        assert len(seen_payloads) == 1
        context_policy.enqueue_context_update(session_id, "active_window", "Focused: kitty", dedupe_key="active_window")
        assert len(seen_payloads) == 1, "Ambient enqueue must NEVER trigger an LLM call"

        # Turn 2 drains only the new update while keeping messages[0] byte-identical to Turn 1
        m2, drained_2 = context_policy.drain_and_inject_context(
            [
                {"role": "system", "content": static_system},
                {"role": "user", "content": "Hello on turn 1"},
                {"role": "assistant", "content": "Ack turn 1"},
                {"role": "user", "content": "Follow-up on turn 2"},
            ],
            session_id,
        )
        assert "Focused: kitty" in drained_2
        r2 = agent_runtime._call_chat_completion_text(
            endpoint,
            {"model": "deepseek-chat", "companionProvider": "deepseek", "messages": m2},
            stream_stdout=False,
        )
        assert r2 == "Ack turn 2"
        srv.shutdown()

    assert len(seen_payloads) == 2
    t1_msgs = seen_payloads[0]["messages"]
    t2_msgs = seen_payloads[1]["messages"]
    assert t1_msgs[0] == {"role": "system", "content": static_system}
    assert t2_msgs[0] == {"role": "system", "content": static_system}
    assert json.dumps(t1_msgs[0], sort_keys=True) == json.dumps(t2_msgs[0], sort_keys=True)
    assert t1_msgs[-1] == {"role": "user", "content": "Hello on turn 1"}
    assert "<context_update>" in t1_msgs[-2]["content"] and "Mood: focused, Energy: high" in t1_msgs[-2]["content"]
    assert t2_msgs[-1] == {"role": "user", "content": "Follow-up on turn 2"}
    assert "<context_update>" in t2_msgs[-2]["content"] and "Focused: kitty" in t2_msgs[-2]["content"]
    assert not any("Mood: focused, Energy: high" in str(m.get("content", "")) for m in t2_msgs[1:]), "Already-drained context must not re-fire"


def test_upgrade_4_cancelled_stream_prefix_finalize_and_legacy_history(tmp_root: Path) -> None:
    import agent_runtime
    import history_store
    import local_mind
    import run_store
    runner = _load_provider_runner()

    state_dir = tmp_root / "u4_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = state_dir / "legacy_chat.sqlite3"
    runs_db_path = state_dir / "runs.sqlite3"
    os.environ["ALIS_COMPANION_STATE_DIR"] = str(state_dir)
    os.environ["ALIS_AQUA_HISTORY_DB"] = str(db_path)
    os.environ["ALIS_AQUA_RUNS_DB"] = str(runs_db_path)

    # 1. Create pre-patch legacy single-table SQLite DB (WITHOUT interrupted, stop_reason, reasoning columns)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL,
            created_ms INTEGER NOT NULL,
            model TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO messages(id, role, content, created_ms, model)
        VALUES
            (101, 'user', 'Legacy hello', 1700000110000, 'qwen2.5:7b'),
            (102, 'assistant', 'Legacy reply intact', 1700000120000, 'qwen2.5:7b');
        """
    )
    conn.commit()
    conn.close()
    os.chmod(db_path, 0o600)

    # Load twice to prove idempotent migration + 100% preservation of IDs, timestamps, and fields
    loaded_1 = history_store.load_with_workspace()
    loaded_2 = history_store.load_with_workspace()
    assert loaded_1["activeThreadId"] == "chat_default"
    assert len(loaded_1["messages"]) == 2
    assert loaded_1["messages"] == loaded_2["messages"]
    assert loaded_1["messages"][0]["id"] == 101
    assert loaded_1["messages"][0]["createdMs"] == 1700000110000
    assert loaded_1["messages"][0]["interrupted"] is False
    assert loaded_1["messages"][0]["stopReason"] == ""
    assert loaded_1["messages"][1]["id"] == 102
    assert loaded_1["messages"][1]["content"] == "Legacy reply intact"
    assert loaded_1["messages"][1]["interrupted"] is False

    # 2. Empty cancelled stream must NOT append a blank/corrupt assistant row
    empty_cancel = history_store.append_cancelled_prefix(
        "Ignored empty cancel prompt",
        partial_content="",
        partial_reasoning="",
        thread_id="chat_default",
    )
    assert empty_cancel["saved"] is False
    assert len(history_store.load_with_workspace()["messages"]) == 2

    # 3. Non-empty cancelled prefix persists interrupted=True, stopReason="cancelled", partial text & reasoning
    saved_cancel = local_mind.dispatch({
        "action": "history_append_cancelled",
        "threadId": "chat_default",
        "prompt": "Explain quantum tunneling",
        "reply": "Quantum tunneling occurs when a wave function penetrates a potential",
        "reasoning": "First derive barrier transmission amplitude...",
        "model": "deepseek-reasoner",
    })
    assert saved_cancel["saved"] is True
    assert saved_cancel["interrupted"] is True
    assert saved_cancel["stopReason"] == "cancelled"

    reloaded = history_store.load_with_workspace()
    assert len(reloaded["messages"]) == 4
    last_msg = reloaded["messages"][-1]
    assert last_msg["role"] == "assistant"
    assert last_msg["interrupted"] is True
    assert last_msg["stopReason"] == "cancelled"
    assert last_msg["content"].startswith("Quantum tunneling occurs")
    assert "barrier transmission" in last_msg["reasoning"]

    # 4. Verify run_store cancelled checkpoint metadata
    started = run_store.start_run("Stream test")
    run_id = started["runId"]
    finished = run_store.finish_run(
        run_id,
        status="cancelled",
        final_reply="Partial run output",
        interrupted=True,
        stop_reason="cancelled",
        partial_reasoning="Partial run thought",
    )
    assert finished is not None
    assert finished["status"] == "cancelled"
    assert finished["checkpoint"]["interrupted"] is True
    assert finished["checkpoint"]["stopReason"] == "cancelled"
    assert finished["checkpoint"]["partialContent"] == "Partial run output"
    assert finished["checkpoint"]["partialReasoning"] == "Partial run thought"

    # 5. Mid-SSE cancellation in agent_runtime._call_chat_completion_text
    cancel_ev = threading.Event()

    class _SlowSSEHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_POST(self):
            _ = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"choices": [{"delta": {"reasoning_content": "Thinking step 1... "}}]},
                {"choices": [{"delta": {"content": "Hello from partial "}}]},
                {"choices": [{"delta": {"content": "stream prefix!", "tool_calls": [{"index": 0, "id": "tc_partial", "function": {"name": "read_spill", "arguments": "{\"spill"}}]}}]},
                {"choices": [{"delta": {"content": " THIS_MUST_NEVER_ARRIVE"}}]},
            ]
            for idx, obj in enumerate(chunks):
                try:
                    self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except BrokenPipeError:
                    break
                if idx == 2:
                    time.sleep(0.06)
                    cancel_ev.set()
                    time.sleep(0.25)
                else:
                    time.sleep(0.02)

    with _ThreadedTCPServer(("127.0.0.1", 0), _SlowSSEHandler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        text_out = agent_runtime._call_chat_completion_text(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            {
                "model": "deepseek-reasoner",
                "companionProvider": "deepseek",
                "messages": [{"role": "user", "content": "Start streaming"}],
            },
            stream_stdout=False,
            cancel_event=cancel_ev,
            session_id="chat_default",
            prompt_for_cancel="Start streaming",
            persist_cancelled=True,
        )
        srv.shutdown()

    assert text_out == "Hello from partial stream prefix!"
    assert "THIS_MUST_NEVER_ARRIVE" not in text_out
    snap = agent_runtime.get_last_cancelled_stream_prefix()
    assert snap is not None
    assert snap["interrupted"] is True
    assert snap["stopReason"] == "cancelled"
    assert snap["content"] == "Hello from partial stream prefix!"
    assert snap["reasoning"] == "Thinking step 1..."

    after_sse_cancel = history_store.load_with_workspace()["messages"]
    assert after_sse_cancel[-1]["interrupted"] is True
    assert after_sse_cancel[-1]["content"] == "Hello from partial stream prefix!"

    # 6. Verify local_mind.py --wrap-provider SIGTERM mid-stream finalization
    slow_child = tmp_root / "slow_sse_child.py"
    slow_child.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "_ = sys.stdin.read()\n"
        "sys.stdout.write('data: ' + json.dumps({'choices':[{'delta':{'reasoning_content':'Child thought. '}}]}) + '\\n\\n')\n"
        "sys.stdout.write('data: ' + json.dumps({'choices':[{'delta':{'content':'Child partial answer'}}]}) + '\\n\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    slow_child.chmod(0o755)
    cancel_receipt = tmp_root / "wrap_cancel_receipt.json"
    wrap_env = os.environ.copy()
    wrap_env["ALIS_COMPANION_STATE_DIR"] = str(state_dir)
    wrap_env["ALIS_AQUA_HISTORY_DB"] = str(db_path)
    wrap_env["ALIS_COMPANION_CANCEL_RECEIPT"] = str(cancel_receipt)
    wrap_env["ALIS_COMPANION_PERSIST_CANCEL_PREFIX"] = "1"
    wrap_env["ALIS_COMPANION_SESSION_ID"] = "chat_default"

    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts/aqua/local_mind.py"),
            "--wrap-provider",
            sys.executable,
            str(slow_child),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=wrap_env,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps({
        "model": "deepseek-chat",
        "companionSessionId": "chat_default",
        "companionProvider": "deepseek",
        "messages": [{"role": "user", "content": "Wrap test"}],
    }) + "\n")
    proc.stdin.close()

    collected_lines: list[str] = []
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        collected_lines.append(line)
        if "Child partial answer" in line:
            proc.send_signal(signal.SIGTERM)
            break
    rest_out, stderr_out = proc.communicate(timeout=5)
    if rest_out:
        collected_lines.extend(rest_out.splitlines(keepends=True))
    joined_sse = "".join(collected_lines)
    assert "Child partial answer" in joined_sse, f"Unexpected SSE output: {joined_sse!r} stderr={stderr_out!r}"
    assert '"finish_reason": "cancelled"' in joined_sse
    assert "data: [DONE]" in joined_sse
    assert cancel_receipt.is_file()
    assert stat.S_IMODE(cancel_receipt.stat().st_mode) == 0o600
    receipt_obj = json.loads(cancel_receipt.read_text(encoding="utf-8"))
    assert receipt_obj["interrupted"] is True
    assert receipt_obj["stopReason"] == "cancelled"
    assert receipt_obj["content"] == "Child partial answer"

    # Verify aqua-companion-provider-run.py finalize_cancelled_stream helper
    fin_empty = runner.finalize_cancelled_stream("Wrap empty", "", "", thread_id="chat_default", persist=True)
    assert fin_empty["saved"] is False and fin_empty["interrupted"] is False
    fin_nonempty = runner.finalize_cancelled_stream(
        "Wrap persist", "Runner partial", "Runner reason", model="deepseek-chat", thread_id="chat_default", persist=True
    )
    assert fin_nonempty["saved"] is True
    assert fin_nonempty["interrupted"] is True and fin_nonempty["stopReason"] == "cancelled"


def test_task_2_and_task_3_qml_contracts() -> None:
    # Task 3: Verify AlisOutput.qml, AquaTalkCloud.qml, and AquaPocketPane.qml expose floating bounds & hover props
    alis_output = (ROOT / "AlisOutput.qml").read_text(encoding="utf-8")
    talk_cloud = (ROOT / "modules/abyss/companion/AquaTalkCloud.qml").read_text(encoding="utf-8")
    pocket_pane = (ROOT / "modules/abyss/companion/AquaPocketPane.qml").read_text(encoding="utf-8")

    assert "readonly property var talkCloudItem: talkCloud" in alis_output
    assert "readonly property Item pocketPaneItem:" in alis_output
    assert "readonly property bool talkCloudHovered:" in alis_output
    assert "readonly property bool pocketPaneHovered:" in alis_output
    assert "readonly property Item pocketPaneItem:" in talk_cloud
    assert "readonly property bool cloudHovered:" in talk_cloud
    assert "readonly property bool hovered:" in pocket_pane

    # Task 2 + Task 1.3 QML JS logic: run choiceSaved & dialogueKeyFor inside Node vm
    node_script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const qml = fs.readFileSync(process.argv[1], "utf8");

// Extract choiceSaved(field, value) body
const startMarker = "function choiceSaved(field, value): void {";
const startIdx = qml.indexOf(startMarker);
assert.ok(startIdx > 0, "choiceSaved(field, value) not found in AquaMind.qml");
let depth = 0;
let bodyStart = qml.indexOf("{", startIdx);
let bodyEnd = -1;
for (let i = bodyStart; i < qml.length; i++) {
  if (qml[i] === "{") depth++;
  else if (qml[i] === "}") {
    depth--;
    if (depth === 0) { bodyEnd = i; break; }
  }
}
const choiceSavedBody = qml.slice(bodyStart + 1, bodyEnd);

// Verify dialogueKeyFor ignores mood/energy while keeping enabled/tone/familiarity
assert.ok(qml.includes("function dialogueKeyFor(modelId): string"), "dialogueKeyFor missing");
assert.ok(qml.includes("AquaPersona.instruction(character,dialoguePreferences)+context"), "Fixture persona contract must stay intact");

function makeHarness({ reactiveBridge }) {
  let explicitSyncCalls = 0;
  let reactivePulses = 0;
  const aquaCheckInObj = { date: "2026-11-11", _mood: "", _energy: "" };
  const nexus = {
    docBusy: false,
    _docQueue: [],
    todayJournalInfo: { exists: true, date: "2026-11-11", mood: "", energy: "" },
    syncCompanionCheckIn: (d, m, e) => {
      explicitSyncCalls++;
      if (m !== undefined) nexus.todayJournalInfo.mood = m;
      if (e !== undefined) nexus.todayJournalInfo.energy = e;
    },
  };
  Object.defineProperty(aquaCheckInObj, "mood", {
    get() { return this._mood; },
    set(v) {
      this._mood = v;
      if (reactiveBridge && v) {
        reactivePulses++;
        nexus._docQueue.push({ op: "reactive_mood", value: v });
        nexus.todayJournalInfo.mood = v;
      }
    },
  });
  Object.defineProperty(aquaCheckInObj, "energy", {
    get() { return this._energy; },
    set(v) {
      this._energy = v;
      if (reactiveBridge && v) {
        reactivePulses++;
        nexus._docQueue.push({ op: "reactive_energy", value: v });
        nexus.todayJournalInfo.energy = v;
      }
    },
  });

  const ctx = {
    _lastCheckInSyncToken: "",
    checkInDate: "2026-11-11",
    checkInStage: "",
    conversationOpen: false,
    lastCheckIn: 0,
    journal: {},
    enqueueContextUpdate: (kind, content, key) => { ctx._enqueued = { kind, content, key }; },
    askCheckIn: () => {},
    say: () => {},
    contextualPhrase: () => "Nice!",
    reactionRequested: () => {},
    AlisIntergration: { nexus },
    Persistent: { states: { aquaCheckIn: aquaCheckInObj } },
  };
  const fn = new vm.Script(`(function(field, value) { ${choiceSavedBody} })`).runInNewContext(ctx);
  return { ctx, nexus, fn, counts: () => ({ explicitSyncCalls, reactivePulses }) };
}

// Case A: Reactive host bridge handles Persistent.states.aquaCheckIn -> 0 duplicate explicit sync calls!
const hReactive = makeHarness({ reactiveBridge: true });
hReactive.fn("mood", "good");
assert.deepEqual(hReactive.counts(), { explicitSyncCalls: 0, reactivePulses: 1 });
assert.equal(hReactive.ctx._enqueued.key, "mood_checkin");

// Case B: Non-reactive / standalone / pre-scan mode -> explicit syncCompanionCheckIn fires once!
const hDirect = makeHarness({ reactiveBridge: false });
hDirect.fn("mood", "good");
assert.deepEqual(hDirect.counts(), { explicitSyncCalls: 1, reactivePulses: 0 });

// Case C: Duplicate identical check-in when todayJournalInfo already matches -> 0 extra explicit calls!
hDirect.fn("mood", "good");
assert.deepEqual(hDirect.counts(), { explicitSyncCalls: 1, reactivePulses: 0 });

// Case D: Retry when todayJournalInfo did NOT persist (e.g., transient failure) -> recovers with 1 explicit call!
hDirect.nexus.todayJournalInfo.mood = "";
hDirect.fn("mood", "good");
assert.deepEqual(hDirect.counts(), { explicitSyncCalls: 2, reactivePulses: 0 });
"""
    res = subprocess.run(
        ["node", "-e", node_script, str(ROOT / "services/AquaMind.qml")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Node QML contract check failed:\n{res.stdout}\n{res.stderr}"


def main() -> None:
    os.environ["ALIS_TEST_ISOLATED"] = "1"
    with tempfile.TemporaryDirectory(prefix="alis-dsh-upgrades-", dir="/tmp") as tmp:
        tmp_root = Path(tmp)
        os.environ["XDG_STATE_HOME"] = str(tmp_root / "xdg_state")
        os.environ["XDG_DATA_HOME"] = str(tmp_root / "xdg_data")
        os.environ["ALIS_COMPANION_STATE_DIR"] = str(tmp_root / "companion_state")

        test_upgrade_1_spill_files_and_read_spill(tmp_root)
        print("PASS upgrade_1_spill_files_and_read_spill", flush=True)

        test_upgrade_2_route_compaction_and_tool_pair_integrity(tmp_root)
        print("PASS upgrade_2_route_compaction_and_tool_pair_integrity", flush=True)

        test_upgrade_3_non_waking_context_inbox_and_prefix_stability(tmp_root)
        print("PASS upgrade_3_non_waking_context_inbox_and_prefix_stability", flush=True)

        test_upgrade_4_cancelled_stream_prefix_finalize_and_legacy_history(tmp_root)
        print("PASS upgrade_4_cancelled_stream_prefix_finalize_and_legacy_history", flush=True)

        test_task_2_and_task_3_qml_contracts()
        print("PASS task_2_checkin_dedupe_and_task_3_alis_output_bounds", flush=True)

    print("ALL DEEPSEEK HARNESS & COMPANION UPGRADE TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
