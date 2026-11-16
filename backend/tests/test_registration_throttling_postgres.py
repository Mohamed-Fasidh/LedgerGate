import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.main import _record_registration_attempt, _registration_hash
from app.models import RegistrationAttempt


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_concurrent_registration_attempts_respect_email_limit_across_connections():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_size=10, max_overflow=0)
    Session = sessionmaker(bind=engine)
    email = f"registration-{uuid.uuid4()}@example.invalid"
    email_hash = _registration_hash(email)

    with engine.begin() as connection:
        connection.execute(delete(RegistrationAttempt).where(
            RegistrationAttempt.email_hash == email_hash
        ))

    def attempt(index):
        with Session() as db:
            return _record_registration_attempt(
                db,
                email,
                f"198.51.100.{index + 1}",
                datetime.now(timezone.utc),
            )

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))

        assert sum(result is None for result in results) == 5
        assert sum(result is not None for result in results) == 3
        with Session() as db:
            assert db.query(RegistrationAttempt).filter(
                RegistrationAttempt.email_hash == email_hash
            ).count() == 5
    finally:
        with engine.begin() as connection:
            connection.execute(delete(RegistrationAttempt).where(
                RegistrationAttempt.email_hash == email_hash
            ))
        engine.dispose()
