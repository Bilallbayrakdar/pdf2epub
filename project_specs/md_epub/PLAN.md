# EPUB TOC Reconciliation, Manzum Line Numbers & Per-Chapter Spine Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate decorative/TOC image artifacts, link and bidirectionally reconcile 100% of chapters across in-book `İçindekiler`, EPUB metadata (`TOC.xhtml`/`toc.ncx`), and body headings, format sequence-verified `manzum` line numbers at detected frequency, and split chapters into separate XHTML spine files so each chapter starts on a new page.

**Architecture:** Pre-render Markdown pipeline in `modules/mark2epub.py` backed by `modules/contents_toc.py` and `modules/verse_lineno.py`. First, `process_markdown_for_images` drops decorative images below `(200px, 200px, 100_000px²)` and omits unreferenced images from the archive. Second, `link_contents` strips images inside `İçindekiler`, merges wrapped table continuation rows using body-heading evidence, aligns TOC entries to single or paired (`# N. Bölüm` + `## Subtitle`) body headings via monotonic dynamic programming, and reconciles clean chapter titles across TOC metadata, in-book links, and body headings. Third, `render_markdown_spines` partitions the book at top-level chapter anchors into separate XHTML spine files, runs `format_verse_line_numbers` on auto-detected `manzum` chapters using `expected = prev + step` sequence recovery, and validates every spine file through `xhtml_gate`.

**Tech Stack:** Python 3.12+ (`.venv`), `Pillow`, `regex`, `markdown`, `lxml`, `difflib`.

**Spec:** `project_specs/md_epub/SPEC.md`

## Global Constraints

- Activate `.venv` before running any Python command (`. .venv/bin/activate`).
- Use `uv` if any package operation is ever needed (none required here; stdlib + existing `.venv` deps suffice).
- Keep code concise and idiomatic; add `# @start <ID>` / `# @end <ID>` code anchors without brackets and register them in `anchor_map/anchors.jsonl`.
- Preserve `get_chapter_XML(work_dir, md_filename, css_filenames, content=None)` backward compatibility so isolated single-chapter callers continue to pass.
- Remove temporary test files (`tests/test_md_epub_pipeline.py`) after full verification passes unless the user explicitly asks to keep them.
- Do not create unsolicited explanatory Markdown documents beyond `SPEC.md` and `PLAN.md`.

## Review Focus

- **Part headers vs wrapped 2-row TOC titles:** Unnumbered part rows (`| PERSEUS | |` before `| ALTIN DUŞ | 21 |` in `Kahramanlar`) must stay separate level-1 entries, whereas wrapped 2-row titles (`| XIII. Odysseus'un Phaiaklar İlinden Ayrılması | |` + `| 221 İthake 'ye Varışı | |` in `Odysseia`, or `| YUNANLAR (BİRİ HARİÇ HEPSİ) | |` + `| REHINELERINE SAYGI GÖSTERIR | 142 |` in `Troya`) must merge because their concatenation matches one body heading far better than two separate headings.
- **Mid-TOC section headings vs post-TOC body headings:** `# **OIDIPUS**` inside `Kahramanlar` `İçindekiler` (followed by `| KÂHİN KONUŞUYOR | 351 |`) must remain inside `İçindekiler`, while `# ÖNSÖZ` in `Odysseia` (followed by `![](...)` and prose) must terminate `İçindekiler`.
- **Prose / index immunity in `manzum` detector:** Scholarly prose with occasional numbers (`(I, 11-15)` in `Önsöz`), proper-noun indexes (`KALYPSO: 1, 14; IV, 557; VI 14, 78, 85` in `Odysseia'da Geçen Adlar`), and prose books (`Troya`, `Kahramanlar`) must not be misclassified as `manzum`.
- **Margin-clipped & OCR-corrupted verse counters:** Sequence tracking after `160` must recover `- 65`, `- 70`, `- 7 5` as `165`, `170`, `175`, recover `5 O` / `11 O` as `50` / `110` and `l 7 5` between `270` and `280` as `275`, and detach `<sup>1</sup> <sup>75</sup>İlk` into counter `175` + body `İlk` without eating legitimate prose words.
- **Cross-spine link integrity & XML validity:** After splitting one `.md` into ~37 `.xhtml` spine files, every `href` in `OPS/TOC.xhtml`, `OPS/toc.ncx`, and the in-book `İçindekiler` table must point to an existing `s000NN-....xhtml#id` whose target `id` exists inside that exact XHTML file.

