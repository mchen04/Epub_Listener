from pathlib import Path

from ebooklib import epub

from epub_listener.infrastructure.parsers.ebooklib_parser import EbookLibParser


def test_parser_does_not_treat_large_navigation_document_as_chapter(tmp_path: Path) -> None:
    path = tmp_path / "large-navigation.epub"
    book = epub.EpubBook()
    book.set_identifier("large-navigation")
    book.set_title("Large Navigation")
    book.set_language("en")

    chapter = epub.EpubHtml(title="Chapter 1", file_name="chapter.xhtml", lang="en")
    chapter.content = "<h1>Chapter 1</h1><p>" + "This is actual chapter prose. " * 10 + "</p>"
    book.add_item(chapter)
    book.toc = tuple(
        epub.Link("chapter.xhtml", f"An intentionally verbose chapter link {number}", str(number))
        for number in range(50)
    )
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    epub.write_epub(path, book)

    chapters = EbookLibParser().parse(path)

    assert len(chapters) == 1
    assert chapters[0].title == "Chapter 1"
    assert "intentionally verbose chapter link" not in chapters[0].text
