"""CORS allowlist regression tests.

A missing entry here is what caused the Tauri WebView to refuse to reach
the backend on the WSL+Windows dev workflow — the frontend loaded from
http://localhost:1420 (Vite) and the backend returned no
Access-Control-Allow-Origin header. Each test pins one origin to the
allowlist so any future "let me just trim this list" PR breaks loudly.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


REQUIRED_ORIGINS = [
    # Old web frontend (Phase 5)
    # Tauri Vite dev server (Phase 9A+)
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    # Tauri WebView schemes
    "tauri://localhost",
    "https://tauri.localhost",
]


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("origin", REQUIRED_ORIGINS)
def test_preflight_allows_known_origin(client: TestClient, origin: str) -> None:
    """An OPTIONS preflight from each allowed origin must echo the origin back."""
    resp = client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("access-control-allow-origin") == origin
    assert "GET" in resp.headers.get("access-control-allow-methods", "")


@pytest.mark.parametrize("origin", REQUIRED_ORIGINS)
def test_get_health_includes_cors_for_known_origin(
    client: TestClient, origin: str
) -> None:
    """Actual requests (not just preflight) must include the CORS header."""
    resp = client.get("/health", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin


def test_unknown_origin_is_rejected(client: TestClient) -> None:
    """A random origin must NOT receive an allow header — keeps the surface tight."""
    resp = client.get("/health", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
