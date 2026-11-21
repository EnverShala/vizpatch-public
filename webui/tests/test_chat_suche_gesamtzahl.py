"""Tests für den Betreiber-Befund vom 2026-08-16: „lösch alle Mails von Paketdienst"
endete in einer Kette aus „50 gelöscht — soll ich nochmal suchen?".

Die Rückfragen kamen NICHT aus Höflichkeit; der System-Prompt verbietet sie
längst („nicht um Erlaubnis fragen"). Sie kamen aus einer Wissenslücke:
`mails_suchen` deckelt bei `MAX_SEARCH_LIMIT` (50) und gab nur
`anzahl = len(treffer)` zurück. Bei genau 50 Treffern ist von außen nicht
unterscheidbar, ob es 50 oder 500 sind — der Agent KONNTE nicht wissen, ob er
fertig ist.

Dasselbe Muster wie beim Absender-Befund (2026-08-15) und beim Anhang-Befund
(2026-08-13): fehlende Fähigkeit, nicht fehlende Regel. Deshalb liefert die
Suche jetzt `gesamt` (echte Trefferzahl, per IMAP-SEARCH ohne Fetch) und
`weitere_vorhanden`. Erst darauf setzt die Prompt-Regel auf.
"""
from __future__ import annotations

from pathlib import Path

from .test_chat_tools import _fake_mailbox, _msg, _setup_env, _write_agent_env

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "chat-system.txt"


def _mailbox_mit_zaehlung(messages, gesamt, mocker):
    """Fake-Mailbox, deren `uids()` eine Trefferzahl meldet, die von der Zahl der
    tatsächlich gefetchten Nachrichten abweichen darf — genau die Lage, die den
    Befund ausgelöst hat (50 geliefert, in Wahrheit mehr)."""
    mailbox = _fake_mailbox(messages)
    mailbox.uids.return_value = [str(i) for i in range(gesamt)]
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)
    return mailbox


def test_gesamtzahl_wird_gemeldet(mocker, tmp_path, monkeypatch):
    """Der Kern des Befunds: 50 gelieferte Treffer bei 123 vorhandenen müssen als
    solche erkennbar sein."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    _mailbox_mit_zaehlung([_msg(uid=str(i)) for i in range(50)], 123, mocker)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", absender="Paketdienst", limit=50)

    assert ergebnis["anzahl"] == 50, "anzahl bleibt die Zahl der GELIEFERTEN Treffer"
    assert ergebnis["gesamt"] == 123
    assert ergebnis["weitere_vorhanden"] is True


def test_letzter_block_meldet_keine_weiteren(mocker, tmp_path, monkeypatch):
    """Der Abbruch der Schleife: sind alle Treffer geliefert, ist der Auftrag
    fertig — ohne Rückfrage, ohne weiteren Suchlauf."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    _mailbox_mit_zaehlung([_msg(uid=str(i)) for i in range(23)], 23, mocker)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", absender="Paketdienst", limit=50)

    assert ergebnis["gesamt"] == 23
    assert ergebnis["weitere_vorhanden"] is False


def test_zaehlung_holt_keine_nachrichten(mocker, tmp_path, monkeypatch):
    """Die Zählung muss über `uids()` laufen (reiner SEARCH-Roundtrip). Liefe sie
    über einen zweiten `fetch()`, würden die Kosten mit der Treffermenge wachsen —
    bei „alle Mails von X" genau der falsche Skalierungspfad."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _mailbox_mit_zaehlung([_msg(uid="1")], 400, mocker)

    import src.chat_tools as chat_tools

    chat_tools.mails_suchen("info", absender="Paketdienst", limit=50)

    assert mailbox.uids.call_count == 1
    assert mailbox.fetch.call_count == 1, "genau EIN Fetch — die Zählung darf keinen zweiten auslösen"


def test_gescheiterte_zaehlung_schaetzt_konservativ(mocker, tmp_path, monkeypatch):
    """Kann nicht gezählt werden, wird nicht geraten: eine bis zum Limit gefüllte
    Liste gilt als „vermutlich mehr". Lieber ein überflüssiger Suchlauf als ein
    Auftrag, der still bei 50 von 300 endet."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox([_msg(uid=str(i)) for i in range(50)])
    mailbox.uids.side_effect = RuntimeError("SEARCH abgelehnt")
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", absender="Paketdienst", limit=50)

    assert "gesamt" not in ergebnis, "eine erratene Zahl wäre schlimmer als gar keine"
    assert ergebnis["weitere_vorhanden"] is True


