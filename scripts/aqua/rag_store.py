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


def _nexus_rag():
    try:
        import nexus_rag
        return nexus_rag
    except Exception:
        return None


def _state_file() -> Path:
    nr = _nexus_rag()
    if nr is not None:
        rag_dir = nr.resolve_rag_vault_dir()
        if rag_dir is not None:
            nr.migrate_legacy_rag_to_nexus()
            return rag_dir / "RAG_Sources.md"
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
        if path.suffix.lower() == ".md":
            nr = _nexus_rag()
            if nr is not None:
                return {"version": 1, "sources": nr.parse_rag_sources_markdown(path.read_text(encoding="utf-8", errors="replace"))}
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
    clean_sources = state.get("sources", [])[:64]
    if path.suffix.lower() == ".md":
        nr = _nexus_rag()
        if nr is not None:
            vault = nr.resolve_nexus_vault_path()
            payload_bytes = nr.render_rag_sources_markdown(clean_sources, str(vault or "")).encode("utf-8")
            file_mode = 0o644
        else:
            raise RagStoreError("rag_store_unavailable", "Nexus RAG helper unavailable.")
    else:
        payload_bytes = json.dumps(
            {"version": 1, "sources": clean_sources},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        file_mode = 0o600
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, file_mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload_bytes)
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
    nr = _nexus_rag()
    if nr is not None:
        rag_dir = nr.resolve_rag_vault_dir()
        if rag_dir is not None:
            k_dir = rag_dir / "Knowledge"
            if k_dir.is_dir() and str(k_dir) not in seen:
                seen.append(str(k_dir))
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


RICH_DOCUMENT_EXTENSIONS = frozenset((
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".epub", ".csv", ".tsv", ".html", ".htm",
))


def _extract_pdf_text(path: Path) -> str:
    import re
    import shutil
    import subprocess
    import zlib

    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin and os.environ.get("ALIS_AQUA_PDFTOTEXT_BIN", "") != "__MISSING__":
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-layout", "-enc", "UTF-8", str(path), "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=6.0,
                check=False,
                text=True,
            )
            if proc.returncode == 0 and proc.stdout and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass

    # Try pypdf / fitz (PyMuPDF) if installed
    for mod_name in ("pypdf", "fitz"):
        try:
            if mod_name == "pypdf":
                import pypdf  # type: ignore
                reader = pypdf.PdfReader(str(path))
                pages_txt = [ (pg.extract_text() or "").strip() for pg in reader.pages ]
                joined = "\n\n".join(t for t in pages_txt if t)
                if joined.strip():
                    return joined.strip()
            elif mod_name == "fitz":
                import fitz  # type: ignore
                doc = fitz.open(str(path))
                pages_txt = [ (pg.get_text("text") or "").strip() for pg in doc ]
                doc.close()
                joined = "\n\n".join(t for t in pages_txt if t)
                if joined.strip():
                    return joined.strip()
        except Exception:
            continue

    # Pure-Python fallback parser for PDF content streams and literal strings
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return ""

    buffers: list[bytes] = [raw_bytes]
    for match in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", raw_bytes, flags=re.DOTALL):
        stream_data = match.group(1)
        buffers.append(stream_data)
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decompressed = zlib.decompress(stream_data, wbits)
                buffers.append(decompressed)
                break
            except Exception:
                continue

    extracted_lines: list[str] = []

    def _unescape_pdf_str(raw_s: bytes) -> str:
        try:
            s = raw_s.decode("utf-8")
        except UnicodeDecodeError:
            s = raw_s.decode("latin-1", errors="ignore")
        s = (
            s.replace(r"\(", "(")
            .replace(r"\)", ")")
            .replace(r"\n", "\n")
            .replace(r"\r", " ")
            .replace(r"\t", " ")
            .replace(r"\\", "\\")
        )
        return s.strip()

    for buf in buffers:
        # Extract TJ arrays: [(Hello) -20 (World)] TJ
        for tj_arr in re.finditer(rb"\[(.*?)\]\s*TJ", buf, flags=re.DOTALL):
            parts = [
                _unescape_pdf_str(m.group(1))
                for m in re.finditer(rb"\((?:\\.|[^\\()])*\)", tj_arr.group(1))
            ]
            joined = "".join(p for p in parts if p)
            if joined.strip():
                extracted_lines.append(joined.strip())
        # Extract <hex_utf16be> Tj
        for tj_hex in re.finditer(rb"<([0-9A-Fa-f]{4,})>\s*(?:Tj|'|\"|\r|\n)", buf):
            raw_hex = tj_hex.group(1).decode("ascii", errors="ignore")
            if len(raw_hex) % 2 == 1:
                raw_hex = raw_hex[:-1]
            try:
                raw_b = bytes.fromhex(raw_hex)
                if raw_b.startswith(b"\xfe\xff"):
                    val = raw_b[2:].decode("utf-16-be", errors="ignore").strip()
                elif len(raw_b) % 2 == 0:
                    val = raw_b.decode("utf-16-be", errors="ignore").strip()
                else:
                    val = raw_b.decode("utf-8", errors="ignore").strip()
                if val:
                    extracted_lines.append(val)
            except Exception:
                continue
        # Extract (string) Tj / ' / "
        for tj_str in re.finditer(rb"\(((?:\\.|[^\\()])*)\)\s*(?:Tj|'|\"|\r|\n)", buf):
            val = _unescape_pdf_str(tj_str.group(1))
            if val and len(val) >= 2:
                extracted_lines.append(val)

    deduped: list[str] = []
    for ln in extracted_lines:
        cleaned = re.sub(r"\s+", " ", ln).strip()
        if cleaned and (not deduped or deduped[-1] != cleaned):
            deduped.append(cleaned)
    return "\n".join(deduped)


