---
phase: 04-german-document-extraction
plan: 03
subsystem: extraction
tags: [claude-vision, german-nlp, prompt-engineering, anthropic-api]

# Dependency graph
requires:
  - phase: 03-multi-format-extraction
    provides: PDF and image extractors with Claude Vision API integration
provides:
  - German-language Claude Vision prompts with German examples
  - German synonym support for amount keywords (Schulden, offener Betrag, Restschuld)
  - Realistic German creditor response patterns in prompts
affects: [04-04-integration, content-extraction, extraction-accuracy]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Prompt language matches document language for better extraction accuracy"]

key-files:
  created: []
  modified:
    - app/services/extraction/pdf_extractor.py
    - app/services/extraction/image_extractor.py

key-decisions:
  - "German prompts with German examples improve extraction accuracy (USER DECISION)"
  - "Use ASCII-safe characters (ae, oe, ue) instead of Umlauts to avoid encoding issues"
  - "Accept German synonyms for Gesamtforderung (Schulden, offener Betrag, Restschuld)"

patterns-established:
  - "Prompt language should match document language for LLM extraction tasks"
  - "Include realistic examples in prompts to guide LLM behavior"
  - "Accept common synonyms/variations of key terms"

# Metrics
duration: 1.6min
completed: 2026-02-05
---

# Phase 04 Plan 03: German Document Extraction Summary

**German Claude Vision prompts with synonym support and realistic creditor response examples for improved extraction accuracy**

## Performance

- **Duration:** 1.6 min
- **Started:** 2026-02-05T16:12:39Z
- **Completed:** 2026-02-05T16:14:15Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Updated PDF extractor EXTRACTION_PROMPT to German with realistic creditor response examples
- Updated image extractor IMAGE_EXTRACTION_PROMPT to German with German examples
- Added German synonym support (Schulden, offener Betrag, Restschuld, Forderungshoehe, Gesamtsumme)
- Used ASCII-safe characters (ae, oe, ue) to avoid encoding issues with Umlauts

## Task Commits

Each task was committed atomically:

1. **Task 1: Update PDF extractor with German Claude prompt** - `8c80325` (feat)
2. **Task 2: Update Image extractor with German Claude prompt** - `e184481` (feat)

## Files Created/Modified
- `app/services/extraction/pdf_extractor.py` - EXTRACTION_PROMPT now in German with examples
- `app/services/extraction/image_extractor.py` - IMAGE_EXTRACTION_PROMPT now in German with examples

## Decisions Made

**1. German prompts with German examples improve extraction accuracy**
- Rationale: USER DECISION - Claude performs better when prompt language matches document language
- Impact: Better extraction accuracy for German creditor documents

**2. Accept German synonyms for Gesamtforderung**
- Rationale: German creditor responses use varied terminology (Schulden, offener Betrag, Restschuld, Forderungshoehe, Gesamtsumme)
- Impact: More robust extraction across different creditor communication styles

**3. Use ASCII-safe characters instead of Umlauts**
- Rationale: Avoid potential encoding issues in API prompts
- Implementation: Use ae, oe, ue instead of ä, ö, ü in prompt text
- Impact: Consistent encoding across different environments

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for:** Phase 04-04 (Integration with extractors)
- German prompts are now active in both PDF and image extractors
- All Claude Vision calls will use German prompts with German examples
- Synonym support enables robust extraction across creditor response variations

**No blockers:** Extraction infrastructure ready for Phase 04-04 integration work

---
*Phase: 04-german-document-extraction*
*Completed: 2026-02-05*
