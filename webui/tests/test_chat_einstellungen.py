"""Tests fuer `einstellungen_lesen` / `einstellung_aendern` /
`wissen_bearbeiten` (Quick 260822-set).

Betreiber-Wunsch 2026-08-22: der Agent soll Firmenkontext, Mail-Sortierung und
die automatische Entwurfs-Erstellung auch per Chat ein-/ausschalten und
veraendern koennen.

**Der Schwerpunkt dieser Datei ist die Allowlist.** Ein Werkzeug, das
`.env`-Werte schreiben kann, ist das lohnendste Ziel fuer eine praeparierte
Mail im Postfach. Die Tests unten nageln deshalb nicht nur fest, was GEHT,
sondern vor allem was NICHT geht: Zugangsdaten, Pseudonymisierung und die
Freigabe-Gates fuer Versand/Abmelden/Loeschen.
"""
from __future__ import annotations

from unittest.mock import MagicMock

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


REGELN = "## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n"


# --- Allowlist: was NICHT geht --------------------------------------------


@pytest.mark.parametrize(
    "verboten",
    [
        "ENABLE_PII_REDACTION",
        "enable_pii_redaction",
        "ENABLE_MAIL_SEND",
        "ENABLE_UNSUBSCRIBE",
        "ENABLE_FOLDER_DELETE",
        "ENABLE_TRASH_PURGE",
        "ENABLE_CHAT_TOOLS",
        "IMAP_PASSWORD",
        "LLM_API_KEY",
        "IMAP_HOST",
        "IMAP_USER",
    ],
)
def test_sicherheitsrelevante_werte_sind_nicht_aenderbar(verboten, tmp_path, monkeypatch):
    """Der Kern des Sicherheitskonzepts. Faellt EINER dieser Faelle, kann eine
    praeparierte Mail im schlimmsten Fall die Pseudonymisierung abschalten oder
    ein Freigabe-Gate oeffnen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    vorher = dict(agents_io.read_env_raw("info"))

    ergebnis = chat_tools.einstellung_aendern("info", verboten, "false")

    assert "fehler" in ergebnis
    # Und wirklich NICHTS geschrieben — nicht nur abgelehnt gemeldet.
    assert agents_io.read_env_raw("info") == vorher


def test_ablehnung_verraet_keine_weiteren_env_namen(tmp_path, monkeypatch):
    """Die Fehlermeldung nennt die vier ERLAUBTEN Namen und spiegelt den
    angefragten zurueck — aber keine ANDEREN gesperrten, sonst waere sie eine
    Landkarte fuer den naechsten Versuch."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    text = chat_tools.einstellung_aendern("info", "IMAP_PASSWORD", "x")["fehler"]

    assert "automatisches_sortieren" in text
    for fremd in ("ENABLE_PII_REDACTION", "ENABLE_MAIL_SEND", "LLM_API_KEY",
                  "ENABLE_CHAT_TOOLS", "ENABLE_FOLDER_DELETE"):
        assert fremd not in text


def test_ablehnung_kappt_den_zurueckgespiegelten_namen(tmp_path, monkeypatch):
    """Sonst waere die Fehlermeldung ein Kanal, ueber den ein praeparierter
    Mailinhalt beliebig langen Text in den Chat schreibt.

    Gemessen wird, dass die Laenge NICHT von der Eingabe abhaengt: eine 500
    Zeichen lange Anfrage darf die Meldung um hoechstens die 40 gekappten
    Zeichen laenger machen als eine kurze. Frueher stand hier eine feste
    Obergrenze (400) — die misst am Ziel vorbei, weil sie mit jedem neuen,
    voellig harmlosen Schalter in der Allowlist wackelt, waehrend der
    eigentliche Kanal (die Eingabe) unbeobachtet bliebe."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    lang = chat_tools.einstellung_aendern("info", "A" * 500, "x")["fehler"]
    kurz = chat_tools.einstellung_aendern("info", "x", "x")["fehler"]

    assert "A" * 500 not in lang
    assert "A" * 41 not in lang          # auf 40 Zeichen gekappt
    assert len(lang) - len(kurz) <= 40   # Laenge haengt nicht an der Eingabe


def test_unbekannter_name_wird_abgelehnt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    assert "fehler" in chat_tools.einstellung_aendern("info", "irgendwas", "true")


def test_unbekannter_wissensbereich_wird_abgelehnt(tmp_path, monkeypatch):
    """Insbesondere kein Schreibzugriff auf style.md — die entsteht automatisch
    aus dem Gesendet-Ordner und wuerde mit dem Re-Learn-Lauf kollidieren."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    for bereich in ("style", "stil", "env", ".env", "passwort"):
        assert "fehler" in chat_tools.wissen_bearbeiten("info", bereich, "ergaenzen", "x")


