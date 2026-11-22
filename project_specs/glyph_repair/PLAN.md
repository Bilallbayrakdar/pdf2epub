# ClearScan Glyph & Line Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect and repair all four ClearScan PDF text-layer corruption classes (`U+FFFD` unmapped glyphs, mixed-class/case word intrusions, fake shared-box ligatures, and small synthesized fonts) in one pass using fast `PP-OCRv5` ONNX line recognition with Surya fallback, then rebuild a clean, valid EPUB.

**Architecture:** A single `pypdfium2` scan extracts per-character glyph boxes and font names across the PDF, flagging ~11% of lines via deterministic structural signals without dictionary false positives. Flagged line crops run through batched `PaddlePaddle/latin_PP-OCRv5_mobile_rec_onnx` CTC inference on CPU (`onnxruntime`), escalating only low-confidence or low-agreement crops to Surya `RecognitionPredictor`. Verified reads splice back into Markdown at three scopes: standalone rare-font headings/cells (gated by glyph-box count), word-boundary swaps for visibly corrupt/shared-box tokens, and 2–3 word context-anchored swaps for plain-letter substitutions.

**Tech Stack:** Python 3.13 (`.venv`), `pypdfium2` (`FPDFText_GetFontInfo` / `get_charbox`), `onnxruntime`, `huggingface_hub`, `PyYAML`, `numpy`, `Pillow`, `surya-ocr`, `lxml`.

**Spec:** Approved bounded design in session (`modules/glyph_repair.py` seam called by `modules/pdf2md.py`).

## Global Constraints

- Activate `.venv` before running any Python command (`source .venv/bin/activate`).
- Install dependencies with `uv` (`uv pip install` in this `requirements.txt` repo; no `pyproject.toml` exists) and pin in `requirements.txt`.
- Keep code concise and idiomatic; do not add unsolicited documentation files or git commits.
- Remove temporary test scripts after verification succeeds unless the user asks to keep them.
- Preserve existing `/tmp/glyph_repair_ocr.json` `{sha1_hex: str}` compatibility so previously recognized Surya lines are reused without re-OCR.
- Never apply dictionary/spellcheck similarity guesses: colloquial or foreign tokens (`yapıyora`, `pontos`) and legitimate questions (`kim?'`, `cüret?'`, `misiniz?2`) must remain untouched.

## Review Focus

- Decorative or map labels with few glyph boxes (`IGa>ub`, 6 boxes) where OCR hallucinates multi-word text (`Ouranos's Genitals`, 17 non-space chars) must be rejected by the glyph-box count gate (`0.65 * n_boxes <= n_ocr_chars <= 1.45 * n_boxes`).
- Plain-letter OCR fixes on rare-font lines (`Tamam` -> `Tanıtım` on the copyright page) must only replace within their local neighbor context (`Tamam amacıyla` -> `Tanıtım amacıyla`), never clobbering `"Tamam, efendim"` in dialogue.
- Markdown-escaped characters (`tutm\*1:ı`, `gör\_ebilmek`), soft-hyphen joins (`aksiyomal:ik` from `aksiyo\ufffemal:ik`), and footnote tags (`çıkmıştı:2` -> `çıkmıştı<sup>2</sup>`) must resolve cleanly without breaking XHTML tags.
- Headings and TOC table cells (`# HERR'N 1 N RÜYRS 1`, `| HERRKLES |     |`, `#### Kııhrıımıınlıır`) must preserve their Markdown prefix/table delimiters so `contents_toc.link_contents` still links all 102 TOC entries.
- Trailing footnote digits (`oldu.1`, `misiniz?2`) and legitimate ligatures (`fi`, `fl`, `fa`, `ğu`, `ru`) must not trigger false-positive line flags or mangle footnote numbers.

---

### Task 1: Structural PDF Line Scanner (`_scan_pdf`)

**Files:**
- Modify: `modules/glyph_repair.py`
- Test: `tests/test_glyph_repair_pipeline.py` (temporary; removed in Task 4)

