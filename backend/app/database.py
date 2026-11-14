from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

database_url = settings.database_url
if database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
elif database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]

connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def set_tenant_context(db, tenant_id: str) -> None:
    """Set transaction-local tenant context for PostgreSQL RLS policies.

    SQLite remains available for isolated development/unit tests; production
    configuration rejects SQLite.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
