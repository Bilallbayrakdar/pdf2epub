import markdown
import os
from xml.dom import minidom
import zipfile
import sys
import json
from PIL import Image
import regex as re
from pathlib import Path
from datetime import datetime, timezone
import subprocess
from typing import Dict, NamedTuple, Optional
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape
import latex2mathml.converter
from modules.contents_toc import TocEntry, link_contents, nest_entries
from modules.verse_lineno import format_verse_line_numbers
from modules.xhtml_gate import ensure_xhtml, ensure_xml

def get_user_input(prompt: str, default: str = "") -> str:
    """Get user input with a default value."""
    user_input = input(f"{prompt} [{default}]: ").strip()
    return user_input if user_input else default

# @start MD-EPUB-META
def get_metadata_from_user(existing_metadata: Optional[Dict] = None, interactive: bool = True) -> Dict:
    """Collect metadata from prompts, or keep defaults when non-interactive."""
    if existing_metadata is None:
        existing_metadata = {}
    
    metadata = existing_metadata.get("metadata", {})
    
    if interactive:
        print("\nPlease provide the following metadata for your EPUB (press Enter to use default value):")
    
    fields = {
        "dc:title": ("Title", metadata.get("dc:title", "Untitled Document")),
        "dc:creator": ("Author(s)", metadata.get("dc:creator", "Unknown Author")),
        "dc:identifier": ("Unique Identifier", metadata.get("dc:identifier", f"id-{datetime.now().strftime('%Y%m%d%H%M%S')}")),
        "dc:language": ("Language (e.g., en, de, fr)", metadata.get("dc:language", "en")),
        "dc:rights": ("Rights", metadata.get("dc:rights", "All rights reserved")),
        "dc:publisher": ("Publisher", metadata.get("dc:publisher", "PDF2EPUB")),
        "dc:date": ("Publication Date (YYYY-MM-DD)", metadata.get("dc:date", datetime.now().strftime("%Y-%m-%d")))
    }
    
    updated_metadata = {}
    for key, (prompt, default) in fields.items():
        value = get_user_input(prompt, default) if interactive else default
        updated_metadata[key] = value
        
    return {
        "metadata": updated_metadata,
        "default_css": existing_metadata.get("default_css", ["style.css"]),
        "chapters": existing_metadata.get("chapters", []),
        "cover_image": existing_metadata.get("cover_image", None)
    }


_PLACEHOLDER_TITLE = frozenset({"", "Untitled Document", "Untitled"})
_PLACEHOLDER_CREATOR = frozenset({"", "Unknown Author", "Unknown"})


def infer_epub_metadata(work_dir: Path, data: dict) -> dict:
    """Fill generic dc:* fields from markdown headings or the folder name."""
    meta = dict(data.get("metadata") or {})
    need_title = meta.get("dc:title", "Untitled Document") in _PLACEHOLDER_TITLE
    need_creator = meta.get("dc:creator", "Unknown Author") in _PLACEHOLDER_CREATOR
    if not (need_title or need_creator):
        return data

    skip = {"icindekiler", "önsöz", "onsöz", "içindekiler"}
    headings: list[str] = []
    for md in sorted(work_dir.glob("*.md")):
        for line in md.read_text(encoding="utf-8").splitlines()[:50]:
            m = re.match(r"^#\s+(.+?)\s*$", line.strip())
            if not m:
                continue
            t = m.group(1).strip("*")
            if _slug := re.sub(r"\s+", " ", t):
                if _slug.casefold() not in skip and len(_slug) > 1:
                    headings.append(_slug)
        if headings:
            break

    if need_title:
        meta["dc:title"] = (
            f"{headings[0]} – {headings[1]}" if len(headings) >= 2 else headings[0]
            if headings else work_dir.name
        )
    if need_creator and headings:
        meta["dc:creator"] = headings[0]
    if meta.get("dc:language") == "en" and re.search(r"[İığüşöçĞÜŞÖÇ]", work_dir.name + meta.get("dc:title", "")):
        meta["dc:language"] = "tr"
    return {**data, "metadata": meta}


# @end MD-EPUB-META

