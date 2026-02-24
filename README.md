# Epub Listener

Epub Listener is a Python-based audiobook generator that takes any standard `.epub` file and transforms it into a fully narrated `.mp4` audiobook complete with skip-able chapters and a dynamic video track displaying the current chapter. 

It leverages **[Edge-TTS](https://github.com/rany2/edge-tts)**, a free Python wrapper for Microsoft Azure's premium cognitive AI voices, to provide incredibly realistic narration without the need to manage API keys or pay for expensive cloud subscriptions.

## Features
- **High-Quality AI Narration**: Uses Microsoft Azure's premium AI voices.
- **Dynamic Video Chapters**: Generates an `.mp4` file that natively supports "Skip to Chapter" functionality on iPhone, VLC, QuickTime, and Google Drive.
- **Customizable Playback Speed**: Accelerate or decelerate the narration generation speed directly from the CLI.
- **Visual Progress**: Automatically generates a beautiful, clean video frame displaying the title of the current chapter as you listen.

## Prerequisites
Before you begin, ensure you have the following installed on your machine:
- **Python 3.8+**
- **FFmpeg**: The script heavily relies on FFmpeg to stitch the video files and burn the chapter metadata. 
  - On macOS, you can install it via Homebrew: `brew install ffmpeg`

## Installation

1. Clone this repository to your local machine:
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

Simply invoke the CLI script with the path to your input EPUB file. By default, it will automatically build a `.mp3` file and save it into an `outputs/` folder.

```bash
# Basic usage (defaults to building an MP3 in the outputs/ directory)
python src/build_audiobook.py my_book.epub
```

### Advanced Options

- **Output Path**: You can explicitly specify the exact destination file and format you want.
  ```bash
  python src/build_audiobook.py my_book.epub /tmp/custom_location.mp4
  ```

- **Output Format**: If you don't provide an exact path, you can specify if you want an `mp3` or `mp4`.
  ```bash
  python src/build_audiobook.py my_book.epub --format mp4
  ```

- **Output Directory**: You can specify a different folder for auto-generated files.
  ```bash
  python src/build_audiobook.py my_book.epub --output-dir ~/Desktop/Audiobooks/
  ```

- **Speed Customization**: You can speed up or slow down the narrator by providing the `--speed` flag.
  ```bash
  python src/build_audiobook.py my_book.epub --speed +15%
  ```

- **Voice Customization**: By default, the script uses `en-US-AriaNeural`. You can change this using the `--voice` flag.
  ```bash
  python src/build_audiobook.py my_book.epub --voice en-GB-RyanNeural
  ```

### Running in the Background
Audio generation for large books (e.g., 50+ chapters) can take hours due to the sheer volume of downloaded voice data and video rendering. It is highly recommended to run long processes in an isolated session on macOS (such as `screen` or `tmux`) so your operating system does not suspend the background script.

```bash
# Launching in a background screen session
screen -S builder -d -m bash -c "source venv/bin/activate && python src/build_audiobook.py input.epub out.mp4 > build.log 2>&1"

# Monitor progress
tail -f build.log
```

## How It Works
1. **Parser Engine**: Uses `EbookLib` and `BeautifulSoup4` to crawl the EPUB's navigation structure, isolating chapter titles and scrubbing raw HTML to extract pure text.
2. **Audio Generation**: Chunks the chapter text and feeds it asynchronously to the `edge-tts` API, pulling down premium `.mp3` audio.
3. **Visual Frame Generation**: Uses `Pillow` to dynamically create a text-centered image containing the chapter marker.
4. **Assembly**: Uses `ffmpeg` to pair the audio and imagery, compute total millisecond duration, format an `FFMETADATA1` file, and seamlessly concatenate everything into a standalone `.mp4` video.
