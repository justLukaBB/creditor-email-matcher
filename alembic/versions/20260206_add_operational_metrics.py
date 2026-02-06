"""add_operational_metrics

Revision ID: 20260206_ops_metrics
Revises: 20260206_add_prompt_mgmt
Create Date: 2026-02-06 17:00:00

Adds: operational_metrics, operational_metrics_daily tables
Purpose: Track pipeline health (queue depth, processing time, errors, token usage, confidence)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

# revision identifiers, used by Alembic.
revision = '20260206_ops_metrics'
down_revision = '20260206_add_prompt_mgmt'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create operational metrics tables:
    1. operational_metrics - Raw metrics with 30-day retention
    2. operational_metrics_daily - Daily aggregated rollups (permanent retention)
    """

    # Create operational_metrics table (raw metrics, 30-day retention)
    op.create_table(
        'operational_metrics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('metric_type', sa.String(50), nullable=False),
        sa.Column('metric_value', sa.Float(), nullable=False),
        sa.Column('labels', JSON(), nullable=True),
        sa.Column('email_id', sa.Integer(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['email_id'], ['incoming_emails.id'], ondelete='SET NULL')
    )
    op.create_index('idx_ops_metrics_type', 'operational_metrics', ['metric_type'])
    op.create_index('idx_ops_metrics_recorded', 'operational_metrics', ['recorded_at'])

    # Create operational_metrics_daily table (daily rollup, permanent retention)
    op.create_table(
        'operational_metrics_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('metric_type', sa.String(50), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('labels_key', sa.String(100), nullable=True),
        sa.Column('sample_count', sa.Integer(), nullable=False),
        sa.Column('sum_value', sa.Float(), nullable=False),
        sa.Column('avg_value', sa.Float(), nullable=False),
        sa.Column('min_value', sa.Float(), nullable=False),
        sa.Column('max_value', sa.Float(), nullable=False),
        sa.Column('p95_value', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_ops_daily_unique', 'operational_metrics_daily',
                   ['metric_type', 'date', 'labels_key'], unique=True)


def downgrade() -> None:
    """
    Drop operational metrics tables.
    """
    op.drop_table('operational_metrics_daily')
    op.drop_table('operational_metrics')
