# Footnotes, Page-Break Prose Rejoining & Heading Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate inline/unformatted bottom-of-page footnotes, mid-sentence page-break paragraph splits, and inconsistent chapter/subsection heading levels across PDF-to-Markdown and Markdown-to-EPUB conversions using general layout and structural rules, and rebuild all three EPUBs in `data/` (`Kahramanlar`, `Troya`, `Odysseia`).

**Architecture:** A new module `modules/structure_norm.py` implements `[PDF-MD-FOOT]` (`normalize_footnotes_and_breaks`), `[MD-EPUB-HEAD]` (`normalize_heading_hierarchy`), and `[MD-EPUB-FOOT]` (`upgrade_footnotes_to_epub3`). `[MD-EPUB-HEAD]` runs before `[PDF-MD-FOOT]` in `mark2epub._prepare_markdown` so stripped running page headers and demoted dialogue/glossary headings let broken page-turn prose re-bridge cleanly, while trailing book-title back-cover blurbs split onto `#arka-kapak`. `[PDF-MD-FOOT]` scans PDF page separator rules and text bounds via `pypdfium2` whenever a real Markdown file has a sibling PDF (falling back to Markdown `<sup>` markers otherwise), skips zones already covered by existing `[^...]:` definitions for strict idempotency, reassembles multi-page continuations, links inline callouts (`<sup>k</sup>`, `¹²³`, `wordk`, `. k`, OCR `ı` for `1`, and `*`/`·` editor/translator notes without disturbing Markdown `*italics*`), and rejoins prose paragraphs split across lifted footnotes or unpunctuated page turns while leaving `manzum` verse untouched. `modules/mark2epub.py` scopes `[^fn-N]:` definitions per spine segment, upgrades only `<div class="footnote">` + `fnref` tags to EPUB 3 `<a epub:type="noteref">` and `<aside epub:type="footnote">` with sequential `1..N` per-chapter numbering, and styles footnotes in CSS.

**Tech Stack:** Python 3.13 (`.venv`), `pypdfium2`, `markdown` 3.10.2, `lxml` (`etree`, `html`), `regex`.

**Spec:** `project_specs/pdf_md/SPEC.md`, `project_specs/md_epub/SPEC.md`

## Global Constraints

- Always activate `.venv` (`. .venv/bin/activate && python ...`) before running Python code.
- `pytest` is not installed in `.venv`; run tests via inline `python -c "..."` commands and remove any temporary test files immediately after verification succeeds.
- Use `uv add` if dependencies are ever needed (none required for this plan).
- Keep code concise, idiomatic, and free of unnecessary boilerplate.
- Do not create unsolicited explanatory `.md` documents.
- Keep existing `data/*/*.md` files untouched on disk when running `mark2epub`; never stage or commit anything under `data/`.
- Release `pypdfium2` page/textpage local references before calling `pdf.close()` so processes never hang at interpreter shutdown.
- Maintain `# @start <ID>` / `# @end <ID>` code anchors and keep `anchor_map/anchors.jsonl` sorted by `id` (valid JSONL, one object per line).

## Review Focus

1. Multi-page footnote continuations (`rule_width > 0.55 * page_width` or unfinished prior note sentence + unmarked continuation block, e.g. `Kahramanlar` pp. 84–85, 90–91, 120–121, 124–125) — must join cleanly onto the prior footnote with de-hyphenation (`"Achealı-` + `lar"` $\to$ `"Achealılar"`).
2. Unnumbered ClearScan footnotes and `-ed.n.` / `-ç.n.` `*`/`·` notes (`Kahramanlar` pp. 39, 95, 111, 119, 120, 127, 327 and `Troya`) — must be extracted via PDF rule zones, link `word*` / `word·` (`Adamanttan·`, `veneri*`) without eating Markdown `*italic*` delimiters, and number sequentially `1..N` per chapter.
3. Strict idempotency with `pdf_path` (`pdf2md` $\to$ `mark2epub`) and hyphenated word callout relocation (`değeri-1` + `nin` $\to$ `değerinin[^fn-N]`) — re-running `normalize_footnotes_and_breaks(out, pdf_path)` must skip PDF zones already present in `[^...]:` definitions so body prose is never re-matched.
4. `manzum` verse preservation and running-header healing (`Odysseia`) — verse lines and 3-digit verse counters (`<sup>845</sup>İthake'yle...`) must never be merged or misclassified as footnotes, while mid-chapter running headers (`## Odysseia'nm Kuruluşu`) are stripped before prose rejoining.
5. Spine-scoped footnotes, `#arka-kapak` back-cover isolation, and EPUB 3 XHTML validity — `render_markdown_spines` must attach only referenced `[^fn-N]:` definitions per spine, isolate `# KAHRAMANLAR` / `# TROYA` onto `#arka-kapak`, and isolate `<div class="footnote">` parsing so `<pre>`/`<code>`, `$...$` math, and `find_xhtml_errors(xhtml) == []` remain intact.

---

### Task 1: Implement `[PDF-MD-FOOT]`, `[MD-EPUB-HEAD]`, and `[MD-EPUB-FOOT]` in `modules/structure_norm.py` and Wire PDF/TOC Hooks

**Files:**
- Create: `modules/structure_norm.py`
- Modify: `modules/contents_toc.py:94-106,625-642`
- Modify: `modules/pdf2md.py:110-120`

**Interfaces:**
- Consumes: Sanitized Markdown string from `sanitize_marker_markdown` and optional PDF path (`Path | str | None`).
- Produces:
  - `resolve_pdf_path(work_dir: Path, md_filename: str = "") -> Path | None`
  - `normalize_footnotes_and_breaks(markdown_text: str, pdf_path: Path | str | None = None) -> str`
  - `normalize_heading_hierarchy(markdown_text: str, doc_hint: str = "") -> str`
  - `upgrade_footnotes_to_epub3(html_text: str) -> str`

- [ ] **Step 1: Run failing test for `[PDF-MD-FOOT]`, `[MD-EPUB-HEAD]`, and `[MD-EPUB-FOOT]`**

Run:
```bash
. .venv/bin/activate && python -c "
from modules.structure_norm import (
    normalize_footnotes_and_breaks,
    normalize_heading_hierarchy,
    upgrade_footnotes_to_epub3,
)
print('IMPORTED')
"
```
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.structure_norm'`.

- [ ] **Step 2: Create `modules/structure_norm.py` and wire `modules/contents_toc.py` and `modules/pdf2md.py`**

Create `modules/structure_norm.py`:
```python
"""General PDF/Markdown footnote extraction, page-break prose rejoining, heading normalization, and EPUB 3 footnotes."""
from __future__ import annotations

import ctypes
import gc
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple
from xml.sax.saxutils import escape as xml_escape

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from lxml import etree, html as lxml_html

_FENCE_MASK = re.compile(r"(?m)^(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^\1[ \t]*$|\Z)")
_NORM_ALNUM = re.compile(r"[^a-z0-9çğıöşü]+")
_FUZZY_TR = str.maketrans(
    {
        "ı": "i", "İ": "i", "I": "i", "l": "i", "1": "i", "0": "o",
        "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o", "ç": "c", "Ç": "c", "â": "a", "î": "i", "û": "u",
    }
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_ANCHOR_RE = re.compile(r"\{:\s*#(icindekiler|arka-kapak|ch-\d+)\s*\}")
_BOLUM_RE = re.compile(r"^[IVXLCDM\d]+\.\s*Bölüm\b", re.I)
_SUP_FN_LINE = re.compile(
    r"^\s*<sup>\s*(\d{1,2}|[*†‡§•.\s]+|[ıIl])\s*</sup>\s*\.?\s*(?=[A-ZÇĞİÖŞÜ\"“'‘(\[]|\S+\s)"
)
_EXPLICIT_FN_PREFIX = re.compile(
    r"^\s*(?:<sup>\s*(\d{1,2}|[*†‡§•.\s]+|[ıIl])\s*</sup>\s*\.?\s*|(\d{1,2})\s*\.?\s+(?=[A-ZÇĞİÖŞÜ\"“'‘(])|([*†‡§•']+)\s+(?=[A-ZÇĞİÖŞÜ]))"
)
_EDITOR_NOTE_TAIL = re.compile(r"(?:[-–—]\s*(?:ed|ç|haz)\.\s*n\.|\((?:ed|ç|haz)\.\s*n\.\))\s*$", re.I)
_FN_DEF_LINE = re.compile(r"^\s*\[\^([^\]]+)\]:\s*(.*)$")
_FN_REF_TAIL = re.compile(r"(?:\s*\[\^[^\]]+\])+\s*$")
_TERM_PUNCT = re.compile(r"[.!?:…\"”'’)\]]\s*$")
_WORD_HYPHEN_END = re.compile(r"([A-Za-zÇĞİÖŞÜçğıöşü])-((?:\[\^[^\]]+\])*)\s*$")
_UNICODE_SUPS = {"1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}
_SKIP_NUM_WORDS = frozenset({"s", "cilt", "mitleri", "bölüm", "sayfa", "no", "şekil", "tablo"})


class _PdfPageInfo(NamedTuple):
    pno: int
    is_cont: bool
    body_first: str
    body_last: str
    fn_text: str


def _norm(text: str) -> str:
    s = re.sub(r"<[^>]+>", " ", text)
    s = re.sub(r"\[\^[^\]]+\]", " ", s)
    return _NORM_ALNUM.sub("", s.replace("İ", "i").replace("I", "ı").lower())


def _norm_nodig(text: str) -> str:
    return re.sub(r"^[\d*•†‡§]+", "", _norm(text))


def _fuzzy_key(text: str) -> str:
    s = _ANCHOR_RE.sub(" ", text)
    s = re.sub(r"<[^>]+>|\[\^[^\]]+\]|[*_`·\\↓]+", " ", s)
    return re.sub(r"[^a-z]", "", s.translate(_FUZZY_TR).lower())


