"""
OutboxMessage Model
Stores operations to be replicated to MongoDB using transactional outbox pattern
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, Index
from sqlalchemy.sql import func
from app.database import Base


class OutboxMessage(Base):
    """
    Transactional outbox for PostgreSQL-to-MongoDB dual writes.

    Records operations that need to be replicated to MongoDB.
    Ensures at-least-once delivery semantics for dual-database saga pattern.
    """
    __tablename__ = "outbox_messages"

    # Primary Key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Aggregate Information
    aggregate_type = Column(String(100), nullable=False)
    # e.g., 'creditor_debt_update', 'incoming_email'

    aggregate_id = Column(String(255), nullable=False)
    # The PostgreSQL record ID this relates to

    # Operation Details
    operation = Column(String(50), nullable=False)
    # 'INSERT', 'UPDATE', 'DELETE'

    payload = Column(JSON, nullable=False)
    # The data to write to MongoDB

    # Idempotency
    idempotency_key = Column(String(255), unique=True, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)

    # Retry Logic
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=5, nullable=False)
    error_message = Column(Text, nullable=True)

    # Indexes for efficient polling of unprocessed messages
    __table_args__ = (
        Index('ix_outbox_unprocessed', 'processed_at', 'retry_count'),
        Index('ix_outbox_created_at', 'created_at'),
    )

    def __repr__(self):
        return f"<OutboxMessage(id={self.id}, type='{self.aggregate_type}', op='{self.operation}', processed={self.processed_at is not None})>"