def _extract_pdf_metadata(path: Path) -> dict:
    import re
    import shutil
    import subprocess

    meta: dict[str, str] = {}
    pdfinfo_bin = shutil.which("pdfinfo")
    if pdfinfo_bin and os.environ.get("ALIS_AQUA_PDFINFO_BIN", "") != "__MISSING__":
        try:
            proc = subprocess.run(
                [pdfinfo_bin, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=4.0,
                check=False,
                text=True,
            )
            if proc.returncode == 0 and proc.stdout:
                for ln in proc.stdout.splitlines():
                    if ":" not in ln:
                        continue
                    k, v = ln.split(":", 1)
                    key = k.strip().lower()
                    val = v.strip()
                    if not val:
                        continue
                    if key == "pages" and val.isdigit():
                        meta["pageCount"] = val
                    elif key in ("title", "author", "creator", "producer", "encrypted"):
                        meta[key] = val
        except (OSError, subprocess.SubprocessError):
            pass

    if "pageCount" not in meta:
        try:
            raw_bytes = path.read_bytes()
            pages = len(re.findall(rb"/Type\s*/Page\b", raw_bytes))
            meta["pageCount"] = str(max(1, pages)) if raw_bytes.startswith(b"%PDF") else "0"
        except OSError:
            meta["pageCount"] = "0"
    return meta


def _extract_docx_structured(path: Path) -> dict:
    import xml.etree.ElementTree as ET
    import zipfile

    empty_res = {
        "text": "",
        "paragraphs": [],
        "tables": [],
        "markdownTables": [],
        "metadata": {},
    }
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                return empty_res
            xml_bytes = zf.read("word/document.xml")
            core_bytes = zf.read("docProps/core.xml") if "docProps/core.xml" in names else b""
    except Exception:
        return empty_res

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return empty_res

    metadata: dict[str, str] = {}
    if core_bytes:
        try:
            core_root = ET.fromstring(core_bytes)
            for child in core_root.iter():
                local = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
                if local in ("title", "creator", "subject", "description", "modified") and (child.text or "").strip():
                    metadata[local] = (child.text or "").strip()
        except ET.ParseError:
            pass

    ns_w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: list[str] = []
    paragraphs: list[str] = []
    tables: list[list[list[str]]] = []
    markdown_tables: list[str] = []

    def _format_para(elem) -> str:
        style_val = ""
        pPr = elem.find(f"{ns_w}pPr")
        if pPr is not None:
            pStyle = pPr.find(f"{ns_w}pStyle")
            if pStyle is not None:
                style_val = str(pStyle.attrib.get(f"{ns_w}val") or "")
        runs = [t.text for t in elem.iter(f"{ns_w}t") if t.text]
        text = "".join(runs).strip()
        if not text:
            return ""
        if "heading1" in style_val.lower() or style_val == "Title":
            return f"# {text}"
        if "heading2" in style_val.lower():
            return f"## {text}"
        if "heading" in style_val.lower():
            return f"### {text}"
        return text

    def _walk_container(container) -> None:
        for child in list(container):
            if child.tag == f"{ns_w}p":
                formatted = _format_para(child)
                if formatted:
                    lines.append(formatted)
                    paragraphs.append(formatted)
            elif child.tag == f"{ns_w}tbl":
                tbl_rows: list[list[str]] = []
                for tr in child.findall(f"{ns_w}tr"):
                    cells: list[str] = []
                    for tc in tr.findall(f"{ns_w}tc"):
                        p_nodes = tc.findall(f"{ns_w}p")
                        if p_nodes:
                            cell_text = " ".join(
                                "".join(t.text for t in p.iter(f"{ns_w}t") if t.text).strip()
                                for p in p_nodes
                            ).strip()
                        else:
                            cell_text = "".join(t.text for t in tc.iter(f"{ns_w}t") if t.text).strip()
                        cells.append(cell_text.replace("\n", " ").strip())
                    if any(cells):
                        tbl_rows.append(cells)
                if tbl_rows:
                    tables.append(tbl_rows)
                    md_rows: list[str] = []
                    col_count = max(len(r) for r in tbl_rows)
                    for idx, r in enumerate(tbl_rows):
                        padded = r + [""] * (col_count - len(r))
                        md_rows.append("| " + " | ".join(padded) + " |")
                        if idx == 0:
                            md_rows.append("| " + " | ".join("---" for _ in range(col_count)) + " |")
                    md_table_str = "\n".join(md_rows)
                    markdown_tables.append(md_table_str)
                    lines.extend(md_rows)
            else:
                _walk_container(child)

    body = root.find(f"{ns_w}body")
    _walk_container(body if body is not None else root)
    return {
        "text": "\n".join(lines),
        "paragraphs": paragraphs,
        "tables": tables,
        "markdownTables": markdown_tables,
        "metadata": metadata,
    }


def _extract_docx_text(path: Path) -> str:
    return _extract_docx_structured(path).get("text", "")


def _col_letters_to_index(cell_ref: str) -> int | None:
    import re
    m = re.match(r"^([A-Za-z]+)\d+$", str(cell_ref or "").strip())
    if not m:
        return None
    idx = 0
    for ch in m.group(1).upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def _extract_xlsx_structured(path: Path) -> dict:
    import xml.etree.ElementTree as ET
    import zipfile

    empty_res = {
        "text": "",
        "paragraphs": [],
        "tables": [],
        "markdownTables": [],
        "sheets": [],
        "formulas": [],
        "formulaErrors": [],
        "metadata": {},
    }
    excel_errors = {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!"}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in ss_root.iter():
                    if si.tag.endswith("}si") or si.tag == "si":
                        texts = [t.text for t in si.iter() if (t.tag.endswith("}t") or t.tag == "t") and t.text]
                        shared_strings.append("".join(texts))

            # Map relationship Id -> target worksheet path
            rel_map: dict[str, str] = {}
            if "xl/_rels/workbook.xml.rels" in names:
                try:
                    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                    for rel in rels_root.iter():
                        if rel.tag.endswith("}Relationship") or rel.tag == "Relationship":
                            rid = str(rel.attrib.get("Id") or "").strip()
                            target = str(rel.attrib.get("Target") or "").strip().lstrip("/")
                            if rid and target:
                                if not target.startswith("xl/"):
                                    target = f"xl/{target}"
                                rel_map[rid] = target
                except ET.ParseError:
                    pass

            sheet_entries: list[tuple[str, str]] = []
            if "xl/workbook.xml" in names:
                try:
                    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
                    sheet_idx = 0
                    for sh in wb_root.iter():
                        if sh.tag.endswith("}sheet") or sh.tag == "sheet":
                            sheet_idx += 1
                            sh_name = str(sh.attrib.get("name") or f"Sheet{sheet_idx}").strip()
                            rid = ""
                            for k, v in sh.attrib.items():
                                if k.endswith("}id") or k == "r:id" or k == "id":
                                    rid = str(v).strip()
                                    break
                            target_xml = rel_map.get(rid, f"xl/worksheets/sheet{sheet_idx}.xml")
                            if target_xml in names:
                                sheet_entries.append((sh_name, target_xml))
                except ET.ParseError:
                    pass

            if not sheet_entries:
                for idx, sfile in enumerate(
                    sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")),
                    start=1,
                ):
                    sheet_entries.append((f"Sheet{idx}", sfile))

            lines: list[str] = []
            paragraphs: list[str] = []
            tables: list[list[list[str]]] = []
            markdown_tables: list[str] = []
            sheets_info: list[dict] = []
            formulas: list[dict] = []
            formula_errors: list[dict] = []

            for sh_name, sfile in sheet_entries:
                heading = f"### Sheet: {sh_name}"
                lines.append(heading)
                paragraphs.append(heading)
                s_root = ET.fromstring(zf.read(sfile))
                sheet_rows: list[list[str]] = []
                for row in s_root.iter():
                    if not (row.tag.endswith("}row") or row.tag == "row"):
                        continue
                    row_cells: dict[int, str] = {}
                    next_col = 0
                    for c in row:
                        if not (c.tag.endswith("}c") or c.tag == "c"):
                            continue
                        c_ref = str(c.attrib.get("r") or "").strip()
                        col_idx = _col_letters_to_index(c_ref)
                        if col_idx is None:
                            col_idx = next_col
                        next_col = col_idx + 1
                        c_type = str(c.attrib.get("t") or "").strip()
                        v_val = ""
                        f_val = ""
                        for child in c:
                            if child.tag.endswith("}f") or child.tag == "f":
                                f_val = (child.text or "").strip().lstrip("=")
                            elif child.tag.endswith("}v") or child.tag == "v":
                                v_val = (child.text or "").strip()
                            elif child.tag.endswith("}is") or child.tag == "is":
                                v_val = "".join(t.text or "" for t in child.iter() if t.text).strip()
                        if c_type == "s" and v_val.isdigit():
                            s_idx = int(v_val)
                            if 0 <= s_idx < len(shared_strings):
                                v_val = shared_strings[s_idx]
                        elif c_type == "b":
                            v_val = "TRUE" if v_val == "1" else ("FALSE" if v_val == "0" else v_val)

                        if v_val.upper() in excel_errors or c_type == "e":
                            formula_errors.append({
                                "sheet": sh_name,
                                "cell": c_ref,
                                "error": v_val or "#ERROR!",
                                "formula": f"={f_val}" if f_val else "",
                            })

                        if f_val:
                            formulas.append({
                                "sheet": sh_name,
                                "cell": c_ref,
                                "formula": f"={f_val}",
                                "value": v_val,
                            })
                            cell_display = f"{v_val} (={f_val})" if v_val else f"={f_val}"
                        else:
                            cell_display = v_val
                        row_cells[col_idx] = cell_display.replace("\n", " ").strip()

                    if row_cells:
                        max_c = max(row_cells.keys())
                        row_vals = [row_cells.get(ci, "") for ci in range(max_c + 1)]
                        if any(row_vals):
                            sheet_rows.append(row_vals)

                if sheet_rows:
                    tables.append(sheet_rows)
                    col_count = max(len(r) for r in sheet_rows)
                    md_rows: list[str] = []
                    for idx, r in enumerate(sheet_rows):
                        padded = r + [""] * (col_count - len(r))
                        md_rows.append("| " + " | ".join(padded) + " |")
                        if idx == 0:
                            md_rows.append("| " + " | ".join("---" for _ in range(col_count)) + " |")
                    md_table_str = "\n".join(md_rows)
                    markdown_tables.append(md_table_str)
                    lines.extend(md_rows)
                sheets_info.append({
                    "name": sh_name,
                    "rowCount": len(sheet_rows),
                    "rows": sheet_rows,
                })

            metadata = {
                "sheetCount": str(len(sheets_info)),
                "sheetNames": ", ".join(s["name"] for s in sheets_info),
                "formulaCount": str(len(formulas)),
                "formulaErrorCount": str(len(formula_errors)),
            }
            return {
                "text": "\n".join(lines),
                "paragraphs": paragraphs,
                "tables": tables,
                "markdownTables": markdown_tables,
                "sheets": sheets_info,
                "formulas": formulas,
                "formulaErrors": formula_errors,
                "metadata": metadata,
            }
    except Exception:
        return empty_res


def _extract_xlsx_text(path: Path) -> str:
    return _extract_xlsx_structured(path).get("text", "")



def _extract_pptx_text(path: Path) -> str:
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        with zipfile.ZipFile(path, "r") as zf:
            slide_files = sorted(
                n for n in zf.namelist()
                if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )
            lines: list[str] = []
            for idx, sname in enumerate(slide_files, start=1):
                s_root = ET.fromstring(zf.read(sname))
                texts = [
                    (el.text or "").strip()
                    for el in s_root.iter()
                    if (el.tag.endswith("}t") or el.tag == "t") and (el.text or "").strip()
                ]
                if texts:
                    lines.append(f"# Slide {idx}")
                    lines.extend(texts)
            return "\n".join(lines)
    except Exception:
        return ""


def _extract_odt_text(path: Path) -> str:
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "content.xml" not in zf.namelist():
                return ""
            root = ET.fromstring(zf.read("content.xml"))
            lines: list[str] = []
            for el in root.iter():
                if el.tag.endswith("}h"):
                    txt = "".join(el.itertext()).strip()
                    if txt:
                        lines.append(f"# {txt}")
                elif el.tag.endswith("}p"):
                    txt = "".join(el.itertext()).strip()
                    if txt:
                        lines.append(txt)
            return "\n".join(lines)
    except Exception:
        return ""


def _extract_epub_text(path: Path) -> str:
    import zipfile
    from web_tools import html_to_markdown

    try:
        with zipfile.ZipFile(path, "r") as zf:
            html_names = sorted(
                n for n in zf.namelist()
                if n.lower().endswith((".xhtml", ".html", ".htm"))
            )
            chunks: list[str] = []
            for name in html_names[:30]:
                raw = zf.read(name).decode("utf-8", errors="replace")
                cleaned = html_to_markdown(raw)
                if cleaned:
                    chunks.append(cleaned)
            return "\n\n".join(chunks)
    except Exception:
        return ""


def _extract_csv_markdown(path: Path, delimiter: str = ",") -> str:
    import csv

    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            lines: list[str] = []
            for idx, row in enumerate(reader):
                if idx >= 200:
                    break
                cleaned = [str(c or "").strip().replace("|", "\\|") for c in row]
                if not any(cleaned):
                    continue
                lines.append("| " + " | ".join(cleaned) + " |")
                if idx == 0:
                    lines.append("| " + " | ".join(["---"] * len(cleaned)) + " |")
            return "\n".join(lines)
    except Exception:
        return ""


def extract_document_markdown(path: Path | str, max_chars: int = 120000) -> str:
    """Convert PDF, DOCX, XLSX, PPTX, ODT, EPUB, CSV/TSV, HTML, JSON, or text into clean Markdown."""
    from web_tools import html_to_markdown

    p = Path(path).expanduser().resolve(strict=False)
    if not p.is_file() or p.is_symlink() or is_forbidden_path(p):
        return ""
    suffix = p.suffix.lower()
    text = ""
    if suffix == ".pdf":
        text = _extract_pdf_text(p)
    elif suffix == ".docx":
        text = _extract_docx_text(p)
    elif suffix == ".xlsx":
        text = _extract_xlsx_text(p)
    elif suffix == ".pptx":
        text = _extract_pptx_text(p)
    elif suffix == ".odt":
        text = _extract_odt_text(p)
    elif suffix == ".epub":
        text = _extract_epub_text(p)
    elif suffix == ".csv":
        text = _extract_csv_markdown(p, delimiter=",")
    elif suffix == ".tsv":
        text = _extract_csv_markdown(p, delimiter="\t")
    elif suffix in (".html", ".htm"):
        try:
            raw_html = p.read_text(encoding="utf-8", errors="replace")
            text = html_to_markdown(raw_html)
        except OSError:
            text = ""
    else:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    return text[:max_chars]


def _rag_db_path() -> Path:
    nr = _nexus_rag()
    if nr is not None:
        return nr.external_rag_db_path()
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    folder = (base / "alis-companion").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / "rag_index.db"


def storage_info() -> dict:
    nr = _nexus_rag()
    vault = nr.resolve_nexus_vault_path() if nr is not None else None
    rag_dir = nr.resolve_rag_vault_dir() if nr is not None else None
    sources_path = _state_file()
    return {
        "storageMode": "nexus" if rag_dir is not None else "standalone",
        "nexusVaultPath": str(vault or ""),
        "ragVaultDir": str(rag_dir or ""),
        "ragSourcesPath": str(sources_path),
        "ragDbPath": str(_rag_db_path()),
    }


_LAST_INDEX_SIGNATURE: tuple | None = None


def sync_fts5_chunks(chunks: list[dict]) -> bool:
    """Synchronize parsed RAG document chunks into a local SQLite FTS5 index."""
    global _LAST_INDEX_SIGNATURE
    import sqlite3

    sig = tuple((c.get("source", ""), c.get("heading", ""), len(c.get("text", ""))) for c in chunks)
    db_path = _rag_db_path()
    if _LAST_INDEX_SIGNATURE == sig and db_path.is_file():
        return True

    try:
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(source, heading, body, tokenize='unicode61')"
            )
            conn.execute("DELETE FROM rag_fts")
            conn.executemany(
                "INSERT INTO rag_fts(source, heading, body) VALUES (?, ?, ?)",
                [
                    (str(c.get("source") or ""), str(c.get("heading") or ""), str(c.get("text") or ""))
                    for c in chunks
                ],
            )
            conn.commit()
            _LAST_INDEX_SIGNATURE = sig
            return True
        finally:
            conn.close()
    except Exception:
        return False


def _char_trigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ").strip()
    if len(cleaned) < 3:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + 3] for i in range(min(180, len(cleaned) - 2))}