def resolve_pdf_path(work_dir: Path, md_filename: str = "") -> Path | None:
    if md_filename and not (work_dir / md_filename).is_file():
        return None
    stem = Path(md_filename).stem if md_filename else work_dir.name
    for cand in (work_dir / f"{stem}.pdf", work_dir.parent / f"{work_dir.name}.pdf", work_dir.parent / f"{stem}.pdf"):
        if cand.is_file():
            return cand
    targets = {unicodedata.normalize("NFC", s) for s in (stem, work_dir.name) if s}
    for folder in (work_dir, work_dir.parent):
        if folder.is_dir():
            for p in folder.glob("*.pdf"):
                if unicodedata.normalize("NFC", p.stem) in targets:
                    return p
    return None


# @start PDF-MD-FOOT
def _clean_pdf_lines(raw: str) -> list[str]:
    lns = [ln.strip() for ln in raw.replace("\x02", "-").replace("\u00ad", "-").splitlines() if ln.strip()]
    while lns and re.fullmatch(r"[\d\sIVXLCDMivxlcdmxlı1.]{1,10}", lns[-1]):
        lns.pop()
    while lns and re.fullmatch(r"[\d\sIVXLCDMivxlcdmxlı1.]{1,10}", lns[0]):
        lns.pop(0)
    return lns


def _scan_pdf_open(pdf: pdfium.PdfDocument) -> list[_PdfPageInfo]:
    out: list[_PdfPageInfo] = []
    for pno in range(len(pdf)):
        page = pdf[pno]
        w, h = page.get_size()
        n_obj = pdfium_c.FPDFPage_CountObjects(page)
        raw_rules: list[tuple[float, float, float, float]] = []
        for oi in range(n_obj):
            obj = pdfium_c.FPDFPage_GetObject(page, oi)
            t = pdfium_c.FPDFPageObj_GetType(obj)
            if t in (2, 3):
                l, b, r, top = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                if pdfium_c.FPDFPageObj_GetBounds(obj, ctypes.byref(l), ctypes.byref(b), ctypes.byref(r), ctypes.byref(top)):
                    bw, bh = r.value - l.value, top.value - b.value
                    if bw > 35 and bh < 6 and h * 0.06 < b.value < h * 0.72:
                        raw_rules.append((l.value, b.value, r.value, top.value))
            obj = None

        raw_rules.sort(key=lambda x: x[3])
        merged: list[tuple[float, float, float, float, float]] = []
        for l_v, b_v, r_v, t_v in raw_rules:
            if merged and abs(t_v - merged[-1][3]) < 4.0:
                pl, pb, pr, pt, _ = merged[-1]
                nl, nr = min(pl, l_v), max(pr, r_v)
                merged[-1] = (nl, min(pb, b_v), nr, max(pt, t_v), nr - nl)
            else:
                merged.append((l_v, b_v, r_v, t_v, r_v - l_v))

        rule = None
        if len(merged) == 1:
            rule = merged[0]
        elif len(merged) == 2 and merged[0][3] < h * 0.50 and merged[1][3] > h * 0.60:
            rule = merged[0]

        tp = page.get_textpage()
        f_cut = h * 0.045
        if rule is not None:
            ry, rw = rule[3], rule[4]
            body_lns = _clean_pdf_lines(tp.get_text_bounded(0, ry, w, h * 0.96))
            fn_lns = _clean_pdf_lines(tp.get_text_bounded(0, f_cut, w, ry))
        else:
            rw = 0.0
            body_lns = _clean_pdf_lines(tp.get_text_bounded(0, f_cut, w, h * 0.96))
            fn_lns = []
        tp = None
        page = None
        out.append(
            _PdfPageInfo(
                pno + 1,
                rw > w * 0.55,
                body_lns[0] if body_lns else "",
                body_lns[-1] if body_lns else "",
                " ".join(fn_lns),
            )
        )
    return out


def _scan_pdf_layout(pdf_path: Path) -> list[_PdfPageInfo]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return _scan_pdf_open(pdf)
    finally:
        gc.collect()
        pdf.close()


def _is_special_block(blk: str) -> bool:
    s = blk.lstrip()
    return (
        not s
        or s.startswith(("#", "|", "![", ">", "- ", "* ", "+ ", "\x00FENCE", "<p class=\"verse\""))
        or bool(_FN_DEF_LINE.match(s))
        or bool(re.fullmatch(r"[-*_\\\s]{3,}", s))
    )


def _detect_manzum_blocks(blocks: list[str]) -> set[int]:
    manzum: set[int] = set()
    sec_start = 0
    for i, blk in enumerate(blocks + ["# END"]):
        if i == len(blocks) or blk.lstrip().startswith("# "):
            sec_text = "\n\n".join(blocks[sec_start:i])
            nums = [
                int(m.group(1))
                for m in re.finditer(r"(?:^|\s)(\d{1,4})(?=\s+[A-ZÇĞİÖŞÜa-zçğıöşü\"“'‘])", sec_text, re.M)
                if 5 <= int(m.group(1)) <= 2000 and int(m.group(1)) % 5 == 0
            ]
            asc = sum(1 for a, b in zip(nums, nums[1:]) if 0 < b - a <= 15)
            if asc >= 5:
                manzum.update(range(sec_start, i))
            sec_start = i
    return manzum


def _has_terminal_punct(text: str) -> bool:
    core = _FN_REF_TAIL.sub("", text.strip())
    core = re.sub(r"</?(?:i|b|em|strong|sup|sub)>", "", core).strip()
    return bool(_TERM_PUNCT.search(core))


def _join_prose_pair(a: str, b: str) -> str:
    a_str, b_str = a.rstrip(), b.lstrip()
    if m := _WORD_HYPHEN_END.search(a_str):
        trail_refs = m.group(2)
        if re.match(r"^[A-Za-zÇĞİÖŞÜçğıöşü]", b_str):
            prefix = a_str[: m.start(1) + 1]
            if trail_refs:
                w_match = re.match(r"^([^\s]+)(.*)$", b_str, re.S)
                if w_match:
                    return f"{prefix}{w_match.group(1)}{trail_refs}{w_match.group(2)}"
            return f"{prefix}{b_str}"
    return f"{a_str} {b_str}"


