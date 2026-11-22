# Namespace: [MD-EPUB] (Markdown to EPUB)

## 1. Overview

Packages a directory of Markdown files and images into an EPUB 3 zip. Pre-render normalization repairs `marker-pdf` split entities and orphan inline tags, extracts interleaved bottom-of-page footnotes (using sibling/directory PDF geometry when present or Markdown markers otherwise) while rejoining page-break prose splits, strips decorative/tiny image artifacts and images inside `İçindekiler`, aligns `İçindekiler` entries to body chapters via monotonic dynamic programming with bidirectional title reconciliation, normalizes chapter and subsection heading hierarchy (`h1`/`h2`/`h3`) while demoting mis-tagged dialogue headings, formats `manzum` (numbered-verse) margin counters into a dedicated column, renders Markdown via an offset-safe HTML block extractor into per-chapter XHTML spine files with EPUB 3 semantic footnotes (`epub:type="noteref"` / `<aside epub:type="footnote">`), and enforces a post-render anti-duplication check plus XHTML syntax gate before writing the `.epub` archive.

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
    5. Reconcile each matched `(TOC title, body heading)` pair token-by-token and Roman-prefix-by-sequence, choosing clean alphabetic tokens over OCR-corrupted tokens (`*`, `/`, stray digits, broken Roman numerals, `rn`/`nn`/`lan` confusions) and writing the canonical title back to the `TocEntry`, the in-book `İçindekiler` link, and the body heading(s) while preserving any inline `[^fn-...]` footnote callout attached to the heading.

### [MD-EPUB-HEAD] Normalize chapter and subsection heading hierarchy
- **Intent:** Ensure consistent heading levels across chapters and peer subsections without level jumps, running-header interruptions, or dialogue/glossary lines mis-tagged as headings.
- **Logic:**
  1. Mask fenced code blocks and preserve pre-TOC frontmatter headings (prior to `{: #icindekiler}` or `{: #ch-001}` when TOC anchors exist).
  2. Normalize every TOC-anchored chapter heading (`{: #icindekiler}` or `{: #ch-\d+}`) to `#` (`<h1>`), keeping paired `# <Roman>. Bölüm {: #ch-\d+}` + `## <Subtitle>` intact, and tag any trailing unanchored `# <BookTitle>` back-cover heading near EOF (matching a pre-TOC frontmatter heading or document title slug) with `{: #arka-kapak}` so it splits onto its own final spine page.
  3. Within each anchored chapter section (bounded by `{: #(?:icindekiler|arka-kapak|ch-\d+)}` when anchors exist, or `# ` otherwise):
     - Drop unanchored running page headers whose normalized title duplicates the enclosing chapter heading (`SequenceMatcher >= 0.85`).
     - Demote unanchored quoted dialogue headings (`"..."` / `“...”` / `'...'` or quote-initial ending in `!?….,`) to plain paragraphs, and demote `\bBkz\.\s+\S` glossary cross-reference headings to `- ` list items when adjacent to list entries (or plain paragraphs otherwise).
     - Normalize all remaining unanchored internal subheadings uniformly to `##` (`<h2>`), using `###` (`<h3>`) only for child subheadings beneath back-to-back category + subcategory heading pairs.

