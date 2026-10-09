import os
import shutil
import tempfile
import time
import base64
import requests
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

MAX_OCR_PAGES = int(os.environ.get('MAX_OCR_PAGES', '30'))

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

CONTENT_TYPE_MAP = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'webp': 'image/webp'
}

# Job management
jobs = {}  # job_id -> job_info
jobs_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=4)  # Adjust based on server capacity
JOB_TIMEOUT_SECONDS = 30 * 60  # 30 minutes
RESULTS_DIR = os.path.join(tempfile.gettempdir(), 'markdown_converter_results')
os.makedirs(RESULTS_DIR, exist_ok=True)

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

def convert_pdf_fast(path):
    """Fast text-layer extraction with PyMuPDF. Returns '' if unavailable or the PDF looks scanned."""
    if pymupdf4llm is None:
        return ''
    try:
        import fitz
        with fitz.open(path) as doc:
            pages = max(len(doc), 1)
        text = pymupdf4llm.to_markdown(path)
    except Exception as e:
        print(f"Fast PDF path failed: {e}")
        return ''
    # Fewer than ~50 characters per page means there is no real text layer
    return text if len(text.strip()) / pages >= 50 else ''

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

def cleanup_job_files(job_id):
    """Clean up temporary files associated with a job."""
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            # Clean up input file
            input_path = job.get('input_path')
            if input_path and os.path.exists(input_path):
                try:
                    os.unlink(input_path)
                except OSError:
                    pass
            # Clean up output file
            output_path = job.get('output_path')
            if output_path and os.path.exists(output_path):
                try:
                    os.unlink(output_path)
                except OSError:
                    pass
            # Remove job from store after a delay? We'll keep it for a while for status checking
            # but we can remove old jobs periodically. For now, we leave it and clean on retrieval.

def process_conversion_job(job_id, file_path, job_type, **kwargs):
    """Background job to process a conversion."""
    try:
        with jobs_lock:
            if job_id not in jobs:
                return  # Job was removed
            jobs[job_id]['status'] = 'processing'
            jobs[job_id]['progress'] = 0
            jobs[job_id]['message'] = 'Starting conversion...'

        if job_type == 'file':
            # Process uploaded file
            suffix = os.path.splitext(file_path)[1] or '.tmp'
            describe = kwargs.get('describe', False)

            # Update progress
            with jobs_lock:
                jobs[job_id]['message'] = 'Detecting file type...'
                jobs[job_id]['progress'] = 10

            markdown_text = ''
            if suffix.lower() == '.pdf':
                markdown_text = convert_pdf_fast(file_path)
                if not markdown_text:
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
            with jobs_lock:
                jobs[job_id]['message'] = 'Extracting embedded images...'
                jobs[job_id]['progress'] = 30

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
                with jobs_lock:
                    jobs[job_id]['message'] = 'Adding image descriptions...'
                    jobs[job_id]['progress'] = 60
                markdown_text += "\n\n## Embedded Image Descriptions\n"
                for img in described:
                    markdown_text += f"\n**{img['filename']}**\n{img['description']}\n"

            # Finalize markdown
            with jobs_lock:
                jobs[job_id]['message'] = 'Generating final markdown...'
                jobs[job_id]['progress'] = 80

            # Save markdown to a temporary file for download
            output_filename = f"{job_id}_{secure_filename(kwargs.get('filename', 'converted.md'))}"
            output_path = os.path.join(RESULTS_DIR, output_filename)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown_text)

            with jobs_lock:
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['progress'] = 100
                jobs[job_id]['message'] = 'Conversion completed'
                jobs[job_id]['output_path'] = output_path
                jobs[job_id]['images'] = images_payload  # For immediate use if needed
                jobs[job_id]['markdown_content'] = markdown_text  # Store for small results

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
            with jobs_lock:
                jobs[job_id]['message'] = 'Building markdown output...'
                jobs[job_id]['progress'] = 80
            markdown_output = (
                f"Image Information:\n"
                f"- Filename: {kwargs.get('original_filename', 'image')}\n"
                f"- {metadata}"
            )
            if ocr_text:
                markdown_output += f"\n\n## Extracted Text\n{ocr_text}"
            if description:
                markdown_output += f"\n\n## Description\n{description}"

            # Save markdown to file
            output_filename = f"{job_id}_{secure_filename(kwargs.get('filename', 'converted.md'))}"
            output_path = os.path.join(RESULTS_DIR, output_filename)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown_output)

            images_payload = [{
                'filename': kwargs.get('original_filename', 'image'),
                'content_type': content_type,
                'data': base64.b64encode(image_bytes).decode('utf-8')
            }]

            with jobs_lock:
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['progress'] = 100
                jobs[job_id]['message'] = 'Conversion completed'
                jobs[job_id]['output_path'] = output_path
                jobs[job_id]['images'] = images_payload
                jobs[job_id]['markdown_content'] = markdown_output


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
                with jobs_lock:
                    jobs[job_id]['message'] = 'Fetching YouTube transcript...'
                    jobs[job_id]['progress'] = 30
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
            with jobs_lock:
                jobs[job_id]['message'] = 'Fetching video metadata...'
                jobs[job_id]['progress'] = 60
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

            with jobs_lock:
                jobs[job_id]['message'] = 'Generating final markdown...'
                jobs[job_id]['progress'] = 80

            # Save markdown to file
            output_filename = f"{job_id}_{secure_filename('converted.md')}"
            output_path = os.path.join(RESULTS_DIR, output_filename)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown_text)

            with jobs_lock:
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['progress'] = 100
                jobs[job_id]['message'] = 'Conversion completed'
                jobs[job_id]['output_path'] = output_path
                jobs[job_id]['markdown_content'] = markdown_text

        else:
            raise ValueError(f"Unknown job type: {job_type}")

    except Exception as e:
        print(f"Job {job_id} failed: {e}")
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['progress'] = 0
                jobs[job_id]['message'] = f"Conversion failed: {str(e)}"
                jobs[job_id]['error'] = str(e)
        # Clean up input file on failure
        cleanup_job_files(job_id)
    finally:
        # Note: We do not clean up the output file here because the user may want to download it.
        # We'll clean up old files periodically or when the job is retrieved after completion.
        pass

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/convert-file', methods=['POST'])
def convert_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Generate job ID
    job_id = str(uuid.uuid4())

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

    # Create job entry
    with jobs_lock:
        jobs[job_id] = {
            'id': job_id,
            'status': 'queued',
            'progress': 0,
            'message': 'File uploaded, queued for conversion',
            'input_path': input_path,
            'job_type': 'file',
            'filename': file.filename,
            'describe': request.form.get('describe') == '1',
            'created_at': time.time()
        }

    # Submit job to thread pool
    executor.submit(process_conversion_job, job_id, input_path, 'file',
                    filename=file.filename, describe=request.form.get('describe') == '1')

    return jsonify({'job_id': job_id, 'status': 'queued'})

