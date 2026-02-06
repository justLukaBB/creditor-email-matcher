---
phase: 09-production-hardening-monitoring
plan: 05
subsystem: monitoring
tags: [monitoring, sentry, circuit-breaker, metrics, correlation-id, observability]

# Dependency graph
requires:
  - phase: 09-01
    provides: Correlation ID middleware and logging infrastructure
  - phase: 09-02
    provides: Circuit breaker implementation
  - phase: 09-03
    provides: Metrics collection system
  - phase: 09-04
    provides: Sentry error tracking and processing reports
provides:
  - Full monitoring integration in email processing pipeline
  - Correlation ID propagation from webhook through actor
  - Circuit breakers on all external services (Claude API, MongoDB, GCS)
  - Metrics recorded at key pipeline stages
  - Sentry context set at actor start
  - Processing reports generated per email
affects: [10-deployment, operations, debugging]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Correlation ID propagation pattern: HTTP context -> actor parameter -> restore context"
    - "Circuit breaker wrapping pattern: breaker.call(service_method, args)"
    - "Monitoring integration pattern: Initialize at start, track at stages, record in finally"

key-files:
  created: []
  modified:
    - app/main.py
    - app/routers/webhook.py
    - app/actors/email_processor.py
    - app/services/entity_extractor_claude.py
    - app/services/mongodb_client.py
    - app/services/storage/gcs_client.py
    - app/config.py

key-decisions:
  - "Restore correlation_id context at actor start (Dramatiq runs in separate threads/processes)"
  - "Record processing time in finally block to capture duration even on failure"
  - "Create processing report before final commit for transactional consistency"
  - "Don't fail processing if metrics or report generation fails (best-effort)"

patterns-established:
  - "Circuit breaker pattern: wrap external calls, log and re-raise CircuitBreakerError for actor retry"
  - "Breadcrumb pattern: add_breadcrumb at key stages for Sentry trace reconstruction"
  - "Metrics pattern: record_* methods with email_id for grouping"

# Metrics
duration: 29min
completed: 2026-02-06
---

# Phase 09 Plan 05: Monitoring Integration Summary

**Complete observability pipeline with correlation ID tracing, circuit breakers on Claude/MongoDB/GCS, metrics collection at pipeline stages, Sentry context, and processing reports**

## Performance

- **Duration:** 29 min
- **Started:** 2026-02-06T21:41:27Z
- **Completed:** 2026-02-06T22:10:40Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments
- Correlation ID flows from webhook through actor execution to all logs and Sentry
- Circuit breakers protect all external services (Claude API, MongoDB, GCS) per REQ-OPS-03
- Metrics recorded at key stages: token usage, confidence, processing time, errors (REQ-OPS-02)
- Sentry context set at actor start with email_id, actor name, correlation_id (REQ-OPS-04)
- Processing reports generated per email with extraction details (REQ-OPS-06)
- Auto-match notifications preserved in UPDATE_AND_NOTIFY route (REQ-OPS-05)
- All monitoring integrated without breaking existing pipeline flow

## Task Commits

Each task was committed atomically:

1. **Task 1: Initialize monitoring at startup and propagate correlation ID** - `2c74ab1` (feat)
   - Import and call init_sentry() in app startup
   - Capture correlation_id in webhook and pass to process_email actor
   - Log monitoring_initialized with sentry_enabled flag

2. **Task 2: Add circuit breakers to external service calls** - `676a721` (feat)
   - Wrap Claude API calls with claude_breaker in entity_extractor_claude
   - Wrap MongoDB operations with mongodb_breaker
   - Wrap GCS downloads with gcs_breaker
   - Handle CircuitBreakerError by logging and re-raising for actor retry
   - Fixed missing anthropic_api_key and anthropic_model in config.py (bug)

3. **Task 3: Integrate metrics, Sentry context, and processing reports** - `33033bd` (feat)
   - Restore correlation_id context at actor start
   - Set Sentry context with email_id, actor, correlation_id
   - Initialize MetricsCollector with db session
   - Add breadcrumbs at key stages: intent, extraction, consolidation, matching
   - Record token_usage, confidence, processing_time, error metrics
   - Create processing_report before final commit
   - Preserve auto-match notification with breadcrumb

## Files Created/Modified
- `app/main.py` - Added init_sentry() call in startup_event, log monitoring_initialized
- `app/routers/webhook.py` - Import correlation_id, capture and pass to actor
- `app/actors/email_processor.py` - Full monitoring integration: correlation ID restore, Sentry context, breadcrumbs, metrics, processing report
- `app/services/entity_extractor_claude.py` - Wrap Claude API calls with circuit breaker
- `app/services/mongodb_client.py` - Wrap MongoDB operations with circuit breaker
- `app/services/storage/gcs_client.py` - Wrap GCS downloads with circuit breaker
- `app/config.py` - Added missing anthropic_api_key and anthropic_model settings (bug fix)

## Decisions Made

**1. Correlation ID restoration in actor**
- Dramatiq actors run in separate threads/processes where HTTP request context is not available
- Must explicitly restore correlation_id context using correlation_id_ctx.set() at actor start
- Enables correlation ID to appear in all actor logs and Sentry traces

**2. Processing time in finally block**
- Record processing time metric in finally block to capture duration even on failure
- Ensures duration tracked for all executions, not just successful ones

**3. Best-effort metrics and reports**
- Wrap metrics.record_* calls in try/except to prevent metrics failures from breaking processing
- Wrap create_processing_report in try/except with warning log
- Monitoring is important but not critical - don't fail email processing for monitoring errors

**4. Processing report before final commit**
- Create processing report before db.commit() for transactional consistency
- If commit fails, report won't exist (consistent state)
- Report includes duration_ms calculated before commit

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Added missing anthropic_api_key and anthropic_model settings**
- **Found during:** Task 2 (Circuit breaker integration)
- **Issue:** entity_extractor_claude.py references settings.anthropic_api_key and settings.anthropic_model, but these fields were not defined in app/config.py, causing AttributeError on import
- **Fix:** Added anthropic_api_key: Optional[str] = None and anthropic_model: str = "claude-sonnet-4-5-20250929" to Settings class
- **Files modified:** app/config.py
- **Verification:** Python import succeeds without AttributeError
- **Committed in:** 676a721 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Bug fix essential for correct configuration. No scope creep.

## Issues Encountered
None - all tasks executed as planned after bug fix.

## User Setup Required

None - no external service configuration required.

However, for full functionality:
- Set ANTHROPIC_API_KEY in environment for Claude API access
- Set SENTRY_DSN in environment for error tracking
- Set CIRCUIT_BREAKER_ALERT_EMAIL for circuit breaker alerts
- Set MONGODB_URL for database operations
- Set GCS credentials for attachment downloads

All settings are optional with graceful degradation (warnings logged if not configured).

## Next Phase Readiness

**Phase 9 Complete:**
- All monitoring infrastructure integrated into production pipeline
- Correlation ID tracing end-to-end (REQ-OPS-01)
- Metrics collection at key stages (REQ-OPS-02)
- Circuit breakers on external services (REQ-OPS-03)
- Sentry error tracking with context (REQ-OPS-04)
- Auto-match notifications preserved (REQ-OPS-05)
- Processing reports generated (REQ-OPS-06)

**Ready for Phase 10 (Deployment):**
- Production hardening complete
- Observability infrastructure in place
- System ready for production deployment with full monitoring

**No blockers** - all requirements satisfied.

---
*Phase: 09-production-hardening-monitoring*
*Completed: 2026-02-06*
