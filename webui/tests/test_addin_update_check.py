"""Tests für GET /addin/update-check + das Logikmodul `update_check.py`
(260728-irn, Stufe 1 — Erkennung).

Alle GitHub-Calls werden gestubbt — kein echtes Netzwerk. `reset_cache()` wird
in jedem Test aufgerufen, damit der Modul-Cache nicht zwischen Tests
durchschlägt (Muster `test_addin_agents.py`, Fixtures `client`/`authed_client`
aus conftest).
"""
from __future__ import annotations

import json

import httpx
import pytest

from src import update_check


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))


@pytest.fixture(autouse=True)
def _reset_cache():
    update_check.reset_cache()
    yield
    update_check.reset_cache()


# --- parse_version / compare_versions -------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("v1.9.0", (1, 9, 0)),
        ("1.15.0", (1, 15, 0)),
        ("v1.15.0-beta.1", (1, 15, 0)),
        ("v1.15.0+build5", (1, 15, 0)),
        ("", None),
        ("unknown", None),
        ("latest", None),
        ("v1.x", None),
        (None, None),
    ],
)
def test_parse_version(raw, expected):
    assert update_check.parse_version(raw) == expected


def test_compare_versions_numeric_not_lexicographic():
    # v1.9.0 < v1.15.0 nur bei numerischem Vergleich; lexikografisch waere "1.9" > "1.15".
    assert update_check.compare_versions("v1.9.0", "v1.15.0") == -1


def test_compare_versions_leading_v_optional():
    assert update_check.compare_versions("v1.15.0", "1.15.0") == 0


def test_compare_versions_installed_newer_than_latest():
    assert update_check.compare_versions("v2.0.0", "v1.15.0") == 1


@pytest.mark.parametrize("installed,latest", [("", "v1.15.0"), ("v1.15.0", "unknown"), ("v1.x", "v1.15.0")])
def test_compare_versions_unparsable_returns_none(installed, latest):
    assert update_check.compare_versions(installed, latest) is None


# --- resolve_installed_version ---------------------------------------------


def test_resolve_installed_version_prefers_env(monkeypatch):
    monkeypatch.setenv("VIZPATCH_VERSION", "v1.2.0")
    monkeypatch.setattr(update_check.docker_ctrl, "get_webui_image_tag", lambda: "vizpatch-webui:v9.9.9")
    assert update_check.resolve_installed_version() == "v1.2.0"


def test_resolve_installed_version_falls_back_to_image_tag(monkeypatch):
    monkeypatch.delenv("VIZPATCH_VERSION", raising=False)
    monkeypatch.setattr(update_check.docker_ctrl, "get_webui_image_tag", lambda: "vizpatch-webui:v1.15.0")
    assert update_check.resolve_installed_version() == "v1.15.0"


def test_resolve_installed_version_none_when_unresolvable(monkeypatch):
    monkeypatch.delenv("VIZPATCH_VERSION", raising=False)
    monkeypatch.setattr(update_check.docker_ctrl, "get_webui_image_tag", lambda: None)
    assert update_check.resolve_installed_version() is None


def test_resolve_installed_version_none_when_image_tag_unparsable(monkeypatch):
    monkeypatch.delenv("VIZPATCH_VERSION", raising=False)
    monkeypatch.setattr(update_check.docker_ctrl, "get_webui_image_tag", lambda: "vizpatch-webui:latest")
    assert update_check.resolve_installed_version() is None


def test_resolve_installed_version_docker_error_swallowed(monkeypatch):
    monkeypatch.delenv("VIZPATCH_VERSION", raising=False)

    def _raise():
        raise RuntimeError("docker socket down")

    monkeypatch.setattr(update_check.docker_ctrl, "get_webui_image_tag", _raise)
    assert update_check.resolve_installed_version() is None


# --- index_url --------------------------------------------------------------


def test_index_url_default(monkeypatch):
    monkeypatch.delenv("VIZPATCH_VERSION_INDEX_URL", raising=False)
    assert update_check.index_url() == update_check._DEFAULT_VERSION_INDEX_URL


def test_index_url_override(monkeypatch):
    monkeypatch.setenv("VIZPATCH_VERSION_INDEX_URL", "https://updates.example.com/vizpatch/latest.json")
    assert update_check.index_url() == "https://updates.example.com/vizpatch/latest.json"


