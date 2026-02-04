# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-04)

**Core value:** Gläubiger-Antworten werden zuverlässig dem richtigen Mandanten und Gläubiger zugeordnet und die Forderungsdaten korrekt in die Datenbank geschrieben — ohne manuellen Aufwand.

**Current focus:** Phase 1 - Dual-Database Audit & Consistency

## Current Position

Phase: 1 of 10 (Dual-Database Audit & Consistency)
Plan: 1 of 4 complete
Status: In progress
Last activity: 2026-02-04 — Completed 01-01-PLAN.md (Saga infrastructure models)

Progress: [█░░░░░░░░░] 2.5%

## Performance Metrics

**Velocity:**
- Total plans completed: 1
- Average duration: 4 minutes
- Total execution time: 0.07 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 1 | 4 min | 4 min |

**Recent Trend:**
- 01-01: 4 minutes (database models - fast, no DB operations)
- Trend: First plan baseline established

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

### Pending Todos

- Set DATABASE_URL environment variable before Plan 01-02 execution (required for DualDatabaseWriter testing)

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
Stopped at: Completed 01-01-PLAN.md execution - saga infrastructure database models created
Resume file: None

---

**Next action:** Execute Plan 01-02 (DualDatabaseWriter saga implementation) or continue Phase 1 planning
