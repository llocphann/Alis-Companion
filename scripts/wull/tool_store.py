"""Typed read-only metadata tools, conditional RAG/routing gate, and confirmed edits.

All operations are one-shot and strictly allowlisted. Model output never
executes an edit directly: every mutation requires an explicit user-confirmed
proposal token within its deadline, produces a verifiable receipt with before/
after values and SHA-256 digest, supports idempotent confirmation/cancellation,
and supports deterministic rollback.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

PROPOSAL_TTL_MS = 120_000
MAX_PROPOSALS = 64
MAX_RECEIPTS = 128

ALLOWED_PREFERENCE_EDITS = {
    "personality": {"calm", "lively", "shy"},
    "appearanceFrequency": {"rare", "normal", "frequent"},
    "dialogueTone": {"gentle", "playful", "concise"},
    "familiarity": {"reserved", "neutral", "warm"},
    "proactive": {"rare", "occasional", "regular", "often", "manual"},
    "proactiveQuietEnabled": {True, False},
}

CONFIG_PATHS = {
    "personality": "abyss.companion.personality",
    "appearanceFrequency": "abyss.companion.appearanceFrequency",
    "dialogueTone": "abyss.companionMind.dialogueTone",
    "familiarity": "abyss.companionMind.familiarity",
    "proactive": "abyss.companionMind.proactive",
    "proactiveQuietEnabled": "abyss.companionMind.proactiveQuietEnabled",
}

DOC_KEYWORDS = frozenset({
    "architecture", "companion", "config", "cowork", "doc", "docs", "event",
    "focus", "hadalis", "hadanion", "memory", "obsidian", "planner", "pocket",
    "portal", "privacy", "reminder", "repo", "schedule", "setting", "settings",
    "shader", "todo", "wayland", "wull",
})


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _state_file() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    folder = (base / "hadanion").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / "tool_receipts.json"


def _load_store() -> dict:
    path = _state_file()
    if not path.is_file():
        return {"proposals": {}, "receipts": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError()
        proposals = raw.get("proposals", {})
        receipts = raw.get("receipts", [])
        if not isinstance(proposals, dict) or not isinstance(receipts, list):
            raise ValueError()
        return {"proposals": proposals, "receipts": receipts}
    except (OSError, ValueError) as exc:
        raise ToolError("store_unavailable", "Tool receipt store could not be read.") from exc


def _save_store(store: dict) -> None:
    path = _state_file()
    payload = json.dumps(store, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".tool-receipts-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ToolError("store_unavailable", "Tool receipt store could not be saved.") from exc


def _now_ms(override=None) -> int:
    if override is not None:
        if type(override) is not int or override < 0:
            raise ToolError("invalid_clock", "Invalid timestamp for tool operation.")
        return override
    return int(time.time() * 1000)


def _clean_line(text: str, limit: int = 160) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def _git_summary(root: Path) -> tuple[str, str]:
    if not (root / ".git").exists():
        return "", ""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root), text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()[:80]
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root), text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()[:40]
        return branch, sha
    except (OSError, subprocess.SubprocessError):
        return "", ""


def inspect_metadata(options: dict, companion_root: Path, host_root: Path) -> dict:
    kind = str(options.get("kind", "")).strip()
    if kind == "host_metadata":
        manifest_path = companion_root / "manifest.json"
        manifest = {}
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
        return {
            "kind": "host_metadata",
            "hostAvailable": host_root.is_dir(),
            "packageId": _clean_line(manifest.get("id", "hadanion"), 64),
            "version": _clean_line(manifest.get("version", "0.1.0"), 32),
            "localOnlyDefault": True,
            "capabilities": ["host_metadata", "repo_metadata", "doc_metadata", "preference_patch"],
            "headings": [],
        }
    if kind == "repo_metadata":
        target = str(options.get("target", "companion")).strip()
        if target not in ("companion", "host"):
            raise ToolError("invalid_target", "Repository target must be companion or host.")
        root = companion_root if target == "companion" else host_root
        available = root.is_dir()
        branch, sha = _git_summary(root) if available else ("", "")
        docs_dir = root / "docs"
        doc_count = sum(1 for p in docs_dir.glob("*.md") if p.is_file()) if docs_dir.is_dir() else 0
        return {
            "kind": "repo_metadata",
            "hostAvailable": available,
            "packageId": target,
            "version": branch or "detached",
            "localOnlyDefault": True,
            "capabilities": [sha] if sha else [],
            "headings": [f"docs:{doc_count}"],
        }
    if kind == "doc_metadata":
        target = str(options.get("target", "README.md")).strip()
        if not target or ".." in target or target.startswith("/") or "\\" in target:
            raise ToolError("invalid_target", "Documentation path must be a relative package document.")
        candidate = (companion_root / target).resolve()
        if not candidate.is_relative_to(companion_root.resolve()) or candidate.suffix.lower() != ".md":
            raise ToolError("invalid_target", "Only markdown documents inside the package may be inspected.")
        if not candidate.is_file() or candidate.stat().st_size > 256 * 1024:
            raise ToolError("doc_unavailable", "Requested documentation file is unavailable.")
        headings = []
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^(#{1,4})\s+(.+?)\s*#*$", line)
            if match:
                headings.append(_clean_line(match.group(2), 120))
                if len(headings) >= 16:
                    break
        return {
            "kind": "doc_metadata",
            "hostAvailable": True,
            "packageId": target[:120],
            "version": str(candidate.stat().st_size),
            "localOnlyDefault": True,
            "capabilities": ["read_only"],
            "headings": headings,
        }
    raise ToolError("invalid_tool", "Unsupported metadata inspection tool.")


def evaluate_routing(options: dict, companion_root: Path) -> dict:
    prompt = str(options.get("prompt", "")).strip()
    if not prompt or len(prompt) > 1200:
        raise ToolError("invalid_prompt", "Prompt must contain 1 to 1200 characters.")
    local_only = options.get("localOnly") is not False
    is_local_model = options.get("isLocalModel") is True
    if local_only and not is_local_model:
        return {
            "allowed": False,
            "route": "blocked",
            "ragUsed": False,
            "reason": "local_only_required",
            "snippets": [],
            "estimatedTokens": 0,
        }
    tokens = set(re.findall(r"[a-z0-9_]+", prompt.casefold()))
    matched = sorted(tokens & DOC_KEYWORDS)
    doc_opt_in = options.get("docOptIn") is True
    if len(prompt) <= 48 and not matched:
        return {
            "allowed": True,
            "route": "reflex",
            "ragUsed": False,
            "reason": "short_turn_no_retrieval_cost",
            "snippets": [],
            "estimatedTokens": max(1, len(prompt) // 4),
        }
    if not matched or not doc_opt_in:
        return {
            "allowed": True,
            "route": "reflex" if len(prompt) <= 140 else "brain",
            "ragUsed": False,
            "reason": "rag_not_needed" if not matched else "doc_consent_disabled",
            "snippets": [],
            "estimatedTokens": max(1, len(prompt) // 4),
        }
    readme = companion_root / "README.md"
    snippets = []
    if readme.is_file() and readme.stat().st_size <= 256 * 1024:
        paragraphs = [
            _clean_line(block, 240)
            for block in readme.read_text(encoding="utf-8", errors="replace").split("\n\n")
            if block.strip()
        ]
        for para in paragraphs:
            lower = para.casefold()
            if any(word in lower for word in matched):
                snippets.append(para)
                if len(snippets) >= 3:
                    break
    total_chars = sum(len(s) for s in snippets)
    return {
        "allowed": True,
        "route": "brain",
        "ragUsed": bool(snippets),
        "reason": "grounded_doc_snippets" if snippets else "no_matching_snippets",
        "snippets": snippets[:3],
        "estimatedTokens": max(1, (len(prompt) + total_chars) // 4),
    }


def _validate_preference_value(field: str, value) -> None:
    if field not in ALLOWED_PREFERENCE_EDITS:
        raise ToolError("invalid_field", "Preference field is not in the allowlist.")
    allowed = ALLOWED_PREFERENCE_EDITS[field]
    if type(value) is bool:
        if value not in allowed or not any(type(x) is bool for x in allowed):
            raise ToolError("invalid_value", "Invalid boolean value for preference.")
    elif isinstance(value, str):
        if value not in allowed:
            raise ToolError("invalid_value", "Invalid option for preference.")
    else:
        raise ToolError("invalid_value", "Preference value must be a valid option.")


def _digest(obj: dict) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def propose_edit(options: dict) -> dict:
    field = str(options.get("field", "")).strip()
    before_val = options.get("beforeValue")
    after_val = options.get("afterValue")
    _validate_preference_value(field, before_val)
    _validate_preference_value(field, after_val)
    now = _now_ms(options.get("nowMs"))
    store = _load_store()
    proposals = store["proposals"]
    # Expire old proposals and cap queue size
    for pid, item in list(proposals.items()):
        if item.get("status") == "pending" and now > int(item.get("expiresMs", 0)):
            item["status"] = "expired"
    if len(proposals) >= MAX_PROPOSALS:
        oldest = sorted(proposals.keys(), key=lambda k: int(proposals[k].get("createdMs", 0)))[0]
        del proposals[oldest]
    seed = f"{field}:{before_val}:{after_val}:{now}:{len(proposals)}"
    proposal_id = "prop_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    record = {
        "proposalId": proposal_id,
        "field": field,
        "configPath": CONFIG_PATHS[field],
        "beforeValue": before_val,
        "afterValue": after_val,
        "createdMs": now,
        "expiresMs": now + PROPOSAL_TTL_MS,
        "status": "pending",
        "receiptId": "",
    }
    proposals[proposal_id] = record
    _save_store(store)
    return record


def confirm_edit(options: dict) -> dict:
    if options.get("confirmed") is not True:
        raise ToolError("explicit_consent_required", "Explicit user confirmation is required to apply a tool edit.")
    proposal_id = str(options.get("proposalId", "")).strip()
    if not re.fullmatch(r"prop_[0-9a-f]{16}", proposal_id):
        raise ToolError("invalid_proposal", "Invalid tool proposal identifier.")
    now = _now_ms(options.get("nowMs"))
    store = _load_store()
    proposal = store["proposals"].get(proposal_id)
    if not isinstance(proposal, dict):
        raise ToolError("proposal_missing", "Tool proposal was not found.")
    if proposal.get("status") == "confirmed" and proposal.get("receiptId"):
        for existing in store["receipts"]:
            if existing.get("receiptId") == proposal["receiptId"]:
                result = dict(existing)
                result["idempotent"] = True
                return result
    if proposal.get("status") == "cancelled":
        raise ToolError("proposal_cancelled", "Cancelled tool proposal cannot be confirmed.")
    if proposal.get("status") == "expired" or now > int(proposal.get("expiresMs", 0)):
        proposal["status"] = "expired"
        _save_store(store)
        raise ToolError("proposal_expired", "Tool proposal timed out before confirmation.")
    receipt_core = {
        "proposalId": proposal_id,
        "field": proposal["field"],
        "configPath": proposal["configPath"],
        "beforeValue": proposal["beforeValue"],
        "afterValue": proposal["afterValue"],
        "appliedAtMs": now,
    }
    digest = _digest(receipt_core)
    receipt_id = "rcpt_" + digest[:16]
    receipt = {
        **receipt_core,
        "receiptId": receipt_id,
        "digest": digest,
        "rolledBack": False,
        "idempotent": False,
    }
    proposal["status"] = "confirmed"
    proposal["receiptId"] = receipt_id
    store["receipts"] = (store["receipts"] + [receipt])[-MAX_RECEIPTS:]
    _save_store(store)
    return receipt


def cancel_edit(options: dict) -> dict:
    proposal_id = str(options.get("proposalId", "")).strip()
    if not re.fullmatch(r"prop_[0-9a-f]{16}", proposal_id):
        raise ToolError("invalid_proposal", "Invalid tool proposal identifier.")
    store = _load_store()
    proposal = store["proposals"].get(proposal_id)
    if not isinstance(proposal, dict):
        raise ToolError("proposal_missing", "Tool proposal was not found.")
    if proposal.get("status") == "confirmed":
        raise ToolError("already_confirmed", "Confirmed proposal must use rollback instead of cancel.")
    proposal["status"] = "cancelled"
    _save_store(store)
    return {"proposalId": proposal_id, "cancelled": True, "status": "cancelled"}


def rollback_edit(options: dict) -> dict:
    if options.get("confirmed") is not True:
        raise ToolError("explicit_consent_required", "Explicit user confirmation is required for rollback.")
    receipt_id = str(options.get("receiptId", "")).strip()
    if not re.fullmatch(r"rcpt_[0-9a-f]{16}", receipt_id):
        raise ToolError("invalid_receipt", "Invalid tool receipt identifier.")
    store = _load_store()
    for receipt in store["receipts"]:
        if receipt.get("receiptId") == receipt_id:
            already = receipt.get("rolledBack") is True
            receipt["rolledBack"] = True
            _save_store(store)
            result = dict(receipt)
            result["idempotent"] = already
            return result
    raise ToolError("receipt_missing", "Tool receipt was not found.")


def list_receipts() -> dict:
    store = _load_store()
    return {"receipts": store["receipts"][-MAX_RECEIPTS:]}
