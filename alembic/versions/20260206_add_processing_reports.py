"""Add processing reports table

Revision ID: 20260206_processing_reports
Revises: 20260206_add_operational_metrics
Create Date: 2026-02-06

Per-email processing audit trail for operational visibility (REQ-OPS-06).
Captures extraction results, confidence scores, and pipeline metadata.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision = '20260206_processing_reports'
down_revision = '20260206_ops_metrics'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create processing_reports table"""
    op.create_table(
        'processing_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email_id', sa.Integer(), nullable=False, comment="Foreign key to incoming_emails table"),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), comment="When this report was created"),
        sa.Column('extracted_fields', JSON(), nullable=False, comment="Extracted fields with per-field confidence and source"),
        sa.Column('missing_fields', JSON(), nullable=True, comment="List of required fields that couldn't be extracted"),
        sa.Column('overall_confidence', sa.Float(), nullable=False, comment="Overall confidence score (0.0-1.0)"),
        sa.Column('confidence_route', sa.String(20), nullable=False, comment="Confidence-based routing: high, medium, low"),
        sa.Column('needs_review', sa.Boolean(), server_default='false', comment="Whether this email was routed to manual review"),
        sa.Column('review_reason', sa.String(100), nullable=True, comment="Reason for manual review routing"),
        sa.Column('intent', sa.String(50), nullable=True, comment="Email intent classification result"),
        sa.Column('sources_processed', sa.Integer(), server_default='1', comment="Number of content sources processed (email body + attachments)"),
        sa.Column('total_tokens_used', sa.Integer(), server_default='0', comment="Total tokens consumed during extraction"),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True, comment="Total processing time in milliseconds"),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['email_id'], ['incoming_emails.id']),
        sa.UniqueConstraint('email_id', name='uq_processing_report_email')
    )

    # Indexes for common queries
    op.create_index('idx_processing_report_created', 'processing_reports', ['created_at'])
    op.create_index('idx_processing_report_needs_review', 'processing_reports', ['needs_review'])


def downgrade() -> None:
    """Drop processing_reports table"""
    op.drop_index('idx_processing_report_needs_review', table_name='processing_reports')
    op.drop_index('idx_processing_report_created', table_name='processing_reports')
    op.drop_table('processing_reports')
