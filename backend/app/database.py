from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        # Tenant context is session-scoped so it survives endpoint commits. Always
        # clear it before returning a pooled connection to prevent cross-tenant leaks.
        try:
            db.rollback()
            db.execute(text("RESET app.tenant_id"))
            db.commit()
        except Exception:
            db.rollback()
        db.close()
