"""add_manual_review_queue_table

Revision ID: 20260205_1829_add_manual
Revises: 20260205_1722_add_agent
Create Date: 2026-02-05 18:29:00

Adds: manual_review_queue table for human review workflow
Purpose: Store items flagged with low confidence or conflicts for manual review
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '20260205_1829_add_manual'
down_revision = '20260205_1722_add_agent'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create manual_review_queue table for tracking items requiring human review.

    Features:
    - Priority-based queue ordering
    - Claim tracking with FOR UPDATE SKIP LOCKED concurrency
    - Resolution tracking with notes
    - JSONB for flexible review details
    """
    op.create_table(
        'manual_review_queue',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email_id', sa.Integer(), nullable=False),
        sa.Column('review_reason', sa.String(length=100), nullable=False),
        sa.Column('review_details', JSONB, nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claimed_by', sa.String(length=255), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution', sa.String(length=50), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['email_id'], ['incoming_emails.id'], ),
    )

    # Create indexes for efficient queue queries
    op.create_index('idx_manual_review_queue_email_id', 'manual_review_queue', ['email_id'])
    op.create_index('idx_manual_review_queue_id', 'manual_review_queue', ['id'])

    # Partial index for pending items (most common query)
    op.execute("""
        CREATE INDEX idx_manual_review_queue_pending
        ON manual_review_queue (resolved_at, priority, created_at)
        WHERE resolved_at IS NULL
    """)

    # Partial index for claimed but unresolved items
    op.execute("""
        CREATE INDEX idx_manual_review_queue_claimed
        ON manual_review_queue (claimed_at, resolved_at)
        WHERE claimed_at IS NOT NULL AND resolved_at IS NULL
    """)


def downgrade() -> None:
    """
    Drop manual_review_queue table and all indexes.
    """
    op.drop_index('idx_manual_review_queue_claimed', table_name='manual_review_queue')
    op.drop_index('idx_manual_review_queue_pending', table_name='manual_review_queue')
    op.drop_index('idx_manual_review_queue_id', table_name='manual_review_queue')
    op.drop_index('idx_manual_review_queue_email_id', table_name='manual_review_queue')
    op.drop_table('manual_review_queue')
