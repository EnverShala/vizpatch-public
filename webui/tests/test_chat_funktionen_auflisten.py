"""Tests für `funktionen_auflisten` — die Selbstauskunft des Chat-Agenten.

Betreiber-Wunsch 2026-08-30: auf „hilfe", „was kannst du" und jede ähnliche
Formulierung soll eine kurze Liste der Fähigkeiten kommen.

**Der wichtigste Test hier ist nicht, dass etwas aufgezählt wird, sondern dass
die Auskunft WAHR und VOLLSTÄNDIG ist.** Zwei Fehlerbilder, beide schlimmer als
gar keine Antwort:

* Eine abgeschaltete Funktion als „kann ich" melden — der Betreiber wartet dann
  auf Entwürfe, die nie kommen.
* Eine abgeschaltete Funktion verschweigen — dann erfährt er nie, dass es sie
  gibt. Betreiber-Vorgabe: aufzählen, aber mit „(muss erst aktiviert werden)".

Dazu die Vollständigkeit um die Agent-Aufgaben herum (Entwürfe, Sortieren,
Urlaub): die sind keine Chat-Werkzeuge und würden in einer aus Werkzeugnamen
zusammengeratenen Liste strukturell fehlen.
"""
from __future__ import annotations

import pytest


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


def _write_agent_env(agent_id="info", **extra):
    import src.agents_io as agents_io

    werte = {
        "IMAP_USER": "info@ionos.de",
        "IMAP_PASSWORD": "imap-pw",
        "LLM_API_KEY": "sk-ant-test-key",
        "LLM_PROVIDER": "anthropic",
    }
    werte.update(extra)
    agents_io.write_env(agent_id, werte)


def _alle_punkte(ergebnis):
    return [p for g in ergebnis["faehigkeiten"] for p in g["punkte"]]


def _text_aller_punkte(ergebnis):
    return " ".join(p["text"] for p in _alle_punkte(ergebnis)).lower()


def _punkt_mit(ergebnis, begriff):
    treffer = [p for p in _alle_punkte(ergebnis) if begriff in p["text"].lower()]
    assert treffer, "kein Punkt enthält: " + begriff
    return treffer[0]


# --- Die Agent-Aufgaben dürfen nicht fehlen --------------------------------


def test_nennt_die_automatischen_agent_aufgaben(tmp_path, monkeypatch):
    """Entwürfe, Sortieren und Urlaubsantwort sind KEINE Chat-Werkzeuge — sie
    laufen im Polling-Agenten. Genau deshalb gibt es dieses Werkzeug: ohne die
    Tabelle würde das Modell ausgerechnet das Herz des Produkts vergessen."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    _write_agent_env()
    text = _text_aller_punkte(chat_tools.funktionen_auflisten("info"))

    assert "entwürfe" in text or "entwurf" in text
    assert "sortier" in text
    assert "abwesenheitsnotiz" in text or "urlaub" in text


def test_nennt_die_wichtigsten_postfach_faehigkeiten(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    _write_agent_env()
    text = _text_aller_punkte(chat_tools.funktionen_auflisten("info"))

    for begriff in ("suchen", "zusammenfassen", "papierkorb", "ordner",
                    "anhäng", "markieren"):
        assert begriff in text, "fehlt in der Selbstauskunft: " + begriff


# --- Ist-Zustand: der eigentliche Grund für das Werkzeug -------------------


def test_abgeschaltete_funktion_wird_als_abgeschaltet_gemeldet(tmp_path, monkeypatch):
    """Der Kern. `ENABLE_DRAFT_CREATION=false` heißt: es kommen keine Entwürfe.
    Das als „ich lege automatisch Entwürfe an" zu melden, wäre eine
    Falschauskunft an genau der Stelle, an der jemand nachfragt."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    _write_agent_env(ENABLE_DRAFT_CREATION="false")
    punkt = _punkt_mit(chat_tools.funktionen_auflisten("info"), "kundenanfragen")

    assert punkt.get("aktiv") is False


