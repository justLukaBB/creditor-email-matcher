---
phase: 01-dual-database-audit-consistency
plan: 04
subsystem: audit
tags: audit, consistency, postgresql, mongodb, reporting, health-score, recovery-plan

# Dependency graph
requires:
  - phase: 01-01
    provides: "IncomingEmail model with extracted_data and processing status fields"
  - phase: 01-02
    provides: "MongoDB client singleton for database queries"
provides:
  - "AuditService with reusable audit logic comparing PostgreSQL and MongoDB"
  - "CLI audit script for one-time data consistency check"
  - "Recovery plan categorization: auto-recoverable, manual_review, stalled"
  - "Health score calculation: (total - mismatches) / total"
  - "JSON report export with detailed mismatch information"
affects:
  - "Operations team (can run audit_consistency.py to check system health)"
  - "Phase 2 onward (establishes baseline consistency before new processing pipeline)"

# Tech tracking
tech-stack:
  added:
    - "structlog for audit logging (already in requirements.txt)"
  patterns:
    - "Standalone CLI script for database auditing"
    - "Health score calculation for quantifying consistency"
    - "Recovery plan with action categorization"
    - "JSON report export for programmatic analysis"

key-files:
  created:
    - "app/services/audit.py (AuditService class)"
    - "scripts/audit_consistency.py (CLI audit script)"
  modified: []

key-decisions:
  - "Exit code reflects health score (0 for healthy >= 0.95, 1 for issues)"
  - "30-day default lookback for audit period"
  - "Standalone script works without running FastAPI app"
  - "Recovery plan categorizes mismatches: re-sync, manual_review, stalled, no_action"
  - "Health score calculated as: (total_checked - total_issues) / total_checked"

patterns-established:
  - "AuditService with _find_mongo_match() using same matching logic as mongodb_client.py"
  - "Mismatch types: pg_matched_no_mongo_record, amount_mismatch, processing_failed, stalled_processing, incomplete_extraction"
  - "Severity levels: high, medium, low"
  - "JSON report saved to scripts/audit_report_{timestamp}.json"

# Metrics
duration: 5min
completed: 2026-02-04
---

# Phase 01 Plan 04: Data Consistency Audit Script Summary

**AuditService with CLI script comparing PostgreSQL incoming_emails with MongoDB clients.final_creditor_list, mismatch detection by type, recovery plan generation with action categorization, health score calculation, and JSON export**

## Performance

- **Duration:** 5 minutes (estimated, checkpoint approved)
- **Started:** 2026-02-04T15:02:04Z (after 01-03 completion)
- **Completed:** 2026-02-04T15:07:04Z (estimated)
- **Tasks:** 2 (1 auto + 1 checkpoint)
- **Files modified:** 2

## Accomplishments

