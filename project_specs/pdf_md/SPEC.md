# Namespace: [PDF-MD] (PDF to Markdown)

## 1. Overview

Turns a PDF path or a directory of PDFs into sanitized, structurally normalized Markdown, a metadata JSON file, and extracted images. marker-pdf does the layout extraction, ClearScan glyph corruption is repaired against rendered PDF line crops, marker-pdf inline HTML/entity artifacts are sanitized, and bottom-of-page footnotes and page-break prose splits are normalized before saving `<stem>.md`.

## 2. Component Logic

### [PDF-MD-QUEUE] Queue input PDFs
- **Intent:** Build the list of PDF files to convert.
- **Logic:**
  1. If the path is a directory, collect every `*.pdf` in it.
  2. If the path is a PDF file, use that file alone.
  3. Exit with an error when the path is missing or not a PDF.

### [PDF-MD-CLEAN] Sanitize marker-pdf inline HTML and split entities
- **Intent:** Repair split HTML entities, orphan inline tags, and malformed numeric character references emitted by `marker-pdf` before Markdown rendering.
- **Logic:**
  1. Mask fenced code blocks and inline code spans so literal code content is untouched.
  2. Reassemble HTML entities split across inline tags by `marker-pdf` (`<(tag)>(&#?[A-Za-z0-9]*)</\1>([A-Za-z0-9]+;)` and adjacent tag-split entities):
     - Convert line-leading `<sup>&#</sup>x27;` followed by whitespace into `<sup>*</sup>` (ClearScan-corrupted asterisk footnote marker).
     - Unwrap bogus `<sup>` wrappers around quote entities (`<sup>&</sup>quot;` $\to$ `"`, non-footnote `<sup>&#</sup>x27;` $\to$ `'`), while preserving any leading superscript digits/symbols (`<sup>1&</sup>quot;` $\to$ `<sup>1</sup>"`).
     - Rejoin any other split valid entity (`&amp;`, `&lt;`, `&gt;`, numeric/named refs) cleanly outside the tag boundary.
  3. Balance inline formatting tags (`sup`, `sub`, `i`, `b`, `em`, `strong`, `u`, `small`, `mark`) per paragraph block by dropping unmatched closing tags and closing unclosed opening tags at the end of the line.
  4. Escape any remaining `&#` not followed by `[0-9]+;` or `[xX][0-9a-fA-F]+;` as `&amp;#` so CPython `HTMLParser.goahead` never aborts mid-stream on malformed character references.

### [PDF-MD-FOOT] Extract bottom-of-page footnotes and rejoin page-break prose splits
- **Intent:** Lift interleaved bottom-of-page footnotes (including ClearScan unnumbered notes, editor/translator `*` notes, and multi-page continuations) into linked Markdown footnotes and rejoin prose paragraphs split across page boundaries without altering verse (`manzum`) sections.
- **Logic:**
  1. Mask fenced code blocks, skip PDF footnote zones already covered by existing `[^...]:` definitions and offset new `fn-N` IDs past any existing `fn-N` for strict idempotency, split internal `<sup>`-prefixed footnote lines out of multi-line prose blocks, and mark `manzum` (periodic-counter verse) sections so verse lines are never merged or misclassified as footnotes.
  2. When the source PDF is available, scan each page with `pypdfium2` (releasing page/textpage handles before `pdf.close()`), merge colinear horizontal rule segments in the lower page region, and extract the footnote zone below single-rule separators (marking rules wider than `55%` of page width as multi-page footnote continuations) along with each page's first and last body lines.
  3. Align non-empty PDF footnote zones monotonically to Markdown paragraph spans via dynamic programming over character `n`-gram candidates, and additionally detect standalone explicit footnote paragraphs (`^<sup>(\d{1,2}|[*†‡§•.\s]+|[ıIl])</sup>`) outside `manzum` sections when no PDF zone covers them.
  4. Reassemble multi-page footnote continuations (via continuation rule width or unfinished prior footnote sentence + unmarked continuation block, de-hyphenating wrapped words), treat unnumbered `-ed.n.` / `-ç.n.` editor/translator notes as `*` markers without advancing numeric sequence, infer missing per-page footnote sequence numbers (`1, 2, ...`), link matching inline callouts in the page's preceding prose (`<sup>k</sup>`, Unicode `¹²³`, `wordk`, `word. k`, OCR-corrupted `ı` for `1`, or `*`/`·` symbol markers without consuming Markdown `*italic*` delimiters) to `[^fn-N]` references (attaching unreferenced notes to the end of the preceding body paragraph), and emit `[^fn-N]: ...` definitions.
  5. Rejoin consecutive plain prose paragraphs split across lifted footnotes, verified PDF page turns lacking terminal punctuation, or explicit word-wrap hyphens (`word-\n\ncontinuation`), moving any hyphen-attached `[^fn-N]` callout past the rejoined word tail.
- **Constraint:** Must operate idempotently when re-run on already normalized Markdown (both with and without `pdf_path`) and must work in Markdown-only fallback mode when no PDF file is present.

### [PDF-MD-CONV] Convert one PDF
- **Intent:** Write Markdown, metadata, and images for one PDF.
- **Logic:**
  1. Load marker-pdf models and convert the PDF.
  2. Honor `--max-pages` and `--start-page` when both are set; otherwise convert the whole file.
  3. Run glyph repair, `[PDF-MD-CLEAN]`, and `[PDF-MD-FOOT]` (passing `input_path`) on `rendered.markdown`, then write `<stem>.md`, `<stem>_metadata.json`, and `images/` under the output directory.

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

### [PDF-MD-CLEAN]
```bash
. .venv/bin/activate && python -c "from modules.md_sanitize import sanitize_marker_markdown; s='<sup>&#</sup>x27; Elmasa benzer -ed.n.\n\n<sup>&</sup>quot;Diş sırası bende!\"\n\n<sup>&#</sup>x27;Düşen mezarı\n\n- 13. Foo</i> bar\n\nA &# B \x60<sup>&#</sup>x27;\x60 ve \x60\x60raw \x60 <sup>&#</sup>x27;\x60\x60\n'; out=sanitize_marker_markdown(s); assert '<sup>*</sup> Elmasa benzer' in out and '\"Diş sırası bende!\"' in out and \"'Düşen mezarı\" in out and '</i>' not in out and 'A &amp;# B' in out and '\x60<sup>&#</sup>x27;\x60' in out and '\x60\x60raw \x60 <sup>&#</sup>x27;\x60\x60' in out; print('PASS')"
```
Expected: `PASS`.

### [PDF-MD-FOOT]
```bash
. .venv/bin/activate && python -c "from modules.structure_norm import normalize_footnotes_and_breaks; s='*italik* ve veneri* terimi\n\n<sup>*</sup>Medikal bir terim. -ed.n.\n\nile şimdi yengeç\n\n<sup>1</sup>Yunan seramik sanatında Hidra tasviri.\n\nde sudan fırlayıp başladı. ı Sonra tavsi-\n\nye aldı.\n'; out=normalize_footnotes_and_breaks(s); assert '*italik* ve veneri[^fn-1] terimi ile şimdi yengeç de sudan fırlayıp başladı.[^fn-2] Sonra tavsiye aldı.' in out and '[^fn-1]: Medikal bir terim. -ed.n.' in out and '[^fn-2]: Yunan seramik sanatında Hidra tasviri.' in out and normalize_footnotes_and_breaks(out)==out, out; print('PASS')"
```
Expected: `PASS`.