---

### Task 1: Image Dimension Filter & Archive Exclusion (`[MD-EPUB-IMG]`)

**Files:**
- Modify: `modules/mark2epub.py`
- Test: `tests/test_md_epub_pipeline.py` (temporary; removed at completion)

**Interfaces:**
- Produces:
  - `process_markdown_for_images(markdown_text: str, work_dir: Path, min_width: int = 200, min_height: int = 200, min_area: int = 100_000) -> tuple[str, list[str]]`

- [ ] **Step 1: Write failing tests for decorative image filtering and archive exclusion**

Create `tests/test_md_epub_pipeline.py`:

```python
import tempfile
import zipfile
from pathlib import Path
from PIL import Image
from modules.mark2epub import convert_to_epub, process_markdown_for_images


def test_filter_small_and_line_divider_images():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "images").mkdir()
        Image.new("RGB", (1280, 52)).save(d / "images/divider.jpeg")
        Image.new("RGB", (150, 147)).save(d / "images/stamp.jpeg")
        Image.new("RGB", (420, 180)).save(d / "images/small_area.jpeg")  # 75,600 < 100k
        Image.new("RGB", (800, 400)).save(d / "images/real_map.jpeg")    # 320,000 >= 100k

        src = "Intro\n\n![](divider.jpeg)\n\n![](stamp.jpeg)\n\n![](small_area.jpeg)\n\n![Map](real_map.jpeg)\n"
        out, kept = process_markdown_for_images(src, d)
        assert "divider.jpeg" not in out
        assert "stamp.jpeg" not in out
        assert "small_area.jpeg" not in out
        assert "![Map](images/real_map.jpeg)" in out
        assert kept == ["real_map.jpeg"]


def test_filtered_images_excluded_from_epub_zip_and_opf():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "sample"
        (d / "images").mkdir(parents=True)
        Image.new("RGB", (1260, 50)).save(d / "images/bar.jpeg")
        Image.new("RGB", (600, 500)).save(d / "images/plate.jpeg")
        (d / "sample.md").write_text("# Chap\n\n![](bar.jpeg)\n\n![](plate.jpeg)\n", encoding="utf-8")

        convert_to_epub(d, d.parent, interactive=False)
        with zipfile.ZipFile(d / "sample.epub") as zf:
            names = set(zf.namelist())
            assert "OPS/images/plate.jpeg" in names
            assert "OPS/images/bar.jpeg" not in names
            opf = zf.read("OPS/package.opf").decode("utf-8")
            assert "plate.jpeg" in opf and "bar.jpeg" not in opf
```

- [ ] **Step 2: Run pytest to confirm failure**

Run: `. .venv/bin/activate && python -m pytest tests/test_md_epub_pipeline.py -v`
Expected: FAIL (`divider.jpeg` still kept and `bar.jpeg` still copied to `OPS/images/`).

- [ ] **Step 3: Implement `[MD-EPUB-IMG]` in `modules/mark2epub.py`**

Update `process_markdown_for_images` with `# @start MD-EPUB-IMG` / `# @end MD-EPUB-IMG` to inspect `Image.open(actual).size` and drop references where `w < min_width or h < min_height or w * h < min_area`. In `main()`, pass `image_filenames=sorted(processed_images)` to `get_packageOPF_XML` and stop copying unreferenced `remaining_images` into the EPUB zip.

