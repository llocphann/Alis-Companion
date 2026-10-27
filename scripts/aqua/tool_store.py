"""Typed read-only metadata tools, conditional RAG/routing gate, and desktop agent tools.

Permissions and path model:
- User Home ($HOME): full access (read, write, edit, create, copy, cut, delete with
  confirmation, run, and symlink traversal inside $HOME) across all files and folders
  in $HOME (including $HOME root and dot-directories).
- System paths (outside $HOME): require explicit user confirmation AND sudo password
  verification via `execute_system_sudo_op` (`sudo -S -k -p ''`).
- Directory listings return all items (up to 500 entries per page) without artificial
  8-line or 10-line truncation.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from code_tools import retrieve_rag
from sandbox_runner import SandboxError, run_sandboxed
from screen_context import observe_active_window

PROPOSAL_TTL_MS = 120_000
MAX_PROPOSALS = 64
MAX_RECEIPTS = 128
MAX_FILE_RECEIPTS = 64

ALLOWED_PREFERENCE_EDITS = {
    "personality": {"calm", "balanced", "energetic", "lively", "shy"},
    "appearanceFrequency": {"always", "frequent", "occasional", "rare", "normal"},
    "dialogueTone": {"gentle", "witty", "playful", "concise"},
    "familiarity": {"reserved", "neutral", "familiar", "warm"},
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
    "focus", "alis", "alis-companion", "memory", "obsidian", "planner", "pocket",
    "portal", "privacy", "reminder", "repo", "schedule", "setting", "settings",
    "shader", "todo", "wayland", "aqua", "octo", "agent", "permission",
})


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _state_file() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    folder = (base / "alis-companion").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / "tool_receipts.json"


def _load_store() -> dict:
    path = _state_file()
    if not path.is_file():
        return {"proposals": {}, "receipts": [], "fileReceipts": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError()
        proposals = raw.get("proposals", {})
        receipts = raw.get("receipts", [])
        file_receipts = raw.get("fileReceipts", [])
        if not isinstance(proposals, dict) or not isinstance(receipts, list) or not isinstance(file_receipts, list):
            raise ValueError()
        return {"proposals": proposals, "receipts": receipts, "fileReceipts": file_receipts}
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


def _clean_line(text: str, limit: int = 200) -> str:
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
    if kind == "active_app":
        allow_screenshot = options.get("allowScreenshot") is not False
        observed = observe_active_window(
            allow_screenshot=allow_screenshot,
            previous_window_id=options.get("previousWindowId"),
            now_ms=options.get("nowMs"),
        )
        return {
            "kind": "active_app",
            "hostAvailable": bool(observed["hostAvailable"]),
            "packageId": _clean_line(observed["packageId"], 120) or "desktop",
            "version": _clean_line(observed["version"], 80) or "niri_metadata",
            "localOnlyDefault": True,
            "capabilities": [str(c)[:80] for c in observed.get("capabilities", [])[:16]],
            "headings": [_clean_line(h, 155) for h in observed.get("headings", [])[:16]],
        }
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
            "packageId": _clean_line(manifest.get("id", "alis-companion"), 64),
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
    rag = retrieve_rag(
        prompt,
        companion_root=companion_root,
        extra_dirs=options.get("extraDirs") if isinstance(options.get("extraDirs"), list) else None,
        token_budget=int(options.get("tokenBudget", 480)),
        page=int(options.get("page", 1)),
        page_size=int(options.get("pageSize", 3)),
    )
    snippets = [_clean_line(s, 300) for s in rag["snippets"][:3]]
    total_chars = sum(len(s) for s in snippets)
    return {
        "allowed": True,
        "route": "brain",
        "ragUsed": bool(snippets),
        "reason": "grounded_doc_snippets" if snippets else "no_matching_snippets",
        "snippets": snippets,
        "estimatedTokens": max(1, min(16384, (len(prompt) + total_chars) // 4)),
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


def resolve_candidate_path(raw_path: str, last_dir: str = "") -> Path:
    """Resolve a user or system path candidate without raising outside-home errors yet."""
    text = str(raw_path or "").strip().strip("'\"")
    if not text or "\x00" in text or "\r" in text or "\n" in text:
        raise ToolError("invalid_path", "Specify a valid file or folder path.")
    home = Path.home().resolve()
    if text.startswith("file://"):
        text = text[7:]
    if text in ("~", "$HOME") or text.lower() in ("home", "user home", "thu muc home", "thư mục home"):
        return home
    cand = Path(text).expanduser()
    if not cand.is_absolute():
        if last_dir:
            ld = Path(last_dir).expanduser().resolve(strict=False)
            ld_cand = ld / cand
            if ld_cand.exists() or ld_cand.is_symlink():
                return ld_cand.resolve(strict=False)
        home_raw = home / cand
        desktop_raw = home / "Desktop" / cand
        if home_raw.exists() or home_raw.is_symlink():
            chosen_raw = home_raw
        elif desktop_raw.exists() or desktop_raw.is_symlink():
            chosen_raw = desktop_raw
        elif last_dir:
            chosen_raw = Path(last_dir).expanduser().resolve(strict=False) / cand
        elif (home / "Desktop").is_dir() and "/" not in text:
            chosen_raw = desktop_raw
        else:
            chosen_raw = home_raw
    else:
        chosen_raw = cand
    return chosen_raw.resolve(strict=False)


def _resolve_user_path(raw_path: str, allow_missing: bool = False, last_dir: str = "", allow_system: bool = False) -> Path:
    home = Path.home().resolve()
    resolved = resolve_candidate_path(raw_path, last_dir=last_dir)
    inside_home = (resolved == home) or resolved.is_relative_to(home)
    if not inside_home and not allow_system:
        raise ToolError(
            "system_path_requires_sudo",
            f"Path '{resolved}' is a system path outside your Home directory and requires your permission and sudo password.",
        )
    if not allow_missing and not resolved.exists():
        raise ToolError("path_not_found", f"Path not found: {resolved.name or resolved}")
    return resolved


def _file_sha256(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _atomic_write_text(target: Path, content: str) -> str:
    if target.is_symlink():
        target = target.resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        if target.is_file():
            st_mode = target.stat().st_mode & 0o777
            os.fchmod(fd, st_mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    after_sha = _file_sha256(target)
    expected_sha = hashlib.sha256(payload).hexdigest()
    if after_sha != expected_sha:
        raise ToolError("readback_mismatch", "After-write readback digest did not match written bytes.")
    return after_sha


def _record_file_receipt(store: dict, op: str, target_path: str, dest_path: str, before_sha: str, after_sha: str, backup_text: str | None, before_existed: bool, now: int) -> str:
    core = {
        "op": op,
        "targetPath": target_path,
        "destPath": dest_path,
        "beforeSha256": before_sha,
        "afterSha256": after_sha,
        "appliedAtMs": now,
    }
    digest = _digest(core)
    rid = "rcpt_" + digest[:16]
    entry = {
        **core,
        "receiptId": rid,
        "digest": digest,
        "beforeExisted": before_existed,
        "backupText": backup_text if (backup_text is not None and len(backup_text) <= 262144) else None,
        "rolledBack": False,
    }
    store["fileReceipts"] = (store.get("fileReceipts", []) + [entry])[-MAX_FILE_RECEIPTS:]
    return rid


def _sync_session_context_from_op(target_path: Path, is_dir: bool, candidate_files: list[str] | None = None) -> None:
    try:
        import run_store
        ctx = run_store.load_session_context()
        if is_dir:
            ctx["lastDirectory"] = str(target_path)
            if candidate_files is not None:
                ctx["recentCandidates"] = candidate_files[:20]
                if len(candidate_files) == 1:
                    ctx["lastFile"] = candidate_files[0]
        else:
            ctx["lastFile"] = str(target_path)
            ctx["lastDirectory"] = str(target_path.parent)
            ctx["recentCandidates"] = [str(target_path)]
        run_store.save_session_context(ctx)
    except Exception:
        pass


def _format_dir_entry(entry: Path) -> tuple[bool, bool, str]:
    """Return (is_dir, is_hidden, formatted_line) for a directory entry, handling symlinks safely."""
    name = entry.name
    is_hidden = name.startswith(".")
    is_link = False
    is_dir = False
    try:
        is_link = entry.is_symlink()
        is_dir = entry.is_dir()
    except OSError:
        pass
    tag = "[dir]" if is_dir else "[file]"
    if is_link:
        try:
            link_target = os.readlink(entry)
            return is_dir, is_hidden, _clean_line(f"{tag} {name} -> {link_target}", 200)
        except OSError:
            return is_dir, is_hidden, _clean_line(f"{tag} {name}", 200)
    return is_dir, is_hidden, _clean_line(f"{tag} {name}", 200)


def _list_directory_entries(target: Path, query: str = "", include_hidden: bool = False, page: int = 1, page_size: int = 500) -> dict:
    raw_entries = list(target.iterdir())
    formatted = []
    hidden_count = 0
    candidate_files = []
    for e in raw_entries:
        if query and query not in e.name.lower():
            continue
        is_dir, is_hidden, line = _format_dir_entry(e)
        if is_hidden and not include_hidden and not target.name.startswith("."):
            hidden_count += 1
            continue
        formatted.append((not is_dir, is_hidden, e.name.lower(), line, e))
        if not is_dir:
            candidate_files.append(str(e.resolve(strict=False)))

    # If the folder only has hidden files, show them automatically
    if not formatted and hidden_count > 0:
        for e in raw_entries:
            if query and query not in e.name.lower():
                continue
            is_dir, is_hidden, line = _format_dir_entry(e)
            formatted.append((not is_dir, is_hidden, e.name.lower(), line, e))
            if not is_dir:
                candidate_files.append(str(e.resolve(strict=False)))
        hidden_count = 0

    formatted.sort(key=lambda item: (item[0], item[1], item[2]))
    total_shown_pool = len(formatted)
    start_idx = (page - 1) * page_size
    sliced = formatted[start_idx:start_idx + page_size]
    lines = [item[3] for item in sliced]

    _sync_session_context_from_op(target, is_dir=True, candidate_files=candidate_files)

    hidden_note = f" (+{hidden_count} hidden dot-items; say 'list all' to include)" if hidden_count > 0 else ""
    display_name = target.name or str(target)
    return {
        "op": "access",
        "status": "completed",
        "confirmStage": 0,
        "targetPath": str(target),
        "destPath": "",
        "totalItems": total_shown_pool,
        "hiddenCount": hidden_count,
        "summary": _clean_line(
            f"Folder {display_name}: {len(lines)} of {total_shown_pool} item(s){hidden_note}.",
            240,
        ),
        "lines": lines or ["(empty folder)"],
    }


_SUDO_HELPER_CODE = r"""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

