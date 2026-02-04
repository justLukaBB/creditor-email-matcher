"""
Dual Database Writer
Implements saga pattern for PostgreSQL-MongoDB dual writes with compensation
"""

from typing import Optional, List
from datetime import datetime
import structlog
from sqlalchemy.orm import Session

from app.models.outbox_message import OutboxMessage
from app.models.incoming_email import IncomingEmail
from app.services.idempotency import IdempotencyService

logger = structlog.get_logger(__name__)


class DualDatabaseWriter:
    """
    Saga pattern orchestrator for dual-database writes.

    Write flow:
    1. PostgreSQL write + outbox message (atomic transaction)
    2. Caller commits transaction
    3. MongoDB write attempted (compensatable)
    4. Outbox updated with result

    This ensures PostgreSQL is source of truth and MongoDB can be reconciled.
    """

    def __init__(self, pg_session: Session, idempotency_service: IdempotencyService):
        """
        Initialize dual database writer.

        Args:
            pg_session: Active SQLAlchemy session (caller controls transaction)
            idempotency_service: Idempotency service for duplicate prevention
        """
        self.pg_session = pg_session
        self.idempotency_service = idempotency_service
        self.logger = logger.bind(service="dual_write")

    def update_creditor_debt(
        self,
        email_id: int,
        client_name: str,
        client_aktenzeichen: Optional[str],
        creditor_email: str,
        creditor_name: str,
        new_debt_amount: float,
        response_text: Optional[str],
        reference_numbers: Optional[List[str]],
        idempotency_key: str
    ) -> dict:
        """
        Update creditor debt with saga pattern (PostgreSQL first, then MongoDB).

        This method does NOT commit the PostgreSQL transaction - caller must commit.
        This ensures outbox message is atomic with business data.

        Args:
            email_id: IncomingEmail ID
            client_name: Client name for matching
            client_aktenzeichen: Client case number
            creditor_email: Creditor email address
            creditor_name: Creditor name
            new_debt_amount: Updated debt amount
            response_text: Optional summary text
            reference_numbers: Optional reference numbers
            idempotency_key: Idempotency key for duplicate prevention

        Returns:
            dict with outbox_message_id for post-commit MongoDB write
        """
        log = self.logger.bind(
            operation="creditor_debt_update",
            email_id=email_id,
            idempotency_key=idempotency_key
        )

        log.info("saga_start", saga_step="check_idempotency")

        # Step 1: Check idempotency
        cached_result = self.idempotency_service.check(idempotency_key)
        if cached_result:
            log.info("saga_duplicate_detected", cached_result=cached_result)
            return cached_result

        log.info("saga_step", saga_step="create_outbox_message")

        # Step 2: Create outbox message in same transaction as business data
        payload = {
            "client_name": client_name,
            "client_aktenzeichen": client_aktenzeichen,
            "creditor_email": creditor_email,
            "creditor_name": creditor_name,
            "new_debt_amount": new_debt_amount,
            "response_text": response_text,
            "reference_numbers": reference_numbers
        }

        outbox_message = OutboxMessage(
            aggregate_type="creditor_debt_update",
            aggregate_id=str(email_id),
            operation="UPDATE",
            payload=payload,
            idempotency_key=idempotency_key
        )

        self.pg_session.add(outbox_message)
        self.pg_session.flush()  # Get ID without committing

        log.info(
            "saga_step",
            saga_step="update_email_sync_status",
            outbox_message_id=outbox_message.id
        )

        # Step 3: Update IncomingEmail sync status and idempotency key
        email = self.pg_session.query(IncomingEmail).filter(
            IncomingEmail.id == email_id
        ).first()

        if email:
            email.sync_status = 'pending'
            email.idempotency_key = idempotency_key
            self.pg_session.flush()

        log.info(
            "saga_step",
            saga_step="postgresql_write_complete",
            outbox_message_id=outbox_message.id,
            note="caller_must_commit"
        )

        # Return result for post-commit MongoDB write
        result = {
            "outbox_message_id": outbox_message.id,
            "email_id": email_id,
            "status": "pending_mongodb_sync"
        }

        return result

    def execute_mongodb_write(self, outbox_message_id: int) -> bool:
        """
        Execute MongoDB write for an outbox message (post-commit, compensatable).

        Creates its own session for updating outbox status.

        Args:
            outbox_message_id: OutboxMessage ID to process

        Returns:
            True if MongoDB write successful, False otherwise
        """
        # Import mongodb_service here to use existing singleton
        from app.services.mongodb_client import mongodb_service

        log = self.logger.bind(
            operation="mongodb_write",
            outbox_message_id=outbox_message_id
        )

        log.info("saga_step", saga_step="load_outbox_message")

        # Load outbox message
        outbox_message = self.pg_session.query(OutboxMessage).filter(
            OutboxMessage.id == outbox_message_id
        ).first()

        if not outbox_message:
            log.error("outbox_message_not_found")
            return False

        payload = outbox_message.payload
        email_id = int(outbox_message.aggregate_id)

        log = log.bind(
            email_id=email_id,
            idempotency_key=outbox_message.idempotency_key
        )

        log.info("saga_step", saga_step="attempt_mongodb_write")

        try:
            # Call MongoDB service to update creditor debt
            success = mongodb_service.update_creditor_debt_amount(
                client_name=payload["client_name"],
                client_aktenzeichen=payload.get("client_aktenzeichen"),
                creditor_email=payload["creditor_email"],
                creditor_name=payload["creditor_name"],
                new_debt_amount=payload["new_debt_amount"],
                response_text=payload.get("response_text"),
                reference_numbers=payload.get("reference_numbers")
            )

            if success:
                log.info("saga_step", saga_step="mongodb_write_success")

                # Mark outbox message as processed
                outbox_message.processed_at = datetime.utcnow()

                # Update IncomingEmail sync status
                email = self.pg_session.query(IncomingEmail).filter(
                    IncomingEmail.id == email_id
                ).first()

                if email:
                    email.sync_status = 'synced'
                    email.sync_error = None

                self.pg_session.commit()

                # Store idempotency result
                result = {
                    "outbox_message_id": outbox_message_id,
                    "email_id": email_id,
                    "status": "synced",
                    "mongodb_success": True
                }
                self.idempotency_service.store(
                    key=outbox_message.idempotency_key,
                    result=result
                )

                log.info("saga_complete", result=result)
                return True

            else:
                # MongoDB write failed (client or creditor not found)
                log.warning("saga_step", saga_step="mongodb_write_failed")

                # Increment retry count and store error
                outbox_message.retry_count += 1
                outbox_message.error_message = "MongoDB update failed - client or creditor not found"

                # Update IncomingEmail sync status
                email = self.pg_session.query(IncomingEmail).filter(
                    IncomingEmail.id == email_id
                ).first()

                if email:
                    email.sync_status = 'failed'
                    email.sync_error = outbox_message.error_message

                self.pg_session.commit()

                log.error(
                    "saga_failed",
                    retry_count=outbox_message.retry_count,
                    max_retries=outbox_message.max_retries
                )
                return False

        except Exception as e:
            log.error("saga_step", saga_step="mongodb_write_exception", error=str(e))

            # Increment retry count and store error
            outbox_message.retry_count += 1
            outbox_message.error_message = str(e)

            # Update IncomingEmail sync status
            email = self.pg_session.query(IncomingEmail).filter(
                IncomingEmail.id == email_id
            ).first()

            if email:
                email.sync_status = 'failed'
                email.sync_error = str(e)

            self.pg_session.commit()

            log.error(
                "saga_exception",
                retry_count=outbox_message.retry_count,
                max_retries=outbox_message.max_retries,
                error=str(e)
            )
            return False

    def process_pending_outbox(self, max_retries: int = 5) -> int:
        """
        Process pending outbox messages (retry failed MongoDB writes).

        Called by reconciliation scheduler to retry failed operations.

        Args:
            max_retries: Maximum retry attempts before giving up

        Returns:
            Number of successfully processed messages
        """
        log = self.logger.bind(operation="process_pending_outbox")

        log.info("outbox_processing_start", max_retries=max_retries)

        # Query unprocessed messages with retries remaining
        pending_messages = self.pg_session.query(OutboxMessage).filter(
            OutboxMessage.processed_at.is_(None),
            OutboxMessage.retry_count < max_retries
        ).order_by(OutboxMessage.created_at).all()

        log.info("outbox_messages_found", count=len(pending_messages))

        success_count = 0

        for message in pending_messages:
            log_msg = log.bind(
                outbox_message_id=message.id,
                aggregate_id=message.aggregate_id,
                retry_count=message.retry_count
            )

            log_msg.info("processing_outbox_message")

            success = self.execute_mongodb_write(message.id)

            if success:
                success_count += 1
                log_msg.info("outbox_message_processed_successfully")
            else:
                log_msg.warning("outbox_message_processing_failed")

        log.info(
            "outbox_processing_complete",
            total=len(pending_messages),
            successful=success_count,
            failed=len(pending_messages) - success_count
        )

        return success_count
