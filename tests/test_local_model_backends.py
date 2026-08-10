import json
import os
import shlex
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from ebooklib import epub
from pydantic import ValidationError

from epub_listener import __main__ as main_module
from epub_listener.application.ports import TTSJob, transcript_path_for
from epub_listener.config import Settings
from epub_listener.domain.exceptions import ConfigurationError, TTSGenerationError
from epub_listener.domain.transcript import parse_chapter_file
from epub_listener.infrastructure.tts.batch import SequentialTTSBatchGenerator
from epub_listener.infrastructure.tts.command_tts import CommandTTSProvider, parse_command_template
from epub_listener.infrastructure.tts.factory import create_tts_batch_generator
from epub_listener.infrastructure.tts.huggingface_tts import HuggingFaceTTSProvider
from epub_listener.infrastructure.tts.waveform import atempo_filter, split_for_tts
from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms


def _epub(tmp_path: Path) -> Path:
    path = tmp_path / "book.epub"
    path.write_bytes(b"stub")
    return path


def _fake_command(
    *,
    sleep: float = 0.0,
    child_marker: Path | None = None,
    noise: int = 0,
    exit_code: int = 0,
) -> str:
    script = Path(__file__).parent / "fixtures" / "fake_local_tts.py"
    argv = [
        shlex.quote(sys.executable),
        shlex.quote(str(script)),
        "--output",
        "{output}",
        "--text-file",
        "{text_file}",
        "--voice",
        "{voice}",
        "--sleep",
        str(sleep),
        "--noise",
        str(noise),
        "--exit-code",
        str(exit_code),
    ]
    if child_marker is not None:
        argv.extend(("--child-marker", shlex.quote(str(child_marker))))
    return " ".join(argv)


def test_settings_select_unified_engines_and_fingerprint_model_options(tmp_path: Path) -> None:
    epub = _epub(tmp_path)
    first = Settings(
        input_epub=epub,
        engine="huggingface",
        model="facebook/mms-tts-eng",
        revision="deadbeef",
        model_options={"generate": {"do_sample": False}},
    )
    second = Settings(
        input_epub=epub,
        engine="huggingface",
        model="facebook/mms-tts-eng",
        revision="deadbeef",
        model_options={"generate": {"do_sample": True}},
    )

    assert first.tts_engine == "huggingface"
    assert first.tts_backend.startswith("huggingface:facebook/mms-tts-eng#")
    assert first.tts_backend != second.tts_backend
    assert "deadbeef" not in first.tts_backend

    trusted = first.model_copy(update={"trust_remote_code": True})
    offline = first.model_copy(update={"local_files_only": True})
    assert trusted.tts_backend != first.tts_backend
    assert offline.tts_backend != first.tts_backend


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"engine": "huggingface"}, "requires --model"),
        ({"model": "facebook/mms-tts-eng"}, "--model requires"),
        ({"engine": "command"}, "requires --model-command"),
        (
            {"engine": "edge", "trust_remote_code": True},
            "security flags require --engine huggingface",
        ),
        ({"speed": "-100%"}, r"between -90% and \+1500%"),
    ],
)
def test_settings_reject_ambiguous_or_unsafe_model_configuration(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(input_epub=_epub(tmp_path), **overrides)


def test_model_options_reject_reserved_pipeline_keys(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="dedicated flags"):
        Settings(
            input_epub=_epub(tmp_path),
            engine="huggingface",
            model="model",
            model_options={"pipeline": {"trust_remote_code": True}},
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"engine": "command", "model_command": "tts {output}", "device": "cpu"},
        {"engine": "command", "model_command": "tts {output}", "revision": "main"},
        {"engine": "edge", "chunk_chars": 800},
        {"engine": "huggingface", "model": "model", "model_timeout": 10},
    ],
)
def test_settings_reject_options_that_the_selected_engine_would_ignore(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        Settings(input_epub=_epub(tmp_path), **overrides)


def test_local_model_and_command_dependency_changes_invalidate_resume_key(tmp_path: Path) -> None:
    epub = _epub(tmp_path)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    weights = model_dir / "model.safetensors"
    weights.write_bytes(b"first")
    first_model_key = Settings(
        input_epub=epub, engine="huggingface", model=str(model_dir)
    ).tts_backend
    weights.write_bytes(b"second-version")
    second_model_key = Settings(
        input_epub=epub, engine="huggingface", model=str(model_dir)
    ).tts_backend

    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text("print('first')", encoding="utf-8")
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(wrapper))} --out {{output}}"
    first_command_key = Settings(
        input_epub=epub, engine="command", model_command=command
    ).tts_backend
    wrapper.write_text("print('second version')", encoding="utf-8")
    second_command_key = Settings(
        input_epub=epub, engine="command", model_command=command
    ).tts_backend

    assert first_model_key != second_model_key
    assert first_command_key != second_command_key


