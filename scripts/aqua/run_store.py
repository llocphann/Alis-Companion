"""Durable SQLite run, checkpoint, receipt, and session working-context store.

Stored in $XDG_STATE_HOME/alis-companion/agent_runs.sqlite3 (0600), completely
separate from chat.sqlite3, memory.sqlite3, and Pocket stores.
After an unexpected crash/restart, any in-flight side-effecting step without a
completion receipt is reconciled and marked 'uncertain_after_crash' rather than
blindly re-executing.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


SCHEMA_VERSION = 1
INTERACTIVE_DEFAULTS = {
    "maxTurns": 30,
    "maxDurationSec": 600,
}
RULE_DEFAULTS = {
    "maxTurns": 10,
    "maxDurationSec": 180,
}


class RunStoreError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _now_ms(now_ms=None):
    if isinstance(now_ms, int) and now_ms > 0:
        return now_ms
    return int(time.time() * 1000)


def _db_path():
    custom = os.environ.get("ALIS_AQUA_RUNS_DB", "").strip()
    if custom:
        p = Path(custom)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".local/state")
    folder = base / "alis-companion"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "agent_runs.sqlite3"


def _connect():
    path = _db_path()
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            goal TEXT NOT NULL,
            language TEXT NOT NULL,
            turn_count INTEGER NOT NULL,
            max_turns INTEGER NOT NULL,
            started_ms INTEGER NOT NULL,
            updated_ms INTEGER NOT NULL,
            deadline_ms INTEGER NOT NULL,
            repeated_error_count INTEGER NOT NULL,
            last_error_sig TEXT NOT NULL,
            pid INTEGER NOT NULL,
            checkpoint_json TEXT NOT NULL,
            token_usage_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_steps (
            step_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            side_effect INTEGER NOT NULL,
            target_path TEXT NOT NULL,
            status TEXT NOT NULL,
            before_sha256 TEXT NOT NULL,
            after_sha256 TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            created_ms INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_context (
            session_key TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_ms INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def _file_sha256(path_str):
    if not path_str:
        return ""
    try:
        p = Path(path_str)
        if p.is_file() and not p.is_symlink():
            return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        pass
    return ""


def reconcile_crashed_runs(now_ms=None, force_all=False):
    """Reconcile any run left 'running' from a dead PID without re-executing side-effecting steps."""
    ts = _now_ms(now_ms)
    current_pid = os.getpid()
    reconciled = []
    with closing(_connect()) as db:
        rows = db.execute("SELECT * FROM agent_runs WHERE status = 'running'").fetchall()
        for row in rows:
            pid = int(row["pid"])
            alive = False
            if not force_all:
                if pid == current_pid:
                    alive = True
                elif pid > 0:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except OSError:
                        alive = False
            if alive and int(row["deadline_ms"]) > ts:
                continue

            run_id = row["run_id"]
            steps = db.execute(
                "SELECT * FROM agent_steps WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
            uncertain_steps = []
            for st in steps:
                if st["status"] == "in_progress" and int(st["side_effect"]) == 1:
                    target = st["target_path"]
                    current_hash = _file_sha256(target)
                    before_hash = st["before_sha256"]
                    changed_on_disk = bool(current_hash != before_hash)
                    detail = json.loads(st["detail_json"] or "{}")
                    detail["reconciledAtMs"] = ts
                    detail["changedOnDisk"] = changed_on_disk
                    detail["observedSha256"] = current_hash
                    db.execute(
                        "UPDATE agent_steps SET status = 'uncertain_after_crash', detail_json = ? WHERE step_id = ?",
                        (json.dumps(detail), st["step_id"]),
                    )
                    uncertain_steps.append({
                        "stepId": st["step_id"],
                        "toolName": st["tool_name"],
                        "targetPath": target,
                        "changedOnDisk": changed_on_disk,
                    })
                elif st["status"] == "in_progress":
                    db.execute(
                        "UPDATE agent_steps SET status = 'aborted_after_crash' WHERE step_id = ?",
                        (st["step_id"],),
                    )

            new_status = "uncertain_after_crash" if uncertain_steps else "paused_after_crash"
            cp = json.loads(row["checkpoint_json"] or "{}")
            cp["reconciledAtMs"] = ts
            cp["uncertainSteps"] = uncertain_steps
            db.execute(
                "UPDATE agent_runs SET status = ?, updated_ms = ?, checkpoint_json = ? WHERE run_id = ?",
                (new_status, ts, json.dumps(cp), run_id),
            )
            reconciled.append({
                "runId": run_id,
                "status": new_status,
                "uncertainSteps": uncertain_steps,
            })
        db.commit()
    return reconciled


def start_run(
    goal,
    source="interactive",
    language="vi",
    max_turns=None,
    max_duration_sec=None,
    checkpoint=None,
    now_ms=None,
):
    ts = _now_ms(now_ms)
    reconcile_crashed_runs(ts)
    defaults = RULE_DEFAULTS if source == "rule" else INTERACTIVE_DEFAULTS
    turns = max(1, min(30, int(max_turns or defaults["maxTurns"])))
    duration_sec = max(5, min(600, int(max_duration_sec or defaults["maxDurationSec"])))

    with closing(_connect()) as db:
        active = db.execute("SELECT run_id FROM agent_runs WHERE status = 'running'").fetchone()
        if active:
            raise RunStoreError("single_active_run_busy", f"Run {active['run_id']} is already active.")

        seed = f"{goal}:{source}:{ts}:{os.urandom(4).hex()}"
        run_id = "run_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        cp_data = checkpoint if isinstance(checkpoint, dict) else {"remainingSteps": [], "completedSteps": []}
        usage_data = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
        db.execute(
            """
            INSERT INTO agent_runs(
                run_id, source, status, goal, language, turn_count, max_turns,
                started_ms, updated_ms, deadline_ms, repeated_error_count,
                last_error_sig, pid, checkpoint_json, token_usage_json
            ) VALUES (?, ?, 'running', ?, ?, 0, ?, ?, ?, ?, 0, '', ?, ?, ?)
            """,
            (
                run_id,
                str(source),
                str(goal)[:600],
                str(language or "vi"),
                turns,
                ts,
                ts,
                ts + duration_sec * 1000,
                os.getpid(),
                json.dumps(cp_data),
                json.dumps(usage_data),
            ),
        )
        db.commit()
    return get_run(run_id)


def record_step_start(run_id, seq, tool_name, side_effect=False, target_path="", detail=None, now_ms=None):
    ts = _now_ms(now_ms)
    step_id = f"{run_id}_s{int(seq)}"
    before_sha = _file_sha256(target_path) if side_effect and target_path else ""
    with closing(_connect()) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO agent_steps(
                step_id, run_id, seq, tool_name, side_effect, target_path,
                status, before_sha256, after_sha256, receipt_id, detail_json, created_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 'in_progress', ?, '', '', ?, ?)
            """,
            (
                step_id,
                str(run_id),
                int(seq),
                str(tool_name),
                1 if side_effect else 0,
                str(target_path or ""),
                before_sha,
                json.dumps(detail or {}),
                ts,
            ),
        )
        db.commit()
    return step_id


