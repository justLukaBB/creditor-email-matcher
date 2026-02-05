---
phase: 03-multi-format-document-extraction
plan: 02
subsystem: extraction
tags: [gcs, pymupdf, pdf, docx, xlsx, storage, file-detection]

# Dependency graph
requires:
  - phase: 02-async-job-queue-infrastructure
    provides: Dramatiq actors for async processing
provides:
  - GCS attachment download with automatic temp file cleanup
  - File format detection from extension and MIME type
  - Scanned PDF detection via text-to-filesize ratio
  - FileTooLargeError for files exceeding Claude API 32MB limit
affects: [03-03, 03-04, 03-05, content-extraction-actor, claude-vision-integration]

# Tech tracking
tech-stack:
  added: [google-cloud-storage>=2.18.0, PyMuPDF>=1.24.0]
  patterns: [context-manager-cleanup, text-ratio-scanned-detection]

key-files:
  created:
    - app/services/storage/__init__.py
    - app/services/storage/gcs_client.py
    - app/services/extraction/__init__.py
    - app/services/extraction/detector.py
  modified:
    - app/config.py
    - requirements.txt

key-decisions:
  - "Context manager pattern for temp file cleanup ensures files deleted even on exception"
  - "Text-to-filesize ratio 0.01 threshold for scanned PDF detection (1% text content)"
  - "Sample first 5 pages for large PDFs to optimize scanned detection performance"
  - "Graceful error handling - return safe defaults on failure (e.g., True for is_scanned_pdf)"

patterns-established:
  - "Storage context manager: download_attachment() yields temp path, auto-cleanup in finally"
  - "Format detection priority: MIME type > extension > UNKNOWN"
  - "PyMuPDF resource management: always close doc in finally block"

# Metrics
duration: 3min
completed: 2026-02-05
---

# Phase 3 Plan 2: GCS Storage Client and Format Detection Summary

**GCS attachment handler with context manager cleanup pattern and PyMuPDF-based scanned PDF detection using 0.01 text-to-filesize ratio threshold**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-05T10:02:42Z
- **Completed:** 2026-02-05T10:05:57Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- GCS downloads work with both gs:// and HTTPS URLs via unified interface
- Automatic temp file cleanup in finally block (even on exception)
- File format detection from extension and MIME type with SUPPORTED_FORMATS set
- Scanned PDF detection using text-to-filesize ratio heuristic for routing decisions

## Task Commits

Each task was committed atomically:

1. **Task 1: Create GCS attachment handler with streaming and cleanup** - `83b9185` (feat)
2. **Task 2: Create format detector with scanned PDF detection** - `5e8c699` (feat)

## Files Created/Modified

- `app/services/storage/__init__.py` - Export GCSAttachmentHandler and FileTooLargeError
- `app/services/storage/gcs_client.py` - GCS download handler with context manager pattern
- `app/services/extraction/__init__.py` - Export format detection functions and FileFormat enum
- `app/services/extraction/detector.py` - File format detection and scanned PDF detection logic
- `app/config.py` - Added gcs_bucket_name and gcs_max_file_size_mb settings
- `requirements.txt` - Added google-cloud-storage>=2.18.0 and PyMuPDF>=1.24.0

## Decisions Made

1. **Context manager for temp files** - Using `@contextmanager` pattern ensures cleanup happens in finally block even when exceptions occur during processing

2. **Text-to-filesize ratio 0.01** - If extractable text is less than 1% of file size, PDF is likely scanned (image-based) and needs Claude Vision

3. **Sample first 5 pages** - For performance on large documents, only check first 5 pages for text content when detecting scanned PDFs

4. **Graceful failure to Claude Vision** - When PyMuPDF fails or is unavailable, default to True for is_scanned_pdf() to use Claude Vision as fallback

5. **MIME type priority** - Check Content-Type MIME type before falling back to file extension for more reliable format detection

## Deviations from Plan

None - plan executed exactly as written.

## User Setup Required

**External services require manual configuration:**

| Service | Environment Variable | Source |
|---------|---------------------|--------|
| GCS | GOOGLE_APPLICATION_CREDENTIALS | GCP Console -> IAM -> Service Accounts -> Create Key (JSON) |
| GCS | GCS_BUCKET_NAME | GCP Console -> Cloud Storage -> Bucket name |

Note: GCS credentials only needed for production. Development can use URLs directly.

## Next Phase Readiness

- Storage layer ready for content extraction actors
- Format detector ready to route files to appropriate extractors
- Next plans can build PDF, DOCX, XLSX extractors using these foundations

---
*Phase: 03-multi-format-document-extraction*
*Completed: 2026-02-05*
