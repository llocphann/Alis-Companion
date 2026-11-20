"""Coding tools (search, token-budgeted repo map, safe LSP, test runner) and cited RAG."""
import ast
import os
from pathlib import Path
import re
import shutil
import subprocess

from permission_store import is_forbidden_path, is_sensitive_path, is_symlink_raw
from sandbox_runner import run_sandboxed


IGNORED_DIRS = frozenset((".git", "node_modules", "target", "__pycache__", ".venv", "venv", "dist", "build"))
SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*(?:readonly\s+)?property\s+\S+\s+([A-Za-z_][A-Za-z0-9_]*)\s*:"),
)


class CodeToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def estimate_tokens(text):
    if not text:
        return 0
    return max(1, (len(str(text)) + 3) // 4)


def _safe_workspace_files(root_dir, max_files=250):
    root = Path(root_dir).expanduser().resolve(strict=False)
    if not root.is_dir() or is_forbidden_path(root):
        raise CodeToolError("invalid_workspace", "Workspace directory is unavailable or not permitted.")
    collected = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith("."))
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            fpath = Path(dirpath) / fname
            if fpath.is_symlink():
                continue
            try:
                resolved = fpath.resolve(strict=False)
                if not resolved.is_relative_to(root):
                    continue
            except OSError:
                continue
            collected.append(resolved)
            if len(collected) >= max_files:
                return root, collected
    return root, collected


