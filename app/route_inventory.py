"""Generate the reviewable HTTP trust-boundary inventory from FastAPI routes."""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute, _iter_routes_with_context

_DEPENDENCY_CLASSES = {
    "require_superadmin": "superadmin_session",
    "require_owner_session": "owner_session",
    "require_owner": "owner_session",
    "current_user_ui": "browser_session",
    "current_user": "browser_session",
    "require_write": "session_or_write_token",
    "api_or_session_user": "session_or_token",
    "api_token_auth": "api_token",
}


def _dependency_names(dependant: Any) -> set[str]:
    names: set[str] = set()
    for dependency in getattr(dependant, "dependencies", []):
        call = getattr(dependency, "call", None)
        name = getattr(call, "__name__", "")
        if name:
            names.add(name)
        names.update(_dependency_names(dependency))
    return names


def classify_route(path: str, dependencies: set[str]) -> str:
    if path == "/metrics":
        return "metrics_token"
    if path.startswith("/webhooks/"):
        return "webhook_signature"
    if path == "/mcp":
        return "manual_api_token"
    for dependency, trust_class in _DEPENDENCY_CLASSES.items():
        if dependency in dependencies:
            return trust_class
    return "public"


def route_inventory(app: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for route, context in _iter_routes_with_context(app.routes):
        if not isinstance(route, APIRoute):
            continue
        effective = context or route
        dependencies = _dependency_names(effective.dependant)
        path = effective.path
        for method in sorted(effective.methods or []):
            rows.append(
                {
                    "method": method,
                    "path": path,
                    "name": effective.name,
                    "trust_class": classify_route(path, dependencies),
                    "dependencies": sorted(dependencies & set(_DEPENDENCY_CLASSES)),
                }
            )
    return sorted(rows, key=lambda row: (row["path"], row["method"]))