def query_hybrid_rag(
    query: str,
    chunks: list[dict],
    stop_words: set[str] | None = None,
    min_score: float = 1.0,
) -> list[tuple[float, dict]]:
    """Run hybrid SQLite FTS5 BM25 + semantic token/n-gram similarity + IDF ranking over chunks."""
    import math
    import re
    import sqlite3

    if not chunks:
        return []

    raw_prompt = str(query or "").strip().lower()
    default_sw = {
        "the", "and", "how", "does", "what", "with", "for", "from", "search", "find",
        "rag", "doc", "docs", "tai", "lieu", "tim", "kiem", "tra", "cuu",
        "của", "trong", "như", "thế", "nào", "theo", "tài", "liệu",
        "là", "có", "và", "các", "những", "một", "để", "với", "cho", "từ",
        "này", "đó", "khi", "đã", "đang", "sẽ", "không", "được", "về", "hay",
        "hoặc", "gì", "ở", "tại", "bạn", "mình", "tôi", "hãy", "giúp",
        "la", "co", "va", "cac", "nhung", "mot", "de", "voi", "cho", "tu",
        "nay", "do", "khi", "da", "dang", "se", "khong", "duoc", "ve", "gi",
    }
    sw = default_sw | set(stop_words or ())
    q_words = [
        w for w in re.findall(r"[a-zA-Z0-9_\u00C0-\u1EF9]{2,}", raw_prompt)
        if w not in sw
    ]
    if not q_words:
        return []

    # 1. Synchronize and query SQLite FTS5 BM25
    fts_scores: dict[str, float] = {}
    if q_words and sync_fts5_chunks(chunks):
        db_path = _rag_db_path()
        try:
            conn = sqlite3.connect(str(db_path), timeout=2.0)
            try:
                safe_terms = [re.sub(r"[^a-zA-Z0-9_\u00C0-\u1EF9]", "", w) for w in q_words[:10]]
                safe_terms = [ f'"{t}"*' for t in safe_terms if len(t) >= 2 ]
                if safe_terms:
                    fts_query = " OR ".join(safe_terms)
                    rows = conn.execute(
                        "SELECT source, bm25(rag_fts, 1.8, 2.5, 1.0) AS rank "
                        "FROM rag_fts WHERE rag_fts MATCH ? ORDER BY rank LIMIT 40",
                        (fts_query,),
                    ).fetchall()
                    for src, rank in rows:
                        # bm25() in SQLite FTS5 returns negative values where more negative is better
                        fts_scores[str(src)] = max(0.5, -float(rank or 0.0) * 2.2)
            finally:
                conn.close()
        except Exception:
            pass

    # 2. Compute IDF + semantic character trigram / subword similarity
    n_chunks = max(1, len(chunks))
    doc_freq = {}
    for w in set(q_words):
        doc_freq[w] = sum(
            1 for ch in chunks
            if w in (ch["heading"] + " " + ch["text"] + " " + ch["source"]).lower()
        )
    q_trigrams = _char_trigrams(" ".join(q_words) if q_words else raw_prompt)

    scored: list[tuple[float, dict]] = []
    for ch in chunks:
        src = ch["source"]
        h_low = ch["heading"].lower()
        t_low = ch["text"].lower()
        s_low = src.lower()
        hay = f"{h_low} {t_low} {s_low}"

        score = float(fts_scores.get(src, 0.0))
        exact_hits = 0
        stem_hits = 0
        for w in set(q_words):
            if w in hay:
                exact_hits += 1
                idf = math.log(1.0 + (n_chunks - doc_freq.get(w, 0) + 0.5) / (doc_freq.get(w, 0) + 0.5))
                weight = 2.5 if w in h_low else (1.8 if w in s_low else 1.0)
                score += max(0.5, idf) * weight
            elif len(w) >= 4:
                # Prefix / stem semantic match
                stem = w[:max(4, len(w) - 2)]
                if stem in hay:
                    stem_hits += 1
                    score += 0.65

        if len(q_words) >= 2:
            phrase = " ".join(q_words[:3])
            if phrase in hay:
                score += 3.0

        if (exact_hits > 0 or fts_scores.get(src, 0.0) > 0 or stem_hits >= 2) and q_trigrams:
            c_trigrams = _char_trigrams(f"{h_low} {t_low[:160]}")
            if c_trigrams:
                overlap = len(q_trigrams & c_trigrams) / math.sqrt(len(q_trigrams) * len(c_trigrams))
                score += overlap * 1.5

        if score >= float(min_score):
            scored.append((score, ch))

    scored.sort(key=lambda item: (-item[0], item[1]["source"]))
    return scored

