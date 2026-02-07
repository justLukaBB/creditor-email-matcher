"""add_saga_infrastructure

Revision ID: 20260204_1549_add_saga
Revises: None
Create Date: 2026-02-04 15:49:04

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260204_1549_add_saga'
down_revision = '20260107_1733_381db1c8de34'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create saga infrastructure tables and extend incoming_emails with sync tracking.
    """
    # Create outbox_messages table
    op.create_table(
        'outbox_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('aggregate_type', sa.String(length=100), nullable=False),
        sa.Column('aggregate_id', sa.String(length=255), nullable=False),
        sa.Column('operation', sa.String(length=50), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('max_retries', sa.Integer(), server_default='5', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key')
    )
    op.create_index('ix_outbox_unprocessed', 'outbox_messages', ['processed_at', 'retry_count'])
    op.create_index('ix_outbox_created_at', 'outbox_messages', ['created_at'])

    # Create idempotency_keys table
    op.create_table(
        'idempotency_keys',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key')
    )
    op.create_index('ix_idempotency_keys_key', 'idempotency_keys', ['key'], unique=True)
    op.create_index('ix_idempotency_expires_at', 'idempotency_keys', ['expires_at'])

    # Create reconciliation_reports table
    op.create_table(
        'reconciliation_reports',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('records_checked', sa.Integer(), server_default='0', nullable=False),
        sa.Column('mismatches_found', sa.Integer(), server_default='0', nullable=False),
        sa.Column('auto_repaired', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed_repairs', sa.Integer(), server_default='0', nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=50), server_default='running', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Extend incoming_emails table with sync tracking columns
    op.add_column('incoming_emails', sa.Column('sync_status', sa.String(length=50), server_default='pending', nullable=False))
    op.add_column('incoming_emails', sa.Column('sync_error', sa.Text(), nullable=True))
    op.add_column('incoming_emails', sa.Column('sync_retry_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('incoming_emails', sa.Column('idempotency_key', sa.String(length=255), nullable=True))

    # Add unique constraint on incoming_emails.idempotency_key
    op.create_unique_constraint('uq_incoming_emails_idempotency_key', 'incoming_emails', ['idempotency_key'])


def downgrade() -> None:
    """
    Drop saga infrastructure tables and remove sync tracking columns from incoming_emails.
    """
    # Remove sync tracking columns from incoming_emails
    op.drop_constraint('uq_incoming_emails_idempotency_key', 'incoming_emails', type_='unique')
    op.drop_column('incoming_emails', 'idempotency_key')
    op.drop_column('incoming_emails', 'sync_retry_count')
    op.drop_column('incoming_emails', 'sync_error')
    op.drop_column('incoming_emails', 'sync_status')

    # Drop reconciliation_reports table
    op.drop_table('reconciliation_reports')

    # Drop idempotency_keys table
    op.drop_index('ix_idempotency_expires_at', table_name='idempotency_keys')
    op.drop_index('ix_idempotency_keys_key', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')

    # Drop outbox_messages table
    op.drop_index('ix_outbox_created_at', table_name='outbox_messages')
    op.drop_index('ix_outbox_unprocessed', table_name='outbox_messages')
    op.drop_table('outbox_messages')
