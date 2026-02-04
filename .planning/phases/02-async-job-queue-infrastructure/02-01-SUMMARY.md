---
phase: 02-async-job-queue-infrastructure
plan: 01
subsystem: infra
tags: [dramatiq, redis, async-jobs, worker, message-broker]

# Dependency graph
requires:
  - phase: 01-dual-database-audit-consistency
    provides: Structured logging (structlog), database infrastructure, saga pattern foundation
provides:
  - Dramatiq broker configuration (RedisBroker for production, StubBroker for testing)
  - Worker entrypoint for dramatiq CLI
  - Settings extended with redis_url, worker config, and missing fields
  - Foundation for async job processing in subsequent plans
affects: [02-02, 02-03, 02-04, 03-email-processing, 04-content-extraction, 05-intent-consolidation]

# Tech tracking
tech-stack:
  added: [dramatiq[redis]>=2.0.1, psutil>=5.9.0]
  patterns: [async-job-processing, worker-entrypoint, broker-singleton, stub-testing]

key-files:
  created:
    - app/actors/__init__.py
    - app/worker.py
  modified:
    - app/config.py
    - requirements.txt

key-decisions:
  - "RedisBroker for production with connection pooling, StubBroker for testing without Redis"
  - "Namespace creditor_matcher for Redis key isolation"
  - "2 worker processes x 1 thread for memory efficiency on Render 512MB container"
  - "Worker entrypoint as importable module for dramatiq CLI discovery"

patterns-established:
  - "Broker setup function returns RedisBroker or StubBroker based on redis_url presence"
  - "Worker imports actors package to trigger broker setup and actor registration"
  - "Settings fields added preemptively for referenced but undefined config (environment, webhook_secret, llm_provider)"

# Metrics
duration: 3min
completed: 2026-02-04
---

# Phase 02 Plan 01: Broker Infrastructure Setup Summary

**Dramatiq + Redis broker foundation with RedisBroker/StubBroker auto-switching, worker entrypoint, and extended Settings for async job processing**

## Performance

- **Duration:** 3 minutes (156 seconds)
- **Started:** 2026-02-04T16:04:04Z
- **Completed:** 2026-02-04T16:06:40Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Dramatiq broker infrastructure ready for actor registration
- Worker entrypoint configured for dramatiq CLI with memory-conscious process/thread settings
- Settings class extended with 11 missing fields (redis_url, worker config, environment, webhook_secret, llm_provider, SMTP settings)
- Production/testing mode auto-detection via redis_url presence

## Task Commits

Each task was committed atomically:

1. **Task 1: Dramatiq broker setup and actors package** - `c3a202c` (feat)
2. **Task 2: Worker entrypoint and startup script** - `b071348` (feat)

## Files Created/Modified
- `app/actors/__init__.py` - Dramatiq broker singleton with RedisBroker (production) or StubBroker (testing)
- `app/worker.py` - Worker entrypoint for dramatiq CLI with Procfile documentation and memory budget guidance
- `app/config.py` - Extended Settings with redis_url, worker_processes, worker_threads, environment, webhook_secret, llm_provider, SMTP settings
- `requirements.txt` - Added dramatiq[redis]>=2.0.1 and psutil>=5.9.0

## Decisions Made

1. **Broker auto-switching based on redis_url:** RedisBroker when REDIS_URL environment variable is set (production), StubBroker when not set (testing/development). This allows tests to run without Redis dependency.

2. **Redis configuration:** Namespace `creditor_matcher` for key isolation, connection pooling (max_connections=10), socket timeouts (5s connect, 5s read), keepalive enabled, retry on timeout, 30s heartbeat, 24h dead message TTL.

3. **Worker process/thread configuration:** Defaults to 2 processes x 1 thread for memory efficiency on Render 512MB container (~350MB worker budget after 150MB FastAPI). Documented in worker.py for operational clarity.

4. **Preemptive Settings fields:** Added `environment`, `webhook_secret`, and `llm_provider` fields that were already referenced in main.py and webhook.py but not defined in Settings class. This fixes a bug where accessing undefined settings would raise AttributeError.

5. **SMTP settings for notifications:** Added admin_email, smtp_host, smtp_port, smtp_username, smtp_password fields for future email notification features (referenced in plan but not yet used).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Added missing Settings fields (environment, webhook_secret, llm_provider)**
- **Found during:** Task 1 (config.py review)
- **Issue:** app/main.py references `settings.environment` (lines 32, 33, 75, 82, 123, 191) and app/routers/webhook.py references `settings.webhook_secret` (lines 85, 87) and `settings.llm_provider` (lines 213, 219), but these fields were not defined in Settings class. This would cause AttributeError at runtime.
- **Fix:** Added `environment: str = "development"`, `webhook_secret: Optional[str] = None`, and `llm_provider: str = "claude"` to Settings class.
- **Files modified:** app/config.py
- **Verification:** `python3 -c "from app.config import settings; print(settings.environment)"` prints "development" without error.
- **Committed in:** c3a202c (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug - missing config fields)
**Impact on plan:** Bug fix necessary for correct operation. Existing code referenced these fields but they were undefined. No scope creep - just ensuring consistency between code and config.

## Issues Encountered

None - plan executed smoothly with one bug fix for missing Settings fields.

## User Setup Required

**External services require manual configuration.** See plan frontmatter `user_setup` section:

**Service:** Redis (message broker for Dramatiq)
- **Why:** Dramatiq requires Redis as message broker for production use
- **Environment variable:** `REDIS_URL` (example: `redis://red-xxxxx:6379/0`)
- **Source:** Render Dashboard → Add Redis add-on → copy Internal URL
- **Verification:**
  ```bash
  python3 -c "from app.config import settings; print(settings.redis_url)"
  # Should print Redis URL (not None)

  python3 -c "from app.actors import broker; print(type(broker).__name__)"
  # Should print RedisBroker (not StubBroker)
  ```

**Note:** Without REDIS_URL, broker falls back to StubBroker (in-memory, testing only). This is intentional for development/testing but production deployment requires Redis.

## Next Phase Readiness

**Ready for Phase 02 Plan 02 (Database models for job state machine):**
- Broker infrastructure is in place
- Worker entrypoint ready for actor registration
- Settings extended with all required fields
- Redis add-on setup documented for user

**Next steps:**
1. User adds Redis add-on on Render and sets REDIS_URL environment variable
2. Plan 02 creates database models for job state machine (job_executions table)
3. Plan 03 implements email_processor actor
4. Plan 04 adds retry/error handling middleware

**No blockers.** Foundation is complete and ready for actor implementation.

---
*Phase: 02-async-job-queue-infrastructure*
*Completed: 2026-02-04*