def test_factory_builds_huggingface_and_command_adapters() -> None:
    huggingface = create_tts_batch_generator(
        engine="huggingface",
        model="local/model",
        concurrency="auto",
        max_workers=4,
    )
    command = create_tts_batch_generator(
        engine="command",
        model_command="local-tts --output {output}",
        concurrency="sequential",
        max_workers=4,
    )

    assert isinstance(huggingface, SequentialTTSBatchGenerator)
    assert isinstance(huggingface._provider, HuggingFaceTTSProvider)
    assert isinstance(command._provider, CommandTTSProvider)

    with pytest.raises(ConfigurationError, match="require --concurrency sequential"):
        create_tts_batch_generator(
            engine="huggingface",
            model="local/model",
            concurrency="parallel",
            max_workers=2,
        )


def test_split_for_tts_prefers_sentence_boundaries_and_preserves_words() -> None:
    text = "First sentence has words. Second sentence has several more words. Final words."
    chunks = split_for_tts(text, 40)

    assert len(chunks) == 3
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


def test_atempo_filter_supports_rates_outside_single_filter_range() -> None:
    assert atempo_filter(1.0) is None
    assert atempo_filter(0.25) == "atempo=0.5,atempo=0.5"
    assert atempo_filter(4.0) == "atempo=2,atempo=2"


