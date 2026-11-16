import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.main import _login_attempt_hash, _lock_login_attempt_keys
from app.models import LoginAttempt


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_concurrent_login_failures_are_serialized_by_email_across_connections():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_size=16, max_overflow=0)
    Session = sessionmaker(bind=engine)
    email_hash = _login_attempt_hash(f"login-{uuid.uuid4()}@example.invalid")
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        connection.execute(delete(LoginAttempt).where(LoginAttempt.email_hash == email_hash))

    def attempt(index):
        ip_hash = _login_attempt_hash(f"198.51.100.{index + 1}")
        with Session() as db:
            _lock_login_attempt_keys(db, email_hash, ip_hash)
            failures = db.query(LoginAttempt).filter(
                LoginAttempt.email_hash == email_hash,
                LoginAttempt.succeeded.is_(False),
                LoginAttempt.attempted_at >= now - timedelta(minutes=15),
            ).count()
            if failures >= 10:
                db.rollback()
                return False
            db.add(LoginAttempt(
                email_hash=email_hash,
                ip_hash=ip_hash,
                succeeded=False,
                attempted_at=now,
            ))
            db.commit()
            return True

    try:
        with ThreadPoolExecutor(max_workers=14) as pool:
            allowed = list(pool.map(attempt, range(14)))
        assert sum(allowed) == 10
        assert sum(not value for value in allowed) == 4
        with Session() as db:
            assert db.query(LoginAttempt).filter(
                LoginAttempt.email_hash == email_hash,
                LoginAttempt.succeeded.is_(False),
            ).count() == 10
    finally:
        with engine.begin() as connection:
            connection.execute(delete(LoginAttempt).where(LoginAttempt.email_hash == email_hash))
        engine.dispose()
