"""Read-only cross-layer regression for the root-level API surface (batch 8).

The Vite dev proxy and the desktop gateway must both cover every namespace
FastAPI exposes and every namespace the React client requests. The three sets
are derived from source, not duplicated by hand, so adding a route or a
`request(...)` call without wiring the proxies fails the suite.

Fragment literals passed to `projectPath()` / `chapterPath()` and React Router
pages are not root-level API namespaces; they live in the explicit allowlists
below instead of being silently ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

from bidcraft import desktop

ROOT = Path(__file__).resolve().parents[1]
ROUTES_DIR = ROOT / "bidcraft" / "api" / "routes"
VITE_CONFIG = ROOT / "web" / "vite.config.ts"
WEB_SRC = ROOT / "web" / "src"

_ROUTE_RE = re.compile(
    r'@router\.(?:get|post|put|patch|delete|head|options)\(\s*["\']/([A-Za-z0-9_-]+)'
)
_LITERAL_RE = re.compile(r"['\"`]/([A-Za-z][A-Za-z0-9_-]*)")
_NAMESPACE_BLOCK_RE = re.compile(
    r"apiProxyNamespaces\s*=\s*\[(.*?)\]\s*as\s+const", re.DOTALL
)
_QUOTED_PATH_RE = re.compile(r"['\"`](/[A-Za-z0-9_-]+)['\"`]")

# FastAPI's own documentation endpoints are not part of the product API.
BACKEND_INTERNAL = frozenset({"docs", "redoc", "openapi.json"})

# Frontend literals whose first segment is not a root-level API namespace.
FRONTEND_NON_API = frozenset(
    {
        # React Router pages / client-only locations.
        "app",
        "login",
        "register",
        "my-docs",
        "qualifications",
        # Suffixes handed to projectPath()/chapterPath() helpers.
        "assignments",
        "chapters",
        "diff",
        "lock",
        "restore",
        "revisions",
        "workflow-approvals",
        "workflows",
        # Chapter-planning path suffixes handed to planPath().
        "confirm",
        "generate",
        "history",
        "regenerate",
        # Placeholder filesystem paths and regex/template artifacts.
        "data",
        "g",
        "srv",
        "slow",
    }
)

_EXPECTED_CORE = frozenset(
    {
        "account",
        "admin",
        "agents",
        "approvals",
        "auth",
        "batch-jobs",
        "billing",
        "enterprise",
        "health",
        "jobs",
        "legal",
        "my",
        "packages",
        "projects",
    }
)


def _without_slash(values: set[str] | frozenset[str]) -> set[str]:
    return {value.lstrip("/") for value in values}


def backend_namespaces() -> set[str]:
    found: set[str] = set()
    for source in ROUTES_DIR.glob("*.py"):
        found.update(_ROUTE_RE.findall(source.read_text(encoding="utf-8")))
    return found


def vite_namespaces() -> set[str]:
    text = VITE_CONFIG.read_text(encoding="utf-8")
    block = _NAMESPACE_BLOCK_RE.search(text)
    assert block, "vite.config.ts must export apiProxyNamespaces"
    return _without_slash(set(_QUOTED_PATH_RE.findall(block.group(1))))


def frontend_namespaces() -> set[str]:
    found: set[str] = set()
    for source in WEB_SRC.rglob("*"):
        if source.suffix not in {".ts", ".tsx"} or ".test." in source.name:
            continue
        found.update(_LITERAL_RE.findall(source.read_text(encoding="utf-8")))
    return found - FRONTEND_NON_API


def desktop_namespaces() -> set[str]:
    return _without_slash(set(desktop.PROXY_PREFIXES) | set(desktop.PROXY_PATHS))


def test_backend_namespaces_are_proxied_by_vite() -> None:
    missing = backend_namespaces() - vite_namespaces() - BACKEND_INTERNAL
    assert not missing, f"backend namespaces missing from vite proxy: {sorted(missing)}"

    # The exported list must actually drive `server.proxy`.
    text = VITE_CONFIG.read_text(encoding="utf-8")
    assert "apiProxyNamespaces.map" in text
    assert "proxy," in text


def test_backend_namespaces_are_proxied_by_desktop() -> None:
    missing = backend_namespaces() - desktop_namespaces() - BACKEND_INTERNAL
    assert not missing, f"backend namespaces missing from desktop gateway: {sorted(missing)}"


def test_frontend_namespaces_are_proxied_by_vite() -> None:
    missing = frontend_namespaces() - vite_namespaces()
    assert not missing, f"frontend request namespaces missing from vite proxy: {sorted(missing)}"


def test_frontend_api_namespaces_are_proxied_by_desktop() -> None:
    shared = frontend_namespaces() & backend_namespaces()
    missing = shared - desktop_namespaces()
    assert not missing, f"frontend API namespaces missing from desktop gateway: {sorted(missing)}"


def test_contract_parsers_are_not_empty() -> None:
    """Guard against a parser silently returning an empty set."""
    backend = backend_namespaces()
    vite = vite_namespaces()
    desktop_surface = desktop_namespaces()
    for surface, name in ((backend, "backend"), (vite, "vite"), (desktop_surface, "desktop")):
        missing = _EXPECTED_CORE - surface
        assert not missing, f"{name} contract surface lost: {sorted(missing)}"
