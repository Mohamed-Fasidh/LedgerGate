import os
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_rls_blocks_cross_tenant_and_audit_is_append_only():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.execute(text("SELECT set_config('app.tenant_id', 'tenant-a', false)"))
        # This assumes the test fixture has seeded a row belonging to tenant-b.
        result = db.execute(text("SELECT count(*) FROM invoices WHERE tenant_id='tenant-b'")).scalar_one()
        assert result == 0
        db.rollback()
        db.execute(text("SELECT set_config('app.tenant_id', 'tenant-a', false)"))
        with pytest.raises(Exception, match="append-only"):
            db.execute(text("UPDATE audit_events SET action='tampered' WHERE tenant_id='tenant-a'"))
        db.rollback()
