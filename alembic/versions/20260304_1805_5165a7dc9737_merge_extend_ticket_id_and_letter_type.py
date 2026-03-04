"""merge_extend_ticket_id_and_letter_type

Revision ID: 5165a7dc9737
Revises: 20260208_extend_ticket_id, 20260304_1000_letter_type
Create Date: 2026-03-04 18:05:44.513320

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5165a7dc9737'
down_revision: Union[str, None] = ('20260208_extend_ticket_id', '20260304_1000_letter_type')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