# @start MD-EPUB-REVIEW
def review_markdown(markdown_path: Path, interactive: bool = True) -> tuple[bool, str]:
    """Ask user if they want to review the markdown file."""
    content = markdown_path.read_text(encoding='utf-8')
    if not interactive:
        return True, content
    
    while True:
        response = input("\nWould you like to review the markdown file before conversion? (y/n): ").lower()
        if response in ['y', 'yes']:
            try:
                if sys.platform == 'darwin':
                    subprocess.run(['open', str(markdown_path)], check=True)
                elif os.name == 'posix':
                    subprocess.run(['xdg-open', str(markdown_path)], check=True)
                else:
                    os.startfile(str(markdown_path))
                
                while True:
                    proceed = input("\nPress Enter when you're done editing (or 'q' to abort): ").lower()
                    if proceed == 'q':
                        return False, content
                    elif proceed == '':
                        updated_content = markdown_path.read_text(encoding='utf-8')
                        return True, updated_content
            except Exception as e:
                print(f"\nError opening markdown file: {e}")
                print("Proceeding with conversion...")
                return True, content
        elif response in ['n', 'no']:
            return True, content
        else:
            print("Please enter 'y' or 'n'")
# @end MD-EPUB-REVIEW

def build_image_lookup(images_dir: Path) -> dict:
    """Build a {lowercase_name: actual_path} map for O(1) case-insensitive lookups."""
    lookup = {}
    try:
        for entry in images_dir.iterdir():
            lookup[entry.name.lower()] = entry
    except FileNotFoundError:
        pass
    return lookup

# @start MD-EPUB-IMG
MIN_IMAGE_WIDTH = 200
MIN_IMAGE_HEIGHT = 200
MIN_IMAGE_AREA = 100_000


def process_markdown_for_images(
    markdown_text: str,
    work_dir: Path,
    min_width: int = MIN_IMAGE_WIDTH,
    min_height: int = MIN_IMAGE_HEIGHT,
    min_area: int = MIN_IMAGE_AREA,
) -> tuple[str, list[str]]:
    """Rewrite surviving image references and drop small/decorative image artifacts."""
    images_found: list[str] = []
    images_dir = work_dir / "images"
    lookup = build_image_lookup(images_dir)

    def _replace(match: re.Match) -> str:
        alt_text, image_path = match.groups()
        img_name = Path(image_path.strip()).name
        actual = lookup.get(img_name.lower())
        if actual is None:
            print(f"Warning: Image not found: {images_dir / img_name}")
            return ""
        with Image.open(actual) as img:
            w, h = img.size
            if w < min_width or h < min_height or w * h < min_area:
                return ""
            dark_px = sum(img.convert("L").histogram()[:200])
            if 0 < dark_px < 5_000 and dark_px / (w * h) < 0.005:
                return ""
        if actual.name not in images_found:
            images_found.append(actual.name)
        return f"![{alt_text}](images/{actual.name})"

    modified_text = re.sub(r"!\[(.*?)\]\((.*?)\)", _replace, markdown_text)
    return modified_text, images_found
# @end MD-EPUB-IMG

def copy_and_optimize_image(src_path: Path, dest_path: Path, max_dimension: int = 1800) -> None:
    """Copy image to destination path with optimization for EPUB."""
    try:
        with Image.open(src_path) as img:
            if img.mode == 'RGBA':
                img = img.convert('RGB')
                
            ratio = min(max_dimension / max(img.size[0], img.size[1]), 1.0)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            
            if ratio < 1.0:
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            if src_path.suffix.lower() in ['.jpg', '.jpeg']:
                img.save(dest_path, 'JPEG', quality=85, optimize=True)
            elif src_path.suffix.lower() == '.png':
                img.save(dest_path, 'PNG', optimize=True)
            else:
                dest_path = dest_path.with_suffix('.jpg')
                img.save(dest_path, 'JPEG', quality=85, optimize=True)
                
    except Exception as e:
        print(f"Error processing image {src_path}: {e}")
        raise

def update_package_manifest(doc: minidom.Document, image_filenames: list[str], 
                          manifest: minidom.Element) -> None:
    """
    Update package manifest with image items, ensuring proper media types.
    """
    for i, image_filename in enumerate(image_filenames):
        item = doc.createElement('item')
        item.setAttribute('id', f"image-{i:05d}")
        item.setAttribute('href', f"images/{image_filename}")
        
        # Set appropriate media type based on file extension
        ext = Path(image_filename).suffix.lower()
        if ext in ['.jpg', '.jpeg']:
            media_type = 'image/jpeg'
        elif ext == '.png':
            media_type = 'image/png'
        elif ext == '.gif':
            media_type = 'image/gif'
        else:
            print(f"Warning: Unsupported image type {ext} for {image_filename}")
            continue
            
        item.setAttribute('media-type', media_type)
        manifest.appendChild(item)
        
def get_all_filenames(the_dir, extensions=[]):
    if not os.path.exists(the_dir):
        return []
    all_files = [x for x in os.listdir(the_dir)]
    all_files = [x for x in all_files if x.split(".")[-1] in extensions]
    return all_files

