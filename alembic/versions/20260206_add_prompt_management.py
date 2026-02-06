"""add_prompt_management

Revision ID: 20260206_add_prompt_mgmt
Revises: 20260205_2330
Create Date: 2026-02-06 00:00:00

Adds: prompt_templates, prompt_performance_metrics, prompt_performance_daily tables
Purpose: Database-backed prompt management with versioning and performance tracking
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '20260206_add_prompt_mgmt'
down_revision = '20260205_2330'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create prompt management tables:
    1. prompt_templates - Versioned prompt storage with explicit activation
    2. prompt_performance_metrics - Raw extraction-level metrics (30-day retention)
    3. prompt_performance_daily - Aggregated daily rollups (permanent retention)
    """

    # Create prompt_templates table
    op.create_table(
        'prompt_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_type', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('user_prompt_template', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('model_name', sa.String(length=50), nullable=True, server_default='claude-sonnet-4-5-20250514'),
        sa.Column('temperature', sa.Float(), nullable=True, server_default='0.1'),
        sa.Column('max_tokens', sa.Integer(), nullable=True, server_default='1024'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('version > 0', name='version_positive'),
    )

    # Indexes for prompt_templates
    op.create_index('idx_prompt_templates_id', 'prompt_templates', ['id'])
    op.create_index('idx_prompt_templates_task_type', 'prompt_templates', ['task_type'])
    op.create_index('idx_prompt_templates_created_at', 'prompt_templates', ['created_at'])

    # Partial index for active prompt lookup (only one active per task_type + name)
    op.create_index(
        'idx_prompt_templates_active',
        'prompt_templates',
        ['task_type', 'name'],
        postgresql_where=sa.text('is_active = TRUE')
    )

    # Create prompt_performance_metrics table (raw extraction-level)
    op.create_table(
        'prompt_performance_metrics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('prompt_template_id', sa.Integer(), nullable=False),
        sa.Column('email_id', sa.Integer(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('api_cost_usd', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('extraction_success', sa.Boolean(), nullable=False),
        sa.Column('confidence_score', sa.Float(), nullable=True),
        sa.Column('manual_review_required', sa.Boolean(), nullable=True),
        sa.Column('execution_time_ms', sa.Integer(), nullable=False),
        sa.Column('extracted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['prompt_template_id'], ['prompt_templates.id'], ),
        sa.ForeignKeyConstraint(['email_id'], ['incoming_emails.id'], ),
    )

    # Indexes for prompt_performance_metrics
    op.create_index('idx_prompt_metrics_prompt_id', 'prompt_performance_metrics', ['prompt_template_id'])
    op.create_index('idx_prompt_metrics_extracted_at', 'prompt_performance_metrics', ['extracted_at'])

    # Create prompt_performance_daily table (aggregated rollups)
    op.create_table(
        'prompt_performance_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('prompt_template_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('total_extractions', sa.Integer(), nullable=False),
        sa.Column('total_input_tokens', sa.BigInteger(), nullable=False),
        sa.Column('total_output_tokens', sa.BigInteger(), nullable=False),
        sa.Column('total_api_cost_usd', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('successful_extractions', sa.Integer(), nullable=False),
        sa.Column('avg_confidence_score', sa.Float(), nullable=True),
        sa.Column('manual_review_count', sa.Integer(), nullable=False),
        sa.Column('avg_execution_time_ms', sa.Integer(), nullable=False),
        sa.Column('p95_execution_time_ms', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['prompt_template_id'], ['prompt_templates.id'], ),
    )

    # Unique constraint and indexes for prompt_performance_daily
    op.create_index(
        'idx_prompt_daily_unique',
        'prompt_performance_daily',
        ['prompt_template_id', 'date'],
        unique=True
    )
    op.create_index('idx_prompt_daily_date', 'prompt_performance_daily', ['date'])


def downgrade() -> None:
    """
    Drop prompt management tables in reverse order (daily, metrics, templates).
    """
    # Drop prompt_performance_daily
    op.drop_index('idx_prompt_daily_date', table_name='prompt_performance_daily')
    op.drop_index('idx_prompt_daily_unique', table_name='prompt_performance_daily')
    op.drop_table('prompt_performance_daily')

    # Drop prompt_performance_metrics
    op.drop_index('idx_prompt_metrics_extracted_at', table_name='prompt_performance_metrics')
    op.drop_index('idx_prompt_metrics_prompt_id', table_name='prompt_performance_metrics')
    op.drop_table('prompt_performance_metrics')

    # Drop prompt_templates
    op.drop_index('idx_prompt_templates_active', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_created_at', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_task_type', table_name='prompt_templates')
    op.drop_index('idx_prompt_templates_id', table_name='prompt_templates')
    op.drop_table('prompt_templates')
