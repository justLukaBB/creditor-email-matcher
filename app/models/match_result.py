"""
MatchResult Model
Stores matching attempts and scores for incoming emails
"""

from sqlalchemy import Column, Integer, String, DateTime, Numeric, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.database import Base


class MatchResult(Base):
    """
    Represents a matching attempt between an incoming email and creditor inquiries

    Stores detailed scoring information for transparency and debugging.
    Each incoming email can have multiple match results (one per candidate inquiry).
    """
    __tablename__ = "match_results"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Foreign Keys
    incoming_email_id = Column(Integer, ForeignKey("incoming_emails.id"), nullable=False, index=True)
    creditor_inquiry_id = Column(Integer, ForeignKey("creditor_inquiries.id"), nullable=False, index=True)

    # Overall Matching Score
    total_score = Column(Numeric(5, 4), nullable=False)  # 0.0000 to 1.0000
    confidence_level = Column(String(20), nullable=False)  # high, medium, low

    # Component Scores (for transparency - backward compatibility)
    client_name_score = Column(Numeric(5, 4), nullable=True)
    creditor_score = Column(Numeric(5, 4), nullable=True)
    time_relevance_score = Column(Numeric(5, 4), nullable=True)
    reference_number_score = Column(Numeric(5, 4), nullable=True)
    debt_amount_score = Column(Numeric(5, 4), nullable=True)

    # Scoring Details (JSONB for structured queries and debugging)
    scoring_details = Column(JSONB, nullable=True)
    """
    Example scoring_details structure:
    {
        "client_name_match": {
            "extracted": "Max Mustermann",
            "inquiry": "Mustermann, Max",
            "fuzzy_ratio": 0.95,
            "weight": 0.40
        },
        "creditor_match": {
            "extracted_email": "info@sparkasse-bochum.de",
            "inquiry_email": "info@sparkasse-bochum.de",
            "email_exact_match": true,
            "weight": 0.30
        },
        "time_relevance": {
            "inquiry_sent": "2024-01-01T10:00:00Z",
            "email_received": "2024-01-03T14:30:00Z",
            "days_elapsed": 2.2,
            "score": 0.95,
            "weight": 0.20
        },
        "reference_numbers": {
            "found": ["AZ-123"],
            "matched": true,
            "bonus": 0.10
        }
    }
    """

    # Ambiguity Gap (for threshold calibration)
    ambiguity_gap = Column(Numeric(5, 4), nullable=True)  # Difference between top match and second best

    # Ranking
    rank = Column(Integer, nullable=True)  # Rank among all candidates for this email (1 = best match)

    # Decision
    selected_as_match = Column(Boolean, default=False)
    selection_method = Column(String(50), nullable=True)  # auto, manual, review

    # Timestamps
    calculated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<MatchResult(id={self.id}, email_id={self.incoming_email_id}, score={self.total_score})>"
