import argparse
import os
import shutil
import tempfile
from epub_parser import extract_chapters
from tts_engine import generate_chapter_audio, get_audio_duration_ms
from media_builder import build_metadata_file, merge_audio_and_metadata

def main():
    parser = argparse.ArgumentParser(description="Convert an EPUB file into a narrated MP3 audiobook with chapters.")
    parser.add_argument("input_epub", help="Path to the input EPUB file (e.g. book.epub)")
    parser.add_argument("output_path", nargs="?", default=None, help="Optional: Path to the output .mp3 file. If omitted, auto-generates in --output-dir.")
    parser.add_argument("--output-dir", default="outputs", help="Directory to save generated audiobooks (default: outputs)")
    parser.add_argument("--speed", default="+0%", help="Playback speed modifier (e.g., +10%%, -20%%)")
    parser.add_argument("--voice", default=None, help="Edge-TTS voice to use (e.g. en-US-AriaNeural, en-GB-RyanNeural)")
    parser.add_argument("--author", default="Michael Chen", help="Author string for audiobook metadata")
    parser.add_argument("--resume-dir", default=None, help="Directory containing previously generated temp files to resume from")
    parser.add_argument("--use-kokoro", action="store_true", help="Use local Kokoro-82M TTS instead of Edge-TTS")
    parser.add_argument("--kokoro-voice", default=None, help="Kokoro voice to use (e.g. af_heart, am_fenrir). Only used with --use-kokoro")
    parser.add_argument("--kokoro-lang", default="a", help="Kokoro language code (default: 'a' for American English)")

    args = parser.parse_args()

    # Generate output path if not explicitly provided
    if args.output_path is None:
        base_name = os.path.splitext(os.path.basename(args.input_epub))[0]
        safe_name = "".join(c for c in base_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
        os.makedirs(args.output_dir, exist_ok=True)
        args.output_path = os.path.join(args.output_dir, f"{safe_name}_audiobook.mp3")

    _, ext = os.path.splitext(args.output_path)
    if ext.lower() != ".mp3":
        print(f"Error: Output file must end with .mp3. (Got '{ext}')")
        return

    if not os.path.exists(args.input_epub):
        print(f"Error: Could not find '{args.input_epub}'")
        return

    print(f"Starting conversion for: {args.input_epub}")

    # 1. Parse EPUB
    print("Step 1/4: Parsing EPUB into chapters...")
    chapters = extract_chapters(args.input_epub)

    book_title = os.path.splitext(os.path.basename(args.input_epub))[0]
    book_author = args.author

    if not chapters:
        print("Error: No chapters found in the EPUB file.")
        return
    print(f"Found {len(chapters)} chapters for '{book_title}' by {book_author}. Proceeding to audio generation.")

    audio_segments = []
    metadata_list = []

    # Use existing dir if resuming, else create temp
    is_temp = False
    if args.resume_dir and os.path.exists(args.resume_dir):
        print(f"Resuming from existing directory: {args.resume_dir}")
        temp_dir = args.resume_dir

        # Auto-delete the last generated chapter to prevent corruption
        existing_files = sorted([f for f in os.listdir(temp_dir) if f.startswith("chap_") and f.endswith(".mp3")])
        if existing_files:
            last_file = os.path.join(temp_dir, existing_files[-1])
            print(f"Auto-deleting latest chapter file ({existing_files[-1]}) to prevent corruption...")
            os.remove(last_file)
    else:
        temp_dir = tempfile.mkdtemp(prefix="epub_audiobook_")
        is_temp = True

    try:
        # 2. Generate audio for each chapter
        print("Step 2/4: Generating chapter audio...")
        for i, chapter in enumerate(chapters):
            safe_title = "".join(c for c in chapter['title'] if c.isalnum() or c in (' ', '_')).rstrip()
            if not safe_title:
                safe_title = f"Chapter_{i+1}"

            print(f"  -> Processing: '{safe_title}' ({i+1}/{len(chapters)})")

            audio_path = os.path.join(temp_dir, f"chap_{i:03d}.mp3")

            # Resume: skip if segment already exists and is valid
            if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                print(f"     [-] Found existing file. Skipping generation for '{safe_title}'")
                duration_ms = get_audio_duration_ms(audio_path)
                audio_segments.append(audio_path)
                metadata_list.append({"title": chapter['title'], "duration": duration_ms})
                continue

            print("     [a] Generating TTS AI Voice...")
            duration_ms = generate_chapter_audio(
                chapter['text'], audio_path,
                speed=args.speed, voice=args.voice,
                use_kokoro=args.use_kokoro,
                kokoro_voice=args.kokoro_voice,
                kokoro_lang=args.kokoro_lang
            )
            if duration_ms <= 0:
                print(f"     [!] Warning: Failed to generate audio for '{safe_title}'")
                continue

            audio_segments.append(audio_path)
            metadata_list.append({"title": chapter['title'], "duration": duration_ms})

        if not audio_segments:
            print("Error: Failed to process any valid chapters.")
            return

        # 3. Build chapter metadata
        print("\nStep 3/4: Compiling chapter metadata...")
        meta_file = os.path.join(temp_dir, "ffmetadata.txt")
        build_metadata_file(metadata_list, book_title=book_title, book_author=book_author, output_meta_path=meta_file)

        # 4. Final concatenation
        print(f"\nStep 4/4: Exporting final audiobook: {args.output_path}")
        merge_success = merge_audio_and_metadata(audio_segments, meta_file, args.output_path)

        if merge_success:
            print(f"\nSuccess! Audiobook saved to {args.output_path}.")
            print("You can open this file in VLC, QuickTime, or on your Phone to skip through chapters.")
        else:
            print("\nError encountered during final audio assembly.")

    finally:
        if is_temp:
            print("Cleaning up temporary workspace...")
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

if __name__ == "__main__":
    main()
