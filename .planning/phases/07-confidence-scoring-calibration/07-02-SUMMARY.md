---
phase: 07-confidence-scoring-calibration
plan: 02
subsystem: confidence-routing
tags: [confidence, routing, thresholds, decision-logic]

# Dependency graph
requires: ["07-01"]
provides: ["overall-confidence-calculation", "three-tier-routing", "threshold-config"]
affects: ["07-03", "07-04", "pipeline-integration"]

# Tech tracking
tech-stack:
  added: []
  patterns: ["weakest-link-aggregation", "threshold-based-routing", "dataclass-results"]

# File tracking
key-files:
  created:
    - "app/services/confidence/overall.py"
    - "app/services/confidence/router.py"
  modified:
    - "app/services/confidence/__init__.py"
    - "app/config.py"

# Decisions
decisions:
  - id: "CONF-02-01"
    decision: "Overall confidence uses min() of extraction and match dimensions (weakest-link principle)"
    rationale: "System reliability determined by weakest component - USER DECISION from CONTEXT.md"
    alternatives: ["weighted average", "product of dimensions"]
  - id: "CONF-02-02"
    decision: "Intent confidence excluded by default from overall calculation"
    rationale: "Intent classification already has its own confidence threshold (0.7) that triggers needs_review - Claude's discretion"
    alternatives: ["always include intent", "make it user-configurable"]
  - id: "CONF-02-03"
    decision: "Three-tier routing: HIGH (>0.85), MEDIUM (0.6-0.85), LOW (<0.6)"
    rationale: "USER DECISION from CONTEXT.md - matches matching engine thresholds for consistency"
    alternatives: ["four tiers", "dynamic thresholds per creditor"]
  - id: "CONF-02-04"
    decision: "Global thresholds only (no per-creditor or per-category overrides)"
    rationale: "USER DECISION from CONTEXT.md - simplicity for initial deployment"
    alternatives: ["creditor-specific thresholds", "category-based thresholds"]
  - id: "CONF-02-05"
    decision: "7-day expiration for low-confidence manual review items"
    rationale: "USER DECISION from CONTEXT.md - prevents queue buildup, flags for batch review"
    alternatives: ["no expiration", "configurable expiration per item"]

# Metrics
duration: 3
completed: 2026-02-05
---

# Phase 07 Plan 02: Overall Confidence & Routing Summary

**One-liner:** Weakest-link overall confidence calculation with three-tier routing (AUTO_UPDATE, UPDATE_AND_NOTIFY, MANUAL_REVIEW) based on configurable thresholds

## What Was Built

### Overall Confidence Calculator (`app/services/confidence/overall.py`)

**Purpose:** Combine extraction and match confidence dimensions using weakest-link (min) principle

**Key Implementation:**
- `calculate_overall_confidence()`: Takes agent checkpoints, document types, and match result
- Returns `OverallConfidence` dataclass with full breakdown:
  - `overall`: Final score (min of all dimensions)
  - `extraction`: Extraction dimension confidence
  - `match`: Match dimension confidence
  - `intent`: Optional intent classification confidence (excluded by default)
  - `dimensions_used`: List of which dimensions contributed
  - `weakest_link`: Which dimension was the bottleneck

**Business Logic:**
- **Weakest-link principle:** `overall = min(extraction, match, [intent])`
- **Intent exclusion by default:** Intent classification confidence threshold (0.7) already triggers needs_review in Phase 5, so including it in overall would be redundant
- **Structured logging:** Logs overall score and identifies weakest dimension for debugging

**Exports:**
- `calculate_overall_confidence(agent_checkpoints, document_types, match_result, include_intent=False) -> OverallConfidence`
- `OverallConfidence` dataclass

### Routing Service (`app/services/confidence/router.py`)

**Purpose:** Route emails to appropriate handling (auto-update, update+notify, manual review) based on overall confidence

**Key Implementation:**
- `route_by_confidence()`: Determines routing tier and action based on thresholds
- `get_review_expiration_days()`: Returns 7-day expiration for LOW confidence items
- Three enums:
  - `ConfidenceLevel`: HIGH, MEDIUM, LOW
  - `RoutingAction`: AUTO_UPDATE, UPDATE_AND_NOTIFY, MANUAL_REVIEW
  - `ConfidenceRoute`: Dataclass with level, action, confidence, thresholds, reason

**Routing Tiers (USER DECISIONS from CONTEXT.md):**

| Tier | Threshold | Action | Behavior |
|------|-----------|--------|----------|
| HIGH | >0.85 | `AUTO_UPDATE` | Write to database + log entry only, **no notification** |
| MEDIUM | 0.6-0.85 | `UPDATE_AND_NOTIFY` | Write immediately to database, **notify dedicated review team** for verification |
| LOW | <0.6 | `MANUAL_REVIEW` | Route to manual review queue, **expire after 7 days** if not processed (flag for batch review) |

