"""
IdempotencyKey Model
Stores idempotency keys for duplicate prevention in dual-database writes
"""

from sqlalchemy import Column, Integer, String, DateTime, JSON, Index
from sqlalchemy.sql import func
from app.database import Base


class IdempotencyKey(Base):
    """
    Idempotency key storage for preventing duplicate operations.

    Used to ensure saga operations are idempotent when retried.
    Expired keys are cleaned up by reconciliation job (no Redis needed in Phase 1).
    """
    __tablename__ = "idempotency_keys"

    # Primary Key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Idempotency Key
    key = Column(String(255), unique=True, nullable=False, index=True)

    # Cached Result
    result = Column(JSON, nullable=True)
    # Cached result of the operation for fast duplicate response

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # Index for cleanup queries
    __table_args__ = (
        Index('ix_idempotency_expires_at', 'expires_at'),
    )

    def __repr__(self):
        return f"<IdempotencyKey(id={self.id}, key='{self.key}', expires_at={self.expires_at})>"
