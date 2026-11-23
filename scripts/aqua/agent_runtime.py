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
import concurrent.futures
import contextvars
from dataclasses import dataclass, field
import types
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
import unicodedata
import urllib.request

import code_tools
import context_policy
import spill_store
import thinking_router
from code_tools import (
    CodeToolError,
    build_repo_map,
    convert_or_create_document,
    inspect_file,
    query_lsp,
    read_file,
    retrieve_rag,
    run_project_tests,
    search_code,
)
import a2ui_builder
import a2ui_validator
import desktop_control
from desktop_control import DesktopControlError
import history_store
import mcp_client
from mcp_client import McpError
import memory_store
import domain_rag
import rag_store
from rag_store import RagStoreError
import skill_store
from skill_store import SkillError
import swarm_store
from swarm_store import SwarmStoreError
from permission_store import (
    AgentPermissionError,
    check_permission,
    create_pending_confirmation,
    grant_permission,
    inspect_permissions,
    is_forbidden_path,
    is_sensitive_path,
    is_symlink_raw,
    is_system_path,
    resolve_pending_confirmation,
    revoke_permission,
)
import pocket as pocket_store
from rule_store import (
    DEFAULT_RULES_TEXT,
    RuleStoreError,
    append_instruction_rule,
    create_or_update_rule,
    load_rules_state,
    remove_instruction_rule,
    set_rule_enabled,
    set_rules_text,
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
from screen_context import (
    ScreenContextError,
    capture_screenshot_file,
    format_semantic_snapshot,
    observe_active_window,
    perform_gui_action,
)
from tool_store import (
    ToolError,
    agent_file_op,
    confirm_edit,
    execute_system_sudo_op,
    propose_edit,
    read_spill_tool,
    resolve_candidate_path,
    rollback_file_receipt,
    search_files_in_home,
)
class _LazyModule:
    def __init__(self, module_name: str) -> None:
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_module", None)

    def _load(self):
        mod = object.__getattribute__(self, "_module")
        if mod is None:
            mod = importlib.import_module(object.__getattribute__(self, "_module_name"))
            object.__setattr__(self, "_module", mod)
        return mod

    def __getattr__(self, item: str):
        return getattr(self._load(), item)


web_tools = _LazyModule("web_tools")
import alis_local_bridge
import nexus_rag
from nexus_rag import NexusRagError


AGENT_CONTRACT_VERSION = 1

@dataclass
class _TurnState:
    active_run_steps: list[dict] = field(default_factory=list)
    active_run_start_ms: int = 0
    active_step_meta: dict[str, tuple[str, str]] = field(default_factory=dict)
    active_subagent_runs: list[dict] = field(default_factory=list)
    last_cancelled_stream_prefix: dict | None = None
    current_turn_live_snapshot: dict = field(default_factory=dict)
    current_turn_nexus_vault: str = ""
    current_turn_thread_id: str | None = None
    last_lmstudio_error: str = ""
    last_lmstudio_loaded_model: str = ""


_TURN_STATE_VAR: contextvars.ContextVar[_TurnState | None] = contextvars.ContextVar(
    "aqua_turn_state", default=None
)


def _tctx() -> _TurnState:
    st = _TURN_STATE_VAR.get()
    if st is None:
        st = _TurnState()
        _TURN_STATE_VAR.set(st)
    return st


_SUBAGENT_LOCK = threading.RLock()
_SUBAGENT_MAX_SUMMARY_CHARS = 1200


def get_last_cancelled_stream_prefix() -> dict | None:
    """Return a copy of the most recently finalized cancelled stream prefix, if any."""
    return dict(_tctx().last_cancelled_stream_prefix) if isinstance(_tctx().last_cancelled_stream_prefix, dict) else None

SUBAGENT_ROLES: dict[str, dict] = {
    "researcher": {
        "role": "researcher",
        "title": "Researcher",
        "subtitle": "Explorer / Researcher",
        "enable_thinking": False,
        "temperature": 0.2,
        "status_summary": "Gathering context...",
        "allowed_tools": (
            "code_search",
            "read_file",
            "read_spill",
            "domain_rag",
            "web_search",
            "system_status",
            "inspect_user_configuration",
            "file_inspect",
            "code_inspect",
            "rag_search",
            "web_fetch",
            "system_telemetry",
            "git_inspect",
            "alis_live_state",
            "nexus_knowledge",
        ),
        "system_prompt": (
            "You are Companion Subagent [Researcher] (Explorer / Researcher). "
            "Operate in an isolated context window using read/search/RAG/status tools only. "
            "Gather relevant facts, file paths, and configuration state, and return a compact summary (<= 1200 chars) without raw file dumps."
        ),
    },
    "planner": {
        "role": "planner",
        "title": "Planner",
        "subtitle": "Architect / Planner",
        "enable_thinking": True,
        "temperature": 0.5,
        "status_summary": "Planning execution steps...",
        "allowed_tools": (
            "code_search",
            "read_file",
            "read_spill",
            "domain_rag",
            "file_inspect",
            "code_inspect",
            "rag_search",
        ),
        "system_prompt": (
            "You are Companion Subagent [Planner] (Architect / Planner). "
            "Analyze the requirements and research findings in an isolated context window and produce a concise, ordered execution checklist (<= 1200 chars)."
        ),
    },
    "executor": {
        "role": "executor",
        "title": "Executor",
        "subtitle": "Action / Document / Code Worker",
        "enable_thinking": False,
        "temperature": 0.3,
        "status_summary": "Executing task actions...",
        "allowed_tools": (
            "edit_file",
            "write_file",
            "create_document",
            "convert_document",
            "run_command",
            "desktop_control",
            "file_inspect",
            "read_spill",
            "file_mutate",
            "code_inspect",
            "shell_run",
            "skill_run",
        ),
        "system_prompt": (
            "You are Companion Subagent [Executor] (Action / Document / Code Worker). "
            "Execute concrete file, code, document, command, or desktop actions cleanly and return a compact execution receipt (<= 1200 chars)."
        ),
    },
    "coder": {
        "role": "coder",
        "title": "Coder",
        "subtitle": "Action / Document / Code Worker",
        "enable_thinking": False,
        "temperature": 0.3,
        "status_summary": "Implementing code and document changes...",
        "allowed_tools": (
            "edit_file",
            "write_file",
            "create_document",
            "convert_document",
            "run_command",
            "desktop_control",
            "file_inspect",
            "read_spill",
            "file_mutate",
            "code_inspect",
            "shell_run",
            "skill_run",
        ),
        "system_prompt": (
            "You are Companion Subagent [Coder] (Action / Document / Code Worker). "
            "Implement surgical code or document changes in an isolated context window and return a compact summary (<= 1200 chars)."
        ),
    },
    "verifier": {
        "role": "verifier",
        "title": "Verifier",
        "subtitle": "QA / Verifier",
        "enable_thinking": False,
        "temperature": 0.2,
        "status_summary": "Cross-verifying output...",
        "allowed_tools": (
            "read_file",
            "read_spill",
            "code_search",
            "system_status",
            "inspect_user_configuration",
            "file_inspect",
            "code_inspect",
            "system_telemetry",
            "git_inspect",
        ),
        "system_prompt": (
            "You are Companion Subagent [Verifier] (QA / Verifier). "
            "Independently cross-verify outputs, file readbacks, syntax, security/permissions, and configuration state in an isolated context window and return a concise verification summary (<= 1200 chars)."
        ),
    },
}

SUBAGENT_EFFICIENCY_CRITERIA: tuple[str, ...] = (
    "cross_verification",
    "parallel_discovery",
    "privilege_isolation",
    "context_isolation",
    "cognitive_separation",
)

_PRIVILEGED_MUTATE_EXEC_TOOLS: frozenset[str] = frozenset({
    "file_mutate",
    "edit_file",
    "write_file",
    "create_document",
    "convert_document",
    "shell_run",
    "run_command",
    "exec",
    "desktop_control",
    "process_manage",
    "file_receipt_rollback",
})

_SUBAGENT_ROLE_ALIASES: dict[str, str] = {
    "explorer": "researcher",
    "research": "researcher",
    "investigator": "researcher",
    "architect": "planner",
    "plan": "planner",
    "action": "executor",
    "worker": "executor",
    "document": "executor",
    "code": "coder",
    "developer": "coder",
    "qa": "verifier",
    "test": "verifier",
    "validator": "verifier",
}


def _normalize_subagent_role(role: str) -> str:
    raw = str(role or "researcher").strip().lower()
    if raw in SUBAGENT_ROLES:
        return raw
    return _SUBAGENT_ROLE_ALIASES.get(raw, "researcher")


def _sanitize_english_status_text(summary: str, default_english: str = "Processing step...") -> str:
    """Ensure all Companion & Subagent status labels are strictly in English (never Vietnamese)."""
    text = re.sub(r"\s+", " ", str(summary or "").strip())
    if not text:
        return default_english
    # If any Vietnamese diacritics or common Vietnamese status phrases appear, map to clean English
    if re.search(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", text) or re.search(
        r"\b(?:dang\s+thu\s+thap|dang\s+phan\s+tich|dang\s+thuc\s+thi|dang\s+kiem\s+tra|dang\s+xu\s+ly|thu\s+thap\s+du\s+lieu|lap\s+ke\s+hoach)\b",
        text.lower(),
    ):
        return default_english
    return text


def _reset_activity_steps(now_ms: int | None = None) -> None:
    _tctx().active_run_steps = []
    _tctx().active_subagent_runs = []
    _tctx().active_run_start_ms = int(now_ms or int(time.time() * 1000))
    _tctx().active_step_meta.clear()


def _emit_subagent_progress(
    role: str,
    index: int = 1,
    total: int = 1,
    summary: str = "",
    status: str = "running",
) -> dict:
    """Emit strictly-English subagent progress via both __AQUA_PROGRESS__ and __AQUA_STEP__."""
    role_key = _normalize_subagent_role(role)
    role_spec = SUBAGENT_ROLES[role_key]
    role_title = str(role_spec["title"])
    idx = max(1, int(index or 1))
    tot = max(idx, int(total or 1))
    clean_summary = _sanitize_english_status_text(summary, str(role_spec["status_summary"]))
    step_label = f"🤖 Subagent [{role_title} {idx}/{tot}]: {clean_summary}"
    _emit_activity_step(step_label, kind=f"subagent_{role_key}_{idx}", status=status)
    progress_payload = {
        "phase": "subagent",
        "kind": "subagent",
        "subagentRole": role_key,
        "subagentTitle": role_title,
        "subagentIndex": idx,
        "subagentTotal": tot,
        "summary": clean_summary,
        "current": f"{step_label.rstrip(' ›')} ›",
        "status": status,
        "steps": [s["label"] for s in _tctx().active_run_steps[-10:]],
    }
    if (
        os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1"
        and not os.environ.get("AQUA_TEST_ENDPOINT")
        and not os.environ.get("AI_FIXTURE_URL")
    ):
        try:
            sys.stderr.write("__AQUA_PROGRESS__:" + json.dumps(progress_payload, ensure_ascii=False) + "\n")
            sys.stderr.flush()
        except Exception:
            pass
    return progress_payload


def _emit_activity_step(label: str, kind: str = "step", status: str = "running") -> None:
    text = _sanitize_english_status_text(str(label or "").strip(), "Processing step...")
    if not text:
        return
    if not text.endswith("›"):
        text = text.rstrip(".") + " ›"
    now = int(time.time() * 1000)
    elapsed_s = max(0.1, round((now - (_tctx().active_run_start_ms or now)) / 1000.0, 1))
    is_thought = text.startswith("Thought for ") or kind == "llm_think"
    eff_kind = "llm_think" if is_thought else kind
    entry = {"label": text, "kind": eff_kind, "status": status, "elapsedSec": elapsed_s}
    updated = False
    for idx in range(len(_tctx().active_run_steps) - 1, -1, -1):
        prev = _tctx().active_run_steps[idx]
        if prev.get("kind") == eff_kind or (is_thought and str(prev.get("label") or "").startswith("Thought for ")):
            _tctx().active_run_steps[idx] = entry
            updated = True
            break
    if not updated and (not _tctx().active_run_steps or _tctx().active_run_steps[-1].get("label") != text):
        _tctx().active_run_steps.append(entry)
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
                        "steps": [s["label"] for s in _tctx().active_run_steps[-10:]],
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
    _tctx().active_step_meta[str(step_id)] = (t_name, t_target)
    if t_name in ("web_search", "github_search", "wikipedia_search"):
        lbl = f'Searching web: "{t_target[:44]}" ›' if t_target else "Searching web ›"
    elif t_name == "fetch_url":
        lbl = f"Reading webpage: {web_tools.short_source_domain(t_target)} ›"
    elif t_name == "browser_open":
        lbl = f"Opening browser: {t_target[:40]} ›"
    elif t_name == "screen_observe":
        lbl = "Capturing active window (Niri+Vision) ›"
    elif t_name == "file_search":
        lbl = f"Searching for {Path(t_target).name if t_target else 'files'} ›"
    elif t_name == "file_inspect":
        lbl = f"Inspecting {Path(t_target).name if t_target else 'workspace'} ›"
    elif t_name in ("file_edit", "write_file"):
        lbl = f"Editing {Path(t_target).name if t_target else 'file'} ›"
    elif t_name in ("run_command", "exec"):
        lbl = f"Running command: {t_target[:42]} ›"
    elif t_name == "git_inspect":
        lbl = f"Checking Git in {Path(t_target).name if t_target else 'repository'} ›"
    elif t_name in ("llm_synthesize", "plan_generate"):
        lbl = f"Synthesizing response ({t_target[:32]}) ›" if t_target else "Synthesizing response & plan ›"
    else:
        lbl = f"Running {t_name}" + (f": {Path(t_target).name}" if t_target else "") + " ›"
    _emit_activity_step(lbl, kind=f"step_{step_id}", status="running")
    return step_id


def record_step_finish(step_id, status="completed", target_path="", receipt_id="", detail=None, **kwargs):
    eff_detail = dict(detail) if isinstance(detail, dict) else ({} if detail is None else {"raw": str(detail)})
    if kwargs.get("error_text") and "error" not in eff_detail:
        eff_detail["error"] = str(kwargs["error_text"])[:500]
    res = _store_record_step_finish(
        step_id, status=status, target_path=target_path, receipt_id=receipt_id, detail=eff_detail or None
    )
    t_name, t_target = _tctx().active_step_meta.get(str(step_id), ("tool", str(target_path or "")))
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
    elif t_name == "file_search":
        cnt = det.get("count")
        cnt_str = f" ({cnt} found)" if cnt is not None else ""
        lbl = f"Searched for {Path(t_target).name if t_target else 'files'}{cnt_str} ›"
    elif t_name == "file_inspect":
        lbl = f"Inspected {Path(t_target).name if t_target else 'workspace'} ›"
    elif t_name in ("file_edit", "write_file"):
        lbl = f"Edited {Path(t_target).name if t_target else 'file'} ›"
    elif t_name in ("run_command", "exec"):
        lbl = f"Ran command: {t_target[:42]} ›"
    elif t_name == "git_inspect":
        lbl = f"Checked Git in {Path(t_target).name if t_target else 'repository'} ›"
    elif t_name in ("llm_synthesize", "plan_generate"):
        lbl = f"Synthesized response ({t_target[:32]}) ›" if t_target else "Synthesized response & plan ›"
    else:
        lbl = f"Ran {t_name}" + (f": {Path(t_target).name}" if t_target else "") + " ›"
    _emit_activity_step(lbl, kind=f"step_{step_id}", status=status)
    return res




def _get_system_and_gpu_memory_mb() -> tuple[int, int]:
    """Return (mem_available_mb, amdgpu_gtt_free_mb) from /proc/meminfo and /sys/class/drm."""
    mem_avail_mb = 16384
    gtt_free_mb = 16384
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        for line in meminfo.splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    mem_avail_mb = int(parts[1]) // 1024
                break
    except OSError:
        pass
    try:
        drm_root = Path("/sys/class/drm")
        if drm_root.is_dir():
            for card_dir in drm_root.iterdir():
                if not card_dir.name.startswith("card") or "-" in card_dir.name:
                    continue
                dev_dir = card_dir / "device"
                gtt_tot_f = dev_dir / "mem_info_gtt_total"
                gtt_used_f = dev_dir / "mem_info_gtt_used"
                if gtt_tot_f.is_file() and gtt_used_f.is_file():
                    gtt_tot = int(gtt_tot_f.read_text(encoding="utf-8").strip() or "0")
                    gtt_used = int(gtt_used_f.read_text(encoding="utf-8").strip() or "0")
                    if gtt_tot > 0:
                        gtt_free_mb = max(0, (gtt_tot - gtt_used) // (1024 * 1024))
                        break
    except (OSError, ValueError):
        pass
    return mem_avail_mb, gtt_free_mb


def _find_lmstudio_worker_pids() -> tuple[list[int], list[int]]:
    """Return (llama_server_pids, gdb_pids) owned by current UID."""
    llama_pids: list[int] = []
    gdb_pids: list[int] = []
    uid = os.getuid()
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return llama_pids, gdb_pids
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
            if "llama" in comm:
                llama_pids.append(int(entry.name))
            elif comm == "gdb":
                cmdline = (entry / "cmdline").read_bytes().decode("utf-8", errors="replace").lower()
                if "llama" in cmdline or "lmstudio" in cmdline:
                    gdb_pids.append(int(entry.name))
        except OSError:
            continue
    return llama_pids, gdb_pids


def _kill_runaway_lmstudio_workers(only_gdb: bool = False) -> None:
    """Kill gdb backtrace processes (and optionally runaway llama_server processes) to immediately free GTT/RAM."""
    import signal
    llama_pids, gdb_pids = _find_lmstudio_worker_pids()
    targets = gdb_pids if only_gdb else (gdb_pids + llama_pids)
    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _check_lmstudio_log_recent_fit_or_oom_error(since_ts: float = 0.0) -> str:
    """Inspect the newest LM Studio server log tail for GPU fit/OOM errors since the last successful load."""
    try:
        logs_root = Path.home() / ".lmstudio" / "server-logs"
        if not logs_root.is_dir():
            return ""
        newest_log: Path | None = None
        newest_mtime = 0.0
        for sub in logs_root.iterdir():
            if not sub.is_dir():
                continue
            for lf in sub.glob("*.log"):
                try:
                    mt = lf.stat().st_mtime
                    if mt > newest_mtime:
                        newest_mtime = mt
                        newest_log = lf
                except OSError:
                    continue
        now_s = time.time()
        if newest_log is None or newest_mtime < max(since_ts, now_s - 45.0):
            return ""
        with newest_log.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fsize = fh.tell()
            fh.seek(max(0, fsize - 24576), os.SEEK_SET)
            tail = fh.read().decode("utf-8", errors="replace")
        last_loaded_idx = tail.rfind("llama_server: model loaded")
        active_slice = tail[last_loaded_idx:] if last_loaded_idx >= 0 else tail
        if since_ts > 0.0:
            kept_lines: list[str] = []
            has_any_ts = bool(re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", active_slice))
            keep_current = not has_any_ts
            for line in active_slice.splitlines():
                m_ts = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
                if m_ts:
                    try:
                        line_ts = time.mktime(time.strptime(m_ts.group(1), "%Y-%m-%d %H:%M:%S"))
                        keep_current = line_ts >= (since_ts - 2.0)
                    except ValueError:
                        keep_current = True
                if keep_current:
                    kept_lines.append(line)
            active_slice = "\n".join(kept_lines)
        if "common_fit_params: failed to fit params to free device memory" in active_slice:
            return "gpu_fit_failed"
        if (
            "Not enough memory for command submission" in active_slice
            or "DeviceLostError" in active_slice
            or "failed to allocate buffer" in active_slice
        ):
            return "gpu_oom"
        if "Model load request cancelled by client disconnect" in active_slice and newest_mtime >= now_s - 15.0:
            return "load_cancelled"
    except OSError:
        pass
    return ""


def _start_lmstudio_gtt_watchdog():
    """Start a background daemon thread that kills runaway llama_server before amdgpu GTT hits 0 and crashes niri."""
    import threading
    stop_event = threading.Event()
    start_ts = time.time()
    # Capture caller turn's ContextVar state so background OOM detection propagates to the active turn.
    turn_state = _tctx()

    def _watch():
        while not stop_event.wait(0.15):
            _kill_runaway_lmstudio_workers(only_gdb=True)
            mem_avail_mb, gtt_free_mb = _get_system_and_gpu_memory_mb()
            log_err = _check_lmstudio_log_recent_fit_or_oom_error(since_ts=start_ts - 1.0)
            if (
                gtt_free_mb < 450
                or mem_avail_mb < 512
                or log_err == "gpu_oom"
                or (log_err == "gpu_fit_failed" and gtt_free_mb < 900)
            ):
                turn_state.last_lmstudio_error = "gpu_oom"
                _kill_runaway_lmstudio_workers(only_gdb=False)
                break

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    return stop_event


def _lmstudio_error_user_message(lang: str = "en") -> str:
    if lang == "vi":
        return (
            "⚠️ **Đã bảo vệ phiên màn hình (Niri):** LM Studio đang nạp lại model hoặc thiết lập GPU Offload / Context Length "
            "vượt quá giới hạn bộ nhớ GPU tích hợp (AMD GTT 15 GB). Companion đã tự động chặn/dừng yêu cầu nạp trùng lặp để tránh tràn VRAM làm sập phiên đăng nhập. "
            "Bạn hãy giảm **GPU Offload** (hoặc nạp sẵn model trong LM Studio) rồi gửi lại tin nhắn nhé!"
        )
    return (
        "⚠️ **Desktop Session Protected (Niri):** LM Studio is currently reloading the model or its GPU Offload / Context Length "
        "settings exceed safe integrated GPU memory (15 GB AMD GTT limit). Companion stopped the load request to prevent an `amdgpu` out-of-memory session crash. "
        "Please lower **GPU Offload** in LM Studio (or wait for the model to finish loading) and try again."
    )


def _auto_load_lmstudio_model_safe(
    clean_mdl: str,
    gtt_free_mb: int,
    force_cap_ratio: float | None = None,
) -> bool:
    """Safely load an unloaded LM Studio model via lms CLI with bounded context_length and automatic GPU offload capping if needed."""
    import shutil
    import subprocess
    if not clean_mdl:
        return True
    if not (Path.home() / ".lmstudio" / "models").is_dir():
        return True
    lms_bin = shutil.which("lms") or str(Path.home() / ".lmstudio" / "bin" / "lms")
    if not Path(lms_bin).is_file():
        return True
    safe_gtt_gib = max(2.0, (gtt_free_mb - 3500) / 1024.0)
    load_cmd = [lms_bin, "load", clean_mdl, "-c", "8192"]
    if force_cap_ratio is not None:
        load_cmd.extend(["--gpu", str(round(max(0.15, min(0.85, force_cap_ratio)), 2))])
    else:
        m_gpu = None
        try:
            est_proc = subprocess.run(
                [lms_bin, "load", clean_mdl, "-c", "8192", "--estimate-only"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            est_out = f"{est_proc.stdout or ''}\n{est_proc.stderr or ''}"
            m_gpu = re.search(r"Estimated GPU Memory:\s*([0-9.]+)\s*GiB", est_out, re.IGNORECASE)
        except Exception:
            m_gpu = None
        if m_gpu:
            est_gpu_gib = float(m_gpu.group(1))
            if est_gpu_gib > safe_gtt_gib and est_gpu_gib > 0:
                ratio = max(0.15, min(0.85, round((safe_gtt_gib / est_gpu_gib) - 0.03, 2)))
                load_cmd.extend(["--gpu", str(ratio)])
        elif safe_gtt_gib < 14.0 and any(k in clean_mdl.lower() for k in ("35b", "32b", "30b", "27b", "70b")):
            load_cmd.extend(["--gpu", "0.5"])
    load_cmd.append("-y")
    try:
        _emit_activity_step(
            f"Loading model in LM Studio ({clean_mdl[:24]}) ›",
            kind="lms_load",
            status="running",
        )
        wd_stop = _start_lmstudio_gtt_watchdog()
        try:
            load_proc = subprocess.run(
                load_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=110,
                check=False,
            )
        finally:
            wd_stop.set()
        if _tctx().last_lmstudio_error:
            return False
        if load_proc.returncode == 0:
            _tctx().last_lmstudio_loaded_model = clean_mdl
            _emit_activity_step(
                f"Loaded model in LM Studio ({clean_mdl[:24]}) ›",
                kind="lms_load",
                status="completed",
            )
            return True
    except Exception:
        if _tctx().last_lmstudio_error:
            return False
    return True


def _ensure_lmstudio_server_listening() -> None:
    if not (Path.home() / ".lmstudio" / "models").is_dir():
        return
    import shutil
    import socket
    import subprocess
    try:
        with socket.create_connection(("127.0.0.1", 1234), timeout=0.45):
            return
    except OSError:
        lms_bin = shutil.which("lms") or str(Path.home() / ".lmstudio" / "bin" / "lms")
        if Path(lms_bin).is_file():
            try:
                subprocess.run(
                    [lms_bin, "server", "start"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=14,
                    check=False,
                )
            except Exception:
                pass


def _ensure_lmstudio_runtime_ready(
    endpoint: str = "",
    model_name: str = "",
    v0_url: str = "http://127.0.0.1:1234/api/v0/models",
) -> bool:
    _tctx().last_lmstudio_loaded_model = ""
    try:
        lms_root = Path.home() / ".lmstudio"
        if lms_root.is_dir():
            (lms_root / ".internal" / "temp").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if (
        (
            not endpoint
            or ":1234" not in endpoint
            or os.environ.get("ALIS_TEST_ISOLATED")
            or os.environ.get("AQUA_TEST_ENDPOINT")
            or os.environ.get("AI_FIXTURE_URL")
            or not (Path.home() / ".lmstudio" / "models").is_dir()
        )
        and v0_url == "http://127.0.0.1:1234/api/v0/models"
    ):
        return True
    if _tctx().last_lmstudio_error in ("gpu_oom", "gpu_fit_failed"):
        return False
    _kill_runaway_lmstudio_workers(only_gdb=True)
    clean_mdl = re.sub(r"^(?:lmstudio-|ollama-|gguf:)", "", str(model_name or "").strip())
    if v0_url == "http://127.0.0.1:1234/api/v0/models":
        _ensure_lmstudio_server_listening()
    try:
        t_wait_start = time.time()
        all_ids: list[str] = []
        any_embed_loaded = False
        stopped_runaway_load = False
        _local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            with _local_opener.open(v0_url, timeout=1.5) as r_v0:
                v0_data = json.loads(r_v0.read().decode("utf-8", errors="replace"))
            all_v0_items = [
                it for it in (v0_data.get("data") if isinstance(v0_data, dict) else [])
                if isinstance(it, dict)
            ]
            any_embed_loaded = any(
                str(it.get("type") or "") in ("embeddings", "embedding")
                and str(it.get("state") or "").strip().lower() in ("loaded", "loading")
                for it in all_v0_items
            )
            items = [
                it for it in all_v0_items
                if str(it.get("type") or "") not in ("embeddings", "embedding")
            ]
            all_ids = [str(it.get("id") or "").strip() for it in items if str(it.get("id") or "").strip()]
            loading_items = [it for it in items if str(it.get("state") or "").strip().lower() == "loading"]
            loaded_items = [it for it in items if str(it.get("state") or "").strip().lower() == "loaded"]
            mem_avail_mb, gtt_free_mb = _get_system_and_gpu_memory_mb()
            if loading_items:
                log_err = _check_lmstudio_log_recent_fit_or_oom_error(since_ts=t_wait_start - 2.0)
                if (
                    gtt_free_mb < 450
                    or mem_avail_mb < 512
                    or log_err == "gpu_oom"
                    or (log_err == "gpu_fit_failed" and gtt_free_mb < 900)
                ):
                    _kill_runaway_lmstudio_workers(only_gdb=False)
                    stopped_runaway_load = True
                    break
                if time.time() - t_wait_start > 110.0:
                    _tctx().last_lmstudio_error = "load_timeout"
                    return False
                time.sleep(0.4)
                continue
            # Never unload or override user settings when ANY LLM/VLM model is already loaded!
            if loaded_items:
                if gtt_free_mb < 250:
                    _tctx().last_lmstudio_error = "gpu_oom"
                    return False
                loaded_ids = [str(it.get("id") or "").strip() for it in loaded_items if str(it.get("id") or "").strip()]
                if loaded_ids:
                    _tctx().last_lmstudio_loaded_model = clean_mdl if clean_mdl in loaded_ids else loaded_ids[0]
                return True
            break

        if all_ids and clean_mdl and clean_mdl not in all_ids:
            _tctx().last_lmstudio_loaded_model = all_ids[0]
            clean_mdl = all_ids[0]
        elif all_ids and not clean_mdl:
            _tctx().last_lmstudio_loaded_model = all_ids[0]
            clean_mdl = all_ids[0]

        # All LLM/VLM models are currently 'not-loaded'.
        # Wait if a previous llama_server is still tearing down in /proc and holding GTT/RAM.
        if not any_embed_loaded:
            t_teardown = time.time()
            while time.time() - t_teardown < 15.0:
                llama_pids, gdb_pids = _find_lmstudio_worker_pids()
                if gdb_pids:
                    _kill_runaway_lmstudio_workers(only_gdb=True)
                if not llama_pids:
                    break
                time.sleep(0.4)
            else:
                _kill_runaway_lmstudio_workers(only_gdb=False)

        mem_avail_mb, gtt_free_mb = _get_system_and_gpu_memory_mb()
        if gtt_free_mb < 1024 or mem_avail_mb < 1500:
            _tctx().last_lmstudio_error = "gpu_oom"
            return False

        if v0_url == "http://127.0.0.1:1234/api/v0/models" and clean_mdl:
            return _auto_load_lmstudio_model_safe(
                clean_mdl,
                gtt_free_mb,
                force_cap_ratio=0.45 if stopped_runaway_load else None,
            )
    except Exception:
        pass
    return True


def _urlopen_local(url_or_req, timeout: float = 5.0):
    target_url = url_or_req.full_url if hasattr(url_or_req, "full_url") else str(url_or_req or "")
    host = (urllib.parse.urlsplit(target_url).hostname or "").strip().lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(url_or_req, timeout=timeout)
    return urllib.request.urlopen(url_or_req, timeout=timeout)


def _resolve_live_llm(options: dict) -> tuple[str, str]:
    _ensure_lmstudio_runtime_ready()
    llm_ep = str(options.get("endpoint") or os.environ.get("AQUA_LLM_ENDPOINT") or "").strip()
    llm_model = str(options.get("model") or os.environ.get("AQUA_LLM_MODEL") or "").strip()
    is_isolated_test = bool(os.environ.get("ALIS_TEST_ISOLATED")) and not bool(options.get("endpoint"))
    is_fixture = bool(os.environ.get("AQUA_TEST_ENDPOINT") or os.environ.get("AI_FIXTURE_URL"))
    need_probe = (
        not llm_ep
        or "/v1/" not in llm_ep
        or ":8080" in llm_ep
        or not llm_model
        or llm_model.startswith("gguf:")
        or llm_model.lower().endswith(".gguf")
    )
    if not is_isolated_test and not is_fixture and (need_probe or ":1234" in llm_ep) and (
        str(options.get("runSource") or "interactive") == "interactive"
        or options.get("synthesizeWithLlm") is True
        or options.get("allowLiveProbe") is True
    ):
        _ensure_lmstudio_server_listening()
        try:
            with _urlopen_local("http://127.0.0.1:1234/api/v0/models", timeout=1.2) as v0_resp:
                v0_data = json.loads(v0_resp.read().decode("utf-8", errors="replace"))
            v0_items = [
                it for it in (v0_data.get("data") if isinstance(v0_data, dict) else [])
                if isinstance(it, dict)
                and str(it.get("id") or "").strip()
                and str(it.get("type") or "") not in ("embeddings", "embedding")
                and "embed" not in str(it.get("id") or "").lower()
            ]
            if v0_items:
                llm_ep = "http://127.0.0.1:1234/v1/chat/completions"
                clean_cand = re.sub(r"^(?:lmstudio-|ollama-|gguf:)", "", llm_model)
                loaded_ids = [str(it["id"]).strip() for it in v0_items if str(it.get("state") or "") in ("loaded", "loading")]
                all_ids = [str(it["id"]).strip() for it in v0_items]
                if loaded_ids and clean_cand not in loaded_ids:
                    # Prefer the model already loaded in LM Studio to avoid loading a duplicate 20GB model!
                    llm_model = loaded_ids[0]
                elif clean_cand in all_ids:
                    llm_model = clean_cand
                else:
                    llm_model = loaded_ids[0] if loaded_ids else all_ids[0]
        except Exception:
            if need_probe:
                try:
                    with _urlopen_local("http://127.0.0.1:1234/v1/models", timeout=1.2) as m_resp:
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


def _call_chat_completion_text(
    endpoint: str,
    payload: dict,
    chunk_timeout: float = 125.0,
    max_wall_sec: float = 195.0,
    stream_stdout: bool | None = None,
    cancel_event: threading.Event | None = None,
    session_id: str = "",
    prompt_for_cancel: str = "",
    persist_cancelled: bool = False,
) -> str:
    """Call an OpenAI-compatible chat completions endpoint using SSE streaming (with JSON fallback) so multi-paragraph responses never hit socket header timeouts."""
    _tctx().last_cancelled_stream_prefix = None
    if not _ensure_lmstudio_runtime_ready(endpoint, str(payload.get("model") or "")):
        return ""
    should_stream_stdout = (
        (not bool(getattr(threading.current_thread(), "_aqua_json_rpc_stdout", getattr(sys, "_aqua_json_rpc_stdout", False))))
        if stream_stdout is None
        else bool(stream_stdout)
    )
    is_live_lms = (
        ":1234" in str(endpoint or "")
        and not os.environ.get("ALIS_TEST_ISOLATED")
        and not os.environ.get("AQUA_TEST_ENDPOINT")
        and not os.environ.get("AI_FIXTURE_URL")
    )
    req_body = dict(payload)
    if _tctx().last_lmstudio_loaded_model:
        req_body["model"] = _tctx().last_lmstudio_loaded_model
    if isinstance(req_body.get("messages"), list):
        req_body["messages"], _ = context_policy.compact_messages_for_route(
            req_body["messages"],
            provider=str(req_body.pop("companionProvider", "") or ""),
            model=str(req_body.get("model") or ""),
            endpoint=endpoint,
            reserved_output=int(req_body.get("max_tokens") or 1024),
            context_window=req_body.pop("companionContextWindow", None),
            tools=req_body.get("tools") if isinstance(req_body.get("tools"), list) else None,
        )
    req_body["stream"] = True
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(req_body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    hit_limit = False
    was_cancelled = False
    sig_cancelled = False
    prev_sigterm = None
    prev_sigint = None
    on_main_thread = threading.current_thread() is threading.main_thread()

    if on_main_thread:
        def _on_cancel_signal(_signum, _frame):
            nonlocal sig_cancelled
            sig_cancelled = True

        try:
            prev_sigterm = signal.getsignal(signal.SIGTERM)
            prev_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGTERM, _on_cancel_signal)
            signal.signal(signal.SIGINT, _on_cancel_signal)
        except Exception:
            prev_sigterm = None
            prev_sigint = None

    def _is_cancelled() -> bool:
        thread_cancel = getattr(threading.current_thread(), "_aqua_cancel_event", None)
        return sig_cancelled or (cancel_event is not None and cancel_event.is_set()) or (thread_cancel is not None and thread_cancel.is_set())

    watchdog_stop = _start_lmstudio_gtt_watchdog() if is_live_lms else None
    try:
        with _urlopen_local(req, timeout=chunk_timeout) as resp:
            first_raw = resp.readline()
            if not first_raw:
                return ""
            first_str = first_raw.decode("utf-8", errors="replace").strip()
            # Fallback for non-streaming mock HTTP servers that return a plain JSON object
            if first_str.startswith("{"):
                rest_raw = resp.read()
                full_str = first_raw.decode("utf-8", errors="replace") + rest_raw.decode("utf-8", errors="replace")
                data = json.loads(full_str)
                choices = data.get("choices") or []
                if choices and isinstance(choices[0], dict):
                    msg_obj = choices[0].get("message") or choices[0].get("delta") or {}
                    text = str(msg_obj.get("content") or "").strip()
                    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
                    text = re.sub(r"^\{REASON:[^}]+\}\s*", "", text, flags=re.IGNORECASE).strip()
                    if text:
                        if stream_stdout is True:
                            try:
                                sys.stdout.write(json.dumps({"streamDelta": text}, ensure_ascii=False) + "\n")
                                sys.stdout.flush()
                            except Exception:
                                pass
                        if (
                            os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1"
                            and not os.environ.get("AQUA_TEST_ENDPOINT")
                            and not os.environ.get("AI_FIXTURE_URL")
                        ):
                            try:
                                sys.stderr.write(
                                    "__AQUA_STEP__:"
                                    + json.dumps(
                                        {"streamDelta": text, "kind": "stream_delta", "status": "running"},
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                                sys.stderr.flush()
                            except Exception:
                                pass
                    return text
                return ""

            def _process_sse_line(line_s: str) -> bool:
                nonlocal hit_limit, was_cancelled
                if _is_cancelled():
                    was_cancelled = True
                    return False
                if not line_s or line_s.startswith(":"):
                    return True
                if line_s.startswith("data:"):
                    line_s = line_s[5:].strip()
                if line_s == "[DONE]":
                    return False
                if not line_s.startswith("{"):
                    return True
                try:
                    chunk = json.loads(line_s)
                    ch_list = chunk.get("choices") or []
                    if ch_list and isinstance(ch_list[0], dict):
                        delta = ch_list[0].get("delta") or ch_list[0].get("message") or {}
                        c_piece = delta.get("content")
                        r_piece = delta.get("reasoning_content") or delta.get("reasoning")
                        # Explicitly ignore partial tool_calls on stream chunks; cancelled streams never persist tool_calls.
                        if c_piece:
                            c_str = str(c_piece)
                            content_parts.append(c_str)
                            if should_stream_stdout:
                                try:
                                    sys.stdout.write(json.dumps({"streamDelta": c_str}, ensure_ascii=False) + "\n")
                                    sys.stdout.flush()
                                except Exception:
                                    pass
                            if (
                                os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1"
                                and not os.environ.get("AQUA_TEST_ENDPOINT")
                                and not os.environ.get("AI_FIXTURE_URL")
                            ):
                                try:
                                    sys.stderr.write(
                                        "__AQUA_STEP__:"
                                        + json.dumps(
                                            {"streamDelta": c_str, "kind": "stream_delta", "status": "running"},
                                            ensure_ascii=False,
                                        )
                                        + "\n"
                                    )
                                    sys.stderr.flush()
                                except Exception:
                                    pass
                        if r_piece:
                            reasoning_parts.append(str(r_piece))
                        fr = ch_list[0].get("finish_reason")
                        if fr in ("cancelled", "interrupted"):
                            was_cancelled = True
                            return False
                        if fr == "length":
                            hit_limit = True
                            return False
                        if fr == "stop":
                            return False
                except Exception:
                    pass
                return True

            if _process_sse_line(first_str):
                for raw_line in resp:
                    if _is_cancelled():
                        was_cancelled = True
                        break
                    if time.time() - t0 > max_wall_sec and len("".join(content_parts)) >= 240:
                        hit_limit = True
                        break
                    s_line = raw_line.decode("utf-8", errors="replace").strip()
                    if not _process_sse_line(s_line):
                        break
    except KeyboardInterrupt:
        was_cancelled = True
    except Exception:
        if _is_cancelled():
            was_cancelled = True
        else:
            if is_live_lms and not _tctx().last_lmstudio_error:
                log_err = _check_lmstudio_log_recent_fit_or_oom_error(since_ts=t0 - 1.0)
                if log_err == "gpu_oom":
                    _tctx().last_lmstudio_error = log_err
            if not content_parts and not reasoning_parts:
                return ""
    finally:
        if watchdog_stop is not None:
            watchdog_stop.set()
        if on_main_thread:
            try:
                if prev_sigterm is not None:
                    signal.signal(signal.SIGTERM, prev_sigterm)
                if prev_sigint is not None:
                    signal.signal(signal.SIGINT, prev_sigint)
            except Exception:
                pass

    if _is_cancelled():
        was_cancelled = True

    if was_cancelled:
        partial_raw_content = "".join(content_parts)
        inline_think_matches = re.findall(r"<think(?:ing)?>([\s\S]*?)(?:</think(?:ing)?>|$)", partial_raw_content, flags=re.IGNORECASE)
        cleaned_partial = re.sub(r"<think(?:ing)?>[\s\S]*?(?:</think(?:ing)?>|$)", "", partial_raw_content, flags=re.IGNORECASE).strip()
        cleaned_partial = re.sub(r"^\{REASON:[^}]+\}\s*", "", cleaned_partial, flags=re.IGNORECASE).strip()
        partial_reasoning = "".join(reasoning_parts).strip()
        if not partial_reasoning and inline_think_matches:
            partial_reasoning = "\n".join(m.strip() for m in inline_think_matches if m.strip()).strip()
        _tctx().last_cancelled_stream_prefix = {
            "interrupted": True,
            "stopReason": "cancelled",
            "content": cleaned_partial,
            "reasoning": partial_reasoning,
        }
        cancel_evt = {
            "streamCancelled": True,
            "interrupted": True,
            "stopReason": "cancelled",
            "partialContent": cleaned_partial,
            "partialReasoning": partial_reasoning,
        }
        if should_stream_stdout:
            try:
                sys.stdout.write(json.dumps(cancel_evt, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except Exception:
                pass
        if (
            os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1"
            and not os.environ.get("AQUA_TEST_ENDPOINT")
            and not os.environ.get("AI_FIXTURE_URL")
        ):
            try:
                sys.stderr.write("__AQUA_STEP__:" + json.dumps({**cancel_evt, "kind": "stream_cancelled", "status": "cancelled"}, ensure_ascii=False) + "\n")
                sys.stderr.flush()
            except Exception:
                pass
        if persist_cancelled and (cleaned_partial or partial_reasoning):
            try:
                eff_sid = session_id or str(_tctx().current_turn_thread_id or "") or None
                history_store.append_cancelled_prefix(
                    prompt_for_cancel,
                    cleaned_partial,
                    partial_reasoning,
                    model=str(req_body.get("model") or ""),
                    thread_id=eff_sid,
                )
            except Exception:
                pass
            try:
                cancel_active_runs(
                    "cancelled_by_signal",
                    partial_content=cleaned_partial,
                    partial_reasoning=partial_reasoning,
                )
            except Exception:
                pass
        return cleaned_partial

    text = "".join(content_parts).strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^\{REASON:[^}]+\}\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\n+(?:1[.)][ \t]+[^\n]*\n+)?[ \t]*\d+[.)][ \t]*$", "", text).strip()
    if hit_limit and text and not re.search(r"[.!?)\]\"'`*]$", text):
        lines = text.splitlines()
        while len(lines) > 3 and (
            not re.search(r"[.!?)\]\"'`*]$", lines[-1].strip())
            or re.match(r"^#{1,6}\s+", lines[-1].strip())
        ):
            lines.pop()
        text = "\n".join(lines).rstrip()
    return text


def _fetch_top_search_pages(
    run_id: str,
    results: list[dict],
    steps: list[dict],
    max_pages: int = 2,
    max_chars_per_page: int = 1800,
    timeout_sec: float = 4.0,
    now_ms: int | None = None,
) -> tuple[list[dict], str]:
    """Fetch and extract readable content from the top distinct search result URLs, recording fetch_url steps."""
    candidates: list[dict] = []
    seen_domains: set[str] = set()
    overflow: list[dict] = []
    for r in results or []:
        u = str(r.get("url") or "").strip()
        if not u.startswith(("http://", "https://")):
            continue
        dom = web_tools.short_source_domain(u)
        if dom not in seen_domains:
            seen_domains.add(dom)
            candidates.append(r)
        else:
            overflow.append(r)
    ordered = candidates + overflow

    fetched_pages: list[dict] = []
    blocks: list[str] = []
    sparse_pages: list[tuple[dict, str]] = []
    tried = 0
    max_tries = max(max_pages, min(len(ordered), max_pages + 3))
    for r in ordered:
        if len(fetched_pages) >= max_pages or tried >= max_tries:
            break
        url = str(r.get("url") or "").strip()
        tried += 1
        seq = len(steps) + 1
        f_step = record_step_start(
            run_id,
            seq,
            "fetch_url",
            side_effect=False,
            target_path=url,
            detail={"url": url},
            now_ms=now_ms,
        )
        try:
            page = web_tools.fetch_url_excerpt(url, max_chars=max_chars_per_page, timeout_sec=timeout_sec)
            p_title = str(page.get("title") or r.get("title") or url).strip()
            p_headings = [str(h).strip() for h in (page.get("headings") or [])[:8] if str(h).strip()]
            p_lines = [str(ln).strip() for ln in (page.get("lines") or [])[:12] if str(ln).strip()]
            record_step_finish(
                f_step,
                status="completed",
                target_path=url,
                detail={"title": p_title[:120], "headings": len(p_headings), "lines": len(p_lines)},
            )
            steps.append({"stepId": f_step, "tool": "fetch_url", "status": "completed", "targetPath": url})
            if p_lines or p_headings:
                dom = web_tools.short_source_domain(url)
                page_entry = {
                    "url": url,
                    "domain": dom,
                    "title": p_title,
                    "headings": p_headings,
                    "lines": p_lines,
                }
                sec_lines = [f"### Webpage: {p_title} ({dom} — {url})"]
                if p_headings:
                    sec_lines.append("Key Headings: " + " | ".join(p_headings[:6]))
                for ln in p_lines[:10]:
                    sec_lines.append(f"- {ln[:220]}")
                sec_block = "\n".join(sec_lines)
                # Require actual body lines before counting toward max_pages so JS-only shell pages don't block reading the next result
                if len(p_lines) >= 1 and (len(p_lines) >= 2 or sum(len(x) for x in p_lines) >= 40):
                    fetched_pages.append(page_entry)
                    blocks.append(sec_block)
                else:
                    sparse_pages.append((page_entry, sec_block))
        except Exception as exc:
            record_step_finish(
                f_step,
                status="completed",
                target_path=url,
                detail={"fallback": "snippet_only", "error": str(exc)[:100]},
            )
            steps.append({"stepId": f_step, "tool": "fetch_url", "status": "completed", "targetPath": url})
    if not fetched_pages and sparse_pages:
        for pe, sb in sparse_pages[:max_pages]:
            fetched_pages.append(pe)
            blocks.append(sb)
    return fetched_pages, "\n\n".join(blocks)


def _extract_plan_search_query(goal_text: str, lang: str = "vi") -> str:
    q = web_tools.clean_search_query(goal_text, for_plan=True)
    # Strip leading filler and time markers that weaken search relevance in Vietnamese
    q = re.sub(r"^(?:cho\s+|về\s+|ve\s+|for\s+|about\s+)+", "", q, flags=re.IGNORECASE)
    q = re.sub(
        r"\b(?:vào\s+ngày\s+mai|ngày\s+mai|tối\s+mai|sáng\s+mai|chiều\s+mai|trưa\s+mai|tomorrow(?:\s+(?:morning|afternoon|evening|night))?|vào\s+lúc\s+\d+\s*(?:h|giờ|:)\s*\d*(?:\s*(?:sáng|trưa|chiều|tối))?|lúc\s+\d+\s*(?:h|giờ|:)\s*\d*(?:\s*(?:sáng|trưa|chiều|tối))?|at\s+\d+\s*(?:am|pm|:\d+)?)\b",
        " ",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(r"\b(?:1\s+|một\s+|mot\s+)?(?:buổi|chuyến)\s+(?=dã\s+ngoại|da\s+ngoai|picnic|du\s+lịch|du\s+lich)", " ", q, flags=re.IGNORECASE)
    q = re.sub(r"^(?:1|một|mot|a|an)\s+(?=buổi\s+tiệc|buoi\s+tiec|tiệc|tiec|bữa\s+tiệc|bua\s+tiec|party)", "", q, flags=re.IGNORECASE)
    q = re.sub(r"[,;]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if lang == "vi" and re.search(r"\b(?:dã ngoại|da ngoai|picnic|du lịch|du lich)\b", q, flags=re.IGNORECASE):
        if not re.search(r"\b(?:địa điểm|dia diem|kinh nghiệm|kinh nghiem)\b", q, flags=re.IGNORECASE):
            q = "địa điểm " + q
    return q[:120] or str(goal_text or "").strip()[:120]

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
            "description": "Control Niri windows, columns, workspaces, applications, audio volume, brightness, media playback, clipboard, notifications, or voice STT/TTS.",
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
                            "move_window_to_workspace",
                            "consume_or_expel_window",
                            "consume_window_into_column",
                            "expel_window_from_column",
                            "set_column_width",
                            "set_window_height",
                            "fullscreen_window",
                            "toggle_window_floating",
                            "center_column",
                            "maximize_column",
                            "expand_column_to_available_width",
                            "focus_column_left",
                            "focus_column_right",
                            "move_column_left",
                            "move_column_right",
                            "launch_app",
                            "volume",
                            "brightness",
                            "media",
                            "clipboard_get",
                            "clipboard_set",
                            "primary_selection_get",
                            "notify",
                            "voice_stt",
                            "voice_tts",
                            "voice_tts_stop",
                            "voice_status",
                            "gui_click",
                            "gui_fill",
                            "gui_focus",
                            "atspi_action",
                            "som_click",
                            "click",
                            "fill",
                            "set_text",
                            "focus_element",
                        ],
                    },
                    "action_type": {"type": "string"},
                    "target": {"type": "string"},
                    "element": {"type": "string"},
                    "value": {"type": "string"},
                    "text": {"type": "string"},
                    "ref": {"type": "string", "description": "Semantic AT-SPI element reference (e.g. @e1, @e2)"},
                    "markId": {"type": "integer"},
                    "role": {"type": "string"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_receipt_rollback",
            "description": "Roll back a file edit/create/delete operation using its receiptId.",
            "parameters": {
                "type": "object",
                "properties": {
                    "receiptId": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "voice_tts_stop",
            "description": "Stop any active Companion TTS audio playback process immediately.",
            "parameters": {
                "type": "object",
                "properties": {},
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
            "description": "Invoke a Model Context Protocol (MCP) tool on an enabled MCP server, including Eyes & Hands servers (`computer-use-linux` for screenshots, AT-SPI2 UI tree, mouse/keyboard & window actions; `playwright-mcp` & `chrome-devtools-mcp` for browser DOM/snapshots/actions; `web-search-mcp` & `fetch-mcp` for live web search and URL reading; `graphify-mcp` for codebase knowledge graphs, god nodes & shortest paths; `pipewire-mpris-mcp` for audio/media/voice; `desktop-commander` for terminal/processes; `companion-system` for system/RAG/Pocket).",
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
    {
        "type": "function",
        "function": {
            "name": "delegate_subagent",
            "description": "Delegate a scoped subtask to an isolated-context specialist Subagent (`researcher`, `planner`, `executor`, `coder`, or `verifier`) running sequentially on the same single loaded local model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["researcher", "planner", "executor", "coder", "verifier"],
                        "description": "Specialist subagent role to invoke in an isolated context window.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Concrete subtask instructions for the subagent.",
                    },
                    "context_hint": {
                        "type": "string",
                        "description": "Optional compact context hint or file/domain pointers.",
                    },
                },
                "required": ["role", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_spill",
            "description": "Read exact line ranges or search matches from a session-scoped tool output spill file (`spills/<session>/<call>.txt`) without re-running the tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spill_id": {
                        "type": "string",
                        "description": "Spill identifier shown in the `[... spilled ...]` preview marker.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional path inside the current session's spill directory.",
                    },
                    "offset_line": {
                        "type": "integer",
                        "description": "1-based starting line number (default 1).",
                    },
                    "limit_lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to return (default 120, max 400).",
                    },
                    "grep": {
                        "type": "string",
                        "description": "Optional substring or regex pattern to filter matching lines.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "alis_live_state",
            "description": (
                "Inspect live Alis shell, hardware, and integration state on this machine: weather/AQI/forecast, "
                "local music/MPRIS, Pomodoro/timers, Todo, planner schedule, QuickNotes, notifications, screen time, "
                "Spaced Repetition flashcards, Exercises Tracker workouts, CPU/GPU/RAM/Battery/Thinkfan/TLP, and Niri desktop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": [
                            "all",
                            "weather",
                            "music",
                            "timers",
                            "todos",
                            "schedule",
                            "quicknotes",
                            "notifications",
                            "screentime",
                            "learning",
                            "fitness",
                            "system",
                            "desktop",
                        ],
                    },
                    "query": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nexus_knowledge",
            "description": (
                "Search, read, or inspect the user's Nexus/Obsidian Vault (notes, tags, daily journal, schedule, finance, "
                "collections, and Markdown RAG notes in 90_System/99_RAG) or propose saving a RAG knowledge note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "read", "overview", "journal", "finance", "collections", "save_rag_note"],
                    },
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "folder": {"type": "string"},
                    "tag": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "alis_service_control",
            "description": (
                "Control live Alis desktop services: playback & local music search (`music_toggle`, `music_next`, `music_prev`, "
                "`music_shuffle`, `music_stop`, `music_play_query`), Pomodoro/stopwatch (`pomodoro_toggle`, `pomodoro_start`, "
                "`pomodoro_pause`, `pomodoro_reset`, `stopwatch_toggle`, `stopwatch_reset`), display/power modes "
                "(`night_light_set`, `night_light_toggle`, `gamemode_set`, `gamemode_toggle`, `dnd_set`, `dnd_toggle`), "
                "`weather_refresh`, Nexus navigation (`nexus_open`, `nexus_open_note`, `nexus_open_daily`), "
                "or confirmation-gated creation (`todo_add`, `quicknote_add`)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
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
            "name": "tool_search",
            "description": (
                "Discover and load additional specialized tools (such as git_inspect, web_search, web_fetch, "
                "file_mutate, code_inspect, desktop_control, process_manage, mcp_call, skill_run, delegate_subagent) "
                "into the active schema list on demand."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords describing the capability needed (e.g. 'git diff', 'web fetch', 'kill process').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matching tools to load (default 6).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "emit_ui_card",
            "description": (
                "Emit a single compact A2UI v1.0 interactive card (`<= 6 KB`) alongside your Markdown reply "
                "(only available when Generative A2UI is enabled by the user). Allowed components: Column, Row, "
                "Card, Divider, Text (body|caption), Icon (snake_case), Button, List, ChoicePicker, CheckBox, "
                "TextField (never passwords), Slider, Tabs, Image (local path only)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "surface": {
                        "type": "object",
                        "description": "Complete A2UI v1.0 createSurface envelope (`{version:'1.0', createSurface:{...}}`).",
                    },
                    "title": {"type": "string", "description": "Optional short card title if passing simplified fields."},
                    "components": {"type": "array", "description": "A2UI v1.0 component list containing an id='root' item."},
                    "dataModel": {"type": "object", "description": "Optional JSON dataModel object."},
                },
            },
        },
    },
]

CORE_AGENT_TOOL_NAMES: frozenset[str] = frozenset({
    "file_inspect",
    "shell_run",
    "alis_live_state",
    "nexus_knowledge",
    "read_spill",
    "tool_search",
})

_TAINT_SOURCE_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "web_fetch",
    "screen_observe",
})


def _issue_confirm_id(prefix: str = "conf") -> str:
    seed = f"{prefix}:{time.time_ns()}:{os.getpid()}:{os.urandom(6).hex()}"
    return f"{prefix}_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def search_agent_tool_schemas(query: str, limit: int = 6, *, include_generative_ui: bool = False) -> list[dict]:
    """Search `AGENT_TOOL_SCHEMAS` by name, description, and parameter keywords."""
    q = str(query or "").strip().lower()
    terms = [t for t in re.split(r"[\s,;|/_-]+", q) if t]
    max_items = max(1, min(16, int(limit or 6)))
    pool = [
        s
        for s in AGENT_TOOL_SCHEMAS
        if include_generative_ui or str((s.get("function") or {}).get("name") or "") != "emit_ui_card"
    ]
    if not terms:
        return [s for s in pool if s.get("function", {}).get("name")][:max_items]
    scored: list[tuple[int, dict]] = []
    for schema in pool:
        fn = schema.get("function") or {}
        t_name = str(fn.get("name") or "").strip().lower()
        t_desc = str(fn.get("description") or "").strip().lower()
        props = ((fn.get("parameters") or {}).get("properties") or {})
        prop_blob = " ".join(
            f"{k} {str((v or {}).get('description') or '')}"
            for k, v in props.items()
            if isinstance(v, dict)
        ).lower()
        score = 0
        for term in terms:
            if term == t_name:
                score += 10
            elif term in t_name:
                score += 6
            if term in t_desc:
                score += 3
            if term in prop_blob:
                score += 2
        if score > 0:
            scored.append((score, schema))
    scored.sort(key=lambda pair: (-pair[0], str((pair[1].get("function") or {}).get("name") or "")))
    return [pair[1] for pair in scored[:max_items]]


def select_initial_tool_schemas(prompt: str = "", full: bool = False) -> list[dict]:
    """Select compact core tools initially (`full=True` returns all `AGENT_TOOL_SCHEMAS`)."""
    if full:
        return list(AGENT_TOOL_SCHEMAS)
    core = [
        schema
        for schema in AGENT_TOOL_SCHEMAS
        if str((schema.get("function") or {}).get("name") or "") in CORE_AGENT_TOOL_NAMES
    ]
    return core if core else list(AGENT_TOOL_SCHEMAS)


def _is_privileged_taint_sink(tool_name: str, args: dict | None = None) -> bool:
    """Return True if executing `tool_name` with `args` is a privileged side-effect sink."""
    t_name = str(tool_name or "").strip()
    t_args = args if isinstance(args, dict) else {}
    if t_name in (
        "shell_run",
        "run_command",
        "file_mutate",
        "edit_file",
        "write_file",
        "create_document",
        "convert_document",
        "gui_action",
        "file_receipt_rollback",
        "skill_run",
    ):
        return True
    act = str(t_args.get("action") or t_args.get("op") or "").strip().lower()
    if t_name == "process_manage":
        return act == "kill"
    if t_name == "desktop_control":
        return act in ("launch_app", "gui_click", "gui_fill", "key_press", "clipboard_set", "close_window")
    if t_name == "code_inspect":
        return act == "run_tests"
    if t_name == "skill_manage":
        return act not in ("", "list", "inspect", "reflections", "get", "read", "status", "search")
    if t_name == "mcp_call":
        mcp_tool = str(t_args.get("tool") or "").strip().lower()
        if mcp_tool in (
            "shell_exec",
            "execute_command",
            "desktop_action",
            "window_action",
            "mouse_click",
            "type_text",
            "key_press",
        ):
            return True
        if mcp_tool in ("process_list", "list_processes"):
            nested_args = t_args.get("arguments") if isinstance(t_args.get("arguments"), dict) else t_args
            return str(nested_args.get("action") or "").strip().lower() == "kill"
        return False
    if t_name == "delegate_subagent":
        role = str(t_args.get("role") or "").strip().lower()
        return role in ("executor", "coder")
    return False


def _detect_untrusted_taint_label(
    tool_name: str,
    args: dict | None,
    session_ctx: dict,
    companion_root: Path,
) -> str:
    """Return non-empty taint source label when `tool_name` ingests untrusted external input."""
    t_name = str(tool_name or "").strip()
    t_args = args if isinstance(args, dict) else {}
    if t_name in _TAINT_SOURCE_TOOLS:
        return t_name
    if t_name == "mcp_call":
        srv = str(t_args.get("server") or t_args.get("tool") or "external").strip()
        return f"mcp_call:{srv}"
    if t_name == "file_inspect":
        raw_path = str(t_args.get("path") or t_args.get("target") or "").strip()
        if not raw_path:
            return ""
        base_dir = Path(str(session_ctx.get("lastDirectory") or companion_root)).expanduser()
        cand = Path(raw_path).expanduser()
        resolved = cand.resolve(strict=False) if cand.is_absolute() else (base_dir / cand).resolve(strict=False)
        allowed_roots: list[Path] = [Path(companion_root).resolve(strict=False)]
        for k in ("lastProject", "lastDirectory"):
            val = str(session_ctx.get(k) or "").strip()
            if val:
                allowed_roots.append(Path(val).expanduser().resolve(strict=False))
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            return f"file_inspect:{raw_path[:60]}"
    return ""

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


_DETECT_VI_MARKER_RE = re.compile(
    r"\b(?:giup toi|man hinh|thu muc|tep|cua so|du an|kiem tra"
    r"|xoa|sua|doc\s+(?:tep|file|ghi chu|trang|du lieu|noi dung)|nho rang|quen|quy tac|quyen|tiep tuc"
    r"|dung lai|huy|lam tiep|vua mo|liet ke|tat ca"
    r"|chup anh|chup man hinh|mo ta|thay gi|ban thay|thoi tiet"
    r"|hom nay|ngay mai|bay gio|cho toi|giup minh|noi cho"
    r"|nhin xem|dang mo|nhu the nao|the nao|tai sao|tom tat"
    r"|giai thich)\b"
)


def detect_language(prompt: str) -> str:
    text = str(prompt or "").strip()
    if re.search(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", text):
        return "vi"
    folded = _fold_ascii(text)
    if _DETECT_VI_MARKER_RE.search(folded):
        return "vi"
    return "en"


def is_hypothetical_or_negated(prompt: str) -> bool:
    raw = str(prompt or "").strip()
    if not raw:
        return False
    folded = _fold_ascii(raw)

    if re.match(r"^(?:giai thich|explain|what does|y nghia cua|dich cau|translate|phan tich cau)\b", folded):
        has_quoted = bool(re.search(r"""['"`].+['"`]""", raw))
        has_action_or_path_cue = bool(
            re.search(
                r"""(?:\b(?:rm|delete|xoa|wipe|format|chmod|chown|sudo|kill|pkill|systemctl|mv|cp|dd|mkfs)\b|~/|\./|\.\./|/(?:home|etc|var|usr|tmp|root)/)""",
                folded,
            )
        )
        if has_quoted or has_action_or_path_cue:
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

    raw_head = (
        raw.split(":", 1)[0].strip()
        if re.match(r"^[^:\n]*\b(?:sửa|sua|ghi|thêm|them|viết|viet|tạo|tao|edit|write|append|create)\b", raw, re.IGNORECASE)
        else raw
    )

    # 4a. Quoted filename or folder target in find / locate / named / "ở đâu" queries (preserves spaces inside quotes)
    m_quoted_target = re.search(
        r"""(?:\b(?:find|locate|search(?:\s+for)?|look\s+for|where\s+is|tìm(?:\s+kiếm)?|tim(?:\s+kiem)?|kiếm|kiem|file|tệp|tep|folder|thư\s+mục|thu\s+muc|directory|named|called|tên(?:\s+là)?|ten(?:\s+la)?)\b[^'"`\n]{0,48}['"`]([^'"`\n]{1,120})['"`]|['"`]([^'"`\n]{1,120})['"`]\s*(?:nằm\s+ở\s+đâu|nam\s+o\s+dau|ở\s+đâu|o\s+dau)\b)""",
        raw_head,
        re.IGNORECASE,
    )
    if m_quoted_target:
        q_cand = (m_quoted_target.group(1) or m_quoted_target.group(2) or "").strip().rstrip(".,;!?")
        if q_cand and not q_cand.startswith("/agent"):
            try:
                return str(resolve_candidate_path(q_cand, last_dir=last_dir_str))
            except ToolError:
                return q_cand

    # 4b. Well-known dotfiles (e.g. .bashrc, .zshrc, .gitignore, .env) or filename with extension
    m_dotfile = re.search(
        r"""(?:^|(?<=[\s'"`(]))(\.(?:bashrc|bash_profile|zshrc|zshenv|profile|gitignore|gitconfig|npmrc|vimrc|tmux\.conf|env(?:\.[A-Za-z0-9_.\-]+)?))(?=$|[\s'"`).,;!?])""",
        raw_head,
        re.IGNORECASE,
    )
    if m_dotfile:
        df_name = m_dotfile.group(1)
        try:
            return str(resolve_candidate_path(df_name, last_dir=last_dir_str))
        except ToolError:
            return df_name

    m_file = re.search(
        r"""\b([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*\.(?:txt|md|py|qml|js|cjs|mjs|ts|tsx|jsx|json|rs|toml|sh|bash|zsh|fish|yaml|yml|html|css|scss|vue|svelte|c|cpp|cc|h|hpp|go|java|kt|rb|php|lua|sql|xml|csv|tsv|env|nix|log|conf|cfg|ini|service|tar|gz|zip|pdf|docx|xlsx|pptx|odt|epub|jpg|jpeg|png|webp|svg|mp3|mp4|wav|flac|ogg|ttf|otf|woff|woff2|sqlite3|sqlite|db|lock|bak|patch|diff|AppImage))\b""",
        raw_head,
        re.IGNORECASE,
    )
    if m_file:
        fname = m_file.group(1)
        try:
            return str(resolve_candidate_path(fname, last_dir=last_dir_str))
        except ToolError:
            return fname

    # 4c. Explicit find / locate / named / "ở đâu" file or folder target (unquoted)
    _stop_targets = {
        "for", "me", "in", "on", "at", "the", "a", "an", "file", "files", "folder", "folders",
        "directory", "tep", "tệp", "thư", "thu", "mục", "muc", "cho", "tôi", "toi", "mình",
        "minh", "giúp", "giup", "có", "co", "tên", "ten", "là", "la", "nằm", "nam", "ở", "o",
        "đâu", "dau", "trong", "của", "cua", "này", "nay", "đó", "do", "xem", "coi",
    }
    m_named = re.search(
        r"""\b(?:(?:find|locate|search(?:\s+for)?|look\s+for|where\s+is|tìm(?:\s+kiếm)?|tim(?:\s+kiem)?|kiếm|kiem)(?:\s+(?:giúp|giup|cho|hộ|ho))?(?:\s+(?:tôi|toi|mình|minh|em|me))?(?:\s+(?:the|a|an))?(?:\s+(?:file|tệp|tep|folder|thư\s+mục|thu\s+muc|directory))(?:\s+(?:có\s+tên(?:\s+là)?|co\s+ten(?:\s+la)?|mang\s+tên|mang\s+ten|tên\s+là|ten\s+la|tên|ten|named|called))?|(?:file|tệp|tep|folder|thư\s+mục|thu\s+muc|directory)\s+(?:có\s+tên(?:\s+là)?|co\s+ten(?:\s+la)?|mang\s+tên|mang\s+ten|tên\s+là|ten\s+la|named|called)|(?:tìm(?:\s+kiếm)?|tim(?:\s+kiem)?|kiếm|kiem)\s+(?:giúp|giup|cho)\s+(?:tôi|toi|mình|minh)\s+(?:xem\s+)?|(?:where\s+is|locate)\s+(?:the\s+)?)\s*['"`]?([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*)['"`]?""",
        raw_head,
        re.IGNORECASE,
    )
    if not m_named:
        m_named = re.search(
            r"""\b(?:(?:file|tệp|tep|folder|thư\s+mục|thu\s+muc|directory)\s+)?['"`]?([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*)['"`]?\s+(?:nằm\s+ở\s+đâu|nam\s+o\s+dau|ở\s+đâu|o\s+dau)\b""",
            raw_head,
            re.IGNORECASE,
        )
    if m_named:
        cand_name = m_named.group(1).strip().rstrip(".,;!?")
        if cand_name and cand_name.lower() not in _stop_targets:
            try:
                return str(resolve_candidate_path(cand_name, last_dir=last_dir_str))
            except ToolError:
                return cand_name

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
    raw_last_file = str(session_ctx.get("lastFile") or "")
    if (".obsidian/" in raw_last_file or raw_last_file.endswith("01_Core.css")) and "01_core.css" not in folded and ".obsidian" not in folded:
        raw_last_file = ""
    raw_last_dir = str(session_ctx.get("lastDirectory") or "")
    if ".obsidian" in raw_last_dir and ".obsidian" not in folded and "01_core.css" not in folded:
        raw_last_dir = ""
    candidates = [
        str(c)
        for c in (session_ctx.get("recentCandidates") or [])
        if c and not ((".obsidian/" in str(c) or str(c).endswith("01_Core.css")) and "01_core.css" not in folded and ".obsidian" not in folded)
    ]

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
        target_dir = raw_last_dir or session_ctx.get("lastProject") or ""
        if target_dir:
            return target_dir, []
        return str(Path.home()), []

    is_search_like = bool(
        re.search(
            r"\b(?:find|locate|where\s+is|search\s+for|look\s+for|tim\s+file|tim\s+tep|tim\s+kiem|kiem\s+file|kiem\s+tep|nam\s+o\s+dau|o\s+dau)\b",
            folded,
        )
    )
    anaphora_keys = (
        ("tep do", "tep nay", "tep vua nhac", "tep vua doc", "tep vua mo", "file do", "file nay", "that file", "this file")
        if is_search_like
        else ("tep do", "tep nay", "tep vua nhac", "tep vua doc", "tep vua mo", "file do", "file nay", "that file", "this file", "the file")
    )
    if any(k in folded for k in anaphora_keys):
        if raw_last_file:
            return raw_last_file, []
        if len(candidates) > 1:
            return "", candidates
        if len(candidates) == 1:
            return candidates[0], []

    if len(candidates) > 1 and not raw_last_file and not expect_dir:
        return "", candidates

    if expect_dir:
        return raw_last_dir or str(Path.home()), []
    return raw_last_file or raw_last_dir or str(Path.home()), []


_ATTACHMENT_CACHE: dict[tuple, dict] = {}


def resolve_file_attachments_and_mentions(
    prompt: str,
    attached_files: list | None = None,
    session_ctx: dict | None = None,
    companion_root: Path | None = None,
    max_chars_per_file: int = 8000,
) -> dict:
    """Resolve attached file paths and inline `@file` mentions into extracted Markdown context."""
    ctx = session_ctx or {}
    last_dir = str(ctx.get("lastDirectory") or ctx.get("lastProject") or Path.home())
    comp_root = Path(companion_root).resolve(strict=False) if companion_root else Path(__file__).resolve().parents[2]
    search_dirs: list[Path] = []
    for cand_dir in (last_dir, ctx.get("lastProject"), str(comp_root), str(Path.home())):
        if cand_dir:
            dp = Path(str(cand_dir)).expanduser().resolve(strict=False)
            if dp.is_dir() and dp not in search_dirs:
                search_dirs.append(dp)

    candidate_paths: list[Path] = []
    seen_str: set[str] = set()

    home_dir = Path.home().resolve(strict=False)

    def _safe_resolved_attachment(raw_p: Path, *, check_sensitive: bool = False) -> Path | None:
        resolved = raw_p.resolve(strict=False)
        if is_symlink_raw(raw_p, root_dir=home_dir):
            if not (resolved == home_dir or resolved.is_relative_to(home_dir)):
                return None
            if is_forbidden_path(resolved) or is_sensitive_path(resolved):
                return None
        if not resolved.is_file() or is_forbidden_path(resolved):
            return None
        if check_sensitive and (is_sensitive_path(raw_p) or is_sensitive_path(resolved)):
            return None
        return resolved

    for raw_f in (attached_files or []):
        f_str = str(raw_f or "").strip()
        if f_str.startswith("file://"):
            f_str = f_str[7:]
        if not f_str:
            continue
        raw_p = Path(f_str).expanduser()
        if not raw_p.is_absolute():
            raw_p = Path(last_dir) / raw_p
        p = _safe_resolved_attachment(raw_p)
        if p is not None and str(p) not in seen_str:
            seen_str.add(str(p))
            candidate_paths.append(p)

    # Extract inline @file and @context mentions (e.g. @selection, @clipboard, @screen, @pocket, @git-diff, @README.md)
    raw_prompt = str(prompt or "")
    missing_mentions: list[str] = []
    special_mention_sections: list[str] = []
    seen_special: set[str] = set()
    for m in re.finditer(r"(?:^|(?<=\s))@((?:~/|/|\./|\.\./)?[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*)", raw_prompt):
        token = m.group(1).rstrip(".,;:!?)]}\"'`")
        if not token or len(token) < 2:
            continue
        tok_low = token.lower()
        if tok_low in ("selection", "clipboard", "screen", "pocket", "git-diff"):
            if tok_low in seen_special:
                continue
            seen_special.add(tok_low)
            try:
                import desktop_control
                if tok_low == "selection":
                    sel_info = desktop_control.primary_selection_get()
                    sel_txt = str(sel_info.get("text") or ctx.get("primarySelection") or "").strip()[:3200]
                    special_mention_sections.append(
                        f"### Highlighted Selection (`@selection`)\n```text\n{sel_txt or '(No active primary selection text)'}\n```"
                    )
                elif tok_low == "clipboard":
                    clip_info = desktop_control.clipboard_get()
                    clip_txt = str(clip_info.get("text") or "").strip()[:3200]
                    special_mention_sections.append(
                        f"### Clipboard (`@clipboard`)\n```text\n{clip_txt or '(Clipboard is empty)'}\n```"
                    )
                elif tok_low == "screen":
                    win_info = desktop_control.manage_desktop("list_windows")
                    win_lines = win_info.get("lines") or [win_info.get("summary") or "(No active window info)"]
                    special_mention_sections.append(
                        "### Active Screen / Windows (`@screen`)\n```text\n"
                        + "\n".join(str(x) for x in win_lines[:12])
                        + "\n```"
                    )
                elif tok_low == "pocket":
                    p_items = pocket_store.dispatch({"version": 1, "consent": True, "action": "list"}).get("items", [])
                    p_lines = []
                    for item in p_items[:6]:
                        raw_ip = Path(str(item.get("path") or ""))
                        p_lines.append(f"- `{item.get('name')}` (`{raw_ip}`)")
                        ip = _safe_resolved_attachment(raw_ip, check_sensitive=True)
                        if ip is not None and str(ip) not in seen_str:
                            seen_str.add(str(ip))
                            candidate_paths.append(ip)
                    special_mention_sections.append(
                        "### Pocket Items (`@pocket`)\n" + ("\n".join(p_lines) if p_lines else "(Pocket is currently empty)")
                    )
                elif tok_low == "git-diff":
                    import subprocess
                    git_dir = str(ctx.get("lastDirectory") or ctx.get("lastProject") or comp_root)
                    st_res = subprocess.run(
                        ["git", "-C", git_dir, "status", "--short"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3.0, check=False, text=True
                    )
                    df_res = subprocess.run(
                        ["git", "-C", git_dir, "diff", "--stat", "HEAD"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3.0, check=False, text=True
                    )
                    git_out = ((st_res.stdout or "").strip() + "\n" + (df_res.stdout or "").strip()).strip()[:3200]
                    special_mention_sections.append(
                        f"### Workspace Git Diff (`@git-diff` in `{git_dir}`)\n```diff\n{git_out or '(Working tree clean)'}\n```"
                    )
            except Exception:
                pass
            continue
        resolved_match: Path | None = None
        if token.startswith(("~/", "/", "./", "../")):
            raw_cand = Path(token).expanduser()
            if not raw_cand.is_absolute():
                raw_cand = Path(last_dir) / raw_cand
            resolved_match = _safe_resolved_attachment(raw_cand, check_sensitive=True)
        else:
            for sdir in search_dirs:
                raw_cand = sdir / token
                cand = _safe_resolved_attachment(raw_cand, check_sensitive=True)
                if cand is not None:
                    resolved_match = cand
                    break
        if resolved_match:
            if str(resolved_match) not in seen_str:
                seen_str.add(str(resolved_match))
                candidate_paths.append(resolved_match)
        elif (
            "/" in token
            or "." in token
            or token.lower() in {
                "makefile",
                "dockerfile",
                "pkgbuild",
                "readme",
                "license",
                "copying",
                "justfile",
                "cmakelists",
                "vagrantfile",
                "procfile",
                "gemfile",
                "rakefile",
                "caddyfile",
                "jenkinsfile",
            }
        ) and token not in missing_mentions:
            missing_mentions.append(token)

    cache_key = (
        tuple(
            (str(p), int(p.stat().st_mtime_ns) if p.exists() else 0)
            for p in candidate_paths[:8]
        ),
        tuple(missing_mentions[:8]),
        int(max_chars_per_file),
    )
    if not seen_special and cache_key in _ATTACHMENT_CACHE:
        return dict(_ATTACHMENT_CACHE[cache_key])

    binary_doc_exts = {".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".epub"}
    files_out: list[dict] = []
    context_sections: list[str] = list(special_mention_sections)
    for p in candidate_paths[:8]:
        ext_lower = p.suffix.lower()
        try:
            content = rag_store.extract_document_markdown(p, max_chars=max_chars_per_file)
        except Exception:
            content = ""
        if not content and ext_lower not in binary_doc_exts:
            try:
                raw_txt = p.read_text(encoding="utf-8", errors="replace")[:max_chars_per_file]
                if "\x00" not in raw_txt[:512]:
                    content = raw_txt
            except OSError:
                content = ""
        snippet = content.replace("\x00", "").strip()[:max_chars_per_file]
        if not snippet and ext_lower in binary_doc_exts:
            snippet = f"(No extractable text found in `{p.name}`)"
        files_out.append({
            "path": str(p),
            "name": p.name,
            "ext": ext_lower,
            "content": snippet,
            "charCount": len(snippet),
        })
        if snippet:
            context_sections.append(f"### Attached File: `{p.name}` (`{p}`)\n```markdown\n{snippet[:3200]}\n```")

    if missing_mentions:
        missing_str = ", ".join(f"`@{tok}`" for tok in missing_mentions[:8])
        context_sections.append(
            f"### Missing Mentioned File(s)\n"
            f"WARNING: The following `@file` mention(s) do NOT exist on disk in the active workspace or search directories: {missing_str}. "
            f"Do NOT fabricate, guess, or invent their contents; state clearly that the file was not found."
        )

    joined_block = "\n\n".join(context_sections)
    result = {
        "files": files_out,
        "missingMentions": missing_mentions[:8],
        "contextBlock": joined_block,
        "contextMarkdown": joined_block,
    }
    if len(_ATTACHMENT_CACHE) > 32:
        _ATTACHMENT_CACHE.clear()
    _ATTACHMENT_CACHE[cache_key] = result
    return dict(result)


def _match_screen_observe_intent(folded: str, lang: str) -> dict | None:
    """Detect screen/screenshot observation intent without colliding with 'screen time' telemetry."""
    scrubbed = re.sub(r"\b(?:screen\s*time|thoi gian su dung may|thoi gian dung may)\b", " ", str(folded or ""))
    m_main = re.search(
        r"\b(?:xem man hinh|doc man hinh|nhin man hinh|quet man hinh|kiem tra man hinh|chup man hinh|chup anh man hinh|chup lai man hinh|thay man hinh|thay gi tren man hinh|nhin thay man hinh|doc cua so|cua so hien tai|ung dung dang mo|man hinh dang co gi|tren man hinh|trong anh chup|trong buc anh|mat va tay|scan screen|scan window|what is on my screen|what is on the screen|what do you see|what can you see|what you can see|describe.*screen\s*shot|describe.*screen|can you see.*screen|can you see now|see my screen|see the screen|see things on.*screen|anything on.*screen|on my screen|on the screen|in the screen\s*shot|from the screen\s*shot|about the screen\s*shot|inside the screen\s*shot|read the screen|read current window|inspect active window|look at my screen|take(?:\s+a)?\s+screen\s*shot|capture screen|screen\s*shot|eyes and hands|use your eyes|set of mark|annotate som|danh so phan tu)\b",
        scrubbed,
    )
    m_head = re.match(r"^(?:/agent\s+|/)?(?:access\s+)?(?:screen|scan|window|app|desktop\s+app|eyes|screen\s*shot|som)\b", scrubbed)
    m = m_main or m_head
    if not m:
        return None
    wants_shot = bool(
        re.search(r"\b(?:chup man hinh|chup anh man hinh|chup lai man hinh|take(?:\s+a)?\s+screen\s*shot|capture screen|screen\s*shot|set of mark|annotate som|som)\b", scrubbed)
        or re.match(r"^(?:/agent\s+|/)?(?:screen\s*shot|som)\b", scrubbed)
    )
    wants_som = bool(re.search(r"\b(?:set of mark|annotate som|som|danh so phan tu)\b", scrubbed))
    return {"kind": "screen_observe", "captureScreenshot": wants_shot, "annotateSom": wants_som, "language": lang, "pos": m.start()}


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

    # Check if awaiting sudo password for a system path operation (unless user starts a new slash command)
    if ctx.get("pendingSudoOp") and not raw.startswith("/"):
        return {"kind": "sudo_password_reply", "language": lang}

    # Check if there is a pending clarification in session_ctx (unless user starts a new slash command)
    if ctx.get("pendingClarification") and not raw.startswith("/"):
        p_clar = ctx.get("pendingClarification") or {}
        if str(p_clar.get("kind") or "") == "plan_followup":
            p_opts = p_clar.get("options") if isinstance(p_clar.get("options"), list) else []
            if re.match(r"^(?:#|option\s+|lua chon\s+|y\s+|idea\s+|chon\s+|phuong an\s+)?[1-3](?:\s*[.)])?$", folded) or any(
                isinstance(o, dict)
                and (
                    (o.get("label") and raw.lower() == str(o["label"]).strip().lower())
                    or (o.get("value") and raw.lower() == str(o["value"]).strip().lower())
                )
                for o in p_opts
            ):
                return {"kind": "clarification_reply", "language": lang}
        else:
            return {"kind": "clarification_reply", "language": lang}

    perm_state = inspect_permissions()
    if perm_state.get("latestPending") or ctx.get("pendingDeletePath") or ctx.get("pendingMemoryForget") or ctx.get("pendingClientAction"):
        latest = perm_state.get("latestPending")
        if latest and latest.get("actionKind") == "system_sudo_op" and not raw.startswith("/"):
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

    # Rule management via prompt or `/rules` / `/rule`
    if re.match(r"^/rules?\b", folded):
        rest_rule = re.sub(r"^/rules?\b\s*", "", folded).strip()
        if not rest_rule or rest_rule in ("list", "show", "status", "xem", "danh sach"):
            return {"kind": "rule_manage", "subkind": "list", "language": lang}
        if rest_rule.startswith(("reset", "default", "khoi phuc", "mac dinh")):
            return {"kind": "rule_manage", "subkind": "reset", "language": lang}
        if any(w in rest_rule.split()[:2] for w in ("disable", "delete", "remove", "stop", "tat", "xoa", "huy")):
            return {"kind": "rule_manage", "subkind": "disable", "language": lang}
        return {"kind": "rule_manage", "subkind": "create", "language": lang}
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

    # Local Offline Voice I/O (`/tts`, `/speak`, `/listen`, `/stt`)
    if re.match(r"^/(?:tts|speak|listen|stt)\b", folded) or re.search(
        r"\b(?:doc to|phat am|noi cau|doc bang giong noi|ghi am giong noi|nhan dien giong noi|chuyen giong noi thanh van ban|read aloud|speak aloud|text to speech|speech to text|voice input|transcribe audio)\b",
        folded,
    ):
        v_sub = "status"
        if re.match(r"^/(?:tts|speak)\b", folded) or re.search(r"\b(?:tts|speak|doc to|phat am|noi cau|doc bang giong noi|read aloud|speak aloud|text to speech)\b", folded):
            v_sub = "tts"
        elif re.match(r"^/(?:stt|listen)\b", folded) or re.search(r"\b(?:stt|listen|ghi am|nhan dien giong noi|chuyen giong noi|speech to text|voice input|transcribe)\b", folded):
            v_sub = "stt"
        return {"kind": "voice_task", "subkind": v_sub, "language": lang}

    # Hermes Self-Learning, Workflow Adaptation & Agent Skills (`SKILL.md`)
    if (
        re.match(r"^(?:/skills?|skills?|/learn|/workflow|/reflect|/graphify|/ponytail|/docx|/pdf|/xlsx)\b", folded)
        or re.search(
            r"\b(?:danh sach skill|danh sach ky nang|xem skill|xem ky nang|cac skill|cac ky nang|chay skill|dung skill|dung ky nang|tao skill|tao ky nang|xoa skill|bat skill|tat skill|cap nhat skill|dieu chinh skill|chinh sua skill|bo sung skill|tu hoc|hoc quy trinh|luu quy trinh|dieu chinh quy trinh|rut kinh nghiem|list skills|show skills|run skill|use skill|create skill|delete skill|enable skill|disable skill|patch skill|update skill|learn skill|learn workflow|adapt workflow|self-learn|workflow reflections|system-diagnostics|code-assistant|rag-researcher|focus-planner|screen-copilot|desktop-organizer|window-workspace-layout|git-daily-brief|system-tuner|clipboard-transformer|niri-wm|quickshell-qml|arch-linux|graphify|ponytail)\b",
            folded,
        )
    ):
        if re.match(r"^/graphify\b", folded):
            return {"kind": "skill_manage", "directSkill": "graphify", "language": lang}
        if re.match(r"^/ponytail\b", folded):
            return {"kind": "skill_manage", "directSkill": "ponytail", "language": lang}
        if re.match(r"^/docx\b", folded):
            return {"kind": "skill_manage", "directSkill": "docx", "language": lang}
        if re.match(r"^/pdf\b", folded):
            return {"kind": "skill_manage", "directSkill": "pdf", "language": lang}
        if re.match(r"^/xlsx\b", folded):
            return {"kind": "skill_manage", "directSkill": "xlsx", "language": lang}
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

    # GUI action (Set-of-Mark `/som click <id>`, `@eN` semantic AT-SPI ref click/fill/focus & window focus) before `screen_observe`
    if (
        re.match(r"^(?:/agent\s+|/)?som\s+(?:click|bam|nhan|fill|type|set_text|focus|focus_element|atspi_action)\b", folded)
        or re.search(r"\b(?:chuyen sang cua so|focus window|focus cua so|kiem tra cua so|som click|click mark|bam vao mark|click button|bam nut|fill element|focus element|atspi_action)\b", folded)
        or re.search(r"\b(?:click|bam|nhan|fill|set_text|type|nhap|dien|focus|focus_element|atspi_action)\s+(?:vao\s+|into\s+|on\s+)?(?:\[\d+\]|@e\d+\b)", folded)
    ):
        is_fill_sub = bool(
            re.match(r"^(?:/agent\s+|/)?som\s+(?:fill|type|set_text)\b", folded)
            or re.search(r"\b(?:fill|set_text|type|nhap|dien)\s+(?:vao\s+|into\s+)?(?:\[\d+\]|@e\d+\b)", folded)
            or re.search(r"\bfill\s+element\b", folded)
        )
        is_focus_sub = bool(
            re.match(r"^(?:/agent\s+|/)?som\s+focus(?:_element)?\b", folded)
            or re.search(r"\bfocus(?:_element)?\s+(?:vao\s+|on\s+)?(?:\[\d+\]|@e\d+\b)", folded)
            or re.search(r"\bfocus\s+element\b", folded)
        )
        is_atspi_sub = bool(
            re.match(r"^(?:/agent\s+|/)?som\s+atspi_action\b", folded)
            or re.search(r"\batspi_action\b", folded)
        )
        is_som_sub = bool(
            is_atspi_sub
            or re.match(r"^(?:/agent\s+|/)?som\s+(?:click|bam|nhan)\b", folded)
            or re.search(r"\b(?:som click|click mark|bam vao mark|click button|bam nut)\b", folded)
            or re.search(r"\b(?:click|bam|nhan)\s+(?:vao\s+|on\s+)?(?:\[\d+\]|@e\d+\b)", folded)
        )
        subkind = (
            "fill"
            if is_fill_sub
            else (
                "focus_element"
                if is_focus_sub
                else ("atspi_action" if is_atspi_sub else ("som_click" if is_som_sub else "window"))
            )
        )
        return {"kind": "gui_action", "subkind": subkind, "language": lang}

    if domain_rag.is_config_check_query(raw):
        return {"kind": "rag_query", "subkind": "check_config", "language": lang}
    if domain_rag.is_domain_concept_or_command_query(raw):
        return {"kind": "rag_query", "subkind": "domain_concepts", "language": lang}
    if domain_rag.is_domain_capability_query(raw):
        return {"kind": "rag_query", "subkind": "domain_capability", "language": lang}

    # Live Alis service telemetry, smart service controls & Nexus Vault knowledge + Compound Multi-Intent Orchestration
    if not raw.startswith("/"):
        local_subs = alis_local_bridge.detect_all_alis_local_intents(raw)
        screen_sub = _match_screen_observe_intent(folded, lang)
        combined: list[dict] = []
        seen_keys: set[tuple[str, str]] = set()
        candidates = list(local_subs) + ([screen_sub] if screen_sub else [])
        for cand in sorted(candidates, key=lambda item: int(item.get("pos") or 0)):
            sk = str(cand.get("kind") or "")
            skey = (sk, str(cand.get("topic") or cand.get("action") or cand.get("subkind") or ""))
            if not sk or skey in seen_keys:
                continue
            seen_keys.add(skey)
            clean_sub = {k: v for k, v in cand.items() if k != "pos"}
            clean_sub.setdefault("language", lang)
            combined.append(clean_sub)
            if len(combined) >= 4:
                break
        if len(combined) >= 2:
            return {"kind": "compound_task", "subtasks": combined, "language": lang}

    alis_intent = alis_local_bridge.detect_alis_local_intent(raw)
    if isinstance(alis_intent, dict) and alis_intent.get("kind"):
        alis_intent.setdefault("language", lang)
        return alis_intent

    # Screen / window observation (including Set-of-Mark `/som` screenshot)
    screen_only = _match_screen_observe_intent(folded, lang)
    if screen_only:
        screen_only.pop("pos", None)
        return screen_only

    # Standalone Companion /swarm subagent orchestration (Board / Members / Graph)
    if re.match(r"^/swarm\b", folded):
        return {"kind": "swarm_task", "language": lang}

    # Coding / LSP / repo map / test execution / structured planning (`/plan`)
    if re.match(r"^/(?:clarify|grill-me|grill|ask)\b", folded):
        return {"kind": "clarify_task", "language": lang}
    if re.match(r"^/plan\s*$", folded):
        return {"kind": "clarify_task", "subkind": "plan", "language": lang}
    if (
        re.match(r"^/plan\b", folded)
        or re.search(r"\b(?:lap ke hoach|len ke hoach|thiet ke ke hoach|implementation plan|create a plan|make a plan|build a plan|help me plan)\b", folded)
        or re.search(r"(?:^|\b(?:please|can you|could you|help me|i want to|i need to|we need to|lets|let's|and)\s+)plan\s+(?:a|an|my|the|our|for)\b", folded)
    ):
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

    # Web Search, URL Reader, DOM Snapshot & Browser Control (`web-search-mcp`, `fetch-mcp`, `playwright-mcp`)
    if re.match(r"^/dom\b", folded) or re.search(r"\b(?:dom snapshot|browser snapshot|quet dom)\b", folded):
        return {"kind": "web_task", "subkind": "dom_snapshot", "language": lang}
    if re.search(r"\b(?:click|bam|fill|type|select|chon|nhap)\s+(?:vao\s+|phan tu\s+|element\s+)?\[?e\d+\]?\b", folded):
        return {"kind": "web_task", "subkind": "dom_action", "language": lang}
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
    if (
        re.search(
            r"\b(?:mo trinh duyet|vao trinh duyet|bat trinh duyet|mo browser|mo the browser|mo trang web|mo web|mo internet|vao mang|vao internet|len mang|mo link|mo url|mo youtube|vao youtube|len youtube|tren youtube|on youtube|in youtube|via youtube|search youtube|youtube search|mo google|vao google|open browser|open the browser|open a browser|open default browser|open internet|open the internet|go online|open url|open website|open the website|open in browser|open youtube|go to youtube|visit youtube)\b",
            folded,
        )
        or re.search(
            r"\b(?:mo bai hat|mo bai nhac|mo bai|phat bai hat|phat bai nhac|phat bai|bat bai hat|bat bai nhac|bat bai|nghe bai hat|nghe bai|mo ca khuc|phat ca khuc|mo video|phat video|xem video|play song|play a song|play the song|play music|play video|play a video|play the video|play track|play a track|listen to the song|listen to a song)\s+\S+",
            folded,
        )
        or re.search(
            r"\b(?:play|listen to|watch|search|find|look up|look for|mo|phat|bat|nghe|xem|tim|tim kiem)\b.+\b(?:on|in|via|tren)\s+(?:youtube|yt)\b",
            folded,
        )
    ):
        return {"kind": "web_task", "subkind": "open_browser", "language": lang}
    if re.search(
        r"\b(?:tim tren web|tim kiem tren web|tim tren mang|tim kiem tren mang|tim tren internet|tim kiem tren internet|tra cuu tren web|tra cuu tren mang|tra cuu tren internet|tra cuu thong tin tren internet|su dung internet|dung internet|su dung mang|dung mang|su dung web|dung web|tham khao tren internet|tham khao internet|use the internet|use internet|use the web|tim google|search google|search web|web search|search the internet|search online|tim tren github|search github|tim tren wikipedia|search wikipedia|tra cuu wikipedia)\b",
        folded,
    ) or re.search(
        r"\b(?:tong hop thong tin|tra cuu thong tin|tim thong tin)\b.*\b(?:internet|web|mang|online)\b",
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

    # Desktop, window, Niri column/layout, workspace, app launch, audio, brightness, media, clipboard & notification control
    if re.search(
        r"\b(?:danh sach cua so|cac cua so dang mo|liet ke cua so|list windows|open windows|dong cua so|close window|chuyen workspace|switch workspace|go to workspace|(?:chuyen|dua)\s+cua\s+so(?:\s+#?\d+)?\s+sang\s+workspace|(?:move|send)\s+window(?:\s+#?\d+)?\s+to\s+workspace|consume or expel|gop hoac tach cua so|consume window|expel window|gop cua so vao cot|tach cua so khoi cot|do rong cot|column width|set column width|resize column|chieu cao cua so|window height|set window height|toan man hinh|fullscreen window|toggle fullscreen|cua so noi|float window|unfloat window|floating window|toggle floating|can giua cot|center column|maximize column|phong to cot|expand column|mo rong cot)\b",
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
    has_file_or_path_ref = bool(
        raw.lstrip().startswith("/")
        or _extract_path_from_text(raw)
        or re.search(
            r"\b(?:file|files|tep|folder|folders|thu muc|directory|directories|project|du an|code|line|dong|script|item|items|first|second|dau tien|thu hai)\b|#1|#2",
            folded,
        )
    )
    if (
        re.search(
            r"\b(?:chay lenh|chay script|chay file|thuc thi lenh|run command|run script|execute command|doc thu muc|liet ke|xem thu muc|mo thu muc|kiem tra thu muc|truy cap|xem tat ca|xem het|danh sach file|danh sach tep|danh sach thu muc|co nhung file nao|co gi trong thu muc|tim tep|tim file|tim kiem file|tim kiem tep|tim thu muc|kiem file|kiem tep|kiem thu muc|tim giup.*(?:file|tep|thu muc)|kiem giup.*(?:file|tep|thu muc)|(?:file|tep|thu muc).*(?:nam o dau|o dau)|doc tep|doc file|xem file|xem tep|kiem tra file|kiem tra tep|tom tat tep|tom tat file|phan tich tep|phan tich file|sua tep|sua file|chinh sua tep|chinh sua file|sua code|chinh sua code|mo trinh sua|de sua|ghi vao|viet vao|them vao|them dong|tao tep|tao file|sao chep|di chuyen|chuyen doi|cat file|cat tep|doi ten file|doi ten tep|xoa tep|xoa file|read folder|list directory|list files|list all|list folders|list everything|list items|show files|show all|show folders|show directory|what files|what is in (?:the |that |this )?(?:folder|directory|file)|find file|find the file|find a file|find folder|find the folder|locate file|locate the file|locate folder|where is.*(?:file|folder)|search for.*(?:file|folder)|search file|look for.*(?:file|folder)|read file|inspect file|check file|summarize (?:the |that |this )?(?:file|folder|directory|document|code)|summarize file|analyze file|edit file|edit code|edit this|edit that|open in editor|open editor|create file|copy file|move file|write file|write to file|convert file|cut file|rename file|delete file)\b",
            folded,
        )
        or re.match(
            r"^(?:/agent\s+|/)?(?:(?:hay|vui long|lam on|giup toi|giup minh|toi muon|minh muon|please|can you|could you|help me|i want to)\s+)*(?:access|list|ls|dir|tree|cd|edit|append|create|copy|cp|sao chep|cut|cat|move|mv|di chuyen|chuyen doi|chuyen|doi ten|rename|delete|rm|xoa|run|exec|execute|chay|thuc thi)\b",
            folded,
        )
        or (
            has_file_or_path_ref
            and re.match(
                r"^(?:/agent\s+|/)?(?:(?:hay|vui long|lam on|giup toi|giup minh|toi muon|minh muon|please|can you|could you|help me|i want to)\s+)*(?:open|find|locate|read|inspect|check|show|summarize|write|convert|what is in)\b",
                folded,
            )
        )
        or re.search(
            r"\b(?:chinh sua|sua|edit|mo|doc|xem|kiem tra|truy cap|tim|tim kiem|kiem|find|locate|search|look for|where is|read|open|inspect|check|show|tom tat|summarize|phan tich|analyze|tao|ghi|viet|them|sao chep|copy|di chuyen|move|chuyen doi|convert|cat|cut|doi ten|rename|xoa|delete|chay|run)\b.*(?:~/|/|\.\.?/|\.(?:bashrc|bash_profile|zshrc|zshenv|profile|gitignore|gitconfig|npmrc|vimrc|tmux\.conf|env)\b|\b(?:makefile|dockerfile|pkgbuild|readme|license|copying|justfile|cmakelists|vagrantfile|procfile|gemfile|rakefile|caddyfile|jenkinsfile)\b|\b[a-z0-9_.\-]+\.(?:txt|md|py|qml|js|cjs|mjs|ts|tsx|jsx|json|rs|toml|sh|bash|zsh|fish|yaml|yml|html|css|scss|vue|svelte|c|cpp|cc|h|hpp|go|java|kt|rb|php|lua|sql|xml|csv|tsv|env|nix|log|conf|cfg|ini|service|pdf|docx|xlsx|pptx|odt|epub|jpg|jpeg|png|webp|svg|mp3|mp4|wav|flac|ogg|ttf|otf|woff|woff2|sqlite3|sqlite|db|lock|bak|patch|diff|appimage)\b)",
            folded,
        )
        or re.search(
            r"(?:~/|/|\.\.?/|\.(?:bashrc|bash_profile|zshrc|zshenv|profile|gitignore|gitconfig|npmrc|vimrc|tmux\.conf|env)\b|\b(?:makefile|dockerfile|pkgbuild|readme|license|copying|justfile|cmakelists|vagrantfile|procfile|gemfile|rakefile|caddyfile|jenkinsfile)\b|\b[a-z0-9_.\-]+\.(?:txt|md|py|qml|js|cjs|mjs|ts|tsx|jsx|json|rs|toml|sh|bash|zsh|fish|yaml|yml|html|css|scss|vue|svelte|c|cpp|cc|h|hpp|go|java|kt|rb|php|lua|sql|xml|csv|tsv|env|nix|log|conf|cfg|ini|service|pdf|docx|xlsx|pptx|odt|epub|jpg|jpeg|png|webp|svg|mp3|mp4|wav|flac|ogg|ttf|otf|woff|woff2|sqlite3|sqlite|db|lock|bak|patch|diff|appimage)\b).*\b(?:nam o dau|o dau)\b",
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
        token in lowered for token in ("qwen3.5", "qwen3_5", "qwen35", "qwen3.6", "qwen3_6", "qwen36", "qwen3.7", "qwen3.8", "qwen3-vl", "qwen3:")
    )


def _model_supports_deep_thinking(model_id: str) -> bool:
    lowered = str(model_id or "").strip().lower()
    return _is_qwen35_hybrid_model(lowered) or any(
        token in lowered for token in ("thinking", "deepseek-r1", "qwq", "reason", "gpt-oss")
    )


def _is_mtp_model(model_id: str) -> bool:
    return bool(re.search(r"(?:^|[\s\-_.:/])mtp(?:$|[\s\-_.:/])", str(model_id or ""), re.IGNORECASE))


_CASUAL_OR_MIC_RE = re.compile(
    r"\b(?:can you hear me|do you hear me|hear me|are you listening|listening to me|mic test|test mic|testing mic|alo+|nghe ro khong|nghe thay khong|co nghe khong|ban nghe khong|how are you|how is your day|how's your day|how is it going|how's it going|what's up|whats up|what is your name|who are you|good morning|good afternoon|good evening|ban ten la gi|ban la ai|ban khoe khong|hom nay the nao)\b"
)
def resolve_auto_thinking_detail(
    prompt: str, options: dict | None = None, route_meta: dict | None = None
) -> dict:
    """Adaptively classify prompt complexity via local mmBERT ONNX router + weighted sigmoid fallback."""
    opts = options if isinstance(options, dict) else {}
    meta = route_meta if isinstance(route_meta, dict) else {}
    import thinking_router

    persist = bool(opts.get("persistRouterState")) and not bool(
        os.environ.get("AQUA_TEST_ENDPOINT")
        or os.environ.get("AI_FIXTURE_URL")
        or os.environ.get("ALIS_TEST_ISOLATED")
    )
    return thinking_router.route_thinking_effort(prompt, opts, meta, persist_state=persist)


def resolve_auto_thinking_effort(
    prompt: str, options: dict | None = None, route_meta: dict | None = None
) -> str:
    """Adaptively choose thinking effort ('off', 'low', 'medium', 'high', 'max') by prompt complexity & efficiency."""
    res = resolve_auto_thinking_detail(prompt, options, route_meta)
    return str(res.get("effort") or "off")


def classify_prompt_model_role(prompt: str, options: dict | None = None) -> dict:
    """Classify a prompt into 'action' (Desktop UI & Fast Action) vs 'thinking' (Deep Thinking / Domain RAG).

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
    role_label = "Fast Chat"
    category = "fast_chat"
    reason = (
        "Trò chuyện phản hồi nhanh"
        if lang == "vi"
        else "Fast conversational response"
    )
    deep_thinking_match = None
    strong_deep_cue = None
    domain_rag_domains = domain_rag.detect_domains(raw)
    is_domain_cap = domain_rag.is_domain_capability_query(raw)
    is_domain_concept = domain_rag.is_domain_concept_or_command_query(raw)
    is_config_check = domain_rag.is_config_check_query(raw)

    # 1. Explicit slash commands
    if re.match(r"^/(?:swarm|plan|diagnostics|learn|workflow|reflect)\b", folded):
        role = "thinking"
        role_label = "Deep Thinking"
        category = "slash_thinking"
        reason = (
            "Lệnh chuyên sâu (/swarm, /plan, /diagnostics, /learn, /workflow, /reflect) cần suy luận sâu"
            if lang == "vi"
            else "Deep command (/swarm, /plan, /diagnostics, /learn, /workflow, /reflect) requires deep reasoning"
        )
    elif re.match(r"^/rag\b", folded) and not re.match(r"^/rag(?:\s*$|\s+(?:list|add|remove|del|rm|clear)\b)", folded):
        role = "thinking"
        role_label = "Domain RAG"
        category = "rag_synthesis"
        reason = (
            "Tổng hợp tài liệu RAG đa nguồn cần model suy luận sâu"
            if lang == "vi"
            else "Multi-document RAG synthesis requires deep reasoning"
        )
    elif re.match(
        r"^/(?:scan|access|list|ls|dir|tree|open|cd|edit|write|append|create|copy|cp|cut|move|mv|delete|rm|run|exec|mcp|git|system|processes|undo|rollback|permissions|rules|memory|skills?|ponytail|docx|pdf|xlsx|rag)\b",
        folded,
    ):
        role = "action"
        role_label = "UI Action"
        category = "slash_action"
        reason = (
            "Thao tác trực tiếp trên Desktop / MCP / Tệp ưu tiên tốc độ phản hồi"
            if lang == "vi"
            else "Direct Desktop / MCP / File command routed to fast UI Action model"
        )
    else:
        # 2. Check explicit deep reasoning / analysis / architecture / algorithm / debugging cues
        deep_thinking_match = re.search(
            r"\b(?:suy luan|suy nghi sau|phan tich sau|phan tich ky|phan tich nguyen nhan|giai thich chi tiet|giai thich sau|so sanh uu nhuoc|kien truc|thuat toan|toi uu hoa|debug sau|go loi|tim nguyen nhan|root cause|deadlock|race condition|lock-free|concurrency|de quy|quy hoach dong|dynamic programming|len ke hoach|lap ke hoach|ke hoach chi tiet|create a plan|make a plan|build a plan|help me plan|step by step|tung buoc|chung minh|danh gia kien truc|thiet ke he thong|system design|architecture|algorithm|complexity|big-o|trade-off|tradeoff|deep dive|deep thinking|reason about|analyze deeply|explain why|under the hood|multi-step plan|refactor strategy|triet hoc|philosophy|philosophical|sang tao sau|out of the box|lateral thinking)\b",
            folded,
        ) or re.search(
            r"(?:^(?:tai sao|vi sao|why does|why is|how should we architect|phan tich|analyze|design a|hay phan tich|hay giai thich sau|hay lap ke hoach)|(?:^|\b(?:please|can you|could you|help me|i want to|i need to|we need to|lets|let's|and)\s+)plan\s+(?:a|an|my|the|our|for))\b",
            folded,
        )
        ui_action_match = re.search(
            r"\b(?:click|nhap chuot|bam|keo tha|scroll|cuon trang|go phim|type text|focus window|dong cua so|close window|chuyen workspace|switch workspace|mo ung dung|launch app|open app|am luong|volume|mute|unmute|do sang|brightness|clipboard|chup man hinh|screen\s*shot|ocr|tren man hinh|xem man hinh|nhin man hinh|thay man hinh|trong anh chup|trong buc anh|mo ta anh chup|mo ta buc anh|on screen|on my screen|on the screen|see my screen|see the screen|what do you see|what can you see|what you can see|describe\s+(?:my\s+|the\s+)?screen(?:\s*shot)?|cua so dang mo|active window|computer-use|playwright|devtools|desktop-commander)\b",
            folded,
        )
        strong_deep_cue = re.search(
            r"\b(?:explain why|why does|why is|tai sao|vi sao|phan tich sau|phan tich nguyen nhan|kien truc|thuat toan|algorithm|architecture|complexity|big-o|root cause|tim nguyen nhan|go loi|debug sau|deadlock|race condition|lock-free|step by step|tung buoc|system design|thiet ke he thong|triet hoc|philosophy|sang tao sau|out of the box|lateral thinking)\b",
            folded,
        )
        local_screen_or_svc = bool(
            _match_screen_observe_intent(folded, lang)
            or alis_local_bridge.detect_all_alis_local_intents(raw)
        )
        has_local_or_screen_ui = bool(ui_action_match or local_screen_or_svc)
        hybrid_ui_thinking = bool(has_local_or_screen_ui and (deep_thinking_match or strong_deep_cue))

        if hybrid_ui_thinking:
            role = "thinking"
            role_label = "UI + Deep Thinking"
            category = "hybrid_ui_thinking"
            reason = (
                "Kết hợp hai pha: thu thập/thao tác UI-Desktop (Pha 1) và suy luận Deep Thinking (Pha 2)"
                if lang == "vi"
                else "Two-stage pipeline: Desktop/UI execution (Stage 1) followed by Deep Thinking synthesis (Stage 2)"
            )
        elif deep_thinking_match and (not ui_action_match or strong_deep_cue):
            role = "thinking"
            role_label = "Domain RAG" if domain_rag_domains else "Deep Thinking"
            category = "deep_reasoning"
            reason = (
                "Yêu cầu phân tích sâu, thuật toán, kiến trúc hoặc lập kế hoạch"
                if lang == "vi"
                else "Prompt requests deep analysis, architecture, algorithm design, or planning"
            )
        elif (is_domain_cap or is_domain_concept or is_config_check or domain_rag_domains) and not ui_action_match:
            role = "thinking"
            role_label = "Domain RAG"
            category = "domain_rag"
            reason = (
                "Tra cứu Hệ thống Domain RAG 3 tầng (Niri / Quickshell / Arch Linux)"
                if lang == "vi"
                else "3-Stage Domain RAG lookup (Niri / Quickshell / Arch Linux)"
            )
        elif ui_action_match and not deep_thinking_match:
            role = "action"
            role_label = "UI Action"
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
            if kind == "rag_query" and intent_info.get("subkind") in ("domain_capability", "domain_concepts", "check_config"):
                role = "thinking"
                role_label = "Domain RAG"
                category = "domain_rag"
                reason = (
                    "Tra cứu Hệ thống Domain RAG 3 tầng (Niri / Quickshell / Arch Linux)"
                    if lang == "vi"
                    else "3-Stage Domain RAG lookup (Niri / Quickshell / Arch Linux)"
                )
            elif kind in ("plan_and_code", "self_learn") or (
                kind == "rag_manage" and intent_info.get("subkind") == "query"
            ):
                role = "thinking"
                role_label = "Deep Thinking"
                category = kind
                reason = (
                    "Tác vụ lập kế hoạch mã nguồn, tự học hoặc truy vấn RAG chuyên sâu"
                    if lang == "vi"
                    else "Code planning, self-learning, or RAG synthesis task"
                )
            elif kind in (
                "screen_observe",
                "screen_inspect",
                "gui_action",
                "desktop_control",
                "system_control",
                "file_task",
                "shell_task",
                "git_task",
                "mcp_manage",
                "web_task",
                "voice_task",
                "permission_manage",
                "rule_manage",
                "memory_manage",
                "pocket_manage",
                "skill_manage",
                "confirmation_response",
                "stop",
                "resume",
                "alis_local_info",
                "alis_service_control",
            ):
                role = "action"
                role_label = "UI Action"
                category = kind
                reason = (
                    "Thao tác hệ thống / tệp / giao diện trực tiếp cần phản hồi tức thì"
                    if lang == "vi"
                    else "Direct desktop, file, or system action routed for instant execution"
                )
            elif kind == "nexus_knowledge":
                role = "thinking"
                role_label = "Domain RAG"
                category = "nexus_knowledge"
                reason = (
                    "Tra cứu và tổng hợp tri thức từ Nexus Vault"
                    if lang == "vi"
                    else "Query and synthesize knowledge from Nexus Vault"
                )
            elif (
                not _CASUAL_OR_MIC_RE.search(folded)
                and (
                    len(raw) >= 140
                    or (not ui_action_match and user_effort_hint in ("high", "xhigh", "max"))
                )
                and (
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
                )
            ):
                role = "thinking"
                role_label = "Deep Thinking"
                category = "complex_analysis"
                reason = (
                    "Câu hỏi phân tích hoặc gỡ lỗi chuyên sâu cần suy luận chi tiết"
                    if lang == "vi"
                    else "Analytical or debugging question routed to Deep Thinking model"
                )
            elif (
                re.search(
                    r"\b(?:what is|what are|how does|how do|how to|can you explain|explain|difference between|compare|do you know|do you have|la gi|nhu the nao|lam sao|giai thich|so sanh|co biet|kien thuc)\b",
                    folded,
                )
                and not _CASUAL_OR_MIC_RE.search(folded)
            ):
                role = "thinking"
                role_label = "Deep Thinking"
                category = "knowledge_qa"
                reason = (
                    "Câu hỏi kiến thức & giải thích kỹ thuật"
                    if lang == "vi"
                    else "Knowledge & technical explanation query"
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
    if user_effort not in ("auto", "off", "instant", "low", "medium", "high", "xhigh", "max"):
        user_effort = "off"

    dual_model_active = bool(auto_enabled and action_model and thinking_model and action_model != thinking_model)
    hybrid_ui_thinking = (category == "hybrid_ui_thinking")
    is_light_knowledge = bool(
        category in ("domain_rag", "knowledge_qa")
        and not strong_deep_cue
        and not deep_thinking_match
    )

    auto_router_detail: dict | None = None
    if user_effort == "auto":
        auto_router_detail = resolve_auto_thinking_detail(
            raw,
            opts,
            {
                "role": role,
                "category": category,
                "is_domain_cap": bool(is_domain_cap or is_domain_concept or is_config_check),
            },
        )
        auto_effort = str(auto_router_detail.get("effort") or "off")
        if not auto_enabled:
            selected_model = base_model
            selected_endpoint = base_endpoint
            recommended_effort = auto_effort
        elif role == "thinking":
            selected_model = thinking_model
            selected_endpoint = thinking_endpoint
            recommended_effort = auto_effort
        else:
            selected_model = action_model
            selected_endpoint = action_endpoint
            recommended_effort = "off" if (dual_model_active or auto_effort == "off") else auto_effort
    elif not auto_enabled:
        selected_model = base_model
        selected_endpoint = base_endpoint
        recommended_effort = user_effort
    elif role == "thinking":
        selected_model = thinking_model
        selected_endpoint = thinking_endpoint
        if is_domain_cap or is_domain_concept or is_config_check:
            recommended_effort = "off"
        elif is_light_knowledge:
            recommended_effort = "low" if user_effort in ("medium", "high", "xhigh", "max") else (user_effort if user_effort != "off" else "low")
        else:
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
        tags = thinking_router.PLUSIQ_REASON_TAGS
        if user_effort == "auto":
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
            ) and recommended_effort in ("high", "max"):
                in_chat_directive = tags["spoon"]
            elif recommended_effort == "max":
                in_chat_directive = tags["max"]
            elif recommended_effort == "high":
                in_chat_directive = tags["thinking_high"]
            elif recommended_effort == "medium":
                in_chat_directive = tags["thinking_medium"]
            elif recommended_effort == "low":
                in_chat_directive = tags["thinking_low"]
            else:
                in_chat_directive = tags["medium"]
        elif role == "action" or is_light_knowledge:
            in_chat_directive = tags["xhigh"] if (role == "action" and user_effort in ("xhigh", "max")) else tags["medium"]
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
                in_chat_directive = tags["spoon"]
            elif user_effort == "max" or any(
                k in folded for k in ("einstein", "cuc sau", "deepest", "exhaustive", "toan dien")
            ):
                in_chat_directive = tags["max"]
            else:
                in_chat_directive = tags["thinking_high"]
    else:
        in_chat_directive = ""

    enable_thinking = (
        (recommended_effort not in ("off", "instant"))
        if user_effort == "auto"
        else (
            (role == "thinking" and not is_light_knowledge)
            if auto_enabled
            else (recommended_effort not in ("off", "instant"))
        )
    )
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
        "temperature": (
            round(max(0.0, min(2.0, float(opts["temperature"]))), 2)
            if isinstance(opts.get("temperature"), (int, float)) and opts.get("hasTemperatureOverride") is True
            else (0.7 if mtp_active else None)
        ),
        "repetitionPenalty": 1.0 if mtp_active else None,
        "recommendedTemperature": 0.7 if mtp_active else 0.65,
        "recommendedRepetitionPenalty": 1.0 if mtp_active else None,
        "inChatDirective": in_chat_directive,
        "autoRouterSource": str(auto_router_detail.get("source") or "") if auto_router_detail else "",
        "autoRouterScore": float(auto_router_detail.get("score") or 0.0) if auto_router_detail else 0.0,
        "autoRouterTask": str(auto_router_detail.get("task") or "") if auto_router_detail else "",
        "autoRouterConfidence": float(auto_router_detail.get("confidence") or 0.0) if auto_router_detail else 0.0,
        "hybridUiThinking": bool(hybrid_ui_thinking),
        "hybridStages": (
            {
                "uiModel": action_model,
                "uiEndpoint": action_endpoint,
                "uiEffort": "off",
                "thinkingModel": thinking_model,
                "thinkingEndpoint": thinking_endpoint,
                "thinkingEffort": recommended_effort if recommended_effort not in ("off", "instant") else "medium",
            }
            if hybrid_ui_thinking
            else None
        ),
    }


classify_task_type = classify_prompt_model_role


def _call_local_model_tool_turn(
    endpoint_url: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    enable_thinking: bool = False,
    in_chat_directive: str = "",
    mtp_active: bool | None = None,
    messages: list[dict] | None = None,
    temperature: float | None = None,
    tools: list[dict] | None = None,
    provider: str = "",
    context_window: int | None = None,
    turn_idx: int = 0,
) -> dict | None:
    """Optional structured tool-call query against the active local OpenAI/Ollama endpoint.

    Strictly parses structured `tool_calls` from the API response object; never
    executes JSON or commands found inside plain assistant prose.
    """
    if not endpoint_url or not model_name:
        return None
    if not (endpoint_url.startswith("http://127.0.0.1:") or endpoint_url.startswith("http://localhost:")):
        return None
    if not _ensure_lmstudio_runtime_ready(endpoint_url, model_name):
        return None
    effective_model_name = _tctx().last_lmstudio_loaded_model or model_name
    effective_user_prompt = user_prompt[:1800]
    if in_chat_directive and in_chat_directive not in effective_user_prompt:
        effective_user_prompt = f"{in_chat_directive}\n{effective_user_prompt}"
    effective_mtp = _is_mtp_model(effective_model_name) if mtp_active is None else bool(mtp_active)
    eff_temp = (
        round(max(0.0, min(2.0, float(temperature))), 2)
        if isinstance(temperature, (int, float))
        else (0.7 if effective_mtp else 0.1)
    )
    is_live_lms = (
        ":1234" in endpoint_url
        and not os.environ.get("ALIS_TEST_ISOLATED")
        and not os.environ.get("AQUA_TEST_ENDPOINT")
        and not os.environ.get("AI_FIXTURE_URL")
    )
    t0 = time.time()
    watchdog_stop = _start_lmstudio_gtt_watchdog() if is_live_lms else None
    try:
        if "/v1/" in endpoint_url:
            active_schemas = tools if isinstance(tools, list) else AGENT_TOOL_SCHEMAS
            if isinstance(messages, list) and messages:
                req_messages, _ = context_policy.compact_messages_for_route(
                    messages,
                    provider=provider,
                    model=effective_model_name,
                    endpoint=endpoint_url,
                    reserved_output=2048 if enable_thinking else 1536,
                    context_window=context_window,
                    tools=active_schemas,
                )
            else:
                req_messages = [
                    {"role": "system", "content": system_prompt[:2400]},
                    {"role": "user", "content": effective_user_prompt[:1860]},
                ]
            body = {
                "model": effective_model_name,
                "messages": req_messages,
                "tools": active_schemas,
                "tool_choice": "auto",
                "temperature": eff_temp,
                "max_tokens": 2048 if enable_thinking else 1536,
                "reasoning_effort": "high" if enable_thinking else "none",
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
            req_timeout = 90 if is_live_lms else 25
            with _urlopen_local(req, timeout=req_timeout) as resp:
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
                        "toolCallId": str(tc.get("id") or f"call_t{int(turn_idx)}_{len(tool_calls)+1}"),
                        "name": name,
                        "args": args,
                    }
                    if args_error:
                        tc_entry["argsError"] = args_error
                    tool_calls.append(tc_entry)
            raw_text = str(msg.get("content") or "").strip()
            raw_text = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", raw_text, flags=re.IGNORECASE).strip()
            return {
                "text": raw_text,
                "toolCalls": tool_calls,
                "rawMessage": msg,
                "tokenUsage": {
                    "inputTokens": int(usage.get("prompt_tokens") or 0),
                    "outputTokens": int(usage.get("completion_tokens") or 0),
                },
            }
    except Exception:
        if is_live_lms and not _tctx().last_lmstudio_error:
            log_err = _check_lmstudio_log_recent_fit_or_oom_error(since_ts=t0 - 1.0)
            if log_err == "gpu_oom":
                _tctx().last_lmstudio_error = log_err
        return None
    finally:
        if watchdog_stop is not None:
            watchdog_stop.set()
    return None


def _check_file_syntax_valid(fpath: Path) -> tuple[bool, str]:
    """Check whether a Python or JSON file is syntactically valid."""
    if not fpath.is_file():
        return True, ""
    ext = fpath.suffix.lower()
    try:
        src = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True, ""
    if ext == ".py":
        import ast
        try:
            ast.parse(src, filename=str(fpath))
            return True, ""
        except SyntaxError as exc:
            return False, f"SyntaxError at line {exc.lineno}: {exc.msg}"
    if ext == ".json":
        try:
            json.loads(src)
            return True, ""
        except Exception as exc:
            return False, f"JSONDecodeError: {exc}"
    return True, ""


def _detect_cross_component_impact(target_path: str, new_content: str = "") -> list[dict]:
    """Detect whether symbols defined in `target_path` (or removed by `new_content`) are referenced by sibling code files."""
    fpath = Path(target_path)
    code_exts = (".py", ".qml", ".js", ".ts", ".cjs", ".mjs")
    if not fpath.is_file() or fpath.suffix.lower() not in code_exts:
        return []
    try:
        old_src = fpath.read_text(encoding="utf-8", errors="replace")[:32000]
    except OSError:
        return []
    old_syms = [
        s
        for s in re.findall(r"(?:^|\n)\s*(?:def|class|function)\s+([A-Za-z][A-Za-z0-9_]{2,})\b", old_src)
        if not s.startswith("_") and s not in ("main", "init", "run", "test")
    ]
    if new_content:
        cand_syms = [s for s in old_syms if not re.search(rf"\b{re.escape(s)}\b", new_content)]
    else:
        cand_syms = list(dict.fromkeys(old_syms))[:12]
    if not cand_syms:
        return []
    impacts: list[dict] = []
    try:
        siblings = sorted(
            p for p in fpath.parent.iterdir()
            if p.is_file() and p != fpath and p.suffix.lower() in code_exts
        )[:16]
    except OSError:
        return []
    for sib in siblings:
        try:
            sib_src = sib.read_text(encoding="utf-8", errors="replace")[:32000]
        except OSError:
            continue
        matched = [sym for sym in cand_syms if re.search(rf"\b{re.escape(sym)}\b", sib_src)]
        if matched:
            impacts.append({"file": str(sib), "name": sib.name, "symbols": matched[:4]})
    return impacts


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
    active_sid = str(
        (_tctx().current_turn_thread_id or "")
        or session_ctx.get("activeThreadId")
        or ((session_ctx.get("_activeTurnOptions") or {}).get("threadId") if isinstance(session_ctx.get("_activeTurnOptions"), dict) else "")
        or "chat_default"
    ).strip() or "chat_default"
    tc_id = str(tc.get("toolCallId") or tc.get("id") or "")

    if name == "read_spill":
        try:
            sp_res = read_spill_tool({
                "sessionId": active_sid,
                "spillId": args.get("spill_id") or args.get("spillId") or "",
                "path": args.get("path") or "",
                "offsetLine": args.get("offset_line", args.get("offsetLine", 1)),
                "limitLines": args.get("limit_lines", args.get("limitLines", 120)),
                "grep": args.get("grep") or "",
            })
            return str(sp_res.get("content") or "")
        except ToolError as exc:
            return f"[read_spill error ({exc.code}): {exc.message}]"

    if name in ("file_inspect", "read_file"):
        target = str(args.get("path") or args.get("targetPath") or args.get("file") or last_dir)
        resolved = str(resolve_candidate_path(target, last_dir=last_dir))
        if is_sensitive_path(target) or is_sensitive_path(resolved):
            return f"[{name} blocked: sensitive path {resolved}]"
        perm = check_permission("file_read", target_path=resolved, source="agent_step", now_ms=now_ms)
        if not perm["allowed"]:
            return f"[{name} blocked: {perm['reason']} on {resolved}]"
        op_opts: dict = {
            "op": "access",
            "targetPath": resolved,
            "lastDirectory": last_dir,
            "threadId": active_sid,
            "callId": tc_id,
            "nowMs": now_ms,
        }
        if args.get("startLine") is not None or args.get("start_line") is not None:
            op_opts["startLine"] = args.get("startLine", args.get("start_line"))
        if args.get("endLine") is not None or args.get("end_line") is not None:
            op_opts["endLine"] = args.get("endLine", args.get("end_line"))
        res = agent_file_op(op_opts)
        if res.get("spilled") and res.get("spillPreview"):
            return f"{res['summary']}\n{res['spillPreview']}"
        joined = f"{res['summary']}\n" + "\n".join(res.get("lines", []))
        return spill_store.maybe_spill_tool_output(joined, session_id=active_sid, call_id=tc_id, tool_name=name)[0]

    if name in ("file_mutate", "edit_file", "write_file"):
        op = str(args.get("op") or "edit").strip().lower()
        if op not in {"edit", "write", "create", "patch", "append", "replace_lines", "apply_patch", "delete", "copy", "cut", "move"}:
            return f"[{name} blocked: unsupported operation '{op}']"
        target = str(args.get("path") or args.get("targetPath") or args.get("file") or "")
        if not target.strip():
            return f"[{name} error: missing target path]"
        resolved = str(resolve_candidate_path(target, last_dir=last_dir))
        if is_sensitive_path(target) or is_sensitive_path(resolved):
            return f"[{name} blocked: sensitive path {resolved}]"
        perm_cat = "file_delete" if op == "delete" else "file_write"
        perm = check_permission(perm_cat, target_path=resolved, source="agent_step", now_ms=now_ms)
        if not perm["allowed"]:
            return f"[{name} blocked: {perm['reason']} on {resolved}]"
        dest_raw = str(args.get("destPath") or "").strip()
        if op in ("copy", "cut", "move") and dest_raw:
            dest_resolved = str(resolve_candidate_path(dest_raw, last_dir=last_dir))
            if is_sensitive_path(dest_raw) or is_sensitive_path(dest_resolved):
                return f"[{name} blocked: sensitive destination path {dest_resolved}]"
            dest_perm = check_permission("file_write", target_path=dest_resolved, source="agent_step", now_ms=now_ms)
            if not dest_perm["allowed"]:
                return f"[{name} blocked: {dest_perm['reason']} on {dest_resolved}]"
        resolved_p = Path(resolved)
        existed_before = resolved_p.is_file()
        was_valid_syntax, _ = _check_file_syntax_valid(resolved_p) if existed_before else (True, "")
        res = agent_file_op({
            "op": op,
            "targetPath": resolved,
            "destPath": str(args.get("destPath") or ""),
            "content": str(args.get("content") or args.get("text") or ""),
            "searchText": str(args.get("searchText") or ""),
            "mode": str(args.get("mode") or "write"),
            "lastDirectory": last_dir,
            "nowMs": now_ms,
        })
        if op in ("edit", "patch") and existed_before:
            rcpt_id = str(res.get("receiptId") or "")
            added_lines = int(res.get("addedLines") or 0)
            removed_lines = int(res.get("removedLines") or 0)
            now_valid_syntax, syn_err = _check_file_syntax_valid(resolved_p)
            if rcpt_id and was_valid_syntax and not now_valid_syntax:
                try:
                    rollback_file_receipt(rcpt_id)
                    return f"[{name} reverted: edit introduced syntax error ({syn_err}) in {resolved_p.name}; original file restored]"
                except Exception:
                    pass
            if rcpt_id and added_lines == 0 and removed_lines == 0:
                try:
                    rollback_file_receipt(rcpt_id)
                    return f"[{name} reverted: edit on {resolved_p.name} produced no effective diff (0 lines changed)]"
                except Exception:
                    pass
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:20])

    if name in ("create_document", "convert_document"):
        src_raw = str(args.get("sourcePath") or args.get("srcPath") or args.get("source") or "")
        dst_raw = str(args.get("destPath") or args.get("targetPath") or args.get("outputPath") or args.get("path") or "")
        if not dst_raw:
            return f"[{name} error: missing destination path]"
        dst_resolved = str(resolve_candidate_path(dst_raw, last_dir=last_dir))
        if is_sensitive_path(dst_raw) or is_sensitive_path(dst_resolved):
            return f"[{name} blocked: sensitive path {dst_resolved}]"
        perm_w = check_permission("file_write", target_path=dst_resolved, source="agent_step", now_ms=now_ms)
        if not perm_w["allowed"]:
            return f"[{name} blocked: {perm_w['reason']} on {dst_resolved}]"
        if name == "create_document" or not src_raw or src_raw == dst_raw:
            body_txt = str(args.get("content") or args.get("text") or "")
            edited = agent_file_op({
                "op": "edit",
                "targetPath": dst_resolved,
                "content": body_txt,
                "mode": "write",
                "lastDirectory": last_dir,
                "nowMs": now_ms,
            })
            conv_meta = convert_or_create_document(dst_resolved, dst_resolved)
            return f"{edited['summary']} ({conv_meta.get('bytes', 0)} bytes)"
        src_resolved = str(resolve_candidate_path(src_raw, last_dir=last_dir))
        if is_sensitive_path(src_raw) or is_sensitive_path(src_resolved):
            return f"[{name} blocked: sensitive source path {src_resolved}]"
        perm_r = check_permission("file_read", target_path=src_resolved, source="agent_step", now_ms=now_ms)
        if not perm_r["allowed"]:
            return f"[{name} blocked: {perm_r['reason']} on {src_resolved}]"
        src_info = code_tools.read_file(src_resolved, max_chars=200000)
        edited = agent_file_op({
            "op": "edit",
            "targetPath": dst_resolved,
            "content": str(src_info.get("text") or ""),
            "mode": "write",
            "lastDirectory": last_dir,
            "nowMs": now_ms,
        })
        conv_meta = convert_or_create_document(src_resolved, dst_resolved)
        return f"Converted `{Path(src_resolved).name}` to `{Path(dst_resolved).name}` (`{edited['targetPath']}`, {conv_meta.get('bytes', 0)} bytes)."

    if name in ("shell_run", "run_command"):
        cmd = str(args.get("command") or args.get("cmd") or "").strip()
        cwd = str(args.get("cwd") or last_dir)
        if is_sensitive_path(cwd):
            return f"[{name} blocked: sensitive working directory {cwd}]"
        perm = check_permission("shell_run", target_path=cwd, explicit_prompt_target=False, source="agent_step", now_ms=now_ms)
        if not perm["allowed"]:
            return f"[{name} blocked: {perm['reason']} in {cwd}]"
        res = agent_file_op({
            "op": "run",
            "targetPath": cmd,
            "content": cmd,
            "lastDirectory": cwd,
            "threadId": active_sid,
            "callId": tc_id,
            "nowMs": now_ms,
        })
        if res.get("spilled") and res.get("spillPreview"):
            return f"{res['summary']}\n{res['spillPreview']}"
        joined = f"{res['summary']}\n" + "\n".join(res.get("lines", []))
        return spill_store.maybe_spill_tool_output(joined, session_id=active_sid, call_id=tc_id, tool_name=name)[0]

    if name == "file_receipt_rollback":
        res = agent_file_op({
            "op": "rollback",
            "receiptId": str(args.get("receiptId") or ""),
            "nowMs": now_ms,
        })
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:10])

    if name == "voice_tts_stop":
        res = desktop_control.voice_tts_stop()
        return str(res.get("summary") or "Stopped TTS playback.")

    if name == "desktop_control":
        act_dc = str(args.get("action") or args.get("action_type") or "list_windows").strip()
        if act_dc == "launch_app":
            perm_launch = check_permission("shell_run", source="agent_step", now_ms=now_ms)
            if not perm_launch["allowed"]:
                return f"[desktop_control blocked: {perm_launch['reason']} for launch_app]"
        if act_dc in (
            "gui_click",
            "gui_fill",
            "gui_focus",
            "atspi_action",
            "som_click",
            "click",
            "fill",
            "set_text",
            "focus_element",
            "move",
        ):
            mapped_act = (
                "fill"
                if act_dc in ("gui_fill", "fill", "set_text")
                else (
                    "focus_element"
                    if act_dc in ("gui_focus", "focus_element")
                    else (
                        "atspi_action"
                        if act_dc == "atspi_action"
                        else ("move" if act_dc == "move" else ("click" if act_dc == "click" else "som_click"))
                    )
                )
            )
            gui_res = perform_gui_action(
                action_type=mapped_act,
                x=int(args["x"]) if args.get("x") is not None else None,
                y=int(args["y"]) if args.get("y") is not None else None,
                text=str(args.get("value") or args.get("text") or ""),
                mark_id=args.get("markId") if args.get("markId") is not None else args.get("mark_id"),
                element_name=str(args.get("target") or args.get("element") or args.get("element_name") or ""),
                role=str(args.get("role") or ""),
                ref=str(args.get("ref") or ""),
                now_ms=now_ms,
            )
            if isinstance(gui_res.get("cursorEvent"), dict):
                session_ctx["_lastCursorEvent"] = gui_res["cursorEvent"]
            return str(gui_res.get("summary") or "Completed GUI action.")
        res = desktop_control.manage_desktop(
            action=act_dc,
            target=str(args.get("target") or ""),
            value=str(args.get("value") or ""),
        )
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:25])

    if name in ("gui_action", "screen_observe"):
        if name == "screen_observe":
            explicit_vision = bool(
                args.get("explicitVision")
                or args.get("annotateSom")
                or args.get("som")
            )
            obs_r = observe_active_window(
                allow_screenshot=bool(args.get("allowScreenshot", False) and explicit_vision),
                annotate_som=bool(args.get("annotateSom", False) or args.get("som", False)),
                now_ms=now_ms,
            )
            if not (obs_r.get("elements") or []) and (args.get("allowScreenshot") is not False):
                obs_r = observe_active_window(
                    allow_screenshot=True,
                    annotate_som=True,
                    now_ms=now_ms,
                )
            return str(obs_r.get("semanticSnapshot") or format_semantic_snapshot(obs_r.get("elements") or []))
        gui_res = perform_gui_action(
            action_type=str(args.get("action") or args.get("action_type") or "click"),
            x=int(args["x"]) if args.get("x") is not None else None,
            y=int(args["y"]) if args.get("y") is not None else None,
            text=str(args.get("text") or args.get("value") or ""),
            mark_id=args.get("markId") if args.get("markId") is not None else args.get("mark_id"),
            element_name=str(args.get("element") or args.get("element_name") or args.get("target") or ""),
            role=str(args.get("role") or ""),
            ref=str(args.get("ref") or ""),
            now_ms=now_ms,
        )
        if isinstance(gui_res.get("cursorEvent"), dict):
            session_ctx["_lastCursorEvent"] = gui_res["cursorEvent"]
        return str(gui_res.get("summary") or "Completed GUI action.")

    if name in ("system_telemetry", "system_status"):
        res = desktop_control.get_system_telemetry()
        return f"{res['summary']}\n" + "\n".join(res.get("lines", [])[:25])

    if name == "inspect_user_configuration":
        q_cfg = str(args.get("query") or args.get("prompt") or "inspect user configuration").strip()
        return str(domain_rag.inspect_user_configuration(q_cfg, lang=detect_language(q_cfg), companion_root=companion_root) or "")[:1200]

    if name == "domain_rag":
        q_dom = str(args.get("query") or args.get("prompt") or "").strip()
        doms = args.get("domains") if isinstance(args.get("domains"), list) else domain_rag.detect_domains(q_dom)
        drag = domain_rag.query_domain_rag(
            q_dom,
            domains=doms or None,
            companion_root=companion_root,
            token_budget=int(args.get("tokenBudget") or 400),
        )
        ctx_md = str(drag.get("contextMarkdown") or "").strip()
        if ctx_md:
            return ctx_md[:1200]
        chunks = (
            (drag.get("canonicalChunks") or []) + (drag.get("personalChunks") or [])
            or (drag.get("chunks") or [])
        )
        return "\n".join(
            f"- {c.get('title') or c.get('source')}: {str(c.get('content') or '')[:240]}"
            for c in chunks[:5]
        ) or f"Domain RAG queried ({', '.join(doms or ['general'])}): 0 chunks matched."

    if name == "process_manage":
        proc_act = str(args.get("action") or "list").strip().lower()
        if proc_act == "kill":
            perm_proc = check_permission("shell_run", source="agent_step", now_ms=now_ms)
            if not perm_proc["allowed"]:
                return f"[process_manage blocked: {perm_proc['reason']} for kill]"
        res = desktop_control.manage_processes(
            action=proc_act,
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
        raw_git = f"{res['summary']}\n" + "\n".join(res.get("lines", []))
        return spill_store.maybe_spill_tool_output(raw_git, session_id=active_sid, call_id=tc_id, tool_name=name)[0]

    if name in ("code_inspect", "code_search"):
        ws = str(args.get("workspace") or args.get("path") or session_ctx.get("lastProject") or last_dir)
        act = str(args.get("action") or ("search" if name == "code_search" else "repo_map"))
        q_code = str(args.get("query") or args.get("pattern") or "").strip()
        if act == "repo_map" or (name == "code_search" and not q_code):
            rm = build_repo_map(ws, token_budget=360)
            return "\n".join(e["summary"] for e in rm.get("entries", [])[:16])
        if act == "run_tests":
            tr = run_project_tests(ws)
            raw_tr = "\n".join(tr.get("lines", []))
            return spill_store.maybe_spill_tool_output(raw_tr, session_id=active_sid, call_id=tc_id, tool_name=name)[0]
        sr = search_code(ws, q_code)
        matches = sr.get("matches", [])
        if matches:
            raw_sr = "\n".join(f"{m['file']}:{m['line']}: {m['text']}" for m in matches)
            return spill_store.maybe_spill_tool_output(raw_sr, session_id=active_sid, call_id=tc_id, tool_name=name)[0]
        rm = build_repo_map(ws, token_budget=240)
        return "\n".join(e["summary"] for e in rm.get("entries", [])[:12])

    if name == "rag_search":
        rag = retrieve_rag(str(args.get("query") or ""), companion_root=companion_root, page=int(args.get("page") or 1))
        raw_rag = "\n".join(rag.get("snippets", []))
        return spill_store.maybe_spill_tool_output(raw_rag, session_id=active_sid, call_id=tc_id, tool_name=name)[0]

    if name == "mcp_call":
        mcp_tool_name = str(args.get("tool") or "").strip()
        if mcp_tool_name in ("shell_exec", "execute_command"):
            perm_mcp = check_permission("shell_run", source="agent_step", now_ms=now_ms)
            if not perm_mcp["allowed"]:
                return f"[mcp_call blocked: {perm_mcp['reason']} for {mcp_tool_name}]"
        called = mcp_client.call_tool(
            str(args.get("server") or ""),
            mcp_tool_name,
            args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
            companion_root=companion_root,
        )
        if isinstance((called.get("structured") or {}).get("cursorEvent"), dict):
            session_ctx["_lastCursorEvent"] = called["structured"]["cursorEvent"]
        raw_mcp = "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))
        return spill_store.maybe_spill_tool_output(raw_mcp, session_id=active_sid, call_id=tc_id, tool_name=name)[0]

    if name == "web_search":
        open_br = bool(args.get("openBrowser", False))
        srv = "playwright-mcp" if open_br else "web-search-mcp"
        called = mcp_client.call_tool(
            srv,
            "browser_search" if open_br else "web_search",
            {
                "query": str(args.get("query") or ""),
                "engine": str(args.get("engine") or "auto"),
                "openBrowser": open_br,
            },
            companion_root=companion_root,
            enabled_overrides={srv: True},
        )
        base_out = "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))
        if not open_br and os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1" and not os.environ.get("AQUA_TEST_ENDPOINT"):
            w_res = (called.get("structured") or {}).get("results") or []
            page_blocks: list[str] = []
            for r in w_res[:2]:
                u = str(r.get("url") or "").strip()
                if not u.startswith(("http://", "https://")):
                    continue
                try:
                    pg = web_tools.fetch_url_excerpt(u, max_chars=1400, timeout_sec=3.5)
                    p_lines = [str(ln).strip() for ln in (pg.get("lines") or [])[:8] if str(ln).strip()]
                    if p_lines:
                        dom = web_tools.short_source_domain(u)
                        page_blocks.append(f"### Webpage ({dom} — {u})\n" + "\n".join(f"- {ln[:200]}" for ln in p_lines))
                except Exception:
                    pass
            if page_blocks:
                base_out = f"{base_out}\n\n" + "\n\n".join(page_blocks)
        return base_out

    if name == "web_fetch":
        called = mcp_client.call_tool(
            "fetch-mcp",
            "fetch_url",
            {
                "url": str(args.get("url") or ""),
                "maxChars": int(args.get("maxChars") or 3600),
            },
            companion_root=companion_root,
            enabled_overrides={"fetch-mcp": True},
        )
        raw_fetch = "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))
        return spill_store.maybe_spill_tool_output(raw_fetch, session_id=active_sid, call_id=tc_id, tool_name=name)[0]

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

    if name == "delegate_subagent":
        turn_opts = session_ctx.get("_activeTurnOptions") if isinstance(session_ctx.get("_activeTurnOptions"), dict) else {}
        sub_res = delegate_subagent(
            role=str(args.get("role") or "researcher"),
            task=str(args.get("task") or ""),
            context_hint=str(args.get("context_hint") or args.get("contextHint") or ""),
            options=turn_opts,
            session_ctx=session_ctx,
            companion_root=companion_root,
            now_ms=now_ms,
        )
        return str(sub_res.get("summary") or "")[:_SUBAGENT_MAX_SUMMARY_CHARS]

    if name == "alis_live_state":
        snap = _tctx().current_turn_live_snapshot or alis_local_bridge.load_live_snapshot()
        topic = str(args.get("topic") or "all").strip().lower()
        q = str(args.get("query") or "")
        out = alis_local_bridge.format_live_state(snap, topic=topic, query=q, lang=detect_language(q or "vi"))
        return str(out.get("markdown") or "")[:3000]

    if name == "nexus_knowledge":
        act = str(args.get("action") or "search").strip().lower()
        v_override = _tctx().current_turn_nexus_vault
        q = str(args.get("query") or "")
        if act == "read":
            target_n = str(args.get("path") or args.get("target") or q or "").strip()
            try:
                note = nexus_rag.read_nexus_note(target_n, vault_path=v_override)
                return alis_local_bridge.format_nexus_reply("read", note, query=q, lang=detect_language(q))
            except NexusRagError as exc:
                return f"[nexus_knowledge read error ({exc.code}): {exc.message}]"
        if act in ("overview", "journal", "finance", "collections"):
            ov = nexus_rag.get_nexus_overview(vault_path=v_override, topic=act)
            return alis_local_bridge.format_nexus_reply(act, ov, query=q, lang=detect_language(q))
        if act == "save_rag_note":
            n_title = str(args.get("title") or "").strip() or "Companion RAG Note"
            n_body = str(args.get("content") or q or "").strip()
            cid = _issue_confirm_id("conf")
            session_ctx["pendingClientAction"] = {
                "confirmId": cid,
                "action": "rag_note_save",
                "target": n_title,
                "value": n_body,
                "folder": str(args.get("folder") or "Knowledge"),
                "vaultPath": v_override,
            }
            session_ctx["pendingConfirmId"] = cid
            save_session_context(session_ctx, now_ms=now_ms)
            return f"[Confirmation required (confirmId={cid}) before saving RAG note `{n_title}` to Nexus `90_System/99_RAG/`. Ask the user to confirm.]"
        nx_res = nexus_rag.search_nexus_knowledge(
            q,
            vault_path=v_override,
            folder=str(args.get("folder") or ""),
            tag=str(args.get("tag") or ""),
        )
        return alis_local_bridge.format_nexus_reply("search", nx_res, query=q, lang=detect_language(q))

    if name == "alis_service_control":
        act = str(args.get("action") or "").strip().lower()
        tgt = str(args.get("target") or "").strip()
        val = str(args.get("value") or "").strip()
        if act in alis_local_bridge.SAFE_INSTANT_CLIENT_ACTIONS:
            pending_ca = session_ctx.setdefault("_pendingTurnClientActions", [])
            pending_ca.append({"action": act, "target": tgt, "value": val})
            return f"Queued instant Alis action `{act}` (target=`{tgt}`, value=`{val}`)."
        if act in alis_local_bridge.WRITE_CONFIRM_CLIENT_ACTIONS:
            cid = _issue_confirm_id("conf")
            session_ctx["pendingClientAction"] = {
                "confirmId": cid,
                "action": act,
                "target": tgt,
                "value": val,
                "vaultPath": _tctx().current_turn_nexus_vault,
            }
            session_ctx["pendingConfirmId"] = cid
            save_session_context(session_ctx, now_ms=now_ms)
            return f"[Confirmation required (confirmId={cid}) before executing `{act}` (`{tgt or val}`). Ask the user to confirm.]"
        return f"[alis_service_control error: unsupported action `{act}`]"

    if name == "emit_ui_card":
        if not session_ctx.get("_a2uiGenerativeAllowed"):
            return "[emit_ui_card unavailable: Generative A2UI is disabled in Companion Settings. Reply with Markdown.]"
        raw_surf = args.get("surface") if isinstance(args.get("surface"), dict) else None
        if raw_surf is None and isinstance(args.get("components"), list):
            raw_surf = {
                "version": a2ui_validator.A2UI_VERSION,
                "createSurface": {
                    "surfaceId": _issue_confirm_id("surf_gen"),
                    "catalogId": a2ui_validator.DEFAULT_CATALOG_ID,
                    "sendDataModel": False,
                    "surfaceMeta": {"kind": "generative", "interactive": True},
                    "components": args.get("components"),
                    "dataModel": args.get("dataModel") if isinstance(args.get("dataModel"), dict) else {},
                },
            }
        is_tainted_turn = bool(session_ctx.get("_turnTaintSources") or session_ctx.get("taintSources"))
        validated_surf, v_err = a2ui_validator.validate_and_sanitize_surface(
            raw_surf,
            untrusted_generative=True,
            tainted=is_tainted_turn,
        )
        if v_err or not validated_surf:
            return f"[emit_ui_card rejected ({v_err or 'invalid_surface'}): reply using plain Markdown instead.]"
        session_ctx["_pendingTurnUiSurface"] = validated_surf
        return f"Attached validated A2UI v1.0 card `{validated_surf['createSurface']['surfaceId']}`."

    if name == "tool_search":
        q_ts = str(args.get("query") or args.get("q") or "").strip()
        lim_ts = int(args.get("limit") or 6)
        matched = search_agent_tool_schemas(
            q_ts,
            limit=lim_ts,
            include_generative_ui=bool(session_ctx.get("_a2uiGenerativeAllowed")),
        )
        if not matched:
            return f"No additional tools matched query `{q_ts}`."
        lines_ts: list[str] = []
        for s in matched:
            fn_s = s.get("function") or {}
            t_n = str(fn_s.get("name") or "")
            t_d = str(fn_s.get("description") or "")[:160]
            props_s = list(((fn_s.get("parameters") or {}).get("properties") or {}).keys())
            lines_ts.append(f"- `{t_n}`({', '.join(props_s[:8])}): {t_d}")
        return f"Loaded {len(matched)} tool schema(s):\n" + "\n".join(lines_ts)

    return f"[unknown tool: {name}]"


_SUBAGENT_EXTRA_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file or inspect a directory within permitted scope.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_search",
            "description": "Search workspace source code or build a compact repository map.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "domain_rag",
            "description": "Query the 3-Stage Domain RAG knowledge base (Niri, Quickshell, Arch Linux).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_status",
            "description": "Check Linux system status and telemetry (CPU, RAM, disk, battery, thermals).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_user_configuration",
            "description": "Inspect user Niri, Quickshell, and Alis configuration files.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit an existing file or create a file with syntax validation and rollback receipt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "searchText": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file within permitted workspace scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": "Create a formatted document (.md, .txt, .pdf, .docx, .xlsx, .pptx, .html).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_document",
            "description": "Convert a document from sourcePath to destPath.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sourcePath": {"type": "string"},
                    "destPath": {"type": "string"},
                },
                "required": ["sourcePath", "destPath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command within permitted workspace scope.",
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
            "name": "alis_live_state",
            "description": "Inspect live Alis shell, weather, music, schedule, notifications, and integration state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nexus_knowledge",
            "description": "Search, read, or summarize notes and collections inside the Nexus/Obsidian Vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search", "read", "overview", "journal", "finance", "collections"]},
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                },
            },
        },
    },
]


def delegate_subagent(
    role: str,
    task: str,
    context_hint: str = "",
    *,
    options: dict | None = None,
    session_ctx: dict | None = None,
    companion_root: Path | None = None,
    subagent_index: int = 1,
    subagent_total: int = 1,
    now_ms: int | None = None,
    requested_tools: list[str] | None = None,
) -> dict:
    """Execute a specialist Subagent sequentially on the single loaded local model in an isolated context window."""
    import concurrent.futures

    ctx = dict(session_ctx if isinstance(session_ctx, dict) else load_session_context())
    inherited_opts = ctx.get("_activeTurnOptions") if isinstance(ctx.get("_activeTurnOptions"), dict) else {}
    opts = {**inherited_opts, **dict(options or {})}
    role_key = _normalize_subagent_role(role)
    role_spec = SUBAGENT_ROLES[role_key]
    role_title = str(role_spec["title"])
    task_str = str(task or "").strip()
    hint_str = str(context_hint or "").strip()
    comp_root = Path(companion_root).resolve(strict=False) if companion_root else Path(__file__).resolve().parents[2]
    ts_ms = int(now_ms or int(time.time() * 1000))
    idx = max(1, int(subagent_index or (len(_tctx().active_subagent_runs) + 1)))
    tot = max(idx, int(subagent_total or idx))

    allowed_set = set(role_spec["allowed_tools"])
    read_only_role = role_key in ("researcher", "planner", "verifier")
    blocked_tools: list[str] = []

    req_tool_list = (
        list(requested_tools)
        if isinstance(requested_tools, (list, tuple, set))
        else (list(opts.get("requestedTools")) if isinstance(opts.get("requestedTools"), (list, tuple, set)) else [])
    )
    for rt in req_tool_list:
        rt_name = str(rt or "").strip()
        if not rt_name:
            continue
        if (
            rt_name == "delegate_subagent"
            or rt_name not in allowed_set
            or (read_only_role and rt_name in _PRIVILEGED_MUTATE_EXEC_TOOLS)
        ):
            if rt_name not in blocked_tools:
                blocked_tools.append(rt_name)

    route_meta = opts.get("modelRoute") if isinstance(opts.get("modelRoute"), dict) else {}
    with _SUBAGENT_LOCK:
        pinned_model = (
            _tctx().last_lmstudio_loaded_model
            or str(opts.get("model") or opts.get("selectedModel") or route_meta.get("selectedModel") or "").strip()
            or "qwen3.5-9b-uncensored-hauhaucs-aggressive"
        )
    t0_ms = int(time.time() * 1000)
    # Strictly reuse the single loaded model; never load a second LLM into VRAM/GTT
    pinned_endpoint = str(
        opts.get("endpoint")
        or opts.get("selectedEndpoint")
        or route_meta.get("selectedEndpoint")
        or ""
    ).strip()

    with _SUBAGENT_LOCK:
        running_prog = _emit_subagent_progress(
            role_key,
            index=idx,
            total=tot,
            summary=str(role_spec["status_summary"]),
            status="running",
        )
    running_label = str(running_prog["current"])

    # Isolated context window: fresh [system, user] messages list without main chat history bloat
    sub_sys = str(role_spec["system_prompt"])
    sub_user = f"Subtask [{role_title}]: {task_str[:900]}"
    if hint_str:
        sub_user = f"{sub_user}\n\nContext Hint:\n{hint_str[:600]}"
    sub_messages: list[dict] = [
        {"role": "system", "content": sub_sys},
        {"role": "user", "content": sub_user},
    ]

    role_tool_schemas: list[dict] = []
    _seen_sub_tools: set[str] = set()
    for schema in (AGENT_TOOL_SCHEMAS + _SUBAGENT_EXTRA_TOOL_SCHEMAS):
        t_schema_name = str(schema.get("function", {}).get("name") or "")
        if (
            t_schema_name
            and t_schema_name not in _seen_sub_tools
            and t_schema_name in allowed_set
            and t_schema_name != "delegate_subagent"
            and not (read_only_role and t_schema_name in _PRIVILEGED_MUTATE_EXEC_TOOLS)
        ):
            _seen_sub_tools.add(t_schema_name)
            role_tool_schemas.append(schema)

    sub_steps: list[dict] = []
    findings: list[str] = []
    parallel_io_used = False
    local_ctx = dict(ctx) if isinstance(ctx, dict) else {}
    workspace = str(local_ctx.get("lastProject") or local_ctx.get("lastDirectory") or comp_root)

    if blocked_tools:
        sub_steps.append({"tool": "privilege_guard", "blockedTools": list(blocked_tools)})
        findings.append(
            f"Privilege isolation enforced for [{role_title}]: blocked disallowed tool(s) ({', '.join(blocked_tools)})."
        )

    # Bounded multi-step LLM tool loop on the pinned model (up to subagentMaxTurns, default 3)
    max_sub_turns = max(1, min(3, int(opts.get("subagentMaxTurns") or 3)))
    sub_turns_used = 0
    if (
        pinned_endpoint
        and (pinned_endpoint.startswith("http://127.0.0.1:") or pinned_endpoint.startswith("http://localhost:"))
        and opts.get("useSubagentLlm") is not False
    ):
        for sub_turn in range(1, max_sub_turns + 1):
            sub_turns_used = sub_turn
            llm_turn = _call_local_model_tool_turn(
                endpoint_url=pinned_endpoint,
                model_name=pinned_model,
                system_prompt=sub_sys,
                user_prompt=sub_user,
                enable_thinking=bool(role_spec["enable_thinking"]),
                in_chat_directive="",
                mtp_active=route_meta.get("mtpActive") if "mtpActive" in route_meta else None,
                messages=sub_messages,
                temperature=float(role_spec["temperature"]),
                tools=role_tool_schemas,
                provider=str(opts.get("provider") or route_meta.get("provider") or ""),
                context_window=opts.get("contextWindow"),
                turn_idx=sub_turn,
            )
            if not isinstance(llm_turn, dict):
                break
            tcs = llm_turn.get("toolCalls") or []
            llm_txt = str(llm_turn.get("text") or "").strip()
            if not tcs:
                if llm_txt:
                    findings.append(llm_txt[:700])
                break

            limited_tcs = list(tcs[:2])
            sub_messages.append({
                "role": "assistant",
                "content": llm_txt or "",
                "tool_calls": [
                    {
                        "id": str(tc.get("toolCallId") or f"sub_t{sub_turn}_{i + 1}"),
                        "type": "function",
                        "function": {
                            "name": str(tc.get("name") or ""),
                            "arguments": json.dumps(tc.get("args") or {}, ensure_ascii=False),
                        },
                    }
                    for i, tc in enumerate(limited_tcs)
                ],
            })
            for i, tc in enumerate(limited_tcs):
                tc_id = str(tc.get("toolCallId") or f"sub_t{sub_turn}_{i + 1}")
                tc_name = str(tc.get("name") or "").strip()
                if not tc_name:
                    obs_msg = "[empty tool name]"
                elif (
                    tc_name == "delegate_subagent"
                    or tc_name not in allowed_set
                    or (read_only_role and tc_name in _PRIVILEGED_MUTATE_EXEC_TOOLS)
                ):
                    if tc_name not in blocked_tools:
                        blocked_tools.append(tc_name)
                    sub_steps.append({"tool": tc_name, "status": "blocked_privilege_isolation"})
                    obs_msg = f"[{tc_name} blocked by {role_title} privilege isolation]"
                else:
                    try:
                        obs = _execute_llm_tool_call(tc, local_ctx, comp_root, ts_ms)
                        sub_steps.append({"tool": tc_name, "status": "completed", "observation": obs[:360]})
                        findings.append(f"[{tc_name}] {obs[:360]}")
                        obs_msg = obs[:2400]
                    except Exception as exc:
                        obs_err = f"[{tc_name} error: {exc}]"
                        sub_steps.append({"tool": tc_name, "status": "failed", "error": str(exc)[:200]})
                        findings.append(obs_err[:360])
                        obs_msg = obs_err
                sub_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": tc_name or "unknown",
                    "content": obs_msg,
                })
            if sub_turn == max_sub_turns and llm_txt:
                findings.append(llm_txt[:700])

    # Deterministic role-scoped local execution/context gathering so subagents always return grounded compact summaries
    if not findings or (len(findings) == 1 and blocked_tools):
        if role_key == "researcher":
            combined_q = f"{task_str} {hint_str}".strip()
            doms = domain_rag.detect_domains(combined_q)
            if not doms and domain_rag.is_config_check_query(combined_q):
                doms = ["niri", "quickshell"]
            cand_path = _extract_path_from_text(combined_q, local_ctx)

            def _io_domain_rag() -> tuple[dict | None, str]:
                if not doms:
                    return None, ""
                try:
                    drag = domain_rag.query_domain_rag(
                        task_str,
                        domains=doms,
                        companion_root=comp_root,
                        token_budget=260,
                    )
                    dom_chunks = (
                        (drag.get("canonicalChunks") or []) + (drag.get("personalChunks") or [])
                        or (drag.get("chunks") or [])
                    )
                    snippets = [c.get("title") or c.get("source") or "" for c in dom_chunks[:3]]
                    msg_s = (
                        f"Domain RAG ({', '.join(doms)}): matched {len(dom_chunks)} reference(s)"
                        + (f" ({', '.join(s for s in snippets if s)})" if any(snippets) else "")
                    )
                    return {"tool": "domain_rag", "domains": doms}, msg_s
                except Exception:
                    return None, ""

            def _io_read_target() -> tuple[dict | None, str]:
                if not (cand_path and Path(cand_path).exists()):
                    return None, ""
                p_obj = Path(cand_path)
                if p_obj.is_file():
                    try:
                        info = code_tools.read_file(p_obj, max_chars=420)
                        preview = re.sub(r"\s+", " ", str(info.get("text") or "").strip())[:320]
                        return {"tool": "read_file", "targetPath": str(p_obj)}, f"Inspected `{p_obj.name}`: {preview}"
                    except Exception:
                        return {"tool": "read_file", "targetPath": str(p_obj)}, f"Located target file `{p_obj}`."
                return {"tool": "read_file", "targetPath": str(p_obj)}, f"Inspected directory `{p_obj}`."

            def _io_repo_map() -> tuple[dict | None, str]:
                try:
                    rmap = build_repo_map(workspace, token_budget=180)
                    top_entries = ", ".join(f"`{e['file']}`" for e in (rmap.get("entries") or [])[:4])
                    msg_s = (
                        f"Workspace `{Path(workspace).name}` context: {rmap.get('fileCount', 0)} files indexed"
                        + (f" (key files: {top_entries})." if top_entries else ".")
                    )
                    return {"tool": "code_search", "workspace": workspace}, msg_s
                except Exception:
                    return None, ""

            # Parallel I/O discovery across independent read-only sources via ThreadPoolExecutor
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                fut_rag = pool.submit(_io_domain_rag)
                fut_file = pool.submit(_io_read_target)
                fut_repo = pool.submit(_io_repo_map)
                io_results = [fut_rag.result(), fut_file.result(), fut_repo.result()]
            parallel_io_used = True
            for step_meta, msg_s in io_results:
                if step_meta and msg_s:
                    sub_steps.append(step_meta)
                    findings.append(msg_s)
            if not findings:
                findings.append(f"Gathered context for: {task_str[:240]}")
        elif role_key == "planner":
            sub_steps.append({"tool": "plan_checklist", "task": task_str[:120]})
            findings.append(
                f"Execution Plan for '{task_str[:160]}':\n"
                "1. Validate scope, target files, and configuration constraints.\n"
                "2. Execute targeted changes with backward compatibility.\n"
                "3. Cross-verify output integrity and state readback."
            )
        elif role_key in ("executor", "coder"):
            sub_steps.append({"tool": "action_prepare", "task": task_str[:120]})
            findings.append(
                f"Prepared & scoped concrete {role_title.lower()} actions for '{task_str[:200]}'"
                + (f" (context: {hint_str[:180]})" if hint_str else "")
                + "."
            )
        elif role_key == "verifier":
            sub_steps.append({"tool": "verify_state", "task": task_str[:120], "status": "skipped"})
            findings.append(
                f"Verifier skipped automated execution (no live LLM/check backend ran) for '{task_str[:200]}'."
            )

    header = f"[{role_title} {idx}/{tot}]"
    joined = " | ".join(f.strip() for f in findings if f.strip())
    compact_summary = f"{header} {joined}".strip()[:_SUBAGENT_MAX_SUMMARY_CHARS]
    if not compact_summary:
        compact_summary = f"{header} Completed subtask: {task_str[:300]}"[:_SUBAGENT_MAX_SUMMARY_CHARS]

    with _SUBAGENT_LOCK:
        done_prog = _emit_subagent_progress(
            role_key,
            index=idx,
            total=tot,
            summary=f"Completed ({len(compact_summary)} chars)",
            status="completed",
        )
        elapsed_ms = max(1, int(time.time() * 1000) - t0_ms)
        record = {
            "ok": True,
            "role": role_key,
            "roleTitle": role_title,
            "roleSubtitle": str(role_spec["subtitle"]),
            "task": task_str,
            "contextHint": hint_str[:400],
            "isolatedContext": True,
            "singleModelPinned": True,
            "privilegeIsolated": True,
            "readOnlyRole": read_only_role,
            "parallelDiscoveryUsed": parallel_io_used,
            "blockedTools": blocked_tools,
            "model": pinned_model,
            "endpoint": pinned_endpoint,
            "enableThinking": bool(role_spec["enable_thinking"]),
            "temperature": float(role_spec["temperature"]),
            "allowedTools": list(role_spec["allowed_tools"]),
            "messagesCount": len(sub_messages),
            "llmTurns": sub_turns_used,
            "statusLabel": running_label,
            "completedStatusLabel": str(done_prog["current"]),
            "progressPayload": running_prog,
            "completedProgressPayload": done_prog,
            "summary": compact_summary,
            "summaryLength": len(compact_summary),
            "maxSummaryChars": _SUBAGENT_MAX_SUMMARY_CHARS,
            "subagentIndex": idx,
            "subagentTotal": tot,
            "toolSteps": sub_steps,
            "toolCount": len(sub_steps),
            "elapsedMs": elapsed_ms,
        }
        _tctx().active_subagent_runs.append(record)
        return record


def assess_task_scale_for_subagents(prompt: str, options: dict | None = None) -> dict:
    """Assess whether splitting a task into Subagents is provably more effective than 1 single agent.

    Evaluates 5 architectural Efficiency-Gain Criteria (`SUBAGENT_EFFICIENCY_CRITERIA`):
      1. `cross_verification`  — Independent critic/audit/cross-verification without confirmation bias
      2. `parallel_discovery`  — Parallel multi-source / multi-domain read I/O discovery
      3. `privilege_isolation` — Untrusted external/read input separated from local write/exec tools
      4. `context_isolation`   — Heavy multi-file/log/document/repo ingestion isolated from main KV-cache
      5. `cognitive_separation`— Combining deterministic extraction (temp=0.2, thinking=off) with deep planning (temp=0.5, thinking=on)
    """
    opts = dict(options or {})
    raw = str(prompt or "").strip()
    folded = _fold_ascii(raw)
    domains = domain_rag.detect_domains(raw)

    def _fast_single_agent(reason_msg: str, act_cnt: int = 1, ph_list: list[str] | None = None) -> dict:
        return {
            "shouldDecompose": False,
            "efficiencyJustified": False,
            "scale": "normal",
            "criteria": [],
            "subagentCriteria": [],
            "matchedReasons": [],
            "reason": reason_msg,
            "actionCount": act_cnt,
            "phases": list(ph_list or []),
            "domains": domains,
            "parallelSources": [],
            "subtasks": [],
        }

    if opts.get("disableSubagents") is True or opts.get("autoSubagentDecompose") is False:
        return _fast_single_agent("Subagent decomposition disabled by options", act_cnt=0)

    # Fast-exit for empty, slash commands, or negated/hypothetical queries
    if not raw or raw.startswith("/"):
        return _fast_single_agent("Single slash command or empty prompt", act_cnt=1 if raw else 0)

    if is_hypothetical_or_negated(raw):
        return _fast_single_agent("Negated or hypothetical prompt (no execution)", act_cnt=0)

    explicit_subagent = bool(
        opts.get("forceSubagents") is True
        or re.search(
            r"\b(?:subagents?|sub-agents?|delegate_subagent|goi\s+subagent|dung\s+subagent|chia\s+nho\s+cho\s+subagent|chia\s+subagent|multi-agent|orchestrate\s+subagents?)\b",
            folded,
        )
    )

    # Detect distinct workflow phases present in the prompt
    has_research = bool(
        re.search(
            r"\b(?:kiem\s+tra|thu\s+thap|tim\s+hieu|tra\s+cuu|quet|khao\s+sat|doc\s+va\s+tim|research|investigate|gather|inspect|audit|scan|explore|search\s+and\s+read)\b",
            folded,
        )
    )
    has_plan = bool(
        re.search(
            r"\b(?:phan\s+tich|lap\s+ke\s+hoach|thiet\s+ke|len\s+phuong\s+an|danh\s+gia|tong\s+hop\s+ke\s+hoach|kien\s+truc|analyze|plan|architect|design|outline|evaluate|structure)\b",
            folded,
        )
    )
    has_execute = bool(
        re.search(
            r"\b(?:tao\s+bao\s+cao|viet\s+bao\s+cao|xuat\s+bao\s+cao|cap\s+nhat|trien\s+khai|chinh\s+sua|viet\s+tai\s+lieu|tao\s+file|sua\s+file|chay\s+lenh|implement|build|create\s+report|generate\s+report|write\s+report|refactor|patch|apply\s+changes)\b",
            folded,
        )
    )
    has_verify = bool(
        re.search(
            r"\b(?:xac\s+minh|doi\s+chieu|kiem\s+chung|kiem\s+chung\s+cheo|kiem\s+tra\s+cheo|kiem\s+toan|chay\s+test|verify|validate|cross-verify|cross-check|confirm\s+output|run\s+tests|double-check|independent\s+audit)\b",
            folded,
        )
    )

    phases: list[str] = []
    if has_research:
        phases.append("researcher")
    if has_plan:
        phases.append("planner")
    if has_execute:
        phases.append("executor")
    if has_verify:
        phases.append("verifier")

    # Evaluate the 5 Efficiency-Gain Criteria
    criteria: list[str] = []
    matched_reasons: list[str] = []

    # 1. cross_verification (Kiểm chứng chéo độc lập / Independent Critic & Verification)
    explicit_cross_verify = bool(
        re.search(
            r"\b(?:kiem\s+chung\s+cheo|kiem\s+tra\s+cheo|doi\s+chieu\s+doc\s+lap|kiem\s+toan|so\s+sanh\s+hai\s+gia\s+thuyet|cross-verify|cross-check|independent\s+verif|independent\s+audit|compare\s+two\s+hypotheses|critic)\b",
            folded,
        )
    )
    if explicit_cross_verify or (has_verify and (has_execute or has_plan)):
        criteria.append("cross_verification")
        matched_reasons.append(
            "Independent cross-verification/audit in a clean context window prevents confirmation bias"
        )
        if "verifier" not in phases:
            phases.append("verifier")

    # 2. parallel_discovery (Xử lý song song đa nguồn / Parallel Multi-Source I/O & Discovery)
    parallel_sources: list[str] = [f"domain:{d}" for d in domains]
    if re.search(r"\b(?:web|internet|http://|https://|url|github|wikipedia|duckduckgo)\b", folded):
        parallel_sources.append("source:web")
    if re.search(r"\b(?:log|logs|repo|codebase|thu\s+muc|workspace|git)\b", folded):
        parallel_sources.append("source:workspace")
    if re.search(r"\b(?:cpu|ram|gpu|battery|pin|nhiet\s+do|telemetry|system\s+status)\b", folded):
        parallel_sources.append("source:telemetry")
    explicit_parallel = bool(
        re.search(
            r"\b(?:xu\s+ly\s+song\s+song|song\s+song|dong\s+thoi|cung\s+luc|da\s+nguon|in\s+parallel|simultaneously|parallel\s+discovery|multiple\s+sources)\b",
            folded,
        )
    )
    if (len(parallel_sources) >= 2 and (has_research or has_plan or explicit_parallel)) or explicit_parallel:
        criteria.append("parallel_discovery")
        matched_reasons.append(
            f"Parallel multi-source discovery across {max(2, len(parallel_sources))} independent sources/domains"
        )
        if "researcher" not in phases:
            phases.insert(0, "researcher")

    # 3. privilege_isolation (Phân quyền truy cập / Least-Privilege Tool Sandboxing)
    has_untrusted_external_input = bool(
        re.search(
            r"\b(?:https?://|trang\s+web|webpage|url|external\s+doc|tai\s+lieu\s+ngoai|log\s+ngoai|untrusted|phan\s+quyen\s+truy\s+cap|privilege\s+isolation|least\s+privilege|sandbox)\b",
            folded,
        )
    )
    has_sensitive_local_action = bool(
        has_execute
        or re.search(
            r"\b(?:sua\s+cau\s+hinh|cap\s+nhat\s+config|chay\s+lenh|shell|exec|edit\s+config|modify\s+file|write\s+file|run\s+command|phan\s+quyen\s+truy\s+cap|privilege\s+isolation|least\s+privilege)\b",
            folded,
        )
    )
    if has_untrusted_external_input and has_sensitive_local_action:
        criteria.append("privilege_isolation")
        matched_reasons.append(
            "Least-privilege sandboxing isolates untrusted external/read input in a read-only Researcher before local actions"
        )
        if "researcher" not in phases:
            phases.insert(0, "researcher")
        if "executor" not in phases and has_execute:
            phases.append("executor")

    # 4. context_isolation (Cô lập ngữ cảnh lớn / Context-Bloat Prevention)
    attached_files_list = opts.get("attachedFiles") if isinstance(opts.get("attachedFiles"), list) else []
    doc_mentions = re.findall(r"\.(?:pdf|docx|xlsx|pptx|csv|log|jsonl)\b", folded)
    has_heavy_context = bool(
        len(attached_files_list) >= 2
        or len(doc_mentions) >= 2
        or re.search(
            r"\b(?:toan\s+bo\s+repo|tat\s+ca\s+cac\s+file|nhieu\s+file|hang\s+loat|large\s+log|multi-file|entire\s+repo|entire\s+codebase|deep\s+scan|co\s+lap\s+ngu\s+canh|context\s+isolation)\b",
            folded,
        )
    )
    if has_heavy_context and (has_research or has_plan or has_execute):
        criteria.append("context_isolation")
        matched_reasons.append(
            "Context isolation prevents large multi-file/document/repo dumps from bloating the main agent context window"
        )
        if "researcher" not in phases:
            phases.insert(0, "researcher")

    # 5. cognitive_separation (Tách biệt chế độ suy luận / Specialist Parameter Isolation)
    if has_research and has_plan and (has_execute or has_verify or len(domains) >= 2 or len(raw) >= 48):
        criteria.append("cognitive_separation")
        matched_reasons.append(
            "Cognitive separation isolates deterministic factual extraction (temp=0.2, thinking=off) from architectural planning (temp=0.5, thinking=on)"
        )

    connector_matches = re.findall(
        r"(?:\b(?:roi|sau\s+do|tiep\s+theo|cuoi\s+cung|dong\s+thoi|va\s+sau\s+do|then|after\s+that|next|finally|subsequently|and\s+then)\b|[,;]\s*(?:va|and|roi|then)\b)",
        folded,
    )
    clause_parts = [
        p.strip()
        for p in re.split(
            r"(?:[,;]|\b(?:roi|sau\s+do|tiep\s+theo|cuoi\s+cung|then|after\s+that|next|finally)\b)+",
            raw,
            flags=re.IGNORECASE,
        )
        if len(p.strip()) >= 8
    ]
    action_count = max(len(phases), min(len(clause_parts), len(phases) + len(connector_matches)))

    if explicit_subagent and not criteria:
        criteria.append("cognitive_separation")
        matched_reasons.append("Explicit subagent delegation requested by user/caller")

    # Only decompose when efficiency is justified by >= 1 architectural criterion AND either explicit request or multi-role workflow
    efficiency_justified = bool(
        explicit_subagent
        or (
            len(criteria) >= 1
            and (
                len(phases) >= 2
                or "parallel_discovery" in criteria
                or "privilege_isolation" in criteria
                or "cross_verification" in criteria
            )
            and len(raw) >= 28
        )
    )

    if not efficiency_justified:
        return _fast_single_agent(
            "Single-agent execution is more efficient (no cross-verification, parallel I/O, privilege boundary, or heavy context isolation required)",
            act_cnt=max(1, len(phases)),
            ph_list=phases,
        )

    if not phases:
        phases = ["researcher", "planner", "executor"]
    elif len(phases) == 1:
        if "cross_verification" in criteria and "verifier" not in phases:
            phases.append("verifier")
        elif "privilege_isolation" in criteria and "executor" not in phases:
            phases.append("executor")
        else:
            if "researcher" not in phases:
                phases.insert(0, "researcher")
            if "planner" not in phases:
                phases.append("planner")

    subtasks: list[dict] = []
    total_sub = len(phases)
    for i, role_key in enumerate(phases, start=1):
        spec = SUBAGENT_ROLES[role_key]
        clause_hint = clause_parts[i - 1] if i - 1 < len(clause_parts) else raw
        subtasks.append(
            {
                "role": role_key,
                "title": str(spec["title"]),
                "index": i,
                "total": total_sub,
                "task": clause_hint[:360],
                "allowedTools": list(spec["allowed_tools"]),
                "statusLabel": f"🤖 Subagent [{spec['title']} {i}/{total_sub}]: {spec['status_summary']}",
            }
        )

    return {
        "shouldDecompose": True,
        "efficiencyJustified": True,
        "scale": "large",
        "criteria": criteria,
        "subagentCriteria": list(criteria),
        "matchedReasons": matched_reasons,
        "reason": (
            "Explicit subagent delegation requested"
            if explicit_subagent
            else f"Subagent split justified by {', '.join(criteria)} ({len(phases)} specialist roles: {', '.join(phases)})"
        ),
        "actionCount": max(action_count, len(subtasks)),
        "phases": phases,
        "domains": domains,
        "parallelSources": parallel_sources,
        "subtasks": subtasks,
    }


_SWARM_LOCAL_SEMAPHORE = threading.Semaphore(1)
_CLOUD_SWARM_PROVIDERS = frozenset({
    "openai",
    "anthropic",
    "claude",
    "gemini",
    "google",
    "deepseek",
    "openrouter",
    "codex",
    "groq",
    "together",
    "mistral",
    "cloud",
})


def _is_local_swarm_endpoint(endpoint: str = "", provider: str = "", options: dict | None = None) -> bool:
    opts = options if isinstance(options, dict) else {}
    forced = str(opts.get("swarmForceConcurrency") or "").strip().lower()
    if forced == "parallel":
        return False
    if forced == "serial":
        return True
    prov = str(provider or "").strip().lower()
    ep = str(endpoint or "").strip().lower()
    if prov in _CLOUD_SWARM_PROVIDERS or ep.startswith("https://"):
        return False
    return True


def run_swarm_mission(
    goal: str,
    options: dict,
    companion_root: Path,
    host_root: Path | None = None,
    *,
    session_ctx: dict | None = None,
) -> tuple[dict, str]:
    """Execute a standalone Companion /swarm mission across Coordinator + specialist workers."""
    now_ms = int(options.get("nowMs") or int(time.time() * 1000))
    ctx = session_ctx if isinstance(session_ctx, dict) else load_session_context()
    clean_goal = re.sub(r"^/swarm\b\s*", "", str(goal or "").strip(), flags=re.IGNORECASE).strip() or "Swarm analysis"
    lang = detect_language(clean_goal)
    swarm_opts = dict(options)
    swarm_opts["forceSubagents"] = True
    scale = assess_task_scale_for_subagents(clean_goal, swarm_opts)
    pinned_model = str(options.get("model") or options.get("routedModel") or "local-pinned").strip()
    state = swarm_store.start_swarm_mission(
        clean_goal,
        scale.get("subtasks") or [],
        model=pinned_model,
        now_ms=now_ms,
        emit=True,
    )
    workers = [m for m in state.get("members", []) if isinstance(m, dict) and m.get("id") != "coordinator"]
    total_w = max(1, len(workers))
    accumulated_hints: list[str] = []
    worker_bullets: list[str] = []

    route_meta = options.get("modelRoute") if isinstance(options.get("modelRoute"), dict) else {}
    ep_val = str(options.get("endpoint") or route_meta.get("selectedEndpoint") or "").strip()
    prov_val = str(options.get("provider") or route_meta.get("provider") or "").strip()
    is_local_swarm = _is_local_swarm_endpoint(ep_val, prov_val, options)

    state_lock = threading.Lock()
    layer_buckets: dict[int, list[tuple[int, dict]]] = {}
    for idx, worker in enumerate(workers, start=1):
        lyr = max(1, min(3, int(worker.get("layer") or 1)))
        layer_buckets.setdefault(lyr, []).append((idx, worker))

    def _run_layer_worker(idx: int, worker: dict, layer_hint: str) -> tuple[str, dict | None]:
        fresh_state = swarm_store.load_state(int(time.time() * 1000))
        if fresh_state.get("status") == "stopped":
            return "stopped", fresh_state
        wid = str(worker.get("id") or f"worker-{idx}")
        wrole = str(worker.get("role") or "researcher")
        wtask = str(worker.get("task") or clean_goal)
        with state_lock:
            swarm_store.mark_worker_working(state, wid, now_ms=int(time.time() * 1000), emit=True)
        worker_opts = dict(options)
        if is_local_swarm:
            with _SWARM_LOCAL_SEMAPHORE:
                sub_res = delegate_subagent(
                    role=wrole,
                    task=wtask,
                    context_hint=layer_hint,
                    options=worker_opts,
                    session_ctx=dict(ctx),
                    companion_root=companion_root,
                    subagent_index=idx,
                    subagent_total=total_w,
                    now_ms=int(time.time() * 1000),
                )
        else:
            sub_res = delegate_subagent(
                role=wrole,
                task=wtask,
                context_hint=layer_hint,
                options=worker_opts,
                session_ctx=dict(ctx),
                companion_root=companion_root,
                subagent_index=idx,
                subagent_total=total_w,
                now_ms=int(time.time() * 1000),
            )
        fresh_after = swarm_store.load_state(int(time.time() * 1000))
        if fresh_after.get("status") == "stopped":
            return "stopped", fresh_after
        with state_lock:
            swarm_store.record_worker_completion(state, wid, sub_res, now_ms=int(time.time() * 1000), emit=True)
            raw_sum = str(sub_res.get("rawSummary") or sub_res.get("summary") or "").strip()
            if raw_sum:
                accumulated_hints.append(f"[{wid}] {raw_sum[:360]}")
                worker_bullets.append(f"- **`@{wid}`** ({worker.get('title')}): {raw_sum}")
        return "ok", None

    for lyr in sorted(layer_buckets.keys()):
        layer_workers = layer_buckets[lyr]
        fresh_layer = swarm_store.load_state(int(time.time() * 1000))
        if fresh_layer.get("status") == "stopped":
            return fresh_layer, swarm_store.format_swarm_markdown(fresh_layer, lang=lang)
        with state_lock:
            layer_hint = "\n".join(accumulated_hints[-3:])
        if not is_local_swarm and len(layer_workers) > 1:
            parent_req_id = getattr(threading.current_thread(), "_aqua_request_id", None)
            parent_cancel_ev = getattr(threading.current_thread(), "_aqua_cancel_event", None)

            def _run_with_req_ctx(idx_inner: int, w_inner: dict, hint_inner: str):
                cur_t = threading.current_thread()
                old_rid = getattr(cur_t, "_aqua_request_id", None)
                old_cev = getattr(cur_t, "_aqua_cancel_event", None)
                if parent_req_id:
                    cur_t._aqua_request_id = parent_req_id
                if parent_cancel_ev is not None:
                    cur_t._aqua_cancel_event = parent_cancel_ev
                try:
                    return _run_layer_worker(idx_inner, w_inner, hint_inner)
                finally:
                    cur_t._aqua_request_id = old_rid
                    cur_t._aqua_cancel_event = old_cev

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(layer_workers))) as pool:
                futs = [
                    pool.submit(contextvars.copy_context().run, _run_with_req_ctx, idx_w, w_obj, layer_hint)
                    for idx_w, w_obj in layer_workers
                ]
                for fut in futs:
                    st_res, stopped_state = fut.result()
                    if st_res == "stopped" and isinstance(stopped_state, dict):
                        return stopped_state, swarm_store.format_swarm_markdown(stopped_state, lang=lang)
        else:
            for idx_w, w_obj in layer_workers:
                with state_lock:
                    cur_hint = "\n".join(accumulated_hints[-3:]) if is_local_swarm else layer_hint
                st_res, stopped_state = _run_layer_worker(idx_w, w_obj, cur_hint)
                if st_res == "stopped" and isinstance(stopped_state, dict):
                    return stopped_state, swarm_store.format_swarm_markdown(stopped_state, lang=lang)

    joined_findings = "\n".join(worker_bullets) if worker_bullets else "- All assigned slices completed."
    synth_text = ""
    if str(options.get("endpoint") or "").strip() and str(options.get("model") or "").strip():
        try:
            synth_text = _synthesize_observations_with_llm(
                clean_goal,
                joined_findings,
                lang,
                options,
            )
        except Exception:
            synth_text = ""
    if not synth_text:
        synth_text = (
            f"Completed {len(workers)} specialist slices for **{clean_goal}**.\n{joined_findings}"
            if lang != "vi"
            else f"Đã hoàn tất {len(workers)} nhánh chuyên trách cho **{clean_goal}**.\n{joined_findings}"
        )

    fresh_before_finish = swarm_store.load_state(int(time.time() * 1000))
    cancel_ev_now = getattr(threading.current_thread(), "_aqua_cancel_event", None)
    if fresh_before_finish.get("status") == "stopped" or (cancel_ev_now is not None and cancel_ev_now.is_set()):
        return fresh_before_finish, swarm_store.format_swarm_markdown(fresh_before_finish, lang=lang)

    state = swarm_store.finish_swarm_mission(
        state,
        synth_text,
        status="completed",
        now_ms=int(time.time() * 1000),
        emit=True,
    )
    header = (
        f"### 🐝 Swarm Completed — {state['title']} ({len(workers)} workers)\n"
        if lang != "vi"
        else f"### 🐝 Swarm hoàn tất — {state['title']} ({len(workers)} workers)\n"
    )
    return state, header + synth_text


def execute_swarm_action(payload: dict, companion_root: Path, host_root: Path | None = None) -> dict:
    """Handle UI/helper actions for the Swarm workspace (Board / Members / Graph)."""
    op = str(payload.get("op") or "get").strip().lower()
    now_ms = int(payload.get("nowMs") or int(time.time() * 1000))
    model_name = str(payload.get("model") or "").strip()
    if op == "get":
        return {"op": "get", "swarm": swarm_store.load_state(now_ms)}
    if op == "clear":
        return {"op": "clear", "swarm": swarm_store.clear_swarm_state(model=model_name, now_ms=now_ms, emit=True)}
    if op == "stop":
        cancel_active_runs(reason="cancelled_by_user", now_ms=now_ms)
        return {"op": "stop", "swarm": swarm_store.stop_swarm_state(now_ms=now_ms, emit=True)}
    if op == "add_member":
        role = str(payload.get("role") or "researcher").strip().lower()
        brief = str(payload.get("brief") or payload.get("task") or "").strip()
        state, member = swarm_store.add_member_to_state(role, brief, model=model_name, now_ms=now_ms, emit=True)
        if payload.get("runNow") is True:
            ctx = load_session_context()
            swarm_store.mark_worker_working(state, member["id"], now_ms=int(time.time() * 1000), emit=True)
            sub_res = delegate_subagent(
                role=member["role"],
                task=member["task"],
                context_hint=str(state.get("goal") or ""),
                options=payload,
                session_ctx=ctx,
                companion_root=companion_root,
                subagent_index=1,
                subagent_total=1,
                now_ms=int(time.time() * 1000),
            )
            state = swarm_store.record_worker_completion(state, member["id"], sub_res, now_ms=int(time.time() * 1000), emit=True)
        return {"op": "add_member", "member": member, "swarm": state}
    if op == "remove_member":
        member_id = str(payload.get("memberId") or payload.get("name") or "").strip()
        state = swarm_store.remove_member_from_state(member_id, now_ms=now_ms, emit=True)
        return {"op": "remove_member", "swarm": state}
    if op == "run":
        goal = str(payload.get("goal") or payload.get("prompt") or "").strip()
        if not goal:
            raise SwarmStoreError("empty_goal", "Provide a mission goal for /swarm.")
        state, summary_md = run_swarm_mission(goal, payload, companion_root, host_root)
        return {"op": "run", "swarm": state, "summary": summary_md[:4000]}
    if op == "post":
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            raise SwarmStoreError("empty_message", "Board message cannot be empty.")
        state = swarm_store.load_state(now_ms)
        mentions = swarm_store.extract_mentions(text)
        # If swarm is idle and user enters a mission or /swarm <goal> on the board, launch the swarm!
        if (state.get("status") == "idle" and not mentions) or re.match(r"^/swarm\b", text, flags=re.IGNORECASE):
            state, summary_md = run_swarm_mission(text, payload, companion_root, host_root)
            return {"op": "post", "swarm": state, "summary": summary_md[:4000]}
        swarm_store._append_board_post(
            state,
            sender="user",
            kind="user",
            mentions=mentions,
            text=text,
            now_ms=now_ms,
        )
        state = swarm_store.save_state(state, emit=True, now_ms=now_ms)
        if payload.get("dispatch") is not False and mentions:
            member_map = {str(m.get("id")): m for m in state.get("members", []) if isinstance(m, dict)}
            targets = (
                [m for mid, m in member_map.items() if mid != "coordinator"]
                if "all" in mentions
                else [member_map[m] for m in mentions if m in member_map and m != "coordinator"]
            )
            if targets:
                ctx = load_session_context()
                state["status"] = "running"
                for idx, target_m in enumerate(targets[:4], start=1):
                    wid = str(target_m["id"])
                    clean_inst = re.sub(rf"@{re.escape(wid)}\b", "", text, flags=re.IGNORECASE).strip() or target_m.get("task") or text
                    target_m["task"] = clean_inst[:360]
                    swarm_store.mark_worker_working(state, wid, now_ms=int(time.time() * 1000), emit=True)
                    sub_res = delegate_subagent(
                        role=str(target_m.get("role") or "researcher"),
                        task=str(clean_inst),
                        context_hint=str(state.get("goal") or ""),
                        options=payload,
                        session_ctx=ctx,
                        companion_root=companion_root,
                        subagent_index=idx,
                        subagent_total=len(targets[:4]),
                        now_ms=int(time.time() * 1000),
                    )
                    state = swarm_store.record_worker_completion(state, wid, sub_res, now_ms=int(time.time() * 1000), emit=True)
                state["status"] = "completed"
                state = swarm_store.save_state(state, emit=True, now_ms=int(time.time() * 1000))
        return {"op": "post", "swarm": state}
    raise SwarmStoreError("invalid_swarm_op", f"Unsupported swarm operation: {op}")


def _synthesize_observations_with_llm(
    prompt: str,
    observations_text: str,
    lang_or_options: str | dict = "en",
    options_or_lang: dict | str | None = None,
    sources_or_ctx: str | dict = "",
) -> str:
    """Synthesize gathered tool observations into a natural, detailed Markdown answer via the Local LLM."""
    if isinstance(lang_or_options, dict):
        options = lang_or_options
        lang = str(options_or_lang or detect_language(prompt))
        sources_markdown = str(sources_or_ctx) if isinstance(sources_or_ctx, str) else ""
    else:
        lang = str(lang_or_options or detect_language(prompt))
        options = options_or_lang if isinstance(options_or_lang, dict) else {}
        sources_markdown = str(sources_or_ctx) if isinstance(sources_or_ctx, str) else ""

    ep_url, mdl_name = _resolve_live_llm(options)
    if not ep_url or not (ep_url.startswith("http://127.0.0.1:") or ep_url.startswith("http://localhost:")):
        return ""
    if "/v1/" not in ep_url:
        return ""

    t0_synth = int(time.time() * 1000)
    _emit_activity_step(f"Synthesizing answer ({(mdl_name or 'local-llm')[:24]}) ›", kind="llm_think", status="running")
    try:
        custom_sys = str(options.get("systemPrompt") or "").strip()
        rules_txt = str(options.get("rulesText") or "").strip()
        is_voice = bool(
            options.get("fromVoice")
            or options.get("fromVoiceInput")
            or options.get("voiceMode")
            or options.get("voiceInputTurn")
        )
        effort = str(options.get("thinkingEffort") or "medium").strip().lower()
        synth_max_tokens = 320 if is_voice else (1600 if effort in ("max", "xhigh", "high") else (1200 if effort == "medium" else 700))
        if is_voice:
            if lang == "vi":
                sys_msg = (
                    "Bạn là Alis-Companion. Hãy đọc dữ liệu công cụ bên dưới rồi trả lời trực tiếp bằng 2-4 câu nói tiếng Việt tự nhiên, súc tích như trợ lý giọng nói. "
                    "Nêu thẳng kết luận cốt lõi ngay câu đầu tiên. Không dùng gạch đầu dòng Markdown, không đọc tên file thô/URL/emoji, không thêm câu kết luận sáo rỗng. Không sinh thẻ <think>. {REASON:none}"
                )
            else:
                sys_msg = (
                    "You are Alis-Companion. Read the gathered tool observations below and answer directly in 2-4 natural, conversational spoken sentences. "
                    "Lead with the key takeaway in the very first sentence. Never use Markdown bullet lists, emojis, raw file paths, or URLs, and omit filler conclusions. Do not output <think>. {REASON:none}"
                )
        elif lang == "vi":
            sys_msg = (
                "Bạn là Alis-Companion. Hãy tổng hợp dữ liệu công cụ và trang web bên dưới thành câu trả lời ngắn gọn, sắc bén theo chuẩn Cloud AI (BLUF): "
                "mở đầu ngay bằng 1-2 câu kết luận trực tiếp, điều chỉnh độ dài theo độ phức tạp câu hỏi (đoạn văn ngắn cho câu đơn giản; chỉ dùng danh sách gạch đầu dòng gọn hoặc tiêu đề ### khi cần cấu trúc nhiều ý). "
                "Không sao chép thô log/cấu trúc và không viết câu kết chung chung vô nghĩa. Trích dẫn nguồn gọn gàng dạng ([tên-miền](url)) hoặc `[tệp#dòng]`. "
                "Không sinh thẻ <think>. {REASON:none}"
            )
        else:
            sys_msg = (
                "You are Alis-Companion. Synthesize the gathered tool observations and webpage excerpts into a crisp, high-signal Cloud-AI style answer (BLUF): "
                "lead immediately with a 1-2 sentence direct takeaway, scale structure to prompt complexity (plain prose for simple queries; tight bullets or ### sections only when genuinely multi-part), "
                "and never echo raw logs or append generic wrap-up filler. Cite sources compactly as ([domain](url)) or `[file#line]`. "
                "Do not output <think>. {REASON:none}"
            )
        import prompt_assembler

        untrusted_obs = [("tool_observations", observations_text[:4200])]
        user_q = (
            f"Câu hỏi / Yêu cầu của người dùng: {prompt[:500]}"
            if lang == "vi"
            else f"User Request: {prompt[:500]}"
        )
        assembled_msgs = prompt_assembler.assemble_messages(
            system_persona=sys_msg,
            trusted_system_prompt=custom_sys[:1200],
            trusted_rules=rules_txt[:1000],
            user_prompt=user_q,
            untrusted_blocks=untrusted_obs,
            directive_prefix="{REASON:none}",
            system_max_chars=2400,
        )
        opt_temp = options.get("temperature")
        synth_temp = (
            round(max(0.0, min(2.0, float(opt_temp))), 2)
            if isinstance(opt_temp, (int, float))
            else 0.4
        )
        body = {
            "model": mdl_name or "default",
            "messages": assembled_msgs,
            "temperature": synth_temp,
            "max_tokens": synth_max_tokens,
            "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if _is_mtp_model(mdl_name):
            body["repetition_penalty"] = 1.0
            body["repeat_penalty"] = 1.0
        raw_content = _call_chat_completion_text(ep_url, body, chunk_timeout=65.0, max_wall_sec=120.0)
        if raw_content:
            if sources_markdown and sources_markdown not in raw_content:
                lbl = "📚 **Nguồn tra cứu:**" if lang == "vi" else "📚 **Sources:**"
                raw_content = f"{raw_content.rstrip()}\n\n{lbl} {sources_markdown}"
            return raw_content
    except Exception:
        return ""
    finally:
        dt_s = max(1, int(round((int(time.time() * 1000) - t0_synth) / 1000.0)))
        _emit_activity_step(f"Synthesized in {dt_s}s ›", kind="llm_think", status="completed")
    return ""


def _run_deep_thinking_stage(
    prompt: str,
    stage1_observations: str,
    lang: str,
    options: dict,
    images: list[str] | None = None,
    client_actions: list[dict] | None = None,
) -> str:
    """Stage 2 of a Hybrid UI + Deep Thinking turn: reason deeply over Stage 1 UI/Screen/Desktop evidence."""
    stages = ((options.get("modelRoute") or {}).get("hybridStages") or {}) if isinstance(options.get("modelRoute"), dict) else {}
    stage_opts = dict(options)
    if stages.get("thinkingModel"):
        stage_opts["model"] = str(stages["thinkingModel"])
    if stages.get("thinkingEndpoint"):
        stage_opts["endpoint"] = str(stages["thinkingEndpoint"])
    ep_url, mdl_name = _resolve_live_llm(stage_opts)
    if not ep_url or "/v1/" not in ep_url:
        return ""
    if not (ep_url.startswith("http://127.0.0.1:") or ep_url.startswith("http://localhost:")):
        return ""

    raw_eff = str(stages.get("thinkingEffort") or options.get("thinkingEffort") or "medium").strip().lower()
    if raw_eff in ("off", "instant", "auto", ""):
        raw_eff = "medium"
    t0_deep = int(time.time() * 1000)
    _emit_activity_step(f"Stage 2/2 · Deep Thinking analysis ({raw_eff}) ›", kind="deep_thinking", status="running")
    try:
        custom_sys = str(options.get("systemPrompt") or "").strip()
        rules_txt = str(options.get("rulesText") or "").strip()
        is_voice = bool(
            options.get("fromVoice")
            or options.get("fromVoiceInput")
            or options.get("voiceMode")
            or options.get("voiceInputTurn")
        )
        obs_parts = [f"Stage 1 UI / Desktop observations:\n{str(stage1_observations or '').strip()[:3600]}"]
        if client_actions:
            obs_parts.append(
                "Executed Stage 1 Desktop UI actions: "
                + ", ".join(str(a.get("action") or "") for a in client_actions if isinstance(a, dict))
            )
        if images:
            obs_parts.append(f"Captured screenshots: {len(images)}")
        obs_blob = "\n\n".join(obs_parts)

        if lang == "vi":
            sys_msg = (
                "Bạn là Alis-Companion ở Pha 2 (Deep Thinking). Pha 1 (UI / Desktop) đã thực thi và thu thập dữ liệu thực tế bên dưới. "
                "Hãy phân tích chuyên sâu, suy luận từng bước chặt chẽ dựa trên bằng chứng từ Pha 1 để giải quyết trọn vẹn phần phân tích/kiến trúc/kế hoạch trong câu hỏi của người dùng. "
                + (
                    "Vì đây là lượt Voice, trình bày súc tích bằng 3-4 câu tiếng Việt tự nhiên, không Markdown gạch đầu dòng, không emoji hay đường dẫn thô."
                    if is_voice
                    else "Mở đầu bằng kết luận cốt lõi (BLUF) theo chuẩn Cloud AI, tiếp theo bằng phân tích rõ ràng, có cấu trúc hợp lý."
                )
            )
            usr_msg = f"Yêu cầu của người dùng: {prompt[:600]}\n\n{obs_blob}"
        else:
            sys_msg = (
                "You are Alis-Companion in Stage 2 (Deep Thinking). Stage 1 (UI / Desktop) has already gathered the factual runtime observations below. "
                "Perform rigorous, grounded deep reasoning strictly over Stage 1 evidence to solve the analytical, debugging, architecture, or planning part of the user's request. "
                + (
                    "Since this is a Voice turn, answer in 3-4 crisp, natural spoken sentences with no Markdown bullets, emojis, or raw paths."
                    if is_voice
                    else "Lead with a direct BLUF takeaway (Cloud-AI style), then present well-structured, concise reasoning and recommendations."
                )
            )
            usr_msg = f"User Request: {prompt[:600]}\n\n{obs_blob}"

        if custom_sys:
            sys_msg = f"{custom_sys[:1200]}\n\n{sys_msg}"
        if rules_txt and rules_txt not in sys_msg:
            sys_msg = f"{sys_msg}\n\n## Active Rules\n{rules_txt[:1000]}"

        body = {
            "model": mdl_name or "default",
            "messages": [
                {"role": "system", "content": sys_msg[:2800]},
                {"role": "user", "content": usr_msg},
            ],
            "temperature": 0.6 if not is_voice else 0.45,
            "max_tokens": 420 if is_voice else (2048 if raw_eff in ("high", "xhigh", "max") else 1400),
        }
        if _is_mtp_model(mdl_name):
            body["repetition_penalty"] = 1.0
            body["repeat_penalty"] = 1.0
        try:
            import thinking_router
            body = thinking_router.apply_effort_to_payload(
                body,
                raw_eff,
                api_format="openai",
                model_name=mdl_name,
                endpoint=ep_url,
            )
        except Exception:
            body["reasoning_effort"] = "high" if raw_eff in ("xhigh", "max") else raw_eff
            body["chat_template_kwargs"] = {"enable_thinking": True}

        raw_deep = _call_chat_completion_text(ep_url, body, chunk_timeout=75.0, max_wall_sec=150.0)
        if raw_deep:
            cleaned = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", raw_deep, flags=re.IGNORECASE).strip()
            return cleaned or raw_deep.strip()
    except Exception:
        return ""
    finally:
        dt_s = max(1, int(round((int(time.time() * 1000) - t0_deep) / 1000.0)))
        _emit_activity_step(f"Deep analysis finished in {dt_s}s ›", kind="deep_thinking", status="completed")
    return ""


def run_react_tool_loop(
    prompt: str = "",
    session_ctx: dict | None = None,
    companion_root: Path | None = None,
    options: dict | None = None,
    now_ms: int | None = None,
    max_steps: int = 4,
    *,
    endpoint_url: str = "",
    model_name: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
    enable_thinking: bool | None = None,
    in_chat_directive: str = "",
    mtp_active: bool | None = None,
    temperature: float | None = None,
) -> dict | None:
    """Multi-step ReAct loop: Thought -> Tool Call 1 -> Observation 1 -> Thought -> Tool Call 2 -> Final LLM Synthesis."""
    opts = dict(options or {})
    eff_prompt = str(user_prompt or prompt or opts.get("prompt") or "").strip()
    eff_endpoint = str(endpoint_url or opts.get("endpoint") or "").strip()
    eff_model = str(model_name or opts.get("model") or "").strip()
    if not eff_endpoint or not eff_model:
        return None
    opts["endpoint"] = eff_endpoint
    opts["model"] = eff_model
    ctx = session_ctx if isinstance(session_ctx, dict) else load_session_context()
    ctx["_activeTurnOptions"] = dict(opts)
    ctx["_a2uiGenerativeAllowed"] = bool(opts.get("a2uiGenerativeEnabled") is True)
    ctx["_turnTaintSources"] = []
    ctx.pop("_pendingTurnUiSurface", None)
    prev_pca = ctx.get("pendingClientAction")
    prev_pca_cid = str(prev_pca.get("confirmId") or "") if isinstance(prev_pca, dict) else ""
    comp_root = Path(companion_root).resolve(strict=False) if companion_root else Path(__file__).resolve().parents[2]
    ts_ms = int(now_ms or int(time.time() * 1000))
    lang = detect_language(eff_prompt)
    route_meta = opts.get("modelRoute") if isinstance(opts.get("modelRoute"), dict) else {}
    eff_thinking = bool(enable_thinking) if enable_thinking is not None else bool(route_meta.get("enableThinking", False))
    eff_directive = str(in_chat_directive or route_meta.get("inChatDirective") or "")
    eff_mtp = mtp_active if mtp_active is not None else (route_meta.get("mtpActive") if "mtpActive" in route_meta else None)
    eff_temp = temperature if isinstance(temperature, (int, float)) else (
        opts.get("temperature") if isinstance(opts.get("temperature"), (int, float)) else None
    )

    attached_ctx = str(opts.get("_attachedFilesContext") or "").strip()
    effective_user_content = eff_prompt[:1600]
    if attached_ctx and attached_ctx not in effective_user_content:
        effective_user_content = f"{effective_user_content}\n\n{attached_ctx[:2800]}"
    if eff_directive and eff_directive not in effective_user_content:
        effective_user_content = f"{eff_directive}\n{effective_user_content}"

    active_sid = str(
        opts.get("threadId")
        or (_tctx().current_turn_thread_id or "")
        or ctx.get("activeThreadId")
        or "chat_default"
    ).strip() or "chat_default"
    eff_provider = str(opts.get("provider") or route_meta.get("provider") or "").strip()
    eff_ctx_window = opts.get("contextWindow")

    import prompt_assembler

    raw_sys = str(system_prompt or opts.get("systemPrompt") or "").strip() or (
        "You are Alis-Companion, a full-authority Linux desktop agent running a multi-step ReAct loop. "
        "You may chain multiple tool calls across turns (e.g. search -> fetch_url -> file_mutate, or read_spill on spilled outputs) when needed. "
        "Once you have gathered enough observations, provide a clear, well-structured Markdown answer with compact citations."
    )
    rules_txt = str(opts.get("rulesText") or "").strip()
    untrusted_react_blocks: list[tuple[str, str]] = []
    if attached_ctx:
        untrusted_react_blocks.append(("attached_or_rag_context", attached_ctx[:2800]))
    if opts.get("memoryEnabled") is True:
        try:
            char_key = "octo" if str(opts.get("character") or "").lower() == "octo" else "aqua"
            vis_mems = memory_store.search_visible(char_key, eff_prompt, now_ms=ts_ms, limit=6)
            mem_lines = [
                f"- [{m.get('scope', 'shared')}] {str(m.get('text') or '')[:200]}"
                for m in (vis_mems or [])
                if isinstance(m, dict) and str(m.get("text") or "").strip()
            ]
            if mem_lines:
                untrusted_react_blocks.append(("user_memories", "\n".join(mem_lines)))
        except Exception:
            pass

    prior_messages = opts.get("messages") if isinstance(opts.get("messages"), list) else []
    messages = prompt_assembler.assemble_messages(
        system_persona=raw_sys,
        trusted_rules=rules_txt,
        history=prior_messages,
        user_prompt=eff_prompt[:1600],
        untrusted_blocks=untrusted_react_blocks,
        directive_prefix=eff_directive,
        system_max_chars=2400,
    )
    sys_prompt = str(messages[0].get("content") or raw_sys)
    messages, _ = context_policy.drain_and_inject_context(messages, active_sid)

    run = None
    run_id = ""
    tool_outputs: list[str] = []
    tool_steps: list[dict] = []
    tool_chain: list[str] = []
    thoughts: list[str] = []
    taint_sources: list[str] = []
    taint_blocked = False
    active_tools: list[dict] = select_initial_tool_schemas(
        eff_prompt,
        full=(opts.get("deferTools") is False),
    )
    allow_tainted_exec = opts.get("allowTaintedExecution") is True
    final_synthesis = ""
    step_counter = 0
    max_iters = max(1, min(6, int(max_steps or 4)))
    loop_budget_exhausted = False
    loop_stop_status = "completed"

    for turn_idx in range(1, max_iters + 1):
        messages, _ = context_policy.compact_messages_for_route(
            messages,
            provider=eff_provider,
            model=eff_model,
            endpoint=eff_endpoint,
            reserved_output=2048 if eff_thinking else 1536,
            context_window=eff_ctx_window,
            tools=active_tools,
        )
        llm_turn = _call_local_model_turn_react_step(
            endpoint_url=eff_endpoint,
            model_name=eff_model,
            system_prompt=sys_prompt,
            user_prompt=eff_prompt,
            enable_thinking=eff_thinking,
            in_chat_directive=eff_directive,
            mtp_active=eff_mtp,
            messages=messages,
            temperature=eff_temp,
            tools=active_tools,
            provider=eff_provider,
            context_window=eff_ctx_window,
            turn_idx=turn_idx,
        )
        if not llm_turn:
            break

        turn_text = str(llm_turn.get("text") or "").strip()
        turn_tcs = llm_turn.get("toolCalls") or []

        if not turn_tcs:
            if turn_idx == 1 and not tool_outputs:
                ctx.pop("_a2uiGenerativeAllowed", None)
                ctx.pop("_turnTaintSources", None)
                ctx.pop("_pendingTurnUiSurface", None)
                if (
                    turn_text
                    and ":1234" in str(eff_endpoint or "")
                    and not os.environ.get("ALIS_TEST_ISOLATED")
                    and not os.environ.get("AQUA_TEST_ENDPOINT")
                    and not os.environ.get("AI_FIXTURE_URL")
                ):
                    return {
                        "handled": True,
                        "runId": "",
                        "text": turn_text,
                        "finalText": turn_text,
                        "toolSteps": [],
                        "stepCount": 0,
                        "toolChain": [],
                        "stepsExecuted": 0,
                        "thoughts": [],
                        "synthesized": True,
                        "observations": [],
                        "taintSources": [],
                        "taintBlocked": False,
                    }
                return None
            if turn_text:
                final_synthesis = turn_text
            break

        if run is None:
            run = start_run(eff_prompt, source="interactive", language=lang, max_turns=12, max_duration_sec=180, now_ms=ts_ms)
            run_id = run["runId"]

        if turn_text:
            thoughts.append(turn_text)

        assistant_tc_payload = []
        for tc in turn_tcs[:4]:
            assistant_tc_payload.append({
                "id": tc["toolCallId"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("args") or {}, ensure_ascii=False),
                },
            })
        messages.append({
            "role": "assistant",
            "content": turn_text or "",
            "tool_calls": assistant_tc_payload,
        })

        for tc_idx, tc in enumerate(turn_tcs[:4]):
            step_counter += 1
            t_name = tc["name"]
            t_args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            tool_chain.append(t_name)
            _emit_activity_step(f"Step {step_counter}: `{t_name}` ›", kind="tool", status="running")
            step_id = record_step_start(run_id, step_counter, t_name, side_effect=True, now_ms=ts_ms)
            err_sig = ""
            if taint_sources and not allow_tainted_exec and _is_privileged_taint_sink(t_name, t_args):
                taint_blocked = True
                conf = create_pending_confirmation(
                    "tainted_tool_escalation",
                    t_name,
                    f"Confirm `{t_name}` after untrusted data ({', '.join(taint_sources[:3])})",
                    {"tool": t_name, "args": dict(t_args), "taintSources": list(taint_sources)},
                    now_ms=ts_ms,
                )
                conf_id = str(conf.get("confirmId") or "")
                ctx["pendingConfirmId"] = conf_id
                save_session_context(ctx, now_ms=ts_ms)
                out_str = (
                    f"[CaMeL taint guard: Confirmation required before executing `{t_name}` "
                    f"after reading untrusted data from {', '.join(taint_sources[:3])} "
                    f"(confirmationId={conf_id}). Do not execute without explicit user approval.]"
                )
                record_step_finish(
                    step_id,
                    status="awaiting_confirmation",
                    detail={"taintSources": list(taint_sources), "confirmationId": conf_id},
                )
                tool_outputs.append(f"### `{t_name}`\n{out_str}")
                tool_steps.append({
                    "step": step_counter,
                    "name": t_name,
                    "args": dict(t_args),
                    "status": "awaiting_confirmation",
                    "confirmationId": conf_id,
                    "taintSources": list(taint_sources),
                    "observation": out_str,
                })
            else:
                try:
                    out_str = _execute_llm_tool_call(tc, ctx, comp_root, ts_ms)
                    record_step_finish(step_id, status="completed", detail={"output": out_str[:400]})
                    tool_outputs.append(f"### `{t_name}`\n{out_str}")
                    tool_steps.append({"step": step_counter, "name": t_name, "args": dict(t_args), "status": "completed", "observation": out_str})
                    if t_name == "tool_search":
                        q_ts = str(t_args.get("query") or t_args.get("q") or "").strip()
                        lim_ts = int(t_args.get("limit") or 6)
                        seen_names = {str((s.get("function") or {}).get("name") or "") for s in active_tools}
                        for ms in search_agent_tool_schemas(
                            q_ts,
                            limit=lim_ts,
                            include_generative_ui=bool(ctx.get("_a2uiGenerativeAllowed")),
                        ):
                            mname = str((ms.get("function") or {}).get("name") or "")
                            if mname and mname not in seen_names:
                                active_tools.append(ms)
                                seen_names.add(mname)
                    taint_label = _detect_untrusted_taint_label(t_name, t_args, ctx, comp_root)
                    if taint_label and taint_label not in taint_sources:
                        taint_sources.append(taint_label)
                        ctx["_turnTaintSources"] = list(taint_sources)
                except Exception as exc:
                    err_sig = f"{t_name}:{str(exc)[:100]}"
                    out_str = f"[{t_name} error: {exc}]"
                    record_step_finish(step_id, status="failed", detail={"error": str(exc)})
                    tool_outputs.append(f"### `{t_name}` (error)\n{exc}")
                    tool_steps.append({"step": step_counter, "name": t_name, "args": dict(t_args), "status": "failed", "observation": out_str})
            turn_check = advance_turn(
                run_id,
                error_signature=err_sig,
                token_delta=llm_turn.get("tokenUsage") if tc_idx == 0 else None,
                now_ms=ts_ms,
            )
            spilled_tool_content, _ = spill_store.maybe_spill_tool_output(
                out_str,
                session_id=active_sid,
                call_id=tc["toolCallId"],
                tool_name=t_name,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc["toolCallId"],
                "name": t_name,
                "content": spilled_tool_content,
            })
            if not turn_check.get("allowed", True):
                loop_budget_exhausted = True
                loop_stop_status = str((turn_check.get("run") or {}).get("status") or "stopped_budget_exhausted")
                break

        # Next-step boundary: drain any non-waking context updates queued during tool execution
        messages, _ = context_policy.drain_and_inject_context(messages, active_sid)
        if loop_budget_exhausted:
            break

    if not tool_outputs or not run_id:
        ctx.pop("_a2uiGenerativeAllowed", None)
        ctx.pop("_turnTaintSources", None)
        ctx.pop("_pendingTurnUiSurface", None)
        return None

    observations_joined = "\n\n".join(tool_outputs)
    if final_synthesis and len(final_synthesis.strip()) >= 80:
        combined_msg = final_synthesis
        synthesized = True
    elif final_synthesis:
        combined_msg = f"{final_synthesis}\n\n{observations_joined}"
        synthesized = True
    else:
        synth_try = _synthesize_observations_with_llm(eff_prompt, observations_joined, lang, opts)
        if synth_try and len(synth_try.strip()) >= 80:
            combined_msg = synth_try
            synthesized = True
        elif synth_try:
            combined_msg = f"{synth_try}\n\n{observations_joined}"
            synthesized = True
        else:
            thought_prefix = (thoughts[0] + "\n\n") if thoughts else ""
            combined_msg = f"{thought_prefix}{observations_joined}"
            synthesized = False

    clean_final = combined_msg.strip()
    finish_run(
        run_id,
        status=loop_stop_status if loop_budget_exhausted else "completed",
        checkpoint={"remainingSteps": [], "completedSteps": tool_steps, "finalReply": clean_final[:12000]},
        now_ms=ts_ms,
        final_reply=clean_final,
    )
    pending_ca = ctx.pop("_pendingTurnClientActions", [])
    pending_surf = ctx.pop("_pendingTurnUiSurface", None)
    ctx.pop("_a2uiGenerativeAllowed", None)
    ctx.pop("_turnTaintSources", None)
    pending_conf_id = ""
    cur_pca = ctx.get("pendingClientAction")
    cur_pca_cid = str(cur_pca.get("confirmId") or "") if isinstance(cur_pca, dict) else ""
    new_pca_this_turn = bool(isinstance(cur_pca, dict) and cur_pca_cid and cur_pca_cid != prev_pca_cid)
    if new_pca_this_turn:
        pending_conf_id = cur_pca_cid
    elif taint_blocked:
        for st in reversed(tool_steps):
            if st.get("confirmationId"):
                pending_conf_id = str(st["confirmationId"])
                break
    # Confirmation gate always takes precedence; also re-validate generative surfaces against final turn taint state
    if taint_blocked or new_pca_this_turn:
        pending_surf = None
    elif isinstance(pending_surf, dict):
        pending_surf, _ = a2ui_validator.validate_and_sanitize_surface(
            pending_surf,
            untrusted_generative=True,
            tainted=bool(taint_sources),
        )
    return {
        "handled": True,
        "runId": run_id,
        "text": clean_final,
        "finalText": clean_final,
        "toolSteps": tool_steps,
        "stepCount": step_counter,
        "toolChain": tool_chain,
        "stepsExecuted": step_counter,
        "thoughts": thoughts,
        "synthesized": synthesized,
        "observations": tool_outputs,
        "clientActions": pending_ca if isinstance(pending_ca, list) else [],
        "taintSources": list(taint_sources),
        "taintBlocked": taint_blocked,
        "requiresConfirmation": bool(taint_blocked or new_pca_this_turn),
        "confirmId": pending_conf_id,
        **({"uiSurface": pending_surf} if isinstance(pending_surf, dict) else {}),
    }


def _call_local_model_turn_react_step(
    endpoint_url: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    enable_thinking: bool = False,
    in_chat_directive: str = "",
    mtp_active: bool | None = None,
    messages: list[dict] | None = None,
    temperature: float | None = None,
    tools: list[dict] | None = None,
    provider: str = "",
    context_window: int | None = None,
    turn_idx: int = 0,
) -> dict | None:
    return _call_local_model_tool_turn(
        endpoint_url,
        model_name,
        system_prompt,
        user_prompt,
        enable_thinking=enable_thinking,
        in_chat_directive=in_chat_directive,
        mtp_active=mtp_active,
        messages=messages,
        temperature=temperature,
        tools=tools,
        provider=provider,
        context_window=context_window,
        turn_idx=turn_idx,
    )


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
    elif any(k in folded for k in ("sua code", "chinh sua", "edit", "refactor", "thay doi", "modify", "anh huong", "impact", "module khac", "component")):
        topic_label = clean_topic or workspace_name
        question = (
            f"Để đảm bảo chỉ chỉnh sửa đúng phạm vi được giao và không làm hỏng các thành phần liên quan trong `{topic_label}`, bạn muốn xử lý theo hướng nào?"
            if lang == "vi"
            else f"To keep changes strictly surgical and avoid unintended impact on other components in `{topic_label}`, how would you like to proceed?"
        )
        opts = (
            [
                {
                    "label": "Chỉ sửa đúng tệp/hàm được chỉ định & tự động hoàn tác nếu không hiệu quả",
                    "description": "Giới hạn thay đổi cục bộ, giữ nguyên API các phần khác và dọn sạch nếu không có tác dụng",
                    "value": f"Chỉ sửa đúng phạm vi được chỉ định cho {topic_label}, giữ nguyên các thành phần khác và hoàn tác nếu không hiệu quả",
                    "recommended": True,
                },
                {
                    "label": "Cập nhật đồng bộ các thành phần bị ảnh hưởng kèm kiểm thử hồi quy",
                    "description": "Điều chỉnh cả module gọi liên quan và chạy bộ test tự động để xác minh",
                    "value": f"Cập nhật đồng bộ các thành phần liên quan và chạy kiểm thử hồi quy cho {topic_label}",
                    "recommended": False,
                },
                {
                    "label": "Phân tích phạm vi ảnh hưởng & xem trước Diff trước khi sửa",
                    "description": "Liệt kê các tệp bị tác động để bạn duyệt trước khi chạm vào mã nguồn",
                    "value": f"Phân tích phạm vi ảnh hưởng và hiển thị diff preview trước khi sửa {topic_label}",
                    "recommended": False,
                },
            ]
            if lang == "vi"
            else [
                {
                    "label": "Strictly surgical edit on assigned target & auto-revert if ineffective",
                    "description": "Modify only the assigned scope, preserve external contracts, and clean up no-op edits",
                    "value": f"Apply strictly surgical edits to the assigned target for {topic_label} and revert if ineffective",
                    "recommended": True,
                },
                {
                    "label": "Coordinate updates across affected components with regression tests",
                    "description": "Update dependent callers in lockstep and verify with automated tests",
                    "value": f"Update affected components in lockstep and run regression tests for {topic_label}",
                    "recommended": False,
                },
                {
                    "label": "Inspect cross-component impact & preview diff before editing",
                    "description": "List all impacted files and contracts for your review before modifying code",
                    "value": f"Analyze cross-component impact and preview diff before editing {topic_label}",
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
                    "label": f"Lập kế hoạch từng bước & triển khai đúng phạm vi cho {topic_label}",
                    "description": "Chỉ sửa đúng phần được giao, hỏi nếu ảnh hưởng module khác và dọn sạch nếu không hiệu quả",
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
                    "label": f"Step-by-step surgical implementation & verification for {topic_label}",
                    "description": "Modify only assigned targets, ask if other modules are affected, and revert ineffective edits",
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
    spoken_text: str = "",
) -> dict:
    clean = re.sub(r"\r\n?", "\n", re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")))
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n+(?:1[.)][ \t]+[^\n]*\n+)?[ \t]*\d+[.)][ \t]*$", "", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()[:5800]
    if not clean:
        clean = "Đã hoàn thành." if language == "vi" else "Completed."
    clean_spoken = re.sub(r"\r\n?", "\n", re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(spoken_text or ""))).strip()[:2400]
    expr = expression if expression in ("idle", "happy", "excited", "thinking", "working", "surprised", "sleepy", "sad", "alert") else "idle"
    clean_images = [str(p).strip() for p in (images or []) if str(p or "").strip()][:4]
    if run_id and str(status) == "completed" and clean:
        try:
            finish_run(run_id, status="completed", final_reply=clean)
        except Exception:
            pass
    last_cursor_evt = (session_ctx or {}).pop("_lastCursorEvent", None) if isinstance(session_ctx, dict) else None
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
        "activitySteps": [s["label"] for s in _tctx().active_run_steps[-12:]],
        "workedSeconds": max(1, round((int(time.time() * 1000) - (_tctx().active_run_start_ms or int(time.time() * 1000))) / 1000.0)),
    }
    if clean_spoken:
        out["spokenText"] = clean_spoken
    if isinstance(last_cursor_evt, dict):
        out["cursorEvent"] = last_cursor_evt
    if clean_images:
        out["images"] = clean_images
    if isinstance(clarification, dict) and clarification.get("question") and isinstance(clarification.get("options"), list):
        out["clarification"] = clarification
    return out


def _run_screen_observe_core(
    *,
    prompt: str,
    intent: dict,
    options: dict,
    session_ctx: dict,
    companion_root: Path,
    allow_screenshot: bool,
    lang: str,
    run_id: str,
    now_ms: int,
    step_idx: int = 1,
    vlm_prompt_override: str = "",
) -> dict:
    """Capture/observe active window & analyze via VLM, returning both visual Markdown and natural spoken summary."""
    step_id = record_step_start(run_id, step_idx, "screen_observe", side_effect=False, now_ms=now_ms)
    prev_win = session_ctx.get("lastWindowId")
    folded_p = _fold_ascii(prompt)
    wants_som = bool(
        intent.get("annotateSom")
        or options.get("annotateSom")
        or re.search(r"\b(?:set of mark|som|annotate|danh so|elements)\b", folded_p)
        or re.match(r"^/som\b", folded_p)
    )
    wants_screenshot_img = bool(intent.get("captureScreenshot")) or wants_som or bool(
        re.search(
            r"\b(?:chup man hinh|chup anh man hinh|chup lai man hinh|take(?:\s+a)?\s+screen\s*shot|capture screen|screen\s*shot)\b",
            folded_p,
        )
        or re.match(r"^(?:/agent\s+|/)?screen\s*shot\b", folded_p)
    )
    effective_allow_ss = bool(allow_screenshot or wants_screenshot_img)
    is_live_desktop = (
        not os.environ.get("ALIS_AQUA_SCREEN_FIXTURE")
        and not os.environ.get("AQUA_TEST_ENDPOINT")
        and not os.environ.get("AI_FIXTURE_URL")
    )
    ep_url, mdl_name = _resolve_live_llm(options)
    if not ep_url:
        ep_url = str(options.get("endpoint") or "").strip()
    if not mdl_name:
        mdl_name = re.sub(r"^(?:lmstudio-|ollama-|gguf:)", "", str(options.get("model") or "").strip())
    mdl_lower = mdl_name.lower()
    allow_vlm_call = is_live_desktop or (options.get("synthesizeWithLlm") is True)
    has_live_vlm = bool(
        allow_vlm_call
        and effective_allow_ss
        and ep_url
        and mdl_name
        and "/v1/" in ep_url
        and (ep_url.startswith("http://127.0.0.1:") or ep_url.startswith("http://localhost:"))
        and any(
            k in mdl_lower
            for k in (
                "qwen3.6",
                "qwen3_6",
                "qwen3.5",
                "qwen3_5",
                "qwen3-vl",
                "qwen2-vl",
                "qwen2.5-vl",
                "-vl",
                "vision",
                "llava",
                "pixtral",
                "gemma-3",
                "gemma3",
                "minicpm",
                "internvl",
            )
        )
    )
    is_followup_about_shot = bool(
        re.search(
            r"\b(?:inside the screen\s*shot|in the screen\s*shot|from the screen\s*shot|about the screen\s*shot|describe the screen\s*shot|what(?:'s| is) (?:in|inside) the screen\s*shot|tell me about the screen\s*shot|what (?:you can|can you|do you) see in the screen\s*shot|trong anh chup|trong buc anh|mo ta anh chup|mo ta buc anh)\b",
            folded_p,
        )
        and not re.search(r"\b(?:take|capture)\b|\bchup\s+(?:man\s+hinh|lai|anh|moi)\b", folded_p)
    )
    need_capture_img = bool(effective_allow_ss and not is_followup_about_shot)
    try:
        mcp_obs = mcp_client.call_tool(
            "computer-use-linux",
            "screenshot" if effective_allow_ss else "get_accessibility_tree",
            {
                "allowScreenshot": effective_allow_ss,
                "previousWindowId": prev_win,
                "nowMs": now_ms,
                "captureImage": need_capture_img,
                "skipOcr": True,
                "annotateSom": wants_som,
            },
            companion_root=companion_root,
            enabled_overrides={"computer-use-linux": True},
        )
        obs_res = mcp_obs.get("structured") or observe_active_window(
            allow_screenshot=effective_allow_ss,
            previous_window_id=prev_win,
            now_ms=now_ms,
            capture_image=need_capture_img,
            skip_ocr=True,
            annotate_som=wants_som,
        )
    except McpError:
        obs_res = observe_active_window(
            allow_screenshot=effective_allow_ss,
            previous_window_id=prev_win,
            now_ms=now_ms,
            capture_image=need_capture_img,
            skip_ocr=True,
            annotate_som=wants_som,
        )
    obs = obs_res["observation"]
    screenshot_path = str(obs_res.get("screenshotPath") or obs.get("screenshotPath") or "").strip()
    som_image_path = str(
        obs_res.get("somImagePath")
        or obs_res.get("somScreenshotPath")
        or obs.get("somImagePath")
        or obs.get("somScreenshotPath")
        or ""
    ).strip()
    reused_previous_shot = False
    if is_followup_about_shot and not screenshot_path:
        try:
            saved_shot = str(session_ctx.get("lastScreenshotPath") or "").strip()
            if saved_shot and Path(saved_shot).is_file():
                screenshot_path = saved_shot
            else:
                from screen_context import _screenshots_dir
                recent_shots = sorted(_screenshots_dir().glob("screenshot-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
                if recent_shots and (time.time() - recent_shots[0].stat().st_mtime) <= 600:
                    screenshot_path = str(recent_shots[0])
            if screenshot_path:
                reused_previous_shot = True
                obs_res["screenshotPath"] = screenshot_path
                obs["screenshotPath"] = screenshot_path
                obs["source"] = "niri+vision"
                obs["focusChanged"] = False
                if saved_shot and screenshot_path == saved_shot:
                    if session_ctx.get("lastScreenshotTitle"):
                        obs["title"] = str(session_ctx["lastScreenshotTitle"])
                    if session_ctx.get("lastScreenshotAppId"):
                        obs["appId"] = str(session_ctx["lastScreenshotAppId"])
                    if isinstance(session_ctx.get("lastScreenshotHeadings"), list):
                        obs_res["headings"] = list(session_ctx["lastScreenshotHeadings"])
                else:
                    obs_res["headings"] = []
        except Exception:
            pass
    if (need_capture_img or effective_allow_ss) and not screenshot_path:
        screenshot_path = capture_screenshot_file(now_ms=now_ms)
        if screenshot_path:
            obs_res["screenshotPath"] = screenshot_path
            obs["screenshotPath"] = screenshot_path
    if not reused_previous_shot and screenshot_path:
        session_ctx["lastScreenshotPath"] = screenshot_path
        session_ctx["lastScreenshotTitle"] = str(obs.get("title") or "")
        session_ctx["lastScreenshotAppId"] = str(obs.get("appId") or "")
        session_ctx["lastScreenshotHeadings"] = list(obs_res.get("headings") or [])[:12]
    primary_img = som_image_path if (wants_som and som_image_path) else screenshot_path
    reply_images = [primary_img] if (primary_img and not reused_previous_shot) else []
    record_step_finish(step_id, status="completed", detail=obs)
    advance_turn(run_id, now_ms=now_ms)

    if not reused_previous_shot:
        session_ctx["lastWindowId"] = obs.get("windowId")
        session_ctx["lastAppId"] = obs.get("appId") or ""
    save_session_context(session_ctx, now_ms=now_ms)

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
    som_lines = []
    if wants_som and obs_res.get("elements"):
        for el in obs_res["elements"][:8]:
            el_line = f"- `[{el.get('markId')}]` {el.get('role', 'widget')}: **{el.get('name', '')}** `@({el.get('centerX')}, {el.get('centerY')})`"
            som_lines.append(el_line)
            if el_line not in compact_headings:
                compact_headings.append(el_line)
    lines_preview = "\n".join(compact_headings)
    source_badge = str(obs.get("source") or "niri+vision")
    vlm_img_path = screenshot_path if (screenshot_path and Path(screenshot_path).is_file()) else primary_img
    wants_detail = bool(
        re.search(
            r"\b(?:detail|detailed|as detail as possible|in detail|thorough|everything|all visible|chi tiet|ky luong|day du|tat ca|toan bo)\b",
            folded_p,
        )
    )
    turn_from_voice = bool(
        options.get("fromVoice")
        or options.get("fromVoiceInput")
        or options.get("voiceMode")
        or options.get("voiceInputTurn")
    )
    vlm_succeeded = False
    vlm_prompt = str(vlm_prompt_override or prompt).strip()
    if allow_vlm_call and ep_url and mdl_name and "/v1/" in ep_url and (ep_url.startswith("http://127.0.0.1:") or ep_url.startswith("http://localhost:")):
        try:
            if has_live_vlm and vlm_img_path and Path(vlm_img_path).is_file():
                import native_host_bridge

                _emit_activity_step(
                    f"Analyzing screenshot with Vision ({mdl_name[:24]}) ›",
                    kind="vlm_vision",
                    status="running",
                )
                b64_v, vlm_mime = native_host_bridge.prepare_vlm_image_b64(
                    vlm_img_path,
                    max_dim=1120,
                    quality=85,
                )
                if not b64_v:
                    raise RuntimeError("vlm_image_prepare_failed")
                ground_truth_block = (
                    f"\nAT-SPI & OCR Ground-Truth UI Text (use as ground truth for small UI text; never guess unreadable text):\n{lines_preview}"
                    if lines_preview
                    else ""
                )
                if lang == "vi":
                    if turn_from_voice:
                        sys_vlm = (
                            "Bạn là Alis-Companion. Hãy xem ảnh chụp màn hình và trả lời trực tiếp cho người dùng bằng 2-3 câu nói tiếng Việt tự nhiên, dễ nghe như đang trò chuyện. "
                            "Mở đầu bằng ý chính người dùng đang mở/làm gì trên màn hình, chỉ thêm 1-2 chi tiết nổi bật nhất nếu cần. "
                            "Không dùng gạch đầu dòng Markdown, không emoji, không đọc tên file thô/mã log, không viết câu kết sáo rỗng, và không gọi thanh Alis Bar là macOS hay Windows. Tuyệt đối không đoán mò chữ nhỏ không đọc được. Không sinh thẻ <think>."
                        )
                    elif wants_detail:
                        sys_vlm = (
                            "Bạn là Alis-Companion. Hãy phân tích ảnh chụp màn hình và mở đầu bằng 1 câu tóm tắt ngắn gọn bức tranh tổng thể người dùng đang làm gì, "
                            "rồi trình bày các vùng giao diện chính thực sự đáng chú ý bằng 3-5 gạch đầu dòng Markdown súc tích (câu hoàn chỉnh; không dùng --- hay ###). "
                            "Nếu thấy khung Alis-Companion chỉ nhắc tối đa 1 ý ngắn, không gọi thanh Alis Bar trên cùng là macOS/Windows, không thêm câu kết luận chung chung vô nghĩa. Tuyệt đối không đoán mò chữ nhỏ không đọc được. Không sinh thẻ <think>."
                        )
                    else:
                        sys_vlm = (
                            "Bạn là Alis-Companion. Hãy tổng hợp nội dung màn hình theo phong cách Cloud AI: mở đầu ngay bằng 1-2 câu trực diện trả lời đúng trọng tâm câu hỏi của người dùng; "
                            "chỉ thêm tối đa 2-3 gạch đầu dòng ngắn gọn nếu thật sự cần tách ý (bỏ gạch đầu dòng nếu 2 câu văn đã đủ rõ). "
                            "Không liệt kê máy móc từng thanh công cụ hay mọi góc màn hình, không gọi Alis Bar là macOS/Windows, không viết câu kết sáo rỗng. Tuyệt đối không đoán mò chữ nhỏ không đọc được. Không sinh thẻ <think>."
                        )
                else:
                    if turn_from_voice:
                        sys_vlm = (
                            "You are Alis-Companion. Analyze the captured screenshot and answer in 2-3 natural, conversational spoken sentences matching the user's request. "
                            "Lead with the core takeaway of what the user is working on, mentioning only 1-2 key visible highlights if helpful. "
                            "Never use Markdown bullets, emojis, raw filenames/log strings, or generic wrap-up filler, and never call the Alis top bar macOS or Windows. Never guess unreadable small text. Do not output <think>."
                        )
                    elif wants_detail:
                        sys_vlm = (
                            "You are Alis-Companion. Analyze the captured screenshot and lead with a crisp 1-sentence overview of what the user is doing, "
                            "followed by 3-5 tight Markdown bullets covering only the meaningful visible regions (complete sentences; no --- or ###; mention Alis-Companion briefly at most once). "
                            "Never call the Alis Bar macOS or Windows, omit generic filler conclusions, and never guess unreadable small text. Do not output <think>."
                        )
                    else:
                        sys_vlm = (
                            "You are Alis-Companion. Synthesize what is on screen in Cloud-AI BLUF style: lead directly with a concise 1-2 sentence summary answering the user's question, "
                            "then optionally add at most 2-3 short highlight bullets ONLY if distinct visual details add clear value (omit bullets if prose suffices). "
                            "Do not mechanically inventory every screen bar or edge, never call the Alis Bar macOS or Windows, omit filler wrap-up sentences, and never guess unreadable small text. Do not output <think>."
                        )
                usr_vlm_text = (
                    f"{{REASON:instant}}\n"
                    f"Desktop environment: Arch Linux · Niri Wayland · Alis Desktop Shell (the top bar is the Alis Bar, NEVER call it macOS or Windows; the dark floating chat/companion widget is Alis-Companion).\n"
                    f"Active window: {obs.get('title') or ''} ({obs.get('appId') or 'desktop'}){ground_truth_block}\n"
                    f"User request: {vlm_prompt[:400]}"
                )
                screen_body = {
                    "model": mdl_name,
                    "messages": [
                        {"role": "system", "content": sys_vlm},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": usr_vlm_text},
                                {"type": "image_url", "image_url": {"url": f"data:{vlm_mime or 'image/jpeg'};base64,{b64_v}"}},
                            ],
                        },
                    ],
                    "reasoning_effort": "none",
                    "chat_template_kwargs": {"enable_thinking": False},
                    "temperature": (
                        round(max(0.0, min(2.0, float(options["temperature"]))), 2)
                        if isinstance(options.get("temperature"), (int, float))
                        else 0.2
                    ),
                    "max_tokens": 240 if turn_from_voice else (640 if wants_detail else 360),
                }
                if _is_mtp_model(mdl_name):
                    screen_body["repetition_penalty"] = 1.0
                    screen_body["repeat_penalty"] = 1.0
                ai_txt = _call_chat_completion_text(
                    ep_url,
                    screen_body,
                    chunk_timeout=125.0,
                    max_wall_sec=195.0,
                    stream_stdout=False,
                ).strip()
                ai_txt = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", ai_txt, flags=re.IGNORECASE).strip()
                if ai_txt:
                    lines_preview = ai_txt[:3200]
                    if som_lines:
                        lines_preview = f"{lines_preview}\n" + "\n".join(som_lines)
                    source_badge = "niri+vision"
                    vlm_succeeded = True
                elif _tctx().last_lmstudio_error in ("gpu_oom", "gpu_fit_failed", "low_memory"):
                    lines_preview = f"{lines_preview}\n\n{_lmstudio_error_user_message(lang)}".strip()
        except Exception:
            pass

    win_label = str(obs.get("title") or obs.get("appId") or "desktop").strip()
    if len(win_label) > 44:
        win_label = win_label[:41].rstrip() + "…"
    wants_guide = any(k in folded_p for k in ("eyes", "hands", "mat va tay", "can you see"))
    shot_note_vi = f"\n📷 Đã chụp màn hình (`{Path(primary_img).name}`)." if (primary_img and not reused_previous_shot) else ""
    shot_note_en = f"\n📷 Screenshot captured (`{Path(primary_img).name}`)." if (primary_img and not reused_previous_shot) else ""
    if lang == "vi":
        guide_vi = (
            "\n\n💡 **Mắt & Tay MCP:** `/web` tìm kiếm · `/fetch` đọc trang · `/browser` tự động hoá web · mở ứng dụng / chạy lệnh."
            if wants_guide
            else ""
        )
        msg = (
            f"{lines_preview}{guide_vi}"
            if vlm_succeeded
            else f"**🖥 {win_label}** · `{source_badge}`{changed_note}\n{lines_preview}{shot_note_vi}{guide_vi}"
        ).strip()
        spoken_screen = (
            lines_preview
            if vlm_succeeded
            else f"Tôi đã chụp ảnh màn hình, cửa sổ đang mở là {win_label}."
        )
    else:
        guide_en = (
            "\n\n💡 **MCP Eyes & Hands:** `/web` search · `/fetch` read URL · `/browser` automate web · open apps / run commands."
            if wants_guide
            else ""
        )
        msg = (
            f"{lines_preview}{guide_en}"
            if vlm_succeeded
            else f"**🖥 {win_label}** · `{source_badge}`{changed_note}\n{lines_preview}{shot_note_en}{guide_en}"
        ).strip()
        spoken_screen = (
            lines_preview
            if vlm_succeeded
            else f"I captured the screenshot; your active window is {win_label}."
        )

    return {
        "display": msg,
        "spoken": spoken_screen,
        "images": reply_images,
        "observation": obs,
        "elements": obs_res.get("elements") or [],
        "semanticSnapshot": obs_res.get("semanticSnapshot") or obs.get("semanticSnapshot") or "",
        "somImagePath": som_image_path,
        "vlmSucceeded": vlm_succeeded,
    }




def _persist_turn_to_history(
    prompt: str,
    reply_text: str,
    model: str = "companion-agent",
    thread_id: str | None = None,
) -> tuple[int, int, bool]:
    try:
        tid = str(thread_id or _tctx().current_turn_thread_id or "").strip() or None
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
    session_ctx["pendingConfirmId"] = str(conf.get("confirmId") or "")
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
    now_ms = int(options.get("nowMs") or int(time.time() * 1000))
    req_tid = str(options.get("threadId") or "").strip()
    pending_ctx_updates = options.get("pendingContextUpdates")
    if isinstance(pending_ctx_updates, list):
        eff_inbox_sid = req_tid or "chat_default"
        for item in pending_ctx_updates:
            if isinstance(item, dict) and item.get("content"):
                context_policy.enqueue_context_update(
                    eff_inbox_sid,
                    str(item.get("kind") or "ambient"),
                    item.get("content", ""),
                    dedupe_key=str(item.get("dedupeKey") or ""),
                    now_ms=now_ms,
                )
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

    route_opts = dict(options)
    if str(options.get("thinkingEffort") or "").strip().lower() == "auto":
        route_opts["persistRouterState"] = True
    route_info = classify_prompt_model_role(str(options.get("prompt") or ""), route_opts)
    effective_options = dict(options)
    if route_info.get("selectedModel"):
        effective_options["model"] = route_info["selectedModel"]
    if route_info.get("selectedEndpoint"):
        effective_options["endpoint"] = route_info["selectedEndpoint"]
    if str(options.get("thinkingEffort") or "").strip().lower() == "auto":
        effective_options["thinkingEffort"] = str(route_info.get("recommendedEffort") or "off")
        effective_options["thinkingEffortMode"] = "auto"
    effective_options["routedRole"] = route_info["role"]
    effective_options["modelRoute"] = route_info

    is_hybrid = bool(route_info.get("hybridUiThinking") and isinstance(route_info.get("hybridStages"), dict))
    orig_persist = effective_options.get("persistHistory") is not False
    inner_options = dict(effective_options) if is_hybrid else effective_options
    if is_hybrid:
        hst = route_info["hybridStages"]
        if hst.get("uiModel"):
            inner_options["model"] = str(hst["uiModel"])
        if hst.get("uiEndpoint"):
            inner_options["endpoint"] = str(hst["uiEndpoint"])
        inner_options["thinkingEffort"] = "off"
        inner_options["hybridStage"] = "ui_action"
        inner_options["persistHistory"] = False
        _emit_activity_step("Stage 1/2 · Gathering UI & Desktop context ›", kind="step_hybrid_ui", status="running")

    prev_tid = _tctx().current_turn_thread_id
    _tctx().current_turn_thread_id = req_tid or None
    try:
        result = _execute_agent_turn_inner(inner_options, companion_root, host_root)
    finally:
        _tctx().current_turn_thread_id = prev_tid
        if is_hybrid:
            for meta_key in ("_domainRag", "_taskScale"):
                if meta_key in inner_options:
                    effective_options[meta_key] = inner_options[meta_key]

    if is_hybrid:
        _emit_activity_step("Stage 1/2 · UI & Desktop context gathered ›", kind="step_hybrid_ui", status="completed")
        stage1_text = str(result.get("text") or "").strip()
        can_deep_stage2 = bool(
            result.get("handled")
            and stage1_text
            and not result.get("requiresConfirmation")
            and not result.get("requiresSudoPassword")
            and result.get("intent") in (
                "screen_observe",
                "alis_local_info",
                "alis_service_control",
                "nexus_knowledge",
                "gui_action",
                "compound_task",
                "file_task",
                "shell_task",
                "web_task",
            )
        )
        deep_txt = ""
        if can_deep_stage2:
            deep_txt = _run_deep_thinking_stage(
                str(options.get("prompt") or ""),
                stage1_text,
                str(result.get("language") or route_info.get("language") or "en"),
                effective_options,
                images=result.get("images") if isinstance(result.get("images"), list) else None,
                client_actions=result.get("clientActions") if isinstance(result.get("clientActions"), list) else None,
            )
            if deep_txt:
                result["stage1Observations"] = stage1_text
                result["text"] = deep_txt
                result["synthesized"] = True
                result["spokenText"] = desktop_control._clean_text_for_tts(
                    deep_txt,
                    context_prompt=str(options.get("prompt") or ""),
                )
        if orig_persist and result.get("handled") and str(result.get("text") or "").strip() and not result.get("historySaved"):
            u_id, a_id, saved = _persist_turn_to_history(
                str(options.get("prompt") or ""),
                str(result["text"]),
                thread_id=req_tid or None,
            )
            result["userMessageId"] = u_id
            result["assistantMessageId"] = a_id
            result["historySaved"] = saved
        hst = route_info["hybridStages"]
        result["hybridPipeline"] = {
            "stages": ["ui_action", "deep_thinking"],
            "uiIntent": str(result.get("intent") or ""),
            "uiModel": str(hst.get("uiModel") or ""),
            "thinkingModel": str(hst.get("thinkingModel") or ""),
            "thinkingEffort": str(hst.get("thinkingEffort") or "medium"),
            "deepExecuted": bool(deep_txt),
        }
    result["modelRoute"] = route_info
    result["routedModel"] = route_info.get("selectedModel", "")
    result["routedRole"] = route_info.get("role", "action")
    scale_info = (
        effective_options.get("_taskScale")
        if isinstance(effective_options.get("_taskScale"), dict)
        else assess_task_scale_for_subagents(str(options.get("prompt") or ""), effective_options)
    )
    result["taskScale"] = scale_info
    result["subagentRuns"] = list(_tctx().active_subagent_runs)
    result["subagentCount"] = len(_tctx().active_subagent_runs)
    if (
        _tctx().active_subagent_runs
        and not result.get("handled")
        and (
            effective_options.get("autoSubagentDecompose") is True
            or effective_options.get("forceSubagents") is True
            or bool(
                re.search(
                    r"\b(?:subagents?|sub-agents?|delegate_subagent)\b",
                    _fold_ascii(str(options.get("prompt") or "")),
                )
            )
        )
    ):
        lang_turn = str(result.get("language") or detect_language(str(options.get("prompt") or "")))
        sub_lines = [f"- {r['summary']}" for r in _tctx().active_subagent_runs]
        if lang_turn == "vi":
            orchestrated_msg = (
                f"### Điều phối Subagent hoàn tất ({len(_tctx().active_subagent_runs)} Subagent tuần tự trên cùng 1 model)\n"
                + "\n".join(sub_lines)
            )
        else:
            orchestrated_msg = (
                f"### Subagent Orchestration Complete ({len(_tctx().active_subagent_runs)} sequential subagents on single loaded model)\n"
                + "\n".join(sub_lines)
            )
        ctx_curr = load_session_context()
        formatted = _format_reply(
            orchestrated_msg,
            expression="working",
            status="completed",
            language=lang_turn,
            session_ctx=ctx_curr,
        )
        persist_hist = options.get("persistHistory") is not False
        u_id, a_id, saved = (
            _persist_turn_to_history(str(options.get("prompt") or ""), formatted["text"])
            if persist_hist
            else (0, 0, False)
        )
        result.update(
            {
                "handled": True,
                "intent": "subagent_orchestration",
                **formatted,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }
        )
    from_voice = bool(
        options.get("fromVoice")
        or options.get("fromVoiceInput")
        or options.get("voiceMode")
        or options.get("voiceInputTurn")
    )
    result["fromVoice"] = from_voice
    if isinstance(effective_options.get("_domainRag"), dict) and "domainRag" not in result:
        result["domainRag"] = effective_options["_domainRag"]
    if result.get("handled"):
        if "attachedFiles" not in result:
            ctx_after = result.get("sessionContext") if isinstance(result.get("sessionContext"), dict) else load_session_context()
            att_list = ctx_after.get("attachedFiles") if isinstance(ctx_after.get("attachedFiles"), list) else (
                options.get("attachedFiles") if isinstance(options.get("attachedFiles"), list) else []
            )
            result["attachedFiles"] = att_list
        post_synth_updated = False
        if (
            effective_options.get("synthesizeWithLlm") is True
            and result.get("intent") in ("system_control", "git_task", "rag_query")
            and not result.get("synthesized")
            and result.get("text")
            and "### Synthesis" not in str(result.get("text") or "")
            and "### Tổng hợp" not in str(result.get("text") or "")
        ):
            synth_txt = _synthesize_observations_with_llm(
                str(options.get("prompt") or ""),
                str(result.get("text") or ""),
                str(result.get("language") or "en"),
                effective_options,
            )
            if synth_txt:
                result["text"] = synth_txt
                result["synthesized"] = True
                post_synth_updated = True
                result["spokenText"] = desktop_control._clean_text_for_tts(
                    synth_txt,
                    context_prompt=str(options.get("prompt") or ""),
                )
        if not str(result.get("spokenText") or "").strip() and str(result.get("text") or "").strip():
            try:
                result["spokenText"] = desktop_control._clean_text_for_tts(
                    str(result["text"]),
                    context_prompt=str(options.get("prompt") or ""),
                )
            except Exception:
                pass
        a2ui_allowed = effective_options.get("a2uiEnabled") is not False
        if a2ui_allowed:
            try:
                built_surface = a2ui_builder.build_surface_for_turn(
                    result,
                    snapshot=_tctx().current_turn_live_snapshot,
                    enabled=True,
                )
                if built_surface is not None:
                    result["uiSurface"] = built_surface
            except Exception:
                pass
        else:
            result.pop("uiSurface", None)
        a_msg_id = int(result.get("assistantMessageId") or 0)
        if a_msg_id > 0:
            try:
                history_store.update_message_artifacts(
                    a_msg_id,
                    content=str(result.get("text") or "") if post_synth_updated else None,
                    spoken_text=str(result.get("spokenText") or ""),
                    ui_surface=result.get("uiSurface") if isinstance(result.get("uiSurface"), dict) else None,
                )
            except Exception:
                pass
        if from_voice and str(result.get("text") or "").strip():
            try:
                if (
                    result.get("intent") == "voice_task"
                    and isinstance(result.get("voiceResult"), dict)
                    and result["voiceResult"].get("spoken")
                ):
                    tts_res = result["voiceResult"]
                else:
                    tts_res = desktop_control.voice_tts(
                        text=str(result.get("spokenText") or result.get("text") or ""),
                        voice="en-US-AvaMultilingualNeural",
                        language="auto",
                        context_prompt=str(options.get("prompt") or ""),
                    )
                result["voiceReply"] = {
                    "spoken": bool(tts_res.get("spoken", True)),
                    "transcriptAfterVoice": True,
                    "backend": str(tts_res.get("ttsBackend") or tts_res.get("backend") or "tts"),
                    "voice": str(tts_res.get("voice") or "en-US-AvaMultilingualNeural"),
                    "neuralVoice": str(tts_res.get("neuralVoice") or "en-US-AvaMultilingualNeural"),
                    "spokenText": str(tts_res.get("spokenText") or ""),
                    "streamingChunks": int(tts_res.get("streamingChunks") or 1),
                    "audioPath": str(tts_res.get("audioPath") or ""),
                }
            except Exception as exc:
                result["voiceReply"] = {
                    "spoken": False,
                    "transcriptAfterVoice": True,
                    "error": str(exc),
                }
        else:
            result["voiceReply"] = {
                "spoken": False,
                "transcriptAfterVoice": False,
            }
        try:
            run_id_done = str(result.get("runId") or "")
            if run_id_done and str(result.get("status") or "") == "completed" and result.get("text"):
                finish_run(run_id_done, status="completed", final_reply=str(result["text"]), now_ms=now_ms)
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
            result["swarmState"] = swarm_store.load_state(now_ms)
        except Exception:
            pass
    return result


def _execute_agent_turn_inner(options: dict, companion_root: Path, host_root: Path) -> dict:
    """Core implementation of the natural-language desktop agent turn."""
    _tctx().last_lmstudio_error = ""
    snap_in = options.get("alisLocalSnapshot")
    if isinstance(snap_in, dict) and snap_in:
        alis_local_bridge.save_live_snapshot(snap_in)
        _tctx().current_turn_live_snapshot = snap_in
    else:
        _tctx().current_turn_live_snapshot = alis_local_bridge.load_live_snapshot()
    _tctx().current_turn_nexus_vault = str(options.get("nexusVaultPath") or "").strip()
    now_ms = int(options.get("nowMs") or int(time.time() * 1000))
    _reset_activity_steps(now_ms)
    prompt = str(options.get("prompt") or "").strip()
    sudo_password_opt = str(options.get("sudoPassword") or "")
    persist_history = options.get("persistHistory") is not False
    allow_screenshot = options.get("allowScreenshot") is not False

    reconciled = reconcile_crashed_runs(now_ms)
    session_ctx = load_session_context()
    last_f_cur = str(session_ctx.get("lastFile") or "").strip()
    last_d_cur = str(session_ctx.get("lastDirectory") or "").strip()
    rec_cands = session_ctx.get("recentCandidates") or []
    folded_prompt = _fold_ascii(prompt)
    has_stale_obsidian = (
        bool(last_f_cur and (".obsidian/" in last_f_cur or last_f_cur.endswith("01_Core.css")))
        or bool(last_d_cur and ".obsidian" in last_d_cur)
        or any(".obsidian/" in str(c) or str(c).endswith("01_Core.css") for c in rec_cands)
    )
    if has_stale_obsidian and "01_core.css" not in folded_prompt and ".obsidian" not in folded_prompt:
        if ".obsidian/" in last_f_cur or last_f_cur.endswith("01_Core.css"):
            session_ctx["lastFile"] = ""
        if ".obsidian" in last_d_cur:
            session_ctx["lastDirectory"] = ""
        session_ctx["recentCandidates"] = [
            c for c in rec_cands
            if ".obsidian/" not in str(c) and not str(c).endswith("01_Core.css")
        ]
        save_session_context(session_ctx, now_ms=now_ms)
    pre_resolved = options.get("_resolvedAttachments")
    if isinstance(pre_resolved, dict) and "files" in pre_resolved and "contextMarkdown" in pre_resolved:
        attached_info = pre_resolved
    else:
        attached_info = resolve_file_attachments_and_mentions(
            prompt,
            options.get("attachedFiles") if isinstance(options.get("attachedFiles"), list) else None,
            session_ctx,
        )
        options["_resolvedAttachments"] = attached_info
    if attached_info["files"]:
        session_ctx["attachedFiles"] = [f["path"] for f in attached_info["files"]]
        session_ctx["lastFile"] = attached_info["files"][0]["path"]
        session_ctx["lastDirectory"] = str(Path(attached_info["files"][0]["path"]).parent)
        save_session_context(session_ctx, now_ms=now_ms)

    detected_domains = domain_rag.detect_domains(prompt)
    if not detected_domains and (
        domain_rag.is_config_check_query(prompt)
        or domain_rag.is_domain_concept_or_command_query(prompt)
        or domain_rag.is_domain_capability_query(prompt)
    ):
        detected_domains = ["niri", "quickshell", "archlinux"]
    if detected_domains and options.get("enableDomainRag") is not False:
        try:
            domain_rag_info = domain_rag.query_domain_rag(
                prompt,
                domains=detected_domains,
                companion_root=companion_root,
                token_budget=int(options.get("domainRagTokenBudget") or 800),
            )
            options["_domainRag"] = domain_rag_info
            if domain_rag_info.get("contextMarkdown"):
                existing_md = str(attached_info.get("contextMarkdown") or "").strip()
                if domain_rag_info["contextMarkdown"] not in existing_md:
                    attached_info["contextMarkdown"] = (
                        f"{existing_md}\n\n{domain_rag_info['contextMarkdown']}"
                        if existing_md
                        else domain_rag_info["contextMarkdown"]
                    )
        except Exception:
            pass

    intent = classify_intent(prompt, session_ctx)
    lang = intent["language"]
    kind = intent["kind"]
    if kind != "chat":
        intent_lbl = "planning" if (kind == "code_task" and intent.get("subkind") == "plan") else kind.replace("_", " ")
        _emit_activity_step(f"Analyzed request ({intent_lbl}) ›", kind="intent", status="completed")

    if kind == "swarm_task":
        swarm_arg = re.sub(r"^/swarm\b", "", prompt, flags=re.IGNORECASE).strip()
        folded_arg = swarm_arg.lower()
        pinned_model = str(options.get("model") or options.get("routedModel") or "local-pinned").strip()
        if not swarm_arg or folded_arg in ("status", "board", "members", "graph"):
            st = swarm_store.load_state(now_ms)
            reply_md = swarm_store.format_swarm_markdown(st, lang=lang)
        elif folded_arg in ("stop", "kill", "cancel"):
            cancel_active_runs(reason="cancelled_by_user", now_ms=now_ms)
            st = swarm_store.stop_swarm_state(now_ms=now_ms, emit=True)
            reply_md = swarm_store.format_swarm_markdown(st, lang=lang)
        elif folded_arg in ("clear", "reset"):
            st = swarm_store.clear_swarm_state(model=pinned_model, now_ms=now_ms, emit=True)
            reply_md = (
                "### 🐝 Swarm Cleared\nBoard, worker roster, and graph reset to idle."
                if lang != "vi"
                else "### 🐝 Đã làm mới Swarm\nBảng tin, danh sách thành viên và đồ thị đã trở về trạng thái chờ."
            )
        elif swarm_arg.startswith("@"):
            post_res = execute_swarm_action(
                {**options, "op": "post", "text": swarm_arg, "dispatch": True, "nowMs": now_ms},
                companion_root,
                host_root,
            )
            st = post_res.get("swarm") or swarm_store.load_state(now_ms)
            reply_md = swarm_store.format_swarm_markdown(st, lang=lang)
        else:
            st, reply_md = run_swarm_mission(
                swarm_arg,
                options,
                companion_root,
                host_root,
                session_ctx=session_ctx,
            )
        formatted = _format_reply(
            reply_md,
            expression="working",
            status="completed",
            language=lang,
            session_ctx=session_ctx,
        )
        u_id, a_id, saved = (
            _persist_turn_to_history(prompt, formatted["text"])
            if persist_history
            else (0, 0, False)
        )
        return {
            "handled": True,
            "intent": "swarm_task",
            "openSwarmTab": True,
            "swarmState": st,
            **formatted,
            "userMessageId": u_id,
            "assistantMessageId": a_id,
            "historySaved": saved,
        }

    scale_info = assess_task_scale_for_subagents(prompt, options)
    options["_taskScale"] = scale_info
    if (
        scale_info.get("shouldDecompose")
        and options.get("autoSubagentDecompose") is True
        and kind
        not in (
            "hypothetical_or_negated",
            "stop",
            "sudo_password_reply",
            "confirmation_response",
            "clarification_reply",
            "clarify_task",
            "adjust_budget",
        )
        and not session_ctx.get("pendingClarification")
        and not session_ctx.get("pendingSudoOp")
    ):
        subtasks = scale_info.get("subtasks") or []
        prev_summary = ""
        sub_md_lines: list[str] = []
        auto_swarm = swarm_store.start_swarm_mission(
            prompt,
            subtasks,
            model=str(options.get("model") or options.get("routedModel") or "local-pinned"),
            now_ms=now_ms,
            emit=True,
        )
        auto_workers = [m for m in auto_swarm.get("members", []) if isinstance(m, dict) and m.get("id") != "coordinator"]
        for idx_st, st in enumerate(subtasks):
            wid = str(auto_workers[idx_st]["id"]) if idx_st < len(auto_workers) else f"{st.get('role', 'researcher')}-{idx_st + 1}"
            swarm_store.mark_worker_working(auto_swarm, wid, now_ms=int(time.time() * 1000), emit=True)
            sub_res = delegate_subagent(
                role=str(st.get("role") or "researcher"),
                task=str(st.get("task") or prompt),
                context_hint=prev_summary,
                options=options,
                session_ctx=session_ctx,
                companion_root=companion_root,
                subagent_index=int(st.get("index") or 1),
                subagent_total=int(st.get("total") or len(subtasks) or 1),
                now_ms=now_ms,
            )
            auto_swarm = swarm_store.record_worker_completion(auto_swarm, wid, sub_res, now_ms=int(time.time() * 1000), emit=True)
            prev_summary = str(sub_res.get("summary") or "")
            if prev_summary:
                sub_md_lines.append(f"- {prev_summary}")
        swarm_store.finish_swarm_mission(
            auto_swarm,
            "\n".join(sub_md_lines) if sub_md_lines else "Delegated subtasks completed.",
            status="completed",
            now_ms=int(time.time() * 1000),
            emit=True,
        )
        if sub_md_lines:
            sub_block = "### Subagent Findings (Isolated Context)\n" + "\n".join(sub_md_lines)
            existing_md = str(attached_info.get("contextMarkdown") or "").strip()
            attached_info["contextMarkdown"] = f"{existing_md}\n\n{sub_block}" if existing_md else sub_block

    # 0. Ordinary chat fallback (or multi-step ReAct structured tool-calling loop)
    if kind == "chat":
        endpoint_url = str(options.get("endpoint") or "").strip()
        model_name = str(options.get("model") or "").strip()
        route_meta = options.get("modelRoute") if isinstance(options.get("modelRoute"), dict) else {}
        ponytail_mode = str(options.get("ponytailMode") or session_ctx.get("ponytailMode") or "").strip().lower()
        ponytail_clause = ""
        if ponytail_mode in ("lite", "full", "ultra"):
            ponytail_clause = (
                f"\n\n## Active Ponytail Discipline (mode: {ponytail_mode})\n"
                "- Motto: \"The best code is the code you never wrote.\"\n"
                "- Walk The Ladder before writing code: (1) YAGNI existence check, (2) Reuse existing repo helper/pattern, "
                "(3) Use standard library / native platform, (4) Fewest lines that solve it cleanly.\n"
                "- Never cut security, trust-boundary validation, or data-loss safeguards.\n"
                "- End with an Accountability Receipt noting skipped bloat and any trade-offs."
            )
        if endpoint_url and model_name and options.get("useModelTools") is True:
            custom_sys = str(options.get("systemPrompt") or "").strip()
            rules_txt = str(options.get("rulesText") or "").strip() or str(load_rules_state(now_ms).get("rulesText") or "").strip()
            is_voice_react = bool(
                options.get("fromVoice")
                or options.get("fromVoiceInput")
                or options.get("voiceMode")
                or options.get("voiceInputTurn")
            )
            base_react_sys = (
                "You are Alis-Companion, a full-authority Linux desktop agent running on Arch Linux with Niri Wayland and the Alis Desktop Shell. "
                "You can chain multiple tool calls across steps (Thought -> Tool Call -> Observation -> Next Tool Call -> Final Synthesis). "
                + (
                    "Voice Turn Style: Lead immediately with the direct answer in 2-4 natural, conversational spoken sentences matching the user's language. Never use Markdown bullet lists, emojis, raw file paths, or URLs."
                    if is_voice_react
                    else "Cloud-AI BLUF Style: Lead immediately with a 1-2 sentence direct answer/takeaway, synthesize tool outputs instead of echoing raw logs or long lists, scale structure to prompt complexity (plain prose for simple answers; tight bullets only when multi-part), omit generic filler wrap-ups, and strictly match the user's language."
                )
            )
            active_turn_sid = str(options.get("threadId") or (_tctx().current_turn_thread_id or "") or session_ctx.get("activeThreadId") or "chat_default")
            context_policy.enqueue_context_update(
                active_turn_sid,
                "active_directory",
                str(session_ctx.get("lastDirectory") or Path.home()),
                dedupe_key="active_directory",
                now_ms=now_ms,
            )
            sys_prompt = f"{custom_sys[:1200]}\n\n{base_react_sys}" if custom_sys else base_react_sys
            if rules_txt and rules_txt not in sys_prompt:
                sys_prompt = f"{sys_prompt}\n\n## Active Rules\n{rules_txt[:1000]}"
            if ponytail_clause and "Ponytail Discipline" not in sys_prompt:
                sys_prompt = f"{sys_prompt}{ponytail_clause}"
            prompt_for_react = (
                f"{prompt}\n\n{attached_info['contextMarkdown']}"
                if attached_info["contextMarkdown"]
                else prompt
            )
            max_react_steps = int(options.get("maxReactSteps") or 5)
            opt_temp = options.get("temperature")
            react_res = run_react_tool_loop(
                endpoint_url=endpoint_url,
                model_name=model_name,
                system_prompt=sys_prompt,
                user_prompt=prompt_for_react,
                session_ctx=session_ctx,
                companion_root=companion_root,
                options=options,
                now_ms=now_ms,
                max_steps=max_react_steps,
                enable_thinking=bool(route_meta.get("enableThinking", False)),
                in_chat_directive=str(route_meta.get("inChatDirective") or ""),
                mtp_active=route_meta.get("mtpActive") if "mtpActive" in route_meta else None,
                temperature=float(opt_temp) if isinstance(opt_temp, (int, float)) else None,
            )
            if react_res and react_res.get("handled") and react_res.get("toolSteps"):
                run_id = str(react_res.get("runId") or "")
                if not run_id:
                    run = start_run(prompt, source="interactive", language=lang, max_turns=10, max_duration_sec=180, now_ms=now_ms)
                    run_id = run["runId"]
                    for idx, st in enumerate(react_res["toolSteps"], start=1):
                        step_id = record_step_start(run_id, idx, st["name"], side_effect=True, now_ms=now_ms)
                        record_step_finish(step_id, status=st.get("status", "completed"), detail={"output": str(st.get("observation") or "")[:400]})
                        advance_turn(run_id, now_ms=now_ms)
                    finish_run(run_id, status="completed", now_ms=now_ms)
                combined_msg = str(react_res.get("finalText") or "").strip()
                react_req_conf = bool(react_res.get("requiresConfirmation"))
                react_conf_id = str(react_res.get("confirmId") or "")
                res = _format_reply(
                    combined_msg,
                    expression="thinking" if react_req_conf else "working",
                    status="awaiting_confirmation" if react_req_conf else "completed",
                    run_id=run_id,
                    language=lang,
                    requires_confirmation=react_req_conf,
                    confirm_id=react_conf_id,
                    session_ctx=session_ctx,
                )
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {
                    "handled": True,
                    "intent": "llm_tool_turn",
                    "reactSteps": react_res["toolSteps"],
                    "reactStepCount": react_res["stepCount"],
                    "toolChain": react_res.get("toolChain", []),
                    "synthesized": bool(react_res.get("synthesized", False)),
                    "attachedFiles": attached_info["files"],
                    "ponytailMode": ponytail_mode,
                    "clientActions": react_res.get("clientActions") or [],
                    "taintSources": react_res.get("taintSources") or [],
                    **({"uiSurface": react_res["uiSurface"]} if isinstance(react_res.get("uiSurface"), dict) else {}),
                    **res,
                    "userMessageId": u_id,
                    "assistantMessageId": a_id,
                    "historySaved": saved,
                }
            if react_res and react_res.get("handled") and str(react_res.get("finalText") or "").strip():
                combined_msg = str(react_res.get("finalText") or "").strip()
                res = _format_reply(combined_msg, expression="working", status="completed", language=lang, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {
                    "handled": True,
                    "intent": "chat",
                    "attachedFiles": attached_info["files"],
                    "attachedContextMarkdown": attached_info["contextMarkdown"],
                    "ponytailMode": ponytail_mode,
                    **res,
                    "userMessageId": u_id,
                    "assistantMessageId": a_id,
                    "historySaved": saved,
                }
            if _tctx().last_lmstudio_error in ("gpu_oom", "gpu_fit_failed", "low_memory"):
                msg = _lmstudio_error_user_message(lang)
                res = _format_reply(msg, expression="alert", status="failed", language=lang, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": "chat", **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}
        return {
            "handled": False,
            "intent": "chat",
            "language": lang,
            "reconciledRuns": reconciled,
            "attachedFiles": attached_info["files"],
            "attachedContextMarkdown": attached_info["contextMarkdown"],
            "ponytailMode": ponytail_mode,
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
        stopped_cid = str(session_ctx.get("pendingConfirmId") or (session_ctx.get("pendingSudoOp") or {}).get("confirmId") or "")
        had_pending_stop = bool(session_ctx.get("pendingSudoOp") or session_ctx.get("pendingConfirmId"))
        if had_pending_stop:
            resolve_pending_confirmation(decision="cancel", source="user_prompt", now_ms=now_ms)
        session_ctx["pendingClarification"] = None
        session_ctx["pendingDeletePath"] = ""
        session_ctx["pendingDeleteConfirmId"] = ""
        session_ctx["pendingMemoryForget"] = None
        session_ctx["pendingClientAction"] = None
        session_ctx["pendingSudoOp"] = None
        session_ctx["pendingConfirmId"] = ""
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
        stop_summary = a2ui_validator._clean_str(msg, 280).strip()
        if had_pending_stop:
            history_store.lock_confirm_surfaces(stopped_cid, resolution="cancelled", outcome_summary=stop_summary)
        res = _format_reply(msg, expression="alert", status="cancelled", language=lang, session_ctx=session_ctx)
        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
        return {
            "handled": True,
            "intent": "stop",
            **({"resolvedConfirmId": stopped_cid, "resolvedConfirmResolution": "cancelled", "resolvedConfirmSummary": stop_summary} if stopped_cid else {}),
            **res,
            "userMessageId": u_id,
            "assistantMessageId": a_id,
            "historySaved": saved,
        }

    # 2b. Sudo password reply for pending System path operation
    if kind == "sudo_password_reply":
        pending_sudo = session_ctx.get("pendingSudoOp")
        if not pending_sudo:
            perm_info = inspect_permissions({"nowMs": now_ms})
            latest = perm_info.get("latestPending")
            if latest and latest.get("actionKind") == "system_sudo_op":
                pending_sudo = latest
        sudo_cid = str((pending_sudo or {}).get("confirmId") or session_ctx.get("pendingConfirmId") or "")

        decision = classify_confirmation_response(prompt)
        if decision == "deny" and not sudo_password_opt:
            session_ctx["pendingSudoOp"] = None
            session_ctx["pendingConfirmId"] = ""
            save_session_context(session_ctx, now_ms=now_ms)
            resolve_pending_confirmation(decision="cancel", source="user_prompt", now_ms=now_ms)
            msg = (
                "Đã huỷ thao tác trên đường dẫn hệ thống. Không có thay đổi nào được thực hiện."
                if lang == "vi"
                else "Cancelled the system path operation. No changes were made."
            )
            sudo_cancel_sum = a2ui_validator._clean_str(msg, 280).strip()
            history_store.lock_confirm_surfaces(sudo_cid, resolution="cancelled", outcome_summary=sudo_cancel_sum)
            res = _format_reply(msg, expression="idle", status="cancelled", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "resolvedConfirmId": sudo_cid,
                "resolvedConfirmResolution": "cancelled",
                "resolvedConfirmSummary": sudo_cancel_sum,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

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
            session_ctx["pendingConfirmId"] = ""
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
                sudo_ok_sum = a2ui_validator._clean_str(f"Đã xác thực `sudo` và thực hiện trên `{target_path}` ({sudo_res['summary']}).", 280).strip()
                msg = f"Đã xác thực `sudo` và thực hiện trên `{target_path}` ({sudo_res['summary']}):\n{body_lines}"
            else:
                sudo_ok_sum = a2ui_validator._clean_str(f"Verified `sudo` and completed action on `{target_path}` ({sudo_res['summary']}).", 280).strip()
                msg = f"Verified `sudo` and completed action on `{target_path}` ({sudo_res['summary']}):\n{body_lines}"
            history_store.lock_confirm_surfaces(sudo_cid, resolution="confirmed", outcome_summary=sudo_ok_sum)
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
            out_payload = {
                "handled": True,
                "intent": kind,
                "resolvedConfirmId": sudo_cid,
                "resolvedConfirmResolution": "confirmed",
                "resolvedConfirmSummary": sudo_ok_sum,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }
            if sudo_res.get("editorMode"):
                out_payload["editorMode"] = sudo_res.get("editorMode")
                out_payload["isLargeFile"] = bool(sudo_res.get("isLargeFile"))
                out_payload["openedExternalEditor"] = bool(sudo_res.get("openedExternalEditor"))
                out_payload["editorCommand"] = str(sudo_res.get("editorCommand") or "")
                out_payload["editorPopup"] = sudo_res.get("editorPopup") or {}
            return out_payload
        except ToolError as exc:
            sudo_failed_resolved = False
            sudo_err_sum = ""
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
                    confirm_id=sudo_cid,
                    session_ctx=session_ctx,
                )
            else:
                sudo_failed_resolved = True
                session_ctx["pendingSudoOp"] = None
                session_ctx["pendingConfirmId"] = ""
                save_session_context(session_ctx, now_ms=now_ms)
                resolve_pending_confirmation(decision="cancel", source="user_prompt", now_ms=now_ms)
                msg = (
                    f"Không thể thực hiện thao tác hệ thống (`{exc.code}`): {exc}"
                    if lang == "vi"
                    else f"System operation failed (`{exc.code}`): {exc}"
                )
                sudo_err_sum = a2ui_validator._clean_str(msg, 280).strip()
                history_store.lock_confirm_surfaces(sudo_cid, resolution="failed", outcome_summary=sudo_err_sum)
                res = _format_reply(msg, expression="sad", status="failed", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(redacted_prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "errorCode": exc.code,
                **({"resolvedConfirmId": sudo_cid, "resolvedConfirmResolution": "failed", "resolvedConfirmSummary": sudo_err_sum} if sudo_failed_resolved else {}),
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
    if kind != "clarification_reply" and session_ctx.get("pendingClarification"):
        session_ctx["pendingClarification"] = None
        save_session_context(session_ctx, now_ms=now_ms)
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
        num_match = re.match(r"^(?:#|option\s+|lua chon\s+|y\s+|idea\s+|chon\s+|phuong an\s+)?([1-3])(?:\s*[.)])?$", folded_reply)

        if clar_kind == "plan_followup":
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
            if orig_goal and orig_goal.lower() not in chosen_direction.lower() and "—" not in chosen_direction:
                refined_plan_prompt = f"{orig_goal} — {chosen_direction}"
            elif "—" not in chosen_direction:
                refined_plan_prompt = f"{chosen_direction} — {str((selected_opt or {}).get('label') or ('Chi tiết' if lang == 'vi' else 'Details'))}"
            else:
                refined_plan_prompt = chosen_direction
            return execute_agent_turn(
                {**options, "prompt": refined_plan_prompt},
                companion_root=companion_root,
                host_root=host_root,
            )

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
        if session_ctx.get("pendingClientAction"):
            pca = dict(session_ctx.get("pendingClientAction") or {})
            pca_cid = str(pca.get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            session_ctx["pendingClientAction"] = None
            session_ctx["pendingConfirmId"] = ""
            save_session_context(session_ctx, now_ms=now_ms)
            pca_res_state = "confirmed" if decision == "confirm" else "cancelled"
            emitted_actions: list[dict] = []
            if decision == "confirm":
                p_act = str(pca.get("action") or "")
                p_tgt = str(pca.get("target") or "")
                p_val = str(pca.get("value") or "")
                if p_act == "rag_note_save":
                    saved_note = nexus_rag.save_rag_note(
                        p_tgt or "Companion Note",
                        p_val or p_tgt,
                        folder=str(pca.get("folder") or "Knowledge"),
                        vault_path=str(pca.get("vaultPath") or _tctx().current_turn_nexus_vault),
                    )
                    msg = (
                        f"Đã lưu ghi chú tri thức RAG `{saved_note['title']}` vào Nexus (`{saved_note['relPath']}`) và lập chỉ mục FTS5."
                        if lang == "vi"
                        else f"Saved RAG knowledge note `{saved_note['title']}` in Nexus (`{saved_note['relPath']}`) and indexed it."
                    )
                else:
                    emitted_actions.append(pca)
                    label_tgt = p_tgt or p_val
                    if p_act == "todo_add":
                        msg = (
                            f"Đã xác nhận và thêm việc cần làm vào Todo: `{label_tgt}`."
                            if lang == "vi"
                            else f"Confirmed and added Todo task: `{label_tgt}`."
                        )
                    elif p_act == "quicknote_add":
                        msg = (
                            f"Đã xác nhận và lưu ghi chú vào QuickNotes (`{p_tgt or 'Companion Note'}`)."
                            if lang == "vi"
                            else f"Confirmed and saved note to QuickNotes (`{p_tgt or 'Companion Note'}`)."
                        )
                    else:
                        msg = (
                            f"Đã xác nhận và thực thi thao tác `{p_act}`."
                            if lang == "vi"
                            else f"Confirmed and executed `{p_act}`."
                        )
                res = _format_reply(
                    msg,
                    expression="happy",
                    status="completed",
                    language=lang,
                    requires_confirmation=False,
                    session_ctx=session_ctx,
                )
            else:
                msg = (
                    "Đã huỷ thao tác ghi đang chờ; dữ liệu Todo, QuickNotes và Nexus được giữ nguyên."
                    if lang == "vi"
                    else "Cancelled pending write action; Todo, QuickNotes, and Nexus remain unchanged."
                )
                res = _format_reply(msg, expression="idle", status="cancelled", language=lang, session_ctx=session_ctx)
            pca_summary = a2ui_validator._clean_str(msg, 280).strip()
            history_store.lock_confirm_surfaces(pca_cid, resolution=pca_res_state, outcome_summary=pca_summary)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "clientActions": emitted_actions,
                "resolvedConfirmId": pca_cid,
                "resolvedConfirmResolution": pca_res_state,
                "resolvedConfirmSummary": pca_summary,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        if session_ctx.get("pendingMemoryForget"):
            mem_target = session_ctx.get("pendingMemoryForget")
            mem_cid = str((mem_target or {}).get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            session_ctx["pendingMemoryForget"] = None
            session_ctx["pendingConfirmId"] = ""
            save_session_context(session_ctx, now_ms=now_ms)
            mem_res_state = "confirmed" if decision == "confirm" else "cancelled"
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
            mem_summary = a2ui_validator._clean_str(msg, 280).strip()
            history_store.lock_confirm_surfaces(mem_cid, resolution=mem_res_state, outcome_summary=mem_summary)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "resolvedConfirmId": mem_cid,
                "resolvedConfirmResolution": mem_res_state,
                "resolvedConfirmSummary": mem_summary,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        if session_ctx.get("pendingDeletePath"):
            del_path = session_ctx.get("pendingDeletePath")
            del_cid = str(session_ctx.get("pendingDeleteConfirmId") or session_ctx.get("pendingConfirmId") or "")
            session_ctx["pendingDeletePath"] = ""
            session_ctx["pendingDeleteConfirmId"] = ""
            session_ctx["pendingConfirmId"] = ""
            save_session_context(session_ctx, now_ms=now_ms)
            del_res_state = "confirmed" if decision == "confirm" else "cancelled"
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
            del_summary = a2ui_validator._clean_str(msg, 280).strip()
            history_store.lock_confirm_surfaces(del_cid, resolution=del_res_state, outcome_summary=del_summary)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "resolvedConfirmId": del_cid,
                "resolvedConfirmResolution": del_res_state,
                "resolvedConfirmSummary": del_summary,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        resolved_conf = resolve_pending_confirmation(decision="confirm" if decision == "confirm" else "cancel", source="user_prompt", now_ms=now_ms)
        if resolved_conf:
            res_cid = str(resolved_conf.get("confirmId") or "")
            res_state = "confirmed" if decision == "confirm" else "cancelled"
            session_ctx["pendingConfirmId"] = ""
            save_session_context(session_ctx, now_ms=now_ms)
            if decision == "confirm":
                payload = resolved_conf.get("payload") or {}
                grant_msg = f"Đã xác nhận và cấp quyền cho `{resolved_conf['targetPath']}`." if lang == "vi" else f"Confirmed and granted permission for `{resolved_conf['targetPath']}`."
                grant_summary = a2ui_validator._clean_str(grant_msg, 280).strip()
                if resolved_conf["actionKind"] == "grant_directory":
                    grant_permission(
                        "directory",
                        target=resolved_conf["targetPath"],
                        levels=payload.get("levels") or ["read", "write", "execute"],
                        ttl_ms=int(payload.get("ttlMs") or 3600_000),
                        source="user_prompt",
                        now_ms=now_ms,
                    )
                    history_store.lock_confirm_surfaces(res_cid, resolution=res_state, outcome_summary=grant_summary)
                    resume_prompt = payload.get("originalPrompt")
                    if resume_prompt:
                        resumed = execute_agent_turn(
                            {**options, "prompt": resume_prompt},
                            companion_root=companion_root,
                            host_root=host_root,
                        )
                        if isinstance(resumed, dict):
                            resumed.setdefault("resolvedConfirmId", res_cid)
                            resumed.setdefault("resolvedConfirmResolution", res_state)
                            resumed.setdefault("resolvedConfirmSummary", grant_summary)
                        return resumed
                elif resolved_conf["actionKind"] == "tainted_tool_escalation":
                    tool_name = str(payload.get("tool") or resolved_conf.get("targetPath") or "").strip()
                    tool_args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
                    tool_out = ""
                    if tool_name:
                        try:
                            tool_out = _execute_llm_tool_call(
                                {"name": tool_name, "args": tool_args},
                                session_ctx,
                                companion_root or Path(__file__).resolve().parents[2],
                                now_ms,
                            )
                        except Exception as exc:
                            fail_msg = (
                                f"Thực thi `{tool_name}` sau xác nhận gặp lỗi: {exc}"
                                if lang == "vi"
                                else f"Confirmed `{tool_name}` failed: {exc}"
                            )
                            fail_sum = a2ui_validator._clean_str(fail_msg, 280).strip()
                            history_store.lock_confirm_surfaces(res_cid, resolution="failed", outcome_summary=fail_sum)
                            res = _format_reply(fail_msg, expression="worried", status="failed", language=lang, session_ctx=session_ctx)
                            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                            return {
                                "handled": True,
                                "intent": kind,
                                "resolvedConfirmId": res_cid,
                                "resolvedConfirmResolution": "failed",
                                "resolvedConfirmSummary": fail_sum,
                                **res,
                                "userMessageId": u_id,
                                "assistantMessageId": a_id,
                                "historySaved": saved,
                            }
                    grant_msg = (
                        f"Đã xác nhận và thực thi `{tool_name}`." + (f"\n\n{tool_out[:1200]}" if tool_out else "")
                        if lang == "vi"
                        else f"Confirmed and executed `{tool_name}`." + (f"\n\n{tool_out[:1200]}" if tool_out else "")
                    )
                    grant_summary = a2ui_validator._clean_str(grant_msg, 280).strip()
                    history_store.lock_confirm_surfaces(res_cid, resolution=res_state, outcome_summary=grant_summary)
                else:
                    history_store.lock_confirm_surfaces(res_cid, resolution=res_state, outcome_summary=grant_summary)
                msg = grant_msg
                res_summary = grant_summary
                res = _format_reply(msg, expression="happy", status="completed", language=lang, session_ctx=session_ctx)
            else:
                msg = "Đã từ chối cấp quyền và huỷ thao tác đang chờ." if lang == "vi" else "Denied permission and cancelled the pending action."
                res_summary = a2ui_validator._clean_str(msg, 280).strip()
                history_store.lock_confirm_surfaces(res_cid, resolution=res_state, outcome_summary=res_summary)
                res = _format_reply(msg, expression="idle", status="cancelled", language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "resolvedConfirmId": res_cid,
                "resolvedConfirmResolution": res_state,
                "resolvedConfirmSummary": res_summary,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

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

        # 8. Rule management via natural language or `/rule` / `/rules`
        if kind == "rule_manage":
            sub = intent["subkind"]
            if sub == "list":
                rstate = load_rules_state(now_ms)
                rules = rstate.get("rules", [])
                rules_txt = str(rstate.get("rulesText") or "").strip()
                parts = []
                if rules_txt:
                    parts.append(
                        (f"**Quy tắc hành vi (Rules):**\n{rules_txt}" if lang == "vi" else f"**Active Behavioral Rules:**\n{rules_txt}")
                    )
                if rules:
                    lines = [
                        f"- `{r['ruleId']}` ({r['triggerType']}, {'bật' if r['enabled'] else 'tắt'}): {r['taskPrompt']}"
                        for r in rules[:8]
                    ]
                    parts.append(
                        ("Danh sách quy tắc tự động:\n" if lang == "vi" else "Configured background rules:\n") + "\n".join(lines)
                    )
                elif not parts:
                    parts.append("Hiện chưa có quy tắc tự động nào được bật." if lang == "vi" else "No proactive background rules are currently configured.")
                msg = "\n\n".join(parts)
            elif sub == "reset":
                rstate = set_rules_text(reset=True, now_ms=now_ms)
                msg = (
                    f"Đã khôi phục Quy tắc (Rules) về mặc định:\n{rstate.get('rulesText', DEFAULT_RULES_TEXT)}"
                    if lang == "vi"
                    else f"Reset Rules to default:\n{rstate.get('rulesText', DEFAULT_RULES_TEXT)}"
                )
            elif sub == "disable":
                m_id = re.search(r"\b(rule_[0-9a-f]{8,16})\b", prompt)
                if m_id:
                    set_rule_enabled(rule_id=m_id.group(1), enabled=False, now_ms=now_ms)
                    msg = f"Đã tắt quy tắc `{m_id.group(1)}`." if lang == "vi" else f"Disabled rule `{m_id.group(1)}`."
                else:
                    rm_query = ""
                    if re.match(r"^/rules?\b", _fold_ascii(prompt)):
                        rm_query = re.sub(
                            r"^/rules?\b\s*(?:disable|delete|remove|stop|tat|xoa|huy)\s*:?\s*",
                            "",
                            prompt.strip(),
                            flags=re.IGNORECASE,
                        ).strip()
                    folded_rm = _fold_ascii(rm_query)
                    if rm_query and folded_rm not in ("all", "tat ca", "toan bo", "everything", "rules"):
                        rm_res = remove_instruction_rule(rm_query, now_ms=now_ms)
                        removed_lines = rm_res.get("removedLines") or []
                        if removed_lines:
                            msg = (
                                f"Đã xoá khỏi Quy tắc (Rules): `{removed_lines[0]}`\n\n**Quy tắc hiện tại:**\n{rm_res.get('rulesText', '')}"
                                if lang == "vi"
                                else f"Removed from Rules: `{removed_lines[0]}`\n\n**Active Rules:**\n{rm_res.get('rulesText', '')}"
                            )
                        else:
                            set_rule_enabled(disable_all=True, enabled=False, now_ms=now_ms)
                            msg = "Đã tắt toàn bộ quy tắc tự khởi việc." if lang == "vi" else "Disabled all proactive background rules."
                    else:
                        set_rule_enabled(disable_all=True, enabled=False, now_ms=now_ms)
                        msg = "Đã tắt toàn bộ quy tắc tự khởi việc." if lang == "vi" else "Disabled all proactive background rules."
            else:
                folded = _fold_ascii(prompt)
                planner_events = options.get("plannerEvents") if isinstance(options.get("plannerEvents"), list) else []
                m_time = re.search(r"\b(\d{1,2}:\d{2})\b", prompt)
                m_ev = re.search(r"\b(evt_[A-Za-z0-9_\-]+)\b", prompt)
                is_trigger_rule = bool(
                    m_time
                    or m_ev
                    or planner_events
                    or any(
                        kw in folded
                        for kw in (
                            "focus", "pomodoro", "planner", "when ", "every ",
                            "khi su kien", "khi bat dau", "sau khi ket thuc", "moi ngay",
                        )
                    )
                )
                if not is_trigger_rule and re.match(r"^/rules?\b", folded):
                    clean_rule_line = re.sub(r"^/rules?\b\s*(?:add|create|set|them|tao)?\s*:?\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()
                    rstate = append_instruction_rule(clean_rule_line or prompt.strip(), now_ms=now_ms)
                    msg = (
                        f"Đã thêm vào Quy tắc (Rules): `- {clean_rule_line}`\n\n**Quy tắc hiện tại:**\n{rstate.get('rulesText', '')}"
                        if lang == "vi"
                        else f"Added to Rules: `- {clean_rule_line}`\n\n**Active Rules:**\n{rstate.get('rulesText', '')}"
                    )
                else:
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
            latest_rules = load_rules_state(now_ms)
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, "agentRules": latest_rules, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

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
                    mem_cid = _issue_confirm_id("conf")
                    session_ctx["pendingMemoryForget"] = {"id": matched_mem["id"], "text": matched_mem["text"], "all": False, "confirmId": mem_cid}
                    session_ctx["pendingConfirmId"] = mem_cid
                    save_session_context(session_ctx, now_ms=now_ms)
                    msg = (
                        f"Bạn có chắc muốn xoá ghi nhớ #{matched_mem['id']} (\"{matched_mem['text']}\") khỏi Companion không? "
                        "Hãy trả lời 'đồng ý' để xác nhận hoặc 'huỷ' để giữ lại (lưu ý: các bản sao hoặc backup bên ngoài sẽ không bị xoá)."
                        if lang == "vi"
                        else f"Confirm deleting memory #{matched_mem['id']} (\"{matched_mem['text']}\") from Companion? "
                             "Reply 'yes' to confirm or 'cancel' to keep it (external copies or backups will not be erased)."
                    )
                    finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                    res = _format_reply(msg, expression="alert", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, confirm_id=mem_cid, session_ctx=session_ctx)
                elif items:
                    mem_cid = _issue_confirm_id("conf")
                    session_ctx["pendingMemoryForget"] = {"all": True, "confirmId": mem_cid}
                    session_ctx["pendingConfirmId"] = mem_cid
                    save_session_context(session_ctx, now_ms=now_ms)
                    msg = (
                        f"Bạn muốn xoá toàn bộ {len(items)} ghi nhớ đã lưu trong Companion phải không? Trả lời 'đồng ý' để xác nhận (bản sao/backup bên ngoài không bị ảnh hưởng)."
                        if lang == "vi"
                        else f"Do you want to delete all {len(items)} saved memories in Companion? Reply 'yes' to confirm (external copies/backups are not affected)."
                    )
                    finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                    res = _format_reply(msg, expression="alert", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, confirm_id=mem_cid, session_ctx=session_ctx)
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

        # 10b. Local Offline Voice Mode (STT / TTS / Status via pipewire-mpris-mcp & desktop_control)
        if kind == "voice_task":
            sub = str(intent.get("subkind") or "status")
            step_id = record_step_start(run_id, 1, f"voice_{sub}", side_effect=(sub == "tts"), now_ms=now_ms)
            if sub == "tts":
                clean_tts = re.sub(
                    r"^(?:/tts\s+|/speak\s+|(?:hãy\s+|hay\s+|vui lòng\s+|vui long\s+|please\s+)?(?:đọc to|doc to|phát âm|phat am|nói to|noi to|speak aloud|read aloud|say out loud))\s*:?\s*",
                    "",
                    prompt.strip(),
                    flags=re.IGNORECASE,
                ).strip() or prompt.strip()
                v_res = desktop_control.voice_tts(
                    text=clean_tts,
                    language=lang,
                    context_prompt=str(prompt or ""),
                )
                tts_backend = str(v_res.get("ttsBackend") or v_res.get("backend") or "tts")
                msg = (
                    f"🔊 Đã đọc thành tiếng qua `{tts_backend}`: \"{clean_tts}\""
                    if lang == "vi"
                    else f"🔊 Spoke aloud via `{tts_backend}`: \"{clean_tts}\""
                )
            elif sub == "stt":
                audio_p = str(options.get("audioPath") or _extract_path_from_text(prompt, session_ctx) or "").strip()
                v_res = desktop_control.voice_stt(
                    audio_path=audio_p,
                    duration_sec=int(options.get("durationSec") or 4),
                    language=lang,
                )
                transcript = str(v_res.get("transcript") or "").strip()
                stt_backend = str(v_res.get("sttBackend") or v_res.get("backend") or "stt")
                msg = (
                    f"🎙 Nhận diện giọng nói (`{stt_backend}`): \"{transcript}\""
                    if lang == "vi"
                    else f"🎙 Voice transcription (`{stt_backend}`): \"{transcript}\""
                )
            else:
                v_res = desktop_control.voice_status()
                msg = (
                    f"🎙 Trạng thái Voice ngoại tuyến: {v_res.get('summary', '')}"
                    if lang == "vi"
                    else f"🎙 Offline Voice status: {v_res.get('summary', '')}"
                )
            record_step_finish(step_id, status="completed", detail=v_res)
            advance_turn(run_id, now_ms=now_ms)
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "voiceResult": v_res,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 11. Screen / Active Window Observation (routed via computer-use-linux MCP)
        if kind == "screen_observe":
            sc = _run_screen_observe_core(
                prompt=prompt,
                intent=intent,
                options=options,
                session_ctx=session_ctx,
                companion_root=companion_root,
                allow_screenshot=allow_screenshot,
                lang=lang,
                run_id=run_id,
                now_ms=now_ms,
                step_idx=1,
            )
            observations.append(sc["observation"])
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(
                sc["display"],
                spoken_text=sc["spoken"],
                expression="working",
                status="completed",
                run_id=run_id,
                language=lang,
                observations=observations,
                session_ctx=session_ctx,
                images=sc["images"],
            )
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "elements": sc["elements"],
                "semanticSnapshot": sc["semanticSnapshot"],
                "somImagePath": sc["somImagePath"],
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 11b. Compound Multi-Intent Orchestration (e.g. screenshot + weather / todos / music / system in one turn)
        if kind == "compound_task":
            subtasks = [s for s in (intent.get("subtasks") or []) if isinstance(s, dict)]
            display_sections: list[str] = []
            spoken_parts: list[str] = []
            all_images: list[str] = []
            all_obs: list[dict] = []
            all_client_actions: list[dict] = []
            subtask_labels: list[str] = []
            som_meta: dict = {}

            section_titles_vi = {
                "weather": "Thời tiết",
                "music": "Nhạc cục bộ",
                "timers": "Pomodoro & Tập trung",
                "todos": "Việc cần làm",
                "schedule": "Lịch trình",
                "quicknotes": "QuickNotes",
                "notifications": "Thông báo",
                "screentime": "Thời gian dùng máy",
                "learning": "Spaced Repetition",
                "fitness": "Tập luyện",
                "system": "Hệ thống",
            }
            section_titles_en = {
                "weather": "Weather",
                "music": "Local Music",
                "timers": "Pomodoro & Focus",
                "todos": "Todos",
                "schedule": "Schedule",
                "quicknotes": "QuickNotes",
                "notifications": "Notifications",
                "screentime": "ScreenTime",
                "learning": "Spaced Repetition",
                "fitness": "Workouts",
                "system": "System",
            }
            ctrl_labels_vi = {
                "weather_refresh": "Đã kích hoạt làm mới dữ liệu Thời tiết & AQI.",
                "music_shuffle": "Đã bật/tắt chế độ trộn bài (Shuffle).",
                "pomodoro_start": "Đã bắt đầu phiên tập trung Pomodoro.",
                "pomodoro_pause": "Đã tạm dừng Pomodoro.",
                "pomodoro_reset": "Đã đặt lại Pomodoro.",
                "nexus_open": "Đang mở không gian làm việc Nexus.",
                "nexus_open_daily": "Đang mở Daily Note hôm nay trong Nexus.",
            }
            ctrl_labels_en = {
                "weather_refresh": "Triggered Alis Weather & AQI refresh.",
                "music_shuffle": "Toggled Local Music shuffle mode.",
                "pomodoro_start": "Started Pomodoro focus timer.",
                "pomodoro_pause": "Paused Pomodoro focus timer.",
                "pomodoro_reset": "Reset Pomodoro focus timer.",
                "nexus_open": "Opening Nexus workspace.",
                "nexus_open_daily": "Opening today's Daily Note in Nexus.",
            }

            for s_idx, sub in enumerate(subtasks, start=1):
                skind = str(sub.get("kind") or "")
                if skind == "screen_observe":
                    subtask_labels.append("screen_observe")
                    sc = _run_screen_observe_core(
                        prompt=prompt,
                        intent=sub,
                        options=options,
                        session_ctx=session_ctx,
                        companion_root=companion_root,
                        allow_screenshot=allow_screenshot,
                        lang=lang,
                        run_id=run_id,
                        now_ms=now_ms,
                        step_idx=s_idx,
                        vlm_prompt_override=(
                            "Mô tả súc tích những gì đang hiển thị trên màn hình."
                            if lang == "vi"
                            else "Concisely describe what is currently visible on the screen."
                        ),
                    )
                    all_obs.append(sc["observation"])
                    for im_p in sc["images"]:
                        if im_p and im_p not in all_images:
                            all_images.append(im_p)
                    if sc.get("elements") or sc.get("somImagePath"):
                        som_meta = {
                            "elements": sc["elements"],
                            "semanticSnapshot": sc["semanticSnapshot"],
                            "somImagePath": sc["somImagePath"],
                        }
                    hdr = "Màn hình" if lang == "vi" else "Screen"
                    display_sections.append(f"**{hdr}**\n{sc['display']}")
                    if sc.get("spoken"):
                        spoken_parts.append(str(sc["spoken"]).strip())

                elif skind == "alis_local_info":
                    topic = str(sub.get("topic") or "all")
                    subtask_labels.append(f"alis_local_info:{topic}")
                    st_id = record_step_start(run_id, s_idx, f"alis_{topic}", side_effect=False, now_ms=now_ms)
                    live_res = alis_local_bridge.format_live_state(_tctx().current_turn_live_snapshot, topic=topic, query=prompt, lang=lang)
                    md_part = str(live_res.get("markdown") or "").strip()
                    if topic == "system":
                        try:
                            tel_res = desktop_control.get_system_telemetry()
                            tel_lines = "\n".join(tel_res.get("lines", [])[:4])
                            if tel_lines and tel_lines not in md_part:
                                md_part = f"{md_part}\n{tel_lines}".strip()
                        except Exception:
                            pass
                    record_step_finish(st_id, status="completed", detail=live_res.get("data"))
                    advance_turn(run_id, now_ms=now_ms)
                    hdr = (section_titles_vi if lang == "vi" else section_titles_en).get(topic, topic.title())
                    display_sections.append(f"**{hdr}**\n{md_part}")
                    sp_part = str(live_res.get("spoken") or "").strip()
                    if sp_part:
                        spoken_parts.append(sp_part)

                elif skind == "alis_service_control":
                    act = str(sub.get("action") or "")
                    tgt = str(sub.get("target") or "")
                    val = str(sub.get("value") or "")
                    subtask_labels.append(f"alis_service_control:{act}")
                    st_id = record_step_start(run_id, s_idx, f"alis_ctrl_{act}", side_effect=True, now_ms=now_ms)
                    client_act = {"action": act, "target": tgt, "value": val}
                    all_client_actions.append(client_act)
                    if act in ("night_light_set", "gamemode_set", "dnd_set"):
                        lbl_base = {"night_light_set": "Night Light", "gamemode_set": "Game Mode", "dnd_set": "Do Not Disturb"}[act]
                        act_msg = (f"Đã {'bật' if val == 'on' else 'tắt'} {lbl_base}." if lang == "vi" else f"Turned {lbl_base} {val}.")
                    else:
                        act_msg = (ctrl_labels_vi if lang == "vi" else ctrl_labels_en).get(act, f"Executed {act}.")
                    record_step_finish(st_id, status="completed", detail=client_act)
                    advance_turn(run_id, now_ms=now_ms)
                    display_sections.append(act_msg)
                    spoken_parts.append(act_msg)

                elif skind == "nexus_knowledge":
                    sub_k = str(sub.get("subkind") or "search").strip().lower()
                    q_nx = str(sub.get("query") or prompt).strip()
                    v_override = _tctx().current_turn_nexus_vault
                    subtask_labels.append(f"nexus_knowledge:{sub_k}")
                    st_id = record_step_start(run_id, s_idx, f"nexus_{sub_k}", side_effect=False, now_ms=now_ms)
                    if sub_k in ("overview", "journal", "finance", "collections"):
                        ov = nexus_rag.get_nexus_overview(vault_path=v_override, topic=sub_k)
                        nx_md = alis_local_bridge.format_nexus_reply(sub_k, ov, query=q_nx, lang=lang)
                        nx_det = ov.get("stats")
                    else:
                        nx_res = nexus_rag.search_nexus_knowledge(q_nx, vault_path=v_override, top_k=4, token_budget=700)
                        nx_md = alis_local_bridge.format_nexus_reply("search", nx_res, query=q_nx, lang=lang)
                        nx_det = {"matches": len(nx_res.get("citations") or [])}
                    record_step_finish(st_id, status="completed", detail=nx_det)
                    advance_turn(run_id, now_ms=now_ms)
                    display_sections.append(f"**Nexus**\n{nx_md}")
                    nx_spoken = desktop_control._clean_text_for_tts(nx_md, context_prompt=prompt, max_chars=260)
                    if nx_spoken:
                        spoken_parts.append(nx_spoken)

            observations.extend(all_obs)
            display_msg = "\n\n".join(s for s in display_sections if s).strip()
            clean_spoken_parts = []
            for sp in spoken_parts:
                sp_s = " ".join(sp.split()).strip()
                if not sp_s:
                    continue
                if sp_s[-1] not in ".!?;:…":
                    sp_s += "."
                clean_spoken_parts.append(sp_s)
            spoken_msg = " ".join(clean_spoken_parts).strip()

            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(
                display_msg,
                spoken_text=spoken_msg,
                expression="working",
                status="completed",
                run_id=run_id,
                language=lang,
                observations=observations,
                session_ctx=session_ctx,
                images=all_images,
            )
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "subtaskIntents": subtask_labels,
                "clientActions": all_client_actions,
                **som_meta,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 12. GUI / Niri action broker (routed via computer-use-linux MCP)
        if kind == "gui_action":
            if run_source == "rule":
                raise AgentPermissionError("rule_gui_forbidden", "Background rules are not allowed to control mouse/keyboard or focus.")
            folded_gui = _fold_ascii(prompt)
            sub_gui = str(intent.get("subkind") or "")
            is_fill_action = sub_gui == "fill" or bool(
                re.search(r"\b(?:fill|set_text|type|nhap|dien)\s+(?:vao\s+|into\s+)?(?:\[\d+\]|@e\d+\b)", folded_gui)
            )
            is_focus_elem = sub_gui == "focus_element" or bool(
                re.search(r"\bfocus(?:_element)?\s+(?:vao\s+|on\s+)?(?:\[\d+\]|@e\d+\b)", folded_gui)
            )
            is_atspi_action = sub_gui == "atspi_action" or bool(
                re.search(r"\batspi_action\b", folded_gui)
            )
            is_som_click = (
                is_fill_action
                or is_focus_elem
                or is_atspi_action
                or sub_gui == "som_click"
                or bool(
                    re.search(
                        r"\b(?:som_click|click\s+(?:mark\s+|element\s+|button\s+|into\s+)?(?:\[?#?\d+\]?|@e\d+\b)|click\s+(?:button|element|nut)\s+['\"`][^'\"`]+['\"`])",
                        folded_gui,
                    )
                )
            )
            try:
                if is_som_click:
                    m_ref = re.search(r"(@e\d+)\b", folded_gui)
                    m_mark = (
                        re.search(r"(?:mark|element|o|so|\[|#|@e)\s*(\d+)\b", folded_gui)
                        or re.search(r"\b(?:click|bam|nhan|tap|press|chon|fill|set_text|type|nhap|dien|focus|focus_element|atspi_action)\s+(?:vao\s+|into\s+|on\s+|mark\s+|element\s+|button\s+|nut\s+)?(?:\[?#?(\d+)\]?|@e(\d+)\b)", folded_gui)
                        or re.search(r"^/som\s+(?:\[?#?(\d+)\]?|@e(\d+)\b)", folded_gui)
                    )
                    m_elem = re.search(r"""(?:button|element|nut|click)\s+['"`]([^'"`]+)['"`]""", prompt, re.IGNORECASE)
                    m_role = re.search(r"\b(button|entry|text|link|checkbox|menuitem|tab)\b", folded_gui)
                    m_win_explicit = re.search(r"(?:window|cua so)\s+#?(\d+)\b", folded_gui)
                    win_id = int(m_win_explicit.group(1)) if m_win_explicit else None
                    ref_val = m_ref.group(1) if m_ref else ""
                    mark_digits = next((g for g in (m_mark.groups() if m_mark else ()) if g and g.isdigit()), None)
                    mark_val = int(mark_digits) if mark_digits else None
                    elem_val = m_elem.group(1).strip() if m_elem else ""
                    role_val = m_role.group(1).strip() if m_role else ""
                    fill_text = ""
                    if is_fill_action:
                        m_q_txt = re.search(r"""['"`]([^'"`]+)['"`]""", prompt)
                        if m_q_txt:
                            fill_text = m_q_txt.group(1)
                        else:
                            m_after_ref = re.search(r"(?:@e\d+|\[\d+\])\s+(?:with\s+|voi\s+|to\s+)?(.+)$", prompt, re.IGNORECASE)
                            if m_after_ref:
                                fill_text = m_after_ref.group(1).strip()
                    mcp_tool_name = (
                        "fill"
                        if is_fill_action
                        else ("focus_element" if is_focus_elem else ("atspi_action" if is_atspi_action else "som_click"))
                    )
                    mcp_gui = mcp_client.call_tool(
                        "computer-use-linux",
                        mcp_tool_name,
                        {
                            "ref": ref_val,
                            "markId": mark_val,
                            "element": elem_val,
                            "role": role_val,
                            "text": fill_text,
                            "targetWindowId": win_id,
                            "requireScale": True,
                            "nowMs": now_ms,
                        },
                        companion_root=companion_root,
                        enabled_overrides={"computer-use-linux": True},
                    )
                    gui_res = mcp_gui.get("structured")
                    if not isinstance(gui_res, dict) or "observation" not in gui_res:
                        gui_res = perform_gui_action(
                            action_type=mcp_tool_name,
                            target_window_id=win_id,
                            require_scale=True,
                            now_ms=now_ms,
                            text=fill_text,
                            mark_id=mark_val,
                            element_name=elem_val,
                            role=role_val,
                            ref=ref_val,
                        )
                else:
                    m_win = re.search(r"\b(\d+)\b", prompt)
                    win_id = int(m_win.group(1)) if m_win else session_ctx.get("lastWindowId")
                    req_scale = "click" in folded_gui or "toa do" in folded_gui
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
            is_explicit_som_cmd = folded_gui.startswith("/som")
            return {
                "handled": True,
                "intent": kind if is_explicit_som_cmd else ("screen_observe" if is_som_click else kind),
                "targetElement": gui_res.get("resolvedElement") or gui_res.get("targetElement"),
                "ref": gui_res.get("ref") or (gui_res.get("resolvedElement") or {}).get("ref") or "",
                "clickedCoords": {"x": gui_res.get("x"), "y": gui_res.get("y")} if "x" in gui_res else gui_res.get("clickedCoords"),
                "cursorEvent": gui_res.get("cursorEvent"),
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 13. Coding / LSP / Repo Map / Test Runner tasks
        if kind == "code_task":
            sub = intent["subkind"]
            folded_plan_check = _fold_ascii(prompt)
            raw_plan_path = _extract_path_from_text(prompt, session_ctx) if sub == "plan" else None
            explicit_plan_path = (
                raw_plan_path
                if raw_plan_path and Path(raw_plan_path).expanduser().exists()
                else None
            )
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
                    session_ctx["pendingConfirmId"] = str(conf.get("confirmId") or "")
                    save_session_context(session_ctx, now_ms=now_ms)
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
                display_goal = web_tools.clean_search_query(goal_text, for_plan=False) or goal_text
                display_goal = re.sub(r"^(?:cho\s+|for\s+)", "", display_goal, flags=re.IGNORECASE).strip() or goal_text

                # Perform live web search + top webpage reading when planning real-world / personal / travel / research tasks or when internet search is requested
                llm_plan_reply = ""
                web_context_text = ""
                fetched_pages_text = ""
                web_sources_md = ""
                web_highlights_lines: list[str] = []
                plan_steps: list[dict] = []
                plan_synthesized = False
                is_live_desktop = (
                    (str(options.get("runSource") or "") == "interactive" or options.get("synthesizeWithLlm") is True)
                    and os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1"
                    and not os.environ.get("AQUA_TEST_ENDPOINT")
                    and not (bool(os.environ.get("ALIS_TEST_ISOLATED")) and not bool(options.get("endpoint")) and not bool(os.environ.get("AQUA_TEST_WEB_FIXTURE")))
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
                            plan_steps.append({"stepId": w_step, "tool": "web_search", "status": "completed", "targetPath": search_q})
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
                                if options.get("fetchTopPages") is not False:
                                    effort_lvl = str(options.get("thinkingEffort") or "medium").strip().lower()
                                    max_fetch_pages = int(options.get("maxFetchPages") or (3 if effort_lvl in ("max", "xhigh", "high") else 2))
                                    _, fetched_pages_text = _fetch_top_search_pages(
                                        run_id,
                                        w_results,
                                        plan_steps,
                                        max_pages=max_fetch_pages,
                                        max_chars_per_page=1800,
                                        timeout_sec=4.0,
                                        now_ms=now_ms,
                                    )
                        except Exception as exc:
                            record_step_finish(w_step, status="failed", error_text=str(exc))

                if is_live_desktop:
                    plan_endpoint, clean_model = _resolve_live_llm(options)
                    if plan_endpoint:
                        t0_llm = int(time.time() * 1000)
                        s_step = record_step_start(
                            run_id,
                            len(plan_steps) + 1,
                            "llm_synthesize",
                            side_effect=False,
                            target_path=clean_model or "local-llm",
                            now_ms=now_ms,
                        )
                        try:
                            effort = str(options.get("thinkingEffort") or "medium").strip().lower()
                            plan_max_tokens = 1800 if effort in ("max", "xhigh", "high") else (1200 if effort == "medium" else 700)
                            combined_web_ctx = web_context_text
                            if fetched_pages_text:
                                combined_web_ctx = (combined_web_ctx + "\n\n" + fetched_pages_text).strip()
                            if is_code_plan and workspace:
                                rmap_ctx = build_repo_map(workspace, token_budget=300)
                                files_ctx = ", ".join(e["path"] for e in rmap_ctx["entries"][:8])
                                sys_prompt = (
                                    "Bạn là Alis-Companion. Hãy lập kế hoạch kỹ thuật chi tiết, rõ ràng bằng Markdown tiếng Việt (dùng tiêu đề ### và các bước cụ thể). Không sinh thẻ <think>. {REASON:none}"
                                    if lang == "vi"
                                    else "You are Alis-Companion. Write a thorough, actionable technical implementation plan in English Markdown (use ### headings and clear steps). Do not output <think>. {REASON:none}"
                                )
                                usr_prompt = f"{{REASON:none}}\nMục tiêu: {display_goal}\nWorkspace: {workspace} (Các tệp chính: {files_ctx})"
                                if combined_web_ctx:
                                    usr_prompt += f"\n\nThông tin tra cứu trên Internet:\n{combined_web_ctx[:3600]}"
                            else:
                                if combined_web_ctx.strip():
                                    sys_prompt = (
                                        "Bạn là Alis-Companion. Dựa trên kết quả tìm kiếm và nội dung chi tiết các trang web vừa đọc, hãy lập một kế hoạch đầy đủ, súc tích (~200-280 từ, mỗi mục 2-3 gạch đầu dòng ngắn gọn) bằng Markdown tiếng Việt với đủ 4 mục ###: "
                                        "1. Ý tưởng chủ đề & Chuẩn bị ngay, 2. Danh sách cần chuẩn bị (Đồ ăn & Thức uống, Vật dụng, Âm nhạc & Trò chơi), 3. Lịch trình theo khung giờ, và 4. Mẹo & Lưu ý quan trọng. "
                                        "Trích dẫn gọn tên miền nguồn tham khảo có thực trong dữ liệu bên dưới. Không sinh thẻ <think>. {REASON:none}"
                                        if lang == "vi"
                                        else "You are Alis-Companion. Using the web search results and webpage excerpts below, write a complete, concise, actionable plan (~200-280 words, 2-3 brief bullet points per section) in English Markdown covering all 4 ### sections: "
                                        "1. Concept, Theme & Immediate Prep, 2. Essential Checklist (Food & Drinks, Supplies, Music & Activities), 3. Time-Slotted Schedule, and 4. Host Tips. "
                                        "Cite key recommendations inline only from the provided domain names (e.g. [domain.com]). Ensure all 4 sections finish with complete sentences. Do not output <think>. {REASON:none}"
                                    )
                                    usr_prompt = (
                                        f"{{REASON:none}}\nPlanning Request: {display_goal}\n\n"
                                        f"Live Web Search & Webpage Excerpts:\n{combined_web_ctx[:4200]}\n\n"
                                        f"Synthesize these findings into a complete 4-section plan."
                                    )
                                else:
                                    sys_prompt = (
                                        "Bạn là Alis-Companion. Không có kết quả tìm kiếm web trực tuyến nào được trả về. Hãy lập một kế hoạch thực tế, đầy đủ, súc tích (~200-280 từ, mỗi mục 2-3 gạch đầu dòng ngắn gọn) bằng Markdown tiếng Việt với đủ 4 mục ###: "
                                        "1. Ý tưởng chủ đề & Chuẩn bị ngay, 2. Danh sách cần chuẩn bị (Đồ ăn & Thức uống, Vật dụng, Âm nhạc & Trò chơi), 3. Lịch trình theo khung giờ, và 4. Mẹo & Lưu ý quan trọng. "
                                        "Tuyệt đối KHÔNG bịa đặt tên miền, URL hay nguồn trích dẫn web. Không sinh thẻ <think>. {REASON:none}"
                                        if lang == "vi"
                                        else "You are Alis-Companion. No live web search results were retrieved. Write a complete, concise, actionable offline plan (~200-280 words, 2-3 brief bullet points per section) in English Markdown covering all 4 ### sections: "
                                        "1. Concept, Theme & Immediate Prep, 2. Essential Checklist (Food & Drinks, Supplies, Music & Activities), 3. Time-Slotted Schedule, and 4. Host Tips. "
                                        "Do NOT invent or cite any domain names, URLs, or web sources. Ensure all 4 sections finish with complete sentences. Do not output <think>. {REASON:none}"
                                    )
                                    usr_prompt = (
                                        f"{{REASON:none}}\nPlanning Request: {display_goal}\n\n"
                                        f"Note: Live web search returned 0 results. Provide a grounded offline plan without fabricating any web citations."
                                    )
                            body = {
                                "model": clean_model or "default",
                                "messages": [
                                    {"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": usr_prompt},
                                ],
                                "max_tokens": plan_max_tokens,
                                "temperature": (
                                    round(max(0.0, min(2.0, float(options["temperature"]))), 2)
                                    if isinstance(options.get("temperature"), (int, float))
                                    else 0.45
                                ),
                                "reasoning_effort": "none",
                                "chat_template_kwargs": {"enable_thinking": False},
                            }
                            if _is_mtp_model(clean_model):
                                body["repetition_penalty"] = 1.0
                                body["repeat_penalty"] = 1.0
                            raw_content = _call_chat_completion_text(plan_endpoint, body, chunk_timeout=65.0, max_wall_sec=120.0)
                            if raw_content:
                                llm_plan_reply = raw_content
                                plan_synthesized = True
                            record_step_finish(
                                s_step,
                                status="completed" if llm_plan_reply else "failed",
                                target_path=clean_model or "local-llm",
                                detail={"chars": len(llm_plan_reply)},
                            )
                        except Exception as exc:
                            record_step_finish(s_step, status="failed", target_path=clean_model or "local-llm", error_text=str(exc))
                        finally:
                            dt_llm = max(1, int(round((int(time.time() * 1000) - t0_llm) / 1000.0)))
                            _emit_activity_step(f"Thought for {dt_llm}s ›", kind="llm_think", status="completed")

                folded_goal = _fold_ascii(display_goal)
                is_party_or_evening = any(
                    k in folded_goal
                    for k in ("tiec", "party", "sinh nhat", "birthday", "bbq", "8 gio toi", "20:00", "20h", "buoi toi", "toi mai", "dinner", "lien hoan")
                )
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
                            f"  2. Thực hiện chỉnh sửa nguyên tử đúng phạm vi được giao (hỏi ý kiến nếu ảnh hưởng module khác và tự động hoàn tác nếu không hiệu quả).\n"
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
                            f"  2. Apply focused surgical edits (ask with options if other components are affected, and revert ineffective changes).\n"
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
                        if is_party_or_evening:
                            msg = (
                                f"### Kế hoạch chi tiết: {display_goal}{highlights_block}\n"
                                f"- **1. Ý tưởng chủ đề & Danh sách chuẩn bị:**\n"
                                f"  - Xác nhận không gian tổ chức (tại nhà, sân thượng BBQ hoặc nhà hàng), số lượng khách mời và thực đơn chính kèm đồ uống.\n"
                                f"  - Chuẩn bị loa Bluetooth/playlist nhạc, ánh sáng ấm, bộ dụng cụ ăn uống, đá lạnh và trò chơi tương tác nhóm (Boardgame / Minigame).\n"
                                f"- **2. Lịch trình khung giờ buổi tối (20:00):**\n"
                                f"  - **19:00 – Chuẩn bị & Đón khách:** Bày trí bàn tiệc, kiểm tra âm thanh/ánh sáng và chuẩn bị đồ uống chào mừng (welcome drink).\n"
                                f"  - **20:00 – Khai tiệc & Ẩm thực:** Bắt đầu bữa tiệc chính lúc 8 giờ tối, nâng ly chúc mừng và thưởng thức các món chính.\n"
                                f"  - **21:00 – Giao lưu & Hoạt động nhóm:** Tổ chức minigame/boardgame, âm nhạc thư giãn, cắt bánh và chụp ảnh kỷ niệm.\n"
                                f"  - **22:00 – Kết thúc & Thu dọn:** Dùng tráng miệng nhẹ, hỗ trợ khách đặt xe về an toàn và thu dọn không gian.\n"
                                f"- **3. Lưu ý:** Chuẩn bị thêm món nhẹ/không cồn, lưu ý giảm âm lượng nhạc sau 22:00 và bố trí phương tiện di chuyển an toàn."
                            )
                        else:
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
                        if is_party_or_evening:
                            msg = (
                                f"### Action Plan: {display_goal}{highlights_block}\n"
                                f"- **1. Theme & Preparation Checklist:**\n"
                                f"  - Confirm venue (home, rooftop BBQ, or private lounge), guest count, main menu, and beverages.\n"
                                f"  - Prepare speaker/playlist, warm lighting, tableware, ice, and group party games.\n"
                                f"- **2. Evening Schedule (20:00 Start):**\n"
                                f"  - **19:00 – Setup & Welcome:** Arrange the table, test lighting/music, and serve welcome drinks.\n"
                                f"  - **20:00 – Kickoff & Main Course:** Start the party at 8:00 PM with a toast and main dishes.\n"
                                f"  - **21:00 – Games & Socializing:** Group games, music, dessert/cake, and group photos.\n"
                                f"  - **22:00 – Wrap-Up:** Wind down, arrange safe rides home for guests, and tidy up.\n"
                                f"- **3. Host Tips:** Keep non-alcoholic and dietary options ready, lower music volume after 22:00, and plan safe transport."
                            )
                        else:
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
                if web_sources_md and web_sources_md not in msg:
                    src_label = "📚 **Nguồn tra cứu:**" if lang == "vi" else "📚 **Sources:**"
                    msg = f"{msg.rstrip()}\n\n{src_label} {web_sources_md}"
                plan_clarification = None
                if "—" not in prompt:
                    plan_goal_prefix = (
                        (f"Lên kế hoạch cho {display_goal}" if not re.search(r"\b(?:kế hoạch|ke hoach)\b", display_goal, re.I) else display_goal)
                        if lang == "vi"
                        else (f"Create a plan for {display_goal}" if not re.search(r"\bplan\b", display_goal, re.I) else display_goal)
                    )
                    if is_party_or_evening:
                        clar_q = (
                            "Để mình tinh chỉnh thực đơn, chi phí và kịch bản sát nhất cho buổi tiệc của bạn, bạn muốn chọn phong cách nào?"
                            if lang == "vi"
                            else "To tailor the menu, budget, and schedule to your exact party style, which option do you prefer?"
                        )
                        clar_opts = (
                            [
                                {
                                    "label": "Tiệc ấm cúng tại nhà (Home Party / Đồ nướng & Lẩu)",
                                    "description": "Chủ động không gian, tối ưu chi phí và dễ chuẩn bị cho nhóm 5–12 người",
                                    "value": "Tiệc ấm cúng tại nhà với thực đơn đồ nướng/lẩu và boardgame",
                                    "recommended": True,
                                },
                                {
                                    "label": "Tiệc BBQ ngoài trời / Sân thượng (Chill & Acoustic)",
                                    "description": "Không gian thoáng đãng, phù hợp tiệc nướng tối và giao lưu âm nhạc",
                                    "value": "Tiệc BBQ ngoài trời/sân thượng phong cách chill & acoustic",
                                    "recommended": False,
                                },
                                {
                                    "label": "Đặt bàn tại Nhà hàng / Lounge riêng tư",
                                    "description": "Tiện lợi trọn gói, không mất thời gian nấu nướng và dọn dẹp",
                                    "value": "Tổ chức tại nhà hàng/lounge riêng tư phục vụ trọn gói",
                                    "recommended": False,
                                },
                            ]
                            if lang == "vi"
                            else [
                                {
                                    "label": "Cozy Home Party (Finger Food / Hotpot & Boardgames)",
                                    "description": "Flexible, budget-friendly setup ideal for 5–12 guests",
                                    "value": "Cozy home party with finger food and boardgames",
                                    "recommended": True,
                                },
                                {
                                    "label": "Outdoor / Rooftop BBQ & Acoustic Night",
                                    "description": "Open-air evening vibe with grilled dishes and music",
                                    "value": "Outdoor rooftop BBQ and acoustic party",
                                    "recommended": False,
                                },
                                {
                                    "label": "Private Restaurant / Lounge Reservation",
                                    "description": "Full-service dining with zero prep or cleanup",
                                    "value": "Private restaurant or lounge reservation",
                                    "recommended": False,
                                },
                            ]
                        )
                    elif is_code_plan:
                        clar_q = (
                            "Bạn muốn mình triển khai bước tiếp theo cho kế hoạch kỹ thuật này theo hướng nào?"
                            if lang == "vi"
                            else "How would you like to proceed with implementing this technical plan?"
                        )
                        clar_opts = (
                            [
                                {
                                    "label": "Thực thi đúng phạm vi được giao & tự động hoàn tác nếu không hiệu quả",
                                    "description": "Chỉ sửa các tệp được chỉ định, hỏi nếu ảnh hưởng phần khác và chạy test xác minh",
                                    "value": "Thực thi đúng phạm vi chỉ định và chạy kiểm thử",
                                    "recommended": True,
                                },
                                {
                                    "label": "Kiểm tra ảnh hưởng chéo giữa các module trước khi sửa",
                                    "description": "Phân tích phụ thuộc và hiển thị diff preview để bạn duyệt trước",
                                    "value": "Phân tích ảnh hưởng chéo và xem trước diff",
                                    "recommended": False,
                                },
                                {
                                    "label": "Viết bản mẫu tối giản (Prototype) để kiểm chứng nhanh",
                                    "description": "Thử nghiệm giải pháp nhỏ gọn trước khi áp dụng rộng",
                                    "value": "Tạo bản mẫu tối giản (Prototype)",
                                    "recommended": False,
                                },
                            ]
                            if lang == "vi"
                            else [
                                {
                                    "label": "Execute strictly within assigned scope & auto-revert if ineffective",
                                    "description": "Modify only assigned files, ask if other modules are affected, and verify with tests",
                                    "value": "Execute strictly within assigned scope and run tests",
                                    "recommended": True,
                                },
                                {
                                    "label": "Audit cross-component impact & preview diff first",
                                    "description": "Inspect dependent callers and show diff preview before editing",
                                    "value": "Audit cross-component impact and preview diff",
                                    "recommended": False,
                                },
                                {
                                    "label": "Build a minimal prototype first",
                                    "description": "Validate the core idea with minimal code changes",
                                    "value": "Build a minimal prototype",
                                    "recommended": False,
                                },
                            ]
                        )
                    else:
                        clar_q = (
                            "Để mình hoàn thiện kế hoạch chi tiết sát với nhu cầu của bạn hơn, bạn muốn ưu tiên hướng nào?"
                            if lang == "vi"
                            else "To refine this plan further, which direction would you like to prioritize?"
                        )
                        clar_opts = (
                            [
                                {
                                    "label": "Chi tiết hoá lịch trình, ngân sách dự kiến & danh sách chuẩn bị",
                                    "description": "Bổ sung bảng chi phí ước tính và checklist mua sắm cụ thể",
                                    "value": "Chi tiết hoá lịch trình, ngân sách dự kiến và checklist mua sắm",
                                    "recommended": True,
                                },
                                {
                                    "label": "Gợi ý thêm các địa điểm & hoạt động trải nghiệm",
                                    "description": "So sánh các lựa chọn địa điểm và hoạt động nhóm phù hợp",
                                    "value": "Gợi ý thêm địa điểm và hoạt động trải nghiệm đặc sắc",
                                    "recommended": False,
                                },
                                {
                                    "label": "Phương án tối giản, gọn nhẹ & tiết kiệm thời gian",
                                    "description": "Ưu tiên dịch vụ có sẵn và giảm thiểu khâu chuẩn bị",
                                    "value": "Phương án tối giản, gọn nhẹ và tiết kiệm thời gian",
                                    "recommended": False,
                                },
                            ]
                            if lang == "vi"
                            else [
                                {
                                    "label": "Detailed schedule, budget breakdown & shopping checklist",
                                    "description": "Add estimated costs and an itemized preparation checklist",
                                    "value": "Detailed schedule, budget breakdown, and shopping checklist",
                                    "recommended": True,
                                },
                                {
                                    "label": "Compare alternative venues & group activities",
                                    "description": "Explore top venue options and interactive activities",
                                    "value": "Compare alternative venues and group activities",
                                    "recommended": False,
                                },
                                {
                                    "label": "Minimal-prep, time-saving setup",
                                    "description": "Streamline supplies and use ready-made services",
                                    "value": "Minimal-prep, time-saving setup",
                                    "recommended": False,
                                },
                            ]
                        )
                    plan_clarification = _build_clarification_payload(
                        question=clar_q,
                        options=clar_opts,
                        original_prompt=plan_goal_prefix,
                        language=lang,
                        kind="plan_followup",
                    )
                    session_ctx["pendingClarification"] = plan_clarification
                    save_session_context(session_ctx, now_ms=now_ms)
                    opt_lines = []
                    for item in plan_clarification["options"]:
                        rec_tag = (" (Khuyên dùng)" if lang == "vi" else " (Recommended)") if item.get("recommended") else ""
                        desc_part = f" — {item['description']}" if item.get("description") else ""
                        opt_lines.append(f"{item['number']}. **{item['label']}{rec_tag}**{desc_part}")
                    msg = f"{msg.rstrip()}\n\n**{plan_clarification['question']}**\n" + "\n".join(opt_lines)
            else:
                plan_synthesized = False
                plan_clarification = None
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
            res = _format_reply(
                msg,
                expression="working",
                status="completed",
                run_id=run_id,
                language=lang,
                session_ctx=session_ctx,
                clarification=locals().get("plan_clarification"),
            )
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {"handled": True, "intent": kind, "synthesized": bool(locals().get("plan_synthesized", False)), **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

        # 14. RAG Knowledge Retrieval & Source Management with Citations
        if kind == "rag_query":
            folded = _fold_ascii(prompt)
            raw_clean = re.sub(r"^(?:/rag|rag)\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()
            if intent.get("subkind") == "check_config" or domain_rag.is_config_check_query(prompt):
                msg = domain_rag.inspect_user_configuration(prompt, lang=lang, companion_root=companion_root)
                rag_synthesized = True
            elif intent.get("subkind") == "domain_concepts" or domain_rag.is_domain_concept_or_command_query(prompt):
                msg = domain_rag.format_domain_concepts_reply(prompt, lang=lang, companion_root=companion_root)
                rag_synthesized = True
            elif intent.get("subkind") == "domain_capability" or domain_rag.is_domain_capability_query(prompt):
                msg = domain_rag.format_domain_rag_capability_reply(prompt, lang=lang, companion_root=companion_root)
                rag_synthesized = True
            elif (
                not raw_clean
                or re.match(r"^(?:list|sources|status|danh sach|nguon)\b", _fold_ascii(raw_clean))
                or any(k in folded for k in ("xem nguon rag", "danh sach nguon rag", "list rag sources", "rag sources"))
            ) and not any(k in folded for k in ("them", "add", "xoa", "remove", "save", "luu")):
                sources = rag_store.list_sources()
                active_paths = rag_store.active_source_paths(include_pocket=True)
                sinfo = rag_store.storage_info()
                nx_ov = nexus_rag.get_nexus_overview(vault_path=_tctx().current_turn_nexus_vault) if sinfo.get("storageMode") == "nexus" else {}
                nx_note_cnt = int((nx_ov.get("stats") or {}).get("noteCount") or nx_ov.get("indexedNotes") or 0)
                rows = [f"- `{s['label']}` (`{s['path']}`)" for s in sources] or (["- (chưa có nguồn tuỳ chỉnh; đang dùng tài liệu tích hợp & AquaPocket)"] if lang == "vi" else ["- (no custom sources; using built-in docs & AquaPocket)"])
                storage_note = (
                    (
                        f"\n- Lưu trữ Nexus: `{sinfo['ragVaultDir']}` ({nx_note_cnt} ghi chú Vault)\n- Chỉ mục FTS5 ngoài Vault: `{sinfo['ragDbPath']}`"
                        if lang == "vi"
                        else f"\n- Nexus Storage: `{sinfo['ragVaultDir']}` ({nx_note_cnt} Vault notes)\n- External FTS5 Index: `{sinfo['ragDbPath']}`"
                    )
                    if sinfo.get("storageMode") == "nexus"
                    else ""
                )
                msg = (
                    f"Nguồn tri thức RAG đang hoạt động ({len(active_paths)} đường dẫn + tài liệu tích hợp):\n" + "\n".join(rows) + storage_note
                    if lang == "vi"
                    else f"Active RAG knowledge sources ({len(active_paths)} path(s) + built-in docs):\n" + "\n".join(rows) + storage_note
                )
            elif re.match(r"^(?:save|luu)\s+", _fold_ascii(raw_clean)):
                body_raw = re.sub(r"^(?:save|luu|lưu)\s+", "", raw_clean, flags=re.IGNORECASE).strip()
                if ":" in body_raw:
                    t_part, b_part = body_raw.split(":", 1)
                else:
                    t_part, b_part = (body_raw[:48] or "Companion Note"), body_raw
                rag_cid = _issue_confirm_id("conf")
                session_ctx["pendingClientAction"] = {
                    "confirmId": rag_cid,
                    "action": "rag_note_save",
                    "target": t_part.strip() or "Companion Note",
                    "value": b_part.strip() or t_part.strip(),
                    "folder": "Knowledge",
                    "vaultPath": _tctx().current_turn_nexus_vault,
                }
                session_ctx["pendingConfirmId"] = rag_cid
                save_session_context(session_ctx, now_ms=now_ms)
                finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                confirm_msg = (
                    f"Xác nhận lưu ghi chú RAG **`{t_part.strip()}`** vào thư mục Nexus (`90_System/99_RAG/Knowledge/`)? Trả lời **đồng ý** hoặc **huỷ**."
                    if lang == "vi"
                    else f"Confirm saving RAG note **`{t_part.strip()}`** to Nexus (`90_System/99_RAG/Knowledge/`)? Reply **confirm** or **cancel**."
                )
                res = _format_reply(
                    confirm_msg,
                    expression="thinking",
                    status="awaiting_confirmation",
                    run_id=run_id,
                    language=lang,
                    requires_confirmation=True,
                    confirm_id=rag_cid,
                    session_ctx=session_ctx,
                )
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}
            elif re.match(r"^(?:sync|update|refresh|dong bo|cap nhat)\b", _fold_ascii(raw_clean)) or any(k in folded for k in ("dong bo rag", "cap nhat rag", "sync rag", "update rag")):
                canon = domain_rag.sync_canonical_docs(allow_network=True)
                pers = domain_rag.sync_personal_context(force=True)
                msg = (
                    f"Đã đồng bộ hệ thống RAG 3 tầng: Canonical ({canon['totalDocuments']} tài liệu, cập nhật {canon['updatedCount']}, không đổi {canon['notModifiedCount']}), Personal ({pers['watchedFileCount']} tệp đã lập chỉ mục)."
                    if lang == "vi"
                    else f"Synchronized 3-stage Domain RAG: Canonical ({canon['totalDocuments']} docs, {canon['updatedCount']} updated, {canon['notModifiedCount']} unchanged), Personal ({pers['watchedFileCount']} files indexed)."
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
                home_resolved = Path.home().resolve(strict=False)
                extra = [
                    d["path"]
                    for d in inspect_permissions({"nowMs": now_ms})["directories"]
                    if Path(str(d.get("path") or "")).expanduser().resolve(strict=False) != home_resolved
                ]
                rag = retrieve_rag(query_text, companion_root=companion_root, extra_dirs=extra, token_budget=480, page=1, page_size=3)
                rag_synthesized = False
                if rag["snippets"]:
                    cites = "\n".join(f"- {s}" for s in rag["snippets"])
                    msg = (
                        f"Thông tin trích xuất từ RAG có dẫn nguồn ({rag['estimatedTokens']} tokens):\n{cites}"
                        if lang == "vi"
                        else f"Grounded RAG results with citations ({rag['estimatedTokens']} tokens):\n{cites}"
                    )
                    if options.get("synthesizeWithLlm") is True:
                        synth = _synthesize_observations_with_llm(prompt, cites, options, lang, "")
                        if synth:
                            msg += f"\n\n### {'Tổng hợp' if lang == 'vi' else 'Synthesis'}\n{synth}"
                            rag_synthesized = True
                else:
                    msg = "Không tìm thấy đoạn tài liệu khớp với truy vấn trong phạm vi được cấp." if lang == "vi" else "No matching documentation snippets found within permitted scope."
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "synthesized": locals().get("rag_synthesized", False),
                "attachedContextMarkdown": attached_info["contextMarkdown"],
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 14a. Live Alis Local Service Telemetry (Weather, Music, Pomodoro, Todo, QuickNotes, Notifications, ScreenTime, Learning, Fitness)
        if kind == "alis_local_info":
            topic = str(intent.get("topic") or "all")
            step_id = record_step_start(run_id, 1, f"alis_{topic}", side_effect=False, now_ms=now_ms)
            live_res = alis_local_bridge.format_live_state(_tctx().current_turn_live_snapshot, topic=topic, query=prompt, lang=lang)
            msg = str(live_res.get("markdown") or "")
            if topic == "system":
                try:
                    tel_res = desktop_control.get_system_telemetry()
                    tel_lines = "\n".join(tel_res.get("lines", [])[:6])
                    if tel_lines and tel_lines not in msg:
                        msg = f"{msg}\n\n{tel_lines}"
                except Exception:
                    pass
            record_step_finish(step_id, status="completed", detail=live_res.get("data"))
            advance_turn(run_id, now_ms=now_ms)
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(
                msg,
                spoken_text=str(live_res.get("spoken") or ""),
                expression="happy",
                status="completed",
                run_id=run_id,
                language=lang,
                session_ctx=session_ctx,
            )
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "topic": topic,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 14a2. Smart Alis Service Control (instant playback/toggles/navigation + confirmation-gated writes)
        if kind == "alis_service_control":
            act = str(intent.get("action") or "")
            tgt = str(intent.get("target") or "")
            val = str(intent.get("value") or "")
            req_conf = bool(intent.get("requiresConfirmation", False))
            if req_conf or act in alis_local_bridge.WRITE_CONFIRM_CLIENT_ACTIONS:
                ctrl_cid = _issue_confirm_id("conf")
                session_ctx["pendingClientAction"] = {
                    "confirmId": ctrl_cid,
                    "action": act,
                    "target": tgt,
                    "value": val,
                    "vaultPath": _tctx().current_turn_nexus_vault,
                }
                session_ctx["pendingConfirmId"] = ctrl_cid
                save_session_context(session_ctx, now_ms=now_ms)
                finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                desc_tgt = tgt or val
                if act == "todo_add":
                    msg = (
                        f"Xác nhận thêm việc cần làm **`{desc_tgt}`** vào Todo? Trả lời **đồng ý** hoặc **huỷ**."
                        if lang == "vi"
                        else f"Confirm adding Todo task **`{desc_tgt}`**? Reply **confirm** or **cancel**."
                    )
                elif act == "quicknote_add":
                    msg = (
                        f"Xác nhận lưu ghi chú **`{tgt or 'Companion Note'}`** vào QuickNotes? Trả lời **đồng ý** hoặc **huỷ**."
                        if lang == "vi"
                        else f"Confirm saving QuickNote **`{tgt or 'Companion Note'}`**? Reply **confirm** or **cancel**."
                    )
                else:
                    msg = (
                        f"Xác nhận thực thi thao tác `{act}` (`{desc_tgt}`)? Trả lời **đồng ý** hoặc **huỷ**."
                        if lang == "vi"
                        else f"Confirm executing `{act}` (`{desc_tgt}`)? Reply **confirm** or **cancel**."
                    )
                res = _format_reply(
                    msg,
                    expression="thinking",
                    status="awaiting_confirmation",
                    run_id=run_id,
                    language=lang,
                    requires_confirmation=True,
                    confirm_id=ctrl_cid,
                    session_ctx=session_ctx,
                )
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {
                    "handled": True,
                    "intent": kind,
                    "clientActions": [],
                    **res,
                    "userMessageId": u_id,
                    "assistantMessageId": a_id,
                    "historySaved": saved,
                }

            step_id = record_step_start(run_id, 1, f"alis_ctrl_{act}", side_effect=True, now_ms=now_ms)
            client_act = {"action": act, "target": tgt, "value": val}
            action_labels_vi = {
                "weather_refresh": "Đã kích hoạt làm mới dữ liệu Thời tiết & AQI.",
                "music_toggle": "Đã chuyển trạng thái phát/tạm dừng Local Music.",
                "music_next": "Đã chuyển sang bài tiếp theo.",
                "music_prev": "Đã quay lại bài trước đó.",
                "music_shuffle": "Đã bật/tắt chế độ trộn bài (Shuffle).",
                "music_stop": "Đã dừng phát nhạc.",
                "music_play_query": f"Đang tìm và phát bài `{tgt}` trong thư viện Local Music.",
                "pomodoro_toggle": "Đã bật/tạm dừng bộ đếm Pomodoro.",
                "pomodoro_start": "Đã bắt đầu phiên tập trung Pomodoro.",
                "pomodoro_pause": "Đã tạm dừng Pomodoro.",
                "pomodoro_reset": "Đã đặt lại Pomodoro.",
                "night_light_set": f"Đã {'bật' if val == 'on' else 'tắt'} Night Light.",
                "night_light_toggle": "Đã bật/tắt Night Light.",
                "gamemode_set": f"Đã {'bật' if val == 'on' else 'tắt'} Game Mode.",
                "gamemode_toggle": "Đã bật/tắt Game Mode.",
                "dnd_set": f"Đã {'bật' if val == 'on' else 'tắt'} chế độ Không làm phiền (DND).",
                "dnd_toggle": "Đã bật/tắt chế độ Không làm phiền (DND).",
                "nexus_open": "Đang mở không gian làm việc Nexus.",
                "nexus_open_daily": "Đang mở Daily Note hôm nay trong Nexus.",
            }
            action_labels_en = {
                "weather_refresh": "Triggered Alis Weather & AQI refresh.",
                "music_toggle": "Toggled Local Music playback.",
                "music_next": "Skipped to the next track.",
                "music_prev": "Returned to the previous track.",
                "music_shuffle": "Toggled Local Music shuffle mode.",
                "music_stop": "Stopped Local Music playback.",
                "music_play_query": f"Searching Local Music library to play `{tgt}`.",
                "pomodoro_toggle": "Toggled Pomodoro focus timer.",
                "pomodoro_start": "Started Pomodoro focus timer.",
                "pomodoro_pause": "Paused Pomodoro focus timer.",
                "pomodoro_reset": "Reset Pomodoro focus timer.",
                "night_light_set": f"Turned Night Light {val}.",
                "night_light_toggle": "Toggled Night Light.",
                "gamemode_set": f"Turned Game Mode {val}.",
                "gamemode_toggle": "Toggled Game Mode.",
                "dnd_set": f"Turned Do Not Disturb {val}.",
                "dnd_toggle": "Toggled Do Not Disturb.",
                "nexus_open": "Opening Nexus workspace.",
                "nexus_open_daily": "Opening today's Daily Note in Nexus.",
            }
            msg = (action_labels_vi if lang == "vi" else action_labels_en).get(act, f"Executed `{act}`.")
            record_step_finish(step_id, status="completed", detail=client_act)
            advance_turn(run_id, now_ms=now_ms)
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "clientActions": [client_act],
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 14a3. Unified Nexus Vault Knowledge & Collection Retrieval
        if kind == "nexus_knowledge":
            sub_k = str(intent.get("subkind") or "search").strip().lower()
            q_nx = str(intent.get("query") or prompt).strip()
            v_override = _tctx().current_turn_nexus_vault
            if sub_k == "save_rag_note":
                if ":" in q_nx:
                    t_part, b_part = q_nx.split(":", 1)
                else:
                    t_part, b_part = (q_nx[:48] or "Companion Note"), q_nx
                nx_cid = _issue_confirm_id("conf")
                session_ctx["pendingClientAction"] = {
                    "confirmId": nx_cid,
                    "action": "rag_note_save",
                    "target": t_part.strip() or "Companion Note",
                    "value": b_part.strip() or t_part.strip(),
                    "folder": "Knowledge",
                    "vaultPath": v_override,
                }
                session_ctx["pendingConfirmId"] = nx_cid
                save_session_context(session_ctx, now_ms=now_ms)
                finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                msg = (
                    f"Xác nhận lưu ghi chú **`{t_part.strip()}`** vào thư mục RAG trong Nexus (`90_System/99_RAG/Knowledge/`)? Trả lời **đồng ý** hoặc **huỷ**."
                    if lang == "vi"
                    else f"Confirm saving note **`{t_part.strip()}`** into Nexus RAG (`90_System/99_RAG/Knowledge/`)? Reply **confirm** or **cancel**."
                )
                res = _format_reply(
                    msg,
                    expression="thinking",
                    status="awaiting_confirmation",
                    run_id=run_id,
                    language=lang,
                    requires_confirmation=True,
                    confirm_id=nx_cid,
                    session_ctx=session_ctx,
                )
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

            step_id = record_step_start(run_id, 1, f"nexus_{sub_k}", side_effect=False, now_ms=now_ms)
            nx_synth = False
            if sub_k == "read":
                try:
                    note_d = nexus_rag.read_nexus_note(q_nx, vault_path=v_override)
                    msg = alis_local_bridge.format_nexus_reply("read", note_d, query=q_nx, lang=lang)
                except NexusRagError as exc:
                    msg = str(exc.message)
                nx_detail = {"subkind": sub_k}
            elif sub_k in ("overview", "journal", "finance", "collections"):
                ov = nexus_rag.get_nexus_overview(vault_path=v_override, topic=sub_k)
                msg = alis_local_bridge.format_nexus_reply(sub_k, ov, query=q_nx, lang=lang)
                nx_detail = ov.get("stats")
            else:
                nx_res = nexus_rag.search_nexus_knowledge(q_nx, vault_path=v_override, top_k=5, token_budget=900)
                raw_md = alis_local_bridge.format_nexus_reply("search", nx_res, query=q_nx, lang=lang)
                msg = raw_md
                if options.get("synthesizeWithLlm") is True and (nx_res.get("snippets") or nx_res.get("matchedCollections")):
                    obs_txt = "\n".join(nx_res.get("snippets") or [])
                    synth = _synthesize_observations_with_llm(prompt, obs_txt, lang, options)
                    if synth:
                        msg = f"{synth}\n\n{raw_md}"
                        nx_synth = True
                nx_detail = {"matches": len(nx_res.get("citations") or []), "indexedNotes": nx_res.get("indexedNotes", 0)}
            record_step_finish(step_id, status="completed", detail=nx_detail)
            advance_turn(run_id, now_ms=now_ms)
            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "synthesized": nx_synth,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

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
            dom_elements = []
            dom_action_res = {}

            m_url = re.search(r"(https?://[^\s'\"`<>]+)", raw_clean)
            if sub == "dom_snapshot":
                target_url = m_url.group(1) if m_url else (raw_clean.split()[-1] if raw_clean else "")
                step_id = record_step_start(run_id, 1, "browser_snapshot", side_effect=False, target_path=target_url, now_ms=now_ms)
                called = mcp_client.call_tool(
                    "playwright-mcp",
                    "browser_snapshot",
                    {"url": target_url, "maxElements": 40},
                    companion_root=companion_root,
                    enabled_overrides={"playwright-mcp": True},
                )
                snap_struct = called.get("structured") or {}
                dom_elements = snap_struct.get("elements") or []
                record_step_finish(step_id, status="completed", detail={"url": target_url, "elementCount": len(dom_elements)})
                advance_turn(run_id, now_ms=now_ms)
                out_text = "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))
                msg = (
                    f"Cây DOM tương tác qua `playwright-mcp` ({len(dom_elements)} phần tử):\n{out_text}"
                    if lang == "vi"
                    else f"Interactive DOM snapshot via `playwright-mcp` ({len(dom_elements)} element(s)):\n{out_text}"
                )
            elif sub == "dom_action":
                m_ref = re.search(r"\[?(e\d+)\]?", prompt, re.IGNORECASE)
                ref_id = m_ref.group(1).lower() if m_ref else "e1"
                if any(k in folded for k in ("fill", "dien")):
                    dom_act = "fill"
                elif any(k in folded for k in ("type", "go ")):
                    dom_act = "type"
                elif any(k in folded for k in ("select", "chon")):
                    dom_act = "select"
                else:
                    dom_act = "click"
                m_val = re.search(r"""['"`]([^'"`]+)['"`]""", prompt)
                val_str = m_val.group(1) if m_val else ""
                target_url = m_url.group(1) if m_url else ""
                step_id = record_step_start(run_id, 1, f"browser_{dom_act}", side_effect=True, target_path=ref_id, now_ms=now_ms)
                called = mcp_client.call_tool(
                    "playwright-mcp",
                    f"browser_{dom_act}",
                    {"url": target_url, "ref": ref_id, "value": val_str},
                    companion_root=companion_root,
                    enabled_overrides={"playwright-mcp": True},
                )
                dom_action_res = called.get("structured") or {}
                record_step_finish(step_id, status="completed", detail=dom_action_res)
                advance_turn(run_id, now_ms=now_ms)
                out_text = "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))
                msg = (
                    f"Đã thực hiện thao tác DOM qua `playwright-mcp`: {out_text}"
                    if lang == "vi"
                    else f"Executed DOM action via `playwright-mcp`: {out_text}"
                )
            elif sub == "fetch_url" or (m_url and sub != "open_browser"):
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
                fetch_structured = called.get("structured") or {}
                out_text = "\n".join(c.get("text", "") for c in called.get("content", []) if c.get("text"))
                fetch_has_content = bool(
                    not called.get("isError")
                    and fetch_structured.get("ok") is not False
                    and (
                        bool(fetch_structured.get("lines"))
                        or bool(fetch_structured.get("headings"))
                        or (not fetch_structured and out_text.strip() and "(No readable text" not in out_text and "Error:" not in out_text)
                    )
                )
                msg = (
                    f"Nội dung trích xuất từ trang web qua `fetch-mcp` (Eyes):\n{out_text}"
                    if lang == "vi"
                    else f"Webpage content extracted via `fetch-mcp` (Eyes):\n{out_text}"
                )
            elif sub == "open_browser":
                from web_tools import extract_clean_browser_query, short_source_domain
                engine = "google"
                if "youtube" in folded or re.search(r"\b(?:yt|mo bai|phat bai|bat bai|nghe bai|mo ca khuc|phat ca khuc|mo video|phat video|xem video|play song|play a song|play the song|play music|play video|play a video|play the video|play track|listen to)\b", folded):
                    engine = "youtube"
                elif "github" in folded:
                    engine = "github"
                elif "wikipedia" in folded or re.search(r"\bwiki\b", folded):
                    engine = "wikipedia"
                elif "duckduckgo" in folded or re.search(r"\bddg\b", folded):
                    engine = "duckduckgo"

                cleaned_q = extract_clean_browser_query(raw_clean, engine=engine)
                has_multi_query = bool(
                    m_url
                    and not cleaned_q.get("isDirectUrl")
                    and cleaned_q.get("query")
                    and str(cleaned_q.get("query")).strip() != m_url.group(1)
                )
                use_direct_url = bool(m_url and not has_multi_query)
                if use_direct_url:
                    target_str = m_url.group(1)
                    clean_query = target_str
                    wants_play = False
                else:
                    clean_query = str(cleaned_q.get("query") or "").strip()
                    engine = str(cleaned_q.get("engine") or engine)
                    wants_play = bool(cleaned_q.get("wantsPlay"))
                    target_str = clean_query or raw_clean

                step_id = record_step_start(run_id, 1, "browser_open", side_effect=True, target_path=target_str, now_ms=now_ms)
                opened_call = mcp_client.call_tool(
                    "playwright-mcp",
                    "browser_open",
                    {"target": raw_clean if not use_direct_url else target_str, "engine": engine, "playMedia": wants_play},
                    companion_root=companion_root,
                    enabled_overrides={"playwright-mcp": True},
                )
                opened_info = opened_call.get("structured") or {}
                record_step_finish(step_id, status="completed", detail=opened_info)
                advance_turn(run_id, now_ms=now_ms)

                opened_url = str(opened_info.get("url") or target_str)
                short_dom = short_source_domain(opened_url) if opened_url.startswith(("http://", "https://")) else opened_url
                md_link = f"[{short_dom}]({opened_url})" if opened_url.startswith(("http://", "https://")) else f"`{opened_url}`"
                clean_q_disp = str(opened_info.get("cleanQuery") or clean_query or "").strip()
                if opened_info.get("playedVideo"):
                    vid_title = str(opened_info.get("videoTitle") or clean_q_disp or target_str)
                    msg = (
                        f"Đã mở trình duyệt và phát **{vid_title}** trên YouTube ({md_link})."
                        if lang == "vi"
                        else f"Opened YouTube and started playing **{vid_title}** ({md_link})."
                    )
                elif engine == "youtube" and clean_q_disp and not use_direct_url:
                    msg = (
                        f"Đã mở YouTube và tìm kiếm **{clean_q_disp}** ({md_link})."
                        if lang == "vi"
                        else f"Opened YouTube and searched for **{clean_q_disp}** ({md_link})."
                    )
                elif clean_q_disp and not use_direct_url:
                    msg = (
                        f"Đã mở trình duyệt và tìm kiếm **{clean_q_disp}** ({md_link})."
                        if lang == "vi"
                        else f"Opened default browser and searched for **{clean_q_disp}** ({md_link})."
                    )
                else:
                    msg = (
                        f"Đã mở trình duyệt mặc định ({md_link})."
                        if lang == "vi"
                        else f"Opened default browser ({md_link})."
                    )
            else:
                tool_to_use = "web_search"
                if re.search(
                    r"\b(?:tim tren github|tim kiem tren github|tra cuu github|search github|on github|github repo|github repositories)\b",
                    folded,
                ):
                    tool_to_use = "github_search"
                elif re.search(
                    r"\b(?:tim tren wikipedia|tim kiem tren wikipedia|tra cuu wikipedia|search wikipedia|on wikipedia)\b",
                    folded,
                ):
                    tool_to_use = "wikipedia_search"

                query_str = re.sub(
                    r"^(?:/(?:web|search)\s+)?(?:hay\s+|hãy\s+|vui long\s+|vui lòng\s+|please\s+)?(?:tim kiem tren web|tìm kiếm trên web|tim tren web|tìm trên web|tim kiem tren mang|tìm kiếm trên mạng|tim tren mang|tìm trên mạng|tra cuu tren web|tra cứu trên web|tra cuu tren mang|tra cứu trên mạng|tra cuu tren internet|tra cứu trên internet|tra cuu thong tin tren internet|tra cứu thông tin trên internet|su dung internet|sử dụng internet|dung internet|dùng internet|tim tren github|tìm trên github|search github|tim tren wikipedia|tìm trên wikipedia|search wikipedia|tra cuu wikipedia|tra cứu wikipedia|tim google|tìm google|search google|search web|web search)(?:\s+(?:de\s+tong\s+hop\s+thong\s+tin|để\s+tổng\s+hợp\s+thông\s+tin))?(?:\s+(?:ve|về|cho toi|cho tôi|cho mình|for|about))?\s*:?\s*",
                    "",
                    raw_clean,
                    flags=re.IGNORECASE,
                ).strip() or raw_clean
                query_str = web_tools.clean_search_query(query_str, for_plan=False) or query_str

                step_id = record_step_start(run_id, 1, tool_to_use, side_effect=False, target_path=query_str, now_ms=now_ms)
                called = mcp_client.call_tool(
                    "web-search-mcp",
                    tool_to_use,
                    {"query": query_str, "maxResults": 5},
                    companion_root=companion_root,
                    enabled_overrides={"web-search-mcp": True},
                )
                structured = called.get("structured") or {}
                w_results = structured.get("results") or []
                record_step_finish(step_id, status="completed", detail={"query": query_str, "count": len(w_results)})
                web_steps: list[dict] = [{"stepId": step_id, "tool": tool_to_use, "status": "completed", "targetPath": query_str}]
                advance_turn(run_id, now_ms=now_ms)
                lines = structured.get("lines") or []
                body = "\n".join(lines) if lines else ("(Không tìm thấy kết quả phù hợp)" if lang == "vi" else "(No matching results found)")
                sources_md = str(structured.get("sources_markdown") or web_tools.format_markdown_sources(w_results, max_sources=5))
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
                fetched_pages_text = ""
                should_deep_read = (
                    (options.get("synthesizeWithLlm") is True or str(options.get("runSource") or "") == "interactive")
                    and os.environ.get("AQUA_TEST_AGENT_ENABLED") != "1"
                    and not os.environ.get("AQUA_TEST_ENDPOINT")
                    and not (bool(os.environ.get("ALIS_TEST_ISOLATED")) and not bool(options.get("endpoint")) and not bool(os.environ.get("AQUA_TEST_WEB_FIXTURE")))
                )
                if w_results and should_deep_read and options.get("fetchTopPages") is not False:
                    effort_lvl = str(options.get("thinkingEffort") or "medium").strip().lower()
                    max_fetch_pages = int(options.get("maxFetchPages") or (3 if effort_lvl in ("max", "xhigh", "high") else 2))
                    _, fetched_pages_text = _fetch_top_search_pages(
                        run_id,
                        w_results,
                        web_steps,
                        max_pages=max_fetch_pages,
                        max_chars_per_page=1800,
                        timeout_sec=4.0,
                        now_ms=now_ms,
                    )

            web_synthesized = False
            has_grounding_to_synthesize = (
                bool(locals().get("w_results"))
                or bool(locals().get("fetch_has_content"))
            )
            if has_grounding_to_synthesize and (
                options.get("synthesizeWithLlm") is True or (sub == "search" and locals().get("should_deep_read", False))
            ):
                obs_input = msg
                if locals().get("fetched_pages_text"):
                    obs_input = f"{msg}\n\n## Fetched Webpage Content\n{locals()['fetched_pages_text']}"
                synth_seq = len(locals().get("web_steps") or [{"stepId": 1}]) + 1
                _, synth_mdl = _resolve_live_llm(options)
                s_step = record_step_start(
                    run_id,
                    synth_seq,
                    "llm_synthesize",
                    side_effect=False,
                    target_path=synth_mdl or str(options.get("model") or "local-llm"),
                    now_ms=now_ms,
                )
                synth = _synthesize_observations_with_llm(prompt, obs_input, options, lang, locals().get("sources_md", ""))
                record_step_finish(
                    s_step,
                    status="completed" if synth else "failed",
                    target_path=synth_mdl or str(options.get("model") or "local-llm"),
                    detail={"chars": len(synth or "")},
                )
                if synth:
                    if sub == "search":
                        msg = synth
                    else:
                        msg += f"\n\n### {'Tổng hợp' if lang == 'vi' else 'Synthesis'}\n{synth}"
                    web_synthesized = True

            finish_run(run_id, status="completed", now_ms=now_ms)
            res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
            u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
            return {
                "handled": True,
                "intent": kind,
                "synthesized": web_synthesized,
                "domElements": dom_elements,
                "domAction": dom_action_res,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 14c. Hermes Self-Learning, Workflow Adaptation & Agent Skills (`SKILL.md`)
        if kind == "skill_manage":
            folded = _fold_ascii(prompt)
            direct_skill = str(intent.get("directSkill") or "").strip()
            raw_clean = re.sub(r"^(?:/skills?|skills?|/learn|/workflow|/reflect)\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()
            clean_folded = _fold_ascii(raw_clean)
            extra_skill_meta: dict = {}

            if direct_skill:
                arg_text = re.sub(rf"^/{re.escape(direct_skill)}\s*", "", prompt.strip(), flags=re.IGNORECASE).strip()
                ran = skill_store.run_skill(
                    direct_skill,
                    prompt=arg_text,
                    companion_root=companion_root,
                    workspace=session_ctx.get("lastProject") or session_ctx.get("lastDirectory"),
                )
                if ran.get("ponytailMode"):
                    session_ctx["ponytailMode"] = ran["ponytailMode"]
                    save_session_context(session_ctx, now_ms=now_ms)
                    extra_skill_meta["ponytailMode"] = ran["ponytailMode"]
                extra_skill_meta["skill"] = ran["skill"]
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
                or any(b in folded for b in ("system-diagnostics", "code-assistant", "rag-researcher", "focus-planner", "screen-copilot", "desktop-organizer", "window-workspace-layout", "git-daily-brief", "system-tuner", "clipboard-transformer", "niri-wm", "quickshell-qml", "arch-linux", "ponytail", "docx", "pdf", "xlsx"))
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
                if ran.get("ponytailMode"):
                    session_ctx["ponytailMode"] = ran["ponytailMode"]
                    save_session_context(session_ctx, now_ms=now_ms)
                    extra_skill_meta["ponytailMode"] = ran["ponytailMode"]
                extra_skill_meta["skill"] = ran["skill"]
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
            return {"handled": True, "intent": kind, **extra_skill_meta, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

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
                live_sys = alis_local_bridge.format_live_state(_tctx().current_turn_live_snapshot, topic="system", query=prompt, lang=lang)
                extra_sys = str(live_sys.get("markdown") or "").strip() if _tctx().current_turn_live_snapshot else ""
                msg = (
                    f"Trạng thái hệ thống hiện tại ({tel_res['summary']}):\n{body}"
                    if lang == "vi"
                    else f"System telemetry ({tel_res['summary']}):\n{body}"
                )
                if extra_sys:
                    msg += f"\n\n{extra_sys}"
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
                elif re.search(r"\b(?:chuyen|dua)\s+cua\s+so(?:\s+#?\d+)?\s+sang\s+workspace|\b(?:move|send)\s+window(?:\s+#?\d+)?\s+to\s+workspace", folded):
                    m_ws = re.search(r"(?:workspace)\s+#?(\d+)", folded)
                    m_win = re.search(r"(?:window|cua so)\s+#?(\d+)", folded)
                    ws_target = m_ws.group(1) if m_ws else "1"
                    win_target = m_win.group(1) if m_win else ""
                    desk_res = desktop_control.manage_desktop(action="move_window_to_workspace", target=ws_target, value=win_target)
                elif any(k in folded for k in ("consume or expel", "gop hoac tach cua so")):
                    dir_val = "right" if ("right" in folded or "phai" in folded) else "left"
                    desk_res = desktop_control.manage_desktop(action="consume_or_expel_window", value=dir_val)
                elif any(k in folded for k in ("gop cua so vao cot", "consume window into column", "consume into column", "consume window")):
                    desk_res = desktop_control.manage_desktop(action="consume_window_into_column")
                elif any(k in folded for k in ("tach cua so khoi cot", "expel window from column", "expel from column", "expel window")):
                    desk_res = desktop_control.manage_desktop(action="expel_window_from_column")
                elif any(k in folded for k in ("do rong cot", "column width", "set column width", "resize column", "resize window")):
                    m_pct = re.search(r"([+\-]?\d+\s*%|\d+\s*px)", prompt)
                    val = m_pct.group(1).replace(" ", "") if m_pct else "50%"
                    desk_res = desktop_control.manage_desktop(action="set_column_width", value=val)
                elif any(k in folded for k in ("chieu cao cua so", "window height", "set window height")):
                    m_pct = re.search(r"([+\-]?\d+\s*%|\d+\s*px)", prompt)
                    val = m_pct.group(1).replace(" ", "") if m_pct else "50%"
                    desk_res = desktop_control.manage_desktop(action="set_window_height", value=val)
                elif any(k in folded for k in ("toan man hinh", "fullscreen window", "toggle fullscreen")):
                    m_id = re.search(r"\b(\d+)\b", prompt)
                    desk_res = desktop_control.manage_desktop(action="fullscreen_window", target=m_id.group(1) if m_id else "")
                elif any(k in folded for k in ("cua so noi", "float window", "floating window", "toggle floating", "unfloat window")):
                    m_id = re.search(r"\b(\d+)\b", prompt)
                    desk_res = desktop_control.manage_desktop(action="toggle_window_floating", target=m_id.group(1) if m_id else "")
                elif any(k in folded for k in ("can giua cot", "center column")):
                    desk_res = desktop_control.manage_desktop(action="center_column")
                elif any(k in folded for k in ("maximize column", "phong to cot")):
                    desk_res = desktop_control.manage_desktop(action="maximize_column")
                elif any(k in folded for k in ("expand column", "mo rong cot")):
                    desk_res = desktop_control.manage_desktop(action="expand_column_to_available_width")
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
            return {
                "handled": True,
                "intent": kind,
                "desktopResult": desk_res,
                **res,
                "userMessageId": u_id,
                "assistantMessageId": a_id,
                "historySaved": saved,
            }

        # 16. File & Directory Operations (Full Home access + Sudo-gated System access + Complete Listing)
        if kind == "file_task":
            sub = intent["subkind"]
            folded = _fold_ascii(prompt)
            last_dir = str(session_ctx.get("lastDirectory") or "")

            if sub == "rollback":
                rb = agent_file_op({"op": "rollback", "nowMs": now_ms})
                rb_rcpt = str(rb.get("receiptId") or "")
                if rb_rcpt:
                    receipts.append(rb_rcpt)
                msg = (
                    f"Đã hoàn tác thành công: {rb['summary']}"
                    if lang == "vi"
                    else f"Rollback completed: {rb['summary']}"
                )
                finish_run(run_id, status="completed", now_ms=now_ms)
                res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, receipts=receipts, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {
                    "handled": True,
                    "intent": kind,
                    **res,
                    "receiptId": rb_rcpt,
                    "restored": str(rb.get("restored") or rb.get("targetPath") or ""),
                    "existedBefore": bool(rb.get("existedBefore", False)),
                    "userMessageId": u_id,
                    "assistantMessageId": a_id,
                    "historySaved": saved,
                }

            # Check copy / cut (move / rename) or document format conversion in natural language or command syntax
            m_copy = re.match(
                r"^(?:/agent\s+|/)?(?:(?:hãy|hay|vui\s+lòng|vui\s+long|làm\s+ơn|lam\s+on|giúp\s+tôi|giup\s+toi|giúp\s+mình|giup\s+minh|tôi\s+muốn|toi\s+muon|mình\s+muốn|minh\s+muon|please|can\s+you|could\s+you|help\s+me|i\s+want\s+to)\s+)*(convert|chuyển\s+đổi|chuyen\s+doi|copy|cp|sao\s+chép|sao\s+chep|cut|cắt|cat|move|mv|di\s+chuyển|di\s+chuyen|chuyển|chuyen|đổi\s+tên|doi\s+ten|rename)\s+(?:(?:file|tệp|tep|thư\s+mục|thu\s+muc|folder|directory)\s+)?(.+?)\s*(?:->|=>|\s+to\s+|\s+into\s+|\s+sang\s+|\s+tới\s+|\s+toi\s+|\s+vào\s+|\s+vao\s+|\s+qua\s+|\s+đến\s+|\s+den\s+|\s+thành\s+|\s+thanh\s+)\s*(?:(?:file|tệp|tep|thư\s+mục|thu\s+muc|folder|directory)\s+)?(.+)$",
                prompt.strip(),
                re.IGNORECASE,
            )
            if m_copy:
                verb_raw = re.sub(r"\s+", " ", _fold_ascii(m_copy.group(1))).strip()
                src_raw = m_copy.group(2).strip().strip("'\"`")
                dst_raw = m_copy.group(3).strip().strip("'\"`")
                src_cand = str(resolve_candidate_path(src_raw, last_dir=last_dir))
                dst_cand = str(resolve_candidate_path(dst_raw, last_dir=last_dir))
                src_ext = Path(src_cand).suffix.lower()
                dst_ext = Path(dst_cand).suffix.lower()
                is_doc_convert = bool(
                    verb_raw in ("convert", "chuyen doi")
                    or (
                        verb_raw == "chuyen"
                        and src_ext != dst_ext
                        and (dst_ext in (".docx", ".pdf", ".xlsx") or src_ext in (".docx", ".pdf", ".xlsx"))
                        and Path(src_cand).is_file()
                    )
                )
                if is_doc_convert:
                    if is_system_path(src_cand) or is_system_path(dst_cand):
                        return _prompt_for_system_sudo(
                            "edit",
                            dst_cand,
                            prompt,
                            lang,
                            run_id,
                            session_ctx,
                            now_ms,
                            extra_payload={"sourcePath": src_cand},
                            persist_history=persist_history,
                        )
                    src_info = code_tools.read_file(src_cand, max_chars=200000)
                    if not src_info.get("ok"):
                        raise ToolError("not_found", f"Source file '{src_cand}' could not be read.")
                    edited = agent_file_op({
                        "op": "edit",
                        "targetPath": dst_cand,
                        "content": str(src_info.get("text") or ""),
                        "mode": "write",
                        "lastDirectory": last_dir,
                        "nowMs": now_ms,
                    })
                    conv_meta = convert_or_create_document(src_cand, dst_cand)
                    cv_rcpt = str(edited.get("receiptId") or "")
                    if cv_rcpt:
                        receipts.append(cv_rcpt)
                    session_ctx["lastFile"] = edited["targetPath"]
                    session_ctx["lastDirectory"] = str(Path(edited["targetPath"]).parent)
                    save_session_context(session_ctx, now_ms=now_ms)
                    msg = (
                        f"Đã chuyển đổi `{Path(src_cand).name}` sang `{Path(dst_cand).name}` (`{edited['targetPath']}`, {conv_meta.get('bytes', 0)} bytes)."
                        if lang == "vi"
                        else f"Converted `{Path(src_cand).name}` to `{Path(dst_cand).name}` (`{edited['targetPath']}`, {conv_meta.get('bytes', 0)} bytes)."
                    )
                    finish_run(run_id, status="completed", now_ms=now_ms)
                    res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, receipts=receipts, session_ctx=session_ctx)
                    u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                    return {
                        "handled": True,
                        "intent": kind,
                        **res,
                        "receiptId": cv_rcpt,
                        "convertedDocument": conv_meta,
                        "addedLines": int(edited.get("addedLines") or 0),
                        "removedLines": int(edited.get("removedLines") or 0),
                        "diffPreview": str(edited.get("diffPreview") or ""),
                        "userMessageId": u_id,
                        "assistantMessageId": a_id,
                        "historySaved": saved,
                    }
                op_kind = "copy" if verb_raw in ("copy", "cp", "sao chep") else "cut"
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
                mv_rcpt = str(moved.get("receiptId") or "")
                if mv_rcpt:
                    receipts.append(mv_rcpt)
                msg = f"{moved['summary']}\n" + "\n".join(moved["lines"])
                finish_run(run_id, status="completed", now_ms=now_ms)
                res = _format_reply(msg, expression="happy", status="completed", run_id=run_id, language=lang, receipts=receipts, session_ctx=session_ctx)
                u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                return {
                    "handled": True,
                    "intent": kind,
                    **res,
                    "receiptId": mv_rcpt,
                    "addedLines": int(moved.get("addedLines") or 0),
                    "removedLines": int(moved.get("removedLines") or 0),
                    "diffPreview": str(moved.get("diffPreview") or ""),
                    "userMessageId": u_id,
                    "assistantMessageId": a_id,
                    "historySaved": saved,
                }

            is_list_query = bool(re.search(
                r"\b(?:thu muc|folder|directory|liet ke|list|ls|dir|tree|show files|show all|show folders|xem tat ca|xem het|danh sach)\b",
                folded,
            ))
            expect_dir = is_list_query and not any(k in folded for k in ("doc tep", "doc file", "read file", "sua tep", "sua file", "edit file", "xoa tep", "xoa file", "delete file"))
            target, ambiguous_candidates = _resolve_conversational_target(prompt, session_ctx, expect_dir=expect_dir)
            if not target and attached_info["files"]:
                target = attached_info["files"][0]["path"]

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
                del_cid = _issue_confirm_id("conf")
                session_ctx["pendingDeletePath"] = resolved_del
                session_ctx["pendingDeleteConfirmId"] = del_cid
                session_ctx["pendingConfirmId"] = del_cid
                save_session_context(session_ctx, now_ms=now_ms)
                msg = (
                    f"Xác nhận xoá tệp `{Path(resolved_del).name}` (`{resolved_del}`)? "
                    "Hãy trả lời 'đồng ý' để xoá hoặc 'huỷ' để giữ lại tệp."
                    if lang == "vi"
                    else f"Confirm deleting `{Path(resolved_del).name}` (`{resolved_del}`)? "
                         "Reply 'yes' to confirm deletion or 'cancel' to keep the file."
                )
                finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                res = _format_reply(msg, expression="alert", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, confirm_id=del_cid, session_ctx=session_ctx)
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
                    session_ctx["pendingConfirmId"] = str(conf.get("confirmId") or "")
                    save_session_context(session_ctx, now_ms=now_ms)
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
                        r"\b(?:chinh\s+sua|sua|edit|tao|create)\b.*(?:~/|/|\.\.?/|\b[a-z0-9_.\-]+\.(?:txt|md|py|qml|js|cjs|mjs|ts|tsx|jsx|json|rs|toml|sh|bash|zsh|fish|yaml|yml|html|css|scss|vue|svelte|c|cpp|cc|h|hpp|go|java|kt|rb|php|lua|sql|xml|csv|tsv|env|nix|log|conf|cfg|ini|service|pdf|docx)\b)",
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
                if " — " in content_to_write:
                    content_to_write = content_to_write.split(" — ", 1)[0].strip()
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
                        session_ctx["pendingConfirmId"] = str(conf.get("confirmId") or "")
                        save_session_context(session_ctx, now_ms=now_ms)
                        finish_run(run_id, status="awaiting_confirmation", now_ms=now_ms)
                        msg = (
                            f"Thư mục `{parent_dir}` đã bị thu hồi quyền ghi. Bạn có đồng ý cấp lại quyền cho `{parent_dir}` không? (Trả lời 'đồng ý' hoặc 'huỷ')"
                            if lang == "vi"
                            else f"Directory `{parent_dir}` had write access revoked. Do you grant write permission for `{parent_dir}`? (Reply 'yes' or 'cancel')"
                        )
                        res = _format_reply(msg, expression="thinking", status="awaiting_confirmation", run_id=run_id, language=lang, requires_confirmation=True, confirm_id=conf["confirmId"], session_ctx=session_ctx)
                        u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                        return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

                existed_before = Path(resolved_target).exists()
                has_cross_comp_keyword = bool(
                    not has_explicit_content
                    and "—" not in prompt
                    and re.search(r"\b(?:anh huong|affect|impact|cac thanh phan khac|other components|other modules)\b", folded)
                )
                impacted_siblings = (
                    _detect_cross_component_impact(resolved_target, content_to_write if has_explicit_content else "")
                    if existed_before
                    and "—" not in prompt
                    and not options.get("confirmCrossComponent")
                    and (
                        (has_explicit_content and mode == "write")
                        or re.search(
                            r"\b(?:refactor|rename|doi ten|xoa ham|remove function|thay doi chu ky|change signature)\b",
                            folded,
                        )
                    )
                    else []
                )
                if has_cross_comp_keyword or impacted_siblings:
                    if impacted_siblings and not has_cross_comp_keyword:
                        target_name = Path(resolved_target).name
                        sib_summary = ", ".join(
                            f"`{item['file']}` ({', '.join(item['symbols'])})" for item in impacted_siblings
                        )
                        if lang == "vi":
                            opt1 = f"1. Chỉ sửa gọn trong `{target_name}` và giữ nguyên chữ ký hàm/thành phần đang dùng ở {sib_summary} (Khuyên dùng)"
                            opt2 = f"2. Vẫn cập nhật `{target_name}` theo yêu cầu và đồng bộ thêm các tệp liên quan ({sib_summary})"
                            opt3 = f"3. Mở `{target_name}` trong Editor để tự kiểm tra trước khi sửa"
                            msg = (
                                f"💡 **Kiểm tra phạm vi chỉnh sửa (`{target_name}`):** Phát hiện các tệp cùng thư mục có tham chiếu trực tiếp đến thành phần trong `{target_name}`: {sib_summary}.\n\n"
                                f"Theo quy tắc **Surgical Changes**, tôi tạm dừng để tránh làm gãy các thành phần khác ngoài mục tiêu được giao. Bạn muốn xử lý theo hướng nào?\n"
                                f"- {opt1}\n- {opt2}\n- {opt3}\n\n"
                                f"*(Chọn 1 phương án bên dưới hoặc nhập yêu cầu tuỳ chỉnh)*"
                            )
                        else:
                            opt1 = f"1. Edit strictly inside `{target_name}` while preserving public signatures used by {sib_summary} (Recommended)"
                            opt2 = f"2. Apply the change to `{target_name}` and coordinate updates across {sib_summary}"
                            opt3 = f"3. Open `{target_name}` in the Editor to inspect before modifying"
                            msg = (
                                f"💡 **Surgical Scope Check (`{target_name}`):** Detected sibling files referencing symbols in `{target_name}`: {sib_summary}.\n\n"
                                f"To avoid unintended cross-component breakage, how would you like to proceed?\n"
                                f"- {opt1}\n- {opt2}\n- {opt3}\n\n"
                                f"*(Select an option below or type your custom instruction)*"
                            )
                        clar_payload = {
                            "type": "cross_component_clarification",
                            "originalPrompt": prompt,
                            "targetPath": resolved_target,
                            "impactedFiles": impacted_siblings,
                            "question": (
                                "Chọn phương án giới hạn phạm vi chỉnh sửa hoặc nhập hướng dẫn riêng:"
                                if lang == "vi"
                                else "Choose how to scope this edit or type a custom instruction:"
                            ),
                            "allowCustomInput": True,
                            "customPlaceholder": (
                                "Nhập phạm vi hoặc file được phép sửa thêm..."
                                if lang == "vi"
                                else "Type allowed scope or additional files..."
                            ),
                            "options": [
                                {"id": "opt_1", "label": opt1, "value": opt1, "recommended": True},
                                {"id": "opt_2", "label": opt2, "value": opt2, "recommended": False},
                                {"id": "opt_3", "label": opt3, "value": opt3, "recommended": False},
                            ],
                        }
                    else:
                        msg, clar_payload = _generate_task_clarification(prompt, lang, session_ctx)
                    session_ctx["pendingClarification"] = clar_payload
                    save_session_context(session_ctx, now_ms=now_ms)
                    finish_run(run_id, status="awaiting_clarification", now_ms=now_ms)
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

                prev_trimmed = (
                    (
                        str(code_tools.read_file(resolved_target, max_chars=200000).get("text") or "").strip()
                        if Path(resolved_target).suffix.lower() in (".docx", ".pdf", ".xlsx")
                        else Path(resolved_target).read_text(encoding="utf-8", errors="replace").strip()
                    )
                    if existed_before and Path(resolved_target).is_file()
                    else None
                )
                was_syntax_valid = (
                    _check_file_syntax_valid(Path(resolved_target))[0]
                    if has_explicit_content and existed_before and Path(resolved_target).is_file()
                    else False
                )
                step_id = record_step_start(
                    run_id,
                    2 if summary_part else 1,
                    "file_mutate" if has_explicit_content else "file_open_editor",
                    side_effect=bool(has_explicit_content or not existed_before),
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
                rcpt_id = str(edited.get("receiptId") or (m_rcpt.group(1) if m_rcpt else ""))
                added_lines = int(edited.get("addedLines") or 0)
                removed_lines = int(edited.get("removedLines") or 0)
                reverted_noop = False
                reverted_syntax = ""
                is_noop_edit = bool(
                    has_explicit_content
                    and existed_before
                    and mode == "write"
                    and ((added_lines == 0 and removed_lines == 0) or (prev_trimmed is not None and prev_trimmed == content_to_write.strip()))
                )
                if is_noop_edit and rcpt_id:
                    try:
                        rollback_file_receipt(rcpt_id)
                        reverted_noop = True
                        rcpt_id = ""
                        added_lines = 0
                        removed_lines = 0
                    except Exception:
                        pass
                elif has_explicit_content and existed_before and was_syntax_valid and rcpt_id:
                    syntax_ok, syntax_err = _check_file_syntax_valid(Path(edited["targetPath"]))
                    if not syntax_ok and syntax_err:
                        try:
                            rollback_file_receipt(rcpt_id)
                            reverted_syntax = syntax_err
                            rcpt_id = ""
                            added_lines = 0
                            removed_lines = 0
                        except Exception:
                            pass
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
                elif reverted_noop:
                    msg = summary_part + (
                        f"Thay đổi trên `{target_name}` không tạo ra khác biệt thực tế (0 dòng thay đổi) nên đã tự động hoàn tác/dọn sạch để giữ mã nguồn gọn gàng."
                        if lang == "vi"
                        else f"Edit on `{target_name}` produced no effective diff (0 lines changed) and was automatically reverted/cleaned up."
                    )
                elif reverted_syntax:
                    msg = summary_part + (
                        f"Thay đổi trên `{target_name}` gây lỗi cú pháp (`{reverted_syntax}`) nên đã tự động hoàn tác/khôi phục bản gốc sạch sẽ."
                        if lang == "vi"
                        else f"Edit on `{target_name}` introduced a syntax error (`{reverted_syntax}`) and was automatically reverted to keep the file clean."
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
                    "receiptId": rcpt_id,
                    "addedLines": added_lines,
                    "removedLines": removed_lines,
                    "diffPreview": str(edited.get("diffPreview") or ""),
                    "editorMode": edited.get("editorMode", ""),
                    "isLargeFile": bool(edited.get("isLargeFile", False)),
                    "openedExternalEditor": bool(edited.get("openedExternalEditor", False)),
                    "editorCommand": ed_cmd if edited.get("editorMode") == "external" else "",
                    "editorPopup": popup_meta,
                    "userMessageId": u_id,
                    "assistantMessageId": a_id,
                    "historySaved": saved,
                }

            # Default file/directory inspect, search & full listing
            is_find_query = bool(
                re.search(
                    r"\b(?:tim\s+file|tim\s+tep|tim\s+kiem|tim\s+giup|tim\s+cho|tim\s+thu\s+muc|kiem\s+file|kiem\s+tep|kiem\s+thu\s+muc|kiem\s+giup|nam\s+o\s+dau|o\s+dau|find\b|locate\b|where\s+is\b|search\s+for\b|search\s+file\b|look\s+for\b)\b",
                    folded,
                )
            )
            raw_target_arg = str(target or "").strip()
            if not target:
                target = session_ctx.get("lastDirectory") or str(Path.home())
            resolved_target = str(resolve_candidate_path(target, last_dir=last_dir))
            search_matches: list[Path] = []
            search_step_id = None
            query_name = Path(raw_target_arg).name if raw_target_arg else ""
            if (is_find_query or not Path(resolved_target).exists()) and query_name and Path(resolved_target) != Path.home().resolve():
                search_step_id = (
                    record_step_start(run_id, 1, "file_search", side_effect=False, target_path=query_name, now_ms=now_ms)
                    if is_find_query
                    else None
                )
                search_matches = search_files_in_home(query_name, last_dir=last_dir, max_results=25)
                if search_step_id is not None:
                    record_step_finish(
                        search_step_id,
                        status="completed",
                        target_path=query_name,
                        detail={"count": len(search_matches), "matches": [str(m) for m in search_matches[:10]]},
                    )
                if search_matches:
                    resolved_target = str(search_matches[0])
                elif is_find_query and not Path(resolved_target).exists():
                    advance_turn(run_id, now_ms=now_ms)
                    finish_run(run_id, status="completed", now_ms=now_ms)
                    msg = (
                        f"Không tìm thấy tệp hoặc thư mục nào khớp với `{query_name}` trong `{Path.home()}`."
                        if lang == "vi"
                        else f"Could not find any file or folder matching `{query_name}` in `{Path.home()}`."
                    )
                    res = _format_reply(msg, expression="thinking", status="completed", run_id=run_id, language=lang, session_ctx=session_ctx)
                    u_id, a_id, saved = _persist_turn_to_history(prompt, res["text"]) if persist_history else (0, 0, False)
                    return {"handled": True, "intent": kind, **res, "userMessageId": u_id, "assistantMessageId": a_id, "historySaved": saved}

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
                    session_ctx["pendingConfirmId"] = str(conf.get("confirmId") or "")
                    save_session_context(session_ctx, now_ms=now_ms)
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
            step_id = record_step_start(run_id, 2 if search_step_id is not None else 1, "file_inspect", side_effect=False, target_path=resolved_target, now_ms=now_ms)
            resolved_p_cand = Path(resolved_target)
            if resolved_p_cand.is_file() and resolved_p_cand.suffix.lower() in (".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".epub"):
                doc_info = code_tools.read_file(resolved_p_cand, max_chars=4800)
                extracted_doc = str(doc_info.get("text") or "")
                doc_lines = [ln for ln in extracted_doc.splitlines() if ln.strip()][:40]
                extra_summary = ""
                if resolved_p_cand.suffix.lower() == ".xlsx":
                    sheet_cnt = len(doc_info.get("sheets") or [])
                    form_cnt = len(doc_info.get("formulas") or [])
                    err_cnt = len(doc_info.get("formulaErrors") or [])
                    extra_summary = f", {sheet_cnt} sheet(s), {form_cnt} formula(s), {err_cnt} formula error(s)"
                inspected = {
                    "targetPath": str(resolved_p_cand),
                    "summary": f"Extracted structured Markdown from {resolved_p_cand.name} ({len(extracted_doc)} chars{extra_summary})",
                    "lines": doc_lines or [f"(empty document: {resolved_p_cand.name})"],
                    "tables": doc_info.get("tables") or [],
                    "markdownTables": doc_info.get("markdownTables") or [],
                    "sheets": doc_info.get("sheets") or [],
                    "formulas": doc_info.get("formulas") or [],
                    "formulaErrors": doc_info.get("formulaErrors") or [],
                }
            else:
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
                session_ctx["recentCandidates"] = [str(m) for m in search_matches[:20]] if search_matches else files_in_dir
                if len(files_in_dir) == 1:
                    session_ctx["lastFile"] = files_in_dir[0]
            else:
                session_ctx["lastFile"] = str(resolved_p)
                session_ctx["lastDirectory"] = str(resolved_p.parent)
                session_ctx["recentCandidates"] = [str(m) for m in search_matches[:20]] if search_matches else [str(resolved_p)]
            save_session_context(session_ctx, now_ms=now_ms)

            # Complete listing: include ALL lines returned by tool_store (no [:10] slicing!)
            lines_to_show = inspected["lines"][:25] if (is_find_query and len(inspected["lines"]) > 25) else inspected["lines"]
            body_lines = "\n".join(f"- {ln}" for ln in lines_to_show)
            display_name = resolved_p.name or str(resolved_p)
            found_prefix = ""
            if is_find_query and search_matches:
                found_list = "\n".join(f"- `{m}`" for m in search_matches)
                found_prefix = (
                    f"Đã tìm thấy **{len(search_matches)}** kết quả cho `{query_name or display_name}`:\n{found_list}\n\n"
                    if lang == "vi"
                    else f"Found **{len(search_matches)}** matching path(s) for `{query_name or display_name}`:\n{found_list}\n\n"
                )
            msg = found_prefix + (
                f"Đã đọc `{display_name}` (`{resolved_p}` — {inspected['summary']}):\n{body_lines}"
                if lang == "vi"
                else f"Inspected `{display_name}` (`{resolved_p}` — {inspected['summary']}):\n{body_lines}"
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


def handle_a2ui_action(
    payload: dict,
    companion_root: Path,
    host_root: Path,
) -> dict:
    """Handle trusted UI events emitted by the Companion A2UI v1.0 QML renderer.

    Never trusts raw `dataModel` snapshots (`sendDataModel: false`): only accepts
    allowlisted event names and explicitly bound event `context` keys.
    """
    req = payload if isinstance(payload, dict) else {}
    now_ms = int(req.get("nowMs") or int(time.time() * 1000))
    surface_id = str(req.get("surfaceId") or "").strip()[:80]
    event_name = str(req.get("eventName") or "").strip()
    ctx_in = req.get("context") if isinstance(req.get("context"), dict) else {}
    raw_lang = str(req.get("language") or "en").strip().lower()

    if event_name not in a2ui_validator.ALLOWED_SERVER_EVENTS:
        return {
            "ok": False,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "error": f"disallowed_event:{event_name}",
        }

    existing_surface_row = history_store.get_surface_by_id(surface_id) if surface_id else None
    existing_cs = (
        (existing_surface_row[1].get("createSurface") or {})
        if existing_surface_row and isinstance(existing_surface_row[1], dict)
        else {}
    )
    existing_meta = existing_cs.get("surfaceMeta") if isinstance(existing_cs.get("surfaceMeta"), dict) else {}
    surf_is_tainted = bool(existing_meta.get("tainted") is True)
    surf_is_generative = bool(
        existing_meta.get("kind") == "generative"
        or existing_meta.get("sourceKind") == "generative"
        or surface_id.startswith("surf_gen")
    )

    if raw_lang.startswith("vi"):
        lang = "vi"
    elif raw_lang == "auto" and existing_cs:
        sample_str = json.dumps(existing_cs.get("dataModel") or {}, ensure_ascii=False)
        lang = "vi" if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", sample_str, re.I) else "en"
    else:
        lang = "en"

    # Server-side security & CaMeL taint boundary enforcement (defense-in-depth)
    if surf_is_generative and event_name in a2ui_validator.PRIVILEGED_SERVER_EVENTS:
        return {
            "ok": False,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "error": f"privileged_event_forbidden:{event_name}",
        }
    if surf_is_tainted and event_name not in a2ui_validator.TAINTED_LOCAL_UI_EVENTS:
        return {
            "ok": False,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "error": "tainted_surface_action_forbidden",
        }

    def _outcome_summary(raw_msg: str) -> str:
        return a2ui_validator._clean_str(raw_msg, 280).strip()

    # 1. Confirmation card actions (strictly matched against non-empty confirmId, trusted source="a2ui_confirm")
    if event_name == "confirm_action":
        confirm_id = str(ctx_in.get("confirmId") or req.get("confirmId") or "").strip()
        decision = "confirm" if str(ctx_in.get("decision") or "").strip().lower() == "confirm" else "cancel"
        session_ctx = load_session_context()

        def _persist_confirm_outcome(res_state: str, cid_to_lock: str, summary: str) -> str:
            clean_sum = _outcome_summary(summary)
            if surface_id:
                history_store.patch_surface_by_id(
                    surface_id,
                    lock_resolution=res_state,
                    outcome_summary=clean_sum,
                )
            if cid_to_lock:
                history_store.lock_confirm_surfaces(
                    cid_to_lock,
                    resolution=res_state,
                    outcome_summary=clean_sum,
                )
            return clean_sum

        def _failed_confirm_response(cid_to_lock: str, exc: Exception) -> dict:
            err_txt = str(exc)[:160]
            err_sum = _persist_confirm_outcome("failed", cid_to_lock, f"Error: {err_txt}")
            return {
                "ok": False,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "resolved": True,
                "resolution": "failed",
                "requiresConfirmation": False,
                "confirmId": "",
                "error": err_txt,
                "text": err_sum,
                "expression": "sad",
                "dataPatch": [
                    {"path": "/resolved", "value": True},
                    {"path": "/resolution", "value": "failed"},
                    {"path": "/statusLabel", "value": "Failed"},
                    {"path": "/outcomeSummary", "value": err_sum},
                ],
                "clientActions": [],
            }

        # Security: A2UI confirm_action MUST provide a non-empty confirmId matching the active pending request.
        if not confirm_id:
            missing_msg = _outcome_summary(
                "Thiếu mã xác nhận hợp lệ; thao tác không được thực hiện."
                if lang == "vi"
                else "Missing valid confirmation ID; no action was executed."
            )
            return {
                "ok": True,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "stale": True,
                "resolved": True,
                "resolution": "expired",
                "requiresConfirmation": False,
                "confirmId": "",
                "text": missing_msg,
                "dataPatch": [
                    {"path": "/resolved", "value": True},
                    {"path": "/resolution", "value": "expired"},
                    {"path": "/statusLabel", "value": "Expired"},
                    {"path": "/outcomeSummary", "value": missing_msg},
                ],
                "clientActions": [],
            }

        # Sudo operations can NEVER be confirmed from A2UI (only cancelled)
        if session_ctx.get("pendingSudoOp"):
            sudo_obj = session_ctx.get("pendingSudoOp") or {}
            sudo_cid = str(sudo_obj.get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            if sudo_cid and confirm_id == sudo_cid:
                if decision == "confirm":
                    return {
                        "ok": True,
                        "op": "a2ui_action",
                        "eventName": event_name,
                        "surfaceId": surface_id,
                        "stale": False,
                        "requiresConfirmation": True,
                        "requiresSudoPassword": True,
                        "confirmId": sudo_cid,
                        "text": (
                            "Vui lòng nhập mật khẩu `sudo` trực tiếp vào ô chat."
                            if lang == "vi"
                            else "Please enter your `sudo` password directly in the chat composer."
                        ),
                        "dataPatch": [],
                        "clientActions": [],
                    }
                session_ctx["pendingSudoOp"] = None
                session_ctx["pendingConfirmId"] = ""
                save_session_context(session_ctx, now_ms=now_ms)
                resolve_pending_confirmation(confirm_id=sudo_cid, decision="cancel", source="a2ui_confirm", now_ms=now_ms)
                msg = "Đã huỷ thao tác hệ thống." if lang == "vi" else "Cancelled system path operation."
                summary = _persist_confirm_outcome("cancelled", sudo_cid, msg)
                return {
                    "ok": True,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": "cancelled",
                    "requiresConfirmation": False,
                    "requiresSudoPassword": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "idle",
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": "cancelled"},
                        {"path": "/statusLabel", "value": "Cancelled"},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": [],
                }

        # Pending client action (todo_add, quicknote_add, rag_note_save)
        if isinstance(session_ctx.get("pendingClientAction"), dict):
            pca = dict(session_ctx.get("pendingClientAction") or {})
            pca_cid = str(pca.get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            if pca_cid and confirm_id == pca_cid:
                session_ctx["pendingClientAction"] = None
                session_ctx["pendingConfirmId"] = ""
                save_session_context(session_ctx, now_ms=now_ms)
                res_state = "confirmed" if decision == "confirm" else "cancelled"
                emitted_actions: list[dict] = []
                if decision == "confirm":
                    p_act = str(pca.get("action") or "")
                    p_tgt = str(pca.get("target") or "")
                    p_val = str(pca.get("value") or "")
                    if p_act == "rag_note_save":
                        try:
                            saved_note = nexus_rag.save_rag_note(
                                p_tgt or "Companion Note",
                                p_val or p_tgt,
                                folder=str(pca.get("folder") or "Knowledge"),
                                vault_path=str(pca.get("vaultPath") or _tctx().current_turn_nexus_vault),
                            )
                        except Exception as exc:
                            return _failed_confirm_response(pca_cid, exc)
                        msg = (
                            f"Đã lưu ghi chú RAG `{saved_note['title']}` vào Nexus (`{saved_note['relPath']}`)."
                            if lang == "vi"
                            else f"Saved RAG note `{saved_note['title']}` to Nexus (`{saved_note['relPath']}`)."
                        )
                    else:
                        emitted_actions.append({"action": p_act, "target": p_tgt, "value": p_val})
                        msg = (
                            f"Đã xác nhận thực hiện `{p_act}` (`{p_tgt or p_val}`)."
                            if lang == "vi"
                            else f"Confirmed `{p_act}` (`{p_tgt or p_val}`)."
                        )
                else:
                    msg = "Đã huỷ thao tác đang chờ." if lang == "vi" else "Cancelled pending action."
                summary = _persist_confirm_outcome(res_state, pca_cid, msg)
                return {
                    "ok": True,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": res_state,
                    "requiresConfirmation": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "happy" if decision == "confirm" else "idle",
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": res_state},
                        {"path": "/statusLabel", "value": "Confirmed" if decision == "confirm" else "Cancelled"},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": emitted_actions,
                }

        # Pending memory forget
        if isinstance(session_ctx.get("pendingMemoryForget"), dict):
            mem_target = dict(session_ctx.get("pendingMemoryForget") or {})
            mem_cid = str(mem_target.get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            if mem_cid and confirm_id == mem_cid:
                session_ctx["pendingMemoryForget"] = None
                session_ctx["pendingConfirmId"] = ""
                save_session_context(session_ctx, now_ms=now_ms)
                res_state = "confirmed" if decision == "confirm" else "cancelled"
                if decision == "confirm":
                    try:
                        if mem_target.get("all"):
                            cnt = memory_store.forget_all()
                            msg = f"Đã xoá {cnt} ghi nhớ." if lang == "vi" else f"Removed {cnt} memory item(s)."
                        else:
                            memory_store.forget_one(int(mem_target["id"]))
                            msg = f"Đã xoá ghi nhớ #{mem_target['id']}." if lang == "vi" else f"Forgot memory #{mem_target['id']}."
                    except Exception as exc:
                        return _failed_confirm_response(mem_cid, exc)
                else:
                    msg = "Đã huỷ yêu cầu xoá ghi nhớ." if lang == "vi" else "Cancelled memory deletion."
                summary = _persist_confirm_outcome(res_state, mem_cid, msg)
                return {
                    "ok": True,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": res_state,
                    "requiresConfirmation": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "happy" if decision == "confirm" else "idle",
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": res_state},
                        {"path": "/statusLabel", "value": "Confirmed" if decision == "confirm" else "Cancelled"},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": [],
                }

        # Pending file delete
        if session_ctx.get("pendingDeletePath"):
            del_path = str(session_ctx.get("pendingDeletePath") or "")
            del_cid = str(session_ctx.get("pendingDeleteConfirmId") or session_ctx.get("pendingConfirmId") or "")
            if del_cid and confirm_id == del_cid:
                session_ctx["pendingDeletePath"] = ""
                session_ctx["pendingDeleteConfirmId"] = ""
                session_ctx["pendingConfirmId"] = ""
                save_session_context(session_ctx, now_ms=now_ms)
                res_state = "confirmed" if decision == "confirm" else "cancelled"
                try:
                    if decision == "confirm":
                        agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 0, "nowMs": now_ms})
                        agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 1, "nowMs": now_ms + 1})
                        deleted = agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 2, "nowMs": now_ms + 2})
                        msg = (
                            f"Đã xoá `{Path(del_path).name}` ({deleted['summary']})."
                            if lang == "vi"
                            else f"Deleted `{Path(del_path).name}` ({deleted['summary']})."
                        )
                    else:
                        agent_file_op({"op": "cancel_delete", "targetPath": del_path, "nowMs": now_ms})
                        msg = f"Đã huỷ xoá `{Path(del_path).name}`." if lang == "vi" else f"Cancelled deleting `{Path(del_path).name}`."
                except Exception as exc:
                    return _failed_confirm_response(del_cid, exc)
                summary = _persist_confirm_outcome(res_state, del_cid, msg)
                return {
                    "ok": True,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": res_state,
                    "requiresConfirmation": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "happy" if decision == "confirm" else "idle",
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": res_state},
                        {"path": "/statusLabel", "value": "Confirmed" if decision == "confirm" else "Cancelled"},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": [],
                }

        # Pending permission_store confirmation (grant_directory, tainted_tool_escalation)
        resolved_conf = resolve_pending_confirmation(
            confirm_id=confirm_id,
            decision="confirm" if decision == "confirm" else "cancel",
            source="a2ui_confirm",
            now_ms=now_ms,
        )
        if resolved_conf:
            res_state = "confirmed" if decision == "confirm" else "cancelled"
            resolved_cid = str(resolved_conf.get("confirmId") or confirm_id)
            session_ctx["pendingConfirmId"] = ""
            save_session_context(session_ctx, now_ms=now_ms)
            follow_prompt = ""
            if decision == "confirm":
                p_data = resolved_conf.get("payload") or {}
                if resolved_conf.get("actionKind") == "grant_directory":
                    try:
                        grant_permission(
                            "directory",
                            target=resolved_conf["targetPath"],
                            levels=p_data.get("levels") or ["read", "write", "execute"],
                            ttl_ms=int(p_data.get("ttlMs") or 3600_000),
                            source="a2ui_confirm",
                            now_ms=now_ms,
                        )
                    except Exception as exc:
                        return _failed_confirm_response(resolved_cid, exc)
                    follow_prompt = str(p_data.get("originalPrompt") or "")
                    msg = (
                        f"Đã xác nhận và cấp quyền cho `{resolved_conf['targetPath']}`."
                        if lang == "vi"
                        else f"Confirmed and granted permission for `{resolved_conf['targetPath']}`."
                    )
                elif resolved_conf.get("actionKind") == "tainted_tool_escalation":
                    tool_lbl = str(p_data.get("tool") or resolved_conf.get("targetPath") or "").strip()
                    tool_args = p_data.get("args") if isinstance(p_data.get("args"), dict) else {}
                    tool_out = ""
                    if tool_lbl:
                        try:
                            tool_out = _execute_llm_tool_call(
                                {"name": tool_lbl, "args": tool_args},
                                session_ctx,
                                companion_root or Path(__file__).resolve().parents[2],
                                now_ms,
                            )
                        except Exception as exc:
                            return _failed_confirm_response(resolved_cid, exc)
                    msg = (
                        f"Đã xác nhận và thực thi công cụ `{tool_lbl or 'tool'}`."
                        + (f"\n{tool_out[:800]}" if tool_out else "")
                        if lang == "vi"
                        else f"Confirmed and executed `{tool_lbl or 'tool'}`."
                        + (f"\n{tool_out[:800]}" if tool_out else "")
                    )
                else:
                    msg = (
                        f"Đã xác nhận thao tác cho `{resolved_conf.get('targetPath', '')}`."
                        if lang == "vi"
                        else f"Confirmed action for `{resolved_conf.get('targetPath', '')}`."
                    )
            else:
                msg = "Đã từ chối và huỷ thao tác đang chờ." if lang == "vi" else "Denied and cancelled pending action."
            summary = _persist_confirm_outcome(res_state, resolved_cid, msg)
            return {
                "ok": True,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "resolved": True,
                "resolution": res_state,
                "requiresConfirmation": False,
                "confirmId": "",
                "followUpPrompt": follow_prompt,
                "text": msg,
                "spokenText": msg,
                "expression": "happy" if decision == "confirm" else "idle",
                "dataPatch": [
                    {"path": "/resolved", "value": True},
                    {"path": "/resolution", "value": res_state},
                    {"path": "/statusLabel", "value": "Confirmed" if decision == "confirm" else "Cancelled"},
                    {"path": "/outcomeSummary", "value": summary},
                ],
                "clientActions": [],
            }

        # Stale or mismatched confirmation ID -> idempotently lock unresolved card without clobbering already-resolved outcome
        stale_msg = _outcome_summary(
            "Yêu cầu xác nhận này đã hết hạn hoặc đã xử lý trước đó."
            if lang == "vi"
            else "This confirmation request has already been resolved or expired."
        )
        existing_dm = existing_cs.get("dataModel") if isinstance(existing_cs.get("dataModel"), dict) else {}
        if existing_dm.get("resolved") is True:
            prev_res = str(existing_dm.get("resolution") or "expired")
            prev_lbl = str(existing_dm.get("statusLabel") or "Resolved")
            prev_out = str(existing_dm.get("outcomeSummary") or "")
            stale_patches: list[dict[str, Any]] = [
                {"path": "/resolved", "value": True},
                {"path": "/resolution", "value": prev_res},
                {"path": "/statusLabel", "value": prev_lbl},
            ]
            if prev_out:
                stale_patches.append({"path": "/outcomeSummary", "value": prev_out})
            stale_res_out = prev_res
        else:
            if surface_id:
                history_store.patch_surface_by_id(surface_id, lock_resolution="expired", outcome_summary=stale_msg)
            stale_patches = [
                {"path": "/resolved", "value": True},
                {"path": "/resolution", "value": "expired"},
                {"path": "/statusLabel", "value": "Expired"},
                {"path": "/outcomeSummary", "value": stale_msg},
            ]
            stale_res_out = "expired"
        return {
            "ok": True,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "stale": True,
            "resolved": True,
            "resolution": stale_res_out,
            "requiresConfirmation": False,
            "confirmId": "",
            "text": stale_msg,
            "dataPatch": stale_patches,
            "clientActions": [],
        }

    # 2. Plan checklist toggle / generic checkbox / choice / slider / form state persistence
    if event_name in ("plan_toggle", "checklist_toggle", "choice_submit", "slider_change", "form_submit"):
        patches: list[tuple[str, Any]] = []
        if event_name == "plan_toggle" and "index" in ctx_in:
            try:
                idx = int(ctx_in["index"])
                done_val = bool(ctx_in.get("done", False))
                patches.append((f"/steps/{idx}/done", done_val))
            except (TypeError, ValueError):
                pass
        else:
            if "path" in ctx_in:
                ptr = str(ctx_in.get("path") or "").strip()
                if a2ui_validator._is_valid_pointer(ptr):
                    # Supports A2UI v1.0 value=None key deletion!
                    patches.append((ptr, ctx_in.get("value")))
            if event_name == "form_submit":
                # Preserve all submitted form fields, not just a single `path` key.
                raw_fields = ctx_in.get("values") if isinstance(ctx_in.get("values"), dict) else (
                    ctx_in.get("fields") if isinstance(ctx_in.get("fields"), dict) else None
                )
                if isinstance(raw_fields, dict):
                    for fk, fv in list(raw_fields.items())[:24]:
                        fk_s = str(fk or "").strip()
                        if not fk_s:
                            continue
                        f_ptr = fk_s if fk_s.startswith("/") else f"/inputs/{fk_s}"
                        if a2ui_validator._is_valid_pointer(f_ptr):
                            patches.append((f_ptr, fv))
                for ck, cv in list(ctx_in.items())[:24]:
                    if ck in ("path", "value", "prompt", "values", "fields"):
                        continue
                    ck_s = str(ck or "").strip()
                    if not ck_s:
                        continue
                    c_ptr = ck_s if ck_s.startswith("/") else f"/inputs/{ck_s}"
                    if a2ui_validator._is_valid_pointer(c_ptr):
                        patches.append((c_ptr, cv))
        if surface_id and patches:
            patched_surf = history_store.patch_surface_by_id(surface_id, patches)
            if patched_surf is None:
                return {
                    "ok": False,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "error": "surface_patch_failed",
                    "text": (
                        "Không thể lưu thay đổi thẻ (vượt giới hạn 6 KB hoặc thẻ không tồn tại)."
                        if lang == "vi"
                        else "Could not persist card update (exceeded 6 KB limit or surface not found)."
                    ),
                    "dataPatch": [],
                    "clientActions": [],
                }
        follow_p = "" if surf_is_tainted else str(ctx_in.get("prompt") or "").strip()[:400]
        return {
            "ok": True,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "followUpPrompt": follow_p,
            "dataPatch": [{"path": p, "value": v} for p, v in patches],
            "clientActions": [],
        }

    # 3. Rollback receipt button on Receipt Card
    if event_name == "rollback_receipt":
        rcpt_id = str(ctx_in.get("receiptId") or "").strip()
        if not rcpt_id:
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "missing_receipt_id"}
        try:
            rb = agent_file_op({"op": "rollback", "receiptId": rcpt_id, "nowMs": now_ms})
            if surface_id:
                history_store.patch_surface_by_id(surface_id, [("/rolledBack", True)])
            msg = str(rb.get("summary") or ("Đã hoàn tác thay đổi." if lang == "vi" else "Rolled back file edit."))
            return {
                "ok": True,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "receiptId": rcpt_id,
                "rolledBack": True,
                "text": msg,
                "spokenText": msg,
                "expression": "happy",
                "dataPatch": [{"path": "/rolledBack", "value": True}],
                "clientActions": [],
            }
        except Exception as exc:
            return {
                "ok": False,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "error": str(exc)[:160],
            }

    # 3b. Open file in user's preferred editor (non-generative trusted cards only; guarded above)
    if event_name == "open_editor":
        raw_fp = str(ctx_in.get("path") or ctx_in.get("file") or "").strip()
        if not raw_fp or not a2ui_validator._is_safe_file_path(raw_fp):
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "invalid_file_path"}
        if is_symlink_raw(raw_fp) or is_sensitive_path(raw_fp):
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "forbidden_file_path"}
        target_p = Path(raw_fp).expanduser().resolve(strict=False)
        if is_forbidden_path(target_p) or is_sensitive_path(target_p):
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "forbidden_file_path"}
        try:
            opened = agent_file_op({
                "op": "open_editor",
                "targetPath": str(target_p),
                "nowMs": now_ms,
                "forceExternal": bool(ctx_in.get("forceExternal", False)),
            })
            msg = str(opened.get("summary") or (f"Đã mở `{target_p.name}`." if lang == "vi" else f"Opened `{target_p.name}`."))
            resp: dict[str, Any] = {
                "ok": opened.get("status") != "failed",
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "targetPath": str(opened.get("targetPath") or target_p),
                "text": msg,
                "dataPatch": [],
                "clientActions": [],
            }
            for k in ("editorMode", "editorPopup", "editorCommand"):
                if k in opened:
                    resp[k] = opened[k]
            return resp
        except Exception as exc:
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": str(exc)[:160]}

    # 3c. Save path or snippet to Companion Pocket
    if event_name == "save_pocket":
        import pocket as pocket_store

        raw_path = str(ctx_in.get("path") or ctx_in.get("file") or "").strip()
        raw_text = str(ctx_in.get("text") or ctx_in.get("content") or "").strip()
        raw_label = str(ctx_in.get("label") or ctx_in.get("title") or "").strip()[:120]
        try:
            if raw_path:
                if is_symlink_raw(raw_path) or is_sensitive_path(raw_path):
                    return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "invalid_pocket_path"}
                target_p = Path(raw_path).expanduser().resolve(strict=False)
                if is_forbidden_path(target_p) or is_sensitive_path(target_p) or not target_p.is_file():
                    return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "invalid_pocket_path"}
                perm = check_permission("file_read", target_path=str(target_p), source="a2ui_save_pocket", now_ms=now_ms)
                if not perm.get("allowed"):
                    return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "permission_denied"}
                pk_res = pocket_store.dispatch({
                    "version": 1,
                    "consent": True,
                    "action": "add",
                    "urls": [target_p.as_uri()],
                    "source": "drop",
                })
            elif raw_text:
                notes_dir = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "alis-companion" / "pocket_clips"
                notes_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_label or "clip").strip("_")[:40] or "clip"
                clip_file = (notes_dir / f"{slug}_{now_ms}.md").resolve(strict=False)
                clip_file.write_text(raw_text[:8000], encoding="utf-8")
                os.chmod(clip_file, 0o600)
                pk_res = pocket_store.dispatch({
                    "version": 1,
                    "consent": True,
                    "action": "add",
                    "urls": [clip_file.as_uri()],
                    "source": "drop",
                })
            else:
                return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "missing_pocket_payload"}
            if surface_id:
                history_store.patch_surface_by_id(surface_id, [("/savedToPocket", True)])
            msg = "Đã lưu vào Pocket." if lang == "vi" else "Saved to Pocket."
            return {
                "ok": bool(pk_res.get("ok", True)),
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "text": msg,
                "dataPatch": [{"path": "/savedToPocket", "value": True}],
                "clientActions": [],
            }
        except Exception as exc:
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": str(exc)[:160]}

    # 4. Safe follow-up prompt dispatch (already enforced against generative run_tests & tainted surfaces above)
    if event_name in ("send_prompt", "followup_prompt", "run_tests"):
        follow_p = str(ctx_in.get("prompt") or ("/tests" if event_name == "run_tests" else "")).strip()[:500]
        return {
            "ok": True,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "followUpPrompt": follow_p,
            "dataPatch": [],
            "clientActions": [],
        }

    return {
        "ok": True,
        "op": "a2ui_action",
        "eventName": event_name,
        "surfaceId": surface_id,
        "dataPatch": [],
        "clientActions": [],
    }



_TURN_ATTR_MAP = {
    "_ACTIVE_RUN_STEPS": "active_run_steps",
    "_ACTIVE_RUN_START_MS": "active_run_start_ms",
    "_ACTIVE_STEP_META": "active_step_meta",
    "_ACTIVE_SUBAGENT_RUNS": "active_subagent_runs",
    "_LAST_CANCELLED_STREAM_PREFIX": "last_cancelled_stream_prefix",
    "_CURRENT_TURN_LIVE_SNAPSHOT": "current_turn_live_snapshot",
    "_CURRENT_TURN_NEXUS_VAULT": "current_turn_nexus_vault",
    "_CURRENT_TURN_THREAD_ID": "current_turn_thread_id",
    "_LAST_LMSTUDIO_ERROR": "last_lmstudio_error",
    "_LAST_LMSTUDIO_LOADED_MODEL": "last_lmstudio_loaded_model",
}


class _TurnIsolatedModule(types.ModuleType):
    def __getattr__(self, name: str):
        fld = _TURN_ATTR_MAP.get(name)
        if fld is not None:
            return getattr(_tctx(), fld)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:
        fld = _TURN_ATTR_MAP.get(name)
        if fld is not None:
            setattr(_tctx(), fld, value)
            return
        super().__setattr__(name, value)


if __name__ in sys.modules:
    sys.modules[__name__].__class__ = _TurnIsolatedModule
