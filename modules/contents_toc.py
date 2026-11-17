from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import NamedTuple

_FUZZY = str.maketrans(
    {
        "ı": "i", "İ": "i", "I": "i", "l": "i", "1": "i", "0": "o",
        "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o", "ç": "c", "Ç": "c", "â": "a", "î": "i", "û": "u",
    }
)
_SLUG = str.maketrans(
    {
        "ı": "i", "İ": "i", "I": "i",
        "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o", "ç": "c", "Ç": "c", "â": "a", "î": "i", "û": "u",
    }
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_PAGE_PAIR = re.compile(r"([^,]+?)\s*,\s*(\d{1,4})(?=\s|$)")
_TOC_PAIR_LINE = re.compile(r"^(?:[^,\n]{2,80}?,\s*\d{1,4}\s*)+$")
_TABLE_SEP = re.compile(r"^\|[\s|:-]+\|$")
_BOLUM = re.compile(r"^([IVXLCDMivxlcdm1l\d.]*)\s*Bölüm\s*$", re.I)
_ROMAN_VALS = [("M", 1000), ("CM", 900), ("D", 500), ("CD", 400), ("C", 100), ("XC", 90),
               ("L", 50), ("XL", 40), ("X", 10), ("IX", 9), ("V", 5), ("IV", 4), ("I", 1)]
_VALID_ROMANS: dict[str, int] = {}


def _int_to_roman(n: int) -> str:
    out = []
    for sym, val in _ROMAN_VALS:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


for _n in range(1, 60):
    _VALID_ROMANS[_int_to_roman(_n)] = _n

_MAJOR_SECTIONS = {"onsoz", "odysseia", "ekler", "ek", "takdim", "giris"}
_CONSONANTS = set("bcçdfgğhjklmnprsştvyzqxwBCÇDFGĞHJKLMNPRSŞTVYZQXW")
_TR_DIACRITICS = set("ÇĞİÖŞÜçğıöşüâîû")


class TocEntry(NamedTuple):
    title: str
    level: int
    anchor: str
    href: str = ""


class _TocItem(NamedTuple):
    lines: list[int]
    raw_title: str
    clean_title: str
    roman_hint: str
    page: str
    has_page: bool
    kind: str


class _BodyTarget(NamedTuple):
    anchor_line: int
    sub_line: int | None
    level: int
    roman_num: int | None
    title: str


def _tr_lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def _slug(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`·\\]+", "", text)
    return re.sub(r"[^a-z]", "", text.translate(_SLUG).lower())


def _key(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`·\\]+", "", text)
    return re.sub(r"[^a-z]", "", text.translate(_FUZZY).lower())


def _words_key(text: str) -> list[str]:
    text = re.sub(r"<[^>]+>", " ", text)
    tokens = re.split(r"[\s\-–—/\\*_`·,.]+", text)
    return [k for tok in tokens if len(k := _key(tok)) >= 2]


def _clean_inline(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<sup>\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)\s*</sup>", r"\1", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`·\\]+", "", text)
    text = re.sub(r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü])/(?=[A-Za-zÇĞİÖŞÜçğıöşü])", "l", text)
    text = re.sub(r"\s*'\s*", "'", text)
    text = re.sub(r"\bİI(?=[a-zçğıöşü])", "İ", text)
    return re.sub(r"\s+", " ", text).strip(" -–·.")


def _is_toc_line(stripped: str) -> bool:
    return stripped.startswith("|") or bool(_TOC_PAIR_LINE.match(stripped))


