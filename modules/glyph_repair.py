"""Repair ClearScan PDF text-layer corruption via structural scan + hybrid OCR splice."""
import ctypes
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import yaml

_CACHE = Path("/tmp/glyph_repair_ocr.json")
_MODEL_REPO = "PaddlePaddle/latin_PP-OCRv5_mobile_rec_onnx"
_MODEL_COMMIT = "89d3a50e2c27e2e7cceeab0e944c25c807d5db4f"
_ONNX_STATE = None

_EDGE = ".,!?'\"“”‘’«»()[]·"
_BAD = re.compile(
    r"[\ufffd\ue000-\uf8ff]"
    r"|[A-Za-zÇĞİÖŞÜçğıöşü][:;?<>_\\*]|[:;?<>_\\*][A-Za-zÇĞİÖŞÜçğıöşü]"
    r"|\d[:;?<>]\d"
    r"|[A-Za-zÇĞİÖŞÜçğıöşü]\d[A-Za-zÇĞİÖŞÜçğıöşü]|\d[A-Za-zÇĞİÖŞÜçğıöşü]{2,}"
    r"|[a-zçğıöşü][.,][a-zçğıöşü]"
    r"|[a-zçğıöşü][A-ZÇĞİÖŞÜ]"
    r"|[A-ZÇĞİÖŞÜ]{2,}[a-zçğıöşü][A-ZÇĞİÖŞÜ]+"
)
_FAKE_BOX = frozenset({
    "ın", "in", "rn", "nı", "lc", "ic", "ıı", "ii", "iı", "İı",
    "filı", "Alı", "riı", "ğal", "al", "lm", "lial",
})
_RARE_MAX_LINES = 125
_WORD = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüİıÂâÊêÎîÔôÛû'’\-]+")
_ABBR = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüİı]+\.'[A-Za-zÇĞİÖŞÜçğıöşüİı]+")
_SUP = re.compile(r"<sup>\s*(\d+)\s*</sup>", re.I)
_LETTERS = re.compile(r"[^A-Za-zÇĞİÖŞÜçğıöşüİı]+")


class RepairReport:
    def __init__(self, lines, replaced, unresolved):
        self.lines = lines
        self.replaced = replaced
        self.unresolved = unresolved


class LineJob(NamedTuple):
    raw: str
    norm: str
    page_idx: int
    crop_box: tuple[float, float, float, float]
    n_boxes: int
    rare_font: bool
    bad_words: frozenset[str]


def _core(word):
    c = re.sub(r"[:;?]+$", "", word.strip(_EDGE))
    c = re.sub(r"\?\d+$", "", c)
    return re.sub(r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü.?])\d+$", "", c)


def _norm_pdf(text):
    return text.replace("\ufffe", "-").replace("\u00ad", "-").replace("\x02", "")


def _norm_ocr(text):
    text = re.sub(r"</?br\s*/?>", "", text, flags=re.I)
    return text.replace("â", "a").replace("î", "i")


def _bad(word):
    return bool(_BAD.search(_core(word)))


def _repairable(word, bad_words):
    c = _core(word)
    return c in bad_words or _bad(c) or "\ufffd" in word


def _skel(text):
    return _LETTERS.sub("", text).casefold()


def _alike(a, b):
    if "\ufffd" in a or "\ufffd" in b or re.search(r"\d", a + b):
        return SequenceMatcher(None, _core(a), _core(b)).ratio()
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
    if "\ufffd" in src and re.fullmatch(r"\d+\ufffd", src_c):
        m = re.search(r"\d{4}", dst)
        if m and len(m.group()) == 4:
            return _tidy(src, m.group())
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


def _font_name(tp, idx, buf):
    buf[0] = b"\x00"
    pdfium_raw.FPDFText_GetFontInfo(tp, idx, buf, 128, None)
    return buf.value.decode("latin1", "ignore")


def _scan_pdf(pdf_path):
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        return _scan_pdf_open(pdf)
    finally:
        pdf.close()


