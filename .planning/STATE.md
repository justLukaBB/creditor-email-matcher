# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-04)

**Core value:** Gläubiger-Antworten werden zuverlässig dem richtigen Mandanten und Gläubiger zugeordnet und die Forderungsdaten korrekt in die Datenbank geschrieben — ohne manuellen Aufwand.

**Current focus:** Phase 1 - Dual-Database Audit & Consistency

## Current Position

Phase: 1 of 10 (Dual-Database Audit & Consistency)
Plan: 3 of 4 complete (01-01, 01-02, 01-03 done)
Status: In progress
Last activity: 2026-02-04 — Completed 01-03-PLAN.md (Hourly reconciliation service)

Progress: [███░░░░░░░] 7.5%

## Performance Metrics

**Velocity:**
- Total plans completed: 3
- Average duration: 5 minutes
- Total execution time: 0.26 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 3 | 16 min | 5.3 min |

**Recent Trend:**
- 01-01: 4 minutes (database models - fast, no DB operations)
- 01-02: 8 minutes (dual-write saga implementation with tests)
- 01-03: 4 minutes (reconciliation service with APScheduler)
- Trend: Consistent ~4-5 min for implementation tasks, ~8 min when tests included

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
- DualDatabaseWriter saga pattern with transactional outbox
- PostgreSQL writes complete before MongoDB writes attempted
- IdempotencyService with PostgreSQL-backed key storage
- Compensating transaction strategy: mark sync_status='failed' on MongoDB failure

**New from 01-03:**
- APScheduler for hourly reconciliation (lightweight, no separate worker process)
- BackgroundScheduler (not AsyncIOScheduler) for synchronous SQLAlchemy/PyMongo
- 48-hour lookback window for reconciliation comparison
- Auto-repair strategy: PostgreSQL to MongoDB re-sync on mismatch
- Manual reconciliation trigger endpoint for operational control

### Pending Todos

- Set DATABASE_URL and MONGODB_URL environment variables before running reconciliation in production
- Decision: Keep APScheduler for reconciliation or migrate to Dramatiq in Phase 2?
- Tune reconciliation frequency based on production metrics (currently hourly)

### Blockers/Concerns

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
Stopped at: Completed 01-03-PLAN.md execution - hourly reconciliation service with APScheduler
Resume file: None

---

**Next action:** Execute Plan 01-04 (Data consistency audit script) to complete Phase 1
