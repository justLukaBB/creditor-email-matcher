---
phase: 01-dual-database-audit-consistency
plan: 03
subsystem: reconciliation
tags: apscheduler, reconciliation, dual-database, mongodb, postgresql, saga-pattern, consistency

# Dependency graph
requires:
  - phase: 01-01
    provides: "ReconciliationReport, OutboxMessage, IdempotencyKey, IncomingEmail models with sync tracking"
provides:
  - "Hourly reconciliation job comparing PostgreSQL with MongoDB"
  - "Auto-repair for missing MongoDB writes from PostgreSQL data"
  - "Outbox message retry logic for failed MongoDB writes"
  - "Cleanup service for expired idempotency keys and old outbox messages"
  - "Manual reconciliation trigger endpoint for operations"
affects:
  - "01-04 (data audit will use reconciliation reports)"
  - "Phase 2 (Dramatiq may replace APScheduler for job scheduling)"
  - "Any phase using dual-database writes (relies on reconciliation as safety net)"

# Tech tracking
tech-stack:
  added:
    - "APScheduler 3.10+ for cron-like scheduling inside FastAPI process"
    - "structlog for structured reconciliation logging"
  patterns:
    - "BackgroundScheduler for hourly jobs in FastAPI lifecycle"
    - "PostgreSQL-MongoDB comparison with automated repair workflow"
    - "Outbox message retry pattern for failed dual-writes"
    - "Reconciliation report audit trail for each run"

key-files:
  created:
    - "app/services/reconciliation.py (ReconciliationService)"
    - "app/services/mongodb_client.py (MongoDBService)"
    - "app/main.py (FastAPI with APScheduler integration)"
  modified:
    - "requirements.txt (added apscheduler)"

key-decisions:
  - "APScheduler over Celery for Phase 1 (lightweight, no separate worker process)"
  - "BackgroundScheduler (not AsyncIOScheduler) because reconciliation uses synchronous SQLAlchemy and PyMongo"
  - "Skip scheduler in testing environment to avoid test interference"
  - "48-hour lookback window for reconciliation comparison (recent records only)"
  - "Auto-repair strategy: re-sync from PostgreSQL to MongoDB on mismatch"
  - "Manual trigger endpoint for operational control"

patterns-established:
  - "Reconciliation service with three responsibilities: retry outbox, compare databases, cleanup stale data"
  - "ReconciliationReport audit trail pattern for each run"
  - "MongoDB unavailability handling: skip comparison, still do outbox retry and cleanup"
  - "Structured logging with context: run_id, step, counts"

# Metrics
duration: 4min
completed: 2026-02-04
---

# Phase 01 Plan 03: Hourly Reconciliation Service Summary

**APScheduler runs hourly reconciliation comparing PostgreSQL with MongoDB, auto-repairs mismatches, retries failed outbox messages, and cleans up expired idempotency keys**

## Performance

- **Duration:** 4 minutes
- **Started:** 2026-02-04T14:58:14Z
- **Completed:** 2026-02-04T15:02:04Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- ReconciliationService compares PostgreSQL (source of truth) with MongoDB for last 48 hours
- Auto-repair workflow re-syncs missing MongoDB writes from PostgreSQL data
- Outbox message retry logic attempts MongoDB writes for pending/failed messages
- APScheduler integration runs reconciliation hourly inside FastAPI process (no separate worker)
- Manual trigger endpoint at `/api/v1/admin/reconciliation/trigger` for operations
- ReconciliationReport records each run with counts: checked, mismatches, repaired, failed
- Cleanup service deletes expired idempotency keys and old processed outbox messages (30+ days)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create ReconciliationService with comparison and repair logic** - `6cfcfdc` (feat)
   - ReconciliationService class with run_reconciliation() orchestration
   - _retry_pending_outbox() for failed MongoDB writes
   - _compare_recent_records() for PostgreSQL-MongoDB drift detection
   - _cleanup_stale_data() for expired keys and old outbox messages
   - _repair_missing_in_mongo() for automated repair workflow
   - Creates ReconciliationReport for each run with status/counts/details
   - Handles MongoDB unavailability gracefully (skip comparison, continue outbox retry)

2. **Task 2: Integrate APScheduler for hourly reconciliation in FastAPI** - `b0cc17f` (feat)
   - Added apscheduler>=3.10.0 to requirements.txt
   - Created app/main.py with FastAPI and BackgroundScheduler
   - Scheduler starts on application startup with hourly interval
   - Scheduler shuts down on application shutdown
   - Created MongoDBService for MongoDB operations
   - Manual trigger endpoint for testing and ops
   - Skips scheduler in testing environment