def search_code(workspace_dir, query, max_results=20):
    q = str(query or "").strip()
    if not q or len(q) > 240:
        raise CodeToolError("invalid_query", "Provide a non-empty search query.")
    root, files = _safe_workspace_files(workspace_dir)
    rg_bin = shutil.which("rg")
    matches = []

    if rg_bin and os.environ.get("ALIS_AQUA_RG_BIN", "") != "__MISSING__":
        try:
            proc = subprocess.run(
                [
                    rg_bin,
                    "--line-number",
                    "--no-heading",
                    "--color", "never",
                    "--smart-case",
                    "--max-count", "8",
                    "--no-messages",
                    "--",
                    q,
                    str(root),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=4.0,
                check=False,
            )
            for raw_line in proc.stdout.decode("utf-8", errors="replace").splitlines():
                parts = raw_line.split(":", 2)
                if len(parts) != 3:
                    continue
                f_str, line_no, snippet = parts
                fpath = Path(f_str)
                if fpath.is_symlink():
                    continue
                try:
                    rel = str(fpath.resolve(strict=False).relative_to(root))
                except ValueError:
                    continue
                matches.append({
                    "file": rel,
                    "line": int(line_no) if line_no.isdigit() else 1,
                    "text": snippet.strip()[:200],
                })
                if len(matches) >= max_results:
                    break
        except (OSError, subprocess.SubprocessError):
            pass

    if not matches:
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        for fpath in files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(fpath.relative_to(root))
            for idx, ln in enumerate(content.splitlines(), start=1):
                if pattern.search(ln):
                    matches.append({
                        "file": rel,
                        "line": idx,
                        "text": ln.strip()[:200],
                    })
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break

    return {
        "workspace": str(root),
        "query": q,
        "count": len(matches),
        "matches": matches,
    }


def extract_file_symbols(file_path, root_dir=None):
    if is_symlink_raw(file_path, root_dir=root_dir) or is_sensitive_path(file_path):
        return []
    fpath = Path(file_path).resolve(strict=False)
    if fpath.is_symlink() or not fpath.is_file() or is_forbidden_path(fpath) or is_sensitive_path(fpath):
        return []
    rel = str(fpath.relative_to(root_dir)) if root_dir and fpath.is_relative_to(root_dir) else fpath.name
    try:
        text = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    symbols = []
    for idx, line in enumerate(text.splitlines(), start=1):
        for regex in SYMBOL_PATTERNS:
            m = regex.match(line)
            if m:
                symbols.append({
                    "name": m.group(1),
                    "file": rel,
                    "line": idx,
                    "signature": line.strip()[:140],
                })
                break
    return symbols


def build_repo_map(workspace_dir, focus_files=None, token_budget=600):
    root, files = _safe_workspace_files(workspace_dir, max_files=180)
    focus_set = {str(x) for x in (focus_files or [])}
    ordered = sorted(
        files,
        key=lambda p: (
            0 if (str(p.relative_to(root)) in focus_set or p.name in focus_set) else 1,
            str(p.relative_to(root)),
        ),
    )
    entries = []
    used_tokens = 0
    budget = max(80, min(4096, int(token_budget or 600)))

    for fpath in ordered:
        rel = str(fpath.relative_to(root))
        syms = extract_file_symbols(fpath, root)[:10]
        sym_names = [f"{s['name']}@{s['line']}" for s in syms]
        row_text = rel + (": " + ", ".join(sym_names) if sym_names else "")
        cost = estimate_tokens(row_text)
        if used_tokens + cost > budget and entries:
            break
        entries.append({
            "file": rel,
            "symbols": sym_names,
            "summary": row_text[:200],
        })
        used_tokens += cost

    return {
        "workspace": str(root),
        "tokenBudget": budget,
        "estimatedTokens": used_tokens,
        "fileCount": len(files),
        "entries": entries,
    }


def _find_lsp_binary(language):
    override = os.environ.get("ALIS_AQUA_LSP_BIN", "").strip()
    if override == "__MISSING__":
        return None
    if override and Path(override).is_file():
        return override
    if language == "rust":
        cargo_ra = Path.home() / ".cargo/bin/rust-analyzer"
        return shutil.which("rust-analyzer") or (str(cargo_ra) if cargo_ra.is_file() else None)
    if language == "python":
        return shutil.which("pylsp") or shutil.which("pyright-langserver")
    if language in ("javascript", "typescript"):
        return shutil.which("typescript-language-server")
    return None


def safe_lsp_init_options():
    """Safe LSP initialization options disabling arbitrary build scripts and proc macros."""
    return {
        "cargo": {
            "buildScripts": {"enable": False},
        },
        "procMacro": {
            "enable": False,
        },
        "checkOnSave": False,
    }


def query_lsp(workspace_dir, action="symbols", symbol="", file_path="", language="auto"):
    root, files = _safe_workspace_files(workspace_dir)
    lang = str(language or "auto").lower()
    if lang == "auto":
        if (root / "Cargo.toml").is_file() or any(p.suffix == ".rs" for p in files):
            lang = "rust"
        elif any(p.suffix == ".py" for p in files):
            lang = "python"
        else:
            lang = "generic"

    lsp_bin = _find_lsp_binary(lang)
    if os.environ.get("ALIS_AQUA_LSP_REQUIRE_BINARY") == "1" and not lsp_bin:
        raise CodeToolError(
            "lsp_server_unavailable",
            f"Missing dependency: language server for '{lang}' is not installed.",
        )

    # Collect workspace symbols and definitions safely
    all_symbols = []
    target_files = [Path(root / file_path).resolve(strict=False)] if file_path else files
    for fpath in target_files:
        if fpath in files:
            all_symbols.extend(extract_file_symbols(fpath, root))

    if action in ("definition", "symbols"):
        q = str(symbol or "").strip()
        matched = [
            s for s in all_symbols
            if not q or s["name"] == q or q.lower() in s["name"].lower()
        ][:24]
        return {
            "workspace": str(root),
            "language": lang,
            "lspBinary": lsp_bin or "ast_fallback",
            "safeInitOptions": safe_lsp_init_options(),
            "action": action,
            "symbols": matched,
        }

    if action == "references":
        q = str(symbol or "").strip()
        if not q:
            raise CodeToolError("invalid_symbol", "Provide a symbol name to find references.")
        refs = search_code(root, q, max_results=24)["matches"]
        return {
            "workspace": str(root),
            "language": lang,
            "lspBinary": lsp_bin or "rg_fallback",
            "safeInitOptions": safe_lsp_init_options(),
            "action": "references",
            "symbol": q,
            "references": refs,
        }

    if action == "diagnostics":
        diagnostics = []
        for fpath in target_files[:40]:
            rel = str(fpath.relative_to(root))
            if fpath.suffix == ".py":
                try:
                    source = fpath.read_text(encoding="utf-8")
                    ast.parse(source, filename=rel)
                except SyntaxError as exc:
                    diagnostics.append({
                        "file": rel,
                        "line": int(exc.lineno or 1),
                        "severity": "error",
                        "message": f"SyntaxError: {exc.msg}",
                    })
        return {
            "workspace": str(root),
            "language": lang,
            "lspBinary": lsp_bin or "syntax_checker",
            "safeInitOptions": safe_lsp_init_options(),
            "action": "diagnostics",
            "diagnostics": diagnostics[:24],
        }

    raise CodeToolError("invalid_lsp_action", f"Unsupported LSP action '{action}'.")


def run_project_tests(workspace_dir, command=None, timeout_sec=30.0):
    root, _ = _safe_workspace_files(workspace_dir)
    if command:
        cmd = command
    elif (root / "pytest.ini").is_file() or list(root.glob("test_*.py")) or list(root.glob("tests/test_*.py")):
        cmd = ["python3", "-m", "unittest", "discover"]
    elif (root / "Cargo.toml").is_file():
        cmd = ["cargo", "test", "--offline"]
    else:
        py_tests = sorted(root.glob("**/test*.py"))
        if py_tests:
            cmd = ["python3", str(py_tests[0])]
        else:
            raise CodeToolError("no_test_runner_detected", "Could not detect a test command for this project.")

    res = run_sandboxed(
        cmd,
        cwd=root,
        writable_dirs=[root],
        readonly_dirs=[root],
        allow_network=False,
        timeout_sec=timeout_sec,
        shell=isinstance(cmd, str),
    )
    return {
        "workspace": str(root),
        "command": cmd if isinstance(cmd, str) else " ".join(str(x) for x in cmd),
        "status": res["status"],
        "exitCode": res["exitCode"],
        "elapsedMs": res["elapsedMs"],
        "lines": res["lines"],
    }


RAG_EXTENSIONS = frozenset((
    ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
    ".py", ".qml", ".js", ".cjs", ".ts", ".rs", ".sh", ".html", ".htm", ".csv", ".tsv", ".ini", ".cfg",
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".epub",
    ".xml", ".sql", ".c", ".cpp", ".h", ".go", ".java", ".kt", ".swift", ".css", ".scss",
))

RICH_DOC_EXTENSIONS = frozenset((
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".epub", ".csv", ".tsv", ".html", ".htm",
))


_DOC_CHUNK_CACHE = {}


def _parse_file_chunks(label, path):
    try:
        st = path.stat()
        stat_sig = (st.st_mtime_ns, st.st_size)
        cache_key = (str(path), label)
    except OSError:
        return []
    cached = _DOC_CHUNK_CACHE.get(cache_key)
    if cached is not None and cached[0] == stat_sig:
        return list(cached[1])
    suffix = path.suffix.lower()
    try:
        if suffix in RICH_DOC_EXTENSIONS:
            import rag_store
            doc_text = rag_store.extract_document_markdown(path)
            lines = doc_text.splitlines() if doc_text else []
        else:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    file_chunks = []
    heading = label
    buf = []
    start_line = 1
    heading_capable = suffix in (".md", ".rst", ".txt") or suffix in RICH_DOC_EXTENSIONS
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("#") and heading_capable:
            if buf:
                body = " ".join(buf).strip()
                if body:
                    file_chunks.append({
                        "source": f"{label}#L{start_line}-L{idx - 1}",
                        "heading": heading[:120],
                        "text": body[:260],
                    })
                buf = []
            heading = stripped.lstrip("#").strip() or label
            start_line = idx
        elif stripped:
            buf.append(stripped)
            if len(" ".join(buf)) >= 220:
                body = " ".join(buf).strip()
                file_chunks.append({
                    "source": f"{label}#L{start_line}-L{idx}",
                    "heading": heading[:120],
                    "text": body[:260],
                })
                buf = []
                start_line = idx + 1
    if buf:
        body = " ".join(buf).strip()
        if body:
            file_chunks.append({
                "source": f"{label}#L{start_line}-L{len(lines)}",
                "heading": heading[:120],
                "text": body[:260],
            })
    if len(_DOC_CHUNK_CACHE) >= 256 and cache_key not in _DOC_CHUNK_CACHE:
        _DOC_CHUNK_CACHE.clear()
    _DOC_CHUNK_CACHE[cache_key] = (stat_sig, tuple(file_chunks))
    return file_chunks


def _collect_doc_chunks(companion_root, extra_dirs=None, include_builtin_docs=True):
    chunks = []
    sources = []
    seen_paths = set()
    if include_builtin_docs:
        comp = Path(companion_root).resolve(strict=False)
        root_candidates = [comp]
        if (comp / "optional/alis-companion").is_dir():
            root_candidates.append((comp / "optional/alis-companion").resolve(strict=False))
        env_comp = os.environ.get("ALIS_COMPANION_SOURCE")
        if env_comp and Path(env_comp).is_dir():
            root_candidates.append(Path(env_comp).resolve(strict=False))
        for base in root_candidates:
            for rel in ("README.md", "docs/README.md", "docs/ALIS_COMPANION_CONTEXT_PROTOCOL_V1.md"):
                cand = base / rel
                if cand.is_file() and not cand.is_symlink() and str(cand) not in seen_paths:
                    sources.append((rel, cand))
                    seen_paths.add(str(cand))

    merged_dirs = list(extra_dirs or [])
    try:
        import rag_store
        for p in rag_store.active_source_paths(extra_dirs=merged_dirs, include_pocket=False):
            if p not in merged_dirs:
                merged_dirs.append(p)
    except Exception:
        pass

    home_resolved = Path.home().resolve(strict=False)
    for d in merged_dirs:
        if is_symlink_raw(d) or is_sensitive_path(d):
            continue
        dp = Path(d).expanduser().resolve(strict=False)
        if dp == home_resolved:
            continue
        if dp.is_file() and not dp.is_symlink() and not is_forbidden_path(dp) and not is_sensitive_path(dp):
            if str(dp) not in seen_paths and dp.suffix.lower() in RAG_EXTENSIONS:
                sources.append((dp.name, dp))
                seen_paths.add(str(dp))
        elif dp.is_dir() and not is_forbidden_path(dp) and not is_sensitive_path(dp):
            stop_dir = False
            for dirpath, dirnames, filenames in os.walk(dp, followlinks=False):
                dirnames[:] = sorted(
                    dn for dn in dirnames if dn not in IGNORED_DIRS and not dn.startswith(".")
                )
                for fname in sorted(filenames):
                    if fname.startswith("."):
                        continue
                    f = Path(dirpath) / fname
                    suffix = f.suffix.lower()
                    if f.is_symlink() or not f.is_file() or suffix not in RAG_EXTENSIONS:
                        continue
                    if str(f) in seen_paths:
                        continue
                    try:
                        max_sz = (4 * 1024 * 1024) if suffix in RICH_DOC_EXTENSIONS else (256 * 1024)
                        if f.stat().st_size > max_sz:
                            continue
                        rel_name = str(f.relative_to(dp))
                    except (OSError, ValueError):
                        rel_name = f.name
                    sources.append((rel_name, f))
                    seen_paths.add(str(f))
                    if len(sources) >= 80:
                        stop_dir = True
                        break
                if stop_dir:
                    break

    for label, path in sources:
        chunks.extend(_parse_file_chunks(label, path))
    return chunks


def retrieve_rag(prompt, companion_root, extra_dirs=None, token_budget=480, page=1, page_size=3):
    import rag_store

    raw_prompt = str(prompt or "").strip().lower()
    stop_words = {
        "the", "and", "how", "does", "what", "with", "for", "from", "search", "find",
        "rag", "doc", "docs", "tai", "lieu", "tim", "kiem", "tra", "cuu",
        "của", "trong", "như", "thế", "nào", "theo", "tài", "liệu",
        "là", "có", "và", "các", "những", "một", "để", "với", "cho", "từ",
        "này", "đó", "khi", "đã", "đang", "sẽ", "không", "được", "về", "hay",
        "hoặc", "gì", "ở", "tại", "bạn", "mình", "tôi", "hãy", "giúp",
        "la", "co", "va", "cac", "nhung", "mot", "de", "voi", "cho", "tu",
        "nay", "do", "khi", "da", "dang", "se", "khong", "duoc", "ve", "gi",
    }
    mentions_builtin_topic = bool(
        re.search(
            r"\b(?:alis|companion|aqua|octo|abyss|shader|glsl\w*|portal|quickshell|niri|waffle|extraction|pocket|mcp|skill|arch|archlinux|pacman|makepkg|systemctl|journalctl|wlrlayershell|panelwindow|kdl)\b",
            raw_prompt,
        )
    )
    domain_chunks = []
    try:
        import domain_rag
        detected_doms = domain_rag.detect_domains(raw_prompt)
        if detected_doms:
            c_chunks, p_chunks = domain_rag.collect_domain_chunks(
                domains=detected_doms,
                include_canonical=True,
                include_personal=True,
            )
            domain_chunks = c_chunks + p_chunks
    except Exception:
        domain_chunks = []

    user_chunks = _collect_doc_chunks(companion_root, extra_dirs=extra_dirs, include_builtin_docs=False)
    if user_chunks and not mentions_builtin_topic and not domain_chunks:
        scored = rag_store.query_hybrid_rag(raw_prompt, user_chunks, stop_words=stop_words)
    else:
        chunks = domain_chunks + _collect_doc_chunks(companion_root, extra_dirs=extra_dirs, include_builtin_docs=True)
        scored = rag_store.query_hybrid_rag(raw_prompt, chunks, stop_words=stop_words)

    matched_chunks = [item[1] for item in scored]
    p_idx = max(1, int(page or 1))
    p_size = max(1, min(5, int(page_size or 3)))
    start = (p_idx - 1) * p_size
    page_items = matched_chunks[start:start + p_size]

    budget = max(64, min(2048, int(token_budget or 480)))
    selected = []
    citations = []
    tokens = 0
    for item in page_items:
        formatted = f"[{item['source']}] {item['heading']}: {item['text']}"[:300]
        cost = estimate_tokens(formatted)
        if tokens + cost > budget and selected:
            break
        selected.append(formatted)
        citations.append({
            "source": item["source"],
            "heading": item["heading"],
            "snippet": item["text"],
        })
        tokens += cost

    nexus_extra = []
    try:
        import nexus_rag
        if nexus_rag.resolve_nexus_vault_path() is not None:
            nx_res = nexus_rag.search_nexus_knowledge(
                raw_prompt,
                top_k=max(3, p_size),
                token_budget=max(120, budget - tokens if selected else budget),
            )
            seen_fmt = set(selected)
            for idx, snip in enumerate(nx_res.get("snippets") or []):
                if snip in seen_fmt:
                    continue
                cite = (nx_res.get("citations") or [])[idx] if idx < len(nx_res.get("citations") or []) else {}
                cost = estimate_tokens(snip)
                if tokens + cost > budget and selected:
                    break
                selected.append(snip)
                citations.append({
                    "source": cite.get("source") or "Nexus Vault",
                    "heading": cite.get("heading") or "",
                    "snippet": cite.get("snippet") or snip,
                })
                nexus_extra.append(cite)
                seen_fmt.add(snip)
                tokens += cost
    except Exception:
        nexus_extra = []

    return {
        "snippets": selected[:4],
        "citations": citations[:4],
        "estimatedTokens": tokens,
        "tokenBudget": budget,
        "page": p_idx,
        "pageSize": p_size,
        "totalMatches": len(matched_chunks) + len(nexus_extra),
        "hasMore": (start + p_size) < len(matched_chunks),
        "engine": "sqlite-fts5-hybrid",
        "ragUsed": len(selected) > 0,
        "fts5Used": True,
        "retrievalMode": "hybrid_fts5_bm25_semantic",
    }


def compact_evidence(evidence_items, token_budget=600):
    """Compact conversation/tool observations while preserving receipts, file paths, and citations."""
    budget = max(80, int(token_budget or 600))
    kept = []
    used = 0
    # Prioritize items with receipts, citations, or errors first
    prioritized = sorted(
        enumerate(evidence_items or []),
        key=lambda pair: (
            0 if any(tag in str(pair[1]) for tag in ("rcpt_", "[README", "#L", "error", "failed", "completed")) else 1,
            pair[0],
        ),
    )
    for _, item in prioritized:
        text = str(item).strip()
        if not text:
            continue
        summary = text if len(text) <= 220 else (text[:190] + " ... " + text[-25:])
        cost = estimate_tokens(summary)
        if used + cost > budget and kept:
            break
        kept.append(summary)
        used += cost
    return {
        "compacted": kept,
        "estimatedTokens": used,
        "tokenBudget": budget,
    }


def read_file(file_path, max_chars=24000, start_line=1, end_line=250):
    """Read text, structured paragraphs, and Markdown tables from .docx, .pdf, rich documents, or source files."""
    import rag_store

    raw = str(file_path or "").strip()
    if not raw:
        return {
            "ok": False,
            "error": "invalid_path",
            "path": "",
            "name": "",
            "format": "",
            "text": "",
            "content": "",
            "paragraphs": [],
            "tables": [],
            "markdownTables": [],
            "lines": [],
            "totalLines": 0,
            "metadata": {},
        }

    if rag_store.is_forbidden_path(raw):
        return {
            "ok": False,
            "error": "forbidden_path",
            "path": raw,
            "name": "",
            "format": "",
            "text": "",
            "content": "",
            "paragraphs": [],
            "tables": [],
            "markdownTables": [],
            "lines": [],
            "totalLines": 0,
            "metadata": {},
        }
    p = Path(raw).expanduser().resolve(strict=False)
    if rag_store.is_forbidden_path(p):
        return {
            "ok": False,
            "error": "forbidden_path",
            "path": str(p),
            "name": p.name,
            "format": p.suffix.lower().lstrip("."),
            "text": "",
            "content": "",
            "paragraphs": [],
            "tables": [],
            "markdownTables": [],
            "lines": [],
            "totalLines": 0,
            "metadata": {},
        }
    if not p.exists() or not p.is_file():
        return {
            "ok": False,
            "error": "file_not_found",
            "path": str(p),
            "name": p.name,
            "format": p.suffix.lower().lstrip("."),
            "text": "",
            "content": "",
            "paragraphs": [],
            "tables": [],
            "markdownTables": [],
            "lines": [],
            "totalLines": 0,
            "metadata": {},
        }

    suffix = p.suffix.lower()
    fmt = suffix.lstrip(".") or "txt"
    limit = max(256, int(max_chars or 24000))
    s_line = max(1, int(start_line or 1))
    e_line = max(s_line, int(end_line or 250))

    paragraphs: list[str] = []
    tables: list[list[list[str]]] = []
    markdown_tables: list[str] = []
    metadata: dict[str, str] = {}

    sheets: list[dict] = []
    formulas: list[dict] = []
    formula_errors: list[dict] = []

    if suffix == ".docx":
        info = rag_store._extract_docx_structured(p)
        full_text = str(info.get("text") or "")
        paragraphs = list(info.get("paragraphs") or [])
        tables = list(info.get("tables") or [])
        markdown_tables = list(info.get("markdownTables") or [])
        metadata = dict(info.get("metadata") or {})
    elif suffix == ".xlsx":
        info = rag_store._extract_xlsx_structured(p)
        full_text = str(info.get("text") or "")
        paragraphs = list(info.get("paragraphs") or [])
        tables = list(info.get("tables") or [])
        markdown_tables = list(info.get("markdownTables") or [])
        sheets = list(info.get("sheets") or [])
        formulas = list(info.get("formulas") or [])
        formula_errors = list(info.get("formulaErrors") or [])
        metadata = dict(info.get("metadata") or {})
    elif suffix == ".pdf":
        full_text = rag_store._extract_pdf_text(p)
        metadata = rag_store._extract_pdf_metadata(p)
        paragraphs = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
        # Detect pipe-delimited or multi-column space-aligned table rows in layout-preserved PDF text
        cur_tbl: list[list[str]] = []
        for ln in full_text.splitlines():
            stripped = ln.strip()
            if not stripped:
                if len(cur_tbl) >= 2:
                    tables.append(cur_tbl)
                cur_tbl = []
                continue
            if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if not all(set(c) <= {"-", ":", " "} and c for c in cells):
                    cur_tbl.append(cells)
            elif len(re.split(r"\s{2,}", stripped)) >= 2:
                cols = [c.strip() for c in re.split(r"\s{2,}", stripped) if c.strip()]
                if len(cols) >= 2:
                    cur_tbl.append(cols)
            else:
                if len(cur_tbl) >= 2:
                    tables.append(cur_tbl)
                cur_tbl = []
        if len(cur_tbl) >= 2:
            tables.append(cur_tbl)
        for tbl_rows in tables:
            col_count = max(len(r) for r in tbl_rows)
            md_rows = []
            for idx, r in enumerate(tbl_rows):
                padded = r + [""] * (col_count - len(r))
                md_rows.append("| " + " | ".join(padded) + " |")
                if idx == 0:
                    md_rows.append("| " + " | ".join("---" for _ in range(col_count)) + " |")
            markdown_tables.append("\n".join(md_rows))
    elif suffix in rag_store.RICH_DOCUMENT_EXTENSIONS:
        full_text = rag_store.extract_document_markdown(p, max_chars=limit)
        paragraphs = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    else:
        try:
            max_raw = 16 * 1024 * 1024
            if p.stat().st_size > max_raw:
                return {
                    "ok": False,
                    "error": "file_too_large",
                    "path": str(p),
                    "name": p.name,
                    "format": fmt,
                    "text": "",
                    "content": "",
                    "paragraphs": [],
                    "tables": [],
                    "markdownTables": [],
                    "lines": [],
                    "totalLines": 0,
                    "metadata": {},
                }
            raw_bytes = p.read_bytes()
        except OSError:
            return {
                "ok": False,
                "error": "read_failed",
                "path": str(p),
                "name": p.name,
                "format": fmt,
                "text": "",
                "content": "",
                "paragraphs": [],
                "tables": [],
                "markdownTables": [],
                "lines": [],
                "totalLines": 0,
                "metadata": {},
            }
        if b"\x00" in raw_bytes[:4096]:
            return {
                "ok": False,
                "error": "binary_file",
                "path": str(p),
                "name": p.name,
                "format": fmt,
                "text": "",
                "content": "",
                "paragraphs": [],
                "tables": [],
                "markdownTables": [],
                "lines": [],
                "totalLines": 0,
                "metadata": {},
            }
        full_text = raw_bytes.decode("utf-8", errors="replace")
        paragraphs = [ln for ln in full_text.splitlines() if ln.strip()]

    clipped = full_text[:limit]
    all_lines = full_text.splitlines()
    sliced_lines = all_lines[s_line - 1:e_line]
    res_dict = {
        "ok": True,
        "path": str(p),
        "name": p.name,
        "format": fmt,
        "text": clipped,
        "content": clipped,
        "paragraphs": paragraphs,
        "tables": tables,
        "markdownTables": markdown_tables,
        "lines": sliced_lines,
        "totalLines": len(all_lines),
        "metadata": metadata,
    }
    if suffix == ".xlsx":
        res_dict["sheets"] = sheets
        res_dict["formulas"] = formulas
        res_dict["formulaErrors"] = formula_errors
    elif suffix == ".pdf":
        res_dict["pageCount"] = int(metadata.get("pageCount") or 1)
    return res_dict


inspect_file = read_file


def _parse_markdown_blocks(markdown_text: str) -> list[dict]:
    """Parse Markdown text into ordered blocks: headings, paragraphs, and tables."""
    blocks: list[dict] = []
    lines = (markdown_text or "").splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw_ln = lines[i]
        stripped = raw_ln.strip()
        if not stripped:
            i += 1
            continue
        # Check for Markdown table row: | col1 | col2 |
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            tbl_rows: list[list[str]] = []
            while i < n:
                row_s = lines[i].strip()
                if not (row_s.startswith("|") and row_s.endswith("|") and row_s.count("|") >= 2):
                    break
                cells = [c.strip() for c in row_s.strip("|").split("|")]
                # Skip separator row like | --- | :---: |
                if all(bool(c) and set(c) <= {"-", ":", " "} for c in cells):
                    i += 1
                    continue
                tbl_rows.append(cells)
                i += 1
            if tbl_rows:
                blocks.append({"type": "table", "rows": tbl_rows})
            continue
        if stripped.startswith("### "):
            blocks.append({"type": "heading", "level": 3, "text": stripped[4:].strip()})
        elif stripped.startswith("## "):
            blocks.append({"type": "heading", "level": 2, "text": stripped[3:].strip()})
        elif stripped.startswith("# "):
            blocks.append({"type": "heading", "level": 1, "text": stripped[2:].strip()})
        else:
            blocks.append({"type": "paragraph", "text": stripped})
        i += 1
    return blocks


def _write_docx_from_markdown(markdown_text: str, output_path: Path, title: str = "") -> None:
    import html
    import zipfile

    blocks = _parse_markdown_blocks(markdown_text)
    if title and (not blocks or blocks[0].get("type") != "heading"):
        blocks.insert(0, {"type": "heading", "level": 1, "text": title})

    body_parts: list[str] = []
    for blk in blocks:
        btype = blk.get("type")
        if btype == "heading":
            lvl = int(blk.get("level") or 1)
            style_id = f"Heading{min(3, max(1, lvl))}"
            esc_txt = html.escape(str(blk.get("text") or ""))
            body_parts.append(
                f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/></w:pPr>'
                f'<w:r><w:t xml:space="preserve">{esc_txt}</w:t></w:r></w:p>'
            )
        elif btype == "table":
            rows = blk.get("rows") or []
            if not rows:
                continue
            col_count = max(len(r) for r in rows)
            tr_parts: list[str] = []
            for r in rows:
                padded = list(r) + [""] * (col_count - len(r))
                tc_parts = []
                for cell in padded:
                    esc_cell = html.escape(str(cell or ""))
                    tc_parts.append(
                        f'<w:tc><w:p><w:r><w:t xml:space="preserve">{esc_cell}</w:t></w:r></w:p></w:tc>'
                    )
                tr_parts.append("<w:tr>" + "".join(tc_parts) + "</w:tr>")
            body_parts.append("<w:tbl>" + "".join(tr_parts) + "</w:tbl>")
        else:
            esc_txt = html.escape(str(blk.get("text") or ""))
            body_parts.append(
                f'<w:p><w:r><w:t xml:space="preserve">{esc_txt}</w:t></w:r></w:p>'
            )

    if not body_parts:
        body_parts.append('<w:p><w:r><w:t></w:t></w:r></w:p>')

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body_parts)
        + "<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
        "</w:body></w:document>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    doc_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )
    inferred_title = title or (str(blocks[0].get("text") or "") if blocks and blocks[0].get("type") == "heading" else output_path.stem)
    esc_title = html.escape(inferred_title)
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{esc_title}</dc:title>"
        "<dc:creator>Alis-Companion</dc:creator>"
        "</cp:coreProperties>"
    )

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels_xml)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("docProps/core.xml", core_xml)


