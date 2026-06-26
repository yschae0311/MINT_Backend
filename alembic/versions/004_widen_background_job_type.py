"""widen background_jobs.job_type for community_discovery_pipeline

Revision ID: 004
Revises: 003
Create Date: 2026-06-22

"""

from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE background_jobs ALTER COLUMN job_type TYPE VARCHAR(32)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE background_jobs ALTER COLUMN job_type TYPE VARCHAR(22)")
