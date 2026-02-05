---
phase: 03-multi-format-document-extraction
plan: 04
subsystem: extraction
tags: [python-docx, openpyxl, regex, german-format, email-parsing]

# Dependency graph
requires:
  - phase: 03-01
    provides: SourceExtractionResult, ExtractedAmount, ExtractedEntity models
provides:
  - EmailBodyExtractor for email text amount extraction
  - DOCXExtractor for Word document extraction
  - XLSXExtractor with memory-efficient read_only mode
  - German number format parsing (1.234,56 -> 1234.56)
  - Consistent SourceExtractionResult output from all extractors
affects: [03-05-orchestration, phase-4-entity-extraction]

# Tech tracking
tech-stack:
  added: [python-docx>=1.1.0, openpyxl>=3.1.0]
  patterns: [keyword-adjacent-cell-extraction, highest-amount-wins]

key-files:
  created:
    - app/services/extraction/email_body_extractor.py
    - app/services/extraction/docx_extractor.py
    - app/services/extraction/xlsx_extractor.py
  modified:
    - app/services/extraction/__init__.py
    - requirements.txt

key-decisions:
  - "Highest amount wins from multiple candidates (USER DECISION preserved)"
  - "German number format regex: [0-9][0-9.,]* with flexible separator support"
  - "XLSX keyword-adjacent-cell pattern for spreadsheet amount detection"
  - "read_only=True for XLSX memory efficiency on Render 512MB budget"

patterns-established:
  - "Amount extraction: regex patterns reused across email/DOCX extractors"
  - "XLSX extraction: keyword detection + adjacent cell value lookup"
  - "Confidence scoring: HIGH for German format with comma, MEDIUM otherwise"

# Metrics
duration: 4min
completed: 2026-02-05
---

# Phase 3 Plan 04: Additional Format Extractors Summary

**Email body, DOCX, and XLSX extractors with German number format parsing and consistent SourceExtractionResult output**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-05T10:13:24Z
- **Completed:** 2026-02-05T10:17:36Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments
- EmailBodyExtractor finds Forderungshoehe in email text using flexible regex patterns
- DOCXExtractor extracts from both paragraphs and tables using python-docx
- XLSXExtractor uses memory-efficient read_only mode for constant memory usage
- All extractors return consistent SourceExtractionResult with tokens_used=0 (no API calls)
- German number format (1.234,56) correctly parsed to float (1234.56) in all extractors

## Task Commits

Each task was committed atomically:

1. **Task 1: Create email body extractor** - `9c8ce9c` (feat)
2. **Task 2: Create DOCX extractor** - `f0b0404` (feat)
3. **Task 3: Create XLSX extractor with memory-efficient mode** - `bae7357` (feat)

## Files Created/Modified
- `app/services/extraction/email_body_extractor.py` - Regex-based amount extraction from email text
- `app/services/extraction/docx_extractor.py` - python-docx text extraction from paragraphs and tables
- `app/services/extraction/xlsx_extractor.py` - Memory-efficient openpyxl extraction with keyword detection
- `app/services/extraction/__init__.py` - Added exports for all new extractors
- `requirements.txt` - Added python-docx>=1.1.0 and openpyxl>=3.1.0

## Decisions Made
- **Flexible regex patterns:** Added `[:\s\w]*?` between keywords and amounts to handle phrases like "Gesamtforderung beträgt 1.234,56 EUR" (deviation from original strict patterns)
- **Catch-all amount pattern:** Added `([0-9][0-9.,]*)\s*(EUR|€)` as last pattern to capture amounts not preceded by keywords
- **Confidence scoring:** HIGH confidence for German format (has comma decimal), MEDIUM for integer-only amounts

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed regex patterns to handle flexible text**
- **Found during:** Task 1 (EmailBodyExtractor verification)
- **Issue:** Original patterns `[Gg]esamtforderung[:\s]+([0-9.,]+)` didn't match text like "Gesamtforderung beträgt 1.234,56 EUR" where words appear between keyword and amount
- **Fix:** Changed to `[:\s\w]*?` non-greedy match for flexible separator, added catch-all amount pattern
- **Files modified:** app/services/extraction/email_body_extractor.py
- **Verification:** Test extracts 1234.56 from "Die Gesamtforderung beträgt 1.234,56 EUR"
- **Committed in:** 9c8ce9c (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Minor regex adjustment necessary for real-world German text patterns. No scope creep.

## Issues Encountered
None - dependencies installed successfully, all extractors verified working.

## User Setup Required
None - no external service configuration required. Dependencies added to requirements.txt.

## Next Phase Readiness
- All three extractors complete and exported from package
- Ready for 03-05 orchestration to compose extractors into unified pipeline
- Memory-efficient XLSX extraction suitable for Render 512MB budget
- German number format parsing verified across all extractors

---
*Phase: 03-multi-format-document-extraction*
*Completed: 2026-02-05*
