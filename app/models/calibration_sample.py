"""
CalibrationSample Model
Stores labeled examples from reviewer corrections for confidence threshold calibration
"""

from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.database import Base


class CalibrationSample(Base):
    """
    Represents a calibration sample captured from manual review resolution.

    Labels are captured implicitly:
    - If reviewer changes data, original extraction was WRONG (label=False)
    - If reviewer approves without changes, original extraction was CORRECT (label=True)

    Used for:
    - Threshold auto-adjustment (accumulate 500+ samples per category)
    - Performance monitoring (track accuracy over time)
    - Document type analysis (native PDF vs scanned vs image)
    """
    __tablename__ = "calibration_samples"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Reference to source
    email_id = Column(Integer, ForeignKey("incoming_emails.id"), nullable=False, index=True)
    review_id = Column(Integer, ForeignKey("manual_review_queue.id"), nullable=True, index=True)

    # What we predicted
    predicted_confidence = Column(Float, nullable=False)  # Overall confidence at processing time
    extraction_confidence = Column(Float, nullable=True)  # Extraction dimension
    match_confidence = Column(Float, nullable=True)  # Match dimension
    document_type = Column(String(50), nullable=True)  # native_pdf, scanned_pdf, image, etc.

    # Ground truth label
    was_correct = Column(Boolean, nullable=False)  # True if approved without changes

    # What was changed (for analysis)
    correction_type = Column(String(50), nullable=True)
    # Types: amount_corrected, name_corrected, creditor_corrected, match_corrected, multiple

    correction_details = Column(JSONB, nullable=True)
    """
    Details of what was corrected for analysis.

    Structure:
    {
        "original_amount": 1500.0,
        "corrected_amount": 1550.0,
        "original_client": "Muller Max",
        "corrected_client": "Mueller Max",
        "field_changes": ["amount", "client_name"]
    }
    """

    # Categorization for threshold tuning
    confidence_bucket = Column(String(20), nullable=False)
    # Buckets: high (>0.85), medium (0.6-0.85), low (<0.6)

    # Timestamps
    captured_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<CalibrationSample(id={self.id}, email_id={self.email_id}, was_correct={self.was_correct}, bucket='{self.confidence_bucket}')>"
