"""PostgreSQL integration coverage for durable agent queue claiming and recovery."""
import os
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
def test_agent_queue_claims_are_distinct_and_expired_leases_are_recovered():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_size=5)
    tenant_id = "queue-test-" + uuid.uuid4().hex[:24]
    email = tenant_id + "@example.invalid"
    claims = []

    try:
        with engine.begin() as connection:
            user_id = connection.execute(
                text("""
                    INSERT INTO users
                      (tenant_id, email, hashed_password, role, is_active, created_at)
                    VALUES
                      (:tenant_id, :email, 'not-a-login-hash', 'analyst', true, now())
                    RETURNING id
                """),
                {"tenant_id": tenant_id, "email": email},
            ).scalar_one()

            for _ in range(2):
                claims.append(connection.execute(
                    text("""
                        INSERT INTO agent_tasks
                          (tenant_id, requested_by_user_id, task, status, attempts,
                           max_attempts, available_at, created_at, updated_at)
                        VALUES
                          (:tenant_id, :user_id, 'Review queue test evidence',
                           'queued', 0, 3, now(), now(), now())
                        RETURNING id
                    """),
                    {"tenant_id": tenant_id, "user_id": user_id},
                ).scalar_one())

        def claim(worker_id):
            token = secrets.token_urlsafe(32)
            with engine.begin() as connection:
                row = connection.execute(
                    text("""
                        SELECT task_id, task_tenant_id
                        FROM public.claim_agent_task(:worker_id, :token, 180)
                    """),
                    {"worker_id": worker_id, "token": token},
                ).mappings().first()
            if row is None:
                return None
            return {"id": row["task_id"], "tenant": row["task_tenant_id"], "token": token}

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(claim, ["worker-a", "worker-b"]))

        assert first["tenant"] == tenant_id
        assert second["tenant"] == tenant_id
        assert first["id"] != second["id"]

        # Simulate a crashed worker. A fresh worker must reclaim the same row
        # with a new fencing token and an incremented attempt counter.
        with engine.begin() as connection:
            connection.execute(
                text("""
                    UPDATE agent_tasks
                    SET lease_expires_at = now() - interval '1 second'
                    WHERE id = :job_id AND tenant_id = :tenant_id
                """),
                {"job_id": first["id"], "tenant_id": tenant_id},
            )

        recovered = claim("worker-recovery")
        assert recovered["id"] == first["id"]
        assert recovered["token"] != first["token"]

        with engine.begin() as connection:
            stale_write = connection.execute(
                text("""
                    UPDATE agent_tasks
                    SET status = 'completed'
                    WHERE id = :job_id AND tenant_id = :tenant_id
                      AND lease_token = :old_token
                """),
                {
                    "job_id": first["id"],
                    "tenant_id": tenant_id,
                    "old_token": first["token"],
                },
            )
            state = connection.execute(
                text("""
                    SELECT status, attempts, lease_token
                    FROM agent_tasks
                    WHERE id = :job_id AND tenant_id = :tenant_id
                """),
                {"job_id": first["id"], "tenant_id": tenant_id},
            ).one()

        assert stale_write.rowcount == 0
        assert state.status == "running"
        assert state.attempts == 2
        assert state.lease_token == recovered["token"]

        # An expired lease at the retry limit is terminal and is not reclaimed.
        with engine.begin() as connection:
            connection.execute(
                text("""
                    UPDATE agent_tasks
                    SET attempts = max_attempts,
                        lease_expires_at = now() - interval '1 second'
                    WHERE id = :job_id AND tenant_id = :tenant_id
                """),
                {"job_id": recovered["id"], "tenant_id": tenant_id},
            )

        assert claim("worker-after-exhaustion") is None
        with engine.connect() as connection:
            terminal = connection.execute(
                text("""
                    SELECT status, error_code
                    FROM agent_tasks
                    WHERE id = :job_id AND tenant_id = :tenant_id
                """),
                {"job_id": recovered["id"], "tenant_id": tenant_id},
            ).one()
        assert terminal.status == "failed"
        assert terminal.error_code == "WORKER_LEASE_EXHAUSTED"
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM agent_tasks WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            connection.execute(
                text("DELETE FROM users WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        engine.dispose()
