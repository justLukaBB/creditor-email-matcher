"""
Idempotency Service
Provides PostgreSQL-backed idempotency checking and storage for saga operations
"""

from typing import Optional
from datetime import datetime, timedelta
import hashlib
import json
import structlog
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.idempotency_key import IdempotencyKey

logger = structlog.get_logger(__name__)


def generate_idempotency_key(operation: str, aggregate_id: str, payload: dict) -> str:
    """
    Generate consistent idempotency key for an operation.

    Format: {operation_type}:{aggregate_id}:{content_hash[:16]}

    Args:
        operation: Type of operation (e.g., 'creditor_debt_update')
        aggregate_id: ID of the aggregate being operated on
        payload: Operation payload to hash

    Returns:
        Idempotency key string
    """
    # Create consistent JSON representation for hashing
    payload_json = json.dumps(payload, sort_keys=True, default=str)
    content_hash = hashlib.sha256(payload_json.encode()).hexdigest()

    key = f"{operation}:{aggregate_id}:{content_hash[:16]}"
    return key


class IdempotencyService:
    """
    PostgreSQL-backed idempotency service for preventing duplicate saga operations.

    Uses idempotency_keys table to track processed operations with TTL expiration.
    """

    def __init__(self, session_factory: sessionmaker):
        """
        Initialize idempotency service.

        Args:
            session_factory: SQLAlchemy sessionmaker (not session - creates independent transactions)
        """
        self.session_factory = session_factory
        self.logger = logger.bind(service="idempotency")

    def check(self, key: str) -> Optional[dict]:
        """
        Check if an idempotency key exists and is still valid.

        Args:
            key: Idempotency key to check

        Returns:
            Cached result dict if key exists and not expired, None otherwise
        """
        session: Session = self.session_factory()
        try:
            now = datetime.utcnow()

            # Query for key that exists and is not expired
            idempotency_record = session.query(IdempotencyKey).filter(
                IdempotencyKey.key == key,
                IdempotencyKey.expires_at > now
            ).first()

            if idempotency_record:
                self.logger.info(
                    "idempotency_key_found",
                    key=key,
                    created_at=idempotency_record.created_at,
                    expires_at=idempotency_record.expires_at
                )
                return idempotency_record.result

            # Check if expired key exists - delete it
            expired_record = session.query(IdempotencyKey).filter(
                IdempotencyKey.key == key,
                IdempotencyKey.expires_at <= now
            ).first()

            if expired_record:
                self.logger.info("idempotency_key_expired", key=key)
                session.delete(expired_record)
                session.commit()

            return None

        except Exception as e:
            self.logger.error("idempotency_check_failed", key=key, error=str(e))
            session.rollback()
            return None
        finally:
            session.close()

    def store(self, key: str, result: dict, ttl_seconds: int = 86400) -> bool:
        """
        Store idempotency key with cached result.

        Uses INSERT ... ON CONFLICT DO NOTHING to handle race conditions.

        Args:
            key: Idempotency key
            result: Result to cache for duplicate requests
            ttl_seconds: Time-to-live in seconds (default: 24 hours)

        Returns:
            True if stored successfully, False if key already exists
        """
        session: Session = self.session_factory()
        try:
            now = datetime.utcnow()
            expires_at = now + timedelta(seconds=ttl_seconds)

            # Use PostgreSQL INSERT ... ON CONFLICT DO NOTHING for atomic upsert
            stmt = pg_insert(IdempotencyKey).values(
                key=key,
                result=result,
                created_at=now,
                expires_at=expires_at
            ).on_conflict_do_nothing(index_elements=['key'])

            result_proxy = session.execute(stmt)
            session.commit()

            # Check if row was inserted (rowcount > 0) or conflicted (rowcount = 0)
            inserted = result_proxy.rowcount > 0

            if inserted:
                self.logger.info(
                    "idempotency_key_stored",
                    key=key,
                    ttl_seconds=ttl_seconds,
                    expires_at=expires_at
                )
            else:
                self.logger.info("idempotency_key_already_exists", key=key)

            return inserted

        except Exception as e:
            self.logger.error("idempotency_store_failed", key=key, error=str(e))
            session.rollback()
            return False
        finally:
            session.close()

    def cleanup_expired(self) -> int:
        """
        Delete all expired idempotency keys.

        Called by reconciliation job to prevent unbounded table growth.

        Returns:
            Number of keys deleted
        """
        session: Session = self.session_factory()
        try:
            now = datetime.utcnow()

            # Delete expired keys
            deleted_count = session.query(IdempotencyKey).filter(
                IdempotencyKey.expires_at < now
            ).delete(synchronize_session=False)

            session.commit()

            self.logger.info("idempotency_cleanup_complete", deleted_count=deleted_count)
            return deleted_count

        except Exception as e:
            self.logger.error("idempotency_cleanup_failed", error=str(e))
            session.rollback()
            return 0
        finally:
            session.close()