# @start MD-EPUB-TOC
def _detach_toc_page(title: str, col_page: str) -> tuple[str, str, bool]:
    t = _clean_inline(title)
    col_clean = _clean_inline(col_page)
    p_col = re.sub(r"[^\d]", "", col_clean)
    p_rom = col_clean.strip(" _·.") if not p_col and re.fullmatch(r"[ivxlcığ1l]+", col_clean.strip(" _·."), re.I) else ""
    page = p_col or p_rom
    has_page = bool(page)

    if not page and (m := re.match(r"^(\d{1,4})\s+(.+)$", t)):
        page = m.group(1)
        t = m.group(2).strip()
        has_page = True
    if not page and (m := re.search(r"\s+(?:s\s*(\d{1,3})|(\d{1,4}))$", t)):
        page = f"5{m.group(1)}" if m.group(1) else m.group(2)
        t = t[: m.start()].strip(" -–·")
        has_page = True
    if not page and (m := re.search(r"\s+([ivxlcığ]{2,8})$", t)):
        if set(m.group(1).lower()) <= set("ivxlcığ"):
            page = m.group(1)
            t = t[: m.start()].strip(" -–·")
            has_page = True
    if not page and (m := re.search(r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü])(\d{1,4})(?=\s|$)", t)):
        page = m.group(1)
        t = (t[: m.start()] + t[m.end() :]).strip()
        has_page = True
    if not page and (m := re.search(r"(?:-\s*|\s+)(\d{2,4})(?:\s+|$)", t)):
        page = m.group(1)
        t = (t[: m.start()] + ("- " if t[m.start()] == "-" else " ") + t[m.end() :]).strip()
        has_page = True
    return t.strip(" -–·"), page, has_page


def _parse_roman_prefix(text: str) -> tuple[str, str]:
    if m := re.match(r"^([IVXLCDMivxlcdm1l]{1,6})\.\s+(.+)$", text):
        raw = m.group(1)
        norm = raw.upper().replace("L", "I") if raw.islower() and set(raw) <= {"l", "v", "x", "i"} else raw.upper()
        norm = {"M": "III", "XVD": "XVII", "XVM": "XVIII", "VLL": "VII", "VLLL": "VIII", "LL": "II"}.get(norm, norm)
        if norm in _VALID_ROMANS or set(raw.lower()) <= set("ivxlcdm1"):
            return (norm if norm in _VALID_ROMANS else raw.upper()), m.group(2).strip()
    return "", text


def _parse_bolum_roman(raw_prefix: str) -> int | None:
    tok = raw_prefix.strip().rstrip(".").upper()
    if not tok:
        return None
    if tok.isdigit():
        return int(tok)
    return _VALID_ROMANS.get(tok)


def _link(label: str, anchor: str) -> str:
    safe = label.replace("\\", "\\\\").replace("]", r"\]")
    return f"[{safe}](#{anchor})" if anchor else safe


def _prep_item(toc_item: _TocItem) -> tuple[str, list[str], int | None]:
    _, sub_toc = _parse_roman_prefix(toc_item.clean_title)
    txt = sub_toc if sub_toc else toc_item.clean_title
    return _key(txt), _words_key(txt), _VALID_ROMANS.get(toc_item.roman_hint)


def _prep_target(target: _BodyTarget) -> tuple[str, list[str]]:
    return _key(target.title), _words_key(target.title)


def _sim_fast(
    it_prep: tuple[str, list[str], int | None],
    full_prep: tuple[str, list[str]],
    tg_prep: tuple[str, list[str]],
    target: _BodyTarget,
) -> float:
    kt, wt = (it_prep[0], it_prep[1]) if target.roman_num is not None and it_prep[0] else full_prep
    kh, wh = tg_prep
    if not kt or not kh:
        return 0.0
    seq_ratio = 0.0 if abs(len(kt) - len(kh)) > max(len(kt), len(kh)) * 0.68 else SequenceMatcher(None, kt, kh).ratio()
    bag_ratio = 0.0
    if wt and wh and abs(len(wt) - len(wh)) <= max(len(wt), len(wh)):
        matched_w = 0.0
        used: set[int] = set()
        for a in wt:
            best_j, best_s = -1, 0.0
            for j, b in enumerate(wh):
                if j not in used:
                    s = 1.0 if a == b else SequenceMatcher(None, a, b).ratio()
                    if s > best_s:
                        best_j, best_s = j, s
            if best_s >= 0.70 and best_j >= 0:
                used.add(best_j)
                matched_w += best_s
        bag_ratio = (2.0 * matched_w) / (len(wt) + len(wh))
    score = max(seq_ratio, 0.94 * bag_ratio)
    if it_prep[2] is not None and target.roman_num is not None and it_prep[2] == target.roman_num:
        score = min(1.0, score + 0.06)
    if target.level == 1:
        score += 0.002
    return score


