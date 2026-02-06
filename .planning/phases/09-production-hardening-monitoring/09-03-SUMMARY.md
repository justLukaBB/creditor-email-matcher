---
phase: 09-production-hardening-monitoring
plan: 03
subsystem: monitoring
tags: [postgresql, metrics, rollup, apscheduler, operational-metrics]

# Dependency graph
requires:
  - phase: 08-database-backed-prompt-management
    provides: "Prompt metrics rollup pattern with 30-day raw retention"
  - phase: 09-01
    provides: "Structured JSON logging infrastructure"
provides:
  - "OperationalMetrics and OperationalMetricsDaily models for pipeline health tracking"
  - "MetricsCollector service for recording queue depth, processing time, errors, token usage, confidence"
  - "Daily rollup job aggregating raw metrics with 30-day retention"
affects: [09-04-integration-testing, future-dashboard, future-alerting]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Operational metrics collection following prompt metrics rollup pattern"
    - "MetricsCollector follows DualDatabaseWriter pattern (caller controls transaction)"
    - "Labels JSON column for flexible metric segmentation"
    - "Scheduled rollup at 01:30 daily (30 min after prompt rollup)"

key-files:
  created:
    - app/models/operational_metrics.py
    - app/services/monitoring/metrics.py
    - app/services/metrics_rollup.py
    - alembic/versions/20260206_add_operational_metrics.py
  modified:
    - app/models/__init__.py
    - app/services/monitoring/__init__.py
    - app/scheduler.py

key-decisions:
  - "30-day raw retention matches prompt metrics pattern (USER DECISION)"
  - "Labels JSON column for metric segmentation by actor, model, queue, bucket"
  - "MetricsCollector does NOT commit - caller controls transaction"
  - "Rollup job at 01:30 (30 min after prompt rollup at 01:00)"

patterns-established:
  - "Labels key extraction for grouping metrics (actor:X, model:X, queue:X, bucket:X)"
  - "95th percentile calculation for performance metrics"
  - "Upsert pattern for daily rollup idempotency"

# Metrics
duration: 5min
completed: 2026-02-06
---

# Phase 09 Plan 03: Operational Metrics Collection Summary

**PostgreSQL-backed operational metrics with daily rollup: queue depth, processing time, errors, token usage, confidence distribution**

## Performance

- **Duration:** 5 minutes
- **Started:** 2026-02-06T16:48:12Z
- **Completed:** 2026-02-06T16:53:31Z
- **Tasks:** 3/3 completed
- **Files modified:** 7

## Accomplishments

- OperationalMetrics model with Labels JSON column for flexible metric segmentation
- OperationalMetricsDaily model for permanent aggregated storage
- MetricsCollector service provides 5 recording methods (queue depth, processing time, errors, token usage, confidence)
- Daily rollup job at 01:30 aggregates metrics and cleans data older than 30 days
- Alembic migration creates tables with proper indexes for efficient queries

## Task Commits

Each task was committed atomically:

1. **Task 1: Create operational metrics models (raw + daily rollup)** - `4dcb424` (feat)
   - OperationalMetrics model for raw metrics with 30-day retention
   - OperationalMetricsDaily model for permanent daily aggregates

2. **Task 2: Create Alembic migration for operational metrics tables** - `82c3d51` (feat)
   - Creates operational_metrics and operational_metrics_daily tables
   - Indexes on metric_type and recorded_at for efficient queries

3. **Task 3: Create MetricsCollector service and rollup job** - `5603607` (feat)
   - MetricsCollector with 5 recording methods
   - run_operational_metrics_rollup aggregates and cleans
   - Scheduler integration at 01:30 daily

## Files Created/Modified

### Created
- `app/models/operational_metrics.py` - OperationalMetrics and OperationalMetricsDaily models
- `app/services/monitoring/metrics.py` - MetricsCollector service with record_* methods
- `app/services/metrics_rollup.py` - Daily rollup job with 30-day retention cleanup
- `alembic/versions/20260206_add_operational_metrics.py` - Migration for metrics tables

### Modified
- `app/models/__init__.py` - Export OperationalMetrics and OperationalMetricsDaily
- `app/services/monitoring/__init__.py` - Export MetricsCollector and get_metrics_collector
- `app/scheduler.py` - Add run_scheduled_operational_rollup at 01:30 daily

## Decisions Made

**1. 30-day raw retention (matches prompt metrics pattern)**
- Rationale: Consistent retention policy across all metrics systems
- Raw metrics deleted after aggregation to prevent table bloat

**2. Labels JSON column for metric segmentation**
- Rationale: Flexible grouping without schema changes (actor, model, queue, bucket)
- Labels key extraction enables efficient rollup grouping

**3. MetricsCollector does NOT commit**
- Rationale: Follows DualDatabaseWriter pattern - caller controls transaction
- Prevents partial commits in case of downstream failures

**4. Rollup scheduled at 01:30 (30 min after prompt rollup)**
- Rationale: Spreads database load across scheduler window
- Both rollups complete before 02:00 daily cutoff

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

**Concurrent plan execution detected**
- Found: Circuit breakers from 09-02 committed during 09-03 execution
- Resolution: Updated monitoring __init__.py to ADD MetricsCollector exports without removing circuit_breakers
- Impact: No conflict - both 09-02 and 09-03 exports coexist correctly

## User Setup Required

**Migration required before use:**

```bash
alembic upgrade head
```

This creates:
- `operational_metrics` table (raw metrics with 30-day retention)
- `operational_metrics_daily` table (permanent rollup storage)

**Restart application to activate scheduler:**
- Operational rollup job runs daily at 01:30
- Log: "job_registered", job="operational_metrics_rollup", schedule="daily_01:30"

**No environment variables required** - uses existing DATABASE_URL

## Next Phase Readiness

**Ready for:**
- 09-04: Integration testing can verify metrics collection
- Future dashboard: OperationalMetricsDaily provides historical data
- Future alerting: Raw metrics enable real-time threshold checks

**Integration points:**
- Email processor: record_processing_time, record_error, record_token_usage
- Matching engine: record_confidence distribution
- Queue monitoring: record_queue_depth for Dramatiq queue depth

**Monitoring database for metrics accumulation:**
```sql
-- Check raw metrics count
SELECT metric_type, COUNT(*) FROM operational_metrics GROUP BY metric_type;

-- Check daily rollup records
SELECT metric_type, date, labels_key, sample_count
FROM operational_metrics_daily
ORDER BY date DESC LIMIT 10;
```

**No blockers** - infrastructure complete, ready for integration in actors

---
*Phase: 09-production-hardening-monitoring*
*Plan: 03*
*Completed: 2026-02-06*
