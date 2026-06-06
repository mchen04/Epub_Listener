# Architecture

## Overview

Epub Listener follows a **Layered / Ports & Adapters** architecture to keep business logic decoupled from external dependencies.

```
┌─────────────────────────────────────┐
│           CLI / __main__.py          │  <- Composition Root (wires adapters)
├─────────────────────────────────────┤
│         Application Layer            │
│   ┌─────────────────────────────┐   │
│   │   BuildAudiobookUseCase     │   │  <- Orchestration only
│   │         (orchestrator.py)   │   │
│   └─────────────────────────────┘   │
│   ┌─────────────────────────────┐   │
│   │         Ports               │   │  <- Abstract interfaces
│   │  (ChapterParser, TTSProvider)│   │
│   └─────────────────────────────┘   │
├─────────────────────────────────────┤
│          Domain Layer                │
│   ┌─────────────────────────────┐   │
│   │   Chapter, AudioSegment     │   │  <- Pure dataclasses
│   │   AudiobookProject          │   │
│   └─────────────────────────────┘   │
│   ┌─────────────────────────────┐   │
│   │   Exception Hierarchy       │   │
│   └─────────────────────────────┘   │
├─────────────────────────────────────┤
│        Infrastructure Layer          │
│   ┌─────────┐ ┌─────────┐ ┌──────┐ │
│   │ EbookLib│ │ Edge-TTS│ │ffmpeg│ │  <- Concrete adapters
│   │ Parser  │ │ Kokoro  │ │ asm  │ │
│   └─────────┘ └─────────┘ └──────┘ │
└─────────────────────────────────────┘
```

## Data Flow

1. **Resolve temp dir**: `__main__` determines the working directory (`--resume-dir` if provided, otherwise a fresh `mkdtemp`). Both `JsonProgressTracker` and `BuildAudiobookUseCase` receive this same directory so cached audio and tracker state are always co-located.
2. **Parse**: `EbookLibParser` reads EPUB → list of `Chapter` domain objects. Chapters are deduplicated by title and by SHA-256 text checksum.
3. **Track**: `JsonProgressTracker` checks which chapters are already complete via stored SHA-256 checksums. The tracker owns only checksum state; audio file existence is validated by the orchestrator.
4. **Generate**: `TTSProvider` (Edge or Kokoro) generates `.mp3` segments.
   - Edge-TTS uses `asyncio.Semaphore` for controlled concurrent requests; the semaphore is loop-agnostic (Python 3.10+).
   - Kokoro uses `ProcessPoolExecutor` for CPU-bound inference.
5. **Metadata**: `FFmpegMetadataBuilder` writes `FFMETADATA1` with millisecond chapter boundaries. All title/author/chapter values are escaped per the FFMETADATA1 spec (`\=`, `\;`, `\#`, `\\`).
6. **Assemble**: `FFmpegMediaAssembler` concatenates segments (single-quote-escaped paths for the concat demuxer) and embeds metadata into the final MP3.
7. **Cleanup**: On success, an auto-created temp dir is removed. On failure or interrupt, it is preserved and the path is printed so the user can resume with `--resume-dir`.

## Design Principles

- **Dependency Inversion**: `BuildAudiobookUseCase` depends only on `ports.py` protocols, never concrete classes.
- **Caller-owned lifecycle**: The composition root resolves the temp directory and passes it to both the tracker and `execute(settings, *, temp_dir)`. The use case never creates or destroys directories.
- **Single Responsibility**: Each module has one reason to change. TTS engines are split. FFmpeg logic is centralized in `ffmpeg_runner.py`.
- **Open/Closed**: New TTS providers or parsers can be added without modifying the orchestrator.
- **DRY**: File sanitization, ffmpeg subprocess calls, and speed normalization live in single utility modules.

## Temp Directory Lifecycle

| Scenario | `temp_dir` source | Cleanup |
|----------|-------------------|---------|
| Fresh build (success) | `mkdtemp(prefix="epub_audiobook_")` | Removed by `__main__` on success |
| Fresh build (interrupted/failed) | `mkdtemp(...)` | **Preserved** — path printed; use `--resume-dir` to retry |
| Resume build | `settings.resume_dir` (user-supplied) | Never touched by the program |

## Error Hierarchy

```
EpubListenerError
├── ConfigurationError
├── ParseError
├── TTSGenerationError
├── AssemblyError
├── AudioProbeError
└── ResumeError
```

All domain exceptions inherit from `EpubListenerError`, which `main()` catches for clean user-facing output.
