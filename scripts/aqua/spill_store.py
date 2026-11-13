#!/usr/bin/env python3
"""Session-scoped tool output spill store for Alis-Companion.

Replaces destructive middle/tail truncation of large tool outputs with atomic
0600 UTF-8 spill files under <companion_state_dir>/spills/<session_id>/<call_id>.txt,
returning a structured head+tail preview and exposing a confined read-only
`read_spill` inspector.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import time

DEFAULT_INLINE_MAX_CHARS = 12_000
DEFAULT_INLINE_MAX_LINES = 200
HEAD_MAX_LINES = 60
HEAD_MAX_CHARS = 3_500
TAIL_MAX_LINES = 30
TAIL_MAX_CHARS = 1_800
DEFAULT_RETENTION_DAYS = 7
MAX_READ_LIMIT_LINES = 500
MAX_READ_INLINE_CHARS = 11_500

_LAST_PRUNE_MONO: float = 0.0
_PRUNE_INTERVAL_SEC: float = 300.0


class SpillError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def companion_state_dir() -> Path:
    """Resolve the Companion state root, honoring test/sandbox env overrides."""
    explicit_state = os.environ.get("ALIS_COMPANION_STATE_DIR", "").strip()
    if explicit_state:
        return Path(explicit_state).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "alis-companion"
    # If isolated history/runs DB env vars are set without XDG_STATE_HOME, stay beside them in /tmp.
    runs_db = os.environ.get("ALIS_AQUA_RUNS_DB", "").strip()
    if runs_db:
        return Path(runs_db).expanduser().parent
    hist_db = os.environ.get("ALIS_AQUA_HISTORY_DB", "").strip()
    if hist_db:
        return Path(hist_db).expanduser().parent
    return Path.home() / ".local/state/alis-companion"


def spills_root_dir() -> Path:
    explicit_spills = os.environ.get("ALIS_AQUA_SPILLS_DIR", "").strip()
    if explicit_spills:
        return Path(explicit_spills).expanduser()
    return companion_state_dir() / "spills"


def sanitize_id(raw: str, fallback: str = "default", max_len: int = 72) -> str:
    """Produce a filesystem-safe single path segment with collision-resistant suffix if altered."""
    text = str(raw or "").strip()
    if not text:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    if not cleaned or cleaned in (".", ".."):
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"{fallback}_{digest}"
    if cleaned != text or len(cleaned) > max_len:
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:10]
        prefix = cleaned[: max(8, max_len - 11)].rstrip("._-") or fallback
        return f"{prefix}_{digest}"
    return cleaned


def _ensure_private_dir(folder: Path) -> Path:
    """Create or validate a non-symlink 0700 directory."""
    # Walk existing parents to ensure no component inside spills_root is a symlink.
    if folder.exists() or folder.is_symlink():
        st = folder.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise SpillError("spill_unsafe_dir", f"Spill directory is not a regular directory: {folder}")
        if st.st_uid == os.getuid():
            os.chmod(folder, 0o700)
        return folder
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = folder.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise SpillError("spill_unsafe_dir", f"Spill directory is unsafe: {folder}")
    if st.st_uid == os.getuid():
        os.chmod(folder, 0o700)
    return folder


def session_spill_dir(session_id: str = "default", create: bool = False) -> Path:
    root = spills_root_dir()
    safe_sid = sanitize_id(session_id or "default", fallback="default")
    target = root / safe_sid
    if create:
        _ensure_private_dir(root)
        _ensure_private_dir(target)
    return target


def _bounded_head(lines: list[str], max_lines: int = HEAD_MAX_LINES, max_chars: int = HEAD_MAX_CHARS) -> str:
    chosen: list[str] = []
    used = 0
    for ln in lines[:max_lines]:
        piece = ln
        add_len = len(piece) + (1 if chosen else 0)
        if used + add_len > max_chars:
            rem = max_chars - used - (1 if chosen else 0)
            if rem > 32:
                chosen.append(piece[:rem] + "…")
            elif not chosen:
                chosen.append(piece[:max_chars])
            break
        chosen.append(piece)
        used += add_len
    return "\n".join(chosen)


def _bounded_tail(lines: list[str], max_lines: int = TAIL_MAX_LINES, max_chars: int = TAIL_MAX_CHARS) -> str:
    chosen_rev: list[str] = []
    used = 0
    for ln in reversed(lines[-max_lines:]):
        piece = ln
        add_len = len(piece) + (1 if chosen_rev else 0)
        if used + add_len > max_chars:
            rem = max_chars - used - (1 if chosen_rev else 0)
            if rem > 32:
                chosen_rev.append("…" + piece[-rem:])
            elif not chosen_rev:
                chosen_rev.append(piece[-max_chars:])
            break
        chosen_rev.append(piece)
        used += add_len
    chosen_rev.reverse()
    return "\n".join(chosen_rev)


def save_spill(
    text: str,
    session_id: str = "default",
    call_id: str = "",
    tool_name: str = "",
    prune_expired: bool = True,
) -> dict:
    """Atomically write full UTF-8 tool output to a 0600 session-scoped spill file."""
    global _LAST_PRUNE_MONO
    payload_str = str(text if text is not None else "")
    utf8_bytes = payload_str.encode("utf-8", errors="replace")
    lines = payload_str.splitlines()
    total_lines = len(lines)
    total_bytes = len(utf8_bytes)

    safe_sid = sanitize_id(session_id or "default", fallback="default")
    raw_cid = str(call_id or "").strip()
    if not raw_cid:
        prefix = sanitize_id(tool_name or "tool", fallback="tool", max_len=24)
        digest = hashlib.sha256(
            f"{time.time_ns()}:{os.getpid()}:{total_bytes}".encode("utf-8")
        ).hexdigest()[:10]
        raw_cid = f"{prefix}_{digest}"
    safe_cid = sanitize_id(raw_cid, fallback="call", max_len=64)

    sdir = session_spill_dir(safe_sid, create=True)
    dest = sdir / f"{safe_cid}.txt"
    if dest.exists() or dest.is_symlink():
        st = dest.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise SpillError("spill_unsafe_target", f"Refusing to overwrite non-regular spill target: {dest}")

    fd, tmp_name = tempfile.mkstemp(prefix=".spill_tmp_", suffix=".tmp", dir=str(sdir))
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(utf8_bytes)
        written = 0
        while written < len(view):
            written += os.write(fd, view[written:])
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp_path, dest)
        os.chmod(dest, 0o600)
    except Exception as exc:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SpillError("spill_write_failed", f"Could not write tool spill file: {exc}") from exc

    if prune_expired:
        now_mono = time.monotonic()
        if now_mono - _LAST_PRUNE_MONO >= _PRUNE_INTERVAL_SEC:
            _LAST_PRUNE_MONO = now_mono
            try:
                prune_expired_spills(
                    max_age_days=DEFAULT_RETENTION_DAYS,
                    active_session_ids={safe_sid},
                )
            except Exception:
                pass

    return {
        "spillId": safe_cid,
        "path": str(dest),
        "sessionId": safe_sid,
        "toolName": str(tool_name or ""),
        "totalLines": total_lines,
        "totalBytes": total_bytes,
    }


def format_spill_preview(
    text: str,
    spill_meta: dict,
    head_lines: int = HEAD_MAX_LINES,
    head_chars: int = HEAD_MAX_CHARS,
    tail_lines: int = TAIL_MAX_LINES,
    tail_chars: int = TAIL_MAX_CHARS,
) -> str:
    """Format a structured head + marker + tail inline preview for a spilled tool output."""
    lines = str(text or "").splitlines()
    total_lines = int(spill_meta.get("totalLines") or len(lines))
    total_bytes = int(spill_meta.get("totalBytes") or len(str(text or "").encode("utf-8", errors="replace")))
    spill_id = str(spill_meta.get("spillId") or "")
    spill_path = str(spill_meta.get("path") or "")

    head = _bounded_head(lines, max_lines=head_lines, max_chars=head_chars)
    marker = (
        f"[... spilled {total_lines} lines / {total_bytes} UTF-8 bytes to {spill_path} "
        f"— call read_spill(spill_id=\"{spill_id}\", offset_line=1, limit_lines=120) to inspect exact ranges ...]"
    )
    if len(lines) <= head_lines and len(str(text or "")) > head_chars:
        # Single huge line or few huge lines: take tail from raw text end
        raw_s = str(text or "")
        tail = "…" + raw_s[-tail_chars:] if len(raw_s) > tail_chars else raw_s
    else:
        tail_slice = lines[head_lines:] if len(lines) > head_lines else []
        tail = _bounded_tail(tail_slice, max_lines=tail_lines, max_chars=tail_chars) if tail_slice else ""

    if tail:
        return f"{head}\n{marker}\n{tail}"
    return f"{head}\n{marker}"


def maybe_spill_tool_output(
    text: str,
    session_id: str = "default",
    call_id: str = "",
    tool_name: str = "",
    max_inline_chars: int = DEFAULT_INLINE_MAX_CHARS,
    max_inline_lines: int = DEFAULT_INLINE_MAX_LINES,
) -> tuple[str, dict | None]:
    """Return `(inline_text, spill_meta_or_None)`. Never re-spills `read_spill` outputs."""
    payload_str = str(text if text is not None else "")
    if str(tool_name or "").strip() == "read_spill":
        return payload_str, None
    lines = payload_str.splitlines()
    if len(payload_str) <= max_inline_chars and len(lines) <= max_inline_lines:
        return payload_str, None
    meta = save_spill(
        payload_str,
        session_id=session_id,
        call_id=call_id,
        tool_name=tool_name,
    )
    preview = format_spill_preview(payload_str, meta)
    return preview, meta


def _resolve_confined_spill_file(session_id: str, spill_id: str = "", path: str = "") -> tuple[Path, str]:
    """Resolve and strictly confine a spill target to `<spills_root>/<safe_session_id>/*.txt`."""
    safe_sid = sanitize_id(session_id or "default", fallback="default")
    sdir = session_spill_dir(safe_sid, create=False)
    raw_id = str(spill_id or "").strip()
    raw_path = str(path or "").strip()

    if not raw_id and not raw_path:
        raise SpillError("spill_missing_id", "Provide spill_id (or path within current session spill dir).")

    # Reject any NUL bytes or parent traversal tokens upfront before path normalization.
    for candidate_raw in (raw_id, raw_path):
        if "\x00" in candidate_raw:
            raise SpillError("spill_access_denied", "Invalid NUL byte in spill identifier.")
        parts_check = re.split(r"[\\/]+", candidate_raw)
        if ".." in parts_check:
            raise SpillError("spill_access_denied", "Path traversal ('..') is forbidden in read_spill.")

    if raw_id:
        if "/" in raw_id or "\\" in raw_id:
            # Allow passing the exact session spill path into spill_id only if it has no traversal;
            # delegate to raw_path validation below.
            raw_path = raw_id
            raw_id = ""
        else:
            stem = raw_id[:-4] if raw_id.endswith(".txt") else raw_id
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", stem):
                raise SpillError("spill_access_denied", f"Invalid spill_id: {raw_id}")
            target = sdir / f"{stem}.txt"
            resolved_id = stem

    if not raw_id:
        cand = Path(raw_path).expanduser()
        if not cand.is_absolute():
            # Relative path must be a bare filename inside session dir (no subdirs).
            if len(cand.parts) != 1:
                raise SpillError("spill_access_denied", "Relative spill path must be a single filename.")
            target = sdir / cand.name
        else:
            target = cand
        if target.suffix != ".txt":
            raise SpillError("spill_access_denied", "Spill target must be a .txt spill file in the current session.")
        resolved_id = target.stem

    # Verify root and session_dir exist and are not symlinks.
    root = spills_root_dir()
    for check_dir in (root, sdir):
        if not check_dir.exists() and not check_dir.is_symlink():
            raise SpillError("spill_not_found", f"No spill files found for session '{safe_sid}'.")
        dst = check_dir.lstat()
        if stat.S_ISLNK(dst.st_mode) or not stat.S_ISDIR(dst.st_mode):
            raise SpillError("spill_access_denied", "Spill directory cannot be a symlink.")

    # Check target before resolving so a symlink inside session_dir is rejected immediately.
    if not target.exists() and not target.is_symlink():
        # Check if target was an outside path before returning not_found vs access_denied.
        sdir_real = sdir.resolve(strict=True)
        parent_real = target.parent.resolve(strict=False)
        if parent_real != sdir_real:
            raise SpillError(
                "spill_access_denied",
                "Access denied: spill path is outside the active session spill directory.",
            )
        raise SpillError("spill_not_found", f"Spill '{resolved_id}' does not exist in session '{safe_sid}'.")

    # Reject if target or target.parent is a symlink, or if parent != sdir.
    if target.parent.is_symlink():
        raise SpillError("spill_access_denied", "Access denied: symlinked parent directory.")
    tst = target.lstat()
    if stat.S_ISLNK(tst.st_mode) or not stat.S_ISREG(tst.st_mode):
        raise SpillError("spill_access_denied", "Access denied: spill file cannot be a symlink or special file.")
    if tst.st_uid != os.getuid():
        raise SpillError("spill_access_denied", "Access denied: spill file UID mismatch.")

    sdir_real = sdir.resolve(strict=True)
    target_real = target.resolve(strict=True)
    if target_real.parent != sdir_real:
        raise SpillError(
            "spill_access_denied",
            "Access denied: spill file is outside the current session spill directory.",
        )

    return target_real, resolved_id


def read_spill(
    session_id: str = "default",
    spill_id: str = "",
    path: str = "",
    offset_line: int = 1,
    limit_lines: int = 120,
    grep: str = "",
) -> dict:
    """Read a bounded line range or grep slice from a confined session spill file."""
    target, resolved_id = _resolve_confined_spill_file(session_id=session_id, spill_id=spill_id, path=path)
    raw_bytes = target.read_bytes()
    text = raw_bytes.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    total_lines = len(all_lines)
    total_bytes = len(raw_bytes)

    try:
        off = max(1, int(offset_line or 1))
    except (TypeError, ValueError):
        off = 1
    try:
        lim = max(1, min(MAX_READ_LIMIT_LINES, int(limit_lines or 120)))
    except (TypeError, ValueError):
        lim = 120

    grep_q = str(grep or "").strip()
    numbered: list[tuple[int, str]]
    if grep_q:
        try:
            rx = re.compile(grep_q, re.IGNORECASE)
            numbered = [(idx, ln) for idx, ln in enumerate(all_lines, start=1) if rx.search(ln)]
        except re.error:
            needle = grep_q.lower()
            numbered = [(idx, ln) for idx, ln in enumerate(all_lines, start=1) if needle in ln.lower()]
        # For grep results, offset_line >= 1 filters matching line numbers >= off
        filtered = [(idx, ln) for idx, ln in numbered if idx >= off]
        matched_count = len(filtered)
        window = filtered[:lim]
        has_more = len(filtered) > lim
    else:
        start_idx = min(total_lines, off - 1)
        end_idx = min(total_lines, start_idx + lim)
        window = [(idx, all_lines[idx - 1]) for idx in range(start_idx + 1, end_idx + 1)]
        matched_count = total_lines
        has_more = end_idx < total_lines

    formatted_lines: list[str] = []
    used_chars = 0
    truncated_by_chars = False
    for line_no, ln in window:
        entry = f"L{line_no}: {ln}"
        if len(entry) > 1200:
            entry = entry[:1200] + "…"
        add_len = len(entry) + (1 if formatted_lines else 0)
        if used_chars + add_len > MAX_READ_INLINE_CHARS:
            truncated_by_chars = True
            has_more = True
            break
        formatted_lines.append(entry)
        used_chars += add_len

    next_offset = (window[len(formatted_lines) - 1][0] + 1) if formatted_lines else off
    header = (
        f"[Spill `{resolved_id}` — {total_lines} lines / {total_bytes} bytes"
        + (f" | grep={grep_q!r} ({matched_count} matches)" if grep_q else "")
        + f" | showing {len(formatted_lines)} line(s) from L{off}"
        + (f" — more available at offset_line={next_offset}]" if has_more else " — end of range]")
    )
    body = "\n".join(formatted_lines) if formatted_lines else "(no lines in requested range)"
    return {
        "ok": True,
        "spillId": resolved_id,
        "path": str(target),
        "sessionId": sanitize_id(session_id or "default", fallback="default"),
        "totalLines": total_lines,
        "totalBytes": total_bytes,
        "matchedLines": matched_count,
        "offsetLine": off,
        "limitLines": lim,
        "returnedLines": len(formatted_lines),
        "nextOffsetLine": next_offset if has_more else None,
        "hasMore": bool(has_more or truncated_by_chars),
        "lines": formatted_lines,
        "content": f"{header}\n{body}",
    }


def _safe_rmtree_spill_session(sdir: Path) -> int:
    """Remove regular spill files inside a single session spill dir without following symlinks."""
    if not sdir.exists() and not sdir.is_symlink():
        return 0
    st = sdir.lstat()
    if stat.S_ISLNK(st.st_mode):
        sdir.unlink(missing_ok=True)
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return 0
    removed = 0
    for child in list(sdir.iterdir()):
        try:
            cst = child.lstat()
            if stat.S_ISREG(cst.st_mode) or stat.S_ISLNK(cst.st_mode):
                child.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    try:
        sdir.rmdir()
    except OSError:
        pass
    return removed


def cleanup_session_spills(session_id: str) -> int:
    """Delete all spill files belonging to `session_id`."""
    if not str(session_id or "").strip():
        return 0
    sdir = session_spill_dir(session_id, create=False)
    return _safe_rmtree_spill_session(sdir)


def prune_expired_spills(
    max_age_days: float = DEFAULT_RETENTION_DAYS,
    active_session_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    now_sec: float | None = None,
) -> int:
    """Prune inactive session spill directories older than `max_age_days`."""
    root = spills_root_dir()
    if not root.exists() or root.is_symlink():
        return 0
    rst = root.lstat()
    if not stat.S_ISDIR(rst.st_mode):
        return 0
    now_s = float(now_sec if now_sec is not None else time.time())
    cutoff = now_s - max(0.0, float(max_age_days)) * 86400.0
    protected = {
        sanitize_id(sid, fallback="default")
        for sid in (active_session_ids or ())
        if str(sid or "").strip()
    }
    pruned_dirs = 0
    for entry in list(root.iterdir()):
        try:
            est = entry.lstat()
            if stat.S_ISLNK(est.st_mode):
                continue
            if not stat.S_ISDIR(est.st_mode):
                continue
            if entry.name in protected:
                continue
            newest_mtime = est.st_mtime
            for child in entry.iterdir():
                try:
                    cst = child.lstat()
                    if cst.st_mtime > newest_mtime:
                        newest_mtime = cst.st_mtime
                except OSError:
                    pass
            if newest_mtime < cutoff:
                _safe_rmtree_spill_session(entry)
                pruned_dirs += 1
        except OSError:
            pass
    return pruned_dirs
