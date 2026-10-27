"""Conversational background rules store and evaluator for Alis-Companion Agent.

Rules can be created, edited, listed, paused, or removed via natural language.
Supported triggers:
  - planner_event: bound to real Weekly Planner event ID (e.g. evt_*)
  - focus_end: triggered when a Focus / Pomodoro session completes
  - schedule_time: triggered at a scheduled HH:MM local time

Safety invariants:
  - Single active run lock; never interrupts an active interactive run.
  - Deduplication by trigger key + date; no replay storm after restart.
  - Background rules never hijack mouse/keyboard (gui_action is forbidden).
  - Respects screen lock, Focus mode (except focus_end right on completion),
    fullscreen apps, quiet hours, and global stop.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


SCHEMA_VERSION = 1
TRIGGER_TYPES = frozenset(("planner_event", "focus_end", "schedule_time"))
DEFAULT_RULE_BUDGET = {
    "maxTurns": 10,
    "maxDurationSec": 180,
    "maxRunsPerDay": 2,
}


class RuleStoreError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _now_ms(now_ms=None):
    if isinstance(now_ms, int) and now_ms > 0:
        return now_ms
    return int(time.time() * 1000)


def _today_str(now_ms=None):
    ts = _now_ms(now_ms)
    return datetime.fromtimestamp(ts / 1000.0).strftime("%Y-%m-%d")


def _state_file():
    custom = os.environ.get("ALIS_AQUA_RULES_PATH", "").strip()
    if custom:
        p = Path(custom)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".local/state")
    folder = base / "alis-companion"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "agent_rules.json"


def _default_state(now_ms=None):
    ts = _now_ms(now_ms)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAtMs": ts,
        "globalStop": False,
        "lastStartupMs": ts,
        "rules": [],
        "processedKeys": [],
    }


def _normalize_rule(item, ts):
    if not isinstance(item, dict):
        return None
    ttype = str(item.get("triggerType") or "").strip()
    if ttype not in TRIGGER_TYPES:
        return None
    task = str(item.get("taskPrompt") or "").strip()[:400]
    if not task:
        return None
    rid = str(item.get("ruleId") or "").strip()
    if not rid.startswith("rule_"):
        rid = "rule_" + hashlib.sha256(f"{ttype}:{task}:{ts}".encode("utf-8")).hexdigest()[:12]
    budget_raw = item.get("budget") if isinstance(item.get("budget"), dict) else {}
    budget = {
        "maxTurns": max(1, min(10, int(budget_raw.get("maxTurns", DEFAULT_RULE_BUDGET["maxTurns"])))),
        "maxDurationSec": max(10, min(180, int(budget_raw.get("maxDurationSec", DEFAULT_RULE_BUDGET["maxDurationSec"])))),
        "maxRunsPerDay": max(1, min(10, int(budget_raw.get("maxRunsPerDay", DEFAULT_RULE_BUDGET["maxRunsPerDay"])))),
    }
    spec = item.get("triggerSpec") if isinstance(item.get("triggerSpec"), dict) else {}
    return {
        **{k: v for k, v in item.items() if k not in (
            "ruleId", "title", "triggerType", "triggerSpec", "taskPrompt",
            "allowedScopes", "budget", "enabled", "createdAtMs", "lastRunDate", "runsToday"
        )},
        "ruleId": rid,
        "title": str(item.get("title") or task[:60]).strip()[:120],
        "triggerType": ttype,
        "triggerSpec": {
            "eventId": str(spec.get("eventId") or "").strip()[:120],
            "eventTitle": str(spec.get("eventTitle") or "").strip()[:120],
            "timeHm": str(spec.get("timeHm") or "").strip()[:10],
        },
        "taskPrompt": task,
        "allowedScopes": [
            s for s in (item.get("allowedScopes") or ["file_read", "memory_write", "pocket_write"])
            if s != "gui_action"
        ],
        "allowGuiTakeover": False,
        "budget": budget,
        "enabled": bool(item.get("enabled", True)),
        "createdAtMs": int(item.get("createdAtMs", ts)),
        "lastRunDate": str(item.get("lastRunDate") or ""),
        "runsToday": int(item.get("runsToday", 0)),
    }


def load_rules_state(now_ms=None):
    ts = _now_ms(now_ms)
    path = _state_file()
    if not path.is_file():
        return _default_state(ts)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_state(ts)
    if not isinstance(raw, dict):
        return _default_state(ts)
    rules = []
    seen = set()
    for r in raw.get("rules", []):
        norm = _normalize_rule(r, ts)
        if norm and norm["ruleId"] not in seen:
            seen.add(norm["ruleId"])
            rules.append(norm)
    return {
        **raw,
        "schemaVersion": SCHEMA_VERSION,
        "updatedAtMs": int(raw.get("updatedAtMs", ts)),
        "globalStop": bool(raw.get("globalStop", False)),
        "lastStartupMs": int(raw.get("lastStartupMs", ts)),
        "rules": rules[:64],
        "processedKeys": [str(x) for x in raw.get("processedKeys", []) if isinstance(x, str)][-128:],
    }


def save_rules_state(state):
    path = _state_file()
    payload = (json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".agent_rules_", dir=str(path.parent))
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


def create_or_update_rule(
    trigger_type,
    task_prompt,
    title="",
    event_id="",
    event_title="",
    time_hm="",
    planner_events=None,
    max_runs_per_day=2,
    rule_id="",
    now_ms=None,
):
    ts = _now_ms(now_ms)
    ttype = str(trigger_type or "").strip()
    if ttype not in TRIGGER_TYPES:
        raise RuleStoreError("invalid_trigger_type", "Trigger type must be planner_event, focus_end, or schedule_time.")
    task = str(task_prompt or "").strip()
    if not task:
        raise RuleStoreError("empty_task", "Provide a task for the rule to perform.")

    resolved_event_id = str(event_id or "").strip()
    resolved_event_title = str(event_title or "").strip()
    if ttype == "planner_event":
        # Resolve against real Weekly Planner events if provided
        if isinstance(planner_events, list) and planner_events:
            if resolved_event_id:
                match = next((e for e in planner_events if isinstance(e, dict) and str(e.get("id", "")) == resolved_event_id), None)
                if match:
                    resolved_event_title = str(match.get("title") or resolved_event_title)
            elif resolved_event_title:
                match = next(
                    (
                        e for e in planner_events
                        if isinstance(e, dict) and resolved_event_title.lower() in str(e.get("title", "")).lower()
                    ),
                    None,
                )
                if match and match.get("id"):
                    resolved_event_id = str(match["id"])
                    resolved_event_title = str(match.get("title") or resolved_event_title)
            elif len(planner_events) == 1 and isinstance(planner_events[0], dict) and planner_events[0].get("id"):
                resolved_event_id = str(planner_events[0]["id"])
                resolved_event_title = str(planner_events[0].get("title") or "")
        if not resolved_event_id:
            raise RuleStoreError(
                "planner_event_id_required",
                "Weekly Planner rule requires a real Planner event ID (e.g. evt_*).",
            )

    if ttype == "schedule_time":
        hm = str(time_hm or "").strip()
        if not hm or ":" not in hm:
            raise RuleStoreError("invalid_schedule_time", "Scheduled rule requires a valid HH:MM time.")
        parts = hm.split(":", 1)
        if not (parts[0].isdigit() and parts[1].isdigit() and 0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
            raise RuleStoreError("invalid_schedule_time", "Scheduled rule requires a valid HH:MM time.")
        time_hm = f"{int(parts[0]):02d}:{int(parts[1]):02d}"

    state = load_rules_state(ts)
    seed = rule_id or f"{ttype}:{resolved_event_id}:{time_hm}:{task}"
    rid = rule_id if rule_id.startswith("rule_") else ("rule_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12])

    new_rule = _normalize_rule({
        "ruleId": rid,
        "title": title or task[:60],
        "triggerType": ttype,
        "triggerSpec": {
            "eventId": resolved_event_id,
            "eventTitle": resolved_event_title,
            "timeHm": time_hm,
        },
        "taskPrompt": task,
        "budget": {
            **DEFAULT_RULE_BUDGET,
            "maxRunsPerDay": max(1, min(10, int(max_runs_per_day or 2))),
        },
        "enabled": True,
        "createdAtMs": ts,
    }, ts)

    replaced = False
    for idx, existing in enumerate(state["rules"]):
        if existing["ruleId"] == rid:
            new_rule["runsToday"] = existing.get("runsToday", 0)
            new_rule["lastRunDate"] = existing.get("lastRunDate", "")
            state["rules"][idx] = new_rule
            replaced = True
            break
    if not replaced:
        state["rules"].append(new_rule)

    state["updatedAtMs"] = ts
    save_rules_state(state)
    return new_rule


def set_rule_enabled(rule_id="", enabled=False, disable_all=False, global_stop=None, now_ms=None):
    ts = _now_ms(now_ms)
    state = load_rules_state(ts)
    if isinstance(global_stop, bool):
        state["globalStop"] = global_stop
    changed = []
    for r in state["rules"]:
        if disable_all or (rule_id and (r["ruleId"] == rule_id or rule_id.lower() in r["title"].lower())):
            r["enabled"] = bool(enabled)
            changed.append(r["ruleId"])
    state["updatedAtMs"] = ts
    save_rules_state(state)
    return {
        "globalStop": state["globalStop"],
        "changedRuleIds": changed,
        "rules": state["rules"],
    }


def delete_rule(rule_id, now_ms=None):
    ts = _now_ms(now_ms)
    state = load_rules_state(ts)
    before = len(state["rules"])
    state["rules"] = [
        r for r in state["rules"]
        if r["ruleId"] != rule_id and rule_id.lower() not in r["title"].lower()
    ]
    state["updatedAtMs"] = ts
    save_rules_state(state)
    return {"deleted": len(state["rules"]) < before, "rules": state["rules"]}


def mark_startup_grace(now_ms=None, current_keys=None):
    """Record current trigger keys on startup so missed historical events do not replay in a storm."""
    ts = _now_ms(now_ms)
    state = load_rules_state(ts)
    state["lastStartupMs"] = ts
    if isinstance(current_keys, list):
        merged = list(state.get("processedKeys", []))
        for k in current_keys:
            if isinstance(k, str) and k and k not in merged:
                merged.append(k)
        state["processedKeys"] = merged[-128:]
    state["updatedAtMs"] = ts
    save_rules_state(state)
    return state


def evaluate_triggers(
    event_type,
    event_id="",
    time_hm="",
    trigger_key="",
    active_run_busy=False,
    screen_locked=False,
    focus_active=False,
    fullscreen_active=False,
    quiet_hours_active=False,
    user_actively_typing=False,
    now_ms=None,
):
    """Evaluate whether a proactive rule should fire right now, enforcing all safety gates."""
    ts = _now_ms(now_ms)
    state = load_rules_state(ts)

    if state.get("globalStop", False):
        return {"triggered": [], "didTrigger": False, "reason": "global_stop_active"}
    if active_run_busy:
        return {"triggered": [], "didTrigger": False, "reason": "single_active_run_busy"}
    if screen_locked:
        return {"triggered": [], "didTrigger": False, "reason": "screen_locked"}
    if fullscreen_active:
        return {"triggered": [], "didTrigger": False, "reason": "fullscreen_blocked"}
    if quiet_hours_active:
        return {"triggered": [], "didTrigger": False, "reason": "quiet_hours_blocked"}
    if focus_active and event_type != "focus_end":
        return {"triggered": [], "didTrigger": False, "reason": "focus_mode_active"}

    today = _today_str(ts)
    dedup_key = trigger_key or f"{event_type}:{event_id}:{time_hm}:{today}"
    if dedup_key in state.get("processedKeys", []):
        if ts - int(state.get("lastStartupMs", 0)) < 10_000:
            return {"triggered": [], "didTrigger": False, "reason": "startup_grace"}
        return {"triggered": [], "didTrigger": False, "reason": "duplicate_trigger_suppressed"}
    if ts - int(state.get("lastStartupMs", 0)) < 10_000:
        return {"triggered": [], "didTrigger": False, "reason": "startup_grace"}

    for rule in state["rules"]:
        if not rule.get("enabled", False) or rule.get("triggerType") != event_type:
            continue
        spec = rule.get("triggerSpec", {})
        if event_type == "planner_event" and spec.get("eventId") != str(event_id):
            continue
        if event_type == "schedule_time" and spec.get("timeHm") != str(time_hm):
            continue

        # Reset daily counter if date changed
        if rule.get("lastRunDate") != today:
            rule["lastRunDate"] = today
            rule["runsToday"] = 0
        max_daily = int(rule.get("budget", {}).get("maxRunsPerDay", 2))
        if int(rule.get("runsToday", 0)) >= max_daily:
            return {"triggered": [], "didTrigger": False, "reason": "daily_rule_budget_exhausted", "ruleId": rule["ruleId"]}

        rule["runsToday"] = int(rule.get("runsToday", 0)) + 1
        rule["lastRunDate"] = today
        state["processedKeys"] = (state.get("processedKeys", []) + [dedup_key])[-128:]
        state["updatedAtMs"] = ts
        save_rules_state(state)
        fired_rule = {
            **rule,
            "budget": {
                **(rule.get("budget") or {}),
                "allowGuiTakeover": False,
            },
            "allowGuiTakeover": False,
            "userActivelyWorking": bool(user_actively_typing),
        }
        return {
            "triggered": [fired_rule],
            "didTrigger": True,
            "reason": "ok",
            "rule": fired_rule,
        }

    return {"triggered": [], "didTrigger": False, "reason": "no_matching_rule"}