**Configuration:**
- Thresholds configurable via environment variables:
  - `CONFIDENCE_HIGH_THRESHOLD` (default 0.85)
  - `CONFIDENCE_LOW_THRESHOLD` (default 0.60)
- Overrides supported for testing via function parameters
- Global thresholds only (no per-creditor or per-category overrides)

**Expiration Policy:**
- LOW-confidence items: 7 days in queue, then flagged for batch review
- MEDIUM/HIGH: No queue expiration (processed immediately)

**Exports:**
- `route_by_confidence(overall_confidence, high_threshold=None, low_threshold=None) -> ConfidenceRoute`
- `get_review_expiration_days(level) -> Optional[int]`
- `ConfidenceRoute`, `ConfidenceLevel`, `RoutingAction`

### Configuration (`app/config.py`)

**Added Settings:**
```python
confidence_high_threshold: float = 0.85  # Above this = auto-update, log only
confidence_low_threshold: float = 0.60   # Below this = manual review queue
```

**Environment Variables:**
- `CONFIDENCE_HIGH_THRESHOLD`: Override high threshold (default 0.85)
- `CONFIDENCE_LOW_THRESHOLD`: Override low threshold (default 0.60)

## Integration Points

### Upstream Dependencies
- **Phase 07-01:** Uses `calculate_extraction_confidence` and `calculate_match_confidence` from `dimensions.py`
- **Phase 05:** Reads `agent_checkpoints` JSONB (agent_1_intent, agent_2_extraction)
- **Phase 06:** Reads `match_result` from MatchingEngineV2

### Downstream Integration (Future Plans)
- **Phase 07-03:** Threshold calibration service will consume `ConfidenceRoute` results to build calibration dataset
- **Phase 07-04:** Notification service will use `RoutingAction.UPDATE_AND_NOTIFY` to trigger review team alerts
- **Email processor (Phase 2/5/6):** Will call `calculate_overall_confidence()` and `route_by_confidence()` after Agent 3 consolidation and matching
- **DualDatabaseWriter:** Will check routing action to determine notification behavior

### Data Flow
```
agent_checkpoints (JSONB) ──┐
document_types (List)       ├─> calculate_overall_confidence() ─> OverallConfidence
match_result (Dict)        ─┘

OverallConfidence.overall ──> route_by_confidence() ──> ConfidenceRoute
                                                          ├─> level (HIGH/MEDIUM/LOW)
                                                          ├─> action (routing decision)
                                                          └─> reason (explanation)
```

## Decisions Made

### Technical Decisions

1. **Weakest-link aggregation (min across dimensions)**
   - Rationale: System reliability determined by weakest component, not average performance
   - USER DECISION from CONTEXT.md
   - Alternative considered: Weighted average (rejected - would mask weak links)

2. **Intent confidence excluded by default**
   - Rationale: Intent classification has its own 0.7 threshold that sets needs_review flag (Phase 5)
   - Including intent in overall would be redundant and overly conservative
   - Made configurable via `include_intent` parameter for flexibility
   - Claude's discretion based on existing Phase 5 behavior

3. **Three-tier routing (not four)**
   - Rationale: Matches matching engine thresholds (0.85/0.70) for consistency
   - USER DECISION: HIGH (>0.85), MEDIUM (0.6-0.85), LOW (<0.6)
   - Alternative considered: Four tiers with "very high" and "very low" (rejected - added complexity without clear value)

4. **Global thresholds only**
   - Rationale: USER DECISION from CONTEXT.md - simplicity for initial deployment
   - No per-creditor or per-category threshold overrides
   - Can be enhanced in future based on calibration data (Phase 07-03/04)

5. **7-day expiration for LOW confidence**
   - Rationale: USER DECISION from CONTEXT.md - prevents manual review queue buildup
   - Expired items flagged for batch review (not discarded)
   - MEDIUM and HIGH don't expire (processed immediately)

6. **Threshold overrides in function signature**
   - Rationale: Enable unit testing with different thresholds without changing environment
   - Optional parameters default to settings values
   - Supports future A/B testing or threshold experiments

### Architectural Patterns

1. **Dataclass results over tuples**
   - `OverallConfidence` and `ConfidenceRoute` provide named fields and clarity
   - Enables IDE autocomplete and type checking
   - Better than returning `(score, dimensions, weakest)` tuples

2. **Enum-based routing decisions**
   - `ConfidenceLevel` and `RoutingAction` provide type safety
   - Prevents magic strings throughout codebase
   - Makes routing logic explicit and discoverable

3. **Structured logging with context**
   - Every confidence calculation and routing decision logged
   - Includes all inputs and decision context (thresholds, reason)
   - Critical for debugging production routing issues

## Testing Evidence

All verification tests passed:

