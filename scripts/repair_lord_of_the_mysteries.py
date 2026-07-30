"""Repair the local Lord of the Mysteries EPUB from its complete PDF companion.

The supplied EPUB is structurally valid, but it omits ten numbered chapters,
duplicates every chapter heading in the body, and contains a few scraper
artifacts.  The companion PDF contains the complete 1,409-chapter text.  This
script preserves the EPUB text wherever it exists and extracts only the ten
missing chapters from the PDF's body pages.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from ebooklib import epub

from epub_listener.infrastructure.parsers.ebooklib_parser import EbookLibParser

EXPECTED_CHAPTERS = 1_409
CHAPTER_RE = re.compile(r"^Chapter\s+(\d+)\s*:\s*(.+)$", re.IGNORECASE)
PDF_HEADING_RE = re.compile(r"^Chapter\s+(\d+)\s*:\s*(.+)$", re.IGNORECASE)
TERMINAL_PUNCTUATION_RE = re.compile(r"[.!?…][\"'”’)]?$|[.!?…][\"'”’)]?\s*$")
TABLE_OF_CONTENTS_MARKER = "Table of Contents Lord of the Mysteries Synopsis"

STYLE = """@namespace epub "http://www.idpf.org/2007/ops";
html { -webkit-text-size-adjust: 100%; }
body {
  color: #1c1c1c;
  background: #fff;
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.58;
  margin: 0 auto;
  max-width: 42em;
  padding: 5%;
}
h1 {
  font-size: 1.65em;
  line-height: 1.25;
  margin: 1.8em 0 1.5em;
  text-align: center;
}
p {
  margin: 0 0 0.82em;
  orphans: 2;
  text-indent: 1.2em;
  widows: 2;
}
h1 + p { text-indent: 0; }
"""

BOILERPLATE_PATTERNS = (
    re.compile(r"Read the next chapter on our vipnovel\.com\s*", re.IGNORECASE),
    re.compile(r"Read more chapter on our vipnovel\.com\s*", re.IGNORECASE),
    re.compile(r"Read more chapter on vipnovel\s*", re.IGNORECASE),
    re.compile(
        r"<h5>\s*Translator:\s*Atlas Studios\s*\|\s*Editor:\s*Atlas Studios\s*</h5>\s*",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ChapterText:
    number: int
    title: str
    paragraphs: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class PdfTextBlock:
    text: str
    page_number: int
    y0: float
    first_line_x0: float
    max_font_size: float


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00ad", "").replace("\u00a0", " ")).strip()


def source_chapters(epub_path: Path) -> dict[int, ChapterText]:
    result: dict[int, ChapterText] = {}
    for chapter in EbookLibParser().parse(epub_path):
        match = CHAPTER_RE.fullmatch(chapter.title)
        if not match:
            raise ValueError(f"Unexpected chapter title: {chapter.title!r}")
        number = int(match.group(1))
        paragraphs = [normalize_text(part) for part in chapter.text.split("\n\n")]
        paragraphs = [part for part in paragraphs if part]

        # The source XHTML contains both an h1 and an identical first p.  The
        # rebuilt XHTML supplies its own h1, so discard all repeated leading
        # title paragraphs.
        while paragraphs and paragraphs[0] == chapter.title:
            paragraphs.pop(0)

        cleaned: list[str] = []
        for paragraph in paragraphs:
            if TABLE_OF_CONTENTS_MARKER in paragraph:
                paragraph = paragraph.split(TABLE_OF_CONTENTS_MARKER, 1)[0].rstrip()
                if paragraph:
                    cleaned.append(normalize_text(paragraph))
                # Everything after this marker is the scraped site's table of
                # contents, split across many source paragraphs.
                break
            for pattern in BOILERPLATE_PATTERNS:
                paragraph = pattern.sub("", paragraph)
            paragraph = paragraph.replace("</anno>", "")
            paragraph = normalize_text(paragraph)
            if paragraph:
                cleaned.append(paragraph)

        if number in result:
            raise ValueError(f"Duplicate source chapter number: {number}")
        result[number] = ChapterText(number, chapter.title, tuple(cleaned), "epub")
    return result


def page_blocks(page: pymupdf.Page, page_number: int) -> list[PdfTextBlock]:
    blocks: list[PdfTextBlock] = []
    page_dict = page.get_text("dict", sort=True)
    for raw_block in page_dict.get("blocks", []):
        if raw_block.get("type") != 0:
            continue
        lines = raw_block.get("lines", [])
        line_texts: list[str] = []
        font_sizes: list[float] = []
        first_line_x0 = float(raw_block.get("bbox", [0])[0])
        for line_index, line in enumerate(lines):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans)
            if line_index == 0:
                first_line_x0 = float(line.get("bbox", [first_line_x0])[0])
            line_texts.append(text)
            font_sizes.extend(float(span.get("size", 0)) for span in spans)
        text = normalize_text(" ".join(line_texts))
        if not text:
            continue
        blocks.append(
            PdfTextBlock(
                text=text,
                page_number=page_number,
                y0=float(raw_block.get("bbox", [0, 0])[1]),
                first_line_x0=first_line_x0,
                max_font_size=max(font_sizes, default=0),
            )
        )
    return blocks


def pdf_chapter_locations(document: pymupdf.Document) -> dict[int, tuple[int, int]]:
    """Return chapter -> (zero-based page, heading block index) for body headings."""
    locations: dict[int, tuple[int, int]] = {}
    for page_index, page in enumerate(document):
        blocks = page_blocks(page, page_index + 1)
        for block_index, block in enumerate(blocks):
            match = PDF_HEADING_RE.fullmatch(block.text)
            if not match or block.max_font_size < 15:
                continue
            number = int(match.group(1))
            if not 1 <= number <= EXPECTED_CHAPTERS:
                continue
            if number in locations:
                raise ValueError(f"Multiple PDF body headings found for chapter {number}")
            locations[number] = (page_index, block_index)

    expected = set(range(1, EXPECTED_CHAPTERS + 1))
    missing = sorted(expected - locations.keys())
    if missing:
        raise ValueError(f"PDF body headings missing chapter(s): {missing}")
    return locations


def extract_pdf_chapter(
    document: pymupdf.Document,
    locations: dict[int, tuple[int, int]],
    number: int,
) -> ChapterText:
    start_page, start_block = locations[number]
    if number < EXPECTED_CHAPTERS:
        end_page, end_block = locations[number + 1]
    else:
        end_page, end_block = document.page_count - 1, 1_000_000

    heading_blocks = page_blocks(document[start_page], start_page + 1)
    heading = heading_blocks[start_block].text
    heading_match = PDF_HEADING_RE.fullmatch(heading)
    if not heading_match:
        raise ValueError(f"Could not parse PDF heading for chapter {number}: {heading!r}")
    pdf_title = normalize_text(heading_match.group(2))
    # A wrapped PDF heading can split a hyphenated word between visual lines.
    pdf_title = re.sub(r"(?<=-)\s+(?=\w)", "", pdf_title)
    title = f"Chapter {number}: {pdf_title}"

    paragraphs: list[str] = []
    previous_page: int | None = None
    for page_index in range(start_page, end_page + 1):
        blocks = page_blocks(document[page_index], page_index + 1)
        first = start_block + 1 if page_index == start_page else 0
        last = end_block if page_index == end_page else len(blocks)
        for block in blocks[first:last]:
            text = block.text
            if not text:
                continue

            # A paragraph that crosses a PDF page boundary usually resumes at
            # the normal left margin (about 60 pt) instead of the 73 pt first-
            # line indent.  The punctuation fallback covers layout variants.
            is_page_continuation = (
                bool(paragraphs)
                and previous_page is not None
                and block.page_number != previous_page
                and (block.first_line_x0 < 68 or not TERMINAL_PUNCTUATION_RE.search(paragraphs[-1]))
            )
            if is_page_continuation:
                paragraphs[-1] = normalize_text(f"{paragraphs[-1]} {text}")
            else:
                paragraphs.append(text)
            previous_page = block.page_number

    if not paragraphs:
        raise ValueError(f"No PDF body text extracted for chapter {number}")
    return ChapterText(number, title, tuple(paragraphs), "pdf")


def chapter_xhtml(chapter: ChapterText) -> str:
    heading = html.escape(chapter.title, quote=True)
    body = "\n".join(f"<p>{html.escape(p, quote=True)}</p>" for p in chapter.paragraphs)
    return f"<h1>{heading}</h1>\n{body}\n"


def write_epub(chapters: list[ChapterText], output: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("lord-of-the-mysteries-complete-repaired")
    book.set_title("Lord of the Mysteries")
    book.set_language("en")
    book.add_author("Cuttlefish That Loves Diving")

    stylesheet = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=STYLE,
    )
    book.add_item(stylesheet)

    items: list[epub.EpubHtml] = []
    for chapter in chapters:
        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chapter_{chapter.number:04d}.xhtml",
            lang="en",
        )
        item.content = chapter_xhtml(chapter)
        item.add_item(stylesheet)
        book.add_item(item)
        items.append(item)

    book.toc = tuple(items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *items]
    output.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(output, book)


def write_report(
    chapters: list[ChapterText],
    source_epub: Path,
    source_pdf: Path,
    output: Path,
    report_path: Path,
) -> None:
    forbidden = ("vipnovel", "<h5>", "</anno>", TABLE_OF_CONTENTS_MARKER)
    forbidden_hits = [
        {"chapter": chapter.number, "term": term}
        for chapter in chapters
        for term in forbidden
        if term.lower() in "\n".join(chapter.paragraphs).lower()
    ]
    pdf_chapters = [chapter.number for chapter in chapters if chapter.source == "pdf"]
    lengths = [sum(len(p) for p in chapter.paragraphs) for chapter in chapters]
    report = {
        "title": "Lord of the Mysteries",
        "author": "Cuttlefish That Loves Diving",
        "source_epub": str(source_epub.resolve()),
        "source_pdf": str(source_pdf.resolve()),
        "repaired_epub": str(output.resolve()),
        "chapter_count": len(chapters),
        "first_chapter": chapters[0].title,
        "last_chapter": chapters[-1].title,
        "pdf_restored_chapters": pdf_chapters,
        "chapter_text_characters": {
            "total": sum(lengths),
            "min": min(lengths),
            "max": max(lengths),
        },
        "forbidden_content_hits": forbidden_hits,
        "checks": {
            "all_1409_chapters_present": [c.number for c in chapters]
            == list(range(1, EXPECTED_CHAPTERS + 1)),
            "ten_missing_chapters_restored_from_pdf": len(pdf_chapters) == 10,
            "source_scraper_artifacts_removed": not forbidden_hits,
            "all_chapters_have_body_text": all(c.paragraphs for c in chapters),
            "output_exists": output.is_file() and output.stat().st_size > 0,
        },
    }
    report["passed"] = all(report["checks"].values())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_epub", type=Path)
    parser.add_argument("source_pdf", type=Path)
    parser.add_argument("output_epub", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    existing = source_chapters(args.source_epub)
    missing = sorted(set(range(1, EXPECTED_CHAPTERS + 1)) - existing.keys())
    if len(missing) != 10:
        raise ValueError(f"Expected ten missing EPUB chapters, found {missing}")
    print(f"Source EPUB has {len(existing)} chapters; restoring {missing}", flush=True)

    document = pymupdf.open(args.source_pdf)
    locations = pdf_chapter_locations(document)
    for number in missing:
        existing[number] = extract_pdf_chapter(document, locations, number)
        print(f"Restored chapter {number} from PDF", flush=True)

    chapters = [existing[number] for number in range(1, EXPECTED_CHAPTERS + 1)]
    write_epub(chapters, args.output_epub)
    write_report(chapters, args.source_epub, args.source_pdf, args.output_epub, args.report)
    print(f"Wrote repaired EPUB: {args.output_epub}", flush=True)
    print(f"Wrote verification report: {args.report}", flush=True)


if __name__ == "__main__":
    main()
