ROLE_ACTIONS = {
    "admin": {"invoice.read", "invoice.approve", "policy.write"},
    "reviewer": {"invoice.read", "invoice.approve"},
    "analyst": {"invoice.read", "policy.write"},
}


def policy_allows(tenant_id: str, resource_tenant_id: str, role: str, action: str) -> bool:
    if not tenant_id or not resource_tenant_id or tenant_id != resource_tenant_id:
        return False
    return action in ROLE_ACTIONS.get(role, set())
