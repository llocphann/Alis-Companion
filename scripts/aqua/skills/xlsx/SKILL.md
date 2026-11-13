---
name: xlsx
description: "Comprehensive Excel (.xlsx, .csv, .tsv) spreadsheet reading, formula extraction, zero-formula-error workbook creation, and LibreOffice recalculation verification for Companion on Linux."
category: productivity
version: 1.0.0
triggers: ["xlsx", ".xlsx", "excel", "spreadsheet", "bảng tính", "file excel", "csv to xlsx"]
---

# XLSX Spreadsheet Skill (`xlsx`)

Use this skill whenever the user wants to read, analyze, summarize, extract tables/formulas from, create, convert, or edit Excel (`.xlsx`) workbooks or `.csv` / `.tsv` spreadsheets.

## Workflow Overview

| Task | Recommended Approach |
| :--- | :--- |
| **Read / Inspect Sheets, Values & Formulas** | `code_tools.read_file(path)` (built-in OOXML parser for `xl/workbook.xml`, `xl/sharedStrings.xml`, and `xl/worksheets/sheet*.xml` rendering `### Sheet: <name>` Markdown tables + `<f>` formulas) |
| **Create `.xlsx` from Markdown Table / CSV** | `code_tools.convert_or_create_document(source, output.xlsx, title=...)` (builds valid OOXML `.xlsx` with numeric `<v>`, shared strings, and `=...` `<f>` formulas) |
| **Recalculate & Verify Formulas** | `libreoffice --headless --convert-to pdf` / `code_tools.read_file(output.xlsx)` (check `formulaErrors == []`) |
| **Visual Layout Verification** | `libreoffice --headless --convert-to pdf output.xlsx` → `pdftoppm -png -r 150` |

---

## 1. Reading & Analyzing Spreadsheets (`.xlsx`, `.csv`, `.tsv`)

### A. Built-in Zero-Dependency OOXML Reader (`code_tools.read_file`)
Always start with `code_tools.read_file(file_path)`:
- Parses `.xlsx` archives directly using Python's built-in `zipfile` + `xml.etree.ElementTree` (`xl/workbook.xml`, `xl/_rels/workbook.xml.rels`, `xl/sharedStrings.xml`, and `xl/worksheets/sheet*.xml`) with **0 external pip dependencies**.
- Extracts real worksheet names from `<sheets><sheet name="..."/></sheets>` in `xl/workbook.xml`.
- Resolves shared strings (`t="s"`), inline strings (`t="inlineStr"`), booleans (`t="b"`), string formula results (`t="str"`), errors (`t="e"`), numeric values (`<v>`), and cell formulas (`<f>`).
- Renders each worksheet as a Markdown section:
  ```markdown
  ### Sheet: Summary
  | Item | Q1 | Q2 | Total |
  | --- | --- | --- | --- |
  | Revenue | 120 | 150 | 270 (=SUM(B2:C2)) |
  ```
- Reports `sheets`, `tables`, `markdownTables`, `formulas`, and any `formulaErrors` in `metadata`.

### B. Inspecting `.csv` and `.tsv` Files
- `code_tools.read_file(path)` automatically parses `.csv` and `.tsv` files and converts them into `.xlsx` via `code_tools.convert_or_create_document(csv_path, xlsx_path)`.

---

## 2. Creating & Editing `.xlsx` Workbooks

### A. Core Spreadsheet Quality Rules
1. **Zero-Formula-Error Rule (Mandatory)**:
   - Every delivered `.xlsx` workbook must contain **zero (0)** formula error values:
     - `#REF!` (invalid cell reference)
     - `#DIV/0!` (division by zero — guard with `=IF(B2=0, 0, A2/B2)` or `=IFERROR(..., 0)`)
     - `#VALUE!` (wrong argument type)
     - `#NAME?` (misspelled function name)
     - `#N/A` (lookup value missing — guard with `=IFERROR(INDEX(...), "")`)
     - `#NUM!` (invalid numeric calculation)
2. **Use Dynamic Excel Formulas Instead of Hardcoding Calculated Values**:
   - Never hardcode sums, averages, percentages, or cross-row totals when a formula can compute them dynamically.
   - Use standard Excel functions (without leading `=` inside nested calls, and English uppercase names in OOXML `<f>` tags):
     - Totals & Subtotals: `=SUM(B2:B10)`, `=SUMIFS(D2:D100, B2:B100, "Active")`
     - Averages & Counts: `=AVERAGE(C2:C10)`, `=COUNTA(A2:A50)`, `=COUNTIF(E2:E50, ">0")`
     - Lookups: `=INDEX(C2:C50, MATCH(G2, A2:A50, 0))`
     - Safe Ratios: `=IFERROR(C2/B2, 0)`
3. **OOXML `<f>` Formula Syntax**:
   - In `xl/worksheets/sheet*.xml`, the `<f>` element stores the formula **without** the leading `=` sign (e.g., `<f>SUM(B2:B5)</f>`), while `<v>` stores the cached value if known.
   - When using `code_tools.convert_or_create_document`, cells starting with `=` (such as `=SUM(B2:B3)`) are automatically written as `<f>SUM(B2:B3)</f>` with a pre-evaluated `<v>` cache for basic `SUM`/`AVERAGE`/`MIN`/`MAX` ranges!

### B. Creating `.xlsx` Workbooks via Companion Helper
Call `code_tools.convert_or_create_document(source_path_or_content, output_path, title="Sheet1")`:
- Accepts a Markdown table, CSV/TSV text, or path to a `.md` / `.csv` / `.tsv` file.
- Builds a complete, standards-compliant OOXML `.xlsx` archive containing:
  - `[Content_Types].xml`
  - `_rels/.rels`
  - `docProps/core.xml`
  - `xl/workbook.xml`
  - `xl/_rels/workbook.xml.rels`
  - `xl/styles.xml`
  - `xl/sharedStrings.xml`
  - `xl/worksheets/sheet1.xml`

---

## 3. Recalculation & Verification Workflow on Linux

After creating or editing an `.xlsx` workbook:
1. **Verify Structure, Formulas & Zero Errors**:
   - Run `code_tools.read_file(output_xlsx)` and confirm:
     - All expected sheets and rows render cleanly in `markdownTables`.
     - Formulas appear in `metadata["formulas"]` and `metadata["formulaErrorCount"] == "0"`.
2. **Headless LibreOffice Recalculation & Visual Check**:
   ```bash
   # Convert .xlsx to .pdf via LibreOffice headless (recalculates all formulas on load)
   libreoffice --headless --convert-to pdf output.xlsx --outdir /tmp/xlsx_preview

   # Inspect rendered text/values from recalculated PDF
   pdftotext -layout /tmp/xlsx_preview/output.pdf -

   # Render page image for visual column-width / truncation check
   pdftoppm -png -r 150 /tmp/xlsx_preview/output.pdf /tmp/xlsx_preview/page
   ```
