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
_MULTIPARA_FN_DEF = re.compile(r"(?m)^\[\^([^\]]+)\]:.*(?:\n+(?:[ \t]{2,}|\t).*)*")
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
    s = s.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^a-z]", "", s.translate(_FUZZY_TR))


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


# @start PDF-MD-FOOT-SCAN
def _scan_pdf_open(pdf: pdfium.PdfDocument) -> list[_PdfPageInfo]:
    raw_pages: list[tuple[int, float, float, str, str, str]] = []
    for pno in range(len(pdf)):
        page = None
        tp = None
        obj = None
        try:
            page = pdf[pno]
            w, h = page.get_size()
            n_obj = pdfium_c.FPDFPage_CountObjects(page)
            raw_rules: list[tuple[float, float, float, float]] = []
            img_strips: list[tuple[float, float, float, float, float]] = []
            for oi in range(n_obj):
                obj = pdfium_c.FPDFPage_GetObject(page, oi)
                t = pdfium_c.FPDFPageObj_GetType(obj)
                if t in (2, 3):
                    left, bottom, right, top = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                    if pdfium_c.FPDFPageObj_GetBounds(
                        obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top)
                    ):
                        bw, bh = right.value - left.value, top.value - bottom.value
                        if bw > 35 and bh < 6 and h * 0.06 < bottom.value < h * 0.72:
                            raw_rules.append((left.value, bottom.value, right.value, top.value))
                        elif t == 3 and bw > w * 0.75 and 6 <= bh < 45 and h * 0.025 < bottom.value < h * 0.28:
                            img_strips.append((left.value, bottom.value, right.value, top.value, bw))
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
            elif not merged and len(img_strips) == 1:
                rule = img_strips[0]

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
            raw_pages.append(
                (
                    pno + 1,
                    rw,
                    w,
                    body_lns[0] if body_lns else "",
                    body_lns[-1] if body_lns else "",
                    " ".join(fn_lns),
                )
            )
        finally:
            obj = None
            tp = None
            page = None

    has_narrow = any(0 < rw <= w * 0.55 for _, rw, w, _, _, fn_t in raw_pages if fn_t)
    return [
        _PdfPageInfo(pno, has_narrow and rw > w * 0.55, b_first, b_last, fn_t)
        for pno, rw, w, b_first, b_last, fn_t in raw_pages
    ]


def _scan_pdf_layout(pdf_path: Path) -> list[_PdfPageInfo]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return _scan_pdf_open(pdf)
    finally:
        gc.collect()
        pdf.close()
# @end PDF-MD-FOOT-SCAN


def _is_special_block(blk: str) -> bool:
    s = blk.lstrip()
    return (
        not s
        or s.startswith(("#", "|", "![", ">", "- ", "* ", "+ ", "\x00FENCE", "\x00FNDEF", "<p class=\"verse\""))
        or bool(_FN_DEF_LINE.match(s))
        or bool(re.fullmatch(r"[-*_\\\s]{3,}", s))
    )