def test_eingeschaltete_funktion_wird_als_aktiv_gemeldet(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    _write_agent_env(ENABLE_DRAFT_CREATION="true", ENABLE_AUTO_SORT="true")
    ergebnis = chat_tools.funktionen_auflisten("info")

    assert _punkt_mit(ergebnis, "sortier").get("aktiv") is True
    assert _punkt_mit(ergebnis, "kundenanfragen").get("aktiv") is True


def test_abgeschaltete_funktion_wird_nicht_verschwiegen(tmp_path, monkeypatch):
    """Betreiber-Vorgabe 2026-08-30: „bei abgeschalteten Funktionen soll er auch
    'kann ich' sagen, aber in Klammern 'muss aktiviert werden'". Der Punkt bleibt
    also in der Liste — nur der Zustand ändert sich, nie der Umfang."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    _write_agent_env(ENABLE_AUTO_SORT="false")
    aus = chat_tools.funktionen_auflisten("info")
    _write_agent_env(ENABLE_AUTO_SORT="true")
    an = chat_tools.funktionen_auflisten("info")

    assert len(_alle_punkte(aus)) == len(_alle_punkte(an)), (
        "Die Liste wird kürzer, wenn etwas abgeschaltet ist — sie darf sich nur "
        "im Zustand unterscheiden."
    )
    assert _punkt_mit(aus, "sortier")["text"] == _punkt_mit(an, "sortier")["text"]


def test_hinweis_verlangt_kann_ich_formulierung(tmp_path, monkeypatch):
    """Der Hinweis steuert, wie das Modell formuliert. Er muss das „kann ich,
    muss aber aktiviert werden" ausdrücklich verlangen — sonst schreibt das
    Modell „das kann ich nicht", und die Funktion ist für den Betreiber
    unsichtbar."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    _write_agent_env()
    hinweis = chat_tools.funktionen_auflisten("info")["hinweis"].lower()

    assert "aktiviert werden" in hinweis
    assert "weglassen" in hinweis


# --- Form und Robustheit ---------------------------------------------------


def test_jede_gruppe_hat_punkte_und_jeder_punkt_text(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    _write_agent_env()
    ergebnis = chat_tools.funktionen_auflisten("info")

    assert ergebnis["faehigkeiten"], "keine Gruppen"
    for gruppe in ergebnis["faehigkeiten"]:
        assert gruppe["gruppe"].strip()
        assert gruppe["punkte"], "leere Gruppe: " + gruppe["gruppe"]
        for punkt in gruppe["punkte"]:
            assert punkt["text"].strip()


def test_keine_werkzeugnamen_in_den_texten(tmp_path, monkeypatch):
    """Die Liste ist für einen Menschen. `mails_in_papierkorb` gehört da nicht
    hinein — sonst liest sich die Antwort wie eine API-Dokumentation."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    _write_agent_env()
    text = _text_aller_punkte(chat_tools.funktionen_auflisten("info"))

    for name in ("mails_in_papierkorb", "ordner_auflisten", "entwurf_erstellen",
                 "mail_senden_bestaetigen", "postfach_uebersicht"):
        assert name not in text, "Werkzeugname im Auskunftstext: " + name


def test_alle_flags_der_tabelle_existieren_wirklich():
    """Driftschutz: ein Tippfehler in `flag` würde stillschweigend dazu führen,
    dass der Punkt ohne Aktiv-Zustand ausgeliefert wird — die Auskunft wäre
    dann wieder unvollständig, ohne dass es jemand merkt."""
    import src.chat_tools as chat_tools

    bekannt = set(chat_tools._EINSTELLUNG_FLAGS) | set(chat_tools._EINSTELLUNG_NUR_LESEN)
    for block in chat_tools._FAEHIGKEITEN:
        for punkt in block["punkte"]:
            flag = punkt.get("flag")
            if flag is not None:
                assert flag in bekannt, "Unbekanntes Flag in _FAEHIGKEITEN: " + flag
            # Ein Punkt darf seinen Zustand auch direkt aus einem Env-Namen
            # ziehen (z. B. ENABLE_MAIL_SEND, das der Chat nicht umstellen
            # darf und deshalb nicht in _EINSTELLUNG_FLAGS steht) — aber nicht
            # beides gleichzeitig, sonst ist unklar, was gilt.
            assert not (flag and punkt.get("env")), (
                "Punkt hat flag UND env: " + punkt["text"][:40]
            )


def test_werkzeug_ist_registriert_und_im_schema():
    import src.chat_tools as chat_tools

    assert "funktionen_auflisten" in chat_tools.TOOL_HANDLERS
    assert "funktionen_auflisten" in [t["name"] for t in chat_tools.TOOL_SCHEMAS]


def test_schema_nennt_die_ausloeser():
    """Das Modell muss erkennen, dass „hilfe" hierher gehört — die Formulierung
    des Betreibers ist offen („das waren nur Beispielformulierungen")."""
    import src.chat_tools as chat_tools

    schema = next(
        t for t in chat_tools.TOOL_SCHEMAS if t["name"] == "funktionen_auflisten"
    )
    beschreibung = schema["description"].lower()
    for ausloeser in ("hilfe", "was kannst du", "welche funktionen"):
        assert ausloeser in beschreibung


def test_invalide_agent_id_wirft(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.funktionen_auflisten("../boese")
