"""`urlaub_eintragen` / `urlaub_beenden` — die Urlaubs-Autoantwort per Chat.

Betreiber-Auftrag 2026-08-23: „die option soll natürlich auch über den
agentenchat ausschaltbar oder einschaltbar (mit zeitraumangabe) sein" —
ausdrücklich inklusive der Freigabe, denn „im urlaub ist keiner da um zu
versenden". Damit ist die vorherige Einschränkung (Freigabe nur im
Einstellungen-Fenster) bewusst aufgehoben.

**Warum zwei dedizierte Werkzeuge statt Einträgen in `_EINSTELLUNG_FLAGS`.**
Das Einschalten braucht vier Dinge gleichzeitig: Freigabe, Von-Datum,
Bis-Datum und einen ausgeschalteten Probelauf — plus eine vorhandene
`## Urlaub`-Vorlage. Über vier Einzelschalter wäre der wahrscheinlichste
Ausgang ein Zustand gewesen, der aussieht wie „an" und trotzdem nichts
verschickt: das Einschalten hätte am fehlenden Zeitraum scheitern müssen, der
Zeitraum ohne Freigabe nichts bewirkt, und der Probelauf wäre schlicht
vergessen worden. Im Urlaub sieht niemand nach. Die Tests hier nageln fest,
dass ein Aufruf entweder vollständig wirkt oder gar nichts schreibt.
"""
from __future__ import annotations

import pytest


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


def _agent(vorlage="## Urlaub\nIch bin weg.\n", **extra):
    import src.agents_io as agents_io

    werte = {
        "IMAP_USER": "info@ionos.de",
        "IMAP_PASSWORD": "imap-pw",
        "LLM_API_KEY": "sk-ant-test-key",
    }
    werte.update(extra)
    agents_io.write_env("info", werte)
    if vorlage is not None:
        agents_io.write_vorlagen_md_atomic("info", vorlage)


# --- Einschalten ----------------------------------------------------------


def test_setzt_alles_in_einem_schritt(tmp_path, monkeypatch):
    """Der Kern: EIN Aufruf, und der Agent verschickt tatsächlich."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")

    assert "fehler" not in ergebnis
    env = agents_io.read_env_raw("info")
    assert env["ENABLE_URLAUBSANTWORT"] == "true"
    assert env["URLAUB_VON"] == "2026-09-01"
    assert env["URLAUB_BIS"] == "2026-09-14"
    # Der Punkt, der über Einzelschalter regelmäßig vergessen worden wäre:
    assert env["URLAUB_DRY_RUN"] == "false"


def test_ergebnis_sagt_ausdruecklich_dass_verschickt_wird(tmp_path, monkeypatch):
    """Das Modell soll dem Betreiber nicht bloß „eingetragen" melden."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")
    assert "SCHARF" in ergebnis["achtung"]