req = json.loads(sys.argv[1])
op = req.get("op", "access")
target = Path(req["targetPath"])
dest = Path(req["destPath"]) if req.get("destPath") else None
include_hidden = bool(req.get("includeHidden", False))
query = str(req.get("query", "")).lower()

def fmt_entry(e):
    is_link = e.is_symlink()
    is_dir = False
    try: is_dir = e.is_dir()
    except OSError: pass
    tag = "[dir]" if is_dir else "[file]"
    if is_link:
        try: return is_dir, e.name.startswith("."), f"{tag} {e.name} -> {os.readlink(e)}"[:200]
        except OSError: pass
    return is_dir, e.name.startswith("."), f"{tag} {e.name}"[:200]

if op in ("access", "list", "search"):
    if not target.exists():
        print(json.dumps({"ok": False, "code": "path_not_found", "message": f"Path not found: {target}"}))
        sys.exit(0)
    if target.is_dir():
        items = []
        hidden = 0
        for e in target.iterdir():
            if query and query not in e.name.lower(): continue
            is_d, is_h, ln = fmt_entry(e)
            if is_h and not include_hidden and not target.name.startswith("."):
                hidden += 1
                continue
            items.append((not is_d, is_h, e.name.lower(), ln))
        items.sort()
        lines = [x[3] for x in items[:500]]
        h_note = f" (+{hidden} hidden)" if hidden else ""
        print(json.dumps({
            "ok": True,
            "result": {
                "op": "access",
                "status": "completed",
                "confirmStage": 0,
                "targetPath": str(target),
                "destPath": "",
                "summary": f"System folder {target.name or target}: {len(lines)} item(s){h_note}."[:240],
                "lines": lines or ["(empty folder)"],
            }
        }))
        sys.exit(0)
    else:
        raw = target.read_text(encoding="utf-8", errors="replace")
        all_lines = [ln.strip()[:200] for ln in raw.splitlines() if ln.strip()][:200]
        sha = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
        print(json.dumps({
            "ok": True,
            "result": {
                "op": "access",
                "status": "completed",
                "confirmStage": 0,
                "targetPath": str(target),
                "destPath": "",
                "summary": f"Read system file {target.name} ({target.stat().st_size} bytes, sha256:{sha})."[:240],
                "lines": all_lines or ["(empty file)"],
            }
        }))
        sys.exit(0)