def _write_pdf_from_text(markdown_text: str, output_path: Path, title: str = "") -> None:
    lines = [ln.rstrip() for ln in (markdown_text or "").splitlines()]
    first_non_empty = next((ln.strip() for ln in lines if ln.strip()), "")
    if title and (not first_non_empty or (not first_non_empty.startswith("#") and title not in first_non_empty)):
        lines.insert(0, f"# {title}")
    if not lines:
        lines = [title or output_path.stem]

    cmap = (
        b"/CIDInit /ProcSet findresource begin\n"
        b"12 dict begin\n"
        b"begincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /Adobe-Identity-UCS def\n"
        b"/CMapType 2 def\n"
        b"1 begincodespacerange\n"
        b"<0000> <FFFF>\n"
        b"endcodespacerange\n"
        b"1 beginbfrange\n"
        b"<0000> <FFFF> <0000>\n"
        b"endbfrange\n"
        b"endcmap\n"
        b"CMapName currentdict /CMap defineresource pop\n"
        b"end\n"
        b"end\n"
    )

    stream_cmds = ["BT", "/F1 11 Tf", "54 738 Td", "14 TL"]
    for ln in lines[:120]:
        hex_str = ln.encode("utf-16-be").hex().upper()
        stream_cmds.append(f"<{hex_str}> Tj T*")
    stream_cmds.append("ET")
    stream_bytes = "\n".join(stream_cmds).encode("ascii")

    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii") + stream_bytes + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type0 /BaseFont /Helvetica /Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>\nendobj\n",
        b"6 0 obj\n<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Helvetica /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 600 >>\nendobj\n",
        f"7 0 obj\n<< /Length {len(cmap)} >>\nstream\n".encode("ascii") + cmap + b"\nendstream\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    output_path.write_bytes(bytes(out))