```bash
# Test 1: Import overall confidence calculator
python3 -c "from app.services.confidence import calculate_overall_confidence; print('ok')"
# Output: ok

# Test 2: LOW confidence routing
python3 -c "from app.services.confidence import route_by_confidence, RoutingAction; r = route_by_confidence(0.5); assert r.action == RoutingAction.MANUAL_REVIEW; print('low ok')"
# Output: low ok

# Test 3: MEDIUM confidence routing
python3 -c "from app.services.confidence import route_by_confidence, RoutingAction; r = route_by_confidence(0.75); assert r.action == RoutingAction.UPDATE_AND_NOTIFY; print('medium ok')"
# Output: medium ok

# Test 4: HIGH confidence routing
python3 -c "from app.services.confidence import route_by_confidence, RoutingAction; r = route_by_confidence(0.90); assert r.action == RoutingAction.AUTO_UPDATE; print('high ok')"
# Output: high ok

# Test 5: Config settings
python3 -c "from app.config import settings; print(f'high: {settings.confidence_high_threshold}, low: {settings.confidence_low_threshold}')"
# Output: high: 0.85, low: 0.6
```

**Routing Behavior Verified:**
- 0.50 confidence → MANUAL_REVIEW (LOW tier, <0.6)
- 0.75 confidence → UPDATE_AND_NOTIFY (MEDIUM tier, 0.6-0.85)
- 0.90 confidence → AUTO_UPDATE (HIGH tier, >0.85)
- Thresholds loaded correctly from settings (0.85 high, 0.60 low)

## Deviations from Plan

**None** - Plan executed exactly as written.

All tasks completed without deviations:
- Task 1: Overall confidence calculator implemented with weakest-link logic
- Task 2: Routing service with three tiers and threshold configuration
- All verification tests passed
- Success criteria satisfied

## Next Phase Readiness

### What's Ready for Phase 07-03 (Calibration Service)
- `OverallConfidence` dataclass with full dimension breakdown
- `ConfidenceRoute` with routing decisions and explanations
- Structured confidence scores (extraction, match, overall) ready for calibration dataset
- Weakest-link identifier enables dimension-specific calibration analysis

### What's Ready for Phase 07-04 (Threshold Auto-Adjustment)
- Configurable thresholds via environment variables (CONFIDENCE_HIGH_THRESHOLD, CONFIDENCE_LOW_THRESHOLD)
- Threshold override parameters in `route_by_confidence()` for testing adjusted values
- Structured logging of all routing decisions for threshold performance analysis

### What's Ready for Pipeline Integration
- `calculate_overall_confidence()` ready to call after Agent 3 consolidation + MatchingEngineV2
- `route_by_confidence()` ready to determine notification/queue behavior
- `RoutingAction` enum provides clear instructions for DualDatabaseWriter and notification service

### Blockers/Concerns

**None identified.**

**Phase 07-02 is complete and ready for:**
- Plan 07-03: Calibration service implementation (building calibration dataset from routing decisions)
- Plan 07-04: Notification service for MEDIUM-confidence items (review team alerts)
- Pipeline integration: Adding confidence calculation and routing after matching stage

### Future Enhancements (Out of Scope for Phase 7)

1. **Per-creditor threshold overrides**
   - Some creditors may be more/less reliable
   - Could adjust thresholds based on historical performance
   - Requires Phase 07-03 calibration data first

2. **Dynamic threshold adjustment**
   - Auto-tune thresholds based on reviewer corrections
   - Planned for Phase 07-04 with guardrails (min HIGH = 0.75)

3. **Confidence dimension weights**
   - Currently uses unweighted min (weakest-link)
   - Could weight extraction vs match differently for specific creditor types
   - Would require user decision and calibration data

4. **Confidence decay over time**
   - Older manual review items could have confidence adjusted
   - Would affect expiration logic
   - Not currently needed (7-day expiration sufficient)

## Files Created/Modified

### Created
- `app/services/confidence/overall.py` (107 lines)
  - `calculate_overall_confidence()` function
  - `OverallConfidence` dataclass
- `app/services/confidence/router.py` (131 lines)
  - `route_by_confidence()` function
  - `get_review_expiration_days()` function
  - `ConfidenceLevel`, `RoutingAction`, `ConfidenceRoute` types

### Modified
- `app/services/confidence/__init__.py`
  - Added exports: `calculate_overall_confidence`, `OverallConfidence`, `route_by_confidence`, `get_review_expiration_days`, `ConfidenceRoute`, `ConfidenceLevel`, `RoutingAction`
- `app/config.py`
  - Added `confidence_high_threshold: float = 0.85`
  - Added `confidence_low_threshold: float = 0.60`

## Commits

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | Overall confidence calculator | 0162b5b | overall.py, __init__.py |
| 2 | Three-tier routing service | 64b74d3 | router.py, __init__.py, config.py |

**Total:** 2 tasks, 2 commits, 4 files modified, 238 lines added

---

**Phase 07 Plan 02 Status:** ✅ Complete
**Next:** Execute Plan 07-03 (Calibration Service)
