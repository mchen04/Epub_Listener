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
2. Implement `TTSProvider` from `epub_listener/application/ports.py`.
3. Wire it in `epub_listener/__main__.py`.

Example:

```python
from epub_listener.application.ports import TTSProvider

class MyTTSProvider(TTSProvider):
    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        ...
    def supports_concurrency(self) -> str:
        return "sequential"
```

## Adding a New Scraper

1. Implement `NovelScraper` from `epub_listener/scrapers/base.py`.
2. Place in `epub_listener/scrapers/my_novel.py`.
3. Add a `main()` CLI entry point.

## Logging

Unified logging writes to console and rotating file (`logs/epub_listener.log`).
Configure via `--log-level` or `EPUB_LISTENER_LOG_LEVEL` env var.
