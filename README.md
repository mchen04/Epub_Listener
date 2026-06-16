# Epub Listener

Convert any standard `.epub` file into a fully narrated `.mp3` audiobook with skippable chapters.

## Features
- **High-Quality AI Narration**: Microsoft Azure voices via Edge-TTS (free, no API key) or local Kokoro-82M.
- **MP3 with Chapters**: Embedded ID3 chapter metadata for skip-to-chapter support in VLC, QuickTime, etc.
- **Customizable Speed & Voice**: CLI flags for playback speed and voice selection.
- **Resume Support**: Checksum-based resume prevents re-generating completed chapters.
- **Concurrent Generation**: Shared batch runners drive async Edge-TTS and process-pooled Kokoro generation, with resume progress saved after each completed chapter.

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
```

## Usage

```bash
# Basic usage
python -m epub_listener my_book.epub

# Custom voice and speed
python -m epub_listener my_book.epub --voice en-GB-RyanNeural --speed +15%

# Use local Kokoro TTS
python -m epub_listener my_book.epub --use-kokoro --kokoro-voice af_heart

# Resume an interrupted build (path is printed on interrupt/failure)
python -m epub_listener my_book.epub --resume-dir /tmp/epub_audiobook_xxx
```

### Background Execution

```bash
screen -S builder -d -m bash -c "source venv/bin/activate && python -m epub_listener input.epub > build.log 2>&1"
tail -f build.log
```

## Documentation
- [Architecture](docs/ARCHITECTURE.md)
- [Development](docs/DEVELOPMENT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
