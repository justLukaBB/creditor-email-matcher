# Phase 9: Production Hardening & Monitoring - Context

**Gathered:** 2026-02-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Operational infrastructure for the email processing pipeline: structured logging, metrics collection, circuit breakers, error tracking, email notifications, processing reports, and integration tests. Provides visibility and resilience for 200+ daily emails at production scale.

</domain>

<decisions>
## Implementation Decisions

### Logging & correlation
- JSON structured logging format (machine-parseable, works with log aggregators)
- INFO verbosity level in production (key events only: received, processed, matched, failed)
- Logs to stdout — Render's built-in log viewer handles collection and retention

### Metrics & dashboards
- PostgreSQL tables for metrics storage — no additional infrastructure required
- Throughput-focused metrics priority: queue depth, processing rate, latency
- No dashboard UI needed — query PostgreSQL directly when needed

### Circuit breaker behavior
- Trigger: 5 consecutive failures opens the circuit
- Recovery: Auto-recovery after 60 seconds (no manual intervention required)
- Alerting: Email notification to admin when any circuit breaker opens

### Notifications & reports
- Auto-match notifications sent to single admin email (ADMIN_EMAIL from settings)
- Email content: Summary only — client name, creditor, amount, confidence (one paragraph)

### Claude's Discretion
- Correlation ID granularity (email_id vs email_id + job_id + agent)
- Metrics retention period (raw vs rollup durations)
- Which services get circuit breakers (Claude API, MongoDB, GCS — based on criticality)
- Processing report delivery method (per-email in database vs daily digest)
- Processing report detail level (extraction summary vs full audit trail)

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 09-production-hardening-monitoring*
*Context gathered: 2026-02-06*
