#!/usr/bin/env python3
"""
Scrape Worm (by Wildbow) from parahumans.wordpress.com and produce an epub.

Usage:
    python scrape_worm.py [--output worm.epub] [--delay 1.5] [--start 0] [--end N]

Options:
    --output   Output epub filename (default: worm.epub)
    --delay    Seconds to wait between requests (default: 1.5)
    --start    Index of first chapter to include (0-based, default: 0)
    --end      Index after last chapter to include (default: all)
"""

import argparse
import time
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from ebooklib import epub

TOC_URL = "https://parahumans.wordpress.com/table-of-contents/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch(url: str, session: requests.Session) -> BeautifulSoup:
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_chapter_links(session: requests.Session) -> list[tuple[str, str]]:
    """Return list of (title, url) from the table of contents."""
    print(f"Fetching table of contents from {TOC_URL}")
    soup = fetch(TOC_URL, session)

    # The ToC is a list of links inside .entry-content
    content = soup.select_one(".entry-content")
    if not content:
        sys.exit("Could not find .entry-content on the ToC page.")

    links = []
    for a in content.find_all("a", href=True):
        href = a["href"]
        # Fix scheme-less URLs (e.g. "parahumans.wordpress.com/...")
        if href.startswith("parahumans.wordpress.com"):
            href = "https://" + href
        # Only include actual chapter pages on the site
        if not href.startswith("https://parahumans.wordpress.com/"):
            continue
        # Skip social share links (?share=twitter, ?share=facebook, etc.)
        if "?share=" in href or "?like=" in href:
            continue
        # Skip the ToC page itself and any non-post pages
        if href.rstrip("/") == TOC_URL.rstrip("/"):
            continue
        title = a.get_text(strip=True)
        if title:
            links.append((title, href))

    print(f"Found {len(links)} chapters in the table of contents.")
    return links


def extract_chapter_html(soup: BeautifulSoup, title: str) -> str:
    """Extract clean chapter body HTML."""
    content = soup.select_one(".entry-content")
    if not content:
        return f"<p>[Could not extract content for {title}]</p>"

    # Remove navigation paragraphs (Prev/Next links at top/bottom)
    for p in content.find_all("p"):
        text = p.get_text(strip=True)
        links_in_p = p.find_all("a")
        # If the paragraph is only nav links, drop it
        if links_in_p and all(
            any(kw in a.get_text(strip=True).lower() for kw in ("prev", "next", "last", "first"))
            for a in links_in_p
        ):
            p.decompose()
            continue
        # Also drop empty paragraphs
        if not text:
            p.decompose()

    # Strip all <a> tags but keep their text
    for a in content.find_all("a"):
        a.unwrap()

    # Remove WordPress share/like widgets
    for widget in content.find_all("div", id=lambda x: x and "jp-post-flair" in x):
        widget.decompose()
    for widget in content.find_all("div", class_="sharedaddy"):
        widget.decompose()

    # Remove any script/style tags
    for tag in content.find_all(["script", "style"]):
        tag.decompose()

    return str(content)


def build_epub(
    chapters: list[tuple[str, str]],
    output_path: str,
) -> None:
    book = epub.EpubBook()
    book.set_identifier("worm-wildbow")
    book.set_title("Worm")
    book.set_language("en")
    book.add_author("Wildbow (J.C. McCrae)")

    book.set_cover(
        "cover.xhtml",
        b"",  # no image — placeholder
        create_page=False,
    )

    epub_chapters = []
    spine = ["nav"]

    for i, (title, html_body) in enumerate(chapters):
        chapter_id = f"chapter_{i:04d}"
        file_name = f"{chapter_id}.xhtml"

        c = epub.EpubHtml(title=title, file_name=file_name, lang="en")
        c.content = f"<html><body><h1>{title}</h1>{html_body}</html>"
        book.add_item(c)
        epub_chapters.append(c)
        spine.append(c)

    book.toc = tuple(epub.Link(c.file_name, c.title, c.id) for c in epub_chapters)
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(output_path, book)
    print(f"\nSaved epub to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Worm into an epub")
    parser.add_argument("--output", default="worm.epub", help="Output file path")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests (s)")
    parser.add_argument("--start", type=int, default=0, help="First chapter index (0-based)")
    parser.add_argument("--end", type=int, default=None, help="Chapter index to stop before")
    args = parser.parse_args()

    session = requests.Session()

    all_links = get_chapter_links(session)
    links = all_links[args.start : args.end]

    if not links:
        sys.exit("No chapters in the selected range.")

    print(f"Scraping {len(links)} chapters (indices {args.start}–{(args.end or len(all_links)) - 1})")

    chapters: list[tuple[str, str]] = []
    for i, (title, url) in enumerate(links):
        print(f"  [{i + 1}/{len(links)}] {title}")
        try:
            soup = fetch(url, session)
            html = extract_chapter_html(soup, title)
            chapters.append((title, html))
        except Exception as exc:
            print(f"    WARNING: failed to fetch {url}: {exc}")
            chapters.append((title, f"<p>[Failed to fetch: {exc}]</p>"))

        if i < len(links) - 1:
            time.sleep(args.delay)

    print(f"\nBuilding epub with {len(chapters)} chapters...")
    build_epub(chapters, args.output)


if __name__ == "__main__":
    main()
