"""initial schema

Revision ID: 6c258d56fe0d
Revises:
Create Date: 2026-09-01 22:14:13.697827

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6c258d56fe0d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # pgvector backs the Historian's cross-meeting matching. Neon ships the
    # extension, but it still has to be enabled per database.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "search_cache",
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("query_hash", name=op.f("pk_search_cache")),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )
    op.create_table(
        "meetings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("project", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("raw_transcript", sa.Text(), nullable=False),
        sa.Column("transcript_sha256", sa.String(length=64), nullable=False),
        sa.Column("participants", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("turns", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("injection_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_meetings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meetings")),
        sa.UniqueConstraint(
            "workspace_id", "transcript_sha256", name=op.f("uq_meetings_workspace_id")
        ),
    )
    op.create_index(op.f("ix_meetings_workspace_id"), "meetings", ["workspace_id"], unique=False)
    op.create_table(
        "people",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("aliases", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column("team", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_people_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_people")),
        sa.UniqueConstraint("workspace_id", "email", name=op.f("uq_people_workspace_id")),
    )
    op.create_index(op.f("ix_people_workspace_id"), "people", ["workspace_id"], unique=False)
    op.create_table(
        "commitments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_key", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "kind", sa.Enum("commitment", "action_item", name="commitment_kind"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "extracted",
                "needs_clarification",
                "confirmed",
                "in_progress",
                "done",
                "dropped",
                name="commitment_status",
            ),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("owner_confidence", sa.Float(), nullable=False),
        sa.Column("owner_inference_reason", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("original_due_date", sa.Date(), nullable=True),
        sa.Column("due_confidence", sa.Float(), nullable=False),
        sa.Column("due_raw_text", sa.String(length=255), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("risk_factors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("first_seen_meeting_id", sa.Uuid(), nullable=True),
        sa.Column("last_seen_meeting_id", sa.Uuid(), nullable=True),
        sa.Column("slip_count", sa.Integer(), nullable=False),
        sa.Column("silence_streak", sa.Integer(), nullable=False),
        sa.Column("blocked_by", sa.Text(), nullable=True),
        sa.Column("external_task_id", sa.String(length=64), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "due_confidence >= 0 AND due_confidence <= 1",
            name=op.f("ck_commitments_due_confidence_range"),
        ),
        sa.CheckConstraint(
            "owner_confidence >= 0 AND owner_confidence <= 1",
            name=op.f("ck_commitments_owner_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_meeting_id"],
            ["meetings.id"],
            name=op.f("fk_commitments_first_seen_meeting_id_meetings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_meeting_id"],
            ["meetings.id"],
            name=op.f("fk_commitments_last_seen_meeting_id_meetings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["people.id"],
            name=op.f("fk_commitments_owner_id_people"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_commitments_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commitments")),
    )
    op.create_index(
        op.f("ix_commitments_canonical_key"), "commitments", ["canonical_key"], unique=False
    )
    op.create_index(op.f("ix_commitments_due_date"), "commitments", ["due_date"], unique=False)
    op.create_index(
        op.f("ix_commitments_external_task_id"), "commitments", ["external_task_id"], unique=False
    )
    op.create_index(op.f("ix_commitments_owner_id"), "commitments", ["owner_id"], unique=False)
    op.create_index(
        op.f("ix_commitments_workspace_id"), "commitments", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_commitments_workspace_status", "commitments", ["workspace_id", "status"], unique=False
    )
    op.create_table(
        "communications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("recap_email", "owner_nudge", "digest", name="communication_kind"),
            nullable=False,
        ),
        sa.Column("recipient", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_communications_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_communications")),
    )
    op.create_index(
        op.f("ix_communications_meeting_id"), "communications", ["meeting_id"], unique=False
    )
    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("alternatives_considered", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("enrichment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_decisions_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decisions")),
    )
    op.create_index(op.f("ix_decisions_meeting_id"), "decisions", ["meeting_id"], unique=False)
    op.create_table(
        "rejections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("candidate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rejected_by", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_rejections_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rejections")),
    )
    op.create_index(op.f("ix_rejections_created_at"), "rejections", ["created_at"], unique=False)
    op.create_index(op.f("ix_rejections_meeting_id"), "rejections", ["meeting_id"], unique=False)
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("running", "awaiting_human", "succeeded", "failed", name="run_status"),
            nullable=False,
        ),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_runs_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index(op.f("ix_runs_meeting_id"), "runs", ["meeting_id"], unique=False)
    op.create_index(op.f("ix_runs_thread_id"), "runs", ["thread_id"], unique=False)
    op.create_table(
        "agent_trace",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_agent_trace_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_trace")),
    )
    op.create_index(op.f("ix_agent_trace_created_at"), "agent_trace", ["created_at"], unique=False)
    op.create_index("ix_trace_run_seq", "agent_trace", ["run_id", "seq"], unique=False)
    op.create_table(
        "clarifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("commitment_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "resolved", "abandoned", name="clarification_status"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("interrupt_id", sa.String(length=64), nullable=True),
        sa.Column("resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["commitment_id"],
            ["commitments.id"],
            name=op.f("fk_clarifications_commitment_id_commitments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_clarifications_run_id_runs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clarifications")),
    )
    op.create_index(
        op.f("ix_clarifications_commitment_id"), "clarifications", ["commitment_id"], unique=False
    )
    op.create_index(op.f("ix_clarifications_run_id"), "clarifications", ["run_id"], unique=False)
    op.create_table(
        "commitment_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("commitment_id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=True),
        sa.Column(
            "type",
            sa.Enum(
                "created",
                "clarification_requested",
                "clarification_resolved",
                "owner_assigned",
                "deadline_set",
                "deadline_moved",
                "task_created",
                "task_failed",
                "progressed",
                "blocked",
                "slipped",
                "unmentioned",
                "completed",
                "dropped",
                "descoped",
                name="event_type",
            ),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column(
            "actor_kind", sa.Enum("agent", "human", "system", name="actor_kind"), nullable=False
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["commitment_id"],
            ["commitments.id"],
            name=op.f("fk_commitment_events_commitment_id_commitments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_commitment_events_meeting_id_meetings"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commitment_events")),
    )
    op.create_index(
        op.f("ix_commitment_events_created_at"), "commitment_events", ["created_at"], unique=False
    )
    op.create_index(
        "ix_events_commitment_created",
        "commitment_events",
        ["commitment_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "commitment_mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("commitment_id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "progress",
                "completed",
                "recommitted",
                "blocked",
                "descoped",
                "contradicted",
                "unmentioned",
                name="mention_outcome",
            ),
            nullable=False,
        ),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["commitment_id"],
            ["commitments.id"],
            name=op.f("fk_commitment_mentions_commitment_id_commitments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_commitment_mentions_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commitment_mentions")),
        sa.UniqueConstraint(
            "commitment_id", "meeting_id", name=op.f("uq_commitment_mentions_commitment_id")
        ),
    )
    op.create_index(
        op.f("ix_commitment_mentions_commitment_id"),
        "commitment_mentions",
        ["commitment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_commitment_mentions_created_at"),
        "commitment_mentions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_commitment_mentions_meeting_id"),
        "commitment_mentions",
        ["meeting_id"],
        unique=False,
    )
    op.create_table(
        "mock_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("assignee", sa.String(length=120), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("todo", "in_progress", "done", "cancelled", name="task_status"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("source_commitment_id", sa.Uuid(), nullable=True),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_commitment_id"],
            ["commitments.id"],
            name=op.f("fk_mock_tasks_source_commitment_id_commitments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mock_tasks")),
        sa.UniqueConstraint("external_id", name=op.f("uq_mock_tasks_external_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_mock_tasks_idempotency_key")),
    )
    op.execute(
        "CREATE INDEX ix_commitments_embedding_cosine ON commitments "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_commitments_embedding_cosine")
    op.drop_table("mock_tasks")
    op.drop_index(op.f("ix_commitment_mentions_meeting_id"), table_name="commitment_mentions")
    op.drop_index(op.f("ix_commitment_mentions_created_at"), table_name="commitment_mentions")
    op.drop_index(op.f("ix_commitment_mentions_commitment_id"), table_name="commitment_mentions")
    op.drop_table("commitment_mentions")
    op.drop_index("ix_events_commitment_created", table_name="commitment_events")
    op.drop_index(op.f("ix_commitment_events_created_at"), table_name="commitment_events")
    op.drop_table("commitment_events")
    op.drop_index(op.f("ix_clarifications_run_id"), table_name="clarifications")
    op.drop_index(op.f("ix_clarifications_commitment_id"), table_name="clarifications")
    op.drop_table("clarifications")
    op.drop_index("ix_trace_run_seq", table_name="agent_trace")
    op.drop_index(op.f("ix_agent_trace_created_at"), table_name="agent_trace")
    op.drop_table("agent_trace")
    op.drop_index(op.f("ix_runs_thread_id"), table_name="runs")
    op.drop_index(op.f("ix_runs_meeting_id"), table_name="runs")
    op.drop_table("runs")
    op.drop_index(op.f("ix_rejections_meeting_id"), table_name="rejections")
    op.drop_index(op.f("ix_rejections_created_at"), table_name="rejections")
    op.drop_table("rejections")
    op.drop_index(op.f("ix_decisions_meeting_id"), table_name="decisions")
    op.drop_table("decisions")
    op.drop_index(op.f("ix_communications_meeting_id"), table_name="communications")
    op.drop_table("communications")
    op.drop_index("ix_commitments_workspace_status", table_name="commitments")
    op.drop_index(op.f("ix_commitments_workspace_id"), table_name="commitments")
    op.drop_index(op.f("ix_commitments_owner_id"), table_name="commitments")
    op.drop_index(op.f("ix_commitments_external_task_id"), table_name="commitments")
    op.drop_index(op.f("ix_commitments_due_date"), table_name="commitments")
    op.drop_index(op.f("ix_commitments_canonical_key"), table_name="commitments")
    op.drop_table("commitments")
    op.drop_index(op.f("ix_people_workspace_id"), table_name="people")
    op.drop_table("people")
    op.drop_index(op.f("ix_meetings_workspace_id"), table_name="meetings")
    op.drop_table("meetings")
    op.drop_table("workspaces")
    op.drop_table("search_cache")
    op.execute("DROP EXTENSION IF EXISTS vector")
