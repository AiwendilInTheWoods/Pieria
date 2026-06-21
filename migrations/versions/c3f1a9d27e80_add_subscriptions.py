"""Add subscriptions table (federated Manifest v2 collections)

Revision ID: c3f1a9d27e80
Revises: b1c7e2a4f9d0
Create Date: 2026-06-21 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3f1a9d27e80'
down_revision: Union[str, Sequence[str], None] = 'b1c7e2a4f9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('collection_id', sa.String(), nullable=True),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('publisher_id', sa.String(), nullable=True),
        sa.Column('publisher_name', sa.String(), nullable=True),
        sa.Column('publisher_url', sa.String(), nullable=True),
        sa.Column('trust', sa.String(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('cached_manifest', sa.Text(), nullable=True),
        sa.Column('item_count', sa.Integer(), nullable=True),
        sa.Column('last_synced', sa.DateTime(), nullable=True),
        sa.Column('last_status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_subscriptions_id'), 'subscriptions', ['id'], unique=False)
    op.create_index(op.f('ix_subscriptions_url'), 'subscriptions', ['url'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_subscriptions_url'), table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
