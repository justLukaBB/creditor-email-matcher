"""
Checkpoint Management
Save and retrieve agent results from IncomingEmail.agent_checkpoints JSONB column
"""

from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
import structlog

from app.models.incoming_email import IncomingEmail

logger = structlog.get_logger(__name__)


def save_checkpoint(db: Session, email_id: int, agent_name: str, result: dict) -> None:
    """
    Save agent result as checkpoint in IncomingEmail.agent_checkpoints JSONB.

    Agent names: "agent_1_intent", "agent_2_extraction", "agent_3_consolidation"

    Checkpoint includes timestamp and validation_status automatically.

    Args:
        db: Database session
        email_id: IncomingEmail ID
        agent_name: Agent identifier (agent_1_intent, agent_2_extraction, agent_3_consolidation)
        result: Agent result dictionary to store

    Raises:
        ValueError: If email_id not found

    Example:
        >>> save_checkpoint(
        ...     db, 123, "agent_1_intent",
        ...     {"intent": "debt_statement", "confidence": 0.85, "method": "claude_haiku"}
        ... )
    """
    email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()

    if not email:
        raise ValueError(f"IncomingEmail with id={email_id} not found")

    # Initialize agent_checkpoints if None
    if email.agent_checkpoints is None:
        email.agent_checkpoints = {}

    # Add timestamp to result
    checkpoint_data = result.copy()
    checkpoint_data["timestamp"] = datetime.utcnow().isoformat()

    # Add validation_status if not present (default to "passed")
    if "validation_status" not in checkpoint_data:
        checkpoint_data["validation_status"] = "passed"

    # Store checkpoint under agent name
    email.agent_checkpoints[agent_name] = checkpoint_data

    # Mark the column as modified (SQLAlchemy doesn't auto-detect JSONB changes)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(email, "agent_checkpoints")

    db.commit()

    logger.info(
        "checkpoint_saved",
        email_id=email_id,
        agent_name=agent_name,
        timestamp=checkpoint_data["timestamp"],
        validation_status=checkpoint_data["validation_status"]
    )


def get_checkpoint(db: Session, email_id: int, agent_name: str) -> Optional[Dict[str, Any]]:
    """
    Return cached checkpoint if exists (for replay/skip).

    Args:
        db: Database session
        email_id: IncomingEmail ID
        agent_name: Agent identifier

    Returns:
        Checkpoint dictionary if exists, None otherwise

    Example:
        >>> checkpoint = get_checkpoint(db, 123, "agent_1_intent")
        >>> if checkpoint:
        ...     print(checkpoint["intent"])
    """
    email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()

    if not email or email.agent_checkpoints is None:
        return None

    checkpoint = email.agent_checkpoints.get(agent_name)

    if checkpoint:
        logger.info(
            "checkpoint_retrieved",
            email_id=email_id,
            agent_name=agent_name,
            timestamp=checkpoint.get("timestamp"),
            validation_status=checkpoint.get("validation_status")
        )

    return checkpoint


def has_valid_checkpoint(db: Session, email_id: int, agent_name: str) -> bool:
    """
    Check if checkpoint exists with validation_status != "failed".
    Used for skipping completed agents on retry.

    Args:
        db: Database session
        email_id: IncomingEmail ID
        agent_name: Agent identifier

    Returns:
        True if valid checkpoint exists, False otherwise

    Example:
        >>> if has_valid_checkpoint(db, 123, "agent_1_intent"):
        ...     print("Skip agent 1 - already completed")
    """
    checkpoint = get_checkpoint(db, email_id, agent_name)

    if not checkpoint:
        return False

    validation_status = checkpoint.get("validation_status", "unknown")
    is_valid = validation_status != "failed"

    logger.info(
        "checkpoint_validity_check",
        email_id=email_id,
        agent_name=agent_name,
        validation_status=validation_status,
        is_valid=is_valid
    )

    return is_valid
