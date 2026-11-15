# Namespace: [XHT] (XHTML syntax gate)

## 1. Overview

Detects XML syntax errors in generated XHTML, repairs the ones that a tolerant HTML parse can correct, and reports any error that is still present so a broken chapter is never stored in the EPUB.

## 2. Component Logic

### [XHT-CHECK] Find syntax errors
- **Intent:** List well-formedness errors with line, column, and message.
- **Logic:**
  1. Parse the document as strict XML.
  2. When that fails, parse again in recovery mode and collect ERROR and FATAL entries from the parser log.
  3. Return an empty list when the document is well-formed.
  4. The parser log keeps 100 errors. When it is full, scan later lines for unclosed tags and append those.
- **Constraint:** Use lxml. Do not stop at the first error when the recovery log contains more.

### [XHT-REPAIR] Repair XHTML
- **Intent:** Rewrite a broken document into well-formed XHTML without changing the readable text.
- **Logic:**
  1. Strip XML-illegal control characters.
  2. Replace `--` inside comments so the comment stays legal.
  3. Parse the document with the HTML parser in recovery mode and serialize it as XML, so void tags are closed, entities are defined, bare `&` and `<` are escaped, and block tags are lifted out of `<p>`.
  4. Leave an already well-formed document unchanged. The caller decides that.
- **Constraint:** Use lxml's HTML recovery parser. Do not ask a person to edit the Markdown.

### [XHT-GATE] Accept or report
- **Intent:** Ship only well-formed XHTML, and name any error that repair could not remove.
- **Logic:**
  1. Run XHT-CHECK.
  2. When the list is empty, return the original document.
  3. Otherwise run XHT-REPAIR and check again.
  4. When the repaired document is well-formed, print each original error as `label:line:column: message` and return the repaired document.
  5. When errors remain, or repair itself fails, print each one the same way and raise `UnresolvedXhtmlError`. Do not return the broken document.
  6. Package XML that is not HTML (`package.opf`, `toc.ncx`, `container.xml`) is checked only. Errors are reported and raised; it is not run through the HTML repair.

## 3. Verification

### [XHT-CHECK]
```bash
. .venv/bin/activate && python -c "
from modules.xhtml_gate import find_xhtml_errors
bad='''<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><p>A&nbsp;B & C<br>x</p></body></html>'''
errs=find_xhtml_errors(bad)
assert any('nbsp' in e.message for e in errs), errs
assert all(e.line>=1 and e.column>=1 for e in errs)
print('PASS', errs[0].line, errs[0].column, errs[0].message)
"
```
Expected: a line starting with `PASS` that includes `Entity 'nbsp' not defined`.

### [XHT-REPAIR]
```bash
. .venv/bin/activate && python -c "
from modules.xhtml_gate import find_xhtml_errors, repair_xhtml
bad='''<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><p>Tom &amp; Jerry&nbsp; A & B<br>x</p><!-- bad -- c --><p>a < b</body></html>'''
fixed=repair_xhtml(bad)
assert find_xhtml_errors(fixed)==[], find_xhtml_errors(fixed)
assert 'Tom' in fixed and 'Jerry' in fixed and '<br/>' in fixed
print('PASS')
"
```
Expected: `PASS`.

### [XHT-GATE]
```bash
. .venv/bin/activate && python -c "
import modules.xhtml_gate as g
bad='''<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><p>Tom &amp; Jerry&nbsp; A & B<br>x</p><p>a < b</body></html>'''
good='''<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><p>ok</p></body></html>'''
fixed=g.ensure_xhtml(bad,'OPS/sample.xhtml')
assert g.find_xhtml_errors(fixed)==[] and 'Tom' in fixed
assert g.ensure_xhtml(good,'OPS/ok.xhtml')==good
orig=g.repair_xhtml
g.repair_xhtml=lambda document: document
try:
    g.ensure_xhtml(bad,'OPS/stuck.xhtml')
    raise SystemExit('expected unresolved')
except g.UnresolvedXhtmlError as exc:
    assert 'OPS/stuck.xhtml' in str(exc)
finally:
    g.repair_xhtml=orig
print('PASS')
"
```
Expected: `Fixed N XHTML syntax error(s) in OPS/sample.xhtml` lines, then `Unresolved XHTML syntax error(s) in OPS/stuck.xhtml`, then `PASS`.
