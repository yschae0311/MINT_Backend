"""widen sources.source_type for community_forum

Revision ID: 005
Revises: 004
Create Date: 2026-06-23

"""

from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE sources ALTER COLUMN source_type TYPE VARCHAR(32)")


def downgrade() -> None:
    op.execute("ALTER TABLE sources ALTER COLUMN source_type TYPE VARCHAR(11)")
