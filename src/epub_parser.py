import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import warnings

# Suppress noisy ebooklib warnings about future versions
warnings.filterwarnings('ignore', category=UserWarning, module='ebooklib.epub')
warnings.filterwarnings('ignore', category=FutureWarning, module='ebooklib.epub')

def extract_chapters(epub_path):
    """
    Parses an EPUB file and extracts the text content chapter by chapter.
    Returns a list of dictionaries containing chapter titles and plain text.
    """
    try:
        book = epub.read_epub(epub_path)
    except FileNotFoundError:
        print(f"Error: EPUB file not found: {epub_path}")
        return []
    except Exception as e:
        print(f"Error reading EPUB file: {e}")
        return []

    chapters = []
    
    # Iterate through the items in the book
    for item in book.get_items():
        # Specifically look for document items (HTML/XHTML)
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            # Parse the HTML content using BeautifulSoup
            soup = BeautifulSoup(item.get_body_content(), 'html.parser')
            
            # Attempt to extract a title for the chapter
            # Look for h1, h2, or title tags as they usually denote chapter headings
            title = "Unknown Chapter"
            heading = soup.find(['h1', 'h2', 'title'])
            if heading and heading.text.strip():
                title = heading.text.strip()
                
            # Extract all text, separating paragraphs with newlines
            text = soup.get_text(separator='\n')
            
            # Clean up the text: strip excessive whitespace but keep paragraph structure
            cleaned_lines = [line.strip() for line in text.split('\n') if line.strip()]
            cleaned_text = '\n\n'.join(cleaned_lines)
            
            if cleaned_text:
                chapters.append({
                    "title": title,
                    "text": cleaned_text
                })
                
    # Basic deduplication or filtering of very short chapters (like frontmatter) can be added here if needed.
    return chapters

if __name__ == "__main__":
    # Simple test (requires an actual EPUB file to test against)
    import sys
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
        print(f"Parsing {test_file}...")
        extracted = extract_chapters(test_file)
        for i, chap in enumerate(extracted):
            print(f"\n--- Chapter {i+1}: {chap['title']} ---")
            print(chap['text'][:200] + "..." if len(chap['text']) > 200 else chap['text'])
            print(f"Total length: {len(chap['text'])} characters")
    else:
        print("Usage: python epub_parser.py <path_to_epub>")
