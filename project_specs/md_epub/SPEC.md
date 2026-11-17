# Namespace: [MD-EPUB] (Markdown to EPUB)

## 1. Overview

Packages a directory of Markdown files and images into an EPUB 3 zip. Decorative/tiny image artifacts and images inside `İçindekiler` are stripped before rendering; `İçindekiler` entries are aligned to body chapters via monotonic dynamic programming with bidirectional title reconciliation across EPUB navigation metadata, in-book `İçindekiler`, and body headings; `manzum` (numbered-verse) sections have periodic margin counters extracted and sequence-repaired into a dedicated column; and each top-level chapter is split into its own gated XHTML spine file so chapters always start on a new page.

## 2. Component Logic

### [MD-EPUB-META] Collect EPUB metadata
- **Intent:** Fill Dublin Core metadata from an existing `description.json`, inferred headings, or prompts.
- **Logic:**
  1. Load `description.json` when it exists, inferring placeholder title, creator, and Turkish language tag in non-interactive mode.
  2. In interactive mode, prompt for title, author, identifier, language, rights, publisher, and date.
  3. In non-interactive mode, keep the resolved defaults without reading stdin.
  4. Write the chosen metadata back to `description.json`.

### [MD-EPUB-REVIEW] Review markdown
- **Intent:** Let a person edit the Markdown before packaging, or skip that prompt.
- **Logic:**
  1. Read the Markdown file.
  2. In non-interactive mode, return the text immediately.
  3. In interactive mode, offer to open the file and re-read it after editing.

### [MD-EPUB-IMG] Filter decorative and TOC images
- **Intent:** Exclude small/line-divider image artifacts and images inside `İçindekiler` from rendered chapters and the EPUB archive.
- **Logic:**
  1. Resolve each Markdown `![alt](path)` reference case-insensitively against `work_dir/images`.
  2. Measure pixel dimensions with Pillow; remove the Markdown image reference when `width < 200`, `height < 200`, `width * height < 100_000`, or the image is a near-blank page scan (`< 5,000` dark pixels covering `< 0.5%` of pixel area).
  3. Rewrite surviving image references to `images/<actual_name>` and return the referenced image filenames.
  4. Strip any `![alt](...)` lines inside the `İçindekiler` section range so `İçindekiler` contains no clickable/zoomable images.
- **Constraint:** Unreferenced or dimension-filtered images must not be copied into `OPS/images/` or listed in `package.opf`.

### [MD-EPUB-TOC] Align TOC and reconcile chapter titles
- **Intent:** Link every `İçindekiler` entry to its body heading and reconcile OCR-damaged chapter titles bidirectionally between metadata (`TOC.xhtml`/`toc.ncx`), in-book `İçindekiler`, and body headings.
- **Logic:**
  1. Locate `# İçindekiler` and end the TOC region before the first heading whose next non-blank, non-image, non-rule, non-heading line is body prose rather than a TOC table or page-pair line.
  2. Parse TOC rows, merging wrapped multi-row table continuations and detaching glued Roman or Arabic page numbers from titles while preserving hierarchical TOC levels (e.g. `ODYSSEİA` / `EKLER` / part headers above child chapters).
  3. Build candidate body chapter targets after `İçindekiler`, pairing consecutive `# <Roman>. Bölüm` + `## <Subtitle>` headings as a composite target and ignoring mid-chapter running headers and summary subheadings.
  4. Align TOC entries monotonically to body targets via dynamic programming scored by word-set and fuzzy character similarity so a noisy or reordered TOC row cannot skip subsequent chapters.
  5. Reconcile each matched `(TOC title, body heading)` pair token-by-token and Roman-prefix-by-sequence, choosing clean alphabetic tokens over OCR-corrupted tokens (`*`, `/`, stray digits, broken Roman numerals, `rn`/`nn`/`lan` confusions) and writing the canonical title back to the `TocEntry`, the in-book `İçindekiler` link, and the body heading(s).

### [MD-EPUB-VERSE] Detect manzum chapters and format sequence-verified line numbers
- **Intent:** Detect poem/`manzum` sections, infer their margin line-number frequency, and extract/repair every expected counter into a dedicated margin column.
- **Logic:**
  1. Inspect each chapter section for ascending runs (`>= 5` occurrences) of periodic counters; leave prose chapters untouched.
  2. Infer the counter frequency `step` from the mode of positive differences between clean counters (default `5`).
  3. Strip inline `<sup>`/`</sup>` wrappers around counter digits and scan forward from `prev` (starting at `0`) for `expected = prev + step` before `expected + 2 * step`, matching:
     - exact, whitespace-split, or word-glued digits (`1 1 5` -> `115`, `175İlk` -> `175` + `İlk`),
     - OCR character confusions (`0<->O/o`, `1<->l/I/ı`, `5<->S`, e.g. `5 O` -> `50`, `l 7 5` -> `275`),
     - margin-clipped leading hundreds digits at line/list starts (`- 65` after `160` -> `165`).
  4. When `expected + step` appears with `expected` omitted at a page break, place `expected` at the midpoint paragraph/clause boundary between `prev` and `expected + step`.
  5. Render each verse unit as `<p class="verse"><span class="verse-ln">...</span><span class="verse-body">...</span></p>`.

