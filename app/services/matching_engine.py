"""
Matching Engine Service
Matches incoming emails to creditor inquiries using fuzzy logic and weighted scoring
"""

from typing import List, Dict, Optional
from datetime import datetime, timedelta
from rapidfuzz import fuzz
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
import logging

from app.models import CreditorInquiry
from app.services.entity_extractor import ExtractedEntities
from app.config import settings

logger = logging.getLogger(__name__)


class MatchResult:
    """Represents a single match result with scoring details"""

    def __init__(
        self,
        inquiry: CreditorInquiry,
        total_score: float,
        component_scores: Dict[str, float],
        scoring_details: Dict
    ):
        self.inquiry = inquiry
        self.total_score = total_score
        self.component_scores = component_scores
        self.scoring_details = scoring_details

    @property
    def confidence_level(self) -> str:
        """Categorize match confidence"""
        if self.total_score >= settings.match_threshold_high:
            return "high"
        elif self.total_score >= settings.match_threshold_medium:
            return "medium"
        else:
            return "low"

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage/serialization"""
        return {
            "inquiry_id": self.inquiry.id,
            "total_score": float(self.total_score),
            "confidence_level": self.confidence_level,
            "component_scores": self.component_scores,
            "scoring_details": self.scoring_details
        }


class MatchingEngine:
    """
    Matches incoming emails to creditor inquiries using multiple signals:
    - Client name fuzzy matching (40% weight)
    - Creditor email/name matching (30% weight)
    - Time relevance (20% weight)
    - Reference number matching (10% weight bonus)
    """

    # Scoring weights
    WEIGHT_CLIENT_NAME = 0.40
    WEIGHT_CREDITOR = 0.30
    WEIGHT_TIME = 0.20
    WEIGHT_REFERENCE = 0.10

    def __init__(self, db: Session):
        self.db = db

    def find_matches(
        self,
        extracted_data: ExtractedEntities,
        from_email: str,
        received_at: datetime
    ) -> List[MatchResult]:
        """
        Find matching inquiries for an incoming email

        Args:
            extracted_data: Entities extracted from the email
            from_email: Sender's email address
            received_at: When the email was received

        Returns:
            List of MatchResult objects, sorted by score (best first)
        """
        # Get candidate inquiries
        candidates = self._get_candidate_inquiries(from_email, received_at)

        if not candidates:
            logger.warning("No candidate inquiries found")
            return []

        logger.info(f"Found {len(candidates)} candidate inquiries")

        # Score each candidate
        match_results = []
        for inquiry in candidates:
            match_result = self._score_inquiry(inquiry, extracted_data, from_email, received_at)
            match_results.append(match_result)

        # Sort by score (descending)
        match_results.sort(key=lambda x: x.total_score, reverse=True)

        # Log top matches
        for i, match in enumerate(match_results[:3], 1):
            logger.info(
                f"Match #{i}: Inquiry {match.inquiry.id} - "
                f"Score: {match.total_score:.4f} ({match.confidence_level})"
            )

        return match_results

    def _get_candidate_inquiries(
        self,
        from_email: str,
        received_at: datetime
    ) -> List[CreditorInquiry]:
        """
        Get candidate inquiries to match against

        Strategy:
        1. Filter by time window (last N days)
        2. Prefer inquiries from the same creditor email
        3. Include unreplied inquiries
        """
        # Calculate time window
        lookback_date = received_at - timedelta(days=settings.match_lookback_days)

        # Query inquiries
        query = self.db.query(CreditorInquiry).filter(
            and_(
                CreditorInquiry.sent_at >= lookback_date,
                CreditorInquiry.sent_at <= received_at,
            )
        )

        # Prioritize creditor email match but don't exclude others
        # (creditor might reply from a different email)
        candidates = query.order_by(
            CreditorInquiry.sent_at.desc()
        ).all()

        return candidates

    def _score_inquiry(
        self,
        inquiry: CreditorInquiry,
        extracted: ExtractedEntities,
        from_email: str,
        received_at: datetime
    ) -> MatchResult:
        """
        Score a single inquiry against the extracted data

        Returns:
            MatchResult with total score and component scores
        """
        component_scores = {}
        scoring_details = {}

        # 1. Client Name Score (40% weight)
        client_score = self._score_client_name(
            inquiry.client_name,
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

        # 2. Creditor Score (30% weight)
        creditor_score = self._score_creditor(
            inquiry.creditor_email,
            inquiry.creditor_name,
            inquiry.creditor_name_normalized,
            from_email,
            extracted.creditor_name
        )
        component_scores["creditor"] = creditor_score * self.WEIGHT_CREDITOR
        scoring_details["creditor"] = {
            "inquiry_email": inquiry.creditor_email,
            "from_email": from_email,
            "inquiry_name": inquiry.creditor_name,
            "extracted_name": extracted.creditor_name,
            "score": creditor_score,
            "weight": self.WEIGHT_CREDITOR
        }

        # 3. Time Relevance Score (20% weight)
        time_score = self._score_time_relevance(inquiry.sent_at, received_at)
        component_scores["time"] = time_score * self.WEIGHT_TIME
        scoring_details["time"] = {
            "inquiry_sent": inquiry.sent_at.isoformat(),
            "email_received": received_at.isoformat(),
            "days_elapsed": (received_at - inquiry.sent_at).days,
            "score": time_score,
            "weight": self.WEIGHT_TIME
        }

        # 4. Reference Number Bonus (10% weight)
        ref_score = self._score_reference_numbers(
            inquiry.reference_number,
            extracted.reference_numbers
        )
        component_scores["reference"] = ref_score * self.WEIGHT_REFERENCE
        scoring_details["reference"] = {
            "inquiry_reference": inquiry.reference_number,
            "extracted_references": extracted.reference_numbers,
            "match": ref_score > 0,
            "weight": self.WEIGHT_REFERENCE
        }

        # Calculate total score
        total_score = sum(component_scores.values())

        return MatchResult(
            inquiry=inquiry,
            total_score=total_score,
            component_scores=component_scores,
            scoring_details=scoring_details
        )

    def _score_client_name(
        self,
        inquiry_name: str,
        inquiry_name_normalized: Optional[str],
        extracted_name: Optional[str]
    ) -> float:
        """
        Score client name match using fuzzy matching

        Returns:
            Score from 0.0 to 1.0
        """
        if not extracted_name or not inquiry_name:
            return 0.0

        # Use normalized name if available
        compare_name = inquiry_name_normalized or inquiry_name

        # Try multiple fuzzy matching algorithms and take the best score
        token_sort_ratio = fuzz.token_sort_ratio(compare_name.lower(), extracted_name.lower()) / 100
        partial_ratio = fuzz.partial_ratio(compare_name.lower(), extracted_name.lower()) / 100
        token_set_ratio = fuzz.token_set_ratio(compare_name.lower(), extracted_name.lower()) / 100

        # Take the maximum score
        best_score = max(token_sort_ratio, partial_ratio, token_set_ratio)

        return best_score

    def _score_creditor(
        self,
        inquiry_email: str,
        inquiry_name: Optional[str],
        inquiry_name_normalized: Optional[str],
        from_email: str,
        extracted_name: Optional[str]
    ) -> float:
        """
        Score creditor match based on email and name

        Returns:
            Score from 0.0 to 1.0
        """
        # Email domain match is strongest signal
        inquiry_domain = inquiry_email.split('@')[-1].lower()
        from_domain = from_email.split('@')[-1].lower()

        if inquiry_domain == from_domain:
            return 1.0  # Exact domain match

        # Check if domains are similar (e.g., sparkasse-bochum.de vs sparkasse.de)
        domain_similarity = fuzz.partial_ratio(inquiry_domain, from_domain) / 100
        if domain_similarity > 0.8:
            return 0.9

        # Fall back to name matching if provided
        if extracted_name and inquiry_name:
            compare_name = inquiry_name_normalized or inquiry_name
            name_score = fuzz.token_sort_ratio(compare_name.lower(), extracted_name.lower()) / 100
            return name_score * 0.7  # Reduce confidence since email didn't match

        # No good match
        return 0.3  # Small baseline score

    def _score_time_relevance(self, sent_at: datetime, received_at: datetime) -> float:
        """
        Score based on time elapsed since inquiry was sent

        Fresh inquiries (0-7 days): 1.0
        Medium (8-30 days): 0.7-0.9
        Older (31-60 days): 0.4-0.6

        Returns:
            Score from 0.0 to 1.0
        """
        days_elapsed = (received_at - sent_at).days

        if days_elapsed < 0:
            # Email received before inquiry was sent - impossible
            return 0.0
        elif days_elapsed <= 7:
            return 1.0
        elif days_elapsed <= 14:
            return 0.9
        elif days_elapsed <= 30:
            return 0.7
        elif days_elapsed <= 60:
            return 0.5
        else:
            return 0.2  # Very old, but still possible

    def _score_reference_numbers(
        self,
        inquiry_reference: Optional[str],
        extracted_references: List[str]
    ) -> float:
        """
        Score reference number match

        Returns:
            1.0 if match found, 0.0 otherwise
        """
        if not inquiry_reference or not extracted_references:
            return 0.0

        # Check for exact or partial match
        for ref in extracted_references:
            if inquiry_reference.lower() in ref.lower() or ref.lower() in inquiry_reference.lower():
                return 1.0

        return 0.0


def normalize_name(name: str) -> str:
    """
    Normalize a name for better matching

    Examples:
        "Mustermann, Max" -> "mustermann max"
        "Max Mustermann" -> "mustermann max"
    """
    # Remove punctuation and extra whitespace
    normalized = name.lower()
    normalized = normalized.replace(',', ' ')
    normalized = ' '.join(normalized.split())
    return normalized