# --- Schalter setzen -----------------------------------------------------


@pytest.mark.parametrize("eingabe,erwartet", [
    ("true", "true"), ("an", "true"), ("ein", "true"), ("ja", "true"), ("1", "true"),
    ("false", "false"), ("aus", "false"), ("nein", "false"), ("0", "false"),
])
def test_ein_aus_wird_umgangssprachlich_verstanden(eingabe, erwartet, tmp_path, monkeypatch):
    """Der Betreiber sagt „aus", nicht „false"."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    e = chat_tools.einstellung_aendern("info", "automatische_entwuerfe", eingabe)

    assert e["nachher"] == erwartet
    assert agents_io.read_env_raw("info")["ENABLE_DRAFT_CREATION"] == erwartet


def test_unsinniger_wert_wird_abgelehnt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    assert "fehler" in chat_tools.einstellung_aendern("info", "automatische_entwuerfe", "vielleicht")


def test_vorher_und_nachher_werden_gemeldet(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_DRAFT_CREATION="true")
    import src.chat_tools as chat_tools

    e = chat_tools.einstellung_aendern("info", "automatische_entwuerfe", "aus")

    assert e["vorher"] == "true"
    assert e["nachher"] == "false"
    assert e["geaendert"] is True
    assert "Poll-Zyklus" in e["hinweis"]


def test_unveraenderter_wert_wird_als_solcher_gemeldet(tmp_path, monkeypatch):
    """Sonst meldet der Agent „habe ich ausgeschaltet", obwohl es schon aus war."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_AUTO_SORT="false")
    import src.chat_tools as chat_tools

    e = chat_tools.einstellung_aendern("info", "automatisches_sortieren", "aus")

    assert e["geaendert"] is False
    assert "schon" in e["hinweis"]