def _index_to_col_letters(col_idx: int) -> str:
    n = max(0, int(col_idx)) + 1
    chars: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars)) or "A"


def _eval_simple_excel_formula(formula_expr: str, numeric_grid: dict[tuple[int, int], float]) -> str:
    """Evaluate common Excel formulas (SUM, AVERAGE, MIN, MAX, COUNT, simple binary ops) for cached <v>."""
    expr = str(formula_expr or "").strip().lstrip("=").strip()
    if not expr:
        return ""

    def _ref_to_rc(ref_str: str) -> tuple[int, int] | None:
        m = re.match(r"^\$?([A-Za-z]+)\$?(\d+)$", ref_str.strip())
        if not m:
            return None
        c_idx = 0
        for ch in m.group(1).upper():
            c_idx = c_idx * 26 + (ord(ch) - ord("A") + 1)
        return (int(m.group(2)) - 1, c_idx - 1)

    def _collect_args_values(args_str: str) -> list[float]:
        vals: list[float] = []
        for part in args_str.split(","):
            tok = part.strip()
            if ":" in tok:
                left_s, right_s = tok.split(":", 1)
                rc1 = _ref_to_rc(left_s)
                rc2 = _ref_to_rc(right_s)
                if rc1 and rc2:
                    r_lo, r_hi = min(rc1[0], rc2[0]), max(rc1[0], rc2[0])
                    c_lo, c_hi = min(rc1[1], rc2[1]), max(rc1[1], rc2[1])
                    if (r_hi - r_lo + 1) * (c_hi - c_lo + 1) > 10000:
                        continue
                    for rr in range(r_lo, r_hi + 1):
                        for cc in range(c_lo, c_hi + 1):
                            if (rr, cc) in numeric_grid:
                                vals.append(numeric_grid[(rr, cc)])
            else:
                rc = _ref_to_rc(tok)
                if rc and rc in numeric_grid:
                    vals.append(numeric_grid[rc])
                else:
                    try:
                        vals.append(float(tok))
                    except ValueError:
                        pass
        return vals

    m_fn = re.match(r"^([A-Za-z]+)\(([^()]*)\)$", expr)
    if m_fn:
        fn_name = m_fn.group(1).upper()
        vals = _collect_args_values(m_fn.group(2))
        if not vals:
            return "0"
        res_num: float | None = None
        if fn_name == "SUM":
            res_num = sum(vals)
        elif fn_name == "AVERAGE":
            res_num = sum(vals) / len(vals)
        elif fn_name == "MIN":
            res_num = min(vals)
        elif fn_name == "MAX":
            res_num = max(vals)
        elif fn_name in ("COUNT", "COUNTA"):
            res_num = float(len(vals))
        if res_num is not None:
            return str(int(res_num)) if res_num == int(res_num) else f"{res_num:.6g}"

    # Simple binary cell expression: e.g. B2+C2, B2-C2, B2*C2, B2/C2
    m_bin = re.match(r"^(\$?[A-Za-z]+\$?\d+|\d+(?:\.\d+)?)\s*([+\-*/])\s*(\$?[A-Za-z]+\$?\d+|\d+(?:\.\d+)?)$", expr)
    if m_bin:
        def _resolve_operand(op_s: str) -> float | None:
            rc = _ref_to_rc(op_s)
            if rc is not None:
                return numeric_grid.get(rc, 0.0)
            try:
                return float(op_s)
            except ValueError:
                return None

        lhs = _resolve_operand(m_bin.group(1))
        op_ch = m_bin.group(2)
        rhs = _resolve_operand(m_bin.group(3))
        if lhs is not None and rhs is not None:
            if op_ch == "+":
                out_v = lhs + rhs
            elif op_ch == "-":
                out_v = lhs - rhs
            elif op_ch == "*":
                out_v = lhs * rhs
            elif op_ch == "/" and rhs != 0:
                out_v = lhs / rhs
            else:
                return ""
            return str(int(out_v)) if out_v == int(out_v) else f"{out_v:.6g}"
    return ""


