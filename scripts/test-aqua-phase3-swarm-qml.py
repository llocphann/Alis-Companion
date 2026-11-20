#!/usr/bin/env python3
"""Verification tests for Alis-Companion Phase 3 (Milestone 4) upgrades:
1. Opt-in `autoSubagentDecompose` (disabled unless explicitly True).
2. Bounded multi-step `delegate_subagent` tool loop (up to 3 turns) with strict
   read-only privilege isolation & tool-pair integrity across turns.
3. Layer-grouped concurrent cloud / semaphore(1) local `run_swarm_mission` execution
   with clean `stopped` abort handling.
4. `AquaTalkCloud.qml` O(1) `_bubbleTextRegistry` lookup & valid QML parse check.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AQUA_DIR = ROOT / "scripts" / "aqua"
if str(AQUA_DIR) not in sys.path:
    sys.path.insert(0, str(AQUA_DIR))

import agent_runtime  # noqa: E402
import context_policy  # noqa: E402
import swarm_store  # noqa: E402


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("DISPLAY", None)
    env.pop("AQUA_TEST_ENDPOINT", None)
    env.pop("AI_FIXTURE_URL", None)
    env["HOME"] = str(tmp_path / "home")
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["ALIS_TEST_ISOLATED"] = "1"
    for d in ("home", "state", "config", "cache"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return env


class Phase3SwarmQmlTests(unittest.TestCase):
    def test_01_auto_subagent_decompose_opt_in_by_default(self) -> None:
        complex_prompt = (
            "Investigate and scan my workspace repository, design an architecture plan, "
            "and cross-verify all results independently across files."
        )
        scale = agent_runtime.assess_task_scale_for_subagents(complex_prompt, {})
        self.assertTrue(scale.get("shouldDecompose"), scale)

        with TemporaryDirectory(prefix="alis-phase3-optin-") as tmp:
            tmp_path = Path(tmp)
            ws = tmp_path / "home" / "ws"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "app.py").write_text("print('hi')\n", encoding="utf-8")
            env = _isolated_env(tmp_path)
            with patch.dict(os.environ, env, clear=True):
                agent_runtime._ACTIVE_SUBAGENT_RUNS.clear()
                res_default = agent_runtime.execute_agent_turn(
                    {"prompt": complex_prompt, "workingDir": str(ws)},
                    ROOT,
                    ROOT,
                )
                self.assertEqual(
                    len(agent_runtime._ACTIVE_SUBAGENT_RUNS),
                    0,
                    f"Default execute_agent_turn without autoSubagentDecompose=True spawned subagents: {agent_runtime._ACTIVE_SUBAGENT_RUNS}",
                )
                self.assertIsInstance(res_default, dict)

                agent_runtime._ACTIVE_SUBAGENT_RUNS.clear()
                agent_runtime.execute_agent_turn(
                    {"prompt": complex_prompt, "workingDir": str(ws), "autoSubagentDecompose": True},
                    ROOT,
                    ROOT,
                )
                self.assertGreaterEqual(len(agent_runtime._ACTIVE_SUBAGENT_RUNS), 2)

    def test_02_delegate_subagent_multiturn_loop_and_privilege_isolation(self) -> None:
        observed_requests: list[dict] = []

        class _SubagentMultiTurnHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                observed_requests.append(body)
                msgs = body.get("messages") or []
                tool_msgs = [m for m in msgs if m.get("role") == "tool"]
                if not tool_msgs:
                    resp = {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call_sub_read",
                                            "type": "function",
                                            "function": {
                                                "name": "code_search",
                                                "arguments": json.dumps({"query": "phase3_marker"}),
                                            },
                                        },
                                        {
                                            "id": "call_sub_blocked",
                                            "type": "function",
                                            "function": {
                                                "name": "file_mutate",
                                                "arguments": json.dumps({"action": "write_file", "path": "x.txt", "content": "no"}),
                                            },
                                        },
                                    ],
                                }
                            }
                        ]
                    }
                else:
                    valid_pairs, err = context_policy.validate_tool_pair_integrity(msgs)
                    assert valid_pairs, f"Broken tool-pair integrity in subagent Turn 2: {err}"
                    resp = {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "Multi-turn researcher synthesis complete.",
                                }
                            }
                        ]
                    }
                raw = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        with TemporaryDirectory(prefix="alis-phase3-sub-") as tmp:
            tmp_path = Path(tmp)
            ws = tmp_path / "home" / "ws"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "sample.py").write_text("VALUE = 'phase3_marker_token'\n", encoding="utf-8")
            env = _isolated_env(tmp_path)
            with patch.dict(os.environ, env, clear=True):
                with _ThreadedHTTPServer(("127.0.0.1", 0), _SubagentMultiTurnHandler) as srv:
                    port = srv.server_address[1]
                    threading.Thread(target=srv.serve_forever, daemon=True).start()
                    rec = agent_runtime.delegate_subagent(
                        role="researcher",
                        task="Find phase3_marker inside workspace and summarize.",
                        options={
                            "endpoint": f"http://127.0.0.1:{port}/v1/chat/completions",
                            "model": "qwen3-coder",
                            "subagentMaxTurns": 3,
                        },
                        session_ctx={"lastDirectory": str(ws), "lastProject": str(ws)},
                        companion_root=ROOT,
                    )
                    srv.shutdown()

        self.assertTrue(rec["ok"])
        self.assertEqual(rec["llmTurns"], 2)
        self.assertIn("file_mutate", rec["blockedTools"])
        self.assertEqual(len(observed_requests), 2)
        t1_tool_names = [t["function"]["name"] for t in observed_requests[0]["tools"]]
        self.assertNotIn("file_mutate", t1_tool_names)
        self.assertNotIn("delegate_subagent", t1_tool_names)
        t2_tools = [m for m in observed_requests[1]["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(t2_tools), 2)
        self.assertIn("Multi-turn researcher synthesis complete.", rec["summary"])

    def test_03_swarm_layer_parallel_cloud_vs_serial_local_and_stop(self) -> None:
        tracker_lock = threading.Lock()
        tracker = {"cur": 0, "max": 0, "calls": 0, "stop_after": 0}

        class _SwarmConcurrencyHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                is_tool_turn = bool(body.get("tools"))
                with tracker_lock:
                    tracker["calls"] += 1
                    if is_tool_turn:
                        tracker["cur"] += 1
                        if tracker["cur"] > tracker["max"]:
                            tracker["max"] = tracker["cur"]
                    if tracker["stop_after"] and tracker["calls"] == tracker["stop_after"]:
                        swarm_store.stop_swarm_state(emit=False)
                try:
                    if is_tool_turn:
                        time.sleep(0.22)
                    resp = {"choices": [{"message": {"role": "assistant", "content": "Slice done cleanly."}}]}
                    raw = json.dumps(resp).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                finally:
                    if is_tool_turn:
                        with tracker_lock:
                            tracker["cur"] -= 1

        goal = "Research codebase structure, plan refactoring steps, and cross-verify tests"

        with TemporaryDirectory(prefix="alis-phase3-swarm-") as tmp:
            tmp_path = Path(tmp)
            ws = tmp_path / "home" / "ws"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
            env = _isolated_env(tmp_path)
            with patch.dict(os.environ, env, clear=True):
                with _ThreadedHTTPServer(("127.0.0.1", 0), _SwarmConcurrencyHandler) as srv:
                    port = srv.server_address[1]
                    threading.Thread(target=srv.serve_forever, daemon=True).start()
                    ep = f"http://127.0.0.1:{port}/v1/chat/completions"

                    # A. Cloud / parallel: Layer 1 has multiple workers running concurrently
                    tracker.update({"cur": 0, "max": 0, "calls": 0, "stop_after": 0})
                    cloud_state, cloud_md = agent_runtime.run_swarm_mission(
                        goal,
                        {
                            "endpoint": ep,
                            "model": "gpt-4o",
                            "provider": "openai",
                            "swarmForceConcurrency": "parallel",
                        },
                        ROOT,
                        session_ctx={"lastDirectory": str(ws), "lastProject": str(ws)},
                    )
                    self.assertEqual(cloud_state["status"], "completed", cloud_md)
                    self.assertGreaterEqual(
                        tracker["max"],
                        2,
                        f"Expected parallel Layer-1 execution >= 2 in-flight on cloud swarm, got max={tracker['max']}",
                    )

                    # B. Local VRAM: serialized via _SWARM_LOCAL_SEMAPHORE
                    tracker.update({"cur": 0, "max": 0, "calls": 0, "stop_after": 0})
                    local_state, local_md = agent_runtime.run_swarm_mission(
                        goal,
                        {
                            "endpoint": ep,
                            "model": "qwen3-coder",
                            "provider": "lmstudio",
                        },
                        ROOT,
                        session_ctx={"lastDirectory": str(ws), "lastProject": str(ws)},
                    )
                    self.assertEqual(local_state["status"], "completed", local_md)
                    self.assertEqual(
                        tracker["max"],
                        1,
                        f"Expected serial local VRAM execution (max == 1), got max={tracker['max']}",
                    )

                    # C. Mid-flight stop aborts cleanly without overwriting stopped state
                    tracker.update({"cur": 0, "max": 0, "calls": 0, "stop_after": 1})
                    stopped_state, _ = agent_runtime.run_swarm_mission(
                        goal,
                        {
                            "endpoint": ep,
                            "model": "qwen3-coder",
                            "provider": "lmstudio",
                        },
                        ROOT,
                        session_ctx={"lastDirectory": str(ws), "lastProject": str(ws)},
                    )
                    self.assertEqual(stopped_state["status"], "stopped")
                    srv.shutdown()

    def test_04_aquatalkcloud_bubble_registry_and_qml_syntax(self) -> None:
        qml_path = ROOT / "modules/abyss/companion/AquaTalkCloud.qml"
        src = qml_path.read_text(encoding="utf-8")

        for token in (
            "_bubbleTextRegistry",
            "function registerBubbleText(",
            "function unregisterBubbleText(",
            "function getBubbleTextItem(",
            "onRegistryIndexChanged",
            "Component.onDestruction:",
            "root.unregisterBubbleText(_registeredBubbleIndex, messageText)",
            "root.getBubbleTextItem(idx)",
        ):
            self.assertIn(token, src, f"Missing {token!r} in AquaTalkCloud.qml")

        # Verify clearOtherSelections, selectBubbleText, copyBubbleText, copyActiveSelection avoid tree scan
        sel_block = src.split("function clearOtherSelections(")[1].split("readonly property bool backgroundThinkingBubble:")[0]
        self.assertNotIn("root.findChildByObjectName(transcript", sel_block)

        qmlformat = "/usr/lib/qt6/bin/qmlformat" if Path("/usr/lib/qt6/bin/qmlformat").is_file() else shutil.which("qmlformat")
        if qmlformat:
            res = subprocess.run([qmlformat, str(qml_path)], capture_output=True, text=True, check=False)
            self.assertEqual(res.returncode, 0, f"qmlformat parse failed: {res.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
