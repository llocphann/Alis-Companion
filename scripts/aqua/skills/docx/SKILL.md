---
name: docx
description: "Comprehensive Word (.docx) document reading, creation, template editing, OOXML manipulation, and visual layout verification for Companion on Linux."
category: productivity
version: 1.0.0
triggers: ["docx", ".docx", "word document", "file word", "biểu mẫu", "bieu mau"]
---

# DOCX Document Skill (`docx`)

Use this skill whenever the user wants to read, summarize, extract tables from, create, fill out, or edit Microsoft Word (`.docx`) documents or Vietnamese/English form templates (e.g., `TTSP_BIEUMAU.docx`, reports, contracts, forms).

## Workflow Overview

| Task | Recommended Approach |
| :--- | :--- |
| **Read / Summarize / Extract Tables** | `code_tools.read_file(path)` (built-in OOXML `word/document.xml` parser preserving document order + Markdown tables) or `pandoc -f docx -t markdown` |
| **Create New `.docx` from Markdown/Text** | `code_tools.convert_or_create_document(source, output.docx, title=...)` or `pandoc input.md -o output.docx` |
| **Fill / Edit Existing `.docx` Template** | Direct OOXML (`zipfile` + `word/document.xml`) edit preserving `word/styles.xml`, `w:pPr`, `w:rPr`, and `w:tblPr` |
| **Visual Layout Verification** | `.docx` → `.pdf` (`libreoffice --headless --convert-to pdf`) → `.png` (`pdftoppm -png -r 150`) for Vision inspection |

---

## 1. Reading & Extracting `.docx` Content

### A. Built-in Companion Reader (Fastest & Zero External Dependencies)
Always start with `code_tools.read_file(file_path)`:
- Parses `word/document.xml` directly using Python's `zipfile` + `xml.etree.ElementTree`.
- Preserves exact document body order of headings (`#`, `##`, `###`), paragraphs (`<w:p>`), and tables (`<w:tbl>`).
- Renders every `<w:tbl>` as a clean Markdown table (`| col1 | col2 |` with `| --- | --- |` header separator).

### B. CLI & Metadata Inspection
When you need raw Markdown conversion, comments, or tracked changes:
```bash
# Convert .docx to GitHub-flavored Markdown (including tables)
pandoc --track-changes=all -f docx -t gfm input.docx

# Plain text fallback via LibreOffice
libreoffice --headless --convert-to txt:Text input.docx --outdir /tmp
```

### C. Deep OOXML Inspection (`word/document.xml`)
A `.docx` file is a ZIP archive containing XML files:
- `word/document.xml`: Main document body (`w:body` -> `w:p` paragraphs and `w:tbl` tables).
- `word/styles.xml`: Paragraph, character, and table style definitions.
- `word/header*.xml`, `word/footer*.xml`: Headers and footers.
- `word/comments.xml`: Review comments.
- `docProps/core.xml`: Title, creator, and modification timestamps.

---

## 2. Creating & Editing `.docx` Documents

### A. Creating a New `.docx` Document
1. **Via Companion Helper (`code_tools.convert_or_create_document`)**:
   - Accepts Markdown text or a `.md`/`.txt` file path and writes a well-structured `.docx` (using `pandoc` when available, or Companion's native OOXML `.docx` ZIP builder with full heading, paragraph, and table support).
2. **Via `pandoc` with Reference Styling**:
   ```bash
   pandoc input.md -f markdown -t docx -o output.docx
   # Or apply an existing template's styles:
   pandoc input.md --reference-doc=template.docx -o output.docx
   ```

### B. Editing an Existing Template (e.g., `TTSP_BIEUMAU.docx` / Forms)
**CRITICAL RULE**: Never overwrite an existing form template using `pandoc` if the user wants to preserve the original form layout, logos, borders, headers, or Vietnamese typography (`Times New Roman` / `Arial`). Edit `word/document.xml` **in-place** inside the `.docx` archive:

1. **Unpack & Parse `word/document.xml`**:
   - Read all entries from the source `.docx` using `zipfile.ZipFile`.
   - Parse `word/document.xml` with `xml.etree.ElementTree` (registering `w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"`).
2. **Preserve Formatting (`w:pPr`, `w:rPr`, `w:tblPr`, `w:tcPr`)**:
   - When updating a paragraph's text or filling a dotted/blank form line (`....` / `____`), modify the `<w:t>` text inside the existing `<w:r>` run while keeping `<w:pPr>` and `<w:rPr>` intact.
   - Note that Word often splits a single visible phrase across multiple `<w:r><w:t>` elements. Concatenate `<w:t>` texts at the `<w:p>` level to locate target fields, then put the updated text in the first `<w:r><w:t>` and clear the remaining `<w:t>` nodes in that span.
   - Always set `xml:space="preserve"` (`{http://www.w3.org/XML/1998/namespace}space="preserve"`) on `<w:t>` elements that have leading or trailing spaces.
3. **Filling Tables (`<w:tbl>`)**:
   - Locate target rows (`<w:tr>`) and cells (`<w:tc>`).
   - Ensure every `<w:tc>` contains at least one `<w:p>` element (OOXML requires `<w:p>` inside `<w:tc>`).
4. **Repack the `.docx` Archive**:
   - Write all unmodified ZIP entries verbatim and replace `word/document.xml` with the updated XML bytes using `zipfile.ZIP_DEFLATED`.

---

## 3. Visual Verification Workflow (`.docx` → `.pdf` → `.png`)

After generating or editing a `.docx` document with complex tables or form layouts, verify the visual rendering:

```bash
# Step 1: Convert .docx to .pdf via headless LibreOffice
libreoffice --headless --convert-to pdf output.docx --outdir /tmp/docx_preview

# Step 2: Render PDF pages to PNG images for visual inspection
pdftoppm -png -r 150 /tmp/docx_preview/output.pdf /tmp/docx_preview/page
# (Alternative: pdfimages -png /tmp/docx_preview/output.pdf /tmp/docx_preview/img)
```

Inspect the rendered `/tmp/docx_preview/page-1.png` image to confirm:
- Table columns do not overflow page margins.
- Vietnamese diacritics and fonts render cleanly without clipping.
- Form fields, checkboxes, and signature blocks align properly.
