# Namespace: [PDF-MD] (PDF to Markdown)

## 1. Overview

Turns a PDF path or a directory of PDFs into Markdown, a metadata JSON file, and extracted images. marker-pdf does the layout extraction.

## 2. Component Logic

### [PDF-MD-QUEUE] Queue input PDFs
- **Intent:** Build the list of PDF files to convert.
- **Logic:**
  1. If the path is a directory, collect every `*.pdf` in it.
  2. If the path is a PDF file, use that file alone.
  3. Exit with an error when the path is missing or not a PDF.

### [PDF-MD-CONV] Convert one PDF
- **Intent:** Write Markdown, metadata, and images for one PDF.
- **Logic:**
  1. Load marker-pdf models and convert the PDF.
  2. Honor `--max-pages` and `--start-page` when both are set; otherwise convert the whole file.
  3. Write `<stem>.md`, `<stem>_metadata.json`, and `images/` under the output directory.

### [PDF-MD-RUN] Command entry
- **Intent:** Convert each queued PDF, then hand the Markdown directory to EPUB packaging unless `--skip-epub` is set.
- **Logic:**
  1. Parse CLI arguments and queue PDFs.
  2. Convert each PDF unless `--skip-md` is set.
  3. Call EPUB conversion unless `--skip-epub` is set. `--non-interactive` accepts default metadata and skips the Markdown review prompt.
  4. Exit non-zero if any PDF failed.

## 3. Verification

### [PDF-MD-QUEUE]
```bash
. .venv/bin/activate && python -c "from pathlib import Path; import modules.pdf2md as p; q=p.add_pdfs_to_queue(Path('data/dokumen.pub_kahramanlar.pdf')); assert len(q)==1 and q[0].name=='dokumen.pub_kahramanlar.pdf'; print('PASS')"
```
Expected: `PASS`.
