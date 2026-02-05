---
phase: 06-matching-engine-reconstruction
plan: 03
subsystem: matching-engine
tags: [rapidfuzz, postgresql, thresholds, strategies, fuzzy-matching]

# Dependency graph
requires:
  - phase: 06-01
    provides: MatchingThreshold database model for runtime configuration
  - phase: 06-02
    provides: Signal scorers (score_client_name, score_reference_numbers) with RapidFuzz

provides:
  - ThresholdManager for database-driven threshold lookup with category fallback
  - Three matching strategies: ExactMatchStrategy, FuzzyMatchStrategy, CombinedStrategy
  - StrategyResult dataclass for structured match scoring
  - Consolidated matching package exports

affects: [06-04-match-orchestrator, 06-05-integration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Database-driven configuration pattern with category-based overrides"
    - "Strategy pattern for multiple matching algorithms (REQ-MATCH-05)"
    - "Combined strategy: exact-first with fuzzy fallback for performance"

key-files:
  created:
    - app/services/matching/thresholds.py
    - app/services/matching/strategies.py
  modified:
    - app/services/matching/__init__.py

key-decisions:
  - "ThresholdManager queries database with category → default → hardcoded fallback hierarchy"
  - "Both signals (name AND reference) required for match - enforced in all strategies"
  - "CombinedStrategy recommended for production (exact fast path, fuzzy robustness)"
  - "Hardcoded fallback defaults: 0.70 min_match, 0.15 gap_threshold, 40% name / 60% reference weights"

patterns-established:
  - "Threshold lookup: try category-specific, fallback to 'default' category, fallback to hardcoded"
  - "Strategy pattern with StrategyResult dataclass for consistent return type"
  - "FuzzyMatchStrategy zeros total score if either signal is 0 (enforces both-required rule)"

# Metrics
duration: 2.4min
completed: 2026-02-05
---

# Phase 6 Plan 3: Threshold Management and Matching Strategies Summary

**Database-driven threshold configuration with ThresholdManager and three matching strategies (exact, fuzzy, combined) enforcing both-signals-required rule**

## Performance

- **Duration:** 2.4 min
- **Started:** 2026-02-05T19:44:48Z
- **Completed:** 2026-02-05T19:47:11Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- ThresholdManager enables runtime threshold tuning via PostgreSQL without deployment
- Three matching strategies implement REQ-MATCH-05 with different trade-offs
- CombinedStrategy provides optimal performance (exact fast path) with robustness (fuzzy fallback)
- All strategies enforce CONTEXT.MD "both signals required" rule

## Task Commits

Each task was committed atomically:

1. **Task 1: Create ThresholdManager for runtime database-driven configuration** - `21730e9` (feat)
2. **Task 2: Create matching strategies (exact, fuzzy, combined)** - `016d2d6` (feat)
3. **Task 3: Update matching package exports** - `db79a8f` (feat)

## Files Created/Modified
- `app/services/matching/thresholds.py` - ThresholdManager with database lookup and category fallback
- `app/services/matching/strategies.py` - ExactMatchStrategy, FuzzyMatchStrategy, CombinedStrategy implementations
- `app/services/matching/__init__.py` - Updated to export all 9 matching components

## Decisions Made

**ThresholdManager fallback hierarchy:**
- Try category-specific threshold (e.g., "bank", "inkasso")
- Fallback to "default" category
- Fallback to hardcoded constants (0.70 min_match, 0.15 gap_threshold)

**Strategy design choices:**
- ExactMatchStrategy: 1.0 only if both name AND reference match exactly (case-insensitive)
- FuzzyMatchStrategy: uses signal scorers with weighted average, zeros score if either signal is 0
- CombinedStrategy: tries exact first (performance), falls back to fuzzy (robustness)

**Weight defaults:**
- 40% client_name, 60% reference_number (from CONTEXT.MD suggestion)
- Configurable per category via database

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- ThresholdManager ready for use in match orchestrator (06-04)
- CombinedStrategy recommended as default for production matching
- All three strategies available for different use cases (exact for high-precision, fuzzy for robustness)
- Threshold configuration via database INSERT statements (developers can adjust without deployment)

**Example threshold configuration:**
```sql
INSERT INTO matching_thresholds (category, threshold_type, threshold_value)
VALUES ('default', 'min_match', 0.70),
       ('default', 'gap_threshold', 0.15);

INSERT INTO matching_thresholds (category, weight_name, weight_value, threshold_type, threshold_value)
VALUES ('default', 'client_name', 0.40, 'signal_weight', 0.40),
       ('default', 'reference_number', 0.60, 'signal_weight', 0.60);
```

---
*Phase: 06-matching-engine-reconstruction*
*Completed: 2026-02-05*
