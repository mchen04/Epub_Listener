# Development

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

## Code Quality

```bash
# Format
black epub_listener/

# Lint
ruff check epub_listener/

# Type check
mypy epub_listener/

# Live TTS/network smoke tests
pytest -m live
```

## Adding a New TTS Provider

1. Create `epub_listener/infrastructure/tts/my_tts.py`.
2. Implement the `TTSProvider` Protocol from `epub_listener/infrastructure/tts/ports.py`.
3. Register it through `create_tts_batch_generator()` in `epub_listener/infrastructure/tts/factory.py`.

```python
from pathlib import Path
from epub_listener.infrastructure.tts.ports import TTSProvider

class MyTTSProvider(TTSProvider):
    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Generate one file and return its positive duration in milliseconds."""
        ...
```

Providers only write one requested output file. Batch execution, callback ordering, cancellation, and failure-wave behavior belong in a `TTSBatchGenerator` adapter that reuses the shared helpers in `epub_listener/infrastructure/tts/batch.py`. Raise `TTSGenerationError` on any failed chapter; the application fails the build instead of emitting a partial audiobook.

The `execute()` signature is `use_case.execute(command)`, where `command` is a `BuildAudiobookCommand`. In the CLI composition root, `BuildWorkspace` creates both the command and tracker to keep cached audio and tracker state co-located. If you call `BuildAudiobookUseCase` directly, preserve that same-directory invariant.

## Adding a New Scraper

1. Implement `NovelScraper` from `epub_listener/scrapers/base.py`.
2. Place in `epub_listener/scrapers/my_novel.py`.
3. Add a `main()` CLI entry point.

## Logging

Unified logging writes to console and rotating file (`logs/epub_listener.log`).
Configure via `--log-level` or `EPUB_LISTENER_LOG_LEVEL` env var.
