#!/usr/bin/env python3
"""Model Context Protocol (MCP) client, server registry, and built-in MCP server for Alis-Companion.

Supports:
- JSON-RPC 2.0 MCP protocol (`2024-11-05`) over `stdio`, `http`, and `builtin` transports.
- Standard `mcpServers` config compatibility (`~/.config/alis-companion/mcp.json` and
  `~/.local/state/alis-companion/mcp_servers.json`).
- Built-in `companion-system` MCP server providing desktop, system, clipboard, Pocket,
  and RAG tools/resources/prompts out of the box.
- Standalone stdio MCP server mode (`python3 mcp_client.py --stdio-server`) for testing
  and external MCP host integration.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import select
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


MCP_PROTOCOL_VERSION = "2024-11-05"


class McpError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _state_file() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    folder = (base / "alis-companion").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / "mcp_servers.json"


def _config_mcp_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (base / "alis-companion/mcp.json").resolve()


def _mcp_server_folder(name: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    safe_name = "".join(c for c in str(name or "default") if c.isalnum() or c in "-_.") or "default"
    folder = (base / "alis-companion" / "mcp" / safe_name).resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


BUILTIN_SERVER = {
    "name": "companion-system",
    "transport": "builtin",
    "package": "alis-companion/system",
    "command": "",
    "args": [],
    "url": "",
    "enabled": True,
    "builtin": True,
    "curated": True,
    "category": "system",
    "role": "Core System MCP",
    "version": "1.1.0",
    "latestVersion": "1.1.0",
    "hasUpdate": False,
    "repoUrl": "https://github.com/llocphann/Alis-Companion",
    "description": "Built-in Alis-Companion system health, clipboard, AquaPocket & RAG MCP server",
}

CURATED_SERVERS = [
    {
        "name": "computer-use-linux",
        "transport": "stdio",
        "package": "@agent-sh/computer-use-linux",
        "command": "npx",
        "args": ["-y", "@agent-sh/computer-use-linux"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "eyes-hands",
        "role": "Desktop Eyes & Hands",
        "version": "1.2.0",
        "latestVersion": "1.3.0",
        "hasUpdate": True,
        "repoUrl": "https://github.com/agent-sh/computer-use-linux",
        "description": "Linux/Wayland/Niri desktop Eyes & Hands: screenshots, AT-SPI2 UI tree, mouse/keyboard & window control",
    },
    {
        "name": "playwright-mcp",
        "transport": "stdio",
        "package": "@playwright/mcp",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "eyes-hands",
        "role": "Browser Eyes & Hands",
        "version": "0.0.28",
        "latestVersion": "0.0.31",
        "hasUpdate": True,
        "repoUrl": "https://github.com/microsoft/playwright-mcp",
        "description": "Official Playwright browser Eyes & Hands: accessibility snapshots, DOM navigation, click, type & screenshots",
    },
    {
        "name": "chrome-devtools-mcp",
        "transport": "stdio",
        "package": "chrome-devtools-mcp",
        "command": "npx",
        "args": ["-y", "chrome-devtools-mcp@latest"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "eyes-hands",
        "role": "DevTools Eyes & Hands",
        "version": "0.4.2",
        "latestVersion": "0.4.2",
        "hasUpdate": False,
        "repoUrl": "https://github.com/ChromeDevTools/chrome-devtools-mcp",
        "description": "Chrome DevTools protocol MCP: live DOM inspection, network requests, console logs & JS evaluation",
    },
    {
        "name": "web-search-mcp",
        "transport": "stdio",
        "package": "@nickclyde/duckduckgo-mcp-server",
        "command": "npx",
        "args": ["-y", "@nickclyde/duckduckgo-mcp-server"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "eyes",
        "role": "Web Search Eyes",
        "version": "1.1.0",
        "latestVersion": "1.1.0",
        "hasUpdate": False,
        "repoUrl": "https://github.com/nickclyde/duckduckgo-mcp-server",
        "description": "Live Web Search & Research Eyes: DuckDuckGo, Wikipedia, GitHub & SearXNG search without API keys",
    },
    {
        "name": "fetch-mcp",
        "transport": "stdio",
        "package": "@modelcontextprotocol/server-fetch",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "eyes",
        "role": "Web Reader Eyes",
        "version": "0.6.2",
        "latestVersion": "0.6.2",
        "hasUpdate": False,
        "repoUrl": "https://github.com/modelcontextprotocol/servers",
        "description": "Official MCP Web Reader Eyes: fetch any URL, strip ads/boilerplate, and extract clean Markdown & links",
    },
    {
        "name": "desktop-commander",
        "transport": "stdio",
        "package": "@wonderwhy-er/desktop-commander",
        "command": "npx",
        "args": ["-y", "@wonderwhy-er/desktop-commander@latest"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "hands",
        "role": "Terminal & Files Hands",
        "version": "0.2.14",
        "latestVersion": "0.2.16",
        "hasUpdate": True,
        "repoUrl": "https://github.com/wonderwhy-er/DesktopCommanderMCP",
        "description": "System & terminal Hands: background process management, surgical file block edits & command execution",
    },
    {
        "name": "git-mcp",
        "transport": "stdio",
        "package": "@modelcontextprotocol/server-git",
        "command": "uvx",
        "args": ["mcp-server-git"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "hands",
        "role": "Git Repo Hands",
        "version": "0.6.2",
        "latestVersion": "0.6.2",
        "hasUpdate": False,
        "repoUrl": "https://github.com/modelcontextprotocol/servers",
        "description": "Official Git MCP Hands: inspect status, diffs, commit history, branches & staging across local repositories",
    },
    {
        "name": "sqlite-mcp",
        "transport": "stdio",
        "package": "@modelcontextprotocol/server-sqlite",
        "command": "uvx",
        "args": ["mcp-server-sqlite"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "eyes",
        "role": "SQLite Data Eyes",
        "version": "0.6.2",
        "latestVersion": "0.6.2",
        "hasUpdate": False,
        "repoUrl": "https://github.com/modelcontextprotocol/servers",
        "description": "Local SQLite Data Eyes: inspect database schemas, list tables, and run safe read-only SQL queries on local DBs",
    },
    {
        "name": "pipewire-mpris-mcp",
        "transport": "stdio",
        "package": "alis-companion/pipewire-mpris",
        "command": "python3",
        "args": ["-m", "mcp_pipewire_mpris"],
        "url": "",
        "enabled": True,
        "builtin": False,
        "curated": True,
        "category": "hands",
        "role": "Audio, Media & Clipboard Hands",
        "version": "1.0.0",
        "latestVersion": "1.0.0",
        "hasUpdate": False,
        "repoUrl": "https://github.com/llocphann/Alis-Companion",
        "description": "Linux PipeWire, MPRIS & Wayland Clipboard Hands: control media players, audio volume, screen brightness & clipboard",
    },
    dict(BUILTIN_SERVER),
]

CURATED_SERVER_MAP = {s["name"]: s for s in CURATED_SERVERS}

BUILTIN_TOOLS = [
    {
        "server": "companion-system",
        "name": "system_info",
        "description": "Get Linux system health: OS, kernel, CPU load, RAM usage, disk space, and battery status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "server": "companion-system",
        "name": "active_window",
        "description": "Inspect the currently focused Niri/Wayland window and application metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "allowScreenshot": {"type": "boolean"},
            },
        },
    },
    {
        "server": "companion-system",
        "name": "clipboard_read",
        "description": "Read current desktop clipboard text safely (bounded to 2000 characters).",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "server": "companion-system",
        "name": "rag_search",
        "description": "Search Companion RAG knowledge base and return grounded snippets with citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "page": {"type": "integer", "description": "Result page (default 1)"},
            },
            "required": ["query"],
        },
    },
    {
        "server": "companion-system",
        "name": "pocket_list",
        "description": "List files and metadata references currently stored in AquaPocket.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "server": "companion-system",
        "name": "desktop_action",
        "description": "Control desktop windows, workspaces, applications, audio volume, screen brightness, media playback, clipboard, and desktop notifications.",
        "inputSchema": {
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
    {
        "server": "companion-system",
        "name": "process_list",
        "description": "List top CPU/RAM processes, search running processes by name, or terminate a process by PID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "search", "kill"]},
                "query": {"type": "string"},
                "pid": {"type": "integer"},
                "signal": {"type": "string"},
            },
        },
    },
    {
        "server": "companion-system",
        "name": "git_status",
        "description": "Inspect Git repository status, branch, recent commits, or diff in a workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "log", "diff", "branch"]},
                "workspace": {"type": "string"},
                "args": {"type": "string"},
            },
        },
    },
    {
        "server": "companion-system",
        "name": "shell_exec",
        "description": "Execute a shell command in the user session within a workspace directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
            },
            "required": ["command"],
        },
    },
]

CURATED_TOOLS = {
    "computer-use-linux": [
        {
            "server": "computer-use-linux",
            "name": "screenshot",
            "description": "Capture the active Wayland/Niri window or desktop screen and extract structured visual context.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "allowScreenshot": {"type": "boolean", "description": "Capture screen pixels (default true)"},
                },
            },
        },
        {
            "server": "computer-use-linux",
            "name": "get_accessibility_tree",
            "description": "Read the focused Linux/Wayland window metadata, headings, and AT-SPI2 UI accessibility tree.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "server": "computer-use-linux",
            "name": "mouse_click",
            "description": "Click mouse button at desktop coordinates (x, y) via Wayland portal / ydotool.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "targetWindowId": {"type": "integer"},
                },
                "required": ["x", "y"],
            },
        },
        {
            "server": "computer-use-linux",
            "name": "mouse_move",
            "description": "Move pointer to desktop coordinates (x, y).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                "required": ["x", "y"],
            },
        },
        {
            "server": "computer-use-linux",
            "name": "type_text",
            "description": "Type text into the focused Wayland window via wtype / ydotool.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "targetWindowId": {"type": "integer"},
                },
                "required": ["text"],
            },
        },
        {
            "server": "computer-use-linux",
            "name": "key_press",
            "description": "Press a key or keyboard shortcut (e.g. Return, Escape, ctrl+c) in the focused window.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "targetWindowId": {"type": "integer"},
                },
                "required": ["key"],
            },
        },
        {
            "server": "computer-use-linux",
            "name": "window_action",
            "description": "Manage Wayland/Niri windows, workspaces, and applications (list_windows, focus_window, close_window, switch_workspace, launch_app).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list_windows", "focus_window", "close_window", "switch_workspace", "launch_app"],
                    },
                    "target": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    ],
    "playwright-mcp": [
        {
            "server": "playwright-mcp",
            "name": "browser_snapshot",
            "description": "Capture accessibility tree and readable DOM snapshot of a web page URL.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Target URL to inspect"},
                },
                "required": ["url"],
            },
        },
        {
            "server": "playwright-mcp",
            "name": "browser_navigate",
            "description": "Navigate browser context to a URL and return page title and structured excerpt.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
        },
        {
            "server": "playwright-mcp",
            "name": "browser_click",
            "description": "Click an element on the page by accessibility ref or CSS selector.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string"},
                    "ref": {"type": "string"},
                },
                "required": ["element"],
            },
        },
        {
            "server": "playwright-mcp",
            "name": "browser_type",
            "description": "Type text into an editable web element.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "element": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["element", "text"],
            },
        },
        {
            "server": "playwright-mcp",
            "name": "browser_open",
            "description": "Open a URL or search query directly in the user's default browser (e.g. Zen Browser).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "URL or search query"},
                    "engine": {"type": "string", "enum": ["google", "duckduckgo", "youtube", "github", "wikipedia"]},
                },
                "required": ["target"],
            },
        },
        {
            "server": "playwright-mcp",
            "name": "browser_search",
            "description": "Search via browser and return top live web results.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "engine": {"type": "string"},
                    "openBrowser": {"type": "boolean"},
                },
                "required": ["query"],
            },
        },
    ],
    "web-search-mcp": [
        {
            "server": "web-search-mcp",
            "name": "web_search",
            "description": "Search the web (DuckDuckGo, Wikipedia, GitHub, SearXNG) and return titles, URLs, and snippets.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "maxResults": {"type": "integer", "description": "Max results (1-10)"},
                    "engine": {"type": "string", "enum": ["auto", "duckduckgo", "wikipedia", "github"]},
                },
                "required": ["query"],
            },
        },
        {
            "server": "web-search-mcp",
            "name": "github_search",
            "description": "Search GitHub repositories by keyword/topic and return stars, language, and repo URLs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "maxResults": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
        {
            "server": "web-search-mcp",
            "name": "wikipedia_search",
            "description": "Search Wikipedia (Vietnamese & English) and return article summaries and URLs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "maxResults": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    ],
    "fetch-mcp": [
        {
            "server": "fetch-mcp",
            "name": "fetch_url",
            "description": "Fetch any web URL, strip ads/scripts, and extract clean readable text, headings, and links.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "maxChars": {"type": "integer"},
                },
                "required": ["url"],
            },
        },
        {
            "server": "fetch-mcp",
            "name": "extract_links",
            "description": "Extract hyperlinks and anchor text from a web page URL.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    ],
    "chrome-devtools-mcp": [
        {
            "server": "chrome-devtools-mcp",
            "name": "inspect_dom",
            "description": "Inspect live DOM structure and metadata via Chrome DevTools MCP.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                },
            },
        },
        {
            "server": "chrome-devtools-mcp",
            "name": "list_network_requests",
            "description": "Inspect network request status and headers for a target URL.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
            },
        },
        {
            "server": "chrome-devtools-mcp",
            "name": "evaluate_script",
            "description": "Evaluate a diagnostic expression in the DevTools context.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                },
                "required": ["expression"],
            },
        },
    ],
    "desktop-commander": [
        {
            "server": "desktop-commander",
            "name": "execute_command",
            "description": "Execute a terminal command in a workspace directory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                },
                "required": ["command"],
            },
        },
        {
            "server": "desktop-commander",
            "name": "list_processes",
            "description": "List or manage running OS processes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "search", "kill"]},
                    "query": {"type": "string"},
                    "pid": {"type": "integer"},
                },
            },
        },
        {
            "server": "desktop-commander",
            "name": "read_file_excerpt",
            "description": "Read a bounded excerpt from a local file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    ],
    "git-mcp": [
        {
            "server": "git-mcp",
            "name": "git_status",
            "description": "Show the working tree status of a local Git repository.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to Git repository"},
                },
            },
        },
        {
            "server": "git-mcp",
            "name": "git_diff",
            "description": "Show unstaged or staged changes in a local Git repository.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "target": {"type": "string"},
                },
            },
        },
        {
            "server": "git-mcp",
            "name": "git_log",
            "description": "Show recent commit logs in a local Git repository.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "max_count": {"type": "integer"},
                },
            },
        },
        {
            "server": "git-mcp",
            "name": "git_branch",
            "description": "List branches in a local Git repository.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                },
            },
        },
    ],
    "sqlite-mcp": [
        {
            "server": "sqlite-mcp",
            "name": "list_tables",
            "description": "List all tables in a local SQLite database file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "db_path": {"type": "string", "description": "Path to .sqlite / .db file"},
                },
            },
        },
        {
            "server": "sqlite-mcp",
            "name": "describe_table",
            "description": "Inspect column schema of a table in a local SQLite database.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "db_path": {"type": "string"},
                    "table_name": {"type": "string"},
                },
                "required": ["table_name"],
            },
        },
        {
            "server": "sqlite-mcp",
            "name": "read_query",
            "description": "Execute a safe read-only SELECT query on a local SQLite database.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "db_path": {"type": "string"},
                    "query": {"type": "string", "description": "SELECT SQL query"},
                },
                "required": ["query"],
            },
        },
    ],
    "pipewire-mpris-mcp": [
        {
            "server": "pipewire-mpris-mcp",
            "name": "media_control",
            "description": "Control Linux MPRIS media players (status, play_pause, next, previous).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "play_pause", "next", "previous"]},
                },
            },
        },
        {
            "server": "pipewire-mpris-mcp",
            "name": "audio_volume",
            "description": "Get or adjust PipeWire/PulseAudio volume (get, up, down, set, mute).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "up", "down", "set", "mute"]},
                    "value": {"type": "string"},
                },
            },
        },
        {
            "server": "pipewire-mpris-mcp",
            "name": "clipboard_inspect",
            "description": "Read or write Wayland/X11 clipboard content safely.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write"]},
                    "text": {"type": "string"},
                },
            },
        },
    ],
    "companion-system": BUILTIN_TOOLS,
}

BUILTIN_RESOURCES = [
    {
        "server": "companion-system",
        "uri": "companion://system/overview",
        "name": "System Overview",
        "mimeType": "application/json",
        "description": "Live OS, memory, disk, load average, and battery snapshot.",
    },
    {
        "server": "companion-system",
        "uri": "companion://pocket/items",
        "name": "AquaPocket Items",
        "mimeType": "application/json",
        "description": "Metadata references pinned in AquaPocket.",
    },
    {
        "server": "companion-system",
        "uri": "companion://rag/sources",
        "name": "RAG Knowledge Sources",
        "mimeType": "application/json",
        "description": "Configured RAG folders and files.",
    },
]

BUILTIN_PROMPTS = [
    {
        "server": "companion-system",
        "name": "daily-briefing",
        "description": "Summarize current system state, Pocket files, and RAG knowledge sources.",
        "arguments": [],
    },
]



def _collect_system_info() -> dict:
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "hostname": platform.node(),
        "arch": platform.machine(),
    }
    try:
        load1, load5, load15 = os.getloadavg()
        info["loadAvg"] = f"{load1:.2f}, {load5:.2f}, {load15:.2f}"
    except OSError:
        info["loadAvg"] = "unavailable"
    try:
        meminfo = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meminfo[k.strip()] = int(v.strip().split()[0])
        total_mb = meminfo.get("MemTotal", 0) // 1024
        avail_mb = meminfo.get("MemAvailable", 0) // 1024
        used_mb = max(0, total_mb - avail_mb)
        info["memory"] = f"{used_mb} MiB / {total_mb} MiB"
    except Exception:
        info["memory"] = "unavailable"
    try:
        usage = shutil.disk_usage(str(Path.home()))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        info["diskHome"] = f"{free_gb:.1f} GiB free / {total_gb:.1f} GiB total"
    except Exception:
        info["diskHome"] = "unavailable"
    try:
        bats = sorted(Path("/sys/class/power_supply").glob("BAT*"))
        if bats:
            cap = (bats[0] / "capacity").read_text(encoding="utf-8").strip()
            status = (bats[0] / "status").read_text(encoding="utf-8").strip()
            info["battery"] = f"{cap}% ({status})"
    except Exception:
        pass
    return info


def _execute_builtin_tool(tool_name: str, arguments: dict, companion_root: Path | None = None) -> dict:
    comp_root = companion_root or Path(__file__).resolve().parents[2]
    if tool_name == "system_info":
        sys_info = _collect_system_info()
        text = "\n".join(f"- {k}: {v}" for k, v in sys_info.items())
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": text}],
            "structured": sys_info,
        }
    if tool_name == "active_window":
        from screen_context import observe_active_window
        allow_ss = arguments.get("allowScreenshot") is not False
        obs = observe_active_window(allow_screenshot=allow_ss)
        lines = obs.get("headings", [])[:12]
        text = f"Window: {obs.get('packageId', 'desktop')} ({obs.get('version', 'metadata')})\n" + "\n".join(f"- {ln}" for ln in lines)
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": text.strip()}],
            "structured": obs,
        }
    if tool_name == "clipboard_read":
        clip_text = ""
        if shutil.which("wl-paste"):
            try:
                proc = subprocess.run(
                    ["wl-paste", "--no-newline"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
                if proc.returncode == 0:
                    clip_text = proc.stdout.decode("utf-8", errors="replace")[:2000]
            except Exception:
                pass
        elif shutil.which("xclip"):
            try:
                proc = subprocess.run(
                    ["xclip", "-o", "-selection", "clipboard"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
                if proc.returncode == 0:
                    clip_text = proc.stdout.decode("utf-8", errors="replace")[:2000]
            except Exception:
                pass
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": clip_text or "(clipboard is empty or unavailable)"}],
        }
    if tool_name == "rag_search":
        from code_tools import retrieve_rag
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise McpError("invalid_arguments", "rag_search requires a non-empty 'query' argument.")
        page = int(arguments.get("page") or 1)
        rag = retrieve_rag(query, companion_root=comp_root, page=page, page_size=3)
        text = "\n".join(rag["snippets"]) if rag["snippets"] else "No matching RAG snippets found."
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": text}],
            "structured": rag,
        }
    if tool_name == "pocket_list":
        import pocket as pocket_store
        state = pocket_store.dispatch({"version": 1, "consent": True, "action": "list"})
        items = state.get("items", [])
        text = (
            "\n".join(f"- {i['name']} ({i['path']}, {i['size']} bytes)" for i in items[:20])
            if items
            else "AquaPocket is empty."
        )
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": text}],
            "structured": {"items": items},
        }
    if tool_name == "desktop_action":
        import desktop_control
        act = str(arguments.get("action") or "list_windows").strip()
        target = str(arguments.get("target") or "").strip()
        val = str(arguments.get("value") or "").strip()
        try:
            res = desktop_control.manage_desktop(action=act, target=target, value=val)
        except desktop_control.DesktopControlError as exc:
            raise McpError(exc.code, exc.message) from exc
        text = res["summary"] + ("\n" + "\n".join(res.get("lines", [])) if res.get("lines") else "")
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": text.strip()}],
            "structured": res,
        }
    if tool_name == "process_list":
        import desktop_control
        act = str(arguments.get("action") or "list").strip()
        query = str(arguments.get("query") or "").strip()
        pid = int(arguments.get("pid") or 0)
        sig = str(arguments.get("signal") or "TERM").strip()
        try:
            res = desktop_control.manage_processes(action=act, query=query, pid=pid, signal_name=sig)
        except desktop_control.DesktopControlError as exc:
            raise McpError(exc.code, exc.message) from exc
        text = res["summary"] + ("\n" + "\n".join(res.get("lines", [])) if res.get("lines") else "")
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": text.strip()}],
            "structured": res,
        }
    if tool_name == "git_status":
        import desktop_control
        act = str(arguments.get("action") or "status").strip()
        ws = str(arguments.get("workspace") or Path.home()).strip()
        args_str = str(arguments.get("args") or "").strip()
        try:
            res = desktop_control.execute_git_task(workspace=ws, subaction=act, extra_args=args_str)
        except desktop_control.DesktopControlError as exc:
            raise McpError(exc.code, exc.message) from exc
        text = res["summary"] + ("\n" + "\n".join(res.get("lines", [])) if res.get("lines") else "")
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": text.strip()}],
            "structured": res,
        }
    if tool_name == "shell_exec":
        from sandbox_runner import SandboxError, run_user_command
        cmd = str(arguments.get("command") or "").strip()
        cwd = str(arguments.get("cwd") or Path.home()).strip()
        if not cmd:
            raise McpError("invalid_arguments", "shell_exec requires a non-empty 'command' argument.")
        try:
            res = run_user_command(cmd, cwd, timeout_sec=15.0)
        except SandboxError as exc:
            raise McpError(exc.code, str(exc)) from exc
        text = f"exit={res['exitCode']}\n" + "\n".join(res.get("lines", []))
        return {
            "server": "companion-system",
            "tool": tool_name,
            "isError": res["exitCode"] != 0,
            "content": [{"type": "text", "text": text.strip()}],
            "structured": res,
        }
    raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server 'companion-system'.")


def _execute_curated_tool(
    server_name: str,
    tool_name: str,
    arguments: dict,
    companion_root: Path | None = None,
) -> dict:
    comp_root = companion_root or Path(__file__).resolve().parents[2]
    if server_name == "companion-system":
        return _execute_builtin_tool(tool_name, arguments, companion_root=comp_root)

    if server_name == "computer-use-linux":
        from screen_context import ScreenContextError, observe_active_window, perform_gui_action
        import desktop_control

        if tool_name in ("screenshot", "get_accessibility_tree", "accessibility_tree", "active_window"):
            allow_ss = (tool_name == "screenshot") and (arguments.get("allowScreenshot") is not False)
            obs = observe_active_window(
                allow_screenshot=allow_ss,
                previous_window_id=arguments.get("previousWindowId"),
                now_ms=arguments.get("nowMs"),
                capture_image=bool(arguments.get("captureImage", False)),
                skip_ocr=bool(arguments.get("skipOcr", False)),
            )
            lines = obs.get("headings", [])[:12]
            text = (
                f"Window: {obs.get('packageId', 'desktop')} (source={obs.get('observation', {}).get('source', obs.get('version', 'metadata'))})\n"
                + "\n".join(f"- {ln}" for ln in lines)
            )
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": text.strip()}],
                "structured": obs,
            }
        if tool_name == "mouse_click":
            try:
                res = perform_gui_action(
                    "click",
                    target_window_id=arguments.get("targetWindowId"),
                    x=int(arguments.get("x") or 0),
                    y=int(arguments.get("y") or 0),
                    button=str(arguments.get("button") or "left"),
                )
            except ScreenContextError as exc:
                raise McpError(exc.code, exc.message) from exc
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": f"Clicked {res.get('button', 'left')} at ({res.get('x', 0)}, {res.get('y', 0)})"}],
                "structured": res,
            }
        if tool_name == "mouse_move":
            try:
                res = perform_gui_action(
                    "move",
                    x=int(arguments.get("x") or 0),
                    y=int(arguments.get("y") or 0),
                )
            except ScreenContextError as exc:
                raise McpError(exc.code, exc.message) from exc
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": f"Moved cursor to ({res.get('x', 0)}, {res.get('y', 0)})"}],
                "structured": res,
            }
        if tool_name == "type_text":
            try:
                res = perform_gui_action(
                    "type",
                    target_window_id=arguments.get("targetWindowId"),
                    text=str(arguments.get("text") or ""),
                )
            except ScreenContextError as exc:
                raise McpError(exc.code, exc.message) from exc
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": f"Typed text ({res.get('textLength', 0)} chars) via {res.get('backend', 'wayland')}"}],
                "structured": res,
            }
        if tool_name == "key_press":
            try:
                res = perform_gui_action(
                    "key",
                    target_window_id=arguments.get("targetWindowId"),
                    key=str(arguments.get("key") or "Return"),
                )
            except ScreenContextError as exc:
                raise McpError(exc.code, exc.message) from exc
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": f"Pressed key '{res.get('key', '')}' via {res.get('backend', 'wayland')}"}],
                "structured": res,
            }
        if tool_name == "window_action":
            act = str(arguments.get("action") or "list_windows").strip()
            if act == "inspect_window" or (act == "focus_window" and "targetWindowId" in arguments):
                try:
                    gui_res = perform_gui_action(
                        action_type=act,
                        target_window_id=arguments.get("targetWindowId"),
                        require_scale=bool(arguments.get("requireScale", False)),
                        now_ms=arguments.get("nowMs"),
                    )
                except ScreenContextError as exc:
                    raise McpError(exc.code, exc.message) from exc
                return {
                    "server": server_name,
                    "tool": tool_name,
                    "isError": False,
                    "content": [{"type": "text", "text": gui_res["summary"]}],
                    "structured": gui_res,
                }
            target = str(arguments.get("target") or "").strip()
            val = str(arguments.get("value") or "").strip()
            try:
                res = desktop_control.manage_desktop(action=act, target=target, value=val)
            except desktop_control.DesktopControlError as exc:
                raise McpError(exc.code, exc.message) from exc
            text = res["summary"] + ("\n" + "\n".join(res.get("lines", [])) if res.get("lines") else "")
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": text.strip()}],
                "structured": res,
            }
        raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")

    if server_name == "playwright-mcp":
        if tool_name in ("browser_snapshot", "browser_navigate"):
            from web_tools import WebToolError, fetch_url_excerpt
            url = str(arguments.get("url") or "").strip()
            if not url:
                raise McpError("invalid_arguments", f"{tool_name} requires a non-empty 'url'.")
            try:
                fetched = fetch_url_excerpt(url)
            except WebToolError as exc:
                raise McpError(exc.code, exc.message) from exc
            text = f"Title: {fetched.get('title') or url}\nURL: {fetched.get('url', url)}\n" + "\n".join(fetched.get("lines", [])[:15])
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": text.strip()}],
                "structured": fetched,
            }
        if tool_name in ("browser_click", "browser_type"):
            elem = str(arguments.get("element") or arguments.get("ref") or "target").strip()
            val = str(arguments.get("text") or "").strip()
            summary = f"Playwright {tool_name} on '{elem}'" + (f" with '{val[:60]}'" if val else "")
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": summary}],
                "structured": {"action": tool_name, "element": elem, "text": val},
            }
        if tool_name == "browser_open":
            from web_tools import WebToolError, open_in_browser
            target = str(arguments.get("target") or arguments.get("url") or arguments.get("query") or "").strip()
            engine = str(arguments.get("engine") or "google").strip()
            play_media = arguments.get("playMedia")
            try:
                opened = open_in_browser(target, engine=engine, play_media=play_media)
            except WebToolError as exc:
                raise McpError(exc.code, exc.message) from exc
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": opened["summary"]}],
                "structured": opened,
            }
        if tool_name == "browser_search":
            from web_tools import WebToolError, open_in_browser, web_search
            query = str(arguments.get("query") or arguments.get("target") or "").strip()
            engine = str(arguments.get("engine") or "auto").strip()
            try:
                search_res = web_search(query, max_results=int(arguments.get("maxResults") or 5), engine=engine)
                if arguments.get("openBrowser"):
                    browser_info = open_in_browser(query, engine=engine if engine != "auto" else "google")
                    search_res["browserOpened"] = browser_info
            except WebToolError as exc:
                raise McpError(exc.code, exc.message) from exc
            text = f"Web Search ({search_res['engine']}) for '{query}':\n" + "\n".join(search_res.get("lines", []))
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": text.strip()}],
                "structured": search_res,
            }
        raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")

    if server_name == "web-search-mcp":
        from web_tools import WebToolError, web_search
        query = str(arguments.get("query") or "").strip()
        max_res = int(arguments.get("maxResults") or 6)
        if tool_name == "github_search":
            eng = "github"
        elif tool_name == "wikipedia_search":
            eng = "wikipedia"
        elif tool_name == "web_search":
            eng = str(arguments.get("engine") or "auto").strip()
        else:
            raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")
        try:
            res = web_search(query, max_results=max_res, engine=eng)
        except WebToolError as exc:
            raise McpError(exc.code, exc.message) from exc
        body = "\n".join(res.get("lines", [])) or f"No results found for '{query}'."
        return {
            "server": server_name,
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": f"Search results ({res['engine']}) for '{query}':\n{body}"}],
            "structured": res,
        }

    if server_name == "fetch-mcp":
        from web_tools import WebToolError, fetch_url_excerpt
        url = str(arguments.get("url") or arguments.get("query") or "").strip()
        if not url:
            raise McpError("invalid_arguments", f"{tool_name} requires a non-empty 'url'.")
        max_chars = int(arguments.get("maxChars") or 3600)
        try:
            fetched = fetch_url_excerpt(url, max_chars=max_chars)
        except WebToolError as exc:
            raise McpError(exc.code, exc.message) from exc
        if tool_name == "extract_links":
            links = fetched.get("links", [])
            l_lines = [f"- [{lnk['text']}]({lnk['url']})" for lnk in links[:20]] or ["(no links found)"]
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": f"Links from {fetched['url']}:\n" + "\n".join(l_lines)}],
                "structured": {"url": fetched["url"], "links": links},
            }
        if tool_name in ("fetch_url", "fetch"):
            h_str = ("\nHeadings: " + " · ".join(fetched["headings"][:6])) if fetched.get("headings") else ""
            body_str = "\n".join(fetched.get("lines", [])[:25])
            text = f"Title: {fetched.get('title') or url}\nURL: {fetched.get('url', url)}{h_str}\n\n{body_str}"
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": text.strip()}],
                "structured": fetched,
            }
        raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")

    if server_name == "chrome-devtools-mcp":
        if tool_name in ("inspect_dom", "list_network_requests", "evaluate_script"):
            url = str(arguments.get("url") or "").strip()
            selector = str(arguments.get("selector") or "document.body").strip()
            expr = str(arguments.get("expression") or "").strip()
            if url:
                from web_tools import WebToolError, fetch_url_excerpt
                try:
                    fetched = fetch_url_excerpt(url)
                    text = f"DevTools {tool_name} ({url}): {fetched.get('title') or 'OK'}\n" + "\n".join(fetched.get("lines", [])[:10])
                except WebToolError as exc:
                    text = f"DevTools {tool_name} ({url}): {exc.message}"
            else:
                from screen_context import observe_active_window
                obs = observe_active_window(allow_screenshot=False)
                text = f"DevTools {tool_name} on active window '{obs.get('packageId', 'browser')}' (selector={selector}, expr={expr or 'none'})"
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": text.strip()}],
            }
        raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")

    if server_name == "desktop-commander":
        if tool_name == "execute_command":
            return _execute_builtin_tool("shell_exec", arguments, companion_root=comp_root)
        if tool_name == "list_processes":
            return _execute_builtin_tool("process_list", arguments, companion_root=comp_root)
        if tool_name == "read_file_excerpt":
            from permission_store import is_forbidden_path
            raw_path = str(arguments.get("path") or "").strip()
            if not raw_path:
                raise McpError("invalid_arguments", "read_file_excerpt requires a non-empty 'path'.")
            fpath = Path(raw_path).expanduser().resolve(strict=False)
            if is_forbidden_path(fpath) or not fpath.is_file():
                raise McpError("file_unavailable", f"File '{raw_path}' is unavailable or not permitted.")
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
            except OSError as exc:
                raise McpError("file_read_failed", str(exc)) from exc
            res = {"path": str(fpath), "lines": lines, "lineCount": len(lines)}
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": "\n".join(lines)}],
                "structured": res,
            }
        raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")

    if server_name == "git-mcp":
        import desktop_control
        ws = str(arguments.get("repo_path") or arguments.get("workspace") or comp_root).strip()
        sub_map = {
            "git_status": "status",
            "git_diff": "diff",
            "git_log": "log",
            "git_branch": "branch",
        }
        if tool_name not in sub_map:
            raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")
        extra = str(arguments.get("target") or arguments.get("args") or "").strip()
        try:
            res = desktop_control.execute_git_task(workspace=ws, subaction=sub_map[tool_name], extra_args=extra)
        except desktop_control.DesktopControlError as exc:
            raise McpError(exc.code, exc.message) from exc
        text = res["summary"] + ("\n" + "\n".join(res.get("lines", [])) if res.get("lines") else "")
        return {
            "server": server_name,
            "tool": tool_name,
            "isError": False,
            "content": [{"type": "text", "text": text.strip()}],
            "structured": res,
        }

    if server_name == "sqlite-mcp":
        import sqlite3
        from permission_store import is_forbidden_path
        raw_db = str(arguments.get("db_path") or "").strip()
        if not raw_db:
            state_base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "alis-companion"
            cand_dbs = sorted(state_base.glob("*.db")) + sorted(state_base.glob("*.sqlite*"))
            db_path = cand_dbs[0] if cand_dbs else (state_base / "history.db")
        else:
            db_path = Path(raw_db).expanduser().resolve(strict=False)
        if is_forbidden_path(db_path):
            raise McpError("db_forbidden", f"Database path '{db_path}' is restricted.")
        if not db_path.is_file():
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": f"SQLite database '{db_path.name}' does not exist yet (0 tables)."}],
                "structured": {"dbPath": str(db_path), "rows": []},
            }
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
            conn.row_factory = sqlite3.Row
            try:
                if tool_name == "list_tables":
                    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                    tables = [str(r["name"]) for r in cur.fetchall()]
                    text = f"Tables in `{db_path.name}` ({len(tables)}): " + (", ".join(tables) if tables else "(none)")
                    return {
                        "server": server_name,
                        "tool": tool_name,
                        "isError": False,
                        "content": [{"type": "text", "text": text}],
                        "structured": {"dbPath": str(db_path), "tables": tables},
                    }
                if tool_name == "describe_table":
                    tname = re.sub(r"[^a-zA-Z0-9_]", "", str(arguments.get("table_name") or ""))
                    if not tname:
                        raise McpError("invalid_arguments", "describe_table requires 'table_name'.")
                    cur = conn.execute(f"PRAGMA table_info({tname})")
                    cols = [{"name": r["name"], "type": r["type"], "notnull": bool(r["notnull"]), "pk": bool(r["pk"])} for r in cur.fetchall()]
                    lines = [f"- `{c['name']}` ({c['type'] or 'ANY'}{', PK' if c['pk'] else ''})" for c in cols] or ["(table not found or empty schema)"]
                    return {
                        "server": server_name,
                        "tool": tool_name,
                        "isError": False,
                        "content": [{"type": "text", "text": f"Schema for `{tname}`:\n" + "\n".join(lines)}],
                        "structured": {"table": tname, "columns": cols},
                    }
                if tool_name == "read_query":
                    sql = str(arguments.get("query") or "").strip()
                    if not re.match(r"^(?:SELECT|PRAGMA|EXPLAIN)\b", sql, re.IGNORECASE):
                        raise McpError("read_only_violation", "sqlite-mcp read_query only permits SELECT/PRAGMA/EXPLAIN queries.")
                    cur = conn.execute(sql)
                    rows = [dict(r) for r in cur.fetchmany(25)]
                    text_rows = [json.dumps(r, ensure_ascii=False) for r in rows] or ["(0 rows)"]
                    return {
                        "server": server_name,
                        "tool": tool_name,
                        "isError": False,
                        "content": [{"type": "text", "text": "\n".join(text_rows)}],
                        "structured": {"rowCount": len(rows), "rows": rows},
                    }
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise McpError("sqlite_error", f"SQLite error: {exc}") from exc
        raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")

    if server_name == "pipewire-mpris-mcp":
        import desktop_control
        if tool_name == "media_control":
            act = str(arguments.get("action") or "status").strip().lower()
            desk_act = "media_next" if act == "next" else ("media_prev" if act == "previous" else "media_play_pause")
            if act == "status":
                if shutil.which("playerctl"):
                    proc = subprocess.run(["playerctl", "metadata", "--format", "{{playerName}}: {{artist}} - {{title}} ({{status}})"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2.0, check=False, text=True)
                    status_txt = proc.stdout.strip() or "No active MPRIS media player."
                else:
                    status_txt = "playerctl not installed or no active MPRIS player."
                return {
                    "server": server_name,
                    "tool": tool_name,
                    "isError": False,
                    "content": [{"type": "text", "text": status_txt}],
                    "structured": {"status": status_txt},
                }
            res = desktop_control.manage_desktop(action=desk_act)
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": res["summary"]}],
                "structured": res,
            }
        if tool_name == "audio_volume":
            act = str(arguments.get("action") or "get").strip().lower()
            val = str(arguments.get("value") or "").strip()
            if act == "get":
                if shutil.which("wpctl"):
                    proc = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2.0, check=False, text=True)
                    vol_txt = proc.stdout.strip() or "Volume state unavailable."
                else:
                    vol_txt = "PipeWire wpctl unavailable."
                return {
                    "server": server_name,
                    "tool": tool_name,
                    "isError": False,
                    "content": [{"type": "text", "text": vol_txt}],
                    "structured": {"volume": vol_txt},
                }
            desk_map = {"up": "volume_up", "down": "volume_down", "set": "volume_set", "mute": "volume_mute"}
            res = desktop_control.manage_desktop(action=desk_map.get(act, "volume_up"), value=val)
            return {
                "server": server_name,
                "tool": tool_name,
                "isError": False,
                "content": [{"type": "text", "text": res["summary"]}],
                "structured": res,
            }
        if tool_name == "clipboard_inspect":
            act = str(arguments.get("action") or "read").strip().lower()
            if act == "write":
                txt = str(arguments.get("text") or "")
                res = desktop_control.manage_desktop(action="clipboard_write", value=txt)
                return {
                    "server": server_name,
                    "tool": tool_name,
                    "isError": False,
                    "content": [{"type": "text", "text": res["summary"]}],
                    "structured": res,
                }
            clip_res = _execute_builtin_tool("clipboard_read", {}, companion_root=comp_root)
            clip_res["server"] = server_name
            clip_res["tool"] = tool_name
            return clip_res
        raise McpError("tool_not_found", f"Unknown tool '{tool_name}' on server '{server_name}'.")

    raise McpError("server_not_curated", f"Server '{server_name}' is not a curated built-in server.")


def _read_builtin_resource(uri: str) -> dict:
    if uri == "companion://system/overview":
        data = _collect_system_info()
        return {
            "server": "companion-system",
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(data, ensure_ascii=False),
        }
    if uri == "companion://pocket/items":
        import pocket as pocket_store
        state = pocket_store.dispatch({"version": 1, "consent": True, "action": "list"})
        return {
            "server": "companion-system",
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(state.get("items", []), ensure_ascii=False),
        }
    if uri == "companion://rag/sources":
        import rag_store
        sources = rag_store.list_sources()
        return {
            "server": "companion-system",
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(sources, ensure_ascii=False),
        }
    raise McpError("resource_not_found", f"Unknown MCP resource URI '{uri}'.")


def _version_tuple(ver_str: str) -> tuple[int, ...]:
    cleaned = str(ver_str or "").strip().lstrip("vV").split("-", 1)[0]
    parts: list[int] = []
    for piece in cleaned.split("."):
        digits = "".join(c for c in piece if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


def _newer_version(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    return a if _version_tuple(a) >= _version_tuple(b) else b


def _enrich_server_entry(entry: dict, curated_default: dict | None = None) -> dict:
    base = dict(curated_default) if isinstance(curated_default, dict) else {}
    sname = str(entry.get("name") or base.get("name") or "").strip()[:64]
    transport = str(entry.get("transport") or base.get("transport") or ("http" if entry.get("url") else "stdio"))
    if sname == "companion-system":
        transport = "builtin"
    elif transport not in ("stdio", "http", "builtin"):
        transport = "stdio"
    version = str(entry.get("version") or base.get("version") or "1.0.0").strip()[:32]
    catalog_latest = str(base.get("latestVersion") or version).strip()[:32]
    entry_latest = str(entry.get("latestVersion") or "").strip()[:32]
    latest_version = _newer_version(entry_latest, _newer_version(catalog_latest, version))
    has_update = bool(version != latest_version)
    folder = str(_mcp_server_folder(sname))
    return {
        "name": sname,
        "transport": transport,
        "package": str(entry.get("package") or base.get("package") or sname)[:120],
        "command": str(entry.get("command") if "command" in entry else base.get("command", ""))[:1024],
        "args": [str(a)[:512] for a in (entry.get("args") if isinstance(entry.get("args"), list) else base.get("args", []))[:32]],
        "url": str(entry.get("url") if "url" in entry else base.get("url", ""))[:1024],
        "env": {str(k)[:64]: str(v)[:512] for k, v in (entry.get("env") or base.get("env") or {}).items()} if isinstance(entry.get("env") or base.get("env"), dict) else {},
        "enabled": entry.get("enabled") is not False if "enabled" in entry else (base.get("enabled") is not False),
        "builtin": bool(sname == "companion-system" or entry.get("builtin") or base.get("builtin")),
        "curated": bool(sname in CURATED_SERVER_MAP or entry.get("curated") or base.get("curated")),
        "category": str(entry.get("category") or base.get("category") or "custom")[:40],
        "role": str(entry.get("role") or base.get("role") or f"MCP ({transport})")[:64],
        "version": version,
        "latestVersion": latest_version,
        "hasUpdate": has_update,
        "repoUrl": str(entry.get("repoUrl") or base.get("repoUrl") or "")[:512],
        "folder": folder,
        "description": str(entry.get("description") or base.get("description") or f"MCP server ({transport})")[:200],
    }


def _installed_server_version(sname: str) -> str:
    if not sname:
        return ""
    inst_file = _mcp_server_folder(sname) / "installed.json"
    if inst_file.is_file():
        try:
            inst = json.loads(inst_file.read_text(encoding="utf-8"))
            if isinstance(inst, dict) and inst.get("version"):
                return str(inst["version"]).strip()
        except (OSError, ValueError):
            pass
    return ""


def load_mcp_registry() -> dict:
    servers: dict[str, dict] = {}
    for c_srv in CURATED_SERVERS:
        sname = c_srv["name"]
        merged_default = dict(c_srv)
        inst_ver = _installed_server_version(sname)
        if inst_ver:
            merged_default["version"] = _newer_version(inst_ver, str(c_srv.get("version") or "1.0.0"))
        servers[sname] = _enrich_server_entry(merged_default, c_srv)

    # Load optional ~/.config/alis-companion/mcp.json (Claude/Cursor mcpServers format)
    cfg_path = _config_mcp_file()
    if cfg_path.is_file():
        try:
            raw_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            mcp_map = raw_cfg.get("mcpServers") if isinstance(raw_cfg, dict) else None
            if isinstance(mcp_map, dict):
                for name, spec in list(mcp_map.items())[:32]:
                    if not isinstance(spec, dict) or not name:
                        continue
                    sname = str(name).strip()[:64]
                    base_srv = servers.get(sname) or CURATED_SERVER_MAP.get(sname) or {}
                    merged = dict(base_srv)
                    merged.update(spec)
                    merged["name"] = sname
                    inst_ver = _installed_server_version(sname)
                    best_ver = _newer_version(
                        inst_ver,
                        _newer_version(str(spec.get("version") or ""), str(base_srv.get("version") or "1.0.0")),
                    )
                    if best_ver:
                        merged["version"] = best_ver
                    servers[sname] = _enrich_server_entry(merged, CURATED_SERVER_MAP.get(sname) or base_srv)
        except (OSError, ValueError):
            pass

    state_path = _state_file()
    if state_path.is_file():
        try:
            raw_state = json.loads(state_path.read_text(encoding="utf-8"))
            for entry in (raw_state.get("servers") or [])[:32]:
                if not isinstance(entry, dict) or not entry.get("name"):
                    continue
                sname = str(entry["name"]).strip()[:64]
                inst_ver = _installed_server_version(sname)
                existing_ver = str((servers.get(sname) or {}).get("version") or "")
                if sname in CURATED_SERVER_MAP:
                    c_default = CURATED_SERVER_MAP[sname]
                    merged = dict(c_default)
                    merged["enabled"] = entry.get("enabled") is not False
                    best_ver = _newer_version(
                        inst_ver,
                        _newer_version(
                            str(entry.get("version") or ""),
                            _newer_version(existing_ver, str(c_default.get("version") or "1.0.0")),
                        ),
                    )
                    if best_ver:
                        merged["version"] = best_ver
                    if entry.get("latestVersion"):
                        merged["latestVersion"] = _newer_version(
                            str(entry["latestVersion"]),
                            str(c_default.get("latestVersion") or ""),
                        )
                    servers[sname] = _enrich_server_entry(merged, c_default)
                    continue
                custom_entry = dict(entry)
                best_custom_ver = _newer_version(inst_ver, _newer_version(str(entry.get("version") or ""), existing_ver))
                if best_custom_ver:
                    custom_entry["version"] = best_custom_ver
                servers[sname] = _enrich_server_entry(custom_entry)
        except (OSError, ValueError):
            pass

    return {"version": 1, "servers": list(servers.values())}


def save_mcp_registry(servers: list[dict]) -> None:
    path = _state_file()
    payload = json.dumps(
        {"version": 1, "servers": servers[:32]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".mcp-servers-", dir=str(path.parent))
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
        raise McpError("mcp_store_unavailable", "Could not save MCP server registry.") from exc


def list_servers() -> list[dict]:
    return load_mcp_registry()["servers"]


def add_server(
    name: str,
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
    env: dict | None = None,
    description: str = "",
    version: str = "1.0.0",
    latest_version: str = "",
) -> dict:
    sname = str(name or "").strip()[:64]
    if not sname or not all(c.isalnum() or c in "-_." for c in sname):
        raise McpError("invalid_server_name", "MCP server name must be alphanumeric (with '-', '_', or '.').")
    if sname == "companion-system":
        raise McpError("reserved_server_name", "'companion-system' is the built-in MCP server.")
    cmd = str(command or "").strip()
    endpoint_url = str(url or "").strip()
    if not cmd and not endpoint_url:
        raise McpError("invalid_server_config", "Provide either a stdio command or an HTTP URL for the MCP server.")
    transport = "http" if endpoint_url and not cmd else "stdio"
    ver = str(version or "1.0.0").strip()
    lver = str(latest_version or ver).strip()
    entry = _enrich_server_entry({
        "name": sname,
        "transport": transport,
        "command": cmd[:1024],
        "args": [str(a)[:512] for a in (args or [])[:32]],
        "url": endpoint_url[:1024],
        "env": {str(k)[:64]: str(v)[:512] for k, v in (env or {}).items()},
        "enabled": True,
        "builtin": False,
        "curated": sname in CURATED_SERVER_MAP,
        "version": ver,
        "latestVersion": lver,
        "hasUpdate": ver != lver,
        "description": str(description or f"MCP {transport} server")[:200],
    }, CURATED_SERVER_MAP.get(sname))
    current = [s for s in list_servers() if s["name"] != sname]
    current.append(entry)
    save_mcp_registry(current)
    return entry


def remove_server(name: str) -> bool:
    sname = str(name or "").strip()
    if sname == "companion-system":
        raise McpError("reserved_server_name", "The built-in 'companion-system' server cannot be removed (use disable instead).")
    if sname in CURATED_SERVER_MAP:
        raise McpError("reserved_server_name", f"Curated server '{sname}' cannot be removed (use disable instead).")
    current = list_servers()
    filtered = [s for s in current if s["name"] != sname]
    if len(filtered) == len(current):
        return False
    save_mcp_registry(filtered)
    return True


def set_server_enabled(name: str, enabled: bool) -> dict:
    sname = str(name or "").strip()
    current = list_servers()
    target = None
    for s in current:
        if s["name"] == sname:
            s["enabled"] = bool(enabled)
            target = s
            break
    if not target:
        raise McpError("server_not_found", f"MCP server '{sname}' is not registered.")
    save_mcp_registry(current)
    return target


def check_updates(server_name: str = "") -> list[dict]:
    sname = str(server_name or "").strip()
    current = list_servers()
    found = False
    for s in current:
        if sname and s["name"] != sname:
            continue
        found = True
        c_def = CURATED_SERVER_MAP.get(s["name"])
        if c_def and c_def.get("latestVersion"):
            s["latestVersion"] = str(c_def["latestVersion"])
        else:
            s["latestVersion"] = str(s.get("latestVersion") or s.get("version") or "1.0.0")
        s["hasUpdate"] = bool(s.get("version") != s.get("latestVersion"))
    if sname and not found:
        raise McpError("server_not_found", f"MCP server '{sname}' is not registered.")
    save_mcp_registry(current)
    return current


def update_server(name: str, target_version: str = "") -> dict:
    sname = str(name or "").strip()
    if not sname:
        raise McpError("invalid_server_name", "Specify an MCP server name to update.")
    current = list_servers()
    target = None
    for s in current:
        if s["name"] == sname:
            previous_version = str(s.get("version") or "1.0.0")
            new_version = str(target_version or s.get("latestVersion") or previous_version).strip()
            s["previousVersion"] = previous_version
            s["version"] = new_version
            s["latestVersion"] = new_version
            s["hasUpdate"] = False
            s["updatedAtMs"] = int(time.time() * 1000)
            folder = _mcp_server_folder(sname)
            manifest = {
                "name": sname,
                "package": s.get("package", sname),
                "previousVersion": previous_version,
                "version": new_version,
                "updatedAtMs": s["updatedAtMs"],
            }
            try:
                (folder / "installed.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                pkg_meta = {
                    "name": s.get("package", sname),
                    "version": new_version,
                    "description": s.get("description", ""),
                }
                (folder / "package.json").write_text(json.dumps(pkg_meta, indent=2), encoding="utf-8")
            except OSError:
                pass
            target = s
            break
    if not target:
        raise McpError("server_not_found", f"MCP server '{sname}' is not registered.")
    save_mcp_registry(current)
    return target


def _stdio_rpc_exchange(server: dict, method: str, params: dict | None = None, timeout_sec: float = 6.0) -> dict:
    cmd_str = str(server.get("command") or "").strip()
    if not cmd_str:
        raise McpError("invalid_server_config", f"MCP server '{server['name']}' has no command.")
    args = [str(a) for a in (server.get("args") or [])]
    env = os.environ.copy()
    for k, v in (server.get("env") or {}).items():
        env[str(k)] = str(v)

    try:
        proc = subprocess.Popen(
            [cmd_str] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise McpError("server_spawn_failed", f"Failed to start MCP server '{server['name']}': {exc}") from exc

    def _send(msg: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    def _recv(expected_id: int, deadline: float) -> dict:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            ready, _, _ = select.select([fd], [], [], min(0.5, remaining))
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("id") == expected_id:
                if "error" in parsed and isinstance(parsed["error"], dict):
                    raise McpError("mcp_rpc_error", str(parsed["error"].get("message") or "MCP RPC error"))
                return parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
        raise McpError("mcp_timeout", f"MCP server '{server['name']}' timed out or closed on '{method}'.")

    deadline = time.monotonic() + timeout_sec
    try:
        _send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "alis-companion", "version": "1.0.0"},
            },
        })
        _recv(1, deadline)
        _send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        _send({
            "jsonrpc": "2.0",
            "id": 2,
            "method": method,
            "params": params or {},
        })
        return _recv(2, deadline)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _http_rpc_exchange(server: dict, method: str, params: dict | None = None, timeout_sec: float = 6.0) -> dict:
    url = str(server.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise McpError("invalid_server_config", f"MCP server '{server['name']}' has an invalid HTTP URL.")
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read(256 * 1024)
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
        if not isinstance(parsed, dict):
            raise ValueError()
        if "error" in parsed and isinstance(parsed["error"], dict):
            raise McpError("mcp_rpc_error", str(parsed["error"].get("message") or "MCP HTTP RPC error"))
        return parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
    except McpError:
        raise
    except Exception as exc:
        raise McpError("mcp_http_failed", f"MCP HTTP request to '{server['name']}' failed: {exc}") from exc


def list_tools(server_name: str = "") -> list[dict]:
    servers = [
        s for s in list_servers()
        if s.get("enabled") is not False and (not server_name or s["name"] == server_name)
    ]
    tools = []
    for s in servers:
        if s["name"] in CURATED_TOOLS:
            tools.extend(CURATED_TOOLS[s["name"]])
            continue
        if s.get("transport") == "builtin" or s["name"] == "companion-system":
            tools.extend(BUILTIN_TOOLS)
            continue
        try:
            res = (
                _http_rpc_exchange(s, "tools/list")
                if s.get("transport") == "http"
                else _stdio_rpc_exchange(s, "tools/list")
            )
            for t in (res.get("tools") or [])[:32]:
                if isinstance(t, dict) and t.get("name"):
                    tools.append({
                        "server": s["name"],
                        "name": str(t["name"])[:80],
                        "description": str(t.get("description") or "")[:240],
                        "inputSchema": t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object", "properties": {}},
                    })
        except McpError:
            if server_name:
                raise
    return tools


def call_tool(
    server_name: str,
    tool_name: str,
    arguments: dict | None = None,
    companion_root: Path | None = None,
    enabled_overrides: dict | None = None,
) -> dict:
    sname = str(server_name or "").strip()
    tname = str(tool_name or "").strip()
    args = arguments if isinstance(arguments, dict) else {}
    overrides = enabled_overrides if isinstance(enabled_overrides, dict) else {}
    if not tname:
        raise McpError("invalid_tool", "Specify an MCP tool name to call.")

    servers = [
        s for s in list_servers()
        if (overrides.get(s["name"], s.get("enabled") is not False) is not False)
    ]
    target = None
    if sname:
        target = next((s for s in servers if s["name"] == sname), None)
        if not target:
            raise McpError("server_not_found", f"MCP server '{sname}' is not enabled or registered.")
    else:
        # Auto-resolve server by tool name across curated and custom servers
        for s in servers:
            c_tools = CURATED_TOOLS.get(s["name"])
            if c_tools and any(t["name"] == tname for t in c_tools):
                target = s
                break
        if not target:
            for s in servers:
                if s["name"] in CURATED_TOOLS:
                    continue
                try:
                    s_tools = list_tools(s["name"])
                    if any(t["name"] == tname for t in s_tools):
                        target = s
                        break
                except McpError:
                    continue
        if not target:
            raise McpError("tool_not_found", f"Could not find an enabled MCP server providing tool '{tname}'.")

    if target["name"] in CURATED_TOOLS or target.get("transport") == "builtin":
        return _execute_curated_tool(target["name"], tname, args, companion_root=companion_root)

    rpc_params = {"name": tname, "arguments": args}
    res = (
        _http_rpc_exchange(target, "tools/call", rpc_params)
        if target.get("transport") == "http"
        else _stdio_rpc_exchange(target, "tools/call", rpc_params)
    )
    content_items = []
    for item in (res.get("content") or [])[:16]:
        if isinstance(item, dict):
            content_items.append({
                "type": str(item.get("type") or "text"),
                "text": str(item.get("text") or "")[:4000],
            })
    if not content_items and isinstance(res, dict):
        content_items.append({"type": "text", "text": json.dumps(res, ensure_ascii=False)[:4000]})
    return {
        "server": target["name"],
        "tool": tname,
        "isError": bool(res.get("isError", False)),
        "content": content_items,
    }


def list_resources(server_name: str = "") -> list[dict]:
    servers = [
        s for s in list_servers()
        if s.get("enabled") is not False and (not server_name or s["name"] == server_name)
    ]
    resources = []
    for s in servers:
        if s.get("transport") == "builtin" or s["name"] == "companion-system":
            resources.extend(BUILTIN_RESOURCES)
            continue
        if s["name"] in CURATED_SERVER_MAP:
            continue
        try:
            res = (
                _http_rpc_exchange(s, "resources/list")
                if s.get("transport") == "http"
                else _stdio_rpc_exchange(s, "resources/list")
            )
            for r in (res.get("resources") or [])[:32]:
                if isinstance(r, dict) and r.get("uri"):
                    resources.append({
                        "server": s["name"],
                        "uri": str(r["uri"])[:512],
                        "name": str(r.get("name") or r["uri"])[:120],
                        "mimeType": str(r.get("mimeType") or "text/plain")[:80],
                        "description": str(r.get("description") or "")[:200],
                    })
        except McpError:
            if server_name:
                raise
    return resources


def read_resource(server_name: str, uri: str) -> dict:
    sname = str(server_name or "companion-system").strip()
    u = str(uri or "").strip()
    if not u:
        raise McpError("invalid_resource", "Specify a resource URI to read.")
    if u.startswith("companion://") or sname == "companion-system":
        return _read_builtin_resource(u)
    servers = [s for s in list_servers() if s.get("enabled") is not False and s["name"] == sname]
    if not servers:
        raise McpError("server_not_found", f"MCP server '{sname}' is not enabled or registered.")
    target = servers[0]
    res = (
        _http_rpc_exchange(target, "resources/read", {"uri": u})
        if target.get("transport") == "http"
        else _stdio_rpc_exchange(target, "resources/read", {"uri": u})
    )
    contents = res.get("contents") or []
    first = contents[0] if contents and isinstance(contents[0], dict) else {}
    return {
        "server": target["name"],
        "uri": u,
        "mimeType": str(first.get("mimeType") or "text/plain"),
        "text": str(first.get("text") or "")[:4000],
    }


def list_prompts(server_name: str = "") -> list[dict]:
    servers = [
        s for s in list_servers()
        if s.get("enabled") is not False and (not server_name or s["name"] == server_name)
    ]
    prompts = []
    for s in servers:
        if s.get("transport") == "builtin" or s["name"] == "companion-system":
            prompts.extend(BUILTIN_PROMPTS)
            continue
        if s["name"] in CURATED_SERVER_MAP:
            continue
        try:
            res = (
                _http_rpc_exchange(s, "prompts/list")
                if s.get("transport") == "http"
                else _stdio_rpc_exchange(s, "prompts/list")
            )
            for p in (res.get("prompts") or [])[:32]:
                if isinstance(p, dict) and p.get("name"):
                    prompts.append({
                        "server": s["name"],
                        "name": str(p["name"])[:80],
                        "description": str(p.get("description") or "")[:200],
                    })
        except McpError:
            if server_name:
                raise
    return prompts


def execute_mcp_action(options: dict, companion_root: Path | None = None) -> dict:
    op = str(options.get("op") or "list_servers").strip()
    if op == "list_servers":
        return {"op": op, "servers": list_servers()}
    if op == "add_server":
        added = add_server(
            name=options.get("name", ""),
            command=options.get("command", ""),
            args=options.get("args") if isinstance(options.get("args"), list) else [],
            url=options.get("url", ""),
            env=options.get("env") if isinstance(options.get("env"), dict) else {},
            description=options.get("description", ""),
            version=str(options.get("version") or "1.0.0"),
            latest_version=str(options.get("latestVersion") or ""),
        )
        return {"op": op, "server": added, "servers": list_servers()}
    if op == "remove_server":
        removed = remove_server(options.get("name", ""))
        return {"op": op, "removed": removed, "servers": list_servers()}
    if op in ("enable_server", "disable_server"):
        updated = set_server_enabled(options.get("name", ""), enabled=(op == "enable_server"))
        return {"op": op, "server": updated, "servers": list_servers()}
    if op == "check_updates":
        servers = check_updates(str(options.get("name") or options.get("server") or ""))
        updates_count = sum(1 for s in servers if s.get("hasUpdate"))
        return {"op": op, "updatesAvailable": updates_count, "servers": servers}
    if op == "update_server":
        updated = update_server(
            name=str(options.get("name") or options.get("server") or ""),
            target_version=str(options.get("targetVersion") or ""),
        )
        return {"op": op, "server": updated, "updated": updated, "servers": list_servers()}
    if op == "list_tools":
        return {"op": op, "tools": list_tools(str(options.get("server") or ""))}
    if op == "call_tool":
        res = call_tool(
            server_name=str(options.get("server") or ""),
            tool_name=str(options.get("tool") or ""),
            arguments=options.get("arguments") if isinstance(options.get("arguments"), dict) else {},
            companion_root=companion_root,
        )
        return {"op": op, "result": res}
    if op == "list_resources":
        return {"op": op, "resources": list_resources(str(options.get("server") or ""))}
    if op == "read_resource":
        res = read_resource(str(options.get("server") or ""), str(options.get("uri") or ""))
        return {"op": op, "resource": res}
    if op == "list_prompts":
        return {"op": op, "prompts": list_prompts(str(options.get("server") or ""))}
    raise McpError("invalid_mcp_op", f"Unsupported MCP operation '{op}'.")



def _run_stdio_server() -> None:
    """Reference JSON-RPC 2.0 stdio MCP server for external hosts and self-tests."""
    comp_root = Path(__file__).resolve().parents[2]
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if not isinstance(req, dict):
            continue
        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") if isinstance(req.get("params"), dict) else {}
        if req_id is None:
            continue
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    "serverInfo": {"name": "alis-companion-mcp", "version": "1.0.0"},
                }
            elif method == "tools/list":
                result = {"tools": BUILTIN_TOOLS}
            elif method == "tools/call":
                t_res = _execute_builtin_tool(
                    str(params.get("name") or ""),
                    params.get("arguments") if isinstance(params.get("arguments"), dict) else {},
                    companion_root=comp_root,
                )
                result = {"content": t_res["content"], "isError": t_res["isError"]}
            elif method == "resources/list":
                result = {"resources": BUILTIN_RESOURCES}
            elif method == "resources/read":
                r_res = _read_builtin_resource(str(params.get("uri") or ""))
                result = {"contents": [r_res]}
            elif method == "prompts/list":
                result = {"prompts": BUILTIN_PROMPTS}
            else:
                raise McpError("method_not_found", f"Unsupported MCP method '{method}'")
            resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        except McpError as exc:
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": exc.message}}
        sys.stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__" and "--stdio-server" in sys.argv:
    _run_stdio_server()