**Interfaces:**
- Produces:
  - `LineJob(NamedTuple)`: `(raw: str, norm: str, page_idx: int, crop_box: tuple[float, float, float, float], n_boxes: int, rare_font: bool, bad_words: frozenset[str])`
  - `_scan_pdf(pdf_path: str | Path) -> list[LineJob]`
  - `_bad(word: str) -> bool`

- [ ] **Step 1: Write the failing test for structural detection**

Create `tests/test_glyph_repair_pipeline.py`:

```python
from modules.glyph_repair import _bad, _scan_pdf


def test_bad_word_signals_and_negative_controls():
    for broken in ["kıı;a", "B1:1nu", "Klasi:\\c", "gör_ebilmek", "mümki.n", "ediY,ordu", "AfSON", "201\ufffd", "D9ğru"]:
        assert _bad(broken), f"expected _bad({broken!r})"
    for clean in ["pontos", "yapıyora", "kim?'", "cüret?'", "misiniz?2", "oldu.1", "HERAKLES'İN", "Şti.'ne"]:
        assert not _bad(clean), f"expected clean {clean!r}"


def test_scan_pdf_pinpoints_all_four_clearscan_classes():
    jobs = _scan_pdf("data/dokumen.pub_kahramanlar.pdf")
    assert 900 <= len(jobs) <= 1800, f"unexpected job count: {len(jobs)}"
    by_sub = lambda s: [j for j in jobs if s in j.raw]

    # 1. U+FFFD unmapped glyph
    assert by_sub("\ufffdcılığıyla")
    # 2. Mixed-class / standalone-1 heading + rare font
    hera = by_sub("HERR'N 1 N RÜYRS 1")
    assert hera and hera[0].rare_font and hera[0].n_boxes == 14
    # 3. Fake shared-box ligature words in normal body font
    oly = by_sub("Olyınpos Dağında")
    assert oly and "Olyınpos" in oly[0].bad_words
    hil = by_sub("hilcayesi")
    assert hil and any("hilcayesi" in w for w in hil[0].bad_words)
    # 4. Rare ClearScan fonts (copyright + TOC + author header)
    assert by_sub("Dağıam")[0].rare_font
    assert by_sub("HERRKLES")[0].rare_font
    assert by_sub("STEPHEN FAY")[0].rare_font
    # Negative control: colloquial 'yapıyora' in body font not flagged
    assert not by_sub("Ne yapıyora benziyorum")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && PYTHONPATH=. python -m unittest tests/test_glyph_repair_pipeline.py`
Expected: FAIL with `ImportError: cannot import name '_scan_pdf' from 'modules.glyph_repair'`.

- [ ] **Step 3: Implement `_scan_pdf` and widened `_bad` in `modules/glyph_repair.py`**

Add `LineJob`, per-character `FPDFText_GetFontInfo` scanning, fake shared-box detection, and rare-font (`<= 125` lines) tagging in `modules/glyph_repair.py`:

```python
import ctypes
from typing import NamedTuple
import pypdfium2.raw as pdfium_raw

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
    "ın", "in", "rn", "nı", "lc", "ic", "ıı", "ii", "iı", "ıi", "İı",
    "filı", "Alı", "riı", "ğal", "al", "lm", "lial",
})
_RARE_MAX_LINES = 125


class LineJob(NamedTuple):
    raw: str
    norm: str
    page_idx: int
    crop_box: tuple[float, float, float, float]
    n_boxes: int
    rare_font: bool
    bad_words: frozenset[str]


def _core(word: str) -> str:
    c = re.sub(r"[:;?]+$", "", word.strip(_EDGE))
    return re.sub(r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü.] )\d+$|(?<=[A-Za-zÇĞİÖŞÜçğıöşü.?])\d+$", "", c)


def _font_name(tp, idx: int, buf) -> str:
    buf[0] = b"\x00"
    pdfium_raw.FPDFText_GetFontInfo(tp, idx, buf, 128, None)
    return buf.value.decode("latin1", "ignore")


def _scan_pdf(pdf_path) -> list[LineJob]:
    pdf = pdfium.PdfDocument(pdf_path)
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

        # Group consecutive chars sharing a rounded glyph box; locate fake-box words
        boxes, word_idx, char_to_word = [], 0, []
        in_space = True
        for ch, _, _ in cur:
            if ch.isspace():
                in_space = True
                char_to_word.append(-1)
            else:
                if in_space:
                    if char_to_word and max(char_to_word) >= 0:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && PYTHONPATH=. python -m unittest tests/test_glyph_repair_pipeline.py`
