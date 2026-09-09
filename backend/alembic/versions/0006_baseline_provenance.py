"""Distinguish observed baselines from legacy or supplied values.

Revision ID: 0006_baseline_provenance
Revises: 0005_experiment_case_keys
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_baseline_provenance"
down_revision: str | None = "0005_experiment_case_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("eval_suite_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "baseline_source",
                sa.String(32),
                nullable=False,
                server_default="legacy_unverified",
            )
        )
        batch_op.create_index("ix_eval_suite_runs_baseline_source", ["baseline_source"])


def downgrade() -> None:
    with op.batch_alter_table("eval_suite_runs") as batch_op:
        batch_op.drop_index("ix_eval_suite_runs_baseline_source")
        batch_op.drop_column("baseline_source")
