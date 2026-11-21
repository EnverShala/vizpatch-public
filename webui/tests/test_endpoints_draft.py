"""Tests für den Auto-Entwurf-Schalter ENABLE_DRAFT_CREATION (260725-tqr).

Deckt ab:
- WebUI /save: Der verborgene Marker `draft_section_submitted` entscheidet, ob das
  Flag geschrieben wird. KERN-REGRESSION: Eine NICHT angehakte Checkbox (das Feld
  enable_draft_creation fehlt dann im POST) MUSS als "false" persistiert werden —
  sonst ließe sich die Funktion über die WebUI nie abschalten (klassisches
  HTML-Checkbox-Verhalten: unchecked sendet gar nichts).
- Add-in-Backend GET/POST /addin/agents/{id}/draft-enabled (Wahrheitsquelle Backend).
- Rückwärtskompatibilität: fehlender Schlüssel -> Default AN (Update-Fall).
"""
from __future__ import annotations


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    # config_io (Root-.env) sonst auf echten Default-Pfad -> isolieren (vgl. test_endpoints_style)
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))


# --- WebUI /save: submitted-Marker steuert den Write ------------------------


def test_save_draft_unchecked_persists_false(authed_client, tmp_path, monkeypatch):
    """KERN-REGRESSION (260725-tqr): Formular abgeschickt (Marker gesetzt), Checkbox
    NICHT angehakt (enable_draft_creation fehlt komplett) -> ENABLE_DRAFT_CREATION=false.
    Hinge der Guard an der Checkbox statt am Marker, bliebe das Flag ungeschrieben und
    die Funktion ließe sich über die WebUI nie ausschalten."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={"agent_id": "info", "draft_section_submitted": "1"},
    )
    assert response.status_code in (200, 303)
    assert agents_io.read_env_raw("info").get("ENABLE_DRAFT_CREATION") == "false"


def test_save_draft_checked_persists_true(authed_client, tmp_path, monkeypatch):
    """Wieder-Anschalten über die WebUI: Checkbox angehakt -> true, auch wenn zuvor false."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"ENABLE_DRAFT_CREATION": "false"})
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "draft_section_submitted": "1",
            "enable_draft_creation": "true",
        },
    )
    assert response.status_code in (200, 303)
    assert agents_io.read_env_raw("info").get("ENABLE_DRAFT_CREATION") == "true"


def test_save_without_draft_marker_leaves_flag_untouched(authed_client, tmp_path, monkeypatch):
    """Teil-Saves an /save (z. B. der Autostart-Fetch oder ein anderes Fieldset) OHNE
    Draft-Section dürfen das Flag NICHT anfassen — sonst setzte ein fremder Save es
    versehentlich auf false."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"ENABLE_DRAFT_CREATION": "true"})
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        headers={"HX-Request": "true"},
        data={"agent_id": "info", "style_md": "## Anrede\nSie"},
    )
    assert response.status_code in (200, 303)
    assert agents_io.read_env_raw("info").get("ENABLE_DRAFT_CREATION") == "true"


# --- Add-in-Backend GET/POST ------------------------------------------------


def test_addin_draft_get_defaults_true_when_key_absent(authed_client, tmp_path, monkeypatch):
    """Update-/Rückwärtskompatibilität: Agent ohne ENABLE_DRAFT_CREATION -> enabled=true."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    r = authed_client.get("/addin/agents/info/draft-enabled", auth=("admin", "pw"))
    assert r.status_code == 200
    assert r.json() == {"enabled": True}


def test_addin_draft_get_reflects_false(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"ENABLE_DRAFT_CREATION": "false"})
    r = authed_client.get("/addin/agents/info/draft-enabled", auth=("admin", "pw"))
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


def test_addin_draft_get_unknown_agent_404(authed_client, tmp_path, monkeypatch):
    """Tote Agent-ID -> 404 (explizite Existenzprüfung), NICHT still enabled=true."""
    _setup_env(tmp_path, monkeypatch)
    r = authed_client.get("/addin/agents/ghost/draft-enabled", auth=("admin", "pw"))
    assert r.status_code == 404


def test_addin_draft_post_writes_backend(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"ENABLE_DRAFT_CREATION": "true"})
    r = authed_client.post(
        "/addin/agents/info/draft-enabled",
        auth=("admin", "pw"),
        data={"enabled": "false"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "enabled": False}
    assert agents_io.read_env_raw("info").get("ENABLE_DRAFT_CREATION") == "false"


def test_addin_draft_post_unknown_agent_404_no_phantom_dir(authed_client, tmp_path, monkeypatch):
    """POST auf tote Agent-ID -> 404 und KEIN Phantom-Verzeichnis angelegt."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    r = authed_client.post(
        "/addin/agents/ghost/draft-enabled",
        auth=("admin", "pw"),
        data={"enabled": "false"},
    )
    assert r.status_code == 404
    assert "ghost" not in agents_io.list_agent_ids()


# --- CSRF/Origin: Add-in-POST darf mit Office-Origin (260725-tqr) ------------


def test_addin_draft_post_office_origin_allowed(authed_client, tmp_path, monkeypatch):
    """KERN-REGRESSION: das VSTO-Add-in sendet einen fremden Office-Origin. Der
    zustandsändernde POST muss über die enforce_same_origin-Allowlist erlaubt sein —
    sonst 403 (genau der beim Add-in-Speichern beobachtete Fehler)."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"ENABLE_DRAFT_CREATION": "true"})
    r = authed_client.post(
        "/addin/agents/info/draft-enabled",
        auth=("admin", "pw"),
        data={"enabled": "false"},
        headers={"Origin": "https://outlook.office.com"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "enabled": False}
    assert agents_io.read_env_raw("info").get("ENABLE_DRAFT_CREATION") == "false"


def test_addin_draft_post_foreign_origin_still_rejected(authed_client, tmp_path, monkeypatch):
    """Ein nicht zur Add-in-Allowlist gehörender Origin bleibt geblockt (403)."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"ENABLE_DRAFT_CREATION": "true"})
    r = authed_client.post(
        "/addin/agents/info/draft-enabled",
        auth=("admin", "pw"),
        data={"enabled": "false"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    # Flag darf durch den abgelehnten Request NICHT verändert worden sein.
    assert agents_io.read_env_raw("info").get("ENABLE_DRAFT_CREATION") == "true"
