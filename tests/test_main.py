from pathlib import Path
from typing import Any

import pytest

from epub_listener import __main__ as main_module
from epub_listener.application.commands import BuildAudiobookCommand
from epub_listener.config import Settings
from epub_listener.domain.exceptions import EpubListenerError


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    epub_path = tmp_path / "book.epub"
    epub_path.write_bytes(b"epub")
    return Settings(input_epub=epub_path, **overrides)


class FakeBatchGenerator:
    pass


class FakeTracker:
    instances: list["FakeTracker"] = []

    def __init__(self, path: Path) -> None:
        self.path = path
        FakeTracker.instances.append(self)


class FakeUseCase:
    next_error: BaseException | None = None
    commands: list[BuildAudiobookCommand] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def execute(self, command: BuildAudiobookCommand) -> Path:
        FakeUseCase.commands.append(command)
        if FakeUseCase.next_error:
            raise FakeUseCase.next_error
        command.output_path.write_bytes(b"mp3")
        return command.output_path


def _install_main_fakes(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    auto_dir: Path | None = None,
) -> None:
    FakeTracker.instances = []
    FakeUseCase.commands = []
    FakeUseCase.next_error = None

    monkeypatch.setattr(main_module, "parse_args", lambda: settings)
    monkeypatch.setattr(main_module, "setup_logging", lambda log_level: None)
    monkeypatch.setattr(main_module, "JsonProgressTracker", FakeTracker)
    monkeypatch.setattr(main_module, "BuildAudiobookUseCase", FakeUseCase)
    monkeypatch.setattr(
        main_module,
        "create_tts_batch_generator",
        lambda **kwargs: FakeBatchGenerator(),
    )
    if auto_dir:

        def fake_mkdtemp(prefix: str) -> str:
            auto_dir.mkdir()
            return str(auto_dir)

        monkeypatch.setattr(main_module.tempfile, "mkdtemp", fake_mkdtemp)


def test_main_rejects_missing_resume_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_resume = tmp_path / "missing"
    _install_main_fakes(
        monkeypatch,
        _settings(tmp_path, resume_dir=missing_resume),
    )

    assert main_module.main() == 1

    assert f"resume dir not found: {missing_resume}" in capsys.readouterr().out


def test_main_rejects_file_resume_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    resume_file = tmp_path / "resume-file"
    resume_file.write_text("not a directory", encoding="utf-8")
    _install_main_fakes(
        monkeypatch,
        _settings(tmp_path, resume_dir=resume_file),
    )

    assert main_module.main() == 1

    assert f"resume dir is not a directory: {resume_file}" in capsys.readouterr().out


def test_main_prints_clean_configuration_error_for_invalid_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    epub_path = tmp_path / "book.epub"
    epub_path.write_bytes(b"epub")
    monkeypatch.setattr(
        main_module.sys,
        "argv",
        ["epub-listener", str(epub_path), "--speed", "fast"],
    )

    assert main_module.main() == 1

    captured = capsys.readouterr()
    output = captured.out
    assert captured.err == ""
    assert "Error: Invalid configuration:" in output
    assert "speed: Speed must be like +10% or -20%" in output
    assert "Traceback" not in output


def test_resolve_workspace_uses_existing_resume_dir(tmp_path: Path) -> None:
    resume_dir = tmp_path / "resume"
    resume_dir.mkdir()

    workspace = main_module.resolve_workspace(_settings(tmp_path, resume_dir=resume_dir))

    assert workspace.path == resume_dir
    assert not workspace.auto_created


def test_cleanup_workspace_removes_only_auto_created_dirs(tmp_path: Path) -> None:
    auto_dir = tmp_path / "auto"
    auto_dir.mkdir()
    resume_dir = tmp_path / "resume"
    resume_dir.mkdir()

    main_module.cleanup_workspace(main_module.BuildWorkspace(auto_dir, auto_created=True))
    main_module.cleanup_workspace(main_module.BuildWorkspace(resume_dir, auto_created=False))

    assert not auto_dir.exists()
    assert resume_dir.exists()


def test_main_uses_one_workspace_for_tracker_and_command_then_cleans_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    auto_dir = tmp_path / "auto"
    output_path = tmp_path / "book.mp3"
    _install_main_fakes(
        monkeypatch,
        _settings(tmp_path, output_path=output_path),
        auto_dir,
    )

    assert main_module.main() == 0

    assert "Success! Audiobook saved" in capsys.readouterr().out
    assert FakeUseCase.commands[0].temp_dir == auto_dir
    assert FakeTracker.instances[0].path == auto_dir
    assert not auto_dir.exists()


def test_workspace_command_uses_effective_default_voice_for_generation_key(
    tmp_path: Path,
) -> None:
    workspace = main_module.BuildWorkspace(tmp_path / "work", auto_created=False)

    implicit_edge = workspace.create_command(_settings(tmp_path))
    explicit_edge = workspace.create_command(
        _settings(tmp_path, voice=main_module.EDGE_DEFAULT_VOICE)
    )
    implicit_kokoro = workspace.create_command(_settings(tmp_path, use_kokoro=True))
    mlx_kokoro = workspace.create_command(_settings(tmp_path, use_kokoro=True, kokoro_mlx=True))

    assert implicit_edge.voice == main_module.EDGE_DEFAULT_VOICE
    assert implicit_edge.generation_key == explicit_edge.generation_key
    assert f"voice={main_module.EDGE_DEFAULT_VOICE}" in implicit_edge.generation_key
    assert implicit_kokoro.voice == main_module.KOKORO_DEFAULT_VOICE
    assert f"voice={main_module.KOKORO_DEFAULT_VOICE}" in implicit_kokoro.generation_key
    assert "tts_backend=kokoro-mlx-gain+2.7db" in mlx_kokoro.generation_key


def test_main_preserves_auto_workspace_and_prints_retry_on_build_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    auto_dir = tmp_path / "auto"
    _install_main_fakes(monkeypatch, _settings(tmp_path), auto_dir)
    FakeUseCase.next_error = EpubListenerError("boom")

    assert main_module.main() == 1

    output = capsys.readouterr().out
    assert "Error: boom" in output
    assert f"Retry with: --resume-dir {auto_dir}" in output
    assert auto_dir.exists()


def test_main_preserves_auto_workspace_and_returns_interrupt_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    auto_dir = tmp_path / "auto"
    _install_main_fakes(monkeypatch, _settings(tmp_path), auto_dir)
    FakeUseCase.next_error = KeyboardInterrupt()

    assert main_module.main() == 130

    output = capsys.readouterr().out
    assert "Interrupted." in output
    assert f"Resume with: --resume-dir {auto_dir}" in output
    assert auto_dir.exists()