elif op in ("edit", "create", "patch"):
    content = str(req.get("content", ""))
    mode = str(req.get("mode", "write"))
    existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    if mode == "append":
        sep = "\n" if existing and not existing.endswith("\n") else ""
        updated = existing + sep + content + ("\n" if not content.endswith("\n") else "")
    else:
        updated = content
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(updated, encoding="utf-8")
    sha = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
    preview = [ln.strip()[:200] for ln in updated.splitlines() if ln.strip()][:20]
    print(json.dumps({
        "ok": True,
        "result": {
            "op": "edit",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(target),
            "destPath": "",
            "summary": f"Updated system file {target.name} (sha256:{sha})."[:240],
            "lines": preview or ["(file saved)"],
        }
    }))
    sys.exit(0)
elif op == "delete":
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    print(json.dumps({
        "ok": True,
        "result": {
            "op": "delete",
            "status": "completed",
            "confirmStage": 2,
            "targetPath": str(target),
            "destPath": "",
            "summary": f"Deleted system path {target}."[:240],
            "lines": [f"Removed: {target}"],
        }
    }))
    sys.exit(0)
elif op == "run":
    cmd = str(req.get("content") or req.get("targetPath") or "")
    proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20)
    lines = [ln.strip()[:200] for ln in (proc.stdout or "").splitlines() if ln.strip()][:50]
    st = "completed" if proc.returncode == 0 else "failed"
    print(json.dumps({
        "ok": True,
        "result": {
            "op": "run",
            "status": st,
            "confirmStage": 0,
            "targetPath": str(target),
            "destPath": "",
            "summary": f"Ran system command via sudo ({st}, exit {proc.returncode})."[:240],
            "lines": lines or [f"(exit {proc.returncode})"],
        }
    }))
    sys.exit(0)
