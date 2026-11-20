#!/usr/bin/env python3
"""Alis Local Service Bridge: live desktop state formatter, Nexus knowledge formatter & smart control router."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata


SAFE_INSTANT_CLIENT_ACTIONS = frozenset({
    "weather_refresh",
    "music_toggle",
    "music_next",
    "music_prev",
    "music_shuffle",
    "music_stop",
    "music_play_query",
    "pomodoro_toggle",
    "pomodoro_start",
    "pomodoro_pause",
    "pomodoro_reset",
    "pomodoro_preset",
    "stopwatch_toggle",
    "stopwatch_reset",
    "night_light_set",
    "night_light_toggle",
    "gamemode_set",
    "gamemode_toggle",
    "dnd_set",
    "dnd_toggle",
    "nexus_open",
    "nexus_open_note",
    "nexus_open_daily",
})

WRITE_CONFIRM_CLIENT_ACTIONS = frozenset({
    "todo_add",
    "quicknote_add",
    "rag_note_save",
})


def _fold_ascii(text: str) -> str:
    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D").lower().strip()


def _snapshot_file() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    folder = (base / "alis-companion").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / "live_machine_snapshot.json"


def _encode_bounded_snapshot(snapshot: dict, max_bytes: int = 131072) -> bytes:
    def _dump(obj: dict) -> bytes:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    snap = dict(snapshot)
    if "capturedAtMs" not in snap or not isinstance(snap.get("capturedAtMs"), (int, float)):
        snap["capturedAtMs"] = int(time.time() * 1000)

    encoded = _dump(snap)
    if len(encoded) <= max_bytes:
        return encoded

    def _trim_obj(val, list_cap: int, str_cap: int):
        if isinstance(val, str):
            return val[:str_cap]
        if isinstance(val, list):
            return [_trim_obj(x, list_cap, str_cap) for x in val[:list_cap]]
        if isinstance(val, dict):
            return {k: _trim_obj(v, list_cap, str_cap) for k, v in list(val.items())[:40]}
        return val

    for list_cap, str_cap in ((10, 800), (4, 300), (2, 120)):
        pruned = {k: _trim_obj(v, list_cap, str_cap) for k, v in snap.items()}
        encoded = _dump(pruned)
        if len(encoded) <= max_bytes:
            return encoded

    essential = ("capturedAtMs", "weather", "system", "desktop")
    trimmed = {k: _trim_obj(snap[k], 2, 120) for k in essential if k in snap}
    for k, v in snap.items():
        if k in trimmed:
            continue
        cand = dict(trimmed)
        cand[k] = _trim_obj(v, 1, 80)
        cand_bytes = _dump(cand)
        if len(cand_bytes) <= max_bytes:
            trimmed = cand
    encoded = _dump(trimmed)
    if len(encoded) <= max_bytes:
        return encoded
    return _dump({"capturedAtMs": snap["capturedAtMs"], "truncated": True})


def save_live_snapshot(snapshot: dict) -> None:
    if not isinstance(snapshot, dict) or not snapshot:
        return
    path = _snapshot_file()
    payload = _encode_bounded_snapshot(snapshot, max_bytes=131072)
    fd, tmp_name = tempfile.mkstemp(prefix=".live-snap-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def load_live_snapshot() -> dict:
    path = _snapshot_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fmt_duration_sec(secs: int | float) -> str:
    s = max(0, int(secs or 0))
    h = s // 3600
    m = (s % 3600) // 60
    rem = s % 60
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {rem:02d}s"
    return f"{rem}s"


def _format_weather(w: dict, query: str, lang: str) -> tuple[str, dict]:
    if not isinstance(w, dict) or (not w.get("temp") and not w.get("city") and w.get("enabled") is False):
        msg = (
            "Dịch vụ Thời tiết của Alis hiện chưa bật hoặc chưa có dữ liệu. Bạn có thể bật trong Settings -> Bar -> Weather."
            if lang == "vi"
            else "Alis Weather is currently disabled or has no data yet. Enable it in Settings -> Bar -> Weather."
        )
        return msg, {"available": False}

    city = str(w.get("visibleCity") or w.get("city") or "").strip()
    if city.lower() in ("", "city", "unknown"):
        city = "vị trí hiện tại" if lang == "vi" else "current location"
    temp = str(w.get("temp") or "--")
    feels = str(w.get("tempFeelsLike") or "")
    desc = str(w.get("description") or "").strip()
    hum = str(w.get("humidity") or "")
    uv = str(w.get("uv") or "")
    wind = str(w.get("wind") or "")
    wdir = str(w.get("windDir") or "")
    precip = str(w.get("precip") or "")
    hi = str(w.get("tempMax") or "")
    lo = str(w.get("tempMin") or "")
    sunrise = str(w.get("sunrise") or "")
    sunset = str(w.get("sunset") or "")
    refreshed = str(w.get("lastRefresh") or "")
    aq = w.get("airQuality") if isinstance(w.get("airQuality"), dict) else {}
    forecast = w.get("forecast") if isinstance(w.get("forecast"), list) else []
    hourly = w.get("hourly") if isinstance(w.get("hourly"), list) else []

    q_fold = _fold_ascii(query)
    wants_forecast = any(k in q_fold for k in ("ngay mai", "tuan", "du bao", "forecast", "tomorrow", "week", "chieu", "toi", "gio"))
    wants_aqi = any(k in q_fold for k in ("aqi", "khong khi", "air quality", "bui min", "pm2", "pm10", "o nhiem"))

    lines: list[str] = []
    if lang == "vi":
        head = f"Thời tiết hiện tại tại **{city}**: **{temp}**"
        if feels and feels != temp:
            head += f" (cảm giác như {feels})"
        if desc:
            head += f" — {desc}"
        lines.append(head + ".")
        details = []
        if hi and lo:
            details.append(f"Dao động {lo} – {hi}")
        if hum:
            details.append(f"Độ ẩm {hum}")
        if wind:
            details.append(f"Gió {wind}" + (f" ({wdir})" if wdir else ""))
        if precip:
            details.append(f"Lượng mưa {precip}")
        if uv:
            details.append(f"UV {uv}")
        if details:
            lines.append("- " + " · ".join(details))
        if aq.get("available"):
            aq_line = f"- Chất lượng không khí: **{aq.get('scale', 'AQI')} {aq.get('aqi', '')}**"
            if aq.get("label"):
                aq_line += f" ({aq['label']})"
            if aq.get("pm25"):
                aq_line += f" · PM2.5: {aq['pm25']}"
            if aq.get("pm10") and wants_aqi:
                aq_line += f" · PM10: {aq['pm10']}"
            lines.append(aq_line)
        if hourly and (wants_forecast or "mua" in q_fold or "rain" in q_fold):
            hr_txt = ", ".join(f"{h.get('label', '')}: {h.get('temp', '')}" for h in hourly[:5] if isinstance(h, dict))
            if hr_txt:
                lines.append(f"- Theo giờ tới: {hr_txt}")
        if forecast and (wants_forecast or len(lines) < 4):
            fc_txt = " | ".join(
                f"{f.get('dayName', '')}: {f.get('lo', '')}–{f.get('hi', '')}"
                for f in forecast[:4]
                if isinstance(f, dict)
            )
            if fc_txt:
                lines.append(f"- Dự báo các ngày tới: {fc_txt}")
        if sunrise and sunset and not wants_aqi:
            lines.append(f"- Mặt trời mọc/lặn: {sunrise} / {sunset}" + (f" (cập nhật lúc {refreshed})" if refreshed and refreshed != "--:--" else ""))
    else:
        head = f"Current weather in **{city}**: **{temp}**"
        if feels and feels != temp:
            head += f" (feels like {feels})"
        if desc:
            head += f" — {desc}"
        lines.append(head + ".")
        details = []
        if hi and lo:
            details.append(f"Range {lo}–{hi}")
        if hum:
            details.append(f"Humidity {hum}")
        if wind:
            details.append(f"Wind {wind}" + (f" ({wdir})" if wdir else ""))
        if precip:
            details.append(f"Precip {precip}")
        if uv:
            details.append(f"UV {uv}")
        if details:
            lines.append("- " + " · ".join(details))
        if aq.get("available"):
            aq_line = f"- Air quality: **{aq.get('scale', 'AQI')} {aq.get('aqi', '')}**"
            if aq.get("label"):
                aq_line += f" ({aq['label']})"
            if aq.get("pm25"):
                aq_line += f" · PM2.5: {aq['pm25']}"
            lines.append(aq_line)
        if forecast and wants_forecast:
            fc_txt = " | ".join(
                f"{f.get('dayName', '')}: {f.get('lo', '')}–{f.get('hi', '')}"
                for f in forecast[:4]
                if isinstance(f, dict)
            )
            if fc_txt:
                lines.append(f"- Forecast: {fc_txt}")

    return "\n".join(lines), {"available": True, "city": city, "temp": temp, "aqi": aq.get("aqi", "")}


def _format_music(m: dict, lang: str) -> tuple[str, dict]:
    if not isinstance(m, dict):
        return ("Chưa đọc được trạng thái nhạc trên máy." if lang == "vi" else "Music service snapshot unavailable."), {}
    title = str(m.get("currentTitle") or m.get("mprisTitle") or "").strip()
    artist = str(m.get("currentArtist") or m.get("mprisArtist") or "").strip()
    album = str(m.get("currentAlbum") or "").strip()
    playing = bool(m.get("playing"))
    lib_cnt = int(m.get("libraryCount") or 0)
    vol = m.get("audioVolume")
    muted = bool(m.get("audioMuted"))
    pos_s = _fmt_duration_sec(m.get("positionSec") or 0)
    dur_s = _fmt_duration_sec(m.get("durationSec") or 0)
    vol_txt = f"{'Muted' if muted else f'{round(float(vol) * 100)}%'}" if isinstance(vol, (int, float)) else ""

    if not title:
        msg = (
            f"Hiện không có bài hát nào đang phát (Thư viện Local Music có **{lib_cnt}** bài"
            + (f", âm lượng {vol_txt}" if vol_txt else "")
            + "). Bạn có thể nói `phát bài <tên>` để mở nhạc."
            if lang == "vi"
            else f"No track is currently active (Local Music library has **{lib_cnt}** tracks"
            + (f", volume {vol_txt}" if vol_txt else "")
            + ")."
        )
        return msg, {"playing": False, "libraryCount": lib_cnt}

    state_lbl = ("Đang phát" if playing else "Đang tạm dừng") if lang == "vi" else ("Playing" if playing else "Paused")
    by = f" — {artist}" if artist else ""
    alb = f" (Album: {album})" if album else ""
    extra = [f"{pos_s} / {dur_s}"] if int(m.get("durationSec") or 0) > 0 else []
    if m.get("shuffleMode"):
        extra.append("Shuffle ON")
    if vol_txt:
        extra.append(f"{'Âm lượng' if lang == 'vi' else 'Vol'} {vol_txt}")
    if lib_cnt > 0:
        extra.append(f"{lib_cnt} {'bài trong thư viện' if lang == 'vi' else 'library tracks'}")
    msg = f"{state_lbl}: **{title}**{by}{alb}.\n- " + " · ".join(extra)
    return msg, {"playing": playing, "title": title, "artist": artist, "libraryCount": lib_cnt}


def _format_timers(t: dict, lang: str) -> tuple[str, dict]:
    if not isinstance(t, dict):
        return ("Chưa có dữ liệu Pomodoro/Timer." if lang == "vi" else "Timer data unavailable."), {}
    running = bool(t.get("pomodoroRunning"))
    paused = bool(t.get("pomodoroPaused"))
    on_break = bool(t.get("pomodoroBreak"))
    left = _fmt_duration_sec(t.get("pomodoroSecondsLeft") or 0)
    today_focus = _fmt_duration_sec(t.get("todayFocusSeconds") or 0)
    sessions = int(t.get("todaySessions") or 0)
    tag = str(t.get("activeTag") or "").strip()
    if lang == "vi":
        st = "Đang chạy" if running else ("Đang tạm dừng" if paused else "Đang chờ")
        mode = ("Nghỉ" if on_break else "Tập trung")
        lines = [
            f"Pomodoro ({mode}): **{st}**" + (f" · Còn lại **{left}**" if running or paused else ""),
            f"- Hôm nay bạn đã tập trung **{today_focus}** ({sessions} phiên)" + (f" · Nhãn: `{tag}`" if tag else ""),
        ]
    else:
        st = "Running" if running else ("Paused" if paused else "Idle")
        mode = "Break" if on_break else "Focus"
        lines = [
            f"Pomodoro ({mode}): **{st}**" + (f" · Remaining **{left}**" if running or paused else ""),
            f"- Today's focus: **{today_focus}** ({sessions} session(s))" + (f" · Tag: `{tag}`" if tag else ""),
        ]
    return "\n".join(lines), {"running": running, "todaySessions": sessions}


def _format_todos(td: dict, lang: str) -> tuple[str, dict]:
    if not isinstance(td, dict):
        return ("Chưa đọc được danh sách Todo." if lang == "vi" else "Todo list unavailable."), {}
    items = td.get("items") if isinstance(td.get("items"), list) else []
    open_cnt = int(td.get("openCount") or 0)
    done_cnt = int(td.get("doneCount") or 0)
    backend = str(td.get("backend") or "internal")
    if not items:
        msg = (
            f"Danh sách Todo (`{backend}`) hiện trống ({done_cnt} việc đã xong)."
            if lang == "vi"
            else f"Your `{backend}` Todo list has no open tasks ({done_cnt} done)."
        )
        return msg, {"openCount": open_cnt, "doneCount": done_cnt}
    lines = [
        f"Todo hiện có **{open_cnt}** việc chưa xong và **{done_cnt}** việc đã xong (nguồn `{backend}`):"
        if lang == "vi"
        else f"Todo (`{backend}`): **{open_cnt}** open, **{done_cnt}** done:"
    ]
    for it in items[:8]:
        if isinstance(it, dict):
            mark = "[x]" if it.get("done") else "[ ]"
            tm = f" ({it['timeStr']})" if it.get("timeStr") else ""
            lines.append(f"- `{mark}` {str(it.get('content') or '')[:120]}{tm}")
    return "\n".join(lines), {"openCount": open_cnt, "doneCount": done_cnt}


def _format_schedule(sched: dict, nx: dict, lang: str) -> tuple[str, dict]:
    lines: list[str] = []
    today_evs = (sched.get("todayEvents") or []) if isinstance(sched, dict) else []
    upcoming = (sched.get("upcomingEvents") or []) if isinstance(sched, dict) else []
    nx_sched = (nx.get("dailySchedule") or {}) if isinstance(nx, dict) else {}
    nx_rows = nx_sched.get("scheduleRows") if isinstance(nx_sched.get("scheduleRows"), list) else []

    if today_evs:
        lines.append("**Lịch trình hôm nay (Planner):**" if lang == "vi" else "**Today's Planner Events:**")
        for ev in today_evs[:8]:
            if isinstance(ev, dict):
                tstr = f"`{ev['timeStr']}` " if ev.get("timeStr") else ""
                lines.append(f"- {tstr}{ev.get('title', '')}")
    if nx_rows:
        wd = str(nx_sched.get("weekday") or "Hôm nay")
        lines.append(f"**Lịch cố định Nexus ({wd}):**" if lang == "vi" else f"**Nexus Daily Schedule ({wd}):**")
        for r in nx_rows[:8]:
            if isinstance(r, dict):
                lbl = str(r.get("title") or r.get("name") or r.get("raw") or r.get("time") or r)
                tm = str(r.get("time") or r.get("start") or "")
                lines.append(f"- {(f'`{tm}` ' if tm and tm not in lbl else '')}{lbl[:120]}")
    if upcoming and not today_evs:
        lines.append("**Sự kiện sắp tới:**" if lang == "vi" else "**Upcoming Events:**")
        for ev in upcoming[:6]:
            if isinstance(ev, dict):
                lines.append(f"- `{ev.get('dateStr', '')} {ev.get('timeStr', '')}` {ev.get('title', '')}")
    if not lines:
        msg = "Hôm nay chưa có sự kiện nào trong Planner hoặc lịch Nexus." if lang == "vi" else "No events scheduled for today."
        return msg, {"count": 0}
    return "\n".join(lines), {"todayCount": len(today_evs), "nexusRows": len(nx_rows)}


def _format_quicknotes(qn: dict, lang: str) -> tuple[str, dict]:
    tabs = qn.get("tabs") if isinstance(qn, dict) and isinstance(qn.get("tabs"), list) else []
    if not tabs:
        return ("QuickNotes hiện chưa có ghi chú nào." if lang == "vi" else "QuickNotes is currently empty."), {"tabCount": 0}
    lines = [
        f"Bạn đang có **{len(tabs)}** tab QuickNotes:"
        if lang == "vi"
        else f"You have **{len(tabs)}** QuickNotes tab(s):"
    ]
    for i, t in enumerate(tabs[:6]):
        if isinstance(t, dict):
            prev = str(t.get("textPreview") or "").replace("\n", " ").strip()[:100]
            lines.append(f"- Tab {i + 1} **{t.get('title') or 'Note'}**: {prev or '(trống)'}")
    return "\n".join(lines), {"tabCount": len(tabs)}


def _format_notifications(nf: dict, lang: str) -> tuple[str, dict]:
    if not isinstance(nf, dict):
        return ("Không có dữ liệu thông báo." if lang == "vi" else "Notification data unavailable."), {}
    unread = int(nf.get("unread") or 0)
    silent = bool(nf.get("silent"))
    recent = nf.get("recent") if isinstance(nf.get("recent"), list) else []
    if lang == "vi":
        lines = [f"Thông báo: **{unread}** chưa đọc · Chế độ Không làm phiền (DND): **{'Bật' if silent else 'Tắt'}**."]
    else:
        lines = [f"Notifications: **{unread}** unread · Do Not Disturb: **{'On' if silent else 'Off'}**."]
    for n in recent[:6]:
        if isinstance(n, dict):
            lines.append(f"- **{n.get('appName') or 'App'}**: {str(n.get('summary') or '')[:80]}" + (f" — {str(n.get('body') or '')[:80]}" if n.get("body") else ""))
    return "\n".join(lines), {"unread": unread, "silent": silent}


def _format_screentime(st: dict, lang: str) -> tuple[str, dict]:
    if not isinstance(st, dict) or st.get("enabled") is False:
        return ("ScreenTime hiện đang tắt hoặc chưa bật theo dõi." if lang == "vi" else "ScreenTime tracking is disabled."), {}
    cur_app = str(st.get("currentAppName") or st.get("currentAppId") or "")
    cur_sec = _fmt_duration_sec(st.get("currentSessionSeconds") or 0)
    tot_sec = _fmt_duration_sec(st.get("totalTodaySeconds") or 0)
    tops = st.get("topApps") if isinstance(st.get("topApps"), list) else []
    if lang == "vi":
        lines = [f"Tổng thời gian dùng máy hôm nay: **{tot_sec}**" + (f" · Đang dùng `{cur_app}` ({cur_sec})" if cur_app else "") + "."]
    else:
        lines = [f"Total screen time today: **{tot_sec}**" + (f" · Active `{cur_app}` ({cur_sec})" if cur_app else "") + "."]
    for a in tops[:5]:
        if isinstance(a, dict):
            lines.append(f"- `{a.get('name', '')}`: {_fmt_duration_sec(a.get('seconds') or 0)}")
    return "\n".join(lines), {"totalTodaySeconds": st.get("totalTodaySeconds") or 0}


def _format_learning(lr: dict, lang: str) -> tuple[str, dict]:
    if not isinstance(lr, dict) or not lr:
        return ("Plugin Spaced Repetition chưa bật hoặc chưa sẵn sàng." if lang == "vi" else "Spaced Repetition is unavailable."), {}
    due = int(lr.get("dueCount") or 0)
    rev = int(lr.get("todayReviewedCount") or 0)
    tot = int(lr.get("totalCount") or 0)
    streak = int(lr.get("streakDays") or 0)
    msg = (
        f"Spaced Repetition: Hôm nay còn **{due}** thẻ cần ôn (đã ôn **{rev}** thẻ, tổng {tot} thẻ, chuỗi **{streak} ngày**)."
        if lang == "vi"
        else f"Spaced Repetition: **{due}** cards due today (**{rev}** reviewed today, {tot} total, **{streak}-day** streak)."
    )
    return msg, {"dueCount": due, "todayReviewedCount": rev, "streakDays": streak}


def _format_fitness(fit: dict, nx: dict, lang: str) -> tuple[str, dict]:
    if not isinstance(fit, dict) or not fit:
        return ("Plugin Exercises Tracker chưa bật hoặc chưa có lịch tập hôm nay." if lang == "vi" else "Exercises Tracker is unavailable."), {}
    tot_ex = int(fit.get("totalExercises") or 0)
    done_ex = int(fit.get("completedExercises") or 0)
    done_sets = int(fit.get("completedSets") or 0)
    tot_sets = int(fit.get("totalSets") or 0)
    routine = str(fit.get("routine") or "").strip()
    nx_ex = ((nx.get("dailySchedule") or {}).get("exerciseRows") or []) if isinstance(nx, dict) else []
    lines = [
        (
            f"Tập luyện hôm nay ({fit.get('todayStr', '')}): đã hoàn thành **{done_ex}/{tot_ex}** bài ({done_sets}/{tot_sets} hiệp)"
            + (f" · Lịch: `{routine}`" if routine else "")
            if lang == "vi"
            else f"Today's workouts ({fit.get('todayStr', '')}): **{done_ex}/{tot_ex}** exercises ({done_sets}/{tot_sets} sets)"
            + (f" · Routine: `{routine}`" if routine else "")
        )
    ]
    for er in nx_ex[:5]:
        lines.append(f"- Nexus workout: {str(er)[:120]}")
    return "\n".join(lines), {"completedExercises": done_ex, "totalExercises": tot_ex}


def _scale_pct(raw_val, decimals: int = 1) -> float:
    try:
        val = float(raw_val or 0)
    except (TypeError, ValueError):
        return 0.0
    scaled = val * 100.0 if 0.0 < val < 1.0 else val
    return round(scaled, decimals)


def _format_system(sys_d: dict, desk: dict, lang: str) -> tuple[str, dict]:
    if not isinstance(sys_d, dict) or not sys_d:
        try:
            import desktop_control
            tel = desktop_control.get_system_telemetry()
            return "\n".join(tel.get("lines") or [tel.get("summary") or "System telemetry ready."]), tel.get("telemetry") or {}
        except Exception:
            return ("Chưa đọc được thông số hệ thống." if lang == "vi" else "System telemetry unavailable."), {}

    lines: list[str] = []
    cpu_val = _scale_pct(sys_d.get("cpuPercent"), 1)
    gpu_val = _scale_pct(sys_d.get("gpuPercent"), 1)
    cpu_t = sys_d.get("cpuTemp")
    gpu_t = sys_d.get("gpuTemp")
    mem_u = sys_d.get("memUsedGb")
    mem_tot = sys_d.get("memTotalGb")
    mem_p = sys_d.get("memPercent")
    bat_p = sys_d.get("batteryPercent")
    if lang == "vi":
        lines.append(
            f"**Tài nguyên:** CPU {cpu_val}%"
            + (f" ({cpu_t}°C)" if cpu_t else "")
            + f" · GPU {gpu_val}%"
            + (f" ({gpu_t}°C)" if gpu_t else "")
            + (f" · RAM {mem_u}/{mem_tot} GB ({mem_p}%)" if mem_tot else "")
        )
        hw_parts = []
        if sys_d.get("batteryAvailable"):
            hw_parts.append(f"Pin **{bat_p}%** ({'Đang sạc' if sys_d.get('batteryCharging') else 'Dùng pin'})")
        if sys_d.get("thinkfanAvailable"):
            rpm = sys_d.get("thinkfanRpm")
            prof = sys_d.get("thinkfanProfile") or "auto"
            hw_parts.append(f"Quạt Thinkfan {'bật' if sys_d.get('thinkfanActive') else 'tắt'} ({rpm} RPM, {prof})")
        if sys_d.get("tlpAvailable"):
            lim = sys_d.get("tlpLimit")
            st = sys_d.get("tlpStart")
            hw_parts.append(f"TLP giới hạn sạc {st if st is not None and int(st) >= 0 else '?'}–{lim}%")
        if hw_parts:
            lines.append("- **Phần cứng:** " + " · ".join(hw_parts))
        net_parts = []
        if sys_d.get("networkName") or sys_d.get("networkType"):
            net_parts.append(f"Mạng {sys_d.get('networkName') or sys_d.get('networkType')}")
        if sys_d.get("bluetoothConnectedCount"):
            devs = ", ".join(sys_d.get("bluetoothDeviceNames") or [])
            net_parts.append(f"Bluetooth {sys_d['bluetoothConnectedCount']} thiết bị" + (f" ({devs})" if devs else ""))
        if sys_d.get("nightLightActive") is not None:
            net_parts.append(f"Night Light {'Bật' if sys_d.get('nightLightActive') else 'Tắt'}")
        if sys_d.get("gameModeActive"):
            net_parts.append("GameMode Đang bật")
        if sys_d.get("updatesCount"):
            net_parts.append(f"Có {sys_d['updatesCount']} gói hệ thống có thể cập nhật")
        if int(sys_d.get("shellCommitsBehind") or 0) > 0:
            net_parts.append(f"Alis mới hơn {sys_d['shellCommitsBehind']} commit")
        if net_parts:
            lines.append("- **Kết nối & Trạng thái:** " + " · ".join(net_parts))
        if isinstance(desk, dict) and (desk.get("focusedApp") or desk.get("activeWorkspace")):
            lines.append(f"- **Niri Desktop:** Màn hình `{desk.get('currentOutput', '')}`, cửa sổ `{desk.get('focusedApp', '')}` ({str(desk.get('focusedTitle') or '')[:60]})")
    else:
        lines.append(
            f"**Resources:** CPU {cpu_val}%"
            + (f" ({cpu_t}°C)" if cpu_t else "")
            + f" · GPU {gpu_val}%"
            + (f" ({gpu_t}°C)" if gpu_t else "")
            + (f" · RAM {mem_u}/{mem_tot} GB ({mem_p}%)" if mem_tot else "")
        )
        hw_parts = []
        if sys_d.get("batteryAvailable"):
            hw_parts.append(f"Battery **{bat_p}%** ({'Charging' if sys_d.get('batteryCharging') else 'On battery'})")
        if sys_d.get("thinkfanAvailable"):
            rpm = sys_d.get("thinkfanRpm")
            prof = sys_d.get("thinkfanProfile") or "auto"
            hw_parts.append(f"Thinkfan {'on' if sys_d.get('thinkfanActive') else 'off'} ({rpm} RPM, {prof})")
        if sys_d.get("tlpAvailable"):
            lim = sys_d.get("tlpLimit")
            st = sys_d.get("tlpStart")
            hw_parts.append(f"TLP charge limit {st if st is not None and int(st) >= 0 else '?'}–{lim}%")
        if hw_parts:
            lines.append("- **Hardware:** " + " · ".join(hw_parts))
        net_parts = []
        if sys_d.get("networkName") or sys_d.get("networkType"):
            net_parts.append(f"Network {sys_d.get('networkName') or sys_d.get('networkType')}")
        if sys_d.get("bluetoothConnectedCount"):
            devs = ", ".join(sys_d.get("bluetoothDeviceNames") or [])
            net_parts.append(f"Bluetooth {sys_d['bluetoothConnectedCount']} device(s)" + (f" ({devs})" if devs else ""))
        if sys_d.get("nightLightActive") is not None:
            net_parts.append(f"Night Light {'On' if sys_d.get('nightLightActive') else 'Off'}")
        if sys_d.get("gameModeActive"):
            net_parts.append("GameMode Active")
        if sys_d.get("updatesCount"):
            net_parts.append(f"{sys_d['updatesCount']} system package update(s) available")
        if int(sys_d.get("shellCommitsBehind") or 0) > 0:
            net_parts.append(f"Alis {sys_d['shellCommitsBehind']} commit(s) ahead")
        if net_parts:
            lines.append("- **Connectivity & Status:** " + " · ".join(net_parts))
        if isinstance(desk, dict) and (desk.get("focusedApp") or desk.get("activeWorkspace")):
            lines.append(f"- **Niri Desktop:** Output `{desk.get('currentOutput', '')}`, window `{desk.get('focusedApp', '')}` ({str(desk.get('focusedTitle') or '')[:60]})")
    return "\n".join(lines), sys_d


def _spoken_num_deg(val: str, deg_word: str) -> str:
    s = str(val or "").strip()
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*°?C?$", s, re.IGNORECASE)
    return f"{m.group(1)} {deg_word}" if m else s


def _spoken_weather(w: dict, query: str, lang: str) -> str:
    if not isinstance(w, dict) or (not w.get("temp") and not w.get("city") and w.get("enabled") is False):
        return (
            "Dịch vụ Thời tiết của Alis hiện chưa bật hoặc chưa có dữ liệu."
            if lang == "vi"
            else "Alis Weather is currently disabled or has no data yet."
        )
    city = str(w.get("visibleCity") or w.get("city") or "").strip()
    if city.lower() in ("", "city", "unknown"):
        city = "khu vực hiện tại" if lang == "vi" else "your location"
    else:
        city = re.sub(r",\s*(?:Vietnam|Viet Nam)$", "", city, flags=re.IGNORECASE).strip()
    temp = str(w.get("temp") or "").strip()
    feels = str(w.get("tempFeelsLike") or "").strip()
    desc = str(w.get("description") or "").strip()
    hum = str(w.get("humidity") or "").strip().replace("%", " percent" if lang != "vi" else " phần trăm")
    wind = str(w.get("wind") or "").strip()
    wind = re.sub(r"\bkm/h\b", "kilômét trên giờ" if lang == "vi" else "kilometers per hour", wind, flags=re.IGNORECASE)
    aq = w.get("airQuality") if isinstance(w.get("airQuality"), dict) else {}
    forecast = w.get("forecast") if isinstance(w.get("forecast"), list) else []
    wants_forecast = any(k in _fold_ascii(query) for k in ("ngay mai", "tuan", "du bao", "forecast", "tomorrow", "week"))

    deg = "độ" if lang == "vi" else "degrees"
    t_sp = _spoken_num_deg(temp, deg)
    f_sp = _spoken_num_deg(feels, deg)
    if lang == "vi":
        parts = [f"Tại {city} hiện đang {t_sp}"]
        if feels and feels != temp:
            parts[0] += f", cảm giác như {f_sp}"
        if desc:
            parts[0] += f" và {desc.lower()}"
        parts[0] += "."
        extras = []
        if hum:
            extras.append(f"độ ẩm {hum}")
        if wind:
            extras.append(f"gió {wind}")
        if aq.get("available") and aq.get("label"):
            extras.append(f"chất lượng không khí ở mức {str(aq['label']).lower()}")
        if extras:
            parts.append(", ".join(extras).capitalize() + ".")
        if wants_forecast and forecast and isinstance(forecast[0], dict):
            f0 = forecast[0]
            parts.append(
                f"Dự báo ngày {f0.get('dayName', 'tới')} dao động từ {_spoken_num_deg(f0.get('lo', ''), deg)} đến {_spoken_num_deg(f0.get('hi', ''), deg)}."
            )
        return " ".join(parts)

    parts = [f"Current weather in {city} is {t_sp}"]
    if feels and feels != temp:
        parts[0] += f", feeling like {f_sp}"
    if desc:
        parts[0] += f", with {desc.lower()}"
    parts[0] += "."
    extras = []
    if hum:
        extras.append(f"humidity is {hum}")
    if wind:
        extras.append(f"wind is {wind}")
    if aq.get("available") and aq.get("label"):
        extras.append(f"air quality is {str(aq['label']).lower()}")
    if extras:
        sentence = ", ".join(extras)
        parts.append(sentence[0].upper() + sentence[1:] + ".")
    if wants_forecast and forecast and isinstance(forecast[0], dict):
        f0 = forecast[0]
        parts.append(
            f"For {f0.get('dayName', 'next day')}, expect {_spoken_num_deg(f0.get('lo', ''), deg)} to {_spoken_num_deg(f0.get('hi', ''), deg)}."
        )
    return " ".join(parts)


def _spoken_music(m: dict, lang: str) -> str:
    if not isinstance(m, dict):
        return "Chưa đọc được trạng thái nhạc trên máy." if lang == "vi" else "Music status is unavailable."
    title = str(m.get("currentTitle") or m.get("mprisTitle") or "").strip()
    artist = str(m.get("currentArtist") or m.get("mprisArtist") or "").strip()
    playing = bool(m.get("playing"))
    if not title:
        return "Hiện không có bài hát nào đang phát." if lang == "vi" else "No music is playing right now."
    by = (f" của {artist}" if lang == "vi" else f" by {artist}") if artist else ""
    if lang == "vi":
        return f"{'Đang phát' if playing else 'Đang tạm dừng'} bài {title}{by}."
    return f"{'Now playing' if playing else 'Paused'} {title}{by}."


def _spoken_timers(t: dict, lang: str) -> str:
    if not isinstance(t, dict):
        return "Chưa có dữ liệu Pomodoro." if lang == "vi" else "Timer data is unavailable."
    running = bool(t.get("pomodoroRunning"))
    paused = bool(t.get("pomodoroPaused"))
    left = _fmt_duration_sec(t.get("pomodoroSecondsLeft") or 0)
    today_focus = _fmt_duration_sec(t.get("todayFocusSeconds") or 0)
    if lang == "vi":
        if running or paused:
            return f"Pomodoro đang {'chạy' if running else 'tạm dừng'}, còn lại {left}. Hôm nay bạn đã tập trung {today_focus}."
        return f"Pomodoro đang nghỉ. Hôm nay bạn đã tập trung {today_focus}."
    if running or paused:
        return f"Pomodoro is {'running' if running else 'paused'} with {left} left. You've focused for {today_focus} today."
    return f"Pomodoro is idle. You've focused for {today_focus} today."


def _spoken_todos(td: dict, lang: str) -> str:
    if not isinstance(td, dict):
        return "Chưa đọc được danh sách Todo." if lang == "vi" else "Todo list is unavailable."
    items = td.get("items") if isinstance(td.get("items"), list) else []
    open_cnt = int(td.get("openCount") or 0)
    done_cnt = int(td.get("doneCount") or 0)
    open_names = [str(it.get("content") or "").strip() for it in items if isinstance(it, dict) and not it.get("done")][:3]
    open_names = [n for n in open_names if n]
    if open_cnt == 0:
        return (
            f"Bạn không còn việc nào đang mở, đã hoàn thành {done_cnt} việc."
            if lang == "vi"
            else f"You have no open tasks left, with {done_cnt} completed."
        )
    sample = ", ".join(open_names)
    if lang == "vi":
        return f"Bạn có {open_cnt} việc đang mở" + (f", gồm: {sample}." if sample else ".")
    return f"You have {open_cnt} open task" + ("s" if open_cnt != 1 else "") + (f", including {sample}." if sample else ".")


def _spoken_schedule(sched: dict, nx: dict, lang: str) -> str:
    today_evs = (sched.get("todayEvents") or []) if isinstance(sched, dict) else []
    nx_rows = ((nx.get("dailySchedule") or {}).get("scheduleRows") or []) if isinstance(nx, dict) else []
    titles = []
    for ev in today_evs[:3]:
        if isinstance(ev, dict) and ev.get("title"):
            titles.append(f"{ev.get('timeStr', '')} {ev['title']}".strip())
    for r in nx_rows[:3]:
        if isinstance(r, dict):
            lbl = str(r.get("title") or r.get("name") or r.get("raw") or "").strip()
            tm = str(r.get("time") or r.get("start") or "").strip()
            if lbl:
                titles.append(f"{tm} {lbl}".strip() if tm and tm not in lbl else lbl)
    if not titles:
        return "Hôm nay bạn chưa có sự kiện nào trên lịch." if lang == "vi" else "You have no events scheduled today."
    joined = ", ".join(titles[:3])
    return (f"Lịch hôm nay có: {joined}." if lang == "vi" else f"On today's schedule: {joined}.")


def _spoken_system(sys_d: dict, desk: dict, lang: str) -> str:
    if not isinstance(sys_d, dict) or not sys_d:
        return "Chưa đọc được thông số hệ thống." if lang == "vi" else "System status is unavailable."
    cpu_val = _scale_pct(sys_d.get("cpuPercent"), 0)
    mem_u = sys_d.get("memUsedGb")
    mem_tot = sys_d.get("memTotalGb")
    bat_p = sys_d.get("batteryPercent")
    if lang == "vi":
        parts = [f"CPU đang ở mức {int(cpu_val)} phần trăm"]
        if mem_tot:
            parts.append(f"RAM dùng {mem_u} trên {mem_tot} gigabyte")
        if sys_d.get("batteryAvailable") and bat_p is not None:
            parts.append(f"pin {bat_p} phần trăm")
        return ", ".join(parts) + "."
    parts = [f"CPU usage is {int(cpu_val)} percent"]
    if mem_tot:
        parts.append(f"memory is {mem_u} of {mem_tot} gigabytes")
    if sys_d.get("batteryAvailable") and bat_p is not None:
        parts.append(f"battery is at {bat_p} percent")
    return ", ".join(parts) + "."


def format_live_state(snapshot: dict | None, topic: str = "all", query: str = "", lang: str = "vi") -> dict:
    """Format live Alis + Integration + Nexus state cleanly for visual display and natural spoken delivery."""
    snap = snapshot if isinstance(snapshot, dict) and snapshot else load_live_snapshot()
    t = str(topic or "all").strip().lower()
    spoken = ""
    if t == "weather":
        md, data = _format_weather(snap.get("weather") or {}, query, lang)
        spoken = _spoken_weather(snap.get("weather") or {}, query, lang)
    elif t == "music":
        md, data = _format_music(snap.get("music") or {}, lang)
        spoken = _spoken_music(snap.get("music") or {}, lang)
    elif t == "timers":
        md, data = _format_timers(snap.get("timers") or {}, lang)
        spoken = _spoken_timers(snap.get("timers") or {}, lang)
    elif t == "todos":
        md, data = _format_todos(snap.get("todos") or {}, lang)
        spoken = _spoken_todos(snap.get("todos") or {}, lang)
    elif t == "schedule":
        md, data = _format_schedule(snap.get("schedule") or {}, snap.get("nexus") or {}, lang)
        spoken = _spoken_schedule(snap.get("schedule") or {}, snap.get("nexus") or {}, lang)
    elif t == "quicknotes":
        md, data = _format_quicknotes(snap.get("quicknotes") or {}, lang)
        spoken = md
    elif t == "notifications":
        md, data = _format_notifications(snap.get("notifications") or {}, lang)
        spoken = md
    elif t == "screentime":
        md, data = _format_screentime(snap.get("screentime") or {}, lang)
        spoken = md
    elif t == "learning":
        md, data = _format_learning(snap.get("learning") or {}, lang)
        spoken = md
    elif t == "fitness":
        md, data = _format_fitness(snap.get("fitness") or {}, snap.get("nexus") or {}, lang)
        spoken = md
    elif t in ("system", "desktop"):
        md, data = _format_system(snap.get("system") or {}, snap.get("desktop") or {}, lang)
        spoken = _spoken_system(snap.get("system") or {}, snap.get("desktop") or {}, lang)
    else:
        sections = [
            _format_weather(snap.get("weather") or {}, query, lang)[0],
            _format_music(snap.get("music") or {}, lang)[0],
            _format_timers(snap.get("timers") or {}, lang)[0],
            _format_todos(snap.get("todos") or {}, lang)[0],
            _format_system(snap.get("system") or {}, snap.get("desktop") or {}, lang)[0],
        ]
        md = "\n\n".join(s for s in sections if s)
        spoken = " ".join([
            _spoken_weather(snap.get("weather") or {}, query, lang),
            _spoken_music(snap.get("music") or {}, lang),
            _spoken_timers(snap.get("timers") or {}, lang),
            _spoken_todos(snap.get("todos") or {}, lang),
        ])
        data = {"topics": ["weather", "music", "timers", "todos", "system"]}
    return {"topic": t, "markdown": md, "spoken": spoken or md, "data": data}


def format_nexus_reply(action: str, data: dict, query: str = "", lang: str = "vi") -> str:
    """Format Nexus Vault search citations, note content, overview, journal, finance & collections."""
    act = str(action or "search").strip().lower()
    if act == "read":
        title = data.get("title") or data.get("relPath") or ""
        rel = data.get("relPath") or ""
        tags = ", ".join(f"#{t}" for t in (data.get("tags") or [])[:6])
        hdr = f"### `{rel}` — **{title}**" + (f" ({tags})" if tags else "")
        return f"{hdr}\n\n{str(data.get('content') or '')[:3200]}"

    if act == "journal":
        j = data.get("todayJournal") if isinstance(data.get("todayJournal"), dict) else {}
        if not j or not j.get("exists"):
            return "Hôm nay chưa có trang Daily Journal trong Nexus." if lang == "vi" else "No Daily Journal note exists in Nexus for today."
        parts = []
        if j.get("mood"):
            parts.append(f"Mood: **{j['mood']}**")
        if j.get("energy"):
            parts.append(f"Energy: **{j['energy']}**")
        if j.get("weight"):
            parts.append(f"Weight: **{j['weight']}**")
        return (
            f"Nhật ký hôm nay (`{j.get('relPath', '')}`): " + (", ".join(parts) or "đã tạo, chưa điền mood/energy.")
            if lang == "vi"
            else f"Today's journal (`{j.get('relPath', '')}`): " + (", ".join(parts) or "exists, no mood/energy filled yet.")
        )

    if act == "finance":
        fin = data.get("financeSummary") if isinstance(data.get("financeSummary"), dict) else {}
        if not fin or not fin.get("available"):
            return "Chưa tìm thấy dữ liệu tài chính trong cache Nexus." if lang == "vi" else "No Nexus finance summary available."
        return (
            f"Tổng hợp tài chính trong Nexus: Thu **{fin.get('totalIncome', 0)}**, Chi **{fin.get('totalExpense', fin.get('totalSpent', 0))}**, Số dư **{fin.get('closingBalance', fin.get('balance', 0))}**."
            if lang == "vi"
            else f"Nexus Finance Summary: Income **{fin.get('totalIncome', 0)}**, Expense **{fin.get('totalExpense', fin.get('totalSpent', 0))}**, Balance **{fin.get('closingBalance', fin.get('balance', 0))}**."
        )

    if act == "collections":
        counts = data.get("collectionCounts") or {}
        samples = data.get("collectionSamples") or {}
        q_fold = _fold_ascii(query)
        target_cols = [c for c in counts if c.lower() in q_fold]
        if not target_cols:
            target_cols = list(counts.keys())[:6]
        lines = ["**Bộ sưu tập trong Nexus:**" if lang == "vi" else "**Nexus Collections:**"]
        for c in target_cols:
            sm = ", ".join(s.get("title", "") for s in (samples.get(c) or [])[:4] if s.get("title"))
            lines.append(f"- `{c}` ({counts.get(c, 0)} mục)" + (f": {sm}" if sm else ""))
        return "\n".join(lines)

    if act == "overview":
        st = data.get("stats") or {}
        lines = [
            (
                f"**Nexus Knowledge Hub** (`{data.get('vaultPath', '')}`): **{st.get('noteCount', 0)}** ghi chú, "
                f"**{st.get('openTasks', 0)}** việc mở, thư mục RAG `{data.get('ragDir', '')}`."
                if lang == "vi"
                else f"**Nexus Knowledge Hub** (`{data.get('vaultPath', '')}`): **{st.get('noteCount', 0)}** notes, "
                f"**{st.get('openTasks', 0)}** open tasks, RAG folder `{data.get('ragDir', '')}`."
            )
        ]
        recent = data.get("recentNotes") or []
        if recent:
            lines.append("- Ghi chú gần đây: " + ", ".join(f"`{n.get('title') or n.get('relPath')}`" for n in recent[:5]))
        return "\n".join(lines)

    # Default: search results
    cites = data.get("citations") or []
    cols = data.get("matchedCollections") or []
    lines: list[str] = []
    if cites:
        lines.append(
            f"Tìm thấy **{len(cites)}** trích đoạn liên quan trong Nexus Vault:"
            if lang == "vi"
            else f"Found **{len(cites)}** matching Nexus Vault passages:"
        )
        for c in cites[:4]:
            snip = str(c.get("snippet") or "").replace("\n", " ").strip()[:240]
            lines.append(f"- `[Nexus: {c.get('relPath', '')} > {c.get('heading', '')}]` {snip}")
    if cols:
        col_txt = ", ".join(f"`{m['collection']}:{m['title']}`" for m in cols[:4])
        lines.append(("- Bộ sưu tập khớp: " if lang == "vi" else "- Matching collection entries: ") + col_txt)
    if not lines:
        return (
            f"Không tìm thấy ghi chú hoặc dữ liệu Nexus khớp với `{query}`."
            if lang == "vi"
            else f"No Nexus notes or collection entries matched `{query}`."
        )
    return "\n".join(lines)


def _extract_after_folded_regex(raw: str, pattern: str) -> str | None:
    """Match pattern containing group(1) cleanly on diacritic-folded text while preserving Unicode slice."""
    orig = unicodedata.normalize("NFC", str(raw or ""))
    fold_chars: list[str] = []
    idx_map: list[int] = []
    for i, ch in enumerate(orig):
        nfd = unicodedata.normalize("NFD", ch)
        for sub in nfd:
            if unicodedata.category(sub) != "Mn":
                fold_chars.append("d" if sub in ("đ", "Đ") else sub.lower())
                idx_map.append(i)
    m = re.search(pattern, "".join(fold_chars))
    if not m:
        return None
    s_idx = m.start(1)
    if s_idx >= len(idx_map):
        return ""
    return orig[idx_map[s_idx] :].strip()


def detect_alis_local_intent(prompt: str) -> dict | None:
    """Detect natural-language or slash intents for live Alis services & Nexus knowledge."""
    raw = str(prompt or "").strip()
    if not raw:
        return None
    folded = _fold_ascii(raw)
    has_code_ext = bool(re.search(r"\b[a-z0-9_.\-]+\.(?:qml|py|js|rs|ts|json|kdl|frag|service|sh)\b", folded))

    # 1. Explicit slash commands
    if re.match(r"^/weather\b", folded):
        if any(w in folded for w in ("refresh", "update", "cap nhat", "lam moi")):
            return {"kind": "alis_service_control", "action": "weather_refresh", "target": "", "value": "", "requiresConfirmation": False}
        return {"kind": "alis_local_info", "topic": "weather"}

    if re.match(r"^/pomodoro\b", folded):
        if re.search(r"\b(?:start|run|bat|chay|resume|tiep tuc)\b", folded):
            return {"kind": "alis_service_control", "action": "pomodoro_start", "target": "", "value": "", "requiresConfirmation": False}
        if re.search(r"\b(?:pause|tam dung)\b", folded):
            return {"kind": "alis_service_control", "action": "pomodoro_pause", "target": "", "value": "", "requiresConfirmation": False}
        if re.search(r"\b(?:reset|stop|dung|tat|dat lai)\b", folded):
            return {"kind": "alis_service_control", "action": "pomodoro_reset", "target": "", "value": "", "requiresConfirmation": False}
        if re.search(r"\btoggle\b", folded):
            return {"kind": "alis_service_control", "action": "pomodoro_toggle", "target": "", "value": "", "requiresConfirmation": False}
        return {"kind": "alis_local_info", "topic": "timers"}

    if re.match(r"^/music\b", folded):
        arg = re.sub(r"^/music\s*", "", raw, flags=re.I).strip()
        arg_f = _fold_ascii(arg)
        if not arg or arg_f in ("status", "info", "now"):
            return {"kind": "alis_local_info", "topic": "music"}
        if re.match(r"^(?:toggle|play/pause)\b", arg_f):
            return {"kind": "alis_service_control", "action": "music_toggle", "target": "", "value": "", "requiresConfirmation": False}
        if re.match(r"^(?:pause|resume|tam dung|tiep tuc)\b", arg_f):
            return {"kind": "alis_service_control", "action": "music_toggle", "target": "", "value": "", "requiresConfirmation": False}
        if re.match(r"^(?:next|skip|qua bai)\b", arg_f):
            return {"kind": "alis_service_control", "action": "music_next", "target": "", "value": "", "requiresConfirmation": False}
        if re.match(r"^(?:prev|previous|bai truoc)\b", arg_f):
            return {"kind": "alis_service_control", "action": "music_prev", "target": "", "value": "", "requiresConfirmation": False}
        if re.match(r"^(?:shuffle|tron)\b", arg_f):
            return {"kind": "alis_service_control", "action": "music_shuffle", "target": "", "value": "", "requiresConfirmation": False}
        if re.match(r"^(?:stop|dung)\b", arg_f):
            return {"kind": "alis_service_control", "action": "music_stop", "target": "", "value": "", "requiresConfirmation": False}
        q_song = re.sub(r"^(?:play|phat|mo|nghe)\s+", "", arg, flags=re.I).strip()
        return {"kind": "alis_service_control", "action": "music_play_query", "target": q_song or arg, "value": "", "requiresConfirmation": False}

    if re.match(r"^/todo\b", folded):
        arg = re.sub(r"^/todo\s*", "", raw, flags=re.I).strip()
        if not arg or _fold_ascii(arg) in ("list", "show", "xem"):
            return {"kind": "alis_local_info", "topic": "todos"}
        task_txt = re.sub(r"^(?:add|create|them|tao)\s+", "", arg, flags=re.I).strip() or arg
        return {"kind": "alis_service_control", "action": "todo_add", "target": task_txt, "value": task_txt, "requiresConfirmation": True}

    if re.match(r"^/quicknote\b", folded):
        arg = re.sub(r"^/quicknote\s*", "", raw, flags=re.I).strip()
        if not arg or _fold_ascii(arg) in ("list", "show", "xem"):
            return {"kind": "alis_local_info", "topic": "quicknotes"}
        note_txt = re.sub(r"^(?:add|create|them|tao|ghi)\s+", "", arg, flags=re.I).strip() or arg
        if ":" in note_txt:
            t_part, b_part = note_txt.split(":", 1)
            return {"kind": "alis_service_control", "action": "quicknote_add", "target": t_part.strip() or "Companion Note", "value": b_part.strip(), "requiresConfirmation": True}
        return {"kind": "alis_service_control", "action": "quicknote_add", "target": "Companion Note", "value": note_txt, "requiresConfirmation": True}

    if re.match(r"^/nexus\b", folded):
        arg = re.sub(r"^/nexus\s*", "", raw, flags=re.I).strip()
        arg_f = _fold_ascii(arg)
        if not arg or arg_f in ("overview", "stats", "status", "tong quan"):
            return {"kind": "nexus_knowledge", "subkind": "overview", "query": arg}
        if arg_f in ("open", "mo"):
            return {"kind": "alis_service_control", "action": "nexus_open", "target": "", "value": "", "requiresConfirmation": False}
        if arg_f in ("daily", "journal", "nhat ky"):
            return {"kind": "nexus_knowledge", "subkind": "journal", "query": arg}
        if re.match(r"^(?:read|doc|xem)\s+", arg_f):
            q_note = re.sub(r"^(?:read|doc|đọc|xem)\s+", "", arg, flags=re.I).strip()
            return {"kind": "nexus_knowledge", "subkind": "read", "query": q_note}
        if re.match(r"^(?:save|luu)\s+", arg_f):
            body = re.sub(r"^(?:save|luu|lưu)\s+", "", arg, flags=re.I).strip()
            return {"kind": "nexus_knowledge", "subkind": "save_rag_note", "query": body}
        q_clean = re.sub(r"^(?:search|tim|tìm|query)\s+", "", arg, flags=re.I).strip()
        return {"kind": "nexus_knowledge", "subkind": "search", "query": q_clean or arg}

    has_explicit_nexus_scope = bool(
        re.search(r"(?:trong\s+(?:nexus|obsidian|vault|ghi chu cua toi)|cua nexus|in\s+(?:my\s+)?(?:nexus|vault|notes))\b", folded)
    )
    if has_code_ext and not has_explicit_nexus_scope:
        return None

    # 2. Natural write-confirmed service actions (Todo / QuickNote / RAG note)
    todo_txt = _extract_after_folded_regex(
        raw,
        r"(?:^|\b)(?:hay\s+|vui long\s+|please\s+|help me\s+)?(?:them|tao|nhac|add|create)\s+(?:mot\s+|a\s+)?(?:viec\s+can\s+lam|dau\s+viec|todo|task|nhiem\s+vu)(?:\s*(?::|la|that|de)\s*|\s+)(.+)$",
    )
    if todo_txt and not any(k in folded for k in ("file", "obsidian.json", "script")):
        task_clean = todo_txt.strip(" .\"'")
        if task_clean:
            return {"kind": "alis_service_control", "action": "todo_add", "target": task_clean, "value": task_clean, "requiresConfirmation": True}

    qn_txt = _extract_after_folded_regex(
        raw,
        r"(?:^|\b)(?:hay\s+|vui long\s+|please\s+)?(?:them|tao|ghi|luu|add|create)\s+(?:vao\s+|into\s+)?(?:quick\s*note|quicknote|ghi\s+chu\s+nhanh|notepad)(?:\s*(?::|la)\s*|\s+)(.+)$",
    )
    if qn_txt:
        if ":" in qn_txt:
            tp, bp = qn_txt.split(":", 1)
            return {"kind": "alis_service_control", "action": "quicknote_add", "target": tp.strip() or "Companion Note", "value": bp.strip(), "requiresConfirmation": True}
        return {"kind": "alis_service_control", "action": "quicknote_add", "target": "Companion Note", "value": qn_txt, "requiresConfirmation": True}

    rag_save_txt = _extract_after_folded_regex(
        raw,
        r"(?:^|\b)(?:hay\s+|please\s+)?(?:luu|save|ghi)\s+(?:kien thuc\s+|thong tin\s+|ghi chu\s+)?(?:nay\s+)?vao\s+(?:nexus\s+)?rag(?:\s*:\s*|\s+)(.+)$",
    )
    if rag_save_txt:
        return {"kind": "nexus_knowledge", "subkind": "save_rag_note", "query": rag_save_txt}

    # 3. Safe instant controls
    has_web_target = bool(re.search(r"\b(?:youtube|yt|browser|trinh duyet|web|internet|google|wikipedia|tren mang|http)\b", folded))

    if re.search(r"\b(?:cap nhat thoi tiet|lam moi thoi tiet|refresh weather)\b", folded):
        return {"kind": "alis_service_control", "action": "weather_refresh", "target": "", "value": "", "requiresConfirmation": False}

    # Music playback / library search control (never steal YouTube/web/browser)
    if not has_web_target:
        if re.search(r"\b(?:bat shuffle|tat shuffle|tron bai|toggle shuffle|shuffle nhac)\b", folded):
            return {"kind": "alis_service_control", "action": "music_shuffle", "target": "", "value": "", "requiresConfirmation": False}
        song_q = _extract_after_folded_regex(
            raw,
            r"^(?:hay\s+|vui long\s+|please\s+|can you\s+)?(?:phat|bat|mo|nghe|play)\s+(?:bai\s+hat|bai\s+nhac|nhac|ca\s+khuc|bai|track|song|music)?\s*(.+)$",
        )
        if song_q and re.search(r"^(?:hay\s+|vui long\s+|please\s+|can you\s+)?(?:phat|bat|mo|nghe|play)\b", folded) and re.search(r"\b(?:bai hat|bai nhac|ca khuc|nhac|phat bai|play\s+(?:song|track|music)|track|song)\b", folded):
            q_part_f = _fold_ascii(song_q)
            if q_part_f and q_part_f not in ("nhac", "music", "tiep", "lai", "nua", "gi", "gi?") and "/" not in song_q and "?" not in song_q:
                return {"kind": "alis_service_control", "action": "music_play_query", "target": song_q, "value": "", "requiresConfirmation": False}

    if re.search(r"\b(?:pomodoro|bo dem tap trung|focus timer)\b", folded):
        if re.search(r"\b(?:bat dau|chay|start|bat|mo)\b", folded):
            return {"kind": "alis_service_control", "action": "pomodoro_start", "target": "", "value": "", "requiresConfirmation": False}
        if re.search(r"\b(?:tam dung|pause|dung)\b", folded):
            return {"kind": "alis_service_control", "action": "pomodoro_pause", "target": "", "value": "", "requiresConfirmation": False}
        if re.search(r"\b(?:reset|dat lai|tat)\b", folded):
            return {"kind": "alis_service_control", "action": "pomodoro_reset", "target": "", "value": "", "requiresConfirmation": False}

    if re.search(r"\b(?:night\s*light|loc anh sang xanh|che do ban dem|hyprsunset)\b", folded) and re.search(r"\b(?:bat|mo|tat|chuyen|enable|disable|turn\s+on|turn\s+off|toggle)\b", folded):
        val = "off" if re.search(r"\b(?:tat|disable|turn\s+off)\b", folded) else ("on" if re.search(r"\b(?:bat|mo|enable|turn\s+on)\b", folded) else "toggle")
        act = "night_light_toggle" if val == "toggle" else "night_light_set"
        return {"kind": "alis_service_control", "action": act, "target": "", "value": val, "requiresConfirmation": False}

    if re.search(r"\b(?:game\s*mode|che do choi game|che do game)\b", folded) and re.search(r"\b(?:bat|mo|tat|chuyen|enable|disable|turn\s+on|turn\s+off|toggle)\b", folded):
        val = "off" if re.search(r"\b(?:tat|disable|turn\s+off)\b", folded) else ("on" if re.search(r"\b(?:bat|mo|enable|turn\s+on)\b", folded) else "toggle")
        act = "gamemode_toggle" if val == "toggle" else "gamemode_set"
        return {"kind": "alis_service_control", "action": act, "target": "", "value": val, "requiresConfirmation": False}

    if re.search(r"\b(?:dnd|do not disturb|khong lam phien|che do im lang)\b", folded) and re.search(r"\b(?:bat|mo|tat|chuyen|enable|disable|turn\s+on|turn\s+off|toggle)\b", folded):
        val = "off" if re.search(r"\b(?:tat|disable|turn\s+off)\b", folded) else ("on" if re.search(r"\b(?:bat|mo|enable|turn\s+on)\b", folded) else "toggle")
        act = "dnd_toggle" if val == "toggle" else "dnd_set"
        return {"kind": "alis_service_control", "action": act, "target": "", "value": val, "requiresConfirmation": False}

    if re.search(r"\b(?:mo|open)\s+(?:nhat ky\s+(?:hom nay\s+)?(?:trong\s+)?nexus|daily\s+note\s+(?:in\s+)?nexus|nexus(?:\s+workspace)?)\b", folded):
        act = "nexus_open_daily" if re.search(r"\b(?:nhat ky|daily)\b", folded) else "nexus_open"
        return {"kind": "alis_service_control", "action": act, "target": "", "value": "", "requiresConfirmation": False}

    if has_web_target:
        return None

    # 4. Nexus Vault Knowledge queries (prioritize explicit Nexus/Vault scope over live system topics)
    if re.search(r"\b(?:tong quan nexus|thong ke nexus|nexus (?:overview|stats|status))\b", folded):
        return {"kind": "nexus_knowledge", "subkind": "overview", "query": raw}

    if re.search(r"\b(?:nhat ky hom nay|mood va energy|today'?s?\s+journal|my journal today)\b", folded):
        return {"kind": "nexus_knowledge", "subkind": "journal", "query": raw}

    if re.search(r"\b(?:tai chinh trong nexus|so du trong nexus|chi tieu trong nexus|nexus finance|finance summary)\b", folded):
        return {"kind": "nexus_knowledge", "subkind": "finance", "query": raw}

    if re.search(r"\b(?:bo suu tap.*nexus|sach trong nexus|phim trong nexus|cong thuc.*nexus|dia diem.*nexus|nexus collections?)\b", folded):
        return {"kind": "nexus_knowledge", "subkind": "collections", "query": raw}

    if re.search(r"(?:trong\s+(?:nexus|obsidian|vault|ghi chu cua toi)|cua nexus|in\s+(?:my\s+)?(?:nexus|vault|notes))\b", folded):
        if re.match(r"^(?:hay\s+|vui long\s+)?(?:doc|xem noi dung|read)\s+(?:ghi chu|note)\b", folded):
            q_note = re.sub(r"^(?:hay\s+|vui lòng\s+|vui long\s+)?(?:đọc|doc|xem nội dung|xem noi dung|read)\s+(?:ghi chú|ghi chu|note)\s*", "", raw, flags=re.I)
            q_note = re.sub(r"\s+(?:trong|in)\s+(?:my\s+)?(?:nexus|vault|obsidian|ghi chú của tôi|ghi chu cua toi)\b.*$", "", q_note, flags=re.I).strip()
            return {"kind": "nexus_knowledge", "subkind": "read", "query": q_note or raw}
        return {"kind": "nexus_knowledge", "subkind": "search", "query": raw}

    # 5. Read-only Live Alis / Integration state queries
    if re.search(r"\b(?:thoi tiet|du bao thoi tiet|nhiet do ngoai troi|troi co mua|troi hom nay|chi so aqi|chat luong khong khi|pm2\.?5|weather|forecast|air quality|aqi|temperature outside|rain today|rain tomorrow)\b", folded):
        return {"kind": "alis_local_info", "topic": "weather"}

    if re.search(r"\b(?:dang phat bai gi|dang nghe bai gi|bai hat hien tai|nhac dang phat|what song is playing|what's playing|currently playing|now playing)\b", folded):
        return {"kind": "alis_local_info", "topic": "music"}

    if re.search(r"\b(?:pomodoro|thoi gian tap trung|hom nay.*tap trung|stopwatch|focus time today|pomodoro status)\b", folded):
        return {"kind": "alis_local_info", "topic": "timers"}

    if re.search(r"\b(?:viec can lam|danh sach todo|xem todo|hom nay.*can lam gi|todo list|my todos|open tasks|tasks today)\b", folded):
        return {"kind": "alis_local_info", "topic": "todos"}

    if re.search(r"\b(?:lich trinh hom nay|lich hom nay|thoi khoa bieu|su kien hom nay|su kien sap toi|today'?s?\s+schedule|my schedule|upcoming events)\b", folded):
        return {"kind": "alis_local_info", "topic": "schedule"}

    if re.search(r"\b(?:quick\s*notes?|ghi chu nhanh|my notepad|show notepad)\b", folded):
        return {"kind": "alis_local_info", "topic": "quicknotes"}

    if re.search(r"\b(?:co thong bao nao|thong bao moi|thong bao chua doc|thong bao gan day|unread notifications|recent notifications|notification count)\b", folded):
        return {"kind": "alis_local_info", "topic": "notifications"}

    if re.search(r"\b(?:screen\s*time|thoi gian su dung may|thoi gian dung may|dung ung dung nao nhieu|app usage today)\b", folded):
        return {"kind": "alis_local_info", "topic": "screentime"}

    if re.search(r"\b(?:flashcards?|the ghi nho|spaced repetition|bao nhieu the can on|due cards|study streak)\b", folded):
        return {"kind": "alis_local_info", "topic": "learning"}

    if re.search(r"\b(?:bai tap hom nay|exercises tracker|hom nay tap gi|tien do tap luyen|workout today)\b", folded):
        return {"kind": "alis_local_info", "topic": "fitness"}

    if re.search(r"\b(?:thinkfan|toc do quat|fan rpm|tlp|gioi han sac|charge limit|night\s*light|game\s*mode|cap nhat he thong|goi cap nhat)\b", folded):
        return {"kind": "alis_local_info", "topic": "system"}

    return None


def detect_all_alis_local_intents(prompt: str) -> list[dict]:
    """Detect all non-slash Alis local service/control/Nexus intents in prompt order for compound orchestration."""
    raw = str(prompt or "").strip()
    if not raw:
        return []
    if raw.startswith("/"):
        single = detect_alis_local_intent(raw)
        return [dict(single, pos=0)] if isinstance(single, dict) else []

    folded = _fold_ascii(raw)
    has_code_ext = bool(re.search(r"\b[a-z0-9_.\-]+\.(?:qml|py|js|rs|ts|json|kdl|frag|service|sh)\b", folded))
    has_explicit_nexus_scope = bool(
        re.search(r"(?:trong\s+(?:nexus|obsidian|vault|ghi chu cua toi)|cua nexus|in\s+(?:my\s+)?(?:nexus|vault|notes))\b", folded)
    )
    if has_code_ext and not has_explicit_nexus_scope:
        return []

    # Standalone confirmation-gated writes remain single-turn interactive prompts
    for pat in (
        r"(?:^|\b)(?:hay\s+|vui long\s+|please\s+|help me\s+)?(?:them|tao|nhac|add|create)\s+(?:mot\s+|a\s+)?(?:viec\s+can\s+lam|dau\s+viec|todo|task|nhiem\s+vu)(?:\s*(?::|la|that|de)\s*|\s+)(.+)$",
        r"(?:^|\b)(?:hay\s+|vui long\s+|please\s+|help me\s+)?(?:them|tao|ghi|luu|add|create)\s+(?:vao\s+|into\s+)?(?:quick\s*note|quicknote|ghi\s+chu\s+nhanh|notepad)(?:\s*(?::|la)\s*|\s+)(.+)$",
        r"(?:^|\b)(?:hay\s+|please\s+)?(?:luu|save|ghi)\s+(?:kien thuc\s+|thong tin\s+|ghi chu\s+)?(?:nay\s+)?vao\s+(?:nexus\s+)?rag(?:\s*:\s*|\s+)(.+)$",
    ):
        if _extract_after_folded_regex(raw, pat):
            single = detect_alis_local_intent(raw)
            return [dict(single, pos=0)] if isinstance(single, dict) else []

    found: list[dict] = []
    controlled_topics: set[str] = set()
    has_web_target = bool(re.search(r"\b(?:youtube|yt|browser|trinh duyet|web|internet|google|wikipedia|tren mang|http)\b", folded))

    m_wr = re.search(r"\b(?:cap nhat thoi tiet|lam moi thoi tiet|refresh weather)\b", folded)
    if m_wr:
        found.append({"kind": "alis_service_control", "action": "weather_refresh", "target": "", "value": "", "requiresConfirmation": False, "pos": m_wr.start()})
        controlled_topics.add("weather")

    if not has_web_target:
        m_ms = re.search(r"\b(?:bat shuffle|tat shuffle|tron bai|toggle shuffle|shuffle nhac)\b", folded)
        if m_ms:
            found.append({"kind": "alis_service_control", "action": "music_shuffle", "target": "", "value": "", "requiresConfirmation": False, "pos": m_ms.start()})
            controlled_topics.add("music")

    _clause_split_re = re.compile(r"(?:[;,\n]+|\b(?:and then|and also|and|then|roi sau do|sau do|roi|va|dong thoi)\b)")

    def _clause_for_match(m_obj) -> str:
        c_start = 0
        for b in _clause_split_re.finditer(folded, 0, m_obj.start()):
            c_start = b.end()
        after = _clause_split_re.search(folded, m_obj.end())
        c_end = after.start() if after else len(folded)
        return folded[c_start:c_end]

    m_pomo = re.search(r"\b(?:pomodoro|bo dem tap trung|focus timer)\b", folded)
    if m_pomo:
        pomo_clause = _clause_for_match(m_pomo)
        if re.search(r"\b(?:bat dau|chay|start|bat|mo)\b", pomo_clause):
            found.append({"kind": "alis_service_control", "action": "pomodoro_start", "target": "", "value": "", "requiresConfirmation": False, "pos": m_pomo.start()})
            controlled_topics.add("timers")
        elif re.search(r"\b(?:tam dung|pause|dung)\b", pomo_clause):
            found.append({"kind": "alis_service_control", "action": "pomodoro_pause", "target": "", "value": "", "requiresConfirmation": False, "pos": m_pomo.start()})
            controlled_topics.add("timers")
        elif re.search(r"\b(?:reset|dat lai|tat)\b", pomo_clause):
            found.append({"kind": "alis_service_control", "action": "pomodoro_reset", "target": "", "value": "", "requiresConfirmation": False, "pos": m_pomo.start()})
            controlled_topics.add("timers")

    m_nl = re.search(r"\b(?:night\s*light|loc anh sang xanh|che do ban dem|hyprsunset)\b", folded)
    if m_nl:
        nl_clause = _clause_for_match(m_nl)
        if re.search(r"\b(?:bat|mo|tat|chuyen|enable|disable|turn\s+on|turn\s+off|toggle)\b", nl_clause):
            val = "off" if re.search(r"\b(?:tat|disable|turn\s+off)\b", nl_clause) else ("on" if re.search(r"\b(?:bat|mo|enable|turn\s+on)\b", nl_clause) else "toggle")
            act = "night_light_toggle" if val == "toggle" else "night_light_set"
            found.append({"kind": "alis_service_control", "action": act, "target": "", "value": val, "requiresConfirmation": False, "pos": m_nl.start()})
            controlled_topics.add("system")

    m_gm = re.search(r"\b(?:game\s*mode|che do choi game|che do game)\b", folded)
    if m_gm:
        gm_clause = _clause_for_match(m_gm)
        if re.search(r"\b(?:bat|mo|tat|chuyen|enable|disable|turn\s+on|turn\s+off|toggle)\b", gm_clause):
            val = "off" if re.search(r"\b(?:tat|disable|turn\s+off)\b", gm_clause) else ("on" if re.search(r"\b(?:bat|mo|enable|turn\s+on)\b", gm_clause) else "toggle")
            act = "gamemode_toggle" if val == "toggle" else "gamemode_set"
            found.append({"kind": "alis_service_control", "action": act, "target": "", "value": val, "requiresConfirmation": False, "pos": m_gm.start()})
            controlled_topics.add("system")

    m_dnd = re.search(r"\b(?:dnd|do not disturb|khong lam phien|che do im lang)\b", folded)
    if m_dnd:
        dnd_clause = _clause_for_match(m_dnd)
        if re.search(r"\b(?:bat|mo|tat|chuyen|enable|disable|turn\s+on|turn\s+off|toggle)\b", dnd_clause):
            val = "off" if re.search(r"\b(?:tat|disable|turn\s+off)\b", dnd_clause) else ("on" if re.search(r"\b(?:bat|mo|enable|turn\s+on)\b", dnd_clause) else "toggle")
            act = "dnd_toggle" if val == "toggle" else "dnd_set"
            found.append({"kind": "alis_service_control", "action": act, "target": "", "value": val, "requiresConfirmation": False, "pos": m_dnd.start()})
            controlled_topics.add("notifications")

    def _mask_span(buf: str, start: int, end: int) -> str:
        s = max(0, int(start))
        e = min(len(buf), int(end))
        if e <= s:
            return buf
        return buf[:s] + (" " * (e - s)) + buf[e:]

    scan_folded = folded

    m_nxopen = re.search(r"\b(?:mo|open)\s+(?:nhat ky\s+(?:hom nay\s+)?(?:trong\s+)?nexus|daily\s+note\s+(?:in\s+)?nexus|nexus(?:\s+workspace)?)\b", scan_folded)
    if m_nxopen:
        act = "nexus_open_daily" if re.search(r"\b(?:nhat ky|daily)\b", m_nxopen.group(0)) else "nexus_open"
        found.append({"kind": "alis_service_control", "action": act, "target": "", "value": "", "requiresConfirmation": False, "pos": m_nxopen.start()})
        scan_folded = _mask_span(scan_folded, m_nxopen.start(), m_nxopen.end())

    if has_web_target:
        found.sort(key=lambda item: int(item.get("pos") or 0))
        return found

    nexus_patterns = [
        ("overview", r"\b(?:tong quan nexus|thong ke nexus|nexus (?:overview|stats|status))\b"),
        ("journal", r"\b(?:nhat ky hom nay|mood va energy|today'?s?\s+journal|my journal today)\b"),
        ("finance", r"\b(?:tai chinh trong nexus|so du trong nexus|chi tieu trong nexus|nexus finance|finance summary)\b"),
        ("collections", r"\b(?:bo suu tap.*nexus|sach trong nexus|phim trong nexus|cong thuc.*nexus|dia diem.*nexus|nexus collections?)\b"),
    ]
    matched_nx = False
    for subk, pat in nexus_patterns:
        m = re.search(pat, scan_folded)
        if m:
            found.append({"kind": "nexus_knowledge", "subkind": subk, "query": raw, "pos": m.start()})
            scan_folded = _mask_span(scan_folded, m.start(), m.end())
            matched_nx = True
    if not matched_nx:
        m_nxs = re.search(r"(?:trong\s+(?:nexus|obsidian|vault|ghi chu cua toi)|cua nexus|in\s+(?:my\s+)?(?:nexus|vault|notes))\b", scan_folded)
        if m_nxs:
            prefix = scan_folded[:m_nxs.start()]
            clause_start = 0
            boundaries = list(re.finditer(r"(?:[;,\n]+|\b(?:and then|then|and also|roi sau do|sau do|roi|dong thoi)\b)", prefix))
            if boundaries:
                clause_start = boundaries[-1].end()
            verbs = list(re.finditer(r"\b(?:tim kiem|tim|tra cuu|doc|xem|search|find|look up|read|query)\b", prefix[clause_start:]))
            if verbs:
                clause_start += verbs[-1].start()
            nx_query = raw[clause_start:m_nxs.end()].strip() if len(raw) == len(folded) else raw
            found.append({"kind": "nexus_knowledge", "subkind": "search", "query": nx_query or raw, "pos": clause_start})
            scan_folded = _mask_span(scan_folded, clause_start, m_nxs.end())

    topic_patterns = [
        ("weather", r"\b(?:thoi tiet|du bao thoi tiet|nhiet do ngoai troi|troi co mua|troi hom nay|chi so aqi|chat luong khong khi|pm2\.?5|weather|forecast|air quality|aqi|temperature outside|rain today|rain tomorrow)\b"),
        ("music", r"\b(?:dang phat bai gi|dang nghe bai gi|bai hat hien tai|nhac dang phat|what song is playing|what's playing|currently playing|now playing)\b"),
        ("timers", r"\b(?:pomodoro|thoi gian tap trung|hom nay.*tap trung|stopwatch|focus time today|pomodoro status)\b"),
        ("todos", r"\b(?:viec can lam|danh sach todo|xem todo|hom nay.*can lam gi|todo list|my todos|open tasks|tasks today)\b"),
        ("schedule", r"\b(?:lich trinh hom nay|lich hom nay|thoi khoa bieu|su kien hom nay|su kien sap toi|today'?s?\s+schedule|my schedule|upcoming events)\b"),
        ("quicknotes", r"\b(?:quick\s*notes?|ghi chu nhanh|my notepad|show notepad)\b"),
        ("notifications", r"\b(?:co thong bao nao|thong bao moi|thong bao chua doc|thong bao gan day|unread notifications|recent notifications|notification count)\b"),
        ("screentime", r"\b(?:screen\s*time|thoi gian su dung may|thoi gian dung may|dung ung dung nao nhieu|app usage today)\b"),
        ("learning", r"\b(?:flashcards?|the ghi nho|spaced repetition|bao nhieu the can on|due cards|study streak)\b"),
        ("fitness", r"\b(?:bai tap hom nay|exercises tracker|hom nay tap gi|tien do tap luyen|workout today)\b"),
        ("system", r"\b(?:thinkfan|toc do quat|fan rpm|tlp|gioi han sac|charge limit|night\s*light|game\s*mode|cap nhat he thong|goi cap nhat)\b"),
    ]
    for topic, pat in topic_patterns:
        if topic in controlled_topics:
            continue
        m = re.search(pat, scan_folded)
        if m:
            found.append({"kind": "alis_local_info", "topic": topic, "pos": m.start()})

    found.sort(key=lambda item: int(item.get("pos") or 0))
    return found
