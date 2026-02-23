import os
import subprocess
from PIL import Image, ImageDraw, ImageFont

def generate_chapter_image(chapter_title, output_path, width=1280, height=720):
    """
    Creates a black image with the chapter title text centered on it.
    """
    # Create black background
    img = Image.new('RGB', (width, height), color=(20, 20, 20))
    d = ImageDraw.Draw(img)
    
    # Try to load a generic system font, fallback to default bitmap if not found
    try:
        # Looking for a standard font like Arial or Helvetica on Mac
        font = ImageFont.truetype("Arial", 64)
    except IOError:
        try:
            # Fallback for Mac
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 64)
        except IOError:
            # Utter fallback
            font = ImageFont.load_default()
            
    # Calculate text bounding wrapper to center it
    try:
        # getbbox returns (left, top, right, bottom)
        left, top, right, bottom = d.textbbox((0, 0), chapter_title, font=font)
        text_width = right - left
        text_height = bottom - top
    except AttributeError:
        # Backwards compatibility for older PIL
        text_width, text_height = d.textsize(chapter_title, font=font)
    
    # Text coordinates
    x = (width - text_width) / 2
    y = (height - text_height) / 2
    
    # Draw text in white
    d.text((x, y), chapter_title, font=font, fill=(240, 240, 240))
    
    img.save(output_path)
    return output_path

def create_chapter_video(image_path, audio_path, output_mp4, duration_ms):
    """
    Uses ffmpeg to create a video joining a single static image and an audio file.
    """
    duration_sec = duration_ms / 1000.0
    
    # ffmpeg command to mix a static image (-loop 1) and audio into a finite video slice
    command = [
        "ffmpeg",
        "-y", # overwrite output
        "-loop", "1",
        "-framerate", "1", # Extremely low framerate since it's a static image
        "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "copy", # Copy audio directly without re-encoding to save massive time
        "-pix_fmt", "yuv420p",
        "-shortest", # Stop encoding when the shortest stream (audio) ends
        "-t", str(duration_sec),
        "-v", "quiet",
        output_mp4
    ]
    
    try:
        subprocess.run(command, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to create video for {image_path}: {e}")
        return False

def build_metadata_file(chapters_info, output_meta_path="ffmetadata.txt"):
    """
    Generates an FFMETADATA1 formatted file for chapter skipping capability.
    
    Args:
        chapters_info (list): List of dicts, e.g. [{"title": "Ch 1", "duration": 5000}, ...] 
                              (duration is in milliseconds)
    """
    with open(output_meta_path, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\n")
        f.write("title=Audiobook\n\n")
        
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

def merge_videos_and_metadata(video_segments, metadata_file, final_output):
    """
    Uses ffmpeg concat demuxer to merge multiple mp4 chapters into one file,
    and applies the chapter metadata file.
    """
    # Create the concat input file list
    concat_list_file = "concat_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for vid in video_segments:
            # ffmpeg concat module requires paths starting with 'file' and single quotes escaped
            escaped_path = vid.replace("'", "'\\''")
            f.write(f"file '{escaped_path}'\n")

    command = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_file,
        "-i", metadata_file,
        "-map_metadata", "1", # Use metadata from the 2nd input file (the txt file)
    ]
    
    # If MP3 mode, just copy audio stream and set ID3 tags appropriately
    if final_output.lower().endswith(".mp3"):
        command.extend([
            "-c:a", "copy",
            "-write_id3v1", "1",
            "-id3v2_version", "3"
        ])
    else:
        # MP4 Mode
        command.extend([
            "-c", "copy" # Copy both video and audio streams directly
        ])
        
    command.append(final_output)
    
    try:
        subprocess.run(command, check=True)
        # Cleanup concat list
        if os.path.exists(concat_list_file):
            os.remove(concat_list_file)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error merging final video: {e}")
        return False
