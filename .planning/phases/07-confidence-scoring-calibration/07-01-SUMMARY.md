---
phase: 07-confidence-scoring-calibration
plan: 01
subsystem: scoring
tags: [confidence, calibration, threshold-tuning, structlog, sqlalchemy, alembic]

# Dependency graph
requires:
  - phase: 05-multi-agent-pipeline-design
    provides: agent_checkpoints JSONB column for extraction data
  - phase: 06-matching-engine-reconstruction
    provides: MatchingEngineV2 with total_score and match status
provides:
  - Confidence dimension calculators (extraction and match)
  - CalibrationSample database model for threshold tuning
  - Source quality baseline constants (native_pdf 0.95 to image 0.70)
affects:
  - 07-02 (routing decisions - uses confidence dimensions)
  - 07-03 (calibration data collection - uses CalibrationSample model)
  - 07-04 (threshold auto-adjustment - analyzes CalibrationSample data)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Dimension-based confidence calculation (separate extraction and match scores)
    - Weakest-link source quality approach (minimum across all sources)
    - Implicit labeling from reviewer corrections (no manual labeling required)

key-files:
  created:
    - app/services/confidence/__init__.py
    - app/services/confidence/dimensions.py
    - app/models/calibration_sample.py
    - alembic/versions/20260205_2300_add_calibration_samples.py
  modified:
    - app/models/__init__.py

key-decisions:
  - "Document-level extraction confidence only (not field-level)"
  - "Source quality baselines: native_pdf (0.95) > docx (0.90) > xlsx (0.85) > scanned_pdf (0.75) > email_body (0.80) > image (0.70)"
  - "Weakest-link approach at source level (minimum quality across all sources)"
  - "Completeness penalty: 0.1 per missing key field (amount, client_name, creditor_name)"
  - "Ambiguity penalty: 30% reduction for ambiguous matches"
  - "Confidence floor: 0.3 (never below), ceiling: 1.0"
  - "Implicit labeling: was_correct=True if approved without changes, False if corrected"
  - "Confidence buckets: high (>0.85), medium (0.6-0.85), low (<0.6)"

patterns-established:
  - "calculate_extraction_confidence: agent_checkpoints -> float 0.0-1.0"
  - "calculate_match_confidence: match_result -> float 0.0-1.0"
  - "CalibrationSample captures: predicted_confidence, dimensions, ground truth, correction details"
  - "Structured logging with full context for confidence calculations"

# Metrics
duration: 3.85min
completed: 2026-02-05
---

# Phase 07 Plan 01: Confidence Dimensions & Calibration Summary

**Confidence dimension calculators with source quality baselines and CalibrationSample model for threshold auto-tuning from production data**

## Performance

- **Duration:** 3.85 min
- **Started:** 2026-02-05T21:57:00Z
- **Completed:** 2026-02-05T22:00:51Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Created confidence dimension service with extraction and match confidence calculators
- Established source quality baselines differentiating native PDF (0.95) from scanned (0.75) from image (0.70)
- Implemented weakest-link approach at source level for extraction confidence
- Created CalibrationSample model for storing labeled examples from reviewer corrections
- Added migration for calibration_samples table with threshold tuning indexes

## Task Commits

Each task was committed atomically:

1. **Task 1: Create confidence dimensions service** - `8928aa3` (feat)
   - calculate_extraction_confidence: document-level confidence from source quality + completeness
   - calculate_match_confidence: matching score with ambiguity adjustment
   - Source quality baselines and completeness penalties

2. **Task 2: Create CalibrationSample model and migration** - `0fa9861` (feat)
   - CalibrationSample model with implicit labeling from reviewer corrections
   - Migration creates calibration_samples table with tuning indexes
   - Foreign keys to incoming_emails and manual_review_queue

## Files Created/Modified

**Created:**
- `app/services/confidence/__init__.py` - Confidence service exports
- `app/services/confidence/dimensions.py` - Dimension calculators with source quality baselines
- `app/models/calibration_sample.py` - CalibrationSample model for threshold tuning
- `alembic/versions/20260205_2300_add_calibration_samples.py` - Migration for calibration_samples table

**Modified:**
- `app/models/__init__.py` - Added CalibrationSample export

## Decisions Made

### Source Quality Baselines (Claude's Discretion)
- **native_pdf: 0.95** - Text extraction, no OCR needed (highest quality)
- **docx: 0.90** - Structured text format
- **xlsx: 0.85** - Tabular data, may need context
- **email_body: 0.80** - Text but often noisy
- **scanned_pdf: 0.75** - Claude Vision, OCR variability
- **image: 0.70** - Claude Vision, least reliable (lowest quality)
- **unknown: 0.60** - Unknown formats get low baseline

Rationale: Reflects extraction reliability - native formats more reliable than OCR/Vision-dependent formats.

### Completeness Adjustment
- **Penalty: 0.1 per missing key field** (amount, client_name, creditor_name)
- **Floor: 0.3** - Never return confidence below 0.3 (even with multiple missing fields)
- **Ceiling: 1.0** - Never return confidence above 1.0

Rationale: Missing key fields indicate incomplete extraction, but extreme penalties aren't useful.

### Match Confidence Ambiguity Adjustment (Claude's Discretion)
- **Ambiguous matches: score × (1 - 0.3)** - 30% reduction for uncertainty
- **Auto-matched: score directly** - No penalty for clear matches
- **Below threshold: score directly** - Already low, no further penalty
- **No candidates/no recent inquiry: 0.0** - Cannot match without candidates

Rationale: Ambiguous status means multiple candidates are close - reduces confidence to reflect uncertainty.

### Implicit Labeling Strategy
- **was_correct = True** - Reviewer approved without changes
- **was_correct = False** - Reviewer corrected any field
- **correction_type** - Categorizes what was corrected (amount, name, creditor, match, multiple)
- **correction_details JSONB** - Stores original vs corrected values for analysis

Rationale: No manual labeling required - labels captured naturally from review workflow.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - all verifications passed on first attempt.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for Plan 07-02 (Routing Service):**
- Confidence dimension calculators available and tested
- calculate_extraction_confidence returns float 0.0-1.0 based on source quality + completeness
- calculate_match_confidence returns float 0.0-1.0 with ambiguity adjustment
- Both functions handle edge cases (empty inputs, missing fields, unknown statuses)

**Ready for Plan 07-03 (Calibration Data Collection):**
- CalibrationSample model created and exported
- Migration ready to create calibration_samples table
- Indexes optimized for threshold tuning queries (confidence_bucket, was_correct)
- correction_details JSONB structure defined for analysis

**Migration Required:**
```bash
alembic upgrade head
```

Creates `calibration_samples` table with indexes for threshold calibration queries.

**No Blockers:**
- All dependencies satisfied (agent_checkpoints from Phase 5, match_result from Phase 6)
- All verifications passed (imports ok, migration syntax valid)
- Ready for routing service integration in Plan 07-02

---
*Phase: 07-confidence-scoring-calibration*
*Completed: 2026-02-05*
