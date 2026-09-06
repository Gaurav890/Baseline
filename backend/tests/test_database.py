from app.database import sqlalchemy_database_url


def test_render_postgres_url_selects_psycopg3_driver() -> None:
    assert (
        sqlalchemy_database_url("postgresql://user:pass@db.example/baseline")
        == "postgresql+psycopg://user:pass@db.example/baseline"
    )
    explicit = "postgresql+psycopg://user:pass@db.example/baseline"
    assert sqlalchemy_database_url(explicit) == explicit
