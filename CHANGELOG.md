# Changelog

## 2.1.0 — 2026-08-09

### Added

- A unified `--engine` interface for Edge-TTS, Kokoro, MLX Kokoro, Hugging Face, and arbitrary local TTS commands.
- Hugging Face Hub and local-directory support for models accepted by the Transformers text-to-speech pipeline, including namespaced model options, speaker embeddings, revision pinning, offline loading, device selection, and explicit remote-code opt-in.
- A shell-free command adapter for other local model runtimes that write WAV, MP3, FLAC, or Ogg audio.
- Model documentation, packaging metadata, an MIT license, and source-distribution manifests.

### Changed

- Local waveform engines now share bounded sentence-aware chunking, disk-streamed audio assembly, portable speed conversion, transcript fallback, validation, and atomic MP3 commits.
- Heavy Kokoro, Hugging Face, and MLX dependencies are installable extras instead of default dependencies.
- Resume identities now include model configuration, loading policy, local artifact signatures, speaker embeddings, command dependencies, voice, and speed.
- Older Kokoro flags remain compatibility aliases while the implementation uses the unified backend selection path.

### Hardened

- Local commands run without a shell, use bounded diagnostic logs, enforce timeouts, and terminate descendant processes on POSIX.
- Hugging Face custom repository code is disabled by default, offline state is restored after loading, and model outputs are validated before encoding.
- Runtime preflight checks report missing FFmpeg, optional packages, and local executables before EPUB parsing or model downloads begin.

### Verified

- 196 default tests and all 11 live integration/model tests passed.
- Live coverage included Edge-TTS, Kokoro CPU, hybrid MPS, MLX Kokoro, and `facebook/mms-tts-eng` from the Hub, offline cache, and a local model directory.
- Acoustic timing gates passed for Edge and Kokoro with zero chapter-marker shift.
- Black, Ruff security lint, mypy, wheel/sdist builds, a clean base-wheel installation, and an installed-wheel EPUB-to-MP3 command-backend conversion passed.
