"""Guard the API route table against accidentally public business endpoints."""
from fastapi.routing import APIRoute

from app.main import app
from app.security import get_current_user


PUBLIC_ENDPOINTS = {
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/token"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/password-reset/request"),
    ("POST", "/api/v1/auth/password-reset/confirm"),
}


def _dependency_tree_contains(dependant, target) -> bool:
    if dependant.call is target:
        return True
    return any(
        _dependency_tree_contains(child, target)
        for child in dependant.dependencies
    )


def test_every_business_api_route_requires_authenticated_user():
    """Every /api/v1 route is protected unless explicitly public for auth."""
    discovered_public = set()

    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/"):
            continue

        for method in route.methods or set():
            endpoint = (method, route.path)
            if endpoint in PUBLIC_ENDPOINTS:
                discovered_public.add(endpoint)
                continue

            assert _dependency_tree_contains(route.dependant, get_current_user), (
                f"{method} {route.path} is missing an authenticated-user dependency"
            )

    assert discovered_public == PUBLIC_ENDPOINTS, (
        "Public authentication allowlist no longer matches registered routes: "
        f"missing={PUBLIC_ENDPOINTS - discovered_public}, "
        f"unexpected={discovered_public - PUBLIC_ENDPOINTS}"
    )
