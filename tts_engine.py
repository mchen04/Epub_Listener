import asyncio
import edge_tts
import os
import subprocess
import json

# Recommended natural voices: en-US-AriaNeural, en-GB-RyanNeural, en-US-GuyNeural
DEFAULT_VOICE = "en-US-AriaNeural"

async def _generate_audio(text, output_file, voice, rate):
    """
    Asynchronous helper to communicate with Edge-TTS and save the audio file.
    """
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_file)

def generate_chapter_audio(text, output_path, speed="+0%", voice=DEFAULT_VOICE):
    """
    Generates an audio file from text using Edge-TTS natively via Python asyncio.
    Synchronous wrapper meant to be called by the main tool.
    
    Args:
        text (str): The chapter text to convert.
        output_path (str): The destination path for the .mp3 file.
        speed (str): The speech rate string formatted as '+X%' or '-X%'.
        voice (str): The Edge-TTS voice identifier.
        
    Returns:
        int: Duration of the generated audio in milliseconds, or 0 if failed.
    """
    try:
        # Run the asynchronous edge_tts command synchronously
        asyncio.run(_generate_audio(text, output_path, voice, speed))
        
        # We need the exact duration in milliseconds for accurate chapter metadata.
        # We can use ffprobe to get this efficiently.
        if os.path.exists(output_path):
            return get_audio_duration_ms(output_path)
            
    except Exception as e:
        print(f"Error generating audio for file {output_path}: {e}")
        
    return 0

def get_audio_duration_ms(audio_file_path):
    """
    Uses ffprobe to extract the exact duration of an audio file in milliseconds.
    """
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
            # Duration is returned in seconds as a float string, e.g., "12.3456"
            duration_sec = float(data["format"]["duration"])
            # Convert to milliseconds and round to integer
            return int(duration_sec * 1000)
    except Exception as e:
        print(f"Error calculating duration for {audio_file_path}: {e}")
        
    return 0

if __name__ == "__main__":
    # Test script for the TTS module
    test_text = "This is a brief test chapter, evaluating whether the Edge-TTS integration is functioning correctly and generating valid MP3 files."
    test_out = "test_output.mp3"
    print(f"Generating test audio '{test_out}'...")
    duration = generate_chapter_audio(test_text, test_out, speed="+20%")
    print(f"Completed! Duration: {duration} ms")
