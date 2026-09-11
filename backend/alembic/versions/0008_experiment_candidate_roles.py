"""Add explicit baseline and candidate roles to experiments.

Revision ID: 0008_candidate_roles
Revises: 0007_agent_safety_controls
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_candidate_roles"
down_revision: str | None = "0007_agent_safety_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_candidates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "role",
                sa.String(length=24),
                nullable=False,
                server_default="candidate",
            )
        )
        batch_op.create_index("ix_experiment_candidates_role", ["role"])

    # Existing experiments did not record which candidate represented production.
    # Leave them as candidates rather than inventing baseline provenance.


def downgrade() -> None:
    with op.batch_alter_table("experiment_candidates") as batch_op:
        batch_op.drop_index("ix_experiment_candidates_role")
        batch_op.drop_column("role")
