# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-04)

**Core value:** Gläubiger-Antworten werden zuverlässig dem richtigen Mandanten und Gläubiger zugeordnet und die Forderungsdaten korrekt in die Datenbank geschrieben — ohne manuellen Aufwand.

**Current focus:** Phase 1 - Dual-Database Audit & Consistency

## Current Position

Phase: 1 of 10 (Dual-Database Audit & Consistency)
Plan: 2 of 4 complete
Status: In progress
Last activity: 2026-02-04 — Completed 01-02-PLAN.md (DualDatabaseWriter saga pattern)

Progress: [██░░░░░░░░] 5.0%

## Performance Metrics

**Velocity:**
- Total plans completed: 2
- Average duration: 4.5 minutes
- Total execution time: 0.15 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 2 | 9 min | 4.5 min |

**Recent Trend:**
- 01-01: 4 minutes (database models - fast, no DB operations)
- 01-02: 5 minutes (saga services - code generation)
- Trend: Consistent velocity, no database operations yet

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

### Pending Todos

- Install dependencies: `pip install -r requirements.txt` (includes structlog>=24.1.0)
- Set DATABASE_URL and MONGODB_URL environment variables
- Run migration: `alembic upgrade head` (creates outbox_messages, idempotency_keys tables)
- Copy missing service files from _existing-code/ if webhook runtime testing needed

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
Stopped at: Completed 01-02-PLAN.md execution - DualDatabaseWriter saga pattern implemented
Resume file: None

---

**Next action:** Execute Plan 01-03 (Reconciliation service) or Plan 01-04 (Data audit scripts)