def record_step_finish(step_id, status="completed", target_path="", receipt_id="", detail=None):
    after_sha = _file_sha256(target_path) if target_path else ""
    with closing(_connect()) as db:
        db.execute(
            """
            UPDATE agent_steps
            SET status = ?, after_sha256 = ?, receipt_id = ?, detail_json = ?
            WHERE step_id = ?
            """,
            (
                str(status),
                after_sha,
                str(receipt_id or ""),
                json.dumps(detail or {}),
                str(step_id),
            ),
        )
        db.commit()


def advance_turn(run_id, error_signature="", token_delta=None, checkpoint=None, now_ms=None):
    ts = _now_ms(now_ms)
    with closing(_connect()) as db:
        row = db.execute("SELECT * FROM agent_runs WHERE run_id = ?", (str(run_id),)).fetchone()
        if not row:
            raise RunStoreError("run_not_found", "Agent run not found.")

        turn_count = int(row["turn_count"]) + 1
        max_turns = int(row["max_turns"])
        deadline_ms = int(row["deadline_ms"])
        rep_err = int(row["repeated_error_count"])
        last_sig = str(row["last_error_sig"])

        if error_signature:
            if error_signature == last_sig:
                rep_err += 1
            else:
                rep_err = 1
                last_sig = str(error_signature)[:160]
        else:
            rep_err = 0
            last_sig = ""

        usage = json.loads(row["token_usage_json"] or "{}")
        if isinstance(token_delta, dict):
            usage["inputTokens"] = int(usage.get("inputTokens", 0)) + int(token_delta.get("inputTokens", 0))
            usage["outputTokens"] = int(usage.get("outputTokens", 0)) + int(token_delta.get("outputTokens", 0))
            usage["totalTokens"] = usage["inputTokens"] + usage["outputTokens"]

        cp = checkpoint if isinstance(checkpoint, dict) else json.loads(row["checkpoint_json"] or "{}")
        status = row["status"]
        stop_reason = ""
        if rep_err >= 3:
            status = "stopped_repeated_error"
            stop_reason = "repeated_error_limit"
        elif turn_count >= max_turns:
            status = "stopped_budget_exhausted"
            stop_reason = "turn_budget_exhausted"
        elif ts >= deadline_ms:
            status = "stopped_budget_exhausted"
            stop_reason = "time_budget_exhausted"

        db.execute(
            """
            UPDATE agent_runs
            SET turn_count = ?, status = ?, updated_ms = ?, repeated_error_count = ?,
                last_error_sig = ?, checkpoint_json = ?, token_usage_json = ?
            WHERE run_id = ?
            """,
            (
                turn_count,
                status,
                ts,
                rep_err,
                last_sig,
                json.dumps(cp),
                json.dumps(usage),
                str(run_id),
            ),
        )
        db.commit()
    return {
        "run": get_run(run_id),
        "allowed": not bool(stop_reason),
        "reason": stop_reason,
        "stopped": bool(stop_reason),
        "stopReason": stop_reason,
    }


