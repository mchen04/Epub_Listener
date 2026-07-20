# Epub Listener

Convert any standard `.epub` file into a fully narrated `.mp3` audiobook with skippable chapters.

## Features
- **High-Quality AI Narration**: Microsoft Azure voices via Edge-TTS (free, no API key) or local Kokoro-82M.
- **MP3 with Chapters**: Embedded ID3 chapter metadata for skip-to-chapter support in VLC, QuickTime, etc.
- **Customizable Speed & Voice**: CLI flags for playback speed and voice selection.
- **Resume Support**: Checksum-based resume prevents re-generating completed chapters.
- **Concurrent Generation**: Shared batch runners drive async Edge-TTS and process-pooled Kokoro generation, with resume progress saved after each completed chapter.
- **Read-Along Transcript** (default on): captures the word timings the TTS engines already produce and embeds a timestamped transcript in the MP3 as one ID3 GEOB frame (plus a sidecar JSON), so read-along players like Hark can highlight the narrated sentence and mark the spoken word. Timestamps are chapter-relative and measured against the final assembled audio (median word-onset error ≤ 30 ms per engine). Kokoro and Edge-TTS produce word-level cues; MLX Kokoro falls back to sentence-level. Pass `--no-transcript` to disable; with it off, output behaves exactly as before. Format: [`docs/transcript-format.md`](docs/transcript-format.md).

## Prerequisites
- **Python 3.10+**
- **FFmpeg**: `brew install ffmpeg` (macOS) or equivalent.
- **espeak-ng** (Kokoro only): `brew install espeak-ng`.

## Installation

```bash
git clone https://github.com/mchen04/Epub_Listener.git
cd Epub_Listener
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Optional: editable install, adds the `epub-listener` console command
pip install -e .

# Optional: Apple-Silicon-optimized Kokoro inference
pip install '.[mlx]'
```

## Usage

```bash
# Basic usage
python -m epub_listener my_book.epub

# Custom voice and speed
python -m epub_listener my_book.epub --voice en-GB-RyanNeural --speed +15%

# Use local Kokoro TTS
python -m epub_listener my_book.epub --use-kokoro --kokoro-voice af_heart

# Tune Kokoro for Apple Silicon with one MPS and one CPU worker
python -m epub_listener my_book.epub --use-kokoro --kokoro-hybrid-mps

# Faster Kokoro inference on Apple Silicon (same voices, sequential MLX execution)
python -m epub_listener my_book.epub --use-kokoro --kokoro-mlx \
  --kokoro-voice af_heart --concurrency sequential

# Generate without the embedded read-along transcript
python -m epub_listener my_book.epub --no-transcript

# Override title and author metadata
python -m epub_listener my_book.epub --title "My Book" --author "Author Name"

# Resume an interrupted build (path is printed on interrupt/failure)
python -m epub_listener my_book.epub --resume-dir /tmp/epub_audiobook_xxx
```

### Background Execution

```bash
screen -S builder -d -m bash -c "source venv/bin/activate && python -m epub_listener input.epub > build.log 2>&1"
tail -f build.log
```

## Web Scrapers

`epub_listener/scrapers/` contains scrapers that produce EPUBs for the pipeline. Currently included: Worm (by Wildbow).

```bash
python -m epub_listener.scrapers.worm --output worm.epub
```

## Documentation
- [Architecture](docs/ARCHITECTURE.md)
- [Development](docs/DEVELOPMENT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
