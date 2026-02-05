---
phase: 05-multi-agent-pipeline-validation
plan: 04
subsystem: database, api, validation, review-queue
tags: [postgresql, sqlalchemy, fastapi, manual-review, concurrency, for-update-skip-locked]

# Dependency graph
requires:
  - phase: 01-dual-database-audit-consistency
    provides: IncomingEmail model with PostgreSQL foundation
  - phase: 05-01-multi-agent-pipeline-foundation
    provides: agent_checkpoints column and validation utilities
provides:
  - ManualReviewQueue model with claim tracking and resolution workflow
  - Manual review REST API with claim concurrency control
  - Review queue enqueue helpers with priority mapping
  - FOR UPDATE SKIP LOCKED pattern for concurrent reviewer access
  - Priority-based queue ordering (1=highest, 10=lowest)
affects: [
  05-05-end-to-end-integration,
  agent-execution-flows,
  human-in-the-loop-workflow
]

# Tech tracking
tech-stack:
  added: []
  patterns: [
    "FOR UPDATE SKIP LOCKED for row-level concurrency control",
    "Priority queue pattern with partial indexes",
    "Review queue service layer pattern",
    "Pydantic request/response models for API validation"
  ]

key-files:
  created:
    - app/models/manual_review.py
    - app/routers/manual_review.py
    - app/services/validation/review_queue.py
    - alembic/versions/20260205_1829_add_manual_review_queue_table.py
  modified:
    - app/models/__init__.py
    - app/routers/__init__.py
    - app/main.py
    - app/services/validation/__init__.py

key-decisions:
  - "FOR UPDATE SKIP LOCKED for claim concurrency (prevents duplicate claims without serializing all transactions)"
  - "Priority mapping by review reason (manual_escalation=1, validation_failed=2, conflict=3, low_confidence=5)"
  - "Duplicate detection: skip enqueue if unresolved item exists for same email_id"
  - "Partial indexes on resolved_at for efficient pending/claimed queries"

patterns-established:
  - "Review queue workflow: unclaimed -> claimed -> resolved"
  - "Claim-next endpoint returns highest priority unclaimed item automatically"
  - "Review details stored as JSONB for flexible context"
  - "Resolution status enum: approved, rejected, corrected, escalated, spam"

# Metrics
duration: 4.8min
completed: 2026-02-05
---

# Phase 05 Plan 04: Manual Review Queue Infrastructure Summary

**ManualReviewQueue model with FOR UPDATE SKIP LOCKED claim concurrency, REST API with 6 endpoints, and priority-based enqueue helpers for human review workflow**

## Performance

- **Duration:** 4.8 min (286 seconds)
- **Started:** 2026-02-05T17:28:52Z
- **Completed:** 2026-02-05T17:33:38Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments
- ManualReviewQueue database model with claim tracking and resolution workflow
- REST API with list, stats, claim, claim-next, resolve, and email detail endpoints
- Review queue service helpers with duplicate detection and priority mapping
- FOR UPDATE SKIP LOCKED concurrency control prevents duplicate claims by concurrent reviewers
- Alembic migration with partial indexes for efficient pending/claimed queries

## Task Commits

Each task was committed atomically:

1. **Task 1: Create ManualReviewQueue model and migration** - `1d1c158` (feat)
2. **Task 2: Create manual review API endpoints** - `ea57888` (feat)
3. **Task 3: Add helper to enqueue review items** - `35a1856` (feat)

## Files Created/Modified

**Created:**
- `app/models/manual_review.py` - ManualReviewQueue SQLAlchemy model with claim/resolution tracking
- `app/routers/manual_review.py` - REST API for manual review queue (6 endpoints)
- `app/services/validation/review_queue.py` - Enqueue helpers with duplicate detection and priority mapping
- `alembic/versions/20260205_1829_add_manual_review_queue_table.py` - Migration with partial indexes

**Modified:**
- `app/models/__init__.py` - Export ManualReviewQueue
- `app/routers/__init__.py` - Export manual_review_router
- `app/main.py` - Register manual_review_router
- `app/services/validation/__init__.py` - Export review queue helpers

## Decisions Made

**1. FOR UPDATE SKIP LOCKED for claim concurrency**
- Prevents duplicate claims without serializing all transactions
- Allows multiple reviewers to claim-next simultaneously without conflicts
- Claimed item returns 404 to other concurrent requests (already locked)

**2. Priority mapping by review reason**
- manual_escalation=1 (highest), validation_failed=2, conflict_detected=3, low_confidence=5 (medium), duplicate_suspected=7 (low)
- Default priority=5 for unknown reasons
- Allows filtering by priority range in list endpoint

**3. Duplicate detection in enqueue_for_review**
- Check for existing unresolved item for same email_id
- Skip enqueue and return existing item if found
- Prevents review queue pollution with duplicate entries

**4. Partial indexes for efficient queries**
- `idx_manual_review_queue_pending` on (resolved_at, priority, created_at) WHERE resolved_at IS NULL
- `idx_manual_review_queue_claimed` on (claimed_at, resolved_at) WHERE claimed_at IS NOT NULL AND resolved_at IS NULL
- Optimizes most common queries (list pending, list claimed)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for Phase 05-05 (End-to-End Integration):**
- Manual review queue infrastructure complete
- Agents can enqueue items using `enqueue_for_review(db, email_id, reason, details)`
- Human reviewers can claim and resolve items via REST API
- Duplicate detection prevents queue pollution
- Priority-based ordering ensures critical items reviewed first

**Integration points for agents:**
```python
from app.services.validation import enqueue_for_review

# In agent logic when confidence < threshold or conflicts detected
if needs_review:
    enqueue_for_review(
        db=db,
        email_id=email.id,
        reason="low_confidence",  # or "conflict_detected", "validation_failed"
        details={
            "confidence": 0.45,
            "conflicts": ["amount_mismatch"],
            "extracted_values": {...}
        }
    )
```

**API endpoints for UI:**
- GET /api/v1/reviews - List pending items (with filters)
- POST /api/v1/reviews/claim-next - Claim highest priority item
- POST /api/v1/reviews/{id}/resolve - Resolve with status and notes
- GET /api/v1/reviews/{id}/email - Get email details for review

**No blockers or concerns.**

---
*Phase: 05-multi-agent-pipeline-validation*
*Completed: 2026-02-05*
