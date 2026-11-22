#!/usr/bin/env python3
"""Stateless Tier 1 Deterministic A2UI v1.0 surface builders for Alis-Companion.

Generates compact, validated A2UI v1.0 `createSurface` payloads (<= 6 KB) for 5
net-new Companion card families without duplicating existing Companion panes:
  1. Confirmation Cards (all 14+ confirmation points; sudo stays composer-only)
  2. Live Snapshot / `compound_task` / `/plan` interactive Answer & Checklist Cards
  3. Web / RAG / Nexus Citations Cards
  4. Test / Shell / Git / Diff Receipt Cards (with Meta-Chip deduplication)
  5. Hybrid Stage 1+2 UI+Deep Grounding Summary Cards
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any

import a2ui_validator


def _make_surface_id(prefix: str, seed_text: str) -> str:
    h = hashlib.sha256(f"{prefix}:{seed_text}:{time.time_ns()}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"surf_{prefix}_{h}"


def _short(text: Any, limit: int = 120) -> str:
    s = " ".join(str(text or "").split()).strip()
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)].rstrip() + "…"


def _first_nonempty_line(text: Any) -> str:
    for line in str(text or "").splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _extract_markdown_links(text: str, limit: int = 4) -> list[tuple[str, str]]:
    """Extract (label, https_url) pairs from Markdown links or bare URLs."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r"\[([^\]]{1,90})\]\((https?://[^\s)]+)\)", text or ""):
        lbl = _short(m.group(1), 64)
        url = m.group(2).strip().rstrip(".,;:")
        if url not in seen:
            seen.add(url)
            found.append((lbl or url, url))
            if len(found) >= limit:
                return found
    for m in re.finditer(r"(?<![(\[])(https?://[^\s<>)\]]+)", text or ""):
        url = m.group(1).strip().rstrip(".,;:")
        if url not in seen:
            seen.add(url)
            host = re.sub(r"^https?://(?:www\.)?", "", url).split("/", 1)[0]
            found.append((_short(host or url, 64), url))
            if len(found) >= limit:
                break
    return found


def _extract_plan_steps(text: str, limit: int = 8) -> list[str]:
    steps: list[str] = []
    for line in str(text or "").splitlines():
        m = re.match(r"^\s*(?:\d+[.)]|[-*•]\s+\[[ xX]\])\s+(.+)$", line.strip())
        if m:
            step = re.sub(r"[*_`]+", "", m.group(1)).strip()
            if len(step) >= 4:
                steps.append(_short(step, 110))
                if len(steps) >= limit:
                    break
    return steps


def _btn_with_label(
    btn_id: str,
    label_id: str,
    label_text: str,
    action: dict[str, Any],
    *,
    variant: str = "secondary",
    icon_id: str = "",
    icon_name: str = "",
    disabled: Any = None,
) -> list[dict[str, Any]]:
    comps: list[dict[str, Any]] = []
    if icon_id and icon_name:
        row_id = f"{btn_id}_row"
        comps.append({"id": icon_id, "component": "Icon", "name": icon_name, "size": 14})
        comps.append({"id": label_id, "component": "Text", "variant": "caption", "text": label_text})
        comps.append({"id": row_id, "component": "Row", "gap": 4, "align": "center", "children": [icon_id, label_id]})
        btn_child = row_id
    else:
        comps.append({"id": label_id, "component": "Text", "variant": "caption", "text": label_text})
        btn_child = label_id
    btn: dict[str, Any] = {
        "id": btn_id,
        "component": "Button",
        "child": btn_child,
        "variant": variant,
        "action": action,
    }
    if disabled is not None:
        btn["disabled"] = disabled
    comps.append(btn)
    return comps


def build_confirmation_surface(result: dict[str, Any], *, lang: str = "en") -> dict[str, Any] | None:
    """Group 1: Deterministic Confirmation Card (sudo never renders a password input)."""
    confirm_id = str(result.get("confirmId") or "").strip()
    is_sudo = bool(result.get("requiresSudoPassword"))
    sess = result.get("sessionContext") if isinstance(result.get("sessionContext"), dict) else {}
    vi = str(lang or "en").lower().startswith("vi")

    action_label = "Xác nhận thao tác" if vi else "Confirm Action"
    detail_text = ""
    icon_name = "verified_user"
    tone = "warning"
    confirm_variant = "primary"

    if is_sudo:
        icon_name = "admin_panel_settings"
        tone = "warning"
        action_label = "Yêu cầu mật khẩu sudo" if vi else "Sudo Password Required"
        sudo_obj = sess.get("pendingSudoOp") if isinstance(sess.get("pendingSudoOp"), dict) else {}
        tgt = str(sudo_obj.get("targetPath") or (sudo_obj.get("payload") or {}).get("targetPath") or "")
        detail_text = (
            f"Nhập mật khẩu `sudo` trực tiếp vào ô chat bên dưới cho `{_short(tgt, 64)}` (không lưu vào lịch sử)."
            if vi
            else f"Enter your `sudo` password in the chat composer below for `{_short(tgt, 64)}` (never stored)."
        )
    elif sess.get("pendingDeletePath"):
        icon_name = "delete_forever"
        tone = "error"
        confirm_variant = "danger"
        action_label = "Xác nhận xoá tệp" if vi else "Confirm File Deletion"
        detail_text = f"`{_short(sess.get('pendingDeletePath'), 80)}`"
    elif isinstance(sess.get("pendingMemoryForget"), dict):
        icon_name = "memory"
        tone = "error"
        confirm_variant = "danger"
        mem = sess["pendingMemoryForget"]
        action_label = "Xác nhận xoá ghi nhớ" if vi else "Confirm Forget Memory"
        detail_text = (
            ("Xoá toàn bộ ghi nhớ Companion" if vi else "Delete all Companion memories")
            if mem.get("all")
            else _short(mem.get("text") or f"#{mem.get('id', '')}", 90)
        )
    elif isinstance(sess.get("pendingClientAction"), dict):
        pca = sess["pendingClientAction"]
        p_act = str(pca.get("action") or "")
        p_tgt = _short(pca.get("target") or pca.get("value") or "", 80)
        icon_name = "edit_note"
        tone = "accent"
        if p_act == "todo_add":
            action_label = "Thêm vào Todo" if vi else "Add Todo Task"
            icon_name = "checklist"
        elif p_act == "quicknote_add":
            action_label = "Lưu vào QuickNotes" if vi else "Save QuickNote"
            icon_name = "sticky_note_2"
        elif p_act == "rag_note_save":
            action_label = "Lưu ghi chú Nexus RAG" if vi else "Save Nexus RAG Note"
            icon_name = "library_books"
        detail_text = p_tgt or p_act
    else:
        first_line = _first_nonempty_line(result.get("text"))
        detail_text = _short(re.sub(r"[*_`]+", "", first_line), 110)

    sid = _make_surface_id("confirm", confirm_id or detail_text)
    comps: list[dict[str, Any]] = [
        {"id": "hdr_icon", "component": "Icon", "name": icon_name, "size": 16, "tone": "error" if tone == "error" else "accent"},
        {"id": "hdr_title", "component": "Text", "variant": "body", "text": f"### {action_label}"},
        {"id": "hdr_row", "component": "Row", "gap": 6, "align": "center", "children": ["hdr_icon", "hdr_title"]},
        {"id": "detail_txt", "component": "Text", "variant": "caption", "tone": "muted", "text": detail_text or action_label},
        {"id": "status_txt", "component": "Text", "variant": "caption", "tone": "accent", "text": {"path": "/statusLabel"}},
        {"id": "outcome_txt", "component": "Text", "variant": "caption", "tone": "default", "text": {"path": "/outcomeSummary"}, "visible": {"path": "/outcomeSummary"}, "maxLines": 3},
    ]

    btn_ids: list[str] = []
    if not is_sudo:
        comps.extend(
            _btn_with_label(
                "btn_confirm",
                "btn_confirm_lbl",
                "Đồng ý" if vi else "Confirm",
                {"event": {"name": "confirm_action", "context": {"confirmId": confirm_id, "decision": "confirm"}}},
                variant=confirm_variant,
                icon_id="btn_confirm_ico",
                icon_name="check",
                disabled={"path": "/resolved"},
            )
        )
        btn_ids.append("btn_confirm")

    comps.extend(
        _btn_with_label(
            "btn_cancel",
            "btn_cancel_lbl",
            "Huỷ" if vi else "Cancel",
            {"event": {"name": "confirm_action", "context": {"confirmId": confirm_id, "decision": "cancel"}}},
            variant="secondary",
            icon_id="btn_cancel_ico",
            icon_name="close",
            disabled={"path": "/resolved"},
        )
    )
    btn_ids.append("btn_cancel")

    comps.append({"id": "actions_row", "component": "Row", "gap": 6, "wrap": True, "children": btn_ids})
    comps.append({"id": "body_col", "component": "Column", "gap": 6, "children": ["hdr_row", "detail_txt", "status_txt", "outcome_txt", "actions_row"]})
    comps.append({"id": "root", "component": "Card", "tone": tone, "child": "body_col"})

    raw_surface = {
        "version": "1.0",
        "createSurface": {
            "surfaceId": sid,
            "catalogId": a2ui_validator.DEFAULT_CATALOG_ID,
            "sendDataModel": False,
            "surfaceMeta": {
                "kind": "confirmation",
                "interactive": True,
                "confirmId": confirm_id,
            },
            "components": comps,
            "dataModel": {
                "confirmId": confirm_id,
                "resolved": False,
                "resolution": "pending",
                "statusLabel": ("Đang chờ xác nhận" if vi else "Awaiting confirmation"),
                "outcomeSummary": "",
            },
        },
    }
    valid, _err = a2ui_validator.validate_and_sanitize_surface(raw_surface)
    return valid


def build_snapshot_surface(
    result: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    *,
    lang: str = "en",
) -> dict[str, Any] | None:
    """Group 2a: Live Snapshot & `compound_task` Answer Card + 1-Tap Safe Action / Deep-Link."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    topic = str(result.get("topic") or "all").strip().lower()
    intent_name = str(result.get("intent") or "")
    vi = str(lang or "en").lower().startswith("vi")

    if intent_name == "compound_task":
        subtasks = [str(s) for s in (result.get("subtasks") or [])]
        title = "Tổng hợp nhanh" if vi else "Quick Multi-Task Summary"
        icon_name = "dashboard"
        metrics: list[dict[str, str]] = []
        actions: list[tuple[str, str, str, str, str]] = []  # (label, icon, act, target, value)
        for st in subtasks[:4]:
            if "weather" in st:
                w = snap.get("weather") if isinstance(snap.get("weather"), dict) else {}
                temp = str(w.get("temp") or "--°C")
                desc = str(w.get("description") or "")
                metrics.append({"label": "Thời tiết" if vi else "Weather", "value": _short(f"{temp} {desc}".strip(), 48)})
                actions.append(("Làm mới thời tiết" if vi else "Refresh Weather", "refresh", "weather_refresh", "", ""))
            elif "music" in st:
                m = snap.get("music") if isinstance(snap.get("music"), dict) else {}
                tr = str(m.get("currentTitle") or m.get("mprisTitle") or ("Đang dừng" if vi else "Idle"))
                metrics.append({"label": "Nhạc" if vi else "Music", "value": _short(tr, 48)})
                actions.append(("Phát / Tạm dừng" if vi else "Play / Pause", "play_arrow", "music_toggle", "", ""))
            elif "timers" in st:
                t = snap.get("timers") if isinstance(snap.get("timers"), dict) else {}
                left_m = max(0, round(float(t.get("pomodoroSecondsLeft") or 0) / 60))
                metrics.append({"label": "Pomodoro", "value": f"{left_m}m" if t.get("pomodoroRunning") else ("Nghỉ" if vi else "Idle")})
                actions.append(("Pomodoro", "timer", "pomodoro_toggle", "", ""))
            elif "system" in st:
                sy = snap.get("system") if isinstance(snap.get("system"), dict) else {}
                cpu = sy.get("cpuPercent")
                mem = sy.get("memPercent")
                metrics.append({"label": "Hệ thống" if vi else "System", "value": f"CPU {cpu}% · RAM {mem}%" if cpu is not None else "OK"})
                actions.append(("DND", "do_not_disturb_on", "dnd_toggle", "", ""))
            elif "screen_observe" in st:
                metrics.append({"label": "Màn hình" if vi else "Screen", "value": "Đã phân tích" if vi else "Analyzed"})
        if not metrics:
            first_ln = _first_nonempty_line(result.get("text"))
            metrics.append({"label": "Trạng thái" if vi else "Status", "value": _short(re.sub(r"[*_`#]+", "", first_ln), 64)})
        if not actions:
            actions.append(("Mở Nexus" if vi else "Open Nexus", "hub", "nexus_open", "", ""))
    else:
        title_map_vi = {
            "weather": ("Thời tiết & AQI", "partly_cloudy_day"),
            "music": ("Nhạc cục bộ", "music_note"),
            "timers": ("Pomodoro & Tập trung", "timer"),
            "todos": ("Việc cần làm", "checklist"),
            "schedule": ("Lịch trình hôm nay", "calendar_today"),
            "system": ("Hệ thống & Pin", "monitoring"),
            "learning": ("Spaced Repetition", "school"),
            "fitness": ("Tập luyện", "fitness_center"),
        }
        title_map_en = {
            "weather": ("Weather & AQI", "partly_cloudy_day"),
            "music": ("Local Music", "music_note"),
            "timers": ("Pomodoro & Focus", "timer"),
            "todos": ("Tasks & Todo", "checklist"),
            "schedule": ("Today's Schedule", "calendar_today"),
            "system": ("System & Power", "monitoring"),
            "learning": ("Spaced Repetition", "school"),
            "fitness": ("Workouts", "fitness_center"),
        }
        title, icon_name = (title_map_vi if vi else title_map_en).get(topic, ("Trạng thái Alis" if vi else "Alis Snapshot", "dashboard"))
        metrics = []
        actions = []
        if topic == "weather":
            w = snap.get("weather") if isinstance(snap.get("weather"), dict) else {}
            if w.get("temp"):
                metrics.append({"label": "Nhiệt độ" if vi else "Temp", "value": _short(f"{w.get('temp')} {w.get('description', '')}".strip(), 48)})
            if w.get("aqi") is not None:
                metrics.append({"label": "AQI", "value": str(w.get("aqi"))})
            actions.append(("Làm mới" if vi else "Refresh", "refresh", "weather_refresh", "", ""))
        elif topic == "music":
            m = snap.get("music") if isinstance(snap.get("music"), dict) else {}
            tr = str(m.get("currentTitle") or m.get("mprisTitle") or ("Chưa phát" if vi else "Not playing"))
            metrics.append({"label": "Đang phát" if vi else "Track", "value": _short(tr, 54)})
            actions.append(("Phát / Dừng" if vi else "Play / Pause", "play_arrow", "music_toggle", "", ""))
            actions.append(("Bài tiếp" if vi else "Next", "skip_next", "music_next", "", ""))
        elif topic == "timers":
            t = snap.get("timers") if isinstance(snap.get("timers"), dict) else {}
            running = bool(t.get("pomodoroRunning"))
            left_m = max(0, round(float(t.get("pomodoroSecondsLeft") or 0) / 60))
            metrics.append({"label": "Pomodoro", "value": (f"{left_m}m" if running else ("Chưa chạy" if vi else "Stopped"))})
            actions.append(("Bắt đầu / Dừng" if vi else "Start / Pause", "timer", "pomodoro_toggle", "", ""))
            actions.append(("Đặt lại" if vi else "Reset", "restart_alt", "pomodoro_reset", "", ""))
        elif topic == "system":
            sy = snap.get("system") if isinstance(snap.get("system"), dict) else {}
            if sy.get("cpuPercent") is not None:
                metrics.append({"label": "CPU", "value": f"{sy.get('cpuPercent')}%"})
            if sy.get("memPercent") is not None:
                metrics.append({"label": "RAM", "value": f"{sy.get('memPercent')}%"})
            if sy.get("batteryPercent") is not None:
                metrics.append({"label": "Pin" if vi else "Battery", "value": f"{sy.get('batteryPercent')}%"})
            actions.append(("Night Light", "nightlight", "night_light_toggle", "", ""))
            actions.append(("DND", "do_not_disturb_on", "dnd_toggle", "", ""))
        else:
            actions.append(("Daily Note", "edit_calendar", "nexus_open_daily", "", ""))
            actions.append(("Mở Nexus" if vi else "Open Nexus", "hub", "nexus_open", "", ""))

        if not metrics:
            first_ln = _first_nonempty_line(result.get("text"))
            metrics.append({"label": title, "value": _short(re.sub(r"[*_`#]+", "", first_ln), 64)})

    sid = _make_surface_id("snap", f"{intent_name}:{topic}")
    comps: list[dict[str, Any]] = [
        {"id": "hdr_ico", "component": "Icon", "name": icon_name, "size": 15, "tone": "accent"},
        {"id": "hdr_txt", "component": "Text", "variant": "body", "text": f"### {title}"},
        {"id": "hdr_row", "component": "Row", "gap": 6, "align": "center", "children": ["hdr_ico", "hdr_txt"]},
        {"id": "m_lbl", "component": "Text", "variant": "caption", "tone": "muted", "text": {"path": "/label"}},
        {"id": "m_val", "component": "Text", "variant": "body", "align": "right", "text": {"path": "/value"}},
        {"id": "m_row", "component": "Row", "gap": 8, "justify": "spaceBetween", "children": ["m_lbl", "m_val"]},
        {"id": "m_list", "component": "List", "gap": 4, "children": {"componentId": "m_row", "path": "/metrics"}},
    ]
    btn_ids: list[str] = []
    for idx, (albl, aico, act, atgt, aval) in enumerate(actions[:3]):
        bid = f"act_{idx}"
        comps.extend(
            _btn_with_label(
                bid,
                f"{bid}_lbl",
                albl,
                {"functionCall": {"call": "clientAction", "args": {"action": act, "target": atgt, "value": aval}}},
                variant="primary" if idx == 0 else "secondary",
                icon_id=f"{bid}_ico",
                icon_name=aico,
            )
        )
        btn_ids.append(bid)

    body_children = ["hdr_row", "m_list"]
    if btn_ids:
        comps.append({"id": "act_row", "component": "Row", "gap": 6, "wrap": True, "children": btn_ids})
        body_children.append("act_row")
    comps.append({"id": "body_col", "component": "Column", "gap": 6, "children": body_children})
    comps.append({"id": "root", "component": "Card", "tone": "subtle", "child": "body_col"})

    raw_surface = {
        "version": "1.0",
        "createSurface": {
            "surfaceId": sid,
            "catalogId": a2ui_validator.DEFAULT_CATALOG_ID,
            "sendDataModel": False,
            "surfaceMeta": {"kind": "snapshot", "interactive": False},
            "components": comps,
            "dataModel": {"metrics": metrics[:5]},
        },
    }
    valid, _err = a2ui_validator.validate_and_sanitize_surface(raw_surface)
    return valid


def build_plan_surface(result: dict[str, Any], *, lang: str = "en") -> dict[str, Any] | None:
    """Group 2b: Interactive `/plan` Checklist Card persisted across restarts."""
    plan_src = str(result.get("text") or "")
    clar = result.get("clarification")
    if isinstance(clar, dict) and clar.get("question"):
        q_str = str(clar["question"]).strip()
        if q_str:
            pos = plan_src.find(q_str)
            if pos > 0:
                plan_src = plan_src[:pos]
    steps = _extract_plan_steps(plan_src, limit=7)
    if len(steps) < 2:
        return None
    vi = str(lang or "en").lower().startswith("vi")
    sid = _make_surface_id("plan", steps[0])
    step_items = [{"index": i, "label": st, "done": False} for i, st in enumerate(steps)]
    comps: list[dict[str, Any]] = [
        {"id": "p_ico", "component": "Icon", "name": "checklist", "size": 15, "tone": "accent"},
        {"id": "p_title", "component": "Text", "variant": "body", "text": f"### {'Danh sách bước thực hiện' if vi else 'Action Plan Checklist'}"},
        {"id": "p_hdr", "component": "Row", "gap": 6, "align": "center", "children": ["p_ico", "p_title"]},
        {
            "id": "step_chk",
            "component": "CheckBox",
            "label": {"path": "/label"},
            "value": {"path": "/done"},
            "action": {"event": {"name": "plan_toggle", "context": {"index": {"path": "/index"}, "done": {"path": "/done"}}}},
        },
        {"id": "step_list", "component": "List", "gap": 4, "children": {"componentId": "step_chk", "path": "/steps"}},
        {"id": "body_col", "component": "Column", "gap": 6, "children": ["p_hdr", "step_list"]},
        {"id": "root", "component": "Card", "tone": "subtle", "child": "body_col"},
    ]
    raw_surface = {
        "version": "1.0",
        "createSurface": {
            "surfaceId": sid,
            "catalogId": a2ui_validator.DEFAULT_CATALOG_ID,
            "sendDataModel": False,
            "surfaceMeta": {"kind": "plan", "interactive": True},
            "components": comps,
            "dataModel": {"steps": step_items},
        },
    }
    valid, _err = a2ui_validator.validate_and_sanitize_surface(raw_surface)
    return valid


def build_citations_surface(result: dict[str, Any], *, lang: str = "en") -> dict[str, Any] | None:
    """Group 3: Web / RAG / Nexus Citations Card with safe 1-tap open actions."""
    intent_name = str(result.get("intent") or "")
    text = str(result.get("text") or "")
    vi = str(lang or "en").lower().startswith("vi")
    links = _extract_markdown_links(text, limit=4)

    items: list[dict[str, Any]] = []
    if links:
        for lbl, url in links:
            items.append({"title": _short(lbl, 56), "subtitle": _short(re.sub(r"^https?://", "", url), 52), "url": url})
    elif intent_name == "nexus_knowledge":
        # Extract backticked Nexus note paths (`90_System/...` or `*.md`)
        seen: set[str] = set()
        for m in re.finditer(r"`([^`\n]{2,120}\.md)`", text):
            rel = m.group(1).strip()
            if rel not in seen:
                seen.add(rel)
                items.append({"title": _short(rel.rsplit("/", 1)[-1], 52), "subtitle": _short(rel, 56), "notePath": rel})
                if len(items) >= 4:
                    break
    elif intent_name == "rag_query":
        seen = set()
        for m in re.finditer(r"`((?:/|~/)[A-Za-z0-9_./\-]+)`", text):
            fp = m.group(1).strip()
            if fp not in seen:
                seen.add(fp)
                items.append({"title": _short(fp.rsplit("/", 1)[-1], 52), "subtitle": _short(fp, 56), "filePath": fp})
                if len(items) >= 4:
                    break

    if not items:
        return None

    sid = _make_surface_id("cite", f"{intent_name}:{items[0]['title']}")
    hdr_text = (
        "Nguồn tham khảo" if vi else "Sources & Citations"
    )
    comps: list[dict[str, Any]] = [
        {"id": "c_ico", "component": "Icon", "name": "travel_explore" if links else "menu_book", "size": 15, "tone": "accent"},
        {"id": "c_hdr_txt", "component": "Text", "variant": "body", "text": f"### {hdr_text}"},
        {"id": "c_hdr", "component": "Row", "gap": 6, "align": "center", "children": ["c_ico", "c_hdr_txt"]},
    ]
    row_ids: list[str] = []
    for idx, it in enumerate(items[:4]):
        t_id = f"ct_{idx}"
        s_id = f"cs_{idx}"
        col_id = f"cc_{idx}"
        b_id = f"cb_{idx}"
        r_id = f"cr_{idx}"
        comps.append({"id": t_id, "component": "Text", "variant": "body", "text": it["title"], "maxLines": 1})
        comps.append({"id": s_id, "component": "Text", "variant": "caption", "tone": "muted", "text": it["subtitle"], "maxLines": 1})
        comps.append({"id": col_id, "component": "Column", "gap": 1, "weight": 1.0, "children": [t_id, s_id]})
        if it.get("url"):
            act = {"functionCall": {"call": "openUrl", "args": {"url": it["url"]}}}
            blbl = "Mở" if vi else "Open"
            bico = "open_in_new"
        elif it.get("notePath"):
            act = {"functionCall": {"call": "clientAction", "args": {"action": "nexus_open_note", "target": it["notePath"], "value": ""}}}
            blbl = "Nexus"
            bico = "hub"
        else:
            act = {"functionCall": {"call": "openFile", "args": {"path": it["filePath"]}}}
            blbl = "Mở tệp" if vi else "Open"
            bico = "description"
        comps.extend(_btn_with_label(b_id, f"{b_id}_lbl", blbl, act, variant="ghost", icon_id=f"{b_id}_ico", icon_name=bico))
        comps.append({"id": r_id, "component": "Row", "gap": 6, "align": "center", "justify": "spaceBetween", "children": [col_id, b_id]})
        row_ids.append(r_id)

    comps.append({"id": "body_col", "component": "Column", "gap": 5, "children": ["c_hdr"] + row_ids})
    comps.append({"id": "root", "component": "Card", "tone": "subtle", "child": "body_col"})

    raw_surface = {
        "version": "1.0",
        "createSurface": {
            "surfaceId": sid,
            "catalogId": a2ui_validator.DEFAULT_CATALOG_ID,
            "sendDataModel": False,
            "surfaceMeta": {"kind": "citations", "interactive": False},
            "components": comps,
            "dataModel": {"count": len(items)},
        },
    }
    valid, _err = a2ui_validator.validate_and_sanitize_surface(raw_surface)
    return valid


def build_receipt_surface(result: dict[str, Any], *, lang: str = "en") -> dict[str, Any] | None:
    """Group 4: Test / Shell / Git / Diff Receipt Card with Meta-Chip deduplication."""
    intent_name = str(result.get("intent") or "")
    receipt_id = str(result.get("receiptId") or "").strip()
    added = int(result.get("addedLines") or 0)
    removed = int(result.get("removedLines") or 0)
    target_path = str(result.get("targetPath") or "").strip()
    status = str(result.get("status") or "completed").strip().lower()
    text = str(result.get("text") or "")
    vi = str(lang or "en").lower().startswith("vi")

    has_diff = bool(receipt_id or added > 0 or removed > 0)
    is_test_or_git = intent_name in ("git_task", "code_task", "shell_task") and any(
        k in text.lower() for k in ("exit ", "mã thoát", "git ", "passed", "failed", "diagnostics", "test execution", "chạy kiểm tra")
    )
    if not has_diff and not is_test_or_git:
        return None

    head_low = text.lower()[:160]
    has_nonzero_fail = bool(
        re.search(r"\b[1-9]\d*\s+(?:failed|fail|errors?)\b", head_low)
        or re.search(r"\b(?:exit|mã thoát)\s*(?:code\s*)?[1-9]\d*\b", head_low)
        or (
            "failed" in head_low
            and not re.search(r"\b0\s+failed\b", head_low)
            and not re.search(r"\bno\s+failed\b", head_low)
        )
    )
    ok_status = status == "completed" and not has_nonzero_fail
    icon_name = "check_circle" if ok_status else "error"
    tone = "success" if ok_status else "error"
    if has_diff:
        title = ("Biên lai chỉnh sửa tệp" if vi else "File Edit Receipt")
        summary = f"`{_short(target_path.rsplit('/', 1)[-1] if target_path else receipt_id, 48)}` · +{added} / -{removed}"
    elif intent_name == "git_task":
        title = "Biên lai Git" if vi else "Git Receipt"
        summary = _short(re.sub(r"[*_`#]+", "", _first_nonempty_line(text)), 84)
    else:
        title = "Kết quả chạy lệnh / kiểm tra" if vi else "Command / Test Receipt"
        summary = _short(re.sub(r"[*_`#]+", "", _first_nonempty_line(text)), 84)

    sid = _make_surface_id("rcpt", receipt_id or summary)
    comps: list[dict[str, Any]] = [
        {"id": "r_ico", "component": "Icon", "name": icon_name, "size": 15, "tone": "success" if ok_status else "error"},
        {"id": "r_title", "component": "Text", "variant": "body", "text": f"### {title}"},
        {"id": "r_hdr", "component": "Row", "gap": 6, "align": "center", "children": ["r_ico", "r_title"]},
        {"id": "r_sum", "component": "Text", "variant": "caption", "tone": "muted", "text": summary},
    ]
    btn_ids: list[str] = []
    if receipt_id:
        comps.extend(
            _btn_with_label(
                "btn_undo",
                "btn_undo_lbl",
                "Hoàn tác" if vi else "Undo",
                {"event": {"name": "rollback_receipt", "context": {"receiptId": receipt_id}}},
                variant="secondary",
                icon_id="btn_undo_ico",
                icon_name="undo",
                disabled={"path": "/rolledBack"},
            )
        )
        btn_ids.append("btn_undo")
    if target_path and not target_path.startswith(("http://", "https://")):
        comps.extend(
            _btn_with_label(
                "btn_open",
                "btn_open_lbl",
                "Mở Editor" if vi else "Open File",
                {"functionCall": {"call": "openFile", "args": {"path": target_path}}},
                variant="ghost",
                icon_id="btn_open_ico",
                icon_name="open_in_new",
            )
        )
        btn_ids.append("btn_open")
    if not has_diff and intent_name != "git_task" and ("test" in text.lower() or "kiểm tra" in text.lower()):
        comps.extend(
            _btn_with_label(
                "btn_rerun",
                "btn_rerun_lbl",
                "Chạy lại" if vi else "Re-run",
                {"event": {"name": "send_prompt", "context": {"prompt": "/tests"}}},
                variant="secondary",
                icon_id="btn_rerun_ico",
                icon_name="refresh",
            )
        )
        btn_ids.append("btn_rerun")

    children = ["r_hdr", "r_sum"]
    if btn_ids:
        comps.append({"id": "r_acts", "component": "Row", "gap": 6, "wrap": True, "children": btn_ids})
        children.append("r_acts")
    comps.append({"id": "body_col", "component": "Column", "gap": 5, "children": children})
    comps.append({"id": "root", "component": "Card", "tone": tone, "child": "body_col"})

    raw_surface = {
        "version": "1.0",
        "createSurface": {
            "surfaceId": sid,
            "catalogId": a2ui_validator.DEFAULT_CATALOG_ID,
            "sendDataModel": False,
            "surfaceMeta": {
                "kind": "receipt",
                "interactive": bool(receipt_id),
                "receiptId": receipt_id,
                "dedupeUndoChip": bool(receipt_id),
            },
            "components": comps,
            "dataModel": {
                "receiptId": receipt_id,
                "addedLines": added,
                "removedLines": removed,
                "rolledBack": False,
            },
        },
    }
    valid, _err = a2ui_validator.validate_and_sanitize_surface(raw_surface)
    return valid


def build_hybrid_surface(result: dict[str, Any], *, lang: str = "en") -> dict[str, Any] | None:
    """Group 5: Hybrid Stage 1 (UI Grounding) + Stage 2 (Deep Thinking) Summary Card."""
    hp = result.get("hybridPipeline")
    if not isinstance(hp, dict) or not hp.get("deepExecuted"):
        return None
    vi = str(lang or "en").lower().startswith("vi")
    ui_model = _short(hp.get("uiModel") or "UI/Vision", 36)
    think_model = _short(hp.get("thinkingModel") or "Deep Thinking", 36)
    effort = str(hp.get("thinkingEffort") or "medium")
    stage1_obs = _short(re.sub(r"[*_`#]+", "", str(result.get("stage1Observations") or "")), 180)
    sid = _make_surface_id("hybrid", f"{ui_model}:{think_model}")

    comps: list[dict[str, Any]] = [
        {"id": "h_ico", "component": "Icon", "name": "neurology", "size": 15, "tone": "accent"},
        {"id": "h_title", "component": "Text", "variant": "body", "text": f"### {'Phân tích 2 tầng (UI + Deep)' if vi else 'Hybrid 2-Stage Grounding'}"},
        {"id": "h_hdr", "component": "Row", "gap": 6, "align": "center", "children": ["h_ico", "h_title"]},
        {"id": "h_meta", "component": "Text", "variant": "caption", "tone": "accent", "text": f"Stage 1: {ui_model}  →  Stage 2: {think_model} ({effort})"},
    ]
    children = ["h_hdr", "h_meta"]
    if stage1_obs:
        comps.append({"id": "h_obs", "component": "Text", "variant": "caption", "tone": "muted", "text": stage1_obs, "maxLines": 3})
        children.append("h_obs")

    comps.append({"id": "body_col", "component": "Column", "gap": 5, "children": children})
    comps.append({"id": "root", "component": "Card", "tone": "subtle", "child": "body_col"})

    raw_surface = {
        "version": "1.0",
        "createSurface": {
            "surfaceId": sid,
            "catalogId": a2ui_validator.DEFAULT_CATALOG_ID,
            "sendDataModel": False,
            "surfaceMeta": {"kind": "hybrid", "interactive": False},
            "components": comps,
            "dataModel": {"uiModel": ui_model, "thinkingModel": think_model, "effort": effort},
        },
    }
    valid, _err = a2ui_validator.validate_and_sanitize_surface(raw_surface)
    return valid


def build_surface_for_turn(
    result: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Build the single best Tier 1 deterministic A2UI v1.0 surface for an agent turn result."""
    if not enabled or not isinstance(result, dict) or not result.get("handled"):
        return None
    lang = str(result.get("language") or "en")

    # Priority 1: Confirmation Card (interactive gate) always takes precedence over generative cards
    if result.get("requiresConfirmation") is True or result.get("requiresSudoPassword") is True:
        return build_confirmation_surface(result, lang=lang)

    # Preserve Tier 2 generative surface if the LLM already emitted a valid one on this turn
    if isinstance(result.get("uiSurface"), dict):
        cs_existing = result["uiSurface"].get("createSurface") if isinstance(result["uiSurface"].get("createSurface"), dict) else {}
        existing_meta = cs_existing.get("surfaceMeta") if isinstance(cs_existing.get("surfaceMeta"), dict) else {}
        is_gen = (
            existing_meta.get("kind") == "generative"
            or existing_meta.get("sourceKind") == "generative"
            or str(cs_existing.get("surfaceId") or "").startswith("surf_gen")
        )
        is_tainted = bool(result.get("taintSources"))
        valid_existing, _ = a2ui_validator.validate_and_sanitize_surface(
            result["uiSurface"],
            untrusted_generative=is_gen,
            tainted=(is_gen and is_tainted),
        )
        if valid_existing is not None:
            return valid_existing

    # Priority 2: Hybrid Stage 1+2 Summary Card
    if isinstance(result.get("hybridPipeline"), dict) and result["hybridPipeline"].get("deepExecuted"):
        return build_hybrid_surface(result, lang=lang)

    intent_name = str(result.get("intent") or "")

    # Priority 3: File Diff / Test / Shell / Git Receipt Card
    if (
        result.get("receiptId")
        or int(result.get("addedLines") or 0) > 0
        or int(result.get("removedLines") or 0) > 0
        or intent_name in ("git_task", "shell_task")
    ):
        rcpt = build_receipt_surface(result, lang=lang)
        if rcpt:
            return rcpt

    # Priority 4: Live Snapshot / Compound Task Answer Cards
    if intent_name in ("alis_local_info", "compound_task"):
        return build_snapshot_surface(result, snapshot=snapshot, lang=lang)

    # Priority 5: Web / RAG / Nexus Citations
    if intent_name in ("web_task", "rag_query", "nexus_knowledge"):
        cite = build_citations_surface(result, lang=lang)
        if cite:
            return cite

    # Priority 6: Code Task — Receipt (tests/diagnostics) or Interactive /plan Checklist
    if intent_name == "code_task":
        rcpt = build_receipt_surface(result, lang=lang)
        if rcpt:
            return rcpt
        plan = build_plan_surface(result, lang=lang)
        if plan:
            return plan

    return None
