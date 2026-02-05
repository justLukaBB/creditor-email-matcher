---
phase: 06
plan: 01
subsystem: matching-engine
tags: [database, sqlalchemy, alembic, matching, configuration]
requires:
  - phase-01: Dual-database infrastructure for PostgreSQL models
  - phase-02: Job state machine columns on incoming_emails
dependencies:
  requires:
    - "01-01": PostgreSQL Base and database connection
    - "01-02": incoming_emails table for foreign key reference
  provides:
    - matching_thresholds: Database-driven threshold configuration
    - creditor_inquiries: Source of truth for matching queries
    - match_results: Match scoring with JSONB explainability
  affects:
    - "06-02": Matching signals will query matching_thresholds
    - "06-03": Scoring engine will write to match_results
    - "06-04": Gap analysis will use ambiguity_gap column
tech-stack:
  added:
    - sqlalchemy.dialects.postgresql.JSONB: Structured explainability storage
  patterns:
    - database-driven-config: Runtime threshold changes without deployment
    - category-based-thresholds: Different rules for different creditor types
    - jsonb-explainability: Queryable scoring details for debugging
key-files:
  created:
    - app/models/matching_config.py: MatchingThreshold model
    - app/models/creditor_inquiry.py: CreditorInquiry model
    - app/models/match_result.py: MatchResult model with JSONB
    - alembic/versions/20260205_1923_add_matching_infrastructure.py: Migration with default thresholds
  modified:
    - app/models/__init__.py: Export matching models
decisions:
  - id: MATCH-CONFIG-01
    choice: Database-driven thresholds via matching_thresholds table
    reasoning: Enables runtime calibration without code deployment
    alternatives: Hardcoded constants, YAML config files
  - id: MATCH-CONFIG-02
    choice: JSONB for scoring_details (not JSON)
    reasoning: Enables structured queries on explainability data (e.g., WHERE scoring_details->>'method' = 'fuzzy')
    alternatives: JSON type (no indexes), separate columns (rigid schema)
  - id: MATCH-CONFIG-03
    choice: Default weights 40% name, 60% reference
    reasoning: From CONTEXT.MD - reference numbers are more reliable than fuzzy name matching
    alternatives: 50/50 split, equal weights for all signals
  - id: MATCH-CONFIG-04
    choice: Keep component score columns for backward compatibility
    reasoning: Existing code may query client_name_score etc. - JSONB is additive, not replacement
    alternatives: Remove legacy columns, migration to update queries
metrics:
  duration: 37 minutes
  completed: 2026-02-05
---

# Phase 6 Plan 01: Matching Engine Database Infrastructure Summary

Database models and migration for matching engine reconstruction.

## One-Liner

Created MatchingThreshold (database-driven config), CreditorInquiry (matching source of truth), and MatchResult (JSONB explainability) models with default thresholds (min_match=0.70, gap=0.15, weights 40/60).

## Overview

This plan established the database foundation for the matching engine. Three SQLAlchemy models were created: MatchingThreshold for category-based configuration, CreditorInquiry as the source of truth for matching queries, and MatchResult with JSONB scoring_details for explainability. An Alembic migration creates the tables, indexes, and inserts default threshold values.

**Why this matters:** Database schema must exist before matching logic can query thresholds or store results. JSONB enables structured queries on scoring details for debugging and calibration.

## Tasks Completed

### Task 1: Create MatchingThreshold model for database-driven configuration
**Status:** ✓ Complete
**Commit:** cbaa447

Created `app/models/matching_config.py` with MatchingThreshold SQLAlchemy model:
- Category-based configuration (default, bank, inkasso)
- Supports both threshold values and weight configuration
- Unique constraint on (category, threshold_type, weight_name)
- Index on (category, threshold_type) for efficient lookups
- Enables runtime threshold changes without deployment

**Key implementation details:**
- Numeric(5, 4) for thresholds: 0.0000 to 1.0000 precision
- Weight configuration uses weight_name and weight_value columns
- Description column for documentation

### Task 2: Add CreditorInquiry and MatchResult models to active codebase
**Status:** ✓ Complete
**Commit:** f607fe6

Created two models from _existing-code and updated to current requirements:

**CreditorInquiry** (`app/models/creditor_inquiry.py`):
- Source of truth for matching incoming emails
- Client information: client_name, client_name_normalized for fuzzy matching
- Creditor information: creditor_name, creditor_email, creditor_name_normalized
- Debt context: debt_amount, reference_number
- Zendesk tracking: zendesk_ticket_id, zendesk_side_conversation_id
- Status tracking: status, response_received
- Timestamps: sent_at, created_at, updated_at

