"""add_deterministic_routing_columns_to_incoming_emails

Revision ID: 20260412_1100_det_routing
Revises: 20260412_1000_routing
Create Date: 2026-04-12 11:00:00

Adds deterministic routing columns to incoming_emails for Phase 4:
- to_addresses: Array of recipient addresses (for Reply-To parsing)
- in_reply_to_header: In-Reply-To header value (for Message-ID matching)
- routing_method: Which routing stage produced the match
- routing_id_parsed: The routing ID extracted from the email
- deterministic_match: Whether this email was routed deterministically
- deterministic_confidence: Confidence score from deterministic routing
- deterministic_inquiry_id: Matched inquiry ID from deterministic routing
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = '20260412_1100_det_routing'
down_revision = '20260412_1000_routing'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'incoming_emails',
        sa.Column('to_addresses', ARRAY(sa.String), nullable=True)
    )
    op.add_column(
        'incoming_emails',
        sa.Column('in_reply_to_header', sa.String(500), nullable=True)
    )
    op.add_column(
        'incoming_emails',
        sa.Column('routing_method', sa.String(50), nullable=True)
    )
    op.add_column(
        'incoming_emails',
        sa.Column('routing_id_parsed', sa.String(20), nullable=True)
    )
    op.add_column(
        'incoming_emails',
        sa.Column('deterministic_match', sa.Boolean, nullable=True)
    )
    op.add_column(
        'incoming_emails',
        sa.Column('deterministic_confidence', sa.Float, nullable=True)
    )
    op.add_column(
        'incoming_emails',
        sa.Column('deterministic_inquiry_id', sa.Integer, nullable=True)
    )

    op.create_index('ix_incoming_emails_routing_id_parsed', 'incoming_emails', ['routing_id_parsed'])
    op.create_index('ix_incoming_emails_deterministic_inquiry_id', 'incoming_emails', ['deterministic_inquiry_id'])


def downgrade() -> None:
    op.drop_index('ix_incoming_emails_deterministic_inquiry_id', 'incoming_emails')
    op.drop_index('ix_incoming_emails_routing_id_parsed', 'incoming_emails')
    op.drop_column('incoming_emails', 'deterministic_inquiry_id')
    op.drop_column('incoming_emails', 'deterministic_confidence')
    op.drop_column('incoming_emails', 'deterministic_match')
    op.drop_column('incoming_emails', 'routing_id_parsed')
    op.drop_column('incoming_emails', 'routing_method')
    op.drop_column('incoming_emails', 'in_reply_to_header')
    op.drop_column('incoming_emails', 'to_addresses')
