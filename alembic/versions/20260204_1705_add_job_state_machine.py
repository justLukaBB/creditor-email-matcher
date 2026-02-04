"""add_job_state_machine

Revision ID: 20260204_1705_add_job
Revises: 20260204_1549_add_saga
Create Date: 2026-02-04 17:05:00

Adds: started_at, completed_at (timestamps for job lifecycle tracking)
Adds: retry_count (Dramatiq retry tracking)
Adds: attachment_urls (Zendesk attachment URLs for Phase 3)
Adds: index on (processing_status, received_at) for job queue queries
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260204_1705_add_job'
down_revision = '20260204_1549_add_saga'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add job state machine fields and attachment URLs to incoming_emails table.
    """
    # Add job state machine columns
    op.add_column('incoming_emails',
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('incoming_emails',
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('incoming_emails',
        sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('incoming_emails',
        sa.Column('attachment_urls', sa.JSON(), nullable=True))

    # Index for efficient job status queries (worker polling, status API)
    op.create_index(
        'ix_incoming_emails_status_received',
        'incoming_emails',
        ['processing_status', 'received_at']
    )


def downgrade() -> None:
    """
    Remove job state machine fields and attachment URLs from incoming_emails table.
    """
    op.drop_index('ix_incoming_emails_status_received', 'incoming_emails')
    op.drop_column('incoming_emails', 'attachment_urls')
    op.drop_column('incoming_emails', 'retry_count')
    op.drop_column('incoming_emails', 'completed_at')
    op.drop_column('incoming_emails', 'started_at')