**MatchResult** (`app/models/match_result.py`):
- Match scoring with explainability
- Foreign keys: incoming_email_id, creditor_inquiry_id
- Overall scoring: total_score, confidence_level
- **JSONB scoring_details** for structured explainability queries
- **ambiguity_gap column** for threshold calibration (difference between #1 and #2)
- Component scores (backward compatibility): client_name_score, creditor_score, time_relevance_score, reference_number_score, debt_amount_score
- Ranking and decision: rank, selected_as_match, selection_method

**Updated `app/models/__init__.py`**:
- Added imports for MatchingThreshold, CreditorInquiry, MatchResult
- Added to __all__ list

### Task 3: Create Alembic migration with default threshold values
**Status:** ✓ Complete
**Commit:** f2cf509

Created migration `alembic/versions/20260205_1923_add_matching_infrastructure.py`:

**Tables created:**
1. `matching_thresholds`: Category-based threshold and weight configuration
2. `match_results`: Match scoring with JSONB scoring_details and ambiguity_gap
3. `creditor_inquiries`: Defensive CREATE IF NOT EXISTS (owned by Node.js portal)

**Indexes created:**
- `idx_matching_thresholds_lookup` on (category, threshold_type)
- `idx_match_results_email_id` on incoming_email_id
- `idx_match_results_creditor_inquiry_id` on creditor_inquiry_id
- Partial index `idx_match_results_selected` on (selected_as_match, calculated_at) WHERE selected_as_match=true
- All creditor_inquiries indexes (IF NOT EXISTS pattern)

**Default threshold configuration inserted:**
```sql
-- Thresholds
('default', 'min_match', 0.7000, 'Minimum score for any match consideration')
('default', 'gap_threshold', 0.1500, 'Gap between #1 and #2 for auto-match')

-- Weights (CONTEXT.MD: 40% name, 60% reference)
('default', 'weight', 'client_name', 0.4000, 'Weight for client name signal')
('default', 'weight', 'reference_number', 0.6000, 'Weight for reference number signal')
```

**Downgrade:**
- Drops match_results and matching_thresholds tables
- Does NOT drop creditor_inquiries (owned by Node.js portal)

## Decisions Made

### 1. Database-Driven Thresholds
**Decision:** Store thresholds in PostgreSQL matching_thresholds table instead of code constants.

**Reasoning:** Enables runtime calibration without code deployment. Operations team can adjust thresholds based on production metrics.

**Alternatives considered:**
- Hardcoded constants: Requires deployment for changes
- YAML config files: Requires container restart, no historical tracking

**Impact:** Threshold changes become operational tasks instead of engineering tasks.

### 2. JSONB for scoring_details
**Decision:** Use JSONB type for scoring_details instead of JSON.

**Reasoning:** Enables structured queries on explainability data:
```sql
-- Find matches using specific method
SELECT * FROM match_results WHERE scoring_details->>'method' = 'fuzzy';

-- Find matches with high name score
SELECT * FROM match_results WHERE (scoring_details->'client_name_match'->>'fuzzy_ratio')::float > 0.9;
```

**Alternatives considered:**
- JSON type: No indexing support
- Separate columns: Rigid schema, can't add new signals without migration

**Impact:** Debugging and calibration queries become possible without exporting to external tools.

### 3. Default Weights: 40% Name, 60% Reference
**Decision:** Set default weights to 40% client_name, 60% reference_number.

**Reasoning:** From CONTEXT.MD - reference numbers (AZ, Kundennummer) are more reliable than fuzzy name matching. German names have variations (Müller vs Mueller) that reduce name matching reliability.

**Alternatives considered:**
- 50/50 split: Treats all signals equally
- Equal weights for all signals: Dilutes strong signals

**Impact:** Matching engine prioritizes reference number matches over name matches by default.

### 4. Keep Component Score Columns
**Decision:** Keep client_name_score, creditor_score, etc. columns alongside JSONB scoring_details.

**Reasoning:** Backward compatibility - existing code may query these columns. JSONB is additive enhancement, not replacement.

**Alternatives considered:**
- Remove legacy columns: Requires migrating existing queries
- Only use JSONB: Breaks backward compatibility

**Impact:** No breaking changes to existing queries, JSONB adoption can be gradual.

## Deviations from Plan

None - plan executed exactly as written.

## Verification Results

All verification criteria met:

1. ✓ All three models import without error: `python3 -c "from app.models import MatchingThreshold, CreditorInquiry, MatchResult"`
2. ✓ Migration file exists in alembic/versions/20260205_1923_add_matching_infrastructure.py
3. ✓ Migration includes default threshold configuration (min_match=0.70, gap_threshold=0.15, weights 40/60)
4. ✓ JSONB type used for scoring_details (verified in source code)

## Success Criteria

All success criteria satisfied:

- ✓ MatchingThreshold model with category-based configuration
- ✓ CreditorInquiry model copied from _existing-code (no schema changes)
- ✓ MatchResult model with JSONB scoring_details and ambiguity_gap column
- ✓ Alembic migration ready to run
- ✓ Default thresholds: min_match=0.70, gap_threshold=0.15, weights 40/60

## Next Phase Readiness

**Blockers:** None

**Dependencies satisfied:**
- PostgreSQL models can be imported from app.models
- Migration ready for `alembic upgrade head`
- Default thresholds configured for immediate use

**Next plan prerequisites:**
- 06-02 (Matching Signals): Can query matching_thresholds for weights
- 06-03 (Scoring Engine): Can write to match_results
- 06-04 (Gap Analysis): Can use ambiguity_gap column for calibration

## Technical Notes

### JSONB vs JSON Type
JSONB provides several advantages:
1. **Indexing:** Can create GIN indexes on JSONB columns
2. **Query performance:** Binary format is faster for queries
3. **Duplicate key elimination:** JSONB automatically deduplicates object keys

Example query pattern:
```python
# Find matches with specific scoring method
db.query(MatchResult).filter(
    MatchResult.scoring_details['method'].astext == 'fuzzy'
).all()
```

### Migration Safety
The migration uses defensive patterns:
- `CREATE TABLE IF NOT EXISTS` for creditor_inquiries (owned by Node.js portal)
- `CREATE INDEX IF NOT EXISTS` for all creditor_inquiries indexes
- Does not drop creditor_inquiries in downgrade

### Default Threshold Values
Default configuration enables immediate use:
- **min_match=0.70:** Only consider matches with 70%+ total score
- **gap_threshold=0.15:** Auto-match if #1 beats #2 by 15%+ (e.g., 0.85 vs 0.70)
- **Weights:** 40% name + 60% reference = 100% total

Example scenario:
- Email has fuzzy name match (score 0.8) and exact reference match (score 1.0)
- Total score = (0.8 × 0.4) + (1.0 × 0.6) = 0.32 + 0.6 = 0.92
- Above min_match threshold (0.70) → candidate
- If next best candidate scores 0.75, gap = 0.92 - 0.75 = 0.17
- Gap exceeds threshold (0.15) → auto-match

## Files Changed

### Created
- `app/models/matching_config.py` (56 lines)
- `app/models/creditor_inquiry.py` (55 lines)
- `app/models/match_result.py` (91 lines)
- `alembic/versions/20260205_1923_add_matching_infrastructure.py` (218 lines)

### Modified
- `app/models/__init__.py` (3 imports added, 3 exports added)

**Total:** 4 files created, 1 file modified, 420 lines added

## Deployment Notes

**Migration:** Run `alembic upgrade head` to create tables and insert default thresholds.

**Verification:** After migration:
```sql
-- Verify thresholds
SELECT * FROM matching_thresholds ORDER BY category, threshold_type, weight_name;

-- Should return 4 rows:
-- default | min_match | 0.7000
-- default | gap_threshold | 0.1500
-- default | weight | client_name | 0.4000
-- default | weight | reference_number | 0.6000
```

**Rollback:** `alembic downgrade -1` to drop matching infrastructure (preserves creditor_inquiries).

## Performance Considerations

**Index usage:**
- `idx_matching_thresholds_lookup`: Efficient threshold queries by category
- `idx_match_results_email_id`: Fast lookup of all matches for an email
- Partial index on selected_as_match: Optimizes queries for historical matches

**JSONB storage:**
- Slightly larger storage than JSON (binary format)
- Faster query performance (no JSON parsing)
- Trade-off is acceptable for explainability queries

## Open Questions

None - all requirements satisfied.

## Links

- Plan: `.planning/phases/06-matching-engine-reconstruction/06-01-PLAN.md`
- CONTEXT: `.planning/phases/06-matching-engine-reconstruction/06-CONTEXT.md`
- Next: `06-02-PLAN.md` (Matching Signals)
