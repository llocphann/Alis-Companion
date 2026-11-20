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
import difflib
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
from sandbox_runner import SandboxError, run_sandboxed, run_user_command
from screen_context import observe_active_window
from spill_store import SpillError, maybe_spill_tool_output, read_spill as read_spill_store

PROPOSAL_TTL_MS = 120_000
MAX_PROPOSALS = 64
MAX_RECEIPTS = 128
MAX_FILE_RECEIPTS = 64
SMALL_FILE_MAX_BYTES = 24 * 1024
SMALL_FILE_MAX_LINES = 300

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
            "version": _clean_line(manifest.get("version", "1.0.0"), 32),
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


_SEARCH_EXCLUDE_DIRS = frozenset({
    ".git", "node_modules", ".cache", ".local", ".cargo", ".rustup",
    ".lmstudio", ".mozilla", ".gemini", "__pycache__", ".venv", "venv",
    ".npm", ".pnpm-store", ".gradle", ".m2", "target", "dist", "build",
    ".vscode", ".vscode-oss", ".cursor", ".copilot", ".codex", ".nvm",
    ".pyenv", ".steam", ".var", ".Trash", ".wine",
})


def search_files_in_home(
    query: str,
    last_dir: str = "",
    max_results: int = 25,
    timeout_sec: float = 2.5,
) -> list[Path]:
    """Search $HOME for files or folders matching `query` by exact or partial filename."""
    text = str(query or "").strip().strip("'\"`")
    if not text or "\x00" in text or "\r" in text or "\n" in text:
        return []
    home = Path.home().resolve()
    q_name = Path(text).name or text
    q_lower = q_name.lower()
    if not q_lower:
        return []

    found_raw: list[Path] = []
    obs_vault = home / "Shared_Documents" / "Obsidian-Vault"
    seen_index_by_real: dict[str, int] = {}

    def _add(p: Path) -> None:
        try:
            rp = p.resolve(strict=False)
            if not rp.exists() or not ((rp == home) or rp.is_relative_to(home)):
                return
            cand_p = p if ((p == home or p.is_relative_to(home)) and p.exists()) else rp
            real_key = str(rp)
            if real_key in seen_index_by_real:
                idx = seen_index_by_real[real_key]
                prev_p = found_raw[idx]
                if obs_vault.exists() and cand_p.is_relative_to(obs_vault) and not prev_p.is_relative_to(obs_vault):
                    found_raw[idx] = cand_p
                return
            seen_index_by_real[real_key] = len(found_raw)
            found_raw.append(cand_p)
        except OSError:
            pass

    try:
        direct = resolve_candidate_path(text, last_dir=last_dir)
        if direct.exists():
            _add(direct)
    except ToolError:
        pass

    priority_roots: list[Path] = []
    if last_dir:
        ld_p = Path(last_dir).expanduser().resolve(strict=False)
        if ld_p.is_dir() and ((ld_p == home) or ld_p.is_relative_to(home)):
            priority_roots.append(ld_p)
    for sub in ("Shared_Documents", "Desktop", "Documents", "Projects", "Downloads", ".obsidian", ".config"):
        cand_root = home / sub
        if cand_root.is_dir() and cand_root not in priority_roots:
            priority_roots.append(cand_root)

    fd_bin = shutil.which("fd") or shutil.which("fdfind")
    used_fd = False
    if fd_bin:
        base_flags = [fd_bin, "-H", "-I", "-L"]
        for ex in sorted(_SEARCH_EXCLUDE_DIRS):
            base_flags.extend(["-E", ex])
        # Pass 1: priority user/vault roots so exact matches inside Obsidian-Vault / active project are never crowded out
        if priority_roots:
            cmd_prio = [
                *base_flags,
                "--max-results",
                str(max(150, max_results * 8)),
                "-F",
                "--",
                q_name,
                *[str(r) for r in priority_roots],
            ]
            try:
                proc_p = subprocess.run(
                    cmd_prio,
                    capture_output=True,
                    text=True,
                    timeout=min(1.5, timeout_sec),
                    check=False,
                )
                if proc_p.returncode in (0, 1):
                    used_fd = True
                    for ln in proc_p.stdout.splitlines():
                        ln_s = ln.strip()
                        if ln_s:
                            _add(Path(ln_s))
            except (OSError, subprocess.SubprocessError):
                pass

        # Pass 2: full $HOME sweep if still needed
        cmd_home = [
            *base_flags,
            "--max-results",
            str(max(300, max_results * 12)),
            "-F",
            "--",
            q_name,
            str(home),
        ]
        try:
            proc = subprocess.run(
                cmd_home,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            if proc.returncode in (0, 1):
                used_fd = True
                for ln in proc.stdout.splitlines():
                    ln_s = ln.strip()
                    if ln_s:
                        _add(Path(ln_s))
        except (OSError, subprocess.SubprocessError):
            pass

        # Pass 3: separator-flexible search (e.g. TTSP_BIEUMAU -> TTSP.Bieumau.TTBDNVSP.2026) if literal search had 0 hits
        if not found_raw and re.search(r"[._\-\s]+", q_name):
            sep_tokens = [re.escape(tok) for tok in re.split(r"[._\-\s]+", q_name) if tok]
            if len(sep_tokens) >= 2:
                sep_pattern = r"[._\-\s]*".join(sep_tokens)
                sep_passes = [[str(r) for r in priority_roots], [str(home)]] if priority_roots else [[str(home)]]
                for sep_roots in sep_passes:
                    if found_raw or not sep_roots:
                        break
                    cmd_sep = [
                        *base_flags,
                        "-i",
                        "--max-results",
                        str(max(100, max_results * 4)),
                        "--",
                        sep_pattern,
                        *sep_roots,
                    ]
                    try:
                        proc_sep = subprocess.run(
                            cmd_sep,
                            capture_output=True,
                            text=True,
                            timeout=min(1.5, timeout_sec),
                            check=False,
                        )
                        if proc_sep.returncode in (0, 1):
                            for ln in proc_sep.stdout.splitlines():
                                ln_s = ln.strip()
                                if ln_s:
                                    _add(Path(ln_s))
                    except (OSError, subprocess.SubprocessError):
                        pass

    q_norm = re.sub(r"[._\-\s]+", "", q_lower)
    if not used_fd:
        deadline = time.monotonic() + timeout_sec
        walk_roots = list(priority_roots)
        if home not in walk_roots:
            walk_roots.append(home)

        visited_dirs: set[str] = set()
        for root_dir in walk_roots:
            if time.monotonic() > deadline or len(found_raw) >= max_results * 4:
                break
            for cur_root, dirs, files in os.walk(root_dir, topdown=True, followlinks=True):
                if time.monotonic() > deadline or len(found_raw) >= max_results * 4:
                    break
                cur_key = str(Path(cur_root).resolve(strict=False))
                if cur_key in visited_dirs:
                    dirs[:] = []
                    continue
                visited_dirs.add(cur_key)
                dirs[:] = [
                    d for d in dirs
                    if d not in _SEARCH_EXCLUDE_DIRS
                ]
                for entry in files + dirs:
                    elower = entry.lower()
                    if elower == q_lower or q_lower in elower:
                        _add(Path(cur_root) / entry)
                    elif len(q_norm) >= 4 and re.search(r"[._\-\s]+", q_lower):
                        enorm = re.sub(r"[._\-\s]+", "", elower)
                        if q_norm in enorm:
                            _add(Path(cur_root) / entry)

    ld_resolved = Path(last_dir).expanduser().resolve(strict=False) if last_dir else None
    obs_vault = home / "Shared_Documents" / "Obsidian-Vault"

    def _rank(p: Path) -> tuple[int, int, int, int, str]:
        pname = p.name
        plower = pname.lower()
        exact_tier = 0 if pname == q_name else (1 if plower == q_lower else 2)
        in_last_dir = 0 if (ld_resolved and (p == ld_resolved or p.is_relative_to(ld_resolved))) else 1
        in_vault = 0 if (obs_vault.exists() and p.is_relative_to(obs_vault)) else 1
        return (exact_tier, in_last_dir, in_vault, len(p.parts), str(p).lower())

    found_raw.sort(key=_rank)
    return found_raw[:max_results]


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


def _file_receipt_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    folder = (base / "alis-companion" / "file-receipts").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _compute_diff_metrics(before_text: str | None, after_text: str | None, file_name: str = "file") -> dict:
    old_lines = (before_text or "").splitlines(keepends=True) if before_text is not None else []
    new_lines = (after_text or "").splitlines(keepends=True) if after_text is not None else []
    diff_gen = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file_name}",
            tofile=f"b/{file_name}",
        )
    )
    added = sum(1 for ln in diff_gen if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_gen if ln.startswith("-") and not ln.startswith("---"))
    preview_lines = [ln.rstrip("\r\n") for ln in diff_gen[:40]]
    return {
        "addedLines": added,
        "removedLines": removed,
        "diffPreview": "\n".join(preview_lines),
    }