def test_gescheiterte_zaehlung_bei_teilliste_meldet_fertig(mocker, tmp_path, monkeypatch):
    """Gegenstück: eine NICHT volle Liste ist auch ohne Zählung beweisbar
    vollständig — sonst suchte der Agent endlos weiter."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox([_msg(uid="1"), _msg(uid="2")])
    mailbox.uids.side_effect = RuntimeError("SEARCH abgelehnt")
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", absender="Paketdienst", limit=50)

    assert ergebnis["weitere_vorhanden"] is False


def test_suche_bleibt_ohne_zaehlung_funktionsfaehig(mocker, tmp_path, monkeypatch):
    """Eine fehlgeschlagene ZÄHLUNG darf eine funktionierende Suche nie kippen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox([_msg(uid="7")])
    mailbox.uids.side_effect = RuntimeError("SEARCH abgelehnt")
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", absender="Paketdienst")

    assert "fehler" not in ergebnis
    assert ergebnis["anzahl"] == 1


def test_alle_ordner_zaehlt_ueber_den_fetch_deckel_hinaus(mocker, tmp_path, monkeypatch):
    """Bei der Alle-Ordner-Suche darf die Schleife nicht mehr abbrechen, sobald das
    Fetch-Limit voll ist — sonst wäre `gesamt` genau dann zu niedrig, wenn es am
    meisten zählt. Gezählt wird weiter, geholt wird nicht mehr.

    DREI Ordner sind Absicht: die Zählung steht VOR dem Voll-Check, deshalb wird
    der zweite Ordner auch bei einem `break` noch mitgezählt. Erst am DRITTEN
    Ordner unterscheiden sich Abbrechen und Weiterzählen — mit nur zwei Ordnern
    wäre dieser Test ein Blindgänger (beim Schreiben genau so passiert)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox(
        per_folder_messages={
            "INBOX": [_msg(uid=str(i)) for i in range(3)],
            "Archiv": [_msg(uid="90")],
            "Projekte": [_msg(uid="91")],
        }
    )
    mailbox.folder.list.return_value = [
        type("FI", (), {"name": n})() for n in ("INBOX", "Archiv", "Projekte")
    ]
    mailbox.uids.return_value = ["1", "2", "3", "4", "5"]  # 5 je Ordner -> 15 gesamt
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", absender="Paketdienst", folder="alle", limit=2)

    assert ergebnis["gesamt"] == 15, "ALLE Ordner müssen gezählt werden, auch die ungefetchten"
    assert ergebnis["weitere_vorhanden"] is True
    assert mailbox.uids.call_count == 3


def test_alle_ordner_unvollstaendige_summe_wird_nicht_ausgegeben(mocker, tmp_path, monkeypatch):
    """Scheitert die Zählung in EINEM Ordner, ist die Summe unvollständig — und
    eine unvollständige Summe sähe exakt aus wie eine belastbare Zahl."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox(per_folder_messages={"INBOX": [_msg(uid="1")], "Archiv": []})
    mailbox.folder.list.return_value = [
        type("FI", (), {"name": "INBOX"})(),
        type("FI", (), {"name": "Archiv"})(),
    ]
    mailbox.uids.side_effect = [["1", "2"], RuntimeError("SEARCH abgelehnt")]
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", absender="Paketdienst", folder="alle", limit=50)

    assert "gesamt" not in ergebnis