def _align_pdf_fn_zones(zones: list[_PdfPageInfo], blocks: list[str], manzum: set[int]) -> dict[int, tuple[int, int, _PdfPageInfo]]:
    if not zones:
        return {}
    md_norms = [_norm_nodig(b) for b in blocks]
    gram_idx: dict[str, list[int]] = defaultdict(list)
    for i, mn in enumerate(md_norms):
        if i in manzum or _is_special_block(blocks[i]):
            continue
        for step, width in ((3, 6), (4, 9)):
            for pos in range(0, max(1, len(mn) - width + 1), step):
                g = mn[pos : pos + width]
                if len(g) >= 4:
                    gram_idx[g].append(i)

    zone_cands: list[list[tuple[int, int, float]]] = []
    for z in zones:
        fn_n = _norm_nodig(z.fn_text)
        if len(fn_n) < 3:
            zone_cands.append([])
            continue
        hits: Counter[int] = Counter()
        for step, width in ((3, 6), (4, 9)):
            for pos in range(0, max(1, len(fn_n) - width + 1), step):
                for idx in gram_idx.get(fn_n[pos : pos + width], ()):
                    hits[idx] += 1
        seeds = {idx for idx, _ in hits.most_common(45)}
        if len(fn_n) < 28:
            for idx, mn in enumerate(md_norms):
                if idx not in manzum and not _is_special_block(blocks[idx]) and mn:
                    if fn_n == mn or (len(fn_n) >= 7 and fn_n in mn and len(mn) <= len(fn_n) * 2 + 15):
                        seeds.add(idx)
        spans: dict[tuple[int, int], float] = {}
        for idx in seeds:
            for s_i in range(max(0, idx - 3), idx + 1):
                if s_i in manzum or _is_special_block(blocks[s_i]):
                    continue
                acc = ""
                for e_i in range(s_i, min(len(blocks), s_i + 8)):
                    if e_i in manzum or _is_special_block(blocks[e_i]):
                        break
                    acc += md_norms[e_i]
                    if len(acc) > len(fn_n) * 1.45 + 35:
                        break
                    r = SequenceMatcher(None, fn_n, acc).ratio()
                    if r >= 0.70 and r > spans.get((s_i, e_i), 0.0):
                        spans[(s_i, e_i)] = r
        zone_cands.append(sorted((s_i, e_i, r) for (s_i, e_i), r in spans.items()))

    states: dict[int, tuple[float, list[tuple[int, int, int]]]] = {-1: (0.0, [])}
    for zi, spans in enumerate(zone_cands):
        if not spans:
            continue
        nxt = dict(states)
        for s_i, e_i, r in spans:
            best_prev = max(((sc, ch) for pe, (sc, ch) in states.items() if pe < s_i), key=lambda x: x[0], default=None)
            if best_prev is not None:
                cand_sc = best_prev[0] + r
                if e_i not in nxt or cand_sc > nxt[e_i][0]:
                    nxt[e_i] = (cand_sc, best_prev[1] + [(zi, s_i, e_i)])
        pruned: dict[int, tuple[float, list[tuple[int, int, int]]]] = {}
        best_so_far = -1.0
        for e_i in sorted(nxt):
            sc, ch = nxt[e_i]
            if sc > best_so_far:
                pruned[e_i] = (sc, ch)
                best_so_far = sc
        states = pruned

    _, best_chain = max(states.values(), key=lambda x: x[0])
    return {zi: (s_i, e_i, zones[zi]) for zi, s_i, e_i in best_chain}


def _strip_fn_prefix(blk: str) -> tuple[str | None, str]:
    m = _EXPLICIT_FN_PREFIX.match(blk)
    if not m:
        body = blk.strip()
        return ("*" if _EDITOR_NOTE_TAIL.search(body) else None), body
    raw = (m.group(1) or m.group(2) or m.group(3) or "").strip()
    if raw in {"ı", "I", "l"}:
        norm_m = "1"
    elif raw in {"'", "*"}:
        norm_m = "*"
    else:
        norm_m = re.sub(r"\s+", "", raw)
    body = blk[m.end() :].lstrip(" .")
    return (norm_m or "*"), body.strip()


def _insert_callout_in_block(text: str, marker: str, fn_id: str, min_pos: int = 0) -> tuple[str, int] | None:
    sub = text[min_pos:]
    patterns: list[tuple[str, bool]] = []
    if marker.isdigit():
        sup_alt = f"(?:{re.escape(marker)}|[ıIl])" if marker == "1" else re.escape(marker)
        patterns.append((rf"<sup>\s*{sup_alt}\s*</sup>", False))
        if marker in _UNICODE_SUPS:
            patterns.append((re.escape(_UNICODE_SUPS[marker]), False))
        patterns.append((rf"(?<=[A-Za-zÇĞİÖŞÜçğıöşü.,;!?\"”’')\]\-])\s*{re.escape(marker)}(?![0-9A-Za-zÇĞİÖŞÜçğıöşü])", False))
        if marker == "1":
            patterns.append((r"(?<=[.,;!?\"”’')\]])\s*ı(?=\s|$|[.,;!?\"”’')])", False))
            patterns.append((r"(?<=[aeıioöuüAEIİOÖUÜ]|'nın|'nin|'nun|'nün|'dan|'den|'tan|'ten)ı(?=\b|\s|[.,;!?\"”’')])", False))
    else:
        patterns.append((r"<sup>\s*(?:\*|[•†‡§.\s]+)\s*</sup>", False))
        patterns.append((r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü.,;!?\"”’')\]])(?:\*|·)(?=\s|$|[.,;!?\"”’')])", True))

    for pat, check_emph in patterns:
        for m in re.finditer(pat, sub):
            abs_s, abs_e = min_pos + m.start(), min_pos + m.end()
            if text[max(0, abs_s - 3) : abs_s].endswith("[^"):
                continue
            if check_emph and m.group(0) == "*":
                line_start = text.rfind("\n", 0, abs_s) + 1
                if text[line_start:abs_s].count("*") % 2 == 1:
                    continue
            prev_word = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", text[max(0, abs_s - 15) : abs_s])
            if marker.isdigit() and abs_s > 0 and text[abs_s - 1].isspace() and prev_word:
                if prev_word[-1].replace("İ", "i").lower() in _SKIP_NUM_WORDS:
                    continue
            new_text = f"{text[:abs_s]}[^{fn_id}]{text[abs_e:]}"
            return new_text, abs_s + len(f"[^{fn_id}]")
    return None


def _zone_covered_by_existing(fn_n: str, existing_norms: list[str]) -> bool:
    if not existing_norms or not fn_n:
        return False
    for efn in existing_norms:
        if not efn:
            continue
        if fn_n in efn or (len(efn) >= 10 and efn in fn_n):
            return True
        if abs(len(fn_n) - len(efn)) <= max(len(fn_n), len(efn)) * 0.45:
            if SequenceMatcher(None, fn_n, efn).ratio() >= 0.72:
                return True
    return False


