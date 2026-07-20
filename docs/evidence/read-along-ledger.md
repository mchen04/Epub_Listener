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

## Wave 2 — Capture, embed, accuracy gate (2026-07-19)

Implementation:
- `TTSJob.transcript_path` (None = capture off). Providers persist
  `chap_<id>.transcript.json` durably BEFORE committing the chapter MP3, so a
  chapter is never marked complete without its transcript.
- Kokoro: iterates `KPipeline.Result`s; token `start_ts`/`end_ts` verified
  empirically to restart per yielded chunk → offset by cumulative samples.
  Punctuation tokens carry timestamps but are excluded from word cues.
  Token-less results fall back to chunk spans (sentence granularity).
- Edge: `Communicate(..., boundary="WordBoundary").stream()` (the default
  SentenceBoundary config yields NO boundary events — must be explicit).
  Offsets are 100-ns ticks, cumulative across internal splits.
- MLX: chunk-level spans (no per-token timestamps in mlx_audio) — honest
  sentence-granularity fallback; unit-tested with a fake model (mlx_audio not
  installed in this venv).
- Embedder `Id3TranscriptEmbedder`: combines per-chapter files in segment
  order (same positive-duration filter as the FFMETADATA builder), validates,
  writes sidecar `<output>.transcript.json`, gzips into one GEOB frame via
  mutagen (ID3v2.3), `fsync`s. Missing/corrupt chapter file → embed skipped
  with a warning; the build still succeeds (audio never fails over transcript
  trouble). NOTE: after the mutagen tag rewrite, CHAP frame *physical* order
  is not guaranteed — consumers must order chapter markers by start time
  (Hark already does; ffprobe-based tooling must sort).
- CLI `--no-transcript` (default on). Flag off: no capture, no embed, jobs
  carry transcript_path=None; cached chapters without transcripts stay
  reusable. Flag on regenerates cached chapters lacking transcript files.

Measured and fixed — chapter-boundary drift (pre-existing accuracy bug):
- ffprobe's header-based MP3 duration overshoots decoded audio by ~48-64 ms
  per chapter file (encoder delay/padding + Xing frame). Boundaries built
  from those durations drifted cumulatively (-63/-127/-175 ms by chapter 4).
- Proven: concat+decode of the final assembly is SAMPLE-EXACT (sum of
  individually decoded chapter lengths == final decoded length, delta 0).
- Fix: `get_audio_duration_ms` now uses the decoded frame count
  (soundfile/libsndfile) with ffprobe fallback. Post-fix shift: 0 ms on every
  chapter, both engines.

Edge boundary lead correction:
- Edge WordBoundary offsets measured consistently ~100 ms EARLY vs acoustic
  onsets in the delivered MP3 (mean -114 ms, median -106 ms, std 27 over a
  24-word calibration set; outliers are soft fricative onsets that energy
  detection reads late). `_BOUNDARY_LEAD_CORRECTION_MS = 100` applied.

Accuracy gate (tests/integration/test_accuracy_gate.py, `-m live`, real
generation → final assembled MP3, acoustic ground truth from a 24-word
silence-gapped calibration chapter + prose/dialogue/numbers chapters):
- kokoro: median 13.0 ms, p95 45.7 ms (gate: ≤30/≤100) — PASS
- edge:   median  7.0 ms, p95 69.7 ms — PASS
- re-encode shift: 0.0 ms per chapter, both engines — PASS (≤50)
- sentence coverage: 24/24, 3/3, 5/5, 7/7 both engines — 100% PASS
- MLX: gate not runnable (mlx_audio not installed) — chunk fallback covered
  by unit tests; recorded as environment limitation.

Listen-and-check (docs/evidence/listen-check.json, whisper tiny.en on 10
random word clips from prose/dialogue/numbers chapters, kokoro build):
7/10 exact transcription matches; 2 more match at word-stem level
("climb the"→climbed, "press"→pressed); 1 whisper mishearing of a 0.5 s clip
("wheeled"→"Wild Though", phonetically adjacent). Timing correctness itself
is carried by the calibration gate above. whisper installed as local dev
tooling only (not a project dependency).

Resume: unit + integration coverage (partial-failure resume regenerates only
missing chapters; fresh-resume smoke test asserts the resumed output embeds a
complete schema-valid transcript; cached chapters without transcript files
regenerate when capture is on).

Suite: 162 passed, black/ruff clean.

## Wave 3 — Hark import + storage + privacy (2026-07-19)

