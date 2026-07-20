"""Embeds the combined read-along transcript into the final MP3's ID3 tag."""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

from epub_listener import __version__
from epub_listener.application.ports import TranscriptEmbedder, transcript_path_for
from epub_listener.domain.models import AudioSegment
from epub_listener.domain.transcript import (
    GEOB_DESCRIPTION,
    GEOB_FILENAME,
    GEOB_MIME,
    BookTranscript,
    ChapterTranscript,
    book_transcript_to_dict,
    parse_book_transcript,
    parse_chapter_file,
)
from epub_listener.infrastructure.utils.durable_file import fsync_path, write_text_durably

logger = logging.getLogger(__name__)


class Id3TranscriptEmbedder(TranscriptEmbedder):
    """Adds one GEOB frame to the assembled MP3 and writes a sidecar JSON.

    Chapter indexes are assigned from the same segment ordering and
    positive-duration filter the FFMETADATA builder uses, so transcript
    chapter i always refers to the MP3's i-th chapter marker.
    """

    def embed(
        self,
        segments: list[AudioSegment],
        chapter_titles: dict[str, str],
        engine: str,
        generation_key: str,
        output: Path,
    ) -> bool:
        try:
            document = self._combined_document(segments, chapter_titles, engine, generation_key)
            payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
            self._write_frame(output, payload)
            write_text_durably(output.with_suffix(".transcript.json"), payload + "\n")
        except Exception as exc:
            # The audiobook is already durably written by the time this runs, so
            # NO transcript problem may fail the build. Corrupt or unreadable
            # chapter files (UnicodeDecodeError/JSON), schema violations
            # (TranscriptError), and gzip/mutagen/tagging failures are all
            # contained here: log and skip the transcript, keep the playable MP3.
            logger.warning("Transcript embedding skipped: %s", exc)
            return False
        return True

    def _combined_document(
        self,
        segments: list[AudioSegment],
        chapter_titles: dict[str, str],
        engine: str,
        generation_key: str,
    ) -> dict:
        chapters: list[ChapterTranscript] = []
        index = 0
        for segment in segments:
            if segment.duration_ms <= 0:
                # The metadata builder skips these, so they have no chapter
                # marker; skipping keeps transcript indexes aligned.
                continue
            chapter_file = transcript_path_for(segment.path)
            parsed = parse_chapter_file(
                json.loads(chapter_file.read_text(encoding="utf-8")),
                where=f"chapter transcript {chapter_file.name}",
            )
            chapters.append(
                ChapterTranscript(
                    index=index,
                    title=chapter_titles.get(segment.chapter_id, "Unknown"),
                    granularity=parsed["granularity"],
                    sentences=parsed["sentences"],
                )
            )
            index += 1
        document = book_transcript_to_dict(
            BookTranscript(
                producer=f"epub_listener/{__version__}",
                engine=engine,
                generation_key=generation_key,
                language="en",
                chapters=tuple(chapters),
            )
        )
        parse_book_transcript(document)
        return document

    def _write_frame(self, output: Path, payload: str) -> None:
        try:
            from mutagen.id3 import GEOB, ID3, Encoding
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise OSError(f"mutagen is not installed: {exc}") from exc

        compressed = gzip.compress(payload.encode("utf-8"), mtime=0)
        tags = ID3(output)
        tags.delall("GEOB")
        tags.add(
            GEOB(
                encoding=Encoding.LATIN1,
                mime=GEOB_MIME,
                filename=GEOB_FILENAME,
                desc=GEOB_DESCRIPTION,
                data=compressed,
            )
        )
        tags.save(output, v2_version=3)
        fsync_path(output)