def normalize_footnotes_and_breaks(markdown_text: str, pdf_path: Path | str | None = None) -> str:
    """Lift interleaved footnotes to Markdown footnote defs and rejoin split prose paragraphs."""
    fences: dict[str, str] = {}

    def _stash_fence(m: re.Match) -> str:
        key = f"\x00FENCE{len(fences)}\x00"
        fences[key] = m.group(0)
        return key

    masked = _FENCE_MASK.sub(_stash_fence, markdown_text)
    raw_blocks = [b.strip() for b in re.split(r"\n{2,}", masked.strip()) if b.strip()]
    if not raw_blocks:
        return markdown_text

    existing_fn_norms: list[str] = []
    max_fn_num = 0
    for m in re.finditer(r"(?m)^\[\^([^\]]+)\]:\s*(.+)$", masked):
        existing_fn_norms.append(_norm_nodig(m.group(2)))
    for m in re.finditer(r"\[\^fn-(\d+)\]", masked):
        max_fn_num = max(max_fn_num, int(m.group(1)))

    # Split internal <sup>-prefixed footnote lines glued inside a multi-line prose block
    blocks: list[str] = []
    for blk in raw_blocks:
        if _is_special_block(blk) or "\n" not in blk:
            blocks.append(blk)
            continue
        cur: list[str] = []
        for ln in blk.splitlines():
            if cur and _SUP_FN_LINE.match(ln):
                blocks.append("\n".join(cur).strip())
                cur = [ln]
            else:
                cur.append(ln)
        if cur:
            blocks.append("\n".join(cur).strip())

    manzum = _detect_manzum_blocks(blocks)
    resolved_pdf = Path(pdf_path) if pdf_path and Path(pdf_path).is_file() else None
    page_infos = _scan_pdf_layout(resolved_pdf) if resolved_pdf else []
    fn_zones = [
        p
        for p in page_infos
        if p.fn_text and not _zone_covered_by_existing(_norm_nodig(p.fn_text), existing_fn_norms)
    ]
    aligned = _align_pdf_fn_zones(fn_zones, blocks, manzum)

    # Mark footnote spans from PDF alignment + explicit Markdown <sup> fallback
    block_to_zone: dict[int, tuple[int, int, bool]] = {}
    for _zi, (s_i, e_i, zinfo) in aligned.items():
        for k in range(s_i, e_i + 1):
            block_to_zone[k] = (s_i, e_i, zinfo.is_cont)

    for idx, blk in enumerate(blocks):
        if idx not in block_to_zone and idx not in manzum and not _is_special_block(blk):
            if _SUP_FN_LINE.match(blk):
                block_to_zone[idx] = (idx, idx, False)

    # Build page-turn boundary pairs from PDF body_last -> next page body_first
    turn_pairs: list[tuple[str, str]] = []
    for a_info, b_info in zip(page_infos, page_infos[1:]):
        if a_info.body_last and b_info.body_first:
            nl, nf = _norm(a_info.body_last[-36:]), _norm(b_info.body_first[:36])
            if len(nl) >= 6 and len(nf) >= 6:
                turn_pairs.append((nl, nf))

    is_fn_block = set(block_to_zone.keys())
    notes: list[dict] = []
    idx = 0
    while idx < len(blocks):
        if idx not in block_to_zone:
            idx += 1
            continue
        s_i, e_i, is_cont = block_to_zone[idx]
        span_idx = s_i
        page_seq = 0
        while span_idx <= e_i:
            marker, clean_body = _strip_fn_prefix(blocks[span_idx])
            if (
                span_idx == s_i
                and notes
                and (is_cont or (marker is None and not _has_terminal_punct(notes[-1]["text"])))
            ):
                notes[-1]["text"] = _join_prose_pair(notes[-1]["text"], clean_body)
            elif span_idx > s_i and marker is None and notes and not _has_terminal_punct(notes[-1]["text"]):
                notes[-1]["text"] = _join_prose_pair(notes[-1]["text"], clean_body)
            else:
                if marker is not None and marker.isdigit():
                    page_seq = int(marker)
                elif marker is None:
                    page_seq += 1
                    marker = str(page_seq)
                notes.append(
                    {
                        "anchor": s_i,
                        "marker": marker,
                        "text": clean_body,
                        "id": f"fn-{max_fn_num + len(notes) + 1}",
                    }
                )
            span_idx += 1
        idx = e_i + 1

    # Link callouts into preceding body blocks per anchor group
    notes_by_anchor: dict[int, list[dict]] = defaultdict(list)
    for note in notes:
        notes_by_anchor[note["anchor"]].append(note)

    attached_defs: dict[int, list[str]] = defaultdict(list)
    prev_anchor = 0
    for anchor_i in sorted(notes_by_anchor):
        group = notes_by_anchor[anchor_i]
        # Never cross backward over an anchored chapter boundary
        chap_floor = max(
            (k + 1 for k in range(prev_anchor, anchor_i) if blocks[k].lstrip().startswith("#") and _ANCHOR_RE.search(blocks[k])),
            default=max(0, prev_anchor - 2),
        )
        cand_idxs = [
            k
            for k in range(chap_floor, anchor_i)
            if k not in is_fn_block
            and not blocks[k].lstrip().startswith(("|", "![", "\x00FENCE"))
            and not _FN_DEF_LINE.match(blocks[k])
        ][-10:]
        prose_cands = [k for k in cand_idxs if not blocks[k].lstrip().startswith("#")]
        search_cands = prose_cands or cand_idxs
        cur_cand_pos = 0
        cur_char_pos = 0
        for note in group:
            placed_blk = None
            for ci in range(cur_cand_pos, len(search_cands)):
                b_idx = search_cands[ci]
                min_p = cur_char_pos if ci == cur_cand_pos else 0
                res = _insert_callout_in_block(blocks[b_idx], note["marker"], note["id"], min_p)
                if res is not None:
                    blocks[b_idx], next_pos = res
                    cur_cand_pos = ci
                    cur_char_pos = next_pos
                    placed_blk = b_idx
                    break
            if placed_blk is None:
                for b_idx in reversed(search_cands):
                    res = _insert_callout_in_block(blocks[b_idx], note["marker"], note["id"], 0)
                    if res is not None:
                        blocks[b_idx], _ = res
                        placed_blk = b_idx
                        break
            if placed_blk is None:
                fallback_idx = (
                    prose_cands[-1]
                    if prose_cands
                    else next((k for k in range(anchor_i + 1, len(blocks)) if k not in is_fn_block and not _is_special_block(blocks[k])), None)
                )
                if fallback_idx is not None:
                    blocks[fallback_idx] = f"{blocks[fallback_idx].rstrip()}[^{note['id']}]"
                    placed_blk = fallback_idx
            if placed_blk is not None:
                attached_defs[placed_blk].append(f"[^{note['id']}]: {note['text']}")
        prev_anchor = anchor_i

    # Build surviving body sequence and rejoin split prose paragraphs
    surviving: list[tuple[int, str, bool]] = []
    lifted_since_last = False
    for i, blk in enumerate(blocks):
        if i in is_fn_block:
            lifted_since_last = True
            continue
        surviving.append((i, blk, lifted_since_last))
        lifted_since_last = False

    merged_blocks: list[str] = []
    merged_defs: list[list[str]] = []
    merged_manzum: list[bool] = []
    turn_cursor = 0

    for orig_i, blk, had_lifted_fn in surviving:
        in_mz = orig_i in manzum
        defs_here = list(attached_defs.get(orig_i, ()))
        if (
            merged_blocks
            and not in_mz
            and not merged_manzum[-1]
            and not _is_special_block(merged_blocks[-1])
            and not _is_special_block(blk)
        ):
            prev_b = merged_blocks[-1]
            starts_dialogue = bool(re.match(r"^[\"“'‘«—–·]", blk.lstrip()))
            hyph_split = bool(_WORD_HYPHEN_END.search(prev_b)) and bool(re.match(r"^[A-Za-zÇĞİÖŞÜçğıöşü]", blk.lstrip()))
            lacks_term = not _has_terminal_punct(prev_b)
            fn_bridge = had_lifted_fn and (hyph_split or (lacks_term and not starts_dialogue))

            pdf_turn = False
            if not fn_bridge and not hyph_split and lacks_term and not starts_dialogue and turn_pairs:
                p_tail = _norm(prev_b[-45:])
                c_head = _norm(blk[:45])
                for ti in range(max(0, turn_cursor - 2), min(len(turn_pairs), turn_cursor + 25)):
                    tl, tf = turn_pairs[ti]
                    if (tl[-12:] in p_tail or p_tail[-12:] in tl) and (tf[:12] in c_head or c_head[:12] in tf):
                        pdf_turn = True
                        turn_cursor = ti + 1
                        break

            if hyph_split or fn_bridge or pdf_turn:
                merged_blocks[-1] = _join_prose_pair(prev_b, blk)
                merged_defs[-1].extend(defs_here)
                continue

        merged_blocks.append(blk)
        merged_defs.append(defs_here)
        merged_manzum.append(in_mz)

    out_blocks: list[str] = []
    for blk, dlist in zip(merged_blocks, merged_defs):
        out_blocks.append(blk)
        out_blocks.extend(dlist)

    result = "\n\n".join(out_blocks) + ("\n" if markdown_text.endswith("\n") else "")
    for key, orig in fences.items():
        result = result.replace(key, orig)
    return result
# @end PDF-MD-FOOT


# @start MD-EPUB-HEAD
_DIALOGUE_HEAD = re.compile(r"^[\"“'‘«].*[\"”'’»!?….,]$")
_BKZ_GLOSSARY = re.compile(r"\bBkz\.\s+\S")


