# Phase 2: Async Job Queue Infrastructure - Context

**Gathered:** 2026-02-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Dramatiq + Redis job queue replaces synchronous webhook processing for reliable async handling of 200+ emails/day. Includes retry logic, crash recovery, job state tracking, and webhook schema update for attachments. Does NOT include actual attachment processing (Phase 3) or multi-agent pipeline (Phase 5).

</domain>

<decisions>
## Implementation Decisions

### Webhook transition
- Webhook validates synchronously before queuing: signature check, dedup, basic schema validation. Zendesk gets 200 only if valid.
- Simple 200 OK response — no job ID returned to Zendesk.
- Webhook saves incoming_email record to PostgreSQL (RECEIVED status) synchronously, then queues the processing job. Guaranteed audit trail before async handoff.
- Update webhook schema now to accept attachment URLs from Zendesk. Store URLs in PostgreSQL. Actual download/processing deferred to Phase 3.

### Job status visibility
- REST API endpoints for job status (GET /jobs, GET /jobs/{id}) with status, timestamps, error details.
- No auth on status endpoints — rely on Render's internal networking.
- Minimal state machine: RECEIVED → QUEUED → PROCESSING → COMPLETED/FAILED.
- Email notification on permanent failure (after all retries exhausted) using existing SMTP setup.

### Worker deployment
- Dramatiq worker runs in same Render service as FastAPI (not a separate service). Shared 512MB memory limit.
- 2-3 parallel worker threads. Needs memory monitoring to stay within 512MB.
- Render Redis add-on for message broker. Same network, simple setup.
- Keep APScheduler for reconciliation as-is. Don't migrate to Dramatiq periodic tasks.

### Claude's Discretion
- Exact retry backoff configuration (intervals, max retries)
- Memory monitoring implementation (gc.collect strategy, max-tasks-per-child)
- Dramatiq middleware selection
- Job serialization format

</decisions>

<specifics>
## Specific Ideas

- Webhook does sync validation + PostgreSQL write, then queues — this preserves the audit trail from Phase 1's DualDatabaseWriter pattern
- Attachment URLs stored now means Phase 3 just needs to add download + extraction logic, no schema changes
- Email alerts on permanent failure reuse existing SMTP configuration from v1 notification system

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-async-job-queue-infrastructure*
*Context gathered: 2026-02-04*