def _scan_pdf_open(pdf):
    buf = ctypes.create_string_buffer(128)
    raw_lines, font_counts = [], Counter()

    for pi, page in enumerate(pdf):
        tp = page.get_textpage()
        W, H = page.get_size()
        cur = []
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            if ch in "\r\n":
                if cur:
                    raw_lines.append((pi, W, H, cur))
                    for fn in {c[2] for c in cur if c[0] != " " and c[2]}:
                        font_counts[fn] += 1
                    cur = []
                continue
            if ch in "\x02":
                ch = "-"
            elif ch == "\u00ad":
                ch = "-"
            elif ch == "\u00a0":
                ch = " "
            cur.append((ch, tp.get_charbox(i), _font_name(tp, i, buf)))
        if cur:
            raw_lines.append((pi, W, H, cur))
            for fn in {c[2] for c in cur if c[0] != " " and c[2]}:
                font_counts[fn] += 1

    rare_fonts = {fn for fn, cnt in font_counts.items() if fn and cnt <= _RARE_MAX_LINES}
    jobs = []
    for pi, W, H, cur in raw_lines:
        raw = "".join(ch for ch, _, _ in cur)
        norm = _norm_pdf(raw)
        non_space = [(ch, b, fn) for ch, b, fn in cur if ch != " " and b[2] > b[0]]
        if not non_space:
            continue

        char_to_word, word_idx, in_space = [], 0, True
        for ch, _, _ in cur:
            if ch.isspace():
                in_space, char_to_word = True, char_to_word + [-1]
            else:
                if in_space and char_to_word and max(char_to_word) >= 0:
                    word_idx += 1
                in_space = False
                char_to_word.append(word_idx)

        words = norm.split()
        bad_words = {_core(w) for w in words if _bad(w) or w in ("1", "ı")}
        prev_rb, run, run_words, n_boxes = None, [], set(), 0
        for (ch, b, _), wi in zip(cur, char_to_word):
            if wi < 0 or b[2] <= b[0]:
                if len(run) > 1 and ("".join(run) in _FAKE_BOX or not "".join(run).isalpha()):
                    for rw in run_words:
                        if rw < len(words):
                            bad_words.add(_core(words[rw]))
                prev_rb, run, run_words = None, [], set()
                continue
            rb = (round(b[0], 1), round(b[1], 1), round(b[2], 1), round(b[3], 1))
            if rb == prev_rb:
                run.append(ch)
                run_words.add(wi)
            else:
                if len(run) > 1 and ("".join(run) in _FAKE_BOX or not "".join(run).isalpha()):
                    for rw in run_words:
                        if rw < len(words):
                            bad_words.add(_core(words[rw]))
                n_boxes += 1
                prev_rb, run, run_words = rb, [ch], {wi}
        if len(run) > 1 and ("".join(run) in _FAKE_BOX or not "".join(run).isalpha()):
            for rw in run_words:
                if rw < len(words):
                    bad_words.add(_core(words[rw]))

        rare = sum(fn in rare_fonts for _, _, fn in non_space) >= 0.5 * len(non_space)
        if not bad_words and not rare:
            continue

        x0 = min(b[0] for _, b, _ in non_space)
        y0 = min(b[1] for _, b, _ in non_space)
        x1 = max(b[2] for _, b, _ in non_space)
        y1 = max(b[3] for _, b, _ in non_space)
        pad = 1.5
        crop = (max(0.0, x0 - pad), max(0.0, y0 - pad), max(0.0, W - (x1 + pad)), max(0.0, H - (y1 + pad)))
        if min(x1 - x0, y1 - y0) < 1 or crop[0] + crop[2] >= W - 0.5 or crop[1] + crop[3] >= H - 0.5:
            continue
        jobs.append(LineJob(raw, norm, pi, crop, n_boxes, rare, frozenset(filter(None, bad_words))))
    return jobs


