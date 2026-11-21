"""Tests für den Auto-Sortier-Schalter ENABLE_AUTO_SORT/AUTO_SORT_DRY_RUN
(Phase 14, Plan 14-01, AUTO-01/05) — Muster 1:1 wie test_endpoints_draft.py.

Deckt ab:
- WebUI /save: der verborgene Marker `auto_sort_section_submitted` entscheidet,
  ob die Flags geschrieben werden. KERN-REGRESSION: eine NICHT angehakte
  Checkbox (das Feld fehlt dann im POST) MUSS als "false" persistiert werden —
  sonst ließe sich das Feature über die WebUI nie abschalten.
- Unterschiedliche Defaults: ENABLE_AUTO_SORT fehlt -> aus; AUTO_SORT_DRY_RUN
  fehlt -> an (sicherer Default).
- sortierregeln_md wird unabhängig von der Checkbox gespeichert.
"""
from __future__ import annotations


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))


def test_save_auto_sort_unchecked_persists_false(authed_client, tmp_path, monkeypatch):
    """KERN-REGRESSION: Marker gesetzt, beide Checkboxen NICHT angehakt ->
    ENABLE_AUTO_SORT=false UND AUTO_SORT_DRY_RUN=false. Ohne den Marker-Guard
    ließe sich das Feature über die WebUI nie ausschalten."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={"agent_id": "info", "auto_sort_section_submitted": "1"},
    )
    assert response.status_code in (200, 303)
    env = agents_io.read_env_raw("info")
    assert env.get("ENABLE_AUTO_SORT") == "false"
    assert env.get("AUTO_SORT_DRY_RUN") == "false"


def test_save_auto_sort_checked_persists_true(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "auto_sort_section_submitted": "1",
            "enable_auto_sort": "true",
            "auto_sort_dry_run": "true",
        },
    )
    assert response.status_code in (200, 303)
    env = agents_io.read_env_raw("info")
    assert env.get("ENABLE_AUTO_SORT") == "true"
    assert env.get("AUTO_SORT_DRY_RUN") == "true"


def test_save_without_auto_sort_marker_leaves_flags_untouched(authed_client, tmp_path, monkeypatch):
    """Teil-Saves ohne die Auto-Sort-Section duerfen die Flags NICHT anfassen."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"ENABLE_AUTO_SORT": "true", "AUTO_SORT_DRY_RUN": "false"})
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        headers={"HX-Request": "true"},
        data={"agent_id": "info", "style_md": "## Anrede\nSie"},
    )
    assert response.status_code in (200, 303)
    env = agents_io.read_env_raw("info")
    assert env.get("ENABLE_AUTO_SORT") == "true"
    assert env.get("AUTO_SORT_DRY_RUN") == "false"


def test_save_sortierregeln_md_independent_of_checkbox(authed_client, tmp_path, monkeypatch):
    """Die Kategorien-Datei ist auch OHNE die Checkbox-Section speicherbar —
    der Betreiber kann Kategorien vorbereiten, bevor er das Feature aktiviert."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    inhalt = "## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n"
    response = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        headers={"HX-Request": "true"},
        data={"agent_id": "info", "sortierregeln_md": inhalt},
    )
    assert response.status_code in (200, 303)
    assert agents_io.read_sortierregeln_md("info") == inhalt


def test_agent_edit_form_renders_sortierregeln_md(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"IMAP_USER": "info@x.de"})
    agents_io.write_sortierregeln_md_atomic("info", "## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n")

    response = authed_client.get("/agents/info/edit", auth=("admin", "pw"))

    assert response.status_code == 200
    assert "## Rechnungen" in response.text
    assert 'name="sortierregeln_md"' in response.text
    assert 'name="enable_auto_sort"' in response.text
    assert 'name="auto_sort_dry_run"' in response.text
