"""Tests für den Intelligenz-Modus (Betreiber-Wunsch 2026-08-24).

Muster 1:1 wie `test_endpoints_draft.py`. Deckt ab:
- WebUI `/save`: der verborgene Marker `intelligenz_section_submitted` steuert
  den Write; ein unzulässiger Wert wird abgelehnt statt still korrigiert.
- Add-in-Backend GET/POST `/addin/agents/{id}/intelligenz` (Wahrheitsquelle
  Backend, das Add-in hält keinen lokalen Wert).
- Rückwärtskompatibilität: fehlender Schlüssel -> „standard" (Update-Fall). Das
  ist der wichtigste Test der Datei — ein Update darf niemandem still die
  Kosten verdreifachen.
"""
from __future__ import annotations


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))


# --- WebUI /save -----------------------------------------------------------


def test_save_schreibt_hoch(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "intelligenz_section_submitted": "1",
            "intelligenz_modus": "hoch",
        },
    )
    assert response.status_code in (200, 303)
    assert agents_io.read_env_raw("info").get("INTELLIGENZ_MODUS") == "hoch"


def test_save_schreibt_standard_zurueck(authed_client, tmp_path, monkeypatch):
    """Zurückschalten muss über die WebUI möglich sein — sonst wäre der Modus
    eine Einbahnstraße."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"INTELLIGENZ_MODUS": "hoch"})
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "intelligenz_section_submitted": "1",
            "intelligenz_modus": "standard",
        },
    )
    assert response.status_code in (200, 303)
    assert agents_io.read_env_raw("info").get("INTELLIGENZ_MODUS") == "standard"


def test_save_ohne_marker_schreibt_nichts(authed_client, tmp_path, monkeypatch):
    """Wird eine andere Section gespeichert, darf der Modus unberührt bleiben."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"INTELLIGENZ_MODUS": "hoch"})
    authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={"agent_id": "info", "draft_section_submitted": "1"},
    )
    assert agents_io.read_env_raw("info").get("INTELLIGENZ_MODUS") == "hoch"


def test_save_lehnt_unsinn_ab_statt_still_zu_korrigieren(authed_client, tmp_path, monkeypatch):
    """Im Formular wählt ein Mensch bewusst. Ein stilles Zurückfallen auf
    „standard" würde ihm etwas anderes speichern, als er gewählt hat."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"INTELLIGENZ_MODUS": "hoch"})
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "intelligenz_section_submitted": "1",
            "intelligenz_modus": "maximal",
        },
    )
    assert response.status_code in (200, 303)
    # Unverändert — nicht auf "standard" heruntergesetzt.
    assert agents_io.read_env_raw("info").get("INTELLIGENZ_MODUS") == "hoch"


# --- Add-in-Endpoints ------------------------------------------------------


def test_addin_get_default_ist_standard(authed_client, tmp_path, monkeypatch):
    """Der Update-Fall: ein Agent ohne den Schlüssel gilt als „standard"."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    response = authed_client.get("/addin/agents/info/intelligenz", auth=("admin", "pw"))
    assert response.status_code == 200
    assert response.json() == {"modus": "standard"}


def test_addin_get_meldet_kaputten_wert_als_standard(authed_client, tmp_path, monkeypatch):
    """Steht Unsinn in der `.env` (von Hand editiert), muss der Dialog dasselbe
    zeigen, was der Agent tut — und der fällt auf „standard" zurück."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"INTELLIGENZ_MODUS": "ganz-hoch"})
    response = authed_client.get("/addin/agents/info/intelligenz", auth=("admin", "pw"))
    assert response.json() == {"modus": "standard"}


def test_addin_post_setzt_und_liest_zurueck(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    post = authed_client.post(
        "/addin/agents/info/intelligenz", auth=("admin", "pw"), data={"modus": "hoch"}
    )
    assert post.status_code == 200
    assert post.json() == {"ok": True, "modus": "hoch"}
    assert agents_io.read_env_raw("info").get("INTELLIGENZ_MODUS") == "hoch"
    assert authed_client.get(
        "/addin/agents/info/intelligenz", auth=("admin", "pw")
    ).json() == {"modus": "hoch"}


def test_addin_post_lehnt_unzulaessigen_wert_ab(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    response = authed_client.post(
        "/addin/agents/info/intelligenz", auth=("admin", "pw"), data={"modus": "turbo"}
    )
    assert response.status_code == 400
    assert "INTELLIGENZ_MODUS" not in agents_io.read_env_raw("info")


def test_addin_unbekannter_agent_ist_404_und_legt_nichts_an(
    authed_client, tmp_path, monkeypatch
):
    """Ohne Existenzprüfung VOR dem Write würde `write_env` ein
    Phantom-Verzeichnis für eine tote Agent-ID anlegen (dieselbe Falle wie bei
    /draft-enabled)."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    assert (
        authed_client.get("/addin/agents/gibtsnicht/intelligenz", auth=("admin", "pw")).status_code
        == 404
    )
    assert (
        authed_client.post(
            "/addin/agents/gibtsnicht/intelligenz",
            auth=("admin", "pw"),
            data={"modus": "hoch"},
        ).status_code
        == 404
    )
    assert "gibtsnicht" not in agents_io.list_agent_ids()
