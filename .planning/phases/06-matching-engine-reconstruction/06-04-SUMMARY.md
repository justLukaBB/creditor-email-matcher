---
phase: 06-matching-engine-reconstruction
plan: 04
subsystem: matching-engine
tags: [rapidfuzz, sqlalchemy, matching, explainability, creditor-inquiries]

# Dependency graph
requires:
  - phase: 06-01
    provides: MatchingThreshold, CreditorInquiry, MatchResult models
  - phase: 06-02
    provides: Signal scorers (score_client_name, score_reference_numbers) and ExplainabilityBuilder
  - phase: 06-03
    provides: ThresholdManager and matching strategies (ExactMatchStrategy, FuzzyMatchStrategy, CombinedStrategy)
provides:
  - MatchingEngineV2 class with creditor_inquiries 30-day filtering
  - find_match() orchestration method with gap threshold logic
  - save_match_results() persistence to MatchResult table
  - MatchCandidate and MatchingResult dataclasses
affects: [06-05-pipeline-integration, manual-review-integration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Gap threshold for ambiguity detection (top_score - second_score >= threshold)"
    - "30-day creditor_inquiries filtering as search space narrowing optimization"
    - "Both signals required enforcement (name AND reference)"

key-files:
  created:
    - app/services/matching_engine_v2.py
    - tests/test_matching_engine_v2.py
  modified: []

key-decisions:
  - "30-day lookback window for creditor_inquiries (matches business expectation of response times)"
  - "Gap threshold ambiguity detection: gap < threshold routes to manual review with top 3 candidates"
  - "All candidates get explainability JSONB regardless of match status"
  - "save_match_results uses db.flush() not commit (caller controls transaction)"

patterns-established:
  - "MatchingResult dataclass encapsulates status, match, candidates, gap, needs_review flag"
  - "Both signals required: if either name or reference score is 0, total score is penalized"
  - "Ambiguous matches route to manual review with top 3 candidates for reviewer decision"

# Metrics
duration: 2.8min
completed: 2026-02-05
---

# Phase 06 Plan 04: Match Orchestrator Summary

**MatchingEngineV2 with creditor_inquiries 30-day filtering, gap threshold ambiguity detection, and explainability JSONB for all candidates**

## Performance

- **Duration:** 2.8 minutes
- **Started:** 2026-02-05T21:56:52Z
- **Completed:** 2026-02-05T21:59:43Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- MatchingEngineV2 filters candidates by 30-day creditor_inquiries window
- Gap threshold determines auto_matched vs ambiguous status
- All match results include explainability JSONB with signal breakdown
- save_match_results persists to MatchResult table with rank and selection_method

## Task Commits

Each task was committed atomically:

1. **Task 1: Create MatchingEngineV2 with creditor_inquiries filtering** - `d25f239` (feat)
2. **Task 2: Add tests for matching engine core functionality** - `d0655ee` (test)

## Files Created/Modified
- `app/services/matching_engine_v2.py` - Core matching engine with find_match() and save_match_results()
- `tests/test_matching_engine_v2.py` - Tests for no_candidates, both_signals_required, gap_threshold, explainability

## Decisions Made

1. **30-day lookback window**: Matches business expectation that creditors respond within a month
2. **Gap threshold for ambiguity**: When top match is not "clearly ahead" (gap < 0.15), route to manual review with top 3 candidates
3. **All candidates get explainability**: Even below-threshold and ambiguous matches get full scoring_details JSONB for debugging
4. **db.flush() not commit**: save_match_results uses flush() to get IDs without committing, letting caller control transaction boundaries

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## Next Phase Readiness

- MatchingEngineV2 ready for pipeline integration (06-05)
- Tests verify core functionality (no_candidates, both_signals_required, gap_threshold, explainability format)
- Manual review queue integration can consume needs_review flag and candidates list
- Threshold calibration can use scoring_details JSONB for analysis

**Blockers:** None

---
*Phase: 06-matching-engine-reconstruction*
*Completed: 2026-02-05*
