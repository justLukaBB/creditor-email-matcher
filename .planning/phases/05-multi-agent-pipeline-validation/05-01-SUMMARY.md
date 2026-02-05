---
phase: 05-multi-agent-pipeline-validation
plan: 01
subsystem: database, validation, pipeline-infrastructure
tags: [postgresql, jsonb, pydantic, sqlalchemy, multi-agent, validation, checkpoints]

# Dependency graph
requires:
  - phase: 01-dual-database-audit-consistency
    provides: IncomingEmail model with PostgreSQL foundation
  - phase: 02-async-job-queue-infrastructure
    provides: Job state machine columns and async processing
provides:
  - agent_checkpoints JSONB column for storing intermediate agent results
  - EmailIntent enum with 6 intent types for email classification
  - IntentResult Pydantic model for intent classification validation
  - validate_with_partial_results for schema validation with partial result preservation
  - check_confidence_threshold for confidence-based review flagging
  - Checkpoint utilities (save, get, has_valid) for agent state management
affects: [
  05-02-intent-classifier,
  05-03-extraction-orchestrator,
  05-04-consolidation-agent,
  multi-agent-pipeline
]

# Tech tracking
tech-stack:
  added: []
  patterns: [
    "JSONB checkpoint storage pattern for multi-agent state",
    "Partial validation with needs_review flag (fail-open approach)",
    "Confidence threshold gating with 0.7 default",
    "flag_modified for SQLAlchemy JSONB change detection"
  ]

key-files:
  created:
    - app/models/intent_classification.py
    - app/services/validation/schema_validator.py
    - app/services/validation/confidence_checker.py
    - app/services/validation/checkpoint.py
    - alembic/versions/20260205_1722_add_agent_checkpoints.py
  modified:
    - app/models/incoming_email.py
    - app/models/__init__.py
    - app/services/validation/__init__.py

key-decisions:
  - "JSONB checkpoint storage in IncomingEmail.agent_checkpoints for agent state persistence"
  - "0.7 confidence threshold for needs_review flag (USER DECISION: fail-open, don't block)"
  - "Partial validation preserves valid fields, nulls failed fields, sets needs_review flag"
  - "Three agent names: agent_1_intent, agent_2_extraction, agent_3_consolidation"
  - "Auto-add timestamp and validation_status to all checkpoints"

patterns-established:
  - "Multi-agent checkpoint pattern: {agent_name: {result, timestamp, validation_status}}"
  - "Partial validation pattern: return {data, needs_review, validation_errors}"
  - "Confidence check pattern: return {passes, needs_review, confidence, threshold}"
  - "Skip-on-retry pattern: has_valid_checkpoint enables idempotent agent execution"

# Metrics
duration: 4min
completed: 2026-02-05
---

# Phase 05 Plan 01: Multi-Agent Pipeline Foundation Summary

**JSONB checkpoint storage with 6 intent types, partial Pydantic validation, and 0.7 confidence threshold for needs_review flagging**

## Performance

- **Duration:** 4 minutes
- **Started:** 2026-02-05T17:21:12Z
- **Completed:** 2026-02-05T17:25:14Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments

- Agent checkpoint storage infrastructure with JSONB column for intermediate results
- Intent classification models with 6 email types (debt_statement, payment_plan, rejection, inquiry, auto_reply, spam)
- Validation services enabling partial results and confidence-based review flagging
- Database migration for agent_checkpoints column addition

## Task Commits

Each task was committed atomically:

1. **Task 1: Add checkpoint storage and intent models** - `1c47e2a` (feat)
2. **Task 2: Create validation services** - `f179ee9` (feat)
3. **Task 3: Add checkpoint save utility** - `9b31a8e` (feat)

## Files Created/Modified

- `app/models/incoming_email.py` - Added agent_checkpoints JSONB column with documentation
- `app/models/intent_classification.py` - EmailIntent enum and IntentResult Pydantic model
- `app/models/__init__.py` - Export EmailIntent and IntentResult
- `app/services/validation/schema_validator.py` - Partial validation with needs_review flag
- `app/services/validation/confidence_checker.py` - 0.7 threshold check with logging
- `app/services/validation/checkpoint.py` - Save, get, and has_valid checkpoint utilities
- `app/services/validation/__init__.py` - Export all validation functions
- `alembic/versions/20260205_1722_add_agent_checkpoints.py` - Migration for JSONB column

## Decisions Made

**Checkpoint Storage Structure:**
- Store all agent results in single JSONB column (agent_checkpoints)
- Three agent namespaces: agent_1_intent, agent_2_extraction, agent_3_consolidation
- Auto-add timestamp and validation_status to every checkpoint
- Use flag_modified() for SQLAlchemy JSONB change detection

**Validation Strategy:**
- Partial validation: preserve valid fields, null failed fields, continue processing
- Set needs_review=True for validation errors (don't block pipeline)
- Confidence threshold 0.7 for needs_review flag (USER DECISION from 05-CONTEXT.md)
- Extract field names from ValidationError.errors()[i]["loc"][0]

**Intent Classification:**
- 6 intent types cover all creditor response patterns
- skip_extraction=True for auto_reply and spam intents
- method field tracks classification approach (header/regex/noreply/claude_haiku)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for Phase 05-02 (Intent Classifier Agent):**
- EmailIntent enum defines 6 classification targets
- IntentResult model validates classification output
- Checkpoint utilities enable agent state persistence
- Confidence checker ready for intent classification confidence gating

**Ready for Phase 05-03 (Extraction Orchestrator):**
- validate_with_partial_results enables partial extraction results
- Checkpoint storage enables extraction result caching
- Skip-on-retry pattern via has_valid_checkpoint

**Ready for Phase 05-04 (Consolidation Agent):**
- Checkpoint pattern established for consolidation results
- Validation utilities ready for final data validation

**No blockers.** All foundation components for multi-agent pipeline in place.

---
*Phase: 05-multi-agent-pipeline-validation*
*Completed: 2026-02-05*