"""


def execute_system_sudo_op(options: dict, sudo_password: str) -> dict:
    """Execute a user-approved operation on a System path outside $HOME using sudo -S -k."""
    pwd = str(sudo_password or "")
    if not pwd:
        raise ToolError("sudo_password_required", "Please enter your sudo password to access system paths outside Home.")

    # Support deterministic unit testing without requiring root on CI/test runners
    test_expected_pwd = os.environ.get("AQUA_TEST_SUDO_PASSWORD", "")
    if test_expected_pwd:
        if pwd != test_expected_pwd:
            raise ToolError("sudo_auth_failed", "Incorrect sudo password or authentication failed.")
        return agent_file_op({**options, "allowSystem": True})

    op = str(options.get("op", "access")).strip().lower()
    raw_target = str(options.get("targetPath", "")).strip()
    resolved = resolve_candidate_path(raw_target)
    req_payload = {
        "op": op,
        "targetPath": str(resolved),
        "destPath": str(options.get("destPath", "")),
        "content": str(options.get("content", "")),
        "mode": str(options.get("mode", "write")),
        "query": str(options.get("query", "")),
        "includeHidden": bool(options.get("includeHidden", False)),
    }
    try:
        proc = subprocess.run(
            ["/usr/bin/sudo", "-S", "-k", "-p", "", "--", "/usr/bin/python3", "-c", _SUDO_HELPER_CODE, json.dumps(req_payload)],
            input=(pwd + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=25,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("sudo_timed_out", "System sudo operation timed out.") from exc
    except OSError as exc:
        raise ToolError("sudo_unavailable", "sudo is unavailable on this system.") from exc

    stderr_text = proc.stderr.decode("utf-8", errors="replace").strip()
    stdout_text = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        if any(k in stderr_text.lower() for k in ("sorry, try again", "incorrect password", "authentication failure", "no password was provided")):
            raise ToolError("sudo_auth_failed", "Incorrect sudo password. Please try again or reply 'cancel'.")
        raise ToolError("system_op_failed", _clean_line(stderr_text or f"sudo exited with code {proc.returncode}", 160))

    try:
        last_line = [ln for ln in stdout_text.splitlines() if ln.strip()][-1]
        parsed = json.loads(last_line)
    except (IndexError, ValueError) as exc:
        raise ToolError("system_op_failed", "Invalid output from privileged helper.") from exc

    if not parsed.get("ok"):
        raise ToolError(str(parsed.get("code") or "system_op_failed"), str(parsed.get("message") or "System operation failed."))
    return parsed["result"]


def agent_file_op(options: dict) -> dict:
    op = str(options.get("op", "")).strip().lower()
    valid_ops = (
        "access", "list", "search", "create", "edit", "patch",
        "copy", "cut", "delete", "cancel_delete", "rollback", "run",
    )
    if op not in valid_ops:
        raise ToolError("invalid_op", f"Unsupported agent file operation: {op}")
    now = _now_ms(options.get("nowMs"))
    allow_system = bool(options.get("allowSystem", False))
    last_dir = str(options.get("lastDirectory") or "").strip()
    store = _load_store()
    proposals = store["proposals"]

    if op == "cancel_delete":
        proposals.pop("delete_pending", None)
        _save_store(store)
        return {
            "op": "cancel_delete",
            "status": "cancelled",
            "confirmStage": 0,
            "targetPath": "",
            "destPath": "",
            "summary": "Delete request cancelled. No files were deleted.",
            "lines": [],
        }

    if op == "rollback":
        req_id = str(options.get("receiptId", "")).strip()
        file_receipts = store.get("fileReceipts", [])
        matched = None
        for r in reversed(file_receipts):
            if req_id and r.get("receiptId") != req_id:
                continue
            if not r.get("rolledBack"):
                matched = r
                break
        if not matched:
            raise ToolError("receipt_missing", "No matching recoverable file receipt found.")
        rop = matched.get("op")
        tpath = Path(matched["targetPath"])
        dpath = Path(matched["destPath"]) if matched.get("destPath") else None
        if rop in ("edit", "create", "patch"):
            if not matched.get("beforeExisted"):
                if tpath.is_file() and not tpath.is_symlink():
                    tpath.unlink()
            elif matched.get("backupText") is not None:
                _atomic_write_text(tpath, matched["backupText"])
            else:
                raise ToolError("rollback_unavailable", "Backup payload not available for this file receipt.")
        elif rop == "delete":
            if matched.get("backupText") is not None:
                _atomic_write_text(tpath, matched["backupText"])
            else:
                raise ToolError("rollback_unavailable", "Deleted directory or binary file cannot be restored.")
        elif rop == "copy" and dpath and dpath.is_file() and not dpath.is_symlink():
            dpath.unlink()
        elif rop == "cut" and dpath and dpath.exists() and not tpath.exists():
            shutil.move(str(dpath), str(tpath))
        matched["rolledBack"] = True
        _save_store(store)
        return {
            "op": "rollback",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(tpath),
            "destPath": str(dpath) if dpath else "",
            "summary": _clean_line(f"Rolled back {rop} on {tpath.name} ({matched['receiptId']}).", 240),
            "lines": [_clean_line(f"Restored receipt: {matched['receiptId']}", 180)],
        }

    raw_target = str(options.get("targetPath", "")).strip()
    if op == "run":
        cmd_text = str(options.get("content", "")).strip() or raw_target
        if not cmd_text or "\x00" in cmd_text:
            raise ToolError("invalid_script", "Specify a script path or command to run.")
        home = Path.home().resolve()
        work_dir = Path(last_dir) if (last_dir and Path(last_dir).is_dir()) else ((home / "Desktop") if (home / "Desktop").is_dir() else home)
        target_label = cmd_text
        resolved_script = None
        first_token = cmd_text.split()[0] if cmd_text.split() else ""
        try:
            cand = _resolve_user_path(first_token, allow_missing=False, last_dir=last_dir, allow_system=allow_system)
            if cand.is_file():
                resolved_script = cand
                work_dir = cand.parent
                target_label = cand.name
        except ToolError as exc:
            if exc.code == "system_path_requires_sudo":
                raise
            resolved_script = None

        timeout_sec = float(options.get("timeoutSec") or 15.0)
        try:
            if resolved_script is not None and len(cmd_text.split()) == 1:
                suffix = resolved_script.suffix.lower()
                if suffix == ".py":
                    argv = ["/usr/bin/python3", str(resolved_script)]
                elif suffix in (".js", ".cjs", ".mjs"):
                    argv = ["/usr/bin/node", str(resolved_script)]
                else:
                    argv = ["/usr/bin/bash", str(resolved_script)]
                ran = run_sandboxed(
                    argv,
                    cwd=work_dir,
                    writable_dirs=[home, work_dir],
                    readonly_dirs=[home],
                    allow_network=bool(options.get("allowNetwork", False)),
                    timeout_sec=timeout_sec,
                    shell=False,
                )
            else:
                ran = run_sandboxed(
                    cmd_text,
                    cwd=work_dir,
                    writable_dirs=[home, work_dir],
                    readonly_dirs=[home],
                    allow_network=bool(options.get("allowNetwork", False)),
                    timeout_sec=timeout_sec,
                    shell=True,
                )
        except SandboxError as exc:
            raise ToolError(exc.code, exc.message) from exc

        out_lines = [_clean_line(ln, 200) for ln in ran["lines"] if ln.strip()][:200]
        return {
            "op": "run",
            "status": ran["status"],
            "confirmStage": 0,
            "targetPath": str(resolved_script or work_dir)[:400],
            "destPath": "",
            "summary": _clean_line(f"Ran '{target_label}' ({ran['status']}, exit {ran['exitCode']}).", 240),
            "lines": out_lines or [f"({ran['status']} with exit code {ran['exitCode']})"],
        }

    if op == "access" and not raw_target:
        observed = observe_active_window(
            allow_screenshot=options.get("allowScreenshot") is not False,
            previous_window_id=options.get("previousWindowId"),
            now_ms=now,
        )
        obs_meta = observed["observation"]
        app_id = _clean_line(obs_meta.get("appId") or "desktop", 80) or "desktop"
        title = _clean_line(obs_meta.get("title") or "Active workspace", 120) or "Active workspace"
        method = _clean_line(obs_meta.get("source") or "niri_metadata", 60)
        out_lines = [
            _clean_line(f"app:{app_id} | {title} | source:{method}", 180),
        ]
        for ln in observed.get("headings", [])[:16]:
            cleaned = _clean_line(ln, 180)
            if cleaned and cleaned not in out_lines:
                out_lines.append(cleaned)
        return {
            "op": "access",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": "",
            "destPath": "",
            "summary": _clean_line(f"Observed active window ({app_id}, {method}).", 240),
            "lines": out_lines[:20],
        }

    if op in ("access", "list", "search"):
        target = _resolve_user_path(raw_target, allow_missing=False, last_dir=last_dir, allow_system=allow_system)
        query = str(options.get("query", "")).strip().lower()
        include_hidden = bool(options.get("includeHidden", False))
        page = max(1, int(options.get("page") or 1))
        page_size = max(1, min(500, int(options.get("pageSize") or 500)))

        if target.is_dir():
            return _list_directory_entries(
                target,
                query=query,
                include_hidden=include_hidden,
                page=page,
                page_size=page_size,
            )

        _sync_session_context_from_op(target, is_dir=False)
        size = target.stat().st_size
        if size > 512 * 1024:
            return {
                "op": "access",
                "status": "completed",
                "confirmStage": 0,
                "targetPath": str(target),
                "destPath": "",
                "summary": _clean_line(f"File {target.name} ({size} bytes, binary/large).", 240),
                "lines": [_clean_line(f"Path: {target}", 180), f"Size: {size} bytes"],
            }
        raw_text = target.read_text(encoding="utf-8", errors="replace")
        all_lines = raw_text.splitlines()
        start_line = max(1, int(options.get("startLine") or 1))
        end_line = min(len(all_lines), int(options.get("endLine") or (start_line + 199)))
        sliced = all_lines[start_line - 1:end_line] if all_lines else []
        lines = [_clean_line(ln, 200) for ln in sliced if ln.strip()][:200]
        return {
            "op": "access",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(target),
            "destPath": "",
            "summary": _clean_line(f"Read {target.name} ({size} bytes, sha256:{_file_sha256(target)[:12]}).", 240),
            "lines": lines or ["(empty file)"],
        }

    if op in ("edit", "create", "patch"):
        target = _resolve_user_path(raw_target, allow_missing=True, last_dir=last_dir, allow_system=allow_system)
        if target.exists() and target.is_dir():
            raise ToolError("is_directory", "Cannot edit a folder as a text file.")
        before_existed = target.is_file()
        if op == "create" and before_existed and options.get("overwrite") is not True:
            raise ToolError("file_already_exists", f"File '{target.name}' already exists; confirm overwrite or use edit.")

        before_sha = _file_sha256(target) if before_existed else ""
        expected_sha = str(options.get("expectedSha256", "")).strip()
        if expected_sha and before_existed and before_sha != expected_sha and not before_sha.startswith(expected_sha):
            raise ToolError(
                "conflict_detected",
                f"File '{target.name}' was modified externally since last read; refusing to overwrite.",
            )

        mode = "replace" if op == "patch" else str(options.get("mode", "write")).strip().lower()
        content = str(options.get("content", ""))
        if len(content) > 16000:
            raise ToolError("content_too_large", "Edit content exceeds the 16000-character limit.")

        existing = target.read_text(encoding="utf-8", errors="replace") if before_existed else ""
        if mode == "append":
            sep = "\n" if existing and not existing.endswith("\n") else ""
            updated = existing + sep + content + ("\n" if not content.endswith("\n") else "")
        elif mode == "replace":
            search_text = str(options.get("searchText", ""))
            if not search_text or search_text not in existing:
                raise ToolError("search_not_found", "Target text to replace was not found in file.")
            updated = existing.replace(search_text, content, 1)
        else:
            updated = content

        after_sha = _atomic_write_text(target, updated)
        rid = _record_file_receipt(
            store,
            op="edit" if op != "create" else "create",
            target_path=str(target),
            dest_path="",
            before_sha=before_sha,
            after_sha=after_sha,
            backup_text=existing if before_existed else None,
            before_existed=before_existed,
            now=now,
        )
        _save_store(store)
        _sync_session_context_from_op(target, is_dir=False)
        preview = [_clean_line(ln, 200) for ln in updated.splitlines() if ln.strip()][:20]
        return {
            "op": "edit",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(target),
            "destPath": "",
            "summary": _clean_line(
                f"Updated {target.name} ({len(updated.encode('utf-8'))} bytes, receipt {rid}).",
                240,
            ),
            "lines": preview or ["(file saved)"],
        }

    if op in ("copy", "cut"):
        src = _resolve_user_path(raw_target, allow_missing=False, last_dir=last_dir, allow_system=allow_system)
        raw_dest = str(options.get("destPath", "")).strip()
        if not raw_dest:
            raise ToolError("missing_destination", "Specify a destination path for copy or cut.")
        dst = _resolve_user_path(raw_dest, allow_missing=True, last_dir=last_dir, allow_system=allow_system)
        if dst.exists() and dst.is_dir():
            dst = (dst / src.name).resolve()
        if dst.exists() and options.get("overwrite") is False:
            raise ToolError("destination_exists", f"Destination '{dst.name}' already exists.")

        before_sha = _file_sha256(src) if src.is_file() else ""
        dst_existed = dst.is_file()
        dst_backup = dst.read_text(encoding="utf-8", errors="replace") if (dst_existed and dst.stat().st_size <= 262144) else None
        dst.parent.mkdir(parents=True, exist_ok=True)
        if op == "copy":
            if src.is_dir():
                shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
            else:
                fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.name}.", dir=str(dst.parent))
                os.close(fd)
                tmp_dst = Path(tmp_name)
                try:
                    shutil.copy2(src, tmp_dst, follow_symlinks=True)
                    os.replace(tmp_dst, dst)
                except Exception:
                    tmp_dst.unlink(missing_ok=True)
                    raise
            verb = "Copied"
        else:
            shutil.move(str(src), str(dst))
            verb = "Moved (cut)"
        after_sha = _file_sha256(dst) if dst.is_file() else ""
        rid = _record_file_receipt(
            store,
            op=op,
            target_path=str(src),
            dest_path=str(dst),
            before_sha=before_sha,
            after_sha=after_sha,
            backup_text=dst_backup,
            before_existed=dst_existed,
            now=now,
        )
        _save_store(store)
        return {
            "op": op,
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(src),
            "destPath": str(dst),
            "summary": _clean_line(f"{verb} {src.name} -> {dst} (receipt {rid})", 240),
            "lines": [_clean_line(f"From: {src}", 180), _clean_line(f"To: {dst}", 180)],
        }

    # op == "delete": requires 2-step confirmation (confirmStep 0 -> 1 -> 2)
    target = _resolve_user_path(raw_target, allow_missing=False, last_dir=last_dir, allow_system=allow_system)
    step = int(options.get("confirmStep") or 0)
    pending_del = proposals.get("delete_pending")
    if step <= 0:
        proposals["delete_pending"] = {
            "targetPath": str(target),
            "stage": 1,
            "expiresMs": now + PROPOSAL_TTL_MS,
        }
        _save_store(store)
        return {
            "op": "delete",
            "status": "confirm_1_required",
            "confirmStage": 1,
            "targetPath": str(target),
            "destPath": "",
            "summary": _clean_line(f"Confirm 1/2: Delete '{target.name}'? Click Confirm (1/2) to continue.", 240),
            "lines": [_clean_line(f"Target: {target}", 180)],
        }
    if not isinstance(pending_del, dict) or pending_del.get("targetPath") != str(target) or now > int(pending_del.get("expiresMs", 0)):
        raise ToolError("delete_session_expired", "Delete confirmation expired or changed. Start delete again.")
    if step == 1:
        pending_del["stage"] = 2
        pending_del["expiresMs"] = now + PROPOSAL_TTL_MS
        _save_store(store)
        return {
            "op": "delete",
            "status": "confirm_2_required",
            "confirmStage": 2,
            "targetPath": str(target),
            "destPath": "",
            "summary": _clean_line(f"Confirm 2/2 (Final): Permanently delete '{target.name}'? Confirm a 2nd time to delete.", 240),
            "lines": [_clean_line(f"Final check: {target}", 180)],
        }
    if step == 2:
        if int(pending_del.get("stage", 0)) < 2:
            raise ToolError("second_confirmation_required", "Delete requires 2 confirmations (step 1 before step 2).")
        before_sha = _file_sha256(target) if target.is_file() else ""
        backup_text = None
        if target.is_file() and target.stat().st_size <= 262144:
            backup_text = target.read_text(encoding="utf-8", errors="replace")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        proposals.pop("delete_pending", None)
        rid = _record_file_receipt(
            store,
            op="delete",
            target_path=str(target),
            dest_path="",
            before_sha=before_sha,
            after_sha="",
            backup_text=backup_text,
            before_existed=True,
            now=now,
        )
        _save_store(store)
        return {
            "op": "delete",
            "status": "completed",
            "confirmStage": 2,
            "targetPath": str(target),
            "destPath": "",
            "summary": _clean_line(f"Deleted '{target.name}' after 2x confirmation (receipt {rid}).", 240),
            "lines": [_clean_line(f"Removed: {target}", 180)],
        }
    raise ToolError("invalid_confirm_step", "Invalid delete confirmation step.")
