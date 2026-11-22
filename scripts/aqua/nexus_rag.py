#!/usr/bin/env python3
"""Unified Nexus Vault RAG Storage, Migration & Incremental Full-Vault FTS5 Engine.

Stores human-readable Markdown RAG sources & knowledge notes inside a visible
Nexus vault directory (default `<vault>/90_System/99_RAG/`) while keeping binary
SQLite FTS5 indexes and sync manifests outside the vault in `XDG_STATE_HOME`
(`quickshell/user/nexus_rag.db`) to avoid Syncthing/Git binary conflicts.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import unicodedata

from permission_store import is_forbidden_path, is_sensitive_path, is_symlink_raw

IGNORED_VAULT_DIRS = frozenset({
    ".git",
    ".obsidian",
    ".nexus",
    ".trash",
    ".stfolder",
    ".stversions",
    "node_modules",
    "target",
    "__pycache__",
    "venv",
    ".venv",
    "dist",
    "build",
    "graphify-out",
})
INDEXABLE_SUFFIXES = frozenset({".md", ".txt", ".org"})
DEFAULT_RAG_FOLDER = "90_System/99_RAG"
MAX_NOTE_BYTES = 256 * 1024
SYNC_COOLDOWN_MS = 120_000
_NEXUS_CACHE_MEMO: tuple[str, int, int, dict] | None = None


class NexusRagError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def _xdg_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))


def _expand_home(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    home_str = str(Path(os.environ.get("HOME") or str(Path.home())))
    if s == "~":
        return home_str
    if s.startswith("~/"):
        return os.path.join(home_str, s[2:])
    return s


def _fold_ascii(text: str) -> str:
    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D").lower()


def _load_alis_config() -> dict:
    cfg_home = _xdg_config_home()
    for rel in (Path("alis/config.json"), Path("quickshell/alis/config.json")):
        cand = cfg_home / rel
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def resolve_nexus_vault_path(explicit_vault: str = "") -> Path | None:
    """Resolve the active Nexus/Obsidian vault directory strictly within isolated config."""
    candidates: list[str] = []
    if str(explicit_vault or "").strip():
        candidates.append(str(explicit_vault).strip())
    env_override = os.environ.get("ALIS_NEXUS_VAULT")
    if env_override is not None:
        val = env_override.strip()
        if not val or val == "__DISABLED__":
            return None
        candidates.append(val)
    if not candidates:
        cfg = _load_alis_config()
        todo = cfg.get("todo") if isinstance(cfg.get("todo"), dict) else {}
        nx = todo.get("nexus") if isinstance(todo.get("nexus"), dict) else {}
        obs = todo.get("obsidian") if isinstance(todo.get("obsidian"), dict) else {}
        notes = cfg.get("notes") if isinstance(cfg.get("notes"), dict) else {}
        zk = notes.get("zettelkasten") if isinstance(notes.get("zettelkasten"), dict) else {}
        for raw_v in (nx.get("vaultPath"), obs.get("vaultPath"), zk.get("vaultPath")):
            if isinstance(raw_v, str) and raw_v.strip():
                candidates.append(raw_v.strip())
    for raw_c in candidates:
        expanded = _expand_home(raw_c)
        if not expanded or expanded == "__DISABLED__":
            return None
        try:
            p = Path(expanded).resolve(strict=False)
            if p.is_dir() and not is_forbidden_path(p):
                return p
        except OSError:
            continue
    return None


def resolve_rag_folder_rel() -> str:
    cfg = _load_alis_config()
    companion_mind = ((cfg.get("abyss") or {}).get("companionMind") or {}) if isinstance(cfg.get("abyss"), dict) else {}
    raw = str(companion_mind.get("ragVaultFolder") or "").strip() if isinstance(companion_mind, dict) else ""
    if not raw:
        return DEFAULT_RAG_FOLDER
    parts = [p for p in raw.replace("\\", "/").split("/") if p and p not in (".", "..")]
    if not parts or raw.startswith("/"):
        return DEFAULT_RAG_FOLDER
    return "/".join(parts)


def resolve_rag_vault_dir(explicit_vault: str = "") -> Path | None:
    vault = resolve_nexus_vault_path(explicit_vault)
    if vault is None:
        return None
    rel = resolve_rag_folder_rel()
    target = (vault / rel).resolve(strict=False)
    try:
        target.relative_to(vault)
    except ValueError:
        return None
    if is_forbidden_path(target):
        return None
    return target


def external_rag_db_path() -> Path:
    folder = (_xdg_state_home() / "quickshell" / "user").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / "nexus_rag.db"


def external_manifests_dir() -> Path:
    folder = (_xdg_state_home() / "quickshell" / "user" / "nexus_rag_manifests").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def nexus_vault_cache_path() -> Path:
    return (_xdg_state_home() / "quickshell" / "user" / "nexus_vault_cache.json").resolve()


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = str(text or "").encode("utf-8", errors="replace")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def ensure_rag_readme(rag_dir: Path) -> None:
    readme = rag_dir / "README.md"
    if readme.exists():
        return
    body = (
        "---\n"
        "title: Companion RAG Knowledge Hub\n"
        "tags:\n"
        "  - system/rag\n"
        "  - alis/companion\n"
        "---\n\n"
        "# Companion RAG Knowledge Hub\n\n"
        "Thư mục này lưu trữ tri thức & nguồn RAG của **Alis-Companion** dưới dạng Markdown dễ đọc ngay trong **Nexus**:\n\n"
        "- `RAG_Sources.md` — Danh sách thư mục & tài liệu nguồn đã đăng ký vào RAG.\n"
        "- `Canonical/` — Tài liệu chuẩn (Niri, Quickshell, Arch Linux) đã đồng bộ.\n"
        "- `Personal/` — Bản tóm tắt cấu hình máy đã lọc bỏ secrets.\n"
        "- `Knowledge/` — Ghi chú tri thức do bạn hoặc Companion lưu lại.\n\n"
        "> Chỉ mục SQLite FTS5 (`nexus_rag.db`) được đặt ngoài Vault tại `~/.local/state/quickshell/user/` để tránh xung đột đồng bộ Git/Syncthing.\n"
    )
    try:
        _atomic_write_text(readme, body, mode=0o644)
    except OSError:
        pass


def render_rag_sources_markdown(sources: list[dict], vault_path: str = "") -> str:
    clean_sources: list[dict] = []
    seen: set[str] = set()
    for s in (sources or [])[:64]:
        if not isinstance(s, dict):
            continue
        p = str(s.get("path") or "").strip()[:4096]
        if not p or p in seen:
            continue
        seen.add(p)
        clean_sources.append({
            "path": p,
            "label": str(s.get("label") or Path(p).name)[:120],
            "enabled": s.get("enabled") is not False,
            "addedAtMs": int(s.get("addedAtMs") or 0),
        })

    now_ms = int(time.time() * 1000)
    lines = [
        "---",
        "title: Companion RAG Sources",
        "tags:",
        "  - system/rag",
        "  - alis/companion",
        f"updatedAtMs: {now_ms}",
        "---",
        "",
        "# Companion RAG Sources",
        "",
    ]
    if vault_path:
        lines.append(f"- Nexus Vault: `{vault_path}`")
        lines.append("")
    if clean_sources:
        for entry in clean_sources:
            mark = "x" if entry["enabled"] else " "
            lines.append(f"- [{mark}] **{entry['label']}**: `{entry['path']}`")
    else:
        lines.append("- *(No custom external RAG folders registered yet; Nexus Vault & built-in docs are active.)*")
    lines.append("")
    raw_json = json.dumps({"version": 1, "sources": clean_sources}, ensure_ascii=False, indent=2)
    lines.append("<!-- alis-rag-sources-json")
    lines.append(raw_json)
    lines.append("-->")
    lines.append("")
    return "\n".join(lines)


def parse_rag_sources_markdown(text: str) -> list[dict]:
    raw = str(text or "")
    m = re.search(r"<!--\s*alis-rag-sources-json\s*([\s\S]*?)\s*-->", raw)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            rows = data.get("sources") if isinstance(data, dict) else None
            if isinstance(rows, list):
                valid = []
                for entry in rows[:64]:
                    if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"].strip():
                        p_str = entry["path"].strip()[:4096]
                        valid.append({
                            "path": p_str,
                            "label": str(entry.get("label") or Path(p_str).name)[:120],
                            "enabled": entry.get("enabled") is not False,
                            "addedAtMs": int(entry.get("addedAtMs") or 0),
                        })
                return valid
        except (ValueError, TypeError):
            pass

    parsed: list[dict] = []
    for line in raw.splitlines():
        m_item = re.match(r"^\s*-\s*\[([ xX])\]\s*(?:\*\*([^*]+)\*\*\s*:\s*)?`([^`]+)`", line)
        if m_item:
            mark, lbl, p_str = m_item.groups()
            p_clean = p_str.strip()[:4096]
            if p_clean:
                parsed.append({
                    "path": p_clean,
                    "label": str(lbl or Path(p_clean).name).strip()[:120],
                    "enabled": mark.lower() == "x",
                    "addedAtMs": 0,
                })
    return parsed


def _legacy_sources_file() -> Path:
    return (_xdg_state_home() / "alis-companion" / "rag_sources.json").resolve()


def _legacy_domain_rag_dir() -> Path:
    return (_xdg_state_home() / "alis-companion" / "domain_rag").resolve()


def migrate_legacy_rag_to_nexus(explicit_vault: str = "") -> dict:
    """Idempotently migrate legacy Companion RAG files into visible Nexus folders without overwriting edits."""
    rag_dir = resolve_rag_vault_dir(explicit_vault)
    vault = resolve_nexus_vault_path(explicit_vault)
    if rag_dir is None or vault is None:
        return {"migrated": False, "reason": "no_vault"}

    for sub in ("Canonical", "Personal", "Knowledge"):
        (rag_dir / sub).mkdir(parents=True, exist_ok=True)
    ensure_rag_readme(rag_dir)

    sources_md = rag_dir / "RAG_Sources.md"
    existing_sources = parse_rag_sources_markdown(sources_md.read_text(encoding="utf-8", errors="replace")) if sources_md.is_file() else []
    legacy_sources_path = _legacy_sources_file()
    legacy_sources: list[dict] = []
    if legacy_sources_path.is_file():
        try:
            raw_leg = json.loads(legacy_sources_path.read_text(encoding="utf-8"))
            if isinstance(raw_leg, dict) and isinstance(raw_leg.get("sources"), list):
                legacy_sources = [s for s in raw_leg["sources"] if isinstance(s, dict) and s.get("path")]
        except Exception:
            legacy_sources = []

    merged = list(existing_sources)
    seen_paths = {s["path"] for s in merged}
    added_sources = 0
    for ls in legacy_sources:
        p = str(ls.get("path") or "").strip()
        if p and p not in seen_paths:
            seen_paths.add(p)
            merged.append({
                "path": p[:4096],
                "label": str(ls.get("label") or Path(p).name)[:120],
                "enabled": ls.get("enabled") is not False,
                "addedAtMs": int(ls.get("addedAtMs") or 0),
            })
            added_sources += 1

    if added_sources > 0 or not sources_md.exists():
        _atomic_write_text(sources_md, render_rag_sources_markdown(merged, str(vault)), mode=0o644)

    legacy_dr = _legacy_domain_rag_dir()
    copied_docs = 0
    for src_name, dst_name in (("canonical", "Canonical"), ("personal", "Personal")):
        src_root = legacy_dr / src_name
        dst_root = rag_dir / dst_name
        if not src_root.is_dir():
            continue
        for dp, dirnames, filenames in os.walk(src_root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                src_f = Path(dp) / fn
                if src_f.is_symlink() or not src_f.is_file():
                    continue
                try:
                    rel = src_f.relative_to(src_root)
                except ValueError:
                    continue
                dst_f = dst_root / rel
                if not dst_f.exists():
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_f, dst_f)
                    copied_docs += 1

    ext_manifests = external_manifests_dir()
    for mf_name in ("manifest.json", "personal_manifest.json"):
        src_m = legacy_dr / mf_name
        dst_m = ext_manifests / mf_name
        if src_m.is_file() and not dst_m.exists():
            shutil.copy2(src_m, dst_m)

    return {
        "migrated": True,
        "ragDir": str(rag_dir),
        "sourcesMd": str(sources_md),
        "addedSources": added_sources,
        "copiedDocs": copied_docs,
    }


def load_nexus_cache(explicit_vault: str = "") -> dict:
    global _NEXUS_CACHE_MEMO
    vault = resolve_nexus_vault_path(explicit_vault)
    cache_path = nexus_vault_cache_path()
    try:
        st = cache_path.stat()
    except OSError:
        _NEXUS_CACHE_MEMO = None
        return {}
    if not cache_path.is_file():
        return {}
    cache_key = (str(cache_path), int(st.st_mtime_ns), int(st.st_size))
    memo = _NEXUS_CACHE_MEMO
    if memo is not None and memo[:3] == cache_key:
        data = memo[3]
    else:
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        _NEXUS_CACHE_MEMO = (cache_key[0], cache_key[1], cache_key[2], data)
    if vault is not None:
        vp = str(data.get("vaultPath") or "").strip()
        if vp and Path(_expand_home(vp)).resolve(strict=False) != vault:
            return {}
    return data


def _extract_frontmatter_and_body(text: str, rel_path: str) -> tuple[str, list[str], str]:
    raw = str(text or "")
    lines = raw.splitlines()
    title = Path(rel_path).stem
    tags: list[str] = []
    body_start = 0

    if lines and lines[0].strip() == "---":
        end_idx = -1
        for i in range(1, min(len(lines), 80)):
            if lines[i].strip() in ("---", "..."):
                end_idx = i
                break
        if end_idx > 0:
            fm_lines = lines[1:end_idx]
            body_start = end_idx + 1
            in_tags = False
            for fl in fm_lines:
                s = fl.strip()
                if not s or s.startswith("#"):
                    continue
                if re.match(r"^title\s*:\s*", fl, re.I):
                    val = fl.split(":", 1)[1].strip().strip("\"'")
                    if val:
                        title = val[:160]
                    in_tags = False
                elif re.match(r"^tags\s*:\s*", fl, re.I):
                    rest = fl.split(":", 1)[1].strip()
                    if rest.startswith("[") and rest.endswith("]"):
                        for item in rest[1:-1].split(","):
                            t = item.strip().strip("\"'# ")
                            if t and t not in tags:
                                tags.append(t)
                        in_tags = False
                    elif rest:
                        t = rest.strip("\"'# ")
                        if t and t not in tags:
                            tags.append(t)
                        in_tags = False
                    else:
                        in_tags = True
                elif in_tags and re.match(r"^\s*-\s+", fl):
                    t = re.sub(r"^\s*-\s+", "", fl).strip().strip("\"'# ")
                    if t and t not in tags:
                        tags.append(t)
                elif not fl.startswith((" ", "\t")):
                    in_tags = False

    body = "\n".join(lines[body_start:])
    if title == Path(rel_path).stem:
        for ln in lines[body_start : body_start + 30]:
            m_h1 = re.match(r"^\s*#\s+(.+)$", ln)
            if m_h1:
                cand = m_h1.group(1).strip()
                if cand:
                    title = cand[:160]
                break

    for m_tag in re.finditer(r"(?:^|\s)#([A-Za-z0-9_/\-\u00C0-\u1EF9]{2,40})", body[:8000]):
        tg = m_tag.group(1).strip("/")
        if tg and tg not in tags:
            tags.append(tg)
            if len(tags) >= 24:
                break

    return title, tags, body


def _chunk_note_body(body: str, default_heading: str, max_chunk_chars: int = 950) -> list[tuple[str, str]]:
    import knowledge_index

    chunks = knowledge_index.chunk_document(
        body,
        title=default_heading,
        source_label=default_heading,
        max_chunk_chars=max_chunk_chars,
        overlap_ratio=0.15,
        max_chunks=200,
    )
    if not chunks and str(body or "").strip():
        return [(default_heading, str(body).strip()[:max_chunk_chars])]
    return [(str(c["heading"]), str(c["content"])) for c in chunks]


def _init_nexus_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("CREATE TABLE IF NOT EXISTS nexus_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS nexus_files ("
        "rel_path TEXT PRIMARY KEY, "
        "mtime_ns INTEGER NOT NULL, "
        "size_bytes INTEGER NOT NULL, "
        "title TEXT NOT NULL, "
        "folder TEXT NOT NULL, "
        "tags TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS nexus_fts USING fts5("
        "rel_path, title, folder, tags, heading, body, folded, tokenize='unicode61')"
    )


def _index_single_file(
    conn: sqlite3.Connection,
    rel_str: str,
    full_path: Path,
    st_mtime_ns: int,
    st_size: int,
) -> bool:
    try:
        raw = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    title, tags, body = _extract_frontmatter_and_body(raw, rel_str)
    folder = rel_str.split("/", 1)[0] if "/" in rel_str else "."
    tags_str = ", ".join(tags)
    chunks = _chunk_note_body(body, title)
    if not chunks:
        chunks = [(title, title)]

    conn.execute("DELETE FROM nexus_fts WHERE rel_path = ?", (rel_str,))
    conn.execute(
        "INSERT OR REPLACE INTO nexus_files(rel_path, mtime_ns, size_bytes, title, folder, tags) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rel_str, st_mtime_ns, st_size, title, folder, tags_str),
    )
    import knowledge_index
    rows = []
    folded_meta = knowledge_index.fold_text(f"{rel_str} {title} {folder} {tags_str}", include_cjk_bigrams=True)
    for hd, txt in chunks:
        rows.append((
            rel_str,
            title,
            folder,
            tags_str,
            hd,
            txt,
            f"{folded_meta}\n{knowledge_index.fold_text(hd, include_cjk_bigrams=True)}\n{knowledge_index.fold_text(txt, include_cjk_bigrams=True)}",
        ))
    conn.executemany(
        "INSERT INTO nexus_fts(rel_path, title, folder, tags, heading, body, folded) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return True


def sync_nexus_vault_index(
    explicit_vault: str = "",
    *,
    force: bool = False,
    max_files: int = 3000,
) -> dict:
    """Incrementally index Nexus Vault notes into external SQLite FTS5 (`nexus_rag.db`)."""
    vault = resolve_nexus_vault_path(explicit_vault)
    db_path = external_rag_db_path()
    if vault is None:
        return {"ok": False, "reason": "no_vault", "dbPath": str(db_path), "indexedNotes": 0}

    now_ms = int(time.time() * 1000)
    cache = load_nexus_cache(str(vault))
    scan_sig = str(cache.get("_scanSignature") or "")

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
        _init_nexus_db(conn)
        meta = dict(conn.execute("SELECT key, value FROM nexus_meta").fetchall())
        prev_vault = meta.get("vault_path", "")
        prev_sync = int(meta.get("last_sync_ms") or 0)
        prev_sig = meta.get("scan_sig", "")
        total_prev = int(conn.execute("SELECT COUNT(*) FROM nexus_files").fetchone()[0] or 0)

        if (
            not force
            and prev_vault == str(vault)
            and total_prev > 0
            and (now_ms - prev_sync) < SYNC_COOLDOWN_MS
            and (not scan_sig or scan_sig == prev_sig)
        ):
            return {
                "ok": True,
                "cached": True,
                "vaultPath": str(vault),
                "dbPath": str(db_path),
                "indexedNotes": total_prev,
                "updatedFiles": 0,
                "removedFiles": 0,
            }

        if prev_vault and prev_vault != str(vault):
            conn.execute("DELETE FROM nexus_files")
            conn.execute("DELETE FROM nexus_fts")

        existing = {
            row[0]: (int(row[1]), int(row[2]))
            for row in conn.execute("SELECT rel_path, mtime_ns, size_bytes FROM nexus_files").fetchall()
        }

        seen_files: set[str] = set()
        updated = 0
        file_count = 0

        for dirpath, dirnames, filenames in os.walk(vault, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames if d not in IGNORED_VAULT_DIRS and not d.startswith(".")
            )
            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                full = Path(dirpath) / fname
                if full.is_symlink() or full.suffix.lower() not in INDEXABLE_SUFFIXES:
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                if not full.is_file() or st.st_size > MAX_NOTE_BYTES:
                    continue
                try:
                    rel_str = full.relative_to(vault).as_posix()
                except ValueError:
                    continue
                seen_files.add(rel_str)
                file_count += 1
                prev_stat = existing.get(rel_str)
                if force or prev_stat != (int(st.st_mtime_ns), int(st.st_size)):
                    if _index_single_file(conn, rel_str, full, int(st.st_mtime_ns), int(st.st_size)):
                        updated += 1
                if file_count >= max_files:
                    break
            if file_count >= max_files:
                break

        removed_paths = [rp for rp in existing if rp not in seen_files]
        for rp in removed_paths:
            conn.execute("DELETE FROM nexus_files WHERE rel_path = ?", (rp,))
            conn.execute("DELETE FROM nexus_fts WHERE rel_path = ?", (rp,))

        conn.execute("INSERT OR REPLACE INTO nexus_meta(key, value) VALUES ('vault_path', ?)", (str(vault),))
        conn.execute("INSERT OR REPLACE INTO nexus_meta(key, value) VALUES ('last_sync_ms', ?)", (str(now_ms),))
        conn.execute("INSERT OR REPLACE INTO nexus_meta(key, value) VALUES ('scan_sig', ?)", (scan_sig,))
        conn.commit()
        total_now = int(conn.execute("SELECT COUNT(*) FROM nexus_files").fetchone()[0] or 0)
        return {
            "ok": True,
            "cached": False,
            "vaultPath": str(vault),
            "dbPath": str(db_path),
            "indexedNotes": total_now,
            "updatedFiles": updated,
            "removedFiles": len(removed_paths),
        }
    finally:
        conn.close()


_SEARCH_STOPWORDS = frozenset({
    "the", "and", "how", "does", "what", "with", "for", "from", "search", "find",
    "note", "notes", "nexus", "obsidian", "vault", "rag", "about", "show", "read",
    "tim", "kiem", "tra", "cuu", "xem", "doc", "ghi", "chu", "trong", "cua",
    "nhu", "the", "nao", "theo", "tai", "lieu", "la", "co", "va", "cac", "nhung",
    "mot", "de", "voi", "cho", "tu", "nay", "do", "khi", "da", "dang", "se",
    "khong", "duoc", "ve", "hay", "hoac", "gi", "o", "tai", "ban", "minh", "toi",
})


def _extract_query_terms(query: str) -> list[str]:
    import knowledge_index

    terms = knowledge_index.fts5_query_terms(query, stop_words=set(_SEARCH_STOPWORDS), limit=10)
    if terms:
        return terms
    folded = _fold_ascii(query)
    raw_toks = re.findall(r"[a-z0-9_+\-#.:]{2,}", folded)
    return [t.strip(".,:") for t in raw_toks if len(t.strip(".,:")) >= 2][:6]


def _search_collections_cache(cache: dict, terms: list[str], raw_query: str) -> list[dict]:
    cols = cache.get("collections")
    if not isinstance(cols, dict) or not terms:
        return []
    matches: list[dict] = []
    q_fold = _fold_ascii(raw_query)
    for col_name, items in cols.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or item.get("stem") or "").strip()
            rel = str(item.get("relPath") or "").strip()
            tags_s = " ".join(str(x) for x in (item.get("tags") or []) if x)
            summary = str(item.get("summary") or item.get("tagline") or item.get("author") or item.get("director") or "").strip()
            hay = _fold_ascii(f"{col_name} {title} {rel} {tags_s} {summary}")
            hits = sum(1 for t in terms if t in hay)
            if hits <= 0 and q_fold not in hay:
                continue
            matches.append({
                "collection": str(col_name),
                "title": title or rel,
                "relPath": rel,
                "summary": summary[:180],
                "score": hits * 2 + (3 if title and any(t in _fold_ascii(title) for t in terms) else 0),
            })
    matches.sort(key=lambda x: (-x["score"], x["title"]))
    return matches[:6]


def search_nexus_knowledge(
    query: str,
    *,
    vault_path: str = "",
    top_k: int = 6,
    folder: str = "",
    tag: str = "",
    include_collections: bool = True,
    token_budget: int = 900,
) -> dict:
    """Query indexed Nexus Vault notes & collections via FTS5 BM25 + diacritic folding."""
    vault = resolve_nexus_vault_path(vault_path)
    rag_dir = resolve_rag_vault_dir(str(vault) if vault else "")
    db_path = external_rag_db_path()
    if vault is None:
        return {
            "ok": False,
            "snippets": [],
            "citations": [],
            "matchedCollections": [],
            "vaultPath": "",
            "ragDir": "",
            "dbPath": str(db_path),
            "indexedNotes": 0,
        }

    sync_info = sync_nexus_vault_index(str(vault))
    terms = _extract_query_terms(query)
    cache = load_nexus_cache(str(vault))
    matched_cols = _search_collections_cache(cache, terms, query) if include_collections else []

    if not terms and not folder and not tag:
        return {
            "ok": True,
            "snippets": [],
            "citations": [],
            "matchedCollections": matched_cols,
            "vaultPath": str(vault),
            "ragDir": str(rag_dir or ""),
            "dbPath": str(db_path),
            "indexedNotes": sync_info.get("indexedNotes", 0),
        }

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    scored_rows: list[tuple[float, dict]] = []
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        seen_keys: set[tuple[str, str]] = set()
        fts_expr_and = " AND ".join(f'"{t}"*' for t in terms[:6]) if terms else ""
        fts_expr_or = " OR ".join(f'"{t}"*' for t in terms[:8]) if terms else ""

        def _esc_like(s: str) -> str:
            return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        def _run_filter_only() -> None:
            where_clauses: list[str] = []
            params: list[object] = []
            if folder:
                where_clauses.append("(lower(folder) LIKE ? ESCAPE '\\' OR lower(rel_path) LIKE ? ESCAPE '\\')")
                f_low = f"%{_esc_like(folder.strip().lower())}%"
                params.extend([f_low, f_low])
            if tag:
                where_clauses.append("lower(tags) LIKE ? ESCAPE '\\'")
                params.append(f"%{_esc_like(tag.strip().lstrip('#').lower())}%")
            if not where_clauses:
                return
            sql = (
                "SELECT rel_path, title, folder, tags, heading, body, 0.0 AS rank "
                f"FROM nexus_fts WHERE {' AND '.join(where_clauses)} LIMIT 30"
            )
            for rp, ttl, fld, tgs, hd, bd, _rank in conn.execute(sql, params).fetchall():
                key = (str(rp), str(hd))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                scored_rows.append((
                    2.0,
                    {
                        "source": f"Nexus: {rp}",
                        "relPath": str(rp),
                        "title": str(ttl),
                        "folder": str(fld),
                        "tags": [t.strip() for t in str(tgs).split(",") if t.strip()],
                        "heading": str(hd),
                        "snippet": str(bd).strip(),
                        "score": 2.0,
                    },
                ))

        def _run_fts(expr: str, bonus: float) -> None:
            if not expr:
                return
            where_clauses = ["nexus_fts MATCH ?"]
            params: list[object] = [expr]
            if folder:
                where_clauses.append("(lower(folder) LIKE ? ESCAPE '\\' OR lower(rel_path) LIKE ? ESCAPE '\\')")
                f_low = f"%{_esc_like(folder.strip().lower())}%"
                params.extend([f_low, f_low])
            if tag:
                where_clauses.append("lower(tags) LIKE ? ESCAPE '\\'")
                params.append(f"%{_esc_like(tag.strip().lstrip('#').lower())}%")
            sql = (
                "SELECT rel_path, title, folder, tags, heading, body, "
                "bm25(nexus_fts, 1.5, 2.8, 1.2, 1.8, 2.0, 1.0, 0.9) AS rank "
                f"FROM nexus_fts WHERE {' AND '.join(where_clauses)} ORDER BY rank LIMIT 30"
            )
            for rp, ttl, fld, tgs, hd, bd, rank in conn.execute(sql, params).fetchall():
                key = (str(rp), str(hd))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                score = bonus + max(0.2, -float(rank or 0.0))
                if any(t in _fold_ascii(ttl) for t in terms):
                    score += 3.5
                if any(t in _fold_ascii(tgs) for t in terms):
                    score += 2.0
                scored_rows.append((
                    score,
                    {
                        "source": f"Nexus: {rp}",
                        "relPath": str(rp),
                        "title": str(ttl),
                        "folder": str(fld),
                        "tags": [t.strip() for t in str(tgs).split(",") if t.strip()],
                        "heading": str(hd),
                        "snippet": str(bd).strip(),
                        "score": round(score, 3),
                    },
                ))

        if fts_expr_and:
            _run_fts(fts_expr_and, 10.0)
        if len(scored_rows) < top_k and fts_expr_or:
            _run_fts(fts_expr_or, 1.0)
        if not terms and (folder or tag):
            _run_filter_only()
    except sqlite3.Error:
        pass
    finally:
        conn.close()

    import knowledge_index
    exact_ranked = [((str(c["relPath"]), str(c["heading"]))) for _, c in sorted(scored_rows, key=lambda x: (-x[0], x[1]["relPath"]))]
    lex_ranked = [
        (str(c["relPath"]), str(c["heading"]))
        for _, c in sorted(
            scored_rows,
            key=lambda x: (
                -sum(1 for t in terms if t in knowledge_index.fold_text(f"{x[1]['title']} {x[1]['heading']} {x[1]['snippet']}")),
                x[1]["relPath"],
            ),
        )
    ]
    rrf_scores = dict(knowledge_index.rrf_fuse([exact_ranked, lex_ranked], k=60))
    reranked: list[tuple[float, dict]] = []
    for base_sc, c in scored_rows:
        r_key = (str(c["relPath"]), str(c["heading"]))
        final_sc = base_sc + rrf_scores.get(r_key, 0.0) * 18.0
        c["score"] = round(final_sc, 3)
        reranked.append((final_sc, c))
    reranked.sort(key=lambda x: (-x[0], x[1]["relPath"]))
    scored_rows = reranked
    citations: list[dict] = []
    snippets: list[str] = []
    est_tokens = 0
    for _, c in scored_rows[: max(1, min(12, top_k))]:
        line = f"[{c['source']} > {c['heading']}] {c['snippet'][:280]}"
        cost = max(1, len(line) // 4)
        if est_tokens + cost > token_budget and snippets:
            break
        snippets.append(line)
        citations.append(c)
        est_tokens += cost

    return {
        "ok": True,
        "snippets": snippets,
        "citations": citations,
        "matchedCollections": matched_cols,
        "vaultPath": str(vault),
        "ragDir": str(rag_dir or ""),
        "dbPath": str(db_path),
        "indexedNotes": sync_info.get("indexedNotes", 0),
        "estimatedTokens": est_tokens,
    }


def read_nexus_note(target: str, *, vault_path: str = "", max_chars: int = 6000) -> dict:
    """Safely read a Markdown note strictly inside Nexus Vault by relPath or title lookup."""
    vault = resolve_nexus_vault_path(vault_path)
    if vault is None:
        raise NexusRagError("no_nexus_vault", "Nexus Vault is not configured.")
    raw = str(target or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith("/") or ".." in Path(raw).parts:
        raise NexusRagError("invalid_note_path", "Invalid or unsafe Nexus note path.")

    raw_cand = vault / raw
    cand = raw_cand.resolve(strict=False)
    try:
        cand.relative_to(vault)
    except ValueError as exc:
        raise NexusRagError("forbidden_note_path", "Note path resolves outside Nexus Vault.") from exc

    resolved_file: Path | None = None
    if not is_symlink_raw(raw_cand, root_dir=vault) and cand.is_file() and not cand.is_symlink() and not is_forbidden_path(cand) and not is_sensitive_path(cand):
        resolved_file = cand
    elif not raw.endswith(".md"):
        raw_cand_md = vault / f"{raw}.md"
        cand_md = raw_cand_md.resolve(strict=False)
        try:
            cand_md.relative_to(vault)
            if not is_symlink_raw(raw_cand_md, root_dir=vault) and cand_md.is_file() and not cand_md.is_symlink() and not is_forbidden_path(cand_md) and not is_sensitive_path(cand_md):
                resolved_file = cand_md
        except ValueError:
            pass

    if resolved_file is None:
        found = search_nexus_knowledge(raw, vault_path=str(vault), top_k=3, include_collections=False)
        cites = found.get("citations") or []
        if cites:
            rp = str(cites[0].get("relPath") or "")
            if rp:
                raw_alt = vault / rp
                alt = raw_alt.resolve(strict=False)
                try:
                    alt.relative_to(vault)
                    if not is_symlink_raw(raw_alt, root_dir=vault) and alt.is_file() and not alt.is_symlink() and not is_sensitive_path(alt):
                        resolved_file = alt
                except ValueError:
                    pass

    if resolved_file is None:
        raise NexusRagError("note_not_found", f"Could not find Nexus note matching '{raw}'.")

    rel_str = resolved_file.relative_to(vault).as_posix()
    text = resolved_file.read_text(encoding="utf-8", errors="replace")
    title, tags, body = _extract_frontmatter_and_body(text, rel_str)
    headings = [m.group(1).strip() for m in re.finditer(r"^#{1,4}\s+(.+)$", body, re.M)][:20]
    links = []
    for m in re.finditer(r"\[\[([^\]|]+)", body):
        lk = m.group(1).strip()
        if lk and lk not in links:
            links.append(lk)
            if len(links) >= 20:
                break
    truncated = len(body) > max_chars
    return {
        "ok": True,
        "relPath": rel_str,
        "path": str(resolved_file),
        "title": title,
        "folder": rel_str.split("/", 1)[0] if "/" in rel_str else ".",
        "tags": tags,
        "headings": headings,
        "links": links,
        "content": body[:max_chars],
        "truncated": truncated,
    }


def get_nexus_overview(*, vault_path: str = "", topic: str = "all") -> dict:
    """Return structured Nexus summary (journal, schedule, finance, collections, stats, RAG paths)."""
    vault = resolve_nexus_vault_path(vault_path)
    rag_dir = resolve_rag_vault_dir(str(vault) if vault else "")
    db_path = external_rag_db_path()
    cache = load_nexus_cache(str(vault) if vault else "")
    stats = cache.get("stats") if isinstance(cache.get("stats"), dict) else {}
    collections_raw = cache.get("collections") if isinstance(cache.get("collections"), dict) else {}
    collection_counts: dict[str, int] = {}
    collection_samples: dict[str, list[dict]] = {}
    for k, v in collections_raw.items():
        if isinstance(v, list):
            collection_counts[str(k)] = len(v)
            collection_samples[str(k)] = [
                {
                    "title": str(item.get("title") or item.get("name") or item.get("stem") or item.get("relPath") or ""),
                    "relPath": str(item.get("relPath") or ""),
                    "rating": item.get("rating", item.get("score")),
                }
                for item in v[:8]
                if isinstance(item, dict)
            ]

    indexed_notes = 0
    if db_path.is_file():
        try:
            conn = sqlite3.connect(str(db_path), timeout=2.0)
            indexed_notes = int(conn.execute("SELECT COUNT(*) FROM nexus_files").fetchone()[0] or 0)
            conn.close()
        except Exception:
            indexed_notes = 0

    return {
        "ok": vault is not None,
        "topic": topic,
        "vaultPath": str(vault or ""),
        "vaultName": str(cache.get("vaultName") or (vault.name if vault else "")),
        "ragDir": str(rag_dir or ""),
        "ragSourcesPath": str((rag_dir / "RAG_Sources.md") if rag_dir else ""),
        "dbPath": str(db_path),
        "indexedNotes": indexed_notes,
        "stats": {
            "noteCount": int(stats.get("noteCount") or indexed_notes),
            "journalCount": int(stats.get("journalCount") or 0),
            "bookCount": int(stats.get("bookCount") or 0),
            "tagCount": int(stats.get("tagCount") or 0),
            "openTasks": int(stats.get("openTasks") or 0),
            "doneTasks": int(stats.get("doneTasks") or 0),
        },
        "todayJournal": cache.get("todayJournal") if isinstance(cache.get("todayJournal"), dict) else {},
        "dailySchedule": cache.get("dailySchedule") if isinstance(cache.get("dailySchedule"), dict) else {},
        "allWeekdaysSchedule": cache.get("allWeekdaysSchedule") if isinstance(cache.get("allWeekdaysSchedule"), dict) else {},
        "financeSummary": cache.get("financeSummary") if isinstance(cache.get("financeSummary"), dict) else {},
        "recentNotes": (cache.get("recentNotes") or [])[:10] if isinstance(cache.get("recentNotes"), list) else [],
        "tags": [
            {"name": str(t.get("name") or ""), "count": int(t.get("count") or 0)}
            for t in (cache.get("tags") or [])[:20]
            if isinstance(t, dict)
        ],
        "collectionCounts": collection_counts,
        "collectionSamples": collection_samples,
    }


def save_rag_note(
    title: str,
    content: str,
    *,
    folder: str = "Knowledge",
    tags: list[str] | None = None,
    vault_path: str = "",
) -> dict:
    """Save a Markdown RAG knowledge note atomically inside `<vault>/90_System/99_RAG/` and index it."""
    rag_dir = resolve_rag_vault_dir(vault_path)
    vault = resolve_nexus_vault_path(vault_path)
    if rag_dir is None or vault is None:
        raise NexusRagError("no_nexus_vault", "Nexus Vault is not configured.")

    ensure_rag_readme(rag_dir)
    clean_title = str(title or "").strip()[:120] or "Companion Note"
    clean_folder = re.sub(r"[^A-Za-z0-9_\-/]", "_", str(folder or "Knowledge")).strip("/.") or "Knowledge"
    clean_folder = clean_folder.split("/")[0]
    if clean_folder not in ("Knowledge", "Canonical", "Personal"):
        clean_folder = "Knowledge"
    target_dir = rag_dir / clean_folder
    target_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "-", _fold_ascii(clean_title)).strip("-")[:64] or "note"
    dst = target_dir / f"{slug}.md"
    suffix_idx = 2
    while dst.exists():
        dst = target_dir / f"{slug}-{suffix_idx}.md"
        suffix_idx += 1

    clean_title = re.sub(r"[\r\n\x00]+", " ", clean_title).strip()
    tag_list = ["system/rag", "alis/companion"]
    for t in (tags or []):
        ts = re.sub(r"[\r\n\x00:#]+", " ", str(t or "")).strip().lstrip("#")
        if ts and ts not in tag_list:
            tag_list.append(ts)

    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    fm_tags = "\n".join(f"  - {t}" for t in tag_list)
    safe_title_yaml = clean_title.replace('"', "'").replace("---", "-")
    md_text = (
        "---\n"
        f"title: \"{safe_title_yaml}\"\n"
        "tags:\n"
        f"{fm_tags}\n"
        f"created: {now_iso}\n"
        "---\n\n"
        f"# {clean_title}\n\n"
        f"{str(content or '').strip()}\n"
    )
    _atomic_write_text(dst, md_text, mode=0o644)
    rel_str = dst.relative_to(vault).as_posix()

    db_path = external_rag_db_path()
    try:
        st = dst.stat()
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        try:
            _init_nexus_db(conn)
            _index_single_file(conn, rel_str, dst, int(st.st_mtime_ns), int(st.st_size))
            conn.execute("INSERT OR REPLACE INTO nexus_meta(key, value) VALUES ('vault_path', ?)", (str(vault),))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

    return {
        "ok": True,
        "title": clean_title,
        "relPath": rel_str,
        "path": str(dst),
        "folder": clean_folder,
    }