Expected: `OK` (2 tests pass in ~18s).

---

### Task 2: Hybrid Line Recognizer (`PP-OCRv5` ONNX + Surya Fallback)

**Files:**
- Modify: `requirements.txt`
- Modify: `modules/glyph_repair.py`
- Test: `tests/test_glyph_repair_pipeline.py`

**Interfaces:**
- Consumes: `LineJob` from Task 1
- Produces:
  - `_onnx_recognize(images: list[Image.Image]) -> list[tuple[str, float]]`
  - `_recognize_jobs(pdf_path: str | Path, jobs: list[LineJob]) -> list[str]`

- [ ] **Step 1: Install `onnxruntime` via `uv` and pin in `requirements.txt`**

Run: `source .venv/bin/activate && uv pip install onnxruntime`
Add `onnxruntime==<installed_version>` to `requirements.txt`.

- [ ] **Step 2: Write the failing test for Turkish ONNX recognition and fallback**

Append to `tests/test_glyph_repair_pipeline.py`:

```python
import pypdfium2 as pdfium
from modules.glyph_repair import _onnx_recognize, _recognize_jobs, _scan_pdf


def test_hybrid_recognizer_reads_turkish_lines_and_headings():
    jobs = _scan_pdf("data/dokumen.pub_kahramanlar.pdf")
    targets = [
        j for j in jobs
        if any(k in j.raw for k in ("HERR'N 1 N RÜYRS 1", "Olyınpos Dağında", "Dağıam Ltd.", "hilcayesi"))
    ]
    assert len(targets) >= 4
    pdf = pdfium.PdfDocument("data/dokumen.pub_kahramanlar.pdf")
    imgs = [pdf[j.page_idx].render(scale=4, crop=j.crop_box).to_pil().convert("RGB") for j in targets]
    onnx_out = _onnx_recognize(imgs)
    assert len(onnx_out) == len(imgs)
    assert all(isinstance(t, str) and 0.0 <= c <= 1.0 for t, c in onnx_out)

    resolved = _recognize_jobs("data/dokumen.pub_kahramanlar.pdf", targets)
    joined = "\n".join(resolved)
    assert "HERA'NIN RÜYASI" in joined.upper() or "HERA'NIN" in joined.upper()
    assert "Olympos" in joined
    assert "Dağıtım" in joined
    assert "hikayesi" in joined
```

- [ ] **Step 3: Run test to verify it fails**

Run: `source .venv/bin/activate && PYTHONPATH=. python -m unittest tests/test_glyph_repair_pipeline.TestCase` (or `python -m unittest tests/test_glyph_repair_pipeline.py`)
Expected: FAIL with `ImportError: cannot import name '_onnx_recognize'`.

- [ ] **Step 4: Implement `_onnx_recognize` and `_recognize_jobs` in `modules/glyph_repair.py`**

Implement `PaddlePaddle/latin_PP-OCRv5_mobile_rec_onnx` download/session caching, `RecResizeImg` (`H=48`, BGR `[-1, 1]` normalization), greedy CTC decoding with mean token probability, Surya escalation on low confidence (`< 0.88` or prose ratio `< 0.75` or residual `_BAD`), and `/tmp/glyph_repair_ocr.json` persistence:

