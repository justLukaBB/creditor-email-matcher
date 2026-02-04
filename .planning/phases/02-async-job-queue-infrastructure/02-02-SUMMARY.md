---
phase: 02-async-job-queue-infrastructure
plan: 02
subsystem: data-models
tags: [database, sqlalchemy, alembic, pydantic, webhook-schema]
requires: [01-01-database-models, 01-02-dual-write-saga]
provides: [job-state-tracking, attachment-storage-schema, job-queue-migration]
affects: [02-03-enqueue-worker, 03-email-processing]
tech-stack:
  added: []
  patterns: [state-machine-columns, composite-index]
decisions:
  - job-tracking-in-incoming-emails
  - attachment-urls-json-column
  - status-received-composite-index
key-files:
  created:
    - alembic/versions/20260204_1705_add_job_state_machine.py
    - app/models/webhook_schemas.py
  modified:
    - app/models/incoming_email.py
metrics:
  duration: 3min
  tasks-completed: 2
  commits: 1
  deviations: 1
completed: 2026-02-04
---

# Phase 02 Plan 02: Job State Machine Schema Summary

**One-liner:** Extended IncomingEmail model with job lifecycle timestamps, retry tracking, and attachment URL storage for async worker processing.

## What Was Built

### Job State Machine Columns
Added four new columns to the IncomingEmail model to track job lifecycle:

1. **started_at** (DateTime): Timestamp when Dramatiq worker begins processing
2. **completed_at** (DateTime): Timestamp when processing finishes (success or failure)
3. **retry_count** (Integer): Counter for Dramatiq retry attempts (default: 0)
4. **attachment_urls** (JSON): Array of Zendesk attachment metadata for Phase 3 processing

### State Machine Documentation
Updated processing_status field documentation to define the complete state machine:
```
received -> queued -> processing -> completed | failed
```

Each state clearly defined:
- **received**: Webhook validated and email stored
- **queued**: Enqueued to Dramatiq for async processing
- **processing**: Worker picked up and is processing
- **completed**: Successfully finished
- **failed**: Permanently failed (all retries exhausted)

### Webhook Schema Update
Extended ZendeskWebhookEmail Pydantic schema with attachments field:
```python
attachments: Optional[List[dict]] = Field(
    default=None,
    description="List of attachment metadata from Zendesk. Each dict has: url, filename, content_type, size"
)
```

Expected attachment structure:
```json
{
  "url": "https://...",
  "filename": "rechnung.pdf",
  "content_type": "application/pdf",
  "size": 12345
}
```

### Database Migration
Created Alembic migration `20260204_1705_add_job_state_machine.py`:
- Adds started_at, completed_at, retry_count, attachment_urls columns
- Creates composite index on (processing_status, received_at) for efficient worker polling
- Down migration cleanly removes all additions
- References previous migration: 20260204_1549_add_saga

### Composite Index Rationale
The `ix_incoming_emails_status_received` index enables efficient queries for:
- Worker polling: `WHERE processing_status = 'queued' ORDER BY received_at LIMIT 100`
- Status dashboards: `WHERE processing_status IN ('processing', 'failed')`
- Age-based monitoring: `WHERE processing_status = 'queued' AND received_at < NOW() - INTERVAL '5 minutes'`

## Decisions Made

### Job Tracking in IncomingEmail Table
**Decision:** Use IncomingEmail as the job tracking table rather than creating separate job metadata table.

**Rationale:**
- Each incoming email maps 1:1 to a processing job
- Avoids JOIN overhead for status queries
- Simplifies worker code (single table update)
- Processing state is intrinsic to the email entity

**Alternative considered:** Separate `job_metadata` table with foreign key to incoming_emails
**Rejected because:** Adds complexity and query overhead for no clear benefit

### Attachment URLs as JSON Column
**Decision:** Store attachment metadata as JSON array in attachment_urls column.

**Rationale:**
- Variable number of attachments per email (0-N)
- Metadata structure may evolve (Zendesk may add fields)
- No need to query individual attachments (always fetched with parent email)
- Phase 3 workers will iterate array for download/processing

**Schema flexibility:** JSON allows Zendesk to add fields without migration
**Trade-off:** Cannot index individual attachments (acceptable - no such queries needed)

### Composite Index on (processing_status, received_at)
**Decision:** Create composite index covering status + timestamp rather than separate indexes.

**Rationale:**
- Worker polling always filters by status AND orders by timestamp
- Composite index serves the exact query pattern
- PostgreSQL can use index for status-only queries (leading column)
- Avoids bitmap index scan overhead of combining two indexes

**Index choice:** (status, timestamp) not (timestamp, status)
- Status has low cardinality (~5 values) - good for leading column
- Timestamp has high cardinality - good for ordering
- Matches WHERE + ORDER BY query pattern

## Implementation Notes

### Backward Compatibility
All new columns are nullable (except retry_count with default 0):
- Existing rows work without migration data backfill
- Old webhook handlers continue working (attachments field optional)
- Pydantic schema allows extra fields (forward compatible)