### [MD-EPUB-CHAP] Render a chapter
- **Intent:** Turn one Markdown chapter or spine slice into a valid XHTML document with MathML and styling.
- **Logic:**
  1. Filter decorative images (`[MD-EPUB-IMG]`), normalize section subheadings, format verse line numbers when `manzum` (`[MD-EPUB-VERSE]`), and link/reconcile `İçindekiler` when present (`[MD-EPUB-TOC]`).
  2. Render Markdown with `codehilite`, `tables`, `fenced_code`, `footnotes`, and `attr_list`.
  3. Convert LaTeX `$...$` and `$$...$$` to MathML without altering fenced code blocks.
  4. Wrap the HTML body in an XHTML 1.1 / EPUB 3 document declaring the EPUB and MathML namespaces.

### [MD-EPUB-SPLIT] Split Markdown into per-chapter spine documents
- **Intent:** Partition a Markdown book into separate XHTML spine segments so every chapter begins at the top of a new page and links resolve across spine files.
- **Logic:**
  1. After image filtering and TOC/heading reconciliation, identify split points at `# İçindekiler` and every anchored top-level chapter heading (keeping paired `# <Roman>. Bölüm` + `## <Subtitle>` headings together at the top of the same spine segment).
  2. Assign each segment its deterministic spine filename `s{idx:05d}-{stem}.xhtml` and rewrite in-book `(#ch-NNN)` links and `TocEntry.href` values to `{target_doc}#ch-NNN`.
  3. Render each segment into its own XHTML document via `[MD-EPUB-CHAP]`.

### [MD-EPUB-PACK] Write the EPUB zip
- **Intent:** Assemble package documents, per-chapter XHTML spine files, surviving content images, and CSS into one `.epub`.
- **Logic:**
  1. Produce spine XHTML segments and navigation `TocEntry` items for each source Markdown file via `[MD-EPUB-SPLIT]`.
  2. Run every spine XHTML document, `titlepage.xhtml`, and `TOC.xhtml` through the XHTML gate, and check `package.opf`, `toc.ncx`, and `container.xml` as strict XML.
  3. Store `mimetype` uncompressed first, followed by gated XML/XHTML documents, referenced content images only, and CSS.
  4. Abort before creating the `.epub` archive if any XML/XHTML check fails.

## 3. Verification

### [MD-EPUB-IMG]
```bash
. .venv/bin/activate && python -c "import tempfile; from pathlib import Path; from PIL import Image; from modules.mark2epub import process_markdown_for_images; d=Path(tempfile.mkdtemp()); (d/'images').mkdir(); Image.new('RGB',(1280,52)).save(d/'images/bar.jpeg'); Image.new('RGB',(600,400)).save(d/'images/map.jpeg'); md,imgs=process_markdown_for_images('![](bar.jpeg)\n\n![](map.jpeg)\n', d); assert 'bar.jpeg' not in md and imgs==['map.jpeg']; print('PASS')"
```
Expected: `PASS`.

### [MD-EPUB-TOC]
```bash
. .venv/bin/activate && python -c "from pathlib import Path; from modules.contents_toc import link_contents; md=Path('data/Homeros Odysseia I\u0307s\u0327 Bankas\u0131 Yay\u0131nlar\u0131/Homeros Odysseia I\u0307s\u0327 Bankas\u0131 Yay\u0131nlar\u0131.md').read_text(); out,entries=link_contents(md); anchored=[e for e in entries if e.anchor]; assert len(entries)>=30 and len(anchored)==len(entries), (len(anchored), len(entries)); toc_slice=out.split('{: #icindekiler}',1)[1].split('{: #ch-001}',1)[0]; assert '![' not in toc_slice; print('PASS')"
```
Expected: `PASS`.

### [MD-EPUB-VERSE]
```bash
. .venv/bin/activate && python -c "from modules.verse_lineno import format_verse_line_numbers; s='# I. Bölüm\n\n## S\n\na, 5 b. 10 c, 15 d, 20 e.\n- 25 f\n- 3 O g\n- <sup>3</sup> <sup>5</sup>İlk h\n'; out=format_verse_line_numbers(s); assert all(f'>{n}<' in out for n in (5,10,15,20,25,30,35)) and '>İlk h<' in out; prose='# Önsöz\n\nBu 5 ve 10 sayılı metin düz yazıdır.\n'; assert format_verse_line_numbers(prose)==prose; print('PASS')"
```
Expected: `PASS`.

### [MD-EPUB-CHAP]
```bash
. .venv/bin/activate && python -c "from modules.mark2epub import get_chapter_XML; from modules.xhtml_gate import find_xhtml_errors; x,imgs,_toc=get_chapter_XML('.','nope.md',['style.css'], content='# T\n\nTom & Jerry\n'); assert find_xhtml_errors(x)==[] and 'Tom' in x and imgs==[]; print('PASS')"
```
Expected: `PASS`.

### [MD-EPUB-SPLIT]
```bash
. .venv/bin/activate && python -c "from modules.mark2epub import render_markdown_spines; spines,imgs,toc=render_markdown_spines('.','book.md',['style.css'], content='# İçindekiler\n\n| Bir | 1 |\n| İki | 5 |\n\n# Bir\n\nMetin A.\n\n# İki\n\nMetin B.\n'); assert len(spines)>=3 and all(e.href and '#' in e.href for e in toc); print('PASS')"
```
Expected: `PASS`.