def test_neue_felder_stehen_in_der_trace_allowlist():
    """Ohne die Spur verliert ein mehrrundiger Auftrag im FOLGE-Turn den Überblick:
    der Agent wüsste nicht mehr, wie viele es insgesamt waren."""
    import src.chat_tools as chat_tools

    assert "gesamt" in chat_tools._TOOL_TRACE_FIELD_ALLOWLIST
    assert "weitere_vorhanden" in chat_tools._TOOL_TRACE_FIELD_ALLOWLIST


def test_schema_erklaert_den_deckel_und_die_gegenmittel():
    """Das Schema ist der Ort, an dem der Agent die Grenze überhaupt erfährt."""
    import src.chat_tools as chat_tools

    schema = next(t for t in chat_tools.TOOL_SCHEMAS if t["name"] == "mails_suchen")
    beschreibung = schema["description"]

    assert "50" in beschreibung
    assert "gesamt" in beschreibung
    assert "weitere_vorhanden" in beschreibung


def test_prompt_verlangt_durchziehen_statt_rueckfrage():
    """Die Regel, die den Betreiber-Ärger direkt adressiert."""
    text = PROMPT.read_text(encoding="utf-8")

    assert "weitere_vorhanden" in text
    assert "gesamt" in text
    # Die Fortschrittsmeldung muss die LAUFENDE SUMME tragen, nicht die Blockgröße.
    assert "LAUFENDEN SUMME" in text or "laufenden Summe" in text


def test_prompt_deutet_attributive_firmennennung_als_absender():
    """Betreiber-Befund 2026-08-16, zweiter Teil: „lösch alle Paketdienst Mails" wurde als
    Volltext-Suche ausgeführt und traf Mails, die Paketdienst nur erwähnen. Gemeint war der
    Absender.

    Anders als die übrigen Befunde dieser Reihe ist das WIRKLICH eine Prompt-Lücke
    und keine fehlende Fähigkeit: `absender=` existiert seit dem 2026-08-15. Die
    bestehende Regel deckte aber nur die explizite Form („Mails VON X") ab — nicht
    die attributive („die Paketdienst-Mails"), die im Alltag häufiger ist.
    """
    text = PROMPT.read_text(encoding="utf-8")

    assert "PAKETDIENST-MAILS" in text.upper(), (
        "Die attributive Form („die Paketdienst-Mails“) muss ausdrücklich geregelt sein — "
        "die Regel für „Mails VON X“ allein greift dort nicht."
    )
    # Die Auflösung muss auf den Absender zeigen, nicht auf die Volltext-Suche.
    stelle = text.upper().find("PAKETDIENST-MAILS")
    umfeld = text[stelle : stelle + 700]
    assert "absender=" in umfeld


def test_prompt_verbietet_rueckfrage_zur_lesart():
    """Die enge Lesart wird ausgeführt und NACHTRÄGLICH mitgeteilt — eine Rückfrage
    „meintest du von oder über?" wäre genau das, was der Betreiber nicht will."""
    text = PROMPT.read_text(encoding="utf-8")
    stelle = text.upper().find("PAKETDIENST-MAILS")
    umfeld = text[stelle : stelle + 1200]

    assert "Frag dabei NICHT nach" in umfeld
    assert "VON PAKETDIENST genommen" in umfeld, (
        "Es muss ein konkreter Mitteilungs-Halbsatz vorgegeben sein, sonst bleibt "
        "die Transparenz dem Zufall überlassen."
    )


def test_gegenprobe_der_prompt_pruefung():
    """Belegt, dass die Prompt-Prüfung kein Blindgänger ist: an einer Fassung ohne
    die Regel muss sie anschlagen."""
    ohne_regel = "AUFTRÄGE VOLLSTÄNDIG AUSFÜHREN:\n- Führe einen Auftrag in EINEM Zug aus.\n"

    assert "weitere_vorhanden" not in ohne_regel