```python
import math
import numpy as np
import yaml

_MODEL_REPO = "PaddlePaddle/latin_PP-OCRv5_mobile_rec_onnx"
_MODEL_COMMIT = "89d3a50e2c27e2e7cceeab0e944c25c807d5db4f"
_ONNX_STATE = None


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
        resized = []
        max_w = 160
        for i in idxs:
            im = images[i]
            w = max(16, min(3200, int(math.ceil(48.0 * im.width / max(1, im.height)))))
            max_w = max(max_w, w)
            arr = np.asarray(im.resize((w, 48)), dtype=np.float32)[:, :, ::-1]  # RGB -> BGR
            arr = (arr / 255.0 - 0.5) / 0.5
            resized.append(arr.transpose(2, 0, 1))
        batch = np.zeros((len(idxs), 3, 48, max_w), dtype=np.float32)
        for bi, chw in enumerate(resized):
            batch[bi, :, :, :chw.shape[2]] = chw
        probs = sess.run(None, {in_name: batch})[0]
        if probs.min() < 0.0 or probs.max() > 1.001:
            e = np.exp(probs - probs.max(axis=-1, keepdims=True))
            probs = e / e.sum(axis=-1, keepdims=True)
        cls_ids = probs.argmax(axis=-1)
        cls_p = probs.max(axis=-1)
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


def _needs_surya(job: LineJob, text: str, conf: float) -> bool:
    if not text or conf < 0.88:
        return True
    clean = _norm_ocr(text)
    ns_ocr = len(re.sub(r"\s+", "", clean))
    if not (0.65 * job.n_boxes <= ns_ocr <= 1.45 * job.n_boxes):
        return True
    if any(_bad(w) for w in clean.split()):
        return True
    ratio = SequenceMatcher(None, job.norm.casefold(), clean.casefold()).ratio()
    return ratio < (0.48 if job.rare_font else 0.76)


def _recognize_jobs(pdf_path, jobs: list[LineJob]) -> list[str]:
    cache = json.loads(_CACHE.read_text()) if _CACHE.exists() else {}
    keys = [hashlib.sha1(j.raw.encode()).hexdigest() for j in jobs]
    miss_idx = [i for i, k in enumerate(keys) if k not in cache]
    if miss_idx:
        pdf = pdfium.PdfDocument(pdf_path)
        miss_imgs = [
            pdf[jobs[i].page_idx].render(scale=4, crop=jobs[i].crop_box).to_pil().convert("RGB")
            for i in miss_idx
        ]
        onnx_res = _onnx_recognize(miss_imgs)
        surya_todo = []
        for local_i, (i, (txt, conf)) in enumerate(zip(miss_idx, onnx_res)):
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
    return [cache[k] for k in keys]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `source .venv/bin/activate && PYTHONPATH=. python -m unittest tests/test_glyph_repair_pipeline.py`
Expected: `OK`.

---

### Task 3: Three-Tier Context-Safe Markdown Splicer (`repair_markdown`)

**Files:**
- Modify: `modules/glyph_repair.py`
- Test: `tests/test_glyph_repair_pipeline.py`

**Interfaces:**
- Consumes: `LineJob`, `_scan_pdf`, `_recognize_jobs`
- Produces: `repair_markdown(pdf_path, markdown) -> tuple[str, RepairReport]` (unchanged signature for `modules/pdf2md.py`)

- [ ] **Step 1: Write failing tests for heading/cell repair, context-anchored swaps, and hallucination rejection**

Append to `tests/test_glyph_repair_pipeline.py`:

```python
from modules.glyph_repair import LineJob, _splice_markdown