def _is_all_upper(text: str) -> bool:
    clean = re.sub(r"[*_`]+", "", text).strip()
    letters = [c for c in clean if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def normalize_heading_hierarchy(markdown_text: str, doc_hint: str = "") -> str:
    """Normalize chapter headings to h1, subsection headings to h2/h3, strip running headers, and isolate back-cover blurbs."""
    fences: dict[str, str] = {}

    def _stash(m: re.Match) -> str:
        key = f"\x00HFENCE{len(fences)}\x00"
        fences[key] = m.group(0)
        return key

    masked = _FENCE_MASK.sub(_stash, markdown_text)
    lines = masked.splitlines()
    has_anchors = any(_ANCHOR_RE.search(ln) for ln in lines)
    first_anchor = next((i for i, ln in enumerate(lines) if _ANCHOR_RE.search(ln)), 0)

    # Collect frontmatter/book title slugs before first_anchor to detect trailing back-cover blurbs
    book_keys = {
        _fuzzy_key(hm.group(2))
        for ln in lines[:first_anchor]
        if (hm := _HEADING.match(ln)) and len(_fuzzy_key(hm.group(2))) >= 4
    }
    if doc_hint:
        for tok in re.split(r"[\s_\-]+", Path(doc_hint).stem):
            if len(_fuzzy_key(tok)) >= 4:
                book_keys.add(_fuzzy_key(tok))

    # Tag trailing unanchored `# <BookTitle>` near EOF with `{: #arka-kapak}`
    if has_anchors and book_keys:
        nonempty_tail = [idx for idx in range(len(lines) - 1, first_anchor, -1) if lines[idx].strip()][:30]
        for idx in reversed(nonempty_tail):
            if (hm := _HEADING.match(lines[idx])) and len(hm.group(1)) == 1 and not _ANCHOR_RE.search(hm.group(2)):
                hk = _fuzzy_key(hm.group(2))
                if hk in book_keys or any(len(bk) >= 5 and hk == bk for bk in book_keys):
                    lines[idx] = f"# {hm.group(2).strip()} {{: #arka-kapak}}"
                    break

    # Pass 1: promote anchored chapter headings to `#` (preserving `# X. Bölüm` + `## Subtitle`), demote dialogue & Bkz. lines
    paired_subs: set[int] = set()
    for i in range(first_anchor, len(lines)):
        m = _HEADING.match(lines[i])
        if not m:
            continue
        rest = m.group(2).strip()
        if _ANCHOR_RE.search(rest):
            lines[i] = f"# {rest}"
            clean_t = _ANCHOR_RE.sub("", rest).strip()
            if _BOLUM_RE.match(clean_t):
                for j in range(i + 1, min(len(lines), i + 5)):
                    if not lines[j].strip():
                        continue
                    if sm := _HEADING.match(lines[j]):
                        if not _ANCHOR_RE.search(sm.group(2)):
                            lines[j] = f"## {sm.group(2).strip()}"
                            paired_subs.add(j)
                    break
        else:
            plain = re.sub(r"[*_`]+", "", rest).strip()
            if _DIALOGUE_HEAD.match(plain):
                lines[i] = rest
            elif _BKZ_GLOSSARY.search(plain):
                near_list = any(
                    lines[n].lstrip().startswith("- ")
                    for n in range(max(first_anchor, i - 4), min(len(lines), i + 5))
                    if n != i and lines[n].strip()
                )
                lines[i] = f"- {plain}" if near_list else plain

    # Pass 2: within each chapter, drop running headers and normalize unanchored subheadings to `##` (or `###` under parent groups)
    chap_starts = [
        i
        for i in range(first_anchor, len(lines))
        if (_ANCHOR_RE.search(lines[i]) if has_anchors else lines[i].startswith("# "))
    ]
    to_drop: set[int] = set()
    for pos, cs in enumerate(chap_starts):
        ce = chap_starts[pos + 1] if pos + 1 < len(chap_starts) else len(lines)
        chap_hm = _HEADING.match(lines[cs])
        chap_title_line = lines[cs]
        if cs + 1 < ce and ( cs + 1 in paired_subs ):
            chap_title_line = lines[cs + 1]
        chap_key = _fuzzy_key(_HEADING.match(chap_title_line).group(2)) if _HEADING.match(chap_title_line) else (
            _fuzzy_key(chap_hm.group(2)) if chap_hm else ""
        )

        sub_idxs: list[int] = []
        for k in range(cs + 1, ce):
            if k in paired_subs:
                continue
            hm = _HEADING.match(lines[k])
            if not hm or _ANCHOR_RE.search(hm.group(2)):
                continue
            sk = _fuzzy_key(hm.group(2))
            if len(chap_key) >= 10 and len(sk) >= 10 and SequenceMatcher(None, sk, chap_key).ratio() >= 0.85:
                to_drop.add(k)
                continue
            sub_idxs.append(k)

        if not sub_idxs:
            continue

        # Detect back-to-back category + subcategory heading pairs (no prose between them)
        parent_idxs: set[int] = set()
        child_idxs: set[int] = set()
        for a, b in zip(sub_idxs, sub_idxs[1:]):
            if not any(lines[x].strip() for x in range(a + 1, b)):
                parent_idxs.add(a)
                child_idxs.add(b)

        in_cat = False
        cat_all_caps = False
        for k in sub_idxs:
            txt = _HEADING.match(lines[k]).group(2).strip()
            if k in parent_idxs:
                lines[k] = f"## {txt}"
                in_cat = True
                cat_all_caps = _is_all_upper(txt)
            elif k in child_idxs:
                lines[k] = f"### {txt}"
            elif in_cat and cat_all_caps and _is_all_upper(txt):
                lines[k] = f"## {txt}"
            elif in_cat:
                lines[k] = f"### {txt}"
            else:
                lines[k] = f"## {txt}"

    kept = [ln for idx, ln in enumerate(lines) if idx not in to_drop]
    out = "\n".join(kept) + ("\n" if markdown_text.endswith("\n") else "")
    for key, orig in fences.items():
        out = out.replace(key, orig)
    return out
# @end MD-EPUB-HEAD


# @start MD-EPUB-FOOT
_FN_DIV_RE = re.compile(r'<div\s+class="footnote">\s*<hr\s*/?>\s*<ol>([\s\S]*?)</ol>\s*</div>')


def _ncname_id(raw_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", raw_id).strip("-")
    return cleaned if cleaned and (cleaned[0].isalpha() or cleaned[0] == "_") else f"fn-{cleaned or '1'}"


def upgrade_footnotes_to_epub3(html_text: str) -> str:
    """Convert python-markdown footnote HTML into EPUB 3 noteref links and <aside epub:type='footnote'> blocks."""
    if 'class="footnote"' not in html_text and 'class="footnote-ref"' not in html_text:
        return html_text

    def _repl_ref(m: re.Match) -> str:
        ref_id = _ncname_id(m.group(1))
        fn_id = _ncname_id(m.group(2))
        label = m.group(3)
        return (
            f'<a class="footnote-ref" epub:type="noteref" role="doc-noteref" '
            f'id="{ref_id}" href="#{fn_id}"><sup>{label}</sup></a>'
        )

    html_text = re.sub(
        r'<sup\s+id="([^"]+)">\s*<a\s+class="footnote-ref"\s+href="#([^"]+)">([^<]+)</a>\s*</sup>',
        _repl_ref,
        html_text,
    )

    def _repl_div(m: re.Match) -> str:
        parser = lxml_html.HTMLParser(encoding="utf-8")
        ol_el = lxml_html.fragment_fromstring(f"<ol>{m.group(1)}</ol>", parser=parser)
        asides: list[str] = []
        for idx, li in enumerate(ol_el.findall("li"), 1):
            fn_id = _ncname_id(li.get("id") or f"fn-{idx}")
            back_href = ""
            for a_el in li.xpath('.//a[contains(@class, "footnote-backref")]'):
                raw_href = (a_el.get("href") or "").lstrip("#")
                if raw_href:
                    clean_href = f"#{_ncname_id(raw_href)}"
                    a_el.set("href", clean_href)
                    a_el.set("role", "doc-backlink")
                    if not back_href:
                        back_href = clean_href
            children = list(li)
            if not children or children[0].tag != "p":
                p0 = etree.Element("p")
                p0.text = li.text
                li.text = None
                children.insert(0, p0)
            else:
                p0 = children[0]
            num_a = etree.Element("a", {"class": "footnote-num", "href": back_href or f"#{fn_id}"})
            num_a.text = f"{idx}."
            num_a.tail = " " + (p0.text or "")
            p0.text = None
            p0.insert(0, num_a)
            inner_xml = "".join(etree.tostring(ch, encoding="unicode", method="xml") for ch in children)
            asides.append(f'<aside class="footnote" epub:type="footnote" id="{xml_escape(fn_id)}">{inner_xml}</aside>')
        return '<section class="footnotes" epub:type="footnotes" role="doc-endnotes"><hr/>' + "".join(asides) + "</section>"

    return _FN_DIV_RE.sub(_repl_div, html_text)
# @end MD-EPUB-FOOT
```

Modify `modules/contents_toc.py`:
1. In `_clean_inline` (`modules/contents_toc.py:94-106`), strip Markdown footnote refs `\[\^[^\]]+\]` before cleaning inline text so TOC labels never include `[^fn-1]`:
```python
def _clean_inline(text: str) -> str:
    text = re.sub(r"\[\^[^\]]+\]", "", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<sup>\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)\s*</sup>", r"\1", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`·\\]+", "", text)
    text = re.sub(r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü])/(?=[A-Za-zÇĞİÖŞÜçğıöşü])", "l", text)
    text = re.sub(r"\s*'\s*", "'", text)
    text = re.sub(r"\bİI(?=[a-zçğıöşü])", "İ", text)
    return re.sub(r"\s+", " ", text).strip(" -–·.")