- [ ] **Step 4: Run pytest to verify Task 1 passes**

Run: `. .venv/bin/activate && python -m pytest tests/test_md_epub_pipeline.py -v`
Expected: PASS.

---

### Task 2: DP TOC Alignment, Wrapped-Row Merging, TOC Image Strip & Title Reconciliation (`[MD-EPUB-TOC]`)

**Files:**
- Modify: `modules/contents_toc.py`
- Test: `tests/test_md_epub_pipeline.py`

**Interfaces:**
- Produces:
  - `TocEntry(NamedTuple)`: `(title: str, level: int, anchor: str, href: str = "")`
  - `link_contents(markdown: str) -> tuple[str, list[TocEntry]]`
  - `nest_entries(entries: list[TocEntry]) -> list[dict]`

- [ ] **Step 1: Add failing tests covering all TOC & title-reconciliation edge cases**

Append to `tests/test_md_epub_pipeline.py`:

```python
from modules.contents_toc import link_contents


def test_toc_strips_images_stops_at_body_and_merges_wrapped_rows():
    md = (
        "# İçindekiler\n\n"
        "![](toc_banner.jpeg)\n\n"
        "| Ö NSÖZ ··vii | |\n"
        "|---|---|\n"
        "| XIII. Odysseus'un Phaiaklar İlinden Ayrılması | |\n"
        "| 221 İthake 'ye Varışı | |\n"
        "| XVD. Telemakhos'un Kente Dönüşü | 287 |\n"
        "| XX. Tanrıdan İşmarlar | 341 |\n"
        "| Kronolojik Sıraya Göre Odysseus'un Yolculuğu | |\n"
        "| ve Serüvenleri | 417 |\n\n"
        "![](divider.jpeg)\n\n"
        "# ÖNSÖZ\n\n"
        "![](map.jpeg)\n\n"
        "Bu önsöz metni altmış karakterden çok daha uzun bir düz yazı paragrafıdır ve içindekiler burada bitmelidir.\n\n"
        "# XIII. Bölüm\n\n"
        "## Odysseus'un Phaiaklar İlinden Ayrılması - İthake'ye Varışı\n\n"
        "Destan metni.\n\n"
        "# XVII. Bölüm\n\n"
        "## Telemakhos'un Kente Dönüşü\n\n"
        "Destan metni.\n\n"
        "# XX. Bölüm\n\n"
        "## Tanndan İşınarlar\n\n"
        "Destan metni.\n\n"
        "# Kronolojik Sıraya Göre Odysseus'un Yolculuğu ve Serüven/eri\\*\n\n"
        "Ek metni.\n"
    )
    out, entries = link_contents(md)
    assert len(entries) == 5
    assert all(e.anchor for e in entries), entries
    toc_region = out.split("{: #icindekiler}", 1)[1].split("{: #ch-001}", 1)[0]
    assert "![" not in toc_region
    assert "![](map.jpeg)" in out  # preserved after # ÖNSÖZ!
    titles = [e.title for e in entries]
    assert titles[0] == "ÖNSÖZ"
    assert "XVII." in titles[2] and "XVD" not in titles[2]
    assert "Tanrıdan İşmarlar" in titles[3]
    assert "## Tanrıdan İşmarlar" in out  # body heading repaired from clean TOC!
    assert "Serüvenleri" in titles[4] and "/" not in titles[4] and "*" not in titles[4]
    assert "Serüvenleri" in out.splitlines()[-3]


def test_odysseia_and_kahramanlar_full_toc_alignment():
    ody_path = Path("data/Homeros Odysseia I\u0307s\u0327 Bankas\u0131 Yay\u0131nlar\u0131/Homeros Odysseia I\u0307s\u0327 Bankas\u0131 Yay\u0131nlar\u0131.md")
    if ody_path.exists():
        out, entries = link_contents(ody_path.read_text(encoding="utf-8"))
        assert 30 <= len(entries) <= 36, len(entries)
        unanchored = [e for e in entries if not e.anchor]
        assert unanchored == [], f"Unanchored entries: {unanchored}"
        bad = [e.title for e in entries if any(tok in e.title for tok in ("xxııı", "xxvii", "xxxv", "XVD", "xvm", "Serüven/eri"))]
        assert bad == [], f"Unreconciled TOC titles: {bad}"

    kah_path = Path("data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.md")
    if kah_path.exists():
        _out, k_entries = link_contents(kah_path.read_text(encoding="utf-8"))
        assert len(k_entries) >= 95
        assert sum(1 for e in k_entries if e.anchor) >= 95
```

