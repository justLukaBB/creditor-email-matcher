---
phase: 06-matching-engine-reconstruction
verified: 2026-02-05T23:45:00Z
status: passed
score: 6/6 must-haves verified
re_verification: false
---

# Phase 6: Matching Engine Reconstruction Verification Report

**Phase Goal:** Rebuilt matching engine with fuzzy matching, creditor_inquiries integration, and explainability replaces the bypassed v1 code, enabling reliable client/creditor assignment.

**Verified:** 2026-02-05T23:45:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Matching engine uses fuzzy matching (RapidFuzz) on names and reference numbers with configurable thresholds | ✓ VERIFIED | `app/services/matching/signals.py` uses RapidFuzz 3.x with explicit `processor=utils.default_process`, implements token_sort_ratio, partial_ratio, token_set_ratio for names; fuzzy partial_ratio for reference OCR errors. ThresholdManager queries PostgreSQL for configurable thresholds. |
| 2 | creditor_inquiries table integration narrows match candidates to recent sent emails | ✓ VERIFIED | `MatchingEngineV2._get_candidate_inquiries()` queries `creditor_inquiries` table with 30-day filter (lines 226-249). Migration creates creditor_inquiries table with indexes. Email processor calls `engine.find_match()` at line 472. |
| 3 | Explainability layer logs match reasoning (e.g., matched because name_similarity=0.92, aktenzeichen=exact) | ✓ VERIFIED | `ExplainabilityBuilder.build()` creates JSONB payload with version, signals (client_name + reference_number scores, weighted_scores, inquiry/extracted values), weights, gap, filters_applied. Stored in `MatchResult.scoring_details` JSONB column. |
| 4 | Thresholds configurable per creditor category without redeployment | ✓ VERIFIED | `ThresholdManager` queries `matching_thresholds` table with category fallback (category → default → hardcoded). Migration inserts default thresholds (min_match=0.70, gap_threshold=0.15, weights 40/60). No code changes needed for threshold updates. |
| 5 | Multiple strategies available: exact match, fuzzy match, reference-based matching | ✓ VERIFIED | Three strategies implemented: `ExactMatchStrategy` (exact name+ref match), `FuzzyMatchStrategy` (RapidFuzz with weighted signals), `CombinedStrategy` (exact first, fuzzy fallback). CombinedStrategy used as default in production. |
| 6 | Ambiguous matches (multiple candidates above threshold) route to human review | ✓ VERIFIED | `MatchingEngineV2._decide_match()` calculates gap between #1 and #2, returns status="ambiguous" if gap < threshold. Email processor calls `enqueue_ambiguous_match()` at line 582, which creates `ManualReviewQueue` entry with top-3 candidates and signal breakdown. |

