"""Tests für den Urlaubstext im Add-in-Dialog (Betreiber-Auftrag 2026-08-24).

Der Kunde arbeitet ausschliesslich in Outlook. Bis hierher konnte er den Urlaub
im Dialog eintragen, aber den Text, der in seinem Namen hinausgeht, dort weder
sehen noch aendern — der Hinweis verwies ihn auf Chat oder Weboberflaeche. Das
war die wahrscheinlichste Stelle zum Scheitern.

Drei Dinge sind hier wichtiger als der Rest:

* **Ein Bestands-Add-in darf die Vorlage nicht loeschen.** Es schickt das
  Formfeld gar nicht mit; wuerde ein fehlendes Feld als "leer" gelten, waere
  nach dem naechsten Speichern eines alten Clients die Vorlage weg — und der
  Agent schwiege im Urlaub, ohne dass es jemand merkt.
* **Der Vorschlag ist kein stiller Default.** Er wird ANGEZEIGT und erst durch
  Speichern wirksam. Der Riegel „ohne Vorlage kein Versand" bleibt.
* **Die uebrigen Vorlagen bleiben unberuehrt.** Der Dialog editiert genau einen
  Baustein, nicht die ganze Datei.
"""
from __future__ import annotations


def _addin_headers():
    # Add-in-Requests tragen eine fremde Office-Origin; die CSRF-Middleware
    # laesst sie nur fuer die Add-in-Pfade durch (Muster wie
    # `test_endpoints_urlaub.py`).
    return {"Origin": "https://outlook.office.com"}


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))


# --- Helfer (reine Textumformung) -------------------------------------------


def test_urlaubstext_aus_liest_abschnitt_und_synonyme():
    from src import chat_tools

    md = "## Rechnung\nText A\n\n## Abwesenheit\nBin weg.\nZweite Zeile.\n"
    assert chat_tools.urlaubstext_aus(md) == "Bin weg.\nZweite Zeile."
    assert chat_tools.urlaubstext_aus("## Anderes\nnix") == ""
    assert chat_tools.urlaubstext_aus("") == ""


def test_vorlagen_md_mit_urlaub_laesst_andere_abschnitte_stehen():
    from src import chat_tools

    md = "## Rechnung\nText A\n\n## Urlaub\nAlt\n"
    neu = chat_tools.vorlagen_md_mit_urlaub(md, "Neu")
    assert "## Rechnung" in neu and "Text A" in neu
    assert chat_tools.urlaubstext_aus(neu) == "Neu"
    assert "Alt" not in neu


def test_vorlagen_md_mit_urlaub_legt_abschnitt_an_wenn_er_fehlt():
    from src import chat_tools

    neu = chat_tools.vorlagen_md_mit_urlaub("## Rechnung\nText A\n", "Bin weg")
    assert chat_tools.urlaubstext_aus(neu) == "Bin weg"
    assert "## Rechnung" in neu


def test_vorlagen_md_mit_urlaub_behaelt_vorhandene_schreibweise():
    """Wer seinen Abschnitt „Abwesenheit" genannt hat, soll ihn nach dem
    Speichern nicht ploetzlich „Urlaub" heissen sehen."""
    from src import chat_tools

    neu = chat_tools.vorlagen_md_mit_urlaub("## Abwesenheit\nAlt\n", "Neu")
    assert "## Abwesenheit" in neu
    assert "## Urlaub" not in neu


def test_leerer_text_entfernt_den_abschnitt():
    from src import chat_tools

    neu = chat_tools.vorlagen_md_mit_urlaub("## Rechnung\nA\n\n## Urlaub\nAlt\n", "")
    assert chat_tools.urlaubstext_aus(neu) == ""
    assert "## Rechnung" in neu


# --- GET --------------------------------------------------------------------


