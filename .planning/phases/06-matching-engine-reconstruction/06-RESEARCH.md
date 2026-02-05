# Phase 6: Matching Engine Reconstruction - Research

**Researched:** 2026-02-05
**Domain:** Entity matching with fuzzy string matching and explainability
**Confidence:** HIGH

## Summary

Phase 6 rebuilds the v1 matching engine (currently in codebase) with enhanced capabilities: fuzzy matching via RapidFuzz, creditor_inquiries table filtering, explainability logging in PostgreSQL JSONB, and configurable thresholds. The v1 engine already implements weighted scoring (40% client name, 30% creditor, 20% time, 10% reference) and uses RapidFuzz 3.6.0, providing a solid foundation.

**Key findings:**
- RapidFuzz 3.x requires explicit preprocessing (no longer default), use `token_sort_ratio` for name matching with word order differences
- creditor_inquiries table exists with normalized name columns and 30-day lookback pattern already implemented
- PostgreSQL JSONB is optimal for explainability storage with selective retrieval to avoid TOAST performance penalties
- Configurable thresholds stored in database table enable runtime tuning without deployment

**Primary recommendation:** Extend v1 matching engine by adding CONTEXT.md-mandated features (both signals required, gap threshold for ambiguity, explainability JSONB storage, database-driven configuration) rather than complete rewrite.

## Standard Stack

The established libraries/tools for fuzzy matching and explainability:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| RapidFuzz | 3.6.0 | Fuzzy string matching (names, references, OCR errors) | Already in requirements.txt; industry-standard C++ implementation 100x faster than FuzzyWuzzy |
| PostgreSQL | 2.0.25+ | Storage for thresholds, explainability JSONB, match results | Existing source of truth from Phase 1; JSONB for semi-structured data |
| SQLAlchemy | 2.0.25+ | ORM for threshold configuration queries | Existing ORM infrastructure |
| Pydantic | 2.5.0+ | Validation schemas for match results | Existing validation layer from Phase 5 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | 24.1.0+ | Correlation IDs for explainability audit trail | Already integrated in Phase 5; use for linking match decisions to extraction checkpoints |
| pyspellchecker | 0.8.4+ | German-aware OCR error correction | Phase 4 German text processing; apply to Aktenzeichen before fuzzy matching |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| RapidFuzz | FuzzyWuzzy | FuzzyWuzzy is deprecated and 100x slower; RapidFuzz is drop-in replacement |
| PostgreSQL JSONB | Separate explainability table | Separate table requires joins; JSONB keeps match + explanation atomic |
| Database config | Environment variables | Env vars require redeployment; database allows runtime threshold tuning |

**Installation:**
```bash
# Already in requirements.txt:
# rapidfuzz>=3.6.0
# sqlalchemy>=2.0.25
# psycopg[binary]>=3.3.0
# No new dependencies required
```

## Architecture Patterns

### Recommended Project Structure
```
app/services/
├── matching_engine.py           # MatchingEngine class (v1 exists)
├── matching/
│   ├── __init__.py
│   ├── signals.py               # NEW: Signal scorers (name, reference, time, email domain)
│   ├── strategies.py            # NEW: MatchingStrategy (exact, fuzzy, reference-based)
│   ├── thresholds.py            # NEW: ThresholdManager (database-driven config)
│   └── explainability.py        # NEW: ExplainabilityBuilder (JSONB format)
app/models/
├── match_result.py              # EXISTS: MatchResult model with JSONB column
├── matching_config.py           # NEW: MatchingThreshold model
└── creditor_inquiry.py          # EXISTS: CreditorInquiry (in _existing-code)
```

### Pattern 1: Signal-Based Weighted Scoring
**What:** Each matching signal (name similarity, reference match, time decay, email domain) is scored 0.0-1.0, then weighted and summed. V1 implementation already uses this pattern.

**When to use:** Multi-dimensional entity matching where no single signal is authoritative.

**Example:**
```python
# Source: Existing v1 matching_engine.py (lines 166-230)
# CONTEXT.md decision: Both name AND reference required (no auto-match on single signal)

def _score_inquiry(self, inquiry, extracted, from_email, received_at) -> MatchResult:
    component_scores = {}
    scoring_details = {}

    # Signal 1: Client Name (40% weight in v1; CONTEXT.md says configurable)
    client_score = self._score_client_name(
        inquiry.client_name_normalized,
        extracted.client_name
    )
    component_scores["client_name"] = client_score * self.WEIGHT_CLIENT_NAME
    scoring_details["client_name"] = {
        "inquiry_name": inquiry.client_name,
        "extracted_name": extracted.client_name,
        "fuzzy_ratio": client_score,
        "weight": self.WEIGHT_CLIENT_NAME
    }

    # Signal 2: Reference Number (10% weight in v1; CONTEXT.md says 60% suggested)
    # CONTEXT.md: Handle OCR errors with fuzzy matching
    ref_score = self._score_reference_numbers_fuzzy(  # NEW: fuzzy variant
        inquiry.reference_number,
        extracted.reference_numbers
    )

    # CONTEXT.md: Both signals required for match
    # If either name or reference score is 0, overall score should be penalized
    if client_score == 0 or ref_score == 0:
        total_score = 0.0  # Hard requirement
    else:
        total_score = sum(component_scores.values())

    return MatchResult(inquiry, total_score, component_scores, scoring_details)
```

