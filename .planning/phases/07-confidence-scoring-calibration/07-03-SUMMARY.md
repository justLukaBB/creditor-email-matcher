---
phase: 07
plan: 03
subsystem: calibration
tags: [calibration, manual-review, implicit-labeling, threshold-tuning]

# Dependency Graph
requires:
  - 07-01  # CalibrationSample model and confidence dimension calculators
  - 05-04  # ManualReviewQueue infrastructure

provides:
  - Calibration sample collection from review resolutions
  - Implicit labeling from reviewer corrections
  - Correction type detection and tracking

affects:
  - 07-04  # Threshold calibration analyzer (will use collected samples)
  - Future threshold auto-adjustment features

# Tech Stack
tech-stack:
  added:
    - app/services/calibration/collector.py
  patterns:
    - Implicit labeling from user actions
    - Correction type classification
    - Document type extraction from checkpoints

# File Tracking
key-files:
  created:
    - app/services/calibration/__init__.py
    - app/services/calibration/collector.py
  modified:
    - app/routers/manual_review.py

# Decisions
decisions:
  - slug: implicit-labeling-from-resolutions
    summary: "Approval = correct, correction = incorrect"
    context: "Labels captured from reviewer actions without explicit labeling UI"
    impact: "Enables gradual calibration dataset accumulation from production"

  - slug: skip-spam-rejected-escalated
    summary: "Spam/rejected/escalated resolutions not captured for calibration"
    context: "These resolutions don't provide useful calibration signals"
    impact: "Calibration dataset focuses on genuine prediction quality"

  - slug: correction-type-classification
    summary: "Classify corrections as amount/client_name/creditor_name/match/multiple"
    context: "Enables analysis of which dimension needs threshold adjustment"
    impact: "Future threshold tuning can target specific weak dimensions"

# Metrics
metrics:
  duration: "2.92 min"
  completed: "2026-02-05"
---

# Phase 7 Plan 3: Calibration Data Collection Summary

**One-liner:** Implicit labeling from manual review resolutions (approval = correct, correction = incorrect) with correction type classification

## What Was Built

Implemented calibration data collection that captures labeled examples from manual review resolutions for threshold tuning.

### Core Components

**1. Calibration Collector Service** (`app/services/calibration/collector.py`)
- `capture_calibration_sample()` creates CalibrationSample records from resolutions
- Implicit labeling: approved = was_correct=True, corrected = was_correct=False
- Detects correction type: amount_corrected, client_name_corrected, creditor_name_corrected, match_corrected, multiple
- Extracts document type from agent_checkpoints (priority: native_pdf > scanned_pdf > docx > xlsx > image > email_body)
- Categorizes into confidence buckets: high (>0.85), medium (0.6-0.85), low (<0.6)
- Skips spam/rejected/escalated resolutions (not useful for calibration)

**2. Manual Review Resolution Integration** (`app/routers/manual_review.py`)
- Updated ResolveRequest schema to accept optional corrected_data for corrections
- Resolve endpoint captures calibration sample for approved/corrected resolutions
- All changes atomic within same transaction (resolution + calibration)
- Calibration capture happens before commit for transactional consistency

## Task Breakdown

### Task 1: Create calibration collector service
**Commit:** 7ceecf8
**Files:**
- `app/services/calibration/__init__.py` (new)
- `app/services/calibration/collector.py` (new)

**Implemented:**
- Implicit labeling logic (approval = correct, correction = incorrect)
- Correction type detection with field-level tracking
- Document type extraction from agent checkpoints
- Confidence bucketing using same thresholds as routing (0.85/0.60)
- Selective capture (skip spam/rejected/escalated)

### Task 2: Update manual review resolution to capture calibration
**Commit:** 37e3178
**Files:**
- `app/routers/manual_review.py` (modified)

**Implemented:**
- Extended ResolveRequest with corrected_data field
- Calibration capture in resolve endpoint for approved/corrected resolutions
- Email loaded for calibration context
- Transactional consistency (resolution + calibration in same transaction)

## Technical Details

### Implicit Labeling Strategy

**USER DECISION from CONTEXT.md:**
- Labels captured implicitly from reviewer corrections
- If reviewer changes data, original was wrong (was_correct=False)
- If reviewer approves without changes, original was correct (was_correct=True)

**Implementation:**
```python
was_correct = resolution == "approved"  # True if approved, False if corrected
```

### Correction Type Detection

Compares original extracted_data with corrected_data to detect what changed:

| Correction Type | Trigger | Details Captured |
|----------------|---------|------------------|
| `amount_corrected` | debt_amount changed | original_amount, corrected_amount |
| `client_name_corrected` | client_name changed | original_client, corrected_client |
| `creditor_name_corrected` | creditor_name changed | original_creditor, corrected_creditor |
| `match_corrected` | matched_inquiry_id changed | original_inquiry_id, corrected_inquiry_id |
| `multiple` | 2+ fields changed | field_changes array |
| `none` | no changes (approval) | empty details |