class FakePipeline:
    def __init__(self, model_type: str = "bark") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.model = SimpleNamespace(config=SimpleNamespace(model_type=model_type))

    def __call__(self, text: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((text, kwargs))
        duration_frames = max(1_600, len(text) * 80)
        time = np.arange(duration_frames, dtype=np.float32) / 16_000
        audio = np.sin(2 * np.pi * 440 * time, dtype=np.float32)[np.newaxis, :]
        return {"audio": audio, "sampling_rate": 16_000}


def test_huggingface_provider_streams_chunks_reuses_model_and_captures_transcript(
    tmp_path: Path,
) -> None:
    factory_calls: list[dict[str, Any]] = []
    pipeline = FakePipeline()

    def factory(**kwargs: Any) -> FakePipeline:
        factory_calls.append(kwargs)
        return pipeline

    provider = HuggingFaceTTSProvider(
        model="org/model",
        revision="abc123",
        device="cpu",
        dtype="float32",
        trust_remote_code=False,
        local_files_only=True,
        model_options={
            "pipeline": {"use_fast": False},
            "preprocess": {"language": "en"},
            "forward": {"speaker_id": 2},
            "generate": {"do_sample": False},
        },
        chunk_chars=100,
        chunk_pause_ms=20,
        pipeline_factory=factory,
    )
    output = tmp_path / "chapter.mp3"
    text = "One sentence with enough words to use some space. " * 5
    job = TTSJob(
        "0001",
        text,
        output,
        "voice-preset",
        "+0%",
        transcript_path=transcript_path_for(output),
    )

    duration = provider.run_job(job)

    assert duration == get_audio_duration_ms(output)
    assert duration > 0
    assert len(factory_calls) == 1
    assert len(pipeline.calls) >= 2
    assert factory_calls[0] == {
        "use_fast": False,
        "device": "cpu",
        "task": "text-to-speech",
        "model": "org/model",
        "revision": "abc123",
        "dtype": "float32",
        "trust_remote_code": False,
    }
    call_options = pipeline.calls[0][1]
    assert call_options["preprocess_params"] == {
        "language": "en",
        "voice_preset": "voice-preset",
    }
    assert call_options["forward_params"] == {"speaker_id": 2}
    assert call_options["generate_kwargs"] == {"do_sample": False}

    transcript = parse_chapter_file(
        json.loads(transcript_path_for(output).read_text(encoding="utf-8"))
    )
    assert transcript["engine"] == "huggingface:org/model"
    assert transcript["granularity"] == "sentence"
    assert len(transcript["sentences"]) >= 5


def test_huggingface_provider_preserves_existing_output_on_invalid_model_result(
    tmp_path: Path,
) -> None:
    output = tmp_path / "chapter.mp3"
    output.write_bytes(b"existing")
    provider = HuggingFaceTTSProvider(
        model="broken",
        pipeline_factory=lambda **kwargs: lambda text, **call_kwargs: {
            "audio": [],
            "sampling_rate": 16_000,
        },
    )

    with pytest.raises(TTSGenerationError, match="no audio samples"):
        provider.generate("Some text", output, None, "+0%")

    assert output.read_bytes() == b"existing"


def test_huggingface_voice_shortcuts_are_architecture_aware(tmp_path: Path) -> None:
    unknown_pipeline = FakePipeline(model_type="custom")
    unknown = HuggingFaceTTSProvider(
        model="custom",
        pipeline_factory=lambda **kwargs: unknown_pipeline,
    )
    with pytest.raises(TTSGenerationError, match="no generic --voice mapping"):
        unknown.synthesize_chunk("text", "narrator", work_dir=tmp_path, chunk_index=0)

    vits_pipeline = FakePipeline(model_type="vits")
    vits = HuggingFaceTTSProvider(
        model="vits",
        pipeline_factory=lambda **kwargs: vits_pipeline,
    )
    vits.synthesize_chunk("text", "3", work_dir=tmp_path, chunk_index=0)

    assert vits_pipeline.calls[0][1]["forward_params"]["speaker_id"] == 3


def test_huggingface_speaker_embedding_is_loaded_safely_and_reused(tmp_path: Path) -> None:
    embedding = tmp_path / "speaker.npy"
    np.save(embedding, np.arange(8, dtype=np.float32))
    pipeline = FakePipeline(model_type="speecht5")
    provider = HuggingFaceTTSProvider(
        model="speecht5",
        speaker_embedding=embedding,
        pipeline_factory=lambda **kwargs: pipeline,
    )

    provider.synthesize_chunk("first", None, work_dir=tmp_path, chunk_index=0)
    provider.synthesize_chunk("second", None, work_dir=tmp_path, chunk_index=1)

    first = pipeline.calls[0][1]["forward_params"]["speaker_embeddings"]
    second = pipeline.calls[1][1]["forward_params"]["speaker_embeddings"]
    assert tuple(first.shape) == (1, 8)
    assert first is second


def test_command_provider_runs_real_local_executable_without_shell(tmp_path: Path) -> None:
    output = tmp_path / "command.mp3"
    provider = CommandTTSProvider(
        command=_fake_command(),
        output_format="wav",
        timeout_seconds=5,
        chunk_chars=0,
    )
    job = TTSJob(
        "0002",
        "The local executable receives this exact text.",
        output,
        "demo-voice",
        "+0%",
        transcript_path=transcript_path_for(output),
    )

    duration = provider.run_job(job)

    assert duration == get_audio_duration_ms(output)
    assert duration > 0
    transcript = parse_chapter_file(
        json.loads(transcript_path_for(output).read_text(encoding="utf-8"))
    )
    assert transcript["engine"] == "command"
    assert transcript["sentences"]
    assert not list(tmp_path.glob(".*.command.*"))


def test_command_template_and_timeout_are_hardened(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"must contain the \{output\}"):
        parse_command_template("local-tts --voice {voice}")
    with pytest.raises(ConfigurationError, match="Unknown.*placeholder"):
        parse_command_template("local-tts --output {output} --text {text}")
    with pytest.raises(ConfigurationError, match="executable must not contain placeholders"):
        parse_command_template("{voice} --output {output}")

    provider = CommandTTSProvider(
        command=_fake_command(sleep=1),
        timeout_seconds=0.01,
    )
    with pytest.raises(TTSGenerationError, match="timed out"):
        provider.generate("text", tmp_path / "timeout.mp3", None, "+0%")


def test_command_error_output_is_bounded(tmp_path: Path) -> None:
    provider = CommandTTSProvider(
        command=_fake_command(noise=20_000, exit_code=7),
        timeout_seconds=5,
    )
    with pytest.raises(TTSGenerationError) as raised:
        provider.generate("text", tmp_path / "failed.mp3", None, "+0%")

    message = str(raised.value)
    assert "status 7" in message
    assert len(message) < 2_200


@pytest.mark.skipif(os.name != "posix", reason="process-group semantics are POSIX-specific")
def test_command_timeout_kills_descendant_processes(tmp_path: Path) -> None:
    marker = tmp_path / "leaked-child.txt"
    provider = CommandTTSProvider(
        command=_fake_command(sleep=2, child_marker=marker),
        timeout_seconds=0.05,
    )

    with pytest.raises(TTSGenerationError, match="timed out"):
        provider.generate("text", tmp_path / "timeout.mp3", None, "+0%")
    time.sleep(0.35)

    assert not marker.exists()


def test_cli_converts_real_epub_with_arbitrary_local_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    book_path = tmp_path / "local.epub"
    book = epub.EpubBook()
    book.set_identifier("local-command-integration")
    book.set_title("Local Command Integration")
    book.set_language("en")
    chapter = epub.EpubHtml(title="Only Chapter", file_name="chapter.xhtml", lang="en")
    chapter.content = (
        "<h1>Only Chapter</h1><p>"
        + "This is an end to end local model test with enough real prose for extraction. " * 3
        + "</p>"
    )
    book.add_item(chapter)
    book.toc = (epub.Link("chapter.xhtml", "Only Chapter", "only"),)
    book.spine = [chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(book_path, book)

    output = tmp_path / "local.mp3"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "epub-listener",
            str(book_path),
            str(output),
            "--engine",
            "command",
            "--model-command",
            _fake_command(),
            "--chunk-chars",
            "0",
            "--log-level",
            "WARNING",
        ],
    )

    assert main_module.main() == 0

    assert output.stat().st_size > 0
    assert output.with_suffix(".transcript.json").stat().st_size > 0
    assert "Success! Audiobook saved" in capsys.readouterr().out


@pytest.mark.live
def test_live_huggingface_transformers_model(tmp_path: Path) -> None:
    """Small real-model gate for the public Transformers TTS contract."""
    provider = HuggingFaceTTSProvider(
        model="facebook/mms-tts-eng",
        device="cpu",
        dtype="float32",
        local_files_only=False,
        chunk_chars=0,
        chunk_pause_ms=0,
    )
    output = tmp_path / "huggingface.mp3"

    duration = provider.generate("A real model compatibility check.", output, None, "+0%")

    assert duration == get_audio_duration_ms(output)
    assert duration > 0
    assert output.stat().st_size > 0