```
2. In `link_contents` (`modules/contents_toc.py:629-640`), preserve any inline `[^fn-...]` callouts attached to body headings when writing reconciled titles:
```python
            if target.sub_line is not None and canon_bolum is not None:
                m1 = _HEADING.match(lines[target.anchor_line])
                h1_marks = "#" if m1 and len(m1.group(1)) <= 2 else (m1.group(1) if m1 else "#")
                fn1 = "".join(re.findall(r"\[\^[^\]]+\]", lines[target.anchor_line]))
                lines[target.anchor_line] = f"{h1_marks} {canon_bolum}{fn1} {{: #{anchor}}}"
                m2 = _HEADING.match(lines[target.sub_line])
                h2_marks = m2.group(1) if m2 else "##"
                fn2 = "".join(re.findall(r"\[\^[^\]]+\]", lines[target.sub_line]))
                lines[target.sub_line] = f"{h2_marks} {canon_main}{fn2}"
            else:
                mh = _HEADING.match(lines[target.anchor_line])
                h_marks = mh.group(1) if mh else "#"
                fn_h = "".join(re.findall(r"\[\^[^\]]+\]", lines[target.anchor_line]))
                lines[target.anchor_line] = f"{h_marks} {canon_main}{fn_h} {{: #{anchor}}}"
```

Modify `modules/pdf2md.py` inside `convert_pdf` (`modules/pdf2md.py:110-120`):
```python
        from modules.glyph_repair import repair_markdown
        from modules.md_sanitize import sanitize_marker_markdown
        from modules.structure_norm import normalize_footnotes_and_breaks

        full_text, repair = repair_markdown(input_path, rendered.markdown)
        # @start PDF-MD-CLEAN
        full_text = sanitize_marker_markdown(full_text)
        # @end PDF-MD-CLEAN
        # @start PDF-MD-FOOT
        full_text = normalize_footnotes_and_breaks(full_text, pdf_path=input_path)
        # @end PDF-MD-FOOT
```

- [ ] **Step 3: Run unit tests for `normalize_footnotes_and_breaks`, `normalize_heading_hierarchy`, and `upgrade_footnotes_to_epub3`**

Run:
```bash
. .venv/bin/activate && python -c "
from pathlib import Path
from modules.structure_norm import (
    normalize_footnotes_and_breaks,
    normalize_heading_hierarchy,
    upgrade_footnotes_to_epub3,
)

# 1. Footnote extraction + hyphen/callout move + *italic* vs word* + prose merge + idempotency
s = (
    '*italik* ve veneri* terimi ile Kraliyet sürüsünün değeri-1\n\n'
    '<sup>1</sup>Detaylar için Mitos cildine bakınız.\n\n'
    'Medikal bir terim. -ed.n.\n\n'
    'nin artmasını sağladı. Daha da kötüsü şimdi yengeç\n\n'
    '<sup>2</sup>Yunan seramik sanatında Hidra tasviri.\n\n'
    'de sudan fırlayıp başladı. 2\n'
)
# Note: without PDF, explicit <sup> notes + prose merge run; test explicit <sup>*</sup> too
s2 = s.replace('Medikal bir terim. -ed.n.', '<sup>*</sup>Medikal bir terim. -ed.n.')
out = normalize_footnotes_and_breaks(s2)
assert '*italik* ve veneri[^fn-2] terimi ile Kraliyet sürüsünün değerinin[^fn-1] artmasını sağladı.' in out, out
assert 'şimdi yengeç de sudan fırlayıp başladı.[^fn-3]' in out, out
assert normalize_footnotes_and_breaks(out) == out, 'Idempotency check failed'

# 2. Heading hierarchy normalization + running header strip + Bkz. demotion + #arka-kapak
h = (
    '# KAHRAMANLAR\n\n'
    '#### ODYSSEİA KURULUŞU {: #ch-001}\n\nMetin.\n\n'
    '## Odysseia Kurulusu\n\nDevam.\n\n'
    '# HERAKLES {: #ch-002}\n\n'
    '#### 1. NEMEA ASLANI\n\nA.\n\n'
    '# 3. KERYNEİA GEYİĞİ\n\nB.\n\n'
    '## 7. GİRİT BOĞASI\n\nC.\n\n'
    '## \"Bekle !\"\n\n'
    '- EROS Aşk.\n#### KADER TANRIÇALARI Bkz. Moiralar.\n- GAİA Toprak.\n\n'
    '# KARAKTERLER {: #ch-003}\n\n'
    '#### YUNANLAR\n# Troya Savaşından Önceki Nesiller\n\n- AİAKOS Kral.\n\n'
    '# GÖRSELLER {: #ch-004}\n\n'
    '#### BİRİNCİ KISIM\n\n1. Resim\n\n'
    '# İKİNCİ KISIM\n\n2. Resim\n\n'
    '# KAHRAMANLAR\n\nArka kapak yazısı.\n'
)
hout = normalize_heading_hierarchy(h)
assert '# ODYSSEİA KURULUŞU {: #ch-001}' in hout and '## Odysseia Kurulusu' not in hout, hout
assert '## 1. NEMEA ASLANI' in hout and '## 3. KERYNEİA GEYİĞİ' in hout and '## 7. GİRİT BOĞASI' in hout, hout
assert '\n\"Bekle !\"\n' in hout and '- KADER TANRIÇALARI Bkz. Moiralar.' in hout, hout
assert '## YUNANLAR\n### Troya Savaşından Önceki Nesiller' in hout, hout
assert '## BİRİNCİ KISIM' in hout and '## İKİNCİ KISIM' in hout and '# KAHRAMANLAR {: #arka-kapak}' in hout, hout

# 3. EPUB 3 footnote HTML upgrade (leaves $x < y$ math in body untouched)
raw_html = (
    '<p>Math $x < y$ and text<sup id=\"fnref:fn-1\"><a class=\"footnote-ref\" href=\"#fn:fn-1\">1</a></sup>.</p>'
    '<div class=\"footnote\"><hr /><ol><li id=\"fn:fn-1\">'
    '<p>Note <i>italic</i>.&#160;<a class=\"footnote-backref\" href=\"#fnref:fn-1\">&#8617;</a></p>'
    '</li></ol></div>'
)
uhtml = upgrade_footnotes_to_epub3(raw_html)
assert '$x < y$' in uhtml and 'epub:type=\"noteref\"' in uhtml and 'id=\"fnref-fn-1\"' in uhtml, uhtml
assert '<aside class=\"footnote\" epub:type=\"footnote\" id=\"fn-fn-1\">' in uhtml, uhtml
print('PASS')
"
```
Expected: `PASS`.

- [ ] **Step 4: Commit Task 1**

```bash
git add modules/structure_norm.py modules/contents_toc.py modules/pdf2md.py
git commit -m "Add PDF/Markdown footnote extraction, page-break prose joining, and heading normalization"
```

---

### Task 2: Wire Structural Normalization & EPUB 3 Footnotes into `modules/mark2epub.py`

**Files:**
- Modify: `modules/mark2epub.py:20-27,558-770,980-998`

**Interfaces:**
- Consumes: `resolve_pdf_path`, `normalize_footnotes_and_breaks`, `normalize_heading_hierarchy`, and `upgrade_footnotes_to_epub3` from `modules/structure_norm.py`.
- Produces: Updated `_ANCHOR_ATTR`, `_prepare_markdown`, `_markdown_slice_to_xhtml`, `render_markdown_spines`, `_norm_prose` / `verify_no_prose_duplication`, and `default_css_content` in `modules/mark2epub.py`.

- [ ] **Step 1: Run failing test for spine-scoped EPUB 3 footnotes, heading normalization, `#arka-kapak` split, and deduplication compatibility**

Run:
```bash
. .venv/bin/activate && python -c "
from modules.mark2epub import render_markdown_spines
from modules.xhtml_gate import find_xhtml_errors

md = (
    '# KİTAP\n\n# İçindekiler\n\n| Bir | 1 |\n| İki | 5 |\n\n'
    '#### Bir\n\n#### 1. Alt Başlık\n\nCümle başı\n\n<sup>1</sup>Birinci dipnot metni.\n\ndevam etti.1\n\n'
    '# İki\n\n## \"Merhaba!\"\n\nİkinci bölüm metni.\n\n# KİTAP\n\nArka kapak tanıtımı.\n'
)
spines, imgs, toc = render_markdown_spines('.', 'book.md', ['style.css'], content=md)
s_bir = next(sp.xhtml for sp in spines if 'Alt Başlık' in sp.xhtml)
s_iki = next(sp.xhtml for sp in spines if 'İkinci bölüm' in sp.xhtml)
s_back = next(sp.xhtml for sp in spines if 'Arka kapak tanıtımı.' in sp.xhtml)
assert s_back != s_iki
assert '<h1 id=\"ch-001\">Bir</h1>' in s_bir and '<h2>1. Alt Başlık</h2>' in s_bir
assert 'Cümle başı devam etti.' in s_bir and 'epub:type=\"noteref\"' in s_bir
assert '<aside class=\"footnote\"' in s_bir and 'Birinci dipnot metni.' in s_bir
assert 'Birinci dipnot metni.' not in s_iki and '<h2>\"Merhaba!\"' not in s_iki
assert all(find_xhtml_errors(sp.xhtml) == [] for sp in spines)
print('PASS')
"
```
Expected: FAIL (`s_back == s_iki`, `<h4>` headings remain unnormalized, and footnotes remain inline `<p><sup>1</sup>...`).

