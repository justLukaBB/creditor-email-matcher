"""add_calibration_samples

Revision ID: 20260205_2300_add_calibration
Revises: 20260205_1923_add_matching
Create Date: 2026-02-05 23:00:00

Adds: calibration_samples table
Purpose: Store labeled examples from reviewer corrections for threshold calibration
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '20260205_2300_add_calibration'
down_revision = '20260205_1923_add_matching'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create calibration_samples table for confidence threshold tuning.

    Table stores:
    - Predicted confidence scores (overall + dimensions)
    - Ground truth labels from reviewer corrections
    - Correction details for analysis
    - Confidence bucket categorization for threshold adjustment
    """

    # Create calibration_samples table
    op.create_table(
        'calibration_samples',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email_id', sa.Integer(), nullable=False),
        sa.Column('review_id', sa.Integer(), nullable=True),
        sa.Column('predicted_confidence', sa.Float(), nullable=False),
        sa.Column('extraction_confidence', sa.Float(), nullable=True),
        sa.Column('match_confidence', sa.Float(), nullable=True),
        sa.Column('document_type', sa.String(length=50), nullable=True),
        sa.Column('was_correct', sa.Boolean(), nullable=False),
        sa.Column('correction_type', sa.String(length=50), nullable=True),
        sa.Column('correction_details', JSONB, nullable=True),
        sa.Column('confidence_bucket', sa.String(length=20), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['email_id'], ['incoming_emails.id'], ),
        sa.ForeignKeyConstraint(['review_id'], ['manual_review_queue.id'], ),
    )

    # Create indexes for efficient querying
    op.create_index('idx_calibration_samples_email_id', 'calibration_samples', ['email_id'])
    op.create_index('idx_calibration_samples_review_id', 'calibration_samples', ['review_id'])
    op.create_index('idx_calibration_samples_confidence_bucket', 'calibration_samples', ['confidence_bucket'])
    op.create_index('idx_calibration_samples_captured_at', 'calibration_samples', ['captured_at'])

    # Composite index for threshold analysis queries (bucket + correctness)
    op.create_index(
        'idx_calibration_samples_bucket_correct',
        'calibration_samples',
        ['confidence_bucket', 'was_correct']
    )


def downgrade() -> None:
    """
    Drop calibration_samples table and indexes.
    """
    # Drop indexes
    op.drop_index('idx_calibration_samples_bucket_correct', table_name='calibration_samples')
    op.drop_index('idx_calibration_samples_captured_at', table_name='calibration_samples')
    op.drop_index('idx_calibration_samples_confidence_bucket', table_name='calibration_samples')
    op.drop_index('idx_calibration_samples_review_id', table_name='calibration_samples')
    op.drop_index('idx_calibration_samples_email_id', table_name='calibration_samples')

    # Drop table
    op.drop_table('calibration_samples')
