"""separate_cc_bcc_in_incoming_emails

Revision ID: 20260526_1000_cc_bcc
Revises: 20260424_1000_routing_v2
Create Date: 2026-05-26 10:00:00

Phase 5.2 (Email-Vollarchiv):

Today the inbound Resend webhook handler merges every recipient (To + CC) into
the single `to_addresses` ARRAY column, so we lose the To/CC distinction once
the row hits Postgres. The portal-side full archive needs CC/BCC visible
separately for compliance (legal CCs to Sekretariat, etc.) and for the
deterministic routing audit.

This migration adds the two columns as additive, nullable arrays. New writes
fill them. Historical rows keep empty/null values — backfill is impossible
since the original To/CC split has already been lost upstream.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


revision = '20260526_1000_cc_bcc'
down_revision = '20260424_1000_routing_v2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'incoming_emails',
        sa.Column('cc_addresses', ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        'incoming_emails',
        sa.Column('bcc_addresses', ARRAY(sa.String()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('incoming_emails', 'bcc_addresses')
    op.drop_column('incoming_emails', 'cc_addresses')
