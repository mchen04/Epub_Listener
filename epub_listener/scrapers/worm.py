"""Worm (by Wildbow) scraper."""

import argparse
import html
import logging
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from ebooklib import epub

from epub_listener.infrastructure.utils.durable_file import durably_replace
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


class ScrapeError(RuntimeError):
    """Expected scraper failure caused by remote content or extraction shape."""


class WormScraper(NovelScraper):
    """Scrapes Worm from parahumans.wordpress.com into an EPUB."""

    def __init__(
        self,
        delay: float = 1.5,
        start: int = 0,
        end: int | None = None,
        allow_partial: bool = False,
    ) -> None:
        self.delay = delay
        self.start = start
        self.end = end
        self.allow_partial = allow_partial

    def scrape(self, output_path: str) -> None:
        """Scrape Worm and save as EPUB.

        Raises:
            ValueError: If the selected chapter range is empty.
            ScrapeError: If a chapter cannot be fetched or extracted and
                ``allow_partial`` is false.
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
                except (requests.RequestException, ScrapeError) as exc:
                    logger.warning("Failed to fetch %s: %s", url, exc)
                    if not self.allow_partial:
                        raise ScrapeError(f"Failed to fetch {title} ({url}): {exc}") from exc
                    chapters.append((title, f"<p>[Failed to fetch: {html.escape(str(exc))}]</p>"))
                if i < len(links) - 1:
                    time.sleep(self.delay)

        self._build_epub(chapters, output_path)

    def _get_chapter_links(self, session: requests.Session) -> list[tuple[str, str]]:
        logger.info("Fetching table of contents from %s", TOC_URL)
        try:
            resp = session.get(TOC_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ScrapeError(f"Failed to fetch table of contents ({TOC_URL}): {exc}") from exc
        soup = BeautifulSoup(resp.text, "html.parser")

        content = soup.select_one(".entry-content")
        if not content:
            raise ScrapeError("Could not find .entry-content on ToC page.")

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
            raise ScrapeError("Could not find .entry-content on chapter page.")

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
        output = Path(output_path)
        tmp_output = output.with_name(f".{output.stem}.tmp{output.suffix}")
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
        try:
            tmp_output.unlink(missing_ok=True)
            epub.write_epub(str(tmp_output), book)
            durably_replace(tmp_output, output)
        except OSError as exc:
            raise ScrapeError(f"Failed to write EPUB {output}: {exc}") from exc
        finally:
            tmp_output.unlink(missing_ok=True)
        logger.info("Saved EPUB to: %s", output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Worm into an EPUB")
    parser.add_argument("--output", default="worm.epub", help="Output file path")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests (s)")
    parser.add_argument("--start", type=int, default=0, help="First chapter index (0-based)")
    parser.add_argument("--end", type=int, default=None, help="Chapter index to stop before")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write an EPUB with placeholders for chapters that fail to fetch",
    )
    args = parser.parse_args()

    scraper = WormScraper(
        delay=args.delay,
        start=args.start,
        end=args.end,
        allow_partial=args.allow_partial,
    )
    try:
        scraper.scrape(args.output)
    except (ScrapeError, ValueError) as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
