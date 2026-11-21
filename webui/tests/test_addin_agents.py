"""Tests für GET /addin/agents (Agentenliste fürs native Add-in).

Das VSTO-Add-in liest hierüber den REAL konfigurierten Agent-Namen und zeigt ihn
im Pane-Header (Design-Überarbeitung 2026-07-24). Der Endpoint ist
Session-Gate-ausgenommen (/addin/-Präfix) und verlangt ein gesetztes
WEBUI_PASSWORD (`require_setup`). GET ist eine sichere Methode → keine
Origin-/CSRF-Prüfung.
"""
from __future__ import annotations


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))


def test_agents_returns_list_shape(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    r = authed_client.get("/addin/agents")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("agents"), list)


def test_agents_lists_configured_agent(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    # Agent = Unterordner unter <config>/agents/ (siehe agents_io.list_agent_ids).
    (tmp_path / "config" / "agents" / "muster-gmbh").mkdir(parents=True)
    r = authed_client.get("/addin/agents")
    assert r.status_code == 200
    assert "muster-gmbh" in r.json()["agents"]


def test_agents_without_password_forbidden(client, tmp_path, monkeypatch):
    """Ohne gesetztes WEBUI_PASSWORD greift require_setup -> 403."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.delenv("WEBUI_PASSWORD", raising=False)
    r = client.get("/addin/agents")
    assert r.status_code == 403
