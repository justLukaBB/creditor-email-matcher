"""add_agent_checkpoints

Revision ID: 20260205_1722_add_agent
Revises: 20260204_1705_add_job
Create Date: 2026-02-05 17:22:00

Adds: agent_checkpoints (JSONB column for multi-agent pipeline intermediate results)
Purpose: Store results from Agent 1 (intent), Agent 2 (extraction), Agent 3 (consolidation)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '20260205_1722_add_agent'
down_revision = '20260204_1705_add_job'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add agent_checkpoints JSONB column to incoming_emails table.

    Stores intermediate results from multi-agent pipeline:
    - agent_1_intent: intent classification with confidence
    - agent_2_extraction: content extraction results
    - agent_3_consolidation: final consolidated data
    """
    op.add_column('incoming_emails',
        sa.Column('agent_checkpoints', JSONB, nullable=True,
                  comment='Multi-agent pipeline intermediate results'))


def downgrade() -> None:
    """
    Remove agent_checkpoints column from incoming_emails table.
    """
    op.drop_column('incoming_emails', 'agent_checkpoints')
