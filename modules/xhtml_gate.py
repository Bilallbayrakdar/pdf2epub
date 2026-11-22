import re
import sys
from html.parser import HTMLParser
from typing import NamedTuple

from lxml import etree, html


class XhtmlError(NamedTuple):
    line: int
    column: int
    message: str


class UnresolvedXhtmlError(Exception):
    def __init__(self, label: str, errors: list[XhtmlError]):
        self.label = label
        self.errors = errors
        detail = "\n".join(
            f"{label}:{err.line}:{err.column}: {err.message}" for err in errors
        )
        super().__init__(detail or label)


_XML_LOG_LIMIT = 100
_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
_ILLEGAL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_XML_DECL = re.compile(r"^\s*<\?xml\b[^?]*\?>", re.IGNORECASE)
_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)


def _as_bytes(document: str) -> bytes:
    return document.encode("utf-8", errors="replace")


def _report_unresolved(label: str, errors: list[XhtmlError]) -> None:
    print(f"Unresolved XHTML syntax error(s) in {label}:", file=sys.stderr)
    for err in errors:
        print(
            f"  {label}:{err.line}:{err.column}: {err.message}",
            file=sys.stderr,
        )


# @start XHT-CHECK
def find_xhtml_errors(document: str) -> list[XhtmlError]:
    raw = _as_bytes(document)
    strict = etree.XMLParser(recover=False, encoding="utf-8")
    try:
        etree.fromstring(raw, strict)
        return []
    except etree.XMLSyntaxError as exc:
        recover = etree.XMLParser(recover=True, encoding="utf-8")
        try:
            etree.fromstring(raw, recover)
        except etree.XMLSyntaxError:
            pass
        seen: set[tuple[int, int, str]] = set()
        errors: list[XhtmlError] = []
        for err in recover.error_log:
            if err.level_name not in ("ERROR", "FATAL"):
                continue
            item = (err.line, err.column, err.message.strip())
            if item in seen:
                continue
            seen.add(item)
            errors.append(XhtmlError(*item))
        if not errors:
            return [XhtmlError(0, 0, str(exc).strip())]
        # libxml2 keeps only the first 100 errors. Past that, a tag stack
        # still names every later mismatch the log dropped.
        if len(errors) >= _XML_LOG_LIMIT:
            last_line = max(err.line for err in errors)
            errors.extend(
                err for err in _structural_errors(document) if err.line > last_line
            )
        return errors
# @end XHT-CHECK


# @start XHT-CHECK-STACK
class _TagStack(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[tuple[str, int, int]] = []
        self.errors: list[XhtmlError] = []

    def handle_starttag(self, tag, attrs):
        line, column = self.getpos()
        column += 1
        if tag in _VOID:
            self.errors.append(
                XhtmlError(line, column, f"Opening and ending tag mismatch: {tag}")
            )
            return
        self.stack.append((tag, line, column))

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        line, column = self.getpos()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] != tag:
                continue
            for open_tag, open_line, open_column in self.stack[index + 1 :]:
                self.errors.append(
                    XhtmlError(
                        open_line,
                        open_column,
                        f"Opening and ending tag mismatch: {open_tag}",
                    )
                )
            del self.stack[index:]
            return
        self.errors.append(
            XhtmlError(line, column + 1, f"Opening and ending tag mismatch: {tag}")
        )

    def close(self):
        super().close()
        for tag, line, column in self.stack:
            self.errors.append(
                XhtmlError(line, column, f"Opening and ending tag mismatch: {tag}")
            )


def _structural_errors(document: str) -> list[XhtmlError]:
    checker = _TagStack()
    checker.feed(document)
    checker.close()
    return checker.errors
# @end XHT-CHECK-STACK


# @start XHT-REPAIR-CMT
def _fix_comments(text: str) -> str:
    def repl(match: re.Match) -> str:
        body = match.group(1).replace("--", "- -")
        if body.endswith("-"):
            body += " "
        return f"<!--{body}-->"

    return _COMMENT.sub(repl, text)
# @end XHT-REPAIR-CMT


# @start XHT-REPAIR
def repair_xhtml(document: str) -> str:
    text = _fix_comments(_ILLEGAL.sub("", document))
    text = _XML_DECL.sub("", text, count=1)
    parser = html.HTMLParser(encoding="utf-8", recover=True)
    # @start XHT-REPAIR-HTML
    # HTML recovery closes void tags, escapes bare '&' and '<', expands HTML
    # entities, and lifts block elements out of <p>. XML serialization then
    # emits well-formed XHTML.
    root = html.document_fromstring(_as_bytes(text), parser=parser)
    repaired = etree.tostring(
        root, xml_declaration=True, encoding="utf-8", method="xml"
    ).decode("utf-8")
    # @end XHT-REPAIR-HTML
    return _fix_comments(repaired)
# @end XHT-REPAIR


# @start XHT-GATE
def ensure_xhtml(document: str, label: str) -> str:
    found = find_xhtml_errors(document)
    if not found:
        return document
    try:
        repaired = repair_xhtml(document)
    except Exception as exc:
        error = XhtmlError(1, 1, f"repair failed: {exc}")
        _report_unresolved(label, [error])
        raise UnresolvedXhtmlError(label, [error]) from exc
    remaining = find_xhtml_errors(repaired)
    if remaining:
        _report_unresolved(label, remaining)
        raise UnresolvedXhtmlError(label, remaining)
    print(f"Fixed {len(found)} XHTML syntax error(s) in {label}:")
    for err in found:
        print(f"  {label}:{err.line}:{err.column}: {err.message}")
    return repaired


def ensure_xml(document: str, label: str) -> str:
    found = find_xhtml_errors(document)
    if found:
        _report_unresolved(label, found)
        raise UnresolvedXhtmlError(label, found)
    return document
# @end XHT-GATE
