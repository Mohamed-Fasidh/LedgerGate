"""Durable, tenant-scoped agent job queue with fenced PostgreSQL leases."""
import logging
import os
import secrets
import socket
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, update

from app.database import SessionLocal
from app.models import AgentTask
from app.security import set_database_tenant

logger = logging.getLogger(__name__)
LEASE_SECONDS = 180
MAX_ATTEMPTS = 3
POLL_SECONDS = 1.0


def _now():
    return datetime.now(timezone.utc)


def claim_one(session_factory=SessionLocal, *, worker_id: str, now=None):
    """Atomically claim one due task; PostgreSQL function bypasses only queue-table RLS."""
    lease_token = secrets.token_urlsafe(32)
    with session_factory() as db:
        if db.get_bind().dialect.name != "postgresql":
            raise RuntimeError("Durable agent workers require PostgreSQL")
        row = db.execute(
            text(
                "SELECT task_id, task_tenant_id "
                "FROM public.claim_agent_task(:worker_id, :lease_token, :lease_seconds)"
            ),
            {
                "worker_id": worker_id,
                "lease_token": lease_token,
                "lease_seconds": LEASE_SECONDS,
            },
        ).mappings().first()
        db.commit()
    if row is None:
        return None
    return {
        "id": row["task_id"],
        "tenant_id": row["task_tenant_id"],
        "lease_token": lease_token,
    }


def _retry_or_fail(session_factory, claim, *, now=None):
    now = now or _now()
    with session_factory() as db:
        set_database_tenant(db, claim["tenant_id"])
        row = db.query(AgentTask).filter(
            AgentTask.id == claim["id"],
            AgentTask.tenant_id == claim["tenant_id"],
            AgentTask.status == "running",
            AgentTask.lease_token == claim["lease_token"],
        ).first()
        if row is None:
            db.rollback()
            return False
        exhausted = row.attempts >= row.max_attempts
        row.status = "failed" if exhausted else "queued"
        row.error_code = "WORKER_EXECUTION_ERROR"
        row.available_at = now + timedelta(seconds=min(30 * (2 ** max(row.attempts - 1, 0)), 300))
        row.finished_at = now if exhausted else None
        row.worker_id = None
        row.lease_token = None
        row.locked_at = None
        row.lease_expires_at = None
        row.updated_at = now
        db.commit()
        return True


def process_one(session_factory=SessionLocal, *, worker_id: str | None = None, orchestrator_factory=None) -> bool:
    """Claim, execute and persist one read-only task with lease-token fencing."""
    worker_id = worker_id or f"{socket.gethostname()[:40]}:{os.getpid()}"
    claim = claim_one(session_factory, worker_id=worker_id)
    if claim is None:
        return False

    try:
        with session_factory() as db:
            set_database_tenant(db, claim["tenant_id"])
            job = db.query(AgentTask).filter(
                AgentTask.id == claim["id"],
                AgentTask.tenant_id == claim["tenant_id"],
                AgentTask.status == "running",
                AgentTask.lease_token == claim["lease_token"],
            ).first()
            if job is None:
                db.rollback()
                return True
            task_text = job.task
            requester_id = job.requested_by_user_id

            if orchestrator_factory is None:
                from app.main import _build_agent_orchestrator
                orchestrator = _build_agent_orchestrator(db, claim["tenant_id"])
            else:
                orchestrator = orchestrator_factory(db, claim["tenant_id"])
            result = orchestrator.run(task_text)
            from fastapi.encoders import jsonable_encoder
            encoded = jsonable_encoder(result)
            if result.status == "completed":
                terminal_status = "completed"
            elif result.status == "cancelled":
                terminal_status = "cancelled"
            elif result.status == "timed_out":
                terminal_status = "timed_out"
            else:
                terminal_status = "failed"

            now = _now()
            updated = db.execute(
                update(AgentTask)
                .where(
                    AgentTask.id == claim["id"],
                    AgentTask.tenant_id == claim["tenant_id"],
                    AgentTask.status == "running",
                    AgentTask.lease_token == claim["lease_token"],
                    AgentTask.lease_expires_at > now,
                )
                .values(
                    status=terminal_status,
                    result_json=encoded,
                    error_code=result.error_code,
                    finished_at=now,
                    worker_id=None,
                    lease_token=None,
                    locked_at=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            db.commit()
            if updated.rowcount != 1:
                logger.warning("Agent job lease expired before result commit (job_id=%s)", claim["id"])
            else:
                logger.info("Agent job completed (job_id=%s requester_id=%s status=%s)", claim["id"], requester_id, terminal_status)
        return True
    except Exception as exc:
        # Exception messages may contain task text, arguments, or provider data.
        logger.warning("Agent job worker failed (job_id=%s error_type=%s)", claim["id"], type(exc).__name__)
        try:
            _retry_or_fail(session_factory, claim)
        except Exception as retry_exc:
            logger.error("Agent job retry transition failed (job_id=%s error_type=%s)", claim["id"], type(retry_exc).__name__)
        return True


def run_forever(session_factory=SessionLocal):
    worker_id = f"{socket.gethostname()[:40]}:{os.getpid()}"
    logger.info("Durable agent worker started (worker_id=%s)", worker_id)
    while True:
        if not process_one(session_factory, worker_id=worker_id):
            time.sleep(POLL_SECONDS)
