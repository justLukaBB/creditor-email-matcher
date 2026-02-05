---
phase: 07-confidence-scoring-calibration
plan: 04
subsystem: pipeline
tags: [confidence-scoring, routing, email-processing, validation, manual-review]

# Dependency graph
requires:
  - phase: 07-02
    provides: Overall confidence calculator and three-tier routing logic
  - phase: 07-03
    provides: Calibration data collection from manual review resolutions
  - phase: 06-05
    provides: MatchingEngineV2 pipeline integration
  - phase: 05-05
    provides: Multi-agent pipeline with checkpoints

provides:
  - Confidence-based routing integrated into email processing pipeline
  - Three-tier routing (HIGH/MEDIUM/LOW) determines processing path
  - Confidence dimension storage in IncomingEmail for analysis
  - Enhanced manual review queue with expiration support

affects: [08-prompt-management, 09-llm-cost-control, future-threshold-calibration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Confidence-based routing modifies existing matching status handling"
    - "Weakest-link confidence calculation across dimensions"
    - "Three-tier processing paths based on calibrated thresholds"

key-files:
  created:
    - alembic/versions/20260205_2330_add_confidence_columns.py
  modified:
    - app/models/incoming_email.py
    - app/actors/email_processor.py
    - app/services/validation/review_queue.py

key-decisions:
  - "HIGH confidence emails auto-update with log only, NO notification"
  - "MEDIUM confidence emails auto-update WITH notification for verification"
  - "LOW confidence emails route to manual review queue with 7-day expiration"
  - "Confidence routing applies AFTER matching, modifying behavior of auto_matched status"
  - "Store confidence dimensions as integers 0-100 for consistency with match_confidence"

patterns-established:
  - "Confidence breakdown stored for threshold tuning analysis"
  - "expiration_days parameter in review queue for low-confidence routing"
  - "Route-based notification control (HIGH: no notify, MEDIUM: notify)"

# Metrics
duration: 3.48min
completed: 2026-02-05
---

# Phase 07 Plan 04: Pipeline Routing Integration Summary

**Confidence-based three-tier routing integrated into email processor: HIGH auto-updates silently, MEDIUM notifies review team, LOW queues for manual review with expiration**

## Performance

- **Duration:** 3.48 min (209 seconds)
- **Started:** 2026-02-05T22:13:14Z
- **Completed:** 2026-02-05T22:16:43Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Email processor calculates overall confidence with dimension breakdown
- Confidence dimensions stored in IncomingEmail (extraction_confidence, overall_confidence, confidence_route)
- HIGH confidence (>0.85): auto-update with log only, NO notification
- MEDIUM confidence (0.6-0.85): auto-update WITH notification to review team
- LOW confidence (<0.6): route to manual review queue with 7-day expiration

## Task Commits

Each task was committed atomically:

1. **Task 1: Add confidence columns and migration** - `3f73dad` (feat)
2. **Task 2: Integrate confidence routing into email processor** - `fbaad4a` (feat)

**Plan metadata:** (to be committed after SUMMARY.md creation)

## Files Created/Modified
- `app/models/incoming_email.py` - Added extraction_confidence, overall_confidence, confidence_route columns
- `alembic/versions/20260205_2330_add_confidence_columns.py` - Migration for confidence scoring columns with index
- `app/actors/email_processor.py` - Integrated confidence calculation and three-tier routing logic
- `app/services/validation/review_queue.py` - Enhanced enqueue_for_review with expiration_days parameter

## Decisions Made

**Confidence routing modifies matching behavior:**
- Existing matching status handling preserved (ambiguous still goes to review)
- Confidence routing ENHANCES auto_matched status, not replaces it
- LOW confidence can override auto_matched and route to manual review

**Notification control via routing:**
- HIGH confidence: no notification (silent auto-update)
- MEDIUM confidence: notification sent after database write
- LOW confidence: no notification (manual review queue handles it)

**Storage format:**
- All confidence values stored as integers 0-100 (consistent with match_confidence)
- confidence_route stored as string: "high", "medium", "low"
- Index on (confidence_route, created_at) for efficient routing queries

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - integration proceeded smoothly. All confidence modules from prior plans (07-01, 07-02) worked as expected.

## User Setup Required

None - no external service configuration required.

**Migration required:**
```bash
alembic upgrade head
```

This creates the confidence scoring columns (extraction_confidence, overall_confidence, confidence_route) and the routing index.

## Next Phase Readiness

**Phase 7 complete:** All 4 plans executed successfully.

**Ready for Phase 8 (Prompt Management):**
- Confidence scoring fully integrated into pipeline
- Calibration data collection active (from 07-03)
- Review queue enhanced with expiration support
- Email processor using confidence-based routing

**Confidence system operational:**
- Extraction confidence from document quality baseline + completeness
- Match confidence from matching score with ambiguity penalty
- Overall confidence using weakest-link principle
- Three-tier routing with configurable thresholds (settings.confidence_high_threshold, settings.confidence_low_threshold)

**Next phase can:**
- Build prompt management system for runtime prompt updates
- Track prompt version usage for A/B testing
- Use confidence scores to route to different prompt variants

**Phase 8 blockers:** None

---
*Phase: 07-confidence-scoring-calibration*
*Completed: 2026-02-05*
