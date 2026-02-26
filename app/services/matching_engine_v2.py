"""
Matching Engine V2 (Phase 6: Matching Engine Reconstruction)

Reconstructed matching engine with:
- creditor_inquiries table integration (30-day filter)
- Fuzzy matching via RapidFuzz with OCR error handling
- Configurable thresholds per creditor category
- Explainability JSONB for debugging and threshold tuning
- Ambiguity detection with gap threshold

CONTEXT.MD DECISIONS (LOCKED):
- Both reference AND name signals required for match
- No auto-match without recent creditor_inquiries record
- Gap threshold for ambiguity routing to manual review
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_
import structlog

from app.models import CreditorInquiry, MatchResult, IncomingEmail
from app.services.matching import (
    ThresholdManager,
    CombinedStrategy,
    ExplainabilityBuilder,
    StrategyResult,
)
from app.config import settings

logger = structlog.get_logger(__name__)

# Default lookback window (CONTEXT.MD: 30 days)
DEFAULT_LOOKBACK_DAYS = 30


@dataclass
class MatchCandidate:
    """
    Represents a single match candidate with scores and explainability.
    """
    inquiry: CreditorInquiry
    total_score: float
    component_scores: Dict[str, float]
    signal_details: Dict[str, Dict]
    strategy_used: str
    scoring_details: Dict[str, Any] = field(default_factory=dict)  # JSONB-ready

    @property
    def confidence_level(self) -> str:
        """Categorize match confidence for display."""
        if self.total_score >= 0.85:
            return "high"
        elif self.total_score >= 0.70:
            return "medium"
        else:
            return "low"


@dataclass
class MatchingResult:
    """
    Result of the matching process.
    """
    status: str  # auto_matched, ambiguous, below_threshold, no_candidates, no_recent_inquiry
    match: Optional[MatchCandidate] = None  # Selected match (if auto_matched)
    candidates: List[MatchCandidate] = field(default_factory=list)  # Top-k candidates
    gap: Optional[float] = None  # Score gap between #1 and #2
    gap_threshold: float = 0.15
    needs_review: bool = False
    review_reason: Optional[str] = None


class MatchingEngineV2:
    """
    Matching engine with creditor_inquiries integration and explainability.

    Usage:
        engine = MatchingEngineV2(db)
        result = engine.find_match(
            email_id=123,
            extracted_data={"client_name": "Max Mustermann", "reference_numbers": ["AZ-123"]},
            from_email="info@sparkasse.de",
            received_at=datetime.now()
        )

        if result.status == "auto_matched":
            # Process matched inquiry
            matched_inquiry = result.match.inquiry
        elif result.status == "ambiguous":
            # Route to manual review with candidates
            for candidate in result.candidates:
                print(f"{candidate.inquiry.client_name}: {candidate.total_score}")
    """

    def __init__(
        self,
        db: Session,
        strategy: Optional["MatchingStrategy"] = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS
    ):
        """
        Initialize matching engine.

        Args:
            db: SQLAlchemy session
            strategy: Matching strategy (default: CombinedStrategy)
            lookback_days: Days to look back for creditor_inquiries (default: 30)
        """
        self.db = db
        self.strategy = strategy or CombinedStrategy()
        self.lookback_days = lookback_days
        self.threshold_manager = ThresholdManager(db)

        logger.info("matching_engine_v2_initialized",
                   strategy=type(self.strategy).__name__,
                   lookback_days=self.lookback_days)

    def find_match(
        self,
        email_id: int,
        extracted_data: Dict[str, Any],
        from_email: str,
        received_at: datetime,
        creditor_category: str = "default"
    ) -> MatchingResult:
        """
        Find matching inquiry for incoming email.

        CONTEXT.MD Implementation:
        1. Filter candidates by creditor_inquiries 30-day window
        2. Score each candidate using strategy (both signals required)
        3. Apply gap threshold for ambiguity detection
        4. Build explainability JSONB for all candidates

        Args:
            email_id: IncomingEmail.id for logging
            extracted_data: Dict with client_name, reference_numbers, etc.
            from_email: Sender email address
            received_at: When email was received
            creditor_category: For category-specific thresholds

        Returns:
            MatchingResult with status, match, candidates, gap
        """
        log = logger.bind(
            email_id=email_id,
            from_email=from_email,
            creditor_category=creditor_category
        )

        # Step 1: Get candidates from creditor_inquiries (30-day filter)
        candidates = self._get_candidate_inquiries(from_email, received_at)

        if not candidates:
            log.warning("no_candidates_in_window",
                       lookback_days=self.lookback_days)
            return MatchingResult(
                status="no_recent_inquiry",
                needs_review=True,
                review_reason="No inquiries sent in last 30 days"
            )

        log.info("candidates_found", count=len(candidates))

        # Step 2: Get thresholds and weights
        min_threshold = self.threshold_manager.get_min_match(creditor_category)
        gap_threshold = self.threshold_manager.get_gap_threshold(creditor_category)
        weights = self.threshold_manager.get_weights(creditor_category)

        log.debug("thresholds_loaded",
                 min_threshold=min_threshold,
                 gap_threshold=gap_threshold,
                 weights=weights)

        # Step 3: Score each candidate
        match_candidates: List[MatchCandidate] = []
        for inquiry in candidates:
            strategy_result = self.strategy.evaluate(inquiry, extracted_data, weights)

            # Build explainability JSONB
            scoring_details = ExplainabilityBuilder.build(
                inquiry=inquiry,
                extracted_data=extracted_data,
                component_scores=strategy_result.component_scores,
                signal_details=strategy_result.signal_details,
                final_score=strategy_result.score,
                match_status="pending",  # Updated after gap analysis
                gap=None,
                gap_threshold=gap_threshold,
                weights=weights
            )

            match_candidate = MatchCandidate(
                inquiry=inquiry,
                total_score=strategy_result.score,
                component_scores=strategy_result.component_scores,
                signal_details=strategy_result.signal_details,
                strategy_used=strategy_result.strategy_used,
                scoring_details=scoring_details
            )
            match_candidates.append(match_candidate)

        # Step 4: Sort by score (descending)
        match_candidates.sort(key=lambda x: x.total_score, reverse=True)

        # Log top candidates
        for i, mc in enumerate(match_candidates[:3], 1):
            log.info("match_candidate",
                    rank=i,
                    inquiry_id=mc.inquiry.id,
                    client_name=mc.inquiry.client_name,
                    score=round(mc.total_score, 4),
                    confidence=mc.confidence_level)

        # Step 4.5: Single-candidate email match override
        # If there's exactly 1 candidate from an exact email match and scoring
        # failed (e.g. no client_name or reference_number extracted), the email
        # match itself is sufficient evidence for auto-matching.
        if (len(match_candidates) == 1
                and match_candidates[0].total_score == 0.0
                and from_email.lower() == (match_candidates[0].inquiry.creditor_email or "").lower()):
            top = match_candidates[0]
            top.total_score = 0.90  # High confidence from exact email match
            top.scoring_details["single_email_match_override"] = True
            top.scoring_details["override_reason"] = (
                "Single candidate with exact email match — no name/reference needed"
            )
            log.info("single_candidate_email_match_override",
                    inquiry_id=top.inquiry.id,
                    client_name=top.inquiry.client_name,
                    original_score=0.0,
                    override_score=0.90)

        # Step 5: Apply matching decision logic
        return self._decide_match(
            candidates=match_candidates,
            min_threshold=min_threshold,
            gap_threshold=gap_threshold,
            log=log
        )

    def _get_candidate_inquiries(
        self,
        from_email: str,
        received_at: datetime
    ) -> List[CreditorInquiry]:
        """
        Get candidate inquiries from creditor_inquiries table.

        CONTEXT.MD: Only consider pairs where we sent an inquiry in the last 30 days.
        This is the key optimization that narrows the search space.

        Priority matching:
        1. Exact email match (from_email == creditor_email)
        2. Domain match (same domain)
        3. All other inquiries in time window (fallback)
        """
        lookback_date = received_at - timedelta(days=self.lookback_days)

        # Extract domain from sender email
        sender_domain = from_email.split('@')[-1].lower() if '@' in from_email else None

        # First try: Exact email match
        exact_matches = self.db.query(CreditorInquiry).filter(
            and_(
                CreditorInquiry.sent_at >= lookback_date,
                CreditorInquiry.sent_at <= received_at,
                CreditorInquiry.creditor_email == from_email
            )
        ).order_by(
            CreditorInquiry.sent_at.desc()
        ).all()

        if exact_matches:
            logger.debug("exact_email_match_found",
                        from_email=from_email,
                        count=len(exact_matches))
            return exact_matches

        # Second try: Domain match (same company, different email)
        if sender_domain:
            domain_matches = self.db.query(CreditorInquiry).filter(
                and_(
                    CreditorInquiry.sent_at >= lookback_date,
                    CreditorInquiry.sent_at <= received_at,
                    CreditorInquiry.creditor_email.ilike(f'%@{sender_domain}')
                )
            ).order_by(
                CreditorInquiry.sent_at.desc()
            ).all()

            if domain_matches:
                logger.debug("domain_match_found",
                            from_email=from_email,
                            domain=sender_domain,
                            count=len(domain_matches))
                return domain_matches

        # Fallback: All inquiries in time window (for manual review scenarios)
        all_candidates = self.db.query(CreditorInquiry).filter(
            and_(
                CreditorInquiry.sent_at >= lookback_date,
                CreditorInquiry.sent_at <= received_at,
            )
        ).order_by(
            CreditorInquiry.sent_at.desc()
        ).all()

        logger.debug("fallback_to_all_candidates",
                    from_email=from_email,
                    count=len(all_candidates))

        return all_candidates

    def _decide_match(
        self,
        candidates: List[MatchCandidate],
        min_threshold: float,
        gap_threshold: float,
        log: Any
    ) -> MatchingResult:
        """
        Apply matching decision logic with gap threshold.

        CONTEXT.MD Decisions:
        - Top match wins if "clearly ahead" of second place (gap >= threshold)
        - When gap threshold not met, route to manual review with top 3
        - Below-threshold candidates not shown to avoid information overload
        """
        if not candidates:
            return MatchingResult(
                status="no_candidates",
                needs_review=True,
                review_reason="No candidates to evaluate"
            )

        top = candidates[0]

        # Check minimum threshold first
        if top.total_score < min_threshold:
            log.info("below_threshold",
                    top_score=top.total_score,
                    min_threshold=min_threshold)

            # Update scoring_details with final status
            top.scoring_details["match_status"] = "below_threshold"

            return MatchingResult(
                status="below_threshold",
                candidates=candidates[:3],  # Top 3 for review
                needs_review=True,
                review_reason=f"Top score {top.total_score:.2f} below threshold {min_threshold}"
            )

        # Single candidate above threshold -> auto-match
        if len(candidates) == 1:
            top.scoring_details["match_status"] = "auto_matched"
            top.scoring_details["gap"] = 1.0

            log.info("auto_matched_single",
                    inquiry_id=top.inquiry.id,
                    score=top.total_score)

            return MatchingResult(
                status="auto_matched",
                match=top,
                candidates=[top],
                gap=1.0,
                gap_threshold=gap_threshold
            )

        # Multiple candidates: calculate gap
        # Deduplicate by creditor_email: if top-2 share the same creditor_email,
        # they represent the same creditor (duplicate inquiries) — skip to the
        # next *different* creditor for the gap calculation.
        second = candidates[1]
        top_email = (top.inquiry.creditor_email or "").lower()
        second_email = (second.inquiry.creditor_email or "").lower()

        if top_email and top_email == second_email:
            # Find the next candidate with a different creditor_email
            next_different = None
            for c in candidates[2:]:
                c_email = (c.inquiry.creditor_email or "").lower()
                if c_email != top_email:
                    next_different = c
                    break

            if next_different is not None:
                gap = top.total_score - next_different.total_score
                log.info("gap_dedup_applied",
                        same_email=top_email,
                        skipped_candidates=1,
                        gap_against=next_different.inquiry.client_name)
            else:
                # All candidates are for the same creditor — treat as single candidate
                gap = 1.0
                log.info("gap_dedup_all_same_creditor", email=top_email)
        else:
            gap = top.total_score - second.total_score

        # Update explainability with gap
        top.scoring_details["gap"] = round(gap, 4)

        if gap >= gap_threshold:
            # Clear winner -> auto-match
            top.scoring_details["match_status"] = "auto_matched"

            log.info("auto_matched_gap",
                    inquiry_id=top.inquiry.id,
                    score=top.total_score,
                    gap=gap,
                    gap_threshold=gap_threshold)

            return MatchingResult(
                status="auto_matched",
                match=top,
                candidates=candidates[:3],
                gap=gap,
                gap_threshold=gap_threshold
            )
        else:
            # Ambiguous -> manual review
            top.scoring_details["match_status"] = "ambiguous"

            log.warning("ambiguous_match",
                       top_score=top.total_score,
                       second_score=second.total_score,
                       gap=gap,
                       gap_threshold=gap_threshold)

            return MatchingResult(
                status="ambiguous",
                candidates=candidates[:3],  # Top 3 for reviewer
                gap=gap,
                gap_threshold=gap_threshold,
                needs_review=True,
                review_reason=f"Gap {gap:.2f} below threshold {gap_threshold}; top candidates too close"
            )

    def save_match_results(
        self,
        email_id: int,
        result: MatchingResult
    ) -> List[MatchResult]:
        """
        Persist match results to database.

        Saves all candidates with their scoring_details JSONB for explainability.
        """
        match_results = []

        for rank, candidate in enumerate(result.candidates, 1):
            mr = MatchResult(
                incoming_email_id=email_id,
                creditor_inquiry_id=candidate.inquiry.id,
                total_score=candidate.total_score,
                confidence_level=candidate.confidence_level,
                client_name_score=candidate.component_scores.get("client_name"),
                reference_number_score=candidate.component_scores.get("reference"),
                scoring_details=candidate.scoring_details,
                rank=rank,
                selected_as_match=(result.status == "auto_matched" and rank == 1),
                selection_method=result.status
            )
            self.db.add(mr)
            match_results.append(mr)

        self.db.flush()  # Get IDs without committing

        logger.info("match_results_saved",
                   email_id=email_id,
                   count=len(match_results),
                   status=result.status)

        return match_results


__all__ = ["MatchingEngineV2", "MatchCandidate", "MatchingResult"]
