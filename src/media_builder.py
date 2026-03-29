import os
import subprocess

def build_metadata_file(chapters_info, book_title="Audiobook", book_author="Unknown Author", output_meta_path="ffmetadata.txt"):
    """
    Generates an FFMETADATA1 formatted file for chapter skipping capability.

    Args:
        chapters_info (list): List of dicts, e.g. [{"title": "Ch 1", "duration": 5000}, ...]
                              (duration is in milliseconds)
    """
    with open(output_meta_path, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\n")
        f.write(f"title={book_title}\n")
        f.write(f"artist={book_author}\n")
        f.write(f"album_artist={book_author}\n")
        f.write(f"album={book_title}\n\n")

        current_time_ms = 0

        for chapter in chapters_info:
            dur = chapter.get("duration", 0)
            if dur <= 0:
                continue

            start = current_time_ms
            end = current_time_ms + dur

            f.write("[CHAPTER]\n")
            f.write("TIMEBASE=1/1000\n")
            f.write(f"START={int(start)}\n")
            f.write(f"END={int(end)}\n")
            f.write(f"title={chapter['title']}\n\n")

            current_time_ms = end

    return output_meta_path

def merge_audio_and_metadata(audio_segments, metadata_file, final_output):
    """
    Uses ffmpeg concat demuxer to merge multiple mp3 chapters into one file,
    and applies the chapter metadata file.
    """
    concat_list_file = os.path.join(os.path.dirname(audio_segments[0]), "concat_list.txt")
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for seg in audio_segments:
            escaped_path = seg.replace("'", "'\\''")
            f.write(f"file '{escaped_path}'\n")

    command = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_file,
        "-i", metadata_file,
        "-map_metadata", "1",
        "-c:a", "copy",
        "-write_id3v1", "1",
        "-id3v2_version", "3",
        final_output
    ]

    try:
        subprocess.run(command, check=True)
        if os.path.exists(concat_list_file):
            os.remove(concat_list_file)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error merging final audio: {e}")
        return False
