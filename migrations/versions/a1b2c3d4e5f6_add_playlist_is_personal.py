"""Add is_personal to playlists (personal My-Photos albums vs Museum collections)

Revision ID: a1b2c3d4e5f6
Revises: f2a3b4c5d6e7
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add playlists.is_personal + backfill existing personal albums.

    Defensive: boot-time create_all may already have created the column on a fresh DB, which would make a
    plain add_column raise 'duplicate column'. So add it only if it's missing (see the create_all/Alembic
    drift lesson). The backfill then always runs so pre-existing DBs get their personal albums marked."""
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("playlists")}
    if "is_personal" not in cols:
        with op.batch_alter_table("playlists", schema=None) as batch_op:
            batch_op.add_column(sa.Column("is_personal", sa.Boolean(), nullable=False,
                                          server_default=sa.false()))

    # Mark the "My Photos" default and any playlist that already holds a personal photo.
    op.execute("""
        UPDATE playlists SET is_personal = 1
        WHERE name = 'My Photos'
           OR id IN (
               SELECT DISTINCT pa.playlist_id
               FROM playlist_artwork pa
               JOIN artworks a ON a.id = pa.artwork_id
               WHERE a.is_personal = 1
           )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("playlists", schema=None) as batch_op:
        batch_op.drop_column("is_personal")
