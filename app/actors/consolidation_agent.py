"""
Agent 3: Consolidation Actor (Phase 5: Multi-Agent Pipeline Validation)

Merges extraction results from all sources, resolves conflicts using majority voting,
detects conflicts with existing database records, and computes final confidence.

Purpose: Produce final validated extraction result with conflict detection and needs_review flagging.
Output: Agent 3 checkpoint with final_amount, conflicts_detected, and validation_status.
"""

import dramatiq
import structlog
from typing import Dict, Any, Optional
from datetime import datetime

from app.actors import broker

logger = structlog.get_logger(__name__)


@dramatiq.actor(
    broker=broker,
    max_retries=3,
    min_backoff=15000,  # 15 seconds
    max_backoff=300000,  # 5 minutes
    queue_name="consolidation"
)
def consolidate_results(email_id: int) -> Dict[str, Any]:
    """
    Agent 3: Consolidate extraction results and detect conflicts.

    This actor:
    1. Loads email and Agent 2 extraction checkpoint
    2. Queries MongoDB for existing data
    3. Detects conflicts with existing database records
    4. Computes final confidence
    5. Sets needs_review flag if conflicts or low confidence
    6. Saves Agent 3 checkpoint

    Args:
        email_id: IncomingEmail.id to process

    Returns:
        Dict with consolidation results:
        {
            "final_amount": float,
            "conflicts_detected": int,
            "needs_review": bool,
            "validation_status": str
        }

    Raises:
        ValueError: If email not found or Agent 2 checkpoint missing
        Re-raises exceptions to trigger Dramatiq retry
    """
    logger.info("consolidate_results_started", email_id=email_id)

    # Lazy imports to avoid circular dependencies
    from app.database import SessionLocal
    from app.models.incoming_email import IncomingEmail
    from app.services.validation import (
        get_checkpoint,
        save_checkpoint,
        check_confidence_threshold,
        detect_database_conflicts,
    )
    from app.services.mongodb_client import mongodb_service
    from app.services.extraction import ExtractionConsolidator

    db = SessionLocal()
    try:
        # Step 1: Load email
        email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()

        if not email:
            raise ValueError(f"IncomingEmail with id={email_id} not found")

        logger.info(
            "consolidation_email_loaded",
            email_id=email_id,
            from_email=email.from_email,
            zendesk_ticket_id=email.zendesk_ticket_id
        )

        # Step 2: Get Agent 2 extraction checkpoint
        agent2_checkpoint = get_checkpoint(db, email_id, "agent_2_extraction")

        if not agent2_checkpoint:
            raise ValueError(
                f"Agent 2 extraction checkpoint not found for email_id={email_id}. "
                "Agent 2 must run before Agent 3."
            )

        logger.info(
            "agent2_checkpoint_loaded",
            email_id=email_id,
            sources_processed=agent2_checkpoint.get("sources_processed"),
            gesamtforderung=agent2_checkpoint.get("gesamtforderung")
        )

        # Step 3: Extract Agent 2 results
        extracted_data = {
            "gesamtforderung": agent2_checkpoint.get("gesamtforderung"),
            "client_name": agent2_checkpoint.get("client_name"),
            "creditor_name": agent2_checkpoint.get("creditor_name"),
        }

        # Get confidence from Agent 2 (convert string to float)
        confidence_str = agent2_checkpoint.get("confidence", "MEDIUM")
        confidence_mapping = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}
        extraction_confidence = confidence_mapping.get(confidence_str, 0.7)

        # Step 4: Query MongoDB for existing data
        existing_data = None
        if mongodb_service.is_available():
            # Try multiple lookup strategies
            if email.zendesk_ticket_id:
                client = mongodb_service.get_client_by_ticket(email.zendesk_ticket_id)
                if client:
                    logger.info(
                        "mongodb_client_found_by_ticket",
                        email_id=email_id,
                        zendesk_ticket_id=email.zendesk_ticket_id
                    )

            # Try by client name if not found by ticket
            if not client and extracted_data.get("client_name"):
                client_name = extracted_data["client_name"]
                # Split name into first and last
                name_parts = client_name.strip().split(None, 1)
                if len(name_parts) == 2:
                    first_name, last_name = name_parts
                    client = mongodb_service.get_client_by_name(first_name, last_name)
                    if client:
                        logger.info(
                            "mongodb_client_found_by_name",
                            email_id=email_id,
                            client_name=client_name
                        )

            # Extract existing data if client found
            if client:
                # Find matching creditor in final_creditor_list
                creditor_email = email.from_email
                creditor_name = extracted_data.get("creditor_name")
                matched_creditor = None

                for cred in client.get("final_creditor_list", []):
                    # Match by email (primary) or name (fallback)
                    cred_email = cred.get("sender_email", "").lower().strip()
                    search_email = creditor_email.lower().strip()
                    email_match = (search_email in cred_email) or (cred_email in search_email)

                    if email_match:
                        matched_creditor = cred
                        logger.info(
                            "mongodb_creditor_matched",
                            email_id=email_id,
                            creditor_name=cred.get("sender_name")
                        )
                        break

                if matched_creditor:
                    existing_data = {
                        "debt_amount": matched_creditor.get("claim_amount"),
                        "client_name": f"{client.get('firstName', '')} {client.get('lastName', '')}".strip(),
                        "creditor_name": matched_creditor.get("sender_name"),
                    }
                    logger.info(
                        "mongodb_existing_data_loaded",
                        email_id=email_id,
                        existing_amount=existing_data["debt_amount"]
                    )
        else:
            logger.warning(
                "mongodb_unavailable",
                email_id=email_id,
                reason="cannot_detect_conflicts"
            )

        # Step 5: Detect conflicts with existing database records
        conflicts = detect_database_conflicts(extracted_data, existing_data)

        logger.info(
            "conflict_detection_complete",
            email_id=email_id,
            conflicts_detected=len(conflicts),
            conflict_fields=[c["field"] for c in conflicts]
        )

        # Step 6: Check confidence threshold
        confidence_check = check_confidence_threshold(extraction_confidence)

        # Step 7: Determine needs_review flag
        # USER DECISION: needs_review if conflicts detected OR confidence < 0.7
        needs_review = (len(conflicts) > 0) or confidence_check["needs_review"]

        if needs_review:
            reason_parts = []
            if len(conflicts) > 0:
                reason_parts.append(f"{len(conflicts)} conflicts detected")
            if confidence_check["needs_review"]:
                reason_parts.append(f"low confidence ({extraction_confidence})")

            logger.warning(
                "needs_review_flag_set",
                email_id=email_id,
                reason=", ".join(reason_parts),
                conflicts=len(conflicts),
                confidence=extraction_confidence
            )

        # Step 8: Build final checkpoint result
        final_result = {
            "final_amount": extracted_data["gesamtforderung"],
            "client_name": extracted_data.get("client_name"),
            "creditor_name": extracted_data.get("creditor_name"),
            "conflicts_detected": len(conflicts),
            "conflicts": conflicts,  # Full conflict details
            "confidence": extraction_confidence,
            "needs_review": needs_review,
            "validation_status": "passed" if not needs_review else "needs_review",
            "sources_processed": agent2_checkpoint.get("sources_processed", 0),
            "total_tokens_used": agent2_checkpoint.get("total_tokens_used", 0),
        }

        # Step 9: Save Agent 3 checkpoint
        save_checkpoint(db, email_id, "agent_3_consolidation", final_result)

        logger.info(
            "consolidate_results_completed",
            email_id=email_id,
            final_amount=final_result["final_amount"],
            conflicts_detected=final_result["conflicts_detected"],
            needs_review=final_result["needs_review"],
            validation_status=final_result["validation_status"]
        )

        return final_result

    except Exception as e:
        logger.error(
            "consolidate_results_error",
            email_id=email_id,
            error=str(e),
            exception_type=type(e).__name__,
            exc_info=True
        )
        # Re-raise to trigger Dramatiq retry
        raise

    finally:
        db.close()


__all__ = ["consolidate_results"]
