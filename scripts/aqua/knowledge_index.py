#!/usr/bin/env python3
"""Unified hybrid knowledge index: accent-folded FTS5 + float16 embeddings + RRF.

Provides a single reusable foundation for `nexus_rag.py` and `rag_store.py` with:
  1. Accent/diacritic folding (`đ`/`Đ` -> `d`), symbol token preservation (`C++`, `window-rule`),
     and CJK overlapping bigram expansion.
  2. Structure-aware document chunking with 15% contextual overlap and header provenance
     (no 24-chunk truncation).
  3. SQLite WAL external-content FTS5 + IEEE-754 float16 embedding storage + RRF fusion.
  4. Zero cold model loading: only embeds via an already-warm local embedding model.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import time
from typing import Any
import unicodedata
import urllib.parse
import urllib.request

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_CJK_SPAN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]+")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_BOUNDARY_RE = re.compile(r"^(?:def\s+|async\s+def\s+|class\s+|function\s+|export\s+|component\s+|impl\s+|struct\s+|fn\s+)")


def _urlopen_local(req_or_url, timeout: float = 2.0):
    url = req_or_url.full_url if hasattr(req_or_url, "full_url") else str(req_or_url or "")
    host = (urllib.parse.urlparse(url).hostname or "").strip().lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return _NO_PROXY_OPENER.open(req_or_url, timeout=timeout)
    return urllib.request.urlopen(req_or_url, timeout=timeout)


def expand_cjk_bigrams(text: str) -> str:
    """Append overlapping 2-char bigrams for CJK spans so FTS5 unicode61 finds sub-phrases."""
    raw = str(text or "")
    if not _CJK_SPAN_RE.search(raw):
        return raw
    extras: list[str] = []
    for match in _CJK_SPAN_RE.finditer(raw):
        span = match.group(0)
        if len(span) >= 2:
            extras.extend(span[i:i + 2] for i in range(len(span) - 1))
    if not extras:
        return raw
    return raw + " " + " ".join(extras)


def fold_text(text: str, *, include_cjk_bigrams: bool = False) -> str:
    """Normalize text: NFD diacritics stripped, `đ`/`Đ` -> `d`, lowercase."""
    raw = str(text or "")
    if raw.isascii():
        return raw.lower()
    norm = unicodedata.normalize("NFD", raw)
    stripped = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
    folded = stripped.replace("đ", "d").replace("Đ", "d").lower()
    if include_cjk_bigrams:
        folded = expand_cjk_bigrams(folded)
    return folded


def fts5_query_terms(query: str, stop_words: set[str] | None = None, limit: int = 10) -> list[str]:
    """Extract clean FTS5 query tokens preserving `C++`, `window-rule`, `file.qml` and CJK bigrams."""
    folded = fold_text(query)
    sw = stop_words or set()
    terms: list[str] = []
    seen: set[str] = set()

    for match in _CJK_SPAN_RE.finditer(folded):
        span = match.group(0)
        if len(span) >= 2:
            for i in range(len(span) - 1):
                bg = span[i:i + 2]
                if bg not in seen:
                    seen.add(bg)
                    terms.append(bg)
        elif span not in seen:
            seen.add(span)
            terms.append(span)

    for raw_tok in re.findall(r"[a-z0-9_+\-#.:]{2,}", folded):
        tok = raw_tok.strip(".,:")
        if len(tok) < 2 or tok in sw:
            continue
        safe = tok.replace('"', "")
        if safe and safe not in seen:
            seen.add(safe)
            terms.append(safe)
        if len(terms) >= limit:
            break
    return terms[:limit]


def build_fts5_match(terms: list[str], *, operator: str = "OR") -> str:
    op = " AND " if operator.upper() == "AND" else " OR "
    parts: list[str] = []
    for t in terms:
        clean = str(t or "").replace('"', "").strip()
        if not clean:
            continue
        if any(ch in clean for ch in "+-#.:"):
            parts.append(f'"{clean}"')
        else:
            parts.append(f'"{clean}"*')
    return op.join(parts)


def _overlap_tail(text: str, overlap_chars: int) -> str:
    if overlap_chars <= 0 or not text:
        return ""
    if len(text) <= overlap_chars:
        return text
    tail = text[-overlap_chars:]
    ws_idx = tail.find(" ")
    if 0 <= ws_idx < len(tail) - 8:
        tail = tail[ws_idx + 1:]
    return tail.strip()


def chunk_document(
    text: str,
    *,
    title: str = "",
    source_label: str = "",
    max_chunk_chars: int = 950,
    overlap_ratio: float = 0.15,
    max_chunks: int = 200,
) -> list[dict[str, Any]]:
    """Split text into structure-aware chunks with ~15% overlap and contextual headers."""
    raw = str(text or "").strip()
    if not raw:
        return []
    max_chars = max(180, int(max_chunk_chars))
    ov_chars = max(0, min(max_chars // 3, int(round(max_chars * max(0.0, min(0.35, overlap_ratio))))))
    default_hd = str(title or source_label or "Document").strip()[:140] or "Document"
    prov_label = str(source_label or title or "Document").strip()[:120]

    chunks: list[dict[str, Any]] = []
    heading = default_hd
    buf: list[str] = []
    buf_len = 0
    pending_overlap = ""

    def _emit_chunk(chunk_hd: str, chunk_body: str) -> None:
        nonlocal chunks
        if len(chunks) >= max_chunks:
            return
        body_clean = chunk_body.strip()
        if not body_clean:
            return
        ctx_prefix = f"[{prov_label} | {chunk_hd}]" if prov_label and chunk_hd != prov_label else f"[{chunk_hd}]"
        context_text = f"{ctx_prefix}\n{body_clean}"
        folded = fold_text(f"{ctx_prefix}\n{body_clean}", include_cjk_bigrams=True)
        digest = hashlib.sha256(context_text.encode("utf-8", errors="replace")).hexdigest()
        chunks.append({
            "chunkIndex": len(chunks),
            "heading": chunk_hd,
            "content": body_clean,
            "contextText": context_text,
            "folded": folded,
            "sha256": digest,
        })

    def _flush(*, keep_overlap: bool) -> None:
        nonlocal buf, buf_len, pending_overlap
        joined = "\n".join(buf).strip()
        if joined:
            if len(joined) > max_chars:
                pos = 0
                step = max(120, max_chars - ov_chars)
                while pos < len(joined) and len(chunks) < max_chunks:
                    piece = joined[pos:pos + max_chars].strip()
                    if piece:
                        _emit_chunk(heading, piece)
                    if pos + max_chars >= len(joined):
                        break
                    pos += step
                pending_overlap = _overlap_tail(joined, ov_chars) if keep_overlap else ""
            else:
                _emit_chunk(heading, joined)
                pending_overlap = _overlap_tail(joined, ov_chars) if keep_overlap else ""
        elif not keep_overlap:
            pending_overlap = ""
        buf = []
        buf_len = 0

    for line in raw.splitlines():
        if len(chunks) >= max_chunks:
            break
        s = line.strip()
        if not s:
            if buf_len >= max_chars - 120:
                _flush(keep_overlap=True)
            continue
        m_h = _HEADING_RE.match(s)
        if m_h:
            _flush(keep_overlap=False)
            heading = m_h.group(2).strip()[:140] or default_hd
            pending_overlap = ""
            continue
        if _BOUNDARY_RE.match(s) and buf_len >= int(max_chars * 0.55):
            _flush(keep_overlap=True)
        if not buf and pending_overlap:
            buf.append(pending_overlap)
            buf_len += len(pending_overlap) + 1
            pending_overlap = ""
        if buf_len + len(s) + 1 > max_chars and buf:
            _flush(keep_overlap=True)
            if pending_overlap:
                buf.append(pending_overlap)
                buf_len += len(pending_overlap) + 1
                pending_overlap = ""
        buf.append(s)
        buf_len += len(s) + 1

    if len(chunks) < max_chunks:
        _flush(keep_overlap=False)

    if not chunks and raw:
        _emit_chunk(default_hd, raw[:max_chars])
    return chunks


def open_index_db(db_path: Path) -> sqlite3.Connection:
    """Open WAL + busy_timeout SQLite connection with external-content FTS5 & float16 tables."""
    target = Path(db_path).expanduser()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=5.0)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ki_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_key TEXT NOT NULL,
            source TEXT NOT NULL,
            heading TEXT NOT NULL,
            content TEXT NOT NULL,
            folded TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            updated_ms INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ki_chunks_doc_key ON ki_chunks(doc_key)")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS ki_chunks_fts USING fts5(
            heading,
            folded,
            content='ki_chunks',
            content_rowid='id',
            tokenize="unicode61 tokenchars '_+-#.:'"
        )
        """
    )
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS ki_chunks_ai AFTER INSERT ON ki_chunks BEGIN
            INSERT INTO ki_chunks_fts(rowid, heading, folded) VALUES (new.id, new.heading, new.folded);
        END;
        CREATE TRIGGER IF NOT EXISTS ki_chunks_ad AFTER DELETE ON ki_chunks BEGIN
            INSERT INTO ki_chunks_fts(ki_chunks_fts, rowid, heading, folded) VALUES ('delete', old.id, old.heading, old.folded);
        END;
        CREATE TRIGGER IF NOT EXISTS ki_chunks_au AFTER UPDATE ON ki_chunks BEGIN
            INSERT INTO ki_chunks_fts(ki_chunks_fts, rowid, heading, folded) VALUES ('delete', old.id, old.heading, old.folded);
            INSERT INTO ki_chunks_fts(rowid, heading, folded) VALUES (new.id, new.heading, new.folded);
        END;
        CREATE TABLE IF NOT EXISTS ki_embeddings (
            chunk_id INTEGER PRIMARY KEY,
            model_id TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vec BLOB NOT NULL
        );
        """
    )
    return conn


def pack_float16(vec: list[float]) -> bytes:
    if not vec:
        return b""
    return struct.pack(f"<{len(vec)}e", *(float(x) for x in vec))


def unpack_float16(blob: bytes) -> list[float]:
    if not blob or len(blob) % 2 != 0:
        return []
    dim = len(blob) // 2
    return list(struct.unpack(f"<{dim}e", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 1e-9 or nb <= 1e-9:
        return 0.0
    return dot / (na * nb)


def probe_warm_embedding_model(endpoint: str = "", timeout: float = 0.6) -> tuple[str, str]:
    """Return `(embed_endpoint, model_id)` ONLY if a local embedding model is already warm."""
    test_ep = os.environ.get("ALIS_TEST_EMBED_ENDPOINT", "").strip()
    if os.environ.get("ALIS_TEST_ISOLATED") == "1" and not test_ep and not endpoint:
        return "", ""

    cand_ep = (test_ep or endpoint or os.environ.get("ALIS_AQUA_LOCAL_ENDPOINT") or "http://127.0.0.1:1234/v1/chat/completions").strip()
    parsed = urllib.parse.urlparse(cand_ep)
    host = (parsed.hostname or "").strip().lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return "", ""
    base = f"{parsed.scheme or 'http'}://{parsed.netloc}"

    if test_ep:
        return test_ep, os.environ.get("ALIS_TEST_EMBED_MODEL", "text-embedding-test")

    def _looks_embed(mid: str, mtype: str = "") -> bool:
        low = mid.lower()
        return "embed" in mtype.lower() or any(k in low for k in ("embed", "bge-", "nomic", "e5-", "minilm", "gte-"))

    if parsed.port == 1234 or "lmstudio" in cand_ep.lower():
        try:
            with _urlopen_local(f"{base}/api/v0/models", timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            for item in (data.get("data") or []):
                if isinstance(item, dict) and str(item.get("state") or "").lower() == "loaded":
                    mid = str(item.get("id") or "").strip()
                    if mid and _looks_embed(mid, str(item.get("type") or "")):
                        return f"{base}/v1/embeddings", mid
        except Exception:
            pass

    if parsed.port == 11434 or "ollama" in cand_ep.lower():
        try:
            with _urlopen_local(f"{base}/api/ps", timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            for m in (data.get("models") or []):
                if isinstance(m, dict):
                    mid = str(m.get("name") or "").strip()
                    if mid and _looks_embed(mid):
                        return f"{base}/v1/embeddings", mid
        except Exception:
            pass
    return "", ""


def embed_texts(texts: list[str], endpoint: str, model: str, timeout: float = 4.0) -> list[list[float]]:
    if not texts or not endpoint or not model:
        return []
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _urlopen_local(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            if isinstance(data.get("embeddings"), list):
                return [[float(v) for v in row] for row in data["embeddings"] if isinstance(row, list)]
            if isinstance(data.get("data"), list):
                rows = sorted(
                    (it for it in data["data"] if isinstance(it, dict) and isinstance(it.get("embedding"), list)),
                    key=lambda it: int(it.get("index", 0)),
                )
                return [[float(v) for v in row["embedding"]] for row in rows]
    except Exception:
        return []
    return []


def rrf_fuse(ranked_lists: list[list[Any]], k: int = 60, weights: list[float] | None = None) -> list[tuple[Any, float]]:
    """Reciprocal Rank Fusion over multiple ranked lists of hashable item identifiers."""
    scores: dict[Any, float] = {}
    k_val = max(1, int(k))
    for l_idx, rlist in enumerate(ranked_lists or []):
        w = float(weights[l_idx]) if (weights and l_idx < len(weights)) else 1.0
        for rank_idx, item_id in enumerate(rlist):
            scores[item_id] = scores.get(item_id, 0.0) + w * (1.0 / (k_val + rank_idx + 1))
    return sorted(scores.items(), key=lambda item: -item[1])


def sync_documents(
    db_path: Path,
    docs: list[dict[str, Any]],
    *,
    embed_endpoint: str = "",
    embed_model: str = "",
) -> dict[str, int]:
    """Synchronize documents into `ki_chunks` / `ki_chunks_fts` and optional float16 embeddings."""
    conn = open_index_db(db_path)
    now_ms = int(time.time() * 1000)
    total_chunks = 0
    embedded_count = 0
    try:
        active_keys = {str(d.get("docKey") or "") for d in docs if d.get("docKey")}
        existing_keys = {r[0] for r in conn.execute("SELECT DISTINCT doc_key FROM ki_chunks").fetchall()}
        for stale in existing_keys - active_keys:
            conn.execute("DELETE FROM ki_embeddings WHERE chunk_id IN (SELECT id FROM ki_chunks WHERE doc_key = ?)", (stale,))
            conn.execute("DELETE FROM ki_chunks WHERE doc_key = ?", (stale,))

        pending_embed_ids: list[int] = []
        pending_embed_texts: list[str] = []

        for doc in docs:
            doc_key = str(doc.get("docKey") or "").strip()
            if not doc_key:
                continue
            source = str(doc.get("source") or doc_key)
            title = str(doc.get("title") or source)
            chunks = chunk_document(str(doc.get("text") or ""), title=title, source_label=source)
            new_hashes = [c["sha256"] for c in chunks]
            prev_hashes = [r[0] for r in conn.execute("SELECT sha256 FROM ki_chunks WHERE doc_key = ? ORDER BY id", (doc_key,)).fetchall()]
            if new_hashes != prev_hashes:
                conn.execute("DELETE FROM ki_embeddings WHERE chunk_id IN (SELECT id FROM ki_chunks WHERE doc_key = ?)", (doc_key,))
                conn.execute("DELETE FROM ki_chunks WHERE doc_key = ?", (doc_key,))
                for ch in chunks:
                    cur = conn.execute(
                        "INSERT INTO ki_chunks(doc_key, source, heading, content, folded, sha256, updated_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (doc_key, source, ch["heading"], ch["content"], ch["folded"], ch["sha256"], now_ms),
                    )
                    cid = int(cur.lastrowid)
                    if embed_endpoint and embed_model:
                        pending_embed_ids.append(cid)
                        pending_embed_texts.append(ch["contextText"][:1800])
            elif embed_endpoint and embed_model:
                missing_rows = conn.execute(
                    "SELECT id, heading, content FROM ki_chunks WHERE doc_key = ? "
                    "AND id NOT IN (SELECT chunk_id FROM ki_embeddings WHERE model_id = ?)",
                    (doc_key, embed_model),
                ).fetchall()
                for cid, hd, bd in missing_rows:
                    pending_embed_ids.append(int(cid))
                    pending_embed_texts.append(f"[{source} | {hd}]\n{bd}"[:1800])
            total_chunks += len(chunks)

        if embed_endpoint and embed_model and pending_embed_texts:
            for start in range(0, len(pending_embed_texts), 24):
                batch_ids = pending_embed_ids[start:start + 24]
                batch_txt = pending_embed_texts[start:start + 24]
                vecs = embed_texts(batch_txt, embed_endpoint, embed_model)
                if len(vecs) == len(batch_ids):
                    for cid, vec in zip(batch_ids, vecs):
                        if not vec:
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO ki_embeddings(chunk_id, model_id, dim, vec) VALUES (?, ?, ?, ?)",
                            (cid, embed_model, len(vec), pack_float16(vec)),
                        )
                        embedded_count += 1
        conn.commit()
    finally:
        conn.close()
    return {"documents": len(docs), "chunks": total_chunks, "embeddedChunks": embedded_count}


def hybrid_search(
    db_path: Path,
    query: str,
    *,
    limit: int = 8,
    embed_endpoint: str = "",
    embed_model: str = "",
) -> list[dict[str, Any]]:
    """Run FTS5 BM25 + optional warm float16 embedding search fused via RRF."""
    if not Path(db_path).is_file():
        return []
    terms = fts5_query_terms(query)
    conn = open_index_db(db_path)
    try:
        fts_ids: list[int] = []
        seen_fts: set[int] = set()
        for op in ("AND", "OR"):
            expr = build_fts5_match(terms, operator=op)
            if not expr:
                continue
            try:
                rows = conn.execute(
                    "SELECT rowid FROM ki_chunks_fts WHERE ki_chunks_fts MATCH ? ORDER BY bm25(ki_chunks_fts, 2.0, 1.0) LIMIT 40",
                    (expr,),
                ).fetchall()
                for (rid,) in rows:
                    rid_i = int(rid)
                    if rid_i not in seen_fts:
                        seen_fts.add(rid_i)
                        fts_ids.append(rid_i)
            except sqlite3.Error:
                pass

        vec_ids: list[int] = []
        if embed_endpoint and embed_model:
            q_vecs = embed_texts([query], embed_endpoint, embed_model)
            if q_vecs and q_vecs[0]:
                q_vec = q_vecs[0]
                scored_vecs: list[tuple[float, int]] = []
                for cid, blob in conn.execute(
                    "SELECT chunk_id, vec FROM ki_embeddings WHERE model_id = ? LIMIT 4000",
                    (embed_model,),
                ).fetchall():
                    sim = _cosine_similarity(q_vec, unpack_float16(blob))
                    if sim > 0.15:
                        scored_vecs.append((sim, int(cid)))
                scored_vecs.sort(key=lambda x: -x[0])
                vec_ids = [cid for _, cid in scored_vecs[:40]]

        ranked_lists = [lst for lst in (fts_ids, vec_ids) if lst]
        if not ranked_lists:
            return []
        fused = rrf_fuse(ranked_lists, k=60)
        top_pairs = fused[: max(1, min(32, int(limit)))]
        id_list = [int(cid) for cid, _ in top_pairs]
        placeholders = ",".join("?" for _ in id_list)
        meta_rows = {
            int(r[0]): r
            for r in conn.execute(
                f"SELECT id, doc_key, source, heading, content FROM ki_chunks WHERE id IN ({placeholders})",
                id_list,
            ).fetchall()
        }
        results: list[dict[str, Any]] = []
        for cid, rrf_score in top_pairs:
            row = meta_rows.get(int(cid))
            if not row:
                continue
            results.append({
                "chunkId": int(row[0]),
                "docKey": str(row[1]),
                "source": str(row[2]),
                "heading": str(row[3]),
                "content": str(row[4]),
                "score": round(float(rrf_score), 6),
            })
        return results
    finally:
        conn.close()