def _sim(toc_item: _TocItem, target: _BodyTarget) -> float:
    return _sim_fast(
        _prep_item(toc_item),
        (_key(toc_item.clean_title), _words_key(toc_item.clean_title)),
        _prep_target(target),
        target,
    )


# @start MD-EPUB-TOC-RECON
def _word_cleanliness(w: str) -> float:
    score = 0.0
    letters = [c for c in w if c.isalpha()]
    if not letters:
        return -5.0
    if any(c.isdigit() for c in w):
        score -= 3.0
    if any(c in "/*\\_|" for c in w):
        score -= 3.0
    # Mixed-case OCR errors: trailing uppercase on lowercase word (`UğraklarL`) or stray lowercase inside ALL-CAPS (`TEPKi`)
    n_upper = sum(1 for c in letters if c.isupper())
    n_lower = len(letters) - n_upper
    if len(letters) >= 3 and w[-1].isupper() and n_lower >= 2:
        score -= 2.5
    if len(letters) >= 3 and n_upper >= 2 and 0 < n_lower <= 2 and not (w[0].isupper() and all(c.islower() for c in letters[1:])):
        score -= 2.2
    if w[0] == "I" and len(w) > 2 and all(c.islower() for c in letters[1:]) and _tr_lower(w).startswith("ınsan"):
        score -= 1.0
    consec_c = 0
    low = _tr_lower(w)
    for ch in letters:
        consec_c = consec_c + 1 if ch in _CONSONANTS else 0
        if consec_c >= 3 and not any(k in low for k in ("skh", "khr", "str", "ntr", "mph", "lkh", "rkh", "sthm", "mps", "türk")):
            score -= 1.5
    if low.endswith(("'run", "'nm")):
        score -= 2.0
    if low.endswith(("'nın", "'nin", "'nun", "'nün")):
        score += 0.8
    if "'" in w:
        score += 0.3
    if w[0].isupper():
        score += 0.15
    score += 0.08 * sum(1 for c in letters if c in _TR_DIACRITICS)
    score += 0.04 * len(letters)
    return score


def _merge_word_pair(wt: str, wb: str, prefer_toc: bool = True) -> str:
    if wt == wb:
        return wt
    if _tr_lower(wt) == _tr_lower(wb):
        st, sb = _word_cleanliness(wt), _word_cleanliness(wb)
        if abs(st - sb) > 0.05:
            return wt if st > sb else wb
        return wt if wt[0].isupper() and not wb[0].isupper() else wb
    for a, b in ((wt, wb), (wb, wt)):
        if "'" in a and "'" not in b:
            stem, _suf = a.split("'", 1)
            if _tr_lower(b).startswith(_tr_lower(stem)) and len(b) > len(stem):
                cand = f"{stem}'{b[len(stem):]}"
                if _word_cleanliness(cand) >= max(_word_cleanliness(wt), _word_cleanliness(wb)):
                    return cand
    if wt[0] == "İ" and wb[0] == "I" and len(wb) >= len(wt):
        cand = "İ" + wb[1:]
        if _word_cleanliness(cand) >= max(_word_cleanliness(wt), _word_cleanliness(wb)):
            return cand
    if wb[0] == "İ" and wt[0] == "I" and len(wt) >= len(wb):
        cand = "İ" + wt[1:]
        if _word_cleanliness(cand) >= max(_word_cleanliness(wt), _word_cleanliness(wb)):
            return cand
    st, sb = _word_cleanliness(wt), _word_cleanliness(wb)
    if abs(st - sb) <= 0.12:
        return wt if prefer_toc else wb
    return wt if st > sb else wb


