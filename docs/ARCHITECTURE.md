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
│   │ (ChapterParser,             │   │
│   │  TTSBatchGenerator)         │   │
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

1. **Resolve workspace**: `__main__` creates one `BuildWorkspace` (`--resume-dir` if provided, otherwise a fresh `mkdtemp`). The workspace creates both `BuildAudiobookCommand` and `JsonProgressTracker`, so cached audio and tracker state are always co-located.
2. **Parse**: `EbookLibParser` reads EPUB → list of `Chapter` domain objects. Duplicate titles are disambiguated and exact duplicate chapter text is dropped.
3. **Track**: `JsonProgressTracker` checks which chapters are already complete via stored SHA-256 checksums and cached durations. Audio file existence is validated by the orchestrator.
4. **Generate**: `TTSBatchGenerator` owns batch execution and calls a backend job boundary to write `.mp3` segments.
   - Edge-TTS uses the shared async batch runner configured by `--max-workers`.
   - Kokoro uses the shared future batch runner with a provider-owned process-safe job API, a process pool, and process-local pipeline caching. The optional hybrid mode binds one worker to Apple MPS and one to CPU.
   - MLX Kokoro uses the same single-job provider port through the sequential batch adapter, keeping Apple-specific model loading out of the application layer.
   - The batch runner calls back after each chapter completes so resume state is persisted during long concurrent batches. A failed chapter fails the build instead of silently producing a partial audiobook.
5. **Metadata**: `FFmpegMetadataBuilder` writes `FFMETADATA1` with millisecond chapter boundaries. All title/author/chapter values are escaped per the FFMETADATA1 spec (`\=`, `\;`, `\#`, `\\`).
6. **Assemble**: `FFmpegMediaAssembler` concatenates segments (single-quote-escaped paths for the concat demuxer), re-encodes once to remove accumulated per-chapter MP3 padding, and embeds metadata into the final MP3. The timeout scales with total audiobook duration.
7. **Cleanup**: On success, an auto-created temp dir is removed. On failure or interrupt, it is preserved and the path is printed so the user can resume with `--resume-dir`.

## Design Principles

- **Dependency Inversion**: `BuildAudiobookUseCase` depends only on `ports.py` protocols and an application command, never concrete classes or CLI/Pydantic settings.
- **Caller-owned lifecycle**: The composition root resolves one `BuildWorkspace`, which creates both the tracker and `BuildAudiobookCommand`. The use case never creates or destroys directories.
- **Single Responsibility**: Each module has one reason to change. TTS engines are split. FFmpeg logic is centralized in `ffmpeg_runner.py`.
- **Open/Closed**: New TTS providers or parsers can be added without modifying the orchestrator. TTS engines own file generation; batch behavior lives in `TTSBatchGenerator` adapters and shared runner helpers.
- **DRY**: File sanitization, ffmpeg subprocess calls, and speed normalization live in single utility modules.

## Temp Directory Lifecycle

| Scenario | `temp_dir` source | Cleanup |
|----------|-------------------|---------|
| Fresh build (success) | `mkdtemp(prefix="epub_audiobook_")` | Removed by `__main__` on success |
| Fresh build (interrupted/failed) | `mkdtemp(...)` | **Preserved** — path printed; use `--resume-dir` to retry |
| Resume build | `settings.resume_dir` (user-supplied) | Written during the build, never deleted by the program |

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
