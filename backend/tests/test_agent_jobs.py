from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.main as main
from app.schemas import AgentTaskIn
from app.services.agent_jobs import claim_one


class FakeDB:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.executed = None

    def add(self, value):
        self.added.append(value)

    def flush(self):
        for value in self.added:
            value.id = 91
            value.created_at = datetime.now(timezone.utc)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def query(self, model):
        assert model.__name__ == "AgentTask"
        return FakeQuery(self.row)

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def execute(self, statement, params):
        self.executed = (str(statement), params)
        return FakeMappings(self.row)


class FakeQuery:
    def __init__(self, row):
        self.row = row
        self.conditions = []

    def filter(self, *conditions):
        self.conditions.extend(conditions)
        return self

    def first(self):
        return self.row


class FakeMappings:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


def test_submit_agent_job_persists_tenant_and_requester_and_records_idempotency(monkeypatch):
    captured = {"idempotency": [], "events": []}
    db = FakeDB()
    monkeypatch.setattr(main, "_get_idempotent_response", lambda *args: None)
    monkeypatch.setattr(main, "_store_idempotency_response", lambda *args: captured["idempotency"].append(args))
    monkeypatch.setattr(main, "record_event", lambda *args: captured["events"].append(args))

    response = main.submit_agent_job(
        AgentTaskIn(task="Review invoice 91"),
        idempotency_key="agent-job-key-91",
        db=db,
        user=SimpleNamespace(id=7, tenant_id="tenant-a"),
    )

    job = db.added[0]
    assert (job.tenant_id, job.requested_by_user_id, job.task, job.status) == (
        "tenant-a", 7, "Review invoice 91", "queued"
    )
    assert response["job_id"] == 91
    assert response["status"] == "queued"
    assert db.committed is True
    assert len(captured["idempotency"]) == 1
    assert captured["events"][0][2:5] == ("agent_task", 91, "queued")


def test_submit_agent_job_replays_existing_idempotency_response(monkeypatch):
    cached = {"job_id": 91, "status": "queued", "created_at": "2026-11-17T00:00:00+00:00"}
    monkeypatch.setattr(main, "_get_idempotent_response", lambda *args: cached)

    class NoWriteDB:
        def add(self, _value):
            pytest.fail("Idempotent replay must not enqueue another job")

    response = main.submit_agent_job(
        AgentTaskIn(task="Review invoice 91"),
        idempotency_key="agent-job-key-91",
        db=NoWriteDB(),
        user=SimpleNamespace(id=7, tenant_id="tenant-a"),
    )
    assert response == cached


def test_get_agent_job_filters_by_tenant_and_requester(monkeypatch):
    row = SimpleNamespace(
        id=91, status="completed", attempts=1, max_attempts=3,
        created_at=datetime.now(timezone.utc), started_at=None, finished_at=None,
        result_json={"status": "completed"}, error_code=None,
    )
    captured = {}
    query = FakeQuery(row)
    db = SimpleNamespace(query=lambda _model: query)
    monkeypatch.setattr(main, "set_database_tenant", lambda _db, tenant: captured.setdefault("tenant", tenant))

    response = main.get_agent_job(
        91, db=db, user=SimpleNamespace(id=7, tenant_id="tenant-a")
    )

    rendered = [str(condition) for condition in query.conditions]
    assert captured["tenant"] == "tenant-a"
    assert any("agent_tasks.tenant_id" in item for item in rendered)
    assert any("agent_tasks.requested_by_user_id" in item for item in rendered)
    assert response["job_id"] == 91
    assert response["result"]["status"] == "completed"


def test_claim_one_calls_only_the_narrow_database_claim_function():
    row = {"task_id": 91, "task_tenant_id": "tenant-a"}
    db = FakeDB(row)

    class Factory:
        def __call__(self):
            return self
        def __enter__(self):
            return db
        def __exit__(self, *_args):
            return False

    claim = claim_one(Factory(), worker_id="agent-worker-test")

    assert claim["id"] == 91
    assert claim["tenant_id"] == "tenant-a"
    assert len(claim["lease_token"]) >= 32
    sql, params = db.executed
    assert "public.claim_agent_task" in sql
    assert params["worker_id"] == "agent-worker-test"
    assert params["lease_token"] == claim["lease_token"]
    assert db.committed is True