def _collapse_ocr_splits(tokens: list[str], ref_tokens: list[str]) -> list[str]:
    ref_keys = {_key(r): r for r in ref_tokens if len(_key(r)) >= 3}
    out: list[str] = []
    i = 0
    while i < len(tokens):
        merged = None
        for span in (3, 2):
            if i + span <= len(tokens):
                joined = "".join(tokens[i : i + span])
                if _key(joined) in ref_keys:
                    merged = ref_keys[_key(joined)]
                    i += span
                    break
        if merged is not None:
            out.append(merged)
        else:
            out.append(tokens[i])
            i += 1
    return out


def _dash_positions(s: str) -> set[str]:
    """Record `_key` of words immediately followed by an explicit ` - ` or ` – ` dash separator."""
    after_keys: set[str] = set()
    for m in re.finditer(r"(\S+)\s+[–—-]\s+", s):
        if k := _key(m.group(1)):
            after_keys.add(k)
    return after_keys


def _reconcile_titles(toc_item: _TocItem, target: _BodyTarget) -> tuple[str, str | None, str]:
    """Return (canonical_toc_title, canonical_bolum_heading_or_None, canonical_main_heading)."""
    _, toc_sub = _parse_roman_prefix(toc_item.clean_title)
    t_str = _clean_inline(toc_sub if target.roman_num is not None and toc_sub else toc_item.clean_title)
    b_str = _clean_inline(target.title)
    if target.roman_num is not None:
        t_str = re.sub(r"\b[IVXLC]{1,5}\.\s+", "", t_str).strip(" -–")

    dashes = _dash_positions(b_str) | (_dash_positions(t_str) if " – " in t_str or " - " in t_str else set())
    t_words = [w for w in re.split(r"[\s\-–—]+", t_str) if w]
    b_words = [w for w in re.split(r"[\s\-–—]+", b_str) if w]
    t_words = _collapse_ocr_splits(t_words, b_words)
    b_words = _collapse_ocr_splits(b_words, t_words)

    seq_r = SequenceMatcher(None, _key(" ".join(t_words)), _key(" ".join(b_words))).ratio()
    if seq_r < 0.72 and len(b_words) >= 2:
        t_reordered: list[str] = []
        used: set[int] = set()
        for bw in b_words:
            best_i, best_s = -1, 0.0
            for idx, tw in enumerate(t_words):
                if idx not in used:
                    s = SequenceMatcher(None, _key(tw), _key(bw)).ratio()
                    if s > best_s:
                        best_i, best_s = idx, s
            if best_s >= 0.70 and best_i >= 0:
                used.add(best_i)
                t_reordered.append(t_words[best_i])
            else:
                t_reordered.append(bw)
        if len(used) >= max(1, len(b_words) - 1):
            t_words = t_reordered

    t_keys = [_key(w) for w in t_words]
    b_keys = [_key(w) for w in b_words]
    prefer_toc = sum(_word_cleanliness(w) for w in t_words) >= sum(_word_cleanliness(w) for w in b_words)
    sm = SequenceMatcher(None, t_keys, b_keys)
    canon_words: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for tw, bw in zip(t_words[i1:i2], b_words[j1:j2]):
                canon_words.append(_merge_word_pair(tw, bw, prefer_toc))
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            for tw, bw in zip(t_words[i1:i2], b_words[j1:j2]):
                if SequenceMatcher(None, _key(tw), _key(bw)).ratio() >= 0.60:
                    canon_words.append(_merge_word_pair(tw, bw, prefer_toc))
                else:
                    canon_words.append(bw if _word_cleanliness(bw) >= _word_cleanliness(tw) else tw)
        else:
            seg_t, seg_b = t_words[i1:i2], b_words[j1:j2]
            score_t = sum(_word_cleanliness(w) for w in seg_t) / max(1, len(seg_t))
            score_b = sum(_word_cleanliness(w) for w in seg_b) / max(1, len(seg_b))
            canon_words.extend(seg_b if (seg_b and score_b >= score_t) else seg_t)

    if canon_words:
        pieces = []
        for idx, w in enumerate(canon_words):
            pieces.append(w)
            if idx + 1 < len(canon_words) and _key(w) in dashes:
                pieces.append("–")
        clean_sub = " ".join(pieces)
    else:
        clean_sub = b_str or t_str

    if target.roman_num is not None:
        rom = _int_to_roman(target.roman_num)
        return f"{rom}. {clean_sub}", f"{rom}. Bölüm", clean_sub
    return clean_sub, None, clean_sub
