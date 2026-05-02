# Epub_Listener - Technical Specifications

## 1. Overview
Epub_Listener is an audiobook generation tool that takes an EPUB file as input and outputs a single `.mp3` file containing the full text synthesized into realistic AI voice audio. The output file supports chapter navigation to easily skip to certain chapters.

## 2. Core Features
- **EPUB Parsing**: Extract text content and chapter structure from input EPUB files.
- **AI Text-to-Speech (TTS) Engine**: Convert text to high-quality audio using Edge-TTS (Microsoft Azure cloud voices).
- **Speed Customization**: Allow the user to specify the reading speed of the generated audio.
- **Chapter Navigation**: Combine the generated audio into a final MP3 with embedded chapter metadata to allow skipping between chapters.

## 3. Technology Stack & Research Findings

### 3.1 AI TTS — Edge-TTS
- Python wrapper around Microsoft Edge's "Read Aloud" feature (Azure's premium AI voices)
- Requires internet connection but no API key
- No enforced character limits, very natural-sounding voices
- Speed adjustment supported via rate parameter
- Voices: en-US-AriaNeural (default), en-GB-RyanNeural, en-US-GuyNeural, etc.

### 3.1b AI TTS — Kokoro-82M (local, optional)
- Open-weight TTS model with 82 million parameters (StyleTTS 2 architecture)
- Runs entirely offline; no internet or API key required after first download
- Comparable quality to larger models while being significantly faster and more cost-efficient
- Outputs 24 kHz WAV, converted internally to MP3 via ffmpeg
- Supports voice selection and speed control
- First run downloads ~300 MB of model weights; requires `espeak-ng` system package
- Language codes: `a` (American English, default), `b` (British English), plus other languages
- See available voices at: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md

### 3.2 Media Format & Chapter Navigation
- **FFmpeg Integration**: FFmpeg encodes the final output MP3 by concatenating per-chapter audio segments.
- **Chapter Metadata**: Created via `FFMETADATA1` format with `[CHAPTER]` blocks containing millisecond start/end times.
- **ID3 Tags**: FFmpeg embeds ID3v1/v2.3 tags (title, artist, album) directly into the MP3.

## 4. Architecture

- **Format:** Output is `.mp3` with embedded chapter metadata.
- **Chapter Navigation:** Works in VLC, QuickTime, iPhone, Google Drive, etc.
- **Application Interface:** Python CLI: `python src/build_audiobook.py book.epub [options]`
- **Primary Technologies:** Python, Edge-TTS, FFmpeg, EbookLib, BeautifulSoup4.