- [ ] **Step 2: Update `modules/mark2epub.py`**

Apply exact updates in `modules/mark2epub.py`:

1. Import from `modules.structure_norm`:
```python
from modules.structure_norm import (
    normalize_footnotes_and_breaks,
    normalize_heading_hierarchy,
    resolve_pdf_path,
    upgrade_footnotes_to_epub3,
)
```
2. Update `_ANCHOR_ATTR` (`modules/mark2epub.py:559`):
```python
_ANCHOR_ATTR = re.compile(r"\{:\s*#(icindekiler|arka-kapak|ch-\d+)\s*\}")
```
3. In `_markdown_slice_to_xhtml` (`modules/mark2epub.py:617`):
```python
    raw_html = md_engine.convert(markdown_data)
    # @start MD-EPUB-FOOT
    raw_html = upgrade_footnotes_to_epub3(raw_html)
    # @end MD-EPUB-FOOT
    html_text = convert_math_to_mathml(raw_html)
```
4. In `_prepare_markdown` (`modules/mark2epub.py:631-646`):
```python
def _prepare_markdown(
    work_dir_path: Path, md_filename: str, content: Optional[str] = None
) -> tuple[str, list[str], list[TocEntry]]:
    raw_md = (work_dir_path / md_filename).read_text(encoding="utf-8") if content is None else content
    pdf_path = resolve_pdf_path(work_dir_path, md_filename)
    markdown_data = sanitize_marker_markdown(raw_md)
    # Link TOC first so images inside İçindekiler are stripped before image collection
    markdown_data, toc_entries = link_contents(markdown_data)
    markdown_data, chapter_images = process_markdown_for_images(markdown_data, work_dir_path)
    # @start MD-EPUB-HEAD
    markdown_data = normalize_heading_hierarchy(markdown_data, doc_hint=md_filename)
    # @end MD-EPUB-HEAD
    # @start PDF-MD-FOOT
    markdown_data = normalize_footnotes_and_breaks(markdown_data, pdf_path=pdf_path)
    # @end PDF-MD-FOOT
    return markdown_data, chapter_images, toc_entries
```
5. In `[MD-EPUB-DEDUP]` (`modules/mark2epub.py:665-690`):
```python
_FN_BADGE_STRIP = re.compile(r'<a\b[^>]*\bclass="[^"]*\bfootnote-ref\b[^"]*"[^>]*>[\s\S]*?</a>', re.I)
_MD_FN_STRIP = re.compile(r"\[\^[^\]]+\]")
_MD_LINK_UNWRAP = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_ATTR_STRIP = re.compile(r"\{:[^}]*\}")
_TAG_STRIP = re.compile(r"<[^>]+>")


def _norm_prose(text: str) -> str:
    s = _FN_BADGE_STRIP.sub("", text)
    s = _TAG_STRIP.sub("", s)
    s = _MD_FN_STRIP.sub("", s)
    s = _MD_LINK_UNWRAP.sub(r"\1", s)
    s = _MD_ATTR_STRIP.sub("", s)
    s = std_html.unescape(s).translate(str.maketrans("", "", "*_\\`"))
    return re.sub(r"\s+", " ", s).strip()
```
And in `verify_no_prose_duplication`:
```python
        for p_el in root.xpath(
            "//p[not(ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' footnote ') "
            "or contains(concat(' ', normalize-space(@class), ' '), ' footnotes ')])]"
        ):
            p_html = lxml_html.tostring(p_el, encoding="unicode")
            norm_p = _norm_prose(p_html)
            if len(norm_p) >= min_len:
                ep_counts[norm_p] += 1
```
6. In `render_markdown_spines` (`modules/mark2epub.py:717-755`):
```python
    fn_defs = {m.group(1): m.group(0) for m in _FN_DEF.finditer(markdown_data)}
    body_md = _FN_DEF.sub("", markdown_data) if fn_defs else markdown_data
    lines = body_md.splitlines()
```
And when attaching definitions per segment:
```python
        if fn_defs:
            used_fns = [fid for fid in re.findall(r"\[\^([^\]]+)\](?!:)", seg_md) if fid in fn_defs]
            seg_defs = [fn_defs[fid] for fid in dict.fromkeys(used_fns)]
            if seg_defs:
                seg_md = seg_md.rstrip() + "\n\n" + "\n\n".join(seg_defs) + "\n"
