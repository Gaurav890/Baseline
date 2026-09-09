"""Add production trace, feedback, dataset promotion, and canary state.

Revision ID: 0003_production_feedback
Revises: 0002_experiments
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_production_feedback"
down_revision: str | None = "0002_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(sa.Column("external_trace_id", sa.String(255), nullable=True))
        batch_op.create_index("ix_agent_runs_external_trace_id", ["external_trace_id"], unique=True)

    op.create_table(
        "user_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_user_feedback_run_id", "user_feedback", ["run_id"])
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_name", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("parent_version_id", sa.String(36), nullable=True),
        sa.Column("source_run_id", sa.String(36), nullable=True),
        sa.Column("scenarios", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("dataset_name", "version", name="uq_dataset_version"),
    )
    op.create_index("ix_dataset_versions_dataset_name", "dataset_versions", ["dataset_name"])
    op.create_index("ix_dataset_versions_status", "dataset_versions", ["status"])
    op.create_index("ix_dataset_versions_source_run_id", "dataset_versions", ["source_run_id"])
    op.create_table(
        "canary_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("canary_id", sa.String(120), nullable=False),
        sa.Column("connector", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("provenance", sa.String(24), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("canary_id", "connector", "status"):
        op.create_index(f"ix_canary_runs_{column}", "canary_runs", [column])


def downgrade() -> None:
    op.drop_table("canary_runs")
    op.drop_table("dataset_versions")
    op.drop_table("user_feedback")
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_external_trace_id")
        batch_op.drop_column("external_trace_id")
