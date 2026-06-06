"""Worm (by Wildbow) scraper."""

import argparse
import html
import logging
import sys
import time

import requests
from bs4 import BeautifulSoup
from ebooklib import epub

from epub_listener.scrapers.base import NovelScraper

logger = logging.getLogger(__name__)

TOC_URL = "https://parahumans.wordpress.com/table-of-contents/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


class WormScraper(NovelScraper):
    """Scrapes Worm from parahumans.wordpress.com into an EPUB."""

    def __init__(self, delay: float = 1.5, start: int = 0, end: int | None = None) -> None:
        self.delay = delay
        self.start = start
        self.end = end

    def scrape(self, output_path: str) -> None:
        """Scrape Worm and save as EPUB.

        Raises:
            ValueError: If the selected chapter range is empty.
        """
        with requests.Session() as session:
            all_links = self._get_chapter_links(session)
            links = all_links[self.start : self.end]

            if not links:
                raise ValueError("No chapters in the selected range.")

            logger.info("Scraping %d chapters...", len(links))
            chapters: list[tuple[str, str]] = []
            for i, (title, url) in enumerate(links):
                logger.info("  [%d/%d] %s", i + 1, len(links), title)
                try:
                    body = self._fetch_chapter_html(url, session)
                    chapters.append((title, body))
                except Exception as exc:
                    logger.warning("Failed to fetch %s: %s", url, exc)
                    chapters.append((title, f"<p>[Failed to fetch: {html.escape(str(exc))}]</p>"))
                if i < len(links) - 1:
                    time.sleep(self.delay)

        self._build_epub(chapters, output_path)

    def _get_chapter_links(self, session: requests.Session) -> list[tuple[str, str]]:
        logger.info("Fetching table of contents from %s", TOC_URL)
        resp = session.get(TOC_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        content = soup.select_one(".entry-content")
        if not content:
            raise RuntimeError("Could not find .entry-content on ToC page.")

        links: list[tuple[str, str]] = []
        for a in content.find_all("a", href=True):
            raw_href = a.get("href")
            if not isinstance(raw_href, str):
                continue
            href = raw_href
            if href.startswith("parahumans.wordpress.com"):
                href = "https://" + href
            if not href.startswith("https://parahumans.wordpress.com/"):
                continue
            if "?share=" in href or "?like=" in href:
                continue
            if href.rstrip("/") == TOC_URL.rstrip("/"):
                continue
            title = a.get_text(strip=True)
            if title:
                links.append((title, href))

        logger.info("Found %d chapters in ToC.", len(links))
        return links

    def _fetch_chapter_html(self, url: str, session: requests.Session) -> str:
        resp = session.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return self._extract_chapter_html(soup)

    def _extract_chapter_html(self, soup: BeautifulSoup) -> str:
        content = soup.select_one(".entry-content")
        if not content:
            return "<p>[Could not extract content]</p>"

        for p in content.find_all("p"):
            text = p.get_text(strip=True)
            links_in_p = p.find_all("a")
            if links_in_p and all(
                any(
                    kw in a.get_text(strip=True).lower() for kw in ("prev", "next", "last", "first")
                )
                for a in links_in_p
            ):
                p.decompose()
                continue
            if not text:
                p.decompose()

        for a in content.find_all("a"):
            a.unwrap()

        for widget in content.find_all("div", id=lambda x: x and "jp-post-flair" in x):
            widget.decompose()
        for widget in content.find_all("div", class_="sharedaddy"):
            widget.decompose()
        for tag in content.find_all(["script", "style"]):
            tag.decompose()

        return str(content)

    def _build_epub(self, chapters: list[tuple[str, str]], output_path: str) -> None:
        book = epub.EpubBook()
        book.set_identifier("worm-wildbow")
        book.set_title("Worm")
        book.set_language("en")
        book.add_author("Wildbow (J.C. McCrae)")
        book.set_cover("cover.xhtml", b"", create_page=False)

        epub_chapters = []
        spine = ["nav"]
        for i, (title, html_body) in enumerate(chapters):
            chapter_id = f"chapter_{i:04d}"
            file_name = f"{chapter_id}.xhtml"
            c = epub.EpubHtml(title=title, file_name=file_name, lang="en")
            c.content = f"<html><body><h1>{html.escape(title)}</h1>{html_body}</html>"
            book.add_item(c)
            epub_chapters.append(c)
            spine.append(c)

        book.toc = tuple(epub.Link(c.file_name, c.title, c.id) for c in epub_chapters)
        book.spine = spine
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(output_path, book)
        logger.info("Saved EPUB to: %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Worm into an EPUB")
    parser.add_argument("--output", default="worm.epub", help="Output file path")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests (s)")
    parser.add_argument("--start", type=int, default=0, help="First chapter index (0-based)")
    parser.add_argument("--end", type=int, default=None, help="Chapter index to stop before")
    args = parser.parse_args()

    scraper = WormScraper(delay=args.delay, start=args.start, end=args.end)
    try:
        scraper.scrape(args.output)
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
