"""mock task number sequence

Task references need to be human readable (KPT-104) and collision free under
concurrent creates. A sequence gives both; deriving the number from a count or
a max would race.

Revision ID: 4363f24e9155
Revises: 6c258d56fe0d
Create Date: 2026-09-01 23:14:12.504336

"""

from collections.abc import Sequence

from alembic import op

revision: str = "4363f24e9155"
down_revision: str | Sequence[str] | None = "6c258d56fe0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS mock_task_number_seq START WITH 101")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS mock_task_number_seq")
