# PDF2EPUB 📚

This version is published by [Bilal Bayrakdar](https://github.com/Bilallbayrakdar). It originates from [overcuriousity/pdf2epub](https://github.com/overcuriousity/pdf2epub), and the original MIT copyright stays in [LICENSE](LICENSE).

Convert PDF files to nicely structured Markdown and EPUB format with intelligent layout detection.

## ✨ Features

- 📖 Smart layout detection for books and academic papers
- 🔍 Hybrid text extraction with targeted line-level OCR glyph repair
- 📊 Table detection, clickable Table of Contents linking, and verse line-number alignment
- 🖼️ Image extraction and optimization
- 📝 Clean Markdown & EPUB 3 output with semantic footnotes and normalized headings
- 📱 Validated XHTML packaging with customizable styling and automatic metadata inference
- 🌍 Multi-language support
- 🚀 GPU acceleration support (NVIDIA, AMD & Apple Silicon)

## What This Version Adds

- **Targeted Corrupt-Glyph Repair (`modules/glyph_repair.py`):** Detects broken ClearScan or subset-font encodings on specific PDF lines (for example, `sabanmı` -> `sabanını` or `açıkIar` -> `açıklar`) and splices line-level PP-OCRv5 (`onnxruntime`) corrections without re-OCRing clean pages.
- **EPUB 3 Footnotes & Page-Break Rejoining (`modules/structure_norm.py`):** Locates bottom-of-page footnote rules in the PDF (including unnumbered ClearScan notes and multi-page continuations), converts them into per-chapter `1..N` EPUB 3 `noteref` / `<aside epub:type="footnote">` notes for tap-to-popup readers, and rejoins sentences or hyphenated words split across footnote zones:
  ```text
  PDF page bottom: "...olay" + [footnote rule + note] + next page: "içinde olay..."
  EPUB output:     "...olay içinde olay..." + linked chapter-end <aside> footnote
  ```
- **Chapter Spines & Clickable Table of Contents (`modules/contents_toc.py`):** Splits books into per-chapter XHTML spines and aligns printed TOC tables (*Contents* / *İçindekiler*) to target chapters via dynamic programming so both in-page TOC rows and the EPUB reader drawer are clickable.
- **Heading & Back-Cover Normalization (`modules/structure_norm.py`):** Normalizes inconsistent OCR subheading levels inside each chapter (`<h1>` chapter title with uniform `<h2>`/`<h3>` subsections), demotes misclassified quoted dialogue or glossary cross-references (`Bkz.`) to paragraphs, and isolates trailing back-cover blurbs onto a separate page.
- **Verse Line-Number Formatting (`modules/verse_lineno.py`):** Detects margin line numbers in poetic works (`5, 10, 15...`) and formats them as aligned margin labels (`span.verse-ln`) instead of numbers glued into verse text.
- **Entity Sanitization & Strict XHTML Gate (`modules/md_sanitize.py`, `modules/xhtml_gate.py`):** Repairs split `marker-pdf` HTML entities (`<sup>&#</sup>x27;` -> `'`) and unclosed inline tags, guards against duplicated body prose across spines, and validates/repairs every `.xhtml`, `.opf`, and `.ncx` file with `lxml` before writing the `.epub`.

## 🛠️ Dependencies

- Python 3.10–3.14 (3.13 recommended, see below)
- PyTorch (with CUDA/ROCm/MPS support for GPU acceleration)
- marker-pdf==1.10.2
- transformers==4.57.6
- markdown==3.10.2
- latex2mathml==3.81.0
- lxml==6.1.3
- onnxruntime==1.32.0

### ⚠️ Python version

Python 3.13 is recommended.

`marker-pdf` constrains `Pillow<11.0.0`, and Pillow only ships Python 3.14
wheels from 11.3.0 onward. On Python 3.10–3.13 every dependency installs as a
prebuilt wheel. On 3.14, pip has to build Pillow from source instead: this
works, but it is slower and requires a working C toolchain plus the image
library headers Pillow links against. On Debian/Ubuntu install them first:

```bash
sudo apt install libjpeg-dev zlib1g-dev libtiff-dev libfreetype6-dev libwebp-dev
```

Without these headers the install fails with
`RequiredDependencyException: The headers or library files could not be found for jpeg`.

## 💻 Installation

1. Create and activate a virtual environment.

On Linux/Mac:
```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

On Windows:
```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
```

2. Install Python dependencies (this installs PyTorch as well):
```bash
pip install -r requirements.txt
```

3. GPU acceleration (optional):

PyTorch is installed as a dependency in step 2. On Apple Silicon that wheel
already supports MPS, so no further action is needed. For a specific CUDA or
ROCm build, reinstall PyTorch using the selector at
[pytorch.org/get-started/locally](https://pytorch.org/get-started/locally/).
For example, for AMD GPUs with ROCm:
```bash
pip uninstall torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.2
```

4. Verify GPU support:
```python
import torch
print(torch.__version__)  # PyTorch version
print(torch.cuda.is_available())  # Should return True for NVIDIA
print(torch.backends.mps.is_available())  # Should return True for Apple Silicon
print(torch.version.hip)  # Should print ROCm version for AMD
```

### 🐳 Docker

A CPU-only image can be built from the included `Dockerfile`:

```bash
docker build -t pdf2epub .
```

Run it with your PDFs mounted at `/data` and a model cache volume (marker-pdf
downloads its models on first run):

```bash
docker run -it --rm \
  -v "$(pwd)":/data \
  -v pdf2epub-models:/models \
  pdf2epub input.pdf
```

`-it` is used when prompting interactively for EPUB metadata; pass `--non-interactive` (or `--skip-epub`) for unattended runs.

Tagged releases of the upstream project are published to
`ghcr.io/overcuriousity/pdf2epub` by the Docker workflow.

## 🚀 Usage

### Basic Usage

Convert a single PDF file:
```bash
python main.py input.pdf
```

Convert unattended (auto-inferring title, author, and language without prompts):
```bash
python main.py input.pdf --non-interactive
```

Convert all PDFs in a directory:
```bash
python main.py input_directory/
```

By default, EPUB generation prompts interactively for metadata (press Enter to
accept inferred defaults). Use `--non-interactive` to accept inferred metadata
automatically in scripts/CI, or `--skip-epub` to produce only Markdown.

### Advanced Options

```bash
python main.py [input_path] [output_path] [options]

Options:
  --max-pages INT          Maximum number of pages to process
  --start-page INT         Page number to start from
  --skip-epub              Skip EPUB generation, only create markdown
  --skip-md                Skip markdown generation, use existing markdown files
  --non-interactive        Accept default EPUB metadata and skip the review prompt
```

If `input_path` is omitted, all PDFs in `./input/` are processed.

### Examples

Process a specific range of pages:
```bash
python main.py book.pdf --start-page 10 --max-pages 50
```

Convert to markdown only:
```bash
python main.py thesis.pdf --skip-epub
```

### Output Structure

```
output_directory/
├── document_name/
│   ├── document_name.md
│   ├── document_name.epub
│   ├── document_name_metadata.json
│   └── images/
│       ├── image1.png
│       ├── image2.jpg
│       └── ...
```

## 🤝 Contributing

Contributions are welcome! Here's how you can help:

1. Fork the repository
2. Create a new branch for your feature
3. Commit your changes
4. Push to your branch
5. Create a Pull Request

Please ensure your code follows the existing style and includes appropriate tests.

### Development Setup

1. Clone the repository:
```bash
git clone https://github.com/Bilallbayrakdar/pdf2epub.git
cd pdf2epub
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

3. Install development dependencies:
```bash
pip install -r requirements.txt
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🐛 Known Issues

- Some image embedding might need manual adjustment
- Some complex mathematical equations might not be perfectly converted
- Certain PDF layouts with multiple columns may require manual adjustment
- Font detection might be imperfect in some cases

## 🙏 Acknowledgments

This project builds upon several excellent open-source libraries:
- [marker-pdf](https://github.com/VikParuchuri/marker) for PDF processing
- [mark2epub](https://github.com/AlexPof/mark2epub) for markdown conversion
- [PyTorch](https://pytorch.org/) for GPU acceleration
- [Transformers](https://huggingface.co/transformers) for advanced text processing