### Document Type Extraction

Extracts from `agent_checkpoints.agent_2_extraction.sources_processed`:

**Priority:** native_pdf > scanned_pdf > docx > xlsx > image > email_body

Rationale: Higher priority = more reliable source type for calibration analysis.

### Confidence Bucketing

Uses same thresholds as routing for consistency:
- **high:** ≥0.85 (hardcoded, will move to config in 07-02)
- **medium:** 0.60-0.85
- **low:** <0.60

### Selective Capture

**Skipped resolutions:**
- `spam` - Not a prediction quality issue
- `rejected` - User rejection, not prediction error
- `escalated` - Human decision needed, unclear ground truth

**Captured resolutions:**
- `approved` - was_correct=True
- `corrected` - was_correct=False with correction details

## Decisions Made

**1. Implicit labeling from reviewer actions**
- Approval without changes = original extraction was correct
- Correction = original extraction was incorrect
- Rationale: Avoids requiring explicit labeling UI, gradual accumulation from production

**2. Skip spam/rejected/escalated for calibration**
- These resolutions don't provide useful prediction quality signals
- Calibration dataset focuses on genuine extraction/matching accuracy

**3. Correction type classification**
- Track which fields were corrected (amount, names, match)
- Enables future analysis: "low confidence on scanned PDFs → mostly amount_corrected"
- Supports dimension-specific threshold tuning

**4. Document type extraction from checkpoints**
- Use agent_2_extraction.sources_processed to identify document type
- Priority order reflects information density (native PDF most reliable)
- Enables analysis: "native PDFs have higher accuracy than scanned PDFs"

## Integration Points

### Upstream Dependencies
- **07-01:** CalibrationSample model with was_correct, correction_type, document_type fields
- **05-04:** ManualReviewQueue infrastructure with resolution workflow
- **05-01:** agent_checkpoints JSONB for document type extraction

### Downstream Impact
- **07-04:** Threshold calibration analyzer will use accumulated samples
- **Future:** Auto-adjustment will analyze correction_type patterns per confidence bucket

## Testing & Verification

**Service Import:**
```bash
python3 -c "from app.services.calibration import capture_calibration_sample; print('service ok')"
# Output: service ok
```

**Router Integration:**
```bash
python3 -c "from app.routers.manual_review import router; paths = [r.path for r in router.routes]; print('resolve' in str(paths))"
# Output: True
```

**Calibration Import in Router:**
```bash
grep -l "capture_calibration_sample" app/routers/manual_review.py
# Output: app/routers/manual_review.py
```

All verifications passed.

## Deviations from Plan

None - plan executed exactly as written.

## Next Phase Readiness

### Blockers
None.

### Concerns
1. **Confidence dimension values:** extraction_confidence set to None (will be populated in 07-02 integration)
2. **Threshold config:** Using hardcoded 0.85/0.60 thresholds (will move to config in 07-02)
3. **Corrected data schema:** UI needs to send corrected_data matching extracted_data structure

### Prerequisites for Next Plan (07-04)
- Accumulate 100+ calibration samples before threshold analysis is useful
- Ensure corrected_data includes all relevant fields (debt_amount, client_name, creditor_name, matched_inquiry_id)

## Performance Metrics

**Execution Time:** 2.92 minutes
- Task 1: ~1.5 minutes (create collector service)
- Task 2: ~1.4 minutes (update manual review router)

**Code Changes:**
- 2 files created
- 1 file modified
- ~220 lines of code added

**Commits:**
- 7ceecf8: Calibration collector service
- 37e3178: Manual review resolution integration

## Success Criteria Met

- [x] Calibration collector creates samples with correct labels based on resolution
- [x] "approved" resolution = was_correct=True
- [x] "corrected" resolution = was_correct=False with correction details
- [x] Correction type detected (amount_corrected, client_name_corrected, etc.)
- [x] Document type extracted from agent checkpoints
- [x] Manual review resolve endpoint triggers calibration capture
- [x] Spam/rejected/escalated resolutions skipped

## Production Deployment Notes

**No migration required** - CalibrationSample table created in 07-01 migration.

**Configuration:**
- Hardcoded thresholds (0.85/0.60) will move to config in 07-02
- No environment variables needed for this plan

**Operational Notes:**
- Calibration samples accumulate gradually from manual review resolutions
- View calibration data: `SELECT * FROM calibration_samples ORDER BY captured_at DESC LIMIT 10;`
- Monitor calibration accumulation: `SELECT confidence_bucket, was_correct, COUNT(*) FROM calibration_samples GROUP BY confidence_bucket, was_correct;`

**Manual Review UI Requirements:**
- For corrections, UI must send `corrected_data` matching extracted_data structure
- Fields: `debt_amount`, `client_name`, `creditor_name`, `matched_inquiry_id`
- Example: `{"corrected_data": {"debt_amount": 1550.0, "client_name": "Mueller Max"}}`
