# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-04)

**Core value:** Gläubiger-Antworten werden zuverlässig dem richtigen Mandanten und Gläubiger zugeordnet und die Forderungsdaten korrekt in die Datenbank geschrieben — ohne manuellen Aufwand.

**Current focus:** Phase 2 - Async Job Queue Infrastructure

## Current Position

Phase: 2 of 10 (Async Job Queue Infrastructure)
Plan: 4 of 4 complete (Phase 2 COMPLETE)
Status: Phase complete
Last activity: 2026-02-04 — Completed 02-04-PLAN.md (API integration and deployment)

Progress: [████████░░] 20%

## Performance Metrics

**Velocity:**
- Total plans completed: 8
- Average duration: 4.25 minutes
- Total execution time: 0.57 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 4 | 21 min | 5.25 min |
| 2 | 4 | 13 min | 3.25 min |

**Recent Trend:**
- 01-04: 5 minutes (audit service with CLI script)
- 02-01: 3 minutes (Dramatiq broker infrastructure setup)
- 02-02: 3 minutes (Job state machine database schema)
- 02-03: 2 minutes (Email processor Dramatiq actor)
- 02-04: 5 minutes (API integration and deployment)
- Trend: Schema/model updates ~3 min, API/integration work ~5 min, actor creation ~2 min

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Phase 1: PostgreSQL as single source of truth with saga pattern for dual-database writes
- Phase 2: Dramatiq + Redis over Celery for simpler deployment and lower memory footprint
- Phase 3-5: Three-agent architecture (Email Processing → Content Extraction → Consolidation)
- Phase 3: Claude Vision for PDF/image extraction (no separate OCR service)
- Phase 5: Intent-based processing with different extraction strategies per email type
- Phase 6: Matching engine reactivation rather than rebuild from scratch
- Phase 8: Prompt repository in PostgreSQL for runtime updates without deployment

**New from 01-01:**
- Integer primary keys (not UUIDs) to match existing codebase convention
- PostgreSQL-based idempotency storage in Phase 1 (Redis deferred to Phase 2)
- Nullable idempotency_key on IncomingEmail for backward compatibility
- Manual migration over autogenerate (no DB connection available)

**New from 01-02:**
- DualDatabaseWriter does NOT commit - caller controls transaction (atomic outbox + business data)
- MongoDB write happens post-commit (compensatable, PostgreSQL is source of truth)
- Idempotency key format: operation:aggregate_id:hash (SHA256 of JSON payload)
- MongoDB-only fallback mode preserved for backward compatibility
- Import mongodb_service singleton (reuse existing MongoDB client)

**New from 01-03:**
- APScheduler for hourly reconciliation (lightweight, no separate worker process)
- BackgroundScheduler (not AsyncIOScheduler) for synchronous SQLAlchemy/PyMongo
- 48-hour lookback window for reconciliation comparison
- Auto-repair strategy: PostgreSQL to MongoDB re-sync on mismatch
- Manual reconciliation trigger endpoint for operational control

**New from 01-04:**
- Exit code reflects health score (0 for healthy >= 0.95, 1 for issues)
- 30-day default lookback for audit period
- Standalone audit script works without running FastAPI app
- Recovery plan categorizes mismatches: re-sync, manual_review, stalled, no_action
- Health score calculation: (total_checked - total_issues) / total_checked

**New from 02-01:**
- Dramatiq broker auto-switches: RedisBroker (production) or StubBroker (testing) based on redis_url
- Redis namespace: creditor_matcher for key isolation
- Worker configuration: 2 processes x 1 thread for Render 512MB memory budget
- Settings extended with missing fields: environment, webhook_secret, llm_provider, SMTP settings
- Worker entrypoint (app/worker.py) imports actors package for broker setup

**New from 02-02:**
- IncomingEmail tracks job lifecycle: started_at, completed_at timestamps for async processing
- retry_count column separate from sync_retry_count (job retries vs MongoDB sync retries)
- attachment_urls JSON column stores Zendesk attachment metadata for Phase 3 processing
- Composite index (processing_status, received_at) for efficient worker polling
- ZendeskWebhookEmail schema accepts attachments field with URL, filename, content_type, size

**New from 02-03:**
- Email processor Dramatiq actor with max_retries=5 and exponential backoff (15s to 5min)
- should_retry predicate for selective retry (transient vs permanent failures)
- on_process_email_failure callback invokes notify_permanent_failure after all retries exhausted
- State machine: received -> queued -> processing -> completed/failed/not_creditor_reply
- FOR UPDATE SKIP LOCKED row locking prevents duplicate processing
- gc.collect() after each job for 512MB memory constraint

**New from 02-04:**
- Job status REST API with no authentication (relies on Render internal networking)
- Manual retry endpoint resets to "queued" status and increments retry_count
- FailureNotifier uses app.config.settings for SMTP (separate from email_notifier)
- Procfile runs web (uvicorn) + worker (dramatiq) processes for Render deployment
- All routers (webhook, jobs) registered in FastAPI app
- App version bumped to 0.3.0

### Pending Todos

**Phase 2 Deployment Prerequisites:**
- Install dependencies: `pip install -r requirements.txt` (includes dramatiq[redis]>=2.0.1, psutil>=5.9.0)
- Set DATABASE_URL and MONGODB_URL environment variables
- Add Redis add-on on Render and set REDIS_URL environment variable (see 02-01-SUMMARY.md)
- Set SMTP environment variables for failure notifications: SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, ADMIN_EMAIL (see 02-04-SUMMARY.md)
- Run migration: `alembic upgrade head` (creates outbox_messages, idempotency_keys, reconciliation_reports tables + adds job state columns)
- Consider CREATE INDEX CONCURRENTLY for production if incoming_emails table is large (see 02-02-SUMMARY.md)
- Deploy to Render with Procfile (starts web + worker processes)

**Phase 1 Outstanding:**
- Run baseline audit: `python scripts/audit_consistency.py --lookback-days 30` to establish current consistency state
- Tune reconciliation frequency based on production metrics (currently hourly)

### Blockers/Concerns

**Phase 2 Complete:** All 4 plans executed (broker infrastructure, job state schema, email processor actor, API integration). Ready for production deployment and Phase 3 planning.

**Production Deployment Required:** Phases 1 and 2 code complete but not deployed. Need to:
1. Deploy to production environment with Procfile
2. Configure REDIS_URL and SMTP environment variables
3. Run migration: `alembic upgrade head`
4. Run baseline audit against production databases
5. Verify webhook endpoint receives emails and enqueues to Dramatiq
6. Verify failure notifications work (test with failed job)

**Phase 3 Blocker:** Claude Vision API integration requires research-phase before detailed planning to verify:
- Current token limits for images and PDFs
- Image size restrictions
- Batch processing patterns
- Current pricing (2026 rates)
- Page-by-page processing best practices

**Production Risk:** Render 512MB memory limits require careful worker configuration (max-tasks-per-child, gc.collect()) to prevent OOM kills during PDF processing.

**Migration Risk:** v1 system bypassed matching engine likely due to database consistency issues. Must validate Phase 1 fixes prevent regression before building v2 pipeline on same foundation.

## Session Continuity

Last session: 2026-02-04
Stopped at: Completed 02-04-PLAN.md execution - API integration and deployment
Resume file: None

---

**Next action:** Phase 2 complete. Ready for Phase 3 planning (Content Extraction Agent). Requires research phase for Claude Vision API integration (token limits, image size restrictions, pricing, batch processing patterns).
