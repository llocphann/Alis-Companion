"""Coding tools (search, token-budgeted repo map, safe LSP, test runner) and cited RAG."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from permission_store import is_forbidden_path
from sandbox_runner import SandboxError, run_sandboxed


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
    fpath = Path(file_path).resolve(strict=False)
    if fpath.is_symlink() or not fpath.is_file():
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


def _collect_doc_chunks(companion_root, extra_dirs=None):
    chunks = []
    sources = []
    comp = Path(companion_root).resolve(strict=False)
    for rel in ("README.md", "docs/README.md", "docs/ALIS_EXTRACTION.md"):
        cand = comp / rel
        if cand.is_file() and not cand.is_symlink():
            sources.append((rel, cand))

    for d in (extra_dirs or []):
        dp = Path(d).expanduser().resolve(strict=False)
        if dp.is_file() and not dp.is_symlink() and not is_forbidden_path(dp):
            sources.append((dp.name, dp))
        elif dp.is_dir() and not is_forbidden_path(dp):
            for f in sorted(dp.rglob("*")):
                if f.is_file() and not f.is_symlink() and f.suffix.lower() in (".md", ".txt", ".rst"):
                    try:
                        rel_name = str(f.relative_to(dp))
                    except ValueError:
                        rel_name = f.name
                    sources.append((rel_name, f))
                    if len(sources) >= 40:
                        break

    for label, path in sources:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        heading = label
        buf = []
        start_line = 1
        for idx, raw in enumerate(lines, start=1):
            stripped = raw.strip()
            if stripped.startswith("#"):
                if buf:
                    body = " ".join(buf).strip()
                    if body:
                        chunks.append({
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
                    chunks.append({
                        "source": f"{label}#L{start_line}-L{idx}",
                        "heading": heading[:120],
                        "text": body[:260],
                    })
                    buf = []
                    start_line = idx + 1
        if buf:
            body = " ".join(buf).strip()
            if body:
                chunks.append({
                    "source": f"{label}#L{start_line}-L{len(lines)}",
                    "heading": heading[:120],
                    "text": body[:260],
                })
    return chunks


def retrieve_rag(prompt, companion_root, extra_dirs=None, token_budget=480, page=1, page_size=3):
    q_words = {
        w for w in re.findall(r"[a-zA-Z0-9_\u00C0-\u1EF9]{3,}", str(prompt or "").lower())
        if w not in {"the", "and", "how", "does", "what", "with", "for", "from", "của", "trong", "như", "thế", "nào"}
    }
    chunks = _collect_doc_chunks(companion_root, extra_dirs=extra_dirs)
    scored = []
    for ch in chunks:
        hay = (ch["heading"] + " " + ch["text"]).lower()
        overlap = sum(2 if w in ch["heading"].lower() else 1 for w in q_words if w in hay)
        if overlap > 0:
            scored.append((overlap, ch))
    scored.sort(key=lambda item: (-item[0], item[1]["source"]))

    # If no keyword overlap but chunks exist, take top overview chunk when explicitly requested
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

    return {
        "snippets": selected[:3],
        "citations": citations[:3],
        "estimatedTokens": tokens,
        "tokenBudget": budget,
        "page": p_idx,
        "pageSize": p_size,
        "totalMatches": len(matched_chunks),
        "hasMore": (start + p_size) < len(matched_chunks),
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
