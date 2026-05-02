# Epub Listener

Epub Listener is a Python-based audiobook generator that takes any standard `.epub` file and transforms it into a fully narrated `.mp3` audiobook with skip-able chapters. It uses **Edge-TTS** (Microsoft Azure's premium AI voices — free, no API key needed).

## Features
- **High-Quality AI Narration**: Uses Microsoft Azure's premium AI voices via Edge-TTS.
- **MP3 with Chapters**: Output is `.mp3` with embedded ID3 chapter metadata for skip-to-chapter support.
- **Customizable Playback Speed**: Accelerate or decelerate the narration speed directly from the CLI.
- **Voice Selection**: Choose from a variety of Edge-TTS voices.
- **Local TTS (Kokoro-82M)**: Optionally use the lightweight, high-quality local Kokoro model for offline generation.
- **Resume Support**: Resume interrupted builds without re-generating completed chapters.

## Prerequisites
- **Python 3.10+**
- **FFmpeg**: Required for audio assembly and metadata embedding.
  - On macOS: `brew install ffmpeg`
- **espeak-ng** (only if using `--use-kokoro`): Required by Kokoro for text processing.
  - On macOS: `brew install espeak-ng`

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/mchen04/Epub_Listener.git
   cd Epub_Listener
   ```

2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install the required Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

```bash
# Basic usage — outputs an MP3 into the outputs/ directory
python src/build_audiobook.py my_book.epub
```

### Options

- **Custom output path**:
  ```bash
  python src/build_audiobook.py my_book.epub /tmp/my_audiobook.mp3
  ```

- **Output directory**:
  ```bash
  python src/build_audiobook.py my_book.epub --output-dir ~/Desktop/Audiobooks/
  ```

- **Playback speed** (`--speed`):
  ```bash
  python src/build_audiobook.py my_book.epub --speed +15%
  ```

- **Voice** (`--voice`): Defaults to `en-US-AriaNeural`.
  ```bash
  python src/build_audiobook.py my_book.epub --voice en-GB-RyanNeural
  ```

- **Resume an interrupted build** (`--resume-dir`):
  ```bash
  python src/build_audiobook.py my_book.epub --resume-dir /tmp/epub_audiobook_xyz
  ```

- **Use local Kokoro TTS** (`--use-kokoro`):
  ```bash
  python src/build_audiobook.py my_book.epub --use-kokoro
  ```

- **Kokoro voice & language** (`--kokoro-voice`, `--kokoro-lang`):
  ```bash
  python src/build_audiobook.py my_book.epub --use-kokoro --kokoro-voice af_heart --kokoro-lang a
  ```
  See the full [voice list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md) for available options.

### Running in the Background
Audio generation for large books (e.g., 50+ chapters) can take a long time. Run in a persistent session so your OS doesn't suspend it:

```bash
# Launch in a background screen session
screen -S builder -d -m bash -c "source venv/bin/activate && python src/build_audiobook.py input.epub > build.log 2>&1"

# Monitor progress
tail -f build.log
```

## How It Works
1. **Parser**: Uses `EbookLib` and `BeautifulSoup4` to crawl the EPUB's navigation structure, extract chapter titles, and scrub HTML to plain text.
2. **Audio Generation**: Feeds each chapter's text to Edge-TTS (cloud) or Kokoro-82M (local) to produce a `.mp3` audio segment.
3. **Assembly**: Uses `ffmpeg` to concatenate all chapter segments, generate an `FFMETADATA1` chapter map, and merge everything into a single `.mp3` with embedded chapter metadata and ID3 tags.
