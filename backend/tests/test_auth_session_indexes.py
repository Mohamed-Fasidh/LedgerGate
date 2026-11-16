from app.models import AuthSession


def test_auth_session_query_indexes_match_listing_and_family_revocation():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in AuthSession.__table__.indexes
    }
    assert indexes["ix_auth_sessions_tenant_user_active_created"] == (
        "tenant_id", "user_id", "revoked_at", "created_at", "id"
    )
    assert indexes["ix_auth_sessions_tenant_family_active"] == (
        "tenant_id", "family_id", "revoked_at"
    )
