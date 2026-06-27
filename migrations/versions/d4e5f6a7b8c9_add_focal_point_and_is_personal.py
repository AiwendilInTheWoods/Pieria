"""Add focal_point (focal_x/focal_y) + is_personal to artworks

Revision ID: d4e5f6a7b8c9
Revises: c3f1a9d27e80
Create Date: 2026-06-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3f1a9d27e80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default backfills existing rows; the ORM Python-side defaults apply to new rows.
    with op.batch_alter_table('artworks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_personal', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
        batch_op.add_column(sa.Column('focal_x', sa.Float(), nullable=False, server_default='0.5'))
        batch_op.add_column(sa.Column('focal_y', sa.Float(), nullable=False, server_default='0.5'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('artworks', schema=None) as batch_op:
        batch_op.drop_column('focal_y')
        batch_op.drop_column('focal_x')
        batch_op.drop_column('is_personal')
