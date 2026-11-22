"""Auto-detect manzum (numbered-verse) sections and render sequence-verified margin line numbers."""
from __future__ import annotations

from collections import Counter
import html
import markdown as _md
import regex as re

_SECTION_SPLIT = re.compile(
    r"(?m)^(?=#\s+\S|##\s+[IVXLCDM\d]+\.\s+Bölüm\b)"
)
_RUNNING_HDR = re.compile(
    r"(?m)^#{2,6}\s+(?:Odysse\S*|[IVXLCDM\d]+\.\s+Bölüm)\s*$"
)
_HEADING_LINE = re.compile(r"^#{1,6}\s+")
_INDEX_LINE = re.compile(r"(?m)^[0-9.\s]*[A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ0-9\s()'.,/-]{1,45}:\s+.*$")
_CITATION_PAREN = re.compile(r"\([^)\n]{1,45}\)")

_DIGIT_OCR = {
    "0": "[0Oo]",
    "1": "[1lIı]",
    "2": "[2Zz]",
    "3": "3",
    "4": "4",
    "5": "[5S]",
    "6": "[6G]",
    "7": "7",
    "8": "[8B]",
    "9": "9",
}


# @start MD-EPUB-VERSE
def detect_verse_step(text: str, default_step: int = 5) -> int | None:
    """Return periodic line-number step (e.g. 5) when `text` is manzum/verse, else None."""
    cleaned = _CITATION_PAREN.sub(" ", text)
    cleaned = _INDEX_LINE.sub(" ", cleaned)
    cleaned = re.sub(r"(?m)^#{1,6}\s+.*$", " ", cleaned)
    cleaned = re.sub(r"(?m)^\|.*$", " ", cleaned)
    cleaned = re.sub(r"(?<=[.,;?!])\s*<sup>\s*\d+\s*</sup>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"<sup>\s*(\d+)\s*</sup>", r" \1 ", cleaned, flags=re.I)
    cleaned = re.sub(r"(?m)^(\s*(?:-\s*)?)\d{1,3}\.\s+", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)(\d)[ \t]+([0Oo])(?!\w)", lambda m: m.group(1) + "0", cleaned)
    for _ in range(2):
        cleaned = re.sub(
            r"(?<!\w)(\d{1,2})[ \t]+(\d{1,2})(?!\w)",
            lambda m: m.group(1) + m.group(2) if len(m.group(1) + m.group(2)) <= 3 else m.group(0),
            cleaned,
        )

    nums = [
        int(m.group(1))
        for m in re.finditer(r"(?<![A-Za-zÇĞİÖŞÜçğıöşü\d])(\d{1,3})(?![A-Za-zÇĞİÖŞÜçğıöşü\d.])", cleaned)
        if 1 <= int(m.group(1)) <= 999
    ]
    if len(nums) < 5:
        return None

    deltas: Counter[int] = Counter()
    for i, a in enumerate(nums):
        for b in nums[i + 1 : i + 5]:
            if 4 <= b - a <= 20:
                deltas[b - a] += 1

    candidates = [default_step] + [d for d, _ in deltas.most_common(6) if d >= 4 and d != default_step]
    chains: dict[int, int] = {}
    for step in candidates:
        chain = 0
        for start_idx, val in enumerate(nums):
            if val % step != 0 or val > step * 6:
                continue
            cur = val
            length = 1
            for nxt in nums[start_idx + 1 :]:
                if nxt % step == 0 and cur < nxt <= cur + max(50, step * 8):
                    cur = nxt
                    length += 1
            if length > chain:
                chain = length
        chains[step] = chain

    if not chains:
        return None
    best_step = max(chains, key=lambda s: chains[s])
    for finer in sorted(chains):
        if finer < best_step and best_step % finer == 0 and chains[finer] >= max(5, chains[best_step]):
            best_step = finer
            break

    best_chain = chains[best_step]
    if best_chain < 5:
        return None
    div_ratio = sum(1 for n in nums if n % best_step == 0) / len(nums)
    if best_chain < 15 and div_ratio < 0.45:
        return None
    if div_ratio < 0.30:
        return None
    if len(cleaned) > 2000 and (len(cleaned) / best_chain) > 950:
        return None
    return best_step


def _normalize_verse_body(body: str) -> str:
    body = _RUNNING_HDR.sub("", body)
    # Strip blockquote markers `> ` created by Marker on indented dialogue verses
    body = re.sub(r"(?m)^[ \t]*>[ \t]*", "", body)
    # Unwrap non-digit letter/quote superscript OCR artifacts (`<sup>K</sup>ydanlara` -> `Kydanlara`, `<sup>&quot;</sup>` -> `"`)
    body = re.sub(
        r"<sup>\s*([A-Za-zÇĞİÖŞÜçğıöşü\"'“”]|&quot;|&apos;)+\s*</sup>",
        lambda m: html.unescape(re.sub(r"</?sup>", "", m.group(0)).strip()),
        body,
        flags=re.I,
    )
    # Unwrap digit/OCR-counter superscripts (`<sup>1</sup> <sup>75</sup>İlk` -> ` 1 75 İlk`)
    body = re.sub(r"<sup>\s*([\dOolIıS\s]+)\s*</sup>(?=[A-ZÇĞİÖŞÜ\"'“])", r" \1 ", body)
    body = re.sub(r"<sup>\s*([\d\s]+)\s*</sup>", r" \1 ", body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    return body


def _counter_regex(target: int, clipped: bool = False, line_start_ocr: bool = False) -> re.Pattern:
    s = str(target)[1:] if clipped else str(target)
    parts = []
    for idx, ch in enumerate(s):
        if idx == 0 and ch == "2" and line_start_ocr and len(s) == 3:
            parts.append("[2Zz1lI]")
        elif len(s) == 1 and ch == "5":
            parts.append("[5S]" if line_start_ocr else "5")
        else:
            parts.append(_DIGIT_OCR[ch])
    core = r"[ \t]{0,2}".join(parts)
    if clipped:
        return re.compile(rf"(?m)^(?P<lead>[ \t]*-[ \t]+|[ \t]*)(?P<num>{core})(?=[ \t]+|[A-ZÇĞİÖŞÜ\"“'])")
    if line_start_ocr:
        return re.compile(rf"(?m)^(?P<lead>[ \t]*-[ \t]+)(?P<num>{core})(?=$|[ \t]+)")
    return re.compile(
        rf"(?:(?P<lead>^[ \t]*-[ \t]+|^[ \t]*)|(?<=[\s,.;:!?…\"”'’—\-]))"
        rf"(?P<num>{core})"
        rf"(?=$|[\s,.;:!?…\"”'’—\-]|[A-ZÇĞİÖŞÜ])",
        re.M,
    )


def _find_counter(win: str, target: int, step: int = 5, allow_clipped: bool = True) -> tuple[int, int] | None:
    """Find (span_start, span_end) of `target` in `win`."""
    candidates: list[tuple[re.Pattern, bool, int]] = [
        (_counter_regex(target), False, len(win)),
        (_counter_regex(target, line_start_ocr=True), True, len(win)),
    ]
    if allow_clipped and target >= 100:
        candidates.append((_counter_regex(target, clipped=True), False, min(len(win), 700)))

    for pat, allow_all_alpha, limit in candidates:
        sub_win = win[:limit]
        for m in pat.finditer(sub_win):
            num_txt = m.group("num")
            if not allow_all_alpha and not any(c.isdigit() for c in num_txt):
                continue
            end_pos = m.end("num")
            if end_pos < len(sub_win) and sub_win[end_pos].isalpha():
                if not num_txt[-1].isdigit():
                    continue
            # If clipped without `- ` list prefix, verify `target + step` follows to avoid eating prose digits
            if limit < len(win) and "-" not in (m.groupdict().get("lead") or ""):
                if _find_counter(win[end_pos : end_pos + 700], target + step, step, allow_clipped=True) is None:
                    continue
            start_pos = m.start("num")
            line_start = sub_win.rfind("\n", 0, start_pos) + 1
            prefix = sub_win[line_start:start_pos]
            if re.fullmatch(r"[ \t]*-[ \t]+|[ \t]*", prefix):
                start_pos = line_start
            return start_pos, end_pos
    return None


def _split_midpoint(chunk: str) -> tuple[str, str]:
    chunk = chunk.strip()
    if not chunk:
        return "", ""
    mid = len(chunk) // 2
    paras = [m.span() for m in re.finditer(r"\n\s*\n", chunk) if 0 < m.start() and m.end() < len(chunk)]
    if paras:
        s, e = min(paras, key=lambda sp: abs(sp[0] - mid))
        return chunk[:s].strip(), chunk[e:].strip()
    clauses = [m.end() for m in re.finditer(r"[.!?…,:;]\s+|\n", chunk) if 0 < m.end() < len(chunk)]
    if clauses:
        cut = min(clauses, key=lambda p: abs(p - mid))
        return chunk[:cut].strip(), chunk[cut:].strip()
    space_cuts = [m.start() for m in re.finditer(r"\s+", chunk) if 0 < m.start() < len(chunk)]
    if space_cuts:
        cut = min(space_cuts, key=lambda p: abs(p - mid))
        return chunk[:cut].strip(), chunk[cut:].strip()
    return chunk, ""


def _render_inline_md(text: str) -> str:
    rendered = _md.markdown(text).strip()
    if rendered.startswith("<p>") and rendered.endswith("</p>") and "<p>" not in rendered[3:-4]:
        return rendered[3:-4]
    return rendered


def _verse_p(ln: int | None, body: str) -> str:
    body = re.sub(r"^\s*[-–>]\s+", "", body.strip())
    body = re.sub(r"\s+", " ", body).strip()
    if not body and ln is None:
        return ""
    if ln is not None:
        ln_html = f'<span class="verse-ln" aria-label="Satır {ln}">{ln}</span>'
    else:
        ln_html = '<span class="verse-ln verse-ln-empty" aria-hidden="true"></span>'
    inner = _render_inline_md(body) if body else ""
    return f'<p class="verse">{ln_html}<span class="verse-body">{inner}</span></p>'


def _emit_chunk(ln: int | None, chunk: str, out: list[str]) -> None:
    raw_blocks = [b.strip() for b in re.split(r"\n\s*\n|(?m)^(?=[ \t]*-[ \t]+)", chunk) if b.strip()]
    if not raw_blocks:
        return
    pending_ln = ln
    for blk in raw_blocks:
        if _HEADING_LINE.match(blk) or blk.startswith("![") or blk.startswith("|"):
            out.append(blk)
        else:
            out.append(_verse_p(pending_ln, blk))
            pending_ln = None


# @start MD-EPUB-VERSE-SEQ
def _format_manzum_section(section: str, step: int) -> str:
    lines = section.splitlines(keepends=True)
    head_end = 0
    while head_end < len(lines):
        s = lines[head_end].strip()
        if not s or _HEADING_LINE.match(s) or s.startswith("!["):
            head_end += 1
        else:
            break
    header = "".join(lines[:head_end])
    body = _normalize_verse_body("".join(lines[head_end:]))
    if not body.strip():
        return section

    rendered: list[str] = []
    pos = 0
    prev = 0
    current_ln: int | None = None
    n_len = len(body)

    while pos < n_len:
        expected = prev + step
        win = body[pos : min(n_len, pos + 2000)]
        hit_exp = _find_counter(win, expected, step)
        hit_nxt = _find_counter(win, expected + step, step)

        if hit_exp is not None and (hit_nxt is None or hit_exp[0] < hit_nxt[0]):
            _emit_chunk(current_ln, win[: hit_exp[0]], rendered)
            current_ln = expected
            prev = expected
            pos += hit_exp[1]
            continue

        if hit_nxt is not None:
            first_h, second_h = _split_midpoint(win[: hit_nxt[0]])
            if second_h:
                _emit_chunk(current_ln, first_h, rendered)
                _emit_chunk(expected, second_h, rendered)
            else:
                _emit_chunk(current_ln or expected, first_h, rendered)
            current_ln = expected + step
            prev = expected + step
            pos += hit_nxt[1]
            continue

        hit_nxt2 = _find_counter(win, expected + 2 * step, step)
        if hit_nxt2 is not None:
            chunk = win[: hit_nxt2[0]]
            h1, rem = _split_midpoint(chunk)
            h2, h3 = _split_midpoint(rem)
            _emit_chunk(current_ln, h1, rendered)
            if h2:
                _emit_chunk(expected, h2, rendered)
            if h3:
                _emit_chunk(expected + step, h3, rendered)
            current_ln = expected + 2 * step
            prev = expected + 2 * step
            pos += hit_nxt2[1]
            continue

        _emit_chunk(current_ln, body[pos:], rendered)
        current_ln = None
        break

    if current_ln is not None and (not rendered or f">{current_ln}<" not in rendered[-1]):
        # Attach trailing counter to previous verse paragraph if empty tail
        rendered.append(_verse_p(current_ln, " "))

    trail = "\n" if section.endswith("\n") else ""
    return header + "\n\n".join(r for r in rendered if r) + trail
# @end MD-EPUB-VERSE-SEQ


def format_verse_line_numbers(md: str) -> str:
    """Detect manzum sections and extract sequence-verified margin line numbers into a dedicated column."""
    parts = [p for p in _SECTION_SPLIT.split(md) if p]
    if not parts:
        return md
    out: list[str] = []
    for part in parts:
        step = detect_verse_step(part)
        if step is not None:
            out.append(_format_manzum_section(part, step))
        else:
            out.append(part)
    return "".join(out)
# @end MD-EPUB-VERSE
