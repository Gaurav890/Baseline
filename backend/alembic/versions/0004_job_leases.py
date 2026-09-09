"""Add recoverable worker leases and retry limits.

Revision ID: 0004_job_leases
Revises: 0003_production_feedback
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_job_leases"
down_revision: str | None = "0003_production_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evaluation_jobs") as batch_op:
        batch_op.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
        )
        batch_op.add_column(sa.Column("lease_owner", sa.String(160), nullable=True))
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_evaluation_jobs_lease_expires_at", ["lease_expires_at"])


def downgrade() -> None:
    with op.batch_alter_table("evaluation_jobs") as batch_op:
        batch_op.drop_index("ix_evaluation_jobs_lease_expires_at")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_owner")
        batch_op.drop_column("max_attempts")