def test_splice_repairs_headings_context_words_and_rejects_hallucinations():
    md = (
        "#### Kııhrıımıınlıır\n\n"
        "© 201\ufffd, Stephen Fry\n\n"
        "Anatolialit Ajans \ufffdcılığıyla Alfa Basım Yayım Dağıam Ltd. Şti.'ne aittir. "
        "Tamam amacıyla, kaynak göstermek şarayla yapılacak kısa alıntılar.\n\n"
        "| HERRKLES                             |     |\n\n"
        "# HERR'N 1 N RÜYRS 1\n\n"
        'Olyınpos Dağında kahvaltı vakti. "Tamam, efendim," dedi. Ne yapıyora benziyorum? oinops pontos.\n'
    )
    jobs = [
        LineJob("Kııhrıımıınlıır", "Kııhrıımıınlıır", 2, (0, 0, 10, 10), 11, True, frozenset({"Kııhrıımıınlıır"})),
        LineJob("© 201\ufffd, Stephen Fry", "© 201\ufffd, Stephen Fry", 2, (0, 0, 10, 10), 16, True, frozenset({"201\ufffd"})),
        LineJob(
            "Anatolialit Ajans \ufffdcılığıyla Alfa Basım Yayım Dağıam Ltd. Şti:ne aittir. Tamam amacıyla, kaynak göstermek şarayla yapılacak",
            "Anatolialit Ajans \ufffdcılığıyla Alfa Basım Yayım Dağıam Ltd. Şti:ne aittir. Tamam amacıyla, kaynak göstermek şarayla yapılacak",
            2, (0, 0, 10, 10), 95, True, frozenset({"\ufffdcılığıyla", "Şti:ne"}),
        ),
        LineJob("HERRKLES", "HERRKLES", 4, (0, 0, 10, 10), 8, True, frozenset()),
        LineJob("HERR'N 1 N RÜYRS 1", "HERR'N 1 N RÜYRS 1", 16, (0, 0, 10, 10), 14, True, frozenset({"1"})),
        LineJob("Olyınpos Dağında kahvaltı vakti.", "Olyınpos Dağında kahvaltı vakti.", 16, (0, 0, 10, 10), 28, False, frozenset({"Olyınpos"})),
        LineJob("IGa>ub", "IGa>ub", 11, (0, 0, 10, 10), 6, True, frozenset({"IGa>ub"})),
    ]
    ocrs = [
        "Kahramanlar",
        "© 2018, Stephen Fry",
        "Anatolialit Ajans aracılığıyla Alfa Basım Yayım Dağıtım Ltd. Şti.'ne aittir. Tanıtım amacıyla, kaynak göstermek şartıyla yapılacak",
        "HERAKLES",
        "HERA'NIN RÜYASI",
        "Olympos Dağında kahvaltı vakti.",
        "Ouranos's Genitals",
    ]
    out, rep = _splice_markdown(md, jobs, ocrs)
    assert "\ufffd" not in out
    assert "#### Kahramanlar" in out
    assert "© 2018, Stephen Fry" in out
    assert "aracılığıyla" in out and "Dağıtım Ltd." in out
    assert "Tanıtım amacıyla" in out and "göstermek şartıyla" in out
    assert "| HERAKLES |" in out or "| HERAKLES" in out
    assert "# HERA'NIN RÜYASI" in out
    assert "Olympos Dağında" in out
    # Dialogue "Tamam", colloquial "yapıyora", Greek "pontos" untouched; hallucination rejected
    assert '"Tamam, efendim,"' in out
    assert "Ne yapıyora benziyorum?" in out
    assert "oinops pontos." in out
    assert "Genitals" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && PYTHONPATH=. python -m unittest tests/test_glyph_repair_pipeline.py`
Expected: FAIL with `ImportError: cannot import name '_splice_markdown'`.

- [ ] **Step 3: Implement `_splice_markdown` and wire `repair_markdown` in `modules/glyph_repair.py`**

Implement the three splice tiers:
1. **Standalone rare-font line splice:** when a rare-font job's normalized text matches the body of a Markdown heading (`# ...`) or TOC table cell (`| ... | page |`), and the OCR text passes the glyph-box gate (`0.65 * job.n_boxes <= ns_ocr <= 1.45 * job.n_boxes`) plus non-space similarity `>= 0.50` without introducing `_BAD` tokens, rewrite that heading/cell body in place.
2. **Global corrupt-word splice:** on aligned opcodes (`ratio >= 0.72`), words that are in `job.bad_words` or match `_bad` use `_pair` + `_apply_one` (handling `\ufffd`, `\*`, `\_`, dehyphenation, `<sup>n</sup>`, and line-split hyphens).
3. **Context-anchored plain-word splice:** on rare-font or flagged lines (`ratio >= 0.80`), 1-to-1 word replacements `(src_w, dst_w)` where `src_w` has no punctuation signal (`Dağıam` -> `Dağıtım`, `Tamam` -> `Tanıtım`, `şarayla` -> `şartıyla`, `yanşmalan` -> `yarışmaları`) are applied **only** together with their adjacent unchanged neighbor word from the opcode (`prev_w + " " + src_w` or `src_w + " " + next_w`), preserving trailing punctuation and case.