**Score:** 6/6 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/models/matching_config.py` | MatchingThreshold SQLAlchemy model | ✓ VERIFIED | 57 lines, class MatchingThreshold with category, threshold_type, threshold_value, weight_name, weight_value columns. UniqueConstraint + Index defined. |
| `app/models/creditor_inquiry.py` | CreditorInquiry model for matching source | ✓ VERIFIED | Exists, contains client_name, client_name_normalized, creditor_email, reference_number, sent_at columns. |
| `app/models/match_result.py` | MatchResult with JSONB scoring_details | ✓ VERIFIED | 86 lines, uses `JSONB` type for scoring_details column (line 40), has ambiguity_gap, rank, selected_as_match columns. |
| `app/services/matching/signals.py` | Signal scorers with RapidFuzz | ✓ VERIFIED | 166 lines, exports `score_client_name()` and `score_reference_numbers()`. Uses `from rapidfuzz import fuzz, utils` with explicit `processor=utils.default_process`. Returns (score, details) tuple. |
| `app/services/matching/explainability.py` | ExplainabilityBuilder for JSONB | ✓ VERIFIED | 111 lines, ExplainabilityBuilder.build() returns dict with version="v2.0", signals, weights, gap, filters_applied structure. |
| `app/services/matching/thresholds.py` | ThresholdManager for runtime config | ✓ VERIFIED | 120 lines, queries `MatchingThreshold` table with category fallback. Methods: get_threshold(), get_weights(), get_min_match(), get_gap_threshold(). Hardcoded fallbacks: 0.70, 0.15, {40/60}. |
| `app/services/matching/strategies.py` | Three matching strategies | ✓ VERIFIED | 170+ lines, implements ExactMatchStrategy, FuzzyMatchStrategy, CombinedStrategy. All return StrategyResult dataclass. FuzzyMatchStrategy uses signal scorers, enforces both-signals-required (line 136-137). |
| `app/services/matching_engine_v2.py` | Core matching engine with find_match() | ✓ VERIFIED | 389 lines, MatchingEngineV2 class with find_match() and save_match_results() methods. Uses ThresholdManager, CombinedStrategy, ExplainabilityBuilder. 30-day creditor_inquiries filter at lines 240-249. Gap threshold logic at lines 308-349. |
| `app/services/review_queue.py` | Ambiguous match enqueueing | ✓ VERIFIED | 150+ lines, enqueue_ambiguous_match() creates ManualReviewQueue entry with top-3 candidates, signal breakdown, gap analysis, and status-specific instructions. |
| `alembic/versions/20260205_1923_add_matching_infrastructure.py` | Migration with default thresholds | ✓ VERIFIED | 218 lines, creates matching_thresholds, match_results, creditor_inquiries tables. Inserts default thresholds via op.execute(). Includes indexes and foreign keys. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `app/services/matching/signals.py` | rapidfuzz | `from rapidfuzz import fuzz, utils` | ✓ WIRED | Line 13 imports RapidFuzz. Used with `processor=utils.default_process` parameter (lines 58-60, 63-67, 70-74). requirements.txt has `rapidfuzz>=3.6.0`. |
| `app/services/matching/thresholds.py` | `app/models/matching_config.py` | SQLAlchemy query | ✓ WIRED | Lines 51-54 query `MatchingThreshold` table with filters on category and threshold_type. Returns threshold_value. |
| `app/services/matching/strategies.py` | `app/services/matching/signals.py` | Signal scorer imports | ✓ WIRED | Line 17 imports score_client_name, score_reference_numbers. FuzzyMatchStrategy.evaluate() calls them at lines 122-126, 129-132. |
| `app/services/matching_engine_v2.py` | `app/models/creditor_inquiry.py` | SQLAlchemy query with 30-day filter | ✓ WIRED | Lines 240-249 query CreditorInquiry with `sent_at >= lookback_date` and `sent_at <= received_at` filters. Returns list of candidates. |
| `app/services/matching_engine_v2.py` | `app/services/matching/strategies.py` | `strategy.evaluate()` | ✓ WIRED | Line 181 calls `self.strategy.evaluate(inquiry, extracted_data, weights)` in find_match() loop. Returns StrategyResult. |
| `app/services/matching_engine_v2.py` | `app/services/matching/explainability.py` | `ExplainabilityBuilder.build()` | ✓ WIRED | Lines 184-194 call ExplainabilityBuilder.build() with inquiry, extracted_data, scores, signals, final_score, status, gap, threshold, weights. Returns JSONB-ready dict. |
| `app/actors/email_processor.py` | `app/services/matching_engine_v2.py` | `engine.find_match()` | ✓ WIRED | Line 227 imports MatchingEngineV2. Line 459 creates engine. Line 472 calls engine.find_match() with email_id, extracted_data, from_email, received_at, creditor_category. Line 481 calls engine.save_match_results(). |
| `app/actors/email_processor.py` | `app/services/review_queue.py` | `enqueue_ambiguous_match()` | ✓ WIRED | Line 228 imports enqueue_ambiguous_match. Line 582 calls enqueue_ambiguous_match(db, email_id, matching_result) for non-auto-matched cases. Returns review queue ID. |

### Requirements Coverage

| Requirement | Status | Supporting Evidence |
|-------------|--------|---------------------|
| REQ-MATCH-01: Fuzzy matching on names/references | ✓ SATISFIED | RapidFuzz integration in signals.py with token_sort_ratio, partial_ratio, token_set_ratio for names; partial_ratio for reference OCR errors. |
| REQ-MATCH-02: creditor_inquiries integration | ✓ SATISFIED | MatchingEngineV2._get_candidate_inquiries() queries creditor_inquiries with 30-day filter. Migration creates table with indexes. |
| REQ-MATCH-03: Explainability | ✓ SATISFIED | ExplainabilityBuilder creates JSONB with version, signals (scores + weighted_scores + algorithm details), weights, gap, filters_applied. Stored in match_results.scoring_details. |
| REQ-MATCH-04: Configurable thresholds | ✓ SATISFIED | ThresholdManager queries matching_thresholds table with category-based overrides. Runtime changes without deployment. Default thresholds in migration. |
| REQ-MATCH-05: Multiple strategies | ✓ SATISFIED | Three strategies: ExactMatchStrategy, FuzzyMatchStrategy, CombinedStrategy. All implement MatchingStrategy interface. CombinedStrategy default. |
| REQ-MATCH-06: Ambiguous match routing | ✓ SATISFIED | Gap threshold logic in _decide_match() compares top two candidates. Status="ambiguous" when gap < threshold. enqueue_ambiguous_match() creates ManualReviewQueue entry with top-3 candidates. |

### Anti-Patterns Found

None detected. Code quality checks:

- No TODO/FIXME comments in production code
- No placeholder returns (return null, return {}, return [])
- All signal scorers return substantive (score, details) tuples
- All strategies enforce "both signals required" rule
- Gap threshold logic properly handles single candidate and multiple candidates
- ExplainabilityBuilder produces complete JSONB structure

### Human Verification Required

None. All success criteria can be verified programmatically.

The following would benefit from human testing in production:

1. **Threshold calibration**: After initial deployment, review match_results.scoring_details JSONB to tune thresholds based on real match distributions.
2. **OCR error handling**: Verify reference matching handles common OCR errors (1→I, 0→O, 5→S) in production creditor responses.
3. **Manual review UI**: Ensure reviewers can effectively use top-3 candidates with signal breakdown to make decisions (Phase 7 requirement).

## Overall Status: PASSED

All 6 observable truths verified. All 10 required artifacts exist, are substantive (adequate line counts), and are wired correctly. All 8 key links verified. All 6 requirements satisfied. No blocking anti-patterns found.

**Phase 6 goal achieved:** Matching engine with fuzzy matching, creditor_inquiries integration, and explainability successfully replaces bypassed v1 code.

### Production Readiness Notes

**Ready for deployment with these prerequisites:**

1. **Migration required**: Run `alembic upgrade head` to create matching_thresholds, match_results, creditor_inquiries tables.
2. **creditor_inquiries data**: Historical backfill needed from MongoDB or manual entry. Without this data, all matches will return status="no_recent_inquiry" and route to manual review.
3. **Default thresholds**: Migration inserts defaults (min_match=0.70, gap_threshold=0.15, weights 40/60). Adjust based on business requirements before deployment.
4. **Manual review queue**: Phase 7 (Manual Review UI) required for reviewers to process ambiguous matches. Queue will accumulate items until Phase 7 deployed.

**Dependencies satisfied:**
- RapidFuzz >=3.6.0 in requirements.txt
- PostgreSQL JSONB support (for scoring_details)
- DualDatabaseWriter integration (Phase 1)
- ManualReviewQueue infrastructure (Phase 5)

**Next steps:**
- Phase 7: Manual review UI to process ambiguous matches
- Phase 8: Threshold tuning based on match_results explainability data
- Production monitoring: Track match status distribution (auto_matched, ambiguous, below_threshold, no_recent_inquiry)

---

*Verified: 2026-02-05T23:45:00Z*
*Verifier: Claude (gsd-verifier)*