@pytest.mark.parametrize("wert", ["0", "201", "-5", "viele"])
def test_max_mails_ausserhalb_der_grenzen_wird_abgelehnt(wert, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    assert "fehler" in chat_tools.einstellung_aendern("info", "sortieren_max_mails", wert)


def test_max_mails_innerhalb_der_grenzen_wird_gesetzt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    e = chat_tools.einstellung_aendern("info", "sortieren_max_mails", "120")

    assert e["nachher"] == "120"
    assert agents_io.read_env_raw("info")["AUTO_SORT_MAX_MAILS"] == "120"


# --- Wirkungslose Zustaende verhindern -----------------------------------


def test_sortieren_laesst_sich_ohne_kategorien_nicht_einschalten(tmp_path, monkeypatch):
    """Sonst waere die Funktion an, taete nichts, und der Agent meldete
    „eingeschaltet" — der Betreiber wartet dann auf eine Wirkung, die nie
    kommt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    e = chat_tools.einstellung_aendern("info", "automatisches_sortieren", "an")

    assert "fehler" in e
    assert "Kategorie" in e["fehler"]
    assert "wissen_bearbeiten" in e["fehler"]
    assert agents_io.read_env_raw("info").get("ENABLE_AUTO_SORT") in (None, "", "false")


def test_sortieren_mit_kategorien_laesst_sich_einschalten(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.einstellung_aendern("info", "automatisches_sortieren", "an")

    assert e["nachher"] == "true"


def test_einschalten_bei_aktivem_probelauf_warnt(tmp_path, monkeypatch):
    """Probelauf ist per Default AN. Ohne diesen Hinweis meldet der Agent
    „sortiere ab jetzt", bewegt aber nichts."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.einstellung_aendern("info", "automatisches_sortieren", "an")

    assert "achtung" in e
    assert "Probelauf" in e["achtung"]


def test_ohne_probelauf_keine_warnung(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(AUTO_SORT_DRY_RUN="false")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.einstellung_aendern("info", "automatisches_sortieren", "an")

    assert "achtung" not in e


# --- Lesen ---------------------------------------------------------------


def test_lesen_nennt_die_vier_schalter_mit_defaults(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    e = chat_tools.einstellungen_lesen("info")

    assert e["einstellungen"]["automatische_entwuerfe"] is True   # Default an
    assert e["einstellungen"]["automatisches_sortieren"] is False  # Default aus
    assert e["einstellungen"]["sortieren_probelauf"] is True       # Default an
    assert e["einstellungen"]["sortieren_max_mails"] == 50


def test_lesen_enthaelt_keine_zugangsdaten(tmp_path, monkeypatch):
    """Das Ergebnis geht ans Modell und in den Chat-Verlauf."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    roh = str(chat_tools.einstellungen_lesen("info"))

    assert "imap-pw" not in roh
    assert "sk-ant" not in roh
    assert "IMAP_PASSWORD" not in roh
    assert "LLM_API_KEY" not in roh


def test_lesen_warnt_wenn_sortieren_an_aber_probelauf_auch(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_AUTO_SORT="true", AUTO_SORT_DRY_RUN="true")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.einstellungen_lesen("info")

    assert "achtung" in e
    assert "NICHTS" in e["achtung"]


def test_lesen_warnt_wenn_sortieren_an_aber_keine_kategorien(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_AUTO_SORT="true", AUTO_SORT_DRY_RUN="false")
    import src.chat_tools as chat_tools

    e = chat_tools.einstellungen_lesen("info")

    assert "achtung" in e
    assert "keine gültige Kategorie" in e["achtung"]


def test_lesen_zeigt_kategorien_und_kontextumfang(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_context_md_atomic("info", "# Firma\nWir sind die Muster GmbH.\n")
    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.einstellungen_lesen("info")

    assert e["firmenkontext_zeichen"] > 0
    assert "Muster GmbH" in e["firmenkontext_anfang"]
    assert e["sortierregeln_kategorien"] == [
        {"name": "Rechnungen", "aktion": "verschieben", "ziel": "Rechnungen"}
    ]


# --- Wissen bearbeiten ---------------------------------------------------


def test_ergaenzen_haengt_an_statt_zu_ueberschreiben(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_context_md_atomic("info", "# Firma\nAlter Inhalt.\n")

    e = chat_tools.wissen_bearbeiten(
        "info", "firmenkontext", "ergaenzen", "Samstags bis 14 Uhr offen.")

    inhalt = agents_io.read_context_md("info")
    assert "Alter Inhalt." in inhalt
    assert "Samstags bis 14 Uhr offen." in inhalt
    assert e["modus"] == "ergaenzen"
    assert e["zeichen_nachher"] > e["zeichen_vorher"]


def test_ersetzen_ueberschreibt_und_sagt_es_deutlich(tmp_path, monkeypatch):
    """Wochenlang aufgebautes Betriebswissen darf nicht stillschweigend
    verschwinden."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_context_md_atomic("info", "# Firma\nAlter Inhalt.\n")

    e = chat_tools.wissen_bearbeiten("info", "firmenkontext", "ersetzen", "Ganz neu.")

    inhalt = agents_io.read_context_md("info")
    assert "Alter Inhalt." not in inhalt
    assert "Ganz neu." in inhalt
    assert "ersetzt" in e
    assert "ERSETZT" in e["ersetzt"]


def test_ersetzen_legt_eine_sicherung_an(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_context_md_atomic("info", "# Firma\nWichtiges Altwissen.\n")
    chat_tools.wissen_bearbeiten("info", "firmenkontext", "ersetzen", "Neu.")

    bak = agents_io._agent_dir("info") / "firmenkontext.bak"
    assert bak.exists()
    assert "Wichtiges Altwissen." in bak.read_text(encoding="utf-8")


def test_ergaenzen_auf_leere_datei_geht(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    e = chat_tools.wissen_bearbeiten("info", "firmenkontext", "ergaenzen", "Erster Satz.")

    assert "fehler" not in e
    assert "Erster Satz." in agents_io.read_context_md("info")


def test_leerer_text_aendert_nichts(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_context_md_atomic("info", "Bestand.\n")

    e = chat_tools.wissen_bearbeiten("info", "firmenkontext", "ersetzen", "   ")

    assert "fehler" in e
    assert agents_io.read_context_md("info") == "Bestand.\n"


def test_zu_langer_text_wird_abgelehnt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    e = chat_tools.wissen_bearbeiten(
        "info", "firmenkontext", "ersetzen", "x" * (chat_tools.MAX_WISSEN_ZEICHEN + 1))

    assert "fehler" in e


def test_kaputte_sortierregeln_werden_nicht_gespeichert(tmp_path, monkeypatch):
    """Sonst stuenden dort Regeln, die der Agent still ignoriert — und der
    Betreiber glaubt, sortiert zu haben."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.wissen_bearbeiten(
        "info", "sortierregeln", "ersetzen", "Verschieb halt die Rechnungen irgendwohin")

    assert "fehler" in e
    assert "Aktion:" in e["fehler"]
    # Bestand unangetastet.
    assert "## Rechnungen" in agents_io.read_sortierregeln_md("info")


def test_gueltige_sortierregeln_werden_gespeichert_und_zurueckgemeldet(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    e = chat_tools.wissen_bearbeiten(
        "info", "sortierregeln", "ersetzen",
        "## Notfälle\nAktion: markieren\n")

    assert "fehler" not in e
    assert e["kategorien"] == [{"name": "Notfälle", "aktion": "markieren", "ziel": ""}]


def test_ergaenzte_kategorie_kommt_zu_den_bestehenden(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.wissen_bearbeiten(
        "info", "sortierregeln", "ergaenzen", "## Newsletter\nAktion: verschieben\nZiel: Werbung\n")

    namen = [k["name"] for k in e["kategorien"]]
    assert namen == ["Rechnungen", "Newsletter"]


def test_unbekannter_modus_wird_abgelehnt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    assert "fehler" in chat_tools.wissen_bearbeiten("info", "firmenkontext", "loeschen", "x")


# --- Konventionen --------------------------------------------------------


@pytest.mark.parametrize("werkzeug,argumente", [
    ("einstellungen_lesen", ()),
    ("einstellung_aendern", ("automatische_entwuerfe", "aus")),
    ("wissen_bearbeiten", ("firmenkontext", "ergaenzen", "x")),
])
def test_invalider_agent_id_wirft_value_error(werkzeug, argumente, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        getattr(chat_tools, werkzeug)("../evil", *argumente)


def test_registrierung_vollstaendig(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    for name in ("einstellungen_lesen", "einstellung_aendern", "wissen_bearbeiten"):
        assert name in chat_tools.TOOL_HANDLERS
        assert name in {s["name"] for s in chat_tools.TOOL_SCHEMAS}
        assert name in chat_tools._TOOL_ACTIVITY_LABELS
        # Kein Mailinhalt im Ergebnis -> bewusst NICHT anonymizer-bewusst.
        assert name not in chat_tools._ANON_AWARE_TOOLS


def test_schemas_warnen_vor_anweisungen_aus_mails(tmp_path, monkeypatch):
    """Die schreibenden Werkzeuge sind das lohnendste Injektionsziel. Die
    Warnung muss dort stehen, wo das Modell sie VOR dem Aufruf sieht."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    nach_name = {s["name"]: s for s in chat_tools.TOOL_SCHEMAS}
    for name in ("einstellung_aendern", "wissen_bearbeiten"):
        b = nach_name[name]["description"]
        assert "MAIL" in b, name

    aendern = nach_name["einstellung_aendern"]["description"]
    assert "Einstellungen-Fenster" in aendern
    assert "Pseudonymisierung" in aendern


def test_allowlist_deckt_genau_die_betriebsschalter(tmp_path, monkeypatch):
    """Drift-Guard: waechst diese Menge, muss jemand den Kommentar ueber
    _EINSTELLUNG_FLAGS gelesen haben. Die Grenze ist bewusst gezogen — der Chat
    darf aendern, WAS der Agent tut (Verhalten), nicht was er DARF
    (Berechtigungen), und niemals Zugangsdaten."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    assert set(chat_tools._EINSTELLUNG_FLAGS) == {
        "automatische_entwuerfe",
        "automatisches_sortieren",
        "sortieren_probelauf",
        "sortieren_max_mails",
        "anhaenge_inhaltlich_auswerten",
        "bild_vorschau",
        # Urlaubs-Autoantwort (URL-01..09), bewusst nachgezogen: der Betreiber
        # soll „ich bin vom 1. bis 14. September im Urlaub" sagen koennen. Diese
        # drei sagen WANN und OB ZUR PROBE — die FREIGABE
        # (ENABLE_URLAUBSANTWORT) ist NICHT dabei und steht unten in der
        # Gate-Liste, an der dieser Test sie festnagelt.
        "urlaub_probelauf",
        "urlaub_von",
        "urlaub_bis",
        # Intelligenz-Modus (2026-08-24): eine Kosten-/Qualitaetsstellung, keine
        # Berechtigung — er aendert WIE gut der Agent hinsieht, nicht WAS er
        # darf. Damit auf der richtigen Seite der Trennlinie.
        "intelligenz_modus",
    }
    assert set(chat_tools._WISSEN_BEREICHE) == {
        "firmenkontext", "sortierregeln", "vorlagen"
    }
    env_namen = {e["env"] for e in chat_tools._EINSTELLUNG_FLAGS.values()}
    assert env_namen == {
        "ENABLE_DRAFT_CREATION",
        "ENABLE_AUTO_SORT",
        "AUTO_SORT_DRY_RUN",
        "AUTO_SORT_MAX_MAILS",
        "ENABLE_ATTACHMENT_VISION",
        "ENABLE_BILD_VORSCHAU",
        "URLAUB_DRY_RUN",
        "URLAUB_VON",
        "URLAUB_BIS",
        "INTELLIGENZ_MODUS",
    }
    # Keine BERECHTIGUNG in der Allowlist — das ist die eigentliche Zusicherung.
    # ENABLE_URLAUBSANTWORT steht hier, seit es die Urlaubs-Autoantwort gibt:
    # es ist das Gate fuer die EINZIGE Funktion des Produkts, die selbstaendig
    # Mails verschickt, und damit genau die Art Wert, die der Chat nicht
    # anfassen darf. Geaendert wird es ausschliesslich im Einstellungen-Fenster
    # (Browser-Formular bzw. Add-in-Dialog).
    for gate in ("ENABLE_PII_REDACTION", "ENABLE_MAIL_SEND", "ENABLE_UNSUBSCRIBE",
                 "ENABLE_FOLDER_DELETE", "ENABLE_TRASH_PURGE", "ENABLE_CHAT_TOOLS",
                 "ENABLE_URLAUBSANTWORT"):
        assert gate not in env_namen

    # Nur-Lese-Werte (im Ergebnis von einstellungen_lesen, aber NICHT aenderbar).
    assert set(chat_tools._EINSTELLUNG_NUR_LESEN) == {"urlaubsantwort_freigegeben"}
    assert set(chat_tools._EINSTELLUNG_NUR_LESEN) & set(chat_tools._EINSTELLUNG_FLAGS) == set()


# --- Vorlagen (Betreiber-Nachtrag 2026-08-22) ---------------------------


def test_vorlage_anlegen_und_ergaenzen(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    chat_tools.wissen_bearbeiten(
        "info", "vorlagen", "ergaenzen", "## Terminanfrage\nGern, wann passt es Ihnen?\n")
    chat_tools.wissen_bearbeiten(
        "info", "vorlagen", "ergaenzen", "## Mahnung\nBitte begleichen Sie …\n")

    inhalt = agents_io.read_vorlagen_md("info")
    assert "## Terminanfrage" in inhalt
    assert "## Mahnung" in inhalt


def test_vorlage_entfernen(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic(
        "info", "## Terminanfrage\nGern.\n\n## Mahnung\nBitte zahlen.\n")

    e = chat_tools.wissen_bearbeiten("info", "vorlagen", "entfernen", "Mahnung")

    inhalt = agents_io.read_vorlagen_md("info")
    assert "## Mahnung" not in inhalt
    assert "Bitte zahlen." not in inhalt
    assert "## Terminanfrage" in inhalt
    assert e["modus"] == "entfernen"
    assert e["entfernter_abschnitt"] == "Mahnung"


def test_entfernen_ist_gross_klein_unabhaengig(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic("info", "## Terminanfrage\nGern.\n")

    e = chat_tools.wissen_bearbeiten("info", "vorlagen", "entfernen", "terminANFRAGE")

    assert "fehler" not in e
    assert "Terminanfrage" not in agents_io.read_vorlagen_md("info")


def test_entfernen_eines_unbekannten_abschnitts_nennt_die_vorhandenen(tmp_path, monkeypatch):
    """Ein 'entfernt' auf eine Vorlage, die es nicht gibt, waere eine
    Falschmeldung — und der Agent soll nicht raten, wie sie heisst."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic("info", "## Terminanfrage\nGern.\n")

    e = chat_tools.wissen_bearbeiten("info", "vorlagen", "entfernen", "Gibtsnicht")

    assert "fehler" in e
    assert e["vorhandene_abschnitte"] == ["Terminanfrage"]
    # Bestand unangetastet.
    assert "## Terminanfrage" in agents_io.read_vorlagen_md("info")


def test_entfernen_laesst_unterabschnitte_beim_abschnitt(tmp_path, monkeypatch):
    """'###' beendet einen Abschnitt NICHT — sonst blieben Reste einer
    entfernten Vorlage stehen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic(
        "info", "## A\n### Unterpunkt\nDetail.\n\n## B\nB-Text.\n")

    chat_tools.wissen_bearbeiten("info", "vorlagen", "entfernen", "A")

    inhalt = agents_io.read_vorlagen_md("info")
    assert "Unterpunkt" not in inhalt
    assert "Detail." not in inhalt
    assert "B-Text." in inhalt


def test_letzte_kategorie_entfernen_geht_aber_warnt(tmp_path, monkeypatch):
    """Beim ENTFERNEN gilt der Kategorien-Zwang nicht — der Betreiber darf auch
    die letzte loswerden. Ist das Sortieren dann noch an, muss das im Ergebnis
    stehen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_AUTO_SORT="true", AUTO_SORT_DRY_RUN="false")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.wissen_bearbeiten("info", "sortierregeln", "entfernen", "Rechnungen")

    assert "fehler" not in e
    assert e["kategorien"] == []
    assert "achtung" in e
    assert "nichts mehr" in e["achtung"]


def test_letzte_kategorie_entfernen_ohne_aktives_sortieren_warnt_nicht(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_sortierregeln_md_atomic("info", REGELN)

    e = chat_tools.wissen_bearbeiten("info", "sortierregeln", "entfernen", "Rechnungen")

    assert "achtung" not in e


def test_entfernen_ohne_namen_wird_abgelehnt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    e = chat_tools.wissen_bearbeiten("info", "vorlagen", "entfernen", "   ")

    assert "fehler" in e
    assert "Abschnittsname" in e["fehler"]


def test_lesen_nennt_die_vorlagen_namen_ohne_texte(tmp_path, monkeypatch):
    """Die vollen Texte wuerden das Ergebnis und den Chat-Verlauf fluten."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic(
        "info", "## Terminanfrage\nGeheimer Vorlagentext.\n\n## Mahnung\nX.\n")

    e = chat_tools.einstellungen_lesen("info")

    assert e["vorlagen"] == ["Terminanfrage", "Mahnung"]
    assert "Geheimer Vorlagentext." not in str(e)


def test_neue_schalter_sind_setzbar(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    chat_tools.einstellung_aendern("info", "anhaenge_inhaltlich_auswerten", "aus")
    chat_tools.einstellung_aendern("info", "bild_vorschau", "aus")

    env = agents_io.read_env_raw("info")
    assert env["ENABLE_ATTACHMENT_VISION"] == "false"
    assert env["ENABLE_BILD_VORSCHAU"] == "false"


# --- Rückgängig (Betreiber-Nachtrag 2026-08-22) --------------------------
#
# Der Betreiber sagt "mach das rückgängig" — also MUSS es über dasselbe
# `letzte_aktion_rueckgaengig` laufen wie bei Mail-Aktionen, nicht über ein
# eigenes Werkzeug, das er sich merken müsste.


def test_geaenderter_schalter_laesst_sich_rueckgaengig_machen(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_DRAFT_CREATION="true")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    chat_tools.einstellung_aendern("info", "automatische_entwuerfe", "aus")
    assert agents_io.read_env_raw("info")["ENABLE_DRAFT_CREATION"] == "false"

    e = chat_tools.letzte_aktion_rueckgaengig("info")

    assert agents_io.read_env_raw("info")["ENABLE_DRAFT_CREATION"] == "true"
    assert "fehler" not in e


def test_unveraenderter_schalter_landet_nicht_im_journal(tmp_path, monkeypatch):
    """Sonst würde ein „mach das rückgängig" eine Nicht-Änderung zurückdrehen
    und die eigentlich gemeinte Aktion verdecken."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_AUTO_SORT="false")
    import src.chat_tools as chat_tools

    chat_tools.einstellung_aendern("info", "automatisches_sortieren", "aus")

    e = chat_tools.letzte_aktion_rueckgaengig("info")
    assert "fehler" in e


def test_ergaenzter_firmenkontext_laesst_sich_rueckgaengig_machen(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_context_md_atomic("info", "# Firma\nAlter Stand.\n")
    chat_tools.wissen_bearbeiten("info", "firmenkontext", "ergaenzen", "Neuer Satz.")
    assert "Neuer Satz." in agents_io.read_context_md("info")

    chat_tools.letzte_aktion_rueckgaengig("info")

    inhalt = agents_io.read_context_md("info")
    assert "Neuer Satz." not in inhalt
    assert "Alter Stand." in inhalt


def test_ersetzter_firmenkontext_kommt_zurueck(tmp_path, monkeypatch):
    """Der teuerste Fall: 'schreib das Firmenwissen neu' und dann 'nein, doch
    nicht'."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_context_md_atomic("info", "# Firma\nWochenlang aufgebaut.\n")
    chat_tools.wissen_bearbeiten("info", "firmenkontext", "ersetzen", "Alles neu.")

    chat_tools.letzte_aktion_rueckgaengig("info")

    assert "Wochenlang aufgebaut." in agents_io.read_context_md("info")


def test_entfernte_vorlage_kommt_zurueck(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic("info", "## Mahnung\nBitte zahlen.\n")
    chat_tools.wissen_bearbeiten("info", "vorlagen", "entfernen", "Mahnung")
    assert "## Mahnung" not in agents_io.read_vorlagen_md("info")

    chat_tools.letzte_aktion_rueckgaengig("info")

    assert "## Mahnung" in agents_io.read_vorlagen_md("info")


def test_erstmalig_angelegte_datei_wird_wieder_leer(tmp_path, monkeypatch):
    """Auch der leere Vorzustand wird gesichert — sonst liesse sich das
    ERSTMALIGE Anlegen nicht zurückdrehen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    chat_tools.wissen_bearbeiten("info", "vorlagen", "ergaenzen", "## Neu\nText.\n")
    assert "## Neu" in agents_io.read_vorlagen_md("info")

    chat_tools.letzte_aktion_rueckgaengig("info")

    assert agents_io.read_vorlagen_md("info").strip() == ""


def test_rueckgaengig_meldet_die_eine_sicherungsstufe(tmp_path, monkeypatch):
    """Zwei Änderungen hintereinander lassen sich nur EINMAL zurückdrehen —
    das muss im Ergebnis stehen, statt mehr zu versprechen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_context_md_atomic("info", "Stand A.\n")
    chat_tools.wissen_bearbeiten("info", "firmenkontext", "ersetzen", "Stand B.")

    e = chat_tools.letzte_aktion_rueckgaengig("info")

    text = str(e)
    assert "EINE Sicherungsstufe" in text or "eine Sicherungsstufe" in text


def test_gesperrter_schluessel_wird_auch_ueber_das_journal_nicht_geschrieben(tmp_path, monkeypatch):
    """Schrumpft die Allowlist später, darf ein ALTER Journal-Eintrag nicht der
    Weg sein, einen inzwischen gesperrten Wert doch zu setzen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info", "einstellung:gibt_es_nicht_mehr", "true", "false",
        [{"uid": "", "message_id": ""}])

    e = chat_tools.letzte_aktion_rueckgaengig("info")

    assert "fehler" in str(e)


# --- Intelligenz-Modus (Betreiber-Wunsch 2026-08-24) ---------------------


def test_intelligenz_modus_default_ist_standard(tmp_path, monkeypatch):
    """Ein Postfach, dessen `.env` die Variable nicht kennt, gilt als
    'standard' — ein Update darf niemandem still das Dreifache berechnen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.einstellungen_lesen("info")
    assert ergebnis["einstellungen"]["intelligenz_modus"] == "standard"


def test_intelligenz_modus_auf_hoch_stellen(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.einstellung_aendern("info", "intelligenz_modus", "hoch")
    assert "fehler" not in ergebnis
    assert agents_io.read_env_raw("info")["INTELLIGENZ_MODUS"] == "hoch"


@pytest.mark.parametrize(
    "gesagt,erwartet",
    [
        ("hoch", "hoch"),
        ("HOCH", "hoch"),
        ("high", "hoch"),
        ("High Intelligence", "hoch"),
        ("hohe intelligenz", "hoch"),
        ("genauer", "hoch"),
        ("standard", "standard"),
        ("normal", "standard"),
        ("sparsam", "standard"),
    ],
)
def test_umgangssprache_wird_verstanden(tmp_path, monkeypatch, gesagt, erwartet):
    """Der Betreiber sagt im Chat nicht den technischen Wert. Sagt er 'stell auf
    High Intelligence', darf das Modell nicht an der Wertliste scheitern."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    assert "fehler" not in chat_tools.einstellung_aendern("info", "intelligenz_modus", gesagt)
    assert agents_io.read_env_raw("info")["INTELLIGENZ_MODUS"] == erwartet


@pytest.mark.parametrize("wert", ["maximum", "true", "42", "", "sehr hoch aber sparsam"])
def test_unbekannter_wert_wird_abgelehnt_statt_geraten(tmp_path, monkeypatch, wert):
    """Beim SCHREIBEN wird ein unklarer Wert abgelehnt — anders als beim Lesen,
    wo auf 'standard' zurückgefallen wird. Eine stille Falschsetzung kostet hier
    Geld; eine Rückfrage kostet nichts."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.einstellung_aendern("info", "intelligenz_modus", wert)
    assert "fehler" in ergebnis
    assert "standard" in ergebnis["fehler"] and "hoch" in ergebnis["fehler"]
    assert "INTELLIGENZ_MODUS" not in agents_io.read_env_raw("info")


def test_kaputter_wert_in_der_env_wird_als_standard_berichtet(tmp_path, monkeypatch):
    """Gegenstück: steht bereits Unsinn in der `.env` (von Hand editiert), muss
    der Bericht dasselbe sagen wie der Agent tut — und der fällt auf 'standard'
    zurück, statt zu streiken."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(INTELLIGENZ_MODUS="ganz-hoch")
    import src.chat_tools as chat_tools

    assert chat_tools.einstellungen_lesen("info")["einstellungen"]["intelligenz_modus"] == "standard"


def test_beschreibung_nennt_die_kosten_und_die_grenze(tmp_path, monkeypatch):
    """Der Hinweis muss zwei Dinge sagen: dass es mehr kostet, und dass es
    NICHT ändert, ob überhaupt gesendet/sortiert wird. Sonst schaltet jemand ihn
    ein und erwartet etwas anderes."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    text = chat_tools._EINSTELLUNG_FLAGS["intelligenz_modus"]["beschreibung"].lower()
    assert "kostet" in text or "tokens" in text
    assert "nicht" in text
