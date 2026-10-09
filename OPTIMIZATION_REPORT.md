# Optimization Report: Flask-based Markdown Converter

## Overview
This report details the changes made to remove two features from the Flask-based Markdown Converter to reduce the deployment bundle size:
1. Text input â†’ Markdown
2. Website URL â†’ Markdown

The goal was to bring the deployment bundle below the 500 MB limit.

## Changes Made

### Backend (`app.py`)
- **Removed** the `/api/convert-text` endpoint and its associated function (`convert_text`).
- **Removed** the `/api/convert-url` endpoint and its associated function (`convert_url`).
- **Removed** the `job_type == 'url'` block in the `process_conversion_job` background job function.

### Frontend (`templates/index.html`)
- **Updated landing pills**: Removed "URLs" and "Plain text" pills; retained "YouTube" pill.
- **Modified URL slide (slide 2)**:
  - Changed hint to: "Paste a YouTube or video URL"
  - Changed placeholder to: "https://www.youtube.com/watch?v=â€¦"
  - Changed button text to: "Convert Video URL"
- **Removed text slide (slide 3)** entirely.
- **Updated dot navigation**: Reduced from four dots to three dots to match the three remaining slides.

### Frontend (`static/script.js`)
- **Updated `SLIDES` array**:
  ```javascript
  const SLIDES = ['Upload a non-image file', 'Upload an image', 'Convert YouTube URL'];
  ```
- **Removed** `textInput` and `convertTextBtn` from the ELEMENTS section.
- **Removed** event listener for `convertTextBtn`.
- **Updated** `clearBtn` event listener to no longer clear `textInput` (since the element was removed).
- The dot navigation in the HTML was updated to three dots, and the script uses the live DOM collection, so it adapts automatically.

## Features Removed
1. **Text input â†’ Markdown**: Users can no longer paste raw text to be converted to markdown (though note that the conversion was a passthrough with no actual markdown transformation).
2. **Website URL â†’ Markdown**: Users can no longer paste arbitrary website URLs (e.g., Wikipedia articles) to be converted to markdown.

## Features Preserved
- **File conversion**: PDF, DOCX, PPTX, XLSX, HTML, TXT files (via `/api/convert-file`).
- **Image conversion**: PNG, JPG, GIF, WEBP images (via `/api/convert-image`), including OCR and image description.
- **Video conversion**: YouTube and generic video URLs (via `/api/convert-video-url`), including transcript extraction and metadata.

## Dependencies

The following dependency was removed from `requirements.txt`:

* `markitdown-ocr` â€” removed to reduce the dependency footprint.

The following dependencies remain:

* Flask, Werkzeug, and gunicorn for the web server.
* `markitdown` for file conversion.
* `python-pptx`, `python-docx`, PyMuPDF, and Pillow for document and image handling.
* `pytesseract` for OCR integration.
* `pymupdf4llm` for PDF-to-Markdown conversion.
* `youtube-transcript-api` and `yt-dlp` for video processing.
* `requests` and `python-dotenv` for HTTP requests and environment configuration.

The RapidOCR fallback was also removed from `app.py`. Image OCR now relies on Tesseract when available. Because `pytesseract` is only a Python wrapper, OCR may not work in environments where the Tesseract executable is unavailable.

## Bundle Size Impact

The changes reduce functionality and remove the `markitdown-ocr` dependency and RapidOCR fallback. However, the actual reduction in deployment bundle size has not yet been measured.

The original deployment bundle was reported to exceed the 500 MB limit. Further investigation is needed to identify the dependencies or build artifacts responsible.

Next steps:

* Inspect the latest Vercel build logs for the actual bundle-size error.
* Verify which dependencies and files are included in the deployment.
* Measure the deployment after these changes.
* Avoid removing additional dependencies until their impact on the bundle size and application functionality is understood.

## Verification
The changes were made to ensure that:
- The remaining endpoints (`/api/convert-file`, `/api/convert-image`, `/api/convert-video-url`) are intact and functional.
- The removed endpoints (`/api/convert-text`, `/api/convert-url`) now return 404 errors.
- The UI correctly reflects the three-slide workflow (File, Image, YouTube Video).
- All styling and interactive elements (theme toggle, carousel navigation, clear button, etc.) continue to work as expected.

## Files Changed
1. `OneDrive/Documents/markdown-converter/app.py`
2. `OneDrive/Documents/markdown-converter/templates/index.html`
3. `OneDrive/Documents/markdown-converter/static/script.js`

No data, credentials, or unrelated functionality was deleted. Changes were made locally and not deployed or pushed to GitHub.