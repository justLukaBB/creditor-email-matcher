---
phase: 03-multi-format-document-extraction
plan: 03
subsystem: extraction
tags: [pdf, pymupdf, claude-vision, ocr, german-currency]

# Dependency graph
requires:
  - phase: 03-01
    provides: SourceExtractionResult and ExtractedAmount models
  - phase: 03-02
    provides: is_scanned_pdf, is_encrypted_pdf detection functions
provides:
  - PDFExtractor class for digital and scanned PDF processing
  - PyMuPDF extraction for text-selectable PDFs (zero API cost)
  - Claude Vision fallback for scanned/image-based PDFs
  - German currency parsing (1.234,56 EUR format)
  - Page limit handling (first 5 + last 5 for >10 pages)
affects: [03-06-consolidation, content-extraction-actor]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy Claude client initialization (only when scanned PDF encountered)"
    - "Token budget check before API call (fail fast)"
    - "Page truncation strategy for large documents"

key-files:
  created:
    - app/services/extraction/pdf_extractor.py
  modified:
    - app/services/extraction/__init__.py
    - app/models/extraction_result.py

key-decisions:
  - "PyMuPDF for digital PDFs (zero cost), Claude Vision only for scanned"
  - "Page limit: first 5 + last 5 for documents over 10 pages (USER DECISION)"
  - "Token estimate: ~2000 tokens per page for Claude Vision"
  - "Lazy Claude client initialization to avoid API key requirement when not needed"

patterns-established:
  - "PDF type routing: encrypt check -> scanned check -> extract"
  - "Error handling: return SourceExtractionResult with error field, extraction_method='skipped'"
  - "German number parsing: replace '.' with '', then ',' with '.'"

# Metrics
duration: 4min
completed: 2026-02-05
---

# Phase 03 Plan 03: PDF Extractor Summary

**PDF extraction with PyMuPDF for digital documents and Claude Vision fallback for scanned PDFs, with token budget enforcement and German currency parsing**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-05T10:12:32Z
- **Completed:** 2026-02-05T10:17:11Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- PDFExtractor class with automatic routing based on PDF type (digital vs scanned)
- PyMuPDF extraction for digital PDFs at zero API cost
- Claude Vision API integration with structured JSON extraction prompt
- Token budget check before API calls prevents cost overruns
- German currency format parsing (1.234,56 EUR -> 1234.56)
- Page limit handling: documents >10 pages process first 5 + last 5 only
- Graceful handling of encrypted and missing files

## Task Commits

Note: The core PDFExtractor was committed as part of a parallel execution:

1. **Task 1+2: PDFExtractor with PyMuPDF and Claude Vision** - `9c8ce9c` (feat - committed as part of 03-04 parallel execution)
2. **Bug fix: Add 'skipped' extraction_method** - `a2a5e63` (fix)

**Deviation:** The pdf_extractor.py file was included in commit 9c8ce9c attributed to 03-04 due to parallel execution. The code meets all 03-03 requirements.

## Files Created/Modified

- `app/services/extraction/pdf_extractor.py` - PDFExtractor class with PyMuPDF and Claude Vision extraction methods
- `app/services/extraction/__init__.py` - Added PDFExtractor and EXTRACTION_PROMPT exports
- `app/models/extraction_result.py` - Added 'skipped' to extraction_method Literal for error handling

## Decisions Made

1. **Lazy Claude client initialization** - Client only created when first scanned PDF encountered, avoiding API key requirement for digital-only workloads
2. **Token estimation at 2000 per page** - Conservative estimate for Claude Vision to ensure budget checks are reliable
3. **First 5 + last 5 page strategy** - For documents >10 pages, captures both opening details and final amounts/summaries

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Added 'skipped' to extraction_method Literal**
- **Found during:** Task verification
- **Issue:** SourceExtractionResult model didn't include "skipped" in extraction_method Literal, causing ValidationError when handling encrypted/missing PDFs
- **Fix:** Added "skipped" to the Literal type definition
- **Files modified:** app/models/extraction_result.py
- **Verification:** PDFExtractor.extract() now handles missing files without crashing
- **Committed in:** a2a5e63

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Bug fix essential for graceful error handling. No scope creep.

## Issues Encountered

- **Parallel execution overlap:** The pdf_extractor.py was committed as part of 03-04 plan execution due to concurrent agent work. This is a process issue, not a code issue. All 03-03 requirements are met by the committed code.

## User Setup Required

None - no external service configuration required. The Anthropic API key (ANTHROPIC_API_KEY) is already configured from previous phases.

## Next Phase Readiness

- PDFExtractor ready for integration with content extraction actor (Phase 03-06)
- Digital PDFs process at zero cost using PyMuPDF
- Scanned PDFs route to Claude Vision with budget protection
- All error cases handled gracefully without crashing

**Dependencies for next plans:**
- 03-04 (Email Body Extractor): Independent, can proceed in parallel
- 03-05 (DOCX/XLSX Extractor): Independent, can proceed in parallel
- 03-06 (Consolidation): Depends on all extractors (03-03, 03-04, 03-05)

---
*Phase: 03-multi-format-document-extraction*
*Completed: 2026-02-05*
