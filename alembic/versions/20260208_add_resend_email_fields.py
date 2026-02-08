"""Add resend_email_id and email_provider to creditor_inquiries

Revision ID: 20260208_resend_fields
Revises: 20260206_processing_reports
Create Date: 2026-02-08

Adds support for Resend email provider as alternative to Zendesk Side Conversations.
- resend_email_id: Stores the Resend message ID for tracking
- email_provider: Indicates which provider sent the email ('zendesk' or 'resend')
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260208_resend_fields'
down_revision = '20260206_processing_reports'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add resend email fields to creditor_inquiries"""
    op.add_column(
        'creditor_inquiries',
        sa.Column('resend_email_id', sa.String(100), nullable=True)
    )
    op.add_column(
        'creditor_inquiries',
        sa.Column('email_provider', sa.String(20), server_default='zendesk', nullable=False)
    )

    # Index for looking up by resend_email_id
    op.create_index(
        'idx_creditor_inquiries_resend_email_id',
        'creditor_inquiries',
        ['resend_email_id']
    )


def downgrade() -> None:
    """Remove resend email fields from creditor_inquiries"""
    op.drop_index('idx_creditor_inquiries_resend_email_id', table_name='creditor_inquiries')
    op.drop_column('creditor_inquiries', 'email_provider')
    op.drop_column('creditor_inquiries', 'resend_email_id')
