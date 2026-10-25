#!/usr/bin/env python3
"""Verify typed read-only metadata tools, conditional RAG/routing, and confirmed edits."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(os.environ.get("HADANION_SOURCE", Path(__file__).resolve().parents[1]))
HOST = Path(os.environ.get("HADALIS_ROOT", ROOT))


def call_mind(env, payload):
    proc = subprocess.run(
        ["python3", str(ROOT / "scripts/wull/local_mind.py"), "--host-root", str(HOST)],
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
vm.runInContext(fs.readFileSync("services/WullReplyGuard.js", "utf8").replace(/^\\.pragma library\\s*/, ""), pub);
const ctx = vm.createContext({ PublicReply: pub, Number, Array, Object, JSON, Math });
const src = fs.readFileSync("services/WullHelperReplyGuard.js", "utf8")
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

print("COMPANION_LOCAL_TOOLS_PASS readOnlyMetadata routingCostGate confirmedReceipts timeoutCancel idempotentRollback")