def _record_file_receipt(
    store: dict,
    op: str,
    target_path: str,
    dest_path: str,
    before_sha: str,
    after_sha: str,
    backup_text: str | None,
    before_existed: bool,
    now: int,
    *,
    new_content: str | None = None,
) -> str:
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
    diff_metrics = _compute_diff_metrics(
        backup_text if before_existed else "",
        new_content if new_content is not None else "",
        Path(target_path).name or "file",
    )
    safe_backup = backup_text if (backup_text is not None and len(backup_text) <= 262144) else None
    entry = {
        **core,
        "receiptId": rid,
        "path": target_path,
        "digest": digest,
        "beforeExisted": before_existed,
        "existedBefore": before_existed,
        "backupText": safe_backup,
        "oldContent": safe_backup,
        "addedLines": diff_metrics["addedLines"],
        "removedLines": diff_metrics["removedLines"],
        "diffPreview": diff_metrics["diffPreview"],
        "timestamp": now,
        "rolledBack": False,
    }
    store["fileReceipts"] = (store.get("fileReceipts", []) + [entry])[-MAX_FILE_RECEIPTS:]
    try:
        rcpt_file = _file_receipt_dir() / f"{rid}.json"
        rcpt_file.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return rid


def _save_file_receipt(
    path: str | Path,
    op: str,
    new_content: str | None = None,
    *,
    before_text: str | None = None,
    before_existed: bool | None = None,
    dest_path: str = "",
    now: int | None = None,
) -> dict:
    """Create and persist a file mutation receipt with unified diff metrics (+X / -Y)."""
    target = Path(path).expanduser().resolve(strict=False)
    existed = target.is_file() if before_existed is None else bool(before_existed)
    old_txt = before_text
    if old_txt is None and existed and target.is_file():
        try:
            if target.stat().st_size <= 262144:
                old_txt = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old_txt = None
    before_sha = _file_sha256(target) if (existed and target.is_file()) else ""
    after_sha = (
        hashlib.sha256(new_content.encode("utf-8")).hexdigest()
        if isinstance(new_content, str)
        else ""
    )
    ts = _now_ms(now)
    store = _load_store()
    rid = _record_file_receipt(
        store,
        op=op,
        target_path=str(target),
        dest_path=dest_path,
        before_sha=before_sha,
        after_sha=after_sha,
        backup_text=old_txt,
        before_existed=existed,
        now=ts,
        new_content=new_content,
    )
    _save_store(store)
    matched = store["fileReceipts"][-1]
    return {
        "receiptId": rid,
        "addedLines": matched["addedLines"],
        "removedLines": matched["removedLines"],
        "diffPreview": matched["diffPreview"],
        "existedBefore": existed,
        "path": str(target),
    }


