import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from epub_listener.infrastructure.tts import mlx_kokoro_tts


def _install_fake_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    mlx_package = ModuleType("mlx")
    mlx_core = ModuleType("mlx.core")
    mlx_core.eval = lambda value: None  # type: ignore[attr-defined]
    mlx_package.core = mlx_core  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx", mlx_package)
    monkeypatch.setitem(sys.modules, "mlx.core", mlx_core)


def _force_legacy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin run_job to the mlx-audio fallback.

    Without this, a machine with fastkoko installed takes the FastKokoro path
    and silently ignores the faked mlx-audio model, so these tests would pass
    or fail depending on the developer's environment.
    """
    monkeypatch.setattr(mlx_kokoro_tts, "_get_engine", lambda preset=None: None)


def test_mlx_provider_applies_gain_limits_peaks_and_commits_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install_fake_mlx(monkeypatch)
    _force_legacy_path(monkeypatch)
    source = np.array([0.25, -0.5, 0.9], dtype=np.float32)

    class FakeModel:
        generate_kwargs: dict[str, object] = {}

        def generate(self, *args: object, **kwargs: object) -> list[SimpleNamespace]:
            self.generate_kwargs = kwargs
            return [SimpleNamespace(audio=source)]

    model = FakeModel()
    captured_wav = np.array([], dtype=np.float32)

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        nonlocal captured_wav
        assert kwargs == {}
        wav_path = args[1]
        mp3_path = args[-1]
        assert isinstance(wav_path, Path)
        assert isinstance(mp3_path, Path)
        captured_wav, sample_rate = sf.read(wav_path, dtype="float32")
        assert sample_rate == mlx_kokoro_tts.SAMPLE_RATE
        mp3_path.write_bytes(b"encoded")

    def fake_commit(source_path: Path, output_path: Path) -> int:
        shutil.copyfile(source_path, output_path)
        return 1234

    monkeypatch.setattr(mlx_kokoro_tts, "_get_model", lambda: model)
    monkeypatch.setattr(mlx_kokoro_tts, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(mlx_kokoro_tts, "commit_generated_mp3", fake_commit)

    output = tmp_path / "chapter.mp3"
    duration = mlx_kokoro_tts.KokoroMLXTTSProvider().generate(
        "text",
        output,
        "bf_emma",
        "+0%",
    )

    expected = np.clip(source * mlx_kokoro_tts.OUTPUT_GAIN, -1.0, 1.0)
    assert captured_wav == pytest.approx(expected, abs=4e-5)
    assert duration == 1234
    assert model.generate_kwargs["lang_code"] == "b"
    assert output.read_bytes() == b"encoded"
    assert "clipped 1 of 3 samples" in caplog.text
    assert not (tmp_path / ".chapter.tmp.wav").exists()
    assert not (tmp_path / ".chapter.tmp.mp3").exists()


def test_mlx_provider_captures_chunk_level_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from epub_listener.application.ports import TTSJob, transcript_path_for
    from epub_listener.domain.transcript import parse_chapter_file

    _install_fake_mlx(monkeypatch)
    _force_legacy_path(monkeypatch)
    chunk = np.zeros(mlx_kokoro_tts.SAMPLE_RATE, dtype=np.float32)  # 1s per chunk

    class FakeModel:
        def generate(self, *args: object, **kwargs: object) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(audio=chunk, graphemes="First chunk here."),
                SimpleNamespace(audio=chunk, graphemes="Second chunk there."),
            ]

    monkeypatch.setattr(mlx_kokoro_tts, "_get_model", lambda: FakeModel())
    monkeypatch.setattr(mlx_kokoro_tts, "run_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(mlx_kokoro_tts, "commit_generated_mp3", lambda source, output: 2000)

    output = tmp_path / "chapter.mp3"
    job = TTSJob(
        "0000",
        "First chunk here.\nSecond chunk there.",
        output,
        "af_heart",
        "+0%",
        transcript_path=transcript_path_for(output),
    )
    assert mlx_kokoro_tts.KokoroMLXTTSProvider().run_job(job) == 2000

    parsed = parse_chapter_file(json.loads(transcript_path_for(output).read_text(encoding="utf-8")))
    assert parsed["engine"] == "kokoro-mlx"
    assert parsed["granularity"] == "sentence"
    sentences = parsed["sentences"]
    assert [s.text for s in sentences] == ["First chunk here.", "Second chunk there."]
    assert sentences[1].start_ms == 1000


def test_fast_path_writes_engine_audio_without_legacy_gain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FastKokoro path is preferred and must not re-apply the 2.7 dB hack."""
    source = np.array([0.25, -0.5, 0.9], dtype=np.float32)
    captured_wav = np.array([], dtype=np.float32)

    class FakeEngine:
        synth_kwargs: dict[str, object] = {}

        def synth(self, text: str, **kwargs: object) -> list[SimpleNamespace]:
            self.synth_kwargs = kwargs
            return [SimpleNamespace(audio=source, graphemes=text, tokens=[])]

    engine = FakeEngine()

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        nonlocal captured_wav
        captured_wav, sample_rate = sf.read(args[1], dtype="float32")
        assert sample_rate == mlx_kokoro_tts.SAMPLE_RATE
        Path(args[-1]).write_bytes(b"encoded")  # type: ignore[arg-type]

    monkeypatch.setattr(mlx_kokoro_tts, "_get_engine", lambda preset=None: engine)
    monkeypatch.setattr(mlx_kokoro_tts, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(mlx_kokoro_tts, "commit_generated_mp3", lambda source, output: 4321)

    output = tmp_path / "chapter.mp3"
    duration = mlx_kokoro_tts.KokoroMLXTTSProvider().generate("text", output, "bf_emma", "+0%")

    assert duration == 4321
    # Unscaled: the numerical fixes removed the need for OUTPUT_GAIN.
    assert captured_wav == pytest.approx(source, abs=4e-5)
    assert engine.synth_kwargs["voice"] == "bf_emma"
    assert engine.synth_kwargs["speed"] == 1.0
    assert not (tmp_path / ".chapter.tmp.wav").exists()
    assert not (tmp_path / ".chapter.tmp.mp3").exists()
