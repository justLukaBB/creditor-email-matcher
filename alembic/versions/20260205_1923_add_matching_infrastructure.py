"""add_matching_infrastructure

Revision ID: 20260205_1923_add_matching
Revises: 20260205_1829_add_manual
Create Date: 2026-02-05 19:23:00

Adds: matching_thresholds, match_results tables, verifies creditor_inquiries
Purpose: Database infrastructure for matching engine reconstruction
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '20260205_1923_add_matching'
down_revision = '20260205_1829_add_manual'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create matching infrastructure tables and default thresholds.

    Tables:
    - matching_thresholds: Database-driven threshold and weight configuration
    - match_results: Match scoring with JSONB explainability
    - creditor_inquiries: Verify existence (created by Node.js portal)
    """

    # Create matching_thresholds table
    op.create_table(
        'matching_thresholds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('threshold_type', sa.String(length=50), nullable=False),
        sa.Column('threshold_value', sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column('weight_name', sa.String(length=50), nullable=True),
        sa.Column('weight_value', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('category', 'threshold_type', 'weight_name', name='uq_matching_threshold_config'),
    )

    # Create efficient lookup index
    op.create_index(
        'idx_matching_thresholds_lookup',
        'matching_thresholds',
        ['category', 'threshold_type'],
    )

    # Create match_results table
    op.create_table(
        'match_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('incoming_email_id', sa.Integer(), nullable=False),
        sa.Column('creditor_inquiry_id', sa.Integer(), nullable=False),
        sa.Column('total_score', sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column('confidence_level', sa.String(length=20), nullable=False),
        sa.Column('client_name_score', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('creditor_score', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('time_relevance_score', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('reference_number_score', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('debt_amount_score', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('scoring_details', JSONB, nullable=True),
        sa.Column('ambiguity_gap', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('rank', sa.Integer(), nullable=True),
        sa.Column('selected_as_match', sa.Boolean(), nullable=True),
        sa.Column('selection_method', sa.String(length=50), nullable=True),
        sa.Column('calculated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['incoming_email_id'], ['incoming_emails.id'], ),
        sa.ForeignKeyConstraint(['creditor_inquiry_id'], ['creditor_inquiries.id'], ),
    )

    # Create indexes on match_results
    op.create_index('idx_match_results_email_id', 'match_results', ['incoming_email_id'])
    op.create_index('idx_match_results_id', 'match_results', ['id'])
    op.create_index('idx_match_results_creditor_inquiry_id', 'match_results', ['creditor_inquiry_id'])

    # Partial index for selected matches
    op.execute("""
        CREATE INDEX idx_match_results_selected
        ON match_results (selected_as_match, calculated_at)
        WHERE selected_as_match = true
    """)

    # Create creditor_inquiries table (defensive - should already exist from Node.js portal)
    # Check if table exists first
    op.execute("""
        CREATE TABLE IF NOT EXISTS creditor_inquiries (
            id SERIAL PRIMARY KEY,
            client_name VARCHAR(255) NOT NULL,
            client_name_normalized VARCHAR(255),
            creditor_name VARCHAR(255) NOT NULL,
            creditor_email VARCHAR(255) NOT NULL,
            creditor_name_normalized VARCHAR(255),
            debt_amount NUMERIC(10, 2),
            reference_number VARCHAR(100),
            zendesk_ticket_id VARCHAR(50) NOT NULL,
            zendesk_side_conversation_id VARCHAR(50),
            email_subject VARCHAR(500),
            email_body TEXT,
            status VARCHAR(50) DEFAULT 'sent',
            response_received BOOLEAN DEFAULT false,
            sent_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """)

    # Create indexes on creditor_inquiries if they don't exist
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_id
        ON creditor_inquiries (id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_client_name
        ON creditor_inquiries (client_name)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_client_name_normalized
        ON creditor_inquiries (client_name_normalized)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_creditor_name
        ON creditor_inquiries (creditor_name)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_creditor_email
        ON creditor_inquiries (creditor_email)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_creditor_name_normalized
        ON creditor_inquiries (creditor_name_normalized)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_reference_number
        ON creditor_inquiries (reference_number)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_zendesk_ticket_id
        ON creditor_inquiries (zendesk_ticket_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_creditor_inquiries_zendesk_side_conversation_id
        ON creditor_inquiries (zendesk_side_conversation_id)
    """)

    # Insert default threshold configuration
    # CONTEXT.MD: Default thresholds (40% name, 60% reference)
    op.execute("""
        INSERT INTO matching_thresholds (category, threshold_type, threshold_value, description)
        VALUES
            ('default', 'min_match', 0.7000, 'Minimum score for any match consideration'),
            ('default', 'gap_threshold', 0.1500, 'Gap between #1 and #2 for auto-match')
    """)

    op.execute("""
        INSERT INTO matching_thresholds (category, threshold_type, threshold_value, weight_name, weight_value, description)
        VALUES
            ('default', 'weight', 0.0000, 'client_name', 0.4000, 'Weight for client name signal'),
            ('default', 'weight', 0.0000, 'reference_number', 0.6000, 'Weight for reference number signal')
    """)


def downgrade() -> None:
    """
    Drop matching infrastructure tables and indexes.

    Note: Does NOT drop creditor_inquiries (owned by Node.js portal)
    """
    # Drop match_results
    op.drop_index('idx_match_results_selected', table_name='match_results')
    op.drop_index('idx_match_results_creditor_inquiry_id', table_name='match_results')
    op.drop_index('idx_match_results_id', table_name='match_results')
    op.drop_index('idx_match_results_email_id', table_name='match_results')
    op.drop_table('match_results')

    # Drop matching_thresholds
    op.drop_index('idx_matching_thresholds_lookup', table_name='matching_thresholds')
    op.drop_table('matching_thresholds')

    # Note: We do NOT drop creditor_inquiries as it's owned by the Node.js portal