### Pattern 2: Database-Driven Threshold Configuration
**What:** Store thresholds in PostgreSQL table with category-based overrides, query at runtime for each match operation.

**When to use:** When thresholds need tuning based on production data without redeployment.

**Example:**
```python
# Source: Data-driven rules engine pattern from research
# https://medium.com/@jonblankenship/using-the-specification-pattern-to-build-a-data-driven-rules-engine-b3db95189ff8

class MatchingThreshold(Base):
    """Configuration table for matching thresholds"""
    __tablename__ = "matching_thresholds"

    id = Column(Integer, primary_key=True)
    category = Column(String(50), nullable=False)  # "default", "bank", "inkasso"
    threshold_type = Column(String(50), nullable=False)  # "min_match", "gap_threshold"
    threshold_value = Column(Numeric(5, 4), nullable=False)  # 0.0000 to 1.0000
    weight_name = Column(String(50), nullable=True)  # "client_name", "reference"
    weight_value = Column(Numeric(5, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class ThresholdManager:
    """Runtime threshold lookup with category-based overrides"""

    def __init__(self, db: Session):
        self.db = db
        self._cache = {}  # Optional: cache for performance

    def get_threshold(self, creditor_category: str, threshold_type: str) -> float:
        """
        Get threshold with category override fallback to default.

        Args:
            creditor_category: "bank", "inkasso", "agency", etc.
            threshold_type: "min_match", "gap_threshold"
        """
        # Try category-specific first
        threshold = self.db.query(MatchingThreshold).filter(
            MatchingThreshold.category == creditor_category,
            MatchingThreshold.threshold_type == threshold_type
        ).first()

        if threshold:
            return float(threshold.threshold_value)

        # Fallback to default
        default = self.db.query(MatchingThreshold).filter(
            MatchingThreshold.category == "default",
            MatchingThreshold.threshold_type == threshold_type
        ).first()

        return float(default.threshold_value) if default else 0.70  # Hardcoded fallback

    def get_weights(self, creditor_category: str) -> Dict[str, float]:
        """Get signal weights for category (e.g., 40% name, 60% reference)"""
        weights = self.db.query(MatchingThreshold).filter(
            MatchingThreshold.category == creditor_category,
            MatchingThreshold.weight_name.isnot(None)
        ).all()

        if not weights:
            # Fallback to defaults
            weights = self.db.query(MatchingThreshold).filter(
                MatchingThreshold.category == "default",
                MatchingThreshold.weight_name.isnot(None)
            ).all()

        return {w.weight_name: float(w.weight_value) for w in weights}
```

### Pattern 3: Ambiguity Detection with Gap Threshold
**What:** When top match is "clearly ahead" of second place (gap > threshold), auto-select. Otherwise route to manual review with top-k candidates.

**When to use:** High-stakes matching where false positives are costly (e.g., updating wrong client record).

**Example:**
```python
# Source: CONTEXT.md decisions and tie-breaking research
# https://en.wikipedia.org/wiki/Candidates_Tournament_2026 (tie-break procedures)

def find_best_match(
    self,
    match_results: List[MatchResult],
    gap_threshold: float = 0.15  # CONTEXT.md: TBD, calibrate from data
) -> Dict[str, Any]:
    """
    Determine best match with ambiguity detection.

    Returns:
        {
            "match": MatchResult or None,
            "status": "auto_matched" | "ambiguous" | "no_candidates",
            "candidates": [top 3 MatchResult],
            "gap": float (difference between #1 and #2)
        }
    """
    if not match_results:
        return {"match": None, "status": "no_candidates", "candidates": []}

    # Sort descending by score
    sorted_matches = sorted(match_results, key=lambda x: x.total_score, reverse=True)

    top_match = sorted_matches[0]

    # Check minimum threshold first
    min_threshold = 0.70  # Should come from ThresholdManager
    if top_match.total_score < min_threshold:
        return {
            "match": None,
            "status": "below_threshold",
            "candidates": sorted_matches[:3]
        }

    # If only one candidate, auto-match
    if len(sorted_matches) == 1:
        return {
            "match": top_match,
            "status": "auto_matched",
            "candidates": [top_match],
            "gap": 1.0  # No competition
        }

    # Calculate gap between #1 and #2
    second_match = sorted_matches[1]
    gap = top_match.total_score - second_match.total_score

    # CONTEXT.md: "clearly ahead" determination
    if gap >= gap_threshold:
        return {
            "match": top_match,
            "status": "auto_matched",
            "candidates": sorted_matches[:3],
            "gap": gap
        }
    else:
        # Ambiguous: route to manual review
        return {
            "match": None,
            "status": "ambiguous",
            "candidates": sorted_matches[:3],  # Show top 3
            "gap": gap
        }
```

