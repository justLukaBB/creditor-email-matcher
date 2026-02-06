---
phase: 08-database-backed-prompt-management
plan: 04
subsystem: prompt-management
tags: prompt-seeding, metrics-rollup, apscheduler, jinja2, postgresql, automation

# Dependency graph
requires:
  - phase: 08-03
    provides: "Prompt integration into extractors with metrics recording"
provides:
  - "Seed script migrating hardcoded prompts to database as v1"
  - "Daily metrics aggregation into prompt_performance_daily"
  - "Automated cleanup of raw metrics older than 30 days"
  - "APScheduler job for daily rollup at 01:00"
affects:
  - "Production deployment (requires running seed script after migration)"
  - "Prompt management UI (Phase 9 can use seeded prompts)"
  - "Performance monitoring (daily rollups enable long-term trend analysis)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Idempotent seeding with version existence checks"
    - "Upsert pattern for daily rollups (update existing, insert new)"
    - "Combined rollup+cleanup job for efficient scheduling"
    - "Centralized scheduler module with all background jobs"

key-files:
  created:
    - "scripts/seed_prompts.py (initial prompt migration)"
    - "app/services/prompt_rollup.py (daily aggregation service)"
    - "app/scheduler.py (centralized job definitions)"
  modified:
    - "app/main.py (refactored to use scheduler module)"

key-decisions:
  - "All 4 prompts seeded as is_active=True (ready for production)"
  - "Jinja2 variable syntax: {{ variable }} instead of f-string {variable}"
  - "Daily rollup at 01:00 (low-traffic time for aggregation)"
  - "30-day raw metrics retention enforced by cleanup job (USER DECISION)"
  - "Scheduler module separates job definitions from FastAPI app"

patterns-established:
  - "Idempotent migration scripts: check before insert, skip if exists"
  - "Dual-table metrics: raw (30-day) + daily rollups (permanent)"
  - "Centralized scheduler module exports jobs for manual triggering"
  - "Combined aggregation+cleanup jobs reduce scheduler complexity"

# Metrics
duration: 4min
completed: 2026-02-06
---

# Phase 08 Plan 04: Seeding and Automated Metrics Rollup Summary

**Seed script migrates 4 hardcoded prompts to database as v1, daily rollup job aggregates raw metrics into permanent summaries, APScheduler runs at 01:00**

## Performance

- **Duration:** 4 minutes
- **Started:** 2026-02-06T15:48:26Z
- **Completed:** 2026-02-06T15:52:13Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments

- Seed script creates v1 of all 4 prompts: classification.email_intent, extraction.email_body, extraction.pdf_scanned, extraction.image
- Converted hardcoded f-string variables to Jinja2 {{ variable }} syntax
- All seeded prompts start as is_active=True (ready for production use)
- Idempotent seeding: checks for existing versions before inserting
- Daily rollup aggregates raw metrics into prompt_performance_daily table
- Upsert logic: updates existing rollups, inserts new records
- Cleanup job deletes raw metrics older than 30 days (USER DECISION)
- APScheduler job runs daily at 01:00 (combines aggregation + cleanup)
- Centralized scheduler module in app/scheduler.py exports all jobs
- Refactored main.py to use scheduler module for better organization

## Task Commits

Each task was committed atomically:

1. **Task 1: Create seed script for initial prompts** - `dd3b705` (feat)
   - scripts/seed_prompts.py with PROMPTS_TO_SEED array
   - 4 prompts: classification.email_intent, extraction.email_body, extraction.pdf_scanned, extraction.image
   - Converted f-string {variable} to Jinja2 {{ variable }} syntax
   - Idempotent: queries existing before inserting
   - All prompts seeded as is_active=True
   - Email body extraction has both system_prompt and user_prompt_template
   - Vision prompts (PDF, image) have no variables (documents are visual)

2. **Task 2: Create daily metrics rollup service** - `213c125` (feat)
   - app/services/prompt_rollup.py with 3 exported functions
   - aggregate_daily_metrics() groups by prompt_template_id and date
   - Upsert logic: updates existing rollups, inserts new
   - cleanup_old_raw_metrics() deletes records older than retention_days (default 30)
   - run_daily_rollup_job() combines aggregation + cleanup for scheduler
   - Structured logging for all operations with counts

