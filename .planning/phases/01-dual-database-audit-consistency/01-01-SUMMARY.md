# Phase 01 Plan 01: Saga Infrastructure Database Models Summary

**One-liner:** Created PostgreSQL schema foundation for saga pattern with OutboxMessage, IdempotencyKey, ReconciliationReport tables and IncomingEmail sync tracking columns.

---

## Plan Reference

**Phase:** 01-dual-database-audit-consistency
**Plan:** 01
**Type:** Foundation / Database Schema
**Completed:** 2026-02-04

---

## What Was Built

### Database Models Created

1. **OutboxMessage** (`app/models/outbox_message.py`)
   - Transactional outbox pattern for PostgreSQL-to-MongoDB dual writes
   - Fields: aggregate_type, aggregate_id, operation, payload, idempotency_key, processed_at, retry_count, max_retries, error_message
   - Indexes: (processed_at, retry_count) for efficient polling, created_at for cleanup
   - Purpose: Ensures at-least-once delivery semantics for MongoDB replication

2. **IdempotencyKey** (`app/models/idempotency_key.py`)
   - Duplicate prevention for saga operations
   - Fields: key (unique, indexed), result (cached operation result), created_at, expires_at
   - Index: expires_at for cleanup queries
   - Purpose: Prevents duplicate writes when operations retry

3. **ReconciliationReport** (`app/models/reconciliation_report.py`)
   - Audit trail for PostgreSQL-MongoDB consistency checks
   - Fields: run_at, completed_at, records_checked, mismatches_found, auto_repaired, failed_repairs, details (JSON), status, error_message
   - Purpose: Tracks reconciliation job results and mismatch repairs

4. **IncomingEmail Extended** (`app/models/incoming_email.py`)
   - Added sync tracking columns: sync_status, sync_error, sync_retry_count, idempotency_key
   - All original columns preserved (REQ-MIGRATE-01 backward compatibility)
   - Purpose: Tracks MongoDB sync status per email record

### Database Migration

**Migration:** `alembic/versions/20260204_1549_add_saga_infrastructure.py`

**Creates:**
- `outbox_messages` table with 2 indexes
- `idempotency_keys` table with 2 indexes
- `reconciliation_reports` table
- 4 new columns on `incoming_emails` table with unique constraint

**Depends on:** Initial migration `20260107_1733_381db1c8de34`

**Reversible:** Full downgrade removes all new tables and columns

### Infrastructure Files

- `app/database.py` - SQLAlchemy Base and session management
- `app/config.py` - Pydantic settings for environment configuration
- `alembic.ini` - Alembic configuration with timestamped migration filenames
- `alembic/env.py` - Alembic environment with model imports

---

## Key Implementation Decisions

### Integer Primary Keys
**Decision:** Use Integer PKs (not UUIDs) for all new models
**Rationale:** Matches existing codebase convention (IncomingEmail uses Integer PK)
**Impact:** Consistent with v1 schema, simpler joins, smaller index size

### Server-Default Timestamps
**Decision:** Use `func.now()` for created_at timestamps
**Rationale:** Matches existing pattern in IncomingEmail model
**Impact:** Database generates timestamps, consistent with existing models

### Sync Status Enum Values
**Decision:** sync_status values: pending, synced, failed, not_applicable
**Rationale:** Covers all MongoDB sync states for reconciliation logic
**Impact:** Clear state machine for saga pattern compensation

### Idempotency Key as Optional Nullable
**Decision:** IncomingEmail.idempotency_key is nullable with unique constraint
**Rationale:** Allows backward compatibility with existing records that lack keys
**Impact:** New records get keys, old records can be migrated gradually

---

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | 4c2f4ea | feat(01-01): create saga infrastructure models |
| 2 | 0b8bee5 | feat(01-01): create Alembic migration for saga infrastructure |

**Total commits:** 2

---

## Files Created

### Models
- `app/models/outbox_message.py` (58 lines)
- `app/models/idempotency_key.py` (44 lines)
- `app/models/reconciliation_report.py` (50 lines)
- `app/models/incoming_email.py` (87 lines)
- `app/models/__init__.py` (12 lines)

### Infrastructure
- `app/database.py` (58 lines)
- `app/config.py` (18 lines)
- `app/__init__.py` (3 lines)

### Migrations
- `alembic.ini` (131 lines)
- `alembic/env.py` (79 lines)
- `alembic/versions/20260204_1549_add_saga_infrastructure.py` (106 lines)

**Total:** 11 files, ~646 lines of code

---

## Deviations from Plan

**None** - Plan executed exactly as written.

