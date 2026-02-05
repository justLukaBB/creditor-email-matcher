"""
Explainability Builder

Produces JSONB-ready match explanations for debugging and threshold tuning.

Design decisions from CONTEXT.MD:
- Primary audience: developers (for debugging and threshold tuning)
- Detail level: signal scores only (no verbose reasoning)
- Storage: PostgreSQL JSONB column on match_results
- Retention: 90-day pruning (handled separately)
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models.creditor_inquiry import CreditorInquiry


class ExplainabilityBuilder:
    """
    Build JSONB explainability payloads for match results.

    Produces structured explanations suitable for PostgreSQL JSONB storage.
    Focuses on signal scores and weights for debugging and tuning.
    """

    VERSION = "v2.0"  # Track explainability schema version

    @staticmethod
    def build(
        inquiry: "CreditorInquiry",
        extracted_data: dict,
        component_scores: dict[str, float],
        signal_details: dict[str, dict],
        final_score: float,
        match_status: str,
        gap: Optional[float] = None,
        gap_threshold: float = 0.15,
        weights: Optional[dict[str, float]] = None,
    ) -> dict:
        """
        Build explainability JSONB payload.

        Args:
            inquiry: Matched CreditorInquiry object
            extracted_data: Raw extracted data from creditor answer
            component_scores: Raw signal scores (before weighting)
            signal_details: Detailed scoring info from signal scorers
            final_score: Final weighted score
            match_status: Match outcome (auto_matched, ambiguous, below_threshold, no_candidates)
            gap: Score difference between top 2 candidates (for ambiguity)
            gap_threshold: Threshold for ambiguous matches
            weights: Signal weights used in scoring

        Returns:
            Dict suitable for MatchResult.scoring_details JSONB column

        Example:
            >>> builder = ExplainabilityBuilder()
            >>> payload = builder.build(
            ...     inquiry=inquiry_obj,
            ...     extracted_data={"client_name": "Max Mustermann", "reference_numbers": ["AZ-12345"]},
            ...     component_scores={"client_name": 0.95, "reference": 1.0},
            ...     signal_details={"client_name": {...}, "reference": {...}},
            ...     final_score=0.97,
            ...     match_status="auto_matched",
            ...     weights={"client_name": 0.4, "reference_number": 0.6}
            ... )
            >>> payload["version"]
            'v2.0'
        """
        if weights is None:
            weights = {"client_name": 0.40, "reference_number": 0.60}

        return {
            "version": ExplainabilityBuilder.VERSION,
            "match_status": match_status,  # auto_matched, ambiguous, below_threshold, no_candidates
            "final_score": round(final_score, 4),
            "gap": round(gap, 4) if gap is not None else None,
            "gap_threshold": gap_threshold,
            "signals": {
                "client_name": {
                    "score": round(component_scores.get("client_name", 0), 4),
                    "weighted_score": round(
                        component_scores.get("client_name", 0) * weights.get("client_name", 0.4),
                        4
                    ),
                    "inquiry_value": inquiry.client_name,
                    "extracted_value": extracted_data.get("client_name"),
                    **signal_details.get("client_name", {})
                },
                "reference_number": {
                    "score": round(component_scores.get("reference", 0), 4),
                    "weighted_score": round(
                        component_scores.get("reference", 0) * weights.get("reference_number", 0.6),
                        4
                    ),
                    "inquiry_value": inquiry.reference_number,
                    "extracted_value": extracted_data.get("reference_numbers"),
                    **signal_details.get("reference", {})
                }
            },
            "weights": weights,
            "filters_applied": {
                "creditor_inquiries_window_days": 30,
                "both_signals_required": True  # CONTEXT.MD decision
            },
            "inquiry_id": inquiry.id,
            "inquiry_sent_at": inquiry.sent_at.isoformat() if inquiry.sent_at else None
        }