def get_packageOPF_XML(md_filenames=[], image_filenames=[], css_filenames=[], description_data=None, toc_href=None, spine_filenames: Optional[list[str]] = None):
    doc = minidom.Document()

    package = doc.createElement('package')
    package.setAttribute('xmlns',"http://www.idpf.org/2007/opf")
    package.setAttribute('version',"3.0")
    package.setAttribute('xml:lang',"en")
    package.setAttribute("unique-identifier","pub-id")

    ## Now building the metadata

    metadata = doc.createElement('metadata')
    metadata.setAttribute('xmlns:dc', 'http://purl.org/dc/elements/1.1/')

    for k,v in description_data["metadata"].items():
        if len(v):
            x = doc.createElement(k)
            for metadata_type,id_label in [("dc:title","title"),("dc:creator","creator"),("dc:identifier","pub-id")]:
                if k==metadata_type:
                    x.setAttribute('id',id_label)
            x.appendChild(doc.createTextNode(v))
            metadata.appendChild(x)

    # Required by EPUB 3: dcterms:modified timestamp
    modified_meta = doc.createElement('meta')
    modified_meta.setAttribute('property', 'dcterms:modified')
    modified_meta.appendChild(doc.createTextNode(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
    metadata.appendChild(modified_meta)


    ## Now building the manifest

    manifest = doc.createElement('manifest')

    ## TOC.xhtml file for EPUB 3
    x = doc.createElement('item')
    x.setAttribute('id',"toc")
    x.setAttribute('properties',"nav")
    x.setAttribute('href',"TOC.xhtml")
    x.setAttribute('media-type',"application/xhtml+xml")
    manifest.appendChild(x)

    ## Ensure retrocompatibility by also providing a TOC.ncx file
    x = doc.createElement('item')
    x.setAttribute('id',"ncx")
    x.setAttribute('href',"toc.ncx")
    x.setAttribute('media-type',"application/x-dtbncx+xml")
    manifest.appendChild(x)

    x = doc.createElement('item')
    x.setAttribute('id',"titlepage")
    x.setAttribute('href',"titlepage.xhtml")
    x.setAttribute('media-type',"application/xhtml+xml")
    manifest.appendChild(x)

    resolved_spines = (
        spine_filenames
        if spine_filenames is not None
        else ["s{:05d}-{}.xhtml".format(i, fn.split(".")[0]) for i, fn in enumerate(md_filenames)]
    )
    for i, spine_fn in enumerate(resolved_spines):
        x = doc.createElement('item')
        x.setAttribute('id', "s{:05d}".format(i))
        x.setAttribute('href', quote(spine_fn))
        x.setAttribute('media-type', "application/xhtml+xml")
        manifest.appendChild(x)

    for i,image_filename in enumerate(image_filenames):
        x = doc.createElement('item')
        x.setAttribute('id',"image-{:05d}".format(i))
        x.setAttribute('href', "images/{}".format(quote(image_filename)))
        ext = Path(image_filename).suffix.lower()
        if ext == '.gif':
            x.setAttribute('media-type',"image/gif")
        elif ext in ['.jpg', '.jpeg']:
            x.setAttribute('media-type',"image/jpeg")
        elif ext == '.png':
            x.setAttribute('media-type',"image/png")
        if image_filename==description_data["cover_image"]:
            x.setAttribute('properties',"cover-image")

            ## Ensure compatibility by also providing a meta tag in the metadata
            y = doc.createElement('meta')
            y.setAttribute('name',"cover")
            y.setAttribute('content',"image-{:05d}".format(i))
            metadata.appendChild(y)
        manifest.appendChild(x)

    for i,css_filename in enumerate(css_filenames):
        x = doc.createElement('item')
        x.setAttribute('id',"css-{:05d}".format(i))
        x.setAttribute('href',"css/{}".format(css_filename))
        x.setAttribute('media-type',"text/css")
        manifest.appendChild(x)

    ## Now building the spine

    spine = doc.createElement('spine')
    spine.setAttribute('toc', "ncx")

    x = doc.createElement('itemref')
    x.setAttribute('idref',"titlepage")
    x.setAttribute('linear',"yes")
    spine.appendChild(x)
    for i, _ in enumerate(resolved_spines):
        x = doc.createElement('itemref')
        x.setAttribute('idref', "s{:05d}".format(i))
        x.setAttribute('linear', "yes")
        spine.appendChild(x)

    guide = doc.createElement('guide')
    x = doc.createElement('reference')
    x.setAttribute('type',"cover")
    x.setAttribute('title',"Cover image")
    x.setAttribute('href',"titlepage.xhtml")
    guide.appendChild(x)
    if toc_href:
        x = doc.createElement('reference')
        x.setAttribute('type', "toc")
        x.setAttribute('title', "İçindekiler")
        x.setAttribute('href', toc_href)
        guide.appendChild(x)


    package.appendChild(metadata)
    package.appendChild(manifest)
    package.appendChild(spine)
    package.appendChild(guide)
    doc.appendChild(package)

    return doc.toprettyxml()


def get_container_XML():
    container_data = """<?xml version="1.0" encoding="UTF-8" ?>\n"""
    container_data += """<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n"""
    container_data += """<rootfiles>\n"""
    container_data += """<rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/>\n"""
    container_data += """</rootfiles>\n</container>"""
    return container_data

def get_coverpage_XML(title, authors):
    """Generate a simple cover page with title and optional author input."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head>
<title>Cover Page</title>
<style type="text/css">
body {{ 
    margin: 0;
    padding: 0;
    height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    font-family: serif;
}}
.cover {{
    padding: 3em;
    text-align: center;
    border: 1px solid #ccc;
    max-width: 80%;
}}
h1 {{
    font-size: 2em;
    margin-bottom: 1em;
    line-height: 1.2;
    color: #333;
}}
p {{
    font-size: 1.2em;
    font-style: italic;
    color: #666;
    line-height: 1.4;
}}
</style>
</head>
<body>
    <div class="cover">
        <h1>{xml_escape(title)}</h1>
        <p>{xml_escape(authors) if authors else ''}</p>
    </div>
</body>
</html>"""

def _nav_html(nodes: list[dict]) -> str:
    if not nodes:
        return ""
    parts = ['<ol>']
    for node in nodes:
        label = xml_escape(node["title"])
        body = f'<a href="{xml_escape(node["href"])}">{label}</a>' if node["href"] else f"<span>{label}</span>"
        parts.append(f"<li>{body}{_nav_html(node['children'])}</li>")
    parts.append("</ol>")
    return "".join(parts)


def _first_href(node: dict) -> str:
    if node.get("href"):
        return node["href"]
    for ch in node.get("children", []):
        if h := _first_href(ch):
            return h
    return ""


def _nav_ncx(nodes: list[dict], order: list[int]) -> str:
    chunks = []
    for node in nodes:
        resolved_src = _first_href(node)
        if not resolved_src:
            continue
        play = order[0]
        order[0] += 1
        label = xml_escape(node["title"])
        src = xml_escape(resolved_src)
        chunks.append(
            f'<navPoint id="navpoint-{play}" playOrder="{play}">'
            f"<navLabel><text>{label}</text></navLabel>"
            f'<content src="{src}"/>'
            f"{_nav_ncx(node['children'], order)}</navPoint>"
        )
    return "".join(chunks)


def get_TOC_XML(default_css_filenames, nav_entries: list[TocEntry]):
    ## Returns the XML data for the TOC.xhtml file

    toc_xhtml = """<?xml version="1.0" encoding="UTF-8"?>\n"""
    toc_xhtml += """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="tr">\n"""
    toc_xhtml += """<head>\n<meta http-equiv="default-style" content="text/html; charset=utf-8"/>\n"""
    toc_xhtml += """<title>İçindekiler</title>\n"""

    for css_filename in default_css_filenames:
        toc_xhtml += """<link rel="stylesheet" href="css/{}" type="text/css"/>\n""".format(css_filename)

    toc_xhtml += """</head>\n<body>\n"""
    toc_xhtml += """<nav epub:type="toc" role="doc-toc" id="toc">\n<h2>İçindekiler</h2>\n"""
    toc_xhtml += _nav_html(nest_entries(nav_entries))
    toc_xhtml += """</nav>\n</body>\n</html>"""

    return toc_xhtml

def get_TOCNCX_XML(nav_entries: list[TocEntry], uid="", title=""):
    ## Returns the XML data for the TOC.ncx file

    toc_ncx = """<?xml version="1.0" encoding="UTF-8"?>\n"""
    toc_ncx += """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" xml:lang="tr" version="2005-1">\n"""
    toc_ncx += """<head>\n"""
    toc_ncx += """<meta name="dtb:uid" content="{}"/>\n""".format(xml_escape(uid))
    toc_ncx += """<meta name="dtb:depth" content="2"/>\n"""
    toc_ncx += """<meta name="dtb:totalPageCount" content="0"/>\n"""
    toc_ncx += """<meta name="dtb:maxPageNumber" content="0"/>\n"""
    toc_ncx += """</head>\n"""
    toc_ncx += """<docTitle><text>{}</text></docTitle>\n""".format(xml_escape(title))
    toc_ncx += """<navMap>\n"""
    toc_ncx += _nav_ncx(nest_entries(nav_entries), [1])
    toc_ncx += """</navMap>\n</ncx>"""

    return toc_ncx

def convert_math_to_mathml(html_text: str) -> str:
    """Replace LaTeX math expressions in HTML with MathML, skipping code blocks."""
    # Mask <pre>/<code> blocks so their $ delimiters are never treated as math
    placeholders = {}
    counter = [0]

    def mask(m):
        key = f"\x00MASK{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = m.group(0)
        return key

    masked = re.sub(r'<pre[\s\S]*?</pre>|<code[\s\S]*?</code>', mask, html_text, flags=re.DOTALL)

    def try_convert(latex):
        try:
            return latex2mathml.converter.convert(latex)
        except Exception:
            return None

    # Standalone display math that Markdown wrapped in <p>: replace the whole
    # paragraph to avoid invalid XHTML like <p><div>...</div></p>
    def replace_display_paragraph(m):
        mathml = try_convert(m.group(1))
        return f'<div class="math-display">{mathml}</div>' if mathml else m.group(0)

    # Remaining $$...$$ (inside phrasing content): use <span> to stay valid
    def replace_display_inline(m):
        mathml = try_convert(m.group(1))
        return f'<span class="math-display">{mathml}</span>' if mathml else m.group(0)

    # Inline $...$
    def replace_inline(m):
        mathml = try_convert(m.group(1))
        return f'<span class="math-inline">{mathml}</span>' if mathml else m.group(0)

    masked = re.sub(r'<p>\s*\$\$(.*?)\$\$\s*</p>', replace_display_paragraph, masked, flags=re.DOTALL)
    masked = re.sub(r'\$\$(.*?)\$\$', replace_display_inline, masked, flags=re.DOTALL)
    masked = re.sub(r'(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)', replace_inline, masked, flags=re.DOTALL)

    for key, original in placeholders.items():
        masked = masked.replace(key, original)

    return masked

class SpineDoc(NamedTuple):
    filename: str
    xhtml: str


_ANCHOR_ATTR = re.compile(r"\{:\s*#(icindekiler|ch-\d+)\s*\}")
_ANY_ID_ATTR = re.compile(r"\{:\s*#([A-Za-z0-9_:-]+)\s*\}")
_FN_DEF = re.compile(r"(?m)^\[\^([^\]]+)\]:.*(?:\n(?:[ \t]{2,}|\t).*)*")


def _extract_doc_title(markdown_data: str, fallback: str = "Bölüm") -> str:
    for ln in markdown_data.splitlines():
        if m := re.match(r"^#{1,6}\s+(.+?)(?:\s*\{:.*?\})?\s*$", ln.strip()):
            cleaned = re.sub(r"[*_`]+", "", m.group(1)).strip()
            if cleaned:
                return cleaned
    return fallback


# @start MD-EPUB-CHAP
def _markdown_slice_to_xhtml(
    markdown_data: str, css_filenames: list[str], doc_title: str = "Bölüm", lang: str = "tr"
) -> str:
    html_text = markdown.markdown(
        markdown_data,
        extensions=["codehilite", "tables", "fenced_code", "footnotes", "attr_list"],
        extension_configs={"codehilite": {"guess_lang": False}},
    )
    html_text = convert_math_to_mathml(html_text)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:m="http://www.w3.org/1998/Math/MathML" lang="{xml_escape(lang)}">
<head>
    <meta http-equiv="default-style" content="text/html; charset=utf-8"/>
    <title>{xml_escape(doc_title)}</title>
    {''.join(f'<link rel="stylesheet" href="css/{css}" type="text/css" media="all"/>' for css in css_filenames)}
</head>
<body>
{html_text}
</body>
</html>"""


def _prepare_markdown(
    work_dir_path: Path, md_filename: str, content: Optional[str] = None
) -> tuple[str, list[str], list[TocEntry]]:
    if content is None:
        markdown_data = (work_dir_path / md_filename).read_text(encoding="utf-8")
    else:
        markdown_data = content
    # Link TOC first so images inside İçindekiler are stripped before image collection
    markdown_data, toc_entries = link_contents(markdown_data)
    markdown_data, chapter_images = process_markdown_for_images(markdown_data, work_dir_path)
    markdown_data = re.sub(
        r"^#{4,6}(\s+(?:[IVXLCDM\d]+|\d+)\.\s+Bölüm)\s*$",
        r"##\1",
        markdown_data,
        flags=re.M,
    )
    return markdown_data, chapter_images, toc_entries


def get_chapter_XML(
    work_dir: str, md_filename: str, css_filenames: list[str], content: Optional[str] = None
) -> tuple[str, list[str], list[TocEntry]]:
    """Convert one Markdown document to XHTML and return (xhtml, images, toc_entries)."""
    markdown_data, chapter_images, toc_entries = _prepare_markdown(Path(work_dir), md_filename, content)
    doc_title = _extract_doc_title(markdown_data, Path(md_filename).stem)
    markdown_data = format_verse_line_numbers(markdown_data)
    local_toc = [TocEntry(e.title, e.level, e.anchor, f"#{e.anchor}" if e.anchor else "") for e in toc_entries]
    return _markdown_slice_to_xhtml(markdown_data, css_filenames, doc_title=doc_title), chapter_images, local_toc
# @end MD-EPUB-CHAP


# @start MD-EPUB-SPLIT
def render_markdown_spines(
    work_dir: str | Path,
    md_filename: str,
    css_filenames: list[str],
    content: Optional[str] = None,
    start_index: int = 0,
    lang: str = "tr",
) -> tuple[list[SpineDoc], list[str], list[TocEntry]]:
    """Partition a Markdown document at chapter anchors into separate XHTML spine documents."""
    stem = Path(md_filename).stem
    markdown_data, chapter_images, toc_entries = _prepare_markdown(Path(work_dir), md_filename, content)
    fn_defs = {m.group(1): m.group(0) for m in _FN_DEF.finditer(markdown_data)}
    lines = markdown_data.splitlines()

    split_points: list[int] = [idx for idx, line in enumerate(lines) if _ANCHOR_ATTR.search(line)]
    segments: list[list[str]] = []
    if not split_points:
        segments.append(lines)
    else:
        if split_points[0] > 0 and any(ln.strip() for ln in lines[: split_points[0]]):
            segments.append(lines[: split_points[0]])
        for pos, sp in enumerate(split_points):
            nxt = split_points[pos + 1] if pos + 1 < len(split_points) else len(lines)
            segments.append(lines[sp:nxt])

    seg_filenames = [f"s{start_index + idx:05d}-{stem}.xhtml" for idx in range(len(segments))]
    anchor_to_href: dict[str, str] = {}
    for fn, seg_lines in zip(seg_filenames, segments):
        q_fn = quote(fn)
        for ln in seg_lines:
            for m in _ANY_ID_ATTR.finditer(ln):
                anchor_to_href[m.group(1)] = q_fn

    def _rewrite_link(m: re.Match) -> str:
        aid = m.group(1)
        doc_href = anchor_to_href.get(aid, "")
        return f"]({doc_href}#{aid})" if doc_href else m.group(0)

    spines: list[SpineDoc] = []
    for fn, seg_lines in zip(seg_filenames, segments):
        seg_md = "\n".join(seg_lines) + "\n"
        seg_md = re.sub(r"\]\(#([A-Za-z0-9_:-]+)\)", _rewrite_link, seg_md)
        if fn_defs:
            used_fns = [fid for fid in re.findall(r"\[\^([^\]]+)\](?!:)", seg_md) if fid in fn_defs]
            missing_defs = [fn_defs[fid] for fid in dict.fromkeys(used_fns) if f"[^{fid}]:" not in seg_md]
            if missing_defs:
                seg_md = seg_md.rstrip() + "\n\n" + "\n\n".join(missing_defs) + "\n"
        doc_title = _extract_doc_title(seg_md, stem)
        seg_md = format_verse_line_numbers(seg_md)
        spines.append(SpineDoc(fn, _markdown_slice_to_xhtml(seg_md, css_filenames, doc_title=doc_title, lang=lang)))

    resolved_toc = [
        TocEntry(
            e.title,
            e.level,
            e.anchor,
            f"{anchor_to_href[e.anchor]}#{e.anchor}" if e.anchor and e.anchor in anchor_to_href else "",
        )
        for e in toc_entries
    ]
    return spines, chapter_images, resolved_toc
# @end MD-EPUB-SPLIT



def convert_to_epub(markdown_dir: Path, output_path: Path, interactive: bool = True) -> None:
    """
    Convert markdown files and images to EPUB format.
    """
    if not markdown_dir.exists():
        raise FileNotFoundError(f"Markdown directory not found: {markdown_dir}")
        
    if not list(markdown_dir.glob('*.md')):
        raise ValueError(f"No markdown files found in: {markdown_dir}")
    
    # Generate EPUB file
    epub_path = markdown_dir / f"{markdown_dir.name}.epub"
    main([str(markdown_dir), str(epub_path)], interactive=interactive)

def main(args, interactive: bool = True):
    if len(args) < 2:
        print("\nUsage:\n    python md2epub.py <markdown_directory> <output_file.epub>")
        exit(1)

    work_dir = args[0]
    output_path = args[1]

    images_dir = os.path.join(work_dir, 'images/')
    css_dir = os.path.join(work_dir, 'css/')

    try:
        # Reading/Creating the JSON file containing the description of the eBook
        description_path = os.path.join(work_dir, "description.json")
        existing_metadata = {}
        
        if os.path.exists(description_path):
            with open(description_path, 'r', encoding='utf-8') as f:
                existing_metadata = json.load(f)

        if not interactive:
            existing_metadata = infer_epub_metadata(Path(work_dir), existing_metadata)

        # Get metadata from user
        json_data = get_metadata_from_user(existing_metadata, interactive=interactive)
        
        # Find all markdown files if not already in metadata
        if not json_data["chapters"]:
            markdown_files = [f for f in os.listdir(work_dir) if f.endswith('.md')]
            for md_file in sorted(markdown_files):
                json_data["chapters"].append({
                    "markdown": md_file,
                    "css": ""
                })
        
        # Save the updated description.json
        with open(description_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2)
        
        # Review markdown files and store updated content
        chapter_contents = {}
        for chapter in json_data["chapters"]:
            md_path = Path(work_dir) / chapter["markdown"]
            should_continue, content = review_markdown(md_path, interactive=interactive)
            if not should_continue:
                print("\nConversion aborted by user.")
                return
            chapter_contents[chapter["markdown"]] = content

        # Get title and author
        title = json_data["metadata"].get("dc:title", "Untitled Document")
        authors = json_data["metadata"].get("dc:creator", None)

        # Compile list of files
        all_md_filenames = []
        all_css_filenames = json_data["default_css"][:]
        for chapter in json_data["chapters"]:
            if chapter["markdown"] not in all_md_filenames:
                all_md_filenames.append(chapter["markdown"])
            if len(chapter["css"]) and (chapter["css"] not in all_css_filenames):
                all_css_filenames.append(chapter["css"])
        
        all_image_filenames = get_all_filenames(images_dir, extensions=["gif", "jpg", "jpeg", "png"])

        # First process all chapters and images
        images_dir = Path(work_dir) / 'images'
        epub_images_dir = Path(work_dir) / 'epub_images'
        processed_images = {}  # Store processed image data
        all_referenced_images = set()
        all_spines: list[SpineDoc] = []
        nav_entries: list[TocEntry] = []
        toc_href = None

        # First pass: Process chapters into per-chapter spine documents and collect image references
        print("\nProcessing chapters and collecting image references...")
        for chapter in json_data["chapters"]:
            css_files = json_data["default_css"][:]
            if chapter["css"]:
                css_files.append(chapter["css"])

            spines, chapter_images, toc_entries = render_markdown_spines(
                work_dir,
                chapter["markdown"],
                css_files,
                content=chapter_contents[chapter["markdown"]],
                start_index=len(all_spines),
                lang=json_data["metadata"].get("dc:language", "tr"),
            )
            all_spines.extend(spines)
            all_referenced_images.update(chapter_images)

            if toc_entries:
                ic_doc = next(
                    (quote(sp.filename) for sp in spines if 'id="icindekiler"' in sp.xhtml),
                    quote(spines[0].filename),
                )
                ic_href = f"{ic_doc}#icindekiler"
                toc_href = toc_href or ic_href
                nav_entries.append(TocEntry("İçindekiler", 1, "icindekiler", ic_href))
                nav_entries.extend(toc_entries)
            elif spines:
                stem = Path(chapter["markdown"]).stem
                nav_entries.append(TocEntry(stem, 1, "", quote(spines[0].filename)))

        if json_data.get("cover_image"):
            all_referenced_images.add(json_data["cover_image"])

        # Process and optimize images
        print("\nProcessing and optimizing images...")
        if images_dir.exists() and all_referenced_images:
            epub_images_dir.mkdir(exist_ok=True)
            
            for image in all_referenced_images:
                src_path = images_dir / image
                if src_path.exists():
                    try:
                        dest_path = epub_images_dir / image
                        copy_and_optimize_image(src_path, dest_path)
                        
                        # Store processed image data
                        with open(dest_path, "rb") as f:
                            processed_images[image] = f.read()
                    except Exception as e:
                        print(f"Warning: Failed to process image {image}: {e}")
                else:
                    print(f"Warning: Referenced image not found: {src_path}")
            
            # Cleanup temporary directory
            import shutil
            shutil.rmtree(epub_images_dir, ignore_errors=True)

        # @start MD-EPUB-PACK
        # @start XHT-GATE
        print("\nChecking XHTML...")
        gated_spines: list[SpineDoc] = []
        for sp in all_spines:
            gated_spines.append(SpineDoc(sp.filename, ensure_xhtml(sp.xhtml, f"OPS/{sp.filename}")))
        coverpage_data = ensure_xhtml(get_coverpage_XML(title, authors), "OPS/titlepage.xhtml")
        toc_xml = ensure_xhtml(
            get_TOC_XML(json_data["default_css"], nav_entries), "OPS/TOC.xhtml"
        )
        ncx_xml = ensure_xml(
            get_TOCNCX_XML(
                nav_entries,
                uid=json_data["metadata"].get("dc:identifier", ""),
                title=json_data["metadata"].get("dc:title", ""),
            ),
            "OPS/toc.ncx",
        )
        opf_xml = ensure_xml(
            get_packageOPF_XML(
                md_filenames=all_md_filenames,
                image_filenames=sorted(processed_images.keys()),
                css_filenames=all_css_filenames,
                description_data=json_data,
                toc_href=toc_href,
                spine_filenames=[sp.filename for sp in gated_spines],
            ),
            "OPS/package.opf",
        )
        container_xml = ensure_xml(get_container_XML(), "META-INF/container.xml")
        print(f"XHTML check passed for {len(gated_spines) + 5} document(s).")
        # @end XHT-GATE

        # Now create the EPUB file with all prepared content
        print("\nCreating EPUB file...")
        with zipfile.ZipFile(output_path, "w") as epub:
            # Write mimetype (must be first and uncompressed)
            epub.writestr("mimetype", "application/epub+zip")

            epub.writestr("META-INF/container.xml", container_xml, zipfile.ZIP_DEFLATED)
            epub.writestr("OPS/package.opf", opf_xml, zipfile.ZIP_DEFLATED)
            epub.writestr("OPS/titlepage.xhtml", coverpage_data.encode("utf-8"), zipfile.ZIP_DEFLATED)

            print(f"Writing {len(gated_spines)} spine chapters...")
            for sp in gated_spines:
                epub.writestr(
                    f"OPS/{sp.filename}",
                    sp.xhtml.encode("utf-8"),
                    zipfile.ZIP_DEFLATED,
                )

            # Write processed images
            if processed_images:
                print(f"Writing {len(processed_images)} processed images...")
                for image_name, image_data in processed_images.items():
                    epub.writestr(f"OPS/images/{image_name}", image_data, zipfile.ZIP_DEFLATED)

            print("Writing table of contents...")
            epub.writestr("OPS/TOC.xhtml", toc_xml, zipfile.ZIP_DEFLATED)
            epub.writestr("OPS/toc.ncx", ncx_xml, zipfile.ZIP_DEFLATED)

            # Copy CSS files; write a default style.css for any that are missing
            default_css_content = b"""body { font-family: serif; line-height: 1.5; margin: 5%; }
h1, h2, h3, h4, h5, h6 { font-family: sans-serif; }
h1 { font-size: 1.6em; margin-top: 1.2em; }
h2 { font-size: 1.35em; margin-top: 1em; }
h3, h4 { font-size: 1.2em; margin-top: 0.8em; }
#icindekiler table { border-collapse: collapse; width: 100%; margin: 0.5em 0; }
#icindekiler td, #icindekiler th { padding: 0.2em 0.5em; vertical-align: top; }
img { max-width: 100%; height: auto; }
pre, code { font-family: monospace; font-size: 0.9em; }
p.verse { display: table; width: 100%; table-layout: fixed; margin: 0.35em 0; border: 0; }
.verse-ln { display: table-cell; width: 2.75em; padding-right: 0.6em; text-align: right; vertical-align: top; font-size: 0.82em; color: #555; font-variant-numeric: tabular-nums; }
.verse-ln-empty { visibility: hidden; }
.verse-body { display: table-cell; vertical-align: top; }
"""
            print(f"Writing {len(all_css_filenames)} CSS files...")
            for css in all_css_filenames:
                css_path = os.path.join(css_dir, css)
                if os.path.exists(css_path):
                    with open(css_path, "rb") as f:
                        epub.writestr(f"OPS/css/{css}", f.read(), zipfile.ZIP_DEFLATED)
                else:
                    epub.writestr(f"OPS/css/{css}", default_css_content, zipfile.ZIP_DEFLATED)

        print(f"\nEPUB creation complete: {output_path}")
        # @end MD-EPUB-PACK
        
    except Exception:
        import traceback
        print(f"Error processing {work_dir}:")
        print(traceback.format_exc())
        raise

if __name__ == "__main__":
    main(sys.argv[1:])
