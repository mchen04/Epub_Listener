import argparse
import os
import tempfile
from epub_parser import extract_chapters
from tts_engine import generate_chapter_audio
from media_builder import generate_chapter_image, create_chapter_video, build_metadata_file, merge_videos_and_metadata

def main():
    parser = argparse.ArgumentParser(description="Convert an EPUB file into a narrated Audiobook MP4 file with chapters.")
    parser.add_argument("input_epub", help="Path to the input EPUB file (e.g. book.epub)")
    parser.add_argument("output_mp4", help="Path to the output MP4 file (e.g. audiobook.mp4)")
    parser.add_argument("--speed", default="+0%", help="Playback speed modifier (e.g., +10%, -20%)")
    parser.add_argument("--voice", default="en-US-AriaNeural", help="Edge-TTS Voice to use")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_epub):
        print(f"Error: Could not find '{args.input_epub}'")
        return
        
    print(f"Starting conversion for: {args.input_epub}")
    
    # 1. Parse EPUB
    print("Step 1/4: Parsing EPUB into chapters...")
    chapters = extract_chapters(args.input_epub)
    if not chapters:
        print("Error: No chapters found in the EPUB file.")
        return
    print(f"Found {len(chapters)} chapters. Proceeding to audio generation.")
    
    video_segments = []
    metadata_list = []
    
    # Use a system temporary directory to automatically handle complex cleanup
    with tempfile.TemporaryDirectory(prefix="epub_audiobook_") as temp_dir:
        # Loop through each chapter to build audio/video parts
        for i, chapter in enumerate(chapters):
            safe_title = "".join(c for c in chapter['title'] if c.isalnum() or c in (' ', '_')).rstrip()
            if not safe_title:
                safe_title = f"Chapter_{i+1}"
                
            print(f"  -> Processing: '{safe_title}' ({i+1}/{len(chapters)})")
            
            audio_path = os.path.join(temp_dir, f"chap_{i:03d}.mp3")
            image_path = os.path.join(temp_dir, f"chap_{i:03d}.png")
            video_path = os.path.join(temp_dir, f"chap_{i:03d}.mp4")
            
            # 2. TTS Generation
            print("     [a] Generating TTS AI Voice...")
            duration_ms = generate_chapter_audio(chapter['text'], audio_path, speed=args.speed, voice=args.voice)
            
            if duration_ms <= 0:
                print(f"     [!] Warning: Failed to generate audio for '{safe_title}'")
                continue
                
            # 3. Generating visual frame
            print("     [b] Building dynamic chapter visual...")
            generate_chapter_image(chapter['title'], image_path)
            
            # 4. Mix video/audio splice for chapter
            print("     [c] Combining AV Streams via ffmpeg...")
            success = create_chapter_video(image_path, audio_path, video_path, duration_ms)
            
            if success:
                video_segments.append(video_path)
                metadata_list.append({
                    "title": chapter['title'],
                    "duration": duration_ms
                })
        
        if not video_segments:
            print("Error: Failed to process any valid chapters.")
            return
            
        # 5. Build standard Chapters Metadata
        print("\nStep 3/4: Compiling chapter metadata...")
        meta_file = os.path.join(temp_dir, "ffmetadata.txt")
        build_metadata_file(metadata_list, output_meta_path=meta_file)
        
        # 6. Final concatenation
        print(f"\nStep 4/4: Exporting final audiobook: {args.output_mp4}")
        merge_success = merge_videos_and_metadata(video_segments, meta_file, args.output_mp4)
        
        if merge_success:
            print(f"\nSuccess! Audiobook saved to {args.output_mp4}.")
            print("You can view this file in VLC, QuickTime, or on a Phone to skip through chapters.")
        else:
            print("\nError encountered during final video assembly.")
            
if __name__ == "__main__":
    main()