- [ ] **Step 2: Run pytest to verify failure**

Run: `. .venv/bin/activate && python -m pytest tests/test_md_epub_pipeline.py -k toc -v`
Expected: FAIL (`len(unanchored) > 20`, `# ÖNSÖZ` swallowed, wrapped rows unmerged).

- [ ] **Step 3: Implement `[MD-EPUB-TOC]` in `modules/contents_toc.py`**

Implement `# @start MD-EPUB-TOC` / `# @end MD-EPUB-TOC`:
1. **End-of-TOC boundary & image strip:** When scanning after `# İçindekiler`, ignore `![](...)` lines (mark them for removal inside `[start, end)`). At any heading `i` without `_PAGE_PAIR`, look ahead skipping blank lines, horizontal rules, and `![](...)` lines to find the first non-heading content line `first_content`: if `first_content` does not start with `|` and has no `_PAGE_PAIR` and `len(first_content) > 60`, set `end = i; break`.
2. **Page-numeral detachment:** Strip trailing/leading glued page markers from TOC cell titles: trailing/leading Arabic digits (`113`, `221`, `83`, `s 3` -> page `53`), trailing Roman page numerals (`··vii`, `ix`, `xxııı`, `xxvii`, `xxxv`), and stray OCR bullets/underscores (`_`, `·`). Detect major section rows (`ÖNSÖZ`, `ODYSSEİA`, `EKLER`, or unnumbered all-caps part headers) as `level = 1` and chapters beneath a part header as `level = 2`.
3. **Body heading targets & composite chapter pairing:** Collect headings at `n >= end`, skipping mid-section PDF running headers (level `>= 3` repeating book/section titles like `#### Odysseia`, `#### XIV. Bölüm` inside a chapter, or repeated `## Odysseia 'da Geçen Adlar` inside the index) and skipping `#### <Roman>. Bölüm` mini-summaries inside `Önsöz` (before `# **ODYSSEİA**`). Pair consecutive `# <Roman>. Bölüm` (line `n1`) + `## <Subtitle>` (line `n2`, with only blank lines between) into a single composite target `(n1, n2, roman_heading, subtitle)`.
4. **Wrapped continuation merging:** Before final alignment, if table row `k` has `page == ""` and row `k+1` has a page number (or detached page number), merge row `k` + row `k+1` whenever `max_sim(merged, targets) > max(max_sim(row_k, targets), max_sim(row_k1, targets)) + 0.08` or row `k+1` starts lowercase.
5. **Monotonic dynamic programming alignment:** Compute `sim(i, j)` combining fuzzy `_key` similarity, token-bag Jaccard/SequenceMatcher similarity (handling swapped halves like `Odysseus'un Salı / Kalypso'nun Mağarası`), and Roman-numeral match bonus. Solve exact O(N·M) monotonic DP maximizing total similarity (with small skip penalty so valid matches `>= 0.48` are always taken in order without greedy cursor jumps).
6. **Bidirectional title reconciliation:**
   - For paired epic chapters (`Bölüm` targets), determine the canonical Roman numeral `I`..`XXIV` from valid Roman parsing or sequential chapter index (fixing `ll.` -> `II.`, `m.` -> `III.`, `X.` before `X.` -> `IX.`, `XVD.` -> `XVII.`, `xvm.` -> `XVIII.`, and body `# I. Bölüm` on Book II -> `# II. Bölüm`, `## VI. Bölüm` before `VI.` -> `# V. Bölüm`).
   - Reconcile word tokens between TOC subtitle and body heading word-by-word via `SequenceMatcher` opcodes: strip `*`, `\`, `/`, `<sup>` tags, and internal OCR spaces (`Ai o los` -> `Aiolos`, `T elemakhos` -> `Telemakhos`, `Lakedaimon' da` -> `Lakedaimon'da`, `İIthakelilerin` -> `İthakelilerin`); when two aligned words differ by 1–2 chars, score each word for OCR cleanliness (penalizing digits/symbols, `rn`/`nn`/`ın` for `r`/`m`, trailing uppercase `L` after lowercase `UğraklarL`, `run` vs `nın`, `lan` vs `ları` when the partner has `ları`, `Ayniması` vs `Ayrılması`, `Toplannsı` vs `Toplantısı`, `Sesieniş` vs `Sesleniş`, `Anlanr` vs `Anlatır`, `Sığıları` vs `Sığırları`, `Tanndan İşınarlar` vs `Tanrıdan İşmarlar`, `Serüven/eri` vs `Serüvenleri`).
   - Emit canonical TOC title (`f"{roman}. {clean_sub}"` for numbered chapters, `clean_title` otherwise) to `TocEntry`, in-book `İçindekiler` table/list links, and rewrite the matched body heading line(s) in `lines` (`# {roman}. Bölüm {: #ch-NNN}` + `## {clean_sub}`, or `# {clean_title} {: #ch-NNN}`).

