"""
Signal Scorer Functions

Provides core matching logic for name and reference number signals.

Design decisions:
- RapidFuzz 3.x requires explicit preprocessing via processor parameter
- Name matching uses multiple algorithms (token_sort, partial, token_set) and returns best
- Reference matching handles OCR errors with fuzzy matching (not just exact)
"""

from typing import Optional
from rapidfuzz import fuzz, utils
import structlog

logger = structlog.get_logger(__name__)


def score_client_name(
    inquiry_name: str,
    inquiry_name_normalized: Optional[str],
    extracted_name: Optional[str]
) -> tuple[float, dict]:
    """
    Score client name match using RapidFuzz with multiple algorithms.

    RapidFuzz 3.x requirement: Must use processor parameter for preprocessing.
    Previous versions auto-lowercased; 3.x does not.

    Args:
        inquiry_name: Original client name from inquiry
        inquiry_name_normalized: Pre-normalized name (optional)
        extracted_name: Client name extracted from creditor answer

    Returns:
        Tuple of (score 0.0-1.0, scoring_details dict)

    Example:
        >>> score, details = score_client_name("Mustermann, Max", "mustermann max", "Max Mustermann")
        >>> score >= 0.9  # Should match well
        True
    """
    if not extracted_name or not inquiry_name:
        return 0.0, {
            "algorithm_used": "none",
            "inquiry_value": inquiry_name,
            "extracted_value": extracted_name,
            "all_scores": {},
            "reason": "missing_input"
        }

    # Use normalized name if available, else use original
    compare_name = inquiry_name_normalized or inquiry_name

    # RapidFuzz 3.x: MUST use processor parameter for preprocessing
    # utils.default_process: lowercase + strip punctuation/whitespace
    scores = {}

    # Algorithm 1: Token sort (handles word order)
    scores["token_sort_ratio"] = fuzz.token_sort_ratio(
        compare_name, extracted_name,
        processor=utils.default_process,
        score_cutoff=50  # Early exit optimization
    ) / 100

    # Algorithm 2: Partial ratio (handles substring matches)
    scores["partial_ratio"] = fuzz.partial_ratio(
        compare_name, extracted_name,
        processor=utils.default_process,
        score_cutoff=50
    ) / 100

    # Algorithm 3: Token set (handles extra/missing tokens)
    scores["token_set_ratio"] = fuzz.token_set_ratio(
        compare_name, extracted_name,
        processor=utils.default_process,
        score_cutoff=50
    ) / 100

    # Return best score across all algorithms
    best_score = max(scores.values())
    best_algorithm = max(scores, key=scores.get)

    # Log detailed matching info for debugging
    logger.debug("client_name_score_details",
                inquiry_name=inquiry_name,
                extracted_name=extracted_name,
                compare_name=compare_name,
                best_score=best_score,
                best_algorithm=best_algorithm,
                all_scores=scores)

    return best_score, {
        "algorithm_used": best_algorithm,
        "inquiry_value": inquiry_name,
        "extracted_value": extracted_name,
        "all_scores": scores
    }


def score_reference_numbers(
    inquiry_reference: Optional[str],
    extracted_references: list[str]
) -> tuple[float, dict]:
    """
    Score reference number match with OCR error handling.

    Strategy:
    1. Exact match after normalization -> 1.0
    2. Fuzzy partial match (handles OCR errors like 1->I, 0->O) -> use partial_ratio
    3. Token-based match (handles word order) -> use token_sort_ratio

    Args:
        inquiry_reference: Reference number from inquiry
        extracted_references: List of reference numbers extracted from creditor answer

    Returns:
        Tuple of (score 0.0-1.0, scoring_details dict)

    Example:
        >>> score, details = score_reference_numbers("AZ-12345", ["AZ-I2345"])  # 1->I OCR error
        >>> score >= 0.8  # Should match with fuzzy
        True
    """
    if not inquiry_reference or not extracted_references:
        return 0.0, {
            "matched_reference": None,
            "algorithm_used": "none",
            "raw_score": 0.0,
            "reason": "missing_input"
        }

    # Normalize inquiry reference (uppercase, strip whitespace)
    normalized_inquiry = inquiry_reference.upper().strip()

    best_score = 0.0
    best_match = None
    best_algorithm = None

    for extracted_ref in extracted_references:
        # Normalize extracted reference
        normalized_extracted = extracted_ref.upper().strip()

        # Strategy 1: Exact match
        if normalized_inquiry == normalized_extracted:
            return 1.0, {
                "matched_reference": extracted_ref,
                "algorithm_used": "exact",
                "raw_score": 1.0
            }

        # Strategy 2: Partial ratio (handles OCR errors and missing prefix/suffix)
        partial_score = fuzz.partial_ratio(
            normalized_inquiry, normalized_extracted,
            score_cutoff=80  # Only consider if reasonably close
        ) / 100

        if partial_score > best_score:
            best_score = partial_score
            best_match = extracted_ref
            best_algorithm = "partial_ratio"

        # Strategy 3: Token sort (handles word order changes)
        token_score = fuzz.token_sort_ratio(
            normalized_inquiry, normalized_extracted,
            score_cutoff=80
        ) / 100

        if token_score > best_score:
            best_score = token_score
            best_match = extracted_ref
            best_algorithm = "token_sort_ratio"

    return best_score, {
        "matched_reference": best_match,
        "algorithm_used": best_algorithm or "none",
        "raw_score": best_score
    }
