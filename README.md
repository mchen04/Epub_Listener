# Epub Listener

Convert a standard `.epub` into a narrated `.mp3` audiobook with skippable chapters and an optional read-along transcript.

## Why use it?

- **Bring your own narrator**: Edge-TTS, Kokoro, any text-to-speech model supported by the Hugging Face Transformers pipeline, a downloaded model directory, or any local executable that writes audio.
- **One command**: sensible defaults work with Edge-TTS and every backend uses the same output, speed, resume, metadata, and transcript pipeline.
- **Safe resume**: checksums include the engine, model options, voice, and speed, so changing models cannot splice incompatible cached chapters together.
- **Long-book support**: local model input is sentence-aware and bounded, audio is streamed to disk, and models are loaded once per build.
- **Player-friendly output**: one normalized MP3 with ID3 chapter markers for VLC, QuickTime, and other modern players.
- **Read-along data**: word timings where an engine exposes them, sentence estimates otherwise, embedded as an ID3 GEOB frame and written as JSON beside the MP3.

## Requirements

- Python 3.10+
- FFmpeg (`brew install ffmpeg` on macOS)
- `espeak-ng` only for Kokoro (`brew install espeak-ng` on macOS)

## Install

```bash
git clone https://github.com/mchen04/epub-listener.git
cd epub-listener
python3 -m venv .venv
source .venv/bin/activate

# Small default install: EPUB conversion, Edge-TTS, and local commands
pip install -e .

# Add the engines you plan to use
pip install -e '.[kokoro]'
pip install -e '.[huggingface]'
pip install -e '.[mlx]'
```

`pip install -r requirements.txt` remains a compatibility shortcut for the default plus Kokoro. For development, use `pip install -r requirements-dev.txt`.

## Quick start

```bash
# Zero-config cloud narration (no API key)
epub-listener book.epub

# Explicit output, voice, and speed
epub-listener book.epub book.mp3 --voice en-GB-RyanNeural --speed +15%

# Local Kokoro
epub-listener book.epub --engine kokoro --voice af_heart

# Apple MLX Kokoro
epub-listener book.epub --engine kokoro-mlx --voice af_heart --concurrency sequential

# A Hugging Face Hub model
epub-listener book.epub --engine huggingface --model facebook/mms-tts-eng

# The same interface accepts a downloaded model directory and stays offline
epub-listener book.epub --engine huggingface --model /models/my-tts --local-files-only
```

The older `--use-kokoro`, `--kokoro-voice`, and `--kokoro-mlx` forms still work, but `--engine` and `--voice` are the preferred interface.

### Hugging Face models

Epub Listener targets the Transformers `text-to-speech`/`text-to-audio` pipeline. That covers pipeline-compatible TTS architectures such as Bark, CSM, Dia, MMS, VITS, and SpeechT5; models for unrelated tasks are not speech synthesizers and are intentionally rejected by Transformers.

```bash
# Bark voice preset
epub-listener book.epub --engine huggingface --model suno/bark-small \
  --voice v2/en_speaker_6

# Reproducible model revision and explicit CPU execution
epub-listener book.epub --engine huggingface --model facebook/mms-tts-eng \
  --revision COMMIT_SHA --device cpu --dtype float32

# Model-specific pipeline arguments, inline or from @options.json
epub-listener book.epub --engine huggingface --model org/model \
  --model-options '{"generate":{"do_sample":false}}'
```

Models with architecture-specific inputs remain available without hard-coded special cases: pass `pipeline`, `preprocess`, `forward`, or `generate` objects through `--model-options`. SpeechT5 speaker embeddings can be supplied directly with `--speaker-embedding speaker.npy`. Custom repository code is disabled unless you explicitly pass `--trust-remote-code`; review and pin that repository first.

`--voice` is mapped automatically where Transformers has a clear convention: a Bark voice preset or a numeric VITS speaker ID. Other multi-speaker models differ, so their model-card option belongs in `--model-options` instead of being guessed.

See [Model backends](docs/MODELS.md) for the full option contract and examples. Hugging Face also maintains the current [Transformers text-to-speech model guide](https://huggingface.co/docs/transformers/tasks/text-to-speech).

### Any other local model

The command backend is language- and framework-neutral. It runs without a shell, sends each text chunk on stdin, and requires the executable to write an audio file at `{output}`.

```bash
# Piper writes WAV and reads text from stdin
epub-listener book.epub --engine command \
  --model-command 'piper --model en_US-lessac-medium.onnx --output_file {output}'

# A wrapper may read the same text from {text_file}; voice is optional
epub-listener book.epub --engine command \
  --model-command 'my-local-tts --text-file {text_file} --voice {voice} --out {output}' \
  --voice narrator --command-output-format flac
```

WAV, MP3, FLAC, and Ogg engine output is normalized to the final MP3. Command text is never interpolated into an argument, and `shell=True` is never used.

## Common controls

```bash
# Disable transcript embedding
epub-listener book.epub --no-transcript

# Override metadata
epub-listener book.epub --title "My Book" --author "Author Name"

# Resume an interrupted build using the printed directory
epub-listener book.epub --resume-dir /tmp/epub_audiobook_xxx

# Tune local-model chunking; 0 lets the model/wrapper handle all text
epub-listener book.epub --engine huggingface --model org/model \
  --chunk-chars 800 --chunk-pause-ms 60
```

Playback speed accepts `-90%` through `+1500%`; local waveforms use a portable ffmpeg filter chain and cloud/model-native engines receive the equivalent multiplier.

For long unattended builds:

```bash
screen -S builder -d -m bash -c "source .venv/bin/activate && epub-listener book.epub > build.log 2>&1"
tail -f build.log
```

## Web scrapers

`epub_listener/scrapers/` includes a Worm (Wildbow) scraper that produces an EPUB for the normal pipeline:

```bash
python -m epub_listener.scrapers.worm --output worm.epub
```

## Documentation

- [Changelog](CHANGELOG.md)
- [Model backends](docs/MODELS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Development](docs/DEVELOPMENT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Transcript format](docs/transcript-format.md)
