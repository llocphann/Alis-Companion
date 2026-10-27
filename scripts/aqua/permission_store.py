"""Scoped, versioned, inspectable and revocable permissions for Alis-Companion Agent.

Permissions model:
- Inside User Home ($HOME): full read/write/execute access by default across all
  files, directories, dot-directories, and symlinks resolving within $HOME
  (destructive deletes still require explicit confirmation).
- System paths (outside $HOME, e.g. /etc, /usr, /var, /opt, /root, /mnt):
  always require explicit user permission AND sudo password verification.
- Untrusted file/screen/RAG/model text can never grant or elevate permissions.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


SCHEMA_VERSION = 2
TOOL_CATEGORIES = frozenset((
    "file_read",
    "file_write",
    "file_delete",
    "shell_run",
    "screen_capture",
    "gui_action",
    "code_lsp",
    "network",
    "memory_write",
    "pocket_write",
    "rule_manage",
    "preference_edit",
))
TRUSTED_GRANT_SOURCES = frozenset(("user_prompt", "settings_ui", "legacy_migration"))


class AgentPermissionError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _now_ms(now_ms=None):
    if isinstance(now_ms, int) and now_ms > 0:
        return now_ms
    return int(time.time() * 1000)


def _state_file():
    custom = os.environ.get("ALIS_AQUA_PERMISSIONS_PATH", "").strip()
    if custom:
        path = Path(custom)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".local/state")
    folder = base / "alis-companion"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "agent_permissions.json"


def _default_state(now_ms=None):
    ts = _now_ms(now_ms)
    home = str(Path.home().resolve())
    desktop = str((Path.home() / "Desktop").resolve())
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAtMs": ts,
        "migratedLegacy": True,
        "homeFullAccess": True,
        "networkAllowed": False,
        "networkExpiresMs": 0,
        "directories": [
            {
                "id": "perm_dir_home_full",
                "path": home,
                "levels": ["read", "write", "execute"],
                "grantedAtMs": ts,
                "expiresAtMs": 0,
                "source": "legacy_migration",
            },
            {
                "id": "perm_dir_desktop_default",
                "path": desktop,
                "levels": ["read", "write", "execute"],
                "grantedAtMs": ts,
                "expiresAtMs": 0,
                "source": "legacy_migration",
            },
        ],
        "revokedDirectories": [],
        "apps": [],
        "tools": {
            "file_read": {"allowed": True, "expiresAtMs": 0},
            "file_write": {"allowed": True, "expiresAtMs": 0},
            "file_delete": {"allowed": True, "requiresConfirm": True, "expiresAtMs": 0},
            "shell_run": {"allowed": True, "expiresAtMs": 0},
            "screen_capture": {"allowed": True, "expiresAtMs": 0},
            "gui_action": {"allowed": True, "requiresConfirm": False, "expiresAtMs": 0},
            "code_lsp": {"allowed": True, "expiresAtMs": 0},
            "network": {"allowed": False, "expiresAtMs": 0},
            "memory_write": {"allowed": True, "requiresConfirm": True, "expiresAtMs": 0},
            "pocket_write": {"allowed": True, "expiresAtMs": 0},
            "rule_manage": {"allowed": True, "expiresAtMs": 0},
            "preference_edit": {"allowed": True, "expiresAtMs": 0},
        },
        "pendingConfirmations": [],
    }


def _normalize_loaded_state(raw, now_ms=None):
    ts = _now_ms(now_ms)
    base = _default_state(ts)
    if not isinstance(raw, dict):
        return base
    home = str(Path.home().resolve())
    merged = dict(raw)
    old_schema = int(raw.get("schemaVersion", 1) or 1)
    merged["schemaVersion"] = SCHEMA_VERSION
    if not isinstance(merged.get("updatedAtMs"), int):
        merged["updatedAtMs"] = ts
    if not isinstance(merged.get("homeFullAccess"), bool):
        merged["homeFullAccess"] = True
    if not isinstance(merged.get("networkAllowed"), bool):
        merged["networkAllowed"] = bool(raw.get("network", False))
    if not isinstance(merged.get("networkExpiresMs"), int):
        merged["networkExpiresMs"] = 0

    legacy_full = raw.get("fullAccess", raw.get("agentToolsEnabled", None))
    if isinstance(legacy_full, bool) and not raw.get("migratedLegacy"):
        merged["migratedLegacy"] = True
        if not legacy_full:
            for k in ("file_write", "shell_run", "gui_action"):
                if k in base["tools"]:
                    base["tools"][k]["allowed"] = False

    revoked_dirs = []
    if isinstance(raw.get("revokedDirectories"), list):
        for r in raw["revokedDirectories"]:
            if isinstance(r, str) and r.strip():
                revoked_dirs.append(r.strip())
    merged["revokedDirectories"] = revoked_dirs

    dirs = []
    seen_ids = set()
    has_home_entry = False
    raw_dirs = raw.get("directories")
    if isinstance(raw_dirs, list):
        for item in raw_dirs:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            p = item["path"].strip()
            if not p:
                continue
            pid = str(item.get("id") or ("perm_dir_" + hashlib.sha256(p.encode("utf-8")).hexdigest()[:12]))
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            levels = [lvl for lvl in (item.get("levels") or ["read"]) if lvl in ("read", "write", "execute")]
            # Upgrade schema v1 read-only home entry to full read/write/execute
            if (pid in ("perm_dir_home_read", "perm_dir_home_full") or p == home) and old_schema < 2:
                levels = ["read", "write", "execute"]
                pid = "perm_dir_home_full"
            if p == home or pid == "perm_dir_home_full":
                has_home_entry = True
            dirs.append({
                **{k: v for k, v in item.items() if k not in ("id", "path", "levels", "grantedAtMs", "expiresAtMs", "source")},
                "id": pid,
                "path": p,
                "levels": levels or ["read", "write", "execute"],
                "grantedAtMs": int(item.get("grantedAtMs", ts)),
                "expiresAtMs": int(item.get("expiresAtMs", 0)),
                "source": str(item.get("source", "legacy_migration")),
            })
        if not has_home_entry and merged.get("homeFullAccess", True) and home not in revoked_dirs:
            dirs.insert(0, {
                "id": "perm_dir_home_full",
                "path": home,
                "levels": ["read", "write", "execute"],
                "grantedAtMs": ts,
                "expiresAtMs": 0,
                "source": "legacy_migration",
            })
    else:
        dirs = base["directories"]
    merged["directories"] = dirs

    apps = []
    if isinstance(raw.get("apps"), list):
        for item in raw["apps"]:
            if isinstance(item, dict) and isinstance(item.get("appId"), str) and item["appId"].strip():
                apps.append({
                    "id": str(item.get("id") or ("perm_app_" + hashlib.sha256(item["appId"].encode("utf-8")).hexdigest()[:12])),
                    "appId": item["appId"].strip(),
                    "levels": [lvl for lvl in (item.get("levels") or ["inspect"]) if lvl in ("inspect", "interact")],
                    "grantedAtMs": int(item.get("grantedAtMs", ts)),
                    "expiresAtMs": int(item.get("expiresAtMs", 0)),
                    "source": str(item.get("source", "user_prompt")),
                })
    merged["apps"] = apps

    tools = dict(base["tools"])
    if isinstance(raw.get("tools"), dict):
        for k, v in raw["tools"].items():
            if k in tools and isinstance(v, dict):
                tools[k] = {
                    **tools[k],
                    "allowed": bool(v.get("allowed", tools[k]["allowed"])),
                    "requiresConfirm": bool(v.get("requiresConfirm", tools[k].get("requiresConfirm", False))),
                    "expiresAtMs": int(v.get("expiresAtMs", 0)),
                }
    merged["tools"] = tools

    pending = []
    if isinstance(raw.get("pendingConfirmations"), list):
        for item in raw["pendingConfirmations"]:
            if isinstance(item, dict) and isinstance(item.get("confirmId"), str):
                pending.append(item)
    merged["pendingConfirmations"] = pending[-32:]
    return merged


def load_state(now_ms=None):
    ts = _now_ms(now_ms)
    path = _state_file()
    if not path.is_file():
        return _default_state(ts)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_state(ts)
    return _normalize_loaded_state(raw, ts)


def save_state(state):
    path = _state_file()
    payload = (json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".agent_perm_", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def is_inside_user_home(target_path):
    if not target_path:
        return True
    raw = str(target_path).strip()
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        return False
    try:
        home = Path.home().resolve()
        expanded = Path(raw).expanduser()
        if not expanded.is_absolute():
            expanded = home / expanded
        resolved = expanded.resolve(strict=False)
        return resolved == home or resolved.is_relative_to(home)
    except OSError:
        return False


def is_system_path(target_path):
    if not target_path:
        return False
    return not is_inside_user_home(target_path)


def is_forbidden_path(target_path):
    if not target_path:
        return False
    raw = str(target_path).strip()
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        return True
    return False


def _active_entry(entry, ts):
    exp = int(entry.get("expiresAtMs", 0))
    return exp == 0 or exp > ts


def check_permission(
    category,
    target_path="",
    app_id="",
    level="read",
    explicit_prompt_target=False,
    source="user_prompt",
    now_ms=None,
):
    ts = _now_ms(now_ms)
    if source not in TRUSTED_GRANT_SOURCES and source != "agent_step":
        return {
            "allowed": False,
            "requiresConfirmation": False,
            "requiresSudo": False,
            "reason": "untrusted_source_blocked",
            "category": str(category),
            "targetPath": str(target_path),
        }
    if category not in TOOL_CATEGORIES:
        return {
            "allowed": False,
            "requiresConfirmation": False,
            "requiresSudo": False,
            "reason": "unknown_tool_category",
            "category": str(category),
            "targetPath": str(target_path),
        }
    if target_path and is_forbidden_path(target_path):
        return {
            "allowed": False,
            "requiresConfirmation": False,
            "requiresSudo": False,
            "reason": "invalid_target_path",
            "category": str(category),
            "targetPath": str(target_path),
        }

    state = load_state(ts)
    tool_cfg = state.get("tools", {}).get(category, {"allowed": False, "expiresAtMs": 0})
    if not _active_entry(tool_cfg, ts) or not tool_cfg.get("allowed", False):
        return {
            "allowed": False,
            "requiresConfirmation": True,
            "requiresSudo": False,
            "reason": "tool_category_disabled",
            "category": str(category),
            "targetPath": str(target_path),
        }

    if category == "network":
        net_ok = bool(state.get("networkAllowed", False)) and (
            int(state.get("networkExpiresMs", 0)) == 0 or int(state.get("networkExpiresMs", 0)) > ts
        )
        return {
            "allowed": net_ok,
            "requiresConfirmation": not net_ok,
            "requiresSudo": False,
            "reason": "ok" if net_ok else "external_network_requires_confirmation",
            "category": "network",
            "targetPath": str(target_path),
        }

    if target_path:
        try:
            home = Path.home().resolve()
            expanded = Path(str(target_path).strip()).expanduser()
            if not expanded.is_absolute():
                expanded = home / expanded
            resolved_path = expanded.resolve(strict=False)
            resolved = str(resolved_path)
        except OSError:
            return {
                "allowed": False,
                "requiresConfirmation": False,
                "requiresSudo": False,
                "reason": "invalid_target_path",
                "category": str(category),
                "targetPath": str(target_path),
            }

        inside_home = resolved_path == home or resolved_path.is_relative_to(home)

        # System path outside $HOME: always requires explicit user permission AND sudo password
        if not inside_home:
            return {
                "allowed": False,
                "requiresConfirmation": True,
                "requiresSudo": True,
                "reason": "system_path_requires_sudo",
                "category": str(category),
                "targetPath": resolved,
            }

        if category == "file_delete":
            return {
                "allowed": False,
                "requiresConfirmation": True,
                "requiresSudo": False,
                "reason": "destructive_delete_requires_confirmation",
                "category": "file_delete",
                "targetPath": resolved,
            }

        # Check if user explicitly revoked this directory inside $HOME
        for rev in state.get("revokedDirectories", []):
            rev_resolved = str(Path(rev).expanduser().resolve(strict=False))
            if resolved == rev_resolved or resolved.startswith(rev_resolved + "/"):
                return {
                    "allowed": False,
                    "requiresConfirmation": True,
                    "requiresSudo": False,
                    "reason": "outside_granted_directory_scope",
                    "category": str(category),
                    "targetPath": resolved,
                }

        needed_level = "execute" if category == "shell_run" else ("write" if category in ("file_write", "file_delete") else "read")
        for d in state.get("directories", []):
            if not _active_entry(d, ts):
                continue
            dpath = str(Path(d["path"]).expanduser().resolve(strict=False))
            if (resolved == dpath or resolved.startswith(dpath + "/")) and needed_level in (d.get("levels") or []):
                return {
                    "allowed": True,
                    "requiresConfirmation": bool(tool_cfg.get("requiresConfirm", False)),
                    "requiresSudo": False,
                    "reason": "granted_directory_scope",
                    "category": category,
                    "targetPath": resolved,
                }

        # Default: full access to everything inside current User Home ($HOME)
        if state.get("homeFullAccess", True):
            return {
                "allowed": True,
                "requiresConfirmation": False,
                "requiresSudo": False,
                "reason": "user_home_full_access",
                "category": category,
                "targetPath": resolved,
            }

        return {
            "allowed": False,
            "requiresConfirmation": True,
            "requiresSudo": False,
            "reason": "outside_granted_directory_scope",
            "category": category,
            "targetPath": resolved,
        }

    if category == "file_delete":
        return {
            "allowed": False,
            "requiresConfirmation": True,
            "requiresSudo": False,
            "reason": "destructive_delete_requires_confirmation",
            "category": "file_delete",
            "targetPath": str(target_path),
        }

    if app_id and category == "gui_action":
        for app in state.get("apps", []):
            if _active_entry(app, ts) and app.get("appId") in (app_id, "*") and "interact" in (app.get("levels") or []):
                return {
                    "allowed": True,
                    "requiresConfirmation": False,
                    "requiresSudo": False,
                    "reason": "granted_app_scope",
                    "category": category,
                    "targetPath": app_id,
                }

    return {
        "allowed": True,
        "requiresConfirmation": bool(tool_cfg.get("requiresConfirm", False)),
        "requiresSudo": False,
        "reason": "ok",
        "category": category,
        "targetPath": str(target_path),
    }


def grant_permission(scope_type, target="", levels=None, ttl_ms=0, source="user_prompt", now_ms=None):
    ts = _now_ms(now_ms)
    if source not in TRUSTED_GRANT_SOURCES:
        raise AgentPermissionError("untrusted_grant_source", "Untrusted content cannot grant or modify permissions.")
    state = load_state(ts)
    expires = (ts + int(ttl_ms)) if isinstance(ttl_ms, int) and ttl_ms > 0 else 0

    if scope_type == "directory":
        if not target or is_forbidden_path(target):
            raise AgentPermissionError("forbidden_path", "Cannot grant permission to an invalid path.")
        resolved = str(Path(target).expanduser().resolve(strict=False))
        lvls = [x for x in (levels or ["read", "write", "execute"]) if x in ("read", "write", "execute")] or ["read", "write", "execute"]
        pid = "perm_dir_" + hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
        state["revokedDirectories"] = [
            r for r in state.get("revokedDirectories", [])
            if str(Path(r).expanduser().resolve(strict=False)) != resolved
        ]
        updated = False
        for item in state["directories"]:
            if item.get("id") == pid or item.get("path") == resolved:
                item["path"] = resolved
                item["levels"] = sorted(set(item.get("levels", []) + lvls))
                item["grantedAtMs"] = ts
                item["expiresAtMs"] = expires
                item["source"] = source
                updated = True
                break
        if not updated:
            state["directories"].append({
                "id": pid,
                "path": resolved,
                "levels": lvls,
                "grantedAtMs": ts,
                "expiresAtMs": expires,
                "source": source,
            })
    elif scope_type == "app":
        app_name = str(target).strip()
        if not app_name:
            raise AgentPermissionError("invalid_app", "Application identifier is required.")
        lvls = [x for x in (levels or ["inspect", "interact"]) if x in ("inspect", "interact")] or ["inspect"]
        aid = "perm_app_" + hashlib.sha256(app_name.encode("utf-8")).hexdigest()[:12]
        state["apps"] = [a for a in state["apps"] if a.get("appId") != app_name]
        state["apps"].append({
            "id": aid,
            "appId": app_name,
            "levels": lvls,
            "grantedAtMs": ts,
            "expiresAtMs": expires,
            "source": source,
        })
    elif scope_type == "tool":
        cat = str(target).strip()
        if cat not in TOOL_CATEGORIES:
            raise AgentPermissionError("invalid_tool", "Unknown tool category.")
        state["tools"][cat] = {
            **state["tools"].get(cat, {}),
            "allowed": True,
            "expiresAtMs": expires,
        }
        if cat == "network":
            state["networkAllowed"] = True
            state["networkExpiresMs"] = expires
    elif scope_type == "network":
        state["networkAllowed"] = True
        state["networkExpiresMs"] = expires
        state["tools"]["network"] = {"allowed": True, "expiresAtMs": expires}
    else:
        raise AgentPermissionError("invalid_scope_type", "Unsupported permission scope type.")

    state["updatedAtMs"] = ts
    save_state(state)
    return inspect_permissions({"nowMs": ts})


def revoke_permission(scope_type="", target="", revoke_all=False, now_ms=None):
    ts = _now_ms(now_ms)
    state = load_state(ts)
    if revoke_all:
        state["directories"] = []
        state["apps"] = []
        state["networkAllowed"] = False
        state["networkExpiresMs"] = 0
        for k in state["tools"]:
            if k not in ("file_read",):
                state["tools"][k]["allowed"] = False
        state["pendingConfirmations"] = []
    elif scope_type == "directory" and target:
        resolved = str(Path(target).expanduser().resolve(strict=False))
        state["directories"] = [
            d for d in state["directories"]
            if d.get("path") not in (target, resolved) and d.get("id") != target
        ]
        if resolved not in state.get("revokedDirectories", []):
            state.setdefault("revokedDirectories", []).append(resolved)
    elif scope_type == "app" and target:
        state["apps"] = [a for a in state["apps"] if a.get("appId") != target and a.get("id") != target]
    elif scope_type == "tool" and target in state["tools"]:
        state["tools"][target]["allowed"] = False
        if target == "network":
            state["networkAllowed"] = False
    elif scope_type == "network":
        state["networkAllowed"] = False
        state["networkExpiresMs"] = 0
        state["tools"]["network"]["allowed"] = False
    state["updatedAtMs"] = ts
    save_state(state)
    return inspect_permissions({"nowMs": ts})


def create_pending_confirmation(action_kind, target_path, summary, payload=None, now_ms=None):
    ts = _now_ms(now_ms)
    state = load_state(ts)
    seed = f"{action_kind}:{target_path}:{ts}:{os.urandom(4).hex()}"
    cid = "conf_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    entry = {
        "confirmId": cid,
        "actionKind": str(action_kind),
        "targetPath": str(target_path),
        "summary": str(summary)[:240],
        "payload": payload if isinstance(payload, dict) else {},
        "createdAtMs": ts,
        "expiresAtMs": ts + 180_000,
        "status": "pending",
    }
    state["pendingConfirmations"] = [
        c for c in state.get("pendingConfirmations", [])
        if c.get("status") == "pending" and int(c.get("expiresAtMs", 0)) > ts
    ][-15:] + [entry]
    state["updatedAtMs"] = ts
    save_state(state)
    return entry


def resolve_pending_confirmation(confirm_id="", decision="confirm", source="user_prompt", now_ms=None):
    ts = _now_ms(now_ms)
    if source not in TRUSTED_GRANT_SOURCES:
        raise AgentPermissionError("untrusted_confirmation", "Untrusted data cannot confirm pending actions.")
    state = load_state(ts)
    pending = state.get("pendingConfirmations", [])
    matched = None
    for item in reversed(pending):
        if item.get("status") != "pending":
            continue
        if confirm_id and item.get("confirmId") != confirm_id:
            continue
        if int(item.get("expiresAtMs", 0)) <= ts:
            item["status"] = "expired"
            continue
        matched = item
        break
    if not matched:
        save_state(state)
        return None
    matched["status"] = "confirmed" if decision == "confirm" else "cancelled"
    matched["resolvedAtMs"] = ts
    state["updatedAtMs"] = ts
    save_state(state)
    return matched


def inspect_permissions(options=None):
    opts = options or {}
    ts = _now_ms(opts.get("nowMs"))
    state = load_state(ts)
    active_dirs = [
        {
            "id": str(d["id"]),
            "path": str(d["path"]),
            "levels": list(d.get("levels", ["read", "write", "execute"])),
            "expiresAtMs": int(d.get("expiresAtMs", 0)),
        }
        for d in state.get("directories", [])
        if _active_entry(d, ts)
    ]
    active_apps = [
        {
            "id": str(a["id"]),
            "id_str": str(a["id"]),
            "appId": str(a["appId"]),
            "levels": list(a.get("levels", ["inspect"])),
            "expiresAtMs": int(a.get("expiresAtMs", 0)),
        }
        for a in state.get("apps", [])
        if _active_entry(a, ts)
    ]
    active_tools = {
        k: bool(v.get("allowed", False)) and _active_entry(v, ts)
        for k, v in state.get("tools", {}).items()
    }
    pending = [
        c for c in state.get("pendingConfirmations", [])
        if c.get("status") == "pending" and int(c.get("expiresAtMs", 0)) > ts
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "homeFullAccess": bool(state.get("homeFullAccess", True)),
        "networkAllowed": bool(state.get("networkAllowed", False)) and (
            int(state.get("networkExpiresMs", 0)) == 0 or int(state.get("networkExpiresMs", 0)) > ts
        ),
        "directories": active_dirs[:32],
        "revokedDirectories": list(state.get("revokedDirectories", []))[:32],
        "apps": active_apps[:32],
        "tools": active_tools,
        "pendingCount": len(pending),
        "latestPending": pending[-1] if pending else None,
    }
