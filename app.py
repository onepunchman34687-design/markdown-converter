import os
import tempfile
import time
import base64
import requests
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound
import yt_dlp
from flask import Flask, request, jsonify, render_template
from markitdown import MarkItDown
from markitdown_ocr import register_converters
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from PIL import Image
from image_extract import extract_images
from image_describe import describe_image

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB limit
md = MarkItDown()
register_converters(md)

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

CONTENT_TYPE_MAP = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'webp': 'image/webp'
}

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

    suffix = os.path.splitext(file.filename)[1] or '.tmp'

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file.save(tmp.name)
            result = md.convert(tmp.name)
            # Pull any embedded pictures out of PDFs / DOCX / PPTX as real image bytes
            # (no OCR, no LLM — just reading the file's internal structure directly)
            embedded_images = extract_images(tmp.name, suffix)
        os.unlink(tmp.name)

        images_payload = [
            {
                'filename': img['filename'],
                'content_type': img['content_type'],
                'data': base64.b64encode(img['data']).decode('utf-8'),
                'description': img.get('description')
            }
            for img in embedded_images
        ]

        markdown_text = result.text_content
        described = [img for img in embedded_images if img.get('description')]
        if described:
            markdown_text += "\n\n## Embedded Image Descriptions\n"
            for img in described:
                markdown_text += f"\n**{img['filename']}**\n{img['description']}\n"

        return jsonify({'markdown': markdown_text, 'images': images_payload})
    except Exception as e:
        return jsonify({'error': f'Conversion failed: {str(e)}'}), 500

@app.route('/api/convert-image', methods=['POST'])
def convert_image():
    if 'file' not in request.files:
        return jsonify({'error': 'No image file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No image file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid image file type.'}), 400

    original_filename = secure_filename(file.filename)
    suffix = os.path.splitext(original_filename)[1] or '.tmp'

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file.save(tmp.name)
            image_path = tmp.name

        metadata = get_image_metadata(image_path)

        with open(image_path, 'rb') as f:
            image_bytes = f.read()

        os.unlink(image_path)

        ext = suffix.lstrip('.').lower()
        content_type = CONTENT_TYPE_MAP.get(ext, 'image/png')

        # Describe the image ONCE here so the markdown is self-contained —
        # a text-only LLM reading the pushed .md later gets real content,
        # not just dimensions. Returns None if too small / no API key / failed,
        # in which case we fall back to metadata-only (old behavior).
        description = describe_image(image_bytes, content_type)

        # The markdown preview just shows metadata for now — the actual
        # ![image](url) embed gets stitched in once the real image is
        # pushed to GitHub and we know its real raw URL (see /api/push-to-github).
        markdown_output = (
            f"Image Information:\n"
            f"- Filename: {original_filename}\n"
            f"- {metadata}"
        )
        if description:
            markdown_output += f"\n\n## Description\n{description}"

        images_payload = [{
            'filename': original_filename,
            'content_type': content_type,
            'data': base64.b64encode(image_bytes).decode('utf-8')
        }]

        return jsonify({'markdown': markdown_output, 'images': images_payload})

    except Exception as e:
        return jsonify({'error': f'Image conversion failed: {str(e)}'}), 500

@app.route('/api/convert-url', methods=['POST'])
def convert_url():
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        result = md.convert(url)
        return jsonify({'markdown': result.text_content})
    except Exception as e:
        return jsonify({'error': f'URL conversion failed: {str(e)}'}), 500

@app.route('/api/convert-video-url', methods=['POST'])
def convert_video_url():
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'No video URL provided'}), 400

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
            return jsonify({'markdown': markdown_transcript})
        except NoTranscriptFound:
            pass
        except Exception as e:
            print(f"YouTube transcript API failed for {url}: {e}")
            pass

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

            return jsonify({'markdown': markdown_metadata})

    except Exception as e:
        return jsonify({'error': f'Video processing failed: {str(e)}'}), 500

@app.route('/api/convert-text', methods=['POST'])
def convert_text():
    data = request.get_json()
    text = data.get('text', '').strip()

    if not text:
        return jsonify({'error': 'No text provided'}), 400

    return jsonify({'markdown': text})

@app.route('/api/push-to-github', methods=['POST'])
def push_to_github():
    try:
        data = request.get_json()
        markdown_content = data.get('markdown', '')
        filename = data.get('filename', 'converted.md').strip()
        images = data.get('images', [])  # [{filename, content_type, data(base64)}, ...]

        if not markdown_content and not images:
            return jsonify({'error': 'No content to push'}), 400

        github_token = os.environ.get('GITHUB_TOKEN')
        github_repo = os.environ.get('GITHUB_REPO')
        github_branch = os.environ.get('GITHUB_BRANCH', 'main')
        github_folder = os.environ.get('GITHUB_FOLDER', 'converted')

        if not github_token or not github_repo:
            return jsonify({'error': 'GitHub is not configured on the server. Set GITHUB_TOKEN and GITHUB_REPO.'}), 500

        safe_filename = secure_filename(filename)
        if not safe_filename:
            safe_filename = 'converted.md'
        if not safe_filename.endswith('.md'):
            safe_filename += '.md'

        timestamp = int(time.time())
        timestamped_filename = f"{timestamp}_{safe_filename}"

        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

        # Push real image bytes first, collect their real GitHub raw URLs
        pushed_images = []
        for img in images:
            img_filename = secure_filename(img.get('filename') or 'image.png')
            img_folder = f"{github_folder}/{timestamp}_images" if github_folder else f"{timestamp}_images"
            img_path = f"{img_folder}/{img_filename}"
            img_api_url = f"https://api.github.com/repos/{github_repo}/contents/{img_path}"

            img_payload = {
                "message": f"Add image: {img_filename}",
                "content": img.get('data', ''),  # already base64-encoded by the client
                "branch": github_branch
            }

            img_response = requests.put(img_api_url, json=img_payload, headers=headers, timeout=15)
            if img_response.status_code in (200, 201):
                pushed_images.append({
                    'filename': img_filename,
                    'raw_url': img_response.json()['content']['download_url']
                })
            # A single failed image push shouldn't kill the whole request — skip and continue

        if pushed_images:
            if len(pushed_images) == 1:
                # Standalone image upload — embed the real image right above its metadata
                img = pushed_images[0]
                markdown_content = f"![{img['filename']}]({img['raw_url']})\n\n{markdown_content}"
            else:
                # Multiple images extracted from a document — gallery at the bottom
                markdown_content += "\n\n## Extracted Images\n\n"
                for img in pushed_images:
                    markdown_content += f"![{img['filename']}]({img['raw_url']})\n\n"

        path = f"{github_folder}/{timestamped_filename}" if github_folder else timestamped_filename
        api_url = f"https://api.github.com/repos/{github_repo}/contents/{path}"

        content_encoded = base64.b64encode(markdown_content.encode('utf-8')).decode('utf-8')

        payload = {
            "message": f"Add converted markdown: {timestamped_filename}",
            "content": content_encoded,
            "branch": github_branch
        }

        response = requests.put(api_url, json=payload, headers=headers, timeout=15)

        if response.status_code in (200, 201):
            result = response.json()
            return jsonify({
                'success': True,
                'html_url': result['content']['html_url'],
                'raw_url': result['content']['download_url'],
                'images_pushed': len(pushed_images)
            })
        else:
            error_detail = response.json().get('message', 'Unknown GitHub API error')
            return jsonify({'error': f'GitHub API error: {error_detail}'}), response.status_code

    except Exception as e:
        return jsonify({'error': f'Push failed: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True)