### [MD-EPUB-FOOT] Render EPUB 3 semantic footnotes
- **Intent:** Convert rendered Markdown footnotes into accessible EPUB 3 `noteref` links and `<aside epub:type="footnote">` blocks with sequential `1..N` per-chapter numbering for tap-to-popup support and clean chapter-end typography.
- **Logic:**
  1. Normalize footnote and back-reference element IDs in rendered HTML to colon-free XML `NCName`s (`fn-...`, `fnref-...`) while preserving sequential `1..N` per-chapter numbering.
  2. Rewrite inline `<sup id="fnref-..."><a class="footnote-ref" href="#fn-...">N</a></sup>` elements into `<a class="footnote-ref" epub:type="noteref" role="doc-noteref" id="fnref-..." href="#fn-..."><sup>N</sup></a>` via targeted regex without re-parsing body HTML (preserving `<pre>`/`<code>` and `$...$` math).
  3. Isolate and parse only the `<div class="footnote">...</div>` block, rewriting it into `<section class="footnotes" epub:type="footnotes" role="doc-endnotes"><hr/>...</section>` and converting each `<li id="fn-...">` into `<aside class="footnote" epub:type="footnote" id="fn-...">` with a leading `<a class="footnote-num" href="#fnref-...">N.</a>` label and `↩` back-link.
  4. Style `.footnote-ref`, `section.footnotes`, and `aside.footnote` in the default stylesheet with smaller font size, compact margins, and a subtle horizontal divider rule.

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
- **Intent:** Turn one Markdown chapter or spine slice into a valid XHTML document with MathML, normalized headings, and EPUB 3 footnotes without HTML-extractor offset corruption.
- **Logic:**
  1. Sanitize `marker-pdf` split entities and unbalanced inline tags (`[PDF-MD-CLEAN]`), link/reconcile `İçindekiler` when present (`[MD-EPUB-TOC]`), filter decorative images (`[MD-EPUB-IMG]`), normalize chapter and subsection heading hierarchy while stripping mid-chapter running headers (`[MD-EPUB-HEAD]`), extract bottom-of-page footnotes and rejoin page-break prose splits (`[PDF-MD-FOOT]`, auto-discovering `<work_dir>/<stem>.pdf` or `<work_dir.parent>/<work_dir.name>.pdf` whenever `(work_dir / md_filename).is_file()` is true), and format verse line numbers when `manzum` (`[MD-EPUB-VERSE]`).
  2. Render Markdown with `codehilite`, `tables`, `fenced_code`, `footnotes`, and `attr_list` using an offset-safe `HtmlBlockPreprocessor` / `HTMLExtractor` subclass that validates `self.rawdata.startswith("</", start)` in `get_endtag_text` and resets `lineno_start_cache` whenever `HTMLParser.goahead` slices `self.rawdata` before EOF.
  3. Upgrade rendered footnote HTML to EPUB 3 semantic `<a epub:type="noteref">` and `<aside epub:type="footnote">` markup (`[MD-EPUB-FOOT]`), and convert LaTeX `$...$` and `$$...$$` to MathML without altering fenced code blocks.
  4. Wrap the HTML body in an XHTML 1.1 / EPUB 3 document declaring the EPUB and MathML namespaces.

### [MD-EPUB-SPLIT] Split Markdown into per-chapter spine documents
- **Intent:** Partition a Markdown book into separate XHTML spine segments so every chapter begins at the top of a new page and links and chapter footnotes resolve cleanly within their spine files.
- **Logic:**
  1. After Markdown sanitization, TOC/heading reconciliation, image filtering, heading hierarchy normalization, and footnote extraction, collect all `[^id]: ...` footnote definitions and strip them from the shared Markdown body before splitting at `#icindekiler`, `#arka-kapak`, and every `#ch-\d+` chapter heading (keeping paired `# <Roman>. Bölüm` + `## <Subtitle>` headings together at the top of the same spine segment).
  2. Assign each segment its deterministic spine filename `s{idx:05d}-{stem}.xhtml`, rewrite in-book `(#ch-NNN)` links and `TocEntry.href` values to `{target_doc}#ch-NNN`, and append only the footnote definitions referenced (`[^id]`) within that segment.
  3. Render each segment into its own XHTML document via `[MD-EPUB-CHAP]`.

### [MD-EPUB-DEDUP] Verify rendered spines have no prose duplication
- **Intent:** Reject rendered XHTML spines if Markdown-to-XHTML conversion introduces repeated prose paragraphs not present in the source Markdown.
- **Logic:**
  1. Normalize text by stripping `<a class="footnote-ref"...>...</a>` callout badges, stripping HTML tags, unescaping HTML entities, unwrapping Markdown link syntax `[label](url)` to `label`, stripping Markdown footnote callouts `[^...]` and emphasis/escape characters (`*_\\`), and collapsing whitespace.
  2. Extract every `<p>` element outside `.footnote` / `.footnotes` blocks across the rendered XHTML spines whose normalized text has length `>= 50`.
  3. For each normalized paragraph occurring `>= 2` times across the rendered spines, verify its count does not exceed `max(1, source_occurrences)` in the normalized pre-render Markdown segments; otherwise raise `ContentDuplicationError` naming the duplicated passage and counts.

### [MD-EPUB-PACK] Write the EPUB zip
- **Intent:** Assemble package documents, per-chapter XHTML spine files, surviving content images, and CSS into one `.epub`.
- **Logic:**
  1. Produce spine XHTML segments and navigation `TocEntry` items for each source Markdown file via `[MD-EPUB-SPLIT]` and verify zero newly introduced prose duplication via `[MD-EPUB-DEDUP]`.
  2. Run every spine XHTML document, `titlepage.xhtml`, and `TOC.xhtml` through the XHTML gate, and check `package.opf`, `toc.ncx`, and `container.xml` as strict XML.
  3. Store `mimetype` uncompressed first, followed by gated XML/XHTML documents, referenced content images only, and CSS.
  4. Abort before creating the `.epub` archive if content duplication or any XML/XHTML check fails.

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

