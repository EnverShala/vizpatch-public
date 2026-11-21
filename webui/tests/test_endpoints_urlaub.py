"""Tests für den Urlaubs-Abschnitt im Agenten-Formular (URL-01..09) —
Muster 1:1 wie `test_endpoints_auto_sort.py`.

Zwei Dinge sind hier wichtiger als sonst:

* **Ausschalten muss gehen.** Dieselbe Kern-Regression wie beim Sortierlauf:
  eine nicht angehakte Checkbox sendet gar nichts, deshalb hängt der Guard am
  verborgenen Marker. Bei der EINZIGEN sendenden Funktion des Produkts wäre ein
  Schalter, der sich nicht mehr umlegen lässt, besonders unangenehm.
* **Ein unbrauchbarer Zeitraum wird abgewiesen.** Ein Tippfehler in deutscher
  Schreibweise oder ein Bis-vor-Von würde still nie greifen — der Betreiber
  sähe einen eingetragenen Urlaub, der nichts tut.
"""
from __future__ import annotations

from datetime import date


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))


def test_urlaub_unchecked_persistiert_false(authed_client, tmp_path, monkeypatch):
    """KERN-REGRESSION: Marker gesetzt, Checkboxen nicht angehakt -> beide
    Flags auf false. Ohne den Marker-Guard ließe sich die Autoantwort über die
    WebUI nie abschalten."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    antwort = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={"agent_id": "info", "urlaub_section_submitted": "1"},
    )
    assert antwort.status_code in (200, 303)
    env = agents_io.read_env_raw("info")
    assert env.get("ENABLE_URLAUBSANTWORT") == "false"
    assert env.get("URLAUB_DRY_RUN") == "false"


def test_urlaub_wird_mit_zeitraum_gespeichert(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    antwort = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "urlaub_section_submitted": "1",
            "enable_urlaubsantwort": "true",
            "urlaub_dry_run": "true",
            "urlaub_von": "2026-09-01",
            "urlaub_bis": "2026-09-14",
        },
    )
    assert antwort.status_code in (200, 303)
    env = agents_io.read_env_raw("info")
    assert env.get("ENABLE_URLAUBSANTWORT") == "true"
    assert env.get("URLAUB_DRY_RUN") == "true"
    assert env.get("URLAUB_VON") == "2026-09-01"
    assert env.get("URLAUB_BIS") == "2026-09-14"


def test_aktivieren_ohne_zeitraum_wird_abgelehnt(authed_client, tmp_path, monkeypatch):
    """Sonst sähe der Betreiber einen gesetzten Haken und bekäme nie eine
    Antwort verschickt."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "urlaub_section_submitted": "1",
            "enable_urlaubsantwort": "true",
        },
    )
    env = agents_io.read_env_raw("info")
    assert env.get("ENABLE_URLAUBSANTWORT") in (None, "")


def test_bis_vor_von_wird_abgelehnt(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "urlaub_section_submitted": "1",
            "enable_urlaubsantwort": "true",
            "urlaub_von": "2026-09-14",
            "urlaub_bis": "2026-09-01",
        },
    )
    assert agents_io.read_env_raw("info").get("URLAUB_VON") in (None, "")


def test_deutsche_datumsschreibweise_wird_abgelehnt(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={
            "agent_id": "info",
            "urlaub_section_submitted": "1",
            "enable_urlaubsantwort": "true",
            "urlaub_von": "01.09.2026",
            "urlaub_bis": "14.09.2026",
        },
    )
    assert agents_io.read_env_raw("info").get("URLAUB_VON") in (None, "")


def test_urlaub_austragen_bei_ausgeschalteter_funktion(authed_client, tmp_path, monkeypatch):
    """Leerer Zeitraum ist gültig, solange die Funktion nicht aktiviert wird —
    sonst bliebe ein vergangener Urlaub für immer im Formular stehen."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"URLAUB_VON": "2026-01-01", "URLAUB_BIS": "2026-01-14"})
    antwort = authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={"agent_id": "info", "urlaub_section_submitted": "1"},
    )
    assert antwort.status_code in (200, 303)
    env = agents_io.read_env_raw("info")
    assert env.get("URLAUB_VON") == ""
    assert env.get("ENABLE_URLAUBSANTWORT") == "false"


def test_save_ohne_marker_laesst_urlaub_unberuehrt(authed_client, tmp_path, monkeypatch):
    """Ein Speichern aus einer ANDEREN Section darf den Urlaub nicht
    zurücksetzen."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env(
        "info",
        {"ENABLE_URLAUBSANTWORT": "true", "URLAUB_VON": "2026-09-01", "URLAUB_BIS": "2026-09-14"},
    )
    authed_client.post(
        "/save",
        auth=("admin", "pw"),
        data={"agent_id": "info", "draft_section_submitted": "1"},
    )
    env = agents_io.read_env_raw("info")
    assert env.get("ENABLE_URLAUBSANTWORT") == "true"
    assert env.get("URLAUB_VON") == "2026-09-01"


