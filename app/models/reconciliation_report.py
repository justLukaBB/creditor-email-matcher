"""
ReconciliationReport Model
Tracks reconciliation runs comparing PostgreSQL and MongoDB
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, JSON
from sqlalchemy.sql import func
from app.database import Base


class ReconciliationReport(Base):
    """
    Audit trail for PostgreSQL-MongoDB reconciliation runs.

    Tracks mismatches found and repair actions taken during periodic
    consistency checks between the two databases.
    """
    __tablename__ = "reconciliation_reports"

    # Primary Key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Run Timestamps
    run_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Reconciliation Metrics
    records_checked = Column(Integer, default=0, nullable=False)
    mismatches_found = Column(Integer, default=0, nullable=False)
    auto_repaired = Column(Integer, default=0, nullable=False)
    failed_repairs = Column(Integer, default=0, nullable=False)

    # Mismatch Details
    details = Column(JSON, nullable=True)
    # List of mismatch details: [{type, postgres_id, mongo_id, field, pg_value, mongo_value}]

    # Status
    status = Column(String(50), default='running', nullable=False)
    # Statuses: running, completed, failed

    error_message = Column(Text, nullable=True)

    def __repr__(self):
        return f"<ReconciliationReport(id={self.id}, run_at={self.run_at}, status='{self.status}', mismatches={self.mismatches_found})>"