def finish_run(run_id, status="completed", checkpoint=None, now_ms=None):
    ts = _now_ms(now_ms)
    with closing(_connect()) as db:
        row = db.execute("SELECT * FROM agent_runs WHERE run_id = ?", (str(run_id),)).fetchone()
        if not row:
            return None
        cp = checkpoint if isinstance(checkpoint, dict) else json.loads(row["checkpoint_json"] or "{}")
        db.execute(
            "UPDATE agent_runs SET status = ?, updated_ms = ?, checkpoint_json = ? WHERE run_id = ?",
            (str(status), ts, json.dumps(cp), str(run_id)),
        )
        db.commit()
    return get_run(run_id)


def cancel_active_runs(reason="cancelled_by_user", now_ms=None):
    ts = _now_ms(now_ms)
    cancelled_ids = []
    with closing(_connect()) as db:
        rows = db.execute(
            "SELECT * FROM agent_runs WHERE status IN ('running', 'awaiting_confirmation')"
        ).fetchall()
        for row in rows:
            cp = json.loads(row["checkpoint_json"] or "{}")
            cp["cancelledReason"] = str(reason)
            cp["cancelledAtMs"] = ts
            db.execute(
                "UPDATE agent_runs SET status = 'cancelled', updated_ms = ?, checkpoint_json = ? WHERE run_id = ?",
                (ts, json.dumps(cp), row["run_id"]),
            )
            cancelled_ids.append(row["run_id"])
        db.commit()
    return cancelled_ids


def get_run(run_id):
    with closing(_connect()) as db:
        row = db.execute("SELECT * FROM agent_runs WHERE run_id = ?", (str(run_id),)).fetchone()
        if not row:
            return None
        steps = db.execute("SELECT * FROM agent_steps WHERE run_id = ? ORDER BY seq ASC", (str(run_id),)).fetchall()
        return {
            "runId": row["run_id"],
            "source": row["source"],
            "status": row["status"],
            "goal": row["goal"],
            "language": row["language"],
            "turnCount": int(row["turn_count"]),
            "maxTurns": int(row["max_turns"]),
            "startedMs": int(row["started_ms"]),
            "updatedMs": int(row["updated_ms"]),
            "deadlineMs": int(row["deadline_ms"]),
            "repeatedErrorCount": int(row["repeated_error_count"]),
            "lastErrorSig": row["last_error_sig"],
            "checkpoint": json.loads(row["checkpoint_json"] or "{}"),
            "tokenUsage": json.loads(row["token_usage_json"] or "{}"),
            "steps": [
                {
                    "stepId": s["step_id"],
                    "seq": int(s["seq"]),
                    "toolName": s["tool_name"],
                    "sideEffect": bool(s["side_effect"]),
                    "targetPath": s["target_path"],
                    "status": s["status"],
                    "beforeSha256": s["before_sha256"],
                    "afterSha256": s["after_sha256"],
                    "receiptId": s["receipt_id"],
                    "detail": json.loads(s["detail_json"] or "{}"),
                }
                for s in steps
            ],
        }


def latest_resumable_run():
    with closing(_connect()) as db:
        row = db.execute(
            """
            SELECT run_id FROM agent_runs
            WHERE status IN ('paused', 'paused_after_crash', 'uncertain_after_crash', 'cancelled', 'awaiting_confirmation')
            ORDER BY updated_ms DESC LIMIT 1
            """
        ).fetchone()
        return get_run(row["run_id"]) if row else None


def load_session_context(session_key="default"):
    with closing(_connect()) as db:
        row = db.execute(
            "SELECT data_json FROM session_context WHERE session_key = ?",
            (str(session_key),),
        ).fetchone()
        if not row:
            return {
                "lastFile": "",
                "lastDirectory": "",
                "lastWindowId": None,
                "lastAppId": "",
                "lastProject": "",
                "recentCandidates": [],
                "remainingSteps": [],
                "customMaxTurns": 0,
                "customMaxDurationSec": 0,
            }
        try:
            data = json.loads(row["data_json"])
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}


def save_session_context(context_dict, session_key="default", now_ms=None):
    ts = _now_ms(now_ms)
    current = load_session_context(session_key)
    merged = {**current, **(context_dict or {})}
    with closing(_connect()) as db:
        db.execute(
            "INSERT OR REPLACE INTO session_context(session_key, data_json, updated_ms) VALUES (?, ?, ?)",
            (str(session_key), json.dumps(merged, ensure_ascii=True), ts),
        )
        db.commit()
    return merged
