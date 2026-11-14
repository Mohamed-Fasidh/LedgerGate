from app.models import AuditEvent

def record_event(db, user, entity_type, entity_id, action, details=None):
    event = AuditEvent(
        tenant_id=user.tenant_id, actor_user_id=user.id,
        entity_type=entity_type, entity_id=str(entity_id),
        action=action, details=details or {}
    )
    db.add(event)
    return event
