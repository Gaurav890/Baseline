"""Make experiment case execution retry-safe.

Revision ID: 0005_experiment_case_keys
Revises: 0004_job_leases
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_experiment_case_keys"
down_revision: str | None = "0004_job_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(sa.Column("experiment_case_key", sa.String(255), nullable=True))
        batch_op.create_index(
            "ix_agent_runs_experiment_case_key", ["experiment_case_key"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_experiment_case_key")
        batch_op.drop_column("experiment_case_key")
