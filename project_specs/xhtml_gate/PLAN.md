# XHTML syntax gate

Repair generated XHTML before it is stored in the EPUB. Markdown from marker-pdf is often fine, but the HTML it renders is not always XML: undefined entities (`&nbsp;`), a bare `&`, unclosed `br`/`img`, and a `<p>` left open. Reading apps report those as syntax errors at a line in the unpacked XHTML.

## Approach

Check with lxml's strict XML parser. On failure, collect every ERROR/FATAL from a recovery parse so the report has line, column, and message. Repair by stripping illegal control characters, neutralizing `--` inside comments, then parsing with lxml's HTML recovery parser and serializing XML. That closes void tags, escapes bare `&` and `<`, defines entities, and lifts block elements out of `<p>`. Check again. Return the repaired document when it is well-formed. When it is not, print `label:line:column: message` and abort before the zip is written.

Already valid documents are returned unchanged. `package.opf`, `toc.ncx`, and `container.xml` are checked only, because an HTML rewrite would destroy them.

`--non-interactive` accepts default metadata so a book can be packaged without prompts. The gate itself always runs.

## Files

- `modules/xhtml_gate.py` — check, repair, gate
- `modules/mark2epub.py` — run the gate on every XHTML document before `ZipFile` opens; thread `interactive`
- `main.py` — `--non-interactive`
- `requirements.txt` — pin `lxml`

## Done when

1. The three XHT verification commands print `PASS`.
2. A Markdown chapter containing `&nbsp;`, a bare `&`, `<br>`, and an unclosed `<p>` becomes an EPUB whose unpacked XHTML parses as XML.
3. `data/dokumen.pub_kahramanlar.pdf` converts to an EPUB whose every XHTML, OPF, NCX, and container document parses as XML.
