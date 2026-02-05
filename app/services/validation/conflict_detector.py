"""
Conflict Detector (Phase 5: Multi-Agent Pipeline Validation)

Detects conflicts between newly extracted data and existing database records.
Implements majority voting for resolving conflicts across multiple extraction sources.

Conflict Rules (USER DECISIONS):
1. Amount conflict: >10% difference threshold
2. Name conflict: different client/creditor names entirely
3. Majority voting: winner is value with most occurrences, confidence based on voting strength
"""

from typing import List, Dict, Any, Optional, Tuple
import structlog

logger = structlog.get_logger(__name__)


def detect_database_conflicts(
    extracted_data: Dict[str, Any],
    existing_data: Optional[Dict[str, Any]],
    amount_threshold: float = 0.10
) -> List[Dict[str, Any]]:
    """
    Detect conflicts between extracted data and existing database records.

    USER DECISION: >10% difference = conflict flag, not blocking.
    Conflicts flag items for manual review but don't stop the pipeline.

    Args:
        extracted_data: Newly extracted data from Agent 2
                       Expected keys: gesamtforderung, client_name, creditor_name
        existing_data: Existing data from MongoDB client record
                      Expected keys: debt_amount, claim_amount, client_name, creditor_name
        amount_threshold: Percentage threshold for amount conflict (default 0.10 = 10%)

    Returns:
        List of conflict dictionaries:
        [
            {
                "field": "gesamtforderung",
                "extracted_value": 1500.0,
                "existing_value": 1000.0,
                "difference_percent": 50.0,
                "reason": "Amount differs by more than 10%"
            }
        ]

    Example:
        >>> extracted = {"gesamtforderung": 1500, "client_name": "Max Mustermann"}
        >>> existing = {"debt_amount": 1000, "client_name": "Max Mustermann"}
        >>> conflicts = detect_database_conflicts(extracted, existing)
        >>> len(conflicts)
        1
        >>> conflicts[0]["field"]
        'gesamtforderung'
    """
    conflicts = []

    # If no existing data, no conflicts possible
    if not existing_data:
        logger.info(
            "no_existing_data",
            reason="no_conflicts_possible"
        )
        return conflicts

    log = logger.bind(
        extracted_amount=extracted_data.get("gesamtforderung"),
        existing_amount=existing_data.get("debt_amount") or existing_data.get("claim_amount")
    )

    # Check amount conflict
    extracted_amount = extracted_data.get("gesamtforderung")
    existing_amount = existing_data.get("debt_amount") or existing_data.get("claim_amount")

    if extracted_amount is not None and existing_amount is not None:
        # Calculate percentage difference
        if existing_amount > 0:
            diff_percent = abs(extracted_amount - existing_amount) / existing_amount

            if diff_percent > amount_threshold:
                conflict = {
                    "field": "gesamtforderung",
                    "extracted_value": extracted_amount,
                    "existing_value": existing_amount,
                    "difference_percent": round(diff_percent * 100, 2),
                    "reason": f"Amount differs by more than {int(amount_threshold * 100)}%"
                }
                conflicts.append(conflict)

                log.warning(
                    "amount_conflict_detected",
                    difference_percent=conflict["difference_percent"],
                    threshold_percent=int(amount_threshold * 100)
                )

    # Check client name conflict
    extracted_client = extracted_data.get("client_name")
    existing_client = existing_data.get("client_name")

    if extracted_client and existing_client:
        # Simple comparison: different names entirely (case-insensitive)
        if extracted_client.lower().strip() != existing_client.lower().strip():
            conflict = {
                "field": "client_name",
                "extracted_value": extracted_client,
                "existing_value": existing_client,
                "difference_percent": None,
                "reason": "Client names do not match"
            }
            conflicts.append(conflict)

            log.warning(
                "client_name_conflict_detected",
                extracted=extracted_client,
                existing=existing_client
            )

    # Check creditor name conflict
    extracted_creditor = extracted_data.get("creditor_name")
    existing_creditor = existing_data.get("creditor_name")

    if extracted_creditor and existing_creditor:
        if extracted_creditor.lower().strip() != existing_creditor.lower().strip():
            conflict = {
                "field": "creditor_name",
                "extracted_value": extracted_creditor,
                "existing_value": existing_creditor,
                "difference_percent": None,
                "reason": "Creditor names do not match"
            }
            conflicts.append(conflict)

            log.warning(
                "creditor_name_conflict_detected",
                extracted=extracted_creditor,
                existing=existing_creditor
            )

    log.info(
        "conflict_detection_complete",
        total_conflicts=len(conflicts),
        conflict_fields=[c["field"] for c in conflicts]
    )

    return conflicts


def resolve_conflict_by_majority(amounts: List[float]) -> Tuple[float, float]:
    """
    Resolve conflict using majority voting.

    Winner is the value with most occurrences.
    Confidence based on voting strength: majority_count / total_count.

    Args:
        amounts: List of amounts from different sources

    Returns:
        Tuple of (winning_amount, confidence)
        - winning_amount: The amount that appeared most frequently
        - confidence: Float from 0.0 to 1.0 based on voting strength

    Example:
        >>> resolve_conflict_by_majority([1500.0, 1500.0, 1200.0])
        (1500.0, 0.67)

        >>> resolve_conflict_by_majority([1000.0, 1000.0, 1000.0])
        (1000.0, 1.0)

        >>> resolve_conflict_by_majority([1000.0])
        (1000.0, 1.0)
    """
    if not amounts:
        logger.warning("no_amounts_to_resolve")
        return (0.0, 0.0)

    # Count occurrences (round to 2 decimal places for comparison)
    from collections import Counter
    rounded_amounts = [round(amt, 2) for amt in amounts]
    counter = Counter(rounded_amounts)

    # Find most common
    winner, winner_count = counter.most_common(1)[0]
    total_count = len(amounts)

    # Calculate confidence
    confidence = round(winner_count / total_count, 2)

    logger.info(
        "majority_voting_complete",
        winner=winner,
        winner_count=winner_count,
        total_count=total_count,
        confidence=confidence,
        all_amounts=amounts
    )

    return (winner, confidence)


__all__ = ["detect_database_conflicts", "resolve_conflict_by_majority"]
