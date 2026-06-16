# Troubleshooting

## Resuming an Interrupted Build

When a fresh build is interrupted or fails, the program prints the temp directory path:

```
Resume with: --resume-dir /tmp/epub_audiobook_abc123
```

Pass that path on the next run to skip already-completed chapters:

```bash
python -m epub_listener my_book.epub --resume-dir /tmp/epub_audiobook_abc123
```

The progress tracker uses **SHA-256 checksums** of chapter text. If the EPUB content changed between runs (e.g., re-downloaded with different formatting), affected chapters are regenerated automatically.

**Note**: On a successful fresh build, the auto-created temp dir is deleted. Interrupted/failed fresh builds preserve it, and user-supplied resume dirs are never deleted by the program.

## FFmpeg Not Found

Ensure `ffmpeg` and `ffprobe` are in your PATH:

```bash
which ffmpeg
which ffprobe
```

macOS: `brew install ffmpeg`

## Kokoro OOM / Slow

Kokoro is CPU/GPU intensive. It uses process-local model caches in parallel mode. Reduce `--max-workers` (default: 4) or use `--concurrency sequential`.

```bash
python -m epub_listener book.epub --use-kokoro --max-workers 1 --concurrency sequential
```

## Edge-TTS Connection Errors

Edge-TTS requires an internet connection. If you hit rate limits or transient service errors, reduce async concurrency with `--max-workers`.

## Output MP3 Has No Chapters

Ensure your player supports ID3v2.3 chapter metadata. VLC, QuickTime, and most modern players do. Some older players may not.