- [ ] **Step 4: Run pytest to verify Task 2 passes**

Run: `. .venv/bin/activate && python -m pytest tests/test_md_epub_pipeline.py -v`
Expected: PASS.

---

### Task 3: `Manzum` Auto-Detection & Sequence-Verified Verse Line Numbering (`[MD-EPUB-VERSE]`)

**Files:**
- Modify: `modules/verse_lineno.py`
- Test: `tests/test_md_epub_pipeline.py`

**Interfaces:**
- Produces:
  - `detect_verse_step(text: str, default_step: int = 5) -> int | None`
  - `format_verse_line_numbers(md: str) -> str`

- [ ] **Step 1: Add failing tests for `manzum` detection, frequency inference, and sequence recovery**

Append to `tests/test_md_epub_pipeline.py`:

```python
import re
from modules.verse_lineno import detect_verse_step, format_verse_line_numbers


def test_manzum_detection_distinguishes_poetry_from_prose_and_index():
    prose = "# Önsöz\n\nİlyada (I, 1-10) ve (I, 11-15) ile (I, 16-26) arasında anlatılır.\n"
    index_txt = "## K\n\nKALYPSO: 1, 14; IV, 557; VI 14, 78, 85, 116, 180.\n"
    poem = (
        "# Bölüm\n\n"
        "Bir dize, 5 ikinci dize. 10 Üçüncü dize.\n"
        "- 15 dördüncü dize\n- 20 beşinci dize\n- 25 altıncı dize\n- 30 yedinci dize\n"
    )
    assert detect_verse_step(prose) is None
    assert detect_verse_step(index_txt) is None
    assert detect_verse_step(poem) == 5
    assert format_verse_line_numbers(prose) == prose
    assert format_verse_line_numbers(index_txt) == index_txt


def test_sequence_recovery_handles_ocr_splits_clipped_hundreds_and_page_gap():
    poem = (
        "# VI. Bölüm\n\n## Başlık\n\n"
        "Giriş dizesi, 5 devamı. 10 İkinci parça, 15 üçüncü.\n"
        "20 dördüncü parça.\n"
        "- 25 beşinci\n"
        "- 3 O altıncı\n"
        "- <sup>3</sup> <sup>5</sup>İlk yedinci\n"
        "40 sekizinci\n"
        "#### Odysseia\n\n"
        "Paragraf A satır bir.\n\n"
        "Paragraf B satır iki (45 düşmüş).\n\n"
        "- 50 onuncu satır.\n"
    )
    out = format_verse_line_numbers(poem)
    nums = [int(x) for x in re.findall(r'<span class="verse-ln"[^>]*>(\d+)</span>', out)]
    assert nums == [5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
    assert ">İlk yedinci<" in out
    assert "#### Odysseia" not in out


def test_clipped_hundreds_recovery_in_odysseia_book_6():
    ody_path = Path("data/Homeros Odysseia I\u0307s\u0327 Bankas\u0131 Yay\u0131nlar\u0131/Homeros Odysseia I\u0307s\u0327 Bankas\u0131 Yay\u0131nlar\u0131.md")
    if ody_path.exists():
        lines = ody_path.read_text(encoding="utf-8").splitlines()
        book6 = "\n".join(lines[1541:1673])
        out = format_verse_line_numbers(book6)
        nums = [int(x) for x in re.findall(r'<span class="verse-ln"[^>]*>(\d+)</span>', out)]
        assert nums == list(range(5, max(nums) + 1, 5)), nums
```

