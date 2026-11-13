---
name: pdf
description: "Comprehensive PDF (.pdf) text/table layout extraction, image rendering, document creation, conversion, and page manipulation for Companion on Linux."
category: productivity
version: 1.0.0
triggers: ["pdf", ".pdf", "file pdf", "pdftotext", "extract pdf"]
---

# PDF Document Skill (`pdf`)

Use this skill whenever the user wants to read, summarize, extract tables or images from, create, convert, merge, or inspect `.pdf` documents.

## Workflow Overview

| Task | Recommended Approach |
| :--- | :--- |
| **Read Text & Preserve Table Layout** | `code_tools.read_file(path)` or `pdftotext -layout input.pdf -` |
| **Inspect Metadata & Page Count** | `pdfinfo input.pdf` |
| **Extract Embedded Images** | `pdfimages -png input.pdf /tmp/img_prefix` |
| **Render Pages for Vision / OCR** | `pdftoppm -png -r 150 input.pdf /tmp/page` |
| **Create / Convert to `.pdf`** | `code_tools.convert_or_create_document(source, output.pdf, title=...)` or `libreoffice --headless --convert-to pdf` |
| **Merge / Split PDF Pages** | `pdfunite`, `pdfseparate`, `qpdf`, or Python `pypdf` |

---

## 1. Reading & Extracting Text, Layout, and Images from `.pdf`

### A. Built-in Companion Reader (`code_tools.read_file`)
Always start with `code_tools.read_file(file_path)`:
- Runs `pdftotext -layout <path> -` automatically when available so multi-column text, financial tables, and aligned form fields stay intact.
- Automatically falls back to Python libraries (`pypdf` / `fitz` / `pdfplumber`) or pure-Python PDF stream decompression if `pdftotext` is unavailable.

### B. Poppler CLI Tools (`pdftotext`, `pdfinfo`, `pdfimages`, `pdftoppm`)
```bash
# Extract text preserving spatial/column/table layout
pdftotext -layout input.pdf -

# Extract specific page range (e.g., pages 1 to 5)
pdftotext -f 1 -l 5 -layout input.pdf -

# Inspect page count, dimensions, author, and encryption status
pdfinfo input.pdf

# Extract all embedded raster images as PNGs
pdfimages -png input.pdf /tmp/extracted_img

# Render pages 1-3 as 150 DPI PNGs for Vision inspection or scanned PDFs
pdftoppm -png -r 150 -f 1 -l 3 input.pdf /tmp/pdf_page
```

### C. Python PDF Libraries (When Installed)
- **`pdfplumber`**: Best for structured table extraction (`page.extract_tables()`) and bounding-box coordinates.
- **`pypdf` / `fitz` (PyMuPDF)**: Fast page-by-page text extraction, bookmarks/outline inspection, and form field (`AcroForm`) reading.

---

## 2. Creating & Converting Documents to `.pdf`

### A. Via Companion Helper (`code_tools.convert_or_create_document`)
Call `code_tools.convert_or_create_document(source_path_or_content, output_path, title=...)`:
- Converts Markdown, plain text, HTML, or `.docx` files into `.pdf`.
- Uses `libreoffice --headless --convert-to pdf` or `pandoc` when installed, and includes a built-in pure-Python PDF 1.4 generator fallback so valid, readable `.pdf` files can always be created even in minimal environments.

### B. Via Markdown / DOCX + LibreOffice Headless
When creating a styled report or converting an existing `.docx` / `.html` document:
```bash
# Convert Markdown -> DOCX -> PDF (clean typography without needing a multi-GB TeX install)
pandoc input.md -o /tmp/intermediate.docx
libreoffice --headless --convert-to pdf /tmp/intermediate.docx --outdir /path/to/output_dir

# Convert .docx or .html directly to .pdf
libreoffice --headless --convert-to pdf document.docx --outdir /path/to/output_dir
```

### C. Merging & Splitting PDFs
```bash
# Merge multiple PDFs into one
pdfunite part1.pdf part2.pdf merged.pdf

# Split a PDF into single-page files
pdfseparate input.pdf /tmp/page-%d.pdf
```

---

## 3. Visual Verification Workflow

Whenever you generate or modify a `.pdf` file, verify both its text layer and visual layout:
1. Run `code_tools.read_file(output_pdf)` (or `pdftotext -layout output.pdf -`) to confirm the text layer is selectable and properly ordered.
2. Render the first page with `pdftoppm -png -r 150 -f 1 -l 1 output.pdf /tmp/verify_page` to check page breaks, margins, and table formatting.