```python
def _valid_line_ocr(job: LineJob, ocr_clean: str) -> bool:
    ns_ocr = re.sub(r"\s+", "", ocr_clean)
    if not ns_ocr or not (0.65 * job.n_boxes <= len(ns_ocr) <= 1.45 * job.n_boxes):
        return False
    if any(_bad(w) for w in ocr_clean.split()):
        return False
    ns_src = re.sub(r"\s+", "", job.norm)
    r = SequenceMatcher(None, ns_src.casefold(), ns_ocr.casefold()).ratio()
    return r >= (0.50 if job.rare_font else 0.72)


def _replace_standalone_lines(md: str, line_map: dict[str, str]) -> tuple[str, dict[str, str]]:
    if not line_map:
        return md, {}
    applied = {}
    out_lines = []
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
```

Extend `_pair` to accept `bad_words: frozenset[str]` (treating `w in bad_words or _bad(w)` as a repairable token and allowing digits/years when `\ufffd` is repaired, e.g. `201\ufffd` -> `2018`, plus `\_` escape handling in `_apply_one`), and collect context-anchored `(phrase_bad, phrase_good)` pairs for 1-to-1 plain-letter differences on high-agreement (`>= 0.80`) lines. Wire `repair_markdown(pdf_path, markdown)` to call `_scan_pdf`, `_recognize_jobs`, and `_splice_markdown`.

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && PYTHONPATH=. python -m unittest tests/test_glyph_repair_pipeline.py`
Expected: `OK` (all 4 tests pass).

---

### Task 4: Full-Book Execution, EPUB Rebuild, and Test Cleanup

**Files:**
- Update (untracked data): `data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.md`, `data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.epub`
- Delete: `tests/test_glyph_repair_pipeline.py` (temporary test per user rule) and `modules/lexicon_repair.py` (unused leftover from the discarded dictionary experiment)

- [ ] **Step 1: Run `repair_markdown` against `data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.md`**

Run:
```bash
source .venv/bin/activate && PYTHONPATH=. python -u -c "
from pathlib import Path
from modules.glyph_repair import repair_markdown
md_path = Path('data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.md')
fixed, rep = repair_markdown('data/dokumen.pub_kahramanlar.pdf', md_path.read_text(encoding='utf-8'))
print(f'lines={rep.lines} replaced={len(rep.replaced)} unresolved={len(rep.unresolved)}')
assert '\ufffd' not in fixed, f'remaining FFFD: {fixed.count(chr(0xfffd))}'
md_path.write_text(fixed, encoding='utf-8')
"
```
Expected: `0` remaining `\ufffd` characters in the written Markdown.

- [ ] **Step 2: Rebuild the EPUB non-interactively**

Run:
```bash
source .venv/bin/activate && python -u main.py data/dokumen.pub_kahramanlar.pdf data --skip-md --non-interactive
```
Expected: `XHTML check passed for 6 document(s).` and `EPUB creation complete: data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.epub`.

- [ ] **Step 3: Run every check in the Verification section below**

Execute all verification commands in `## Verification` and confirm every check prints `PASS`.

- [ ] **Step 4: Remove temporary test and discarded dictionary files**

Run: `rm -rf tests/test_glyph_repair_pipeline.py tests modules/lexicon_repair.py` (leaving no untracked test scripts or unused lexicon module behind).

---

## Verification

This section explains how to verify each newly implemented feature independently and on the generated EPUB. Always activate `.venv` first (`source .venv/bin/activate`).

