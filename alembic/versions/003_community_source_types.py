"""community source types and user_submitted created_by

Revision ID: 003
Revises: 002
Create Date: 2026-06-22

"""

from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'reddit'")
    op.execute("ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'community_forum'")
    op.execute("ALTER TYPE created_by ADD VALUE IF NOT EXISTS 'user_submitted'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely.
    pass