- [ ] **Step 2: Run pytest to verify failure**

Run: `. .venv/bin/activate && python -m pytest tests/test_md_epub_pipeline.py -k "manzum or sequence or book_6" -v`
Expected: FAIL (`detect_verse_step` missing; `3 O`, `<sup>3</sup> <sup>5</sup>İlk`, `- 65` after `160`, and dropped `45` fail).

- [ ] **Step 3: Implement `[MD-EPUB-VERSE]` in `modules/verse_lineno.py`**

Implement `# @start MD-EPUB-VERSE` / `# @end MD-EPUB-VERSE`:
1. **Section partitioning & `detect_verse_step`:** Partition Markdown by top-level `# ` / `## <Roman>. Bölüm` chapter boundaries (or treat single-chapter slice directly). Ignore citation parentheses `(I, 11-15)` and `ALLCAPS:` index lines. Extract clean standalone/margin candidate integers `<= 999`; look for an ascending subsequence of length `>= 5` whose consecutive deltas have a clear positive mode `step` (typically `5`, `10`, etc.). Return `step` if found, else `None`.
2. **Pre-normalization inside `manzum` sections:** Drop mid-chapter PDF running headers (`^#{3,6}\s+Odysse\w+\s*$`, `^#{3,6}\s+[IVXLC]+\.\s+Bölüm\s*$`) and unwrap digit-only `<sup>...</sup>` tags (`<sup>1</sup> <sup>75</sup>` -> `1 75 `) so glued words like `<sup>75</sup>İlk` separate cleanly into `1 75 İlk`.
3. **Expected-counter scanner (`expected = prev + step`):**
   - Build a targeted matcher `_find_counter(text, start_pos, target)`:
     - Primary pattern: `target` digits separated by optional spaces, allowing OCR confusions (`0`: `[0Oo]`, `1`: `[1lIı]`, `2`: `[2lI]`, `5`: `[5S]`, `8`: `[8B]`) when bounded by start-of-line/list `- `, whitespace, or punctuation (not inside an ordinary letters-only Turkish word unless at least one real digit is present or it follows `- ` at line start, e.g. `- l 7 5` between `270` and `280`).
     - Clipped-hundreds fallback (when `target >= 100`): match `str(target)[1:]` at line/list start (`^(?:-\s*)?65\b` when `target == 165`) if `target + step` (or clipped `(target + step) % 100`) follows afterward.
   - Search within a bounded forward window (`<= 1400` chars) for `expected`:
     - If `expected` is found before `expected + step`, emit the text slice before the match under `prev_pending`, then set `prev_pending = expected` and advance `prev = expected`.
     - If `expected` is absent in the window but `expected + step` IS found within `<= 1600` chars, split the intervening text between `prev` and `expected + step` at the nearest paragraph break (or sentence boundary near the midpoint), assign `expected` to the second half, and continue from `expected + step`.
