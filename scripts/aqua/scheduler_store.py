#!/usr/bin/env python3
"""Persistent SQLite scheduler store for Alis-Companion.

Manages one-shot reminders, recurring schedules (`daily@HH:MM`,
`weekly@mon,wed@HH:MM`, `every:Nm`), and background process-exit watchers
with zero mutable global state and pure stdlib SQLite WAL storage.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from typing import Any

MAX_ACTIVE_JOBS = 64

_WEEKDAY_MAP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


class SchedulerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(self.message)


def _now_ms(now_ms: int | None = None) -> int:
    return int(now_ms) if now_ms is not None else int(time.time() * 1000)


def scheduler_db_path() -> Path:
    custom = os.environ.get("ALIS_AQUA_STATE_DIR", "").strip()
    if custom:
        state_dir = Path(custom).expanduser().resolve(strict=False)
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
        base = Path(xdg_state).expanduser() if xdg_state else (Path.home() / ".local" / "state")
        state_dir = (base / "alis-companion").resolve(strict=False)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(state_dir, 0o700)
    except OSError:
        pass
    return state_dir / "scheduler.db"


def _connect() -> sqlite3.Connection:
    dbp = scheduler_db_path()
    conn = sqlite3.connect(str(dbp), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    ver = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if ver < 1:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('reminder','recurring','process_watch')),
                title TEXT NOT NULL,
                prompt TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('active','completed','cancelled')),
                due_ms INTEGER,
                recurrence TEXT NOT NULL DEFAULT '',
                watch_pid INTEGER,
                watch_name TEXT NOT NULL DEFAULT '',
                last_fired_ms INTEGER,
                created_ms INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sched_status_due ON scheduled_jobs(status, due_ms)"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    try:
        if dbp.exists():
            os.chmod(dbp, 0o600)
    except OSError:
        pass
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    try:
        meta = json.loads(str(row["metadata_json"] or "{}"))
        if not isinstance(meta, dict):
            meta = {}
    except ValueError:
        meta = {}
    return {
        "id": str(row["id"]),
        "kind": str(row["kind"]),
        "title": str(row["title"]),
        "prompt": str(row["prompt"] or ""),
        "status": str(row["status"]),
        "dueMs": int(row["due_ms"]) if row["due_ms"] is not None else None,
        "recurrence": str(row["recurrence"] or ""),
        "watchPid": int(row["watch_pid"]) if row["watch_pid"] is not None else None,
        "watchName": str(row["watch_name"] or ""),
        "lastFiredMs": int(row["last_fired_ms"]) if row["last_fired_ms"] is not None else None,
        "createdMs": int(row["created_ms"]),
        "metadata": meta,
    }


def _gen_job_id(prefix: str = "job") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10_000_000}_{secrets.token_hex(3)}"


def _check_active_cap(conn: sqlite3.Connection) -> None:
    cnt = int(conn.execute("SELECT COUNT(*) FROM scheduled_jobs WHERE status = 'active'").fetchone()[0])
    if cnt >= MAX_ACTIVE_JOBS:
        raise SchedulerError("job_limit_reached", f"Maximum {MAX_ACTIVE_JOBS} active scheduled jobs reached.")


def parse_hhmm(raw: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(raw or ""))
    if not m:
        raise SchedulerError("invalid_time", f"Invalid HH:MM time expression: '{raw}'.")
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise SchedulerError("invalid_time", f"Out-of-range HH:MM time: '{raw}'.")
    return hh, mm


def compute_next_recurring_ms(recurrence: str, *, after_ms: int) -> tuple[str, int]:
    """Validate and compute the next future trigger timestamp in epoch ms."""
    spec = str(recurrence or "").strip().lower()
    m_every = re.match(r"^every:(\d+)\s*m$", spec)
    if m_every:
        mins = int(m_every.group(1))
        if mins < 5 or mins > 10080:
            raise SchedulerError("invalid_recurrence", "Interval recurrence must be between 5m and 10080m (7 days).")
        return f"every:{mins}m", after_ms + mins * 60_000

    base_dt = datetime.fromtimestamp(after_ms / 1000.0).astimezone()
    m_daily = re.match(r"^daily@(\d{1,2}:\d{2})$", spec)
    if m_daily:
        hh, mm = parse_hhmm(m_daily.group(1))
        cand = base_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= base_dt:
            cand += timedelta(days=1)
        norm = f"daily@{hh:02d}:{mm:02d}"
        return norm, int(cand.timestamp() * 1000)

    m_weekly = re.match(r"^weekly@([a-z,]+)@(\d{1,2}:\d{2})$", spec)
    if m_weekly:
        raw_days = [d.strip() for d in m_weekly.group(1).split(",") if d.strip()]
        if not raw_days:
            raise SchedulerError("invalid_recurrence", "Weekly recurrence requires at least one weekday.")
        day_nums: list[int] = []
        norm_days: list[str] = []
        rev = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
        for d in raw_days:
            if d not in _WEEKDAY_MAP:
                raise SchedulerError("invalid_recurrence", f"Invalid weekday '{d}' in weekly recurrence.")
            num = _WEEKDAY_MAP[d]
            if num not in day_nums:
                day_nums.append(num)
                norm_days.append(rev[num])
        day_nums.sort()
        norm_days = [rev[n] for n in day_nums]
        hh, mm = parse_hhmm(m_weekly.group(2))
        for offset in range(0, 8):
            day_dt = (base_dt + timedelta(days=offset)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if day_dt.weekday() in day_nums and day_dt > base_dt:
                norm = f"weekly@{','.join(norm_days)}@{hh:02d}:{mm:02d}"
                return norm, int(day_dt.timestamp() * 1000)
        raise SchedulerError("invalid_recurrence", "Could not compute next weekly trigger.")

    raise SchedulerError(
        "invalid_recurrence",
        f"Unsupported recurrence '{recurrence}'. Use 'daily@HH:MM', 'weekly@mon,wed@HH:MM', or 'every:Nm'.",
    )


def _pid_starttime(pid: int) -> str:
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
        rp = raw.rfind(")")
        if rp != -1:
            fields = raw[rp + 2:].split()
            if len(fields) > 19:
                return str(fields[19])
    except OSError:
        pass
    return ""


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    stat_p = Path(f"/proc/{pid}/stat")
    if stat_p.is_file():
        try:
            raw = stat_p.read_text(encoding="utf-8", errors="replace")
            rp = raw.rfind(")")
            if rp != -1 and len(raw) > rp + 2:
                state = raw[rp + 2]
                if state == "Z":
                    return False
            return True
        except OSError:
            pass
    elif Path(f"/proc/{pid}").exists():
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _matching_pids_for_name(proc_name: str) -> list[int]:
    want = str(proc_name or "").strip().lower()
    if not want:
        return []
    pids: list[int] = []
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            pid_val = int(entry.name)
            try:
                comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
                if comm == want:
                    if _is_pid_alive(pid_val):
                        pids.append(pid_val)
                    continue
                cmd_raw = (entry / "cmdline").read_bytes()
                if cmd_raw:
                    exe_tok = cmd_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
                    if Path(exe_tok).name.lower() == want and _is_pid_alive(pid_val):
                        pids.append(pid_val)
            except OSError:
                continue
    return pids


def create_reminder(
    title: str,
    *,
    delay_minutes: float | int | None = None,
    due_ms: int | None = None,
    at_time: str = "",
    prompt: str = "",
    now_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_title = str(title or "").strip()[:180]
    if not clean_title:
        raise SchedulerError("missing_title", "Specify a reminder title or message.")
    ts = _now_ms(now_ms)
    eff_due: int | None = None
    if due_ms is not None:
        eff_due = int(due_ms)
    elif delay_minutes is not None:
        mins = float(delay_minutes)
        if mins <= 0 or mins > 525600:
            raise SchedulerError("invalid_delay", "delayMinutes must be > 0 and <= 525600.")
        eff_due = ts + int(round(mins * 60_000))
    elif str(at_time or "").strip():
        hh, mm = parse_hhmm(at_time)
        base_dt = datetime.fromtimestamp(ts / 1000.0).astimezone()
        cand = base_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= base_dt:
            cand += timedelta(days=1)
        eff_due = int(cand.timestamp() * 1000)
    else:
        raise SchedulerError("missing_due_time", "Specify delayMinutes, atTime ('HH:MM'), or dueMs.")

    jid = _gen_job_id("rem")
    meta_json = json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False)
    conn = _connect()
    try:
        _check_active_cap(conn)
        conn.execute(
            """
            INSERT INTO scheduled_jobs(
                id, kind, title, prompt, status, due_ms, recurrence,
                watch_pid, watch_name, last_fired_ms, created_ms, metadata_json
            ) VALUES (?, 'reminder', ?, ?, 'active', ?, '', NULL, '', NULL, ?, ?)
            """,
            (jid, clean_title, str(prompt or "").strip()[:400], eff_due, ts, meta_json),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (jid,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def create_recurring(
    title: str,
    recurrence: str,
    *,
    prompt: str = "",
    now_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_title = str(title or "").strip()[:180]
    if not clean_title:
        raise SchedulerError("missing_title", "Specify a recurring schedule title.")
    ts = _now_ms(now_ms)
    norm_rec, next_due = compute_next_recurring_ms(recurrence, after_ms=ts)
    jid = _gen_job_id("rec")
    meta_json = json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False)
    conn = _connect()
    try:
        _check_active_cap(conn)
        conn.execute(
            """
            INSERT INTO scheduled_jobs(
                id, kind, title, prompt, status, due_ms, recurrence,
                watch_pid, watch_name, last_fired_ms, created_ms, metadata_json
            ) VALUES (?, 'recurring', ?, ?, 'active', ?, ?, NULL, '', NULL, ?, ?)
            """,
            (jid, clean_title, str(prompt or "").strip()[:400], next_due, norm_rec, ts, meta_json),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (jid,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def create_process_watch(
    title: str = "",
    *,
    pid: int | None = None,
    process_name: str = "",
    prompt: str = "",
    now_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_name = str(process_name or "").strip()[:128]
    eff_pid: int | None = int(pid) if pid is not None else None
    meta_dict = dict(metadata) if isinstance(metadata, dict) else {}
    if eff_pid is not None:
        if eff_pid <= 0 or not _is_pid_alive(eff_pid):
            raise SchedulerError("process_not_running", f"Process PID {eff_pid} is not currently running.")
        st_time = _pid_starttime(eff_pid)
        if st_time:
            meta_dict["watchStarttime"] = st_time
    elif clean_name:
        if clean_name.startswith("-"):
            raise SchedulerError("invalid_process_name", f"Invalid process name '{clean_name}'.")
        matches = _matching_pids_for_name(clean_name)
        if not matches:
            raise SchedulerError("process_not_running", f"No live process matching '{clean_name}' is running.")
        # Keep eff_pid = None when watching by process name so poll_due_jobs monitors ALL matching instances.
    else:
        raise SchedulerError("missing_process_target", "Specify a running PID or processName to watch.")

    eff_title = str(title or "").strip()[:180] or f"Process exit: {clean_name or eff_pid}"
    ts = _now_ms(now_ms)
    meta_json = json.dumps(meta_dict, ensure_ascii=False)
    jid = _gen_job_id("watch")
    conn = _connect()
    try:
        _check_active_cap(conn)
        conn.execute(
            """
            INSERT INTO scheduled_jobs(
                id, kind, title, prompt, status, due_ms, recurrence,
                watch_pid, watch_name, last_fired_ms, created_ms, metadata_json
            ) VALUES (?, 'process_watch', ?, ?, 'active', NULL, '', ?, ?, NULL, ?, ?)
            """,
            (jid, eff_title, str(prompt or "").strip()[:400], eff_pid, clean_name, ts, meta_json),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (jid,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_jobs(include_completed: bool = False) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        if include_completed:
            rows = conn.execute(
                "SELECT * FROM scheduled_jobs ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, COALESCE(due_ms, created_ms) ASC LIMIT 128"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE status = 'active' ORDER BY COALESCE(due_ms, created_ms) ASC LIMIT 64"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def cancel_job(job_id: str) -> dict[str, Any]:
    jid = str(job_id or "").strip()
    if not jid:
        raise SchedulerError("missing_job_id", "Specify a job ID to cancel.")
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (jid,)).fetchone()
        if not row:
            raise SchedulerError("job_not_found", f"Scheduled job '{jid}' does not exist.")
        conn.execute("UPDATE scheduled_jobs SET status = 'cancelled' WHERE id = ?", (jid,))
        conn.commit()
        updated = conn.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (jid,)).fetchone()
        return _row_to_dict(updated)
    finally:
        conn.close()


def _send_desktop_notify(title: str, body: str) -> bool:
    import native_host_bridge

    if native_host_bridge.is_test_or_isolated_env():
        return False
    nsend = shutil.which("notify-send")
    if not nsend:
        return False
    try:
        subprocess.run(
            [nsend, "--app-name=Alis Companion", str(title)[:120], str(body)[:240]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
            check=False,
        )
        return True
    except Exception:
        return False


def poll_due_jobs(now_ms: int | None = None, *, send_notify: bool = False) -> dict[str, Any]:
    """Evaluate active reminders, recurring jobs, and process watches cleanly."""
    ts = _now_ms(now_ms)
    fired: list[dict[str, Any]] = []
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_jobs WHERE status = 'active' ORDER BY COALESCE(due_ms, created_ms) ASC"
        ).fetchall()
        for r in rows:
            job = _row_to_dict(r)
            kind = job["kind"]
            jid = job["id"]
            if kind == "reminder":
                due = job["dueMs"]
                if due is not None and due <= ts:
                    conn.execute(
                        "UPDATE scheduled_jobs SET status = 'completed', last_fired_ms = ? WHERE id = ?",
                        (ts, jid),
                    )
                    job["status"] = "completed"
                    job["lastFiredMs"] = ts
                    fired.append(job)
            elif kind == "recurring":
                due = job["dueMs"]
                if due is not None and due <= ts:
                    try:
                        _, next_due = compute_next_recurring_ms(job["recurrence"], after_ms=ts)
                    except SchedulerError:
                        next_due = ts + 86_400_000
                    conn.execute(
                        "UPDATE scheduled_jobs SET due_ms = ?, last_fired_ms = ? WHERE id = ?",
                        (next_due, ts, jid),
                    )
                    fired_copy = dict(job)
                    fired_copy["lastFiredMs"] = ts
                    fired_copy["nextDueMs"] = next_due
                    fired.append(fired_copy)
            elif kind == "process_watch":
                wpid = job["watchPid"]
                wname = job["watchName"]
                exited = False
                if wpid is not None:
                    alive = _is_pid_alive(int(wpid))
                    if not alive:
                        exited = True
                    else:
                        exp_start = str((job.get("metadata") or {}).get("watchStarttime") or "")
                        if exp_start:
                            cur_start = _pid_starttime(int(wpid))
                            if cur_start and cur_start != exp_start:
                                exited = True
                elif wname:
                    exited = len(_matching_pids_for_name(wname)) == 0
                if exited:
                    conn.execute(
                        "UPDATE scheduled_jobs SET status = 'completed', last_fired_ms = ? WHERE id = ?",
                        (ts, jid),
                    )
                    job["status"] = "completed"
                    job["lastFiredMs"] = ts
                    fired.append(job)
        conn.commit()
        active_cnt = int(conn.execute("SELECT COUNT(*) FROM scheduled_jobs WHERE status = 'active'").fetchone()[0])
        proc_cnt = int(conn.execute("SELECT COUNT(*) FROM scheduled_jobs WHERE status = 'active' AND kind = 'process_watch'").fetchone()[0])
        next_due_row = conn.execute("SELECT MIN(due_ms) FROM scheduled_jobs WHERE status = 'active' AND due_ms IS NOT NULL").fetchone()
        next_due_ms = int(next_due_row[0]) if (next_due_row and next_due_row[0] is not None) else None
    finally:
        conn.close()

    if send_notify and fired:
        for fjob in fired:
            ttl = fjob.get("title") or "Scheduled Reminder"
            sub = fjob.get("prompt") or f"Triggered ({fjob.get('kind')})"
            _send_desktop_notify(ttl, sub)

    return {
        "ok": True,
        "nowMs": ts,
        "fired": fired,
        "activeCount": active_cnt,
        "activeProcessWatchCount": proc_cnt,
        "nextDueMs": next_due_ms,
    }


def schedule_summary() -> dict[str, Any]:
    """Return active scheduled jobs plus adaptive-timer metadata."""
    jobs = list_jobs(include_completed=False)
    proc_cnt = sum(1 for j in jobs if j.get("kind") == "process_watch")
    due_vals = [int(j["dueMs"]) for j in jobs if j.get("dueMs") is not None]
    return {
        "jobs": jobs,
        "activeCount": len(jobs),
        "activeProcessWatchCount": proc_cnt,
        "nextDueMs": min(due_vals) if due_vals else None,
    }
