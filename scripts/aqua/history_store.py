#!/usr/bin/env python3
"""Crash-safe local persistence for Aqua chat threads, projects, and transcripts.

Transcript persistence is deliberately separate from inference context. SQLite
stores projects, threads, and messages incrementally under XDG_STATE_HOME;
callers choose a bounded recent window for model input and page older rows only
for the UI. Legacy single-table databases (`messages` without `thread_id`) are
migrated idempotently in place to `chat_default` without losing any history.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

MAX_ROWS = 2000
MAX_CONTENT = 6000
MAX_THREADS = 200
MAX_PROJECTS = 64
DEFAULT_THREAD_ID = "chat_default"
DEFAULT_THREAD_TITLE = "New Chat"


class HistoryError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = str(code)
        super().__init__(message or str(code))


def path() -> Path:
    override = os.environ.get("ALIS_AQUA_HISTORY_DB")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "alis/aqua/chat.sqlite3"


def _clean_title(text: str, default: str = DEFAULT_THREAD_TITLE, limit: int = 64) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return default
    return cleaned[:limit]


def _derive_title_from_prompt(prompt: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(prompt or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return DEFAULT_THREAD_TITLE
    if len(cleaned) > 46:
        return cleaned[:45].rstrip() + "…"
    return cleaned


def _gen_id(prefix: str) -> str:
    seed = f"{prefix}:{time.time_ns()}:{os.getpid()}:{os.urandom(8).hex()}"
    return f"{prefix}_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12]}"


def _ensure_schema_and_migrate(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL,
            created_ms INTEGER NOT NULL,
            model TEXT NOT NULL DEFAULT ''
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS thread_messages(
            message_id INTEGER PRIMARY KEY,
            thread_id TEXT NOT NULL
        )"""
    )
    cols = [row[1] for row in db.execute("PRAGMA table_info(messages)").fetchall()]
    if "thread_id" in cols:
        db.execute(
            "INSERT OR IGNORE INTO thread_messages(message_id, thread_id) SELECT id, COALESCE(NULLIF(thread_id,''),'chat_default') FROM messages"
        )
        db.execute(
            """CREATE TABLE messages_v1_clean(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content TEXT NOT NULL,
                created_ms INTEGER NOT NULL,
                model TEXT NOT NULL DEFAULT ''
            )"""
        )
        db.execute(
            "INSERT INTO messages_v1_clean(id,role,content,created_ms,model) SELECT id,role,content,created_ms,model FROM messages"
        )
        db.execute("DROP TABLE messages")
        db.execute("ALTER TABLE messages_v1_clean RENAME TO messages")
    db.execute("CREATE INDEX IF NOT EXISTS messages_recent ON messages(id DESC)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_thread_messages_tid ON thread_messages(thread_id, message_id DESC)"
    )
    db.execute(
        "INSERT OR IGNORE INTO thread_messages(message_id, thread_id) SELECT id, ? FROM messages",
        (DEFAULT_THREAD_ID,),
    )

    db.execute(
        """CREATE TABLE IF NOT EXISTS projects(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            workspace_path TEXT NOT NULL DEFAULT '',
            instructions TEXT NOT NULL DEFAULT '',
            pinned INTEGER NOT NULL DEFAULT 0,
            collapsed INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_ms INTEGER NOT NULL,
            updated_ms INTEGER NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS threads(
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT '',
            pinned INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_ms INTEGER NOT NULL,
            updated_ms INTEGER NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS state_kv(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )

    now = int(time.time() * 1000)
    thread_count = db.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    if thread_count == 0:
        first_user = db.execute(
            "SELECT content, MIN(created_ms), MAX(created_ms) FROM messages WHERE role='user'"
        ).fetchone()
        any_msg = db.execute(
            "SELECT MIN(created_ms), MAX(created_ms) FROM messages"
        ).fetchone()
        initial_title = (
            _derive_title_from_prompt(first_user[0])
            if (first_user and first_user[0])
            else DEFAULT_THREAD_TITLE
        )
        c_ms = int((any_msg[0] if any_msg and any_msg[0] else now) or now)
        u_ms = int((any_msg[1] if any_msg and any_msg[1] else now) or now)
        db.execute(
            """INSERT OR IGNORE INTO threads(id,title,project_id,pinned,sort_order,created_ms,updated_ms)
               VALUES(?,?,?,?,?,?,?)""",
            (DEFAULT_THREAD_ID, initial_title, "", 0, 0, c_ms, u_ms),
        )
        db.execute(
            "INSERT OR REPLACE INTO state_kv(key,value) VALUES('active_thread_id',?)",
            (DEFAULT_THREAD_ID,),
        )
    else:
        orphan_threads = db.execute(
            """SELECT DISTINCT tm.thread_id FROM thread_messages tm
               LEFT JOIN threads t ON t.id = tm.thread_id
               WHERE t.id IS NULL"""
        ).fetchall()
        for (orphan_id,) in orphan_threads:
            oid = str(orphan_id or DEFAULT_THREAD_ID)
            first_u = db.execute(
                """SELECT m.content, MIN(m.created_ms), MAX(m.created_ms)
                   FROM messages m JOIN thread_messages tm ON tm.message_id = m.id
                   WHERE tm.thread_id=? AND m.role='user'""",
                (oid,),
            ).fetchone()
            title = _derive_title_from_prompt(first_u[0]) if (first_u and first_u[0]) else DEFAULT_THREAD_TITLE
            c_ms = int((first_u[1] if first_u and first_u[1] else now) or now)
            u_ms = int((first_u[2] if first_u and first_u[2] else now) or now)
            db.execute(
                """INSERT OR IGNORE INTO threads(id,title,project_id,pinned,sort_order,created_ms,updated_ms)
                   VALUES(?,?,?,?,?,?,?)""",
                (oid, title, "", 0, 0, c_ms, u_ms),
            )

    active_row = db.execute(
        "SELECT value FROM state_kv WHERE key='active_thread_id'"
    ).fetchone()
    active_id = str(active_row[0]) if active_row and active_row[0] else ""
    exists = (
        db.execute("SELECT 1 FROM threads WHERE id=?", (active_id,)).fetchone()
        if active_id
        else None
    )
    if not exists:
        fallback = db.execute(
            "SELECT id FROM threads ORDER BY pinned DESC, sort_order ASC, updated_ms DESC LIMIT 1"
        ).fetchone()
        chosen = str(fallback[0]) if fallback else DEFAULT_THREAD_ID
        db.execute(
            "INSERT OR REPLACE INTO state_kv(key,value) VALUES('active_thread_id',?)",
            (chosen,),
        )


def _connect() -> sqlite3.Connection:
    target = path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = target.parent.stat()
        if parent.st_uid != os.getuid():
            raise HistoryError("history_owner_mismatch")
        os.chmod(target.parent, 0o700)
        if not target.exists():
            fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise HistoryError("history_file_unsafe")
        os.chmod(target, 0o600)
        db = sqlite3.connect(target, timeout=1.5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=1500")
        _ensure_schema_and_migrate(db)
        db.commit()
        return db
    except HistoryError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise HistoryError("history_open_failed") from exc


def _resolve_active_thread_id(db: sqlite3.Connection, requested: str | None = None) -> str:
    req = str(requested or "").strip()
    if req:
        exists = db.execute("SELECT 1 FROM threads WHERE id=?", (req,)).fetchone()
        if exists:
            return req
    row = db.execute("SELECT value FROM state_kv WHERE key='active_thread_id'").fetchone()
    if row and row[0]:
        active = str(row[0])
        if db.execute("SELECT 1 FROM threads WHERE id=?", (active,)).fetchone():
            return active
    first = db.execute(
        "SELECT id FROM threads ORDER BY pinned DESC, sort_order ASC, updated_ms DESC LIMIT 1"
    ).fetchone()
    return str(first[0]) if first else DEFAULT_THREAD_ID


def get_active_thread_id() -> str:
    db = _connect()
    try:
        return _resolve_active_thread_id(db)
    finally:
        db.close()


def get_active_project_context() -> dict:
    """Return active thread and its associated project (if any) for agent/chat context."""
    db = _connect()
    try:
        tid = _resolve_active_thread_id(db)
        t_row = db.execute(
            "SELECT id, title, project_id FROM threads WHERE id=?", (tid,)
        ).fetchone()
        if not t_row:
            return {
                "threadId": tid,
                "threadTitle": DEFAULT_THREAD_TITLE,
                "project": None,
                "projectName": "",
                "workspacePath": "",
                "instructions": "",
            }
        pid = str(t_row[2] or "")
        proj = None
        if pid:
            p_row = db.execute(
                "SELECT id, name, workspace_path, instructions FROM projects WHERE id=?",
                (pid,),
            ).fetchone()
            if p_row:
                proj = {
                    "id": str(p_row[0]),
                    "name": str(p_row[1]),
                    "workspacePath": str(p_row[2] or ""),
                    "instructions": str(p_row[3] or ""),
                }
        return {
            "threadId": str(t_row[0]),
            "threadTitle": str(t_row[1]),
            "project": proj,
            "projectName": proj["name"] if proj else "",
            "workspacePath": proj["workspacePath"] if proj else "",
            "instructions": proj["instructions"] if proj else "",
        }
    except sqlite3.Error:
        return {
            "threadId": DEFAULT_THREAD_ID,
            "threadTitle": DEFAULT_THREAD_TITLE,
            "project": None,
            "projectName": "",
            "workspacePath": "",
            "instructions": "",
        }
    finally:
        db.close()


def _query_threads(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """SELECT t.id, t.title, t.project_id, t.pinned, t.sort_order, t.created_ms, t.updated_ms,
                  COUNT(tm.message_id) AS msg_count,
                  (SELECT m2.content FROM messages m2
                   JOIN thread_messages tm2 ON tm2.message_id = m2.id
                   WHERE tm2.thread_id = t.id ORDER BY m2.id DESC LIMIT 1) AS last_preview
           FROM threads t
           LEFT JOIN thread_messages tm ON tm.thread_id = t.id
           GROUP BY t.id
           ORDER BY t.pinned DESC, t.sort_order ASC, t.updated_ms DESC, t.created_ms DESC
           LIMIT ?""",
        (MAX_THREADS,),
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "title": str(r[1]),
            "projectId": str(r[2] or ""),
            "pinned": bool(r[3]),
            "sortOrder": int(r[4] or 0),
            "createdMs": int(r[5] or 0),
            "updatedMs": int(r[6] or 0),
            "messageCount": int(r[7] or 0),
            "preview": _clean_title(str(r[8] or ""), default="", limit=90),
        }
        for r in rows
    ]


def _query_projects(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """SELECT p.id, p.name, p.workspace_path, p.instructions, p.pinned, p.collapsed,
                  p.sort_order, p.created_ms, p.updated_ms,
                  COUNT(t.id) AS thread_count
           FROM projects p
           LEFT JOIN threads t ON t.project_id = p.id
           GROUP BY p.id
           ORDER BY p.pinned DESC, p.sort_order ASC, p.updated_ms DESC, p.created_ms DESC
           LIMIT ?""",
        (MAX_PROJECTS,),
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "name": str(r[1]),
            "workspacePath": str(r[2] or ""),
            "instructions": str(r[3] or ""),
            "pinned": bool(r[4]),
            "collapsed": bool(r[5]),
            "sortOrder": int(r[6] or 0),
            "createdMs": int(r[7] or 0),
            "updatedMs": int(r[8] or 0),
            "threadCount": int(r[9] or 0),
        }
        for r in rows
    ]


def _query_messages(
    db: sqlite3.Connection, thread_id: str, limit: int = 60, before_id: int = 0
) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    before = max(0, int(before_id or 0))
    if before:
        rows = db.execute(
            "SELECT m.id,m.role,m.content,m.created_ms,m.model FROM messages m "
            "JOIN thread_messages tm ON tm.message_id = m.id "
            "WHERE tm.thread_id=? AND m.id<? ORDER BY m.id DESC LIMIT ?",
            (thread_id, before, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT m.id,m.role,m.content,m.created_ms,m.model FROM messages m "
            "JOIN thread_messages tm ON tm.message_id = m.id "
            "WHERE tm.thread_id=? ORDER BY m.id DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
    return [
        {
            "id": row[0],
            "role": row[1],
            "content": row[2],
            "createdMs": row[3],
            "model": row[4],
        }
        for row in reversed(rows)
    ]


def _has_more_messages(db: sqlite3.Connection, thread_id: str, msgs: list[dict]) -> bool:
    if not msgs:
        return False
    oldest_id = int(msgs[0].get("id") or 0)
    if oldest_id <= 0:
        return False
    row = db.execute(
        """SELECT 1 FROM messages m
           JOIN thread_messages tm ON tm.message_id = m.id
           WHERE tm.thread_id=? AND m.id<? LIMIT 1""",
        (thread_id, oldest_id),
    ).fetchone()
    return row is not None


def load(limit: int = 60, before_id: int = 0, thread_id: str | None = None) -> list[dict]:
    db = _connect()
    try:
        tid = _resolve_active_thread_id(db, thread_id)
        return _query_messages(db, tid, limit=limit, before_id=before_id)
    except sqlite3.Error as exc:
        raise HistoryError("history_read_failed") from exc
    finally:
        db.close()


def load_with_workspace(
    limit: int = 60, before_id: int = 0, thread_id: str | None = None
) -> dict:
    db = _connect()
    try:
        tid = _resolve_active_thread_id(db, thread_id)
        msgs = _query_messages(db, tid, limit=limit, before_id=before_id)
        return {
            "messages": msgs,
            "hasMore": _has_more_messages(db, tid, msgs),
            "activeThreadId": tid,
            "threads": _query_threads(db),
            "projects": _query_projects(db),
        }
    except sqlite3.Error as exc:
        raise HistoryError("history_read_failed") from exc
    finally:
        db.close()


def _trim(db: sqlite3.Connection, thread_id: str) -> None:
    cutoff = db.execute(
        """SELECT m.id FROM messages m
           JOIN thread_messages tm ON tm.message_id = m.id
           WHERE tm.thread_id=? ORDER BY m.id DESC LIMIT 1 OFFSET ?""",
        (thread_id, MAX_ROWS - 1),
    ).fetchone()
    if cutoff:
        db.execute(
            """DELETE FROM messages WHERE id IN (
                   SELECT message_id FROM thread_messages WHERE thread_id=? AND message_id<?
               )""",
            (thread_id, cutoff[0]),
        )
        db.execute(
            "DELETE FROM thread_messages WHERE thread_id=? AND message_id<?",
            (thread_id, cutoff[0]),
        )


def _touch_thread_on_message(
    db: sqlite3.Connection, thread_id: str, user_text: str | None, now_ms: int
) -> None:
    row = db.execute(
        "SELECT title FROM threads WHERE id=?", (thread_id,)
    ).fetchone()
    if not row:
        title = _derive_title_from_prompt(user_text or "") if user_text else DEFAULT_THREAD_TITLE
        db.execute(
            """INSERT INTO threads(id,title,project_id,pinned,sort_order,created_ms,updated_ms)
               VALUES(?,?,?,?,?,?,?)""",
            (thread_id, title, "", 0, 0, now_ms, now_ms),
        )
        return
    current_title = str(row[0] or "")
    if user_text and current_title in (DEFAULT_THREAD_TITLE, "Cuộc trò chuyện mới", "Untitled", ""):
        msg_count = db.execute(
            """SELECT COUNT(*) FROM messages m
               JOIN thread_messages tm ON tm.message_id = m.id
               WHERE tm.thread_id=? AND m.role='user'""",
            (thread_id,),
        ).fetchone()[0]
        if msg_count <= 1:
            new_title = _derive_title_from_prompt(user_text)
            db.execute(
                "UPDATE threads SET title=?, updated_ms=? WHERE id=?",
                (new_title, now_ms, thread_id),
            )
            return
    db.execute("UPDATE threads SET updated_ms=? WHERE id=?", (now_ms, thread_id))


def append(role: str, content: str, model: str = "", thread_id: str | None = None) -> int:
    if role not in ("user", "assistant"):
        raise HistoryError("invalid_role")
    value = str(content).strip()
    if not value:
        raise HistoryError("empty_content")
    value = value[:MAX_CONTENT]
    model = str(model)[:160]
    db = _connect()
    try:
        tid = _resolve_active_thread_id(db, thread_id)
        now = int(time.time() * 1000)
        with db:
            cursor = db.execute(
                "INSERT INTO messages(role,content,created_ms,model) VALUES(?,?,?,?)",
                (role, value, now, model),
            )
            msg_id = int(cursor.lastrowid)
            db.execute(
                "INSERT OR REPLACE INTO thread_messages(message_id,thread_id) VALUES(?,?)",
                (msg_id, tid),
            )
            _touch_thread_on_message(db, tid, value if role == "user" else None, now)
            _trim(db, tid)
        return msg_id
    except sqlite3.Error as exc:
        raise HistoryError("history_write_failed") from exc
    finally:
        db.close()


def append_exchange(
    user_text: str, assistant_text: str, model: str = "", thread_id: str | None = None
) -> tuple[int, int]:
    user = str(user_text).strip()[:MAX_CONTENT]
    assistant = str(assistant_text).strip()[:MAX_CONTENT]
    if not user or not assistant:
        raise HistoryError("empty_content")
    model = str(model)[:160]
    db = _connect()
    try:
        tid = _resolve_active_thread_id(db, thread_id)
        now = int(time.time() * 1000)
        with db:
            user_cursor = db.execute(
                "INSERT INTO messages(role,content,created_ms,model) VALUES('user',?,?,?)",
                (user, now, model),
            )
            u_id = int(user_cursor.lastrowid)
            db.execute(
                "INSERT OR REPLACE INTO thread_messages(message_id,thread_id) VALUES(?,?)",
                (u_id, tid),
            )
            assistant_cursor = db.execute(
                "INSERT INTO messages(role,content,created_ms,model) VALUES('assistant',?,?,?)",
                (assistant, now + 1, model),
            )
            a_id = int(assistant_cursor.lastrowid)
            db.execute(
                "INSERT OR REPLACE INTO thread_messages(message_id,thread_id) VALUES(?,?)",
                (a_id, tid),
            )
            _touch_thread_on_message(db, tid, user, now + 1)
            _trim(db, tid)
        return u_id, a_id
    except sqlite3.Error as exc:
        raise HistoryError("history_write_failed") from exc
    finally:
        db.close()


def clear(thread_id: str | None = None) -> None:
    db = _connect()
    try:
        tid = _resolve_active_thread_id(db, thread_id)
        now = int(time.time() * 1000)
        with db:
            db.execute(
                "DELETE FROM messages WHERE id IN (SELECT message_id FROM thread_messages WHERE thread_id=?)",
                (tid,),
            )
            db.execute("DELETE FROM thread_messages WHERE thread_id=?", (tid,))
            db.execute(
                "UPDATE threads SET title=?, updated_ms=? WHERE id=?",
                (DEFAULT_THREAD_TITLE, now, tid),
            )
            _sync_project_workspace_to_session(db, tid, force_reset_ephemeral=True)
    except sqlite3.Error as exc:
        raise HistoryError("history_clear_failed") from exc
    finally:
        db.close()


def _sync_project_workspace_to_session(
    db: sqlite3.Connection, thread_id: str, force_reset_ephemeral: bool = False
) -> None:
    """When switching to a thread, sync its Project workspace_path and clear stale thread-specific prompts."""
    try:
        row = db.execute(
            """SELECT p.workspace_path FROM threads t
               JOIN projects p ON p.id = t.project_id
               WHERE t.id = ?""",
            (thread_id,),
        ).fetchone()
        import run_store
        ctx = run_store.load_session_context()
        prev_tid = str(ctx.get("activeThreadId") or "")
        changed = False
        if force_reset_ephemeral or (prev_tid and prev_tid != thread_id):
            ctx["pendingClarification"] = None
            ctx["pendingDeletePath"] = ""
            ctx["pendingMemoryForget"] = None
            ctx["pendingSudoOp"] = None
            changed = True
            try:
                import permission_store
                permission_store.resolve_pending_confirmation(decision="cancel", source="thread_switch")
            except Exception:
                pass
        if prev_tid != thread_id:
            ctx["activeThreadId"] = thread_id
            changed = True
        resolved_ws = ""
        if row and row[0]:
            wpath = str(row[0]).strip()
            p = Path(wpath).expanduser()
            if p.is_dir():
                resolved_ws = str(p.resolve())
        if resolved_ws:
            if ctx.get("lastProject") != resolved_ws or ctx.get("lastDirectory") != resolved_ws:
                ctx["lastProject"] = resolved_ws
                ctx["lastDirectory"] = resolved_ws
                changed = True
        else:
            if ctx.get("lastProject"):
                ctx["lastProject"] = ""
                changed = True
        if changed:
            run_store.save_session_context(ctx)
    except Exception:
        pass


def execute_thread_action(payload: dict) -> dict:
    op = str(payload.get("op") or payload.get("subaction") or "list").strip().lower()
    db = _connect()
    try:
        now = int(time.time() * 1000)
        with db:
            if op == "list":
                tid = _resolve_active_thread_id(db, payload.get("threadId"))
                msgs = _query_messages(db, tid, limit=int(payload.get("limit") or 60))
                return {
                    "op": "list",
                    "activeThreadId": tid,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                    "messages": msgs,
                    "hasMore": _has_more_messages(db, tid, msgs),
                }

            if op == "create_thread":
                title = _clean_title(payload.get("title", ""), default=DEFAULT_THREAD_TITLE)
                project_id = str(payload.get("projectId") or "").strip()
                if project_id:
                    if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                        project_id = ""
                new_id = _gen_id("chat")
                min_order_row = db.execute(
                    "SELECT COALESCE(MIN(sort_order), 0) FROM threads WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                sort_order = int(min_order_row[0] or 0) - 1
                db.execute(
                    """INSERT INTO threads(id,title,project_id,pinned,sort_order,created_ms,updated_ms)
                       VALUES(?,?,?,?,?,?,?)""",
                    (new_id, title, project_id, 0, sort_order, now, now),
                )
                if payload.get("select", True) is not False:
                    db.execute(
                        "INSERT OR REPLACE INTO state_kv(key,value) VALUES('active_thread_id',?)",
                        (new_id,),
                    )
                    _sync_project_workspace_to_session(db, new_id, force_reset_ephemeral=True)
                active_id = _resolve_active_thread_id(db)
                msgs = _query_messages(db, active_id, limit=60)
                return {
                    "op": "create_thread",
                    "threadId": new_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                    "messages": msgs,
                    "hasMore": _has_more_messages(db, active_id, msgs),
                }

            if op == "select_thread":
                target_id = str(payload.get("threadId") or "").strip()
                if not target_id or not db.execute("SELECT 1 FROM threads WHERE id=?", (target_id,)).fetchone():
                    raise HistoryError("thread_not_found", "Selected chat thread does not exist.")
                db.execute(
                    "INSERT OR REPLACE INTO state_kv(key,value) VALUES('active_thread_id',?)",
                    (target_id,),
                )
                _sync_project_workspace_to_session(db, target_id)
                msgs = _query_messages(db, target_id, limit=int(payload.get("limit") or 60))
                return {
                    "op": "select_thread",
                    "activeThreadId": target_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                    "messages": msgs,
                    "hasMore": _has_more_messages(db, target_id, msgs),
                }

            if op == "rename_thread":
                target_id = str(payload.get("threadId") or "").strip()
                title = _clean_title(payload.get("title", ""), default="")
                if not target_id or not title:
                    raise HistoryError("invalid_thread_rename", "Provide a thread ID and non-empty title.")
                db.execute(
                    "UPDATE threads SET title=?, updated_ms=? WHERE id=?",
                    (title, now, target_id),
                )
                active_id = _resolve_active_thread_id(db)
                return {
                    "op": "rename_thread",
                    "threadId": target_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                }

            if op == "pin_thread":
                target_id = str(payload.get("threadId") or "").strip()
                row = db.execute("SELECT pinned FROM threads WHERE id=?", (target_id,)).fetchone()
                if not row:
                    raise HistoryError("thread_not_found", "Chat thread not found.")
                desired = payload.get("pinned")
                new_pin = (0 if row[0] else 1) if desired is None else (1 if desired else 0)
                db.execute(
                    "UPDATE threads SET pinned=?, updated_ms=? WHERE id=?",
                    (new_pin, now, target_id),
                )
                active_id = _resolve_active_thread_id(db)
                return {
                    "op": "pin_thread",
                    "threadId": target_id,
                    "pinned": bool(new_pin),
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                }

            if op == "move_thread":
                target_id = str(payload.get("threadId") or "").strip()
                row = db.execute(
                    "SELECT project_id, pinned FROM threads WHERE id=?", (target_id,)
                ).fetchone()
                if not row:
                    raise HistoryError("thread_not_found", "Chat thread not found.")
                new_project_id = (
                    str(payload.get("projectId")).strip()
                    if "projectId" in payload and payload.get("projectId") is not None
                    else str(row[0] or "")
                )
                if new_project_id and not db.execute(
                    "SELECT 1 FROM projects WHERE id=?", (new_project_id,)
                ).fetchone():
                    new_project_id = ""
                db.execute(
                    "UPDATE threads SET project_id=?, updated_ms=? WHERE id=?",
                    (new_project_id, now, target_id),
                )
                siblings = [
                    r[0]
                    for r in db.execute(
                        """SELECT id FROM threads
                           WHERE project_id=? AND id!=?
                           ORDER BY pinned DESC, sort_order ASC, updated_ms DESC""",
                        (new_project_id, target_id),
                    ).fetchall()
                ]
                target_index = payload.get("targetIndex")
                if isinstance(target_index, int) and 0 <= target_index <= len(siblings):
                    siblings.insert(target_index, target_id)
                else:
                    siblings.insert(0, target_id)
                for idx, tid_item in enumerate(siblings):
                    db.execute("UPDATE threads SET sort_order=? WHERE id=?", (idx, tid_item))
                active_id = _resolve_active_thread_id(db)
                if target_id == active_id:
                    _sync_project_workspace_to_session(db, active_id)
                return {
                    "op": "move_thread",
                    "threadId": target_id,
                    "projectId": new_project_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                }

            if op == "delete_thread":
                target_id = str(payload.get("threadId") or "").strip()
                if not target_id:
                    raise HistoryError("thread_not_found", "Chat thread not found.")
                prev_active = _resolve_active_thread_id(db)
                db.execute(
                    "DELETE FROM messages WHERE id IN (SELECT message_id FROM thread_messages WHERE thread_id=?)",
                    (target_id,),
                )
                db.execute("DELETE FROM thread_messages WHERE thread_id=?", (target_id,))
                db.execute("DELETE FROM threads WHERE id=?", (target_id,))
                remaining = db.execute(
                    "SELECT id FROM threads ORDER BY pinned DESC, sort_order ASC, updated_ms DESC LIMIT 1"
                ).fetchone()
                if not remaining:
                    fresh_id = DEFAULT_THREAD_ID
                    db.execute(
                        """INSERT INTO threads(id,title,project_id,pinned,sort_order,created_ms,updated_ms)
                           VALUES(?,?,?,?,?,?,?)""",
                        (fresh_id, DEFAULT_THREAD_TITLE, "", 0, 0, now, now),
                    )
                    next_active = fresh_id
                else:
                    next_active = str(remaining[0])
                current_active = _resolve_active_thread_id(db)
                switched = False
                if current_active == target_id or not db.execute(
                    "SELECT 1 FROM threads WHERE id=?", (current_active,)
                ).fetchone():
                    current_active = next_active
                    switched = True
                    db.execute(
                        "INSERT OR REPLACE INTO state_kv(key,value) VALUES('active_thread_id',?)",
                        (current_active,),
                    )
                _sync_project_workspace_to_session(
                    db, current_active, force_reset_ephemeral=(switched or prev_active == target_id)
                )
                msgs = _query_messages(db, current_active, limit=60)
                return {
                    "op": "delete_thread",
                    "deletedThreadId": target_id,
                    "activeThreadId": current_active,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                    "messages": msgs,
                    "hasMore": _has_more_messages(db, current_active, msgs),
                }

            if op == "clear_thread":
                target_id = str(payload.get("threadId") or "").strip() or _resolve_active_thread_id(db)
                if not target_id or not db.execute("SELECT 1 FROM threads WHERE id=?", (target_id,)).fetchone():
                    raise HistoryError("thread_not_found", "Chat thread not found.")
                db.execute(
                    "DELETE FROM messages WHERE id IN (SELECT message_id FROM thread_messages WHERE thread_id=?)",
                    (target_id,),
                )
                db.execute("DELETE FROM thread_messages WHERE thread_id=?", (target_id,))
                db.execute("UPDATE threads SET updated_ms=? WHERE id=?", (now, target_id))
                active_id = _resolve_active_thread_id(db)
                if target_id == active_id:
                    _sync_project_workspace_to_session(db, active_id, force_reset_ephemeral=True)
                msgs = _query_messages(db, active_id, limit=60)
                return {
                    "op": "clear_thread",
                    "threadId": target_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                    "messages": msgs,
                    "hasMore": _has_more_messages(db, active_id, msgs),
                }

            if op == "duplicate_thread":
                target_id = str(payload.get("threadId") or "").strip() or _resolve_active_thread_id(db)
                row = db.execute(
                    "SELECT title, project_id, pinned FROM threads WHERE id=?", (target_id,)
                ).fetchone()
                if not row:
                    raise HistoryError("thread_not_found", "Chat thread not found.")
                orig_title = str(row[0] or DEFAULT_THREAD_TITLE)
                dup_title = _clean_title(
                    payload.get("title") or f"{orig_title} (Copy)",
                    default=f"{orig_title} (Copy)",
                )
                proj_id = str(row[1] or "")
                new_id = _gen_id("chat")
                min_order_row = db.execute(
                    "SELECT COALESCE(MIN(sort_order), 0) FROM threads WHERE project_id=?",
                    (proj_id,),
                ).fetchone()
                sort_order = int(min_order_row[0] or 0) - 1
                db.execute(
                    """INSERT INTO threads(id,title,project_id,pinned,sort_order,created_ms,updated_ms)
                       VALUES(?,?,?,?,?,?,?)""",
                    (new_id, dup_title, proj_id, 0, sort_order, now, now),
                )
                src_msgs = db.execute(
                    """SELECT m.role, m.content, m.created_ms, m.model
                       FROM messages m JOIN thread_messages tm ON tm.message_id = m.id
                       WHERE tm.thread_id=?
                       ORDER BY m.id DESC
                       LIMIT ?""",
                    (target_id, MAX_ROWS),
                ).fetchall()
                src_msgs.reverse()
                for m_role, m_content, m_created, m_model in src_msgs:
                    cur = db.execute(
                        "INSERT INTO messages(role,content,created_ms,model) VALUES(?,?,?,?)",
                        (str(m_role), str(m_content), int(m_created or now), str(m_model or "")),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO thread_messages(message_id,thread_id) VALUES(?,?)",
                        (int(cur.lastrowid), new_id),
                    )
                if payload.get("select", True) is not False:
                    db.execute(
                        "INSERT OR REPLACE INTO state_kv(key,value) VALUES('active_thread_id',?)",
                        (new_id,),
                    )
                    _sync_project_workspace_to_session(db, new_id, force_reset_ephemeral=True)
                active_id = _resolve_active_thread_id(db)
                msgs = _query_messages(db, active_id, limit=60)
                return {
                    "op": "duplicate_thread",
                    "threadId": new_id,
                    "sourceThreadId": target_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                    "messages": msgs,
                    "hasMore": _has_more_messages(db, active_id, msgs),
                }

            if op == "search_threads":
                query = str(payload.get("query") or "").strip()
                active_id = _resolve_active_thread_id(db)
                all_threads = _query_threads(db)
                if not query:
                    return {
                        "op": "search_threads",
                        "query": "",
                        "matchedThreadIds": [t["id"] for t in all_threads],
                        "activeThreadId": active_id,
                        "threads": all_threads,
                        "projects": _query_projects(db),
                    }
                escaped_q = (
                    query.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                like_pat = f"%{escaped_q}%"
                matched_rows = db.execute(
                    """SELECT DISTINCT t.id
                       FROM threads t
                       LEFT JOIN thread_messages tm ON tm.thread_id = t.id
                       LEFT JOIN messages m ON m.id = tm.message_id
                       WHERE t.title LIKE ? ESCAPE '\\' COLLATE NOCASE
                          OR m.content LIKE ? ESCAPE '\\' COLLATE NOCASE""",
                    (like_pat, like_pat),
                ).fetchall()
                matched_ids = [str(r[0]) for r in matched_rows if r and r[0]]
                return {
                    "op": "search_threads",
                    "query": query,
                    "matchedThreadIds": matched_ids,
                    "activeThreadId": active_id,
                    "threads": all_threads,
                    "projects": _query_projects(db),
                }

            if op == "create_project":
                name = _clean_title(payload.get("name", ""), default="New Project", limit=60)
                workspace_path = str(payload.get("workspacePath") or "").strip()[:1024]
                if workspace_path:
                    workspace_path = str(Path(workspace_path).expanduser())
                instructions = str(payload.get("instructions") or "").strip()[:1200]
                proj_id = _gen_id("proj")
                min_order_row = db.execute(
                    "SELECT COALESCE(MIN(sort_order), 0) FROM projects"
                ).fetchone()
                sort_order = int(min_order_row[0] or 0) - 1
                db.execute(
                    """INSERT INTO projects(id,name,workspace_path,instructions,pinned,collapsed,sort_order,created_ms,updated_ms)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (proj_id, name, workspace_path, instructions, 0, 0, sort_order, now, now),
                )
                create_initial_chat = payload.get("createThread", False) is True
                active_id = _resolve_active_thread_id(db)
                if create_initial_chat:
                    chat_id = _gen_id("chat")
                    db.execute(
                        """INSERT INTO threads(id,title,project_id,pinned,sort_order,created_ms,updated_ms)
                           VALUES(?,?,?,?,?,?,?)""",
                        (chat_id, DEFAULT_THREAD_TITLE, proj_id, 0, 0, now, now),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO state_kv(key,value) VALUES('active_thread_id',?)",
                        (chat_id,),
                    )
                    active_id = chat_id
                    _sync_project_workspace_to_session(db, chat_id, force_reset_ephemeral=True)
                return {
                    "op": "create_project",
                    "projectId": proj_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                }

            if op == "rename_project":
                proj_id = str(payload.get("projectId") or "").strip()
                row = db.execute(
                    "SELECT name, workspace_path, instructions FROM projects WHERE id=?",
                    (proj_id,),
                ).fetchone()
                if not row:
                    raise HistoryError("project_not_found", "Project not found.")
                new_name = _clean_title(payload.get("name") or row[0], default=str(row[0]), limit=60)
                new_ws = (
                    str(Path(str(payload.get("workspacePath")).strip()).expanduser())
                    if payload.get("workspacePath")
                    else ("" if payload.get("workspacePath") == "" else str(row[1] or ""))
                )
                new_inst = (
                    str(payload.get("instructions"))[:1200]
                    if payload.get("instructions") is not None
                    else str(row[2] or "")
                )
                db.execute(
                    "UPDATE projects SET name=?, workspace_path=?, instructions=?, updated_ms=? WHERE id=?",
                    (new_name, new_ws, new_inst, now, proj_id),
                )
                active_id = _resolve_active_thread_id(db)
                _sync_project_workspace_to_session(db, active_id)
                return {
                    "op": "rename_project",
                    "projectId": proj_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                }

            if op == "pin_project":
                proj_id = str(payload.get("projectId") or "").strip()
                row = db.execute("SELECT pinned FROM projects WHERE id=?", (proj_id,)).fetchone()
                if not row:
                    raise HistoryError("project_not_found", "Project not found.")
                desired = payload.get("pinned")
                new_pin = (0 if row[0] else 1) if desired is None else (1 if desired else 0)
                db.execute(
                    "UPDATE projects SET pinned=?, updated_ms=? WHERE id=?",
                    (new_pin, now, proj_id),
                )
                active_id = _resolve_active_thread_id(db)
                return {
                    "op": "pin_project",
                    "projectId": proj_id,
                    "pinned": bool(new_pin),
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                }

            if op == "toggle_project_collapsed":
                proj_id = str(payload.get("projectId") or "").strip()
                row = db.execute("SELECT collapsed FROM projects WHERE id=?", (proj_id,)).fetchone()
                if not row:
                    raise HistoryError("project_not_found", "Project not found.")
                desired = payload.get("collapsed")
                new_col = (0 if row[0] else 1) if desired is None else (1 if desired else 0)
                db.execute(
                    "UPDATE projects SET collapsed=? WHERE id=?",
                    (new_col, proj_id),
                )
                active_id = _resolve_active_thread_id(db)
                return {
                    "op": "toggle_project_collapsed",
                    "projectId": proj_id,
                    "collapsed": bool(new_col),
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                }

            if op == "move_project":
                proj_id = str(payload.get("projectId") or "").strip()
                if not db.execute("SELECT 1 FROM projects WHERE id=?", (proj_id,)).fetchone():
                    raise HistoryError("project_not_found", "Project not found.")
                others = [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM projects WHERE id!=? ORDER BY pinned DESC, sort_order ASC, updated_ms DESC",
                        (proj_id,),
                    ).fetchall()
                ]
                target_index = payload.get("targetIndex")
                if isinstance(target_index, int) and 0 <= target_index <= len(others):
                    others.insert(target_index, proj_id)
                else:
                    others.insert(0, proj_id)
                for idx, pid_item in enumerate(others):
                    db.execute("UPDATE projects SET sort_order=? WHERE id=?", (idx, pid_item))
                active_id = _resolve_active_thread_id(db)
                return {
                    "op": "move_project",
                    "projectId": proj_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                }

            if op == "delete_project":
                proj_id = str(payload.get("projectId") or "").strip()
                if not proj_id:
                    raise HistoryError("project_not_found", "Project not found.")
                prev_active = _resolve_active_thread_id(db)
                delete_threads = payload.get("deleteThreads") is True
                if delete_threads:
                    t_ids = [
                        r[0]
                        for r in db.execute(
                            "SELECT id FROM threads WHERE project_id=?", (proj_id,)
                        ).fetchall()
                    ]
                    for tid_item in t_ids:
                        db.execute(
                            "DELETE FROM messages WHERE id IN (SELECT message_id FROM thread_messages WHERE thread_id=?)",
                            (tid_item,),
                        )
                        db.execute("DELETE FROM thread_messages WHERE thread_id=?", (tid_item,))
                    db.execute("DELETE FROM threads WHERE project_id=?", (proj_id,))
                else:
                    db.execute(
                        "UPDATE threads SET project_id='', updated_ms=? WHERE project_id=?",
                        (now, proj_id),
                    )
                db.execute("DELETE FROM projects WHERE id=?", (proj_id,))
                if db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 0:
                    db.execute(
                        """INSERT INTO threads(id,title,project_id,pinned,sort_order,created_ms,updated_ms)
                           VALUES(?,?,?,?,?,?,?)""",
                        (DEFAULT_THREAD_ID, DEFAULT_THREAD_TITLE, "", 0, 0, now, now),
                    )
                active_id = _resolve_active_thread_id(db)
                db.execute(
                    "INSERT OR REPLACE INTO state_kv(key,value) VALUES('active_thread_id',?)",
                    (active_id,),
                )
                _sync_project_workspace_to_session(
                    db, active_id, force_reset_ephemeral=(active_id != prev_active)
                )
                msgs = _query_messages(db, active_id, limit=60)
                return {
                    "op": "delete_project",
                    "deletedProjectId": proj_id,
                    "activeThreadId": active_id,
                    "threads": _query_threads(db),
                    "projects": _query_projects(db),
                    "messages": msgs,
                    "hasMore": _has_more_messages(db, active_id, msgs),
                }

        raise HistoryError("invalid_thread_op", f"Unsupported thread operation: {op}")
    except HistoryError:
        raise
    except sqlite3.Error as exc:
        raise HistoryError("history_write_failed") from exc
    finally:
        db.close()
