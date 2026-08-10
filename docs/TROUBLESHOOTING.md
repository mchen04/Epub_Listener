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

The preferred equivalent is `--engine kokoro`. Kokoro is optional; install it with `pip install -e '.[kokoro]'`.

## Hugging Face Model Does Not Load

Install the adapter and common inference dependencies:

```bash
pip install -e '.[huggingface]'
```

Confirm the repository supports the Transformers `text-to-speech` pipeline, not merely a custom README script. Model-specific dependencies and inputs are listed on its model card. Pass architecture inputs with `--model-options @options.json`, or use `--speaker-embedding` for SpeechT5. A custom-script-only repository can be wrapped with `--engine command`.

Remote repository code is intentionally disabled. If a reviewed model requires it, pin `--revision` to a commit and add `--trust-remote-code`. Do not enable that flag for an untrusted repository.

If MPS or CUDA fails for a model, establish correctness first with:

```bash
epub-listener book.epub --engine huggingface --model org/model \
  --device cpu --dtype float32
```

For token-length errors, lower `--chunk-chars`; for a model with its own long-text processor, use `--chunk-chars 0`.

## Local Command Fails

The template must contain `{output}` and the executable must write the declared `--command-output-format`. Text arrives on stdin and at `{text_file}`. The adapter does not invoke a shell, so pipes, redirects, environment assignments, and shell expansion are not interpreted; put those operations in a reviewed wrapper script.

Run the executable directly with a short input, then retry with `--log-level DEBUG`. Increase `--model-timeout` for slow hardware. Failed and timed-out commands leave an existing completed chapter untouched and keep the build workspace for resume.

## Edge-TTS Connection Errors

Edge-TTS requires an internet connection. If you hit rate limits or transient service errors, reduce async concurrency with `--max-workers`.

## Output MP3 Has No Chapters

Ensure your player supports ID3v2.3 chapter metadata. VLC, QuickTime, and most modern players do. Some older players may not.
