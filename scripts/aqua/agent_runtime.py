"""Natural-language desktop agent runtime for Alis-Companion (AC-F11).

Implements the bounded agent loop:
  goal -> context -> schema tool proposal -> permission check -> execute ->
  readback/observation -> continue or finish.

Key guarantees:
  - Full read/write/execute access across the entire User Home ($HOME), including
    $HOME root, subdirectories, dot-directories, and symlinks inside $HOME.
  - System paths outside $HOME require explicit user permission AND sudo password
    verification (`sudo -S -k`), with password redaction in chat history.
  - Complete file/folder listings without 8-line/10-line/420-char truncation.
  - Single chat box handles casual conversation, natural-language tasks,
    hypothetical/negated/quoted questions (never executed), confirmations,
    and while-busy stop/steer instructions.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata
import urllib.error
import urllib.request

from code_tools import (
    CodeToolError,
    build_repo_map,
    compact_evidence,
    estimate_tokens,
    query_lsp,
    retrieve_rag,
    run_project_tests,
    search_code,
)
import history_store
import memory_store
from permission_store import (
    AgentPermissionError,
    check_permission,
    create_pending_confirmation,
    grant_permission,
    inspect_permissions,
    is_forbidden_path,
    is_system_path,
    resolve_pending_confirmation,
    revoke_permission,
)
import pocket as pocket_store
from rule_store import (
    RuleStoreError,
    create_or_update_rule,
    delete_rule,
    evaluate_triggers,
    load_rules_state,
    mark_startup_grace,
    set_rule_enabled,
)
from run_store import (
    RunStoreError,
    advance_turn,
    cancel_active_runs,
    finish_run,
    get_run,
    latest_resumable_run,
    load_session_context,
    reconcile_crashed_runs,
    record_step_finish,
    record_step_start,
    save_session_context,
    start_run,
)
from screen_context import ScreenContextError, observe_active_window, perform_gui_action
from tool_store import (
    ToolError,
    agent_file_op,
    confirm_edit,
    execute_system_sudo_op,
    propose_edit,
    resolve_candidate_path,
)


AGENT_CONTRACT_VERSION = 1

AGENT_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "file_inspect",
            "description": "Read a file or list/search a directory within permitted scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "query": {"type": "string"},
                    "startLine": {"type": "integer"},
                    "endLine": {"type": "integer"},
                    "page": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_mutate",
            "description": "Create, edit, patch, copy, move, delete, or rollback a file with conflict and symlink checks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["create", "edit", "patch", "copy", "cut", "delete", "rollback"]},
                    "path": {"type": "string"},
                    "destPath": {"type": "string"},
                    "content": {"type": "string"},
                    "searchText": {"type": "string"},
                    "mode": {"type": "string", "enum": ["write", "append", "replace"]},
                },
                "required": ["op", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_observe",
            "description": "Observe the currently focused window or screen via Niri metadata, AT-SPI, or scoped OCR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "allowScreenshot": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_inspect",
            "description": "Search code, build a token-budgeted repo map, query LSP symbols/diagnostics, or run tests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search", "repo_map", "symbols", "definition", "references", "diagnostics", "run_tests"]},
                    "workspace": {"type": "string"},
                    "query": {"type": "string"},
                    "file": {"type": "string"},
                },
                "required": ["action", "workspace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_run",
            "description": "Execute a command inside the bubblewrap sandbox within a permitted workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
]

_GENERIC_LIST_TARGETS = frozenset((
    "files", "file", "folders", "folder", "directories", "directory", "dir", "dirs",
    "all", "all files", "all folders", "all items", "everything", "items", "contents",
    "tat ca", "het", "tat ca file", "tat ca tep", "tat ca thu muc", "het tat ca",
    "cac file", "cac tep", "cac thu muc", "danh sach", "thu muc", "tep",
    "thu muc nay", "thu muc do", "this folder", "this directory", "here", "o day",
    "-a", "-la", "-l",
))


def _fold_ascii(text: str) -> str:
    norm = unicodedata.normalize("NFD", str(text or "").strip().lower())
    norm = re.sub(r"[\u0300-\u036f]", "", norm).replace("đ", "d")
    return re.sub(r"\s+", " ", norm)


def detect_language(prompt: str) -> str:
    text = str(prompt or "").strip()
    if re.search(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", text):
        return "vi"
    folded = _fold_ascii(text)
    vi_markers = (
        "giup toi", "man hinh", "thu muc", "tep ", "cua so", "du an", "kiem tra",
        "xoa ", "sua ", "doc ", "nho rang", "quen ", "quy tac", "quyen ", "tiep tuc",
        "dung lai", "huy ", "lam tiep", "vua mo", "liet ke", "tat ca",
    )
    if any(m in folded for m in vi_markers):
        return "vi"
    return "en"


def is_hypothetical_or_negated(prompt: str) -> bool:
    raw = str(prompt or "").strip()
    if not raw:
        return False
    folded = _fold_ascii(raw)

    if re.match(r"^(?:giai thich|explain|what does|y nghia cua|dich cau|translate|phan tich cau)\b", folded):
        return True
    if re.search(r"""(?:giai thich|explain|phrase|command|lenh|cau)\s+['"`].+['"`]""", folded):
        return True

    if re.match(
        r"^(?:xin\s+|vui long\s+|please\s+)?(?:dung|khong|chua|tuyet doi khong|do not|don't|dont|never|must not|should not)\s+"
        r"(?:xoa|sua|chay|doc|mo|ghi|sao chep|di chuyen|gui|thuc hien|delete|remove|edit|write|run|execute|open|read|modify|touch|clear|forget)\b",
        folded,
    ):
        return True

    if re.match(
        r"^(?:neu|gia su|what if|what would happen if|suppose|assuming|if i|if you)\b",
        folded,
    ) and ("?" in raw or "thi sao" in folded or "happen" in folded or "would" in folded):
        return True

    return False


def is_stop_or_cancel(prompt: str) -> bool:
    folded = _fold_ascii(prompt)
    return bool(re.match(
        r"^(?:hay\s+|vui long\s+|please\s+)?(?:dung|dung lai|dung ngay|ngung|thoi|huy|huy bo|huy tac vu|stop|cancel|abort|halt|pause)(?:\s+(?:lai|ngay|di|tac vu|cong viec|run|task|it|now))?[.!]*$",
        folded,
    ))


def is_resume_command(prompt: str) -> bool:
    folded = _fold_ascii(prompt)
    return bool(re.match(
        r"^(?:hay\s+|vui long\s+|please\s+)?(?:lam tiep|lam tiep phan con lai|tiep tuc|tiep tuc di|tiep tuc tu checkpoint|continue|continue the rest|resume|keep going)(?:\s+.*)?$",
        folded,
    ))


def classify_confirmation_response(prompt: str) -> str | None:
    folded = _fold_ascii(prompt)
    if re.match(
        r"^(?:ok|okay|yes|y|confirm|confirmed|allow|grant|approve|agree|dong y|xac nhan|chap nhan|cap quyen|duoc|uh|vang|lam di|xoa di|tiep tuc xoa)(?:\s+.*)?$",
        folded,
    ):
        return "confirm"
    if re.match(
        r"^(?:no|n|cancel|deny|reject|khong|huy|tu choi|dung|thoi)(?:\s+.*)?$",
        folded,
    ):
        return "deny"
    return None


def _wants_all_or_hidden(prompt: str) -> bool:
    folded = _fold_ascii(prompt)
    return bool(re.search(
        r"\b(?:all|everything|hidden|dotfiles|tat ca|het|toan bo|file an|thu muc an|-a|-la)\b",
        folded,
    ))


