"""add_routing_fields_to_creditor_inquiries

Revision ID: 20260412_1000_routing
Revises: 5165a7dc9737
Create Date: 2026-04-12 10:00:00

Adds deterministic routing fields for Phase 3 multi-tenant email routing:
- routing_id: Encoded routing ID (e.g. SC-A1221-42)
- resend_message_id: Message-ID header for In-Reply-To matching
- kanzlei_id: Tenant identifier
- kanzlei_prefix: 2-3 char tenant prefix used in routing
"""
from alembic import op
import sqlalchemy as sa

revision = '20260412_1000_routing'
down_revision = '5165a7dc9737'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'creditor_inquiries',
        sa.Column('routing_id', sa.String(20), nullable=True)
    )
    op.add_column(
        'creditor_inquiries',
        sa.Column('resend_message_id', sa.String(500), nullable=True)
    )
    op.add_column(
        'creditor_inquiries',
        sa.Column('kanzlei_id', sa.String(50), nullable=True)
    )
    op.add_column(
        'creditor_inquiries',
        sa.Column('kanzlei_prefix', sa.String(3), nullable=True)
    )

    # Indexes for fast lookup in deterministic routing pipeline
    op.create_index('ix_creditor_inquiries_routing_id', 'creditor_inquiries', ['routing_id'])
    op.create_index('ix_creditor_inquiries_resend_message_id', 'creditor_inquiries', ['resend_message_id'])
    op.create_index('ix_creditor_inquiries_kanzlei_id', 'creditor_inquiries', ['kanzlei_id'])


def downgrade() -> None:
    op.drop_index('ix_creditor_inquiries_kanzlei_id', 'creditor_inquiries')
    op.drop_index('ix_creditor_inquiries_resend_message_id', 'creditor_inquiries')
    op.drop_index('ix_creditor_inquiries_routing_id', 'creditor_inquiries')
    op.drop_column('creditor_inquiries', 'kanzlei_prefix')
    op.drop_column('creditor_inquiries', 'kanzlei_id')
    op.drop_column('creditor_inquiries', 'resend_message_id')
    op.drop_column('creditor_inquiries', 'routing_id')
