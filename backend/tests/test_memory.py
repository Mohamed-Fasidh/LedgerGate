import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AgentMemory
from app.services.memory import MAX_MEMORY_RESULTS, recall


@pytest.fixture
def memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AgentMemory.__table__])
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_memory_search_is_tenant_scoped_and_caps_results(memory_db):
    memory_db.add_all(
        [
            AgentMemory(tenant_id="tenant-a", memory_type="episodic", content=f"invoice approval note {i}", metadata_json={})
            for i in range(MAX_MEMORY_RESULTS + 5)
        ]
        + [
            AgentMemory(tenant_id="tenant-b", memory_type="episodic", content="invoice approval confidential", metadata_json={})
        ]
    )
    memory_db.commit()

    results = recall(memory_db, "tenant-a", "invoice approval", limit=10_000)

    assert len(results) == MAX_MEMORY_RESULTS
    assert all("confidential" not in item["content"] for item in results)
    assert all(item["score"] == 1.0 for item in results)


@pytest.mark.parametrize("query", ["", " ", "x", "x" * 501, None])
def test_memory_search_rejects_invalid_or_oversized_queries(memory_db, query):
    with pytest.raises(ValueError):
        recall(memory_db, "tenant-a", query)


def test_memory_search_clamps_nonpositive_result_limit(memory_db):
    memory_db.add(AgentMemory(tenant_id="tenant-a", memory_type="episodic", content="invoice approval", metadata_json={}))
    memory_db.commit()

    results = recall(memory_db, "tenant-a", "invoice approval", limit=-100)

    assert len(results) == 1


def test_memory_search_rejects_non_integer_result_limit(memory_db):
    with pytest.raises(ValueError, match="integer"):
        recall(memory_db, "tenant-a", "invoice approval", limit=1.5)