def _parse_spreadsheet_sheets(source_text: str, default_sheet_name: str = "Sheet1") -> list[tuple[str, list[list[str]]]]:
    import csv
    import io

    raw = str(source_text or "").strip()
    if not raw:
        return [(default_sheet_name or "Sheet1", [["Column1"]])]

    # Split by `### Sheet: <name>` if present
    sections: list[tuple[str, str]] = []
    cur_name = default_sheet_name or "Sheet1"
    cur_lines: list[str] = []
    has_sheet_header = False
    for ln in raw.splitlines():
        m_sh = re.match(r"^#{1,4}\s*Sheet:\s*(.+)$", ln.strip(), re.IGNORECASE)
        if m_sh:
            if has_sheet_header and cur_lines:
                sections.append((cur_name, "\n".join(cur_lines)))
                cur_lines = []
            cur_name = m_sh.group(1).strip()[:31] or "Sheet1"
            has_sheet_header = True
        else:
            cur_lines.append(ln)
    if cur_lines or not sections:
        sections.append((cur_name, "\n".join(cur_lines)))

    parsed_sheets: list[tuple[str, list[list[str]]]] = []
    for sh_name, body_txt in sections:
        blocks = _parse_markdown_blocks(body_txt)
        tbl_blocks = [b["rows"] for b in blocks if b.get("type") == "table" and b.get("rows")]
        if tbl_blocks:
            rows: list[list[str]] = []
            for tb in tbl_blocks:
                rows.extend(tb)
            parsed_sheets.append((sh_name, rows))
            continue

        non_empty = [ln for ln in body_txt.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if not non_empty:
            parsed_sheets.append((sh_name, [[]]))
            continue
        delim = "\t" if any("\t" in ln for ln in non_empty[:5]) else ("," if any("," in ln for ln in non_empty[:5]) else None)
        if delim:
            reader = csv.reader(io.StringIO("\n".join(non_empty)), delimiter=delim)
            rows = [[c.strip() for c in r] for r in reader if any(c.strip() for c in r)]
            parsed_sheets.append((sh_name, rows or [[non_empty[0]]]))
        else:
            parsed_sheets.append((sh_name, [[ln.strip()] for ln in non_empty]))

    return parsed_sheets or [(default_sheet_name or "Sheet1", [[]])]


def _write_xlsx_from_content(source_text: str, output_path: Path, title: str = "") -> None:
    import html
    import zipfile

    default_sh = (title or output_path.stem or "Sheet1").strip()[:31] or "Sheet1"
    # Clean invalid Excel sheet name chars: \ / ? * : [ ]
    default_sh = re.sub(r"[\\/?*:\[\]]", "_", default_sh) or "Sheet1"
    sheets_data = _parse_spreadsheet_sheets(source_text, default_sheet_name=default_sh)

    shared_strings: list[str] = []
    shared_index: dict[str, int] = {}

    def _get_ss_idx(val: str) -> int:
        if val in shared_index:
            return shared_index[val]
        idx = len(shared_strings)
        shared_strings.append(val)
        shared_index[val] = idx
        return idx

    sheet_xml_map: dict[str, str] = {}
    workbook_sheets_xml: list[str] = []
    workbook_rels_xml: list[str] = []
    content_type_overrides: list[str] = []

    for s_idx, (raw_sh_name, rows) in enumerate(sheets_data, start=1):
        clean_sh_name = re.sub(r"[\\/?*:\[\]]", "_", raw_sh_name.strip())[:31] or f"Sheet{s_idx}"
        esc_sh_name = html.escape(clean_sh_name, quote=True)
        rid = f"rId{s_idx}"
        sheet_filename = f"sheet{s_idx}.xml"
        workbook_sheets_xml.append(f'<sheet name="{esc_sh_name}" sheetId="{s_idx}" r:id="{rid}"/>')
        workbook_rels_xml.append(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/{sheet_filename}"/>'
        )
        content_type_overrides.append(
            f'<Override PartName="/xl/worksheets/{sheet_filename}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )

        numeric_grid: dict[tuple[int, int], float] = {}
        row_xml_parts: list[str] = []
        for r_idx0, row in enumerate(rows):
            r_num = r_idx0 + 1
            cell_xml_parts: list[str] = []
            for c_idx0, raw_cell in enumerate(row):
                cell_str = str(raw_cell or "").strip()
                if not cell_str:
                    continue
                col_let = _index_to_col_letters(c_idx0)
                cell_ref = f"{col_let}{r_num}"

                # Support both `=SUM(B2:C2)` and readback format `270 (=SUM(B2:C2))`
                f_expr = ""
                cached_v = ""
                m_readback_f = re.match(r"^(.+?)\s*\(=(.+)\)$", cell_str)
                if m_readback_f:
                    cached_v = m_readback_f.group(1).strip()
                    f_expr = m_readback_f.group(2).strip()
                elif cell_str.startswith("=") and len(cell_str) > 1:
                    f_expr = cell_str[1:].strip()

                if f_expr:
                    if not cached_v:
                        cached_v = _eval_simple_excel_formula(f_expr, numeric_grid)
                    try:
                        numeric_grid[(r_idx0, c_idx0)] = float(cached_v)
                    except ValueError:
                        pass
                    esc_f = html.escape(f_expr)
                    if cached_v != "":
                        esc_v = html.escape(cached_v)
                        cell_xml_parts.append(f'<c r="{cell_ref}"><f>{esc_f}</f><v>{esc_v}</v></c>')
                    else:
                        cell_xml_parts.append(f'<c r="{cell_ref}"><f>{esc_f}</f></c>')
                    continue

                if re.match(r"^-?\d+(?:\.\d+)?$", cell_str):
                    try:
                        numeric_grid[(r_idx0, c_idx0)] = float(cell_str)
                    except ValueError:
                        pass
                    cell_xml_parts.append(f'<c r="{cell_ref}"><v>{cell_str}</v></c>')
                elif cell_str.upper() in ("TRUE", "FALSE"):
                    b_val = "1" if cell_str.upper() == "TRUE" else "0"
                    cell_xml_parts.append(f'<c r="{cell_ref}" t="b"><v>{b_val}</v></c>')
                else:
                    ss_id = _get_ss_idx(cell_str)
                    cell_xml_parts.append(f'<c r="{cell_ref}" t="s"><v>{ss_id}</v></c>')

            row_xml_parts.append(f'<row r="{r_num}">' + "".join(cell_xml_parts) + "</row>")

        sheet_xml_map[f"xl/worksheets/{sheet_filename}"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            + "".join(row_xml_parts)
            + "</sheetData></worksheet>"
        )

    styles_rid = f"rId{len(sheets_data) + 1}"
    ss_rid = f"rId{len(sheets_data) + 2}"
    workbook_rels_xml.append(
        f'<Relationship Id="{styles_rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    workbook_rels_xml.append(
        f'<Relationship Id="{ss_rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
    )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(content_type_overrides)
        + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        + "".join(workbook_sheets_xml)
        + "</sheets></workbook>"
    )
    wb_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(workbook_rels_xml)
        + "</Relationships>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )
    ss_items = "".join(
        f'<si><t xml:space="preserve">{html.escape(s)}</t></si>' for s in shared_strings
    )
    shared_strings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">'
        + ss_items
        + "</sst>"
    )
    esc_title = html.escape(title or output_path.stem)
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{esc_title}</dc:title>"
        "<dc:creator>Alis-Companion</dc:creator>"
        "</cp:coreProperties>"
    )

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/sharedStrings.xml", shared_strings_xml)
        for s_path, s_xml in sheet_xml_map.items():
            zf.writestr(s_path, s_xml)


