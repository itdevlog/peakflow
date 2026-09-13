"""Тесты web-API бота пикфлоуметрии (SP1: только /healthz)."""
from fastapi.testclient import TestClient

from web.api import create_app


def _client() -> TestClient:
    return TestClient(create_app({"config": None}))


def test_healthz_ok():
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_path_404():
    assert _client().get("/nope").status_code == 404