def _is_non_fn_zone_block(blk: str) -> bool:
    s = blk.lstrip()
    return (
        not s
        or s.startswith(("#", "|", "![", ">", "\x00FENCE", "\x00FNDEF", "<p class=\"verse\""))
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


# @start PDF-MD-FOOT-ALIGN
def _align_pdf_fn_zones(
    zones: list[_PdfPageInfo], blocks: list[str], manzum: set[int]
) -> dict[int, tuple[int, int, _PdfPageInfo]]:
    if not zones:
        return {}
    md_norms = [_norm_nodig(b) for b in blocks]
    gram_idx: dict[str, list[int]] = defaultdict(list)
    for i, mn in enumerate(md_norms):
        if i in manzum or _is_non_fn_zone_block(blocks[i]):
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
                if idx not in manzum and not _is_non_fn_zone_block(blocks[idx]) and mn:
                    if fn_n == mn or (len(fn_n) >= 7 and fn_n in mn and len(mn) <= len(fn_n) * 2 + 15):
                        seeds.add(idx)
        spans: dict[tuple[int, int], float] = {}
        for idx in seeds:
            for s_i in range(max(0, idx - 3), idx + 1):
                if s_i in manzum or _is_non_fn_zone_block(blocks[s_i]):
                    continue
                acc = ""
                for e_i in range(s_i, min(len(blocks), s_i + 8)):
                    if e_i in manzum or _is_non_fn_zone_block(blocks[e_i]):
                        break
                    acc += md_norms[e_i]
                    if len(acc) > len(fn_n) * 1.45 + 35:
                        break
                    r = SequenceMatcher(None, fn_n, acc, autojunk=False).ratio()
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
# @end PDF-MD-FOOT-ALIGN


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
            patterns.append((r"(?<=[aeıioöuüAEIİOÖUÜ])ı(?=\b|\s|[.,;!?\"”’')])", False))
            patterns.append((r"(?<='nın|'nin|'nun|'nün|'dan|'den|'tan|'ten)ı(?=\b|\s|[.,;!?\"”’')])", False))
            patterns.append((r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü])\s+ı(?=\s|$|[.,;!?\"”’')])", False))
    else:
        patterns.append((r"<sup>\s*(?:\*|[•†‡§.\s]+)\s*</sup>", False))
        patterns.append((r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü.,;!?\"”’')\]])(?:\\\*|\*|·)(?=\s|$|[.,;!?\"”’')])", True))

    existing_spans = [
        (m.start(), m.end())
        for m in re.finditer(r"\[\^[^\]]+\]|`[^`\n]+`|\]\([^)\n]*\)", text)
    ]
    for pat, check_emph in patterns:
        for m in re.finditer(pat, sub):
            abs_s, abs_e = min_pos + m.start(), min_pos + m.end()
            if any(es <= abs_s < ee or es < abs_e <= ee for es, ee in existing_spans):
                continue
            if check_emph and m.group(0) == "*":
                line_start = text.rfind("\n", 0, abs_s) + 1
                if len(re.findall(r"(?<!\\)\*", text[line_start:abs_s])) % 2 == 1:
                    continue
            prev_word = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", text[max(0, abs_s - 15) : abs_s])
            if marker.isdigit() and abs_s > 0 and text[abs_s - 1].isspace() and prev_word:
                if prev_word[-1].replace("İ", "i").lower() in _SKIP_NUM_WORDS:
                    continue
            new_text = f"{text[:abs_s]}[^{fn_id}]{text[abs_e:]}"
            return new_text, abs_s + len(f"[^{fn_id}]")
    return None


def _zone_covered_by_existing(
    fn_n: str, existing_norms: list[str], existing_grams: set[str] | None = None
) -> bool:
    if not existing_norms or not fn_n:
        return False
    for efn in existing_norms:
        if not efn:
            continue
        if fn_n in efn or (len(efn) >= 10 and efn in fn_n):
            return True
        if abs(len(fn_n) - len(efn)) <= max(len(fn_n), len(efn)) * 0.45:
            if SequenceMatcher(None, fn_n, efn, autojunk=False).ratio() >= 0.72:
                return True
    if len(fn_n) >= 12:
        grams = existing_grams if existing_grams is not None else {
            efn[i : i + 6] for efn in existing_norms for i in range(0, max(0, len(efn) - 5))
        }
        probes = [fn_n[i : i + 6] for i in range(0, len(fn_n) - 5, 2)]
        if probes and sum(1 for g in probes if g in grams) / len(probes) >= 0.65:
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
    existing_fn_norms: list[str] = []
    max_fn_num = 0
    for m in _MULTIPARA_FN_DEF.finditer(masked):
        existing_fn_norms.append(_norm_nodig(m.group(0).split(":", 1)[1]))
    for m in re.finditer(r"\[\^fn-(\d+)\]", masked):
        max_fn_num = max(max_fn_num, int(m.group(1)))

    def _stash_fndef(m: re.Match) -> str:
        key = f"\x00FNDEF{len(fences)}\x00"
        fences[key] = m.group(0)
        return f"\n\n{key}\n\n"

    masked = _MULTIPARA_FN_DEF.sub(_stash_fndef, masked)
    raw_blocks = [b.strip() for b in re.split(r"\n{2,}", masked.strip()) if b.strip()]
    if not raw_blocks:
        return markdown_text

    existing_grams = {
        efn[i : i + 6] for efn in existing_fn_norms for i in range(0, max(0, len(efn) - 5))
    }

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
        if p.fn_text and not _zone_covered_by_existing(_norm_nodig(p.fn_text), existing_fn_norms, existing_grams)
    ]
    aligned = _align_pdf_fn_zones(fn_zones, blocks, manzum)

    # Mark footnote spans from PDF alignment + explicit Markdown <sup> fallback
    block_to_zone: dict[int, tuple[int, int, bool, int | None]] = {}
    for _zi, (s_i, e_i, zinfo) in aligned.items():
        for k in range(s_i, e_i + 1):
            block_to_zone[k] = (s_i, e_i, zinfo.is_cont, zinfo.pno)

    fb_i = 0
    while fb_i < len(blocks):
        if fb_i not in block_to_zone and fb_i not in manzum and not _is_special_block(blocks[fb_i]) and _SUP_FN_LINE.match(blocks[fb_i]):
            fb_end = fb_i
            while (
                fb_end + 1 < len(blocks)
                and (fb_end + 1) not in block_to_zone
                and (fb_end + 1) not in manzum
                and not _is_special_block(blocks[fb_end + 1])
                and _SUP_FN_LINE.match(blocks[fb_end + 1])
            ):
                fb_end += 1
            for k in range(fb_i, fb_end + 1):
                block_to_zone[k] = (fb_i, fb_end, False, None)
            fb_i = fb_end + 1
        else:
            fb_i += 1

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
        s_i, e_i, is_cont, zone_pno = block_to_zone[idx]
        span_idx = s_i
        page_seq = 0
        sub_pos = 0
        while span_idx <= e_i:
            raw_blk = blocks[span_idx]
            sub_items = (
                [re.sub(r"^-\s+", "", it).strip() for it in re.split(r"\n(?=-\s+)", raw_blk.strip()) if it.strip()]
                if raw_blk.lstrip().startswith("- ")
                else [raw_blk]
            )
            for item_txt in sub_items:
                marker, clean_body = _strip_fn_prefix(item_txt)
                same_chap = bool(notes) and not any(
                    blocks[k].lstrip().startswith("#") for k in range(notes[-1]["anchor"], s_i + 1)
                )
                near_page = bool(notes) and (
                    notes[-1]["pno"] is None
                    or zone_pno is None
                    or 0 < zone_pno - notes[-1]["pno"] <= 2
                )
                if (
                    sub_pos == 0
                    and notes
                    and marker is None
                    and same_chap
                    and near_page
                    and (is_cont or not _has_terminal_punct(notes[-1]["text"]))
                ):
                    notes[-1]["text"] = _join_prose_pair(notes[-1]["text"], clean_body)
                    notes[-1]["pno"] = zone_pno or notes[-1]["pno"]
                elif sub_pos > 0 and marker is None and notes and not _has_terminal_punct(notes[-1]["text"]):
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
                            "pno": zone_pno,
                        }
                    )
                sub_pos += 1
            span_idx += 1
        idx = e_i + 1

    # Link callouts into preceding body blocks per anchor group
    notes_by_anchor: dict[int, list[dict]] = defaultdict(list)
    for note in notes:
        notes_by_anchor[note["anchor"]].append(note)

    attached_defs: dict[int, list[str]] = defaultdict(list)
    prev_end = -1
    for anchor_i in sorted(notes_by_anchor):
        group = notes_by_anchor[anchor_i]
        zone_end = block_to_zone[anchor_i][1]
        chap_floor = max(
            (k + 1 for k in range(prev_end + 1, anchor_i) if blocks[k].lstrip().startswith("#") and _ANCHOR_RE.search(blocks[k])),
            default=prev_end + 1,
        )
        cand_idxs = [
            k
            for k in range(chap_floor, anchor_i)
            if k not in is_fn_block
            and not blocks[k].lstrip().startswith(("|", "![", "\x00FENCE", "\x00FNDEF"))
            and not _FN_DEF_LINE.match(blocks[k])
        ][-12:]
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
            allow_post_lookahead = (
                not prose_cands
                or not _has_terminal_punct(blocks[prose_cands[-1]])
                or bool(_WORD_HYPHEN_END.search(blocks[prose_cands[-1]].rstrip()))
            )
            if placed_blk is None and allow_post_lookahead and zone_end + 1 < len(blocks):
                nxt_k = zone_end + 1
                if nxt_k not in is_fn_block and not _is_special_block(blocks[nxt_k]):
                    res = _insert_callout_in_block(blocks[nxt_k], note["marker"], note["id"], 0)
                    if res is not None:
                        blocks[nxt_k], _ = res
                        placed_blk = nxt_k
            if placed_blk is None:
                fallback_idx = (
                    prose_cands[-1]
                    if prose_cands
                    else next((k for k in range(zone_end + 1, len(blocks)) if k not in is_fn_block and not _is_special_block(blocks[k])), None)
                )
                if fallback_idx is not None:
                    blocks[fallback_idx] = f"{blocks[fallback_idx].rstrip()}[^{note['id']}]"
                    placed_blk = fallback_idx
            if placed_blk is not None:
                attached_defs[placed_blk].append(f"[^{note['id']}]: {note['text']}")
        prev_end = zone_end

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

    book_keys = {
        _fuzzy_key(hm.group(2))
        for ln in lines[:first_anchor]
        if (hm := _HEADING.match(ln)) and len(_fuzzy_key(hm.group(2))) >= 4
    }
    if doc_hint:
        for tok in re.split(r"[\s_\-]+", Path(doc_hint).stem):
            if len(_fuzzy_key(tok)) >= 4:
                book_keys.add(_fuzzy_key(tok))

    if has_anchors and book_keys:
        nonempty_tail = [idx for idx in range(len(lines) - 1, first_anchor, -1) if lines[idx].strip()][:30]
        for idx in reversed(nonempty_tail):
            if (hm := _HEADING.match(lines[idx])) and len(hm.group(1)) == 1 and not _ANCHOR_RE.search(hm.group(2)):
                hk = _fuzzy_key(hm.group(2))
                if hk in book_keys or any(len(bk) >= 5 and hk == bk for bk in book_keys):
                    lines[idx] = f"# {hm.group(2).strip()} {{: #arka-kapak}}"
                    break

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

    chap_starts = [
        i
        for i in range(first_anchor, len(lines))
        if (_ANCHOR_RE.search(lines[i]) if has_anchors else lines[i].startswith("# "))
    ]
    to_drop: set[int] = set()
    for pos, cs in enumerate(chap_starts):
        ce = chap_starts[pos + 1] if pos + 1 < len(chap_starts) else len(lines)
        chap_hm = _HEADING.match(lines[cs])
        chap_title_line = lines[cs + 1] if (cs + 1 < ce and (cs + 1) in paired_subs) else lines[cs]
        title_hm = _HEADING.match(chap_title_line)
        chap_key = _fuzzy_key(title_hm.group(2)) if title_hm else (_fuzzy_key(chap_hm.group(2)) if chap_hm else "")

        sub_idxs: list[int] = []
        for k in range(cs + 1, ce):
            if k in paired_subs:
                continue
            hm = _HEADING.match(lines[k])
            if not hm or _ANCHOR_RE.search(hm.group(2)):
                continue
            sk = _fuzzy_key(hm.group(2))
            if len(chap_key) >= 10 and len(sk) >= 10 and SequenceMatcher(None, sk, chap_key, autojunk=False).ratio() >= 0.85:
                to_drop.add(k)
                continue
            sub_idxs.append(k)

        if not sub_idxs:
            continue

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