def _extract_path_from_text(text: str, session_ctx: dict | None = None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    ctx = session_ctx or {}
    home = Path.home().resolve()
    last_dir_str = str(ctx.get("lastDirectory") or "").strip()
    last_dir = Path(last_dir_str) if last_dir_str else None

    # 1. Explicit ~ or $HOME
    if re.search(r"(?:^|\s)(?:~|\$HOME)(?:\s|$)", raw):
        return str(home)
    folded = _fold_ascii(raw)
    if re.search(r"\b(?:thu muc home|user home|home directory|home folder)\b", folded) or re.match(
        r"^(?:/agent\s+)?(?:access|list|ls|dir|open|cd|xem|doc|liet ke)\s+home\s*$",
        folded,
    ):
        return str(home)

    # 2. Explicit path starting with ~/ or / or ./ or ../
    m = re.search(r"""(['"`]?)((?:~/|/|\./|\.\./)[^\s'"`:,;!?()\[\]{}]+)\1""", raw)
    if m and not m.group(2).startswith("/agent"):
        return m.group(2).rstrip(".,;!?()[]{}")

    # 3. Direct command prefix: `access <arg>`, `list <arg>`, `ls <arg>`, `dir <arg>`, `open <arg>`, `cd <arg>`
    m_cmd = re.match(
        r"^(?:/agent\s+)?(?:access|list|ls|dir|open|cd|inspect|read)\s+(.+?)\s*$",
        raw,
        re.IGNORECASE,
    )
    if m_cmd:
        arg = m_cmd.group(1).strip().strip("'\"`").rstrip(".,;!?")
        arg_folded = _fold_ascii(arg)
        if arg_folded not in _GENERIC_LIST_TARGETS and not arg_folded.startswith("all ") and not arg_folded.startswith("files "):
            if arg in ("~", "$HOME") or arg_folded == "home":
                return str(home)
            try:
                cand = resolve_candidate_path(arg, last_dir=last_dir_str)
                if cand.exists() or cand.is_symlink() or "/" in arg or "." in arg:
                    return str(cand)
            except ToolError:
                pass

    # 4. Filename with extension
    m_file = re.search(
        r"""\b([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*\.(?:txt|md|py|qml|js|cjs|json|rs|toml|sh|yaml|yml|html|css|log|conf|cfg|ini|service|tar|gz|zip|pdf|docx|jpg|png|AppImage))\b""",
        raw,
        re.IGNORECASE,
    )
    if m_file:
        fname = m_file.group(1)
        try:
            return str(resolve_candidate_path(fname, last_dir=last_dir_str))
        except ToolError:
            return fname

    # 5. Match against actual child entries in lastDirectory and $HOME
    search_roots = []
    if last_dir and last_dir.is_dir():
        search_roots.append(last_dir)
    if home.is_dir() and home not in search_roots:
        search_roots.append(home)
    desktop = home / "Desktop"
    if desktop.is_dir() and desktop not in search_roots:
        search_roots.append(desktop)

    raw_lower = raw.lower()
    best_match = ""
    best_len = 0
    for root_dir in search_roots:
        try:
            for child in root_dir.iterdir():
                cname = child.name
                if len(cname) < 2 or cname in (".", ".."):
                    continue
                clower = cname.lower()
                if clower in ("chat", "test", "file", "files", "list", "read", "edit", "run"):
                    continue
                pattern = r"(?:^|[\s'\"`/])" + re.escape(clower) + r"(?:$|[\s'\"`.,;!?/])"
                if re.search(pattern, raw_lower):
                    if len(cname) > best_len:
                        best_match = str(child.resolve(strict=False))
                        best_len = len(cname)
        except OSError:
            continue
    if best_match:
        return best_match

    # 6. Well-known user directories even if not yet created
    m_folder = re.search(
        r"\b(Desktop|Documents|Downloads|Music|Pictures|Videos|Public|Templates|Shared_Documents|Projects)\b",
        raw,
        re.IGNORECASE,
    )
    if m_folder:
        return str(home / m_folder.group(1))
    return ""


def _resolve_conversational_target(prompt: str, session_ctx: dict, expect_dir: bool = False) -> tuple[str, list[str]]:
    """Resolve explicit path or conversational anaphora ('tệp đó', 'thư mục này', 'dự án vừa mở')."""
    explicit = _extract_path_from_text(prompt, session_ctx)
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            last_dir = session_ctx.get("lastDirectory") or str(Path.home())
            cand = Path(last_dir) / p
            if cand.exists() or cand.is_symlink():
                return str(cand.resolve(strict=False)), []
        return str(p), []

    folded = _fold_ascii(prompt)
    candidates = [str(c) for c in (session_ctx.get("recentCandidates") or []) if c]

    if not expect_dir and candidates:
        if any(k in folded for k in ("dau tien", "so 1", "thu nhat", "first", "#1")):
            return candidates[0], []
        if len(candidates) >= 2 and any(k in folded for k in ("thu hai", "so 2", "second", "#2")):
            return candidates[1], []
        for cand in candidates:
            if Path(cand).name.lower() in folded:
                return cand, []

    if expect_dir or any(
        k in folded
        for k in (
            "thu muc nay", "thu muc do", "thu muc vua mo", "du an vua mo", "du an nay",
            "this folder", "that folder", "this directory", "that project", "current project",
            "list files", "list all", "list folders", "show files", "show all",
            "liet ke", "xem tat ca", "xem het",
        )
    ):
        target_dir = session_ctx.get("lastDirectory") or session_ctx.get("lastProject") or ""
        if target_dir:
            return target_dir, []
        return str(Path.home()), []

    if any(k in folded for k in ("tep do", "tep nay", "tep vua nhac", "tep vua doc", "tep vua mo", "file do", "file nay", "that file", "this file", "the file")):
        last_file = session_ctx.get("lastFile") or ""
        if last_file:
            return last_file, []
        if len(candidates) > 1:
            return "", candidates
        if len(candidates) == 1:
            return candidates[0], []

    if len(candidates) > 1 and not session_ctx.get("lastFile") and not expect_dir:
        return "", candidates

    if expect_dir:
        return session_ctx.get("lastDirectory") or str(Path.home()), []
    return session_ctx.get("lastFile") or session_ctx.get("lastDirectory") or str(Path.home()), []


def classify_intent(prompt: str, session_ctx: dict | None = None) -> dict:
    """Classify a single-chatbox user prompt without requiring command prefixes."""
    ctx = session_ctx or {}
    raw = str(prompt or "").strip()
    lang = detect_language(raw)
    folded = _fold_ascii(raw)

    if not raw:
        return {"kind": "chat", "language": lang}

    if is_stop_or_cancel(raw):
        return {"kind": "stop", "language": lang}

    if is_hypothetical_or_negated(raw):
        return {"kind": "hypothetical_or_negated", "language": lang}

    # Check if awaiting sudo password for a system path operation
    if ctx.get("pendingSudoOp"):
        return {"kind": "sudo_password_reply", "language": lang}

    # Check if there is a pending clarification in session_ctx
    if ctx.get("pendingClarification"):
        return {"kind": "clarification_reply", "language": lang}

    perm_state = inspect_permissions()
    if perm_state.get("latestPending") or ctx.get("pendingDeletePath") or ctx.get("pendingMemoryForget"):
        latest = perm_state.get("latestPending")
        if latest and latest.get("actionKind") == "system_sudo_op":
            return {"kind": "sudo_password_reply", "language": lang}
        decision = classify_confirmation_response(raw)
        if decision:
            return {"kind": "confirmation_response", "decision": decision, "language": lang}

    if is_resume_command(raw):
        return {"kind": "resume", "language": lang}

    # Budget adjustment via prompt
    m_budget = re.search(r"(?:gioi han|toi da|max turns|limit|budget)\s*(?:la|to|:)?\s*(\d+)\s*(?:vong|luot|turns|steps|phut|minutes)", folded)
    if m_budget:
        val = int(m_budget.group(1))
        is_minutes = any(w in folded for w in ("phut", "minute", "minutes"))
        return {
            "kind": "adjust_budget",
            "value": val,
            "unit": "minutes" if is_minutes else "turns",
            "language": lang,
        }

    # Permission management via prompt
    if any(k in folded for k in ("xem quyen", "quyen hien tai", "kiem tra quyen", "list permissions", "show permissions", "what permissions")):
        return {"kind": "permission_manage", "subkind": "inspect", "language": lang}
    if re.search(r"\b(?:thu hoi quyen|xoa quyen|tat quyen|revoke permission|revoke all permissions|clear permissions)\b", folded):
        return {"kind": "permission_manage", "subkind": "revoke", "language": lang}
    if re.search(r"\b(?:cap quyen|cho phep truy cap|grant permission|allow access to)\b", folded):
        return {"kind": "permission_manage", "subkind": "grant", "language": lang}

    # Rule management via prompt
    if any(k in folded for k in ("danh sach quy tac", "xem quy tac", "cac quy tac", "list rules", "show rules", "active rules")):
        return {"kind": "rule_manage", "subkind": "list", "language": lang}
    if re.search(r"\b(?:tat quy tac|xoa quy tac|huy quy tac|disable rule|delete rule|remove rule|stop all rules)\b", folded):
        return {"kind": "rule_manage", "subkind": "disable", "language": lang}
    if re.search(r"\b(?:tao quy tac|them quy tac|khi su kien|khi bat dau|sau khi ket thuc focus|moi ngay luc|create rule|when planner|when focus ends|every day at)\b", folded):
        return {"kind": "rule_manage", "subkind": "create", "language": lang}

    # Memory management via prompt
    if re.search(r"^(?:hay\s+|vui long\s+|please\s+)?(?:nho rang|ghi nho|luu vao bo nho|remember that|save to memory|please remember)\b", folded):
        return {"kind": "memory_manage", "subkind": "save", "language": lang}
    if re.search(r"\b(?:xem bo nho|xem cac ghi nho|nhung gi ban nho|list memories|show memories|what do you remember)\b", folded):
        return {"kind": "memory_manage", "subkind": "list", "language": lang}
    if re.search(r"^(?:hay\s+|vui long\s+|please\s+)?(?:quen ghi nho|xoa ghi nho|forget memory|delete memory)\b", folded):
        return {"kind": "memory_manage", "subkind": "forget", "language": lang}

    # Pocket management via prompt
    if re.search(r"\b(?:trong pocket|tep trong pocket|xem pocket|them .* vao pocket|bo .* khoi pocket|pocket files|in pocket|add .* to pocket|remove .* from pocket)\b", folded):
        return {"kind": "pocket_manage", "language": lang}

    # Screen / window observation & GUI action
    if re.search(
        r"\b(?:xem man hinh|doc man hinh|doc cua so|cua so hien tai|ung dung dang mo|man hinh dang co gi|tren man hinh|what is on my screen|read the screen|read current window|inspect active window|look at my screen)\b",
        folded,
    ) or re.match(r"^(?:/agent\s+)?access\s+(?:screen|window|app|desktop\s+app)\s*$", folded):
        return {"kind": "screen_observe", "language": lang}
    if re.search(r"\b(?:chuyen sang cua so|focus window|focus cua so|kiem tra cua so)\b", folded):
        return {"kind": "gui_action", "language": lang}

    # Coding / LSP / repo map / test execution
    if re.search(r"\b(?:chay kiem tra|chay test|run tests|run project tests|test du an)\b", folded):
        return {"kind": "code_task", "subkind": "run_tests", "language": lang}
    if re.search(r"\b(?:kiem tra loi|diagnostics|lsp|sua loi trong du an|fix error in project|check errors in)\b", folded):
        return {"kind": "code_task", "subkind": "diagnostics_or_fix", "language": lang}
    if re.search(r"\b(?:tim symbol|tim ham|tim dinh nghia|tim tham chieu|find symbol|find definition|find references|search code|cau truc du an|repo map)\b", folded):
        return {"kind": "code_task", "subkind": "symbol_or_search", "language": lang}

    # Knowledge / RAG search
    if re.search(r"\b(?:tim trong tai lieu|theo tai lieu|tai lieu companion|shader validation|portal travel|search docs|in the documentation)\b", folded):
        return {"kind": "rag_query", "language": lang}

    # Preference update via prompt
    if re.search(r"\b(?:doi tinh cach|chinh tinh cach|set personality|change personality|doi giong dieu|set dialogue tone)\b", folded):
        return {"kind": "preference_manage", "language": lang}

    # Undo / rollback
    if re.search(r"\b(?:hoan tac|undo|rollback|khoi phuc thao tac vua roi)\b", folded):
        return {"kind": "file_task", "subkind": "rollback", "language": lang}

    # Multi-step or single-step file / folder operations in natural language or shorthand
    if (
        re.search(
            r"\b(?:doc thu muc|liet ke|xem thu muc|mo thu muc|truy cap|xem tat ca|xem het|danh sach file|danh sach tep|danh sach thu muc|co nhung file nao|co gi trong thu muc|tim tep|doc tep|doc file|xem file|tom tat tep|sua tep|sua file|ghi vao|them dong|tao tep|tao file|sao chep|di chuyen|xoa tep|xoa file|read folder|list directory|list files|list all|list folders|list everything|list items|show files|show all|show folders|show directory|what files|what is in|read file|summarize|edit file|create file|copy file|move file|delete file)\b",
            folded,
        )
        or re.match(
            r"^(?:/agent\s+)?(?:access|list|ls|dir|tree|open|cd|edit|write|append|copy|cp|cut|move|mv|delete|rm|run|exec)\b",
            folded,
        )
    ):
        return {"kind": "file_task", "subkind": "general", "language": lang}

    return {"kind": "chat", "language": lang}


def _call_local_model_tool_turn(endpoint_url: str, model_name: str, system_prompt: str, user_prompt: str) -> dict | None:
    """Optional structured tool-call query against the active local OpenAI/Ollama endpoint.

    Strictly parses structured `tool_calls` from the API response object; never
    executes JSON or commands found inside plain assistant prose.
    """
    if not endpoint_url or not model_name:
        return None
    if not (endpoint_url.startswith("http://127.0.0.1:") or endpoint_url.startswith("http://localhost:")):
        return None
    try:
        if "/v1/" in endpoint_url:
            body = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt[:2400]},
                    {"role": "user", "content": user_prompt[:1200]},
                ],
                "tools": AGENT_TOOL_SCHEMAS,
                "tool_choice": "auto",
                "temperature": 0.1,
                "max_tokens": 256,
                "reasoning_effort": "low",
                "chat_template_kwargs": {"enable_thinking": False},
                "stream": False,
            }
            req = urllib.request.Request(
                endpoint_url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            usage = data.get("usage") or {}
            tool_calls = []
            for tc in (msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                name = str(fn.get("name") or "")
                raw_args = fn.get("arguments") or "{}"
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args if isinstance(raw_args, dict) else {})
                if name:
                    tool_calls.append({
                        "toolCallId": str(tc.get("id") or f"call_{len(tool_calls)+1}"),
                        "name": name,
                        "args": args,
                    })
            return {
                "text": str(msg.get("content") or "").strip(),
                "toolCalls": tool_calls,
                "tokenUsage": {
                    "inputTokens": int(usage.get("prompt_tokens") or 0),
                    "outputTokens": int(usage.get("completion_tokens") or 0),
                },
            }
    except Exception:
        return None
    return None


def _format_reply(
    text: str,
    expression: str = "happy",
    status: str = "completed",
    run_id: str = "",
    language: str = "vi",
    receipts: list | None = None,
    observations: list | None = None,
    requires_confirmation: bool = False,
    requires_sudo_password: bool = False,
    confirm_id: str = "",
    session_ctx: dict | None = None,
) -> dict:
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")).strip()[:5800]
    if not clean:
        clean = "Đã hoàn thành." if language == "vi" else "Completed."
    expr = expression if expression in ("idle", "happy", "excited", "thinking", "working", "surprised", "sleepy", "sad", "alert") else "idle"
    return {
        "contractVersion": AGENT_CONTRACT_VERSION,
        "runId": str(run_id or ""),
        "status": str(status),
        "language": str(language),
        "text": clean,
        "expression": expr,
        "requiresConfirmation": bool(requires_confirmation),
        "requiresSudoPassword": bool(requires_sudo_password),
        "confirmId": str(confirm_id or ""),
        "receipts": list(receipts or [])[:16],
        "observations": list(observations or [])[:16],
        "sessionContext": session_ctx or load_session_context(),
    }


def _persist_turn_to_history(prompt: str, reply_text: str, model: str = "companion-agent") -> tuple[int, int, bool]:
    try:
        u_id, a_id = history_store.append_exchange(prompt[:1200], reply_text[:5800], model)
        return u_id, a_id, True
    except Exception:
        return 0, 0, False


def _prompt_for_system_sudo(
    op_kind: str,
    target_path: str,
    prompt: str,
    lang: str,
    run_id: str,
    session_ctx: dict,
    now_ms: int,
    extra_payload: dict | None = None,
    persist_history: bool = True,
) -> dict:
    payload = {
        "op": op_kind,
        "targetPath": str(target_path),
        "originalPrompt": prompt,
        "includeHidden": _wants_all_or_hidden(prompt),
        **(extra_payload or {}),
    }
    conf = create_pending_confirmation(
        "system_sudo_op",
        str(target_path),
        f"System {op_kind} on {target_path}",
        payload,
        now_ms=now_ms,
    )
    session_ctx["pendingSudoOp"] = conf
    save_session_context(session_ctx, now_ms=now_ms)
    finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
    if lang == "vi":
        msg = (
            f"Đường dẫn `{target_path}` thuộc hệ thống (nằm ngoài thư mục Home). "
            f"Để cho phép thực hiện thao tác `{op_kind}` tại đây, vui lòng nhập mật khẩu `sudo` của bạn "
            "(hoặc gõ `huỷ` để từ chối — mật khẩu chỉ dùng 1 lần và không lưu vào lịch sử)."
        )
    else:
        msg = (
            f"Path `{target_path}` is a system path outside your Home directory. "
            f"To authorize `{op_kind}` on this path, please enter your `sudo` password "
            "(or type `cancel` to deny — your password is used once and never saved to history)."
        )
    res = _format_reply(
        msg,
        expression="thinking",
        status="awaiting_sudo_password",
        run_id=run_id,
        language=lang,
        requires_confirmation=True,
        requires_sudo_password=True,
        confirm_id=conf["confirmId"],
        session_ctx=session_ctx,
    )
    u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
    return {
        "handled": True,
        "intent": "file_task",
        **res,
        "userMessageId": u_id,
        "assistantMessageId": a_id,
        "historySaved": saved,
    }


def execute_agent_turn(options: dict, companion_root: Path, host_root: Path) -> dict:
    """Execute a natural-language desktop agent turn with full Home access and sudo-gated System access."""
    now_ms = int(options.get("nowMs") or int(time.time() * 1000))
    prompt = str(options.get("prompt") or "").strip()
    sudo_password_opt = str(options.get("sudoPassword") or "")
    persist_history = options.get("persistHistory") is not False
    allow_screenshot = options.get("allowScreenshot") is not False

    reconciled = reconcile_crashed_runs(now_ms)
    session_ctx = load_session_context()
    intent = classify_intent(prompt, session_ctx)
    lang = intent["language"]
    kind = intent["kind"]

    # 0. Ordinary chat fallback
    if kind == "chat":
        return {
            "handled": False,
            "intent": "chat",
            "language": lang,
            "reconciledRuns": reconciled,
        }

    # 1. Negated / hypothetical / quoted guard: NEVER execute tools!
    if kind == "hypothetical_or_negated":
        if lang == "vi":
            msg = (
                "Mình hiểu đây là câu phủ định, giả định hoặc giải thích nên mình **không thực hiện** "
                "bất kỳ thao tác tệp, lệnh hay thay đổi dữ liệu nào. Bạn cứ yên tâm nhé!"
            )
        else:
            msg = (
                "I understand this is a negated, hypothetical, or explanatory prompt, so I **did not execute** "
                "any file, command, or desktop action. Your data remains untouched."
            )
        res = _format_reply(msg, expression="idle", status="no_action_taken", language=lang, session_ctx=session_ctx)
        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
        return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

    # 2. Stop / Cancel active run
    if kind == "stop":
        cancelled_ids = cancel_active_runs("cancelled_by_user", now_ms)
        if session_ctx.get("pendingSudoOp"):
            resolve_pending_confirmation(decision="cancel", source="user_prompt", now_ms=now_ms)
        session_ctx["pendingClarification"] = None
        session_ctx["pendingDeletePath"] = ""
        session_ctx["pendingMemoryForget"] = None
        session_ctx["pendingSudoOp"] = None
        save_session_context(session_ctx, now_ms=now_ms)
        if lang == "vi":
            msg = (
                f"Đã dừng tác vụ ngay lập tức ({', '.join(cancelled_ids) if cancelled_ids else 'đã huỷ yêu cầu đang chờ'}). "
                "Trạng thái hiện tại đã được lưu vào checkpoint để bạn có thể nói 'làm tiếp' bất cứ lúc nào."
            )
        else:
            msg = (
                f"Stopped the active task immediately ({', '.join(cancelled_ids) if cancelled_ids else 'cleared pending request'}). "
                "Progress has been saved to a checkpoint so you can say 'continue' whenever you like."
            )
        res = _format_reply(msg, expression="alert", status="cancelled", language=lang, session_ctx=session_ctx)
        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
        return {"handled": True, "intent": "stop", **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

    # 2b. Sudo password reply for pending System path operation
    if kind == "sudo_password_reply":
        pending_sudo = session_ctx.get("pendingSudoOp")
        if not pending_sudo:
            perm_info = inspect_permissions({"nowMs": now_ms})
            latest = perm_info.get("latestPending")
            if latest and latest.get("actionKind") == "system_sudo_op":
                pending_sudo = latest

        decision = classify_confirmation_response(prompt)
        if decision == "deny" and not sudo_password_opt:
            session_ctx["pendingSudoOp"] = None
            save_session_context(session_ctx, now_ms=now_ms)
            resolve_pending_confirmation(decision="cancel", source="user_prompt", now_ms=now_ms)
            msg = (
                "Đã huỷ thao tác trên đường dẫn hệ thống. Không có thay đổi nào được thực hiện."
                if lang == "vi"
                else "Cancelled the system path operation. No changes were made."
            )
            res = _format_reply(msg, expression="idle", status="cancelled", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        pwd = sudo_password_opt if sudo_password_opt else prompt
        redacted_prompt = "•••••••• (sudo password)"
        sudo_payload = (pending_sudo or {}).get("payload") or {}
        target_path = sudo_payload.get("targetPath") or (pending_sudo or {}).get("targetPath") or ""
        op_name = sudo_payload.get("op") or "access"
        try:
            sudo_res = execute_system_sudo_op(
                {
                    "op": op_name,
                    "targetPath": target_path,
                    "destPath": sudo_payload.get("destPath", ""),
                    "content": sudo_payload.get("content", ""),
                    "mode": sudo_payload.get("mode", "write"),
                    "includeHidden": bool(sudo_payload.get("includeHidden", False)),
                    "nowMs": now_ms,
                },
                sudo_password=pwd,
            )
            session_ctx["pendingSudoOp"] = None
            resolved_p = Path(sudo_res.get("targetPath") or target_path)
            if op_name in ("access", "list") and resolved_p.is_dir():
                session_ctx["lastDirectory"] = str(resolved_p)
            else:
                session_ctx["lastFile"] = str(resolved_p)
                session_ctx["lastDirectory"] = str(resolved_p.parent)
            save_session_context(session_ctx, now_ms=now_ms)
            resolve_pending_confirmation(decision="confirm", source="user_prompt", now_ms=now_ms)

            body_lines = "\n".join(f"- {ln}" for ln in sudo_res.get("lines", []))
            if lang == "vi":
                msg = f"Đã xác thực `sudo` và thực hiện trên `{target_path}` ({sudo_res['summary']}):\n{body_lines}"
            else:
                msg = f"Verified `sudo` and completed action on `{target_path}` ({sudo_res['summary']}):\n{body_lines}"
            res = _format_reply(
                msg,
                expression="happy",
                status="completed",
                language=lang,
                requires_confirmation=False,
                requires_sudo_password=False,
                session_ctx=session_ctx,
            )
            u_id, a_id, saved = _persist_turn_to_history(redacted_prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}
        except ToolError as exc:
            if exc.code == "sudo_auth_failed":
                msg = (
                    f"Mật khẩu `sudo` không đúng khi truy cập `{target_path}`. Vui lòng nhập lại mật khẩu `sudo` hoặc gõ `huỷ` để từ chối."
                    if lang == "vi"
                    else f"Incorrect `sudo` password for `{target_path}`. Please try again or type `cancel` to abort."
                )
                res = _format_reply(
                    msg,
                    expression="sad",
                    status="awaiting_sudo_password",
                    language=lang,
                    requires_confirmation=True,
                    requires_sudo_password=True,
                    confirm_id=(pending_sudo or {}).get("confirmId", ""),
                    session_ctx=session_ctx,
                )
            else:
                session_ctx["pendingSudoOp"] = None
                save_session_context(session_ctx, now_ms=now_ms)
                resolve_pending_confirmation(decision="cancel", source="user_prompt", now_ms=now_ms)
                msg = (
                    f"Không thể thực hiện thao tác hệ thống (`{exc.code}`): {exc}"
                    if lang == "vi"
                    else f"System operation failed (`{exc.code}`): {exc}"
                )
                res = _format_reply(msg, expression="sad", status="failed", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(redacted_prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "errorCode": exc.code,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

    # 3. Adjust run budget via prompt
    if kind == "adjust_budget":
        val = int(intent["value"])
        if intent["unit"] == "minutes":
            sec = max(30, min(600, val * 60))
            session_ctx["customMaxDurationSec"] = sec
            msg = f"Đã cập nhật giới hạn thời gian mỗi lượt chạy thành {val} phút." if lang == "vi" else f"Updated run time budget to {val} minutes."
        else:
            turns = max(1, min(30, val))
            session_ctx["customMaxTurns"] = turns
            msg = f"Đã cập nhật giới hạn vòng lặp agent tối đa thành {turns} vòng." if lang == "vi" else f"Updated maximum agent loop budget to {turns} turns."
        save_session_context(session_ctx, now_ms=now_ms)
        res = _format_reply(msg, expression="happy", status="completed", language=lang, session_ctx=session_ctx)
        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
        return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

    # 4. Clarification reply when multiple files matched previously
    if kind == "clarification_reply":
        pending_clar = session_ctx.get("pendingClarification") or {}
        chosen, _ = _resolve_conversational_target(prompt, session_ctx, expect_dir=False)
        if not chosen and pending_clar.get("candidates"):
            chosen = pending_clar["candidates"][0]
        session_ctx["pendingClarification"] = None
        if chosen:
            session_ctx["lastFile"] = chosen
            save_session_context(session_ctx, now_ms=now_ms)
            orig_goal = str(pending_clar.get("originalPrompt") or f"đọc tệp {chosen}")
            return execute_agent_turn(
                {**options, "prompt": f"{orig_goal} {chosen}"},
                companion_root=companion_root,
                host_root=host_root,
            )

    # 5. Conversational Confirmation / Rejection for pending permission, delete, or memory forget
    if kind == "confirmation_response":
        decision = intent["decision"]
        if session_ctx.get("pendingMemoryForget"):
            mem_target = session_ctx.get("pendingMemoryForget")
            session_ctx["pendingMemoryForget"] = None
            save_session_context(session_ctx, now_ms=now_ms)
            if decision == "confirm":
                if mem_target.get("all"):
                    cnt = memory_store.forget_all()
                    msg = (
                        f"Đã xoá {cnt} ghi nhớ trong bộ nhớ Companion. Lưu ý: các bản sao trong Obsidian hoặc bản backup bên ngoài (nếu có) không bị xoá."
                        if lang == "vi"
                        else f"Removed {cnt} saved memory item(s) from Companion's local store. Note: external Obsidian notes or backups may remain."
                    )
                else:
                    memory_store.forget_one(int(mem_target["id"]))
                    msg = (
                        f"Đã xoá ghi nhớ #{mem_target['id']} ('{mem_target.get('text', '')}'). Các bản sao hoặc backup bên ngoài không bị thay đổi."
                        if lang == "vi"
                        else f"Forgot memory #{mem_target['id']} ('{mem_target.get('text', '')}'). External copies or backups remain unchanged."
                    )
                res = _format_reply(msg, expression="happy", status="completed", language=lang, session_ctx=session_ctx)
            else:
                msg = "Đã huỷ yêu cầu xoá ghi nhớ; dữ liệu được giữ nguyên." if lang == "vi" else "Cancelled memory deletion; your saved memories are unchanged."
                res = _format_reply(msg, expression="idle", status="cancelled", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        if session_ctx.get("pendingDeletePath"):
            del_path = session_ctx.get("pendingDeletePath")
            session_ctx["pendingDeletePath"] = ""
            save_session_context(session_ctx, now_ms=now_ms)
            if decision == "confirm":
                agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 0, "nowMs": now_ms})
                agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 1, "nowMs": now_ms + 1})
                deleted = agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 2, "nowMs": now_ms + 2})
                msg = (
                    f"Đã xoá `{Path(del_path).name}` theo xác nhận của bạn ({deleted['summary']}). Bạn có thể nói 'hoàn tác' nếu muốn khôi phục tệp văn bản vừa xoá."
                    if lang == "vi"
                    else f"Deleted `{Path(del_path).name}` per your confirmation ({deleted['summary']}). You can say 'undo' to restore a deleted text file."
                )
                res = _format_reply(msg, expression="happy", status="completed", language=lang, session_ctx=session_ctx)
            else:
                agent_file_op({"op": "cancel_delete", "targetPath": del_path, "nowMs": now_ms})
                msg = f"Đã huỷ xoá `{Path(del_path).name}`. Tệp vẫn an toàn." if lang == "vi" else f"Cancelled deleting `{Path(del_path).name}`. The file was not touched."
                res = _format_reply(msg, expression="idle", status="cancelled", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        resolved_conf = resolve_pending_confirmation(decision="confirm" if decision == "confirm" else "cancel", source="user_prompt", now_ms=now_ms)
        if resolved_conf:
            if decision == "confirm":
                payload = resolved_conf.get("payload") or {}
                if resolved_conf["actionKind"] == "grant_directory":
                    grant_permission(
                        "directory",
                        target=resolved_conf["targetPath"],
                        levels=payload.get("levels") or ["read", "write", "execute"],
                        ttl_ms=int(payload.get("ttlMs") or 3600_000),
                        source="user_prompt",
                        now_ms=now_ms,
                    )
                    resume_prompt = payload.get("originalPrompt")
                    if resume_prompt:
                        return execute_agent_turn(
                            {**options, "prompt": resume_prompt},
                            companion_root=companion_root,
                            host_root=host_root,
                        )
                msg = f"Đã xác nhận và cấp quyền cho `{resolved_conf['targetPath']}`." if lang == "vi" else f"Confirmed and granted permission for `{resolved_conf['targetPath']}`."
                res = _format_reply(msg, expression="happy", status="completed", language=lang, session_ctx=session_ctx)
            else:
                msg = "Đã từ chối cấp quyền và huỷ thao tác đang chờ." if lang == "vi" else "Denied permission and cancelled the pending action."
                res = _format_reply(msg, expression="idle", status="cancelled", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

    # 6. Resume from checkpoint / remaining steps
    if kind == "resume":
        last_run = latest_resumable_run()
        rem_steps = session_ctx.get("remainingSteps") or (last_run["checkpoint"].get("remainingSteps") if last_run else [])
        if last_run and last_run["status"] == "uncertain_after_crash":
            uncertain = last_run["checkpoint"].get("uncertainSteps") or []
            if lang == "vi":
                msg = (
                    f"Phiên chạy trước (`{last_run['runId']}`) bị ngắt giữa chừng. "
                    f"Mình đã đối chiếu trạng thái đĩa cho {len(uncertain)} thao tác dở dang và **không tự ý chạy lặp lại** thao tác có tác dụng phụ. "
                )
            else:
                msg = (
                    f"Previous run (`{last_run['runId']}`) was interrupted. "
                    f"I reconciled disk state for {len(uncertain)} in-flight step(s) without blindly repeating side effects. "
                )
            if rem_steps:
                next_goal = rem_steps.pop(0)
                session_ctx["remainingSteps"] = rem_steps
                save_session_context(session_ctx, now_ms=now_ms)
                sub = execute_agent_turn({**options, "prompt": next_goal, "persistHistory": False}, companion_root, host_root)
                combined = msg + "\n" + sub.get("text", "")
                res = _format_reply(combined, expression="happy", status="completed", run_id=last_run["runId"], language=lang, session_ctx=session_ctx)
            else:
                finish_run(last_run["runId"], status="completed", now_ms=now_ms)
                res = _format_reply(msg + ("Không còn bước nào tồn đọng." if lang == "vi" else "No remaining steps queued."), expression="happy", status="completed", run_id=last_run["runId"], language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": "resume", **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        if rem_steps:
            next_goal = rem_steps.pop(0)
            session_ctx["remainingSteps"] = rem_steps
            save_session_context(session_ctx, now_ms=now_ms)
            return execute_agent_turn({**options, "prompt": next_goal}, companion_root, host_root)

        msg = "Hiện không có phần công việc nào đang tạm dừng trong checkpoint." if lang == "vi" else "There are no paused steps waiting in the checkpoint."
        res = _format_reply(msg, expression="idle", status="completed", language=lang, session_ctx=session_ctx)
        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
        return {"handled": True, "intent": "resume", **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

    # Start a tracked durable run for this action
    run_source = str(options.get("runSource") or "interactive")
    max_turns = int(session_ctx.get("customMaxTurns") or (10 if run_source == "rule" else 30))
    max_dur = int(session_ctx.get("customMaxDurationSec") or (180 if run_source == "rule" else 600))
    run = start_run(prompt, source=run_source, language=lang, max_turns=max_turns, max_duration_sec=max_dur, now_ms=now_ms)
    run_id = run["runId"]
    receipts = []
    observations = []

    try:
        # 7. Permission management via natural language
        if kind == "permission_manage":
            sub = intent["subkind"]
            if sub == "inspect":
                info = inspect_permissions({"nowMs": now_ms})
                dirs_summary = ", ".join(f"`{d['path']}` ({'/'.join(d['levels'])})" for d in info["directories"][:5]) or "none"
                net_str = "bật" if info["networkAllowed"] else "tắt (local-only)"
                msg = (
                    f"Quyền hiệu lực hiện tại:\n- Toàn quyền trong User Home (`{Path.home()}`): {'bật' if info.get('homeFullAccess', True) else 'tắt'}\n- Đường dẫn hệ thống (ngoài Home): yêu cầu xác nhận + mật khẩu `sudo`\n- Thư mục: {dirs_summary}\n- Mạng ngoài: {net_str}\n- Công cụ đang bật: {', '.join(k for k, v in info['tools'].items() if v)}"
                    if lang == "vi"
                    else f"Effective permissions:\n- User Home (`{Path.home()}`) full access: {'enabled' if info.get('homeFullAccess', True) else 'disabled'}\n- System paths (outside Home): require confirmation + `sudo` password\n- Directories: {dirs_summary}\n- External network: {'enabled' if info['networkAllowed'] else 'disabled (local-only)'}\n- Active tools: {', '.join(k for k, v in info['tools'].items() if v)}"
                )
            elif sub == "revoke":
                folded = _fold_ascii(prompt)
                target_path = _extract_path_from_text(prompt, session_ctx)
                if target_path:
                    revoke_permission("directory", target=target_path, now_ms=now_ms)
                    msg = f"Đã thu hồi quyền truy cập thư mục `{target_path}`." if lang == "vi" else f"Revoked directory permission for `{target_path}`."
                elif "shell" in folded or "lenh" in folded or "script" in folded:
                    revoke_permission("tool", target="shell_run", now_ms=now_ms)
                    msg = "Đã thu hồi quyền chạy lệnh shell/script." if lang == "vi" else "Revoked shell/script execution permission."
                else:
                    revoke_permission(revoke_all=True, now_ms=now_ms)
                    msg = "Đã thu hồi các quyền mở rộng; chỉ giữ quyền đọc cơ bản theo từng yêu cầu." if lang == "vi" else "Revoked extended permissions; keeping only per-request read access."
            else:
                target_path = _extract_path_from_text(prompt, session_ctx) or session_ctx.get("lastDirectory") or str(Path.home())
                m_ttl = re.search(r"(\d+)\s*(?:phut|minutes|min)", _fold_ascii(prompt))
                ttl_ms = int(m_ttl.group(1)) * 60_000 if m_ttl else 3600_000
                grant_permission("directory", target=target_path, levels=["read", "write", "execute"], ttl_ms=ttl_ms, source="user_prompt", now_ms=now_ms)
                msg = (
                    f"Đã cấp quyền đọc/ghi/thực thi cho `{target_path}` (thời hạn {ttl_ms // 60000} phút)."
                    if lang == "vi"
                    else f"Granted read/write/execute permission for `{target_path}` (TTL {ttl_ms // 60000} min)."
                )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 8. Rule management via natural language
        if kind == "rule_manage":
            sub = intent["subkind"]
            if sub == "list":
                rstate = load_rules_state(now_ms)
                rules = rstate.get("rules", [])
                if not rules:
                    msg = "Hiện chưa có quy tắc tự động nào được bật." if lang == "vi" else "No proactive background rules are currently configured."
                else:
                    lines = [
                        f"- `{r['ruleId']}` ({r['triggerType']}, {'bật' if r['enabled'] else 'tắt'}): {r['taskPrompt']}"
                        for r in rules[:8]
                    ]
                    msg = ("Danh sách quy tắc hiện tại:\n" if lang == "vi" else "Configured background rules:\n") + "\n".join(lines)
            elif sub == "disable":
                m_id = re.search(r"\b(rule_[0-9a-f]{8,16})\b", prompt)
                if m_id:
                    set_rule_enabled(rule_id=m_id.group(1), enabled=False, now_ms=now_ms)
                    msg = f"Đã tắt quy tắc `{m_id.group(1)}`." if lang == "vi" else f"Disabled rule `{m_id.group(1)}`."
                else:
                    set_rule_enabled(disable_all=True, enabled=False, now_ms=now_ms)
                    msg = "Đã tắt toàn bộ quy tắc tự khởi việc." if lang == "vi" else "Disabled all proactive background rules."
            else:
                folded = _fold_ascii(prompt)
                planner_events = options.get("plannerEvents") if isinstance(options.get("plannerEvents"), list) else []
                m_time = re.search(r"\b(\d{1,2}:\d{2})\b", prompt)
                m_ev = re.search(r"\b(evt_[A-Za-z0-9_\-]+)\b", prompt)
                if "focus" in folded or "pomodoro" in folded:
                    ttype = "focus_end"
                    ev_id = ""
                    hm = ""
                elif m_time and "planner" not in folded and not m_ev:
                    ttype = "schedule_time"
                    ev_id = ""
                    hm = m_time.group(1)
                else:
                    ttype = "planner_event"
                    ev_id = m_ev.group(1) if m_ev else (str(planner_events[0].get("id")) if planner_events else "evt_planner_default")
                    hm = ""
                created = create_or_update_rule(
                    trigger_type=ttype,
                    task_prompt=prompt,
                    event_id=ev_id,
                    time_hm=hm,
                    planner_events=planner_events,
                    now_ms=now_ms,
                )
                msg = (
                    f"Đã tạo quy tắc `{created['ruleId']}` (nguồn: `{ttype}`, giới hạn: tối đa {created['budget']['maxTurns']} vòng / {created['budget']['maxRunsPerDay']} lần mỗi ngày, không chiếm quyền chuột/bàn phím)."
                    if lang == "vi"
                    else f"Created rule `{created['ruleId']}` (trigger: `{ttype}`, budget: max {created['budget']['maxTurns']} turns / {created['budget']['maxRunsPerDay']} runs per day, no GUI input takeover)."
                )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 9. Memory management via natural language
        if kind == "memory_manage":
            sub = intent["subkind"]
            if sub == "list":
                items = memory_store.list_all()
                if not items:
                    msg = "Bộ nhớ hiện đang trống." if lang == "vi" else "Your saved memory list is currently empty."
                else:
                    rows = [f"- #{m['id']} ({m['scope']}): {m['text']}" for m in items[:16]]
                    msg = ("Các ghi nhớ đã lưu:\n" if lang == "vi" else "Saved memories:\n") + "\n".join(rows)
                finish_run(run_id, status="completed", now_ms=now_ms)
                res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            elif sub == "save":
                cleaned = re.sub(
                    r"^(?:hãy\s+|vui lòng\s+|please\s+)?(?:nhớ rằng|ghi nhớ:?|lưu vào bộ nhớ:?|remember that|save to memory:?|please remember)\s*",
                    "",
                    prompt,
                    flags=re.IGNORECASE,
                ).strip()
                mem_id = memory_store.remember_confirmed(
                    text=(cleaned or prompt)[:300],
                    scope="shared",
                    consent=True,
                    expires_ms=0,
                )
                msg = (
                    f"Đã lưu vào bộ nhớ (#{mem_id}): \"{cleaned or prompt}\"."
                    if lang == "vi"
                    else f"Saved to memory (#{mem_id}): \"{cleaned or prompt}\"."
                )
                finish_run(run_id, status="completed", now_ms=now_ms)
                res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            else:
                items = memory_store.list_all()
                folded = _fold_ascii(prompt)
                query_words = [
                    w for w in re.sub(r"^(?:hay\s+|vui long\s+|please\s+)?(?:quen|xoa|forget|delete)\s+(?:ghi nho|bo nho|memory)\s*", "", folded).split()
                    if len(w) >= 2 and w not in ("tat", "ca", "all", "everything", "toan", "bo")
                ]
                matched_mem = None
                if not any(k in folded for k in ("tat ca", "toan bo", "all memories", "everything")):
                    for m in items:
                        m_folded = _fold_ascii(m["text"])
                        if f"#{m['id']}" in prompt or (query_words and any(w in m_folded for w in query_words)):
                            matched_mem = m
                            break
                if matched_mem:
                    session_ctx["pendingMemoryForget"] = {"id": matched_mem["id"], "text": matched_mem["text"], "all": False}
                    save_session_context(session_ctx, now_ms=now_ms)
                    msg = (
                        f"Bạn có chắc muốn xoá ghi nhớ #{matched_mem['id']} (\"{matched_mem['text']}\") khỏi Companion không? "
                        "Hãy trả lời 'đồng ý' để xác nhận hoặc 'huỷ' để giữ lại (lưu ý: các bản sao hoặc backup bên ngoài sẽ không bị xoá)."
                        if lang == "vi"
                        else f"Confirm deleting memory #{matched_mem['id']} (\"{matched_mem['text']}\") from Companion? "
                             "Reply 'yes' to confirm or 'cancel' to keep it (external copies or backups will not be erased)."
                    )
                    finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                    res = _format_reply(msg, expression="alert", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, session_ctx=session_ctx)
                elif items:
                    session_ctx["pendingMemoryForget"] = {"all": True}
                    save_session_context(session_ctx, now_ms=now_ms)
                    msg = (
                        f"Bạn muốn xoá toàn bộ {len(items)} ghi nhớ đã lưu trong Companion phải không? Trả lời 'đồng ý' để xác nhận (bản sao/backup bên ngoài không bị ảnh hưởng)."
                        if lang == "vi"
                        else f"Do you want to delete all {len(items)} saved memories in Companion? Reply 'yes' to confirm (external copies/backups are not affected)."
                    )
                    finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                    res = _format_reply(msg, expression="alert", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, session_ctx=session_ctx)
                else:
                    msg = "Hiện không có ghi nhớ nào để xoá." if lang == "vi" else "There are no saved memories to delete."
                    finish_run(run_id, status="completed", now_ms=now_ms)
                    res = _format_reply(msg, expression="idle", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 10. Pocket management via natural language
        if kind == "pocket_manage":
            folded = _fold_ascii(prompt)
            target_path = _extract_path_from_text(prompt, session_ctx)
            if any(k in folded for k in ("them ", "add ")) and target_path:
                resolved = Path(target_path).expanduser().resolve(strict=False)
                state = pocket_store.dispatch({"version": 1, "consent": True, "action": "add", "urls": [resolved.as_uri()], "source": "picker"})
                msg = (
                    f"Đã thêm tham chiếu metadata của `{resolved.name}` vào Pocket (tổng {len(state['items'])} mục; tệp gốc giữ nguyên tại chỗ)."
                    if lang == "vi"
                    else f"Added metadata reference for `{resolved.name}` to Pocket ({len(state['items'])} item(s); original file stays in place)."
                )
            elif any(k in folded for k in ("bo ", "xoa ", "remove ")) and target_path:
                items = pocket_store.dispatch({"version": 1, "consent": True, "action": "list"})["items"]
                match = next((i for i in items if target_path in i["path"] or target_path.lower() == i["name"].lower()), None)
                if match:
                    pocket_store.dispatch({"version": 1, "consent": True, "action": "remove", "id": int(match["id"])})
                    msg = f"Đã gỡ tham chiếu `{match['name']}` khỏi Pocket (tệp thật trên đĩa không bị xoá)." if lang == "vi" else f"Removed reference `{match['name']}` from Pocket (the file on disk was not deleted)."
                else:
                    msg = "Không tìm thấy mục đó trong Pocket." if lang == "vi" else "Could not find that item in Pocket."
            elif any(k in folded for k in ("doc ", "noi dung", "read ", "content")):
                items = pocket_store.dispatch({"version": 1, "consent": True, "action": "list"})["items"]
                if not items:
                    msg = "Pocket hiện đang trống." if lang == "vi" else "Pocket is currently empty."
                else:
                    first_path = items[0]["path"]
                    perm = check_permission("file_read", target_path=first_path, explicit_prompt_target=False, now_ms=now_ms)
                    if not perm["allowed"]:
                        msg = f"Pocket giữ tham chiếu tới `{items[0]['name']}`, nhưng cần quyền đọc thư mục chứa tệp để đọc nội dung." if lang == "vi" else f"Pocket holds a reference to `{items[0]['name']}`, but read permission is required to read its content."
                    else:
                        read_res = agent_file_op({"op": "access", "targetPath": first_path, "nowMs": now_ms})
                        session_ctx["lastFile"] = first_path
                        save_session_context(session_ctx, now_ms=now_ms)
                        preview = "\n".join(read_res["lines"][:20])
                        msg = (
                            f"Đã đọc tệp tham chiếu trong Pocket `{items[0]['name']}`:\n{preview}"
                            if lang == "vi"
                            else f"Read Pocket-referenced file `{items[0]['name']}`:\n{preview}"
                        )
            else:
                items = pocket_store.dispatch({"version": 1, "consent": True, "action": "list"})["items"]
                if not items:
                    msg = "Trong Pocket hiện chưa có tệp nào." if lang == "vi" else "Pocket has no referenced items right now."
                else:
                    listing = ", ".join(f"`{i['name']}` ({i['size']} bytes)" for i in items[:16])
                    msg = f"Pocket đang lưu {len(items)} tham chiếu metadata: {listing}." if lang == "vi" else f"Pocket holds {len(items)} metadata reference(s): {listing}."
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 11. Screen / Active Window Observation
        if kind == "screen_observe":
            step_id = record_step_start(run_id, 1, "screen_observe", side_effect=False, now_ms=now_ms)
            prev_win = session_ctx.get("lastWindowId")
            obs_res = observe_active_window(allow_screenshot=allow_screenshot, previous_window_id=prev_win, now_ms=now_ms)
            obs = obs_res["observation"]
            record_step_finish(step_id, status="completed", detail=obs)
            advance_turn(run_id, now_ms=now_ms)

            session_ctx["lastWindowId"] = obs.get("windowId")
            session_ctx["lastAppId"] = obs.get("appId") or ""
            save_session_context(session_ctx, now_ms=now_ms)
            observations.append(obs)

            changed_note = ""
            if obs.get("focusChanged"):
                changed_note = " (cửa sổ vừa thay đổi nên mình đã làm mới quan sát)" if lang == "vi" else " (focused window changed; refreshed observation)"
            lines_preview = "\n".join(f"- {ln}" for ln in obs_res["headings"][:16])
            if lang == "vi":
                msg = (
                    f"Quan sát màn hình hiện tại{changed_note} [nguồn: `{obs['source']}`, cửa sổ: `{obs.get('title') or obs.get('appId')}`, lúc: `{obs['capturedAtMs']}`]:\n"
                    f"{lines_preview}"
                )
            else:
                msg = (
                    f"Current window observation{changed_note} [source: `{obs['source']}`, window: `{obs.get('title') or obs.get('appId')}`, at: `{obs['capturedAtMs']}`]:\n"
                    f"{lines_preview}"
                )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="working", status="completed", run_id=run_id, language=lang, observations=observations, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 12. GUI / Niri action broker
        if kind == "gui_action":
            if run_source == "rule":
                raise AgentPermissionError("rule_gui_forbidden", "Background rules are not allowed to control mouse/keyboard or focus.")
            m_win = re.search(r"\b(\d+)\b", prompt)
            win_id = int(m_win.group(1)) if m_win else session_ctx.get("lastWindowId")
            gui_res = perform_gui_action(
                action_type="focus_window" if m_win else "inspect_window",
                target_window_id=win_id,
                require_scale="click" in _fold_ascii(prompt) or "toa do" in _fold_ascii(prompt),
                now_ms=now_ms,
            )
            observations.append(gui_res["observation"])
            msg = (
                f"Đã thực hiện thao tác cửa sổ có kiểm chứng: {gui_res['summary']}."
                if lang == "vi"
                else f"Completed verified window action: {gui_res['summary']}."
            )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, observations=observations, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 13. Coding / LSP / Repo Map / Test Runner tasks
        if kind == "code_task":
            sub = intent["subkind"]
            workspace, _ = _resolve_conversational_target(prompt, session_ctx, expect_dir=True)
            if Path(workspace).is_file():
                workspace = str(Path(workspace).parent)
            session_ctx["lastProject"] = workspace
            session_ctx["lastDirectory"] = workspace
            save_session_context(session_ctx, now_ms=now_ms)

            if sub == "run_tests":
                perm = check_permission("shell_run", target_path=workspace, explicit_prompt_target=True, now_ms=now_ms)
                if not perm["allowed"]:
                    if perm.get("requiresSudo"):
                        return _prompt_for_system_sudo("run", workspace, prompt, lang, run_id, session_ctx, now_ms, persist_history=persist_history)
                    conf = create_pending_confirmation("grant_directory", workspace, f"Run tests in {workspace}", {"levels": ["read", "write", "execute"], "originalPrompt": prompt}, now_ms)
                    finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                    msg = (
                        f"Để chạy kiểm tra trong `{workspace}`, bạn có đồng ý cấp quyền thực thi cho thư mục này không? (Trả lời 'đồng ý' hoặc 'huỷ')"
                        if lang == "vi"
                        else f"To run tests in `{workspace}`, do you grant execution permission for this folder? (Reply 'yes' or 'cancel')"
                    )
                    res = _format_reply(msg, expression="thinking", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, confirm_id=conf["confirmId"], session_ctx=session_ctx)
                    u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                    return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

                test_res = run_project_tests(workspace)
                out_preview = "\n".join(test_res["lines"][:16])
                msg = (
                    f"Kết quả chạy kiểm tra trong `{Path(workspace).name}` (`{test_res['status']}`, mã thoát `{test_res['exitCode']}`):\n{out_preview}"
                    if lang == "vi"
                    else f"Test execution in `{Path(workspace).name}` (`{test_res['status']}`, exit `{test_res['exitCode']}`):\n{out_preview}"
                )
            elif sub == "diagnostics_or_fix":
                diag_res = query_lsp(workspace, action="diagnostics")
                diags = diag_res["diagnostics"]
                if not diags:
                    rmap = build_repo_map(workspace, token_budget=320)
                    msg = (
                        f"Đã kiểm tra `{Path(workspace).name}` ({rmap['fileCount']} tệp, LSP `{diag_res['lspBinary']}`, tắt build scripts/proc-macro tự động): không phát hiện lỗi cú pháp/diagnostics nào."
                        if lang == "vi"
                        else f"Checked `{Path(workspace).name}` ({rmap['fileCount']} files, LSP `{diag_res['lspBinary']}`, buildScripts/procMacro disabled): no syntax or diagnostic errors found."
                    )
                else:
                    d_lines = "\n".join(f"- `{d['file']}:{d['line']}`: {d['message']}" for d in diags[:12])
                    msg = (
                        f"Phát hiện {len(diags)} lỗi trong `{Path(workspace).name}`:\n{d_lines}"
                        if lang == "vi"
                        else f"Found {len(diags)} diagnostic error(s) in `{Path(workspace).name}`:\n{d_lines}"
                    )
            else:
                folded = _fold_ascii(prompt)
                if "repo map" in folded or "cau truc" in folded:
                    rmap = build_repo_map(workspace, token_budget=480)
                    rows = "\n".join(f"- {e['summary']}" for e in rmap["entries"][:16])
                    msg = (
                        f"Cấu trúc dự án `{Path(workspace).name}` ({rmap['fileCount']} tệp, {rmap['estimatedTokens']}/{rmap['tokenBudget']} tokens):\n{rows}"
                        if lang == "vi"
                        else f"Repo map for `{Path(workspace).name}` ({rmap['fileCount']} files, {rmap['estimatedTokens']}/{rmap['tokenBudget']} tokens):\n{rows}"
                    )
                else:
                    m_sym = re.search(r"""['"`]([A-Za-z_][A-Za-z0-9_]*)['"`]""", prompt) or re.search(r"(?:symbol|ham|function|class|def)\s+([A-Za-z_][A-Za-z0-9_]*)", prompt, re.IGNORECASE)
                    sym_name = m_sym.group(1) if m_sym else ""
                    lsp_res = query_lsp(workspace, action="symbols", symbol=sym_name)
                    syms = lsp_res["symbols"]
                    if syms:
                        session_ctx["lastFile"] = str(Path(workspace) / syms[0]["file"])
                        save_session_context(session_ctx, now_ms=now_ms)
                        s_lines = "\n".join(f"- `{s['name']}` tại `{s['file']}:{s['line']}` (`{s['signature']}`)" for s in syms[:12])
                        msg = (
                            f"Kết quả tìm symbol trong `{Path(workspace).name}`:\n{s_lines}"
                            if lang == "vi"
                            else f"Symbols found in `{Path(workspace).name}`:\n{s_lines}"
                        )
                    else:
                        code_res = search_code(workspace, sym_name or prompt.split()[-1])
                        m_lines = "\n".join(f"- `{m['file']}:{m['line']}`: {m['text']}" for m in code_res["matches"][:12]) or "(no matches)"
                        msg = f"Kết quả tìm kiếm mã trong `{Path(workspace).name}`:\n{m_lines}" if lang == "vi" else f"Code search results in `{Path(workspace).name}`:\n{m_lines}"

            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="working", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 14. RAG Knowledge Retrieval with Citations
        if kind == "rag_query":
            extra = [d["path"] for d in inspect_permissions({"nowMs": now_ms})["directories"]]
            rag = retrieve_rag(prompt, companion_root=companion_root, extra_dirs=extra, token_budget=480, page=1, page_size=3)
            if rag["snippets"]:
                cites = "\n".join(f"- {s}" for s in rag["snippets"])
                msg = (
                    f"Thông tin trích xuất từ tài liệu có dẫn nguồn ({rag['estimatedTokens']} tokens):\n{cites}"
                    if lang == "vi"
                    else f"Grounded documentation results with citations ({rag['estimatedTokens']} tokens):\n{cites}"
                )
            else:
                msg = "Không tìm thấy đoạn tài liệu khớp với truy vấn trong phạm vi được cấp." if lang == "vi" else "No matching documentation snippets found within permitted scope."
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 15. Preference edit via natural language
        if kind == "preference_manage":
            folded = _fold_ascii(prompt)
            field = "personality"
            after_val = "lively"
            for cand in ("calm", "balanced", "energetic", "lively", "shy"):
                if cand in folded:
                    field = "personality"
                    after_val = cand
                    break
            for cand in ("gentle", "witty", "playful", "concise"):
                if cand in folded:
                    field = "dialogueTone"
                    after_val = cand
                    break
            prop = propose_edit({"field": field, "beforeValue": "balanced" if field == "personality" else "gentle", "afterValue": after_val, "nowMs": now_ms})
            confirmed = confirm_edit({"proposalId": prop["proposalId"], "confirmed": True, "nowMs": now_ms + 1})
            receipts.append(confirmed["receiptId"])
            msg = (
                f"Đã cập nhật thiết lập `{field}` thành `{after_val}` (receipt `{confirmed['receiptId']}`)."
                if lang == "vi"
                else f"Updated preference `{field}` to `{after_val}` (receipt `{confirmed['receiptId']}`)."
            )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, receipts=receipts, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 16. File & Directory Operations (Full Home access + Sudo-gated System access + Complete Listing)
        if kind == "file_task":
            sub = intent["subkind"]
            folded = _fold_ascii(prompt)
            last_dir = str(session_ctx.get("lastDirectory") or "")

            if sub == "rollback":
                rb = agent_file_op({"op": "rollback", "nowMs": now_ms})
                msg = (
                    f"Đã hoàn tác thành công: {rb['summary']}"
                    if lang == "vi"
                    else f"Rollback completed: {rb['summary']}"
                )
                finish_run(run_id, status="completed", now_ms=now_ms)
                res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

            # Check copy / cut (move) command (`copy src -> dst`, `cut src -> dst`)
            m_copy = re.match(
                r"^(?:/agent\s+)?(copy|cp|sao\s+chép|sao\s+chep|cut|move|mv|di\s+chuyển|di\s+chuyen)\s+(.+?)\s*(?:->|=>|\s+to\s+|\s+sang\s+|\s+tới\s+|\s+toi\s+|\s+vào\s+|\s+vao\s+)\s*(.+)$",
                prompt,
                re.IGNORECASE,
            )
            if m_copy:
                verb_raw = _fold_ascii(m_copy.group(1))
                op_kind = "copy" if verb_raw in ("copy", "cp", "sao chep") else "cut"
                src_raw = m_copy.group(2).strip().strip("'\"`")
                dst_raw = m_copy.group(3).strip().strip("'\"`")
                src_cand = str(resolve_candidate_path(src_raw, last_dir=last_dir))
                dst_cand = str(resolve_candidate_path(dst_raw, last_dir=last_dir))
                if is_system_path(src_cand) or is_system_path(dst_cand):
                    return _prompt_for_system_sudo(
                        op_kind,
                        src_cand,
                        prompt,
                        lang,
                        run_id,
                        session_ctx,
                        now_ms,
                        extra_payload={"destPath": dst_cand},
                        persist_history=persist_history,
                    )
                moved = agent_file_op({"op": op_kind, "targetPath": src_cand, "destPath": dst_cand, "lastDirectory": last_dir, "nowMs": now_ms})
                msg = f"{moved['summary']}\n" + "\n".join(moved["lines"])
                finish_run(run_id, status="completed", now_ms=now_ms)
                res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

            is_list_query = bool(re.search(
                r"\b(?:thu muc|folder|directory|liet ke|list|ls|dir|tree|show files|show all|show folders|xem tat ca|xem het|danh sach)\b",
                folded,
            ))
            expect_dir = is_list_query and not any(k in folded for k in ("doc tep", "doc file", "read file", "sua tep", "sua file", "edit file", "xoa tep", "xoa file", "delete file"))
            target, ambiguous_candidates = _resolve_conversational_target(prompt, session_ctx, expect_dir=expect_dir)

            # Ambiguity clarification gate
            if ambiguous_candidates:
                session_ctx["pendingClarification"] = {
                    "originalPrompt": prompt,
                    "candidates": ambiguous_candidates[:6],
                }
                save_session_context(session_ctx, now_ms=now_ms)
                names = ", ".join(f"`{Path(c).name}`" for c in ambiguous_candidates[:6])
                msg = (
                    f"Có nhiều tệp phù hợp trong ngữ cảnh vừa rồi ({names}). Bạn muốn mình thao tác trên tệp nào?"
                    if lang == "vi"
                    else f"Multiple files match your reference ({names}). Which one would you like me to use?"
                )
                finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                res = _format_reply(msg, expression="thinking", status="awaiting_clarification", run_id=run_id, language=lang, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

            # Check if delete is requested
            if re.search(r"\b(?:xoa tep|xoa file|delete file|remove file|rm )\b", folded) or re.match(r"^(?:/agent\s+)?(?:delete|rm|xoa)\b", folded):
                if not target:
                    raise ToolError("invalid_path", "Specify which file you want to delete.")
                resolved_del = str(resolve_candidate_path(target, last_dir=last_dir))
                if is_system_path(resolved_del):
                    return _prompt_for_system_sudo("delete", resolved_del, prompt, lang, run_id, session_ctx, now_ms, persist_history=persist_history)
                agent_file_op({"op": "delete", "targetPath": resolved_del, "confirmStep": 0, "lastDirectory": last_dir, "nowMs": now_ms})
                session_ctx["pendingDeletePath"] = resolved_del
                save_session_context(session_ctx, now_ms=now_ms)
                msg = (
                    f"Xác nhận xoá tệp `{Path(resolved_del).name}` (`{resolved_del}`)? "
                    "Hãy trả lời 'đồng ý' để xoá hoặc 'huỷ' để giữ lại tệp."
                    if lang == "vi"
                    else f"Confirm deleting `{Path(resolved_del).name}` (`{resolved_del}`)? "
                         "Reply 'yes' to confirm deletion or 'cancel' to keep the file."
                )
                finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                res = _format_reply(msg, expression="alert", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

            # Check if shell command execution (`run`) is requested
            m_run = re.match(r"^(?:/agent\s+)?(?:run|exec|execute|chay lenh|chay script|chạy lệnh|chạy script)\s+([\s\S]+)$", prompt, re.IGNORECASE)
            if m_run:
                cmd_str = m_run.group(1).strip()
                work_dir = session_ctx.get("lastDirectory") or str(Path.home())
                first_tok = cmd_str.split()[0] if cmd_str.split() else ""
                if first_tok.startswith("/") and is_system_path(first_tok):
                    return _prompt_for_system_sudo(
                        "run",
                        first_tok,
                        prompt,
                        lang,
                        run_id,
                        session_ctx,
                        now_ms,
                        extra_payload={"content": cmd_str},
                        persist_history=persist_history,
                    )
                perm = check_permission("shell_run", target_path=work_dir, explicit_prompt_target=True, now_ms=now_ms)
                if not perm["allowed"]:
                    if perm.get("requiresSudo"):
                        return _prompt_for_system_sudo("run", work_dir, prompt, lang, run_id, session_ctx, now_ms, extra_payload={"content": cmd_str}, persist_history=persist_history)
                    conf = create_pending_confirmation("grant_directory", work_dir, f"Run command in {work_dir}", {"levels": ["read", "write", "execute"], "originalPrompt": prompt}, now_ms)
                    finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                    msg = f"Cần cấp quyền thực thi trong `{work_dir}` để chạy lệnh. Trả lời 'đồng ý' hoặc 'huỷ'." if lang == "vi" else f"Execution permission in `{work_dir}` is required. Reply 'yes' or 'cancel'."
                    res = _format_reply(msg, expression="thinking", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, confirm_id=conf["confirmId"], session_ctx=session_ctx)
                    u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                    return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}
                ran = agent_file_op({"op": "run", "targetPath": cmd_str, "content": cmd_str, "lastDirectory": last_dir, "nowMs": now_ms})
                out_text = "\n".join(ran["lines"])
                msg = f"{ran['summary']}\n{out_text}"
                finish_run(run_id, status="completed" if ran["status"] == "completed" else "failed", now_ms=now_ms)
                res = _format_reply(msg, expression="working" if ran["status"] == "completed" else "sad", status=ran["status"], run_id=run_id, language=lang, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

            # Check if edit / write / append is requested
            m_colon_edit = re.search(r"(?:sửa|sua|ghi|thêm|them|viết|viet|tạo|tao|edit|write|append|create)[^:]*:\s*([\s\S]+)$", prompt, re.IGNORECASE)
            is_edit_intent = bool(m_colon_edit) or any(k in folded for k in ("sua tep", "sua file", "ghi them", "them dong", "tao tep", "tao file", "edit file", "append to", "write to"))

            has_read_and_edit = any(k in folded for k in ("doc ", "tom tat", "read ", "summarize")) and is_edit_intent
            summary_part = ""
            if has_read_and_edit and target and Path(target).expanduser().is_file():
                read_step = record_step_start(run_id, 1, "file_inspect", side_effect=False, target_path=target, now_ms=now_ms)
                read_out = agent_file_op({"op": "access", "targetPath": target, "lastDirectory": last_dir, "nowMs": now_ms})
                record_step_finish(read_step, status="completed", target_path=read_out["targetPath"], detail=read_out)
                advance_turn(run_id, now_ms=now_ms)
                session_ctx["lastFile"] = read_out["targetPath"]
                session_ctx["lastDirectory"] = str(Path(read_out["targetPath"]).parent)
                preview_lines = "; ".join(read_out["lines"][:6])
                summary_part = (
                    f"Tóm tắt `{Path(read_out['targetPath']).name}`: {preview_lines}\n"
                    if lang == "vi"
                    else f"Summary of `{Path(read_out['targetPath']).name}`: {preview_lines}\n"
                )

            if is_edit_intent:
                if not target:
                    raise ToolError("invalid_path", "Specify the target file to edit or create.")
                resolved_target = str(resolve_candidate_path(target, last_dir=last_dir))
                content_to_write = m_colon_edit.group(1).strip() if m_colon_edit else ""
                if not content_to_write:
                    m_quote = re.search(r"""['"`]([^'"`]+)['"`]\s*$""", prompt)
                    content_to_write = m_quote.group(1) if m_quote else "Updated by Companion Agent"
                mode = "append" if any(k in folded for k in ("them", "append", "cuoi tep")) and Path(resolved_target).exists() else "write"

                perm = check_permission("file_write", target_path=resolved_target, explicit_prompt_target=True, now_ms=now_ms)
                if not perm["allowed"]:
                    if perm.get("requiresSudo"):
                        return _prompt_for_system_sudo(
                            "edit",
                            resolved_target,
                            prompt,
                            lang,
                            run_id,
                            session_ctx,
                            now_ms,
                            extra_payload={"content": content_to_write, "mode": mode},
                            persist_history=persist_history,
                        )
                    if perm["requiresConfirmation"]:
                        parent_dir = str(Path(resolved_target).parent)
                        conf = create_pending_confirmation(
                            "grant_directory",
                            parent_dir,
                            f"Write to {resolved_target}",
                            {"levels": ["read", "write", "execute"], "originalPrompt": prompt},
                            now_ms=now_ms,
                        )
                        finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                        msg = (
                            f"Thư mục `{parent_dir}` đã bị thu hồi quyền ghi. Bạn có đồng ý cấp lại quyền cho `{parent_dir}` không? (Trả lời 'đồng ý' hoặc 'huỷ')"
                            if lang == "vi"
                            else f"Directory `{parent_dir}` had write access revoked. Do you grant write permission for `{parent_dir}`? (Reply 'yes' or 'cancel')"
                        )
                        res = _format_reply(msg, expression="thinking", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, confirm_id=conf["confirmId"], session_ctx=session_ctx)
                        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                        return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

                step_id = record_step_start(run_id, 2 if summary_part else 1, "file_mutate", side_effect=True, target_path=resolved_target, now_ms=now_ms)
                edited = agent_file_op({
                    "op": "edit",
                    "targetPath": resolved_target,
                    "content": content_to_write,
                    "mode": mode,
                    "lastDirectory": last_dir,
                    "nowMs": now_ms,
                })
                m_rcpt = re.search(r"(rcpt_[0-9a-f]{16})", edited["summary"])
                rcpt_id = m_rcpt.group(1) if m_rcpt else ""
                if rcpt_id:
                    receipts.append(rcpt_id)
                record_step_finish(step_id, status="completed", target_path=edited["targetPath"], receipt_id=rcpt_id, detail=edited)
                advance_turn(run_id, now_ms=now_ms)

                session_ctx["lastFile"] = edited["targetPath"]
                session_ctx["lastDirectory"] = str(Path(edited["targetPath"]).parent)
                save_session_context(session_ctx, now_ms=now_ms)

                msg = summary_part + (
                    f"Đã cập nhật `{Path(edited['targetPath']).name}` và kiểm tra đọc lại thành công ({edited['summary']})."
                    if lang == "vi"
                    else f"Updated `{Path(edited['targetPath']).name}` and verified readback ({edited['summary']})."
                )
                finish_run(run_id, status="completed", now_ms=now_ms)
                res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, receipts=receipts, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

            # Default file/directory inspect & full listing
            if not target:
                target = session_ctx.get("lastDirectory") or str(Path.home())
            resolved_target = str(resolve_candidate_path(target, last_dir=last_dir))
            perm = check_permission("file_read", target_path=resolved_target, explicit_prompt_target=True, now_ms=now_ms)
            if not perm["allowed"]:
                if perm.get("requiresSudo"):
                    return _prompt_for_system_sudo(
                        "access",
                        resolved_target,
                        prompt,
                        lang,
                        run_id,
                        session_ctx,
                        now_ms,
                        persist_history=persist_history,
                    )
                if perm.get("requiresConfirmation"):
                    target_dir = resolved_target if Path(resolved_target).is_dir() else str(Path(resolved_target).parent)
                    conf = create_pending_confirmation(
                        "grant_directory",
                        target_dir,
                        f"Read {resolved_target}",
                        {"levels": ["read", "write", "execute"], "originalPrompt": prompt},
                        now_ms=now_ms,
                    )
                    finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                    msg = (
                        f"Thư mục `{target_dir}` đã bị thu hồi quyền. Bạn có đồng ý cấp lại quyền cho `{target_dir}` không? (Trả lời 'đồng ý' hoặc 'huỷ')"
                        if lang == "vi"
                        else f"Directory `{target_dir}` had access revoked. Do you grant permission for `{target_dir}`? (Reply 'yes' or 'cancel')"
                    )
                    res = _format_reply(msg, expression="thinking", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, confirm_id=conf["confirmId"], session_ctx=session_ctx)
                    u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                    return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}
                raise AgentPermissionError(perm["reason"], f"Read access to '{resolved_target}' is not permitted ({perm['reason']}).")

            include_hidden = _wants_all_or_hidden(prompt)
            step_id = record_step_start(run_id, 1, "file_inspect", side_effect=False, target_path=resolved_target, now_ms=now_ms)
            inspected = agent_file_op({
                "op": "access",
                "targetPath": resolved_target,
                "includeHidden": include_hidden,
                "lastDirectory": last_dir,
                "pageSize": 500,
                "nowMs": now_ms,
            })
            record_step_finish(step_id, status="completed", target_path=inspected["targetPath"], detail=inspected)
            advance_turn(run_id, now_ms=now_ms)

            resolved_p = Path(inspected["targetPath"])
            if resolved_p.is_dir():
                session_ctx["lastDirectory"] = str(resolved_p)
                files_in_dir = [
                    str(p.resolve(strict=False)) for p in sorted(resolved_p.iterdir(), key=lambda x: x.name.lower())
                    if p.is_file()
                ][:20]
                session_ctx["recentCandidates"] = files_in_dir
                if len(files_in_dir) == 1:
                    session_ctx["lastFile"] = files_in_dir[0]
            else:
                session_ctx["lastFile"] = str(resolved_p)
                session_ctx["lastDirectory"] = str(resolved_p.parent)
                session_ctx["recentCandidates"] = [str(resolved_p)]
            save_session_context(session_ctx, now_ms=now_ms)

            # Complete listing: include ALL lines returned by tool_store (no [:10] slicing!)
            body_lines = "\n".join(f"- {ln}" for ln in inspected["lines"])
            display_name = resolved_p.name or str(resolved_p)
            msg = (
                f"Đã đọc `{display_name}` ({inspected['summary']}):\n{body_lines}"
                if lang == "vi"
                else f"Inspected `{display_name}` ({inspected['summary']}):\n{body_lines}"
            )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

    except (ToolError, AgentPermissionError, ScreenContextError, CodeToolError, RuleStoreError, RunStoreError) as exc:
        err_code = getattr(exc, "code", "agent_error")
        err_msg = str(exc)
        advance_turn(run_id, error_signature=f"{err_code}:{err_msg}", now_ms=now_ms)
        finish_run(run_id, status="failed", now_ms=now_ms)
        msg = (
            f"Không thể thực hiện thao tác (`{err_code}`): {err_msg}"
            if lang == "vi"
            else f"Could not complete action (`{err_code}`): {err_msg}"
        )
        res = _format_reply(msg, expression="sad", status="failed", run_id=run_id, language=lang, session_ctx=session_ctx)
        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
        return {"handled": True, "intent": kind, "errorCode": err_code, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}
