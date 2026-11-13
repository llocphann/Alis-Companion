#!/usr/bin/env python3
"""Standalone Companion /swarm state, shared message board, member roster & DAG graph store."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

SCHEMA_VERSION = 1
MAX_BOARD_MESSAGES = 120
MAX_MEMBERS = 16

ROLE_META: dict[str, dict] = {
    "coordinator": {
        "title": "Swarm Coordinator",
        "icon": "hub",
        "layer": 0,
        "readOnly": True,
    },
    "researcher": {
        "title": "Discovery Researcher",
        "icon": "travel_explore",
        "layer": 1,
        "readOnly": True,
    },
    "planner": {
        "title": "Architecture Planner",
        "icon": "architecture",
        "layer": 1,
        "readOnly": True,
    },
    "coder": {
        "title": "Code Implementer",
        "icon": "code",
        "layer": 2,
        "readOnly": False,
    },
    "executor": {
        "title": "Desktop Executor",
        "icon": "terminal",
        "layer": 2,
        "readOnly": False,
    },
    "verifier": {
        "title": "Quality Verifier",
        "icon": "verified",
        "layer": 3,
        "readOnly": True,
    },
}


class SwarmStoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now_ms(now_ms: int | None = None) -> int:
    return int(now_ms if isinstance(now_ms, (int, float)) and now_ms > 0 else time.time() * 1000)


def _state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else (Path.home() / ".local/state")
    target = (base / "alis-companion").resolve(strict=False)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def _state_file() -> Path:
    return _state_dir() / "swarm_state.json"


def _derive_title(goal: str) -> str:
    cleaned = re.sub(r"^/swarm\b\s*", "", str(goal or "").strip(), flags=re.IGNORECASE).strip()
    if not cleaned:
        return "Companion Swarm"
    first_line = cleaned.splitlines()[0].strip()
    return (first_line[:56].rstrip() + "…") if len(first_line) > 58 else first_line


def extract_mentions(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r"(?:^|\s)@([a-zA-Z0-9_-]{2,32})\b", str(text or "")):
        name = match.group(1).lower()
        if name not in found:
            found.append(name)
    return found


def _make_member(
    member_id: str,
    role: str,
    *,
    task: str = "",
    status: str = "waiting",
    model: str = "",
    summary: str = "",
    tool_count: int = 0,
    elapsed_ms: int = 0,
    last_seq: int = 0,
) -> dict:
    norm_role = role if role in ROLE_META else "researcher"
    meta = ROLE_META[norm_role]
    return {
        "id": member_id,
        "name": member_id,
        "role": norm_role,
        "title": meta["title"],
        "icon": meta["icon"],
        "layer": int(meta["layer"]),
        "readOnly": bool(meta["readOnly"]),
        "status": status if status in ("waiting", "working", "done", "stopped") else "waiting",
        "model": str(model or "local-pinned")[:120],
        "task": str(task or "")[:360],
        "summary": str(summary or "")[:480],
        "toolCount": max(0, int(tool_count or 0)),
        "elapsedMs": max(0, int(elapsed_ms or 0)),
        "lastSeq": max(0, int(last_seq or 0)),
    }


def _rebuild_graph(state: dict) -> dict:
    members = state.get("members") if isinstance(state.get("members"), list) else []
    layer_Buckets: dict[int, list[dict]] = {0: [], 1: [], 2: [], 3: []}
    for m in members:
        if not isinstance(m, dict):
            continue
        lyr = max(0, min(3, int(m.get("layer", ROLE_META.get(str(m.get("role")), ROLE_META["researcher"])["layer"]))))
        layer_Buckets.setdefault(lyr, []).append(m)

    nodes: list[dict] = []
    for lyr in sorted(layer_Buckets.keys()):
        bucket = layer_Buckets[lyr]
        total_in_layer = len(bucket)
        for slot, m in enumerate(bucket):
            nodes.append(
                {
                    "id": str(m.get("id") or ""),
                    "label": str(m.get("name") or m.get("id") or ""),
                    "role": str(m.get("role") or "researcher"),
                    "title": str(m.get("title") or "Worker"),
                    "icon": str(m.get("icon") or "smart_toy"),
                    "layer": lyr,
                    "slot": slot,
                    "layerCount": total_in_layer,
                    "status": str(m.get("status") or "waiting"),
                    "readOnly": bool(m.get("readOnly", True)),
                    "model": str(m.get("model") or "local-pinned"),
                    "task": str(m.get("task") or ""),
                    "summary": str(m.get("summary") or ""),
                    "toolCount": int(m.get("toolCount") or 0),
                    "elapsedMs": int(m.get("elapsedMs") or 0),
                    "lastSeq": int(m.get("lastSeq") or 0),
                }
            )

    edges: list[dict] = []
    node_by_id = {n["id"]: n for n in nodes}
    coord = node_by_id.get("coordinator")
    layer1 = [n for n in nodes if n["layer"] == 1]
    layer2 = [n for n in nodes if n["layer"] == 2]
    layer3 = [n for n in nodes if n["layer"] == 3]

    def _add_edge(src_id: str, dst_id: str, kind: str, label: str) -> None:
        src = node_by_id.get(src_id)
        dst = node_by_id.get(dst_id)
        if not src or not dst or src_id == dst_id:
            return
        eid = f"{src_id}->{dst_id}"
        if any(e["id"] == eid for e in edges):
            return
        active = dst["status"] == "working" or (kind == "reports" and src["status"] == "working")
        completed = src["status"] == "done" and dst["status"] == "done"
        edges.append(
            {
                "id": eid,
                "from": src_id,
                "to": dst_id,
                "kind": kind,
                "label": label,
                "active": bool(active),
                "completed": bool(completed),
                "seq": max(int(src.get("lastSeq") or 0), int(dst.get("lastSeq") or 0)),
            }
        )

    if coord:
        first_wave = layer1 if layer1 else (layer2 if layer2 else layer3)
        for w in first_wave:
            _add_edge("coordinator", w["id"], "delegates", "brief")
        if not layer1 and layer2 and layer3:
            for w2 in layer2:
                for w3 in layer3:
                    _add_edge(w2["id"], w3["id"], "verifies", "verify")

    if layer1 and layer2:
        for w1 in layer1:
            for w2 in layer2:
                _add_edge(w1["id"], w2["id"], "feeds", "context")
    elif layer1 and layer3:
        for w1 in layer1:
            for w3 in layer3:
                _add_edge(w1["id"], w3["id"], "verifies", "verify")

    if layer2 and layer3:
        for w2 in layer2:
            for w3 in layer3:
                _add_edge(w2["id"], w3["id"], "verifies", "verify")

    # Terminal layer reports synthesis back to coordinator when no verifier sits below it
    terminal_nodes = layer3 if layer3 else (layer2 if layer2 else layer1)
    if coord and len(nodes) > 1:
        for tnode in terminal_nodes:
            if tnode["layer"] >= 2:
                _add_edge(tnode["id"], "coordinator", "reports", "report")

    return {"nodes": nodes, "edges": edges}


def default_state(now_ms: int | None = None, model: str = "") -> dict:
    ts = _now_ms(now_ms)
    coord = _make_member(
        "coordinator",
        "coordinator",
        task="Await /swarm goal, partition slices onto the board, and synthesize worker findings.",
        status="waiting",
        model=model or "local-pinned",
        summary="Ready. Run /swarm <goal> or add specialist workers.",
    )
    state = {
        "schemaVersion": SCHEMA_VERSION,
        "swarmId": "swarm_idle",
        "title": "Companion Swarm",
        "goal": "",
        "status": "idle",
        "model": str(model or "local-pinned")[:120],
        "createdMs": ts,
        "updatedMs": ts,
        "nextSeq": 1,
        "members": [coord],
        "board": [],
    }
    state["graph"] = _rebuild_graph(state)
    return state


def emit_swarm_state(state: dict) -> None:
    try:
        sys.stderr.write("__AQUA_SWARM__:" + json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def load_state(now_ms: int | None = None) -> dict:
    fpath = _state_file()
    if not fpath.is_file():
        return default_state(now_ms)
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_state(now_ms)
        if not isinstance(data.get("members"), list) or not data["members"]:
            data["members"] = default_state(now_ms)["members"]
        if not isinstance(data.get("board"), list):
            data["board"] = []
        data["schemaVersion"] = SCHEMA_VERSION
        data["nextSeq"] = max(
            int(data.get("nextSeq") or 1),
            max((int(m.get("seq") or 0) for m in data["board"] if isinstance(m, dict)), default=0) + 1,
        )
        data["graph"] = _rebuild_graph(data)
        return data
    except Exception:
        return default_state(now_ms)


def save_state(state: dict, *, emit: bool = True, now_ms: int | None = None) -> dict:
    ts = _now_ms(now_ms)
    state["schemaVersion"] = SCHEMA_VERSION
    state["updatedMs"] = ts
    if isinstance(state.get("board"), list) and len(state["board"]) > MAX_BOARD_MESSAGES:
        state["board"] = state["board"][-MAX_BOARD_MESSAGES:]
    state["graph"] = _rebuild_graph(state)
    fpath = _state_file()
    tmp = fpath.with_name(f".swarm_state.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, fpath)
    if emit:
        emit_swarm_state(state)
    return state


def _append_board_post(
    state: dict,
    *,
    sender: str,
    text: str,
    kind: str = "finding",
    mentions: list[str] | None = None,
    now_ms: int | None = None,
) -> dict:
    ts = _now_ms(now_ms)
    clean_text = str(text or "").strip()[:2400]
    if not clean_text:
        return {}
    seq = max(1, int(state.get("nextSeq") or 1))
    state["nextSeq"] = seq + 1
    member_map = {str(m.get("id")): m for m in state.get("members", []) if isinstance(m, dict)}
    sender_obj = member_map.get(sender)
    role = str(sender_obj.get("role") if sender_obj else ("user" if sender == "user" else "coordinator"))
    sender_title = str(
        sender_obj.get("title")
        if sender_obj
        else ("Owner" if sender == "user" else ROLE_META.get(role, ROLE_META["coordinator"])["title"])
    )
    parsed_mentions = mentions if isinstance(mentions, list) else extract_mentions(clean_text)
    msg = {
        "seq": seq,
        "sender": sender,
        "senderTitle": sender_title,
        "role": role,
        "kind": kind if kind in ("brief", "finding", "verdict", "synthesis", "user", "status") else "finding",
        "mentions": [str(x).lower()[:32] for x in parsed_mentions[:8]],
        "text": clean_text,
        "timestampMs": ts,
    }
    state.setdefault("board", []).append(msg)
    if sender_obj is not None:
        sender_obj["lastSeq"] = seq
    return msg


def next_worker_id(state: dict, role: str) -> str:
    norm_role = role if role in ROLE_META and role != "coordinator" else "researcher"
    existing = {str(m.get("id") or "") for m in state.get("members", []) if isinstance(m, dict)}
    idx = 1
    while f"{norm_role}-{idx}" in existing:
        idx += 1
    return f"{norm_role}-{idx}"


def build_default_swarm_roster(goal: str, subtasks: list[dict], model: str = "") -> list[dict]:
    """Ensure a balanced Coordinator + multi-specialist worker roster for /swarm."""
    pinned_model = str(model or "local-pinned")[:120]
    roster: list[dict] = [
        _make_member(
            "coordinator",
            "coordinator",
            task=f"Coordinate swarm mission and synthesize findings for: {goal[:200]}",
            status="working",
            model=pinned_model,
            summary="Partitioning goal and posting briefs onto the board...",
        )
    ]
    raw_specs = list(subtasks or [])
    if len(raw_specs) < 2:
        folded = goal.lower()
        is_code = bool(re.search(r"\b(code|bug|fix|refactor|qml|python|rust|test|git|script|build)\b", folded))
        raw_specs = [
            {
                "role": "researcher",
                "task": f"Inspect context, files, and constraints for: {goal[:220]}",
            },
            {
                "role": "coder" if is_code else "planner",
                "task": (
                    f"Formulate concrete code/implementation changes for: {goal[:220]}"
                    if is_code
                    else f"Design structured execution steps and trade-offs for: {goal[:220]}"
                ),
            },
            {
                "role": "verifier",
                "task": f"Verify edge cases, safety gates, and regressions for: {goal[:220]}",
            },
        ]
    elif not any(str(s.get("role")) == "verifier" for s in raw_specs) and len(raw_specs) < 4:
        raw_specs.append(
            {
                "role": "verifier",
                "task": f"Audit worker outputs, regressions, and completion criteria for: {goal[:220]}",
            }
        )

    role_counts: dict[str, int] = {}
    for spec in raw_specs[:MAX_MEMBERS - 1]:
        r = str(spec.get("role") or "researcher").strip().lower()
        if r not in ROLE_META or r == "coordinator":
            r = "researcher"
        role_counts[r] = role_counts.get(r, 0) + 1
        wid = f"{r}-{role_counts[r]}"
        roster.append(
            _make_member(
                wid,
                r,
                task=str(spec.get("task") or goal)[:360],
                status="waiting",
                model=pinned_model,
                summary="Queued on shared board",
            )
        )
    return roster


def start_swarm_mission(
    goal: str,
    subtasks: list[dict],
    *,
    model: str = "",
    now_ms: int | None = None,
    emit: bool = True,
) -> dict:
    ts = _now_ms(now_ms)
    clean_goal = re.sub(r"^/swarm\b\s*", "", str(goal or "").strip(), flags=re.IGNORECASE).strip() or "Swarm analysis"
    pinned_model = str(model or "local-pinned")[:120]
    members = build_default_swarm_roster(clean_goal, subtasks, model=pinned_model)
    state = {
        "schemaVersion": SCHEMA_VERSION,
        "swarmId": f"swarm_{uuid.uuid4().hex[:10]}",
        "title": _derive_title(clean_goal),
        "goal": clean_goal[:600],
        "status": "running",
        "model": pinned_model,
        "createdMs": ts,
        "updatedMs": ts,
        "nextSeq": 1,
        "members": members,
        "board": [],
    }
    worker_mentions = [m["id"] for m in members if m["id"] != "coordinator"]
    _append_board_post(
        state,
        sender="coordinator",
        kind="brief",
        mentions=["all"] + worker_mentions,
        text=f"@all Mission started: **{clean_goal}**\nPinned model: `{pinned_model}` (sequential VRAM-safe execution across {len(worker_mentions)} workers).",
        now_ms=ts,
    )
    for w in members:
        if w["id"] == "coordinator":
            continue
        _append_board_post(
            state,
            sender="coordinator",
            kind="brief",
            mentions=[w["id"]],
            text=f"@{w['id']} [{w['title']}] Assignment: {w['task']}",
            now_ms=ts,
        )
    return save_state(state, emit=emit, now_ms=ts)


def mark_worker_working(state: dict, worker_id: str, *, now_ms: int | None = None, emit: bool = True) -> dict:
    for m in state.get("members", []):
        if m.get("id") == worker_id:
            m["status"] = "working"
            m["summary"] = "Executing assigned brief..."
        elif m.get("id") == "coordinator" and state.get("status") == "running":
            m["status"] = "waiting"
            m["summary"] = f"Waiting on @{worker_id}..."
    return save_state(state, emit=emit, now_ms=now_ms)


def record_worker_completion(
    state: dict,
    worker_id: str,
    sub_res: dict,
    *,
    now_ms: int | None = None,
    emit: bool = True,
) -> dict:
    ts = _now_ms(now_ms)
    raw_summary = str(sub_res.get("rawSummary") or sub_res.get("summary") or "Completed assigned slice.").strip()
    tool_count = int(sub_res.get("toolCount") or 0)
    elapsed_ms = int(sub_res.get("elapsedMs") or 0)
    kind = "verdict" if str(sub_res.get("role") or "").startswith("verifier") or worker_id.startswith("verifier") else "finding"
    for m in state.get("members", []):
        if m.get("id") == worker_id:
            m["status"] = "done"
            m["summary"] = raw_summary[:480]
            m["toolCount"] = tool_count
            m["elapsedMs"] = elapsed_ms
            if sub_res.get("model"):
                m["model"] = str(sub_res["model"])[:120]
            break
    _append_board_post(
        state,
        sender=worker_id,
        kind=kind,
        mentions=["coordinator"],
        text=f"@coordinator {raw_summary}",
        now_ms=ts,
    )
    return save_state(state, emit=emit, now_ms=ts)


def finish_swarm_mission(
    state: dict,
    synthesis_text: str,
    *,
    status: str = "completed",
    now_ms: int | None = None,
    emit: bool = True,
) -> dict:
    ts = _now_ms(now_ms)
    state["status"] = status if status in ("completed", "stopped") else "completed"
    clean_synth = str(synthesis_text or "All swarm workers finished.").strip()
    for m in state.get("members", []):
        if m.get("id") == "coordinator":
            m["status"] = "done" if state["status"] == "completed" else "stopped"
            m["summary"] = clean_synth[:480]
        elif m.get("status") == "working":
            m["status"] = "done" if state["status"] == "completed" else "stopped"
    _append_board_post(
        state,
        sender="coordinator",
        kind="synthesis",
        mentions=["all"],
        text=f"@all **Synthesis ({state['status']})**: {clean_synth[:1400]}",
        now_ms=ts,
    )
    return save_state(state, emit=emit, now_ms=ts)


def add_member_to_state(
    role: str,
    brief: str,
    *,
    model: str = "",
    now_ms: int | None = None,
    emit: bool = True,
) -> tuple[dict, dict]:
    ts = _now_ms(now_ms)
    state = load_state(ts)
    if len(state.get("members", [])) >= MAX_MEMBERS:
        raise SwarmStoreError("swarm_roster_full", f"Swarm roster supports at most {MAX_MEMBERS} members.")
    norm_role = str(role or "researcher").strip().lower()
    if norm_role not in ROLE_META or norm_role == "coordinator":
        norm_role = "researcher"
    wid = next_worker_id(state, norm_role)
    clean_brief = str(brief or f"Assist with {state.get('goal') or 'active workspace task'}").strip()[:360]
    eff_model = str(model or state.get("model") or "local-pinned")[:120]
    member = _make_member(
        wid,
        norm_role,
        task=clean_brief,
        status="waiting",
        model=eff_model,
        summary="Added to roster; ready on board.",
    )
    state.setdefault("members", []).append(member)
    if state.get("swarmId") == "swarm_idle":
        state["swarmId"] = f"swarm_{uuid.uuid4().hex[:10]}"
    _append_board_post(
        state,
        sender="coordinator",
        kind="brief",
        mentions=[wid],
        text=f"@{wid} [{member['title']}] Joined swarm. Brief: {clean_brief}",
        now_ms=ts,
    )
    save_state(state, emit=emit, now_ms=ts)
    return state, member


def remove_member_from_state(member_id: str, *, now_ms: int | None = None, emit: bool = True) -> dict:
    ts = _now_ms(now_ms)
    target = str(member_id or "").strip()
    if not target or target == "coordinator":
        raise SwarmStoreError("invalid_member", "Coordinator cannot be removed from the swarm.")
    state = load_state(ts)
    before = len(state.get("members", []))
    state["members"] = [m for m in state.get("members", []) if str(m.get("id")) != target]
    if len(state["members"]) == before:
        raise SwarmStoreError("member_not_found", f"Worker {target} was not found.")
    _append_board_post(
        state,
        sender="coordinator",
        kind="status",
        mentions=[],
        text=f"Removed `{target}` from the active roster.",
        now_ms=ts,
    )
    return save_state(state, emit=emit, now_ms=ts)


def stop_swarm_state(*, now_ms: int | None = None, emit: bool = True) -> dict:
    ts = _now_ms(now_ms)
    state = load_state(ts)
    state["status"] = "stopped"
    for m in state.get("members", []):
        if m.get("status") == "working":
            m["status"] = "stopped"
            m["summary"] = "Stopped by owner."
        elif m.get("id") == "coordinator":
            m["status"] = "stopped"
            m["summary"] = "Swarm stopped by owner."
    _append_board_post(
        state,
        sender="coordinator",
        kind="status",
        mentions=["all"],
        text="@all Swarm execution stopped by owner.",
        now_ms=ts,
    )
    return save_state(state, emit=emit, now_ms=ts)


def clear_swarm_state(*, model: str = "", now_ms: int | None = None, emit: bool = True) -> dict:
    ts = _now_ms(now_ms)
    state = default_state(ts, model=model)
    return save_state(state, emit=emit, now_ms=ts)


def format_swarm_markdown(state: dict, lang: str = "en") -> str:
    title = str(state.get("title") or "Companion Swarm")
    status = str(state.get("status") or "idle").upper()
    members = state.get("members") if isinstance(state.get("members"), list) else []
    board = state.get("board") if isinstance(state.get("board"), list) else []
    header = (
        f"### 🐝 Swarm: {title} (`{status}`)"
        if lang != "vi"
        else f"### 🐝 Swarm: {title} (`{status}`)"
    )
    lines = [header]
    if state.get("goal"):
        lines.append(f"**Goal:** {state['goal']}")
    lines.append("")
    lines.append("**Members:**")
    for m in members:
        badge = str(m.get("status") or "waiting").upper()
        ro = "RO" if m.get("readOnly") else "RW"
        summ = str(m.get("summary") or m.get("task") or "").splitlines()[0][:140]
        lines.append(f"- **`@{m.get('id')}`** ({m.get('title')} · `{badge}` · `{ro}`): {summ}")
    if board:
        lines.append("")
        lines.append("**Recent Board Posts:**")
        for post in board[-5:]:
            txt_one = str(post.get("text") or "").replace("\n", " ")[:180]
            lines.append(f"- `#{post.get('seq')}` **{post.get('sender')}**: {txt_one}")
    return "\n".join(lines)