# @end MD-EPUB-TOC-RECON


def _collect_body_targets(lines: list[str], end: int) -> list[_BodyTarget]:
    raw_headings: list[tuple[int, int, str]] = []
    for n in range(end, len(lines)):
        if m := _HEADING.match(lines[n]):
            lvl = len(m.group(1))
            txt = m.group(2).strip()
            if len(_slug(txt)) <= 1:
                continue
            raw_headings.append((n, lvl, txt))

    targets: list[_BodyTarget] = []
    seen_major_slugs: set[str] = set()
    idx = 0
    while idx < len(raw_headings):
        n, lvl, txt = raw_headings[idx]
        clean_txt = _clean_inline(txt)
        bm = _BOLUM.match(clean_txt)
        if bm and idx + 1 < len(raw_headings):
            n2, lvl2, txt2 = raw_headings[idx + 1]
            between = [lines[k].strip() for k in range(n + 1, n2) if lines[k].strip()]
            if not between and not _BOLUM.match(_clean_inline(txt2)) and lvl2 <= 3:
                rnum = _parse_bolum_roman(bm.group(1))
                targets.append(_BodyTarget(n, n2, 1, rnum, _clean_inline(txt2)))
                idx += 2
                continue
        if bm:
            idx += 1
            continue
        sl = _key(clean_txt)
        if lvl >= 3 and any(s == sl or SequenceMatcher(None, sl, s).ratio() >= 0.85 for s in seen_major_slugs):
            idx += 1
            continue
        if lvl == 2 and len(sl) >= 12 and any(SequenceMatcher(None, sl, s).ratio() >= 0.85 for s in seen_major_slugs):
            idx += 1
            continue
        if lvl <= 2 and sl:
            seen_major_slugs.add(sl)
        targets.append(_BodyTarget(n, None, lvl, None, clean_txt))
        idx += 1

    bolum_idxs = [k for k, t in enumerate(targets) if t.sub_line is not None]
    for pos, k in enumerate(bolum_idxs):
        prev_num = targets[bolum_idxs[pos - 1]].roman_num if pos > 0 else 0
        next_num = targets[bolum_idxs[pos + 1]].roman_num if pos + 1 < len(bolum_idxs) else None
        cur = targets[k].roman_num
        if prev_num and next_num and next_num - prev_num == 2 and cur != prev_num + 1:
            targets[k] = targets[k]._replace(roman_num=prev_num + 1)
        elif cur is None and prev_num:
            targets[k] = targets[k]._replace(roman_num=prev_num + 1)
    return targets


# @start MD-EPUB-TOC-DP
def _align_monotonic_dp(items: list[_TocItem], targets: list[_BodyTarget], min_score: float = 0.50) -> list[int | None]:
    n, m = len(items), len(targets)
    if not n or not m:
        return [None] * n
    it_preps = [_prep_item(it) for it in items]
    full_preps = [(_key(it.clean_title), _words_key(it.clean_title)) for it in items]
    tg_preps = [_prep_target(tg) for tg in targets]
    scores = [
        [_sim_fast(it_preps[i], full_preps[i], tg_preps[j], targets[j]) - 1e-5 * j for j in range(m)]
        for i in range(n)
    ]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    choice = [[0] * (m + 1) for _ in range(n + 1)]

    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = dp[i][j + 1]
            act = 0
            if dp[i + 1][j] > best:
                best = dp[i + 1][j]
                act = 1
            s = scores[i][j]
            if s >= min_score:
                cand = (s - 0.25) + dp[i + 1][j + 1]
                if cand > best:
                    best = cand
                    act = 2
            dp[i][j] = best
            choice[i][j] = act

    matched: list[int | None] = [None] * n
    i, j = 0, 0
    while i < n and j < m:
        act = choice[i][j]
        if act == 2:
            matched[i] = j
            i += 1
            j += 1
        elif act == 1:
            i += 1
        else:
            j += 1
    return matched
