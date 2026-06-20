"""Add source_url + thumbnail_url to artworks (catalog provenance)

Revision ID: b1c7e2a4f9d0
Revises: 9918fa64b736
Create Date: 2026-06-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c7e2a4f9d0'
down_revision: Union[str, Sequence[str], None] = '9918fa64b736'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('artworks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_url', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('thumbnail_url', sa.String(), nullable=True))
        batch_op.create_index(batch_op.f('ix_artworks_source_url'), ['source_url'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('artworks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_artworks_source_url'))
        batch_op.drop_column('thumbnail_url')
        batch_op.drop_column('source_url')
