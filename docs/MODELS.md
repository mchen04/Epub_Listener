# Model backends

## Engine matrix

| Engine | Model source | Default concurrency | Timing granularity | Install |
|---|---|---:|---|---|
| `edge` | Microsoft Edge service | async | word | base package |
| `kokoro` | `hexgrad/Kokoro-82M` through Kokoro | process pool | word | `.[kokoro]` |
| `kokoro-mlx` | FastKokoro or mlx-audio | sequential | word or sentence | `.[mlx]` |
| `huggingface` | Hub ID or local Transformers directory | sequential | sentence estimate | `.[huggingface]` |
| `command` | any local executable/model runtime | sequential | sentence estimate | base package |

The Hugging Face adapter supports models accepted by the installed Transformers `text-to-speech` pipeline. “All Hugging Face models” cannot literally include classifiers, language models, image generators, or TTS repositories that do not implement the Transformers pipeline contract; those are not interchangeable speech models. The `command` adapter covers other TTS runtimes without requiring project changes.

## Hugging Face

Minimum invocation:

```bash
epub-listener book.epub --engine huggingface --model facebook/mms-tts-eng
```

`--model` may be a Hub repository or local directory. `--revision` accepts a branch, tag, or commit. Pin a reviewed commit for reproducible builds. Authentication uses the normal Hugging Face configuration, including `HF_TOKEN`.

Device selection is `auto` by default: CUDA, then Apple MPS, then CPU. Override it with `--device`. Precision may be `auto`, `float32`, `float16`, or `bfloat16`. A `device_map` supplied in pipeline options takes precedence over the simple device selection and requires Accelerate.

### Model-specific options

`--model-options` accepts a JSON object with four optional namespaces:

```json
{
  "pipeline": {"device_map": "auto"},
  "preprocess": {"voice_preset": "v2/en_speaker_6"},
  "forward": {"speaker_id": 2},
  "generate": {"do_sample": false, "temperature": 0.7}
}
```

- `pipeline` is passed while the model is loaded.
- `preprocess`, `forward`, and `generate` are passed on every synthesis call.
- Dedicated security and identity fields (`model`, `revision`, `device`, `dtype`, `trust_remote_code`, and offline mode) cannot be overridden inside JSON.
- Use `@path.json` instead of inline JSON for complex settings.

SpeechT5-style speaker embeddings can be a finite numeric `.npy` or JSON array:

```bash
epub-listener book.epub --engine huggingface --model microsoft/speecht5_tts \
  --speaker-embedding narrator.npy
```

The `--voice` shortcut means a Bark voice preset or a numeric VITS speaker ID. There is no universal Transformers voice parameter, so Epub Listener rejects an ambiguous `--voice` for other architectures and points to `--model-options` rather than silently ignoring it.

Some repositories require extra Python packages named in their model card. Install those into the same environment. A repository that implements only a custom script—not the Transformers TTS pipeline—belongs behind the command adapter.

### Security and offline use

`--trust-remote-code` executes Python from a model repository on the local machine. It is off by default. Review the repository, pin `--revision` to a commit, and only then enable it.

Use `--local-files-only` with a cached Hub model or a local model directory to prevent downloads:

```bash
epub-listener book.epub --engine huggingface --model ./models/narrator \
  --local-files-only
```

## Local command protocol

The command is parsed with platform shell quoting and launched directly, never through a shell. It receives UTF-8 chapter/chunk text on stdin. These placeholders are allowed:

| Placeholder | Value |
|---|---|
| `{output}` | required temporary output audio path |
| `{text_file}` | optional UTF-8 file containing the same text as stdin |
| `{voice}` | `--voice`, or an empty string |

Example wrapper contract:

```bash
epub-listener book.epub --engine command \
  --model-command 'python local_tts.py --input {text_file} --output {output}' \
  --command-output-format wav --model-timeout 1800
```

The process must exit successfully and write non-empty, decodable WAV, MP3, FLAC, or Ogg audio. The adapter validates sample rate, shape, finite samples, duration, and the encoded MP3 before atomically replacing cached chapter audio. Output logs spill to a temporary file and only a bounded tail enters an error; a timeout or interrupt kills the command's POSIX process group so helper processes do not leak. On an error, timeout, or interrupt, an existing chapter file is preserved.

`--chunk-chars 500` is the default for local engines. Splits prefer sentence boundaries, each chunk is streamed to WAV, and only one model copy is held in memory. Set it to `0` if a wrapper performs its own long-text processing. `--chunk-pause-ms` controls the inserted boundary pause.

## Resume identity

Cached audio is invalidated when output-affecting or model-loading settings change. For Hugging Face this includes model, revision, device, dtype, remote-code/offline policy, model options, local-model file signatures, speaker-embedding content, chunk size, voice, and speed. For commands it includes the command template, signatures of the executable and file arguments, declared format, chunk controls, voice, and speed. Secrets and full command lines are represented only by a short SHA-256 fingerprint in transcript/resume metadata.
