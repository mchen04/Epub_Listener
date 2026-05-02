"""Scraper base protocol."""

from typing import Protocol


class NovelScraper(Protocol):
    """Protocol for web novel scrapers that produce EPUB files."""

    def scrape(self, output_path: str) -> None:
        """Scrape the novel and write an EPUB to output_path."""
        ...
