"""add_letter_type_to_creditor_inquiries

Revision ID: 20260304_1000_letter_type
Revises: 20260205_1829_add_manual
Create Date: 2026-03-04 10:00:00

Adds: letter_type column to creditor_inquiries (first/second)
Purpose: Distinguish 1. Schreiben from 2. Schreiben (Schuldenbereinigungsplan)
"""
from alembic import op
import sqlalchemy as sa

revision = '20260304_1000_letter_type'
down_revision = '20260205_1829_add_manual'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'creditor_inquiries',
        sa.Column('letter_type', sa.String(20), nullable=False, server_default='first')
    )


def downgrade() -> None:
    op.drop_column('creditor_inquiries', 'letter_type')