@pytest.mark.parametrize("raw", ["", "   "])
def test_index_url_empty_env_falls_back_to_default(monkeypatch, raw):
    # Das Compose-Muster `${VAR:-}` liefert einen LEEREN String, nicht None —
    # dieselbe Falle wie bei ADDIN_FRAME_ANCESTORS. Default muss greifen.
    monkeypatch.setenv("VIZPATCH_VERSION_INDEX_URL", raw)
    assert update_check.index_url() == update_check._DEFAULT_VERSION_INDEX_URL


# --- fetch_latest_version ---------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.last_headers = None
        self.last_url = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        self.last_url = url
        self.last_headers = headers
        if self._exc is not None:
            raise self._exc
        return self._response


def test_fetch_latest_version_success(monkeypatch):
    fake = _FakeClient(_FakeResponse(200, {"version": "v1.15.0", "released": "2026-07-26"}))
    monkeypatch.setattr(update_check.httpx, "Client", lambda **kw: fake)
    version, status = update_check.fetch_latest_version()
    assert (version, status) == ("v1.15.0", "ok")
    assert fake.last_url == update_check._DEFAULT_VERSION_INDEX_URL


def test_fetch_latest_version_sends_no_credentials(monkeypatch):
    """Kernzusicherung der tokenfreien Variante: der Index ist oeffentlich, es
    darf KEIN Auth-Header rausgehen (und damit auch keines existieren muessen)."""
    fake = _FakeClient(_FakeResponse(200, {"version": "v1.15.0"}))
    monkeypatch.setattr(update_check.httpx, "Client", lambda **kw: fake)
    update_check.fetch_latest_version()
    assert "Authorization" not in fake.last_headers
    assert not any("token" in k.lower() for k in fake.last_headers)


def test_fetch_latest_version_uses_override_url(monkeypatch):
    monkeypatch.setenv("VIZPATCH_VERSION_INDEX_URL", "https://updates.example.com/v.json")
    fake = _FakeClient(_FakeResponse(200, {"version": "v1.15.0"}))
    monkeypatch.setattr(update_check.httpx, "Client", lambda **kw: fake)
    update_check.fetch_latest_version()
    assert fake.last_url == "https://updates.example.com/v.json"


@pytest.mark.parametrize("code", [401, 403, 404])
def test_fetch_latest_version_no_access(monkeypatch, code):
    fake = _FakeClient(_FakeResponse(code))
    monkeypatch.setattr(update_check.httpx, "Client", lambda **kw: fake)
    version, status = update_check.fetch_latest_version()
    assert version is None
    assert status == "no_access"


def test_fetch_latest_version_network_error(monkeypatch):
    fake = _FakeClient(exc=httpx.ConnectTimeout("timeout"))
    monkeypatch.setattr(update_check.httpx, "Client", lambda **kw: fake)
    version, status = update_check.fetch_latest_version()
    assert version is None
    assert status == "unreachable"


def test_fetch_latest_version_unexpected_status(monkeypatch):
    fake = _FakeClient(_FakeResponse(500))
    monkeypatch.setattr(update_check.httpx, "Client", lambda **kw: fake)
    version, status = update_check.fetch_latest_version()
    assert version is None
    assert status == "unreachable"


@pytest.mark.parametrize(
    "payload",
    [{}, {"version": ""}, {"version": None}, {"version": 115}, {"released": "2026-07-26"}, ["v1.15.0"]],
)
def test_fetch_latest_version_malformed_payload(monkeypatch, payload):
    """Der Index ist eine fremde Datei — kaputtes/fremdes JSON darf nie crashen."""
    fake = _FakeClient(_FakeResponse(200, payload))
    monkeypatch.setattr(update_check.httpx, "Client", lambda **kw: fake)
    version, status = update_check.fetch_latest_version()
    assert version is None
    assert status == "unreachable"


def test_fetch_latest_version_invalid_json(monkeypatch):
    class _BadJson(_FakeResponse):
        def json(self):
            raise ValueError("no json")

    fake = _FakeClient(_BadJson(200))
    monkeypatch.setattr(update_check.httpx, "Client", lambda **kw: fake)
    version, status = update_check.fetch_latest_version()
    assert version is None
    assert status == "unreachable"


