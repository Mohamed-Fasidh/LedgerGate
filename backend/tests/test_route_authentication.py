"""Guard the API router against accidentally public business endpoints."""
import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
PUBLIC_API_ROUTES = {
    "/api/v1/auth/register",
    "/api/v1/auth/token",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/password-reset/request",
    "/api/v1/auth/password-reset/confirm",
}


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _has_auth_dependency(function):
    for argument in function.args.args + function.args.kwonlyargs:
        default = None
        positional = function.args.args
        if argument in positional:
            index = positional.index(argument)
            defaults_start = len(positional) - len(function.args.defaults)
            if index >= defaults_start:
                default = function.args.defaults[index - defaults_start]
        else:
            index = function.args.kwonlyargs.index(argument)
            default = function.args.kw_defaults[index]
        if not isinstance(default, ast.Call) or _call_name(default.func) != "Depends":
            continue
        if not default.args:
            continue
        dependency = default.args[0]
        dependency_callable = dependency.func if isinstance(dependency, ast.Call) else dependency
        if _call_name(dependency_callable) in {"get_current_user", "require_roles"}:
            return True
    return False


def test_all_business_api_routes_require_an_authenticated_role():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))
    unprotected = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "app":
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            path = decorator.args[0].value
            if not isinstance(path, str) or not path.startswith("/api/v1/"):
                continue
            if path in PUBLIC_API_ROUTES:
                continue
            if not _has_auth_dependency(node):
                unprotected.append(f"{path} ({node.name})")
    assert not unprotected, (
        "Business API routes must depend on get_current_user or require_roles; "
        "public exceptions must be explicitly reviewed: " + ", ".join(sorted(unprotected))
    )


PUBLIC_ROUTE_GUARDS = {
    "/api/v1/auth/register": {"_require_allowed_origin"},
    "/api/v1/auth/token": {"_require_allowed_origin"},
    "/api/v1/auth/refresh": {"_require_csrf"},
    "/api/v1/auth/logout": {"_require_csrf"},
    "/api/v1/auth/password-reset/request": {"_require_allowed_origin"},
    "/api/v1/auth/password-reset/confirm": {"_require_allowed_origin"},
}


def _called_names(function):
    names = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name:
                names.add(name)
    return names


def test_public_auth_routes_enforce_origin_or_csrf_guards():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))
    route_functions = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "app":
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                path = decorator.args[0].value
                if path in PUBLIC_ROUTE_GUARDS:
                    route_functions[path] = node

    missing = []
    for path, required_guards in PUBLIC_ROUTE_GUARDS.items():
        function = route_functions.get(path)
        if function is None:
            missing.append(f"{path} (route missing)")
            continue
        absent = required_guards - _called_names(function)
        if absent:
            missing.append(f"{path} (missing {', '.join(sorted(absent))})")

    assert not missing, (
        "Public auth endpoints must enforce their explicit browser-origin/CSRF "
        "guard before processing credentials or refresh tokens: " + "; ".join(missing)
    )


ACCESS_TOKEN_REVOCATION_FUNCTIONS = {
    "change_password",
    "disable_mfa",
    "logout_all_sessions",
    "update_tenant_user_role",
    "deactivate_tenant_user",
}


def test_sensitive_account_lifecycle_handlers_invalidate_access_tokens():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))
    handlers = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = []
    for name in sorted(ACCESS_TOKEN_REVOCATION_FUNCTIONS):
        handler = handlers.get(name)
        if handler is None:
            missing.append(f"{name} (handler missing)")
        elif "_invalidate_access_tokens" not in _called_names(handler):
            missing.append(f"{name} (revocation call missing)")
    assert not missing, (
        "Sensitive account lifecycle operations must invalidate existing access tokens: "
        + "; ".join(missing)
    )
