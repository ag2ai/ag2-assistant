---
name: pdf-tools
description: Use when the user wants to read, summarise, extract text/tables from, split, merge, or fill a PDF. Explains when to read a PDF visually vs. extract its text, and how to manipulate PDFs via code execution.
version: "1.0"
license: Apache-2.0
---

# Working with PDFs

## Reading a PDF

- To **understand or summarise** a PDF the user points you at, use the
  `read_file` tool with its path. AG2 Assistant hands PDFs to the model as visual
  content, so this works even for **scanned PDFs with no text layer** (forms,
  signed documents, image scans). Ask permission the first time if prompted.
- For a **born-digital PDF where you need the exact text** (to quote precisely,
  count, or post-process), extract the text via code execution instead (below).

## Manipulating a PDF (code execution)

Use the code-execution tool. Prefer `pypdf` (pure-Python, no system deps); fall
back to `pdfplumber` for tables. Install on first use if missing
(`pip install pypdf`).

- **Extract text:**
  ```python
  from pypdf import PdfReader
  r = PdfReader("in.pdf")
  print("\n".join(page.extract_text() or "" for page in r.pages))
  ```
- **Split / select pages:**
  ```python
  from pypdf import PdfReader, PdfWriter
  r = PdfReader("in.pdf"); w = PdfWriter()
  for i in (0, 1, 2):      # first three pages
      w.add_page(r.pages[i])
  with open("out.pdf", "wb") as f: w.write(f)
  ```
- **Merge:** add pages from several `PdfReader`s into one `PdfWriter`.
- **Tables:** `pdfplumber` → `page.extract_tables()` is more reliable than plain
  text extraction for tabular data.

## Tips

- If text extraction returns empty strings, the PDF is scanned/image-only — read
  it visually with `read_file` instead.
- Always tell the user where you wrote any output file.
- Don't fabricate content you couldn't extract — say what failed and ask how to
  proceed.
