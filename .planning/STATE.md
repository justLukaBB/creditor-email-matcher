# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-04)

**Core value:** Gläubiger-Antworten werden zuverlässig dem richtigen Mandanten und Gläubiger zugeordnet und die Forderungsdaten korrekt in die Datenbank geschrieben — ohne manuellen Aufwand.

**Current focus:** Phase 2 - Async Job Queue Infrastructure

## Current Position

Phase: 2 of 10 (Async Job Queue Infrastructure)
Plan: 1 of 4 complete (02-01 done)
Status: In progress
Last activity: 2026-02-04 — Completed 02-01-PLAN.md (Broker infrastructure setup)

Progress: [█████░░░░░] 12.5%

## Performance Metrics

**Velocity:**
- Total plans completed: 5
- Average duration: 4.8 minutes
- Total execution time: 0.4 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 4 | 21 min | 5.25 min |
| 2 | 1 | 3 min | 3 min |

**Recent Trend:**
- 01-02: 8 minutes (dual-write saga implementation with tests)
- 01-03: 4 minutes (reconciliation service with APScheduler)
- 01-04: 5 minutes (audit service with CLI script)
- 02-01: 3 minutes (Dramatiq broker infrastructure setup)
- Trend: Infrastructure setup tasks ~3-5 min, implementation with tests ~8 min

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

### Pending Todos

- Install dependencies: `pip install -r requirements.txt` (includes dramatiq[redis]>=2.0.1, psutil>=5.9.0)
- Set DATABASE_URL and MONGODB_URL environment variables
- **NEW:** Add Redis add-on on Render and set REDIS_URL environment variable (see 02-01-SUMMARY.md)
- Run migration: `alembic upgrade head` (creates outbox_messages, idempotency_keys, reconciliation_reports tables)
- Run baseline audit: `python scripts/audit_consistency.py --lookback-days 30` to establish current consistency state
- Decision: Keep APScheduler for reconciliation or migrate to Dramatiq in Phase 2?
- Tune reconciliation frequency based on production metrics (currently hourly)

### Blockers/Concerns

**Phase 2 In Progress:** Plan 02-01 complete (broker infrastructure). Ready for Plan 02-02 (database models for job state machine).

**Production Deployment Required:** Phase 1 code complete but not deployed. Need to:
1. Deploy to production environment
2. Run baseline audit against production databases
3. Address any high-severity mismatches before building Phase 2
4. Verify reconciliation service runs successfully in production

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
Stopped at: Completed 02-01-PLAN.md execution - Dramatiq broker infrastructure setup
Resume file: None

---

**Next action:** Continue Phase 2 with Plan 02-02 (Database models for job state machine)
