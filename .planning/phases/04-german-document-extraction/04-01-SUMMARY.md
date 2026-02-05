---
phase: 04-german-document-extraction
plan: 01
subsystem: extraction
tags: [german, unicode, ocr, validation, pyspellchecker, preprocessing]

# Dependency graph
requires:
  - phase: 03-multi-format-document-extraction
    provides: Extraction pipeline with PDF, DOCX, XLSX, image, and email body extractors
provides:
  - GermanTextPreprocessor with Unicode normalization and OCR correction
  - GermanValidator for postal codes, names, and addresses
  - German text preprocessing layer for extraction pipeline
affects: [04-02-german-amount-parser, 04-03-reference-number-extraction, extraction-pipeline]

# Tech tracking
tech-stack:
  added: [pyspellchecker>=0.8.4]
  patterns: [Unicode NFKC normalization, dictionary-validated OCR correction, conservative preprocessing]

key-files:
  created:
    - app/services/extraction/german_preprocessor.py
    - app/services/extraction/german_validator.py
    - tests/test_german_preprocessor.py
  modified:
    - requirements.txt

key-decisions:
  - "Conservative OCR correction: only restore Umlauts when dictionary validates"
  - "Digit substitutions (3->e, 0->o, 1->l) only for name/address fields, not amounts"
  - "Unicode NFKC normalization for consistent Umlaut representation"

patterns-established:
  - "German text preprocessing: normalize first, then correct OCR errors"
  - "Validation patterns use Unicode escapes for portability"
  - "Case-preserving Umlaut restoration (MUELLER -> MÜLLER)"

# Metrics
duration: 4.5min
completed: 2026-02-05
---

# Phase 4 Plan 01: German Text Preprocessing Summary

**Unicode normalization and OCR-corrected Umlaut restoration with dictionary validation, plus German format validators for postal codes, names, and addresses**

## Performance

- **Duration:** 4.5 min (271 seconds)
- **Started:** 2026-02-05T16:04:37Z
- **Completed:** 2026-02-05T16:09:08Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- GermanTextPreprocessor normalizes Unicode (NFKC) and restores Umlauts from OCR-corrupted digraphs (ue->ü, oe->ö, ae->ä)
- Dictionary-validated OCR correction ensures only valid German words accepted
- GermanValidator validates German postal codes (5 digits), names (with Umlauts and noble prefixes), and street addresses
- Comprehensive test suite with 34 passing tests covering all preprocessing and validation scenarios

## Task Commits

Each task was committed atomically:

1. **Task 1: Create GermanTextPreprocessor with Unicode normalization and OCR correction** - `3fd1d4d` (feat)
2. **Task 2: Create GermanValidator with postal code, name, and address validation** - `5948217` (feat)
3. **Task 3: Add unit tests for preprocessor and validator** - `d227eac` (test)

## Files Created/Modified
- `app/services/extraction/german_preprocessor.py` - GermanTextPreprocessor class with Unicode NFKC normalization, dictionary-validated OCR correction, and name field digit substitutions
- `app/services/extraction/german_validator.py` - GermanValidator class with regex-based validation for postal codes, names, and addresses
- `tests/test_german_preprocessor.py` - 34 unit tests covering normalization, OCR correction, validation patterns, edge cases
- `requirements.txt` - Added pyspellchecker>=0.8.4 for German dictionary validation

## Decisions Made

**1. Conservative OCR correction approach**
- Only restore Umlauts when result exists in German dictionary (SpellChecker)
- Better to miss a correction than introduce errors (USER DECISION from plan)
- Rationale: Prevents "Feuer" -> "Für" type corruptions

**2. Field-specific digit substitutions**
- Digit-to-letter substitutions (3->e, 0->o, 1->l) only via separate `correct_name_field()` method
- NOT applied to general text, amounts, or reference numbers
- Rationale: Prevents corrupting numeric data while fixing OCR errors in names

**3. Unicode portability**
- Used Unicode escapes (\u00e4, \u00f6, \u00fc) in regex patterns
- Ensures portability across different file encodings
- Rationale: Validation patterns work consistently regardless of source file encoding

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - all tasks completed without blocking issues.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for 04-02 (German Amount Parser):**
- GermanTextPreprocessor available for preprocessing amounts before parsing
- Unicode normalization ensures consistent Umlaut representation in amount text
- GermanValidator validates extracted German formats before database storage

**Ready for 04-03 (Reference Number Extraction):**
- GermanTextPreprocessor can normalize reference numbers with Umlauts
- Validation patterns established for German-specific formats

**Integration points:**
- Import `GermanTextPreprocessor` from `app.services.extraction.german_preprocessor`
- Import `GermanValidator` from `app.services.extraction.german_validator`
- Call `preprocessor.preprocess(text)` before extraction to normalize and correct OCR errors
- Call `validator.is_valid_german_format(value, field_type)` to validate extracted data

**No blockers or concerns.**

---
*Phase: 04-german-document-extraction*
*Completed: 2026-02-05*
