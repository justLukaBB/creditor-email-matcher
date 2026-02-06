"""
Operational Metrics Models

Tracks pipeline health metrics: queue depth, processing time, error rates,
token usage, confidence distribution.

Raw metrics: 30-day retention. Cleaned by daily rollup job.
Daily rollup: Permanent retention.
"""

from sqlalchemy import Column, Integer, Float, String, Date, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.sql import func
from app.database import Base


class OperationalMetrics(Base):
    """
    Raw operational metrics. Retention: 30 days. Cleaned by daily rollup job.

    Tracks:
    - queue_depth: Number of emails waiting in processing queue
    - processing_time_ms: Time taken for each processing stage
    - error_count: Errors encountered during processing
    - token_usage: Claude API token consumption per operation
    - confidence_score: Confidence distribution across buckets
    """
    __tablename__ = "operational_metrics"

    # Primary Key
    id = Column(Integer, primary_key=True)

    # Metric classification
    metric_type = Column(String(50), nullable=False, index=True)
    # Values: "queue_depth", "processing_time_ms", "error_count", "token_usage", "confidence_score"

    metric_value = Column(Float, nullable=False)

    # Labels for metric segmentation (JSON)
    # For queue_depth: {"queue": "email_processing"}
    # For processing_time_ms: {"actor": "process_email", "stage": "extraction"}
    # For error_count: {"actor": "process_email", "error_type": "TimeoutError"}
    # For token_usage: {"model": "claude-sonnet", "operation": "extraction"}
    # For confidence_score: {"bucket": "high|medium|low"}
    labels = Column(JSON, nullable=True)

    # Optional link to specific email
    email_id = Column(Integer, ForeignKey("incoming_emails.id", ondelete="SET NULL"), nullable=True)

    # Timestamp
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    def __repr__(self):
        return f"<OperationalMetrics(id={self.id}, type={self.metric_type}, value={self.metric_value})>"


class OperationalMetricsDaily(Base):
    """
    Aggregated metrics. Retention: permanent.

    Daily rollup from OperationalMetrics provides historical analysis
    without table bloat.
    """
    __tablename__ = "operational_metrics_daily"

    # Primary Key
    id = Column(Integer, primary_key=True)

    # Metric classification
    metric_type = Column(String(50), nullable=False)
    date = Column(Date, nullable=False)

    # Labels aggregation key (for label-specific rollups)
    # e.g., "actor:process_email" or "model:claude-sonnet"
    labels_key = Column(String(100), nullable=True)

    # Aggregated statistics
    sample_count = Column(Integer, nullable=False)
    sum_value = Column(Float, nullable=False)  # For counts like errors
    avg_value = Column(Float, nullable=False)
    min_value = Column(Float, nullable=False)
    max_value = Column(Float, nullable=False)
    p95_value = Column(Float, nullable=True)  # 95th percentile

    __table_args__ = (
        # Unique constraint ensures one rollup per (metric_type, date, labels_key)
        # Allows idempotent re-aggregation if job runs multiple times
        Index('idx_ops_daily_unique', 'metric_type', 'date', 'labels_key', unique=True),
    )

    def __repr__(self):
        return f"<OperationalMetricsDaily(type={self.metric_type}, date={self.date}, samples={self.sample_count})>"
