"""
MatchingThreshold Model
Stores database-driven configuration for matching engine thresholds and weights
"""

from sqlalchemy import Column, Integer, String, Numeric, Text, DateTime, Index, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base


class MatchingThreshold(Base):
    """
    Database-driven configuration for matching thresholds and weights.

    Enables runtime threshold changes without code deployment.
    Category-based configuration allows different thresholds for different creditor types.

    Examples:
    - ('default', 'min_match', 0.7000): Minimum score for any match consideration
    - ('default', 'gap_threshold', 0.1500): Gap between #1 and #2 for auto-match
    - ('default', 'client_name', 0.4000): Weight for client name signal
    - ('bank', 'min_match', 0.8000): Higher threshold for bank creditors
    """
    __tablename__ = "matching_thresholds"

    # Primary Key
    id = Column(Integer, primary_key=True)

    # Configuration Scope
    category = Column(String(50), nullable=False)  # "default", "bank", "inkasso"
    threshold_type = Column(String(50), nullable=False)  # "min_match", "gap_threshold"
    threshold_value = Column(Numeric(5, 4), nullable=False)  # 0.0000 to 1.0000

    # Weight Configuration (optional, used when threshold_type is a weight)
    weight_name = Column(String(50), nullable=True)  # "client_name", "reference_number"
    weight_value = Column(Numeric(5, 4), nullable=True)

    # Documentation
    description = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Constraints
    __table_args__ = (
        # Ensure unique configuration per category/threshold combination
        UniqueConstraint('category', 'threshold_type', 'weight_name', name='uq_matching_threshold_config'),
        # Efficient lookup by category and type
        Index('idx_matching_thresholds_lookup', 'category', 'threshold_type'),
    )

    def __repr__(self):
        if self.weight_name:
            return f"<MatchingThreshold(category='{self.category}', weight='{self.weight_name}', value={self.weight_value})>"
        return f"<MatchingThreshold(category='{self.category}', type='{self.threshold_type}', value={self.threshold_value})>"
