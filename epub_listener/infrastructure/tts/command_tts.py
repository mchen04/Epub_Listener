"""Safe subprocess adapter for arbitrary local TTS engines."""

from __future__ import annotations

import os
import shlex
import signal
import string
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

from epub_listener.domain.exceptions import ConfigurationError, TTSGenerationError
from epub_listener.infrastructure.tts.waveform import AudioChunk, WaveformTTSProvider

_ALLOWED_FIELDS = frozenset({"output", "text_file", "voice"})
_ERROR_TAIL_BYTES = 2_000


def parse_command_template(template: str) -> tuple[str, ...]:
    """Parse and validate a command template without invoking a shell."""
    try:
        argv = tuple(shlex.split(template))
    except ValueError as exc:
        raise ConfigurationError(f"Invalid --model-command quoting: {exc}") from exc
    if not argv:
        raise ConfigurationError("--model-command must not be empty")
    if "{" in argv[0] or "}" in argv[0]:
        raise ConfigurationError("--model-command executable must not contain placeholders")

    fields: set[str] = set()
    formatter = string.Formatter()
    try:
        for argument in argv:
            for _, field, format_spec, conversion in formatter.parse(argument):
                if field is None:
                    continue
                if format_spec or conversion:
                    raise ConfigurationError(
                        "--model-command placeholders do not support conversions or format specs"
                    )
                fields.add(field)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid --model-command template: {exc}") from exc
    unknown = fields - _ALLOWED_FIELDS
    if unknown:
        raise ConfigurationError(
            f"Unknown --model-command placeholder(s): {', '.join(sorted(unknown))}"
        )
    if "output" not in fields:
        raise ConfigurationError("--model-command must contain the {output} placeholder")
    return argv


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Kill a command and, on POSIX, descendants in its new process group."""
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    process.communicate()


def _read_log_tail(log_file: BinaryIO) -> str:
    log_file.seek(0, os.SEEK_END)
    size = log_file.tell()
    log_file.seek(max(0, size - _ERROR_TAIL_BYTES))
    payload = log_file.read()
    return payload.decode("utf-8", errors="replace").strip()


def run_local_command(argv: list[str], text: str, timeout_seconds: float) -> tuple[int, str]:
    """Run with bounded log memory and deterministic descendant cleanup."""
    with tempfile.TemporaryFile(mode="w+b") as log_file:
        process = subprocess.Popen(  # noqa: S603 - explicit user-selected executable, no shell
            argv,
            stdin=subprocess.PIPE,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=os.name == "posix",
            shell=False,
        )

        try:
            process.communicate(text, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
            raise
        except BaseException:
            _terminate_process_tree(process)
            raise
        if process.returncode is None:
            raise TTSGenerationError("Local TTS command ended without a return code")
        return process.returncode, _read_log_tail(log_file)


class CommandTTSProvider(WaveformTTSProvider):
    """Invoke any local executable that writes a WAV/MP3/FLAC audio file.

    Chapter text is supplied on stdin and in the optional ``{text_file}``
    placeholder. Arguments are executed directly with ``shell=False``.
    """

    def __init__(
        self,
        *,
        command: str,
        output_format: str = "wav",
        timeout_seconds: float = 1800,
        chunk_chars: int = 0,
        chunk_pause_ms: int = 80,
    ) -> None:
        super().__init__(
            engine_name="command",
            chunk_chars=chunk_chars,
            chunk_pause_ms=chunk_pause_ms,
        )
        self.argv_template = parse_command_template(command)
        if output_format not in {"wav", "mp3", "flac", "ogg"}:
            raise ConfigurationError("--command-output-format must be one of: wav, mp3, flac, ogg")
        if timeout_seconds <= 0:
            raise ConfigurationError("--model-timeout must be positive")
        self.output_format = output_format
        self.timeout_seconds = timeout_seconds

    def synthesize_chunk(
        self,
        text: str,
        voice: str | None,
        *,
        work_dir: Path,
        chunk_index: int,
    ) -> AudioChunk:
        raw_fd, raw_name = tempfile.mkstemp(
            prefix=f".command-{chunk_index}-",
            suffix=f".{self.output_format}",
            dir=work_dir,
        )
        os.close(raw_fd)
        text_fd, text_name = tempfile.mkstemp(
            prefix=f".command-{chunk_index}-",
            suffix=".txt",
            dir=work_dir,
            text=True,
        )
        raw_output = Path(raw_name)
        text_file = Path(text_name)
        values = {
            "output": str(raw_output),
            "text_file": str(text_file),
            "voice": voice or "",
        }
        argv = [argument.format_map(values) for argument in self.argv_template]
        try:
            with os.fdopen(text_fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            returncode, detail = run_local_command(argv, text, self.timeout_seconds)
            if returncode != 0:
                raise TTSGenerationError(
                    f"Local TTS command exited with status {returncode}: "
                    f"{detail or 'no error output'}"
                )
            if not raw_output.exists() or raw_output.stat().st_size == 0:
                raise TTSGenerationError(
                    "Local TTS command succeeded but did not write non-empty {output}"
                )
            try:
                import soundfile as sf
            except ImportError as exc:
                raise TTSGenerationError("soundfile is not installed") from exc
            samples, sample_rate = sf.read(raw_output, dtype="float32", always_2d=False)
            return AudioChunk(samples, sample_rate)
        except FileNotFoundError as exc:
            raise TTSGenerationError(f"Local TTS executable not found: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TTSGenerationError(
                f"Local TTS command timed out after {self.timeout_seconds:g}s"
            ) from exc
        except OSError as exc:
            raise TTSGenerationError(f"Could not run local TTS command: {exc}") from exc
        finally:
            # fdopen owns text_fd only after entering its context. Close it on
            # an earlier format/render failure without masking the root error.
            with suppress(OSError):
                os.close(text_fd)
            raw_output.unlink(missing_ok=True)
            text_file.unlink(missing_ok=True)
