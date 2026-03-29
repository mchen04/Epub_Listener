import asyncio
import edge_tts
import os
import subprocess
import json

# Recommended Edge-TTS voices: en-US-AriaNeural, en-GB-RyanNeural, en-US-GuyNeural
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"


async def _edge_tts_async(text, output_file, voice, rate):
    """Async helper to communicate with Edge-TTS and save the audio file."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_file)


def generate_chapter_audio(text, output_path, *, speed="+0%", voice=None):
    """
    Generate an audio file from text using Edge-TTS.

    Args:
        text: The chapter text to convert.
        output_path: Destination path for the .mp3 file.
        speed: Speech rate string (e.g. '+10%', '-20%').
        voice: Edge-TTS voice identifier. Defaults to en-US-AriaNeural.

    Returns:
        int: Duration of the generated audio in milliseconds, or 0 if failed.
    """
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
