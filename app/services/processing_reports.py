"""
Processing Report Service
Generates and queries per-email processing reports for operational visibility
"""

from datetime import date
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.processing_report import ProcessingReport


def create_processing_report(
    db: Session,
    email_id: int,
    extracted_data: dict,
    agent_checkpoints: dict,
    overall_confidence: float,
    confidence_route: str,
    needs_review: bool,
    review_reason: Optional[str] = None,
    processing_time_ms: Optional[int] = None
) -> ProcessingReport:
    """
    Create processing report from extraction results.

    Builds detailed per-email report with:
    - extracted_fields: what was extracted with per-field confidence
    - missing_fields: what required fields are missing
    - pipeline metadata: intent, sources, tokens

    Args:
        db: Database session
        email_id: Email ID
        extracted_data: Extraction results dict
        agent_checkpoints: Multi-agent pipeline checkpoints
        overall_confidence: Overall confidence score (0.0-1.0)
        confidence_route: Routing decision (high, medium, low)
        needs_review: Whether routed to manual review
        review_reason: Optional reason for review
        processing_time_ms: Optional processing time in milliseconds

    Returns:
        ProcessingReport instance (flushed but not committed)
    """
    # Build extracted_fields with per-field confidence
    extracted_fields = {}
    key_fields = ["client_name", "creditor_name", "debt_amount", "reference_numbers"]

    for field in key_fields:
        value = extracted_data.get(field)
        if value and value != "" and value != []:
            # Get confidence from agent checkpoints or default
            field_confidence = _get_field_confidence(field, agent_checkpoints)

            # Get source from extraction metadata
            source = _get_field_source(field, extracted_data)

            extracted_fields[field] = {
                "value": value,
                "confidence": field_confidence,
                "source": source
            }

    # Build missing_fields list
    missing_fields = []
    for field in key_fields:
        if field not in extracted_fields:
            missing_fields.append(field)

    # Get pipeline metadata from agent checkpoints
    intent = None
    sources_processed = 1
    total_tokens_used = 0

    if agent_checkpoints:
        # Intent from Agent 1
        agent_1 = agent_checkpoints.get("agent_1_intent", {})
        if isinstance(agent_1, dict):
            intent = agent_1.get("intent")

        # Extraction metadata from Agent 2
        agent_2 = agent_checkpoints.get("agent_2_extraction", {})
        if isinstance(agent_2, dict):
            extraction_meta = agent_2.get("extraction_metadata", {})
            if isinstance(extraction_meta, dict):
                sources_processed = extraction_meta.get("sources_processed", 1)
                total_tokens_used = extraction_meta.get("total_tokens_used", 0)

    # Check if report already exists (upsert pattern)
    existing = db.query(ProcessingReport).filter(
        ProcessingReport.email_id == email_id
    ).first()

    if existing:
        # Update existing report
        existing.extracted_fields = extracted_fields
        existing.missing_fields = missing_fields if missing_fields else None
        existing.overall_confidence = overall_confidence
        existing.confidence_route = confidence_route
        existing.needs_review = needs_review
        existing.review_reason = review_reason
        existing.intent = intent
        existing.sources_processed = sources_processed
        existing.total_tokens_used = total_tokens_used
        existing.processing_time_ms = processing_time_ms
        db.flush()
        return existing
    else:
        # Create new report
        report = ProcessingReport(
            email_id=email_id,
            extracted_fields=extracted_fields,
            missing_fields=missing_fields if missing_fields else None,
            overall_confidence=overall_confidence,
            confidence_route=confidence_route,
            needs_review=needs_review,
            review_reason=review_reason,
            intent=intent,
            sources_processed=sources_processed,
            total_tokens_used=total_tokens_used,
            processing_time_ms=processing_time_ms
        )
        db.add(report)
        db.flush()
        return report


def get_processing_report(db: Session, email_id: int) -> Optional[ProcessingReport]:
    """
    Get processing report by email ID.

    Args:
        db: Database session
        email_id: Email ID

    Returns:
        ProcessingReport or None if not found
    """
    return db.query(ProcessingReport).filter(
        ProcessingReport.email_id == email_id
    ).first()


def get_reports_by_date_range(
    db: Session,
    start_date: date,
    end_date: date
) -> List[ProcessingReport]:
    """
    Get processing reports by created date range.

    Args:
        db: Database session
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        List of ProcessingReports ordered by created_at desc
    """
    return db.query(ProcessingReport).filter(
        and_(
            ProcessingReport.created_at >= start_date,
            ProcessingReport.created_at < end_date
        )
    ).order_by(ProcessingReport.created_at.desc()).all()


def get_reports_needing_review(db: Session, limit: int = 100) -> List[ProcessingReport]:
    """
    Get reports that need manual review.

    Args:
        db: Database session
        limit: Maximum number of reports to return

    Returns:
        List of ProcessingReports needing review, ordered by created_at desc
    """
    return db.query(ProcessingReport).filter(
        ProcessingReport.needs_review == True
    ).order_by(ProcessingReport.created_at.desc()).limit(limit).all()


def _get_field_confidence(field: str, agent_checkpoints: dict) -> float:
    """
    Extract per-field confidence from agent checkpoints.

    Args:
        field: Field name
        agent_checkpoints: Multi-agent pipeline checkpoints

    Returns:
        Confidence score or 0.5 default
    """
    if not agent_checkpoints:
        return 0.5

    # Check Agent 3 consolidation for per-field confidence
    agent_3 = agent_checkpoints.get("agent_3_consolidation", {})
    if isinstance(agent_3, dict):
        confidence_scores = agent_3.get("confidence_scores", {})
        if isinstance(confidence_scores, dict) and field in confidence_scores:
            return confidence_scores[field]

    # Default to 0.5 if not found
    return 0.5


def _get_field_source(field: str, extracted_data: dict) -> str:
    """
    Extract source information for a field.

    Args:
        field: Field name
        extracted_data: Extraction results

    Returns:
        Source string (e.g., "email_body", "pdf", "scanned_pdf")
    """
    extraction_metadata = extracted_data.get("extraction_metadata", {})
    if isinstance(extraction_metadata, dict):
        # Look for primary_source or extraction_method
        primary_source = extraction_metadata.get("primary_source")
        if primary_source:
            return primary_source

        extraction_method = extraction_metadata.get("extraction_method")
        if extraction_method:
            return extraction_method

    return "unknown"
