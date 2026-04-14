"""add_kanzlei_id_to_incoming_emails

Revision ID: 20260414_1000_kanzlei_iso
Revises: 20260412_1100_det_routing
Create Date: 2026-04-14 10:00:00

Multi-tenant isolation: adds kanzlei_id to incoming_emails so the matching
pipeline can filter candidates to the correct tenant. Extracted from the
reply-to address domain or routing ID prefix at webhook ingestion time.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260414_1000_kanzlei_iso'
down_revision = '20260412_1100_det_routing'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'incoming_emails',
        sa.Column('kanzlei_id', sa.String(50), nullable=True)
    )
    op.create_index('ix_incoming_emails_kanzlei_id', 'incoming_emails', ['kanzlei_id'])


def downgrade() -> None:
    op.drop_index('ix_incoming_emails_kanzlei_id', 'incoming_emails')
    op.drop_column('incoming_emails', 'kanzlei_id')
