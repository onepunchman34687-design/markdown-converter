"""
image_describe.py
Extracts text from an image, ONCE, using local Tesseract OCR — no API key,
no per-call cost, runs entirely on this machine. The resulting text is what
makes images actually "readable" by text-only LLMs later, since the
markdown file will carry real text content instead of just width/height/
filesize.

Tradeoff vs. a vision-model description (worth knowing):
- Great for: screenshots, scanned documents, photos of notes/whiteboards,
  WhatsApp images of text, slides — anything where the content IS text.
- Useless for: photos of people/objects/scenes, charts/diagrams without
  text labels, anything where the meaning isn't in printed/written text.
  For those, this will simply find no text and return None — the markdown
  falls back to metadata-only, same as before this feature existed.

Design choices baked in here, on purpose:
- Resize/preprocess before OCR. Tesseract does better on images that
  aren't huge or noisy — light downscaling of very large images, grayscale
  conversion, and a reasonable max dimension all help accuracy/speed.
- Skip tiny images. Icons, logos, bullets, etc. aren't worth OCR —
  MIN_DESCRIBE_DIMENSION filters those out before any processing.
- Cache by content hash. Re-uploading the exact same image (same bytes)
  should never trigger a second OCR pass.
- Never raise. A failed OCR pass should degrade to "no description
  available," not break the conversion pipeline.
"""

import io
import hashlib
from PIL import Image
import pytesseract

MAX_LONG_EDGE = 2000          # cap before OCR — keeps runtime reasonable on big photos
MIN_DESCRIBE_DIMENSION = 64   # skip OCR on anything smaller than this (icons, bullets, etc.)
MIN_TEXT_LENGTH = 3           # OCR output shorter than this is treated as "no real text found"

# Simple in-memory cache: sha256(bytes) -> description text.
# Resets when the process restarts. Swap for a real cache/DB if you need
# this to persist across deploys or worker restarts.
_description_cache = {}


def _get_dimensions(image_bytes):
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.size  # (width, height)
    except Exception:
        return None, None


def _prepare_for_ocr(image_bytes, max_long_edge=MAX_LONG_EDGE):
    """
    Opens the image, downscales if it's larger than max_long_edge on its
    long edge (huge photos slow Tesseract down for no accuracy gain),
    and converts to grayscale, which generally helps OCR accuracy.
    Returns a PIL Image ready for pytesseract, or None if it can't be opened.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()

        width, height = img.size
        long_edge = max(width, height)
        if long_edge > max_long_edge:
            scale = max_long_edge / long_edge
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img = img.resize(new_size, Image.LANCZOS)

        if img.mode != "L":
            img = img.convert("L")  # grayscale tends to help Tesseract

        return img
    except Exception as e:
        print(f"OCR preprocessing failed: {e}")
        return None


def describe_image(image_bytes, content_type="image/png"):
    """
    Returns OCR'd text from the image, or None if:
    - the image is too small to bother with (likely an icon/bullet)
    - the image can't be opened
    - Tesseract found no meaningful text (e.g. it's a photo, not a document)

    `content_type` is accepted for call-site compatibility with the old
    vision-API version of this function, but isn't used here.
    """
    width, height = _get_dimensions(image_bytes)
    if width and height and max(width, height) < MIN_DESCRIBE_DIMENSION:
        return None  # too small to bother — likely an icon/bullet/spacer

    content_hash = hashlib.sha256(image_bytes).hexdigest()
    if content_hash in _description_cache:
        return _description_cache[content_hash]

    img = _prepare_for_ocr(image_bytes)
    if img is None:
        return None

    try:
        raw_text = pytesseract.image_to_string(img)
    except Exception as e:
        print(f"OCR failed: {e}")
        return None

    text = raw_text.strip()
    if len(text) < MIN_TEXT_LENGTH:
        return None  # nothing meaningful found — probably a photo, not text

    _description_cache[content_hash] = text
    return text