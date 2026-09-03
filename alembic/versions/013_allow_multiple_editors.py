"""Allow multiple editors per edition.

Revision ID: 013
Revises: 012
Create Date: 2026-09-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_user_editions_one_editor", table_name="user_editions")


def downgrade() -> None:
    op.create_index(
        "uq_user_editions_one_editor",
        "user_editions",
        ["edition_id"],
        unique=True,
        postgresql_where=sa.text("is_editor IS TRUE"),
        sqlite_where=sa.text("is_editor = 1"),
    )
