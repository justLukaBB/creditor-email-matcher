"""add routing_id_v2 columns to creditor_inquiries

Revision ID: 20260424_1000_routing_v2
Revises: 20260414_1000_kanzlei_iso
Create Date: 2026-04-24 10:00:00

Adds columns to support V2 routing ID format (SC-00-1-a3f2-k7p):
- routing_id_version: 'v1' or 'v2'
- creditor_idx_snapshot: frozen creditor array index (anti-reorder protection)
- client_hash: 4-char base36 hash from V2 ID (fast-lookup index)

Also widens routing_id column from String(20) to String(40) to fit V2 format.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260424_1000_routing_v2'
down_revision = '20260414_1000_kanzlei_iso'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen routing_id to fit V2 format (max ~20 chars, but leave headroom)
    op.alter_column(
        'creditor_inquiries',
        'routing_id',
        existing_type=sa.String(20),
        type_=sa.String(40),
        existing_nullable=True,
    )

    op.add_column(
        'creditor_inquiries',
        sa.Column('routing_id_version', sa.String(4), nullable=True),
    )
    op.add_column(
        'creditor_inquiries',
        sa.Column('creditor_idx_snapshot', sa.Integer, nullable=True),
    )
    op.add_column(
        'creditor_inquiries',
        sa.Column('client_hash', sa.String(4), nullable=True),
    )

    op.create_index(
        'ix_creditor_inquiries_routing_id_version',
        'creditor_inquiries',
        ['routing_id_version'],
    )
    op.create_index(
        'ix_creditor_inquiries_creditor_idx_snapshot',
        'creditor_inquiries',
        ['creditor_idx_snapshot'],
    )
    op.create_index(
        'ix_creditor_inquiries_client_hash',
        'creditor_inquiries',
        ['client_hash'],
    )
    # Composite index for V2 lookup: (kanzlei_prefix, creditor_idx_snapshot, letter_type, client_hash)
    op.create_index(
        'ix_creditor_inquiries_v2_lookup',
        'creditor_inquiries',
        ['kanzlei_prefix', 'creditor_idx_snapshot', 'letter_type', 'client_hash'],
    )


def downgrade() -> None:
    op.drop_index('ix_creditor_inquiries_v2_lookup', 'creditor_inquiries')
    op.drop_index('ix_creditor_inquiries_client_hash', 'creditor_inquiries')
    op.drop_index('ix_creditor_inquiries_creditor_idx_snapshot', 'creditor_inquiries')
    op.drop_index('ix_creditor_inquiries_routing_id_version', 'creditor_inquiries')

    op.drop_column('creditor_inquiries', 'client_hash')
    op.drop_column('creditor_inquiries', 'creditor_idx_snapshot')
    op.drop_column('creditor_inquiries', 'routing_id_version')

    op.alter_column(
        'creditor_inquiries',
        'routing_id',
        existing_type=sa.String(40),
        type_=sa.String(20),
        existing_nullable=True,
    )