4. **HTML rendering:** Split paragraphs at extracted counters and line breaks into `<p class="verse"><span class="verse-ln" aria-label="Satır {ln}">{ln}</span><span class="verse-body">{xml_escape(body)}</span></p>` (using `verse-ln-empty` when `ln is None`), stripping leading Markdown list markers (`- `) from verse lines.

- [ ] **Step 4: Run pytest to verify Task 3 passes**

Run: `. .venv/bin/activate && python -m pytest tests/test_md_epub_pipeline.py -v`
Expected: PASS (including all multiples of 5 in `Odysseia` Book VI!).

---

### Task 4: Per-Chapter XHTML Spine Splitting, Cross-File Hrefs & Full Verification (`[MD-EPUB-SPLIT]`, `[MD-EPUB-PACK]`)

**Files:**
- Modify: `modules/mark2epub.py`
- Modify: `anchor_map/anchors.jsonl`
- Test: `tests/test_md_epub_pipeline.py` (deleted after verification passes)

**Interfaces:**
- Produces:
  - `SpineDoc(NamedTuple)`: `(filename: str, xhtml: str)`
  - `render_markdown_spines(work_dir: str | Path, md_filename: str, css_filenames: list[str], content: str | None = None, start_index: int = 0) -> tuple[list[SpineDoc], list[str], list[TocEntry]]`

- [ ] **Step 1: Add failing tests for per-chapter XHTML spine splitting and link resolution**

Append to `tests/test_md_epub_pipeline.py`:

```python
import lxml.etree as ET


def test_per_chapter_xhtml_spine_split_and_cross_file_links():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "epic"
        d.mkdir(parents=True)
        md = (
            "# BAŞLIK\n\nÖn bilgi.\n\n"
            "# İçindekiler\n\n"
            "| Önsöz | vii |\n"
            "|---|---|\n"
            "| I. İlk Bölüm | 1 |\n"
            "| II. İkinci Bölüm | 15 |\n\n"
            "# Önsöz\n\n"
            "Bu önsöz metni altmış karakterden çok daha uzun bir düz yazı paragrafıdır ve ayrı sayfada başlamalıdır.\n\n"
            "# I. Bölüm\n\n## İlk Bölüm\n\n"
            "Dize bir, 5 dize iki. 10 Dize üç, 15 dize dört, 20 dize beş, 25 dize altı.\n\n"
            "# II. Bölüm\n\n## İkinci Bölüm\n\n"
            "Başka bölüm metni.\n"
        )
        (d / "epic.md").write_text(md, encoding="utf-8")
        convert_to_epub(d, d.parent, interactive=False)

        with zipfile.ZipFile(d / "epic.epub") as zf:
            spine_files = sorted(n for n in zf.namelist() if n.startswith("OPS/s") and n.endswith(".xhtml"))
            assert len(spine_files) == 5  # frontmatter, İçindekiler, Önsöz, I. Bölüm, II. Bölüm

            # Every href in TOC.xhtml and in-book İçindekiler resolves to an actual file + id!
            ids_by_doc = {}
            for sf in spine_files:
                root = ET.fromstring(zf.read(sf))
                ids_by_doc[Path(sf).name] = set(root.xpath("//@id"))

            toc_root = ET.fromstring(zf.read("OPS/TOC.xhtml"))
            hrefs = toc_root.xpath("//*[local-name()='a']/@href")
            assert len(hrefs) == 4  # İçindekiler + 3 chapters
            for h in hrefs:
                doc, frag = h.split("#", 1)
                from urllib.parse import unquote
                doc = unquote(doc)
                assert doc in ids_by_doc, (doc, list(ids_by_doc))
                assert frag in ids_by_doc[doc], (frag, doc, ids_by_doc[doc])

            # Check in-book İçindekiler links in spine_files[1]
            ic_root = ET.fromstring(zf.read(spine_files[1]))
            ic_hrefs = ic_root.xpath("//*[local-name()='a']/@href")
            assert len(ic_hrefs) == 3
            for h in ic_hrefs:
                doc, frag = h.split("#", 1)
                from urllib.parse import unquote
                assert frag in ids_by_doc[unquote(doc)]
```

