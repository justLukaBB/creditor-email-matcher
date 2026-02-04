"""
ReconciliationService
Compares PostgreSQL (source of truth) with MongoDB to detect and repair drift
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session, sessionmaker
import structlog

from app.models.reconciliation_report import ReconciliationReport
from app.models.outbox_message import OutboxMessage
from app.models.incoming_email import IncomingEmail
from app.models.idempotency_key import IdempotencyKey

logger = structlog.get_logger()


class ReconciliationService:
    """
    Reconciliation service for PostgreSQL-MongoDB consistency checks.

    Runs three operations per cycle:
    1. Retry pending outbox messages (failed MongoDB writes)
    2. Compare recent PostgreSQL records with MongoDB
    3. Cleanup expired idempotency keys and old outbox messages
    """

    def __init__(self, session_factory: sessionmaker, mongodb_service):
        """
        Initialize ReconciliationService.

        Args:
            session_factory: SQLAlchemy sessionmaker for creating sessions
            mongodb_service: MongoDBService instance for MongoDB operations
        """
        self.session_factory = session_factory
        self.mongodb_service = mongodb_service

    def run_reconciliation(self) -> Dict[str, Any]:
        """
        Main entry point for reconciliation.

        Returns:
            dict: Summary of reconciliation run with counts and results
        """
        session = self.session_factory()

        try:
            # Create ReconciliationReport with status='running'
            report = ReconciliationReport(
                status='running',
                run_at=datetime.utcnow()
            )
            session.add(report)
            session.commit()
            session.refresh(report)

            run_id = report.id
            logger.info("reconciliation_started", run_id=run_id)

            # Step 1: Retry pending outbox messages
            outbox_result = self._retry_pending_outbox(session)
            logger.info("outbox_retry_completed", run_id=run_id, **outbox_result)

            # Step 2: Compare recent records (last 48 hours)
            if self.mongodb_service.is_available():
                mismatches = self._compare_recent_records(session, lookback_hours=48)
                logger.info("comparison_completed", run_id=run_id, mismatches=len(mismatches))
            else:
                logger.warning("mongodb_unavailable", run_id=run_id, action="skipped_comparison")
                mismatches = []

            # Step 3: Cleanup stale data
            cleanup_result = self._cleanup_stale_data(session)
            logger.info("cleanup_completed", run_id=run_id, **cleanup_result)

            # Update report with results
            report.completed_at = datetime.utcnow()
            report.records_checked = len(mismatches) if mismatches else 0
            report.mismatches_found = len([m for m in mismatches if m.get('type') in ['missing_in_mongo', 'data_mismatch']])
            report.auto_repaired = len([m for m in mismatches if m.get('repaired', False)])
            report.failed_repairs = len([m for m in mismatches if not m.get('repaired', False) and m.get('type') != 'extra_in_mongo'])
            report.details = mismatches if mismatches else []
            report.status = 'completed'

            session.commit()

            summary = {
                'run_id': run_id,
                'status': 'completed',
                'outbox_retried': outbox_result['retried'],
                'outbox_succeeded': outbox_result['succeeded'],
                'outbox_failed': outbox_result['failed'],
                'records_checked': report.records_checked,
                'mismatches_found': report.mismatches_found,
                'auto_repaired': report.auto_repaired,
                'failed_repairs': report.failed_repairs,
                'expired_keys_deleted': cleanup_result['expired_keys_deleted'],
                'old_outbox_deleted': cleanup_result['old_outbox_deleted']
            }

            logger.info("reconciliation_completed", **summary)
            return summary

        except Exception as e:
            logger.error("reconciliation_crashed", error=str(e), exc_info=True)

            # Update report status to failed
            try:
                report.status = 'failed'
                report.error_message = str(e)
                report.completed_at = datetime.utcnow()
                session.commit()
            except Exception as report_error:
                logger.error("failed_to_update_report", error=str(report_error))

            raise

        finally:
            session.close()

    def _retry_pending_outbox(self, session: Session) -> Dict[str, int]:
        """
        Retry unprocessed outbox messages (failed MongoDB writes).

        Args:
            session: SQLAlchemy session

        Returns:
            dict: {'retried': N, 'succeeded': N, 'failed': N}
        """
        # Query outbox messages where processed_at IS NULL and retry_count < max_retries
        pending_messages = session.query(OutboxMessage).filter(
            OutboxMessage.processed_at.is_(None),
            OutboxMessage.retry_count < OutboxMessage.max_retries
        ).all()

        retried = 0
        succeeded = 0
        failed = 0

        for msg in pending_messages:
            retried += 1

            try:
                # Extract payload data
                payload = msg.payload

                # Attempt MongoDB write
                # The payload should contain: client_name, client_aktenzeichen, creditor_email,
                # creditor_name, debt_amount, response_text, reference_numbers
                success = self.mongodb_service.update_creditor_debt_amount(
                    client_name=payload.get('client_name', ''),
                    client_aktenzeichen=payload.get('client_aktenzeichen'),
                    creditor_email=payload.get('creditor_email', ''),
                    creditor_name=payload.get('creditor_name', ''),
                    new_debt_amount=payload.get('debt_amount', 0.0),
                    response_text=payload.get('response_text'),
                    reference_numbers=payload.get('reference_numbers')
                )

                if success:
                    # Mark as processed
                    msg.processed_at = datetime.utcnow()

                    # Update corresponding IncomingEmail sync_status
                    if msg.aggregate_type == 'incoming_email':
                        email_id = msg.aggregate_id
                        email = session.query(IncomingEmail).filter(
                            IncomingEmail.id == int(email_id)
                        ).first()

                        if email:
                            email.sync_status = 'synced'
                            email.sync_error = None

                    session.commit()
                    succeeded += 1

                    logger.info("outbox_retry_success",
                               message_id=msg.id,
                               aggregate_type=msg.aggregate_type,
                               aggregate_id=msg.aggregate_id)
                else:
                    # Increment retry count, store error
                    msg.retry_count += 1
                    msg.error_message = "MongoDB update returned False"
                    session.commit()
                    failed += 1

                    logger.warning("outbox_retry_failed",
                                  message_id=msg.id,
                                  retry_count=msg.retry_count,
                                  max_retries=msg.max_retries)

            except Exception as e:
                # Increment retry count, store error
                msg.retry_count += 1
                msg.error_message = str(e)
                session.commit()
                failed += 1

                logger.error("outbox_retry_error",
                            message_id=msg.id,
                            error=str(e),
                            retry_count=msg.retry_count)

        return {
            'retried': retried,
            'succeeded': succeeded,
            'failed': failed
        }

    def _compare_recent_records(self, session: Session, lookback_hours: int = 48) -> List[Dict[str, Any]]:
        """
        Compare PostgreSQL with MongoDB for recent records.

        Looks at IncomingEmail records from last N hours with sync_status='synced'
        and extracted_data containing debt_amount. Compares with MongoDB to detect:
        - Missing records in MongoDB
        - Data mismatches (debt_amount differences)

        Args:
            session: SQLAlchemy session
            lookback_hours: How many hours back to check (default 48)

        Returns:
            list: List of mismatch dicts with details
        """
        mismatches = []

        # Query recent synced emails with extracted data
        cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)

        recent_emails = session.query(IncomingEmail).filter(
            IncomingEmail.created_at >= cutoff,
            IncomingEmail.sync_status == 'synced',
            IncomingEmail.extracted_data.isnot(None)
        ).all()

        logger.info("comparing_records", count=len(recent_emails), lookback_hours=lookback_hours)

        for email in recent_emails:
            extracted = email.extracted_data or {}

            # Only check emails that have debt_amount in extracted data
            if 'debt_amount' not in extracted:
                continue

            client_name = extracted.get('client_name', '')
            client_aktenzeichen = extracted.get('client_aktenzeichen')
            creditor_name = extracted.get('creditor_name', '')
            creditor_email = email.from_email
            debt_amount = extracted.get('debt_amount')

            if not client_name and not client_aktenzeichen:
                # Can't compare without client identification
                continue

            # Find the client in MongoDB
            mongo_client = None

            if client_aktenzeichen:
                mongo_client = self.mongodb_service.get_client_by_aktenzeichen(client_aktenzeichen)

            if not mongo_client and client_name:
                # Try by name
                name_parts = client_name.strip().split(None, 1)
                if len(name_parts) == 2:
                    first_name, last_name = name_parts
                    mongo_client = self.mongodb_service.get_client_by_name(first_name, last_name)

            if not mongo_client:
                # Missing in MongoDB
                mismatch = {
                    'type': 'missing_in_mongo',
                    'email_id': email.id,
                    'client_name': client_name,
                    'client_aktenzeichen': client_aktenzeichen,
                    'creditor_name': creditor_name,
                    'pg_debt_amount': debt_amount,
                    'repaired': False
                }

                # Attempt repair
                repair_success = self._repair_missing_in_mongo(session, email, extracted)
                mismatch['repaired'] = repair_success

                mismatches.append(mismatch)
                continue

            # Client found - check creditor in final_creditor_list
            creditors = mongo_client.get('final_creditor_list', [])
            found_creditor = None

            for cred in creditors:
                # Match by email or name
                cred_email = cred.get('sender_email', '').lower().strip()
                search_email = creditor_email.lower().strip()

                if search_email in cred_email or cred_email in search_email:
                    found_creditor = cred
                    break

                # Fallback: name matching
                cred_name = cred.get('sender_name', '').lower().strip()
                search_name = creditor_name.lower().strip()

                if search_name and cred_name:
                    # Check for word overlap
                    cred_words = set(w for w in cred_name.split() if len(w) > 3)
                    search_words = set(w for w in search_name.split() if len(w) > 3)

                    if cred_words & search_words:
                        found_creditor = cred
                        break

            if not found_creditor:
                # Creditor not in client's list
                mismatch = {
                    'type': 'missing_creditor_in_mongo',
                    'email_id': email.id,
                    'client_aktenzeichen': mongo_client.get('aktenzeichen'),
                    'creditor_name': creditor_name,
                    'creditor_email': creditor_email,
                    'pg_debt_amount': debt_amount,
                    'repaired': False
                }

                # Attempt repair
                repair_success = self._repair_missing_in_mongo(session, email, extracted)
                mismatch['repaired'] = repair_success

                mismatches.append(mismatch)
                continue

            # Creditor found - compare debt amount
            mongo_debt_amount = found_creditor.get('creditor_response_amount') or found_creditor.get('claim_amount')

            if mongo_debt_amount is not None and debt_amount is not None:
                # Compare with tolerance for floating point
                if abs(float(mongo_debt_amount) - float(debt_amount)) > 0.01:
                    mismatch = {
                        'type': 'data_mismatch',
                        'email_id': email.id,
                        'client_aktenzeichen': mongo_client.get('aktenzeichen'),
                        'creditor_name': creditor_name,
                        'field': 'debt_amount',
                        'pg_value': debt_amount,
                        'mongo_value': mongo_debt_amount,
                        'repaired': False
                    }

                    # Attempt repair - update MongoDB with PostgreSQL value
                    repair_success = self._repair_missing_in_mongo(session, email, extracted)
                    mismatch['repaired'] = repair_success

                    mismatches.append(mismatch)

        return mismatches

    def _cleanup_stale_data(self, session: Session) -> Dict[str, int]:
        """
        Cleanup expired idempotency keys and old processed outbox messages.

        Args:
            session: SQLAlchemy session

        Returns:
            dict: {'expired_keys_deleted': N, 'old_outbox_deleted': N}
        """
        # Delete expired idempotency keys
        now = datetime.utcnow()

        expired_keys_count = session.query(IdempotencyKey).filter(
            IdempotencyKey.expires_at < now
        ).delete(synchronize_session=False)

        # Delete old processed outbox messages (older than 30 days)
        thirty_days_ago = now - timedelta(days=30)

        old_outbox_count = session.query(OutboxMessage).filter(
            OutboxMessage.processed_at.isnot(None),
            OutboxMessage.created_at < thirty_days_ago
        ).delete(synchronize_session=False)

        session.commit()

        logger.info("cleanup_completed",
                   expired_keys_deleted=expired_keys_count,
                   old_outbox_deleted=old_outbox_count)

        return {
            'expired_keys_deleted': expired_keys_count,
            'old_outbox_deleted': old_outbox_count
        }

    def _repair_missing_in_mongo(self, session: Session, email: IncomingEmail, extracted_data: Dict[str, Any]) -> bool:
        """
        Attempt to repair missing MongoDB data from PostgreSQL.

        Uses mongodb_service.update_creditor_debt_amount() to write
        missing data to MongoDB.

        Args:
            session: SQLAlchemy session
            email: IncomingEmail record with data to repair
            extracted_data: Extracted data dict from email

        Returns:
            bool: True if repair succeeded, False otherwise
        """
        try:
            success = self.mongodb_service.update_creditor_debt_amount(
                client_name=extracted_data.get('client_name', ''),
                client_aktenzeichen=extracted_data.get('client_aktenzeichen'),
                creditor_email=email.from_email,
                creditor_name=extracted_data.get('creditor_name', ''),
                new_debt_amount=extracted_data.get('debt_amount', 0.0),
                response_text=extracted_data.get('response_text'),
                reference_numbers=extracted_data.get('reference_numbers')
            )

            if success:
                logger.info("repair_success", email_id=email.id)
                return True
            else:
                logger.warning("repair_failed_no_update", email_id=email.id)
                return False

        except Exception as e:
            logger.error("repair_error", email_id=email.id, error=str(e))
            return False