3. **Task 3: Add prompt rollup job to APScheduler** - `92bc5a0` (feat)
   - Created app/scheduler.py with centralized job definitions
   - run_prompt_rollup() wrapper for daily rollup at 01:00
   - run_scheduled_reconciliation() moved from main.py for consistency
   - start_scheduler() initializes both jobs: hourly reconciliation + daily rollup
   - Updated main.py to use scheduler module
   - CronTrigger(hour=1, minute=0) for daily execution
   - Scheduler skipped in testing environment

## Key Insights

**Idempotent seeding pattern:** The seed script checks for existing (task_type, name, version) before inserting. This allows re-running the script safely without duplicate key errors. Critical for deployment scripts.

**Jinja2 conversion gotchas:** Hardcoded prompts used Python f-strings like `{subject}`. Database prompts use Jinja2 `{{ subject }}`. Vision prompts (PDF, image) don't use variables because documents are passed as visual content.

**Upsert pattern for rollups:** Daily aggregation might run multiple times (retries, backfills). Using UPDATE existing + INSERT new prevents duplicate key errors and allows idempotent re-aggregation.

**Combined job reduces complexity:** Instead of separate "aggregate" and "cleanup" jobs, run_daily_rollup_job() does both. Reduces scheduler configuration, ensures cleanup happens after aggregation.

**Scheduler module separation:** Moving jobs from main.py to scheduler.py improves testability (can import/test jobs without starting FastAPI) and organization (all background jobs in one place).

**01:00 timing rationale:** Daily rollup runs at 01:00 (low-traffic time). Aggregates previous day's data. Cleanup happens immediately after, ensuring 30-day retention window is enforced.

## Testing Notes

**Seed script can be tested immediately:**
```bash
# After running migration (alembic upgrade head)
python scripts/seed_prompts.py
# Should output: "Seeded 4 prompts"
# Re-running should output: "Skipped 4 prompts (already exist)"
```

**Rollup job can be tested manually:**
```python
from app.database import SessionLocal
from app.services.prompt_rollup import run_daily_rollup_job
from datetime import date, timedelta

db = SessionLocal()
# Aggregate specific date
from app.services.prompt_rollup import aggregate_daily_metrics
aggregate_daily_metrics(date.today() - timedelta(days=1), db)
db.close()
```

**Scheduler can be tested without waiting 24 hours:**
```python
from app.scheduler import run_prompt_rollup
# Manually trigger rollup job
run_prompt_rollup()
# Check logs for "daily_rollup_completed"
```

## Production Deployment Checklist

Before deploying Phase 8 to production:

1. **Run migration:** `alembic upgrade head` (creates prompt_templates, metrics tables)
2. **Seed initial prompts:** `python scripts/seed_prompts.py` (one-time operation)
3. **Verify prompts active:**
   ```sql
   SELECT task_type, name, version, is_active FROM prompt_templates WHERE is_active = TRUE;
   -- Should return 4 rows (one per prompt type)
   ```
4. **Restart application:** Scheduler will start automatically
5. **Verify scheduler running:** Check `/health` endpoint for `scheduler: running`
6. **Monitor first rollup:** Check logs at 01:00 for "daily_rollup_completed"
7. **Verify metrics recording:** After first extractions, check `prompt_performance_metrics` table

## Next Phase Readiness

**For Phase 9 (Prompt Management UI):**
- ✅ Database has seeded prompts to display
- ✅ Daily metrics available for performance charts
- ✅ Version management infrastructure ready (create/activate/rollback)
- ⚠️ Need to define performance thresholds for alerting (deferred to Phase 9)

**Outstanding items:**
- Performance alerting thresholds (requires 2-4 weeks baseline data)
- Archive policy for old inactive versions (recommended: 90 days)
- Prompt editing UI (out of scope for Phase 8)

## Deviations from Plan

None - plan executed exactly as written.

## Code Statistics

- **Lines added:** 547
- **Lines removed:** 61
- **Files created:** 3
- **Files modified:** 2
- **Functions added:** 6
- **Prompts seeded:** 4

## Related Documentation

- User Decision: 30-day raw retention in 08-CONTEXT.md
- Architecture: Dual-table pattern in 08-RESEARCH.md Pattern 3
- Implementation: Seed prompt structure in 08-RESEARCH.md Code Examples

---

**Phase 8 Status:** 4 of 4 plans complete (100%)
**Next:** Phase 9 - Performance Monitoring and Alerting
