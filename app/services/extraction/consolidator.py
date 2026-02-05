"""
Extraction Consolidator (Phase 3: Multi-Format Document Extraction)

Merges extraction results from all sources (email body, PDFs, DOCX, XLSX, images)
into a single ConsolidatedExtractionResult using business rules.

Business Rules (USER DECISIONS - LOCKED):
1. Email body + attachments: highest amount wins
2. No amount found anywhere: default to 100 EUR
3. Confidence: weakest link across all sources
"""

from typing import List, Optional

import structlog

from app.models.extraction_result import (
    SourceExtractionResult,
    ExtractedAmount,
    ExtractedEntity,
    ConsolidatedExtractionResult,
)


logger = structlog.get_logger(__name__)


class ExtractionConsolidator:
    """
    Consolidator for merging extraction results from multiple sources.

    Applies business rules to produce a final extraction result:
    - Highest amount wins across all sources
    - Default to 100 EUR if no amounts found
    - Final confidence is weakest link

    Usage:
        consolidator = ExtractionConsolidator()

        source_results = [
            email_extractor.extract(body),
            pdf_extractor.extract(attachment1_path),
            image_extractor.extract(attachment2_path),
        ]

        final = consolidator.consolidate(source_results)
        print(f"Final amount: {final.gesamtforderung} EUR")
    """

    def __init__(self, default_amount: float = 100.0):
        """
        Initialize consolidator.

        Args:
            default_amount: Default amount when no amounts found anywhere.
                           USER DECISION: 100 EUR default.
        """
        self.default_amount = default_amount

        logger.debug(
            "extraction_consolidator_initialized",
            default_amount=self.default_amount,
        )

    def consolidate(
        self, source_results: List[SourceExtractionResult]
    ) -> ConsolidatedExtractionResult:
        """
        Merge extraction results from all sources.

        Business rules (USER DECISIONS):
        1. Highest amount wins when multiple sources have amounts
        2. Default to 100 EUR if no amount found
        3. Final confidence is weakest link

        Args:
            source_results: List of SourceExtractionResult from each source

        Returns:
            ConsolidatedExtractionResult with final merged values
        """
        log = logger.bind(num_sources=len(source_results))

        # Handle empty input
        if not source_results:
            log.warning("no_sources_to_consolidate")
            return ConsolidatedExtractionResult(
                gesamtforderung=self.default_amount,
                client_name=None,
                creditor_name=None,
                confidence="LOW",
                sources_processed=0,
                sources_with_amount=0,
                total_tokens_used=0,
                source_results=[],
            )

        # Collect all amounts, names, confidences from sources
        all_amounts: List[ExtractedAmount] = []
        all_client_names: List[ExtractedEntity] = []
        all_creditor_names: List[ExtractedEntity] = []
        total_tokens = 0
        confidences: List[str] = []

        for result in source_results:
            total_tokens += result.tokens_used

            if result.gesamtforderung:
                all_amounts.append(result.gesamtforderung)
                confidences.append(result.gesamtforderung.confidence)
                log.debug(
                    "amount_found",
                    source=result.source_name,
                    value=result.gesamtforderung.value,
                    confidence=result.gesamtforderung.confidence,
                )

            if result.client_name:
                all_client_names.append(result.client_name)
                confidences.append(result.client_name.confidence)

            if result.creditor_name:
                all_creditor_names.append(result.creditor_name)
                confidences.append(result.creditor_name.confidence)

        # Apply highest-amount-wins rule (USER DECISION)
        if all_amounts:
            # Deduplicate: amounts within 1 EUR are considered same
            unique_amounts = self._deduplicate_amounts(all_amounts)
            best_amount = max(unique_amounts, key=lambda x: x.value)
            final_amount = best_amount.value

            log.info(
                "highest_amount_wins",
                final_amount=final_amount,
                total_amounts_found=len(all_amounts),
                unique_amounts=len(unique_amounts),
                source=best_amount.source,
            )
        else:
            # USER DECISION: default to 100 EUR
            final_amount = self.default_amount
            confidences.append("LOW")  # Default amount = low confidence

            log.info(
                "default_amount_applied",
                default_amount=self.default_amount,
                reason="no_amounts_found",
            )

        # Pick best names (prefer HIGH confidence, then longest name)
        final_client_name = self._pick_best_name(all_client_names)
        final_creditor_name = self._pick_best_name(all_creditor_names)

        # Calculate final confidence (weakest link) - USER DECISION
        if not confidences:
            final_confidence = "LOW"
        else:
            confidence_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
            min_conf = min(confidences, key=lambda c: confidence_order.get(c, 0))
            final_confidence = min_conf

        log.info(
            "consolidation_complete",
            final_amount=final_amount,
            final_client_name=final_client_name,
            final_creditor_name=final_creditor_name,
            final_confidence=final_confidence,
            sources_processed=len(source_results),
            sources_with_amount=len(all_amounts),
            total_tokens_used=total_tokens,
        )

        return ConsolidatedExtractionResult(
            gesamtforderung=final_amount,
            client_name=final_client_name,
            creditor_name=final_creditor_name,
            confidence=final_confidence,
            sources_processed=len(source_results),
            sources_with_amount=len(all_amounts),
            total_tokens_used=total_tokens,
            source_results=source_results,
        )

    def _deduplicate_amounts(
        self, amounts: List[ExtractedAmount]
    ) -> List[ExtractedAmount]:
        """
        Deduplicate amounts within 1 EUR of each other.

        Keeps the higher confidence version when amounts are similar.

        Args:
            amounts: List of extracted amounts

        Returns:
            Deduplicated list of amounts
        """
        if not amounts:
            return []

        # Sort by value descending
        sorted_amounts = sorted(amounts, key=lambda x: x.value, reverse=True)

        unique = [sorted_amounts[0]]
        for amount in sorted_amounts[1:]:
            # Check if within 1 EUR of any existing
            if not any(abs(amount.value - existing.value) < 1.0 for existing in unique):
                unique.append(amount)

        logger.debug(
            "amounts_deduplicated",
            original_count=len(amounts),
            unique_count=len(unique),
        )

        return unique

    def _pick_best_name(self, names: List[ExtractedEntity]) -> Optional[str]:
        """
        Pick the best name from candidates.

        Prioritizes:
        1. HIGH confidence names
        2. Longest name (more complete)

        Args:
            names: List of extracted name entities

        Returns:
            Best name string, or None if no names provided
        """
        if not names:
            return None

        # Prefer HIGH confidence
        high_conf = [n for n in names if n.confidence == "HIGH"]
        if high_conf:
            # Among HIGH confidence, pick longest (more complete)
            best = max(high_conf, key=lambda n: len(n.value))
            logger.debug(
                "name_selected",
                value=best.value,
                confidence="HIGH",
                reason="high_confidence_longest",
            )
            return best.value

        # Fall back to longest name
        best = max(names, key=lambda n: len(n.value))
        logger.debug(
            "name_selected",
            value=best.value,
            confidence=best.confidence,
            reason="longest_name",
        )
        return best.value


__all__ = ["ExtractionConsolidator"]
