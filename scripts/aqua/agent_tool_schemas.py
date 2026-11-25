#!/usr/bin/env python3
"""Leaf tool schema catalog and deferred tool search for Alis-Companion.

Zero runtime dependencies on `agent_runtime.py` so both `agent_runtime` and
tests can import it cleanly without circular imports.
"""
from __future__ import annotations

import re

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
            "name": "screen_observe",
            "description": (
                "Observe the currently focused Linux/Niri desktop window and extract its semantic AT-SPI2 element tree "
                "(@e1, @e2 refs with coordinates and clickable/editable states), optional screenshot, and Set-of-Mark annotations. "
                "Use before and after GUI actions to ground targets and verify outcomes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "allowScreenshot": {"type": "boolean"},
                    "annotateSom": {"type": "boolean"},
                    "explicitVision": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_control",
            "description": (
                "Control Niri windows, columns, workspaces, applications, Wayland pointer/keyboard GUI actions "
                "(click, double_click, right_click, scroll, drag, fill, gui_type, gui_key), audio volume/devices/mic, "
                "brightness, media playback, Bluetooth, Wi-Fi, power profile, session lock/suspend, clipboard, screenshots, notifications, or voice STT/TTS."
            ),
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
                            "gui_type",
                            "gui_key",
                            "atspi_action",
                            "som_click",
                            "click",
                            "double_click",
                            "right_click",
                            "scroll",
                            "drag",
                            "fill",
                            "set_text",
                            "focus_element",
                            "bluetooth_status",
                            "bluetooth_toggle",
                            "bluetooth_connect",
                            "bluetooth_disconnect",
                            "wifi_status",
                            "wifi_toggle",
                            "wifi_list",
                            "wifi_connect",
                            "audio_devices",
                            "audio_set_sink",
                            "audio_set_source",
                            "mic_mute",
                            "power_profile",
                            "session_power",
                            "screenshot",
                        ],
                    },
                    "action_type": {"type": "string"},
                    "target": {"type": "string"},
                    "element": {"type": "string"},
                    "value": {"type": "string"},
                    "text": {"type": "string"},
                    "key": {"type": "string"},
                    "ref": {"type": "string", "description": "Semantic AT-SPI element reference (e.g. @e1, @e2)"},
                    "markId": {"type": "integer"},
                    "role": {"type": "string"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "double": {"type": "boolean"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "x2": {"type": "integer"},
                    "y2": {"type": "integer"},
                    "delta": {"type": "integer"},
                    "axis": {"type": "string", "enum": ["vertical", "horizontal"]},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_schedule",
            "description": (
                "Create, list, or cancel persistent desktop reminders, recurring schedules "
                "('daily@HH:MM', 'weekly@mon,wed@HH:MM', 'every:Nm'), or background process exit watchers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create_reminder", "create_recurring", "watch_process", "list_jobs", "cancel_job"],
                    },
                    "title": {"type": "string"},
                    "delayMinutes": {"type": "integer"},
                    "atTime": {"type": "string"},
                    "recurrence": {"type": "string"},
                    "pid": {"type": "integer"},
                    "processName": {"type": "string"},
                    "jobId": {"type": "string"},
                    "prompt": {"type": "string"},
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
                "Discover and load additional specialized tools (such as screen_observe, desktop_control, desktop_schedule, "
                "git_inspect, web_search, web_fetch, file_mutate, code_inspect, process_manage, mcp_call, skill_run, delegate_subagent) "
                "into the active schema list on demand."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords describing the capability needed (e.g. 'git diff', 'web fetch', 'screen observe', 'remind timer').",
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


def core_agent_tool_schemas(prompt: str = "") -> list[dict]:
    """Return the initial core subset of tool schemas."""
    return select_initial_tool_schemas(prompt=prompt, full=False)
