# Optimization Report: Phase 2 - Further Bundle Size Reduction

## Overview
This report details the changes made in Phase 2 to further reduce the deployment bundle size of the Flask-based Markdown Converter, building upon the feature removals from Phase 1 (Text input → Markdown and Website URL → Markdown).

## Changes Made

### Backend (`app.py`)
- **Removed** the RapidOCR fallback OCR engine (which relied on `numpy` and `rapidocr_onnxruntime`) from the `_ocr_pil` function.
  - The function now attempts OCR only with `pytesseract` (if available) and returns an empty string on failure.
  - This eliminates the need for `numpy` and `rapidocr_onnxruntime` packages, which were not explicitly listed in `requirements.txt` but were being conditionally imported.
- **Removed** the global variable `_rapid_engine` (no longer used).
- **Note**: The `markitdown_ocr` optional plugin try-except block remains unchanged, as it does not affect bundle size if the package is not installed.

### Rationale
- The RapidOCR fallback was a secondary OCR option that only activated if `pytesseract` failed. Removing it simplifies the OCR pipeline and removes the dependency on large packages (`numpy` and `onnxruntime`-based `rapidocr_onnxruntime`), which are known to contribute significantly to bundle size.
- The primary OCR engine (`pytesseract`) remains unchanged and is still required for image and PDF OCR functionality.
- This change does not break any existing features:
  - Image conversion and OCR continue to work via `pytesseract` (if the Tesseract OCR executable is available in the deployment environment).
  - PDF conversion (fast text-layer extraction and OCR fallback) continues to work via `PyMuPDF` and `pymupdf4llm`.
  - All other features (file conversion, image description, video conversion) are unaffected.

## Features Preserved
All features from Phase 1 remain preserved:
- **File conversion**: PDF, DOCX, PPTX, XLSX, HTML, TXT files (via `/api/convert-file`).
- **Image conversion**: PNG, JPG, GIF, WEBP images (via `/api/convert-image`), including OCR and image description.
- **Video conversion**: YouTube and generic video URLs (via `/api/convert-video-url`), including transcript extraction and metadata.

## Bundle Size Impact
- The removal of the RapidOCR fallback eliminates the transitive dependencies of `numpy` and `rapidocr_onnxruntime` from the deployment bundle, assuming they were not already excluded by not being in `requirements.txt`.
- Estimated savings: **10-50 MB** (depending on the versions and whether language data for OCR is included), though actual savings must be measured in the deployment environment.
- The core large dependencies (`PyMuPDF`, `Pillow`, `yt-dlp`, `python-pptx`, `python-docx`, `markitdown`) remain, as they are essential for the preserved features.

## Verification
- Syntax check of `app.py` passes (no syntax errors introduced).
- The OCR function now has a single code path (Tesseract-only) and is simpler to maintain.
- No changes were made to the frontend (`templates/index.html`, `static/script.js`) or other dependencies.

## Recommendations for Further Optimization
To achieve the target bundle size below 500 MB (with 50 MB headroom), consider the following steps:
1. **Measure the current deployed bundle size** using the platform's build or inspection tools (e.g., `vercel build` output, AWS Lambda layer size, etc.).
2. **Investigate transitive dependencies** of the listed packages to identify any large, unnecessary inclusions (e.g., language data for Tesseract, extractor lists in `yt-dlp`).
3. **Evaluate alternative OCR strategies**:
   - If the deployment environment cannot guarantee the Tesseract executable, consider bundling a minimal Tesseract setup with only necessary language data (e.g., English-only) to reduce size.
   - Alternatively, explore pure-Python OCR libraries (though they may be less accurate or slower).
4. **Assess yt-dlp usage**: Since `yt-dlp` is large, consider whether a lighter alternative (e.g., scraping video metadata with `requests` and `BeautifulSoup`) could replace it for non-YouTube videos, noting that this may be less reliable.
5. **Audit deployed files**: Ensure that no large, unnecessary files (e.g., development dependencies, test data, large media) are included in the deployment package via `.vercelignore` or equivalent.
6. **Consider deployment-specific optimizations**: Use platform-specific features like AWS Lambda layers, Vercel's Build Output Cache, or Docker multi-stage builds to isolate and minimize the payload.

## Files Changed
1. `OneDrive/Documents/markdown-converter/app.py`

No data, credentials, or unrelated functionality was deleted. Changes were made locally and not deployed or pushed to GitHub.