### Pattern 4: Explainability with PostgreSQL JSONB
**What:** Store match reasoning as JSONB column on match_results table, prune after 90 days.

**When to use:** Debugging, threshold calibration, audit trail for regulatory compliance.

**Example:**
```python
# Source: PostgreSQL JSONB research
# https://www.architecture-weekly.com/p/postgresql-jsonb-powerful-storage
# CONTEXT.md: Developer audience, signal scores only, 90-day retention

class MatchResult(Base):
    """Existing model in _existing-code/app/models/match_result.py"""
    __tablename__ = "match_results"

    id = Column(Integer, primary_key=True)
    incoming_email_id = Column(Integer, ForeignKey("incoming_emails.id"))
    creditor_inquiry_id = Column(Integer, ForeignKey("creditor_inquiries.id"))
    total_score = Column(Numeric(5, 4), nullable=False)
    confidence_level = Column(String(20), nullable=False)

    # EXISTING: scoring_details JSONB column (line 39 in v1)
    scoring_details = Column(JSON, nullable=True)  # Already exists!

    calculated_at = Column(DateTime(timezone=True), server_default=func.now())

# Explainability builder
class ExplainabilityBuilder:
    """Build JSONB explainability payloads for match results"""

    @staticmethod
    def build(
        inquiry: CreditorInquiry,
        extracted: ExtractedEntities,
        component_scores: Dict[str, float],
        final_score: float,
        match_status: str
    ) -> Dict[str, Any]:
        """
        CONTEXT.MD: Signal scores only, no verbose reasoning.
        """
        return {
            "version": "v2.0",  # Track explainability schema version
            "match_status": match_status,  # "auto_matched", "ambiguous", "below_threshold"
            "final_score": round(final_score, 4),
            "signals": {
                "client_name": {
                    "score": round(component_scores.get("client_name", 0), 4),
                    "inquiry_value": inquiry.client_name,
                    "extracted_value": extracted.client_name,
                    "algorithm": "token_sort_ratio"  # RapidFuzz function used
                },
                "reference_number": {
                    "score": round(component_scores.get("reference", 0), 4),
                    "inquiry_value": inquiry.reference_number,
                    "extracted_value": extracted.reference_numbers,
                    "algorithm": "partial_ratio_fuzzy"  # OCR error tolerance
                },
                "creditor_match": {
                    "score": round(component_scores.get("creditor", 0), 4),
                    "inquiry_domain": inquiry.creditor_email.split('@')[-1],
                    "from_domain": extracted.from_email.split('@')[-1],
                    "algorithm": "domain_exact_or_partial"
                },
                "time_relevance": {
                    "score": round(component_scores.get("time", 0), 4),
                    "days_elapsed": (extracted.received_at - inquiry.sent_at).days,
                    "inquiry_sent_at": inquiry.sent_at.isoformat(),
                    "algorithm": "decay_curve"
                }
            },
            "weights": {  # CONTEXT.MD: Configurable weights
                "client_name": 0.40,
                "reference_number": 0.60,
                "creditor": 0.00,  # Could be excluded if using creditor_inquiries filter
                "time": 0.00
            },
            "filters_applied": {
                "creditor_inquiries_window_days": 30,  # CONTEXT.MD: 30-day window
                "both_signals_required": True  # CONTEXT.MD: name AND reference
            }
        }

# Query explainability with selective retrieval (avoid TOAST penalty)
# Source: https://www.michal-drozd.com/en/blog/postgresql-toast-optimization/
# SELECT id, total_score, confidence_level FROM match_results WHERE ...
# SELECT scoring_details FROM match_results WHERE id = ? (separate query only when needed)
```

