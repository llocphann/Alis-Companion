"""Persistent RAG knowledge base sources and cited retrieval for Alis-Companion."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import time

from permission_store import is_forbidden_path


class RagStoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _state_file() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    folder = (base / "alis-companion").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / "rag_sources.json"


def _now_ms(override=None) -> int:
    if override is not None and isinstance(override, int) and override >= 0:
        return override
    return int(time.time() * 1000)


def load_rag_state() -> dict:
    path = _state_file()
    if not path.is_file():
        return {"version": 1, "sources": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError()
        sources = raw.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        valid = []
        for entry in sources[:64]:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"].strip():
                valid.append({
                    "path": entry["path"].strip()[:4096],
                    "label": str(entry.get("label") or Path(entry["path"]).name)[:120],
                    "enabled": entry.get("enabled") is not False,
                    "addedAtMs": int(entry.get("addedAtMs") or 0),
                })
        return {"version": 1, "sources": valid}
    except (OSError, ValueError):
        return {"version": 1, "sources": []}


def save_rag_state(state: dict) -> None:
    path = _state_file()
    payload = json.dumps(
        {"version": 1, "sources": state.get("sources", [])[:64]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".rag-sources-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise RagStoreError("rag_store_unavailable", "Could not save RAG sources.") from exc


def list_sources() -> list[dict]:
    return load_rag_state()["sources"]


def add_source(target_path: str, label: str = "", now_ms=None) -> dict:
    raw = str(target_path or "").strip()
    if not raw:
        raise RagStoreError("invalid_path", "Provide a file or folder path to add to RAG.")
    resolved = Path(raw).expanduser().resolve(strict=False)
    if is_forbidden_path(resolved):
        raise RagStoreError("forbidden_path", "That path is restricted and cannot be indexed in RAG.")
    if not resolved.exists():
        raise RagStoreError("path_not_found", f"Path does not exist: {resolved}")
    state = load_rag_state()
    sources = [s for s in state["sources"] if s["path"] != str(resolved)]
    entry = {
        "path": str(resolved),
        "label": str(label or resolved.name)[:120],
        "enabled": True,
        "addedAtMs": _now_ms(now_ms),
    }
    sources.append(entry)
    state["sources"] = sources[-64:]
    save_rag_state(state)
    return entry


def remove_source(target_path: str) -> bool:
    raw = str(target_path or "").strip()
    if not raw:
        raise RagStoreError("invalid_path", "Provide a source path or name to remove from RAG.")
    resolved_str = str(Path(raw).expanduser().resolve(strict=False))
    state = load_rag_state()
    before = len(state["sources"])
    state["sources"] = [
        s for s in state["sources"]
        if s["path"] != resolved_str and s["path"] != raw and s["label"].lower() != raw.lower()
    ]
    removed = len(state["sources"]) < before
    if removed:
        save_rag_state(state)
    return removed


def active_source_paths(extra_dirs: list[str] | None = None, include_pocket: bool = True) -> list[str]:
    seen = []
    for s in list_sources():
        if s.get("enabled") is not False and s.get("path") and s["path"] not in seen:
            seen.append(s["path"])
    if include_pocket:
        try:
            import pocket as pocket_store
            p_state = pocket_store.dispatch({"version": 1, "consent": True, "action": "list"})
            for item in p_state.get("items", []):
                p_str = str(item.get("path") or "").strip()
                if p_str and p_str not in seen:
                    seen.append(p_str)
        except Exception:
            pass
    for d in (extra_dirs or []):
        d_str = str(d or "").strip()
        if d_str and d_str not in seen:
            seen.append(d_str)
    return seen