def test_probelauf_variante_verschickt_nichts(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.urlaub_eintragen(
        "info", "2026-09-01", "2026-09-14", nur_probelauf=True
    )
    assert agents_io.read_env_raw("info")["URLAUB_DRY_RUN"] == "true"
    assert "PROBELAUF" in ergebnis["achtung"]


def test_wiederholtes_eintragen_ueberschreibt(tmp_path, monkeypatch):
    """„Doch eine Woche länger" muss ohne Umweg gehen."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")
    chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-21")

    assert agents_io.read_env_raw("info")["URLAUB_BIS"] == "2026-09-21"


# --- Vorbedingungen: es wird NICHTS geschrieben ---------------------------


def test_ohne_vorlage_wird_nichts_geschrieben(tmp_path, monkeypatch):
    """Der wichtigste Verzichtstest: lieber gar nicht einschalten als einen
    Zustand herstellen, der aussieht wie „an" und nichts tut."""
    _setup_env(tmp_path, monkeypatch)
    _agent(vorlage="## Terminanfrage\nHallo\n")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    vorher = dict(agents_io.read_env_raw("info"))
    ergebnis = chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")

    assert "fehler" in ergebnis
    assert "## Urlaub" in ergebnis["fehler"]
    assert agents_io.read_env_raw("info") == vorher


def test_ohne_vorlagen_datei_wird_nichts_geschrieben(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent(vorlage=None)
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    vorher = dict(agents_io.read_env_raw("info"))
    assert "fehler" in chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")
    assert agents_io.read_env_raw("info") == vorher


@pytest.mark.parametrize(
    "von,bis",
    [
        ("01.09.2026", "14.09.2026"),
        ("09/01/2026", "09/14/2026"),
        ("2026-13-01", "2026-13-14"),
        ("2026-02-31", "2026-03-01"),
        ("morgen", "uebermorgen"),
    ],
)
def test_nicht_iso_datum_wird_abgelehnt(von, bis, tmp_path, monkeypatch):
    """Kein Raten: ein um Monate verschobener Urlaub verschickt entweder keine
    oder ganz falsche Autoantworten."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.urlaub_eintragen("info", von, bis)
    assert "fehler" in ergebnis
    assert "JJJJ-MM-TT" in ergebnis["fehler"]
    assert "ENABLE_URLAUBSANTWORT" not in agents_io.read_env_raw("info")


def test_fehlendes_datum_wird_abgelehnt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.chat_tools as chat_tools

    assert "fehler" in chat_tools.urlaub_eintragen("info", "2026-09-01", "")
    assert "fehler" in chat_tools.urlaub_eintragen("info", "", "2026-09-14")


def test_bis_vor_von_wird_abgelehnt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.urlaub_eintragen("info", "2026-09-14", "2026-09-01")
    assert "fehler" in ergebnis
    assert "ENABLE_URLAUBSANTWORT" not in agents_io.read_env_raw("info")


def test_ein_tag_urlaub_ist_gueltig(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.chat_tools as chat_tools

    assert "fehler" not in chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-01")


def test_fehlermeldung_kappt_die_eingabe(tmp_path, monkeypatch):
    """Wie bei `einstellung_aendern`: kein Kanal, über den ein präparierter
    Mailinhalt beliebig langen Text in den Chat schreibt."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.chat_tools as chat_tools

    text = chat_tools.urlaub_eintragen("info", "A" * 500, "2026-09-14")["fehler"]
    assert "A" * 500 not in text
    assert "A" * 21 not in text


# --- Ausschalten ----------------------------------------------------------


def test_beenden_schaltet_ab(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")
    ergebnis = chat_tools.urlaub_beenden("info")

    assert ergebnis["aktiv"] is False
    assert ergebnis["geaendert"] is True
    assert agents_io.read_env_raw("info")["ENABLE_URLAUBSANTWORT"] == "false"


def test_beenden_laesst_den_zeitraum_stehen(tmp_path, monkeypatch):
    """„Ich bin zurück" heißt nicht, dass die Daten falsch waren — und ein
    Zeitraum ohne Freigabe bewirkt nichts."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")
    chat_tools.urlaub_beenden("info")

    env = agents_io.read_env_raw("info")
    assert env["URLAUB_VON"] == "2026-09-01"
    assert env["URLAUB_BIS"] == "2026-09-14"


def test_beenden_wenn_schon_aus(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.urlaub_beenden("info")
    assert ergebnis["aktiv"] is False
    assert ergebnis["geaendert"] is False


# --- Journal und Abgrenzung ----------------------------------------------


def test_beides_landet_im_journal(tmp_path, monkeypatch):
    """Damit „mach das rückgängig" auch hier greift."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")
    letzte = action_journal.last_action("info")
    assert letzte is not None
    assert letzte["aktion"] == "einstellung:urlaubsantwort"
    # quell_ordner traegt den Zustand VORHER — hier "aus", weil frisch angelegt.
    assert letzte["quell_ordner"].startswith("aus|")

    chat_tools.urlaub_beenden("info")
    letzte = action_journal.last_action("info")
    assert letzte["quell_ordner"].startswith("an|")


def test_einstellung_aendern_schaltet_die_freigabe_nicht(tmp_path, monkeypatch):
    """Es gibt genau EINEN Weg, den Versand einzuschalten — und der prüft die
    Vorbedingungen. Ein zweiter, ungeprüfter Weg über `einstellung_aendern`
    würde die Prüfungen aushebelbar machen (dann könnte der Agent scharf
    stehen, ohne dass eine Vorlage existiert)."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    vorher = dict(agents_io.read_env_raw("info"))
    for name in ("urlaubsantwort", "ENABLE_URLAUBSANTWORT", "urlaubsantwort_freigegeben"):
        assert "fehler" in chat_tools.einstellung_aendern("info", name, "true")
    assert agents_io.read_env_raw("info") == vorher


def test_zeitraum_feinjustierung_bleibt_ueber_einstellung_aendern_moeglich(tmp_path, monkeypatch):
    """„Verlänger den Urlaub um drei Tage" soll ohne das große Werkzeug gehen —
    der Zeitraum sagt WANN, nicht OB."""
    _setup_env(tmp_path, monkeypatch)
    _agent()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    chat_tools.urlaub_eintragen("info", "2026-09-01", "2026-09-14")
    assert "fehler" not in chat_tools.einstellung_aendern("info", "urlaub_bis", "2026-09-17")
    assert agents_io.read_env_raw("info")["URLAUB_BIS"] == "2026-09-17"


# --- Der Agent hört von selbst auf ----------------------------------------
#
# Betreiber-Vorgabe 2026-08-23: „man soll nach Urlaub nicht sagen müssen 'ich
# bin zurück', er sollte das je nach Datum selber verstehen." Das tut er auch —
# `zeitraum_aktiv` im Agenten prüft den Kalendertag, nicht den Haken. Die Tests
# hier sichern die andere Hälfte: dass er es dem Betreiber auch EHRLICH sagt.
# Ein Agent, der am 20. September „ist freigegeben, 01.–14.09." meldet, ohne zu
# erwähnen, dass längst nichts mehr passiert, ist genau die Halbwahrheit, die
# überall sonst in diesem Werkzeug vermieden wird.


import datetime


def _lage(freigegeben, von, bis, heute):
    import src.chat_tools as chat_tools

    return chat_tools._urlaubslage(
        freigegeben, von, bis, datetime.date.fromisoformat(heute)
    )


@pytest.mark.parametrize(
    "heute,erwartet",
    [
        ("2026-08-31", "spaeter"),   # Tag davor
        ("2026-09-01", "laeuft"),    # erster Urlaubstag EINSCHLIESSLICH
        ("2026-09-07", "laeuft"),
        ("2026-09-14", "laeuft"),    # letzter Urlaubstag EINSCHLIESSLICH
        ("2026-09-15", "vorbei"),    # Tag danach — ohne Zutun des Betreibers
    ],
)
def test_lage_haengt_am_kalendertag_nicht_am_haken(heute, erwartet):
    assert _lage(True, "2026-09-01", "2026-09-14", heute) == erwartet


def test_lage_ohne_freigabe_ist_aus():
    assert _lage(False, "2026-09-01", "2026-09-14", "2026-09-07") == "aus"


@pytest.mark.parametrize(
    "von,bis",
    [("", ""), ("2026-09-01", ""), ("2026-09-14", "2026-09-01"), ("01.09.2026", "14.09.2026")],
)
def test_unbrauchbarer_zeitraum_ist_unvollstaendig(von, bis):
    assert _lage(True, von, bis, "2026-09-07") == "unvollstaendig"


def test_nach_dem_urlaub_meldet_er_dass_nichts_mehr_geht(tmp_path, monkeypatch):
    """Der Kern: der Haken steht noch, es passiert aber nichts mehr — und
    genau das muss im Bericht stehen."""
    _setup_env(tmp_path, monkeypatch)
    _agent(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="false",
        URLAUB_VON="2020-09-01",
        URLAUB_BIS="2020-09-14",
    )
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.einstellungen_lesen("info")

    assert ergebnis["einstellungen"]["urlaubsantwort_freigegeben"] is True
    assert ergebnis["einstellungen"]["urlaub_laeuft_gerade"] is False
    text = ergebnis["achtung_urlaub"]
    assert "zu Ende gegangen" in text
    assert "KEINE Autoantwort mehr" in text
    assert "von selbst auf" in text


def test_vor_dem_urlaub_meldet_er_den_start(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _agent(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="false",
        URLAUB_VON="2099-09-01",
        URLAUB_BIS="2099-09-14",
    )
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.einstellungen_lesen("info")
    assert ergebnis["einstellungen"]["urlaub_laeuft_gerade"] is False
    assert "beginnt erst am 2099-09-01" in ergebnis["achtung_urlaub"]


def test_waehrend_des_urlaubs_kein_falscher_alarm(tmp_path, monkeypatch):
    """Läuft alles, soll KEIN Warnhinweis kommen — sonst stumpft er ab."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    heute = datetime.date.today()
    _agent(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="false",
        URLAUB_VON=(heute - datetime.timedelta(days=1)).isoformat(),
        URLAUB_BIS=(heute + datetime.timedelta(days=7)).isoformat(),
    )

    ergebnis = chat_tools.einstellungen_lesen("info")
    assert ergebnis["einstellungen"]["urlaub_laeuft_gerade"] is True
    assert "achtung_urlaub" not in ergebnis


def test_letzter_urlaubstag_zaehlt_noch(tmp_path, monkeypatch):
    """Der letzte Urlaubstag ist ein Urlaubstag — an ihm wird noch geantwortet."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    heute = datetime.date.today().isoformat()
    _agent(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="false",
        URLAUB_VON=heute,
        URLAUB_BIS=heute,
    )

    ergebnis = chat_tools.einstellungen_lesen("info")
    assert ergebnis["einstellungen"]["urlaub_laeuft_gerade"] is True
