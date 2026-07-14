"""EPUB parser using EbookLib."""

import logging
import warnings
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from epub_listener.application.ports import ChapterParser
from epub_listener.domain.exceptions import ParseError
from epub_listener.domain.models import Chapter

logger = logging.getLogger(__name__)

# EbookLib is noisy about future versions; suppress once at module load.
warnings.filterwarnings("ignore", category=UserWarning, module="ebooklib.epub")
warnings.filterwarnings("ignore", category=FutureWarning, module="ebooklib.epub")


class EbookLibParser(ChapterParser):
    """Parses EPUB files into Chapter objects using EbookLib + BeautifulSoup."""

    MIN_CHAPTER_LENGTH = 100  # characters; filters frontmatter / debris

    def parse(self, epub_path: Path) -> list[Chapter]:
        """Extract chapters from the given EPUB file."""
        if not epub_path.exists():
            raise ParseError(f"EPUB file not found: {epub_path}")

        try:
            book = epub.read_epub(str(epub_path))
        except Exception as exc:
            raise ParseError(f"Failed to read EPUB file: {exc}") from exc

        raw_chapters: list[Chapter] = []
        seen_titles: set[str] = set()
        doc_index = 0

        for item in book.get_items():
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            # EbookLib reports the EPUB 3 navigation document as ordinary
            # XHTML.  Large tables of contents can exceed the length filter
            # and otherwise become a fake final audiobook chapter.
            if isinstance(item, epub.EpubNav):
                continue

            soup = BeautifulSoup(item.get_body_content(), "html.parser")

            heading = soup.find(["h1", "h2", "title"])
            title = heading.get_text(strip=True) if heading else "Unknown Chapter"

            if title in seen_titles:
                title = f"{title} ({doc_index})"
            seen_titles.add(title)

            text = soup.get_text(separator="\n")
            cleaned_lines = [line.strip() for line in text.split("\n") if line.strip()]
            cleaned_text = "\n\n".join(cleaned_lines)

            if len(cleaned_text) >= self.MIN_CHAPTER_LENGTH:
                chapter_id = f"{doc_index:04d}"
                raw_chapters.append(Chapter(id=chapter_id, title=title, text=cleaned_text))
                doc_index += 1

        # Secondary dedup: drop chapters with identical text
        unique: list[Chapter] = []
        seen_texts: set[str] = set()
        for ch in raw_chapters:
            if ch.text not in seen_texts:
                seen_texts.add(ch.text)
                unique.append(ch)

        logger.info("Parsed %d unique chapters from %s", len(unique), epub_path)
        return unique
