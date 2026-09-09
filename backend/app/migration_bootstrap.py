from __future__ import annotations

import logging

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)

_LEGACY_TABLES = {
    "agent_runs",
    "trace_spans",
    "evaluations",
    "human_reviews",
    "eval_suite_runs",
    "integrations",
    "evaluation_jobs",
}


def bootstrap_legacy_development_database() -> None:
    """Adopt only the exact pre-Alembic development schema at revision 0001."""
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" in tables or not tables:
        return
    if settings.is_production:
        raise RuntimeError(
            "Production database has tables but no Alembic version; inspect and stamp it manually"
        )
    if not _LEGACY_TABLES.issubset(tables) or "experiments" in tables:
        raise RuntimeError(
            "Database is not the recognized pre-Alembic development schema; refusing to stamp it"
        )
    config = Config("alembic.ini")
    command.stamp(config, "0001_initial_schema")
    logger.info("legacy_development_database_adopted", extra={"revision": "0001_initial_schema"})


if __name__ == "__main__":
    bootstrap_legacy_development_database()
