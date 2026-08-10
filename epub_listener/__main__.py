"""Composition root and entry point."""

import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from importlib.util import find_spec
from logging.handlers import RotatingFileHandler
from pathlib import Path

from epub_listener.application.commands import BuildAudiobookCommand
from epub_listener.application.orchestrator import BuildAudiobookUseCase
from epub_listener.application.ports import ProgressTracker
from epub_listener.cli import parse_args
from epub_listener.config import Settings
from epub_listener.domain.exceptions import ConfigurationError, EpubListenerError, ResumeError
from epub_listener.infrastructure.media.ffmpeg_assembler import FFmpegMediaAssembler
from epub_listener.infrastructure.media.metadata_builder import FFmpegMetadataBuilder
from epub_listener.infrastructure.media.transcript_embedder import Id3TranscriptEmbedder
from epub_listener.infrastructure.parsers.ebooklib_parser import EbookLibParser
from epub_listener.infrastructure.persistence.json_tracker import JsonProgressTracker
from epub_listener.infrastructure.tts.command_tts import parse_command_template
from epub_listener.infrastructure.tts.edge_tts import DEFAULT_VOICE as EDGE_DEFAULT_VOICE
from epub_listener.infrastructure.tts.factory import create_tts_batch_generator
from epub_listener.infrastructure.tts.kokoro_tts import DEFAULT_VOICE as KOKORO_DEFAULT_VOICE

_LOG_HANDLER_NAMES = {"epub_listener_console", "epub_listener_file"}


@dataclass(frozen=True)
class BuildWorkspace:
    path: Path
    auto_created: bool

    def create_command(self, settings: Settings) -> BuildAudiobookCommand:
        voice = settings.resolved_voice
        if voice is None and settings.tts_engine == "edge":
            voice = EDGE_DEFAULT_VOICE
        elif voice is None and settings.tts_engine in {"kokoro", "kokoro-mlx"}:
            voice = KOKORO_DEFAULT_VOICE
        return BuildAudiobookCommand(
            input_epub=settings.input_epub,
            output_path=settings.resolve_output_path(),
            author=settings.author,
            voice=voice,
            speed=settings.speed,
            temp_dir=self.path,
            title=settings.title,
            tts_backend=settings.tts_backend,
            transcript=settings.transcript,
        )

    def create_tracker(self) -> ProgressTracker:
        return JsonProgressTracker(self.path)


def setup_logging(log_level: str, log_dir: Path = Path("logs")) -> None:
    """Configure unified logging to console and rotating file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    for handler in list(root.handlers):
        if handler.get_name() in _LOG_HANDLER_NAMES:
            root.removeHandler(handler)
            handler.close()

    console = logging.StreamHandler(sys.stdout)
    console.set_name("epub_listener_console")
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "epub_listener.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.set_name("epub_listener_file")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def resolve_workspace(settings: Settings) -> BuildWorkspace:
    if settings.resume_dir:
        if not settings.resume_dir.exists():
            raise ResumeError(f"resume dir not found: {settings.resume_dir}")
        if not settings.resume_dir.is_dir():
            raise ResumeError(f"resume dir is not a directory: {settings.resume_dir}")
        logging.info("Resuming from: %s", settings.resume_dir)
        return BuildWorkspace(settings.resume_dir, auto_created=False)
    return BuildWorkspace(Path(tempfile.mkdtemp(prefix="epub_audiobook_")), auto_created=True)


def cleanup_workspace(workspace: BuildWorkspace) -> None:
    if not workspace.auto_created:
        return
    try:
        shutil.rmtree(workspace.path)
    except OSError as exc:
        logging.warning("Could not remove temp dir %s: %s", workspace.path, exc)


def validate_runtime(settings: Settings) -> None:
    """Fail before parsing/downloading when required runtime tools are absent."""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise ConfigurationError(
            f"Required executable(s) not found in PATH: {', '.join(missing)}. Install FFmpeg."
        )

    engine = settings.tts_engine
    if engine == "huggingface" and find_spec("transformers") is None:
        raise ConfigurationError(
            "Hugging Face support is not installed. Run: pip install '.[huggingface]'"
        )
    if engine == "kokoro" and find_spec("kokoro") is None:
        raise ConfigurationError("Kokoro is not installed. Run: pip install '.[kokoro]'")
    if engine == "kokoro-mlx" and all(
        find_spec(module) is None for module in ("fastkoko", "mlx_audio")
    ):
        raise ConfigurationError("MLX Kokoro is not installed. Run: pip install '.[mlx]'")
    if engine == "command":
        if settings.model_command is None:
            raise ConfigurationError("--engine command requires --model-command")
        executable = parse_command_template(settings.model_command)[0]
        candidate = str(Path(executable).expanduser())
        if shutil.which(candidate) is None:
            raise ConfigurationError(f"Local TTS executable not found: {executable}")


def main() -> int:
    """CLI entry point."""
    workspace: BuildWorkspace | None = None
    build_started = False
    logging_ready = False
    try:
        settings = parse_args()
        validate_runtime(settings)
        setup_logging(settings.log_level)
        logging_ready = True
        tts = create_tts_batch_generator(
            engine=settings.tts_engine,
            concurrency=settings.concurrency,
            max_workers=settings.max_workers,
            kokoro_hybrid_mps=settings.kokoro_hybrid_mps,
            kokoro_mlx=settings.kokoro_mlx,
            kokoro_preset=settings.kokoro_preset,
            model=settings.model,
            revision=settings.revision,
            device=settings.device,
            dtype=settings.dtype,
            trust_remote_code=settings.trust_remote_code,
            local_files_only=settings.local_files_only,
            model_options=settings.model_options,
            speaker_embedding=settings.speaker_embedding,
            chunk_chars=settings.chunk_chars,
            chunk_pause_ms=settings.chunk_pause_ms,
            model_command=settings.model_command,
            command_output_format=settings.command_output_format,
            model_timeout=settings.model_timeout,
        )
        workspace = resolve_workspace(settings)
        command = workspace.create_command(settings)
        use_case = BuildAudiobookUseCase(
            parser=EbookLibParser(),
            tts=tts,
            assembler=FFmpegMediaAssembler(),
            metadata_builder=FFmpegMetadataBuilder(),
            tracker=workspace.create_tracker(),
            transcript_embedder=Id3TranscriptEmbedder(),
        )
        build_started = True
        output = use_case.execute(command)
        print(f"\nSuccess! Audiobook saved to {output}")
        cleanup_workspace(workspace)
        return 0
    except EpubListenerError as exc:
        if logging_ready:
            logging.error("Build failed: %s", exc)
        print(f"Error: {exc}")
        if workspace and workspace.auto_created:
            if build_started:
                print(f"Retry with: --resume-dir {workspace.path}")
            else:
                cleanup_workspace(workspace)
        return 1
    except KeyboardInterrupt:
        logging.warning("Build interrupted by user.")
        print("\nInterrupted.")
        if workspace and workspace.auto_created:
            print(f"Resume with: --resume-dir {workspace.path}")
        return 130


if __name__ == "__main__":
    sys.exit(main())
