---
phase: 06-matching-engine-reconstruction
plan: 02
subsystem: matching-engine
tags: [rapidfuzz, signal-scoring, explainability, matching, fuzzy-matching]

requires:
  - phase: 05
    provides: multi-agent-pipeline
    reason: Provides extraction infrastructure that produces data for matching

provides:
  - signal-scorers
  - explainability-builder
  - rapidfuzz-integration

affects:
  - phase: 06
    plan: 03
    reason: Candidate retrieval will use these signal scorers for scoring
  - phase: 06
    plan: 04
    reason: Match orchestrator will use ExplainabilityBuilder for results

tech-stack:
  added:
    - rapidfuzz: ">=3.6.0"
  patterns:
    - "Signal scorer functions return (score, details) tuple"
    - "RapidFuzz 3.x requires explicit processor parameter"
    - "Multiple fuzzy algorithms, return best score"
    - "ExplainabilityBuilder produces JSONB-ready dict"

key-files:
  created:
    - app/services/matching/__init__.py
    - app/services/matching/signals.py
    - app/services/matching/explainability.py
  modified: []

decisions:
  - name: "Use RapidFuzz with explicit preprocessing"
    context: "RapidFuzz 3.x removed automatic preprocessing"
    choice: "Use processor=utils.default_process parameter"
    alternatives: "Manual preprocessing, downgrade to 2.x"
    rationale: "3.x is current version, explicit preprocessing is clearer"

  - name: "Reference matching handles OCR errors"
    context: "CONTEXT.MD specifies fuzzy matching for OCR errors"
    choice: "Use partial_ratio and token_sort_ratio with score_cutoff=80"
    alternatives: "Exact matching only, custom OCR error correction"
    rationale: "Handles common OCR errors (1->I, 0->O) without custom logic"

  - name: "Multiple algorithms return best score"
    context: "Name matching needs to handle various formats"
    choice: "Try token_sort, partial, token_set; return max"
    alternatives: "Single algorithm, weighted average"
    rationale: "Maximizes match success across different name formats"

metrics:
  duration: "2.6 minutes"
  completed: "2026-02-05"

issues: []
---

# Phase 06 Plan 02: Signal Scorers and Explainability Summary

**One-liner:** Name and reference signal scorers using RapidFuzz 3.x with OCR error handling, plus JSONB explainability builder.

## What Was Built

Implemented the core matching logic for the matching engine:

1. **Signal Scorers Package** (`app/services/matching/`)
   - `score_client_name()`: Name matching with RapidFuzz token_sort, partial, and token_set algorithms
   - `score_reference_numbers()`: Reference matching with OCR error fuzzy matching
   - Both return `(score, details)` tuple for explainability

2. **ExplainabilityBuilder Class**
   - Produces JSONB-ready match explanations
   - Schema version v2.0 for tracking changes
   - Includes signal scores, weights, gap detection, and filters
   - Developer-focused for debugging and threshold tuning

## Technical Implementation

### RapidFuzz 3.x Integration

**Challenge:** RapidFuzz 3.x removed automatic preprocessing (2.x auto-lowercased).

**Solution:** Use explicit `processor=utils.default_process` parameter:

```python
score = fuzz.token_sort_ratio(
    name1, name2,
    processor=utils.default_process,  # lowercase + strip punctuation
    score_cutoff=50  # early exit optimization
) / 100
```

### Name Matching Strategy

Three algorithms, return best:

1. **token_sort_ratio**: Handles word order ("Mustermann, Max" vs "Max Mustermann")
2. **partial_ratio**: Handles substring matches ("Max Mustermann" vs "Mustermann")
3. **token_set_ratio**: Handles extra/missing tokens ("Max Peter Mustermann" vs "Max Mustermann")

### Reference Matching with OCR Error Handling

Three-tier strategy:

1. **Exact match** (normalized): `AZ-12345` == `AZ-12345` → 1.0
2. **Partial ratio**: Handles OCR errors (`AZ-12345` vs `AZ-I2345` → 0.875)
3. **Token sort**: Handles word order changes

**Verification:** OCR error test (`1->I`) passed with score 0.8750 ✓

## Commits

| Commit  | Type | Description                                     |
| ------- | ---- | ----------------------------------------------- |
| d94cf0e | feat | Create signal scorers with RapidFuzz 3.x        |
| 5a9ee61 | feat | Create ExplainabilityBuilder for JSONB payloads |

## Deviations from Plan

None - plan executed exactly as written.

## Verification Results

All verification checks passed:

- ✓ Signal scorers import correctly
- ✓ ExplainabilityBuilder imports correctly
- ✓ Name match test: `score_client_name("Mustermann, Max", ..., "Max Mustermann")` → 1.0
- ✓ Reference OCR test: `score_reference_numbers("AZ-12345", ["AZ-I2345"])` → 0.8750

## Next Phase Readiness

**Ready for:** Phase 06 Plan 03 (Candidate Retrieval)

**Provides:**
- `score_client_name()` for name signal scoring
- `score_reference_numbers()` for reference signal scoring
- `ExplainabilityBuilder.build()` for match result explanations

**Blockers:** None

**Concerns:** None - signal scorers verified with test cases

## Context for Future Work

### For 06-03 (Candidate Retrieval):
- Use these signal scorers to score candidates
- Signal scorers return `(score, details)` tuple
- Details dict includes algorithm used and all scores

### For 06-04 (Match Orchestrator):
- Use ExplainabilityBuilder.build() to create JSONB payload
- Pass component_scores (raw), signal_details (from scorers), weights
- Builder handles rounding and structure

### Must-Haves Reference:

**Truths established:**
- Name matching uses RapidFuzz token_sort_ratio with explicit preprocessing ✓
- Reference matching handles OCR errors with fuzzy partial_ratio ✓
- Explainability builder produces JSONB-ready dict with signal scores ✓

**Artifacts delivered:**
- `app/services/matching/signals.py` exports `score_client_name`, `score_reference_numbers` ✓
- `app/services/matching/explainability.py` exports `ExplainabilityBuilder` ✓

**Key links verified:**
- `from rapidfuzz import fuzz, utils` ✓
- `processor=utils.default_process` pattern used ✓
