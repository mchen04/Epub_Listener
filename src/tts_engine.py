import asyncio
import edge_tts
import os
import subprocess
import json
import tempfile
import numpy as np

# Recommended Edge-TTS voices: en-US-AriaNeural, en-GB-RyanNeural, en-US-GuyNeural
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"

DEFAULT_KOKORO_VOICE = "af_heart"
DEFAULT_KOKORO_LANG = "a"

# Lazy-loaded Kokoro pipeline cache
_kokoro_pipelines = {}


def _get_kokoro_pipeline(lang_code="a"):
    """Lazy-load and cache Kokoro pipelines by language code."""
    global _kokoro_pipelines
    if lang_code not in _kokoro_pipelines:
        try:
            from kokoro import KPipeline
        except ImportError as e:
            raise ImportError(
                "Kokoro is not installed. Run: pip install kokoro>=0.9.4 soundfile"
            ) from e
        _kokoro_pipelines[lang_code] = KPipeline(lang_code=lang_code)
    return _kokoro_pipelines[lang_code]


def _convert_wav_to_mp3(wav_path, mp3_path):
    """Convert a WAV file to MP3 using ffmpeg (already a project dependency)."""
    command = [
        "ffmpeg", "-y", "-i", wav_path,
        "-codec:a", "libmp3lame", "-q:a", "2",
        mp3_path
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _edge_speed_to_kokoro(speed_str):
    """
    Convert Edge-TTS speed string (e.g. '+10%%', '-20%%') to Kokoro float multiplier.
    Defaults to 1.0 if parsing fails.
    """
    try:
        # Remove trailing % and convert to float
        clean = speed_str.replace("%", "").strip()
        delta = float(clean)
        return 1.0 + (delta / 100.0)
    except (ValueError, TypeError):
        return 1.0


async def _edge_tts_async(text, output_file, voice, rate):
    """Async helper to communicate with Edge-TTS and save the audio file."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_file)


def generate_chapter_audio(text, output_path, *, speed="+0%", voice=None,
                           use_kokoro=False, kokoro_voice=None, kokoro_lang=None):
    """
    Generate an audio file from text using either Edge-TTS or Kokoro-82M.

    Args:
        text: The chapter text to convert.
        output_path: Destination path for the .mp3 file.
        speed: Speech rate string (e.g. '+10%%', '-20%%').
        voice: Edge-TTS voice identifier. Defaults to en-US-AriaNeural.
        use_kokoro: If True, use local Kokoro-82M inference instead of Edge-TTS.
        kokoro_voice: Kokoro voice identifier (e.g. 'af_heart', 'am_fenrir').
        kokoro_lang: Kokoro language code (e.g. 'a' for American English,
                     'b' for British English). Defaults to 'a'.

    Returns:
        int: Duration of the generated audio in milliseconds, or 0 if failed.
    """
    if use_kokoro:
        return _generate_kokoro_audio(
            text, output_path,
            voice=kokoro_voice, lang=kokoro_lang, speed=speed
        )
    return _generate_edge_tts_audio(text, output_path, speed=speed, voice=voice)


def _generate_edge_tts_audio(text, output_path, speed="+0%", voice=None):
    """Generate audio using Edge-TTS (cloud/Azure voices)."""
    voice = voice or DEFAULT_EDGE_VOICE
    try:
        asyncio.run(_edge_tts_async(text, output_path, voice, speed))
        if os.path.exists(output_path):
            return get_audio_duration_ms(output_path)
    except ConnectionError as e:
        print(f"Edge-TTS connection error for {output_path}: {e}")
    except OSError as e:
        print(f"Edge-TTS OS error for {output_path}: {e}")
    except Exception as e:
        print(f"Edge-TTS error for {output_path}: {type(e).__name__}: {e}")
    return 0


def _generate_kokoro_audio(text, output_path, voice=None, lang=None, speed="+0%"):
    """Generate audio using local Kokoro-82M inference."""
    voice = voice or DEFAULT_KOKORO_VOICE
    lang = lang or DEFAULT_KOKORO_LANG
    try:
        import soundfile as sf
    except ImportError as e:
        print(f"Kokoro error: soundfile not installed. {e}")
        return 0

    try:
        pipeline = _get_kokoro_pipeline(lang)
        speed_float = _edge_speed_to_kokoro(speed)
        generator = pipeline(text, voice=voice, speed=speed_float)

        segments = []
        sample_rate = 24000
        for _, _, audio in generator:
            segments.append(audio)

        if not segments:
            return 0

        full_audio = np.concatenate(segments)

        # Kokoro outputs raw WAV; write to temp WAV then convert to MP3
        # so the rest of the pipeline stays codec-consistent.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
            tmp_wav_path = tmp_wav.name

        sf.write(tmp_wav_path, full_audio, sample_rate)
        _convert_wav_to_mp3(tmp_wav_path, output_path)
        os.remove(tmp_wav_path)

        if os.path.exists(output_path):
            return get_audio_duration_ms(output_path)
    except Exception as e:
        print(f"Kokoro error for {output_path}: {type(e).__name__}: {e}")
    return 0


def get_audio_duration_ms(audio_file_path):
    """Uses ffprobe to extract the exact duration of an audio file in milliseconds."""
    try:
        command = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            audio_file_path
        ]

        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(result.stdout)

        if "format" in data and "duration" in data["format"]:
            duration_sec = float(data["format"]["duration"])
            return int(duration_sec * 1000)
    except FileNotFoundError:
        print("Error: ffprobe not found. Is FFmpeg installed?")
    except subprocess.CalledProcessError as e:
        print(f"Error running ffprobe on {audio_file_path}: {e}")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"Error parsing ffprobe output for {audio_file_path}: {e}")

    return 0


if __name__ == "__main__":
    test_text = "This is a brief test chapter, evaluating whether the TTS integration is functioning correctly and generating valid MP3 files."
    test_out = "test_output.mp3"
    print(f"Generating test audio '{test_out}' with Edge-TTS...")
    duration = generate_chapter_audio(test_text, test_out, speed="+20%")
    print(f"Completed! Duration: {duration} ms")
