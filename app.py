import os
import shutil
import tempfile
import time
import base64
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound
import yt_dlp
from flask import Flask, request, jsonify, render_template, send_file
from markitdown import MarkItDown
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from PIL import Image, ImageOps
from image_extract import extract_images
from image_describe import describe_image

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB limit
md = MarkItDown()
# Windows: find Tesseract even if it is not on PATH
_WIN_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if pytesseract and not shutil.which("tesseract") and os.path.exists(_WIN_TESSERACT):
    pytesseract.pytesseract.tesseract_cmd = _WIN_TESSERACT

# Error handlers to return JSON instead of HTML
@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File is too large. Maximum size is 50 MB.'}), 413

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Resource not found.'}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error.'}), 500

MAX_OCR_PAGES = int(os.environ.get('MAX_OCR_PAGES', '30'))

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

CONTENT_TYPE_MAP = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'webp': 'image/webp'
}

@app.route('/')
def index():
    return render_template('index.html')

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

def get_image_metadata(file_path):
    try:
        with Image.open(file_path) as img:
            width, height = img.size
            file_size = os.path.getsize(file_path)
            file_type = img.format
            return f"Width: {width}px, Height: {height}px, Size: {file_size} bytes, Type: {file_type}"
    except Exception as e:
        return f"Could not get image metadata: {str(e)}"

# ---------------------------------------------------------------- OCR helpers

def _ocr_pil(img):
    """OCR a PIL image. Uses Tesseract if available, otherwise returns empty string."""
    img = ImageOps.exif_transpose(img)
    img = ImageOps.grayscale(img)
    if img.width < 1500:
        img = img.resize((img.width * 2, img.height * 2))

    if pytesseract:
        try:
            return pytesseract.image_to_string(img, config="--psm 6").strip()
        except Exception as e:
            print(f"Tesseract OCR failed: {e}")
            return ""
    return ""

def ocr_image(path):
    try:
        with Image.open(path) as img:
            return _ocr_pil(img.convert("RGB"))
    except Exception as e:
        print(f"OCR failed for {path}: {e}")
        return ""

# ---------------------------------------------------------------- PDF helpers


def ocr_pdf(path):
    """OCR a scanned PDF page by page (capped by MAX_OCR_PAGES). Returns '' if OCR is unavailable."""
    try:
        import fitz
        parts = []
        with fitz.open(path) as doc:
            total = len(doc)
            for i in range(min(total, MAX_OCR_PAGES)):
                pix = doc[i].get_pixmap(dpi=200)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                text = _ocr_pil(img)
                if text:
                    parts.append(f"## Page {i + 1}\n\n{text}")
            if not parts:
                return ''
            if total > MAX_OCR_PAGES:
                parts.append(f"_OCR stopped after {MAX_OCR_PAGES} of {total} pages._")
        return "\n\n".join(parts)
    except Exception as e:
        print(f"PDF OCR failed: {e}")
        return ''

