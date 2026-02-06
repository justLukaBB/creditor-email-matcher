---
phase: 09-production-hardening-monitoring
plan: 04
subsystem: monitoring
tags: [sentry, error-tracking, processing-reports, operational-visibility, sqlalchemy, fastapi]

# Dependency graph
requires:
  - phase: 09-01
    provides: JSON logging with correlation ID tracking
  - phase: 09-03
    provides: Operational metrics collection infrastructure
  - phase: 05
    provides: Multi-agent pipeline with agent_checkpoints JSONB
  - phase: 08
    provides: Database models and migrations pattern
provides:
  - Sentry error tracking with rich processing context (email_id, actor, correlation_id)
  - ProcessingReport model for per-email extraction audit trail
  - Processing report generation from multi-agent pipeline results
  - Query functions for reports by date range and review status
affects: [09-05-integration, operational-dashboards, error-investigation]

# Tech tracking
tech-stack:
  added: [sentry-sdk[fastapi]>=2.0.0]
  patterns:
    - Sentry context enrichment for email processing
    - Per-email processing report generation from agent checkpoints
    - Upsert pattern for idempotent report creation

key-files:
  created:
    - app/services/monitoring/error_tracking.py
    - app/models/processing_report.py
    - app/services/processing_reports.py
    - alembic/versions/20260206_add_processing_reports.py
  modified:
    - requirements.txt
    - app/config.py
    - app/services/monitoring/__init__.py
    - app/models/__init__.py

key-decisions:
  - "Sentry gracefully disabled when DSN not configured (development-friendly)"
  - "10% traces and profiles sample rate for production performance"
  - "Processing reports use upsert pattern (update existing or insert new)"
  - "Per-field confidence extracted from agent_checkpoints for visibility"

patterns-established:
  - "Sentry context helpers: set_processing_context() for consistent error enrichment"
  - "Report generation extracts structured data from agent checkpoints JSONB"
  - "Missing fields tracked explicitly for operational visibility"

# Metrics
duration: 3.4min
completed: 2026-02-06
---

# Phase 09 Plan 04: Error Tracking and Processing Reports Summary

**Sentry error tracking with email processing context and per-email extraction audit reports satisfying REQ-OPS-06**

## Performance

- **Duration:** 3.4 minutes
- **Started:** 2026-02-06T16:33:35Z
- **Completed:** 2026-02-06T16:36:56Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments

- Sentry SDK integrated with FastAPI for production error tracking
- set_processing_context() enriches errors with email_id, actor, and correlation_id
- ProcessingReport model captures per-email extraction details with per-field confidence
- create_processing_report() generates detailed reports from multi-agent pipeline results
- Query functions support date range filtering and needs_review filtering

## Task Commits

Each task was committed atomically:

1. **Task 1: Add Sentry SDK and error tracking helpers** - `43c8005` (feat)
2. **Task 2: Create ProcessingReport model and migration** - `11a94d3` (feat)
3. **Task 3: Create processing report generation service** - `daa2f15` (feat)

## Files Created/Modified

- `requirements.txt` - Added sentry-sdk[fastapi]>=2.0.0
- `app/config.py` - Added sentry_dsn and sentry_environment settings
- `app/services/monitoring/error_tracking.py` - Sentry initialization and context helpers
- `app/services/monitoring/__init__.py` - Exported error tracking functions
- `app/models/processing_report.py` - Per-email processing audit trail model
- `app/models/__init__.py` - Exported ProcessingReport model
- `app/services/processing_reports.py` - Report generation and query service
- `alembic/versions/20260206_add_processing_reports.py` - Migration creating processing_reports table

## Decisions Made

1. **Graceful Sentry degradation**: init_sentry() logs warning and returns if SENTRY_DSN is None, allowing development without Sentry configuration
2. **Sample rates**: 10% traces and 10% profiles for production performance balance
3. **Upsert pattern**: create_processing_report() updates existing report or inserts new for idempotence
4. **Per-field confidence**: Extracted from agent_3_consolidation checkpoint for detailed field-level visibility
5. **Source tracking**: Extraction metadata provides source information (email_body, pdf, scanned_pdf) per field
6. **Missing fields tracking**: Explicit list of required fields that couldn't be extracted for operational visibility

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - all implementations worked as expected. Sentry gracefully disabled in development environment (no DSN configured).

## User Setup Required

**Sentry configuration required for production error tracking.**

Environment variables to add:
```bash
SENTRY_DSN=https://[key]@[org].ingest.sentry.io/[project]
SENTRY_ENVIRONMENT=production  # Optional, defaults to ENVIRONMENT setting
```

After deployment:
1. Run migration: `alembic upgrade head` (creates processing_reports table)
2. Verify Sentry initialization in logs: `"Sentry initialized"`
3. Test error tracking by triggering an error in email processing
4. Check Sentry dashboard for error events with processing context

Verification:
- Errors should include tags: email_id, actor, correlation_id
- Processing context should show in Sentry event details
- Processing reports queryable: `SELECT * FROM processing_reports LIMIT 10;`

## Next Phase Readiness

**Ready for Phase 09 Plan 05**: Integration testing with complete monitoring stack.

Monitoring infrastructure complete:
- JSON logging with correlation ID (09-01)
- Circuit breakers with email alerts (09-02)
- Operational metrics with rollup (09-03)
- Error tracking and processing reports (09-04)

Next plan should integrate all monitoring into email processor actor:
- Set Sentry processing context at actor start
- Add breadcrumbs during multi-agent pipeline
- Create processing report after Agent 3 consolidation
- Record operational metrics throughout processing

Blockers/Concerns:
- None - all monitoring infrastructure ready for integration

---
*Phase: 09-production-hardening-monitoring*
*Completed: 2026-02-06*