# @end MD-EPUB-TOC-DP


def link_contents(markdown: str) -> tuple[str, list[TocEntry]]:
    """Link İçindekiler entries to body chapters via DP and reconcile titles bidirectionally."""
    lines = markdown.splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if (m := _HEADING.match(line)) and _slug(m.group(2)) == "icindekiler"
        ),
        None,
    )
    if start is None:
        return markdown, []

    raw_items: list[_TocItem] = []
    toc_image_lines: list[int] = []
    end = len(lines)
    i = start + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("!["):
            toc_image_lines.append(i)
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            title = heading.group(2)
            if not _TOC_PAIR_LINE.match(title.strip()):
                first_content = ""
                for j in range(i + 1, len(lines)):
                    cand = lines[j].strip()
                    if not cand or cand.startswith("![") or re.fullmatch(r"[-*_]{3,}", cand):
                        continue
                    if _HEADING.match(lines[j]):
                        continue
                    first_content = cand
                    break
                if first_content and not _is_toc_line(first_content):
                    end = i
                    break
            pairs = _PAGE_PAIR.findall(title) if _TOC_PAIR_LINE.match(title.strip()) else []
            if pairs:
                for t, p in pairs:
                    ct, page, hp = _detach_toc_page(t, p)
                    rom, _ = _parse_roman_prefix(ct)
                    raw_items.append(_TocItem([i], t.strip(), ct, rom, page, hp, "pair"))
            else:
                ct, page, hp = _detach_toc_page(title, "")
                rom, _ = _parse_roman_prefix(ct)
                raw_items.append(_TocItem([i], title, ct, rom, page, hp, "heading"))
        elif stripped.startswith("|") and not _TABLE_SEP.match(stripped):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells and _clean_inline(cells[0]) and not set(cells[0]) <= {"-", ":", " "}:
                ct, page, hp = _detach_toc_page(cells[0], cells[1] if len(cells) > 1 else "")
                if ct:
                    rom, _ = _parse_roman_prefix(ct)
                    raw_items.append(_TocItem([i], cells[0], ct, rom, page, hp, "table"))
        elif _TOC_PAIR_LINE.match(stripped) and not stripped.startswith("!"):
            for t, p in _PAGE_PAIR.findall(stripped):
                ct, page, hp = _detach_toc_page(t, p)
                rom, _ = _parse_roman_prefix(ct)
                raw_items.append(_TocItem([i], t.strip(), ct, rom, page, hp, "pair"))
        i += 1

    for img_ln in toc_image_lines:
        if img_ln < end:
            lines[img_ln] = ""

    targets = _collect_body_targets(lines, end)

    items: list[_TocItem] = []
    k = 0
    while k < len(raw_items):
        cur = raw_items[k]
        while (
            cur.kind == "table"
            and not cur.has_page
            and _slug(cur.clean_title) not in _MAJOR_SECTIONS
            and k + 1 < len(raw_items)
            and raw_items[k + 1].kind == "table"
            and not raw_items[k + 1].roman_hint
        ):
            nxt = raw_items[k + 1]
            sep = " - " if nxt.raw_title.lstrip().startswith(("-", "–")) else " "
            joined_ct = f"{cur.clean_title}{sep}{nxt.clean_title}".strip()
            joined_item = _TocItem(
                cur.lines + nxt.lines,
                f"{cur.raw_title} {nxt.raw_title}",
                joined_ct,
                cur.roman_hint,
                nxt.page,
                nxt.has_page,
                "table",
            )
            s_cur = max((_sim(cur, t) for t in targets), default=0.0)
            s_nxt = max((_sim(nxt, t) for t in targets), default=0.0)
            s_join = max((_sim(joined_item, t) for t in targets), default=0.0)
            starts_cont = bool(nxt.clean_title) and (nxt.clean_title[0].islower() or nxt.raw_title.lstrip().startswith(("-", "–")))
            if starts_cont or s_join > max(s_cur, s_nxt) + 0.05:
                cur = joined_item
                k += 1
                if cur.has_page:
                    break
            else:
                break
        items.append(cur)
        k += 1

    matched = _align_monotonic_dp(items, targets)

    has_parts = any(
        _slug(it.clean_title) in _MAJOR_SECTIONS or (it.kind in {"heading", "table"} and not it.has_page and not it.roman_hint)
        for it in items
    )
    seen_part = False
    entries: list[TocEntry] = []
    by_line: dict[int, list[tuple[str, str, str]]] = {}
    lines_to_drop: set[int] = set()
    seq = 0

    for item, t_idx in zip(items, matched):
        is_part = _slug(item.clean_title) in _MAJOR_SECTIONS or (
            item.kind in {"heading", "table"}
            and not item.has_page
            and not item.roman_hint
            and (t_idx is None or targets[t_idx].roman_num is None)
            and item.clean_title.isupper()
        )
        if is_part:
            level = 1
            seen_part = True
        else:
            level = 2 if (has_parts and seen_part) else 1

        anchor = ""
        canon_toc = item.clean_title
        if t_idx is not None:
            target = targets[t_idx]
            canon_toc, canon_bolum, canon_main = _reconcile_titles(item, target)
            seq += 1
            anchor = f"ch-{seq:03d}"
            if target.sub_line is not None and canon_bolum is not None:
                m1 = _HEADING.match(lines[target.anchor_line])
                h1_marks = "#" if m1 and len(m1.group(1)) <= 2 else (m1.group(1) if m1 else "#")
                lines[target.anchor_line] = f"{h1_marks} {canon_bolum} {{: #{anchor}}}"
                m2 = _HEADING.match(lines[target.sub_line])
                h2_marks = m2.group(1) if m2 else "##"
                lines[target.sub_line] = f"{h2_marks} {canon_main}"
            else:
                mh = _HEADING.match(lines[target.anchor_line])
                h_marks = mh.group(1) if mh else "#"
                lines[target.anchor_line] = f"{h_marks} {canon_main} {{: #{anchor}}}"

        entries.append(TocEntry(canon_toc, level, anchor))
        primary_ln = item.lines[0]
        for extra_ln in item.lines[1:]:
            if extra_ln != primary_ln:
                lines_to_drop.add(extra_ln)
        by_line.setdefault(primary_ln, []).append((canon_toc, item.page, anchor))

    if "{:" not in lines[start]:
        lines[start] = lines[start].rstrip() + " {: #icindekiler}"

    for line_no, group in by_line.items():
        raw = lines[line_no]
        if raw.lstrip().startswith("|"):
            label, page, anchor = group[0]
            lines[line_no] = f"| {_link(label, anchor)} | {page} |"
        elif _HEADING.match(raw) and not _TOC_PAIR_LINE.match(_HEADING.match(raw).group(2).strip()):
            label, _page, anchor = group[0]
            lines[line_no] = f"**{_link(label, anchor)}**"
        else:
            lines[line_no] = "\n".join(
                f"- {_link(label, anchor)}" + (f", {page}" if page else "")
                for label, page, anchor in group
            )

    for i in range(start, end):
        if i in lines_to_drop:
            continue
        if lines[i].lstrip().startswith("|") and not _TABLE_SEP.match(lines[i].strip()):
            cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            if all(not c for c in cells):
                lines_to_drop.add(i)
            else:
                lines[i] = re.sub(r"<br\s*/?>", " ", lines[i], flags=re.I)

    kept_lines = [ln for idx, ln in enumerate(lines) if idx not in lines_to_drop]
    return "\n".join(kept_lines) + ("\n" if markdown.endswith("\n") else ""), entries
# @end MD-EPUB-TOC


def nest_entries(entries: list[TocEntry]) -> list[dict]:
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for entry in entries:
        node = {"title": entry.title, "href": entry.href, "children": []}
        while stack and stack[-1][0] >= entry.level:
            stack.pop()
        (stack[-1][1]["children"] if stack else roots).append(node)
        stack.append((entry.level, node))
    return roots
