---
phase: 03-multi-format-document-extraction
plan: 01
subsystem: extraction
tags: [pydantic, redis, cost-control, token-budget, circuit-breaker, claude-api]

# Dependency graph
requires:
  - phase: 02-async-job-queue-infrastructure
    provides: Redis infrastructure for circuit breaker
provides:
  - ExtractionResult Pydantic models for all extractors
  - TokenBudgetTracker for per-job 100K token limit
  - DailyCostCircuitBreaker for $50/day Claude API limit
  - Cost control settings in app/config.py
affects: [03-02, 03-03, 03-04, phase-4, phase-5]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Per-job token budget tracking with 80% warning threshold
    - Redis INCRBYFLOAT for atomic cost tracking
    - Pydantic models with ConfigDict(from_attributes=True) for ORM compat

key-files:
  created:
    - app/models/extraction_result.py
    - app/services/cost_control/__init__.py
    - app/services/cost_control/token_budget.py
    - app/services/cost_control/circuit_breaker.py
  modified:
    - app/config.py

key-decisions:
  - "Phase 3 extracts only gesamtforderung, client_name, creditor_name - extended fields deferred to Phase 4"
  - "Components dict used only for sum calculation when no explicit Gesamtforderung label exists"
  - "100K token budget per job with 80% warning threshold"
  - "$50/day circuit breaker with Redis INCRBYFLOAT for atomic increment"
  - "48-hour TTL on daily cost keys for debugging visibility"

patterns-established:
  - "TokenBudgetTracker pattern: check_budget() before API call, add_usage() after"
  - "DailyCostCircuitBreaker pattern: check_and_record() with optimistic cost recording"
  - "Confidence levels: HIGH (precise currency format), MEDIUM (numeric), LOW (missing)"

# Metrics
duration: 3min
completed: 2026-02-05
---

# Phase 3 Plan 1: Extraction Models and Cost Control Summary

**Pydantic models for extraction results with per-job 100K token budget and Redis-backed $50/day circuit breaker**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-05T10:02:02Z
- **Completed:** 2026-02-05T10:05:02Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- ExtractionResult models define consistent structure for all extractors (PDF, DOCX, XLSX, image, email body)
- TokenBudgetTracker enforces 100K token limit per extraction job with cost estimation
- DailyCostCircuitBreaker uses Redis atomic counters to prevent daily cost explosions
- Cost control settings added to app/config.py with Claude Sonnet 4.5 pricing

## Task Commits

Each task was committed atomically:

1. **Task 1: Create extraction result Pydantic models** - `aa0f7c8` (feat)
2. **Task 2: Create token budget tracker and daily circuit breaker** - `a15b34e` (feat)

## Files Created/Modified
- `app/models/extraction_result.py` - Pydantic models: ExtractedAmount, ExtractedEntity, SourceExtractionResult, ConsolidatedExtractionResult
- `app/services/cost_control/__init__.py` - Package exports
- `app/services/cost_control/token_budget.py` - Per-job token tracking with budget enforcement
- `app/services/cost_control/circuit_breaker.py` - Redis-backed daily cost limit
- `app/config.py` - Added max_tokens_per_job, daily_cost_limit_usd, Claude pricing settings

## Decisions Made
- **Phase 3 scope locked:** Only extracting gesamtforderung, client_name, creditor_name. Extended roadmap fields (Forderungsaufschluesselung, Bankdaten, Ratenzahlung) deferred to Phase 4.
- **Components dict purpose:** Used solely to compute Gesamtforderung = Hauptforderung + Zinsen + Kosten when no explicit total label exists. Not for extended extraction.
- **Optimistic cost recording:** Circuit breaker records estimated cost before API call (safer than after - prevents budget overrun on crash).
- **48-hour key TTL:** Daily cost keys kept 2 days for debugging visibility.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required. Cost control uses existing Redis infrastructure from Phase 2.

## Next Phase Readiness
- Extraction models ready for PDF extractor (03-02)
- Cost control infrastructure ready for Claude Vision integration
- Settings configurable via environment variables if defaults need adjustment
- TokenBudgetTracker and DailyCostCircuitBreaker injectable for testing

---
*Phase: 03-multi-format-document-extraction*
*Completed: 2026-02-05*
