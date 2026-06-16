from pathlib import Path

import pytest
import requests

from epub_listener.scrapers import worm as worm_module
from epub_listener.scrapers.worm import ScrapeError, WormScraper


def test_worm_scraper_fails_by_default_when_any_chapter_fetch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def chapter_links(self: WormScraper, session: object) -> list[tuple[str, str]]:
        return [("One", "https://example.test/one"), ("Two", "https://example.test/two")]

    def fetch(self: WormScraper, url: str, session: object) -> str:
        if url.endswith("/two"):
            raise ScrapeError("unavailable")
        return "<p>chapter</p>"

    monkeypatch.setattr(WormScraper, "_get_chapter_links", chapter_links)
    monkeypatch.setattr(WormScraper, "_fetch_chapter_html", fetch)

    output = tmp_path / "worm.epub"
    with pytest.raises(ScrapeError, match="Failed to fetch Two"):
        WormScraper(delay=0).scrape(str(output))

    assert not output.exists()


def test_worm_scraper_allow_partial_writes_escaped_failure_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built_chapters: list[tuple[str, str]] = []

    def chapter_links(self: WormScraper, session: object) -> list[tuple[str, str]]:
        return [("One", "https://example.test/one"), ("Two", "https://example.test/two")]

    def fetch(self: WormScraper, url: str, session: object) -> str:
        if url.endswith("/two"):
            raise ScrapeError("bad <tag>")
        return "<p>chapter</p>"

    def build_epub(
        self: WormScraper,
        chapters: list[tuple[str, str]],
        output_path: str,
    ) -> None:
        built_chapters.extend(chapters)
        Path(output_path).write_bytes(b"epub")

    monkeypatch.setattr(WormScraper, "_get_chapter_links", chapter_links)
    monkeypatch.setattr(WormScraper, "_fetch_chapter_html", fetch)
    monkeypatch.setattr(WormScraper, "_build_epub", build_epub)

    output = tmp_path / "worm.epub"
    WormScraper(delay=0, allow_partial=True).scrape(str(output))

    assert output.exists()
    assert built_chapters == [
        ("One", "<p>chapter</p>"),
        ("Two", "<p>[Failed to fetch: bad &lt;tag&gt;]</p>"),
    ]


def test_worm_scraper_toc_fetch_failure_raises_scrape_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = requests.Session()

    def fail_get(*args: object, **kwargs: object) -> requests.Response:
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(session, "get", fail_get)

    with pytest.raises(ScrapeError, match="Failed to fetch table of contents"):
        WormScraper()._get_chapter_links(session)


def test_worm_build_epub_writes_temp_then_durably_replaces_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "worm.epub"
    output.write_bytes(b"old")
    tmp_output = tmp_path / ".worm.tmp.epub"
    events: list[tuple[object, ...]] = []

    def fake_write_epub(path: str, book: object) -> None:
        events.append(("write", Path(path)))
        Path(path).write_bytes(b"new")

    def fake_durably_replace(source: Path, target: Path) -> None:
        events.append(("replace", source, target))
        target.write_bytes(source.read_bytes())
        source.unlink()

    monkeypatch.setattr(worm_module.epub, "write_epub", fake_write_epub)
    monkeypatch.setattr(worm_module, "durably_replace", fake_durably_replace)

    WormScraper(delay=0)._build_epub([("One", "<p>chapter</p>")], str(output))

    assert output.read_bytes() == b"new"
    assert not tmp_output.exists()
    assert events == [
        ("write", tmp_output),
        ("replace", tmp_output, output),
    ]


def test_worm_build_epub_preserves_existing_output_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "worm.epub"
    output.write_bytes(b"old")
    tmp_output = tmp_path / ".worm.tmp.epub"

    def fake_write_epub(path: str, book: object) -> None:
        Path(path).write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(worm_module.epub, "write_epub", fake_write_epub)

    with pytest.raises(ScrapeError, match="Failed to write EPUB"):
        WormScraper(delay=0)._build_epub([("One", "<p>chapter</p>")], str(output))

    assert output.read_bytes() == b"old"
    assert not tmp_output.exists()
