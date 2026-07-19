# Read-Along Text Sync — Evidence Ledger

Goal: capture word timings during TTS generation, embed a transcript in the
output MP3 (Epub Listener), parse + display it as a synced read-along view
(Hark). Two repos, one feature.

## Wave 0 — Inspect and baseline (2026-07-19)

Verified facts (against live code and installed libraries):

1. **Kokoro** (`kokoro==0.9.4`): `kokoro_tts.py:138` iterates
   `for _, _, audio in generator:` discarding graphemes/phonemes.
   `KPipeline.Result` carries `tokens: Optional[List[en.MToken]]` — per-token
   `start_ts`/`end_ts` (seconds, relative to each yielded chunk's audio) for
   English pipelines. Chunk base offset = cumulative samples / 24000.
2. **Edge-TTS** (`edge-tts==7.2.7`): `edge_tts.py:70-76` uses
   `communicate.save()` which discards metadata. `Communicate.stream()` yields
   `{"type": "WordBoundary", "offset", "duration", "text"}` chunks; offsets are
   in 100-ns ticks and already **cumulative across internal text splits** via
   `offset_compensation` in `communicate.py`.
3. **MLX Kokoro**: `mlx_audio` is NOT installed in the venv on this machine
   (optional extra). Capture will use per-chunk boundaries computed from
   cumulative samples (`result.audio` per yielded segment); unit-tested with a
   fake model; accuracy gate not runnable for MLX here — recorded as
   environment limitation, honest fallback per goal §7.
4. **Assembler**: `ffmpeg_assembler.py` concat-decodes + single libmp3lame
   re-encode (`-q:a 2`, id3v2.3). Transcript timestamps stored RELATIVE TO
   CHAPTER START; FFMETADATA chapter boundaries are the anchors. Residual
   shift to be measured in Wave 2.
5. **Resume**: `JsonProgressTracker` (progress.json) + cached
   `chap_{id}.mp3` in workspace. Transcript JSON must persist per chapter
   alongside and invalidate with checksum/generation_key.
6. **Hark**: import path `src/lib/local-import.ts` → `interpretMp3Metadata`
   (`src/domain/mp3.ts`, music-metadata parseBlob) → POST JSON metadata only to
   `/api/books/local` → `storeLocalBookMedia` (chunked CacheStorage + idb
   `downloads`/`cacheEntries` stores, db `chapterline-offline-v1` v5).
   Player hero: `full-player.tsx:122-144`; topbar Details `:106-113`; time via
   `playback-time-store.ts` external store. PRIVACY: transcript must never be
   in any server request.
7. **music-metadata GEOB support**: `lib/id3v2/FrameParser.js:261` parses GEOB
   into `{type, filename, description, data: Uint8Array}` exposed via native
   tags → Hark can read the frame with its existing parser. Browser gunzip via
   `DecompressionStream("gzip")`.

Baselines:
- Epub_Listener: `venv/bin/python -m pytest -q` → **129 passed, 8 deselected** (green).
- Hark: `pnpm verify` (format:check, lint, typecheck, vitest, build) → **exit 0** (green).

Decisions:
- Embedded form: one **GEOB** frame (music-metadata parses it; SYLT loses
  sentence structure and would fight ffmpeg re-muxing). Mime
  `application/gzip`, filename `transcript.json.gz`, description
  `EPUB_LISTENER_TRANSCRIPT`, payload = gzipped schema-v1 JSON. Added
  `mutagen` dependency for post-assembly tag surgery (preserves existing
  chapters/artwork frames written by ffmpeg).
- Word cues carry `charStart`/`charEnd` into the sentence text so the UI can
  mark the spoken word inside the sentence even when TTS normalization
  expands tokens (e.g. "123" → three spoken words map to one char range).
- Granularity per chapter: `"word"` or `"sentence"` (chunk fallback marked as
  sentence-granularity records).
- User authorized (mid-session): at the end, commit, push, squash-merge both
  repos.

Repo state at start: Epub_Listener main @ 296bf05 (untracked novelight
scraper files preserved, unrelated); audiobook_pwa main @ c357768 (clean).
Branches: `feat/read-along-transcript` (Epub_Listener), `feat/read-along`
(Hark).