def test_get_liefert_text_und_vorschlag(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io
    from src import chat_tools

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    agents_io.write_vorlagen_md_atomic("info", "## Urlaub\nBin bis Montag weg.\n")

    antwort = authed_client.get("/addin/agents/info/urlaub", auth=("admin", "pw"))
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["vorlage_vorhanden"] is True
    assert daten["vorlage_text"] == "Bin bis Montag weg."
    assert daten["vorlage_vorschlag"] == chat_tools.URLAUB_VORLAGE_VORSCHLAG


def test_get_ohne_vorlage_liefert_leeren_text_aber_vorschlag(
    authed_client, tmp_path, monkeypatch
):
    """Der Vorschlag steht IMMER bereit — aber `vorlage_text` bleibt leer und
    `vorlage_vorhanden` false, damit der Dialog weiter warnt und der Agent
    weiter schweigt."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})

    daten = authed_client.get("/addin/agents/info/urlaub", auth=("admin", "pw")).json()
    assert daten["vorlage_vorhanden"] is False
    assert daten["vorlage_text"] == ""
    assert daten["vorlage_vorschlag"].strip() != ""


# --- POST -------------------------------------------------------------------


def test_post_ohne_feld_laesst_vorlage_unberuehrt(authed_client, tmp_path, monkeypatch):
    """RUECKWAERTSKOMPATIBILITAET: ein installiertes Add-in ohne das neue Feld
    darf die gespeicherte Vorlage nicht loeschen."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io
    from src import chat_tools

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    agents_io.write_vorlagen_md_atomic("info", "## Urlaub\nBestand.\n")

    antwort = authed_client.post(
        "/addin/agents/info/urlaub",
        auth=("admin", "pw"),
        data={"enabled": "false", "dry_run": "true", "von": "", "bis": ""},
        headers=_addin_headers(),
    )
    assert antwort.status_code == 200
    assert chat_tools.urlaubstext_aus(agents_io.read_vorlagen_md("info")) == "Bestand."


def test_post_mit_feld_schreibt_vorlage(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io
    from src import chat_tools

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})

    antwort = authed_client.post(
        "/addin/agents/info/urlaub",
        auth=("admin", "pw"),
        data={
            "enabled": "false",
            "dry_run": "true",
            "von": "",
            "bis": "",
            "vorlage_gesetzt": "true",
            "vorlage": "Bin ab Montag wieder da.",
        },
        headers=_addin_headers(),
    )
    assert antwort.status_code == 200
    assert (
        chat_tools.urlaubstext_aus(agents_io.read_vorlagen_md("info"))
        == "Bin ab Montag wieder da."
    )


def test_post_mit_leerem_feld_entfernt_vorlage(authed_client, tmp_path, monkeypatch):
    """Ausdruecklich geleert heisst geleert — der Agent schweigt dann wieder,
    was `vorlagen.py` sauber abfaengt."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io
    from src import chat_tools

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    agents_io.write_vorlagen_md_atomic("info", "## Urlaub\nAlt.\n")

    antwort = authed_client.post(
        "/addin/agents/info/urlaub",
        auth=("admin", "pw"),
        data={
            "enabled": "false",
            "dry_run": "true",
            "von": "",
            "bis": "",
            "vorlage_gesetzt": "true",
            "vorlage": "",
        },
        headers=_addin_headers(),
    )
    assert antwort.status_code == 200
    assert chat_tools.urlaubstext_aus(agents_io.read_vorlagen_md("info")) == ""


def test_abgelehnter_zeitraum_schreibt_auch_die_vorlage_nicht(
    authed_client, tmp_path, monkeypatch
):
    """Die Zeitraum-Pruefung laeuft VOR jedem Schreibvorgang. Sonst bliebe nach
    einer abgelehnten Eingabe eine halb angewandte Aenderung zurueck."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io
    from src import chat_tools

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})
    agents_io.write_vorlagen_md_atomic("info", "## Urlaub\nBestand.\n")

    antwort = authed_client.post(
        "/addin/agents/info/urlaub",
        auth=("admin", "pw"),
        data={
            "enabled": "true",
            "dry_run": "false",
            "von": "2026-09-10",
            "bis": "2026-09-01",  # Bis vor Von
            "vorlage_gesetzt": "true",
            "vorlage": "Neu und falsch",
        },
        headers=_addin_headers(),
    )
    assert antwort.status_code == 400
    assert chat_tools.urlaubstext_aus(agents_io.read_vorlagen_md("info")) == "Bestand."