### 1. Verify Structural ClearScan Line Detection (`_scan_pdf` & `_bad`)

Confirms all four ClearScan failure classes (`U+FFFD`, mixed-class/standalone-`1` intrusions, fake shared-box ligatures, and rare synthesized fonts) are detected in `dokumen.pub_kahramanlar.pdf`, while ~85–90% of clean prose and colloquial/Greek words (`yapıyora`, `pontos`, trailing `?`) are left unflagged:

```bash
source .venv/bin/activate && PYTHONPATH=. python -c "
from modules.glyph_repair import _bad, _scan_pdf
jobs = _scan_pdf('data/dokumen.pub_kahramanlar.pdf')
assert 900 <= len(jobs) <= 1800, len(jobs)
assert any('\ufffd' in j.raw for j in jobs)
assert any(\"HERR'N 1 N RÜYRS 1\" in j.raw and j.rare_font for j in jobs)
assert any('Olyınpos' in j.bad_words for j in jobs)
assert any('Dağıam' in j.raw and j.rare_font for j in jobs)
assert not any('Ne yapıyora benziyorum' in j.raw for j in jobs)
for clean in ('pontos', 'yapıyora', \"kim?'\", 'misiniz?2', 'oldu.1'):
    assert not _bad(clean), clean
print('PASS: structural scanner flagged', len(jobs), 'lines')
"
```
Expected output: `PASS: structural scanner flagged <N> lines` with `<N>` around `1,200–1,400` (out of `11,962` PDF lines).

### 2. Verify Hybrid `PP-OCRv5` ONNX + Surya Fallback Recognition

Confirms the 8 MB ONNX CTC recognizer loads, decodes Turkish characters (`ğ, ı, İ, ö, ş, ü`) accurately at high speed on CPU, and escalates low-confidence / heavily garbled display crops to Surya when needed:

```bash
source .venv/bin/activate && PYTHONPATH=. python -c "
import time
from modules.glyph_repair import _recognize_jobs, _scan_pdf
jobs = [
    j for j in _scan_pdf('data/dokumen.pub_kahramanlar.pdf')
    if any(s in j.raw for s in (\"HERR'N 1 N RÜYRS 1\", 'Olyınpos Dağında', 'Dağıam Ltd.', 'hilcayesi'))
]
t0 = time.time()
reads = _recognize_jobs('data/dokumen.pub_kahramanlar.pdf', jobs)
joined = '\n'.join(reads)
assert \"HERA'NIN RÜYASI\" in joined.upper()
assert 'Olympos' in joined and 'Dağıtım' in joined and 'hikayesi' in joined
print(f'PASS: {len(reads)} lines recognized in {time.time() - t0:.2f}s')
"
```
Expected output: `PASS: <N> lines recognized in <T>s` with `HERA'NIN RÜYASI`, `Olympos`, `Dağıtım`, and `hikayesi` present.

### 3. Verify Context-Safe Splicing & Hallucination Protection

Confirms plain-letter replacements (`Tamam amacıyla` -> `Tanıtım amacıyla`) never alter unrelated dialogue (`"Tamam, efendim"`), and map/diagram hallucinations (`IGa>ub` -> `Ouranos's Genitals`) are blocked by the glyph-box count gate:

```bash
source .venv/bin/activate && PYTHONPATH=. python -c "
from modules.glyph_repair import LineJob, _splice_markdown
sample = 'Dağıam Ltd. Tamam amacıyla şarayla. \"Tamam,\" dedi. Ne yapıyora benziyorum? oinops pontos.\n# HERR'\''N 1 N RÜYRS 1\n'
jobs = [
    LineJob('Dağıam Ltd. Tamam amacıyla şarayla.', 'Dağıam Ltd. Tamam amacıyla şarayla.', 2, (0,0,10,10), 31, True, frozenset()),
    LineJob(\"HERR'N 1 N RÜYRS 1\", \"HERR'N 1 N RÜYRS 1\", 16, (0,0,10,10), 14, True, frozenset({'1'})),
    LineJob('IGa>ub', 'IGa>ub', 11, (0,0,10,10), 6, True, frozenset({'IGa>ub'})),
]
ocrs = ['Dağıtım Ltd. Tanıtım amacıyla şartıyla.', \"HERA'NIN RÜYASI\", \"Ouranos's Genitals\"]
out, _ = _splice_markdown(sample, jobs, ocrs)
assert 'Dağıtım Ltd. Tanıtım amacıyla şartıyla.' in out
assert '\"Tamam,\" dedi.' in out
assert 'Ne yapıyora benziyorum?' in out and 'oinops pontos.' in out
assert \"# HERA'NIN RÜYASI\" in out and 'Genitals' not in out
print('PASS: context-safe splice and hallucination gate verified')
"
```
Expected output: `PASS: context-safe splice and hallucination gate verified`.

### 4. Verify Rebuilt EPUB End-to-End (Zero Diamonds, Headings, TOC Links, Valid XHTML)

Unpacks `data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.epub` and verifies:
1. **Zero `U+FFFD` (``) diamond characters** remain anywhere in the chapter XHTML or TOC.
2. **Garbled headings, rare-font text, and shared-box words** (`HERA'NIN RÜYASI`, `HERAKLES`, `STEPHEN FRY`, `Kahramanlar`, `Dağıtım`, `Tanıtım amacıyla`, `şartıyla`, `Olympos`, `hikayesi`, plus earlier `:;?` repairs) are fixed.
3. **Negative controls** (`oinops pontos`, `Ne yapıyora benziyorum?`, `kim?'`, `cüret?'`, `misiniz?2`) are intact.
4. **All 6 XML/XHTML documents inside the EPUB** pass strict `lxml` parsing with `0` syntax errors, and all `İçindekiler` anchor targets resolve:

```bash
source .venv/bin/activate && PYTHONPATH=. python -c "
import re, zipfile
from modules.xhtml_gate import find_xhtml_errors

epub = 'data/dokumen.pub_kahramanlar/dokumen.pub_kahramanlar.epub'
with zipfile.ZipFile(epub) as zf:
    for name in zf.namelist():
        if name.endswith(('.xhtml', '.opf', '.ncx', '.xml')):
            errs = find_xhtml_errors(zf.read(name).decode('utf-8'))
            assert errs == [], (name, errs[:3])
    ch = zf.read('OPS/s00000-dokumen.xhtml').decode('utf-8')
    toc = zf.read('OPS/TOC.xhtml').decode('utf-8')

assert '\ufffd' not in ch and '\ufffd' not in toc, 'U+FFFD diamond remains!'
for gone in (\"HERR'N\", 'RÜYRS', 'Kııhrıımıınlıır', 'Dağıam', 'şarayla', 'Olyınpos', 'hilcayesi', 'Şti:ne', 'da;t:ıa', 'Rhac;lamanthys'):
    assert gone not in ch, f'still broken: {gone}'
for expected in (\"HERA'NIN RÜYASI\", 'STEPHEN FRY', 'Dağıtım', 'Tanıtım amacıyla', 'şartıyla', 'Olympos', 'hikayesi', 'Rhadamanthys', 'aksiyomatik'):
    assert expected in ch, f'missing fix: {expected}'
for keep in ('oinops pontos', 'yapıyora benziyorum', \"kim?'\", \"cüret?'\", 'misiniz?2'):
    assert keep in ch, f'clobbered valid text: {keep}'

ids = set(re.findall(r'id=\"([^\"]+)\"', ch))
hrefs = re.findall(r'href=\"s00000-dokumen\.xhtml#([^\"]+)\"', toc)
assert len(hrefs) >= 100 and all(h in ids for h in hrefs), len(hrefs)
print(f'PASS: EPUB clean (0 FFFD, {len(hrefs)} TOC links resolved, strict XHTML valid)')
"
```
Expected output: `PASS: EPUB clean (0 FFFD, <102+> TOC links resolved, strict XHTML valid)`.
