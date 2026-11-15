import re
from difflib import SequenceMatcher
from typing import NamedTuple

_TRANS = str.maketrans(
    {
        "ı": "i", "İ": "i", "I": "i", "l": "i", "1": "i", "0": "o",
        "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o", "ç": "c", "Ç": "c", "â": "a", "î": "i", "û": "u",
    }
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_PAGE_PAIR = re.compile(r"([^,]+?)\s*,\s*(\d+)")
_MATCH_MIN = 0.64


class TocEntry(NamedTuple):
    title: str
    level: int
    anchor: str
    href: str = ""


def _key(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"[^a-z]", "", text.translate(_TRANS).lower())


def _label(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _link(label: str, anchor: str) -> str:
    safe = label.replace("\\", "\\\\").replace("]", r"\]")
    return f"[{safe}](#{anchor})" if anchor else safe


def link_contents(markdown: str) -> tuple[str, list[TocEntry]]:
    """Turn an İçindekiler section into links that point at later headings."""
    lines = markdown.splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if (m := _HEADING.match(line)) and _key(m.group(2)) == "icindekiler"
        ),
        None,
    )
    if start is None:
        return markdown, []

    bits: list[tuple[int, str, str, int]] = []
    end = len(lines)
    i = start + 1
    while i < len(lines):
        line = lines[i]
        heading = _HEADING.match(line)
        if heading:
            nxt = next((lines[j] for j in range(i + 1, min(i + 8, len(lines))) if lines[j].strip()), "")
            title = heading.group(2)
            if nxt and not nxt.startswith("|") and not _HEADING.match(nxt) and len(nxt) > 60 and not _PAGE_PAIR.search(title):
                end = i
                break
            pairs = _PAGE_PAIR.findall(title)
            if pairs:
                bits.extend((i, t.strip(), p, 2) for t, p in pairs)
            else:
                bits.append((i, title, "", 1))
        elif line.startswith("|") and not re.match(r"^\|\s*-+", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] and not set(cells[0]) <= {"-", ":", " "}:
                page = re.sub(r"[^\d]", "", cells[1] if len(cells) > 1 else "")
                bits.append((i, cells[0], page, 1 if not page else 2))
        elif _PAGE_PAIR.search(line) and not line.startswith("!"):
            bits.extend((i, t.strip(), p, 2) for t, p in _PAGE_PAIR.findall(line))
        i += 1

    headings = [
        (n, _HEADING.match(line).group(2))
        for n, line in enumerate(lines)
        if n >= end and _HEADING.match(line)
    ]
    cursor = 0
    matched: list[tuple[int, str] | None] = []
    for _line, title, _page, _level in bits:
        kt = _key(title)
        found = None
        if len(kt) >= 3:
            for idx in range(cursor, len(headings)):
                hk = _key(headings[idx][1])
                if len(hk) < 3:
                    continue
                if SequenceMatcher(None, kt, hk).ratio() >= _MATCH_MIN:
                    found = idx
                    break
        if found is None:
            matched.append(None)
            continue
        cursor = found + 1
        matched.append(headings[found])

    by_heading: dict[int, str] = {}
    by_line: dict[int, list[tuple[str, str, int, str]]] = {}
    entries: list[TocEntry] = []
    seq = 0
    for (line_no, title, page, level), hit in zip(bits, matched):
        anchor = ""
        if hit is not None:
            seq += 1
            anchor = f"ch-{seq:03d}"
            by_heading.setdefault(hit[0], anchor)
        label = _label(title)
        entries.append(TocEntry(label, level, anchor))
        by_line.setdefault(line_no, []).append((label, page, level, anchor))

    for line_no, anchor in by_heading.items():
        if "{:" not in lines[line_no]:
            lines[line_no] = lines[line_no].rstrip() + f" {{: #{anchor}}}"
    if "{:" not in lines[start]:
        lines[start] = lines[start].rstrip() + " {: #icindekiler}"

    for line_no, group in by_line.items():
        raw = lines[line_no]
        if raw.startswith("|"):
            label, page, _level, anchor = group[0]
            lines[line_no] = f"| {_link(label, anchor)} | {page} |"
        elif _HEADING.match(raw) and not _PAGE_PAIR.search(raw):
            label, _page, _level, anchor = group[0]
            lines[line_no] = f"**{_link(label, anchor)}**"
        else:
            lines[line_no] = "\n".join(
                f"- {_link(label, anchor)}" + (f", {page}" if page else "")
                for label, page, _level, anchor in group
            )

    return "\n".join(lines) + ("\n" if markdown.endswith("\n") else ""), entries


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