### Phase 1 Columns Preserved
Did NOT modify sync_status, sync_error, sync_retry_count, idempotency_key:
- Phase 1 dual-write tracking remains independent
- Job retry tracking (retry_count) separate from MongoDB sync retry tracking (sync_retry_count)
- Two independent state machines operating on same entity

### Migration Naming Convention
Followed project pattern: `YYYYMMDD_HHMM_description.py`
- Timestamp ensures chronological ordering
- Short revision ID: `20260204_1705_add_job`
- Human-readable description in filename

## Testing Strategy

### Manual Verification Performed
1. ✅ IncomingEmail model imports successfully with new columns
2. ✅ Migration file exists with correct up/down operations
3. ✅ Webhook schema includes attachments field
4. ✅ All existing columns preserved in model definition

### Pre-Deployment Testing Required
1. Run migration in test environment: `alembic upgrade head`
2. Verify index created: `\d incoming_emails` in psql
3. Test webhook with attachments payload
4. Verify JSON deserialization of attachment_urls
5. Query performance test for worker polling query

### Integration Testing (Phase 2, Plan 3)
- Enqueue worker creates jobs with started_at timestamp
- Worker updates retry_count on failure
- Worker sets completed_at on success/permanent failure
- Status API returns job lifecycle timestamps

## Deviations from Plan

### Files Already Existed from Plan 02-01
**Issue:** webhook_schemas.py and migration file were already created by plan 02-01 execution.

**Discovery:** When copying webhook_schemas.py from _existing-code and creating migration, found files already existed in git with identical content.

**Root cause:** Plan 02-01 (broker setup) created these files proactively, likely because they're referenced by the worker code.

**Impact:** No negative impact. Files contain exactly the content specified in this plan.

**Resolution:**
- Task 1 (IncomingEmail model update) committed as planned (fddb89b)
- Task 2 work already complete from previous plan (b071348)
- Verified all artifacts meet plan requirements
- Documented in Summary to explain single commit vs expected two

**Classification:** Deviation Rule 3 (blocking issue) - Files needed to exist for Task 2 completion. Previous plan resolved blocker proactively.

## Next Phase Readiness

### Ready for Plan 02-03 (Enqueue Worker)
✅ IncomingEmail has started_at, completed_at for job lifecycle tracking
✅ retry_count column ready for Dramatiq retry middleware
✅ attachment_urls column ready to receive webhook data
✅ Composite index ready for worker polling queries
✅ State machine documented for worker implementation

### Dependency for Plan 02-04 (Status API)
✅ Job state columns available for status endpoint queries
✅ Composite index supports efficient status filtering
✅ Timestamps enable duration calculations

### Dependency for Phase 3 (Email Processing)
✅ attachment_urls schema defined and ready
✅ Worker can populate attachment_urls on job creation
✅ Phase 3 actors can read attachment_urls to download PDFs

### Blockers
None. All Phase 2 plans can proceed.

### Concerns
**Migration execution timing:** Migration adds columns and index. On large incoming_emails table:
- Column additions are fast (nullable columns don't rewrite table in PostgreSQL)
- Index creation locks table for writes (uses CREATE INDEX, not CONCURRENTLY)
- Estimate: <1 second for <10k rows, ~5-10 seconds for 100k rows
- **Mitigation for production:** If table is large, use `CREATE INDEX CONCURRENTLY` in manual migration

**Attachment URL storage:** JSON column stores URLs, but Zendesk attachment URLs may expire:
- Phase 3 must download attachments immediately after job dequeue
- Cannot rely on attachment_urls for replay/reprocessing after expiry
- **Mitigation:** Phase 3 should store downloaded files in S3/local storage with permanent URLs

## File Changes

### Created
- `alembic/versions/20260204_1705_add_job_state_machine.py` (54 lines)
  - Migration adding job state machine columns and composite index
  - Up: add columns + create index
  - Down: drop index + drop columns

- `app/models/webhook_schemas.py` (42 lines)
  - Copied from _existing-code with attachments field added
  - ZendeskWebhookEmail Pydantic schema for webhook validation
  - WebhookResponse schema for API responses

### Modified
- `app/models/incoming_email.py`
  - Added started_at, completed_at, retry_count, attachment_urls columns
  - Updated processing_status documentation with state machine
  - All existing columns preserved (backward compatible)

## Commits

| Commit  | Type | Description                                          | Files |
|---------|------|------------------------------------------------------|-------|
| fddb89b | feat | Add job state machine columns to IncomingEmail model | app/models/incoming_email.py |

**Note:** webhook_schemas.py and migration file were committed in plan 02-01 (commit b071348). This plan's single commit covers the IncomingEmail model changes.

## Metrics

**Execution time:** 3 minutes
**Tasks completed:** 2/2
**Commits:** 1 (1 model update)
**Files modified:** 1
**Files created:** 2 (already existed from 02-01)
**Deviations:** 1 (files already created by previous plan)

**Velocity:** Standard model update plan. Fast execution due to:
- Simple column additions (no complex logic)
- Manual migration (no alembic autogenerate)
- No test file updates required (models don't have unit tests)