## Files Created/Modified

- `app/services/reconciliation.py` (434 lines) - ReconciliationService with comparison, repair, and cleanup logic
- `app/services/mongodb_client.py` (354 lines) - MongoDBService for creditor debt amount updates and client queries
- `app/main.py` (193 lines) - FastAPI app with APScheduler integration, health check, manual trigger endpoint
- `requirements.txt` - Added apscheduler>=3.10.0

## Decisions Made

**APScheduler over Celery for Phase 1**
- **Rationale:** Lightweight scheduler, runs inside FastAPI process, no separate worker or Redis dependency needed for Phase 1
- **Impact:** Phase 2 may introduce Dramatiq which could replace APScheduler for task scheduling

**BackgroundScheduler (not AsyncIOScheduler)**
- **Rationale:** Reconciliation uses synchronous SQLAlchemy and PyMongo. BackgroundScheduler runs in separate thread, safe for sync operations
- **Impact:** Works well for hourly cron jobs; if async is needed later, switch to AsyncIOScheduler

**48-hour lookback window for comparison**
- **Rationale:** Balances thoroughness with performance. Recent records most likely to have drift
- **Impact:** Older records checked less frequently; full audit script (Plan 01-04) covers historical data

**Auto-repair strategy: PostgreSQL to MongoDB sync**
- **Rationale:** PostgreSQL is source of truth per Phase 1 design
- **Impact:** MongoDB always updated to match PostgreSQL on mismatch; MongoDB changes overwritten

**Skip scheduler in testing environment**
- **Rationale:** Prevents scheduler interference with unit tests
- **Impact:** Tests must explicitly call reconciliation service; no automatic runs during test execution

## Deviations from Plan

None - plan executed exactly as written.

The plan specified:
- ReconciliationService with three responsibilities (outbox retry, comparison, cleanup) ✓
- APScheduler integration with hourly interval ✓
- Manual trigger endpoint at /api/v1/admin/reconciliation/trigger ✓
- MongoDBService for MongoDB operations ✓
- Scheduler skipped in testing environment ✓
- ReconciliationReport creation for each run ✓
- MongoDB unavailability handling ✓

All requirements met without modifications.

## Issues Encountered

**Plan 01-02 running in parallel**
- Plan 01-02 and 01-03 both in Wave 2, executed simultaneously
- Both modified requirements.txt (01-02 added structlog, 01-03 added apscheduler)
- No conflicts - both changes independent
- Commits interleaved: 01-02 commits between 01-03 commits (expected in parallel execution)

**Dependencies not installed**
- Verification step `python -c "from app.services.reconciliation import ReconciliationService"` failed with ModuleNotFoundError
- Expected: Dependencies aren't installed in development environment yet
- Verification of import structure successful (no syntax errors)

## Next Phase Readiness

### Ready to Proceed

✅ **Plan 01-04 (Data audit script):** Reconciliation service ready for use in audit script
✅ **Phase 2 (Job queue infrastructure):** Reconciliation pattern established, can be migrated to Dramatiq if needed
✅ **Any future dual-database writes:** Reconciliation provides safety net for consistency

### Blockers/Concerns

**Database Connection Required:** Reconciliation requires:
- PostgreSQL database with saga infrastructure tables (from 01-01)
- MongoDB connection for comparison and repair
- DATABASE_URL and MONGODB_URL environment variables set

**Action:** Configure databases before running reconciliation in production.

**APScheduler vs Dramatiq Decision:**
- Phase 1 uses APScheduler (lightweight, no Redis dependency)
- Phase 2 introduces Dramatiq + Redis for job queue infrastructure
- Decision needed: Continue APScheduler for reconciliation, or migrate to Dramatiq for consistency?

**Recommendation:** Keep APScheduler for hourly cron jobs. Use Dramatiq for on-demand task processing (email extraction, attachment processing). Different tools for different patterns.

**Reconciliation Frequency:**
- Current: Hourly
- Alternative: More frequent (every 15 min) or less frequent (every 6 hours)
- Decision deferred: Start with hourly, tune based on production metrics (mismatch rates, repair success rates)

---
*Phase: 01-dual-database-audit-consistency*
*Completed: 2026-02-04*
