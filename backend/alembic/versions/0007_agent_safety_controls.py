"""Add agent safety policies and decision events.

Revision ID: 0007_agent_safety_controls
Revises: 0006_baseline_provenance
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_agent_safety_controls"
down_revision: str | None = "0006_baseline_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "safety_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_type", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=False),
        sa.Column("allow_mutating_tools", sa.Boolean(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column("max_estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("approval_threshold_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_safety_policies_agent_type",
        "safety_policies",
        ["agent_type"],
        unique=True,
    )
    op.create_table(
        "safety_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("policy_id", sa.String(length=36), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["safety_policies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("run_id", "policy_id", "category", "decision", "created_at"):
        op.create_index(f"ix_safety_events_{column}", "safety_events", [column])


def downgrade() -> None:
    op.drop_table("safety_events")
    op.drop_table("safety_policies")