@app.route('/api/convert-image', methods=['POST'])
def convert_image():
    if 'file' not in request.files:
        return jsonify({'error': 'No image file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No image file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid image file type.'}), 400

    # Generate job ID
    job_id = str(uuid.uuid4())

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

    # Create job entry
    with jobs_lock:
        jobs[job_id] = {
            'id': job_id,
            'status': 'queued',
            'progress': 0,
            'message': 'Image uploaded, queued for conversion',
            'input_path': input_path,
            'job_type': 'image',
            'original_filename': file.filename,
            'created_at': time.time()
        }

    # Submit job to thread pool
    executor.submit(process_conversion_job, job_id, input_path, 'image',
                    original_filename=file.filename)

    return jsonify({'job_id': job_id, 'status': 'queued'})


@app.route('/api/convert-video-url', methods=['POST'])
def convert_video_url():
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'No video URL provided'}), 400

    # Generate job ID
    job_id = str(uuid.uuid4())

    # Create job entry
    with jobs_lock:
        jobs[job_id] = {
            'id': job_id,
            'status': 'queued',
            'progress': 0,
            'message': 'Video URL received, queued for conversion',
            'job_type': 'video',
            'url': url,
            'created_at': time.time()
        }

    # Submit job to thread pool
    executor.submit(process_conversion_job, job_id, None, 'video', url=url)

    return jsonify({'job_id': job_id, 'status': 'queued'})


@app.route('/api/job/<job_id>', methods=['GET'])
def get_job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404

        # Prepare response (excluding internal fields like input_path)
        response = {
            'job_id': job['id'],
            'status': job['status'],
            'progress': job['progress'],
            'message': job['message']
        }
        if job['status'] == 'completed':
            # Provide download URL
            response['download_url'] = f'/api/job/{job_id}/download'
            response['filename'] = os.path.basename(job['output_path']) if job.get('output_path') else 'converted.md'
        elif job['status'] == 'failed':
            response['error'] = job.get('error', 'Unknown error')

        return jsonify(response)

@app.route('/api/job/<job_id>/download', methods=['GET'])
def download_job_result(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404

        if job['status'] != 'completed':
            return jsonify({'error': 'Job not completed yet'}), 400

        output_path = job.get('output_path')
        if not output_path or not os.path.exists(output_path):
            return jsonify({'error': 'Result file not found'}), 404

        # Determine the filename to use for download
        download_filename = job.get('filename', 'converted.md')
        if not download_filename.endswith('.md'):
            download_filename += '.md'

        # Send the file
        try:
            return send_file(output_path, as_attachment=True, download_name=download_filename)
        except Exception as e:
            return jsonify({'error': f'Failed to send file: {str(e)}'}), 500

# Helper function to send file (since we don't have Flask's send_file imported yet)
from flask import send_file

# Periodic cleanup of old jobs and files (call this from a background thread or on request)
def cleanup_old_jobs():
    """Remove jobs older than JOB_TIMEOUT_SECONDS and clean up their files."""
    current_time = time.time()
    with jobs_lock:
        job_ids_to_remove = []
        for job_id, job in jobs.items():
            if current_time - job.get('created_at', 0) > JOB_TIMEOUT_SECONDS:
                job_ids_to_remove.append(job_id)
        for job_id in job_ids_to_remove:
            job = jobs.pop(job_id, None)
            if job:
                cleanup_job_files(job_id)

# We'll call cleanup occasionally - for now, we'll do it on status request for simplicity
# In production, you might want a separate background thread for this.

if __name__ == '__main__':
    app.run(debug=True)