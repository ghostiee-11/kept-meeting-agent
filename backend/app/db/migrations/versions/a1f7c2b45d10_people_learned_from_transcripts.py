"""People learned from transcripts

A roster that only ever comes from a seed script means anybody who joins the
company is invisible to the system until somebody edits code. `source` records
where a person came from, so a participant the system enrolled from a meeting
can be shown as exactly that rather than passed off as verified roster data.

Revision ID: a1f7c2b45d10
Revises: c36697a96994
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f7c2b45d10"
down_revision: str | Sequence[str] | None = "c36697a96994"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "people",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="roster",
        ),
    )
    op.add_column("people", sa.Column("first_seen_meeting_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "people_first_seen_meeting_id_fkey",
        "people",
        "meetings",
        ["first_seen_meeting_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("people_first_seen_meeting_id_fkey", "people", type_="foreignkey")
    op.drop_column("people", "first_seen_meeting_id")
    op.drop_column("people", "source")