```
7. In `default_css_content` (`modules/mark2epub.py:980-994`), append EPUB 3 footnote styling:
```css
a.footnote-ref { text-decoration: none; font-size: 0.8em; vertical-align: super; line-height: 0; }
a.footnote-ref sup { font-size: 1em; vertical-align: baseline; }
section.footnotes { margin-top: 2em; font-size: 0.86em; line-height: 1.4; color: #333; }
section.footnotes hr { border: 0; border-top: 1px solid #bbb; width: 35%; margin: 0 0 0.8em 0; }
aside.footnote { margin: 0.45em 0; }
aside.footnote p { margin: 0.2em 0; }
a.footnote-num, a.footnote-backref { text-decoration: none; color: #555; }
```

- [ ] **Step 3: Re-run Task 2 test and verify idempotency with real PDF path**

Run:
```bash
. .venv/bin/activate && python -c "
from pathlib import Path
from modules.mark2epub import render_markdown_spines
from modules.structure_norm import normalize_footnotes_and_breaks
from modules.md_sanitize import sanitize_marker_markdown
from modules.xhtml_gate import find_xhtml_errors

md = (
    '# KİTAP\n\n# İçindekiler\n\n| Bir | 1 |\n| İki | 5 |\n\n'
    '#### Bir\n\n#### 1. Alt Başlık\n\nCümle başı\n\n<sup>1</sup>Birinci dipnot metni.\n\ndevam etti.1\n\n'
    '# İki\n\n## \"Merhaba!\"\n\nİkinci bölüm metni.\n\n# KİTAP\n\nArka kapak tanıtımı.\n'
)
spines, imgs, toc = render_markdown_spines('.', 'book.md', ['style.css'], content=md)
s_bir = next(sp.xhtml for sp in spines if 'Alt Başlık' in sp.xhtml)
s_iki = next(sp.xhtml for sp in spines if 'İkinci bölüm' in sp.xhtml)
s_back = next(sp.xhtml for sp in spines if 'Arka kapak tanıtımı.' in sp.xhtml)
assert s_back != s_iki
assert '<h1 id=\"ch-001\">Bir</h1>' in s_bir and '<h2>1. Alt Başlık</h2>' in s_bir
assert 'Cümle başı devam etti.' in s_bir and 'epub:type=\"noteref\"' in s_bir
assert '<aside class=\"footnote\"' in s_bir and 'Birinci dipnot metni.' in s_bir
assert 'Birinci dipnot metni.' not in s_iki and '<h2>\"Merhaba!\"' not in s_iki
assert all(find_xhtml_errors(sp.xhtml) == [] for sp in spines)

# Verify idempotency WITH real pdf_path on Odysseia (fastest PDF in data/)
ody_md_p = next(Path('data').glob('Homeros*/*.md'))
ody_pdf_p = next(Path('data').glob('Homeros*.pdf'))
clean_ody = sanitize_marker_markdown(ody_md_p.read_text())
pass1 = normalize_footnotes_and_breaks(clean_ody, pdf_path=ody_pdf_p)
pass2 = normalize_footnotes_and_breaks(pass1, pdf_path=ody_pdf_p)
assert pass1 == pass2, 'PDF idempotency check failed'
print('PASS')
"
```
Expected: `PASS`.

- [ ] **Step 4: Commit Task 2**

```bash
git add modules/mark2epub.py
git commit -m "Render spine-scoped EPUB 3 footnotes, normalize chapter headings, and split back-cover spines"
```

---

### Task 3: Register Anchors, Rebuild & Verify All 3 Local EPUBs (`Kahramanlar`, `Troya`, `Odysseia`), and Run Full Spec Verification

**Files:**
- Modify: `anchor_map/anchors.jsonl`
- Modify: `project_specs/pdf_md/SPEC.md`
- Modify: `project_specs/md_epub/SPEC.md`
- Modify: `project_specs/PLAN.md`
- Rebuild (untracked `data/`, never staged):
  - `data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.epub`
  - `data/Stephen Fry-Yunan Mitleri Cilt 3 Troya/Stephen Fry-Yunan Mitleri Cilt 3 Troya.epub`
  - `data/Homeros Odysseia İş Bankası Yayınları/Homeros Odysseia İş Bankası Yayınları.epub`

**Interfaces:**
- Consumes: Tasks 1–2 implementations and `anchor_map.server.anchor_set`.
- Produces: Updated `anchor_map/anchors.jsonl` (`PDF-MD-FOOT`, `MD-EPUB-HEAD`, `MD-EPUB-FOOT`), verified `SPEC.md` commands, and 3 rebuilt `.epub` archives verified on CPU.

- [ ] **Step 1: Register `PDF-MD-FOOT`, `MD-EPUB-HEAD`, and `MD-EPUB-FOOT` in `anchor_map/anchors.jsonl`**

Run:
```bash
. .venv/bin/activate && python -c "
from anchor_map.server import anchor_set
anchor_set(
    'PDF-MD-FOOT',
    'Extracts bottom-of-page footnotes via PDF rule geometry and Markdown markers, reassembles multi-page notes, links inline callouts, and rejoins page-break prose splits.',
    'modules/structure_norm.py',
    'project_specs/pdf_md/SPEC.md',
)
anchor_set(
    'MD-EPUB-HEAD',
    'Promotes TOC-anchored chapter headings to h1, strips running headers, demotes dialogue/Bkz. lines, normalizes subheadings to h2/h3, and isolates back-cover blurbs.',
    'modules/structure_norm.py',
    'project_specs/md_epub/SPEC.md',
)
anchor_set(
    'MD-EPUB-FOOT',
    'Upgrades rendered Markdown footnotes to EPUB 3 noteref links and aside footnote blocks with NCName IDs and sequential per-chapter numbering.',
    'modules/structure_norm.py',
    'project_specs/md_epub/SPEC.md',
)
print('ANCHORS_UPDATED')
"
```
Expected: `ANCHORS_UPDATED`.

- [ ] **Step 2: Rebuild all 3 EPUBs in `data/` on CPU and verify corpus-wide invariants (`Kahramanlar`, `Troya`, `Odysseia`)**

Run:
```bash
. .venv/bin/activate && python -c "
import zipfile
from pathlib import Path
from lxml import etree
import modules.mark2epub as m2e
from modules.xhtml_gate import find_xhtml_errors

# 1. Rebuild & verify Kahramanlar
k_dir = Path('data/dokumen.pub_kahramanlar')
k_epub = k_dir / 'dokumen.pub_kahramanlar.epub'
m2e.main([str(k_dir), str(k_epub)], interactive=False)
with zipfile.ZipFile(k_epub) as zf:
    s18 = zf.read('OPS/s00018-dokumen.pub_kahramanlar.xhtml').decode('utf-8')
    root = etree.fromstring(s18.encode('utf-8'))
    heads = [(el.tag.split('}')[-1], ''.join(el.itertext()).strip()) for el in root.iter() if el.tag.split('}')[-1] in ('h1','h2','h3','h4','h5','h6')]
    assert heads[0][0] == 'h1' and \"HERAKLES'İN İŞLERİ\" in heads[0][1], heads[0]
    assert len(heads) == 13 and all(lvl == 'h2' for lvl, _ in heads[1:]), heads
    asides = [el for el in root.iter() if el.tag.split('}')[-1] == 'aside']
    assert len(asides) >= 45, len(asides)
    aside_txt = ' '.join(''.join(a.itertext()) for a in asides)
    body_p_txt = ' '.join(
        ''.join(p.itertext())
        for p in root.iter('{http://www.w3.org/1999/xhtml}p')
        if not any(anc.tag.split('}')[-1] == 'aside' for anc in p.iterancestors())
    )
    for expected_fn in (
        'Alkmene ve Amphitryon da birbirlerinin kuzeni',
        'Boğaların, danaların, yabandomuzlarının',
        'kanaryadan türememekte olup',
    ):
        assert expected_fn in aside_txt and expected_fn not in body_p_txt, expected_fn
    assert 'şimdi yengeç de sudan fırlayıp saldırıya geçmişti' in body_p_txt
    assert 'Gözü kararmış ve hiddetli kahraman hıncını' in body_p_txt
    spines = sorted(n for n in zf.namelist() if n.startswith('OPS/s') and n.endswith('.xhtml'))
    assert 'id=\"arka-kapak\"' in zf.read(spines[-1]).decode('utf-8')
    for name in zf.namelist():
        if name.endswith(('.xhtml', '.xml', '.opf', '.ncx')):
            assert find_xhtml_errors(zf.read(name).decode('utf-8')) == [], name

# 2. Rebuild & verify Troya
t_dir = Path('data/Stephen Fry-Yunan Mitleri Cilt 3 Troya')
t_epub = t_dir / 'Stephen Fry-Yunan Mitleri Cilt 3 Troya.epub'
m2e.main([str(t_dir), str(t_epub)], interactive=False)
with zipfile.ZipFile(t_epub) as zf:
    t_spines = sorted(n for n in zf.namelist() if n.startswith('OPS/s') and n.endswith('.xhtml'))
    t_all = '\n'.join(zf.read(n).decode('utf-8') for n in t_spines)
    assert t_all.count('epub:type=\"footnote\"') >= 100
    assert 'id=\"arka-kapak\"' in zf.read(t_spines[-1]).decode('utf-8')
    assert '<h4>KADER TANRIÇALARI Bkz.' not in t_all and '<h2>KADER TANRIÇALARI Bkz.' not in t_all
    for name in zf.namelist():
        if name.endswith(('.xhtml', '.xml', '.opf', '.ncx')):
            assert find_xhtml_errors(zf.read(name).decode('utf-8')) == [], name

# 3. Rebuild & verify Odysseia (24 manzum verse books intact + Önsöz footnotes & running header fix)
o_dir = next(Path('data').glob('Homeros*'))
o_epub = o_dir / f'{o_dir.name}.epub'
m2e.main([str(o_dir), str(o_epub)], interactive=False)
with zipfile.ZipFile(o_epub) as zf:
    o_spines = sorted(n for n in zf.namelist() if n.startswith('OPS/s') and n.endswith('.xhtml'))
    verse_books = [n for n in o_spines if 'class=\"verse-ln\"' in zf.read(n).decode('utf-8')]
    assert len(verse_books) == 24, len(verse_books)
    o_all = '\n'.join(zf.read(n).decode('utf-8') for n in o_spines)
    assert 'Odysseia\x27nm Kuruluşu</h2>' not in o_all
    assert '<h2>Aiolos\x27un Adası</h2>' in o_all
    for name in zf.namelist():
        if name.endswith(('.xhtml', '.xml', '.opf', '.ncx')):
            assert find_xhtml_errors(zf.read(name).decode('utf-8')) == [], name

print('ALL_3_BOOKS_PASS')
"
```
Expected: `ALL_3_BOOKS_PASS`.

- [ ] **Step 3: Execute every bash verification block in `project_specs/**/SPEC.md` plus `ruff` and `compileall`**

Run:
```bash
. .venv/bin/activate && ruff check --select E9,F modules/ main.py anchor_map/ && python -m compileall -q modules/ main.py anchor_map/ && python -c "
import re, subprocess
from pathlib import Path

blocks = []
for spec in sorted(Path('project_specs').glob('*/SPEC.md')):
    ver = spec.read_text().split('## 3. Verification', 1)[1]
    for m in re.finditer(r'\`\`\`bash\n(.*?)\n\`\`\`', ver, re.S):
        blocks.append((spec.as_posix(), m.group(1).strip()))

for spec_path, cmd in blocks:
    res = subprocess.run(['bash', '-lc', cmd], capture_output=True, text=True)
    assert res.returncode == 0 and 'PASS' in res.stdout, (spec_path, cmd, res.stdout, res.stderr)
print(f'ALL {len(blocks)} SPEC VERIFICATION BLOCKS PASSED')
"
```
Expected: `ALL 14 SPEC VERIFICATION BLOCKS PASSED`.

- [ ] **Step 4: Commit specs, plan, and anchor map**

```bash
git add anchor_map/anchors.jsonl project_specs/pdf_md/SPEC.md project_specs/md_epub/SPEC.md project_specs/PLAN.md
git commit -m "Update specs, plan, and anchors for footnote, page-break, and heading normalization"
```
