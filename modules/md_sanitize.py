"""Sanitize marker-pdf split HTML entities, orphan inline tags, and stray numeric charrefs."""
import html
import re

# @start PDF-MD-CLEAN
_CODE_MASK = re.compile(
    r"(?m)^(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^\1[ \t]*$|\Z)"
    r"|(?<!\\)(?<!`)(`+)(?!`)(?:(?!\n\n)[\s\S])+?(?<!`)\2(?!`)"
)
_AST_FN = re.compile(r"(?m)^([ \t]*)<sup\b[^>]*>&#</sup>x27;(?=\s)", re.I)
_TAG_SPLIT_ENT = re.compile(
    r"<(sup|sub|i|b|em|strong|u|small|mark)\b[^>]*>([^<]*?)"
    r"(&(?:#[0-9a-fA-FxX]*|[A-Za-z0-9]*))</\1>([0-9A-Za-z]+;)",
    re.I,
)
_ANY_SPLIT_ENT = re.compile(
    r"(&(?:#[0-9a-fA-FxX]*|[A-Za-z0-9]*))(</?[A-Za-z][A-Za-z0-9]*\b[^>]*>)([0-9A-Za-z]+;)"
)
_VALID_ENT = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]{1,31});")
_INLINE_TAG = re.compile(r"<(/?)(sup|sub|i|b|em|strong|u|small|mark)\b([^>]*)>", re.I)
_BAD_CHARREF = re.compile(r"&#(?!(?:[0-9]+|[xX][0-9a-fA-F]+);)")
_QUOTE_ENTS = frozenset({"&quot;", "&#x27;", "&#39;", "&apos;"})


def _resolve_ent(ent: str) -> str:
    return html.unescape(ent) if ent.lower() in _QUOTE_ENTS else ent


def _fix_tag_split(m: re.Match) -> str:
    tag, lead, head, tail = m.groups()
    ent = head + tail
    if not _VALID_ENT.fullmatch(ent):
        return m.group(0)
    val = _resolve_ent(ent)
    if tag.lower() == "sup":
        return (f"<{tag}>{lead}</{tag}>" if lead else "") + val
    return f"<{tag}>{lead}{val}</{tag}>"


def _fix_any_split(m: re.Match) -> str:
    head, tag, tail = m.groups()
    ent = head + tail
    if not _VALID_ENT.fullmatch(ent):
        return m.group(0)
    val = _resolve_ent(ent)
    return f"{val}{tag}" if tag.startswith("</") else f"{tag}{val}"


def _balance_line_tags(line: str) -> str:
    if "<" not in line:
        return line
    parts: list[tuple[str, str]] = []
    stack: list[tuple[str, int]] = []
    last = 0
    for m in _INLINE_TAG.finditer(line):
        parts.append(("txt", line[last : m.start()]))
        last = m.end()
        closing, name, rest = m.group(1), m.group(2).lower(), m.group(3)
        if rest.rstrip().endswith("/"):
            parts.append(("tag", m.group(0)))
        elif not closing:
            stack.append((name, len(parts)))
            parts.append(("open", m.group(0)))
        else:
            idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == name), -1)
            if idx >= 0:
                for unclosed_name, _ in reversed(stack[idx + 1 :]):
                    parts.append(("close", f"</{unclosed_name}>"))
                del stack[idx:]
                parts.append(("close", m.group(0)))
    parts.append(("txt", line[last:]))
    for unclosed_name, _ in reversed(stack):
        parts.append(("close", f"</{unclosed_name}>"))
    return "".join(val for _, val in parts)


def sanitize_marker_markdown(text: str) -> str:
    """Repair marker-pdf split entities, orphan inline tags, and bare &# sequences."""
    masks: dict[str, str] = {}

    def _stash(m: re.Match) -> str:
        key = f"\x00CODE{len(masks)}\x00"
        masks[key] = m.group(0)
        return key

    masked = _CODE_MASK.sub(_stash, text)
    masked = _AST_FN.sub(r"\1<sup>*</sup>", masked)
    masked = _TAG_SPLIT_ENT.sub(_fix_tag_split, masked)
    masked = _ANY_SPLIT_ENT.sub(_fix_any_split, masked)
    masked = "\n".join(_balance_line_tags(ln) for ln in masked.split("\n"))
    masked = _BAD_CHARREF.sub("&amp;#", masked)
    for key, orig in masks.items():
        masked = masked.replace(key, orig)
    return masked
# @end PDF-MD-CLEAN
