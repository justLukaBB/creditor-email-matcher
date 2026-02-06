"""
Prompt Performance Metrics Models
Tracks per-extraction metrics and daily aggregated rollups
"""

from sqlalchemy import Column, Integer, BigInteger, Float, Numeric, Boolean, Date, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from app.database import Base


class PromptPerformanceMetrics(Base):
    """
    Raw extraction-level metrics. Retention: 30 days.

    After 30 days, data is aggregated into PromptPerformanceDaily
    and deleted from this table.

    Tracks BOTH cost metrics (tokens, API cost) AND quality metrics
    (extraction success, confidence, manual review rate) per USER DECISION
    in CONTEXT.md.
    """
    __tablename__ = "prompt_performance_metrics"

    # Primary Key
    id = Column(Integer, primary_key=True)

    # References
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False, index=True)
    email_id = Column(Integer, ForeignKey("incoming_emails.id"), nullable=False)

    # Cost metrics (USER DECISION: track both cost and quality)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    api_cost_usd = Column(Numeric(10, 6), nullable=False)

    # Quality metrics (USER DECISION: track both cost and quality)
    extraction_success = Column(Boolean, nullable=False)  # Did extraction complete?
    confidence_score = Column(Float, nullable=True)  # Overall confidence
    manual_review_required = Column(Boolean, nullable=True)  # Routed to manual review?

    # Execution metrics
    execution_time_ms = Column(Integer, nullable=False)

    # Timestamp
    extracted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    def __repr__(self):
        return f"<PromptPerformanceMetrics(id={self.id}, prompt_template_id={self.prompt_template_id}, success={self.extraction_success})>"


class PromptPerformanceDaily(Base):
    """
    Daily rollup of prompt performance metrics. Permanent retention.

    Aggregated from PromptPerformanceMetrics via scheduled job.
    Provides historical analysis without table bloat.
    """
    __tablename__ = "prompt_performance_daily"

    # Primary Key
    id = Column(Integer, primary_key=True)

    # References
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False)
    date = Column(Date, nullable=False)

    # Aggregated cost metrics
    total_extractions = Column(Integer, nullable=False)
    total_input_tokens = Column(BigInteger, nullable=False)
    total_output_tokens = Column(BigInteger, nullable=False)
    total_api_cost_usd = Column(Numeric(10, 2), nullable=False)

    # Aggregated quality metrics
    successful_extractions = Column(Integer, nullable=False)
    avg_confidence_score = Column(Float, nullable=True)
    manual_review_count = Column(Integer, nullable=False)

    # Aggregated execution metrics
    avg_execution_time_ms = Column(Integer, nullable=False)
    p95_execution_time_ms = Column(Integer, nullable=True)

    __table_args__ = (
        # Unique constraint ensures one rollup per (prompt_template_id, date)
        # Allows idempotent re-aggregation if job runs multiple times
        Index('idx_prompt_daily_unique', 'prompt_template_id', 'date', unique=True),
    )

    def __repr__(self):
        return f"<PromptPerformanceDaily(prompt_template_id={self.prompt_template_id}, date={self.date}, extractions={self.total_extractions})>"
