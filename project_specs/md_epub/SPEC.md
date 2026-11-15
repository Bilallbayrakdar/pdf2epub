# Namespace: [MD-EPUB] (Markdown to EPUB)

## 1. Overview

Packages a directory of Markdown files and images into an EPUB 3 zip. Chapter bodies are Markdown rendered to XHTML, with LaTeX math rewritten as MathML. Each XHTML document passes through the XHTML gate before it is stored.

## 2. Component Logic

### [MD-EPUB-META] Collect EPUB metadata
- **Intent:** Fill Dublin Core metadata from an existing `description.json` or from prompts.
- **Logic:**
  1. Load `description.json` when it exists.
  2. In interactive mode, prompt for title, author, identifier, language, rights, publisher, and date.
  3. In non-interactive mode, keep the defaults without reading stdin.
  4. Write the chosen metadata back to `description.json`.

### [MD-EPUB-REVIEW] Review markdown
- **Intent:** Let a person edit the Markdown before packaging, or skip that prompt.
- **Logic:**
  1. Read the Markdown file.
  2. In non-interactive mode, return the text immediately.
  3. In interactive mode, offer to open the file and re-read it after editing.

### [MD-EPUB-CHAP] Render a chapter
- **Intent:** Turn one Markdown document into an XHTML chapter and list the images it references.
- **Logic:**
  1. Rewrite image links to `images/<filename>` when the file exists.
  2. Render Markdown with tables, fenced code, footnotes, and codehilite.
  3. Replace LaTeX `$...$` and `$$...$$` with MathML, leaving fenced code untouched.
  4. Wrap the fragment in an XHTML document that declares the EPUB and MathML namespaces.

### [MD-EPUB-PACK] Write the EPUB zip
- **Intent:** Assemble package documents, chapters, images, and CSS into one `.epub`.
- **Logic:**
  1. Render every chapter.
  2. Run each chapter, the title page, and the TOC through the XHTML gate. Run `package.opf`, `toc.ncx`, and `container.xml` through the XML check.
  3. Store `mimetype` first, then the checked documents, images, and CSS.
  4. Do not open the zip when the gate reports an unresolved syntax error.

## 3. Verification

### [MD-EPUB-CHAP]
```bash
. .venv/bin/activate && python -c "from modules.mark2epub import get_chapter_XML; from modules.xhtml_gate import find_xhtml_errors; x,imgs,_toc=get_chapter_XML('.','nope.md',['style.css'], content='# T\n\nTom & Jerry\n'); assert find_xhtml_errors(x)==[] and 'Tom' in x and imgs==[]; print('PASS')"
```
Expected: `PASS`.