### [MD-EPUB-HEAD]
```bash
. .venv/bin/activate && python -c "from modules.structure_norm import normalize_heading_hierarchy; s='# KAHRAMANLAR\n\n#### ODYSSEİA KURULUŞU {: #ch-001}\n\nMetin.\n\n## Odysseia Kurulusu\n\nDevam.\n\n# HERAKLES {: #ch-002}\n\n#### 1. NEMEA ASLANI\n\nA.\n\n# 3. KERYNEİA GEYİĞİ\n\nB.\n\n## 7. GİRİT BOĞASI\n\nC.\n\n## \"Bekle !\"\n\n- EROS Aşk.\n#### KADER TANRIÇALARI Bkz. Moiralar.\n- GAİA Toprak.\n\n# GÖRSELLER {: #ch-003}\n\n#### BİRİNCİ KISIM\n\n1. Resim\n\n# İKİNCİ KISIM\n\n2. Resim\n\n# KAHRAMANLAR\n\nArka kapak yazısı.\n'; out=normalize_heading_hierarchy(s); assert '# ODYSSEİA KURULUŞU {: #ch-001}' in out and '## Odysseia Kurulusu' not in out and '## 1. NEMEA ASLANI' in out and '## 3. KERYNEİA GEYİĞİ' in out and '## 7. GİRİT BOĞASI' in out and '\n\"Bekle !\"\n' in out and '- KADER TANRIÇALARI Bkz. Moiralar.' in out and '## BİRİNCİ KISIM' in out and '## İKİNCİ KISIM' in out and '# KAHRAMANLAR {: #arka-kapak}' in out, out; print('PASS')"
```
Expected: `PASS`.

### [MD-EPUB-FOOT]
```bash
. .venv/bin/activate && python -c "from modules.mark2epub import _markdown_slice_to_xhtml; from modules.xhtml_gate import find_xhtml_errors; x=_markdown_slice_to_xhtml('Metin[^fn-1] devam.\n\n[^fn-1]: Dipnot açıklaması.\n',['style.css']); assert find_xhtml_errors(x)==[] and 'epub:type=\"noteref\"' in x and '<aside class=\"footnote\" epub:type=\"footnote\"' in x and 'Dipnot açıklaması.' in x, x; print('PASS')"
```
Expected: `PASS`.

### [MD-EPUB-CHAP]
```bash
. .venv/bin/activate && python -c "from modules.mark2epub import get_chapter_XML, _markdown_slice_to_xhtml; from modules.xhtml_gate import find_xhtml_errors; x,imgs,_toc=get_chapter_XML('.','nope.md',['style.css'], content='# T\n\nTom & Jerry\n\n<sup>&#</sup>x27; Footnote.\n\nUnique middle paragraph.\n\nTail <sup>1</sup>.\n'); assert find_xhtml_errors(x)==[] and x.count('Unique middle paragraph.')==1 and imgs==[]; raw=_markdown_slice_to_xhtml('<sup>&#</sup>x27; Footnote.\n\nUnique middle paragraph.\n\n\x60</unclosed\x60 span.\n\nTail <sup>1</sup>.\n',['style.css']); assert raw.count('Unique middle paragraph.')==1 and 'Tail' in raw; print('PASS')"
```
Expected: `PASS`.

### [MD-EPUB-SPLIT]
```bash
. .venv/bin/activate && python -c "from modules.mark2epub import render_markdown_spines; spines,imgs,toc=render_markdown_spines('.','book.md',['style.css'], content='# İçindekiler\n\n| Bir | 1 |\n| İki | 5 |\n\n# Bir\n\nMetin A.\n\n# İki\n\nMetin B.\n'); assert len(spines)>=3 and all(e.href and '#' in e.href for e in toc); print('PASS')"
```
Expected: `PASS`.

### [MD-EPUB-DEDUP]
```bash
. .venv/bin/activate && python -c "from modules.mark2epub import SpineDoc, ContentDuplicationError, verify_no_prose_duplication; para='Bu paragraf en az elli karakter uzunluğunda benzersiz bir test metnidir.'; verify_no_prose_duplication(para, [SpineDoc('s00000.xhtml', f'<html><body><p>{para}</p></body></html>')]); ok=False
try:
    verify_no_prose_duplication(para, [SpineDoc('s00000.xhtml', f'<html><body><p>{para}</p><p>{para}</p></body></html>')])
except ContentDuplicationError:
    ok=True
assert ok; print('PASS')"
```
Expected: `PASS`.
