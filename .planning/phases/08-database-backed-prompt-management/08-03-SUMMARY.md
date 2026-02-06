---
phase: 08-database-backed-prompt-management
plan: 03
subsystem: services
tags: [extraction, intent-classification, pdf-extraction, image-extraction, prompt-integration, jinja2, metrics-tracking]

# Dependency graph
requires:
  - phase: 08-02
    provides: PromptRenderer, PromptVersionManager, PromptMetricsService, get_active_prompt
provides:
  - Intent classifier loads prompts from database with fallback to hardcoded
  - Entity extractor loads system/user prompts from database with Jinja2 rendering
  - PDF extractor loads vision prompts from database for scanned PDFs
  - Image extractor loads vision prompts from database for image documents
  - All extractors record metrics with prompt_template_id for performance tracking
affects: [09-prompt-optimization, monitoring, cost-tracking, production-deployment]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Database-first prompt loading with hardcoded fallback for migration period"
    - "Jinja2 variable interpolation for email context (subject, body, from_email)"
    - "Metrics recording after each API call with prompt_template_id"
    - "Optional db session and email_id parameters for backward compatibility"

key-files:
  created: []
  modified:
    - app/services/intent_classifier.py
    - app/services/entity_extractor_claude.py
    - app/services/extraction/pdf_extractor.py
    - app/services/extraction/image_extractor.py

key-decisions:
  - "All extractors accept optional db and email_id parameters for backward compatibility"
  - "Hardcoded prompts preserved as module-level constants for fallback during migration"
  - "Metrics recording failures log warnings but don't fail extraction"
  - "Database session closed in finally blocks to prevent connection leaks"
  - "Vision prompts (PDF, image) don't use variable interpolation since documents are visual"

patterns-established:
  - "Prompt loading pattern: try database first, catch exceptions, fallback to hardcoded"
  - "Metrics recording pattern: check prompt_template exists before recording"
  - "Session management: create session in function, close in finally block"
  - "Parameter pattern: model_name, temperature, max_tokens from prompt_template with fallback to defaults"

# Metrics
duration: 6min
completed: 2026-02-06
---

# Phase 8 Plan 3: Prompt Integration Summary

**All four extraction services (intent classifier, entity extractor, PDF extractor, image extractor) now load prompts from database with Jinja2 rendering, record metrics per extraction, and fall back to hardcoded prompts for migration period**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-06T15:37:54Z
- **Completed:** 2026-02-06T15:44:03Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Intent classifier loads prompts from database (task_type='classification', name='email_intent') with subject/body variable interpolation
- Entity extractor loads system and user prompts from database (task_type='extraction', name='email_body') with from_email/subject/email_body variables
- PDF extractor loads vision prompt from database (task_type='extraction', name='pdf_scanned') for scanned PDF processing
- Image extractor loads vision prompt from database (task_type='extraction', name='image') for image document processing
- All services record extraction metrics with prompt_template_id for performance tracking and cost calculation
- Backward compatibility maintained: new db/email_id parameters optional, hardcoded prompts preserved as fallback

## Task Commits

Each task was committed atomically:

1. **Task 1: Integrate database prompts into intent_classifier.py** - `fd7f233` (feat)
2. **Task 2: Integrate database prompts into entity_extractor_claude.py** - `d5aa52d` (feat)
3. **Task 3: Integrate database prompts into pdf_extractor.py and image_extractor.py** - `8f54590` (feat)

## Files Created/Modified

- `app/services/intent_classifier.py` - Database-backed intent classification with metrics recording
- `app/services/entity_extractor_claude.py` - Database-backed entity extraction with system/user prompt separation
- `app/services/extraction/pdf_extractor.py` - Database-backed PDF extraction with vision prompt loading
- `app/services/extraction/image_extractor.py` - Database-backed image extraction with vision prompt loading

## Decisions Made

**1. Optional db/email_id parameters for backward compatibility**
- Rationale: Existing callers don't have database sessions. Making parameters optional ensures zero breaking changes during migration period.
- Implementation: All new parameters default to None. Services work exactly as before when not provided.

**2. Hardcoded prompts preserved as module-level constants**
- Rationale: Migration from hardcoded to database prompts requires transition period where both work. Keeping constants ensures fallback path exists.
- Implementation: EXTRACTION_PROMPT and IMAGE_EXTRACTION_PROMPT remain as module constants. Intent classifier and entity extractor keep hardcoded prompt construction logic.

**3. Metrics recording failures don't fail extraction**
- Rationale: Recording metrics is secondary to successful extraction. If metrics DB write fails, extraction result should still be returned.
- Implementation: try/except around record_extraction_metrics() with db.rollback() on failure. Logs warning but returns extraction result.

**4. Database session management in finally blocks**
- Rationale: Database connections are limited resources. Must ensure sessions are closed even when exceptions occur.
- Implementation: intent_classifier creates session in try block, closes in finally. Entity extractor accepts session from caller (caller responsible for lifecycle).

**5. Vision prompts don't use Jinja2 variable interpolation**
- Rationale: PDF and image documents are sent as binary data to Claude Vision API. Prompt text is static instruction - no variables to interpolate.
- Implementation: Load prompt_template.user_prompt_template directly without calling PromptRenderer.render() for pdf_scanned and image extraction types.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - all integrations followed established patterns from 08-02 services.

## User Setup Required

None - no external service configuration required. Services use existing database models and services from 08-01 and 08-02.

## Next Phase Readiness

**Ready for Phase 8 Plan 4 (Seeding and Testing):**
- All extraction services integrated with database prompt loading
- Metrics recording infrastructure in place
- Fallback mechanisms tested (syntax-checked)

**Foundation provides:**
- Runtime prompt updates without redeployment (when active prompt exists in database)
- Per-extraction metrics tracking with prompt version linkage
- Audit trail of which prompt version processed each email
- Backward compatibility for gradual migration

**Next steps:**
- Seed database with existing hardcoded prompts (migration script)
- Test prompt activation workflow (create → activate → verify)
- Test metrics recording with real extractions
- Verify fallback behavior when no active prompt exists

**Known constraints:**
- Callers must be updated to pass db session and email_id for metrics tracking to work
- Until prompts are seeded in database, all extractors use hardcoded prompts (expected behavior)
- Metrics are only recorded when both db session and email_id are provided

---
*Phase: 08-database-backed-prompt-management*
*Completed: 2026-02-06*
