# Epub_Listener - Technical Specifications

## 1. Overview
Epub_Listener is an audiobook generation tool that takes an EPUB file as input and outputs a single media file (such as `.m4b` or `.mp4`) containing the full text synthesized into realistic AI voice audio. The output file must support chapter navigation to easily skip to certain chapters.

## 2. Core Features
- **EPUB Parsing**: Extract text content and chapter structure from input EPUB files.
- **AI Text-to-Speech (TTS) Engine**: Convert text to high-quality audio using a free or open-source AI voice model.
- **Speed Customization**: Allow the user to specify the reading speed of the generated audio.
- **Media Encoding & Chapters**: Combine the generated audio into a final media file with embedded chapter metadata to allow skipping between chapters.

## 3. Technology Stack & Research Findings (As of Feb 2026)

### 3.1 AI TTS Options (Online / Cloud)
Since local hosting is not preferred, we need a cloud-based API that is free or has a very generous free tier to handle full audiobooks.
- **Edge TTS (Python Library)**: This is currently the gold standard for free, high-quality online TTS. It acts as an unofficial wrapper around Microsoft Edge's "Read Aloud" feature (which uses Azure's premium AI voices). It requires an internet connection but has no API key requirement, no strictly enforced character limits, and offers very natural-sounding voices.
- **Google Cloud TTS (Free Tier)**: Google offers a free tier (up to 1 million or 4 million characters per month depending on the voice type). Good quality, but requires setting up a Google Cloud account and API key.
- **gTTS (Google Translate TTS)**: Completely free online API, but the voices sound very robotic and lack emotion compared to modern AI.

*Recommendation: **Edge-TTS** is the best solution for generating long audiobooks online without paying for API usage or setting up complex cloud accounts. It provides premium Microsoft Azure voices entirely for free.*

### 3.2 Media Format & Chapter Navigation
**Requirement**: The final file must be an `.mp4` video containing the audio and support skipping to specific chapters. The video track can simply display the book's cover art or a static image.
- **FFmpeg Integration**: 
  - We will use FFmpeg to encode the final `.mp4` file, combining the generated TTS audio with a static image for the video track.
  - Chapters are created by providing a metadata text file in the `FFMETADATA1` format (containing `[CHAPTER]` blocks with millisecond start/end times). FFmpeg parses this text file and embeds it into the `.mp4` container.

## 4. Finalized Technical Scope & Constraints
Based on project requirements, the final product will have the following constraints and architecture:

- **Format:** The output will exclusively be a standard `.mp4` video file. The video will be dynamic: it will display the specific chapter title (or an image representing the chapter) that changes as the audio progresses from chapter to chapter. It will be a standalone file that easily works across platforms (Google Drive, iPhone, etc.) and is easily shareable.
- **Chapter Navigation:** The `.mp4` file will include embedded chapter metadata. This means when you open it in Google Drive, QuickTime, VLC, or iPhone, you can click a "Chapters" menu and instantly skip to any part of the book.
- **Tracking Limitations:** Because the final deliverable is just a standalone `.mp4` video (not an application), it is not technically possible to embed custom start/stop logging or a daily listening tracker inside the file itself. However, modern video players built into phones and browsers generally remember your last paused position natively.
- **TTS Engine:** We will use **Edge-TTS**, a free Python wrapper for Microsoft Azure's cloud voices, avoiding the need for heavy local AI generation or complex API key setups.
- **Application Interface:** A simple Python Command-Line Interface (CLI). You will run a command like `python build_audiobook.py book.epub --speed 1.2` and it will generate the standalone `mp4`.
- **Primary Technologies:** Python, Edge-TTS (for cloud audio generation), and `ffmpeg` (for encoding the final `.mp4` and burning the chapter metadata).