- [ ] **Step 2: Run pytest to verify failure**

Run: `. .venv/bin/activate && python -m pytest tests/test_md_epub_pipeline.py -k spine_split -v`
Expected: FAIL (`len(spine_files) == 1`).

- [ ] **Step 3: Implement `[MD-EPUB-SPLIT]` and update `[MD-EPUB-PACK]` in `modules/mark2epub.py`**

1. Add `render_markdown_spines` wrapped in `# @start MD-EPUB-SPLIT` / `# @end MD-EPUB-SPLIT`:
   - Run `process_markdown_for_images`, normalize `#### <Roman>. Bölüm` -> `##` inside prose only after TOC alignment if needed, and call `link_contents(markdown_data)` on the whole Markdown document first so anchors (`#icindekiler`, `#ch-001`, ...) and reconciled titles are placed globally.
   - Identify split line indices at every heading carrying `{: #icindekiler}` or `{: #ch-\d+}` (keeping any content before `{: #icindekiler}` as segment 0 when non-empty; if a `{: #ch-\d+}` is placed on `## Subtitle` immediately preceded by `# <Roman>. Bölüm`, start the segment at `# <Roman>. Bölüm`).
   - Map each anchor (`icindekiler`, `ch-001`, ...) to its segment's filename `quote(f"s{start_index + seg_idx:05d}-{stem}.xhtml")`.
   - Rewrite in-book Markdown links `](#ch-NNN)` to `]({target_href}#ch-NNN)` and populate `TocEntry.href = f"{target_href}#{entry.anchor}"`.
   - Run `format_verse_line_numbers` on each segment and render XHTML wrapper via shared `_markdown_slice_to_xhtml`.
2. Update `main()` (`[MD-EPUB-PACK]`) and `get_packageOPF_XML` so spine items and manifest items iterate over the generated `SpineDoc` filenames (`s00000-...xhtml`, `s00001-...xhtml`, ...) rather than one file per `.md`. Keep `get_chapter_XML` intact for single-string callers.
3. Update `anchor_map/anchors.jsonl` with `MD-EPUB-IMG`, `MD-EPUB-TOC`, `MD-EPUB-VERSE`, `MD-EPUB-SPLIT`, and refreshed `MD-EPUB-CHAP` / `MD-EPUB-PACK`.

- [ ] **Step 4: Run full test suite, rebuild `Odysseia` EPUB, verify all spec commands, and clean up test script**

Run:
1. `. .venv/bin/activate && python -m pytest tests/test_md_epub_pipeline.py -v`
2. Rebuild `Odysseia`: `. .venv/bin/activate && python -u main.py "data/Homeros Odysseia İş Bankası Yayınları.pdf" data --skip-md --non-interactive`
3. Verify all `[MD-EPUB-*]` commands in `project_specs/md_epub/SPEC.md` and `[XHT-*]` commands in `project_specs/xhtml_gate/SPEC.md` print `PASS`.
4. Inspect unpacked `Odysseia.epub`: confirm 0 decorative `~1260x52` images in `OPS/images/`, 0 `<img>` in the `İçindekiler` spine file, 100% anchored TOC entries in both `İçindekiler` and `OPS/TOC.xhtml` with reconciled titles, separate spine XHTML files per chapter, and uninterrupted `5, 10, 15, ...` verse line numbers across all 24 epic books.
5. Delete temporary `tests/test_md_epub_pipeline.py` (and `tests/` directory if empty).