### Anti-Patterns to Avoid
- **Hardcoded thresholds in code:** Requires redeployment to tune; use database table instead
- **SELECT * with JSONB columns:** Triggers TOAST decompression on every query; select JSONB columns explicitly only when needed
- **Single fuzzy algorithm:** Different signals need different algorithms (token_sort_ratio for names, partial_ratio for references)
- **Exact reference matching only:** OCR errors in scanned documents require fuzzy matching on Aktenzeichen
- **Auto-match without creditor_inquiries filter:** False matches from reused reference numbers; require recent inquiry record

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Fuzzy string matching | Custom Levenshtein distance, character-by-character comparison | RapidFuzz (token_sort_ratio, partial_ratio, WRatio) | Handles word order, substrings, special characters; C++ implementation 100x faster; battle-tested on millions of matches |
| OCR error correction | Pattern matching for common errors (0→O, 1→l) | RapidFuzz partial_ratio + pyspellchecker German dictionary | OCR errors are context-dependent; fuzzy matching handles all edit distance errors; pyspellchecker already integrated in Phase 4 |
| Threshold configuration | JSON config files, environment variables | PostgreSQL table with category-based overrides | Runtime updates without deployment; audit trail of threshold changes; per-category tuning (banks vs Inkasso) |
| Explainability format | Custom JSON schema, log files | PostgreSQL JSONB with versioned schema | Structured queries (WHERE scoring_details->>'match_status' = 'ambiguous'); atomic with match result; TOAST compression for large payloads |
| German name normalization | Custom regex for umlauts, punctuation | Phase 4 German text processing (unicode NFKC + existing normalization) | Already handles "Mustermann, Max" → "mustermann max"; consistent with extraction pipeline |

**Key insight:** Fuzzy matching is deceptively complex. RapidFuzz implements multiple algorithms (ratio, partial_ratio, token_sort_ratio, token_set_ratio) optimized for different scenarios. Custom implementations miss edge cases and are orders of magnitude slower.

## Common Pitfalls

### Pitfall 1: RapidFuzz 3.x Preprocessing Surprise
**What goes wrong:** Strings not preprocessed by default in RapidFuzz 3.x, leading to case-sensitive matches and punctuation false negatives.

**Why it happens:** RapidFuzz 3.0+ removed automatic preprocessing for performance. V1 code uses `.lower()` manually, but inconsistently.

**How to avoid:**
```python
from rapidfuzz import fuzz, utils

# WRONG (v1 code pattern, inconsistent):
score = fuzz.token_sort_ratio(name1.lower(), name2.lower()) / 100

# CORRECT (use processor parameter):
score = fuzz.token_sort_ratio(
    name1,
    name2,
    processor=utils.default_process,  # Lowercases, removes punctuation
    score_cutoff=50  # Early exit if score < 50
) / 100
```

**Warning signs:** Matches fail on "Müller" vs "Mueller", "Mustermann, Max" vs "Max Mustermann"