def convert_or_create_document(source_path_or_content, output_path, title=""):
    """Create or convert a document (.docx, .pdf, .xlsx, .md, .txt) from a source path or Markdown/CSV/text content."""
    import rag_store

    raw_out = str(output_path or "").strip()
    if not raw_out:
        return {"ok": False, "error": "invalid_output_path", "path": ""}
    if rag_store.is_forbidden_path(raw_out):
        return {"ok": False, "error": "forbidden_path", "path": raw_out}

    out = Path(raw_out).expanduser().resolve(strict=False)
    if rag_store.is_forbidden_path(out):
        return {"ok": False, "error": "forbidden_path", "path": str(out)}

    src_text = ""
    src_is_file = False
    if isinstance(source_path_or_content, Path):
        cand = source_path_or_content.expanduser().resolve(strict=False)
        if cand.exists() and cand.is_file():
            if rag_store.is_forbidden_path(cand):
                return {"ok": False, "error": "forbidden_path", "path": str(cand)}
            src_is_file = True
            src_text = read_file(cand).get("text", "")
    else:
        raw_src = str(source_path_or_content or "")
        if "\n" not in raw_src and len(raw_src.strip()) <= 512 and raw_src.strip():
            try:
                cand = Path(raw_src.strip()).expanduser().resolve(strict=False)
                if cand.exists() and cand.is_file():
                    if rag_store.is_forbidden_path(cand):
                        return {"ok": False, "error": "forbidden_path", "path": str(cand)}
                    src_is_file = True
                    src_text = read_file(cand).get("text", "")
            except OSError:
                src_is_file = False
        if not src_is_file:
            src_text = raw_src

    doc_title = str(title or "").strip()
    out.parent.mkdir(parents=True, exist_ok=True)
    target_ext = out.suffix.lower()

    if target_ext == ".docx":
        _write_docx_from_markdown(src_text, out, title=doc_title)
    elif target_ext == ".xlsx":
        _write_xlsx_from_content(src_text, out, title=doc_title)
    elif target_ext == ".pdf":
        _write_pdf_from_text(src_text, out, title=doc_title)
    else:
        final_text = src_text
        if doc_title and not final_text.lstrip().startswith("#"):
            final_text = f"# {doc_title}\n\n{final_text}".strip() + "\n"
        out.write_text(final_text, encoding="utf-8")

    return {
        "ok": True,
        "path": str(out),
        "name": out.name,
        "format": target_ext.lstrip(".") or "txt",
        "bytes": out.stat().st_size if out.exists() else 0,
        "title": doc_title or out.stem,
        "sourceWasFile": src_is_file,
    }