- AuditService compares PostgreSQL incoming_emails with MongoDB clients.final_creditor_list for data consistency
- CLI audit script produces formatted report with summary, mismatches, recovery plan, and health score
- Recovery plan categorizes mismatches into: auto-recoverable (re-sync), manual_review, stalled_processing, no_action
- Health score calculated as (total_checked - total_issues) / total_checked
- JSON report exported to scripts/audit_report_{timestamp}.json for programmatic analysis
- Exit code reflects health: 0 for >= 0.95, 1 for < 0.95, 2 for errors
- Handles missing databases gracefully (reports MongoDB unavailable, doesn't crash)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create AuditService and CLI audit script** - `fb47b9c` (feat)
   - AuditService class with run_full_audit(lookback_days=30)
   - Mismatch detection: pg_matched_no_mongo_record, amount_mismatch, processing_failed, stalled_processing, incomplete_extraction
   - Recovery plan generation with action categorization
   - _find_mongo_match() using aktenzeichen > name search (same logic as mongodb_client.py)
   - CLI script with argparse, formatted report output, JSON export
   - Exit code based on health score

2. **Task 2: Checkpoint - Human verification** - APPROVED (checkpoint)
   - User verified audit script output and code structure
   - Confirmed Phase 1 infrastructure complete

## Files Created/Modified

- `app/services/audit.py` (377 lines) - AuditService with comparison logic and mismatch categorization
- `scripts/audit_consistency.py` (222 lines) - CLI audit script with formatted reporting and JSON export

## Decisions Made

**Exit code reflects health score**
- **Rationale:** Enables integration with monitoring systems and CI/CD pipelines
- **Impact:** Exit 0 (healthy >= 0.95), exit 1 (issues < 0.95), exit 2 (errors)

**30-day default lookback**
- **Rationale:** Balances coverage with performance; recent data most relevant
- **Impact:** Older records not audited by default (use --lookback-days flag for longer periods)

**Standalone script without FastAPI app**
- **Rationale:** Operational simplicity; no need to start web server for audit
- **Impact:** Script initializes DB connections directly via init_db() and SessionLocal

**Recovery plan categorization**
- **Rationale:** Clear action guidance for operations team
- **Impact:** Auto-recoverable (re-sync) vs manual_review vs stalled vs no_action

**Health score calculation**
- **Rationale:** Quantifies consistency as single metric for monitoring
- **Impact:** (total_checked - total_issues) / total_checked; 1.0 = perfect, 0.0 = all mismatches

## Deviations from Plan

None - plan executed exactly as written.

The plan specified:
- AuditService with run_full_audit() comparing PostgreSQL and MongoDB ✓
- Mismatch detection with types, severity, and recovery actions ✓
- CLI script with --lookback-days argument ✓
- Formatted report and JSON export ✓
- Exit code based on health score ✓
- Handles missing databases gracefully ✓
- Works without running FastAPI app ✓

All requirements met without modifications.

## Issues Encountered

None - straightforward implementation based on reconciliation service patterns from Plan 01-03.

## User Setup Required

**Before running audit:**
1. Install dependencies: `pip install -r requirements.txt` (includes structlog)
2. Set DATABASE_URL environment variable for PostgreSQL
3. Set MONGODB_URL environment variable for MongoDB
4. Run migration: `alembic upgrade head` (creates IncomingEmail and related tables)

**To run audit:**
```bash
python scripts/audit_consistency.py --lookback-days 30
```

**To view JSON report:**
```bash
cat scripts/audit_report_20260204_150704.json | jq
```

## Next Phase Readiness

### Phase 1 Complete

✅ **Plan 01-01:** Database models with saga infrastructure (OutboxMessage, IdempotencyKey, ReconciliationReport)
✅ **Plan 01-02:** DualDatabaseWriter saga pattern with idempotency
✅ **Plan 01-03:** ReconciliationService with hourly APScheduler job
✅ **Plan 01-04:** AuditService with CLI audit script (this plan)

**Phase 1 Success Criteria Met:**
- Saga pattern infrastructure in PostgreSQL ✓
- Dual-database writer with transactional outbox ✓
- Hourly reconciliation with auto-repair ✓
- Audit script quantifies existing mismatches with recovery plan ✓

### Ready for Phase 2

✅ **Phase 2 (Job Queue Infrastructure):** Dual-database foundation stable, ready for Dramatiq + Redis integration
✅ **Phase 3 (Email Processing):** IncomingEmail model ready, processing status tracking in place
✅ **Phase 4 (Content Extraction):** extracted_data JSONB field ready for structured data

### Blockers/Concerns

**Production Deployment Required:**
- Phase 1 code complete but not deployed to production
- Need to run audit against production databases to establish baseline consistency
- Recommendation: Deploy Phase 1 to production, run audit, address any high-severity mismatches before building Phase 2

**Audit Frequency:**
- Current: One-time CLI script
- Alternative: Scheduled audit report generation (daily/weekly)
- Decision deferred: Start with manual runs, automate if needed after production data observed

**MongoDB Data Model Assumption:**
- Audit assumes MongoDB has clients collection with final_creditor_list array
- May need adjustment if MongoDB schema differs from assumption
- Recommendation: Verify MongoDB schema matches AuditService expectations before production deployment

---
*Phase: 01-dual-database-audit-consistency*
*Completed: 2026-02-04*
