"""extend_zendesk_ticket_id_column

Revision ID: 20260208_extend_ticket_id
Revises: 20260208_add_resend_email_fields
Create Date: 2026-02-08 17:30:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260208_extend_ticket_id'
down_revision = '20260208_add_resend_email_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Extend zendesk_ticket_id column from 50 to 255 characters.

    Email message_id values (used when integrating with Resend) can be
    longer than 50 characters, e.g.:
    <CAD0UqpBqqNB7qy2OdRBc2-nDDnVue-BW3LWTaA8Q283V1iFXNQ@mail.gmail.com>
    """
    op.alter_column(
        'incoming_emails',
        'zendesk_ticket_id',
        type_=sa.String(255),
        existing_type=sa.String(50),
        existing_nullable=False
    )


def downgrade() -> None:
    """
    Revert zendesk_ticket_id column to 50 characters.
    Note: This may fail if existing data exceeds 50 characters.
    """
    op.alter_column(
        'incoming_emails',
        'zendesk_ticket_id',
        type_=sa.String(50),
        existing_type=sa.String(255),
        existing_nullable=False
    )
