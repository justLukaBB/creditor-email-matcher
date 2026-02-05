"""
Calibration Sample Collector
Captures labeled examples from manual review resolutions for threshold tuning

USER DECISIONS from CONTEXT.md:
- Labels captured implicitly from reviewer corrections
- If reviewer changes data, original was wrong (was_correct=False)
- If reviewer approves without changes, original was correct (was_correct=True)
"""

from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
import structlog

from app.models.calibration_sample import CalibrationSample
from app.models.incoming_email import IncomingEmail
from app.models.manual_review import ManualReviewQueue

logger = structlog.get_logger(__name__)

# Hardcoded thresholds for now (will move to config in routing plan)
CONFIDENCE_HIGH_THRESHOLD = 0.85
CONFIDENCE_LOW_THRESHOLD = 0.60


def _determine_confidence_bucket(confidence: float) -> str:
    """
    Categorize confidence into bucket for analysis.

    Uses same thresholds as router for consistency.
    """
    if confidence >= CONFIDENCE_HIGH_THRESHOLD:
        return "high"
    elif confidence >= CONFIDENCE_LOW_THRESHOLD:
        return "medium"
    else:
        return "low"


def _detect_correction_type(
    original_data: Dict[str, Any],
    corrected_data: Optional[Dict[str, Any]]
) -> tuple[str, Dict[str, Any]]:
    """
    Detect what type of correction was made and capture details.

    Returns:
        (correction_type, correction_details)
    """
    if not corrected_data:
        return ("none", {})

    field_changes = []
    details = {}

    # Check amount correction
    orig_amount = original_data.get("debt_amount")
    corr_amount = corrected_data.get("debt_amount")
    if orig_amount != corr_amount:
        field_changes.append("amount")
        details["original_amount"] = orig_amount
        details["corrected_amount"] = corr_amount

    # Check client name correction
    orig_client = original_data.get("client_name")
    corr_client = corrected_data.get("client_name")
    if orig_client != corr_client:
        field_changes.append("client_name")
        details["original_client"] = orig_client
        details["corrected_client"] = corr_client

    # Check creditor name correction
    orig_creditor = original_data.get("creditor_name")
    corr_creditor = corrected_data.get("creditor_name")
    if orig_creditor != corr_creditor:
        field_changes.append("creditor_name")
        details["original_creditor"] = orig_creditor
        details["corrected_creditor"] = corr_creditor

    # Check match correction (if different inquiry was selected)
    orig_inquiry = original_data.get("matched_inquiry_id")
    corr_inquiry = corrected_data.get("matched_inquiry_id")
    if orig_inquiry != corr_inquiry:
        field_changes.append("match")
        details["original_inquiry_id"] = orig_inquiry
        details["corrected_inquiry_id"] = corr_inquiry

    details["field_changes"] = field_changes

    # Determine correction type
    if len(field_changes) == 0:
        return ("none", details)
    elif len(field_changes) == 1:
        return (f"{field_changes[0]}_corrected", details)
    else:
        return ("multiple", details)


def _extract_document_types(agent_checkpoints: Optional[Dict[str, Any]]) -> str:
    """
    Extract primary document type from agent checkpoints.

    Returns the most significant document type processed.
    """
    if not agent_checkpoints:
        return "unknown"

    extraction_checkpoint = agent_checkpoints.get("agent_2_extraction", {})
    sources = extraction_checkpoint.get("sources_processed", [])

    # Priority: native_pdf > scanned_pdf > docx > xlsx > image > email_body
    priority = ["native_pdf", "scanned_pdf", "docx", "xlsx", "image", "email_body"]

    for doc_type in priority:
        if doc_type in sources or any(doc_type in str(s) for s in sources):
            return doc_type

    return "email_body"  # Default if no attachments


def capture_calibration_sample(
    db: Session,
    review_item: ManualReviewQueue,
    email: IncomingEmail,
    resolution: str,
    corrected_data: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """
    Capture a calibration sample from a manual review resolution.

    USER DECISION: Labels captured implicitly from reviewer corrections.

    Args:
        db: Database session
        review_item: The resolved ManualReviewQueue item
        email: The IncomingEmail being reviewed
        resolution: Resolution type (approved, corrected, rejected, etc.)
        corrected_data: If corrected, the new values

    Returns:
        CalibrationSample.id if created, None if skipped
    """
    # Skip spam/rejected - not useful for calibration
    if resolution in ("spam", "rejected", "escalated"):
        logger.info(
            "calibration_sample_skipped",
            email_id=email.id,
            resolution=resolution,
            reason="resolution_type_not_useful"
        )
        return None

    # Determine if prediction was correct
    # approved without changes = correct
    # corrected = incorrect
    was_correct = resolution == "approved"

    # Get original extracted data
    original_data = email.extracted_data or {}

    # Get confidence values from various sources
    predicted_confidence = original_data.get("confidence", 0.5)

    # Try to get dimension breakdown from pipeline_metadata
    pipeline_meta = original_data.get("pipeline_metadata", {})
    extraction_confidence = None  # Will be calculated in Phase 7 integration
    match_confidence = email.match_confidence / 100.0 if email.match_confidence else None

    # Detect correction type if corrected
    correction_type, correction_details = _detect_correction_type(
        original_data,
        corrected_data
    )

    # Get document type
    document_type = _extract_document_types(email.agent_checkpoints)

    # Determine confidence bucket
    confidence_bucket = _determine_confidence_bucket(predicted_confidence)

    # Create calibration sample
    sample = CalibrationSample(
        email_id=email.id,
        review_id=review_item.id,
        predicted_confidence=predicted_confidence,
        extraction_confidence=extraction_confidence,
        match_confidence=match_confidence,
        document_type=document_type,
        was_correct=was_correct,
        correction_type=correction_type if not was_correct else None,
        correction_details=correction_details if not was_correct else None,
        confidence_bucket=confidence_bucket
    )

    db.add(sample)
    db.flush()

    logger.info(
        "calibration_sample_captured",
        sample_id=sample.id,
        email_id=email.id,
        was_correct=was_correct,
        confidence_bucket=confidence_bucket,
        correction_type=correction_type,
        document_type=document_type
    )

    return sample.id
