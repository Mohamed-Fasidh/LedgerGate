from app.models import IdempotencyRecord


def test_idempotency_retention_cleanup_has_tenant_time_composite_index():
    indexes = {index.name: tuple(column.name for column in index.columns)
               for index in IdempotencyRecord.__table__.indexes}
    assert indexes["ix_idempotency_tenant_created_id"] == ("tenant_id", "created_at", "id")
