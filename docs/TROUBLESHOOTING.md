# Troubleshooting

## Resume Not Working

The progress tracker uses **SHA256 checksums** of chapter text. If the EPUB content changed (e.g., re-downloaded with different formatting), chapters will be regenerated.

To force a fresh build, omit `--resume-dir` or delete the progress directory.

## FFmpeg Not Found

Ensure `ffmpeg` and `ffprobe` are in your PATH:

```bash
which ffmpeg
which ffprobe
```

macOS: `brew install ffmpeg`

## Kokoro OOM / Slow

Kokoro is CPU/GPU intensive. Reduce `--max-workers` (default: 4) or use `--concurrency sequential`.

```bash
python -m epub_listener book.epub --use-kokoro --max-workers 1 --concurrency sequential
```

## Edge-TTS Connection Errors

Edge-TTS requires an internet connection. If you hit rate limits, the provider automatically retries with backoff. Reduce concurrency with `--max-workers` if needed.

## Output MP3 Has No Chapters

Ensure your player supports ID3v2.3 chapter metadata. VLC, QuickTime, and most modern players do. Some older players may not.
