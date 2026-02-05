"""
Overall Confidence Calculator
Combines confidence dimensions using weakest-link (min) principle

REQ-CONFIDENCE-02: Overall confidence = min(all_stages)
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import structlog

from app.services.confidence.dimensions import (
    calculate_extraction_confidence,
    calculate_match_confidence
)

logger = structlog.get_logger(__name__)


@dataclass
class OverallConfidence:
    """Result of overall confidence calculation with breakdown"""
    overall: float  # Final confidence score (weakest-link)
    extraction: float  # Extraction dimension
    match: float  # Match dimension
    intent: Optional[float]  # Intent classification (if included)
    dimensions_used: List[str]  # Which dimensions contributed
    weakest_link: str  # Which dimension was the bottleneck


def calculate_overall_confidence(
    agent_checkpoints: Optional[Dict[str, Any]],
    document_types: List[str],
    match_result: Optional[Dict[str, Any]],
    include_intent: bool = False
) -> OverallConfidence:
    """
    Calculate overall confidence using weakest-link principle.

    USER DECISION from CONTEXT.md: overall_confidence = min(all_stages)

    Claude's Discretion: Whether to include intent classification confidence
    - Default: exclude intent (intent classification is binary enough that
      confidence < 0.7 already triggers needs_review in Phase 5)
    - If include_intent=True, factor it in

    Args:
        agent_checkpoints: JSONB from IncomingEmail.agent_checkpoints
        document_types: List of source types processed (native_pdf, scanned_pdf, etc.)
        match_result: Dict from MatchingEngineV2 or None
        include_intent: Whether to factor in intent classification confidence

    Returns:
        OverallConfidence with breakdown of all dimensions
    """
    dimensions = {}
    dimensions_used = []

    # Calculate extraction confidence
    extraction_conf = calculate_extraction_confidence(agent_checkpoints, document_types)
    dimensions["extraction"] = extraction_conf
    dimensions_used.append("extraction")

    # Calculate match confidence
    match_conf = calculate_match_confidence(match_result)
    dimensions["match"] = match_conf
    dimensions_used.append("match")

    # Optionally include intent confidence
    intent_conf = None
    if include_intent and agent_checkpoints:
        intent_checkpoint = agent_checkpoints.get("agent_1_intent", {})
        intent_conf = intent_checkpoint.get("confidence", 0.0)
        if intent_conf > 0:
            dimensions["intent"] = intent_conf
            dimensions_used.append("intent")

    # Weakest-link: overall = min(all dimensions)
    if not dimensions:
        overall = 0.0
        weakest = "none"
    else:
        overall = min(dimensions.values())
        weakest = min(dimensions, key=dimensions.get)

    result = OverallConfidence(
        overall=overall,
        extraction=extraction_conf,
        match=match_conf,
        intent=intent_conf,
        dimensions_used=dimensions_used,
        weakest_link=weakest
    )

    logger.info(
        "overall_confidence_calculated",
        overall=result.overall,
        extraction=result.extraction,
        match=result.match,
        intent=result.intent,
        weakest_link=result.weakest_link
    )

    return result
