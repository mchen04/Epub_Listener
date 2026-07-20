# Epub Listener Transcript Format (v1)

A generated audiobook MP3 may carry a timestamped transcript of its own
narration. The transcript is produced at TTS generation time (where word
timings are exact and free), embedded inside the MP3, and consumed by
listening apps (e.g. Hark's read-along view). This document is the contract:
a consumer should be able to implement a parser from this file alone.

## Embedding

- **Frame**: one ID3v2.3 `GEOB` (General Encapsulated Object) frame on the
  final MP3, alongside the existing chapter (`CHAP`), artwork (`APIC`) and
  text frames, which are not disturbed.
  - `mimeType`: `application/gzip`
  - `filename`: `transcript.json.gz`
  - `description`: `EPUB_LISTENER_TRANSCRIPT` (consumers identify the frame
    by this description; ignore other GEOB frames)
  - object data: the transcript JSON document, UTF-8, gzip-compressed.
- **Sidecar**: the same JSON (uncompressed) is written next to the output as
  `<output-stem>.transcript.json` for tooling and debugging. The embedded
  frame is authoritative for consumers.
- A file without the frame simply has no transcript. Consumers must treat a
  missing, unparseable, schema-invalid, or oversized transcript identically:
  ignore it and keep the audio fully usable.

## Top-level document

```json
{
  "format": "epub-listener-transcript",
  "version": 1,
  "producer": "epub_listener/1.0",
  "engine": "kokoro",
  "generationKey": "tts_backend=kokoro\nvoice=af_heart\nspeed=+0%",
  "language": "en",
  "chapters": [ ... ]
}
```

| field | type | rules |
| --- | --- | --- |
| `format` | string | exactly `"epub-listener-transcript"` |
| `version` | integer | exactly `1`; consumers must reject other values |
| `producer` | string | producer name/version, informational |
| `engine` | string | TTS engine identifier (`kokoro`, `edge`, `kokoro-mlx`, ...), informational |
| `generationKey` | string | opaque config fingerprint (backend, voice, speed), informational |
| `language` | string | BCP-47-ish language tag; v1 is only verified for `en` |
| `chapters` | array | chapter transcript objects, see below |

## Chapters

```json
{
  "index": 0,
  "title": "Chapter 1",
  "granularity": "word",
  "sentences": [ ... ]
}
```

| field | type | rules |
| --- | --- | --- |
| `index` | integer | ≥ 0, strictly increasing across the array. **`index` i refers to the i-th chapter marker of the MP3's own embedded chapter list** (FFMETADATA/`CHAP` order). The array may be sparse (a chapter without a transcript is simply absent); consumers must tolerate gaps. |
| `title` | string | chapter title, informational (the MP3 chapter list is authoritative) |
| `granularity` | string | `"word"` (sentence records populated with word cues) or `"sentence"` (word arrays are empty; consumers degrade to sentence highlighting) |
| `sentences` | array | see below; may be empty only for an intentionally empty chapter |

**All timestamps are integer milliseconds RELATIVE TO CHAPTER START** — i.e.
relative to that chapter's start time in the MP3's embedded chapter list. The
final assembly re-encode can shift absolute positions slightly; chapter
markers are re-derived from the same segment durations, so chapter-relative
times stay anchored.

## Sentences

```json
{
  "text": "It was chapter 12, and Dr. Hale said \"hello\".",
  "start": 4210,
  "end": 7893,
  "words": [
    { "text": "It", "start": 4210, "end": 4331, "charStart": 0, "charEnd": 2 },
    { "text": "twelve", "start": 5100, "end": 5410, "charStart": 15, "charEnd": 17 }
  ]
}
```

Sentence rules:

- `text`: the sentence as it should be displayed (taken from the source book
  text, punctuation preserved). Non-empty after trimming.
- `start`/`end`: integer ms, `0 ≤ start ≤ end`.
- Sentences are ordered by non-decreasing `start`. Small overlaps between
  adjacent sentences are permitted (encoder timing jitter); consumers should
  select by binary search over `start`.
- `words`: word cue array. Always empty in a `"sentence"`-granularity chapter.
  In a `"word"`-granularity chapter it is populated, except that an
  un-narrated span (e.g. a sentence of stray punctuation) may have an empty
  array; at least one sentence in the chapter carries word cues. Consumers must
  tolerate an occasional empty `words` array in a word-granularity chapter.

Word cue rules:

- `text`: the spoken word as reported by the TTS engine. May differ from the
  displayed slice of `text` when the engine normalizes (e.g. source `12`
  spoken as `twelve`); may not be empty.
- `start`/`end`: integer ms, `0 ≤ start ≤ end`, non-decreasing `start` across
  the array. Word times are expected to fall inside the sentence interval but
  consumers must not assume exact containment.
- `charStart`/`charEnd`: integers with
  `0 ≤ charStart ≤ charEnd ≤ text.length`, a character range **into the
  sentence's `text`** identifying the displayed word to mark. Multiple cues
  may map to the same range (one written token spoken as several words, e.g.
  `123` → "one" "twenty" "three"). The range may be empty (`charStart ==
  charEnd`) for a cue that could not be anchored; consumers should then keep
  the previous marked word.

## Size discipline

Producers keep the document lean (integer ms, no whitespace in the embedded
form). Consumers must enforce caps before parsing and reject violators
without affecting audio import:

- compressed frame size: reject above **24 MiB**;
- decompressed size: reject above `1 MiB + 600 bytes × ceil(duration-seconds)`,
  and always above an absolute **128 MiB** ceiling. (Measured density for
  English narration is ≈ 200 bytes/s; the cap has ~3× headroom.)

## Validation summary (normative)

A consumer must reject the document (and behave as if no transcript exists)
unless all of the following hold: exact `format`/`version` match; `chapters`
strictly increasing `index` ≥ 0; every sentence has non-empty `text`,
integer `0 ≤ start ≤ end`, non-decreasing sentence `start`s within a chapter;
every word cue has non-empty `text`, integer `0 ≤ start ≤ end`,
non-decreasing word `start`s within a sentence, and
`0 ≤ charStart ≤ charEnd ≤ sentence.text.length`; word arrays empty for
`"sentence"` granularity chapters and non-empty for at least one sentence in
a `"word"` granularity chapter with any sentences.

## Shared fixtures

`tests/fixtures/transcripts/valid-word.json` and
`tests/fixtures/transcripts/valid-sentence.json` are canonical fixture
documents; byte-identical copies live in each consuming repo and both sides'
validators must accept them. Producer and consumer test suites both validate
these fixtures against their own implementation of this contract.