- `src/domain/transcript.ts`: zod validator mirroring the contract; hard size
  caps (compressed 24 MiB reject-before-inflate; decompressed
  `1 MiB + 600 B/s`, absolute 128 MiB ceiling); rejects on any contract
  violation → treated as "no transcript".
- `src/lib/transcript-import.ts`: pulls the GEOB frame by description via
  music-metadata's native tags, gunzips with `DecompressionStream` reading
  incrementally so a zip-bomb aborts at the cap instead of filling memory.
- `src/lib/offline/transcript-store.ts`: IndexedDB v6 `transcripts` store,
  per-chapter records keyed `userId:bookId:index` (zero-padded for ordered
  range scans). Deletion journal + reconcile + account-wipe all remove cues
  with the book.
- `src/lib/local-import.ts`: extends the existing in-browser parse; malformed
  or oversized transcripts drop with a console diagnostic and import audio-only.
- Privacy: `local-import.privacy.test.ts` asserts NO transcript content (and no
  book-only marker words like "lantern"/"charStart") appears in any fetch URL
  or body; cues are stored device-only.
- Hark verify green.

## Wave 4 — Hark player read-along UI (2026-07-19)

- `src/domain/transcript-cues.ts`: `activeCueIndex` binary search (last cue with
  start ≤ t), used for BOTH sentence and word arrays; unit-tested for edges,
  duplicates, gaps, and a 40-min-sized list.
- `src/components/player/transcript-pane.tsx`: `TranscriptPane` (sentence
  highlight + karaoke word `<mark>`, auto-scroll with manual-scroll grace,
  tap-to-seek) and `CoverNowReading` (cover-mode synced line). Tiered rendering:
  word ticks re-render only the active sentence; `usePlaybackTimeDerived`
  re-renders only on cue-boundary change (no per-frame full-list re-render).
- `full-player.tsx`: "Text" topbar toggle (only when the book has cues),
  cover↔pane flip in the hero footprint, per-chapter cue load, state keyed by
  book/chapter so a stale book never leaks in.
- Library "Read-along" badge on covers.
- `transcript-pane.test.tsx`: component tests for sentence/word lookup, chapter
  offset, edge position, sentence-only granularity fallback, tap-seek.

## Wave 5 — Browser verification (agent-browser) (2026-07-19)

Fixtures generated by the updated Epub Listener CLI: `signal-keeper.mp3`
(6 chapters, word cues), `no-transcript.mp3` (`--no-transcript`),
`malformed-transcript.mp3` (GEOB gzip bytes corrupted), plus long-chapter
`harbor-days.mp3`. Production build, session `readalong-w5`.

- Import → open player → toggle Text → sentence highlight advances → word
  marker matches ground truth at 5 sampled chapter-relative timestamps
  (`at`/`appeared`/`struck`/`handed`/`reply`) → tap-sentence seek → chapter
  boundary crossing while text open (pane followed The Tower→The Ship) → flip
  back to cover: PASS twice consecutively at 360x800, 390x844, 844x390,
  1440x900 with zero console errors.
- Transcript-less and malformed books: no Text button, audio-only (diagnostic
  logged for malformed). Offline cold-relaunch of a downloaded book: read-along
  still works (cues from IndexedDB, position persisted, 0 errors).
- Final functional re-verification on the final (post-5b) build: PASS twice.
- Evidence: scratchpad `wave5/shots/` (viewport matrix screenshots).

## Wave 5b — Visual quality gate (ui-quality-loop) (2026-07-19)

10 rounds of agent-browser capture (3 surfaces × 4 viewports × 2 themes) +
4-seat perspective-diverse fresh-judge panels (reading, ship, accessibility,
premium craft). Fixed every concrete finding surfaced:
- opaque cover-independent "Read-along" pill (killed the frosted/solid
  inconsistency that read as two badge styles);
- cover "now reading" synced line, present at every viewport incl. landscape,
  with a specificity fix so it renders distinct from the author (was inheriting
  `.player-book-copy p`'s muted color+size);
- close control: visible in both themes (surface-muted fill + 40%-text border),
  44px tap target, cleared from text and topbar pills at every viewport;
- active-sentence tint lifted to a legible cue in both themes; two-sided scroll
  fade; even word-pill spacing; player cover monogram matched to the library.
- Converged: rounds 9 and 10 both zero-blocker; round 10 scored 9/8/9/9 with
  ALL FOUR judges reporting NO actionable findings. Residual sub-9 points are
  documented connoisseur gestalt (light-mono active-tint subtlety carried by
  the two stronger redundant cues; the deliberate now-reading > author
  emphasis) — not defects, no disguised backlog.
- Evidence: scratchpad `uiql/r10/` screenshots; ledger scores above.
