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
import desktop_control
from desktop_control import DesktopControlError
import history_store
import mcp_client
from mcp_client import McpError
import memory_store
import rag_store
from rag_store import RagStoreError
import skill_store
from skill_store import SkillError
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
    record_step_finish as _store_record_step_finish,
    record_step_start as _store_record_step_start,
    save_session_context,
    start_run,
)
from screen_context import ScreenContextError, capture_screenshot_file, observe_active_window, perform_gui_action
from tool_store import (
    ToolError,
    agent_file_op,
    confirm_edit,
    execute_system_sudo_op,
    propose_edit,
    resolve_candidate_path,
)
import web_tools


AGENT_CONTRACT_VERSION = 1

_ACTIVE_RUN_STEPS: list[dict] = []
_ACTIVE_RUN_START_MS: int = 0
_ACTIVE_STEP_META: dict[str, tuple[str, str]] = {}


def _reset_activity_steps(now_ms: int | None = None) -> None:
    global _ACTIVE_RUN_STEPS, _ACTIVE_RUN_START_MS
    _ACTIVE_RUN_STEPS = []
    _ACTIVE_RUN_START_MS = int(now_ms or int(time.time() * 1000))
    _ACTIVE_STEP_META.clear()


def _emit_activity_step(label: str, kind: str = "step", status: str = "running") -> None:
    text = str(label or "").strip()
    if not text:
        return
    if not text.endswith("›"):
        text = text.rstrip(".") + " ›"
    now = int(time.time() * 1000)
    elapsed_s = max(0.1, round((now - (_ACTIVE_RUN_START_MS or now)) / 1000.0, 1))
    entry = {"label": text, "kind": kind, "status": status, "elapsedSec": elapsed_s}
    updated = False
    for idx in range(len(_ACTIVE_RUN_STEPS) - 1, -1, -1):
        if _ACTIVE_RUN_STEPS[idx].get("kind") == kind:
            _ACTIVE_RUN_STEPS[idx] = entry
            updated = True
            break
    if not updated and (not _ACTIVE_RUN_STEPS or _ACTIVE_RUN_STEPS[-1].get("label") != text):
        _ACTIVE_RUN_STEPS.append(entry)
    if (
        os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1"
        and not os.environ.get("AQUA_TEST_ENDPOINT")
        and not os.environ.get("AI_FIXTURE_URL")
    ):
        try:
            sys.stderr.write(
                "__AQUA_STEP__:"
                + json.dumps(
                    {
                        "current": text,
                        "kind": kind,
                        "status": status,
                        "steps": [s["label"] for s in _ACTIVE_RUN_STEPS[-10:]],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.stderr.flush()
        except Exception:
            pass


def record_step_start(run_id, seq, tool_name, side_effect=False, target_path="", detail=None, now_ms=None, **kwargs):
    step_id = _store_record_step_start(
        run_id, seq, tool_name, side_effect=side_effect, target_path=target_path, detail=detail, now_ms=now_ms, **kwargs
    )
    t_name = str(tool_name or "tool")
    t_target = str(target_path or "").strip()
    _ACTIVE_STEP_META[str(step_id)] = (t_name, t_target)
    if t_name in ("web_search", "github_search", "wikipedia_search"):
        lbl = f'Searching web: "{t_target[:44]}" ›' if t_target else "Searching web ›"
    elif t_name == "fetch_url":
        lbl = f"Reading webpage: {web_tools.short_source_domain(t_target)} ›"
    elif t_name == "browser_open":
        lbl = f"Opening browser: {t_target[:40]} ›"
    elif t_name == "screen_observe":
        lbl = "Capturing active window (Niri+Vision) ›"
    elif t_name == "file_inspect":
        lbl = f"Inspecting {Path(t_target).name if t_target else 'workspace'} ›"
    elif t_name in ("file_edit", "write_file"):
        lbl = f"Editing {Path(t_target).name if t_target else 'file'} ›"
    elif t_name in ("run_command", "exec"):
        lbl = f"Running command: {t_target[:42]} ›"
    elif t_name == "git_inspect":
        lbl = f"Checking Git in {Path(t_target).name if t_target else 'repository'} ›"
    else:
        lbl = f"Running {t_name}" + (f": {Path(t_target).name}" if t_target else "") + " ›"
    _emit_activity_step(lbl, kind=f"step_{step_id}", status="running")
    return step_id


def record_step_finish(step_id, status="completed", target_path="", receipt_id="", detail=None, **kwargs):
    res = _store_record_step_finish(
        step_id, status=status, target_path=target_path, receipt_id=receipt_id, detail=detail
    )
    t_name, t_target = _ACTIVE_STEP_META.get(str(step_id), ("tool", str(target_path or "")))
    if not t_target and target_path:
        t_target = str(target_path)
    det = detail if isinstance(detail, dict) else {}
    if t_name in ("web_search", "github_search", "wikipedia_search"):
        cnt = det.get("count")
        cnt_str = f" ({cnt} sources)" if cnt is not None else ""
        lbl = f'Searched web: "{t_target[:38]}"{cnt_str} ›' if t_target else f"Searched web{cnt_str} ›"
    elif t_name == "fetch_url":
        lbl = f"Read webpage: {web_tools.short_source_domain(t_target)} ›"
    elif t_name == "browser_open":
        lbl = f"Opened browser: {t_target[:40]} ›"
    elif t_name == "screen_observe":
        src = det.get("source") or "Niri+Vision"
        lbl = f"Captured active window ({src}) ›"
    elif t_name == "file_inspect":
        lbl = f"Inspected {Path(t_target).name if t_target else 'workspace'} ›"
    elif t_name in ("file_edit", "write_file"):
        lbl = f"Edited {Path(t_target).name if t_target else 'file'} ›"
    elif t_name in ("run_command", "exec"):
        lbl = f"Ran command: {t_target[:42]} ›"
    elif t_name == "git_inspect":
        lbl = f"Checked Git in {Path(t_target).name if t_target else 'repository'} ›"
    else:
        lbl = f"Ran {t_name}" + (f": {Path(t_target).name}" if t_target else "") + " ›"
    _emit_activity_step(lbl, kind=f"step_{step_id}", status=status)
    return res


def _resolve_live_llm(options: dict) -> tuple[str, str]:
    llm_ep = str(options.get("endpoint") or os.environ.get("AQUA_LLM_ENDPOINT") or "").strip()
    llm_model = str(options.get("model") or os.environ.get("AQUA_LLM_MODEL") or "").strip()
    need_probe = not llm_ep or "/v1/" not in llm_ep or not llm_model or llm_model.startswith("gguf:")
    if need_probe and str(options.get("runSource") or "") == "interactive":
        try:
            with urllib.request.urlopen("http://127.0.0.1:1234/v1/models", timeout=1.0) as m_resp:
                m_data = json.loads(m_resp.read().decode("utf-8", errors="replace"))
                m_items = [
                    str(item.get("id") or "").strip()
                    for item in (m_data.get("data") if isinstance(m_data, dict) else [])
                    if isinstance(item, dict)
                    and str(item.get("id") or "").strip()
                    and "embed" not in str(item.get("id") or "").lower()
                ]
                if m_items:
                    llm_ep = "http://127.0.0.1:1234/v1/chat/completions"
                    clean_cand = re.sub(r"^(?:lmstudio-|ollama-|gguf:)", "", llm_model)
                    llm_model = clean_cand if clean_cand in m_items else m_items[0]
        except Exception:
            pass
    if llm_ep and ("/v1/" in llm_ep or ":1234" in llm_ep):
        plan_endpoint = (
            llm_ep
            if llm_ep.endswith("/chat/completions")
            else (llm_ep.rstrip("/") + "/v1/chat/completions" if "/v1" not in llm_ep else llm_ep.rstrip("/") + "/chat/completions")
        )
        clean_model = re.sub(r"^(?:lmstudio-|ollama-|gguf:)", "", llm_model)
        return plan_endpoint, clean_model
    return "", ""


def _extract_plan_search_query(goal_text: str, lang: str = "vi") -> str:
    q = str(goal_text or "").strip()
    # Strip parenthetical meta instructions such as "(tra cứu thông tin trên internet nếu cần thiết)"
    q = re.sub(r"\([^)]*(?:internet|web|mạng|mang|tra cứu|tra cuu|tìm kiếm|tim kiem|search|online|google)[^)]*\)", "", q, flags=re.IGNORECASE)
    # Strip trailing clauses about searching internet
    q = re.sub(
        r"[,;.]?\s*(?:hãy\s+|vui lòng\s+)?(?:tra cứu|tra cuu|tìm kiếm|tim kiem|search)\s+(?:thông tin\s+|thong tin\s+)?(?:trên\s+|tren\s+)?(?:internet|mạng|mang|web|google).*$",
        "",
        q,
        flags=re.IGNORECASE,
    )
    # Strip leading filler and time markers that weaken search relevance
    q = re.sub(r"^(?:cho\s+|về\s+|ve\s+|for\s+|about\s+)+", "", q, flags=re.IGNORECASE)
    q = re.sub(
        r"\b(?:vào\s+ngày\s+mai|ngày\s+mai|vào\s+lúc\s+\d+\s*(?:h|giờ|:)\s*\d*(?:\s*(?:sáng|trưa|chiều|tối))?|lúc\s+\d+\s*(?:h|giờ|:)\s*\d*(?:\s*(?:sáng|trưa|chiều|tối))?|tomorrow(?:\s+at\s+\d+[:\d]*\s*(?:am|pm)?)?)\b",
        " ",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(r"\b(?:buổi\s+|chuyến\s+)\b", " ", q, flags=re.IGNORECASE)
    q = re.sub(r"[,;]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if lang == "vi" and re.search(r"\b(?:dã ngoại|da ngoai|picnic|du lịch|du lich)\b", q, flags=re.IGNORECASE):
        if not re.search(r"\b(?:địa điểm|dia diem|kinh nghiệm|kinh nghiem)\b", q, flags=re.IGNORECASE):
            q = "địa điểm " + q
    return q[:110] or str(goal_text or "").strip()[:110]

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
            "description": "Execute a shell command in the user session within a permitted workspace.",
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
    {
        "type": "function",
        "function": {
            "name": "desktop_control",
            "description": "Control Niri windows, workspaces, applications, audio volume, brightness, media playback, clipboard, or notifications.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list_windows",
                            "focus_window",
                            "close_window",
                            "switch_workspace",
                            "launch_app",
                            "volume",
                            "brightness",
                            "media",
                            "clipboard_get",
                            "clipboard_set",
                            "notify",
                        ],
                    },
                    "target": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_telemetry",
            "description": "Get detailed Linux system telemetry: CPU load, RAM/Swap, disk space, battery, thermal sensors, and top processes.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_manage",
            "description": "List top processes, search processes by name, or terminate a process by PID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "search", "kill"]},
                    "query": {"type": "string"},
                    "pid": {"type": "integer"},
                    "signal": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_inspect",
            "description": "Inspect Git repository status, branch, log, or diff in a workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "log", "diff", "branch"]},
                    "workspace": {"type": "string"},
                    "args": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": "Search the Companion RAG knowledge base across docs, Pocket files, and registered user sources with citations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "page": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_call",
            "description": "Invoke a Model Context Protocol (MCP) tool on an enabled MCP server, including Eyes & Hands servers (`computer-use-linux` for screenshots, AT-SPI2 UI tree, mouse/keyboard & window actions; `playwright-mcp` & `chrome-devtools-mcp` for browser DOM/snapshots/actions; `web-search-mcp` & `fetch-mcp` for live web search and URL reading; `desktop-commander` for terminal/processes; `companion-system` for system/RAG/Pocket).",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["tool"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web (DuckDuckGo, Wikipedia, GitHub, SearXNG) or open a search query/URL in the user's browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "engine": {"type": "string", "enum": ["auto", "duckduckgo", "wikipedia", "github", "google", "youtube"]},
                    "openBrowser": {"type": "boolean"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch any web URL and extract clean readable text, headings, and links.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "maxChars": {"type": "integer"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_run",
            "description": "Execute a Companion Agent Skill (`SKILL.md`) by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["skill"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_manage",
            "description": "Hermes self-learning skill manager: list, inspect, create, patch/refine, or learn Agent Skills (`SKILL.md`) and `references/` files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "inspect", "create", "patch", "learn", "write_reference", "reflections"],
                    },
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "instructions": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "note": {"type": "string"},
                    "target": {"type": "string"},
                    "reference": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["action"],
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
    raw = re.sub(
        r"^/(?:agent\s+)?(?:access|list|ls|dir|tree|open|cd|inspect|read|edit|write|append|create|copy|cp|cut|move|mv|delete|rm|run|exec|rag|mcp|skills?|learn|workflow|reflect|plan|git|system|processes|memory|permissions|rules|undo)\b\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
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
        r"^(?:/agent\s+|/)?(?:access|list|ls|dir|open|cd|xem|doc|liet ke)\s+home\s*$",
        folded,
    ):
        return str(home)

    # 2. Explicit path starting with ~/ or / or ./ or ../
    m = re.search(r"""(['"`]?)((?:~/|/|\./|\.\./)[^\s'"`:,;!?()\[\]{}]+)\1""", raw)
    if m and not m.group(2).startswith("/agent"):
        return m.group(2).rstrip(".,;!?()[]{}")

    # 3. Direct command prefix: `access <arg>`, `list <arg>`, `ls <arg>`, `dir <arg>`, `open <arg>`, `cd <arg>`, `edit <arg>`
    m_cmd = re.match(
        r"^(?:/agent\s+|/)?(?:access|list|ls|dir|open|cd|inspect|read|edit|write|append|create)\s+(.+?)\s*$",
        raw,
        re.IGNORECASE,
    )
    if m_cmd:
        arg = m_cmd.group(1).split(":", 1)[0].strip().strip("'\"`").rstrip(".,;!?")
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
        r"""\b([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*\.(?:txt|md|py|qml|js|cjs|mjs|ts|tsx|jsx|json|rs|toml|sh|bash|zsh|fish|yaml|yml|html|css|scss|vue|svelte|c|cpp|cc|h|hpp|go|java|kt|rb|php|lua|sql|xml|csv|tsv|env|nix|log|conf|cfg|ini|service|tar|gz|zip|pdf|docx|jpg|png|AppImage))\b""",
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

    # Permission management via prompt or `/permissions`
    if re.match(r"^/permissions?\b", folded):
        if any(w in folded for w in ("revoke", "thu hoi", "xoa", "clear")):
            return {"kind": "permission_manage", "subkind": "revoke", "language": lang}
        if any(w in folded for w in ("grant", "allow", "cap")):
            return {"kind": "permission_manage", "subkind": "grant", "language": lang}
        return {"kind": "permission_manage", "subkind": "inspect", "language": lang}
    if any(k in folded for k in ("xem quyen", "quyen hien tai", "kiem tra quyen", "list permissions", "show permissions", "what permissions")):
        return {"kind": "permission_manage", "subkind": "inspect", "language": lang}
    if re.search(r"\b(?:thu hoi quyen|xoa quyen|tat quyen|revoke permission|revoke all permissions|clear permissions)\b", folded):
        return {"kind": "permission_manage", "subkind": "revoke", "language": lang}
    if re.search(r"\b(?:cap quyen|cho phep truy cap|grant permission|allow access to)\b", folded):
        return {"kind": "permission_manage", "subkind": "grant", "language": lang}

    # Rule management via prompt or `/rules`
    if re.match(r"^/rules?\b", folded):
        if any(w in folded for w in ("disable", "delete", "remove", "stop", "tat", "xoa", "huy")):
            return {"kind": "rule_manage", "subkind": "disable", "language": lang}
        if any(w in folded for w in ("create", "add", "when", "every", "tao", "them", "khi", "moi")):
            return {"kind": "rule_manage", "subkind": "create", "language": lang}
        return {"kind": "rule_manage", "subkind": "list", "language": lang}
    if any(k in folded for k in ("danh sach quy tac", "xem quy tac", "cac quy tac", "list rules", "show rules", "active rules")):
        return {"kind": "rule_manage", "subkind": "list", "language": lang}
    if re.search(r"\b(?:tat quy tac|xoa quy tac|huy quy tac|disable rule|delete rule|remove rule|stop all rules)\b", folded):
        return {"kind": "rule_manage", "subkind": "disable", "language": lang}
    if re.search(r"\b(?:tao quy tac|them quy tac|khi su kien|khi bat dau|sau khi ket thuc focus|moi ngay luc|create rule|when planner|when focus ends|every day at)\b", folded):
        return {"kind": "rule_manage", "subkind": "create", "language": lang}

    # Memory management via prompt or `/memory`
    if re.match(r"^/memor(?:y|ies)\b", folded):
        if any(w in folded for w in ("forget", "delete", "remove", "clear", "quen", "xoa")):
            return {"kind": "memory_manage", "subkind": "forget", "language": lang}
        if any(w in folded for w in ("save", "add", "remember", "luu", "nho", "ghi")):
            return {"kind": "memory_manage", "subkind": "save", "language": lang}
        return {"kind": "memory_manage", "subkind": "list", "language": lang}
    if re.search(r"^(?:hay\s+|vui long\s+|please\s+)?(?:nho rang|ghi nho|luu vao bo nho|remember that|save to memory|please remember)\b", folded):
        return {"kind": "memory_manage", "subkind": "save", "language": lang}
    if re.search(r"\b(?:xem bo nho|xem cac ghi nho|nhung gi ban nho|list memories|show memories|what do you remember)\b", folded):
        return {"kind": "memory_manage", "subkind": "list", "language": lang}
    if re.search(r"^(?:hay\s+|vui long\s+|please\s+)?(?:quen ghi nho|xoa ghi nho|forget memory|delete memory)\b", folded):
        return {"kind": "memory_manage", "subkind": "forget", "language": lang}

    # Pocket management via prompt or `/pocket`
    if re.match(r"^/pocket\b", folded) or re.search(r"\b(?:trong pocket|tep trong pocket|xem pocket|them .* vao pocket|bo .* khoi pocket|pocket files|in pocket|add .* to pocket|remove .* from pocket)\b", folded):
        return {"kind": "pocket_manage", "language": lang}

    # Hermes Self-Learning, Workflow Adaptation & Agent Skills (`SKILL.md`)
    if (
        re.match(r"^(?:/skills?|skills?|/learn|/workflow|/reflect)\b", folded)
        or re.search(
            r"\b(?:danh sach skill|danh sach ky nang|xem skill|xem ky nang|cac skill|cac ky nang|chay skill|dung skill|dung ky nang|tao skill|tao ky nang|xoa skill|bat skill|tat skill|cap nhat skill|dieu chinh skill|chinh sua skill|bo sung skill|tu hoc|hoc quy trinh|luu quy trinh|dieu chinh quy trinh|rut kinh nghiem|list skills|show skills|run skill|use skill|create skill|delete skill|enable skill|disable skill|patch skill|update skill|learn skill|learn workflow|adapt workflow|self-learn|workflow reflections|system-diagnostics|code-assistant|rag-researcher|focus-planner|screen-copilot|desktop-organizer|window-workspace-layout|git-daily-brief|system-tuner|clipboard-transformer)\b",
            folded,
        )
    ):
        return {"kind": "skill_manage", "language": lang}

    # Direct `/<skill-name>` slash invocation for any installed/learned skill
    if raw.startswith("/") and len(raw) > 1:
        first_cmd = raw[1:].split()[0].strip().lower()
        if first_cmd:
            try:
                for sk in skill_store.list_skills():
                    if sk.get("enabled", True) and sk["name"].lower() == first_cmd:
                        return {"kind": "skill_manage", "directSkill": sk["name"], "language": lang}
            except Exception:
                pass

    # Screen / window observation & GUI action
    if (
        re.search(
            r"\b(?:xem man hinh|doc man hinh|nhin man hinh|quet man hinh|kiem tra man hinh|chup man hinh|chup anh man hinh|chup lai man hinh|thay man hinh|doc cua so|cua so hien tai|ung dung dang mo|man hinh dang co gi|tren man hinh|mat va tay|scan screen|scan window|what is on my screen|what is on the screen|what do you see|can you see.*screen|can you see now|see my screen|see the screen|read the screen|read current window|inspect active window|look at my screen|take a screenshot|capture screen|screenshot|eyes and hands|use your eyes)\b",
            folded,
        )
        or re.match(r"^(?:/agent\s+|/)?(?:access\s+)?(?:screen|scan|window|app|desktop\s+app|eyes|screenshot)\s*$", folded)
    ):
        wants_shot = bool(
            re.search(r"\b(?:chup man hinh|chup anh man hinh|chup lai man hinh|take a screenshot|capture screen|screenshot)\b", folded)
            or re.match(r"^(?:/agent\s+|/)?screenshot\s*$", folded)
        )
        return {"kind": "screen_observe", "captureScreenshot": wants_shot, "language": lang}
    if re.search(r"\b(?:chuyen sang cua so|focus window|focus cua so|kiem tra cua so)\b", folded):
        return {"kind": "gui_action", "language": lang}

    # Coding / LSP / repo map / test execution / structured planning (`/plan`)
    if re.match(r"^/(?:clarify|grill-me|grill|ask)\b", folded):
        return {"kind": "clarify_task", "language": lang}
    if re.match(r"^/plan\s*$", folded):
        return {"kind": "clarify_task", "subkind": "plan", "language": lang}
    if re.match(r"^/plan\b", folded) or re.search(r"\b(?:lap ke hoach|len ke hoach|thiet ke ke hoach|implementation plan|create a plan for)\b", folded):
        return {"kind": "code_task", "subkind": "plan", "language": lang}
    if re.match(r"^/tests?\b", folded) or re.search(r"\b(?:chay kiem tra|chay test|run tests|run project tests|test du an)\b", folded):
        return {"kind": "code_task", "subkind": "run_tests", "language": lang}
    if re.match(r"^/diagnostics?\b", folded) or re.search(r"\b(?:kiem tra loi|diagnostics|lsp|sua loi trong du an|fix error in project|check errors in)\b", folded):
        return {"kind": "code_task", "subkind": "diagnostics_or_fix", "language": lang}
    if re.search(r"\b(?:tim symbol|tim ham|tim dinh nghia|tim tham chieu|find symbol|find definition|find references|search code|cau truc du an|repo map)\b", folded):
        return {"kind": "code_task", "subkind": "symbol_or_search", "language": lang}

    # Knowledge / RAG search & source management
    if (
        re.match(r"^(?:/rag|rag)\b", folded)
        or re.search(
            r"\b(?:tim trong tai lieu|theo tai lieu|tai lieu companion|shader validation|portal travel|search docs|in the documentation|tra cuu rag|tim trong rag|nguon rag|them vao rag|xoa khoi rag|search rag|rag sources|add to rag|remove from rag|knowledge base)\b",
            folded,
        )
    ):
        return {"kind": "rag_query", "language": lang}

    # MCP (Model Context Protocol) server, tool & resource management
    if (
        re.match(r"^(?:/mcp|mcp)\b", folded)
        or re.search(
            r"\b(?:danh sach mcp|xem mcp|liet ke mcp|cong cu mcp|goi mcp|them mcp|xoa mcp|tai nguyen mcp|mcp server|mcp servers|mcp tool|mcp tools|mcp resource|mcp resources|list mcp|call mcp|add mcp|remove mcp)\b",
            folded,
        )
    ):
        return {"kind": "mcp_manage", "language": lang}

    # Web Search, URL Reader & Browser Control (`web-search-mcp`, `fetch-mcp`, `playwright-mcp`)
    if re.match(r"^/(?:web|search|browser|fetch)\b", folded):
        if re.match(r"^/fetch\b", folded):
            return {"kind": "web_task", "subkind": "fetch_url", "language": lang}
        if re.match(r"^/browser\b", folded):
            return {"kind": "web_task", "subkind": "open_browser", "language": lang}
        return {"kind": "web_task", "subkind": "search", "language": lang}
    if re.search(
        r"\b(?:doc trang web|doc trang|doc link|doc url|tom tat trang|tom tat link|xem trang web|fetch url|read url|read webpage|summarize url|inspect url)\b",
        folded,
    ) or (
        re.search(r"https?://[^\s'\"`<>]+", raw)
        and re.search(r"\b(?:doc|xem|tom tat|kiem tra|read|fetch|summarize|inspect)\b", folded)
    ):
        return {"kind": "web_task", "subkind": "fetch_url", "language": lang}
    if re.search(
        r"\b(?:mo trinh duyet|vao trinh duyet|mo browser|mo trang web|mo web|mo link|mo url|mo youtube|vao youtube|len youtube|tren youtube|mo google|vao google|open browser|open url|open website|open in browser|open youtube)\b",
        folded,
    ) or re.search(
        r"\b(?:mo bai hat|mo bai nhac|mo bai|phat bai hat|phat bai nhac|phat bai|bat bai hat|bat bai nhac|bat bai|nghe bai hat|nghe bai|mo ca khuc|phat ca khuc|play song|play music|play video)\s+\S+",
        folded,
    ):
        return {"kind": "web_task", "subkind": "open_browser", "language": lang}
    if re.search(
        r"\b(?:tim tren web|tim kiem tren web|tim tren mang|tim kiem tren mang|tim tren internet|tim kiem tren internet|tra cuu tren web|tra cuu tren mang|tra cuu tren internet|tra cuu thong tin tren internet|tim google|search google|search web|web search|search the internet|search online|tim tren github|search github|tim tren wikipedia|search wikipedia|tra cuu wikipedia)\b",
        folded,
    ):
        return {"kind": "web_task", "subkind": "search", "language": lang}

    # Preference update via prompt
    if re.search(r"\b(?:doi tinh cach|chinh tinh cach|set personality|change personality|doi giong dieu|set dialogue tone)\b", folded):
        return {"kind": "preference_manage", "language": lang}

    # Git repository inspection & tasks
    if (
        re.match(r"^(?:/git|git\s+(?:status|log|diff|branch))\b", folded)
        or re.search(
            r"\b(?:kiem tra git|trang thai git|git status|git log|git diff|git branch|nhanh git|lich su commit|recent commits)\b",
            folded,
        )
    ):
        return {"kind": "git_task", "language": lang}

    # System telemetry, process management, network & systemd service control
    if re.match(r"^/process(?:es)?\b", folded) or re.search(
        r"\b(?:danh sach tien trinh|cac tien trinh|tien trinh dang chay|top processes|running processes|list processes|tim tien trinh|find process|search process|dung tien trinh|tat tien trinh|kill process|kill pid)\b",
        folded,
    ):
        return {"kind": "system_control", "subkind": "process", "language": lang}
    if re.search(
        r"\b(?:kiem tra mang|trang thai mang|dia chi ip|network status|ip address|kiem tra service|trang thai service|systemd service|service status|restart service|khoi dong lai service)\b",
        folded,
    ):
        return {"kind": "system_control", "subkind": "network_or_service", "language": lang}
    if re.match(r"^/system\b", folded) or re.search(
        r"\b(?:kiem tra he thong|thong tin he thong|tinh trang he thong|suc khoe he thong|system status|system info|system health|system telemetry|dung luong ram|bo nho ram|su dung cpu|cpu usage|ram usage|dung luong o dia|disk space|disk usage|tinh trang pin|battery status|nhiet do may|nhiet do cpu|cpu temperature)\b",
        folded,
    ):
        return {"kind": "system_control", "subkind": "telemetry", "language": lang}

    # Desktop, window, workspace, app launch, audio, brightness, media, clipboard & notification control
    if re.search(
        r"\b(?:danh sach cua so|cac cua so dang mo|liet ke cua so|list windows|open windows|dong cua so|close window|chuyen workspace|switch workspace|go to workspace)\b",
        folded,
    ):
        return {"kind": "desktop_control", "subkind": "windows", "language": lang}
    if re.search(
        r"^(?:hay\s+|vui long\s+|please\s+)?(?:mo ung dung|chay ung dung|khoi dong ung dung|open app|launch app|start app)\s+.+$",
        folded,
    ):
        return {"kind": "desktop_control", "subkind": "launch", "language": lang}
    if re.search(
        r"\b(?:tang am luong|giam am luong|dat am luong|chinh am luong|am luong|tat tieng|bat tieng|volume up|volume down|set volume|mute audio|unmute audio)\b",
        folded,
    ):
        return {"kind": "desktop_control", "subkind": "volume", "language": lang}
    if re.search(
        r"\b(?:tang do sang|giam do sang|dat do sang|chinh do sang|do sang man hinh|brightness up|brightness down|set brightness|screen brightness)\b",
        folded,
    ):
        return {"kind": "desktop_control", "subkind": "brightness", "language": lang}
    if re.search(
        r"\b(?:tam dung nhac|tiep tuc nhac|phat nhac|bai tiep theo|bai truoc do|play media|pause media|toggle media|next track|previous track)\b",
        folded,
    ):
        return {"kind": "desktop_control", "subkind": "media", "language": lang}
    if re.search(
        r"\b(?:doc clipboard|xem clipboard|noi dung clipboard|read clipboard|get clipboard|copy vao clipboard|ghi vao clipboard|luu vao clipboard|set clipboard|copy to clipboard)\b",
        folded,
    ):
        return {"kind": "desktop_control", "subkind": "clipboard", "language": lang}
    if re.search(
        r"\b(?:gui thong bao|tao thong bao|send notification|desktop notification|notify me)\b",
        folded,
    ):
        return {"kind": "desktop_control", "subkind": "notify", "language": lang}

    # Undo / rollback
    if re.match(r"^/(?:undo|rollback)\b", folded) or re.search(r"\b(?:hoan tac|undo|rollback|quay lai buoc truoc|khoi phuc lai|khoi phuc thao tac vua roi|bo thay doi vua roi|revert last change|undo last action)\b", folded):
        return {"kind": "file_task", "subkind": "rollback", "language": lang}

    # Multi-step or single-step file / folder operations in natural language or shorthand
    if (
        re.search(
            r"\b(?:chay lenh|chay script|chay file|thuc thi lenh|run command|run script|execute command|doc thu muc|liet ke|xem thu muc|mo thu muc|kiem tra thu muc|truy cap|xem tat ca|xem het|danh sach file|danh sach tep|danh sach thu muc|co nhung file nao|co gi trong thu muc|tim tep|doc tep|doc file|xem file|xem tep|kiem tra file|kiem tra tep|tom tat tep|sua tep|sua file|chinh sua tep|chinh sua file|sua code|chinh sua code|mo trinh sua|de sua|ghi vao|viet vao|them vao|them dong|tao tep|tao file|sao chep|di chuyen|cat file|cat tep|doi ten file|doi ten tep|xoa tep|xoa file|read folder|list directory|list files|list all|list folders|list everything|list items|show files|show all|show folders|show directory|what files|what is in|read file|inspect file|check file|summarize|edit file|edit code|edit this|edit that|open in editor|open editor|create file|copy file|move file|cut file|rename file|delete file)\b",
            folded,
        )
        or re.match(
            r"^(?:/agent\s+|/)?(?:(?:hay|vui long|lam on|giup toi|giup minh|toi muon|minh muon|please|can you|could you|help me|i want to)\s+)*(?:access|list|ls|dir|tree|open|cd|edit|write|append|create|copy|cp|sao chep|cut|cat|move|mv|di chuyen|chuyen|doi ten|rename|delete|rm|xoa|run|exec|execute|chay|thuc thi)\b",
            folded,
        )
        or re.search(
            r"\b(?:chinh sua|sua|edit|mo|doc|xem|kiem tra|truy cap|tao|ghi|viet|them|sao chep|copy|di chuyen|move|cat|cut|doi ten|rename|xoa|delete|chay|run)\b.*(?:~/|/|\.\.?/|\b[a-z0-9_.\-]+\.(?:txt|md|py|qml|js|cjs|mjs|ts|tsx|jsx|json|rs|toml|sh|bash|zsh|fish|yaml|yml|html|css|scss|vue|svelte|c|cpp|cc|h|hpp|go|java|kt|rb|php|lua|sql|xml|csv|tsv|env|nix|log|conf|cfg|ini|service)\b)",
            folded,
        )
    ):
        return {"kind": "file_task", "subkind": "general", "language": lang}

    if re.match(
        r"^(?:hay\s+|vui long\s+|please\s+|giup toi\s+|giup minh\s+|help me\s+)?(?:toi uu|toi uu hoa|viet script|viet code|sua loi|thiet ke|xay dung|cai thien|optimize|write a script|write code|fix bug|fix errors?|design|build|refactor)(?:\s+(?:giup toi|giup minh|for me|he thong|system|du an|project|app|ung dung|code|giao dien|ui))?\s*[.?!]*$",
        folded,
    ):
        return {"kind": "clarify_task", "language": lang}

    return {"kind": "chat", "language": lang}


def _is_plus_iq_model(model_id: str) -> bool:
    lowered = str(model_id or "").strip().lower()
    return any(
        token in lowered
        for token in (
            "plusiq",
            "davidau",
            "defiant-fable",
            "heretic",
            "neo-imatrix",
            "uncensored-heretic",
        )
    )


def _is_qwen35_hybrid_model(model_id: str) -> bool:
    lowered = str(model_id or "").strip().lower()
    return _is_plus_iq_model(lowered) or any(
        token in lowered for token in ("qwen3.5", "qwen3_5", "qwen3-vl", "qwen3:")
    )


def _model_supports_deep_thinking(model_id: str) -> bool:
    lowered = str(model_id or "").strip().lower()
    return _is_qwen35_hybrid_model(lowered) or any(
        token in lowered for token in ("thinking", "deepseek-r1", "qwq", "reason", "gpt-oss")
    )


def _is_mtp_model(model_id: str) -> bool:
    return bool(re.search(r"(?:^|[\s\-_.:/])mtp(?:$|[\s\-_.:/])", str(model_id or ""), re.IGNORECASE))


def classify_prompt_model_role(prompt: str, options: dict | None = None) -> dict:
    """Classify a prompt into 'action' (Desktop UI & Fast Action) vs 'thinking' (Deep Thinking).

    Supports both Dual-Model routing (switching between actionModel and thinkingModel)
    and Single-Model multi-mode routing (e.g., DavidAU/Qwen3.5 plusIQ {REASON:...}
    in-chat tags and Qwen3.5 enable_thinking toggle).
    """
    opts_raw = options if isinstance(options, dict) else {}
    nested_opts = opts_raw.get("options")
    opts = {**opts_raw, **nested_opts} if isinstance(nested_opts, dict) else opts_raw
    raw = str(prompt or "").strip()
    lang = detect_language(raw)
    folded = _fold_ascii(raw)

    role = "action"
    category = "fast_action_or_chat"
    reason = (
        "Phản hồi nhanh & điều khiển Desktop UI / MCP"
        if lang == "vi"
        else "Fast response & Desktop UI / MCP action"
    )

    # 1. Explicit slash commands
    if re.match(r"^/(?:plan|diagnostics|learn|workflow|reflect)\b", folded):
        role = "thinking"
        category = "slash_thinking"
        reason = (
            "Lệnh chuyên sâu (/plan, /diagnostics, /learn, /workflow, /reflect) cần suy luận sâu"
            if lang == "vi"
            else "Deep command (/plan, /diagnostics, /learn, /workflow, /reflect) requires deep reasoning"
        )
    elif re.match(r"^/rag\b", folded) and not re.match(r"^/rag(?:\s*$|\s+(?:list|add|remove|del|rm|clear)\b)", folded):
        role = "thinking"
        category = "rag_synthesis"
        reason = (
            "Tổng hợp tài liệu RAG đa nguồn cần model suy luận sâu"
            if lang == "vi"
            else "Multi-document RAG synthesis requires deep reasoning"
        )
    elif re.match(
        r"^/(?:scan|access|list|ls|dir|tree|open|cd|edit|write|append|create|copy|cp|cut|move|mv|delete|rm|run|exec|mcp|git|system|processes|undo|rollback|permissions|rules|memory|skills?|rag)\b",
        folded,
    ):
        role = "action"
        category = "slash_action"
        reason = (
            "Thao tác trực tiếp trên Desktop / MCP / Tệp ưu tiên tốc độ phản hồi"
            if lang == "vi"
            else "Direct Desktop / MCP / File command routed to fast UI Action model"
        )
    else:
        # 2. Check explicit deep reasoning / analysis / architecture / algorithm / debugging cues
        deep_thinking_match = re.search(
            r"\b(?:suy luan|suy nghi sau|phan tich sau|phan tich ky|phan tich nguyen nhan|giai thich chi tiet|giai thich sau|so sanh uu nhuoc|kien truc|thuat toan|toi uu hoa|debug sau|go loi|tim nguyen nhan|root cause|deadlock|race condition|lock-free|concurrency|de quy|quy hoach dong|dynamic programming|len ke hoach|lap ke hoach|ke hoach chi tiet|step by step|tung buoc|chung minh|danh gia kien truc|thiet ke he thong|system design|architecture|algorithm|complexity|big-o|trade-off|tradeoff|deep dive|deep thinking|reason about|analyze deeply|explain why|under the hood|multi-step plan|refactor strategy|triet hoc|philosophy|philosophical|sang tao sau|out of the box|lateral thinking)\b",
            folded,
        ) or re.match(
            r"^(?:tai sao|vi sao|why does|why is|how should we architect|phan tich|analyze|design a|hay phan tich|hay giai thich sau|hay lap ke hoach)\b",
            folded,
        )
        ui_action_match = re.search(
            r"\b(?:click|nhap chuot|bam|keo tha|scroll|cuon trang|go phim|type text|focus window|dong cua so|close window|chuyen workspace|switch workspace|mo ung dung|launch app|open app|am luong|volume|mute|unmute|do sang|brightness|clipboard|chup man hinh|screenshot|ocr|tren man hinh|on screen|cua so dang mo|active window|computer-use|playwright|devtools|desktop-commander)\b",
            folded,
        )
        strong_deep_cue = re.search(
            r"\b(?:explain why|why does|why is|tai sao|vi sao|phan tich sau|phan tich nguyen nhan|kien truc|thuat toan|algorithm|architecture|complexity|big-o|root cause|tim nguyen nhan|go loi|debug sau|deadlock|race condition|lock-free|step by step|tung buoc|system design|thiet ke he thong|triet hoc|philosophy|sang tao sau|out of the box|lateral thinking)\b",
            folded,
        )

        if deep_thinking_match and (not ui_action_match or strong_deep_cue):
            role = "thinking"
            category = "deep_reasoning"
            reason = (
                "Yêu cầu phân tích sâu, thuật toán, kiến trúc hoặc lập kế hoạch"
                if lang == "vi"
                else "Prompt requests deep analysis, architecture, algorithm design, or planning"
            )
        elif ui_action_match and not deep_thinking_match:
            role = "action"
            category = "desktop_ui_action"
            reason = (
                "Điều khiển giao diện Desktop, màn hình hoặc công cụ MCP"
                if lang == "vi"
                else "Desktop UI control, screen vision, or MCP tool execution"
            )
        else:
            intent_info = classify_intent(raw)
            kind = intent_info.get("kind", "chat")
            user_effort_hint = str(opts.get("thinkingEffort") or "off").strip().lower()
            if kind in ("plan_and_code", "self_learn") or (
                kind == "rag_manage" and intent_info.get("subkind") == "query"
            ):
                role = "thinking"
                category = kind
                reason = (
                    "Tác vụ lập kế hoạch mã nguồn, tự học hoặc truy vấn RAG chuyên sâu"
                    if lang == "vi"
                    else "Code planning, self-learning, or RAG synthesis task"
                )
            elif kind in (
                "screen_inspect",
                "gui_action",
                "desktop_control",
                "system_control",
                "file_task",
                "shell_task",
                "git_task",
                "mcp_manage",
                "permission_manage",
                "confirmation_response",
                "stop",
                "resume",
            ):
                role = "action"
                category = kind
                reason = (
                    "Thao tác hệ thống / tệp / giao diện trực tiếp cần phản hồi tức thì"
                    if lang == "vi"
                    else "Direct desktop, file, or system action routed for instant execution"
                )
            elif (
                len(raw) >= 140
                or (not ui_action_match and user_effort_hint in ("high", "xhigh", "max"))
            ) and (
                "?" in raw
                or any(
                    k in folded
                    for k in (
                        "explain",
                        "giai thich",
                        "how",
                        "lam sao",
                        "nhu the nao",
                        "compare",
                        "so sanh",
                        "fix bug",
                        "sua loi",
                        "debug",
                        "go loi",
                    )
                )
            ):
                role = "thinking"
                category = "complex_analysis"
                reason = (
                    "Câu hỏi phân tích hoặc gỡ lỗi chuyên sâu cần suy luận chi tiết"
                    if lang == "vi"
                    else "Analytical or debugging question routed to Deep Thinking model"
                )

    auto_enabled = opts.get("autoModelRoutingEnabled", True) is not False
    base_model = str(opts.get("model") or "").strip()
    configured_action = str(opts.get("actionModel") or "").strip()
    configured_thinking = str(opts.get("thinkingModel") or "").strip()
    avail = opts.get("availableModels")
    avail_list = avail if isinstance(avail, list) else []

    def _entry_supports_thinking(entry: dict) -> bool:
        m_id = str(entry.get("name") or entry.get("model") or "").strip()
        caps = entry.get("capabilities") if isinstance(entry.get("capabilities"), dict) else {}
        if caps.get("reasoning") == "supported" or entry.get("thinking") is True:
            return True
        return _model_supports_deep_thinking(m_id)

    action_model = configured_action
    if not action_model and auto_enabled and len(avail_list) > 1 and (not base_model or configured_thinking or not _is_plus_iq_model(base_model)):
        for item in avail_list:
            if isinstance(item, dict) and not _entry_supports_thinking(item):
                action_model = str(item.get("name") or item.get("model") or "").strip()
                if action_model:
                    break
    if not action_model:
        action_model = base_model

    thinking_model = configured_thinking
    if not thinking_model and auto_enabled and len(avail_list) > 0:
        if base_model and _model_supports_deep_thinking(base_model):
            thinking_model = base_model
        else:
            for item in avail_list:
                if isinstance(item, dict) and _entry_supports_thinking(item):
                    thinking_model = str(item.get("name") or item.get("model") or "").strip()
                    if thinking_model:
                        break
    if not thinking_model:
        thinking_model = base_model

    base_endpoint = str(opts.get("endpoint") or "").strip()
    action_endpoint = str(opts.get("actionEndpoint") or "").strip() or base_endpoint
    thinking_endpoint = str(opts.get("thinkingEndpoint") or "").strip() or base_endpoint
    user_effort = str(opts.get("thinkingEffort") or "off").strip().lower()
    if user_effort not in ("off", "instant", "low", "medium", "high", "xhigh", "max"):
        user_effort = "off"

    dual_model_active = bool(auto_enabled and action_model and thinking_model and action_model != thinking_model)

    if not auto_enabled:
        selected_model = base_model
        selected_endpoint = base_endpoint
        recommended_effort = user_effort
    elif role == "thinking":
        selected_model = thinking_model
        selected_endpoint = thinking_endpoint
        recommended_effort = user_effort if user_effort in ("medium", "high", "xhigh", "max") else "high"
    else:
        selected_model = action_model
        selected_endpoint = action_endpoint
        recommended_effort = "off"

    is_plus_iq = _is_plus_iq_model(selected_model)
    is_qwen35 = _is_qwen35_hybrid_model(selected_model)
    is_mtp = _is_mtp_model(selected_model)
    mtp_optimize_enabled = opts.get("mtpOptimizeEnabled", True) is not False
    mtp_active = bool(is_mtp and mtp_optimize_enabled)
    explicit_reason = re.search(r"\{REASON:([a-zA-Z0-9_+-]+)\}", raw, re.IGNORECASE)
    if explicit_reason:
        in_chat_directive = explicit_reason.group(0)
    elif is_plus_iq and auto_enabled:
        if role == "action":
            in_chat_directive = "{REASON:ixhigh}" if user_effort in ("xhigh", "max") else "{REASON:imedium}"
        else:
            if any(
                k in folded
                for k in (
                    "spoon",
                    "no spoon",
                    "triet hoc",
                    "philosophy",
                    "philosophical",
                    "sang tao sau",
                    "out of the box",
                    "lateral thinking",
                )
            ):
                in_chat_directive = "{REASON:spoon}"
            elif user_effort == "max" or any(
                k in folded for k in ("einstein", "cuc sau", "deepest", "exhaustive", "toan dien")
            ):
                in_chat_directive = "{REASON:einstein}"
            else:
                in_chat_directive = "{REASON:xhigh}"
    else:
        in_chat_directive = ""

    enable_thinking = (role == "thinking") if auto_enabled else (recommended_effort not in ("off", "instant"))
    if not auto_enabled:
        routing_mode = "manual"
    elif dual_model_active:
        routing_mode = "dual-model"
    elif is_plus_iq:
        routing_mode = "single-model-plusiq"
    elif is_qwen35 or _model_supports_deep_thinking(selected_model):
        routing_mode = "single-model-hybrid"
    else:
        routing_mode = "single-model"

    role_label = "Deep Thinking" if role == "thinking" else "UI Action"
    return {
        "role": role,
        "roleLabel": role_label,
        "category": category,
        "reason": reason,
        "language": lang,
        "autoRouted": bool(auto_enabled),
        "dualModelActive": dual_model_active,
        "routingMode": routing_mode,
        "selectedModel": selected_model,
        "selectedEndpoint": selected_endpoint,
        "actionModel": action_model,
        "thinkingModel": thinking_model,
        "recommendedEffort": recommended_effort,
        "enableThinking": bool(enable_thinking),
        "isPlusIq": bool(is_plus_iq),
        "isQwen35": bool(is_qwen35),
        "isMtp": bool(is_mtp),
        "mtpOptimizeEnabled": bool(mtp_optimize_enabled),
        "mtpActive": bool(mtp_active),
        "temperature": 0.7 if mtp_active else None,
        "repetitionPenalty": 1.0 if mtp_active else None,
        "recommendedTemperature": 0.7 if mtp_active else 0.65,
        "recommendedRepetitionPenalty": 1.0 if mtp_active else None,
        "inChatDirective": in_chat_directive,
    }


def _call_local_model_tool_turn(
    endpoint_url: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    enable_thinking: bool = False,
    in_chat_directive: str = "",
    mtp_active: bool | None = None,
) -> dict | None:
    """Optional structured tool-call query against the active local OpenAI/Ollama endpoint.

    Strictly parses structured `tool_calls` from the API response object; never
    executes JSON or commands found inside plain assistant prose.
    """
    if not endpoint_url or not model_name:
        return None
    if not (endpoint_url.startswith("http://127.0.0.1:") or endpoint_url.startswith("http://localhost:")):
        return None
    effective_user_prompt = user_prompt[:1200]
    if in_chat_directive and in_chat_directive not in effective_user_prompt:
        effective_user_prompt = f"{in_chat_directive}\n{effective_user_prompt}"
    effective_mtp = _is_mtp_model(model_name) if mtp_active is None else bool(mtp_active)
    try:
        if "/v1/" in endpoint_url:
            body = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt[:2400]},
                    {"role": "user", "content": effective_user_prompt[:1260]},
                ],
                "tools": AGENT_TOOL_SCHEMAS,
                "tool_choice": "auto",
                "temperature": 0.7 if effective_mtp else 0.1,
                "max_tokens": 512 if enable_thinking else 256,
                "reasoning_effort": "high" if enable_thinking else "low",
                "chat_template_kwargs": {"enable_thinking": bool(enable_thinking)},
                "stream": False,
            }
            if effective_mtp:
                body["repetition_penalty"] = 1.0
                body["repeat_penalty"] = 1.0
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
                name = str(fn.get("name") or tc.get("name") or "").strip()
                raw_args = fn.get("arguments") if fn.get("arguments") is not None else tc.get("arguments")
                if raw_args is None:
                    raw_args = tc.get("args")
                args_error = ""
                if isinstance(raw_args, str):
                    raw_stripped = raw_args.strip()
                    if not raw_stripped:
                        args = {}
                    else:
                        try:
                            parsed_args = json.loads(raw_stripped)
                            if isinstance(parsed_args, dict):
                                args = parsed_args
                            else:
                                args = {}
                                args_error = "Tool arguments JSON must be an object."
                        except Exception as exc:
                            args = {}
                            args_error = f"Malformed tool arguments JSON: {exc}"
                elif isinstance(raw_args, dict):
                    args = raw_args
                elif raw_args is None:
                    args = {}
                else:
                    args = {}
                    args_error = "Unsupported tool arguments format."
                if name:
                    tc_entry = {
                        "toolCallId": str(tc.get("id") or f"call_{len(tool_calls)+1}"),
                        "name": name,
                        "args": args,
                    }
                    if args_error:
                        tc_entry["argsError"] = args_error
                    tool_calls.append(tc_entry)
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


def _execute_llm_tool_call(tc: dict, session_ctx: dict, companion_root: Path, now_ms: int) -> str:
    """Execute a single structured LLM tool call within permitted Home/workspace scope."""
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = str(tc.get("name") or fn.get("name") or "").strip()
    if not name:
        return "[unknown tool: ]"
    if tc.get("argsError"):
        return f"[{name} error: {tc['argsError']}]"
    raw_args = tc.get("args")
    if raw_args is None:
        raw_args = tc.get("arguments")
    if raw_args is None and fn:
        raw_args = fn.get("arguments") if fn.get("arguments") is not None else fn.get("args")
    if isinstance(raw_args, str):
        raw_stripped = raw_args.strip()
        if not raw_stripped:
            args = {}
        else:
            try:
                parsed = json.loads(raw_stripped)
            except Exception as exc:
                return f"[{name} error: malformed tool arguments JSON ({exc})]"
            if not isinstance(parsed, dict):
                return f"[{name} error: tool arguments JSON must be an object]"
            args = parsed
    elif isinstance(raw_args, dict):
        args = raw_args
    elif raw_args is None:
        args = {}
    else:
        return f"[{name} error: unsupported tool arguments type]"
    last_dir = str(session_ctx.get("lastDirectory") or Path.home())

    if name == "file_inspect":
        target = str(args.get("path") or last_dir)
        resolved = str(resolve_candidate_path(target, last_dir=last_dir))
        perm = check_permission("file_read", target_path=resolved, explicit_prompt_target=True, now_ms=now_ms)
        if not perm["allowed"]:
            return f"[file_inspect blocked: {perm['reason']} on {resolved}]"
        res = agent_file_op({"op": "access", "targetPath": resolved, "lastDirectory": last_dir, "nowMs": now_ms})
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:40])

    if name == "file_mutate":
        op = str(args.get("op") or "edit")
        target = str(args.get("path") or "")
        resolved = str(resolve_candidate_path(target, last_dir=last_dir))
        perm = check_permission("file_write", target_path=resolved, explicit_prompt_target=True, now_ms=now_ms)
        if not perm["allowed"]:
            return f"[file_mutate blocked: {perm['reason']} on {resolved}]"
        res = agent_file_op({
            "op": op,
            "targetPath": resolved,
            "destPath": str(args.get("destPath") or ""),
            "content": str(args.get("content") or ""),
            "searchText": str(args.get("searchText") or ""),
            "mode": str(args.get("mode") or "write"),
            "lastDirectory": last_dir,
            "nowMs": now_ms,
        })
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:20])

    if name == "shell_run":
        cmd = str(args.get("command") or "").strip()
        cwd = str(args.get("cwd") or last_dir)
        perm = check_permission("shell_run", target_path=cwd, explicit_prompt_target=True, now_ms=now_ms)
        if not perm["allowed"]:
            return f"[shell_run blocked: {perm['reason']} in {cwd}]"
        res = agent_file_op({"op": "run", "targetPath": cmd, "content": cmd, "lastDirectory": cwd, "nowMs": now_ms})
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:40])

    if name == "desktop_control":
        res = desktop_control.manage_desktop(
            action=str(args.get("action") or "list_windows"),
            target=str(args.get("target") or ""),
            value=str(args.get("value") or ""),
        )
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:25])

    if name == "system_telemetry":
        res = desktop_control.get_system_telemetry()
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:25])

    if name == "process_manage":
        res = desktop_control.manage_processes(
            action=str(args.get("action") or "list"),
            query=str(args.get("query") or ""),
            pid=int(args.get("pid") or 0),
            signal_name=str(args.get("signal") or "TERM"),
        )
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:25])

    if name == "git_inspect":
        ws = str(args.get("workspace") or session_ctx.get("lastProject") or last_dir)
        res = desktop_control.execute_git_task(
            workspace=ws,
            subaction=str(args.get("action") or "status"),
            extra_args=str(args.get("args") or ""),
        )
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:30])

    if name == "screen_observe":
        called = mcp_client.call_tool(
            "computer-use-linux",
            "screenshot",
            {"allowScreenshot": bool(args.get("allowScreenshot", True))},
            companion_root=companion_root,
        )
        return "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))

    if name == "gui_action":
        act = str(args.get("action") or "inspect_window")
        called = mcp_client.call_tool(
            "computer-use-linux",
            "window_action",
            {"action": act, "targetWindowId": args.get("targetWindowId")},
            companion_root=companion_root,
        )
        return "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))

    if name == "code_inspect":
        ws = str(args.get("workspace") or session_ctx.get("lastProject") or last_dir)
        act = str(args.get("action") or "repo_map")
        if act == "repo_map":
            rm = build_repo_map(ws, token_budget=360)
            return "\n".join(e["summary"] for e in rm.get("entries", [])[:16])
        if act == "run_tests":
            tr = run_project_tests(ws)
            return "\n".join(tr.get("lines", [])[:20])
        sr = search_code(ws, str(args.get("query") or ""))
        return "\n".join(f"{m['file']}:{m['line']}: {m['text']}" for m in sr.get("matches", [])[:16])

    if name == "rag_search":
        rag = retrieve_rag(str(args.get("query") or ""), companion_root=companion_root, page=int(args.get("page") or 1))
        return "\n".join(rag.get("snippets", []))

    if name == "mcp_call":
        called = mcp_client.call_tool(
            str(args.get("server") or ""),
            str(args.get("tool") or ""),
            args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
            companion_root=companion_root,
        )
        return "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))

    if name == "web_search":
        called = mcp_client.call_tool(
            "playwright-mcp" if args.get("openBrowser") else "web-search-mcp",
            "browser_search" if args.get("openBrowser") else "web_search",
            {
                "query": str(args.get("query") or ""),
                "engine": str(args.get("engine") or "auto"),
                "openBrowser": bool(args.get("openBrowser", False)),
            },
            companion_root=companion_root,
        )
        return "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))

    if name == "web_fetch":
        called = mcp_client.call_tool(
            "fetch-mcp",
            "fetch_url",
            {
                "url": str(args.get("url") or ""),
                "maxChars": int(args.get("maxChars") or 3600),
            },
            companion_root=companion_root,
        )
        return "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))

    if name == "skill_run":
        ran = skill_store.run_skill(
            str(args.get("skill") or ""),
            prompt=str(args.get("prompt") or ""),
            companion_root=companion_root,
            workspace=session_ctx.get("lastProject") or last_dir,
        )
        return ran.get("output") or ran.get("instructions", "")[:600]

    if name == "skill_manage":
        act = str(args.get("action") or "list").strip().lower()
        res = skill_store.execute_skill_action(
            {
                "op": act,
                "name": str(args.get("name") or ""),
                "description": str(args.get("description") or ""),
                "instructions": str(args.get("instructions") or ""),
                "old_text": str(args.get("old_text") or ""),
                "new_text": str(args.get("new_text") or ""),
                "note": str(args.get("note") or ""),
                "target": str(args.get("target") or ""),
                "reference": str(args.get("reference") or ""),
                "content": str(args.get("content") or ""),
                "session": session_ctx,
            },
            companion_root=companion_root,
        )
        if "skill" in res and isinstance(res["skill"], dict):
            sk = res["skill"]
            return f"Skill `{sk.get('name')}` (v{sk.get('version', 1)}, {sk.get('source')}): {sk.get('description')}\n{sk.get('instructions', '')[:900]}"
        if "skills" in res:
            return "\n".join(f"- {s['name']} (v{s.get('version', 1)}): {s['description']}" for s in res["skills"][:20])
        if "reflections" in res:
            return "\n".join(f"- [{r.get('status')}] {r.get('prompt')}: {r.get('lesson')}" for r in res["reflections"][:10])
        return json.dumps(res, ensure_ascii=False)[:800]

    return f"[unknown tool: {name}]"


