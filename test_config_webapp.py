"""Тесты web-конфигурации бота пикфлоуметрии."""
import importlib
import os

import pytest

import config


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    for key in ("WEBAPP_PORT", "WEBAPP_URL", "WEBAPP_HOST"):
        os.environ.pop(key, None)
    importlib.reload(config)


def _reload(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config)


def test_webapp_port_default(monkeypatch):
    monkeypatch.delenv("WEBAPP_PORT", raising=False)
    assert _reload(monkeypatch).WEBAPP_PORT == 8080


def test_webapp_port_number(monkeypatch):
    assert _reload(monkeypatch, WEBAPP_PORT="9000").WEBAPP_PORT == 9000


def test_webapp_port_zero(monkeypatch):
    assert _reload(monkeypatch, WEBAPP_PORT="0").WEBAPP_PORT == 0


def test_webapp_port_empty_is_zero(monkeypatch):
    assert _reload(monkeypatch, WEBAPP_PORT="").WEBAPP_PORT == 0


def test_webapp_port_garbage_is_zero(monkeypatch):
    assert _reload(monkeypatch, WEBAPP_PORT="abc").WEBAPP_PORT == 0


def test_normalize_webapp_url():
    assert config.normalize_webapp_url("") == ""
    assert config.normalize_webapp_url("   ") == ""
    assert config.normalize_webapp_url("https://pick.example.com/") == "https://pick.example.com"
    assert config.normalize_webapp_url("http://pick.example.com") == "http://pick.example.com"
    assert config.normalize_webapp_url("pick.example.com") == ""
    assert config.normalize_webapp_url("ftp://pick.example.com") == ""