# --- build_update_status -----------------------------------------------------


def test_build_update_status_unreachable(monkeypatch):
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: (None, "unreachable"))
    result = update_check.build_update_status()
    assert result["status"] == "unreachable"
    assert result["update_available"] is False


def test_build_update_status_no_access(monkeypatch):
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: (None, "no_access"))
    result = update_check.build_update_status()
    assert result["status"] == "no_access"
    assert result["update_available"] is False


def test_build_update_status_unknown_installed(monkeypatch):
    monkeypatch.setattr(update_check, "resolve_installed_version", lambda: None)
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: ("v1.15.0", "ok"))
    result = update_check.build_update_status()
    assert result["status"] == "unknown_installed"
    assert result["update_available"] is False
    assert result["latest"] == "v1.15.0"


def test_build_update_status_ok_update_available(monkeypatch):
    monkeypatch.setattr(update_check, "resolve_installed_version", lambda: "v1.9.0")
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: ("v1.15.0", "ok"))
    result = update_check.build_update_status()
    assert result["status"] == "ok"
    assert result["update_available"] is True
    assert result["latest"] == "v1.15.0"
    assert result["installed"] == "v1.9.0"


def test_build_update_status_ok_same_version(monkeypatch):
    monkeypatch.setattr(update_check, "resolve_installed_version", lambda: "v1.15.0")
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: ("v1.15.0", "ok"))
    result = update_check.build_update_status()
    assert result["status"] == "ok"
    assert result["update_available"] is False


def test_build_update_status_installed_newer_no_update(monkeypatch):
    """Dev-/Vorab-Installation neuer als der Index -> kein "Update" nach unten."""
    monkeypatch.setattr(update_check, "resolve_installed_version", lambda: "v2.0.0")
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: ("v1.15.0", "ok"))
    result = update_check.build_update_status()
    assert result["status"] == "ok"
    assert result["update_available"] is False


def test_build_update_status_needs_no_token_at_all(monkeypatch):
    """Regression zur tokenfreien Umstellung: auch ohne jedes GITHUB_TOKEN in
    Env oder /config/.env liefert der Check ein normales `ok`."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(update_check, "resolve_installed_version", lambda: "v1.9.0")
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: ("v1.15.0", "ok"))
    result = update_check.build_update_status()
    assert result["status"] == "ok"
    assert result["update_available"] is True
    assert "token" not in json.dumps(result).lower()


def test_build_update_status_caches_within_ttl(monkeypatch):
    monkeypatch.setattr(update_check, "resolve_installed_version", lambda: "v1.9.0")
    calls = {"n": 0}

    def _fake_fetch():
        calls["n"] += 1
        return "v1.15.0", "ok"

    monkeypatch.setattr(update_check, "fetch_latest_version", _fake_fetch)
    update_check.build_update_status()
    update_check.build_update_status()
    assert calls["n"] == 1


# --- GET /addin/update-check endpoint --------------------------------------


def test_endpoint_without_password_forbidden(client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.delenv("WEBUI_PASSWORD", raising=False)
    r = client.get("/addin/update-check")
    assert r.status_code == 403


def test_endpoint_authenticated_returns_defined_shape(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    # Kein echter Netzwerk-Call im Test — der Index wird gestubbt.
    monkeypatch.setattr(update_check, "resolve_installed_version", lambda: "v1.9.0")
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: ("v1.15.0", "ok"))
    r = authed_client.get("/addin/update-check")
    assert r.status_code == 200
    body = r.json()
    for key in ("status", "installed", "latest", "update_available", "message"):
        assert key in body
    assert body["status"] == "ok"
    assert body["update_available"] is True


def test_endpoint_degrades_when_index_unreachable(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(update_check, "fetch_latest_version", lambda: (None, "unreachable"))
    r = authed_client.get("/addin/update-check")
    assert r.status_code == 200
    assert r.json()["status"] == "unreachable"
    assert r.json()["update_available"] is False


def test_endpoint_never_500_on_internal_error(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(update_check, "build_update_status", _raise)
    r = authed_client.get("/addin/update-check")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unreachable"
    assert body["update_available"] is False