def _build_clarification_payload(
    question: str,
    options: list,
    original_prompt: str = "",
    language: str = "vi",
    kind: str = "general",
    custom_placeholder: str = "",
) -> dict:
    """Normalize a Codex / Antigravity / Claude Desktop style clarification prompt with 1-3 selectable ideas + custom input."""
    normalized: list[dict] = []
    has_recommended = False
    for idx, raw_opt in enumerate((options or [])[:3]):
        if isinstance(raw_opt, dict):
            label = str(raw_opt.get("label") or raw_opt.get("title") or raw_opt.get("value") or "").strip()
            desc = str(raw_opt.get("description") or raw_opt.get("subtitle") or "").strip()
            val = str(raw_opt.get("value") or label).strip()
            rec = bool(raw_opt.get("recommended", False))
            target_path = str(raw_opt.get("targetPath") or "")
        else:
            label = str(raw_opt or "").strip()
            desc = ""
            val = label
            rec = idx == 0
            target_path = ""
        if not label:
            continue
        if rec:
            has_recommended = True
        entry = {
            "id": f"opt_{len(normalized) + 1}",
            "index": len(normalized),
            "number": len(normalized) + 1,
            "label": label[:140],
            "description": desc[:200],
            "value": val[:240],
            "recommended": rec,
        }
        if target_path:
            entry["targetPath"] = target_path
        normalized.append(entry)
    if normalized and not has_recommended:
        normalized[0]["recommended"] = True
    placeholder = str(custom_placeholder or "").strip() or (
        "Nhập ý tưởng hoặc hướng xử lý khác của bạn..."
        if language == "vi"
        else "Type your own idea or custom instructions..."
    )
    return {
        "kind": str(kind or "general"),
        "question": str(question or "").strip()[:320],
        "originalPrompt": str(original_prompt or "").strip()[:600],
        "options": normalized[:3],
        "customPlaceholder": placeholder[:140],
        "allowCustom": True,
    }