def _load_onnx():
    global _ONNX_STATE
    if _ONNX_STATE is None:
        from huggingface_hub import hf_hub_download
        import onnxruntime as ort

        model_path = hf_hub_download(_MODEL_REPO, "inference.onnx", revision=_MODEL_COMMIT)
        cfg_path = hf_hub_download(_MODEL_REPO, "inference.yml", revision=_MODEL_COMMIT)
        cfg = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))
        chars = list(cfg["PostProcess"]["character_dict"])
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = min(16, os.cpu_count() or 4)
        sess = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        out_classes = sess.get_outputs()[0].shape[-1]
        vocab = [""] + chars + ([" "] if not isinstance(out_classes, int) or out_classes > len(chars) + 1 else [])
        _ONNX_STATE = (sess, sess.get_inputs()[0].name, vocab)
    return _ONNX_STATE


def _onnx_recognize(images, batch_size=32):
    if not images:
        return []
    sess, in_name, vocab = _load_onnx()
    order = sorted(range(len(images)), key=lambda i: images[i].width / max(1, images[i].height))
    results = [("", 0.0)] * len(images)
    for start in range(0, len(order), batch_size):
        idxs = order[start:start + batch_size]
        resized, max_w = [], 160
        for i in idxs:
            im = images[i]
            w = max(16, min(3200, int(math.ceil(48.0 * im.width / max(1, im.height)))))
            max_w = max(max_w, w)
            arr = np.asarray(im.resize((w, 48)), dtype=np.float32)[:, :, ::-1]
            arr = (arr / 255.0 - 0.5) / 0.5
            resized.append(arr.transpose(2, 0, 1))
        batch = np.zeros((len(idxs), 3, 48, max_w), dtype=np.float32)
        for bi, chw in enumerate(resized):
            batch[bi, :, :, :chw.shape[2]] = chw
        probs = sess.run(None, {in_name: batch})[0]
        if probs.min() < 0.0 or probs.max() > 1.001:
            e = np.exp(probs - probs.max(axis=-1, keepdims=True))
            probs = e / e.sum(axis=-1, keepdims=True)
        cls_ids, cls_p = probs.argmax(axis=-1), probs.max(axis=-1)
        for bi, orig_i in enumerate(idxs):
            chars, confs, prev = [], [], 0
            for cid, cp in zip(cls_ids[bi], cls_p[bi]):
                cid = int(cid)
                if cid != 0 and cid != prev and cid < len(vocab):
                    chars.append(vocab[cid])
                    confs.append(float(cp))
                prev = cid
            text = re.sub(r"\s+", " ", "".join(chars)).strip()
            results[orig_i] = (text, float(np.mean(confs)) if confs else 0.0)
    return results


def _needs_surya(job, text, conf):
    if not text or conf < 0.88:
        return True
    clean = _norm_ocr(text)
    ns_ocr = len(re.sub(r"\s+", "", clean))
    if not (0.65 * job.n_boxes <= ns_ocr <= 1.45 * job.n_boxes):
        return True
    if any(_bad(w) for w in clean.split()):
        return True
    return SequenceMatcher(None, job.norm.casefold(), clean.casefold()).ratio() < (0.48 if job.rare_font else 0.76)


def _recognize_jobs(pdf_path, jobs):
    cache = json.loads(_CACHE.read_text()) if _CACHE.exists() else {}
    keys = [hashlib.sha1(j.raw.encode()).hexdigest() for j in jobs]
    miss_idx = [i for i, k in enumerate(keys) if k not in cache]
    if miss_idx:
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            miss_imgs = [pdf[jobs[i].page_idx].render(scale=4, crop=jobs[i].crop_box).to_pil().convert("RGB") for i in miss_idx]
            onnx_res = _onnx_recognize(miss_imgs)
            surya_todo = []
            for i, (txt, conf) in zip(miss_idx, onnx_res):
                if _needs_surya(jobs[i], txt, conf):
                    surya_todo.append((i, pdf[jobs[i].page_idx].render(scale=5, crop=jobs[i].crop_box).to_pil().convert("RGB")))
                else:
                    cache[keys[i]] = txt
            if surya_todo:
                from surya.foundation import FoundationPredictor
                from surya.recognition import RecognitionPredictor

                rec = RecognitionPredictor(FoundationPredictor())
                for s in range(0, len(surya_todo), 16):
                    chunk = surya_todo[s:s + 16]
                    preds = rec([im for _, im in chunk], bboxes=[[[0, 0, im.width, im.height]] for _, im in chunk])
                    for (i, _), pred in zip(chunk, preds):
                        cache[keys[i]] = " ".join(ln.text for ln in pred.text_lines).strip()
                    _CACHE.write_text(json.dumps(cache, ensure_ascii=False))
            _CACHE.write_text(json.dumps(cache, ensure_ascii=False))
        finally:
            pdf.close()
    return [_norm_ocr(cache[k]) for k in keys]