**Source:** [RapidFuzz documentation](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html), [RapidFuzz best practices](https://medium.com/@shahparthvi22/all-about-rapidfuzz-string-similarity-and-matching-cd26fdc963d8)

### Pitfall 2: Aktenzeichen Format Diversity
**What goes wrong:** Exact matching on Aktenzeichen fails because format varies by court type, institution, year format.

**Why it happens:** German legal documents use institution-specific formats: "XII ZR 17/04" (civil), "5 StR 231/03" (criminal), "AZ-12345/2024" (agency). OCR errors compound the problem.

**How to avoid:**
```python
def _score_reference_numbers_fuzzy(
    self,
    inquiry_reference: Optional[str],
    extracted_references: List[str]
) -> float:
    """
    Fuzzy reference matching to handle OCR errors and format variations.

    Aktenzeichen formats:
    - Court: "XII ZR 17/04", "5 StR 231/03"
    - Agency: "AZ-12345/2024", "KD-98765"
    - Bank: "REF/2024/001"

    OCR errors: 0→O, 1→l, 8→B, slashes vs dashes
    """
    if not inquiry_reference or not extracted_references:
        return 0.0

    # Normalize: uppercase, strip whitespace
    inquiry_norm = inquiry_reference.upper().strip()

    best_score = 0.0
    for ref in extracted_references:
        ref_norm = ref.upper().strip()

        # Strategy 1: Exact match after normalization
        if inquiry_norm == ref_norm:
            return 1.0

        # Strategy 2: Partial match (handles missing prefix/suffix)
        partial_score = fuzz.partial_ratio(
            inquiry_norm,
            ref_norm,
            processor=None,  # Already normalized
            score_cutoff=80  # High threshold for references
        ) / 100

        # Strategy 3: Token-based (handles word order in multi-part refs)
        token_score = fuzz.token_sort_ratio(
            inquiry_norm,
            ref_norm,
            processor=None,
            score_cutoff=80
        ) / 100

        best_score = max(best_score, partial_score, token_score)

    return best_score
```

**Warning signs:** Manual review shows matches with identical-looking Aktenzeichen rejected; OCR'd "AZ-12345" doesn't match "AZ-I2345" (1→I)

**Source:** [Aktenzeichen Wikipedia](https://en.wikipedia.org/wiki/German_legal_citation), [OCR fuzzy matching](https://matchdatapro.com/fuzzy-matching-101-a-complete-guide-for-2026/)

### Pitfall 3: JSONB TOAST Performance Trap
**What goes wrong:** Queries slow to 850ms when selecting `scoring_details` JSONB column, even with WHERE clause.

**Why it happens:** PostgreSQL TOAST stores large JSONB values separately; `SELECT *` decompresses TOAST'd columns even if not needed.

**How to avoid:**
```python
# WRONG: Fetch JSONB on every query
matches = db.query(MatchResult).filter(
    MatchResult.incoming_email_id == email_id
).all()  # SELECT * includes scoring_details, triggers TOAST

# CORRECT: Selective column retrieval
matches = db.query(
    MatchResult.id,
    MatchResult.total_score,
    MatchResult.confidence_level,
    MatchResult.creditor_inquiry_id
).filter(
    MatchResult.incoming_email_id == email_id
).all()  # Omit scoring_details

# Fetch explainability ONLY when needed (developer debugging)
if need_explainability:
    details = db.query(MatchResult.scoring_details).filter(
        MatchResult.id == match_id
    ).scalar()
```

**Warning signs:** List matches endpoint slow despite indexed WHERE clause; EXPLAIN shows "TOAST decompression" in query plan

**Source:** [PostgreSQL TOAST optimization](https://www.michal-drozd.com/en/blog/postgresql-toast-optimization/), [PostgreSQL JSONB performance](https://www.credativ.de/en/blog/postgresql-en/toasted-jsonb-data-in-postgresql-performance-tests-of-different-compression-algorithms/)

### Pitfall 4: Gap Threshold Too Strict or Too Loose
**What goes wrong:**
- **Too strict (gap_threshold=0.30):** Many auto-matchable cases route to manual review, overwhelming reviewers
- **Too loose (gap_threshold=0.05):** Ambiguous matches auto-selected, causing wrong client/creditor assignments

**Why it happens:** Gap threshold balances precision (few errors) vs recall (few manual reviews). Optimal value depends on signal quality and cost of errors.

**How to avoid:**
```python
# CONTEXT.MD: TBD, calibrate from error analysis
# Recommended: Start conservative (0.15), tune based on production data

# Log gap values for calibration
logger.info(
    "match_decision",
    email_id=email_id,
    top_score=top_match.total_score,
    second_score=second_match.total_score,
    gap=gap,
    gap_threshold=gap_threshold,
    decision="auto_matched" if gap >= gap_threshold else "ambiguous"
)

# Collect data for threshold optimization:
# SELECT AVG(gap) FROM match_results WHERE selected_as_match = true AND selection_method = 'manual'
# → If manual reviewers consistently pick top match, gap_threshold too strict
# → If manual reviewers often pick #2 or #3, gap_threshold too loose
```

**Recommended calibration process:**
1. **Shadow mode:** Run matching engine, log all decisions but route 100% to manual review
2. **Analyze gap distribution:** Plot gap values vs reviewer decisions (picked #1, #2, #3)
3. **F1 optimization:** Find gap_threshold maximizing F1 score (balance precision/recall)
4. **Gradual rollout:** Start with strict threshold (0.20), lower as confidence grows

**Warning signs:** Manual review queue overwhelmed with obvious matches OR frequency of wrong auto-matches increasing

**Source:** [F1 score threshold tuning](https://gpttutorpro.com/f1-machine-learning-essentials-optimizing-f1-score-with-threshold-tuning/), [Optimal threshold selection](https://machinelearningmastery.com/threshold-moving-for-imbalanced-classification/)

### Pitfall 5: Ignoring creditor_inquiries 30-Day Window
**What goes wrong:** Matching engine searches all historical inquiries, causing:
- False matches from reused reference numbers
- Performance degradation with large inquiry history
- Matches to expired/irrelevant inquiries

**Why it happens:** Reference numbers may be reused across years; email responses expected within weeks, not months.

**How to avoid:**
```python
# V1 pattern (correct):
lookback_date = received_at - timedelta(days=settings.match_lookback_days)  # 30 days

candidates = self.db.query(CreditorInquiry).filter(
    and_(
        CreditorInquiry.sent_at >= lookback_date,  # CONTEXT.MD: 30-day window
        CreditorInquiry.sent_at <= received_at,
    )
).all()

# CONTEXT.MD decision: No auto-match without recent creditor_inquiries record
if not candidates:
    return {
        "match": None,
        "status": "no_recent_inquiry",
        "message": "No inquiries sent in last 30 days; route to manual review"
    }
```

**Warning signs:** Matches to inquiries from 6+ months ago; same Aktenzeichen matches multiple inquiries

**Source:** CONTEXT.MD decisions, existing v1 implementation

## Code Examples

Verified patterns from existing codebase and official sources:

### Name Matching with RapidFuzz (German Names)
```python
# Source: V1 matching_engine.py + RapidFuzz official docs
from rapidfuzz import fuzz, utils

def _score_client_name(
    self,
    inquiry_name: str,
    inquiry_name_normalized: Optional[str],
    extracted_name: Optional[str]
) -> float:
    """
    Score client name match using multiple RapidFuzz algorithms.

    German name variations:
    - "Mustermann, Max" vs "Max Mustermann" → token_sort_ratio
    - "Müller" vs "Mueller" → handled by normalization
    - "J. Schmidt" vs "Johann Schmidt" → partial_ratio
    """
    if not extracted_name or not inquiry_name:
        return 0.0

    # Use normalized name if available (Phase 4 German processing)
    compare_name = inquiry_name_normalized or inquiry_name

    # RapidFuzz 3.x: explicit preprocessing
    # Source: https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html

    # Algorithm 1: token_sort_ratio (word order insensitive)
    token_sort = fuzz.token_sort_ratio(
        compare_name,
        extracted_name,
        processor=utils.default_process,  # lowercase + punctuation removal
        score_cutoff=50  # Early exit optimization
    ) / 100

    # Algorithm 2: partial_ratio (substring matching for initials)
    partial = fuzz.partial_ratio(
        compare_name,
        extracted_name,
        processor=utils.default_process,
        score_cutoff=50
    ) / 100

    # Algorithm 3: token_set_ratio (handles subset relationships)
    token_set = fuzz.token_set_ratio(
        compare_name,
        extracted_name,
        processor=utils.default_process,
        score_cutoff=50
    ) / 100

    # Take maximum (best algorithm for this pair)
    best_score = max(token_sort, partial, token_set)

    return best_score
```

### Threshold Configuration Table Schema
```sql
-- Migration for matching_thresholds table
CREATE TABLE matching_thresholds (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50) NOT NULL,  -- "default", "bank", "sparkasse", "inkasso", "agency"
    threshold_type VARCHAR(50) NOT NULL,  -- "min_match", "gap_threshold"
    threshold_value NUMERIC(5, 4) NOT NULL CHECK (threshold_value >= 0 AND threshold_value <= 1),
    weight_name VARCHAR(50),  -- "client_name", "reference_number", "creditor", "time"
    weight_value NUMERIC(5, 4) CHECK (weight_value >= 0 AND weight_value <= 1),
    description TEXT,  -- Human-readable note
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_threshold_config UNIQUE (category, threshold_type, weight_name)
);

-- Default configuration (CONTEXT.MD: 40% name, 60% reference suggested)
INSERT INTO matching_thresholds (category, threshold_type, threshold_value, description) VALUES
    ('default', 'min_match', 0.7000, 'Minimum score for any match consideration'),
    ('default', 'gap_threshold', 0.1500, 'Gap between #1 and #2 for auto-match (TBD: calibrate)');

INSERT INTO matching_thresholds (category, weight_name, weight_value, description) VALUES
    ('default', 'client_name', 0.4000, 'V1 weight: 40% client name'),
    ('default', 'reference_number', 0.6000, 'CONTEXT.MD suggested: 60% reference'),
    ('default', 'creditor', 0.0000, 'Filtered by creditor_inquiries table'),
    ('default', 'time', 0.0000, 'Filtered by 30-day window');

-- Creditor category overrides (CONTEXT.MD: Claude's discretion)
-- Example: Banks may have stricter thresholds than Inkasso agencies
INSERT INTO matching_thresholds (category, threshold_type, threshold_value, description) VALUES
    ('bank', 'min_match', 0.8000, 'Banks: higher threshold for precision'),
    ('inkasso', 'min_match', 0.6500, 'Inkasso: lower threshold, more variability in names');

-- Index for fast runtime lookups
CREATE INDEX idx_matching_thresholds_lookup ON matching_thresholds(category, threshold_type);
```

### Manual Review Queue Integration
```python
# Route ambiguous matches to manual review with top-3 candidates
# CONTEXT.MD: Reviewer sees all candidates with scores and signal breakdown

from app.models.manual_review import ManualReviewQueue

def route_to_manual_review(
    db: Session,
    email_id: int,
    match_result: Dict[str, Any],
    candidates: List[MatchResult]
) -> int:
    """
    Create manual review record with top-3 candidates.

    CONTEXT.MD: Show candidates with match scores and signal breakdown.
    Below-threshold candidates not shown (information overload).
    """
    review_queue_entry = ManualReviewQueue(
        incoming_email_id=email_id,
        review_type="ambiguous_match",  # or "no_recent_inquiry", "below_threshold"
        status="pending",
        metadata={
            "match_status": match_result["status"],
            "gap": match_result.get("gap"),
            "gap_threshold": 0.15,  # From ThresholdManager
            "top_candidates": [
                {
                    "inquiry_id": c.inquiry.id,
                    "client_name": c.inquiry.client_name,
                    "creditor_name": c.inquiry.creditor_name,
                    "creditor_email": c.inquiry.creditor_email,
                    "reference_number": c.inquiry.reference_number,
                    "sent_at": c.inquiry.sent_at.isoformat(),
                    "total_score": float(c.total_score),
                    "confidence_level": c.confidence_level,
                    # CONTEXT.MD: Signal breakdown for reviewer
                    "signal_breakdown": {
                        "client_name_score": float(c.component_scores["client_name"]),
                        "reference_score": float(c.component_scores["reference"]),
                        "creditor_score": float(c.component_scores["creditor"]),
                        "time_score": float(c.component_scores["time"])
                    }
                }
                for c in candidates[:3]  # Top 3 only
            ]
        }
    )

    db.add(review_queue_entry)
    db.commit()

    return review_queue_entry.id
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| FuzzyWuzzy library | RapidFuzz 3.x | 2021 | 100x performance improvement; RapidFuzz is maintained, FuzzyWuzzy deprecated |
| Exact reference matching | Fuzzy reference matching with partial_ratio | Phase 6 (new) | Handles OCR errors in scanned documents (0→O, 1→l in Aktenzeichen) |
| Hardcoded thresholds in settings.py | Database table with category overrides | Phase 6 (new) | Runtime threshold tuning without deployment; per-creditor-category configuration |
| Weighted OR logic (any signal above threshold) | Weighted AND logic (both name AND reference required) | Phase 6 (CONTEXT.MD) | Prevents false matches from reused reference numbers |
| Match result only | Match result + explainability JSONB | Phase 6 (REQ-MATCH-03) | Developer debugging, threshold calibration, audit trail for compliance |
| Python logging for decisions | PostgreSQL JSONB with 90-day pruning | Phase 6 (CONTEXT.MD) | Structured queries on match decisions; log files not easily analyzable |
| Manual threshold tuning in code | F1-score optimization with production data | Best practice 2026 | Data-driven threshold selection; balance precision/recall scientifically |

**Deprecated/outdated:**
- **settings.match_threshold_high/medium:** V1 config.py doesn't have these (lines 9-54 in current config.py show no matching thresholds); must be from _existing-code. Replace with database table.
- **Single fuzzy algorithm:** V1 uses max(token_sort, partial, token_set) which is correct; don't revert to single algorithm
- **Component_scores as separate columns:** V1 MatchResult model has individual score columns (client_name_score, etc.); with JSONB explainability, these become redundant. CONTEXT.MD says JSONB only.

## Open Questions

Things that couldn't be fully resolved:

1. **Creditor Category Definitions**
   - What we know: German creditor types include banks (Sparkasse, Volksbank), Inkasso agencies, government agencies, utilities
   - What's unclear: How to automatically categorize creditors (domain-based? manual tagging? NER on creditor name?)
   - Recommendation: Start with manual categorization during creditor_inquiry creation; add `creditor_category` column to creditor_inquiries table. Later phases could add auto-categorization via domain patterns (*.sparkasse.de → "sparkasse", *inkasso* → "inkasso").

2. **Gap Threshold Optimal Value**
   - What we know: CONTEXT.MD says "TBD, calibrate from error analysis"; research suggests F1-score optimization with precision/recall balance
   - What's unclear: Insufficient production data to determine optimal gap_threshold value
   - Recommendation: Shadow mode deployment with gap_threshold=0.15 (conservative), collect 1000+ match decisions with manual review outcomes, plot gap distribution vs reviewer choices, optimize F1 score. Expect optimal value between 0.10-0.20.

3. **Signal Weight Defaults**
   - What we know: V1 uses 40% client, 30% creditor, 20% time, 10% reference. CONTEXT.MD suggests 40% name, 60% reference. creditor_inquiries filtering makes creditor/time signals redundant.
   - What's unclear: Whether to use equal weights (50/50 name/reference) or start with CONTEXT.MD suggestion (40/60)
   - Recommendation: Use 40% name, 60% reference as default; creditor_inquiries filtering already handles creditor/time signals. Make weights configurable per category for tuning.

4. **Explainability JSONB Pruning Strategy**
   - What we know: CONTEXT.MD says 90-day retention to save storage; PostgreSQL TOAST compresses JSONB
   - What's unclear: Whether pruning should DELETE explainability rows or SET scoring_details = NULL
   - Recommendation: SET scoring_details = NULL (preserves match_result record with scores, removes only JSONB payload). Background job: `UPDATE match_results SET scoring_details = NULL WHERE calculated_at < NOW() - INTERVAL '90 days' AND scoring_details IS NOT NULL`.

5. **RapidFuzz WRatio vs Multiple Algorithms**
   - What we know: WRatio() automatically selects best algorithm; V1 uses max(token_sort, partial, token_set)
   - What's unclear: Whether WRatio provides better results or same as manual max()
   - Recommendation: Benchmark WRatio vs V1 approach on 100 test cases. If results similar, use WRatio (less code). If V1 max() approach better, keep it.

## Sources

### Primary (HIGH confidence)
- [RapidFuzz official documentation](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html) - Fuzzy matching algorithms and parameters
- Existing v1 codebase:
  - `app/services/matching_engine.py` - Weighted scoring implementation
  - `_existing-code/app/models/match_result.py` - JSONB scoring_details column
  - `_existing-code/app/models/creditor_inquiry.py` - creditor_inquiries table schema
- CONTEXT.MD - User decisions on signal priority, ambiguity handling, explainability format
- Phase 5 consolidation_agent.py - Consolidation output structure (client_name, creditor_name, gesamtforderung)

### Secondary (MEDIUM confidence)
- [Fuzzy Matching 101: A Complete Guide for 2026](https://matchdatapro.com/fuzzy-matching-101-a-complete-guide-for-2026/) - OCR error handling techniques (20% invoice errors from OCR/typos)
- [PostgreSQL JSONB Performance](https://www.michal-drozd.com/en/blog/postgresql-toast-optimization/) - TOAST strategy and selective retrieval (170x improvement)
- [Data-Driven Rules Engine Pattern](https://medium.com/@jonblankenship/using-the-specification-pattern-to-build-a-data-driven-rules-engine-b3db95189ff8) - Database threshold configuration
- [F1 Score Threshold Tuning](https://gpttutorpro.com/f1-machine-learning-essentials-optimizing-f1-score-with-threshold-tuning/) - Optimal threshold finding via precision/recall balance
- [German Aktenzeichen Format](https://libguides.bodleian.ox.ac.uk/law-german/cases) - Reference number structure (court type, year format)
- [German Debt Collection System](https://germania-inkasso.de/debt-collection-germany/) - Creditor types (banks, Inkasso, Sparkasse, agencies)
- [Audit Trails for AI Compliance 2026](https://lawrence-emenike.medium.com/audit-trails-and-explainability-for-compliance-building-the-transparency-layer-financial-services-d24961bad987) - EU AI Act explainability requirements (August 2026)

### Tertiary (LOW confidence)
- [Weighted Scoring for Multiple Signals](https://productschool.com/blog/product-fundamentals/weighted-scoring-model) - General weighted scoring principles (not matching-specific)
- [AI Lead Scoring 2026](https://content.hubjoy.co/ai-lead-scoring-secrets-intent-signals-hubspot-tips-for-2026) - Multi-signal scoring (fit, engagement, intent) as analogy

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - RapidFuzz 3.6.0 already in requirements.txt, v1 matching_engine.py uses it, PostgreSQL JSONB column exists in MatchResult model
- Architecture: HIGH - V1 weighted scoring pattern proven, database config table standard, JSONB explainability verified in PostgreSQL docs
- Pitfalls: HIGH - RapidFuzz 3.x preprocessing change documented, TOAST performance trap verified, Aktenzeichen format complexity confirmed in German legal docs
- Creditor categories: MEDIUM - German creditor types researched but categorization logic unclear (manual vs auto)
- Gap threshold value: LOW - CONTEXT.MD says TBD; requires production data for calibration

**Research date:** 2026-02-05
**Valid until:** 2026-03-05 (30 days - stable domain: fuzzy matching, PostgreSQL)

**Notes:**
- V1 matching engine provides solid foundation; Phase 6 extends rather than replaces
- RapidFuzz 3.x preprocessing change is critical gotcha (not backward compatible with 2.x)
- CONTEXT.MD decisions locked: both signals required, gap threshold for ambiguity, JSONB explainability, database config
- Threshold calibration requires shadow mode + production data (cannot determine optimal values from research alone)