def _generate_task_clarification(prompt: str, lang: str, session_ctx: dict | None = None) -> tuple[str, dict]:
    """Generate a tailored clarification question + 1-3 concrete ideas + custom input for an underspecified or `/clarify` prompt."""
    ctx = session_ctx or {}
    clean_topic = re.sub(r"^/(?:clarify|grill-me|grill|ask|plan)\b\s*:?\s*", "", str(prompt or "").strip(), flags=re.IGNORECASE).strip()
    folded = _fold_ascii(clean_topic or prompt)
    workspace_name = Path(str(ctx.get("lastProject") or ctx.get("lastDirectory") or Path.home())).name or "workspace"

    if any(k in folded for k in ("toi uu", "optimize", "performance", "ram", "cpu", "nhanh", "speed", "he thong", "system")):
        question = (
            f"Để tối ưu hiệu quả nhất cho `{clean_topic or workspace_name}`, bạn muốn mình ưu tiên hướng nào trước?"
            if lang == "vi"
            else f"To best optimize `{clean_topic or workspace_name}`, which direction should I prioritize first?"
        )
        opts = (
            [
                {
                    "label": "Kiểm tra & tối ưu tài nguyên hệ thống (CPU/RAM/Processes)",
                    "description": "Phân tích tiến trình tốn tài nguyên nhất và đề xuất tinh chỉnh tức thì",
                    "value": "Phân tích CPU, RAM, tiến trình nặng và tối ưu tài nguyên hệ thống",
                    "recommended": True,
                },
                {
                    "label": f"Tối ưu hiệu năng mã nguồn & thời gian khởi chạy trong {workspace_name}",
                    "description": "Rà soát bottleneck, giảm I/O thừa và tối ưu vòng lặp xử lý",
                    "value": f"Rà soát bottleneck và tối ưu hiệu năng mã nguồn trong {workspace_name}",
                    "recommended": False,
                },
                {
                    "label": "Tối ưu tốc độ suy luận Local LLM & MTP",
                    "description": "Tinh chỉnh context window, Thinking Effort và MTP Auto-Optimize",
                    "value": "Tối ưu cấu hình suy luận Local LLM, Thinking Effort và MTP",
                    "recommended": False,
                },
            ]
            if lang == "vi"
            else [
                {
                    "label": "Audit & optimize system resources (CPU/RAM/Processes)",
                    "description": "Inspect top resource-heavy processes and recommend immediate tuning",
                    "value": "Inspect CPU, RAM, top processes, and optimize system resource usage",
                    "recommended": True,
                },
                {
                    "label": f"Optimize code performance & startup latency in {workspace_name}",
                    "description": "Profile hot paths, reduce redundant I/O, and streamline execution",
                    "value": f"Profile hot paths and optimize code performance in {workspace_name}",
                    "recommended": False,
                },
                {
                    "label": "Tune Local LLM inference & MTP throughput",
                    "description": "Optimize context window, Thinking Effort routing, and MTP settings",
                    "value": "Optimize Local LLM inference settings, Thinking Effort, and MTP",
                    "recommended": False,
                },
            ]
        )
    elif any(k in folded for k in ("giao dien", "ui", "ux", "layout", "css", "qml", "thiet ke", "design")):
        question = (
            f"Bạn muốn thiết kế hoặc cải thiện giao diện `{clean_topic or workspace_name}` theo phong cách nào?"
            if lang == "vi"
            else f"Which UI/UX direction would you like for `{clean_topic or workspace_name}`?"
        )
        opts = (
            [
                {
                    "label": "Bố cục Compact & Gọn gàng theo chuẩn Abyss/Quickshell",
                    "description": "Tối ưu khoảng cách, chống tràn chiều ngang và đồng bộ màu sắc",
                    "value": "Thiết kế bố cục compact, gọn gàng, chống tràn khung nhìn theo chuẩn Abyss/Quickshell",
                    "recommended": True,
                },
                {
                    "label": "Tương tác thông minh kiểu Desktop LLM (Cards, Pills & Shortcuts)",
                    "description": "Thêm thẻ hành động trực quan, gợi ý nhanh và phím tắt mượt mà",
                    "value": "Thiết kế thẻ tương tác thông minh, nút chọn nhanh và phím tắt kiểu Desktop LLM",
                    "recommended": False,
                },
                {
                    "label": "Kiểm tra & sửa lỗi hiển thị (Alignment, Clipping, Overflow)",
                    "description": "Rà soát các phần tử bị lệch hoặc tràn khung và căn chỉnh chính xác",
                    "value": "Rà soát và sửa lỗi căn chỉnh, tràn khung nhìn (overflow/clipping) trên giao diện",
                    "recommended": False,
                },
            ]
            if lang == "vi"
            else [
                {
                    "label": "Compact, responsive Abyss/Quickshell layout",
                    "description": "Tighten spacing, prevent horizontal overflow, and align theme tokens",
                    "value": "Design a compact, overflow-safe layout matching Abyss/Quickshell tokens",
                    "recommended": True,
                },
                {
                    "label": "Interactive Desktop LLM cards, pills & keyboard shortcuts",
                    "description": "Add structured action cards, quick-select pills, and smooth navigation",
                    "value": "Build interactive Desktop LLM cards, quick-select pills, and keyboard navigation",
                    "recommended": False,
                },
                {
                    "label": "Visual alignment & overflow audit",
                    "description": "Inspect and fix clipping, button alignment, and responsive width bounds",
                    "value": "Audit and fix UI alignment, clipping, and responsive width bounds",
                    "recommended": False,
                },
            ]
        )
    elif any(k in folded for k in ("sua loi", "fix", "bug", "error", "debug", "diagnostics", "kiem tra")):
        question = (
            f"Để gỡ lỗi nhanh và chính xác trong `{clean_topic or workspace_name}`, bạn muốn mình bắt đầu từ đâu?"
            if lang == "vi"
            else f"To debug `{clean_topic or workspace_name}` effectively, where should we start?"
        )
        opts = (
            [
                {
                    "label": f"Quét LSP Diagnostics & chạy bộ test tự động trong {workspace_name}",
                    "description": "Phát hiện ngay lỗi cú pháp, import hoặc test đang fail",
                    "value": f"Quét LSP diagnostics và chạy kiểm tra tự động trong {workspace_name}",
                    "recommended": True,
                },
                {
                    "label": "Phân tích nguyên nhân gốc rễ (Root Cause) & đề xuất bản vá an toàn",
                    "description": "Khoanh vùng tệp gây lỗi, giữ tương thích ngược và viết test chống hồi quy",
                    "value": f"Phân tích root cause và tạo bản vá an toàn cho {clean_topic or workspace_name}",
                    "recommended": False,
                },
                {
                    "label": "Kiểm tra log dịch vụ & trạng thái runtime hiện tại",
                    "description": "Đọc thông báo lỗi gần nhất từ tiến trình hoặc màn hình đang mở",
                    "value": "Kiểm tra log runtime và trạng thái tiến trình để tìm lỗi",
                    "recommended": False,
                },
            ]
            if lang == "vi"
            else [
                {
                    "label": f"Run LSP diagnostics & automated test suite in {workspace_name}",
                    "description": "Immediately surface syntax errors, broken imports, or failing tests",
                    "value": f"Run LSP diagnostics and automated tests in {workspace_name}",
                    "recommended": True,
                },
                {
                    "label": "Trace root cause & prepare a minimal, backward-compatible fix",
                    "description": "Isolate the faulty module, preserve existing contracts, and add regression tests",
                    "value": f"Trace root cause and craft a minimal fix for {clean_topic or workspace_name}",
                    "recommended": False,
                },
                {
                    "label": "Inspect live runtime logs & active window state",
                    "description": "Check recent process errors and on-screen diagnostic output",
                    "value": "Inspect live runtime logs and process status for errors",
                    "recommended": False,
                },
            ]
        )
    else:
        topic_label = clean_topic or workspace_name
        question = (
            f"Để mình xử lý yêu cầu `{topic_label}` sát ý bạn nhất, bạn muốn chọn hướng tiếp cận nào dưới đây?"
            if lang == "vi"
            else f"To tailor `{topic_label}` to your exact needs, which approach would you like to take?"
        )
        opts = (
            [
                {
                    "label": f"Lập kế hoạch từng bước & triển khai đầy đủ cho {topic_label}",
                    "description": "Khảo sát cấu trúc tệp, thực thi an toàn và kiểm thử tự động",
                    "value": f"Lập kế hoạch chi tiết và triển khai đầy đủ cho {topic_label}",
                    "recommended": True,
                },
                {
                    "label": "Tạo bản mẫu nhanh (Quick Prototype / Script gọn nhẹ)",
                    "description": "Giải pháp tối giản, chạy được ngay với ít thay đổi nhất",
                    "value": f"Viết giải pháp hoặc script mẫu gọn nhẹ, chạy ngay cho {topic_label}",
                    "recommended": False,
                },
                {
                    "label": "Phân tích kiến trúc, ưu/nhược điểm & các trường hợp biên",
                    "description": "Đánh giá kỹ trade-off trước khi chạm vào mã nguồn",
                    "value": f"Phân tích sâu kiến trúc, trade-off và edge cases cho {topic_label}",
                    "recommended": False,
                },
            ]
            if lang == "vi"
            else [
                {
                    "label": f"Step-by-step implementation plan & verification for {topic_label}",
                    "description": "Inspect repo structure, apply surgical changes, and verify with tests",
                    "value": f"Create a step-by-step implementation plan and verification for {topic_label}",
                    "recommended": True,
                },
                {
                    "label": "Minimal, runnable prototype or script",
                    "description": "Fastest self-contained solution with minimal surface area",
                    "value": f"Build a minimal, runnable prototype or script for {topic_label}",
                    "recommended": False,
                },
                {
                    "label": "Deep architectural & trade-off analysis first",
                    "description": "Evaluate design trade-offs and edge cases before modifying files",
                    "value": f"Analyze architecture, trade-offs, and edge cases for {topic_label}",
                    "recommended": False,
                },
            ]
        )

    clar_payload = _build_clarification_payload(
        question=question,
        options=opts,
        original_prompt=clean_topic or prompt,
        language=lang,
        kind="task_clarification",
    )
    bullet_lines = []
    for item in clar_payload["options"]:
        rec_tag = (" (Khuyên dùng)" if lang == "vi" else " (Recommended)") if item.get("recommended") else ""
        desc_part = f" — {item['description']}" if item.get("description") else ""
        bullet_lines.append(f"{item['number']}. **{item['label']}{rec_tag}**{desc_part}")
    footer = (
        "Bạn có thể bấm chọn trực tiếp 1 trong 3 gợi ý bên dưới hoặc nhập ý tưởng riêng vào ô tùy chỉnh."
        if lang == "vi"
        else "Select one of the 1–3 ideas below or type your own direction into the custom input box."
    )
    msg = f"{clar_payload['question']}\n\n" + "\n".join(bullet_lines) + f"\n\n_{footer}_"
    return msg, clar_payload


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
    clarification: dict | None = None,
    images: list | None = None,
) -> dict:
    clean = re.sub(r"\r\n?", "\n", re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")))
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()[:5800]
    if not clean:
        clean = "Đã hoàn thành." if language == "vi" else "Completed."
    expr = expression if expression in ("idle", "happy", "excited", "thinking", "working", "surprised", "sleepy", "sad", "alert") else "idle"
    clean_images = [str(p).strip() for p in (images or []) if str(p or "").strip()][:4]
    out = {
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
        "activitySteps": [s["label"] for s in _ACTIVE_RUN_STEPS[-12:]],
        "workedSeconds": max(1, round((int(time.time() * 1000) - (_ACTIVE_RUN_START_MS or int(time.time() * 1000))) / 1000.0)),
    }
    if clean_images:
        out["images"] = clean_images
    if isinstance(clarification, dict) and clarification.get("question") and isinstance(clarification.get("options"), list):
        out["clarification"] = clarification
    return out


_CURRENT_TURN_THREAD_ID: str | None = None


def _persist_turn_to_history(
    prompt: str,
    reply_text: str,
    model: str = "companion-agent",
    thread_id: str | None = None,
) -> tuple[int, int, bool]:
    try:
        tid = str(thread_id or _CURRENT_TURN_THREAD_ID or "").strip() or None
        u_id, a_id = history_store.append_exchange(
            prompt[:1200], reply_text[:5800], model, thread_id=tid
        )
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
    """Execute a natural-language desktop agent turn with full Home access, active Project binding, and sudo-gated System access."""
    global _CURRENT_TURN_THREAD_ID
    now_ms = int(options.get("nowMs") or int(time.time() * 1000))
    req_tid = str(options.get("threadId") or "").strip()
    if req_tid:
        try:
            if history_store.get_active_thread_id() != req_tid:
                history_store.execute_thread_action({"subaction": "select_thread", "threadId": req_tid, "nowMs": now_ms})
        except Exception:
            pass
    try:
        proj_ctx = history_store.get_active_project_context()
        ws_raw = ""
        if isinstance(proj_ctx, dict):
            ws_raw = str(
                (proj_ctx.get("project") or {}).get("workspacePath")
                or proj_ctx.get("workspacePath")
                or ""
            ).strip()
        if ws_raw and Path(ws_raw).expanduser().is_dir():
            resolved_ws = str(Path(ws_raw).expanduser().resolve(strict=False))
            ctx_now = load_session_context()
            if ctx_now.get("lastProject") != resolved_ws:
                ctx_now["lastProject"] = resolved_ws
                ctx_now["lastDirectory"] = resolved_ws
                save_session_context(ctx_now, now_ms=now_ms)
    except Exception:
        pass

    route_info = classify_prompt_model_role(str(options.get("prompt") or ""), options)
    effective_options = dict(options)
    if route_info.get("selectedModel"):
        effective_options["model"] = route_info["selectedModel"]
    if route_info.get("selectedEndpoint"):
        effective_options["endpoint"] = route_info["selectedEndpoint"]
    effective_options["routedRole"] = route_info["role"]
    effective_options["modelRoute"] = route_info

    prev_tid = _CURRENT_TURN_THREAD_ID
    _CURRENT_TURN_THREAD_ID = req_tid or None
    try:
        result = _execute_agent_turn_inner(effective_options, companion_root, host_root)
    finally:
        _CURRENT_TURN_THREAD_ID = prev_tid
    result["modelRoute"] = route_info
    result["routedModel"] = route_info.get("selectedModel", "")
    result["routedRole"] = route_info.get("role", "action")
    if result.get("handled"):
        try:
            run_id_done = str(result.get("runId") or "")
            run_obj = get_run(run_id_done) if run_id_done else None
            prompt_str = str(options.get("prompt") or "").strip()
            matched_sk = skill_store.match_skills(prompt_str, companion_root=companion_root, max_results=2) if result.get("intent") != "skill_manage" else []
            ref_info = skill_store.auto_reflect_run(
                prompt=prompt_str,
                status=str(result.get("status") or "completed"),
                summary=str(result.get("text") or "")[:240],
                steps=(run_obj or {}).get("steps") or [],
                matched_skills=matched_sk,
                companion_root=companion_root,
            )
            result["selfReflection"] = ref_info
        except Exception:
            pass
        try:
            ws_info = history_store.execute_thread_action({"subaction": "list", "nowMs": now_ms})
            result["activeThreadId"] = ws_info.get("activeThreadId", "chat_default")
            result["threads"] = ws_info.get("threads", [])
            result["projects"] = ws_info.get("projects", [])
        except Exception:
            pass
        try:
            result["skills"] = [
                {
                    "name": s["name"],
                    "description": s["description"],
                    "category": s.get("category", "general"),
                    "version": s.get("version", "1.0.0"),
                    "source": s.get("source", "builtin" if s.get("builtIn") else "user"),
                    "enabled": s["enabled"],
                    "builtIn": s["builtIn"],
                    "folder": str(s.get("folder", "")),
                    "path": str(s.get("path", "")),
                    "usageCount": s.get("usageCount", 0),
                    "triggers": s.get("triggers", []),
                }
                for s in skill_store.list_skills(companion_root=companion_root, include_disabled=True)
            ]
            result["workflowReflections"] = skill_store.list_workflow_reflections(limit=6)
            result["mcpServers"] = mcp_client.list_servers()
        except Exception:
            pass
    return result


def _execute_agent_turn_inner(options: dict, companion_root: Path, host_root: Path) -> dict:
    """Core implementation of the natural-language desktop agent turn."""
    now_ms = int(options.get("nowMs") or int(time.time() * 1000))
    _reset_activity_steps(now_ms)
    prompt = str(options.get("prompt") or "").strip()
    sudo_password_opt = str(options.get("sudoPassword") or "")
    persist_history = options.get("persistHistory") is not False
    allow_screenshot = options.get("allowScreenshot") is not False

    reconciled = reconcile_crashed_runs(now_ms)
    session_ctx = load_session_context()
    intent = classify_intent(prompt, session_ctx)
    lang = intent["language"]
    kind = intent["kind"]
    if kind != "chat":
        intent_lbl = "planning" if (kind == "code_task" and intent.get("subkind") == "plan") else kind.replace("_", " ")
        _emit_activity_step(f"Analyzed request ({intent_lbl}) ›", kind="intent", status="completed")

    # 0. Ordinary chat fallback (or optional local LLM structured tool-calling loop)
    if kind == "chat":
        endpoint_url = str(options.get("endpoint") or "").strip()
        model_name = str(options.get("model") or "").strip()
        route_meta = options.get("modelRoute") if isinstance(options.get("modelRoute"), dict) else {}
        if endpoint_url and model_name and options.get("useModelTools") is True:
            sys_prompt = (
                "You are Alis-Companion, a full-authority Linux desktop agent. "
                f"Active directory: {session_ctx.get('lastDirectory') or Path.home()}. "
                "Call a tool only if the user is asking to inspect/modify files, run commands, or control the desktop/system. "
                "Present responses neatly: lead with a concise 1-sentence summary, use compact bullet points or code blocks for details, and avoid filler or repetition."
            )
            llm_turn = _call_local_model_tool_turn(
                endpoint_url,
                model_name,
                sys_prompt,
                prompt,
                enable_thinking=bool(route_meta.get("enableThinking", False)),
                in_chat_directive=str(route_meta.get("inChatDirective") or ""),
                mtp_active=route_meta.get("mtpActive") if "mtpActive" in route_meta else None,
            )
            if llm_turn and llm_turn.get("toolCalls"):
                run = start_run(prompt, source="interactive", language=lang, max_turns=10, max_duration_sec=180, now_ms=now_ms)
                run_id = run["runId"]
                tool_outputs = []
                for idx, tc in enumerate(llm_turn["toolCalls"][:4], start=1):
                    step_id = record_step_start(run_id, idx, tc["name"], side_effect=True, now_ms=now_ms)
                    try:
                        out_str = _execute_llm_tool_call(tc, session_ctx, companion_root, now_ms)
                        record_step_finish(step_id, status="completed", detail={"output": out_str[:400]})
                        tool_outputs.append(f"### `{tc['name']}`\n{out_str}")
                    except Exception as exc:
                        record_step_finish(step_id, status="failed", detail={"error": str(exc)})
                        tool_outputs.append(f"### `{tc['name']}` (error)\n{exc}")
                    advance_turn(run_id, now_ms=now_ms)
                finish_run(run_id, status="completed", now_ms=now_ms)
                combined_msg = (llm_turn.get("text") + "\n\n" if llm_turn.get("text") else "") + "\n\n".join(tool_outputs)
                res = _format_reply(combined_msg, expression="working", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": "llm_tool_turn", **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}
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
            out_payload = {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}
            if sudo_res.get("editorMode"):
                out_payload["editorMode"] = sudo_res.get("editorMode")
                out_payload["isLargeFile"] = bool(sudo_res.get("isLargeFile"))
                out_payload["openedExternalEditor"] = bool(sudo_res.get("openedExternalEditor"))
                out_payload["editorCommand"] = str(sudo_res.get("editorCommand") or "")
                out_payload["editorPopup"] = sudo_res.get("editorPopup") or {}
            return out_payload
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

    # 4. Clarification reply when multiple files matched previously or a task clarification was posed
    if kind == "clarification_reply":
        pending_clar = session_ctx.get("pendingClarification") or {}
        folded_reply = _fold_ascii(prompt).strip()
        if re.match(r"^(?:skip|bo qua|dismiss|huy|cancel|thoi|khong)$", folded_reply):
            session_ctx["pendingClarification"] = None
            save_session_context(session_ctx, now_ms=now_ms)
            msg = (
                "Đã bỏ qua bước hỏi làm rõ. Bạn có thể nhập yêu cầu mới bất cứ lúc nào."
                if lang == "vi"
                else "Skipped the clarification step. You can enter a new request anytime."
            )
            res = _format_reply(msg, expression="idle", status="cancelled", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        clar_kind = str(pending_clar.get("kind") or "file_ambiguity")
        clar_opts = pending_clar.get("options") if isinstance(pending_clar.get("options"), list) else []
        num_match = re.match(r"^(?:#|option\s+|lua chon\s+|y\s+|idea\s+)?([1-3])(?:\s*[.)])?$", folded_reply)

        if clar_kind == "task_clarification":
            session_ctx["pendingClarification"] = None
            save_session_context(session_ctx, now_ms=now_ms)
            orig_goal = str(pending_clar.get("originalPrompt") or "").strip()
            selected_opt = None
            if num_match:
                opt_idx = int(num_match.group(1)) - 1
                if 0 <= opt_idx < len(clar_opts):
                    selected_opt = clar_opts[opt_idx]
            if not selected_opt:
                for opt in clar_opts:
                    if not isinstance(opt, dict):
                        continue
                    lbl = str(opt.get("label") or "").strip()
                    val = str(opt.get("value") or "").strip()
                    if lbl and (prompt.strip().lower() == lbl.lower() or lbl.lower() in prompt.strip().lower()):
                        selected_opt = opt
                        break
                    if val and (prompt.strip().lower() == val.lower() or val.lower() in prompt.strip().lower()):
                        selected_opt = opt
                        break
            chosen_direction = str((selected_opt or {}).get("value") or (selected_opt or {}).get("label") or prompt).strip()
            chosen_label = str((selected_opt or {}).get("label") or chosen_direction).strip()
            selection_mode = "option" if selected_opt else "custom"
            workspace = str(session_ctx.get("lastProject") or session_ctx.get("lastDirectory") or Path.home())
            rmap = build_repo_map(workspace, token_budget=320)
            top_files = "\n".join(f"  - {e['summary']}" for e in rmap["entries"][:6]) or f"  - `{Path(workspace).name}`"
            if lang == "vi":
                mode_tag = f"Gợi ý #{selected_opt.get('number', 1)}" if selected_opt else "Ý tưởng tùy chỉnh (Custom Idea)"
                msg = (
                    f"### Đã chốt hướng xử lý ({mode_tag}): {chosen_label}\n"
                    f"- **Yêu cầu gốc:** `{orig_goal or chosen_direction}`\n"
                    f"- **Hướng triển khai đã chọn:** {chosen_direction}\n"
                    f"- **Workspace mục tiêu:** `{workspace}` ({rmap['fileCount']} tệp)\n"
                    f"- **Các tệp trọng tâm:**\n{top_files}\n"
                    f"- **Các bước thực hiện:**\n"
                    f"  1. Áp dụng hướng **{chosen_label}** trực tiếp vào `{Path(workspace).name}`.\n"
                    f"  2. Giữ nguyên tương thích ngược và cấu hình hiện có của người dùng.\n"
                    f"  3. Kiểm tra bằng `/diagnostics` hoặc `/tests` để đảm bảo hoạt động ổn định."
                )
            else:
                mode_tag = f"Idea #{selected_opt.get('number', 1)}" if selected_opt else "Custom Idea"
                msg = (
                    f"### Confirmed Direction ({mode_tag}): {chosen_label}\n"
                    f"- **Original Goal:** `{orig_goal or chosen_direction}`\n"
                    f"- **Selected Approach:** {chosen_direction}\n"
                    f"- **Target Workspace:** `{workspace}` ({rmap['fileCount']} files)\n"
                    f"- **Key Files:**\n{top_files}\n"
                    f"- **Action Plan:**\n"
                    f"  1. Execute **{chosen_label}** across `{Path(workspace).name}`.\n"
                    f"  2. Preserve backward compatibility and existing user settings.\n"
                    f"  3. Validate with `/diagnostics` or `/tests` for zero regressions."
                )
            res = _format_reply(msg, expression="happy", status="completed", language=lang, session_ctx=session_ctx)
            res["clarificationResolved"] = {
                "mode": selection_mode,
                "originalPrompt": orig_goal,
                "selectedOption": selected_opt,
                "chosenDirection": chosen_direction,
            }
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        cands = pending_clar.get("candidates") if isinstance(pending_clar.get("candidates"), list) else []
        chosen = ""
        if num_match and cands:
            c_idx = int(num_match.group(1)) - 1
            if 0 <= c_idx < len(cands):
                chosen = str(cands[c_idx])
        if not chosen:
            chosen, _ = _resolve_conversational_target(prompt, session_ctx, expect_dir=False)
        if not chosen and cands:
            for c_path in cands:
                if Path(c_path).name.lower() in prompt.lower():
                    chosen = str(c_path)
                    break
        if not chosen and cands:
            chosen = cands[0]
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

    # 4b. Interactive Clarification (`clarify_task` / `/clarify` / `/grill-me` / underspecified prompt)
    if kind == "clarify_task":
        msg, clar_payload = _generate_task_clarification(prompt, lang, session_ctx)
        session_ctx["pendingClarification"] = {
            "kind": "task_clarification",
            "originalPrompt": clar_payload["originalPrompt"],
            "question": clar_payload["question"],
            "options": clar_payload["options"],
        }
        save_session_context(session_ctx, now_ms=now_ms)
        res = _format_reply(
            msg,
            expression="thinking",
            status="awaiting_clarification",
            language=lang,
            session_ctx=session_ctx,
            clarification=clar_payload,
        )
        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
        return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

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

        # 11. Screen / Active Window Observation (routed via computer-use-linux MCP)
        if kind == "screen_observe":
            step_id = record_step_start(run_id, 1, "screen_observe", side_effect=False, now_ms=now_ms)
            prev_win = session_ctx.get("lastWindowId")
            folded_p = _fold_ascii(prompt)
            wants_screenshot_img = bool(intent.get("captureScreenshot")) or bool(
                re.search(
                    r"\b(?:chup man hinh|chup anh man hinh|chup lai man hinh|take a screenshot|capture screen|screenshot)\b",
                    folded_p,
                )
                or re.match(r"^(?:/agent\s+|/)?screenshot\b", folded_p)
            )
            effective_allow_ss = bool(allow_screenshot or wants_screenshot_img)
            ep_url = str(options.get("endpoint") or "").strip()
            mdl_name = str(options.get("model") or "").strip()
            is_live_desktop = (
                not os.environ.get("ALIS_AQUA_SCREEN_FIXTURE")
                and not os.environ.get("AQUA_TEST_ENDPOINT")
                and not os.environ.get("AI_FIXTURE_URL")
            )
            if is_live_desktop and options.get("runSource") == "interactive" and (not ep_url or "/v1/" not in ep_url or not mdl_name):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:1234/v1/models", timeout=1.5) as r_m:
                        m_payload = json.loads(r_m.read().decode("utf-8", errors="replace"))
                    for m_entry in (m_payload.get("data") or []):
                        cand_id = str(m_entry.get("id") or "").strip()
                        if cand_id and "embed" not in cand_id.lower():
                            mdl_name = cand_id
                            ep_url = "http://127.0.0.1:1234/v1/chat/completions"
                            break
                except Exception:
                    pass
            mdl_lower = mdl_name.lower()
            has_live_vlm = bool(
                is_live_desktop
                and effective_allow_ss
                and ep_url
                and mdl_name
                and "/v1/" in ep_url
                and (ep_url.startswith("http://127.0.0.1:") or ep_url.startswith("http://localhost:"))
                and any(k in mdl_lower for k in ("qwen3.5", "qwen3_5", "qwen2-vl", "qwen2.5-vl", "-vl", "vision", "llava", "pixtral", "gemma-3", "minicpm", "internvl"))
            )
            is_followup_about_shot = bool(
                re.search(r"\b(?:inside the screenshot|in the screenshot|from the screenshot|about the screenshot|trong anh chup|trong buc anh)\b", folded_p)
                and not re.search(r"\b(?:take|capture|chup)\b", folded_p)
            )
            need_capture_img = bool((wants_screenshot_img or has_live_vlm) and not is_followup_about_shot)
            try:
                mcp_obs = mcp_client.call_tool(
                    "computer-use-linux",
                    "screenshot" if effective_allow_ss else "get_accessibility_tree",
                    {
                        "allowScreenshot": effective_allow_ss,
                        "previousWindowId": prev_win,
                        "nowMs": now_ms,
                        "captureImage": need_capture_img,
                        "skipOcr": has_live_vlm,
                    },
                    companion_root=companion_root,
                    enabled_overrides={"computer-use-linux": True},
                )
                obs_res = mcp_obs.get("structured") or observe_active_window(
                    allow_screenshot=effective_allow_ss,
                    previous_window_id=prev_win,
                    now_ms=now_ms,
                    capture_image=need_capture_img,
                    skip_ocr=has_live_vlm,
                )
            except McpError:
                obs_res = observe_active_window(
                    allow_screenshot=effective_allow_ss,
                    previous_window_id=prev_win,
                    now_ms=now_ms,
                    capture_image=need_capture_img,
                    skip_ocr=has_live_vlm,
                )
            obs = obs_res["observation"]
            screenshot_path = str(obs_res.get("screenshotPath") or obs.get("screenshotPath") or "").strip()
            if is_followup_about_shot and not screenshot_path:
                try:
                    from screen_context import _screenshots_dir
                    recent_shots = sorted(_screenshots_dir().glob("screenshot-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if recent_shots and (time.time() - recent_shots[0].stat().st_mtime) <= 300:
                        screenshot_path = str(recent_shots[0])
                        obs_res["screenshotPath"] = screenshot_path
                        obs["screenshotPath"] = screenshot_path
                except Exception:
                    pass
            if (need_capture_img or effective_allow_ss) and not screenshot_path:
                screenshot_path = capture_screenshot_file(now_ms=now_ms)
                if screenshot_path:
                    obs_res["screenshotPath"] = screenshot_path
                    obs["screenshotPath"] = screenshot_path
            reply_images = [screenshot_path] if (wants_screenshot_img and screenshot_path and not is_followup_about_shot) else []
            record_step_finish(step_id, status="completed", detail=obs)
            advance_turn(run_id, now_ms=now_ms)

            session_ctx["lastWindowId"] = obs.get("windowId")
            session_ctx["lastAppId"] = obs.get("appId") or ""
            save_session_context(session_ctx, now_ms=now_ms)
            observations.append(obs)

            changed_note = ""
            if obs.get("focusChanged"):
                changed_note = " *(đã làm mới)*" if lang == "vi" else " *(refreshed)*"
            compact_headings = []
            for raw_ln in obs_res["headings"]:
                ln_txt = " ".join(str(raw_ln or "").split()).strip()
                if not ln_txt:
                    continue
                if len(ln_txt) > 140:
                    ln_txt = ln_txt[:137].rstrip() + "…"
                compact_headings.append(f"- {ln_txt}")
                if len(compact_headings) >= 8:
                    break
            lines_preview = "\n".join(compact_headings)
            source_badge = str(obs.get("source") or "niri+vision")
            if is_live_desktop and ep_url and mdl_name and "/v1/" in ep_url and (ep_url.startswith("http://127.0.0.1:") or ep_url.startswith("http://localhost:")):
                try:
                    if has_live_vlm and screenshot_path and Path(screenshot_path).is_file():
                        import base64
                        import io
                        from PIL import Image
                        im_v = Image.open(screenshot_path).convert("RGB")
                        im_v.thumbnail((728, 728))
                        buf = io.BytesIO()
                        im_v.save(buf, format="JPEG", quality=85)
                        b64_v = base64.b64encode(buf.getvalue()).decode("ascii")
                        if lang == "vi":
                            sys_vlm = (
                                "Bạn là Alis-Companion. Hãy nhìn trực tiếp vào ảnh chụp màn hình của người dùng "
                                "và mô tả/trả lời chính xác những ứng dụng, cửa sổ và nội dung văn bản đang hiển thị "
                                "bằng 3-4 gạch đầu dòng Markdown ngắn gọn. Không sinh thẻ <think>."
                            )
                        else:
                            sys_vlm = (
                                "You are Alis-Companion. Look directly at the user's screenshot and answer their request "
                                "by accurately describing the visible applications, workspace, and text content in 3-4 concise "
                                "Markdown bullet points. Do not output <think>."
                            )
                        usr_vlm_text = (
                            f"{{REASON:instant}}\n"
                            f"Active window: {obs.get('title') or ''} ({obs.get('appId') or 'desktop'})\n"
                            f"User request: {prompt[:400]}"
                        )
                        screen_body = {
                            "model": mdl_name,
                            "messages": [
                                {"role": "system", "content": sys_vlm},
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": usr_vlm_text},
                                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_v}"}},
                                    ],
                                },
                            ],
                            "reasoning_effort": "none",
                            "chat_template_kwargs": {"enable_thinking": False},
                            "temperature": 0.2,
                            "max_tokens": 135,
                        }
                        if _is_mtp_model(mdl_name):
                            screen_body["repetition_penalty"] = 1.0
                            screen_body["repeat_penalty"] = 1.0
                        req_s = urllib.request.Request(
                            ep_url,
                            data=json.dumps(screen_body).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                        )
                        with urllib.request.urlopen(req_s, timeout=75) as resp_s:
                            data_s = json.loads(resp_s.read().decode("utf-8", errors="replace"))
                        ai_txt = str(((data_s.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                        ai_txt = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", ai_txt, flags=re.IGNORECASE).strip()
                        if ai_txt:
                            lines_preview = ai_txt[:1200]
                            source_badge = "niri+vision"
                except Exception:
                    pass
            win_label = str(obs.get("title") or obs.get("appId") or "desktop").strip()
            if len(win_label) > 44:
                win_label = win_label[:41].rstrip() + "…"
            wants_guide = any(k in folded_p for k in ("eyes", "hands", "mat va tay", "can you see"))
            shot_note_vi = f"\n📷 Đã chụp màn hình (`{Path(screenshot_path).name}`)." if (wants_screenshot_img and screenshot_path) else ""
            shot_note_en = f"\n📷 Screenshot captured (`{Path(screenshot_path).name}`)." if (wants_screenshot_img and screenshot_path) else ""
            if lang == "vi":
                guide_vi = (
                    "\n\n💡 **Mắt & Tay MCP:** `/web` tìm kiếm · `/fetch` đọc trang · `/browser` tự động hoá web · mở ứng dụng / chạy lệnh."
                    if wants_guide
                    else ""
                )
                msg = (
                    f"**🖥 {win_label}** · `{source_badge}`{changed_note}\n"
                    f"{lines_preview}{shot_note_vi}{guide_vi}"
                ).strip()
            else:
                guide_en = (
                    "\n\n💡 **MCP Eyes & Hands:** `/web` search · `/fetch` read URL · `/browser` automate web · open apps / run commands."
                    if wants_guide
                    else ""
                )
                msg = (
                    f"**🖥 {win_label}** · `{source_badge}`{changed_note}\n"
                    f"{lines_preview}{shot_note_en}{guide_en}"
                ).strip()
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(
                msg,
                expression="working",
                status="completed",
                run_id=run_id,
                language=lang,
                observations=observations,
                session_ctx=session_ctx,
                images=reply_images,
            )
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 12. GUI / Niri action broker (routed via computer-use-linux MCP)
        if kind == "gui_action":
            if run_source == "rule":
                raise AgentPermissionError("rule_gui_forbidden", "Background rules are not allowed to control mouse/keyboard or focus.")
            m_win = re.search(r"\b(\d+)\b", prompt)
            win_id = int(m_win.group(1)) if m_win else session_ctx.get("lastWindowId")
            req_scale = "click" in _fold_ascii(prompt) or "toa do" in _fold_ascii(prompt)
            try:
                mcp_gui = mcp_client.call_tool(
                    "computer-use-linux",
                    "window_action",
                    {
                        "action": "focus_window" if m_win else "inspect_window",
                        "targetWindowId": win_id,
                        "requireScale": req_scale,
                        "nowMs": now_ms,
                    },
                    companion_root=companion_root,
                    enabled_overrides={"computer-use-linux": True},
                )
                gui_res = mcp_gui.get("structured")
                if not isinstance(gui_res, dict) or "observation" not in gui_res:
                    gui_res = perform_gui_action(
                        action_type="focus_window" if m_win else "inspect_window",
                        target_window_id=win_id,
                        require_scale=req_scale,
                        now_ms=now_ms,
                    )
            except McpError as exc:
                raise ScreenContextError(exc.code, exc.message) from exc
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
            folded_plan_check = _fold_ascii(prompt)
            explicit_plan_path = _extract_path_from_text(prompt, session_ctx) if sub == "plan" else None
            is_code_plan = bool(
                sub != "plan"
                or explicit_plan_path
                or re.search(
                    r"\b(?:code|coding|project|du an|repo|repository|module|file|tep|thu muc|folder|directory|script|app|ung dung|kien truc|architecture|bug|test|tests|diagnostics|lsp|refactor|api|ui|qml|python|rust|js|ts|css|html|shader|plugin|mcp|skill|git|commit|branch|database|sql)\b",
                    folded_plan_check,
                )
            )
            if is_code_plan:
                workspace, _ = _resolve_conversational_target(prompt, session_ctx, expect_dir=True)
                if Path(workspace).is_file():
                    workspace = str(Path(workspace).parent)
                session_ctx["lastProject"] = workspace
                session_ctx["lastDirectory"] = workspace
                save_session_context(session_ctx, now_ms=now_ms)
            else:
                workspace = ""

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
            elif sub == "plan":
                goal_text = re.sub(
                    r"^(?:/plan\s+)?(?:(?:hãy|hay|vui lòng|vui long|giúp tôi|giup toi|giúp mình|giup minh|please)\s+)?(?:lap ke hoach|lập kế hoạch|len ke hoach|lên kế hoạch|thiet ke ke hoach|thiết kế kế hoạch|create a plan for|implementation plan(?:\s+for)?)?\s*:?\s*",
                    "",
                    prompt.strip(),
                    flags=re.IGNORECASE,
                ).strip()
                goal_text = re.sub(r"^/plan\s*:?\s*", "", goal_text, flags=re.IGNORECASE).strip() or (Path(workspace).name if workspace else prompt.strip())
                display_goal = re.sub(r"\s*\([^)]*(?:internet|web|google|tra cứu|tra cuu|tìm kiếm|tim kiem|search)[^)]*\)\s*", "", goal_text, flags=re.IGNORECASE).strip()
                display_goal = re.sub(r"^(?:cho\s+|for\s+)", "", display_goal, flags=re.IGNORECASE).strip() or goal_text

                # Perform live web search when planning real-world / personal / travel / research tasks or when internet search is requested
                llm_plan_reply = ""
                web_context_text = ""
                web_sources_md = ""
                web_highlights_lines: list[str] = []
                is_live_desktop = (
                    str(options.get("runSource") or "") == "interactive"
                    and os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1"
                    and not os.environ.get("AQUA_TEST_ENDPOINT")
                )
                folded_prompt = _fold_ascii(prompt)
                wants_web_for_plan = (not is_code_plan) or any(
                    kw in folded_prompt
                    for kw in (
                        "internet",
                        "tra cuu",
                        "tim kiem",
                        "google",
                        "web",
                        "online",
                        "dia diem",
                        "thoi tiet",
                        "du lich",
                        "da ngoai",
                        "can tho",
                        "ha noi",
                        "sai gon",
                        "da nang",
                        "da lat",
                    )
                )
                if is_live_desktop and wants_web_for_plan:
                    search_q = _extract_plan_search_query(goal_text, lang)
                    if search_q:
                        w_step = record_step_start(run_id, 1, "web_search", side_effect=False, target_path=search_q, now_ms=now_ms)
                        try:
                            web_res = web_tools.web_search(search_q, max_results=5, timeout_sec=6.0)
                            w_results = web_res.get("results") or []
                            record_step_finish(w_step, status="completed", detail={"query": search_q, "count": len(w_results)})
                            if w_results:
                                web_sources_md = str(web_res.get("sources_markdown") or web_tools.format_markdown_sources(w_results, max_sources=5))
                                ctx_rows: list[str] = []
                                for idx, item in enumerate(w_results[:5], 1):
                                    t_str = str(item.get("title") or "").strip()
                                    s_str = str(item.get("snippet") or "").strip()
                                    u_str = str(item.get("url") or "").strip()
                                    dom_str = web_tools.short_source_domain(u_str)
                                    ctx_rows.append(f"{idx}. {t_str} ({dom_str}): {s_str}")
                                    if t_str and len(web_highlights_lines) < 3:
                                        web_highlights_lines.append(f"  - **{t_str}** ([{dom_str}]({u_str})): {s_str[:160]}")
                                web_context_text = "\n".join(ctx_rows)
                        except Exception as exc:
                            record_step_finish(w_step, status="failed", error_text=str(exc))

                if is_live_desktop:
                    plan_endpoint, clean_model = _resolve_live_llm(options)
                    if plan_endpoint:
                        t0_llm = int(time.time() * 1000)
                        _emit_activity_step(f"Thinking & drafting plan ({(clean_model or 'local-llm')[:26]}) ›", kind="llm_think", status="running")
                        try:
                            if is_code_plan and workspace:
                                rmap_ctx = build_repo_map(workspace, token_budget=300)
                                files_ctx = ", ".join(e["path"] for e in rmap_ctx["entries"][:8])
                                sys_prompt = (
                                    "Bạn là Alis-Companion. Hãy lập kế hoạch kỹ thuật ngắn gọn, rõ ràng bằng Markdown tiếng Việt (dùng tiêu đề ### và các bước cụ thể). {REASON:instant}"
                                    if lang == "vi"
                                    else "You are Alis-Companion. Write a concise, actionable technical implementation plan in English Markdown (use ### heading and clear steps). {REASON:instant}"
                                )
                                usr_prompt = f"{{REASON:instant}}\nMục tiêu: {display_goal}\nWorkspace: {workspace} (Các tệp chính: {files_ctx})"
                                if web_context_text:
                                    usr_prompt += f"\n\nThông tin tra cứu trên Internet:\n{web_context_text}"
                            else:
                                sys_prompt = (
                                    "Bạn là Alis-Companion. Hãy viết kế hoạch ngắn gọn, súc tích (dưới 180 từ) bằng Markdown tiếng Việt, đi thẳng vào 3 mục: 1. Địa điểm gợi ý (từ kết quả tra cứu), 2. Lịch trình theo giờ (bắt đầu từ 08:30), 3. Chuẩn bị & Lưu ý. Không viết phần mở đầu dài dòng, không tự chèn URL dài."
                                    if lang == "vi"
                                    else "You are Alis-Companion. Write a concise Markdown plan in English (under 180 words) with 3 direct sections: 1. Recommended Spots (from web search), 2. Time-slotted Schedule, 3. Checklist & Tips. No long intro."
                                )
                                usr_prompt = f"{{REASON:instant}}\nYêu cầu lập kế hoạch: {display_goal}"
                                if web_context_text:
                                    usr_prompt += f"\n\nDữ liệu thực tế vừa tra cứu trên Internet:\n{web_context_text}\n\nHãy tổng hợp ngắn gọn, đầy đủ 3 mục (không bị cụt câu)."
                            req_body = json.dumps({
                                "model": clean_model or "default",
                                "messages": [
                                    {"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": usr_prompt},
                                ],
                                "max_tokens": 320,
                                "temperature": 0.4,
                                "reasoning_effort": "none",
                            }).encode("utf-8")
                            req = urllib.request.Request(plan_endpoint, data=req_body, headers={"Content-Type": "application/json"}, method="POST")
                            with urllib.request.urlopen(req, timeout=75) as resp:
                                resp_obj = json.loads(resp.read().decode("utf-8", errors="replace"))
                                choices = resp_obj.get("choices") or []
                                if choices and isinstance(choices[0], dict):
                                    msg_obj = choices[0].get("message") or {}
                                    raw_content = str(msg_obj.get("content") or "").strip()
                                    raw_content = re.sub(r"<think>[\s\S]*?</think>", "", raw_content).strip()
                                    if "<think>" in raw_content:
                                        raw_content = raw_content.split("<think>", 1)[0].strip()
                                    if raw_content:
                                        llm_plan_reply = raw_content
                        except Exception:
                            pass
                        finally:
                            dt_llm = max(1, int(round((int(time.time() * 1000) - t0_llm) / 1000.0)))
                            _emit_activity_step(f"Thought for {dt_llm}s ›", kind="llm_think", status="completed")

                if is_code_plan and workspace:
                    rmap = build_repo_map(workspace, token_budget=400)
                    matched_sk = skill_store.match_skills(goal_text, companion_root=companion_root, max_results=2)
                    sk_names = ", ".join(f"`{s['name']}`" for s in matched_sk) if matched_sk else ("(không có)" if lang == "vi" else "(none)")
                    top_files = "\n".join(f"  - {e['summary']}" for e in rmap["entries"][:8])
                    if llm_plan_reply:
                        msg = (
                            f"### Kế hoạch thực thi cho: {display_goal}\n"
                            f"- **Workspace:** `{workspace}` ({rmap['fileCount']} tệp) · **Skills:** {sk_names}\n\n"
                            f"{llm_plan_reply}"
                            if lang == "vi"
                            else f"### Implementation Plan: {display_goal}\n"
                            f"- **Workspace:** `{workspace}` ({rmap['fileCount']} files) · **Skills:** {sk_names}\n\n"
                            f"{llm_plan_reply}"
                        )
                    elif lang == "vi":
                        msg = (
                            f"### Kế hoạch thực thi cho: {display_goal}\n"
                            f"- **Workspace:** `{workspace}` ({rmap['fileCount']} tệp)\n"
                            f"- **Skills liên quan:** {sk_names}\n"
                            f"- **Cấu trúc tệp trọng tâm:**\n{top_files}\n"
                            f"- **Các bước đề xuất:**\n"
                            f"  1. Kiểm tra ngữ cảnh và các tệp liên quan trong `{Path(workspace).name}`.\n"
                            f"  2. Thực hiện chỉnh sửa nguyên tử và bảo toàn dữ liệu hiện có.\n"
                            f"  3. Chạy `/tests` hoặc `/diagnostics` để xác minh không có hồi quy.\n"
                            f"  4. Dùng `/learn` để lưu lại quy trình hoặc bài học mới vào Skill."
                        )
                    else:
                        msg = (
                            f"### Implementation Plan: {display_goal}\n"
                            f"- **Workspace:** `{workspace}` ({rmap['fileCount']} files)\n"
                            f"- **Matched Skills:** {sk_names}\n"
                            f"- **Key Workspace Files:**\n{top_files}\n"
                            f"- **Execution Steps:**\n"
                            f"  1. Inspect affected files and contracts in `{Path(workspace).name}`.\n"
                            f"  2. Apply focused edits while preserving backward compatibility.\n"
                            f"  3. Run `/tests` or `/diagnostics` to verify zero regressions.\n"
                            f"  4. Run `/learn` to consolidate new workflow insights into a reusable Skill."
                        )
                else:
                    if llm_plan_reply:
                        msg = llm_plan_reply
                    elif lang == "vi":
                        highlights_block = (
                            "\n- **Gợi ý nổi bật từ Internet:**\n" + "\n".join(web_highlights_lines)
                            if web_highlights_lines
                            else ""
                        )
                        msg = (
                            f"### Kế hoạch chi tiết: {display_goal}{highlights_block}\n"
                            f"- **1. Chuẩn bị & vật dụng cần thiết:**\n"
                            f"  - Xác định địa điểm cụ thể, thời gian xuất phát và kiểm tra dự báo thời tiết.\n"
                            f"  - Chuẩn bị đồ ăn nhẹ, nước uống, thảm trải, túi đựng rác và vật dụng cá nhân.\n"
                            f"- **2. Lịch trình gợi ý:**\n"
                            f"  - **08:30 – Buổi sáng:** Tập trung, di chuyển đến địa điểm, dựng khu vực nghỉ ngơi và tham quan/chụp ảnh.\n"
                            f"  - **11:30 – Buổi trưa:** Dùng bữa trưa, thưởng thức đặc sản địa phương, nghỉ ngơi thư giãn và vui chơi nhóm.\n"
                            f"  - **15:30 – Buổi chiều:** Tham gia hoạt động ngoài trời, thu dọn vệ sinh sạch sẽ và di chuyển về trước khi trời tối.\n"
                            f"- **3. Lưu ý:** Mang theo sạc dự phòng, kem chống nắng/mũ nón và thuốc y tế cơ bản."
                        )
                    else:
                        highlights_block = (
                            "\n- **Top Web Highlights:**\n" + "\n".join(web_highlights_lines)
                            if web_highlights_lines
                            else ""
                        )
                        msg = (
                            f"### Action Plan: {display_goal}{highlights_block}\n"
                            f"- **1. Preparation & Checklist:**\n"
                            f"  - Confirm location, departure time, and weather forecast.\n"
                            f"  - Pack food, drinks, mat/blanket, trash bags, and personal essentials.\n"
                            f"- **2. Suggested Schedule:**\n"
                            f"  - **08:30 – Morning:** Arrive at the location, set up the base spot, and enjoy outdoor activities.\n"
                            f"  - **11:30 – Midday:** Lunch break, relaxation, and group games.\n"
                            f"  - **15:30 – Afternoon:** Clean up the area thoroughly and head back before sunset.\n"
                            f"- **3. Tips:** Bring a power bank, sunscreen/light jacket, and basic first-aid items."
                        )
                if web_sources_md:
                    src_label = "📚 **Nguồn tra cứu:**" if lang == "vi" else "📚 **Sources:**"
                    msg = f"{msg.rstrip()}\n\n{src_label} {web_sources_md}"
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

        # 14. RAG Knowledge Retrieval & Source Management with Citations
        if kind == "rag_query":
            folded = _fold_ascii(prompt)
            raw_clean = re.sub(r"^(?:/rag|rag)\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()
            if (
                not raw_clean
                or re.match(r"^(?:list|sources|status|danh sach|nguon)\b", _fold_ascii(raw_clean))
                or any(k in folded for k in ("xem nguon rag", "danh sach nguon rag", "list rag sources", "rag sources"))
            ) and not any(k in folded for k in ("them", "add", "xoa", "remove")):
                sources = rag_store.list_sources()
                active_paths = rag_store.active_source_paths(include_pocket=True)
                rows = [f"- `{s['label']}` (`{s['path']}`)" for s in sources] or (["- (chưa có nguồn tuỳ chỉnh; đang dùng tài liệu tích hợp & AquaPocket)"] if lang == "vi" else ["- (no custom sources; using built-in docs & AquaPocket)"])
                msg = (
                    f"Nguồn tri thức RAG đang hoạt động ({len(active_paths)} đường dẫn + tài liệu tích hợp):\n" + "\n".join(rows)
                    if lang == "vi"
                    else f"Active RAG knowledge sources ({len(active_paths)} path(s) + built-in docs):\n" + "\n".join(rows)
                )
            elif re.match(r"^(?:add|them)\b", _fold_ascii(raw_clean)) or any(k in folded for k in ("them vao rag", "them nguon rag", "add to rag")):
                target_p = _extract_path_from_text(prompt, session_ctx) or re.sub(r"^(?:add|them|thêm)(?:\s+nguon|\s+nguồn|\s+source)?\s+", "", raw_clean, flags=re.IGNORECASE).strip()
                added = rag_store.add_source(target_p, now_ms=now_ms)
                msg = (
                    f"Đã thêm nguồn tri thức RAG `{added['label']}` (`{added['path']}`)."
                    if lang == "vi"
                    else f"Added RAG knowledge source `{added['label']}` (`{added['path']}`)."
                )
            elif re.match(r"^(?:remove|delete|rm|xoa)\b", _fold_ascii(raw_clean)) or any(k in folded for k in ("xoa khoi rag", "xoa nguon rag", "remove from rag")):
                target_p = _extract_path_from_text(prompt, session_ctx) or re.sub(r"^(?:remove|delete|rm|xoa|xoá)(?:\s+nguon|\s+nguồn|\s+source)?\s+", "", raw_clean, flags=re.IGNORECASE).strip()
                removed = rag_store.remove_source(target_p)
                msg = (
                    (f"Đã gỡ nguồn RAG `{target_p}`." if removed else f"Không tìm thấy nguồn RAG `{target_p}`.")
                    if lang == "vi"
                    else (f"Removed RAG source `{target_p}`." if removed else f"RAG source `{target_p}` was not found.")
                )
            else:
                query_text = re.sub(r"^(?:search|query|tim|tìm|tra cuu|tra cứu)\s+", "", raw_clean, flags=re.IGNORECASE).strip() or prompt
                extra = [d["path"] for d in inspect_permissions({"nowMs": now_ms})["directories"]]
                rag = retrieve_rag(query_text, companion_root=companion_root, extra_dirs=extra, token_budget=480, page=1, page_size=3)
                if rag["snippets"]:
                    cites = "\n".join(f"- {s}" for s in rag["snippets"])
                    msg = (
                        f"Thông tin trích xuất từ RAG có dẫn nguồn ({rag['estimatedTokens']} tokens):\n{cites}"
                        if lang == "vi"
                        else f"Grounded RAG results with citations ({rag['estimatedTokens']} tokens):\n{cites}"
                    )
                else:
                    msg = "Không tìm thấy đoạn tài liệu khớp với truy vấn trong phạm vi được cấp." if lang == "vi" else "No matching documentation snippets found within permitted scope."
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 14b. MCP (Model Context Protocol) Management & Tool/Resource Execution
        if kind == "mcp_manage":
            folded = _fold_ascii(prompt)
            raw_clean = re.sub(r"^(?:/mcp|mcp)\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()
            clean_folded = _fold_ascii(raw_clean)

            if re.match(r"^(?:add|them)\s+", clean_folded) or any(k in folded for k in ("them mcp server", "add mcp server")):
                m_add = re.search(r"(?:add|them|thêm)(?:\s+mcp)?(?:\s+server)?\s+([A-Za-z0-9_.\-]+)\s+(.+)$", raw_clean, re.IGNORECASE)
                if not m_add:
                    raise McpError("invalid_server_config", "Usage: /mcp add <name> <command or http://url>")
                sname = m_add.group(1).strip()
                rest = m_add.group(2).strip()
                if rest.startswith(("http://", "https://")):
                    srv = mcp_client.add_server(name=sname, url=rest)
                else:
                    parts = rest.split()
                    srv = mcp_client.add_server(name=sname, command=parts[0], args=parts[1:])
                msg = (
                    f"Đã đăng ký MCP server `{srv['name']}` (transport `{srv['transport']}`)."
                    if lang == "vi"
                    else f"Registered MCP server `{srv['name']}` (transport `{srv['transport']}`)."
                )
            elif re.match(r"^(?:remove|delete|rm|xoa)\s+", clean_folded) or any(k in folded for k in ("xoa mcp server", "remove mcp server")):
                m_rm = re.search(r"(?:remove|delete|rm|xoa|xoá)(?:\s+mcp)?(?:\s+server)?\s+([A-Za-z0-9_.\-]+)", raw_clean, re.IGNORECASE)
                sname = m_rm.group(1) if m_rm else raw_clean.split()[-1]
                removed = mcp_client.remove_server(sname)
                msg = (
                    (f"Đã xoá MCP server `{sname}`." if removed else f"Không tìm thấy MCP server `{sname}`.")
                    if lang == "vi"
                    else (f"Removed MCP server `{sname}`." if removed else f"MCP server `{sname}` not found.")
                )
            elif re.match(r"^(?:check[-_\s]?updates?|kiem tra cap nhat)\b", clean_folded):
                servers = mcp_client.check_updates()
                upd_rows = [
                    f"- `{s['name']}`: v{s.get('version', '1.0.0')} -> v{s.get('latestVersion', '1.0.0')}"
                    for s in servers
                    if s.get("hasUpdate")
                ]
                if upd_rows:
                    msg = (
                        f"Có {len(upd_rows)} MCP server có bản cập nhật mới:\n" + "\n".join(upd_rows)
                        if lang == "vi"
                        else f"{len(upd_rows)} MCP server update(s) available:\n" + "\n".join(upd_rows)
                    )
                else:
                    msg = "Tất cả MCP server đều đã ở phiên bản mới nhất." if lang == "vi" else "All MCP servers are up to date."
            elif re.match(r"^(?:update|upgrade|cap nhat)\s+", clean_folded) or any(k in folded for k in ("cap nhat mcp", "update mcp")):
                m_up = re.search(r"(?:update|upgrade|cap nhat|cập nhật)(?:\s+mcp)?(?:\s+server)?\s+([A-Za-z0-9_.\-]+)", raw_clean, re.IGNORECASE)
                sname = m_up.group(1).strip() if m_up else raw_clean.split()[-1]
                srv = mcp_client.update_server(sname)
                msg = (
                    f"Đã cập nhật MCP server `{srv['name']}` lên phiên bản `v{srv['version']}`."
                    if lang == "vi"
                    else f"Updated MCP server `{srv['name']}` to `v{srv['version']}`."
                )
            elif re.match(r"^(?:enable|bat|disable|tat)\s+", clean_folded):
                parts = raw_clean.split()
                en = _fold_ascii(parts[0]) in ("enable", "bat")
                sname = parts[-1]
                srv = mcp_client.set_server_enabled(sname, enabled=en)
                msg = (
                    f"Đã {'bật' if srv['enabled'] else 'tắt'} MCP server `{srv['name']}`."
                    if lang == "vi"
                    else f"{'Enabled' if srv['enabled'] else 'Disabled'} MCP server `{srv['name']}`."
                )
            elif re.match(r"^(?:resources|tai nguyen)\b", clean_folded) or "mcp resource" in folded or "tai nguyen mcp" in folded:
                resources = mcp_client.list_resources()
                rows = [f"- `{r['uri']}` ({r['server']}): {r['description']}" for r in resources[:16]]
                msg = (
                    f"Tài nguyên MCP hiện có ({len(resources)}):\n" + "\n".join(rows)
                    if lang == "vi"
                    else f"Available MCP resources ({len(resources)}):\n" + "\n".join(rows)
                )
            elif re.match(r"^(?:read|doc)\s+", clean_folded) or "companion://" in raw_clean:
                m_uri = re.search(r"([a-zA-Z0-9_.\-]+://[^\s]+)", raw_clean)
                uri = m_uri.group(1) if m_uri else raw_clean.split()[-1]
                r_data = mcp_client.read_resource("companion-system", uri)
                msg = (
                    f"Nội dung tài nguyên MCP `{r_data['uri']}`:\n{r_data['text']}"
                    if lang == "vi"
                    else f"MCP Resource `{r_data['uri']}`:\n{r_data['text']}"
                )
            elif re.match(r"^(?:call|run|goi)\s+", clean_folded) or any(k in folded for k in ("goi mcp", "call mcp")):
                m_call = re.search(r"(?:call|run|goi|gọi)(?:\s+mcp)?(?:\s+tool)?\s+([A-Za-z0-9_.\-]+)(?:\s+([\s\S]+))?$", raw_clean, re.IGNORECASE)
                if not m_call:
                    raise McpError("invalid_tool", "Usage: /mcp call [server.]<tool> [json_args]")
                target_spec = m_call.group(1).strip()
                raw_args = (m_call.group(2) or "").strip()
                if "." in target_spec:
                    srv_name, t_name = target_spec.split(".", 1)
                else:
                    srv_name, t_name = "", target_spec
                args_dict = {}
                if raw_args:
                    if raw_args.startswith("{"):
                        try:
                            args_dict = json.loads(raw_args)
                        except ValueError:
                            args_dict = {"query": raw_args}
                    else:
                        args_dict = {"query": raw_args}
                called = mcp_client.call_tool(srv_name, t_name, args_dict, companion_root=companion_root)
                out_text = "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))
                msg = (
                    f"Kết quả MCP `{called['server']}.{called['tool']}`:\n{out_text}"
                    if lang == "vi"
                    else f"MCP `{called['server']}.{called['tool']}` output:\n{out_text}"
                )
            elif re.match(r"^(?:tools|cong cu)\b", clean_folded) or "mcp tool" in folded or "cong cu mcp" in folded:
                tools = mcp_client.list_tools()
                rows = [f"- `{t['server']}.{t['name']}`: {t['description']}" for t in tools[:20]]
                msg = (
                    f"Các công cụ MCP đang hoạt động ({len(tools)}):\n" + "\n".join(rows)
                    if lang == "vi"
                    else f"Active MCP tools ({len(tools)}):\n" + "\n".join(rows)
                )
            else:
                servers = mcp_client.list_servers()
                tools = mcp_client.list_tools()
                s_rows = [
                    f"- `{s['name']}` (v{s.get('version', '1.0.0')}"
                    + (f" -> v{s.get('latestVersion')}" if s.get("hasUpdate") else "")
                    + f", {s['transport']}, {'bật' if s['enabled'] else 'tắt'}): {s['description']}"
                    for s in servers
                ]
                t_names = ", ".join(f"`{t['name']}`" for t in tools[:10])
                msg = (
                    f"Hệ thống MCP (Model Context Protocol) — {len(servers)} server, {len(tools)} công cụ ({t_names}):\n" + "\n".join(s_rows)
                    if lang == "vi"
                    else f"MCP (Model Context Protocol) — {len(servers)} server(s), {len(tools)} tool(s) ({t_names}):\n" + "\n".join(s_rows)
                )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 14d. Web Search, URL Reading & Browser Control (`web-search-mcp`, `fetch-mcp`, `playwright-mcp`)
        if kind == "web_task":
            sub = intent.get("subkind") or "search"
            folded = _fold_ascii(prompt)
            raw_clean = re.sub(r"^/(?:web|search|browser|fetch)\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()

            m_url = re.search(r"(https?://[^\s'\"`<>]+)", raw_clean)
            if sub == "fetch_url" or (m_url and sub != "open_browser"):
                target_url = m_url.group(1) if m_url else raw_clean.split()[-1]
                step_id = record_step_start(run_id, 1, "fetch_url", side_effect=False, target_path=target_url, now_ms=now_ms)
                called = mcp_client.call_tool(
                    "fetch-mcp",
                    "fetch_url",
                    {"url": target_url, "maxChars": 3200},
                    companion_root=companion_root,
                    enabled_overrides={"fetch-mcp": True},
                )
                record_step_finish(step_id, status="completed", detail={"url": target_url})
                advance_turn(run_id, now_ms=now_ms)
                out_text = "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))
                msg = (
                    f"Nội dung trích xuất từ trang web qua `fetch-mcp` (Eyes):\n{out_text}"
                    if lang == "vi"
                    else f"Webpage content extracted via `fetch-mcp` (Eyes):\n{out_text}"
                )
            elif sub == "open_browser":
                from web_tools import extract_clean_browser_query, short_source_domain
                engine = "google"
                if "youtube" in folded or re.search(r"\b(?:mo bai|phat bai|bat bai|nghe bai|mo ca khuc|phat ca khuc|play song|play music|play video)\b", folded):
                    engine = "youtube"
                elif "github" in folded:
                    engine = "github"
                elif "wikipedia" in folded or "wiki" in folded:
                    engine = "wikipedia"
                elif "duckduckgo" in folded or "ddg" in folded:
                    engine = "duckduckgo"

                if m_url:
                    target_str = m_url.group(1)
                    clean_query = target_str
                    wants_play = False
                else:
                    cleaned_q = extract_clean_browser_query(raw_clean, engine=engine)
                    clean_query = str(cleaned_q.get("query") or "").strip()
                    engine = str(cleaned_q.get("engine") or engine)
                    wants_play = bool(cleaned_q.get("wantsPlay"))
                    target_str = clean_query or raw_clean

                step_id = record_step_start(run_id, 1, "browser_open", side_effect=True, target_path=target_str, now_ms=now_ms)
                opened_call = mcp_client.call_tool(
                    "playwright-mcp",
                    "browser_open",
                    {"target": raw_clean if not m_url else target_str, "engine": engine, "playMedia": wants_play},
                    companion_root=companion_root,
                    enabled_overrides={"playwright-mcp": True},
                )
                opened_info = opened_call.get("structured") or {}
                record_step_finish(step_id, status="completed", detail=opened_info)
                advance_turn(run_id, now_ms=now_ms)

                extra_search_text = ""
                search_q_for_eyes = str(opened_info.get("cleanQuery") or clean_query or "").strip()
                if search_q_for_eyes and not m_url:
                    try:
                        srch_call = mcp_client.call_tool(
                            "web-search-mcp",
                            "github_search" if engine == "github" else ("wikipedia_search" if engine == "wikipedia" else "web_search"),
                            {"query": search_q_for_eyes, "maxResults": 4},
                            companion_root=companion_root,
                            enabled_overrides={"web-search-mcp": True},
                        )
                        srch_lines = (srch_call.get("structured") or {}).get("lines") or []
                        if srch_lines:
                            extra_search_text = "\n\n" + (
                                "**Kết quả liên quan (Web Search Eyes):**\n" if lang == "vi" else "**Related search results (Web Search Eyes):**\n"
                            ) + "\n".join(srch_lines)
                    except Exception:
                        pass

                opened_url = str(opened_info.get("url") or target_str)
                short_dom = short_source_domain(opened_url) if opened_url.startswith(("http://", "https://")) else opened_url
                md_link = f"[{short_dom}]({opened_url})" if opened_url.startswith(("http://", "https://")) else f"`{opened_url}`"
                if opened_info.get("playedVideo"):
                    vid_title = str(opened_info.get("videoTitle") or search_q_for_eyes or target_str)
                    msg = (
                        f"Đã mở trình duyệt và phát trực tiếp **{vid_title}** trên YouTube ({md_link}).{extra_search_text}"
                        if lang == "vi"
                        else f"Opened default browser and started playing **{vid_title}** on YouTube ({md_link}).{extra_search_text}"
                    )
                else:
                    msg = (
                        f"Đã mở trình duyệt mặc định ({md_link} — `{opened_url}`) qua `playwright-mcp` (Hands).{extra_search_text}"
                        if lang == "vi"
                        else f"Opened default browser ({md_link} — `{opened_url}`) via `playwright-mcp` (Hands).{extra_search_text}"
                    )
            else:
                tool_to_use = "web_search"
                if "github" in folded:
                    tool_to_use = "github_search"
                elif "wikipedia" in folded or "wiki" in folded:
                    tool_to_use = "wikipedia_search"

                query_str = re.sub(
                    r"^(?:/(?:web|search)\s+)?(?:hay\s+|vui long\s+|please\s+)?(?:tim kiem tren web|tìm kiếm trên web|tim tren web|tìm trên web|tim kiem tren mang|tìm kiếm trên mạng|tim tren mang|tìm trên mạng|tra cuu tren web|tra cứu trên web|tra cuu tren mang|tra cứu trên mạng|tra cuu tren internet|tra cứu trên internet|tra cuu thong tin tren internet|tra cứu thông tin trên internet|tim tren github|tìm trên github|search github|tim tren wikipedia|tìm trên wikipedia|search wikipedia|tra cuu wikipedia|tra cứu wikipedia|tim google|tìm google|search google|search web|web search)(?:\s+(?:ve|về|cho toi|cho mình|for|about))?\s*:?\s*",
                    "",
                    raw_clean,
                    flags=re.IGNORECASE,
                ).strip() or raw_clean

                step_id = record_step_start(run_id, 1, tool_to_use, side_effect=False, target_path=query_str, now_ms=now_ms)
                called = mcp_client.call_tool(
                    "web-search-mcp",
                    tool_to_use,
                    {"query": query_str, "maxResults": 5},
                    companion_root=companion_root,
                    enabled_overrides={"web-search-mcp": True},
                )
                structured = called.get("structured") or {}
                record_step_finish(step_id, status="completed", detail={"query": query_str, "count": len(structured.get("results") or [])})
                advance_turn(run_id, now_ms=now_ms)
                lines = structured.get("lines") or []
                body = "\n".join(lines) if lines else ("(Không tìm thấy kết quả phù hợp)" if lang == "vi" else "(No matching results found)")
                sources_md = str(structured.get("sources_markdown") or web_tools.format_markdown_sources(structured.get("results") or [], max_sources=5))
                sources_footer = (
                    f"\n\n📚 **Nguồn tra cứu:** {sources_md}"
                    if (sources_md and lang == "vi")
                    else (f"\n\n📚 **Sources:** {sources_md}" if sources_md else "")
                )
                msg = (
                    f"Kết quả tìm kiếm dữ liệu qua `web-search-mcp` ({structured.get('engine', 'web')}) cho **{query_str}**:\n{body}{sources_footer}"
                    if lang == "vi"
                    else f"Search results via `web-search-mcp` ({structured.get('engine', 'web')}) for **{query_str}**:\n{body}{sources_footer}"
                )

            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 14c. Hermes Self-Learning, Workflow Adaptation & Agent Skills (`SKILL.md`)
        if kind == "skill_manage":
            folded = _fold_ascii(prompt)
            direct_skill = str(intent.get("directSkill") or "").strip()
            raw_clean = re.sub(r"^(?:/skills?|skills?|/learn|/workflow|/reflect)\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()
            clean_folded = _fold_ascii(raw_clean)

            if direct_skill:
                arg_text = re.sub(rf"^/{re.escape(direct_skill)}\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()
                ran = skill_store.run_skill(
                    direct_skill,
                    prompt=arg_text,
                    companion_root=companion_root,
                    workspace=session_ctx.get("lastProject") or session_ctx.get("lastDirectory"),
                )
                details = ran["output"] if ran["output"] else ran["instructions"][:900]
                msg = (
                    f"Đã kích hoạt Skill `{ran['skill']}` (v{ran.get('version', 1)} — {ran['description']}):\n{details}"
                    if lang == "vi"
                    else f"Executed Skill `{ran['skill']}` (v{ran.get('version', 1)} — {ran['description']}):\n{details}"
                )
            elif (
                re.match(r"^/reflect\b", folded)
                or (re.match(r"^/workflow\b", folded) and not raw_clean)
                or re.match(r"^(?:reflections?|history|lich su)\b", clean_folded)
                or any(k in folded for k in ("rut kinh nghiem", "workflow reflections"))
            ):
                refs = skill_store.list_workflow_reflections(limit=8)
                if not refs and raw_clean:
                    learned = skill_store.learn_skill(
                        raw_clean,
                        session_ctx={"workspace": session_ctx.get("lastProject") or session_ctx.get("lastDirectory"), "history": []},
                        companion_root=companion_root,
                    )
                    sk = learned["skill"]
                    msg = (
                        f"Đã tự học và cập nhật Skill `{sk['name']}` (v{sk.get('version', 1)}): {sk['description']}"
                        if lang == "vi"
                        else f"Self-learned and updated Skill `{sk['name']}` (v{sk.get('version', 1)}): {sk['description']}"
                    )
                else:
                    rows = [
                        f"- `{r.get('prompt', 'task')[:60]}` ({r.get('status', 'completed')}): {r.get('lesson') or r.get('summary', '')[:120]}"
                        for r in refs
                    ] or (["- (Chưa có bản ghi tự phản tư nào; dùng `/learn <quy trình>` để dạy Companion quy trình mới.)"] if lang == "vi" else ["- (No workflow reflections yet; use `/learn <workflow>` to teach Companion a new workflow.)"])
                    msg = (
                        f"Nhật ký tự học & điều chỉnh quy trình ({len(refs)} mục gần nhất):\n" + "\n".join(rows)
                        if lang == "vi"
                        else f"Self-learning & workflow reflections ({len(refs)} recent entries):\n" + "\n".join(rows)
                    )
            elif (
                re.match(r"^/(?:learn|workflow)\b", folded)
                or re.match(r"^(?:learn|adapt|hoc|tu hoc)\b", clean_folded)
                or any(k in folded for k in ("tu hoc", "hoc quy trinh", "luu quy trinh", "dieu chinh quy trinh", "learn skill", "learn workflow", "adapt workflow", "self-learn"))
            ):
                learn_target = re.sub(
                    r"^(?:hay\s+|vui long\s+|please\s+)?(?:tu hoc\s+quy\s+trinh|tự học\s+quy\s+trình|tu hoc\s+skill|tự học\s+skill|tu hoc\s+ky\s+nang|tự học\s+kỹ\s+năng|hoc quy trinh|học quy trình|luu quy trinh|lưu quy trình|dieu chinh quy trinh|điều chỉnh quy trình|tu hoc|tự học|learn skill|learn workflow|adapt workflow|self-learn|learn)\s*:?\s*",
                    "",
                    raw_clean or prompt,
                    flags=re.IGNORECASE,
                ).strip()
                learned = skill_store.learn_skill(
                    learn_target,
                    session_ctx={
                        "workspace": session_ctx.get("lastProject") or session_ctx.get("lastDirectory"),
                    },
                    companion_root=companion_root,
                )
                sk = learned["skill"]
                refs_str = f" (references: {', '.join(sk.get('references', []))})" if sk.get("references") else ""
                act_vi = "đã cập nhật/tinh chỉnh" if learned["action"] == "patched" else "đã tổng hợp mới"
                act_en = "patched/refined" if learned["action"] == "patched" else "synthesized new"
                msg = (
                    f"Companion {act_vi} Skill `{sk['name']}` (v{sk.get('version', 1)}, chế độ `{learned['mode']}`){refs_str}:\n"
                    f"{sk['description']}\n\n{sk['instructions'][:1000]}"
                    if lang == "vi"
                    else f"Companion {act_en} Skill `{sk['name']}` (v{sk.get('version', 1)}, mode `{learned['mode']}`){refs_str}:\n"
                         f"{sk['description']}\n\n{sk['instructions'][:1000]}"
                )
            elif (
                re.match(r"^(?:patch|update|refine|adjust|cap nhat|dieu chinh|chinh sua|bo sung)\s+", clean_folded)
                or any(k in folded for k in ("cap nhat skill", "dieu chinh skill", "chinh sua skill", "bo sung skill", "patch skill", "update skill"))
            ):
                m_pt = re.search(
                    r"(?:patch|update|refine|adjust|cap nhat|cập nhật|dieu chinh|điều chỉnh|chinh sua|chỉnh sửa|bo sung|bổ sung)(?:\s+skill|\s+ky\s+nang|\s+kỹ\s+năng)?\s+([A-Za-z0-9_\-]+)\s*:\s*([\s\S]+)$",
                    prompt,
                    re.IGNORECASE,
                )
                if not m_pt:
                    raise SkillError("invalid_skill_patch", "Usage: /skills patch <name>: <adjustment or old -> new>")
                sname = m_pt.group(1).strip()
                patch_body = m_pt.group(2).strip()
                if "->" in patch_body:
                    old_part, new_part = [p.strip() for p in patch_body.split("->", 1)]
                    patched = skill_store.patch_skill(sname, old_text=old_part, new_text=new_part, companion_root=companion_root)
                else:
                    patched = skill_store.patch_skill(sname, append_note=patch_body, companion_root=companion_root)
                msg = (
                    f"Đã cập nhật Skill `{patched['name']}` lên phiên bản v{patched.get('version', 2)} ({patched['description']})."
                    if lang == "vi"
                    else f"Patched Skill `{patched['name']}` to v{patched.get('version', 2)} ({patched['description']})."
                )
            elif re.match(r"^(?:create|add|tao|them)\s+", clean_folded) or any(k in folded for k in ("tao skill", "tao ky nang", "create skill")):
                m_cr = re.search(r"(?:create|add|tao|tạo|them|thêm)(?:\s+skill|\s+ky\s+nang|\s+kỹ\s+năng)?\s+([A-Za-z0-9_\-]+)\s*:\s*([\s\S]+)$", prompt, re.IGNORECASE)
                if not m_cr:
                    raise SkillError("invalid_skill_spec", "Usage: /skill create <name>: <description> | <instructions>")
                sname = m_cr.group(1).strip()
                spec_body = m_cr.group(2).strip()
                if "|" in spec_body:
                    desc_part, inst_part = [p.strip() for p in spec_body.split("|", 1)]
                else:
                    desc_part, inst_part = spec_body[:160], spec_body
                created = skill_store.create_skill(sname, desc_part, inst_part)
                msg = (
                    f"Đã tạo Skill `{created['name']}` tại `{created['path']}` ({created['description']})."
                    if lang == "vi"
                    else f"Created Skill `{created['name']}` at `{created['path']}` ({created['description']})."
                )
            elif re.match(r"^(?:delete|remove|rm|xoa)\s+", clean_folded) or any(k in folded for k in ("xoa skill", "delete skill")):
                sname = raw_clean.split()[-1]
                skill_store.delete_skill(sname, companion_root=companion_root)
                msg = f"Đã xoá Skill `{sname}`." if lang == "vi" else f"Deleted Skill `{sname}`."
            elif re.match(r"^(?:enable|bat|disable|tat)\s+", clean_folded) or any(k in folded for k in ("bat skill", "tat skill", "enable skill", "disable skill")):
                en = any(w in clean_folded.split()[:2] for w in ("enable", "bat"))
                sname = raw_clean.split()[-1]
                updated = skill_store.set_skill_enabled(sname, enabled=en, companion_root=companion_root)
                msg = (
                    f"Đã {'bật' if updated['enabled'] else 'tắt'} Skill `{updated['name']}`."
                    if lang == "vi"
                    else f"{'Enabled' if updated['enabled'] else 'Disabled'} Skill `{updated['name']}`."
                )
            elif re.match(r"^(?:reference|ref)\s+", clean_folded):
                parts = raw_clean.split()
                if len(parts) < 3:
                    raise SkillError("invalid_reference", "Usage: /skills reference <skill_name> <reference_file>")
                ref_res = skill_store.read_skill_reference(parts[1], parts[2], companion_root=companion_root)
                msg = (
                    f"Tài liệu tham chiếu `{ref_res['reference']}` của Skill `{ref_res['skill']}`:\n{ref_res['content'][:1600]}"
                    if lang == "vi"
                    else f"Reference `{ref_res['reference']}` from Skill `{ref_res['skill']}`:\n{ref_res['content'][:1600]}"
                )
            elif re.match(r"^(?:inspect|info|show|view|chi tiet)\s+", clean_folded):
                sname = raw_clean.split()[-1]
                sk = skill_store.inspect_skill(sname, companion_root=companion_root)
                refs_line = f"\nReferences: {', '.join(sk.get('references', []))}" if sk.get("references") else ""
                msg = (
                    f"Skill `{sk['name']}` (v{sk.get('version', 1)}, {sk['source']}, {'bật' if sk['enabled'] else 'tắt'}, dùng {sk.get('usageCount', 0)} lần):\n{sk['description']}{refs_line}\n\n{sk['instructions'][:1200]}"
                    if lang == "vi"
                    else f"Skill `{sk['name']}` (v{sk.get('version', 1)}, {sk['source']}, {'enabled' if sk['enabled'] else 'disabled'}, used {sk.get('usageCount', 0)}x):\n{sk['description']}{refs_line}\n\n{sk['instructions'][:1200]}"
                )
            elif (
                re.match(r"^(?:run|use|exec|chay|dung)\s+", clean_folded)
                or any(k in folded for k in ("chay skill", "dung skill", "dung ky nang", "run skill", "use skill"))
                or any(b in folded for b in ("system-diagnostics", "code-assistant", "rag-researcher", "focus-planner", "screen-copilot", "desktop-organizer", "window-workspace-layout", "git-daily-brief", "system-tuner", "clipboard-transformer"))
            ):
                m_run_sk = re.search(
                    r"(?:run|use|exec|chay|chạy|dung|dùng)(?:\s+skill|\s+ky\s+nang|\s+kỹ\s+năng)?\s+([A-Za-z0-9_\-]+)(?:\s+([\s\S]+))?$",
                    raw_clean,
                    re.IGNORECASE,
                )
                if m_run_sk:
                    sname = m_run_sk.group(1).strip()
                    arg_text = (m_run_sk.group(2) or "").strip()
                else:
                    matched = skill_store.match_skills(prompt, companion_root=companion_root, max_results=1)
                    if not matched:
                        raise SkillError("skill_not_found", "Specify a skill name to run.")
                    sname = matched[0]["name"]
                    arg_text = raw_clean
                ran = skill_store.run_skill(
                    sname,
                    prompt=arg_text,
                    companion_root=companion_root,
                    workspace=session_ctx.get("lastProject") or session_ctx.get("lastDirectory"),
                )
                details = ran["output"] if ran["output"] else ran["instructions"][:900]
                msg = (
                    f"Đã kích hoạt Skill `{ran['skill']}` ({ran['description']}):\n{details}"
                    if lang == "vi"
                    else f"Executed Skill `{ran['skill']}` ({ran['description']}):\n{details}"
                )
            else:
                skills = skill_store.list_skills(companion_root=companion_root)
                rows = [
                    f"- `{s['name']}` (v{s.get('version', 1)}, {s['source']}, {'bật' if s['enabled'] else 'tắt'}): {s['description']}"
                    for s in skills
                ]
                msg = (
                    f"Danh sách Agent Skills (`SKILL.md`) hiện có ({len(skills)} kỹ năng — gõ `/<tên-skill>` để chạy hoặc `/learn` để tự học):\n" + "\n".join(rows)
                    if lang == "vi"
                    else f"Available Agent Skills (`SKILL.md`, {len(skills)} skill(s) — type `/<skill-name>` to run or `/learn` to self-learn):\n" + "\n".join(rows)
                )
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

        # 15b. Git repository inspection & tasks
        if kind == "git_task":
            folded = _fold_ascii(prompt)
            workspace, _ = _resolve_conversational_target(prompt, session_ctx, expect_dir=True)
            if Path(workspace).is_file():
                workspace = str(Path(workspace).parent)
            if "diff" in folded:
                subact = "diff"
            elif "log" in folded or "commit" in folded:
                subact = "log"
            elif "branch" in folded or "nhanh" in folded:
                subact = "branch"
            else:
                subact = "status"
            step_id = record_step_start(run_id, 1, "git_inspect", side_effect=False, target_path=workspace, now_ms=now_ms)
            git_res = desktop_control.execute_git_task(workspace=workspace, subaction=subact)
            record_step_finish(step_id, status="completed", target_path=workspace, detail=git_res)
            advance_turn(run_id, now_ms=now_ms)
            body_lines = "\n".join(f"- {ln}" for ln in git_res.get("lines", []))
            msg = (
                f"Kết quả Git (`{subact}`) tại `{Path(workspace).name}` ({git_res['summary']}):\n{body_lines}"
                if lang == "vi"
                else f"Git (`{subact}`) in `{Path(workspace).name}` ({git_res['summary']}):\n{body_lines}"
            )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="working", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 15c. System telemetry, process management, network & systemd service control
        if kind == "system_control":
            sub = intent["subkind"]
            folded = _fold_ascii(prompt)
            step_id = record_step_start(run_id, 1, f"system_{sub}", side_effect=(sub == "process" and "kill" in folded), now_ms=now_ms)
            if sub == "process":
                if any(k in folded for k in ("dung tien trinh", "tat tien trinh", "kill process", "kill pid")):
                    m_pid = re.search(r"\b(\d{2,8})\b", prompt)
                    pid_val = int(m_pid.group(1)) if m_pid else 0
                    proc_res = desktop_control.manage_processes(action="kill", pid=pid_val)
                elif any(k in folded for k in ("tim tien trinh", "find process", "search process")):
                    q_match = re.sub(
                        r"^(?:hay\s+|vui long\s+|please\s+)?(?:/process(?:es)?\s+|tim tien trinh|tìm tiến trình|find process|search process)\s*",
                        "",
                        prompt,
                        flags=re.IGNORECASE,
                    ).strip()
                    proc_res = desktop_control.manage_processes(action="search", query=q_match)
                else:
                    proc_res = desktop_control.manage_processes(action="list")
                record_step_finish(step_id, status="completed", detail=proc_res)
                advance_turn(run_id, now_ms=now_ms)
                body = "\n".join(f"- `{ln}`" for ln in proc_res.get("lines", []))
                msg = f"{proc_res['summary']}" + (f":\n{body}" if body else "")
            elif sub == "network_or_service":
                if "service" in folded:
                    m_srv = re.search(r"(?:service)\s+([A-Za-z0-9_.\-@]+)", prompt, re.IGNORECASE)
                    sname = m_srv.group(1) if m_srv else ""
                    act = "service_restart" if ("restart" in folded or "khoi dong lai" in folded) else "service_status"
                    net_res = desktop_control.inspect_network_or_service(action=act, target=sname, user_scope=True)
                else:
                    net_res = desktop_control.inspect_network_or_service(action="network")
                record_step_finish(step_id, status="completed", detail=net_res)
                advance_turn(run_id, now_ms=now_ms)
                body = "\n".join(f"- {ln}" for ln in net_res.get("lines", []))
                msg = f"{net_res['summary']}" + (f":\n{body}" if body else "")
            else:
                tel_res = desktop_control.get_system_telemetry()
                record_step_finish(step_id, status="completed", detail=tel_res)
                advance_turn(run_id, now_ms=now_ms)
                body = "\n".join(tel_res.get("lines", []))
                msg = (
                    f"Trạng thái hệ thống hiện tại ({tel_res['summary']}):\n{body}"
                    if lang == "vi"
                    else f"System telemetry ({tel_res['summary']}):\n{body}"
                )
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="working", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 15d. Desktop, window, workspace, app launch, audio, brightness, media, clipboard & notification control
        if kind == "desktop_control":
            sub = intent["subkind"]
            folded = _fold_ascii(prompt)
            step_id = record_step_start(run_id, 1, f"desktop_{sub}", side_effect=(sub != "windows"), now_ms=now_ms)
            if sub == "windows":
                if any(k in folded for k in ("dong cua so", "close window")):
                    m_id = re.search(r"\b(\d+)\b", prompt)
                    desk_res = desktop_control.manage_desktop(action="close_window", target=m_id.group(1) if m_id else "")
                elif any(k in folded for k in ("chuyen workspace", "switch workspace", "go to workspace")):
                    m_ws = re.search(r"\b(\d+)\b", prompt)
                    desk_res = desktop_control.manage_desktop(action="switch_workspace", target=m_ws.group(1) if m_ws else "1")
                else:
                    desk_res = desktop_control.manage_desktop(action="list_windows")
            elif sub == "launch":
                m_app = re.search(
                    r"^(?:hãy\s+|vui lòng\s+|please\s+)?(?:mở ứng dụng|mo ung dung|chạy ứng dụng|chay ung dung|khởi động ứng dụng|khoi dong ung dung|open app|launch app|start app)\s+(.+)$",
                    prompt,
                    re.IGNORECASE,
                )
                target_app = (m_app.group(1) if m_app else prompt.split()[-1]).strip().strip("'\"`")
                desk_res = desktop_control.manage_desktop(action="launch_app", target=target_app)
            elif sub == "volume":
                if "tat tieng" in folded or (re.search(r"\bmute\b", folded) and "unmute" not in folded):
                    val = "mute"
                elif "bat tieng" in folded or "unmute" in folded:
                    val = "unmute"
                elif "tang" in folded or "up" in folded:
                    m_num = re.search(r"(\d+)\s*%", prompt)
                    val = f"+{m_num.group(1)}%" if m_num else "up"
                elif "giam" in folded or "down" in folded:
                    m_num = re.search(r"(\d+)\s*%", prompt)
                    val = f"-{m_num.group(1)}%" if m_num else "down"
                else:
                    m_num = re.search(r"(\d+)\s*%?", prompt)
                    val = f"{m_num.group(1)}%" if m_num else "get"
                desk_res = desktop_control.manage_desktop(action="volume", value=val)
            elif sub == "brightness":
                if "tang" in folded or "up" in folded:
                    val = "up"
                elif "giam" in folded or "down" in folded:
                    val = "down"
                else:
                    m_num = re.search(r"(\d+)\s*%?", prompt)
                    val = f"{m_num.group(1)}%" if m_num else "get"
                desk_res = desktop_control.manage_desktop(action="brightness", value=val)
            elif sub == "media":
                if "tiep theo" in folded or "next" in folded:
                    val = "next"
                elif "truoc" in folded or "previous" in folded or "prev" in folded:
                    val = "previous"
                elif "tam dung" in folded or "pause" in folded:
                    val = "pause"
                elif "phat" in folded or "play" in folded:
                    val = "play"
                else:
                    val = "play-pause"
                desk_res = desktop_control.manage_desktop(action="media", value=val)
            elif sub == "clipboard":
                if any(k in folded for k in ("copy vao", "ghi vao", "luu vao", "set clipboard", "copy to clipboard")):
                    m_col = re.search(r":\s*([\s\S]+)$", prompt)
                    m_q = re.search(r"""['"`]([^'"`]+)['"`]""", prompt)
                    clip_val = m_col.group(1).strip() if m_col else (m_q.group(1) if m_q else "")
                    desk_res = desktop_control.manage_desktop(action="clipboard_set", value=clip_val)
                else:
                    desk_res = desktop_control.manage_desktop(action="clipboard_get")
            else:
                m_col = re.search(r":\s*([\s\S]+)$", prompt)
                body_txt = m_col.group(1).strip() if m_col else prompt
                desk_res = desktop_control.manage_desktop(action="notify", target="Alis Companion", value=body_txt)

            record_step_finish(step_id, status="completed", detail=desk_res)
            advance_turn(run_id, now_ms=now_ms)
            body_lines = "\n".join(f"- {ln}" for ln in desk_res.get("lines", []))
            msg = f"{desk_res['summary']}" + (f":\n{body_lines}" if body_lines else "")
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
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

            # Check copy / cut (move / rename) in natural language or command syntax
            m_copy = re.match(
                r"^(?:/agent\s+|/)?(?:(?:hãy|hay|vui\s+lòng|vui\s+long|làm\s+ơn|lam\s+on|giúp\s+tôi|giup\s+toi|giúp\s+mình|giup\s+minh|tôi\s+muốn|toi\s+muon|mình\s+muốn|minh\s+muon|please|can\s+you|could\s+you|help\s+me|i\s+want\s+to)\s+)*(copy|cp|sao\s+chép|sao\s+chep|cut|cắt|cat|move|mv|di\s+chuyển|di\s+chuyen|chuyển|chuyen|đổi\s+tên|doi\s+ten|rename)\s+(?:(?:file|tệp|tep|thư\s+mục|thu\s+muc|folder|directory)\s+)?(.+?)\s*(?:->|=>|\s+to\s+|\s+into\s+|\s+sang\s+|\s+tới\s+|\s+toi\s+|\s+vào\s+|\s+vao\s+|\s+qua\s+|\s+đến\s+|\s+den\s+|\s+thành\s+|\s+thanh\s+)\s*(?:(?:file|tệp|tep|thư\s+mục|thu\s+muc|folder|directory)\s+)?(.+)$",
                prompt.strip(),
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
                names = ", ".join(f"`{Path(c).name}`" for c in ambiguous_candidates[:6])
                msg = (
                    f"Có nhiều tệp phù hợp trong ngữ cảnh vừa rồi ({names}). Bạn muốn mình thao tác trên tệp nào?"
                    if lang == "vi"
                    else f"Multiple files match your reference ({names}). Which one would you like me to use?"
                )
                clar_opts = [
                    {
                        "label": Path(c).name,
                        "description": str(Path(c).parent),
                        "value": Path(c).name,
                        "targetPath": str(c),
                        "recommended": idx == 0,
                    }
                    for idx, c in enumerate(ambiguous_candidates[:3])
                ]
                clar_payload = _build_clarification_payload(
                    question=(
                        "Có nhiều tệp phù hợp trong ngữ cảnh vừa rồi. Bạn muốn thao tác trên tệp nào?"
                        if lang == "vi"
                        else "Multiple files match your reference. Which file would you like me to use?"
                    ),
                    options=clar_opts,
                    original_prompt=prompt,
                    language=lang,
                    kind="file_ambiguity",
                    custom_placeholder=(
                        "Nhập tên tệp khác hoặc yêu cầu cụ thể..."
                        if lang == "vi"
                        else "Enter another filename or custom instruction..."
                    ),
                )
                session_ctx["pendingClarification"] = {
                    "kind": "file_ambiguity",
                    "originalPrompt": prompt,
                    "candidates": ambiguous_candidates[:6],
                    "options": clar_payload["options"],
                }
                save_session_context(session_ctx, now_ms=now_ms)
                finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                res = _format_reply(
                    msg,
                    expression="thinking",
                    status="awaiting_clarification",
                    run_id=run_id,
                    language=lang,
                    session_ctx=session_ctx,
                    clarification=clar_payload,
                )
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

            # Check if delete is requested
            if re.search(r"\b(?:xoa tep|xoa file|delete file|remove file|rm )\b", folded) or re.match(r"^(?:/agent\s+|/)?(?:delete|rm|xoa)\b", folded):
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

            # Check if shell command execution (`run`) is requested in natural language or command syntax
            m_run = re.match(
                r"^(?:/agent\s+|/)?(?:(?:hãy|hay|vui\s+lòng|vui\s+long|làm\s+ơn|lam\s+on|giúp\s+tôi|giup\s+toi|giúp\s+mình|giup\s+minh|tôi\s+muốn|toi\s+muon|mình\s+muốn|minh\s+muon|please|can\s+you|could\s+you|help\s+me|i\s+want\s+to)\s+)*(?:run(?:\s+command|\s+script)?|exec|execute(?:\s+command)?|chay\s+lenh|chay\s+script|chay\s+file|thuc\s+thi(?:\s+lenh)?|chạy\s+lệnh|chạy\s+script|chạy\s+file|thực\s+thi(?:\s+lệnh)?)\s*:?\s+([\s\S]+)$",
                prompt.strip(),
                re.IGNORECASE,
            )
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
            m_colon_edit = re.search(r"(?:sửa|sua|ghi|thêm|them|viết|viet|tạo|tao|edit|write|append|create)[^:]*:+\s*([\s\S]+)$", prompt, re.IGNORECASE)
            is_slash_edit = bool(
                re.match(
                    r"^(?:/agent\s+|/)?(?:(?:hay|vui long|lam on|please|can you|let'?s)\s+)?(?:chinh\s+sua|sua|edit|write|append|create)\b",
                    folded,
                )
            )
            is_edit_intent = (
                bool(m_colon_edit)
                or is_slash_edit
                or bool(
                    re.search(
                        r"\b(?:chinh\s+sua|sua|edit)\b.*(?:~/|/|\.\.?/|\b[a-z0-9_.\-]+\.(?:txt|md|py|qml|js|cjs|mjs|ts|tsx|jsx|json|rs|toml|sh|bash|zsh|fish|yaml|yml|html|css|scss|vue|svelte|c|cpp|cc|h|hpp|go|java|kt|rb|php|lua|sql|xml|csv|tsv|env|nix|log|conf|cfg|ini|service)\b)",
                        folded,
                    )
                )
                or any(
                    k in folded
                    for k in (
                        "sua tep", "sua file", "chinh sua tep", "chinh sua file",
                        "sua code", "chinh sua code", "mo trinh sua", "de sua", "ghi them",
                        "them dong", "tao tep", "tao file", "edit file", "edit code",
                        "edit this", "edit that", "open in editor", "open editor",
                        "append to", "write to",
                    )
                )
            )

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
                has_explicit_content = bool(content_to_write)
                if not content_to_write:
                    m_quote = re.search(r"""['"`]([^'"`]+)['"`]\s*$""", prompt)
                    if m_quote:
                        cand_quote = m_quote.group(1).strip()
                        if cand_quote and cand_quote != target and Path(cand_quote).name != Path(resolved_target).name:
                            content_to_write = cand_quote
                            has_explicit_content = True
                mode = "append" if any(k in folded for k in ("them", "append", "cuoi tep")) and Path(resolved_target).exists() else "write"

                perm = check_permission("file_write", target_path=resolved_target, explicit_prompt_target=True, now_ms=now_ms)
                if not perm["allowed"]:
                    if perm.get("requiresSudo"):
                        return _prompt_for_system_sudo(
                            "edit" if has_explicit_content else "open_editor",
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

                step_id = record_step_start(
                    run_id,
                    2 if summary_part else 1,
                    "file_mutate" if has_explicit_content else "file_open_editor",
                    side_effect=bool(has_explicit_content or not Path(resolved_target).exists()),
                    target_path=resolved_target,
                    now_ms=now_ms,
                )
                if has_explicit_content:
                    edited = agent_file_op({
                        "op": "edit",
                        "targetPath": resolved_target,
                        "content": content_to_write,
                        "mode": mode,
                        "lastDirectory": last_dir,
                        "nowMs": now_ms,
                    })
                else:
                    edited = agent_file_op({
                        "op": "open_editor",
                        "targetPath": resolved_target,
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

                target_name = Path(edited["targetPath"]).name
                popup_meta = edited.get("editorPopup") or {}
                line_cnt = int(popup_meta.get("lineCount") or 0)
                byte_sz = int(popup_meta.get("byteSize") or 0)
                ed_cmd = str(edited.get("editorCommand") or "xdg-open")

                if not has_explicit_content:
                    if edited.get("editorMode") == "external":
                        msg = summary_part + (
                            f"Tệp `{target_name}` có kích thước lớn ({line_cnt} dòng, {byte_sz} bytes) nên đã được mở bằng trình Editor mặc định của máy (`{ed_cmd}`)."
                            if lang == "vi"
                            else f"File `{target_name}` is large ({line_cnt} lines, {byte_sz} bytes); opened in your system's default editor (`{ed_cmd}`)."
                        )
                    else:
                        msg = summary_part + (
                            f"Đã mở `{target_name}` ({line_cnt} dòng, {byte_sz} bytes) trong Editor Popup (+50% kích thước)."
                            if lang == "vi"
                            else f"Opened `{target_name}` ({line_cnt} lines, {byte_sz} bytes) in the inline Editor Popup (+50% expanded view)."
                        )
                else:
                    ext_note_vi = f" Đồng thời đã mở bằng trình Editor mặc định (`{ed_cmd}`) vì tệp lớn." if edited.get("editorMode") == "external" else ""
                    ext_note_en = f" Also opened in the system default editor (`{ed_cmd}`) because the file is large." if edited.get("editorMode") == "external" else ""
                    msg = summary_part + (
                        f"Đã cập nhật `{target_name}` và kiểm tra đọc lại thành công ({edited['summary']}).{ext_note_vi}"
                        if lang == "vi"
                        else f"Updated `{target_name}` and verified readback ({edited['summary']}).{ext_note_en}"
                    )
                finish_run(run_id, status="completed", now_ms=now_ms)
                res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, receipts=receipts, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {
                    "handled": True,
                    "intent": kind,
                    **res,
                    "editorMode": edited.get("editorMode", ""),
                    "isLargeFile": bool(edited.get("isLargeFile", False)),
                    "openedExternalEditor": bool(edited.get("openedExternalEditor", False)),
                    "editorCommand": ed_cmd if edited.get("editorMode") == "external" else "",
                    "editorPopup": popup_meta,
                    "userMessageId": u_id,
                    "assistantMessageId": a_id,
                    "historySaved": saved,
                }

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

    except (ToolError, AgentPermissionError, ScreenContextError, CodeToolError, RuleStoreError, RunStoreError, McpError, RagStoreError, SkillError, DesktopControlError) as exc:
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