def rollback_file_receipt(
    receipt_id: str = "",
    *,
    store: dict | None = None,
    raise_on_missing: bool = False,
) -> dict:
    """Restore a file to its pre-mutation state using its receiptId."""
    req_id = str(receipt_id or "").strip()
    if req_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", req_id):
        if raise_on_missing:
            raise ToolError("invalid_receipt_id", "Invalid file receipt ID.")
        return {"ok": False, "error": "Invalid file receipt ID."}
    own_store = store is None
    st = _load_store() if store is None else store
    file_receipts = st.get("fileReceipts", [])
    matched = None
    for r in reversed(file_receipts):
        if req_id and r.get("receiptId") != req_id:
            continue
        if not r.get("rolledBack"):
            matched = r
            break

    rcpt_root = _file_receipt_dir().resolve(strict=False)
    rcpt_file = (rcpt_root / f"{req_id}.json") if req_id else None
    if rcpt_file is not None and not rcpt_file.resolve(strict=False).is_relative_to(rcpt_root):
        rcpt_file = None
    if matched is None and rcpt_file is not None and rcpt_file.is_file() and not rcpt_file.is_symlink():
        try:
            loaded_rcpt = json.loads(rcpt_file.read_text(encoding="utf-8"))
            if isinstance(loaded_rcpt, dict) and not loaded_rcpt.get("rolledBack"):
                matched = loaded_rcpt
        except (OSError, ValueError):
            matched = None

    if not matched:
        if raise_on_missing:
            raise ToolError("receipt_missing", "No matching recoverable file receipt found.")
        return {"ok": False, "error": f"Receipt not found: {req_id}"}

    from permission_store import is_forbidden_path
    rop = str(matched.get("op") or "edit")
    raw_tpath = matched.get("targetPath") or matched.get("path") or ""
    if not raw_tpath or is_forbidden_path(raw_tpath):
        raise ToolError("forbidden_path", "Target path in file receipt is not permitted.")
    tpath = Path(raw_tpath)
    raw_dpath = matched.get("destPath") or ""
    if raw_dpath and is_forbidden_path(raw_dpath):
        raise ToolError("forbidden_path", "Destination path in file receipt is not permitted.")
    dpath = Path(raw_dpath) if raw_dpath else None
    existed_before = bool(matched.get("beforeExisted", matched.get("existedBefore", False)))
    backup = matched.get("backupText") if "backupText" in matched else matched.get("oldContent")

    if rop in ("edit", "create", "patch", "write", "append", "replace_lines", "apply_patch"):
        if not existed_before:
            if tpath.is_file() and not tpath.is_symlink():
                tpath.unlink()
        elif backup is not None:
            if tpath.suffix.lower() in (".docx", ".pdf", ".xlsx"):
                import code_tools
                code_tools.convert_or_create_document(str(backup), tpath)
            else:
                _atomic_write_text(tpath, str(backup))
        else:
            if raise_on_missing:
                raise ToolError("rollback_unavailable", "Backup payload not available for this file receipt.")
            return {"ok": False, "error": "Backup payload not available for this file receipt."}
    elif rop == "delete":
        if backup is not None:
            if tpath.suffix.lower() in (".docx", ".pdf", ".xlsx"):
                import code_tools
                code_tools.convert_or_create_document(str(backup), tpath)
            else:
                _atomic_write_text(tpath, str(backup))
        else:
            if raise_on_missing:
                raise ToolError("rollback_unavailable", "Deleted directory or binary file cannot be restored.")
            return {"ok": False, "error": "Deleted directory or binary file cannot be restored."}
    elif rop == "copy" and dpath and dpath.is_file() and not dpath.is_symlink():
        dpath.unlink()
    elif rop == "cut" and dpath and dpath.exists() and not tpath.exists():
        shutil.move(str(dpath), str(tpath))

    matched["rolledBack"] = True
    rid = str(matched.get("receiptId") or req_id)
    if own_store:
        for r in file_receipts:
            if r.get("receiptId") == rid:
                r["rolledBack"] = True
    _save_store(st)
    if rid:
        rf = _file_receipt_dir() / f"{rid}.json"
        if rf.is_file():
            try:
                rf_data = json.loads(rf.read_text(encoding="utf-8"))
                if isinstance(rf_data, dict):
                    rf_data["rolledBack"] = True
                    rf.write_text(json.dumps(rf_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError):
                pass

    return {
        "ok": True,
        "op": "rollback",
        "status": "completed",
        "confirmStage": 0,
        "receiptId": rid,
        "restored": str(tpath),
        "existedBefore": existed_before,
        "targetPath": str(tpath),
        "destPath": str(dpath) if dpath else "",
        "summary": _clean_line(f"Rolled back {rop} on {tpath.name} ({rid}).", 240),
        "lines": [_clean_line(f"Restored receipt: {rid}", 180)],
    }


def file_receipt_rollback(options_or_id: dict | str = "") -> dict:
    if isinstance(options_or_id, dict):
        rid = str(options_or_id.get("receiptId") or options_or_id.get("id") or "").strip()
    else:
        rid = str(options_or_id or "").strip()
    return rollback_file_receipt(rid)


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
elif op in ("open_editor", "editor"):
    if target.exists() and target.is_dir():
        print(json.dumps({"ok": False, "code": "expected_file", "message": "Specify a file path to open in editor."}))
        sys.exit(0)
    raw_bytes = target.read_bytes() if target.is_file() else b""
    is_binary = b"\x00" in raw_bytes[:8192]
    text = "" if is_binary else raw_bytes.decode("utf-8", errors="replace")
    line_count = len(text.splitlines()) if text else 0
    print(json.dumps({
        "ok": True,
        "result": {
            "op": "open_editor",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(target),
            "destPath": "",
            "byteSize": len(raw_bytes),
            "lineCount": line_count,
            "isBinary": is_binary,
            "rawText": text[:262144],
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
    res = parsed["result"]
    if op in ("open_editor", "editor"):
        import desktop_control
        max_bytes = int(os.environ.get("ALIS_AQUA_SMALL_FILE_MAX_BYTES") or SMALL_FILE_MAX_BYTES)
        max_lines = int(os.environ.get("ALIS_AQUA_SMALL_FILE_MAX_LINES") or SMALL_FILE_MAX_LINES)
        byte_size = int(res.get("byteSize") or 0)
        line_count = int(res.get("lineCount") or 0)
        is_binary = bool(res.get("isBinary"))
        raw_text = str(res.get("rawText") or "")
        is_large = is_binary or (byte_size > max_bytes) or (line_count > max_lines)
        if is_large:
            ext_info = desktop_control.open_in_default_editor(resolved)
            editor_cmd = str(ext_info.get("editorCommand") or "default-editor")
            return {
                "op": "open_editor",
                "status": "completed",
                "confirmStage": 0,
                "targetPath": str(resolved),
                "destPath": "",
                "editorMode": "external",
                "isLargeFile": True,
                "openedExternalEditor": True,
                "editorCommand": editor_cmd,
                "editorPopup": {
                    "active": False,
                    "filePath": str(resolved),
                    "fileName": resolved.name,
                    "content": "",
                    "byteSize": byte_size,
                    "lineCount": line_count,
                    "scaleFactor": 1.0,
                    "slideAnimation": False,
                },
                "summary": _clean_line(
                    f"Opened large system file {resolved.name} ({byte_size} bytes, {line_count} lines) in default editor ({editor_cmd}).",
                    240,
                ),
                "lines": [
                    f"Mode: Default System Editor ({editor_cmd})",
                    f"File: {resolved}",
                    f"Metrics: {byte_size} bytes, {line_count} lines",
                ],
            }
        preview_lines = [_clean_line(ln, 200) for ln in raw_text.splitlines() if ln.strip()][:12]
        return {
            "op": "open_editor",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(resolved),
            "destPath": "",
            "editorMode": "popup",
            "isLargeFile": False,
            "openedExternalEditor": False,
            "editorCommand": "inline-popup",
            "editorPopup": {
                "active": True,
                "filePath": str(resolved),
                "fileName": resolved.name,
                "content": raw_text,
                "byteSize": byte_size,
                "lineCount": line_count,
                "maxSmallBytes": max_bytes,
                "maxSmallLines": max_lines,
                "scaleFactor": 1.5,
                "slideAnimation": True,
            },
            "summary": _clean_line(
                f"Opened system file {resolved.name} ({byte_size} bytes, {line_count} lines) in Companion Editor Popup (1.5x).",
                240,
            ),
            "lines": preview_lines or ["(opened in Editor Popup)"],
        }
    return res


def build_editor_handoff(target: Path, force_external: bool = False) -> dict:
    """Classify `target` into popup editor (small file) vs external system editor (large file)."""
    import desktop_control

    max_bytes = int(os.environ.get("ALIS_AQUA_SMALL_FILE_MAX_BYTES") or SMALL_FILE_MAX_BYTES)
    max_lines = int(os.environ.get("ALIS_AQUA_SMALL_FILE_MAX_LINES") or SMALL_FILE_MAX_LINES)
    byte_size = 0
    text = ""
    line_count = 0
    is_binary = False
    if target.is_file():
        try:
            raw_bytes = target.read_bytes()
            byte_size = len(raw_bytes)
            if target.suffix.lower() in (".docx", ".pdf", ".xlsx"):
                import code_tools
                text = code_tools.read_file(target).get("text", "")
                line_count = len(text.splitlines()) if text else 0
            elif b"\x00" in raw_bytes[:4096]:
                is_binary = True
            else:
                text = raw_bytes.decode("utf-8", errors="replace")
                line_count = len(text.splitlines()) if text else 0
        except OSError:
            byte_size = 0
    is_large = bool(force_external or is_binary or byte_size > max_bytes or line_count > max_lines)
    lang_ext = target.suffix.lstrip(".").lower() or "txt"
    if is_large:
        launch = desktop_control.open_in_default_editor(target)
        return {
            "editorMode": "external",
            "isLargeFile": True,
            "openedExternalEditor": True,
            "editorCommand": str(launch.get("editorCommand") or "xdg-open"),
            "editorPopup": {
                "open": False,
                "filePath": str(target),
                "fileName": target.name,
                "content": "",
                "lineCount": line_count,
                "byteSize": byte_size,
                "language": lang_ext,
                "sha256": _file_sha256(target),
            },
        }
    return {
        "editorMode": "popup",
        "isLargeFile": False,
        "openedExternalEditor": False,
        "editorCommand": "",
        "editorPopup": {
            "open": True,
            "filePath": str(target),
            "fileName": target.name,
            "content": text,
            "lineCount": line_count,
            "byteSize": byte_size,
            "language": lang_ext,
            "sha256": _file_sha256(target),
        },
    }


def agent_file_op(options: dict) -> dict:
    raw_op = str(options.get("op", "")).strip().lower()
    mode_override = ""
    if raw_op == "write":
        op = "edit"
        mode_override = "write"
    elif raw_op == "append":
        op = "edit"
        mode_override = "append"
    elif raw_op in ("replace_lines", "apply_patch"):
        op = "patch"
        mode_override = "replace"
    elif raw_op == "file_receipt_rollback":
        op = "rollback"
    else:
        op = raw_op
    valid_ops = (
        "access", "list", "search", "create", "edit", "patch", "open_editor",
        "copy", "cut", "delete", "cancel_delete", "rollback", "run",
    )
    if op not in valid_ops:
        raise ToolError("invalid_op", f"Unsupported agent file operation: {raw_op}")
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
        req_id = str(options.get("receiptId") or options.get("id") or "").strip()
        return rollback_file_receipt(req_id, store=store, raise_on_missing=True)

    raw_target = str(options.get("targetPath") or options.get("path") or "").strip()
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

        timeout_sec = float(options.get("timeoutSec") or 20.0)
        use_sandbox = bool(options.get("sandbox", False))
        try:
            if resolved_script is not None and len(cmd_text.split()) == 1:
                suffix = resolved_script.suffix.lower()
                if suffix == ".py":
                    argv = ["/usr/bin/python3", str(resolved_script)]
                elif suffix in (".js", ".cjs", ".mjs"):
                    argv = ["/usr/bin/node", str(resolved_script)]
                else:
                    argv = ["/usr/bin/bash", str(resolved_script)]
                if use_sandbox:
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
                    ran = run_user_command(argv, cwd=work_dir, timeout_sec=timeout_sec, shell=False)
            else:
                if use_sandbox:
                    ran = run_sandboxed(
                        cmd_text,
                        cwd=work_dir,
                        writable_dirs=[home, work_dir],
                        readonly_dirs=[home],
                        allow_network=bool(options.get("allowNetwork", False)),
                        timeout_sec=timeout_sec,
                        shell=True,
                    )
                else:
                    ran = run_user_command(cmd_text, cwd=work_dir, timeout_sec=timeout_sec, shell=True)
        except SandboxError as exc:
            raise ToolError(exc.code, exc.message) from exc

        full_run_text = "\n".join(ran["lines"])
        spill_preview, spill_meta = maybe_spill_tool_output(
            full_run_text,
            session_id=str(options.get("threadId") or options.get("sessionId") or "chat_default"),
            call_id=str(options.get("callId") or ""),
            tool_name="shell_run",
        )
        out_lines = [_clean_line(ln, 200) for ln in ran["lines"] if ln.strip()][:200]
        run_res = {
            "op": "run",
            "status": ran["status"],
            "confirmStage": 0,
            "targetPath": str(resolved_script or work_dir)[:400],
            "destPath": "",
            "summary": _clean_line(f"Ran '{target_label}' ({ran['status']}, exit {ran['exitCode']}).", 240),
            "lines": out_lines or [f"({ran['status']} with exit code {ran['exitCode']})"],
        }
        if spill_meta is not None:
            run_res["spilled"] = True
            run_res["spillId"] = spill_meta["spillId"]
            run_res["spillPath"] = spill_meta["path"]
            run_res["spillPreview"] = spill_preview
        return run_res

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
        suffix_low = target.suffix.lower()
        if suffix_low in (".docx", ".pdf", ".xlsx", ".pptx", ".odt", ".epub"):
            import code_tools
            start_line = max(1, int(options.get("startLine") or 1))
            end_line = max(start_line, int(options.get("endLine") or (start_line + 199)))
            doc_info = code_tools.read_file(target, start_line=start_line, end_line=end_line)
            lines = [_clean_line(ln, 200) for ln in (doc_info.get("lines") or []) if ln.strip()][:200]
            return {
                "op": "access",
                "status": "completed",
                "confirmStage": 0,
                "targetPath": str(target),
                "destPath": "",
                "summary": _clean_line(f"Read {target.name} ({size} bytes, sha256:{_file_sha256(target)[:12]}).", 240),
                "lines": lines or ["(empty document)"],
                "tables": doc_info.get("tables") or [],
                "markdownTables": doc_info.get("markdownTables") or [],
            }
        is_binary = False
        if size > 0:
            try:
                with target.open("rb") as fh:
                    is_binary = b"\x00" in fh.read(4096)
            except OSError:
                is_binary = False
        if size > 4 * 1024 * 1024 or (size > 512 * 1024 and is_binary):
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
        explicit_range = options.get("startLine") is not None or options.get("endLine") is not None
        spill_preview, spill_meta = (
            maybe_spill_tool_output(
                raw_text,
                session_id=str(options.get("threadId") or options.get("sessionId") or "chat_default"),
                call_id=str(options.get("callId") or ""),
                tool_name="file_inspect",
            )
            if not explicit_range
            else (raw_text, None)
        )
        start_line = max(1, int(options.get("startLine") or 1))
        end_line = min(len(all_lines), int(options.get("endLine") or (start_line + 199)))
        sliced = all_lines[start_line - 1:end_line] if all_lines else []
        lines = [_clean_line(ln, 200) for ln in sliced if ln.strip()][:200]
        access_res = {
            "op": "access",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(target),
            "destPath": "",
            "summary": _clean_line(f"Read {target.name} ({size} bytes, sha256:{_file_sha256(target)[:12]}).", 240),
            "lines": lines or ["(empty file)"],
        }
        if spill_meta is not None:
            access_res["spilled"] = True
            access_res["spillId"] = spill_meta["spillId"]
            access_res["spillPath"] = spill_meta["path"]
            access_res["spillPreview"] = spill_preview
        return access_res

    if op == "open_editor":
        target = _resolve_user_path(raw_target, allow_missing=True, last_dir=last_dir, allow_system=allow_system)
        if target.exists() and target.is_dir():
            raise ToolError("is_directory", "Cannot open a folder as a text/code file in the editor.")
        before_existed = target.is_file()
        rid = ""
        if not before_existed:
            if target.suffix.lower() in (".docx", ".pdf", ".xlsx"):
                import code_tools
                code_tools.convert_or_create_document("", target, title=target.stem)
                after_sha = _file_sha256(target)
            else:
                after_sha = _atomic_write_text(target, "")
            rid = _record_file_receipt(
                store,
                op="create",
                target_path=str(target),
                dest_path="",
                before_sha="",
                after_sha=after_sha,
                backup_text=None,
                before_existed=False,
                now=now,
            )
            _save_store(store)
        _sync_session_context_from_op(target, is_dir=False)
        force_ext = bool(options.get("forceExternal", False)) or str(options.get("mode", "")).strip().lower() == "external"
        handoff = build_editor_handoff(target, force_external=force_ext)
        popup_info = handoff["editorPopup"]
        if handoff["editorMode"] == "external":
            summary_str = f"Opened large file {target.name} ({popup_info['lineCount']} lines, {popup_info['byteSize']} bytes) in default editor ({handoff['editorCommand']})."
            lines_out = [_clean_line(f"External editor: {handoff['editorCommand']} {target}", 200)]
        else:
            rcpt_note = f", receipt {rid}" if rid else ""
            summary_str = f"Opened {target.name} ({popup_info['lineCount']} lines, {popup_info['byteSize']} bytes{rcpt_note}) in Editor Popup."
            raw_lines = popup_info["content"].splitlines()
            lines_out = [_clean_line(ln, 200) for ln in raw_lines if ln.strip()][:20] or ["(empty file)"]
        return {
            "op": "open_editor",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(target),
            "destPath": "",
            "summary": _clean_line(summary_str, 240),
            "lines": lines_out,
            **handoff,
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

        mode = mode_override or ("replace" if op == "patch" else str(options.get("mode", "write")).strip().lower())
        content = str(options.get("content") if options.get("content") is not None else (options.get("patch") or options.get("newText") or ""))
        if len(content) > 65536:
            raise ToolError("content_too_large", "Edit content exceeds the 65536-character limit.")

        is_rich_doc_out = target.suffix.lower() in (".docx", ".pdf", ".xlsx")
        if is_rich_doc_out and before_existed:
            import code_tools
            existing = code_tools.read_file(target).get("text", "")
        else:
            existing = target.read_text(encoding="utf-8", errors="replace") if before_existed else ""
        if mode == "append":
            sep = "\n" if existing and not existing.endswith("\n") else ""
            updated = existing + sep + content + ("\n" if not content.endswith("\n") else "")
        elif mode == "replace":
            search_text = str(options.get("searchText") or options.get("oldText") or "")
            has_line_range = options.get("startLine") is not None or options.get("endLine") is not None
            if search_text:
                if search_text not in existing:
                    raise ToolError("search_not_found", "Target text to replace was not found in file.")
                updated = existing.replace(search_text, content, 1)
            elif has_line_range:
                orig_lines = existing.splitlines(keepends=True)
                s_line = max(1, int(options.get("startLine") or 1))
                e_line = max(s_line, int(options.get("endLine") or s_line))
                if s_line > len(orig_lines) + 1:
                    raise ToolError("line_range_out_of_bounds", f"startLine {s_line} exceeds file length ({len(orig_lines)} lines).")
                keep_trailing_nl = existing.endswith("\n") or (e_line <= len(orig_lines) and orig_lines[min(e_line - 1, len(orig_lines) - 1)].endswith("\n"))
                repl_text = content + ("\n" if (content and not content.endswith("\n") and keep_trailing_nl) else "")
                updated = "".join(orig_lines[: s_line - 1]) + repl_text + "".join(orig_lines[e_line:])
            elif "@@" in content or "\n-" in ("\n" + content):
                # Parse and apply unified diff hunks
                hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
                patch_lines = content.splitlines()
                hunks: list[tuple[int, list[str], list[str]]] = []
                cur_start = 1
                cur_old: list[str] = []
                cur_new: list[str] = []
                in_hunk = False
                for pl in patch_lines:
                    m = hunk_re.match(pl)
                    if m:
                        if in_hunk:
                            hunks.append((cur_start, cur_old, cur_new))
                        cur_start = int(m.group(1))
                        cur_old, cur_new = [], []
                        in_hunk = True
                        continue
                    if pl.startswith(("--- ", "+++ ", "diff ", "index ")):
                        continue
                    if not in_hunk and pl.startswith(("-", "+", " ")):
                        in_hunk = True
                    if in_hunk:
                        if pl.startswith("-"):
                            cur_old.append(pl[1:])
                        elif pl.startswith("+"):
                            cur_new.append(pl[1:])
                        elif pl.startswith(" "):
                            cur_old.append(pl[1:])
                            cur_new.append(pl[1:])
                        elif pl == "\\ No newline at end of file":
                            continue
                if in_hunk:
                    hunks.append((cur_start, cur_old, cur_new))
                if not hunks:
                    raise ToolError("invalid_patch", "No valid unified diff hunks found in patch content.")
                work_text = existing
                applied_any = False
                for h_start, h_old, h_new in hunks:
                    old_block = "\n".join(h_old)
                    new_block = "\n".join(h_new)
                    if h_old and old_block in work_text:
                        work_text = work_text.replace(old_block, new_block, 1)
                        applied_any = True
                    else:
                        w_lines = work_text.splitlines()
                        idx0 = max(0, min(len(w_lines), h_start - 1))
                        if not h_old or w_lines[idx0 : idx0 + len(h_old)] == h_old:
                            w_lines[idx0 : idx0 + len(h_old)] = h_new
                            work_text = "\n".join(w_lines) + ("\n" if existing.endswith("\n") else "")
                            applied_any = True
                if not applied_any:
                    raise ToolError("search_not_found", "Patch hunks did not match target file content.")
                updated = work_text
            else:
                raise ToolError("search_not_found", "Target text to replace was not found in file.")
        else:
            updated = content

        if is_rich_doc_out:
            import code_tools
            code_tools.convert_or_create_document(
                updated,
                target,
                title=str(options.get("title") or ""),
            )
            after_sha = _file_sha256(target)
        else:
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
            new_content=updated,
        )
        rcpt_entry = store["fileReceipts"][-1]
        _save_store(store)
        _sync_session_context_from_op(target, is_dir=False)
        handoff = build_editor_handoff(target, force_external=bool(options.get("forceExternal", False)))
        preview = [_clean_line(ln, 200) for ln in updated.splitlines() if ln.strip()][:20]
        return {
            "op": "edit",
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(target),
            "destPath": "",
            "receiptId": rid,
            "addedLines": rcpt_entry["addedLines"],
            "removedLines": rcpt_entry["removedLines"],
            "diffPreview": rcpt_entry["diffPreview"],
            "summary": _clean_line(
                f"Updated {target.name} (+{rcpt_entry['addedLines']} / -{rcpt_entry['removedLines']} lines, {len(updated.encode('utf-8'))} bytes, receipt {rid}).",
                240,
            ),
            "lines": preview or ["(file saved)"],
            **handoff,
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
        dst_new_text = dst.read_text(encoding="utf-8", errors="replace") if (dst.is_file() and dst.stat().st_size <= 262144) else None
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
            new_content=dst_new_text,
        )
        rcpt_entry = store["fileReceipts"][-1]
        _save_store(store)
        return {
            "op": op,
            "status": "completed",
            "confirmStage": 0,
            "targetPath": str(src),
            "destPath": str(dst),
            "receiptId": rid,
            "addedLines": rcpt_entry["addedLines"],
            "removedLines": rcpt_entry["removedLines"],
            "diffPreview": rcpt_entry["diffPreview"],
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
            if target.suffix.lower() in (".docx", ".pdf", ".xlsx"):
                import code_tools
                backup_text = code_tools.read_file(target).get("text", "")
            else:
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
            new_content="",
        )
        rcpt_entry = store["fileReceipts"][-1]
        _save_store(store)
        return {
            "op": "delete",
            "status": "completed",
            "confirmStage": 2,
            "targetPath": str(target),
            "destPath": "",
            "receiptId": rid,
            "addedLines": rcpt_entry["addedLines"],
            "removedLines": rcpt_entry["removedLines"],
            "diffPreview": rcpt_entry["diffPreview"],
            "summary": _clean_line(f"Deleted '{target.name}' after 2x confirmation (receipt {rid}).", 240),
            "lines": [_clean_line(f"Removed: {target}", 180)],
        }
    raise ToolError("invalid_confirm_step", "Invalid delete confirmation step.")


def delegate_subagent(role: str, task: str, context_hint: str = "", **kwargs) -> dict:
    """Delegate a scoped subtask to an isolated-context specialist Subagent on the single loaded local model."""
    import agent_runtime

    return agent_runtime.delegate_subagent(role=role, task=task, context_hint=context_hint, **kwargs)


def assess_task_scale_for_subagents(prompt: str, options: dict | None = None) -> dict:
    """Assess whether a prompt is a large/multi-phase workload for sequential subagent decomposition."""
    import agent_runtime

    return agent_runtime.assess_task_scale_for_subagents(prompt=prompt, options=options)


def read_spill_tool(options: dict) -> dict:
    """Read a line slice or grep matches from a session-confined tool output spill file."""
    opts = options if isinstance(options, dict) else {}
    session_id = str(opts.get("sessionId") or opts.get("threadId") or "chat_default").strip() or "chat_default"
    spill_id = str(opts.get("spillId") or opts.get("spill_id") or "").strip()
    path = str(opts.get("path") or "").strip()
    offset_line = opts.get("offsetLine", opts.get("offset_line", 1))
    limit_lines = opts.get("limitLines", opts.get("limit_lines", 120))
    grep = str(opts.get("grep") or "")
    try:
        return read_spill_store(
            session_id=session_id,
            spill_id=spill_id,
            path=path,
            offset_line=offset_line,
            limit_lines=limit_lines,
            grep=grep,
        )
    except SpillError as exc:
        raise ToolError(exc.code, exc.message) from exc