def _valid_line_ocr(job, ocr_clean):
    ns_ocr = re.sub(r"\s+", "", ocr_clean)
    if not ns_ocr or not (0.65 * job.n_boxes <= len(ns_ocr) <= 1.45 * job.n_boxes):
        return False
    if any(_bad(w) for w in ocr_clean.split()):
        return False
    ns_src = re.sub(r"\s+", "", job.norm)
    r = SequenceMatcher(None, ns_src.casefold(), ns_ocr.casefold()).ratio()
    return r >= (0.50 if job.rare_font else 0.72)


def _replace_standalone_lines(md, line_map):
    if not line_map:
        return md, {}
    applied, out_lines = {}, []
    for line in md.splitlines():
        m_head = re.match(r"^(#{1,6}\s+)(.+?)(\s*)$", line)
        m_cell = re.match(r"^(\|\s*)([^|]+?)(\s*\|\s*\d*\s*\|)$", line)
        if m_head:
            body = m_head.group(2).strip()
            if body in line_map and line_map[body] != body:
                applied[body] = line_map[body]
                line = f"{m_head.group(1)}{line_map[body]}{m_head.group(3)}"
        elif m_cell:
            body = m_cell.group(2).strip()
            if body in line_map and line_map[body] != body:
                applied[body] = line_map[body]
                line = f"{m_cell.group(1)}{line_map[body]}{m_cell.group(3)}"
        out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if md.endswith("\n") else ""), applied


def _pair(src, ocr, bad_words):
    sw, dw = src.split(), ocr.split()
    found = {}
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, sw, dw).get_opcodes():
        if tag != "replace" or j2 <= j1:
            continue
        srcs, dsts = sw[i1:i2], dw[j1:j2]
        if not any(_repairable(s, bad_words) for s in srcs):
            continue
        scores = sorted(((_alike(s, d), si, di) for si, s in enumerate(srcs) for di, d in enumerate(dsts)), reverse=True)
        used_s, used_d, chosen = set(), set(), {}
        for score, si, di in scores:
            floor = 0.25 if "\ufffd" in srcs[si] else 0.45
            if si in used_s or di in used_d or score < floor:
                continue
            used_s.add(si)
            used_d.add(di)
            chunk = dsts[di]
            if di + 1 < len(dsts) and _SUP.fullmatch(dsts[di + 1].strip()):
                chunk += dsts[di + 1]
            chosen[si] = chunk
        for si, raw in enumerate(srcs):
            if si not in chosen or not _repairable(raw, bad_words):
                continue
            good = _accept(raw, chosen[si])
            if good:
                found[_core(raw)] = good
    return found


def _context_phrases(sw, dw):
    phrases = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, sw, dw).get_opcodes():
        if tag != "replace" or i2 - i1 != 1 or j2 - j1 != 1:
            continue
        a, b = sw[i1], dw[j1]
        if a == b or _bad(a) or "\ufffd" in a:
            continue
        if i1 > 0 and j1 > 0 and sw[i1 - 1] == dw[j1 - 1]:
            phrases.append((f"{sw[i1 - 1]} {a}", f"{dw[j1 - 1]} {b}"))
        elif i1 + 1 < len(sw) and j1 + 1 < len(dw) and sw[i1 + 1] == dw[j1 + 1]:
            phrases.append((f"{a} {sw[i1 + 1]}", f"{b} {dw[j1 + 1]}"))
    return phrases


