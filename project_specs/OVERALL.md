# PDF2EPUB

Converts a PDF to Markdown with marker-pdf, then packages that Markdown as an EPUB.

## Flow

1. **PDF-MD** queues PDFs and converts each one to Markdown, images, and metadata.
2. **MD-EPUB** collects metadata and turns each Markdown file into an EPUB chapter.
3. **XHT** checks every XHTML (and the package XML) before it is stored. Syntax errors are repaired in place. Anything still invalid is reported with file, line, column, and message, and the EPUB is not written.

## Namespaces

- `pdf_md` — PDF to Markdown
- `md_epub` — Markdown to EPUB packaging
- `xhtml_gate` — XHTML syntax detection and repair
