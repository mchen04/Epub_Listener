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

1. **Parse**: `EbookLibParser` reads EPUB → list of `Chapter` domain objects.
2. **Track**: `JsonProgressTracker` checks which chapters are already complete via SHA256 checksums.
3. **Generate**: `TTSProvider` (Edge or Kokoro) generates `.mp3` segments.
   - Edge-TTS uses `asyncio.Semaphore` for controlled concurrency.
   - Kokoro uses `ProcessPoolExecutor` for CPU-bound inference.
4. **Metadata**: `FFmpegMetadataBuilder` writes `FFMETADATA1` with millisecond chapter boundaries.
5. **Assemble**: `FFmpegMediaAssembler` concatenates segments and embeds metadata into final MP3.

## Design Principles

- **Dependency Inversion**: `BuildAudiobookUseCase` depends only on `ports.py` protocols, never concrete classes.
- **Single Responsibility**: Each module has one reason to change. TTS engines are split. FFmpeg logic is centralized.
- **Open/Closed**: New TTS providers or parsers can be added without modifying the orchestrator.
- **DRY**: File sanitization, ffmpeg subprocess calls, and speed normalization live in single utility modules.
