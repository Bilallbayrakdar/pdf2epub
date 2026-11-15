"""Repair PDF text-layer words whose glyphs were stored as the wrong Unicode.

A broken word holds ':' ';' '?' '<' '>' or a digit inside letters. The drawn
line is fine, so only those lines are recognized and the bad word is spliced
back. Clean ligatures and ordinary punctuation stay as extracted.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pypdfium2 as pdfium

_CACHE = Path("/tmp/glyph_repair_ocr.json")

_EDGE = ".,!?'\"“”‘’«»()[]"
_BAD = re.compile(
    r"[A-Za-zÇĞİÖŞÜçğıöşüİı][:;?<>]|[:;?<>][A-Za-zÇĞİÖŞÜçğıöşüİı]|\d[:;?<>]\d"
)
_WORD = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüİıÂâÊêÎîÔôÛû'’\-]+")
_ABBR = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüİı]+\.'[A-Za-zÇĞİÖŞÜçğıöşüİı]+")
_SUP = re.compile(r"<sup>\s*(\d+)\s*</sup>", re.I)
_LETTERS = re.compile(r"[^A-Za-zÇĞİÖŞÜçğıöşüİı]+")


class RepairReport:
    def __init__(self, lines, replaced, unresolved):
        self.lines = lines
        self.replaced = replaced
        self.unresolved = unresolved


def _core(word):
    return re.sub(r"[:;?]+$", "", word.strip(_EDGE))


def _norm_pdf(text):
    return text.replace("\ufffe", "-").replace("\u00ad", "-").replace("\x02", "")


def _norm_ocr(text):
    return re.sub(r"</?br\s*/?>", "", text, flags=re.I)


def _bad(word):
    return bool(_BAD.search(_core(word)))


def _skel(text):
    return _LETTERS.sub("", text).casefold()


def _alike(a, b):
    sa, sb = _skel(a), _skel(b)
    if not sa or not sb:
        return 0.0
    return SequenceMatcher(None, sa, sb).ratio()


def _ok(src, dst):
    dst = dst.strip()
    if not dst or not (len(src) - 4 <= len(dst) <= len(src) + 2):
        return False
    if _BAD.search(dst):
        return False
    return bool(_WORD.fullmatch(dst.strip(_EDGE)) or _ABBR.fullmatch(dst))


def _accept(src, dst):
    src_c = _core(src)
    sup = _SUP.search(dst)
    if sup:
        body = _tidy(src, _SUP.sub("", dst).strip(" :."))
        if body and re.fullmatch(re.escape(body) + r":\d+", src_c):
            return f"{body}<sup>{sup.group(1)}</sup>"
        return None
    tidy = _tidy(src, dst)
    if _ok(src_c, tidy) or (_ABBR.fullmatch(tidy) and len(src_c) - 4 <= len(tidy) <= len(src_c) + 3):
        return tidy
    return None


def _tidy(src, dst):
    src, dst = _core(src), dst.replace("’", "'").replace("´", "'").strip()
    while dst and dst[-1] in _EDGE:
        dst = dst[:-1]
    while dst and dst[0] in _EDGE:
        dst = dst[1:]
    if src[:1].isupper() and dst[:1].islower():
        dst = dst[:1].upper() + dst[1:]
    return dst


def _lines(page):
    tp = page.get_textpage()
    lines, cur = [], []
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if ch in "\r\n":
            if cur:
                lines.append(cur)
                cur = []
            continue
        if ch in "\u00ad\x02":
            continue
        if ch == "\u00a0":
            ch = " "
        cur.append((ch, tp.get_charbox(i)))
    if cur:
        lines.append(cur)
    return lines


def _text(chars):
    return "".join(ch for ch, _ in chars)


def _render(page, chars, scale=5):
    W, H = page.get_size()
    x0 = min(b[0] for _, b in chars)
    y0 = min(b[1] for _, b in chars)
    x1 = max(b[2] for _, b in chars)
    y1 = max(b[3] for _, b in chars)
    pad = 1.5
    crop = (max(0, x0 - pad), max(0, y0 - pad), max(0, W - (x1 + pad)), max(0, H - (y1 + pad)))
    if min(x1 - x0, y1 - y0) < 1 or crop[0] + crop[2] >= W - 0.5 or crop[1] + crop[3] >= H - 0.5:
        return None
    return page.render(scale=scale, crop=crop).to_pil().convert("RGB")


def _recognize(pairs):
    cache = json.loads(_CACHE.read_text()) if _CACHE.exists() else {}
    missing = [(hashlib.sha1(text.encode()).hexdigest(), img) for text, img in pairs if hashlib.sha1(text.encode()).hexdigest() not in cache]
    if missing:
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor

        rec = RecognitionPredictor(FoundationPredictor())
        step = 16
        for i in range(0, len(missing), step):
            batch = missing[i:i + step]
            preds = rec([im for _, im in batch], bboxes=[[[0, 0, im.width, im.height]] for _, im in batch])
            for (key, _), pred in zip(batch, preds):
                cache[key] = " ".join(ln.text for ln in pred.text_lines).strip()
            _CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    return [cache[hashlib.sha1(text.encode()).hexdigest()] for text, _ in pairs]


def _pair(src, ocr):
    sw, dw = src.split(), ocr.split()
    found = {}
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, sw, dw).get_opcodes():
        if tag != "replace" or j2 <= j1:
            continue
        srcs, dsts = sw[i1:i2], dw[j1:j2]
        if not any(_bad(s) for s in srcs):
            continue
        scores = sorted(
            ((_alike(s, d), si, di) for si, s in enumerate(srcs) for di, d in enumerate(dsts)),
            reverse=True,
        )
        used_s, used_d, chosen = set(), set(), {}
        for score, si, di in scores:
            if si in used_s or di in used_d or score < 0.45:
                continue
            used_s.add(si)
            used_d.add(di)
            chunk = dsts[di]
            if di + 1 < len(dsts) and _SUP.fullmatch(dsts[di + 1].strip()):
                chunk += dsts[di + 1]
            chosen[si] = chunk
        for si, raw in enumerate(srcs):
            if si not in chosen or not _bad(raw):
                continue
            good = _accept(raw, chosen[si])
            if good:
                found[_core(raw)] = good
    return found


def _flat(text):
    return text.replace("-", "")


def _apply_one(md, bad, good):
    boundary = lambda token: rf"(?<!\w){re.escape(token)}(?!\w)"

    def sub(token, repl, text):
        return re.sub(boundary(token), lambda _m: repl, text)

    if re.search(boundary(bad), md):
        return sub(bad, good, md)
    escaped = bad.replace("*", r"\*")
    if escaped != bad and re.search(boundary(escaped), md):
        return sub(escaped, good, md)
    flat_bad, flat_good = _flat(bad), _flat(good)
    if flat_bad != bad and flat_bad != flat_good and re.search(boundary(flat_bad), md):
        return sub(flat_bad, flat_good, md)
    src_parts, dst_parts = bad.split("-"), good.split("-")
    if len(src_parts) == len(dst_parts) > 1:
        for src_part, dst_part in zip(src_parts, dst_parts):
            if src_part != dst_part and _bad(src_part) and re.search(boundary(src_part), md):
                md = sub(src_part, dst_part, md)
    return md


def repair_markdown(pdf_path, markdown):
    pdf = pdfium.PdfDocument(pdf_path)
    jobs = []
    for page in pdf:
        for chars in _lines(page):
            text = _text(chars)
            if any(_bad(w) for w in text.split()):
                img = _render(page, chars)
                if img is not None:
                    jobs.append((text, img))
    votes = defaultdict(Counter)
    unresolved = []
    if jobs:
        print(f"Glyph repair: recognizing {len(jobs)} line(s)", flush=True)
        for src, ocr in zip((t for t, _ in jobs), _recognize(jobs)):
            if len(ocr) < len(src) * 0.4:
                unresolved.extend(_core(w) for w in src.split() if _bad(w))
                continue
            src_n, ocr_n = _norm_pdf(src), _norm_ocr(ocr)
            if SequenceMatcher(None, src_n, ocr_n).ratio() < 0.72:
                unresolved.extend(_core(w) for w in src_n.split() if _bad(w))
                continue
            got = _pair(src_n, ocr_n)
            for w in src.split():
                c = _core(w)
                if _bad(c) and c not in got:
                    unresolved.append(c)
            for bad, good in got.items():
                votes[bad][good] += 1
    replaced, conflicts = {}, []
    for bad, ctr in votes.items():
        good, _n = ctr.most_common(1)[0]
        if len(ctr) == 1:
            replaced[bad] = good
        else:
            conflicts.append(bad)
    out = markdown
    applied = {}
    for bad, good in sorted(replaced.items(), key=lambda kv: -len(kv[0])):
        nxt = _apply_one(out, bad, good)
        if nxt != out:
            applied[bad] = good
            out = nxt
    unresolved = sorted({w for w in unresolved if w not in applied and w in markdown})
    return out, RepairReport(len(jobs), applied, unresolved + [w for w in conflicts if w in markdown])
