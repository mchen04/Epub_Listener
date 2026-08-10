# Development

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Include generic Transformers support when working on it
pip install -e '.[dev,kokoro,huggingface]'

# Optional Apple MLX adapter and its live smoke test
pip install -e '.[dev,mlx]'
```

## Code quality

```bash
# Format
black epub_listener/ tests/

# Lint
ruff check epub_listener/ tests/

# Type check
mypy epub_listener/

# Offline/default suite
pytest

# Live network and real-model smoke tests
pytest -m live
```

## Extending TTS support

Most local models need no Python adapter:

- A Transformers-compatible TTS repository works through `--engine huggingface --model ...` and namespaced `--model-options`.
- Any executable that accepts text and writes audio works through `--engine command`; see [`MODELS.md`](MODELS.md).

Only add an infrastructure adapter for a genuinely different transport or a model API that can expose materially better timing/performance than those generic paths.

### Adding an adapter

1. Create `epub_listener/infrastructure/tts/my_tts.py`.
2. Implement the `TTSProvider` protocol from `epub_listener/infrastructure/tts/ports.py`, or subclass `WaveformTTSProvider` when the engine returns numeric waveform samples.
3. Register it through `create_tts_batch_generator()` in `epub_listener/infrastructure/tts/factory.py`.

```python
from pathlib import Path

from epub_listener.infrastructure.tts.waveform import AudioChunk, WaveformTTSProvider


class MyTTSProvider(WaveformTTSProvider):
    def synthesize_chunk(
        self,
        text: str,
        voice: str | None,
        *,
        work_dir: Path,
        chunk_index: int,
    ) -> AudioChunk:
        samples, sample_rate = my_engine.synthesize(text, voice=voice)
        return AudioChunk(samples, sample_rate)
```

`WaveformTTSProvider` supplies bounded text chunking, disk streaming, speed conversion, sentence-level transcript estimates, MP3 validation, temporary cleanup, and atomic replacement. A non-waveform provider owns one requested output file and must raise `TTSGenerationError` on failure.

Batch execution, callback ordering, cancellation, and failure-wave behavior belong in a `TTSBatchGenerator` adapter that reuses the helpers in `infrastructure/tts/batch.py`. The application fails the build rather than emitting a partial audiobook.

Every new option that can alter audio must participate in `Settings.tts_backend`; otherwise resume could reuse audio generated under a different model configuration.

## Application boundary

The use-case signature is `use_case.execute(command)`, where `command` is a `BuildAudiobookCommand`. In the CLI composition root, `BuildWorkspace` creates both the command and tracker to keep cached audio and tracker state co-located. Preserve that same-directory invariant when constructing the use case directly.

## Adding a scraper

1. Implement `NovelScraper` from `epub_listener/scrapers/base.py`.
2. Place it in `epub_listener/scrapers/my_novel.py`.
3. Add a `main()` CLI entry point.

## Logging

Unified logging writes to the console and a rotating file at `logs/epub_listener.log`. Configure it with `--log-level` or `EPUB_LISTENER_LOG_LEVEL`.

## Release checklist

Run the complete offline quality gate from an environment with the development extras:

```bash
black --check epub_listener tests
ruff check epub_listener tests
mypy epub_listener
pytest -q
git diff --check
```

Then run `pytest -m live` on hardware with the configured engines, build both artifacts with `uv build`, and install the wheel into a fresh virtual environment. Confirm that `epub-listener --help` works and perform one EPUB-to-MP3 conversion through an installed backend before publishing.
