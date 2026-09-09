"""Add persisted comparison experiments and candidates.

Revision ID: 0002_experiments
Revises: 0001_initial_schema
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_experiments"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("dataset_version", sa.String(length=80), nullable=False),
        sa.Column("scenario_ids", sa.JSON(), nullable=False),
        sa.Column("scenario_count", sa.Integer(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("baseline_experiment_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("recommendation", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiments_dataset_version", "experiments", ["dataset_version"])
    op.create_index("ix_experiments_status", "experiments", ["status"])
    op.create_index("ix_experiments_job_id", "experiments", ["job_id"])
    op.create_index(
        "ix_experiments_idempotency_key", "experiments", ["idempotency_key"], unique=True
    )

    op.create_table(
        "experiment_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("quality", sa.Float(), nullable=True),
        sa.Column("pass_rate", sa.Float(), nullable=True),
        sa.Column("task_completion", sa.Float(), nullable=True),
        sa.Column("answer_correctness", sa.Float(), nullable=True),
        sa.Column("tool_selection_accuracy", sa.Float(), nullable=True),
        sa.Column("hallucination_rate", sa.Float(), nullable=True),
        sa.Column("latency_p50_ms", sa.Integer(), nullable=True),
        sa.Column("latency_p95_ms", sa.Integer(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=False),
        sa.Column("cost_per_task", sa.Float(), nullable=True),
        sa.Column("baseline_quality", sa.Float(), nullable=True),
        sa.Column("quality_delta", sa.Float(), nullable=True),
        sa.Column("failure_breakdown", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("experiment_id", "provider", "model", "status"):
        op.create_index(
            f"ix_experiment_candidates_{column}", "experiment_candidates", [column]
        )

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(
            sa.Column("experiment_candidate_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_agent_runs_experiment_candidate_id",
            "experiment_candidates",
            ["experiment_candidate_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_agent_runs_experiment_candidate_id", ["experiment_candidate_id"]
        )

    with op.batch_alter_table("eval_suite_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "kind", sa.String(length=24), nullable=False, server_default="regression"
            )
        )
        batch_op.create_index("ix_eval_suite_runs_kind", ["kind"])


def downgrade() -> None:
    with op.batch_alter_table("eval_suite_runs") as batch_op:
        batch_op.drop_index("ix_eval_suite_runs_kind")
        batch_op.drop_column("kind")
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_experiment_candidate_id")
        batch_op.drop_constraint(
            "fk_agent_runs_experiment_candidate_id", type_="foreignkey"
        )
        batch_op.drop_column("experiment_candidate_id")
    op.drop_table("experiment_candidates")
    op.drop_table("experiments")