The plan specified:
- Four models with specific fields ✓
- Integer PKs matching existing convention ✓
- func.now() for timestamps ✓
- Base imported from app.database ✓
- Alembic migration with dependency on initial migration ✓
- All indexes and constraints ✓

All requirements met without modifications.

---

## Testing & Verification

### What Was Verified

1. **Model imports:** All models import successfully from `app.models`
2. **Migration structure:** Migration file contains all required up/down operations
3. **Backward compatibility:** IncomingEmail retains all original columns
4. **Index definitions:** All indexes present in migration (outbox polling, idempotency cleanup)

### What Was Not Verified (Requires Database)

- Migration execution against actual PostgreSQL database
- Table creation and constraint enforcement
- Index performance on unprocessed message queries
- Unique constraint enforcement on idempotency keys

**Note:** Verification of actual database operations deferred until database is configured. Schema and migration files are syntactically correct and follow Alembic patterns.

---

## Dependencies & Integration

### Provides

- **Schema foundation for Phase 1 Plans 02-04:**
  - Plan 02 (DualDatabaseWriter) uses OutboxMessage and IdempotencyKey tables
  - Plan 03 (Reconciliation service) uses ReconciliationReport table
  - Plan 04 (Data audit) queries all new tables

- **Sync tracking for IncomingEmail:**
  - sync_status tracks MongoDB write status
  - sync_error stores failure reasons
  - sync_retry_count enables retry logic
  - idempotency_key prevents duplicate processing

### Requires

- **PostgreSQL database:** All tables target PostgreSQL
- **SQLAlchemy 2.0+:** Models use SQLAlchemy 2.0 syntax
- **Alembic 1.13+:** Migration uses Alembic patterns
- **Pydantic Settings 2.1+:** Config uses pydantic_settings

### Affects

- **Phase 1 Plan 02:** DualDatabaseWriter saga implementation
- **Phase 1 Plan 03:** Reconciliation job implementation
- **Phase 1 Plan 04:** Data consistency audit scripts

---

## Decisions Made

| Decision | Context | Rationale | Alternatives Considered |
|----------|---------|-----------|-------------------------|
| Manual migration over autogenerate | No database connection available | Autogenerate requires DB connection; manual migration based on models | Wait for DB setup (would delay plan) |
| PostgreSQL-only idempotency (no Redis) | Phase 1 focus on database foundation | Simpler deployment, fewer dependencies | Redis (faster but adds dependency - deferred to Phase 2) |
| Nullable idempotency_key on IncomingEmail | Backward compatibility requirement | Existing records lack keys, new records get them | Required non-null with backfill script |
| timestamp format in migration filename | Alembic convention | Clear ordering, human-readable | Hash-only format (less readable) |

---

## Next Phase Readiness

### Ready to Proceed

✅ **Plan 01-02 (DualDatabaseWriter saga):** All required tables exist
✅ **Plan 01-03 (Reconciliation service):** ReconciliationReport table ready
✅ **Plan 01-04 (Data audit):** Schema in place for mismatch detection

### Blockers/Concerns

**Database Connection Required:** Next plans require actual PostgreSQL database to:
- Run migration (`alembic upgrade head`)
- Test dual-write saga logic
- Execute reconciliation queries

**Action:** Set DATABASE_URL environment variable before Plan 01-02 execution.

**Idempotency Strategy Decision Needed:** Phase 1 uses PostgreSQL table, Phase 2 may add Redis. Confirm:
- Is PostgreSQL-based idempotency sufficient for Phase 1 volume (~200 emails/day)?
- When to introduce Redis for faster idempotency checks?

**Recommendation:** Proceed with PostgreSQL idempotency for Phase 1. Add Redis in Phase 2 when job queue infrastructure added.

---

## Performance Notes

**Execution time:** ~4 minutes (2026-02-04 14:49:04 - 14:52:48 UTC)

**Why fast:**
- No database operations (schema design only)
- No external dependencies to install
- Straightforward model definitions

**Estimated migration runtime:** <5 seconds (creates 3 tables + 4 columns + 6 indexes)

---

## Metadata

**Phase:** 01-dual-database-audit-consistency
**Plan:** 01
**Subsystem:** database-schema
**Tags:** sqlalchemy, alembic, postgresql, saga-pattern, transactional-outbox, idempotency, reconciliation

**Tech Stack Added:**
- None (uses existing SQLAlchemy, Alembic, Pydantic)

**Patterns Established:**
- Transactional outbox table structure
- Idempotency key storage pattern
- Reconciliation audit trail schema
- Sync status tracking on business entities

**Completed:** 2026-02-04
**Duration:** 4 minutes