def test_formular_zeigt_den_urlaubsabschnitt(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env(
        "info",
        {
            "IMAP_USER": "info@ionos.de",
            "IMAP_PASSWORD": "p",
            "LLM_API_KEY": "k",
            "URLAUB_VON": "2026-09-01",
            "URLAUB_BIS": "2026-09-14",
        },
    )
    seite = authed_client.get("/agents/info/edit", auth=("admin", "pw")).text

    assert 'name="enable_urlaubsantwort"' in seite
    assert 'name="urlaub_von"' in seite
    assert "2026-09-01" in seite
    # Der Warnhinweis, der diese Funktion von allen anderen unterscheidet.
    assert "VERSCHICKT" in seite


# ==========================================================================
# Add-in-Endpoints /addin/agents/{id}/urlaub
# ==========================================================================
#
# Der Kunde arbeitet ausschließlich in Outlook (die Browser-Oberfläche ist ein
# Betreiber-Werkzeug). Die FREIGABE der Urlaubs-Autoantwort ist bewusst nicht
# per Chat schaltbar — ohne diese Endpoints könnte der Kunde die Funktion
# deshalb überhaupt nicht einschalten.


def _addin_headers():
    # Add-in-Requests tragen eine fremde Office-Origin; die CSRF-Middleware
    # lässt sie nur für die Add-in-Pfade durch.
    return {"Origin": "https://outlook.office.com"}


def _agent_mit_urlaub(**extra):
    import src.agents_io as agents_io

    werte = {"IMAP_USER": "info@ionos.de", "IMAP_PASSWORD": "p", "LLM_API_KEY": "k"}
    werte.update(extra)
    agents_io.write_env("info", werte)


def test_addin_get_liefert_den_stand(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent_mit_urlaub(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="false",
        URLAUB_VON="2026-09-01",
        URLAUB_BIS="2026-09-14",
    )
    import src.agents_io as agents_io

    agents_io.write_vorlagen_md_atomic("info", "## Urlaub\nIch bin weg.\n")

    antwort = authed_client.get(
        "/addin/agents/info/urlaub", auth=("admin", "pw"), headers=_addin_headers()
    )
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["enabled"] is True
    assert daten["dry_run"] is False
    assert daten["von"] == "2026-09-01"
    assert daten["vorlage_vorhanden"] is True


def test_addin_get_meldet_fehlende_vorlage(authed_client, tmp_path, monkeypatch):
    """Der Dialog warnt daraufhin rot — der Betreiber soll das sehen, BEVOR er
    in den Urlaub fährt."""
    _setup_env(tmp_path, monkeypatch)
    _agent_mit_urlaub(ENABLE_URLAUBSANTWORT="true")
    import src.agents_io as agents_io

    agents_io.write_vorlagen_md_atomic("info", "## Terminanfrage\nHallo\n")

    daten = authed_client.get(
        "/addin/agents/info/urlaub", auth=("admin", "pw"), headers=_addin_headers()
    ).json()
    assert daten["vorlage_vorhanden"] is False


def test_addin_get_defaults_sind_sicher(authed_client, tmp_path, monkeypatch):
    """Bestandsagent ohne die neuen Variablen: aus, Probelauf an."""
    _setup_env(tmp_path, monkeypatch)
    _agent_mit_urlaub()

    daten = authed_client.get(
        "/addin/agents/info/urlaub", auth=("admin", "pw"), headers=_addin_headers()
    ).json()
    assert daten["enabled"] is False
    assert daten["dry_run"] is True


def test_addin_get_unbekannter_agent_404(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    antwort = authed_client.get(
        "/addin/agents/gibtsnicht/urlaub", auth=("admin", "pw"), headers=_addin_headers()
    )
    assert antwort.status_code == 404


def test_addin_post_schreibt_alle_werte(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent_mit_urlaub()
    import src.agents_io as agents_io

    antwort = authed_client.post(
        "/addin/agents/info/urlaub",
        auth=("admin", "pw"),
        headers=_addin_headers(),
        data={
            "enabled": "true",
            "dry_run": "false",
            "von": "2026-09-01",
            "bis": "2026-09-14",
        },
    )
    assert antwort.status_code == 200
    env = agents_io.read_env_raw("info")
    assert env["ENABLE_URLAUBSANTWORT"] == "true"
    assert env["URLAUB_DRY_RUN"] == "false"
    assert env["URLAUB_VON"] == "2026-09-01"
    assert env["URLAUB_BIS"] == "2026-09-14"


def test_addin_post_prueft_den_zeitraum_serverseitig(authed_client, tmp_path, monkeypatch):
    """Ein Client darf nicht an der Prüfung vorbeikommen, nur weil er ein
    anderer Client ist — es ist derselbe Helfer wie im Browser-Formular."""
    _setup_env(tmp_path, monkeypatch)
    _agent_mit_urlaub()
    import src.agents_io as agents_io

    antwort = authed_client.post(
        "/addin/agents/info/urlaub",
        auth=("admin", "pw"),
        headers=_addin_headers(),
        data={"enabled": "true", "von": "2026-09-14", "bis": "2026-09-01"},
    )
    assert antwort.status_code == 400
    assert "Bis-Datum" in antwort.json()["detail"]
    assert agents_io.read_env_raw("info").get("ENABLE_URLAUBSANTWORT") in (None, "")


def test_addin_post_ohne_zeitraum_beim_aktivieren_400(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent_mit_urlaub()

    antwort = authed_client.post(
        "/addin/agents/info/urlaub",
        auth=("admin", "pw"),
        headers=_addin_headers(),
        data={"enabled": "true"},
    )
    assert antwort.status_code == 400


def test_addin_post_unbekannter_agent_legt_nichts_an(authed_client, tmp_path, monkeypatch):
    """Ohne die Existenzprüfung VOR dem Write legte `write_env` ein
    Phantom-Verzeichnis mit nur diesem Flag an."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    antwort = authed_client.post(
        "/addin/agents/gibtsnicht/urlaub",
        auth=("admin", "pw"),
        headers=_addin_headers(),
        data={"enabled": "true", "von": "2026-09-01", "bis": "2026-09-14"},
    )
    assert antwort.status_code == 404
    assert "gibtsnicht" not in agents_io.list_agent_ids()


# ==========================================================================
# Der Agent hört von selbst auf — und das Formular sagt es
# ==========================================================================
#
# Betreiber-Vorgabe 2026-08-23: „man soll nach Urlaub nicht sagen müssen 'ich
# bin zurück', er sollte das je nach Datum selber verstehen." Der Agent tut das
# (er prüft den Kalendertag, nicht den Haken). Die Tests hier sichern, dass die
# Oberflächen das auch zeigen, statt einen gesetzten Haken als „läuft" zu
# verkaufen.


def _vergangener_urlaub():
    from datetime import date, timedelta

    heute = date.today()
    return (heute - timedelta(days=30)).isoformat(), (heute - timedelta(days=20)).isoformat()


def _laufender_urlaub():
    from datetime import date, timedelta

    heute = date.today()
    return (heute - timedelta(days=1)).isoformat(), (heute + timedelta(days=7)).isoformat()


def test_formular_meldet_abgelaufenen_urlaub(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    von, bis = _vergangener_urlaub()
    agents_io.write_env(
        "info",
        {
            "IMAP_USER": "info@ionos.de",
            "IMAP_PASSWORD": "p",
            "LLM_API_KEY": "k",
            "ENABLE_URLAUBSANTWORT": "true",
            "URLAUB_VON": von,
            "URLAUB_BIS": bis,
        },
    )
    seite = authed_client.get("/agents/info/edit", auth=("admin", "pw")).text

    assert "ist vorbei" in seite
    assert "hört von selbst auf" in seite
    assert "Läuft gerade" not in seite


def test_formular_meldet_laufenden_urlaub(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    von, bis = _laufender_urlaub()
    agents_io.write_env(
        "info",
        {
            "IMAP_USER": "info@ionos.de",
            "IMAP_PASSWORD": "p",
            "LLM_API_KEY": "k",
            "ENABLE_URLAUBSANTWORT": "true",
            "URLAUB_VON": von,
            "URLAUB_BIS": bis,
        },
    )
    seite = authed_client.get("/agents/info/edit", auth=("admin", "pw")).text

    assert "Läuft gerade" in seite
    assert "ist vorbei" not in seite


def test_formular_ohne_urlaub_zeigt_keine_lage(authed_client, tmp_path, monkeypatch):
    """Kein Rauschen bei einem Agenten, der die Funktion gar nicht nutzt."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env(
        "info", {"IMAP_USER": "info@ionos.de", "IMAP_PASSWORD": "p", "LLM_API_KEY": "k"}
    )
    seite = authed_client.get("/agents/info/edit", auth=("admin", "pw")).text

    assert "Läuft gerade" not in seite
    assert "ist vorbei" not in seite


def test_addin_endpoint_meldet_die_lage(authed_client, tmp_path, monkeypatch):
    """Das Add-in kann damit dasselbe zeigen — ohne dass der Haken lügt."""
    _setup_env(tmp_path, monkeypatch)
    von, bis = _vergangener_urlaub()
    _agent_mit_urlaub(ENABLE_URLAUBSANTWORT="true", URLAUB_VON=von, URLAUB_BIS=bis)

    daten = authed_client.get(
        "/addin/agents/info/urlaub", auth=("admin", "pw"), headers=_addin_headers()
    ).json()

    assert daten["enabled"] is True   # Haken steht
    assert daten["lage"] == "vorbei"  # wirkt aber nicht mehr