def _flat(text):
    return text.replace("-", "")


def _apply_one(md, bad, good):
    boundary = lambda token: rf"(?<!\w){re.escape(token)}(?!\w)"

    def sub(token, repl, text):
        return re.sub(boundary(token), lambda _m: repl, text)

    if "\ufffd" in bad and bad in md:
        return md.replace(bad, good, 1)
    if re.search(boundary(bad), md):
        return sub(bad, good, md)
    escaped = bad.replace("*", r"\*").replace("_", r"\_")
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


def _fffd_patches(src, dst):
    patches = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, src, dst).get_opcodes():
        if tag == "replace" and "\ufffd" in src[i1:i2] and dst[j1:j2]:
            patches.append((src[i1:i2], dst[j1:j2]))
    return patches


def _splice_markdown(md, jobs, ocrs):
    line_map = {}
    for job, ocr in zip(jobs, ocrs):
        oc = _norm_ocr(ocr)
        if job.rare_font and _valid_line_ocr(job, oc):
            line_map[job.norm.strip()] = oc.strip()

    out, applied = _replace_standalone_lines(md, line_map)
    votes, phrase_votes = defaultdict(Counter), defaultdict(Counter)
    unresolved = []

    for job, ocr in zip(jobs, ocrs):
        oc = _norm_ocr(ocr)
        src = job.norm
        if len(oc) < len(src) * 0.4:
            unresolved.extend(w for w in src.split() if _repairable(w, job.bad_words))
            continue
        if SequenceMatcher(None, src, oc).ratio() < 0.72:
            unresolved.extend(w for w in src.split() if _repairable(w, job.bad_words))
            continue
        for k, v in _pair(src, oc, job.bad_words).items():
            votes[k][v] += 1
        if SequenceMatcher(None, src, oc).ratio() >= 0.80:
            for pb, pg in _context_phrases(src.split(), oc.split()):
                phrase_votes[pb][pg] += 1

    for pb, ctr in phrase_votes.items():
        pg, _ = ctr.most_common(1)[0]
        if len(ctr) == 1 and pb in out:
            nxt = out.replace(pb, pg, 1)
            if nxt != out:
                applied[pb] = pg
                out = nxt

    replaced, conflicts = {}, []
    for bad, ctr in votes.items():
        good, _ = ctr.most_common(1)[0]
        if len(ctr) == 1:
            replaced[bad] = good
        else:
            conflicts.append(bad)

    for bad, good in sorted(replaced.items(), key=lambda kv: -len(kv[0])):
        nxt = _apply_one(out, bad, good)
        if nxt != out:
            applied[bad] = good
            out = nxt

    for job, ocr in zip(jobs, ocrs):
        if "\ufffd" not in job.norm:
            continue
        oc = _norm_ocr(ocr)
        if SequenceMatcher(None, job.norm, oc).ratio() < 0.72:
            continue
        for bad, good in _fffd_patches(job.norm, oc):
            if bad in out and good and not any(_bad(w) for w in good.split()):
                out = out.replace(bad, good, 1)
                applied[bad] = good

    unresolved = sorted({w for w in unresolved if _core(w) not in applied and w in md})
    return out, applied, unresolved + [w for w in conflicts if w in md]


def repair_markdown(pdf_path, markdown):
    jobs = _scan_pdf(pdf_path)
    if not jobs:
        return markdown, RepairReport(0, {}, [])
    print(f"Glyph repair: recognizing {len(jobs)} line(s)", flush=True)
    ocrs = _recognize_jobs(pdf_path, jobs)
    out, applied, unresolved = _splice_markdown(markdown, jobs, ocrs)
    return out, RepairReport(len(jobs), applied, unresolved)