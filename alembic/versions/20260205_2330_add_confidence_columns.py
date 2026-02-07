"""Add confidence scoring columns to incoming_emails

Revision ID: 20260205_2330
Revises: 20260205_2300
Create Date: 2026-02-05
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260205_2330'
down_revision = '20260205_2300_add_calibration'  # After calibration_samples
branch_labels = None
depends_on = None


def upgrade():
    # Add confidence dimension columns
    op.add_column(
        'incoming_emails',
        sa.Column('extraction_confidence', sa.Integer(), nullable=True)
    )
    op.add_column(
        'incoming_emails',
        sa.Column('overall_confidence', sa.Integer(), nullable=True)
    )
    op.add_column(
        'incoming_emails',
        sa.Column('confidence_route', sa.String(20), nullable=True)
    )

    # Index for confidence-based queries (e.g., find all LOW confidence items)
    op.create_index(
        'idx_incoming_emails_confidence_route',
        'incoming_emails',
        ['confidence_route', 'created_at']
    )


def downgrade():
    op.drop_index('idx_incoming_emails_confidence_route', 'incoming_emails')
    op.drop_column('incoming_emails', 'confidence_route')
    op.drop_column('incoming_emails', 'overall_confidence')
    op.drop_column('incoming_emails', 'extraction_confidence')
