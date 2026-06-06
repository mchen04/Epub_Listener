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
```

## Adding a New TTS Provider

1. Create `epub_listener/infrastructure/tts/my_tts.py`.
2. Implement the `TTSProvider` Protocol from `epub_listener/application/ports.py`.
3. Wire it in `epub_listener/__main__.py`.

```python
from pathlib import Path
from epub_listener.application.ports import ConcurrencyStrategy

class MyTTSProvider:
    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Return duration in ms, or 0 on failure (do not raise for per-chapter errors)."""
        ...

    def supports_concurrency(self) -> ConcurrencyStrategy:
        return "sequential"
```

`generate()` should return `0` on per-chapter failure so the orchestrator can skip that chapter and continue building. Only raise for unrecoverable errors (missing binary, invalid auth, etc.).

The `execute()` signature is `use_case.execute(settings, *, temp_dir=temp_dir)`. If you call `BuildAudiobookUseCase` directly (e.g., in tests), pass the same `temp_dir` to both `JsonProgressTracker` and `execute()` so cached audio and tracker state are co-located.

## Adding a New Scraper

1. Implement `NovelScraper` from `epub_listener/scrapers/base.py`.
2. Place in `epub_listener/scrapers/my_novel.py`.
3. Add a `main()` CLI entry point.

## Logging

Unified logging writes to console and rotating file (`logs/epub_listener.log`).
Configure via `--log-level` or `EPUB_LISTENER_LOG_LEVEL` env var.