def perform_conversion(file_path, job_type, **kwargs):
    """Perform conversion and return (markdown_text, images_payload)."""
    if job_type == 'file':
        # Process uploaded file
        suffix = os.path.splitext(file_path)[1] or '.tmp'
        describe = kwargs.get('describe', False)

        markdown_text = ''
        if suffix.lower() == '.pdf':
            markdown_text = ''
            try:
                import fitz
                with fitz.open(file_path) as doc:
                    if len(doc) == 0:
                        markdown_text = ''
                    else:
                        text_parts = []
                        total_chars = 0
                        for page_num in range(len(doc)):
                            page = doc.load_page(page_num)
                            text = page.get_text()
                            text_parts.append(text)
                            total_chars += len(text)
                        if total_chars / len(doc) >= 50:
                            markdown_text = '\n\n'.join(text_parts)
                        else:
                            markdown_text = ocr_pdf(file_path)
            except Exception as e:
                print(f"PDF text extraction failed: {e}")
                markdown_text = ocr_pdf(file_path)
        if not markdown_text:
            # For other file types, use markitdown
            try:
                result = md.convert(file_path)
                markdown_text = result.text_content
            except Exception as e:
                print(f"Markitdown conversion failed: {e}")
                markdown_text = ''

        # Extract embedded images
        embedded_images = extract_images(file_path, suffix, describe=describe)

        images_payload = []
        described = []
        for img in embedded_images:
            img_data = img['data']
            img_filename = img['filename']
            content_type = img['content_type']
            # Encode image data to base64 for transmission
            img_base64 = base64.b64encode(img_data).decode('utf-8')
            images_payload.append({
                'filename': img_filename,
                'content_type': content_type,
                'data': img_base64,
                'description': img.get('description')
            })
            if img.get('description'):
                described.append(img)

        # Add image descriptions to markdown if any
        if described:
            markdown_text += "\n\n## Embedded Image Descriptions\n"
            for img in described:
                markdown_text += f"\n**{img['filename']}**\n{img['description']}\n"

        return markdown_text, images_payload

    elif job_type == 'image':
        # Process single image
        metadata = get_image_metadata(file_path)
        ocr_text = ocr_image(file_path)

        with open(file_path, 'rb') as f:
            image_bytes = f.read()

        ext = os.path.splitext(file_path)[1].lstrip('.').lower()
        content_type = CONTENT_TYPE_MAP.get(ext, 'image/png')

        # Describe the image using markitdown (which may use vision LLM) or local OCR
        description = None
        try:
            # Try markitdown first (which may use vision LLM via plugin)
            result = md.convert(file_path)
            markdown_from_markitdown = result.text_content.strip()
            if markdown_from_markitdown:
                # Use markitdown's output as description
                description = markdown_from_markitdown
            else:
                # Fallback to local describe_image
                description = describe_image(image_bytes, content_type)
        except Exception as e:
            print(f"Markitdown conversion failed: {e}")
            description = describe_image(image_bytes, content_type)

        # Build markdown output
        markdown_output = (
            f"Image Information:\n"
            f"- Filename: {kwargs.get('original_filename', 'image')}\n"
            f"- {metadata}"
        )
        if ocr_text:
            markdown_output += f"\n\n## Extracted Text\n{ocr_text}"
        if description:
            markdown_output += f"\n\n## Description\n{description}"

        images_payload = [{
            'filename': kwargs.get('original_filename', 'image'),
            'content_type': content_type,
            'data': base64.b64encode(image_bytes).decode('utf-8')
        }]

        return markdown_output, images_payload

    elif job_type == 'video':
        # Process video URL (YouTube or generic)
        url = kwargs.get('url')
        video_id = None
        if 'youtube.com/watch?v=' in url or 'youtu.be/' in url:
            try:
                from urllib.parse import urlparse, parse_qs
                parsed_url = urlparse(url)
                if 'youtube.com' in parsed_url.netloc:
                    video_id = parse_qs(parsed_url.query).get('v', [None])[0]
                elif 'youtu.be' in parsed_url.netloc:
                    video_id = parsed_url.path[1:]
            except Exception:
                pass

        if video_id:
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                transcript = transcript_list.find_generated_transcript(['en', 'a.en']).fetch()

                markdown_transcript = "## Video Transcript\n"
                markdown_transcript += f"**Source:** [{url}]({url})\n\n"
                for entry in transcript:
                    start_time = int(entry['start'])
                    minutes = start_time // 60
                    seconds = start_time % 60
                    timestamp = f"{minutes:02d}:{seconds:02d}"
                    markdown_transcript += f"[{timestamp}] {entry['text']}\n"
                markdown_text = markdown_transcript
            except NoTranscriptFound:
                pass  # Fallback to yt-dlp
            except Exception as e:
                print(f"YouTube transcript API failed for {url}: {e}")
                pass  # Fallback to yt-dlp

        # Fallback to yt-dlp for metadata
        try:
            ydl_opts = {
                'quiet': True,
                'skip_download': True,
                'format': 'bestaudio/best',
                'extract_flat': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                title = info.get('title', 'N/A')
                description = info.get('description', 'N/A')
                uploader = info.get('uploader', 'N/A')
                duration = info.get('duration', 0)
                view_count = info.get('view_count', 0)
                upload_date = info.get('upload_date', 'N/A')
                webpage_url = info.get('webpage_url', url)

                duration_minutes = duration // 60
                duration_seconds = duration % 60

                markdown_metadata = "## Video Information\n"
                markdown_metadata += f"**Title:** {title}\n"
                markdown_metadata += f"**Source URL:** [{webpage_url}]({webpage_url})\n"
                markdown_metadata += f"**Uploader:** {uploader}\n"
                markdown_metadata += f"**Duration:** {duration_minutes:02d}:{duration_seconds:02d}\n"
                markdown_metadata += f"**Views:** {view_count:,}\n"
                markdown_metadata += f"**Upload Date:** {upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}\n"
                markdown_metadata += f"\n### Description\n{description}\n"
                markdown_text = markdown_metadata
        except Exception as e:
            print(f"Video processing failed: {e}")
            raise

        return markdown_text, []

    else:
        raise ValueError(f"Unknown job type: {job_type}")

@app.route('/api/convert-file', methods=['POST'])
def convert_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Save uploaded file to a temporary location
    suffix = os.path.splitext(file.filename)[1] or '.tmp'
    fd, input_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        file.save(input_path)
    except Exception as e:
        try:
            os.unlink(input_path)
        except OSError:
            pass
        return jsonify({'error': f'Failed to save uploaded file: {str(e)}'}), 500

    try:
        # Perform conversion
        markdown_text, images_payload = perform_conversion(
            input_path, 'file',
            filename=file.filename,
            describe=request.form.get('describe') == '1'
        )
    except Exception as e:
        print(f"Conversion failed: {e}")
        try:
            os.unlink(input_path)
        except OSError:
            pass
        return jsonify({'error': f'Conversion failed: {str(e)}'}), 500
    finally:
        # Clean up the input file
        try:
            os.unlink(input_path)
        except OSError:
            pass

    # Return the result directly
    return jsonify({
        'markdown': markdown_text,
        'images': images_payload
    })

@app.route('/api/convert-image', methods=['POST'])
def convert_image():
    if 'file' not in request.files:
        return jsonify({'error': 'No image file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No image file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid image file type.'}), 400

    # Save uploaded file to a temporary location
    suffix = os.path.splitext(file.filename)[1] or '.tmp'
    fd, input_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        file.save(input_path)
    except Exception as e:
        try:
            os.unlink(input_path)
        except OSError:
            pass
        return jsonify({'error': f'Failed to save uploaded file: {str(e)}'}), 500

    try:
        # Perform conversion
        markdown_text, images_payload = perform_conversion(
            input_path, 'image',
            original_filename=file.filename
        )
    except Exception as e:
        print(f"Conversion failed: {e}")
        try:
            os.unlink(input_path)
        except OSError:
            pass
        return jsonify({'error': f'Conversion failed: {str(e)}'}), 500
    finally:
        # Clean up the input file
        try:
            os.unlink(input_path)
        except OSError:
            pass

    # Return the result directly
    return jsonify({
        'markdown': markdown_text,
        'images': images_payload
    })

@app.route('/api/convert-video-url', methods=['POST'])
def convert_video_url():
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'No video URL provided'}), 400

    try:
        # Perform conversion
        markdown_text, images_payload = perform_conversion(
            None, 'video',  # file_path is not used for video
            url=url
        )
    except Exception as e:
        print(f"Conversion failed: {e}")
        return jsonify({'error': f'Conversion failed: {str(e)}'}), 500

    # Return the result directly
    return jsonify({
        'markdown': markdown_text,
        'images': images_payload
    })

# We keep the job status and download endpoints for compatibility, but they will return errors
# since we are no longer using jobs. Alternatively, we could remove them, but let's keep them
# to avoid breaking any potential frontend that might still try to use them (though our frontend won't).
@app.route('/api/job/<job_id>', methods=['GET'])
def get_job_status(job_id):
    return jsonify({'error': 'This endpoint is no longer used'}), 410

@app.route('/api/job/<job_id>/download', methods=['GET'])
def download_job_result(job_id):
    return jsonify({'error': 'This endpoint is no longer used'}), 410

if __name__ == '__main__':
    app.run(debug=True)