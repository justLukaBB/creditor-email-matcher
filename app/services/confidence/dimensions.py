"""
Confidence Dimension Calculators

Separate confidence dimensions for extraction and matching stages that can be
combined into overall confidence for routing decisions.

Document-level extraction confidence (not field-level) based on:
- Source quality baselines (native PDF > DOCX > XLSX > scanned PDF > image)
- Completeness adjustment (missing key fields)

Match confidence based on:
- Matching engine score
- Ambiguity adjustment (reduced confidence when multiple candidates are close)
"""

import structlog
from typing import Optional

logger = structlog.get_logger(__name__)


# Source quality baselines per CONTEXT.md Claude's discretion
SOURCE_QUALITY_BASELINES = {
    "native_pdf": 0.95,  # Text extraction, no OCR needed
    "scanned_pdf": 0.75,  # Claude Vision, OCR variability
    "docx": 0.90,  # Structured text
    "xlsx": 0.85,  # Tabular, may need context
    "image": 0.70,  # Claude Vision, least reliable
    "email_body": 0.80,  # Text but often noisy
}

# Key fields that affect completeness
KEY_FIELDS = ["amount", "client_name", "creditor_name"]

# Completeness penalty per missing key field
COMPLETENESS_PENALTY = 0.1

# Minimum confidence floor
MIN_CONFIDENCE = 0.3

# Ambiguity penalty for match confidence (when status is ambiguous)
AMBIGUITY_PENALTY = 0.3


def calculate_extraction_confidence(
    agent_checkpoints: dict,
    document_types: list[str],
    final_extracted_data: dict = None
) -> float:
    """
    Calculate extraction confidence based on source quality and completeness.

    Document-level confidence (not field-level) using weakest-link approach
    at the source level.

    Args:
        agent_checkpoints: JSONB agent checkpoints containing agent_2_extraction
        document_types: List of processed document types (e.g., ["native_pdf", "email_body"])
        final_extracted_data: Merged extracted data from entity extraction (preferred for completeness check)

    Returns:
        float: Confidence score 0.0-1.0

    Business Rules:
        - Source quality: Use minimum baseline across all sources (weakest-link)
        - Completeness: Reduce by 0.1 per missing key field (amount, client_name, creditor_name)
        - Floor: Never return confidence below 0.3
        - Ceiling: Never return confidence above 1.0
    """
    log = logger.bind(document_types=document_types)

    # Get agent_2_extraction checkpoint
    agent_2 = agent_checkpoints.get("agent_2_extraction", {})
    if not agent_2:
        log.warning("agent_2_extraction checkpoint missing, using floor confidence")
        return MIN_CONFIDENCE

    # Calculate source quality baseline (weakest-link)
    if not document_types:
        log.warning("no document types provided, using floor confidence")
        return MIN_CONFIDENCE

    source_qualities = []
    for doc_type in document_types:
        baseline = SOURCE_QUALITY_BASELINES.get(doc_type)
        if baseline is None:
            log.warning("unknown document type, treating as low quality", doc_type=doc_type)
            baseline = 0.60  # Unknown format gets low baseline
        source_qualities.append(baseline)

    # Weakest-link: minimum source quality
    source_confidence = min(source_qualities)
    log.debug(
        "source quality calculated",
        source_qualities=source_qualities,
        min_quality=source_confidence,
    )

    # Check completeness of key fields
    # Prefer final_extracted_data (from entity extraction) over checkpoint data
    if final_extracted_data:
        extracted_data = final_extracted_data
    else:
        # Fallback: Fields may be at top level or inside "extracted_data"
        extracted_data = agent_2.get("extracted_data", agent_2)

    missing_fields = []
    for field in KEY_FIELDS:
        # Map "amount" to "debt_amount" (final_extracted_data) or "gesamtforderung" (checkpoint)
        if field == "amount":
            check_field = "debt_amount" if final_extracted_data else "gesamtforderung"
        else:
            check_field = field
        value = extracted_data.get(check_field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_fields.append(field)

    # Apply completeness penalty
    completeness_penalty = len(missing_fields) * COMPLETENESS_PENALTY
    confidence = source_confidence - completeness_penalty

    log.debug(
        "completeness adjustment applied",
        missing_fields=missing_fields,
        penalty=completeness_penalty,
        base_confidence=source_confidence,
    )

    # Apply floor and ceiling
    confidence = max(MIN_CONFIDENCE, min(1.0, confidence))

    log.info(
        "extraction_confidence calculated",
        confidence=confidence,
        source_confidence=source_confidence,
        missing_fields=missing_fields,
        document_types=document_types,
    )

    return confidence


def calculate_match_confidence(match_result: Optional[dict]) -> float:
    """
    Calculate match confidence from matching engine result.

    Match confidence is derived from the matching engine's total_score with
    adjustments for ambiguity status.

    Args:
        match_result: MatchResult as dict (status, total_score, candidates, etc.)

    Returns:
        float: Confidence score 0.0-1.0

    Business Rules:
        - No match result or no_candidates/no_recent_inquiry: 0.0
        - auto_matched: total_score directly (already 0.0-1.0)
        - ambiguous: score * (1 - 0.3) to reflect uncertainty
        - below_threshold: total_score directly (already low)
    """
    log = logger.bind(has_match_result=match_result is not None)

    if not match_result:
        log.info("match_confidence: no match result", confidence=0.0)
        return 0.0

    status = match_result.get("status")
    total_score = match_result.get("total_score", 0.0)

    # Handle different matching statuses
    if status in ("no_candidates", "no_recent_inquiry"):
        log.info("match_confidence: no matching candidates", status=status, confidence=0.0)
        return 0.0

    if status == "auto_matched":
        # High confidence match, use score directly
        confidence = total_score
        log.info(
            "match_confidence: auto matched",
            confidence=confidence,
            total_score=total_score,
        )
        return confidence

    if status == "ambiguous":
        # Reduce confidence due to uncertainty (multiple close candidates)
        confidence = total_score * (1.0 - AMBIGUITY_PENALTY)
        log.info(
            "match_confidence: ambiguous match",
            confidence=confidence,
            total_score=total_score,
            penalty=AMBIGUITY_PENALTY,
        )
        return confidence

    if status == "below_threshold":
        # Low score, use directly
        confidence = total_score
        log.info(
            "match_confidence: below threshold",
            confidence=confidence,
            total_score=total_score,
        )
        return confidence

    # Unknown status, use score with warning
    log.warning(
        "match_confidence: unknown status, using total_score",
        status=status,
        confidence=total_score,
    )
    return total_score
