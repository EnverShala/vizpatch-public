"""Tests für `postfach_uebersicht` und `top_absender` (Phase 13, Plan 13-05,
MBOX-06/07).

Deckt ab:
1. `postfach_uebersicht`: der Standardaufruf löst pro Ordner GENAU EINEN
   `folder.status(name, options=("MESSAGES", "UNSEEN"))`-Roundtrip aus und
   KEINEN Fetch (13-RESEARCH.md Abschnitt 2). `options` wird explizit
   gesetzt, nicht `None`. Die Größenermittlung (`mit_groesse=True`) ist ein
   separater, teurer Zusatz (Fetch + `size_rfc822`-Summe, Ergebnis in MB).
   Ein einzelner Ordner, dessen STATUS fehlschlägt, lässt den Gesamtaufruf
   nicht scheitern. Ordnerzahl ist auf `MAX_FOLDERS_FOR_ALL_SEARCH` gedeckelt.
2. `top_absender`: die Aggregation läuft über einen `headers_only=True`-
   Fetch + `collections.Counter` über ALLE Treffer im Zeitraum, NICHT nur
   die ersten 50 (Nachweis mit 120 Fake-Mails). Akzeptiert `seit`/`bis`/
   `folder`. Ohne Zeitraum-Angabe greift ein 90-Tage-Standardzeitraum, das
   Ergebnis nennt immer den tatsächlich ausgewerteten Zeitraum. Deckel auf
   20 Absender. Steht in `_ANON_AWARE_TOOLS` (Absenderadressen sind PII).
3. Vollständige Werkzeug-Registrierung (TOOL_SCHEMAS/TOOL_HANDLERS/
   _TOOL_ACTIVITY_LABELS/_ANON_AWARE_TOOLS) — die beiden Guard-Allowlists in
   `test_chat_tools.py` sind dort separat aktualisiert (CLAUDE.md-Vorgabe:
   jedes neue Werkzeug MUSS in BEIDE hartkodierten Allowlists eingetragen
   werden, unabhängig von der files_modified-Liste dieses Plans).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from .test_chat_tools import _fake_folder_info, _fake_mailbox, _msg, _setup_env, _write_agent_env


# --- postfach_uebersicht -----------------------------------------------------


def test_postfach_uebersicht_standardaufruf_nutzt_status_nicht_fetch(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.return_value = [
        _fake_folder_info("INBOX"),
        _fake_folder_info("Archiv"),
    ]
    status_by_folder = {
        "INBOX": {"MESSAGES": 42, "UNSEEN": 5},
        "Archiv": {"MESSAGES": 100, "UNSEEN": 0},
    }
    mock_mailbox.folder.status.side_effect = lambda name, options=None: status_by_folder[name]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.postfach_uebersicht("info")

    assert ergebnis["anzahl_ordner"] == 2
    assert {"ordner": "INBOX", "anzahl": 42, "ungelesen": 5} in ergebnis["ordner"]
    assert {"ordner": "Archiv", "anzahl": 100, "ungelesen": 0} in ergebnis["ordner"]
    # Kernbeleg: kein einziger Fetch im Standardaufruf, nur STATUS-Roundtrips.
    mock_mailbox.fetch.assert_not_called()
    assert mock_mailbox.folder.status.call_count == 2


def test_postfach_uebersicht_setzt_options_explizit_nicht_none(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.return_value = [_fake_folder_info("INBOX")]
    mock_mailbox.folder.status.return_value = {"MESSAGES": 1, "UNSEEN": 0}
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    chat_tools.postfach_uebersicht("info")

    mock_mailbox.folder.status.assert_called_once_with("INBOX", options=("MESSAGES", "UNSEEN"))


def test_postfach_uebersicht_mit_groesse_fetcht_und_summiert_size_rfc822(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    m1 = MagicMock()
    m1.size_rfc822 = 1_000_000
    m2 = MagicMock()
    m2.size_rfc822 = 500_000

    mock_mailbox = _fake_mailbox([m1, m2])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("INBOX")]
    mock_mailbox.folder.status.return_value = {"MESSAGES": 2, "UNSEEN": 0}
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.postfach_uebersicht("info", mit_groesse=True)

    eintrag = ergebnis["ordner"][0]
    assert eintrag["groesse_mb"] == round(1_500_000 / (1024 * 1024), 1)
    fetch_kwargs = mock_mailbox.fetch.call_args.kwargs
    assert fetch_kwargs["headers_only"] is True
    assert fetch_kwargs["mark_seen"] is False


def test_postfach_uebersicht_ein_ordner_status_fehler_bricht_gesamtaufruf_nicht_ab(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.return_value = [
        _fake_folder_info("Kaputt"),
        _fake_folder_info("INBOX"),
    ]

    def _status_side_effect(name, options=None):
        if name == "Kaputt":
            raise RuntimeError("keine Rechte auf diesen Ordner")
        return {"MESSAGES": 3, "UNSEEN": 1}

    mock_mailbox.folder.status.side_effect = _status_side_effect
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.postfach_uebersicht("info")

    assert ergebnis["anzahl_ordner"] == 2
    kaputt = next(e for e in ergebnis["ordner"] if e["ordner"] == "Kaputt")
    assert "fehler" in kaputt
    inbox = next(e for e in ergebnis["ordner"] if e["ordner"] == "INBOX")
    assert inbox["anzahl"] == 3
    assert inbox["ungelesen"] == 1


def test_postfach_uebersicht_deckelt_ordneranzahl(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    viele_ordner = [
        _fake_folder_info(f"Ordner{i}") for i in range(chat_tools.MAX_FOLDERS_FOR_ALL_SEARCH + 5)
    ]
    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.return_value = viele_ordner
    mock_mailbox.folder.status.return_value = {"MESSAGES": 1, "UNSEEN": 0}
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.postfach_uebersicht("info")

    assert len(ergebnis["ordner"]) == chat_tools.MAX_FOLDERS_FOR_ALL_SEARCH
    assert "ordner_gedeckelt" in ergebnis


def test_postfach_uebersicht_beschreibung_warnt_vor_teurer_groesse():
    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "postfach_uebersicht")
    haystack = (
        schema["description"] + schema["input_schema"]["properties"]["mit_groesse"]["description"]
    ).lower()
    assert "teuer" in haystack


def test_postfach_uebersicht_vollstaendig_registriert():
    import src.chat_tools as chat_tools

    assert "postfach_uebersicht" in {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "postfach_uebersicht" in chat_tools.TOOL_HANDLERS
    assert "postfach_uebersicht" in chat_tools._TOOL_ACTIVITY_LABELS
    # Ergebnis trägt nur Ordnernamen/Zahlen, keinen Mailinhalt -> bewusst
    # NICHT in _ANON_AWARE_TOOLS (analog ordner_loeschen/ordner_umbenennen).
    assert "postfach_uebersicht" not in chat_tools._ANON_AWARE_TOOLS


# --- top_absender -------------------------------------------------------------


def test_top_absender_zaehlt_ueber_alle_120_treffer_nicht_nur_50(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    messages = [_msg(uid=str(i), from_="viel@beispiel.de") for i in range(90)]
    messages += [_msg(uid=str(i), from_="wenig@beispiel.de") for i in range(90, 120)]
    mock_mailbox = _fake_mailbox(messages)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.top_absender("info")

    assert ergebnis["ausgewertete_mails"] == 120
    assert ergebnis["absender"][0] == {"von": "viel@beispiel.de", "anzahl": 90}
    assert ergebnis["absender"][1] == {"von": "wenig@beispiel.de", "anzahl": 30}


def test_top_absender_akzeptiert_seit_bis_folder_und_reicht_sie_an_die_suche_weiter(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg(from_="a@example.com")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.top_absender("info", folder="Kunden", seit="2026-01-01", bis="2026-01-31")

    mock_mailbox.folder.set.assert_called_once_with("Kunden")
    kriterium = str(mock_mailbox.fetch.call_args.args[0])
    assert "SINCE 1-Jan-2026" in kriterium
    assert "BEFORE 1-Feb-2026" in kriterium
    assert ergebnis["ordner"] == "Kunden"
    assert ergebnis["seit"] == "2026-01-01"
    assert ergebnis["bis"] == "2026-01-31"


def test_top_absender_default_zeitraum_90_tage_ohne_angabe(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    monkeypatch.setattr(chat_tools.timefmt, "today_local", lambda: date(2026, 8, 18))
    mock_mailbox = _fake_mailbox([_msg(from_="a@example.com")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.top_absender("info")

    # 2026-08-18 minus 90 Tage = 2026-05-20 (verifiziert per datetime-Rechnung).
    assert ergebnis["seit"] == "2026-05-20"
    kriterium = str(mock_mailbox.fetch.call_args.args[0])
    assert "SINCE" in kriterium
    assert "BEFORE" not in kriterium


def test_top_absender_kein_default_wenn_nur_seit_gesetzt_ist(mocker, tmp_path, monkeypatch):
    """Ist MINDESTENS eines der beiden Felder explizit gesetzt, darf der
    90-Tage-Default NICHT eingreifen -- der Betreiber-Wunsch hat Vorrang."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    monkeypatch.setattr(chat_tools.timefmt, "today_local", lambda: date(2026, 8, 18))
    mock_mailbox = _fake_mailbox([_msg(from_="a@example.com")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.top_absender("info", seit="2020-01-01")

    assert ergebnis["seit"] == "2020-01-01"


def test_top_absender_deckelt_auf_20_absender_und_kennzeichnet_das(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    messages = [_msg(uid=str(i), from_=f"person{i}@beispiel.de") for i in range(25)]
    mock_mailbox = _fake_mailbox(messages)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.top_absender("info")

    assert len(ergebnis["absender"]) == chat_tools.MAX_TOP_ABSENDER_EINTRAEGE == 20
    assert "gedeckelt" in ergebnis


def test_top_absender_anonymisiert_absenderadressen_wenn_anonymizer_gesetzt(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    class _FakeAnonymizer:
        def anonymize(self, value):
            return f"ANON[{value}]"

    mock_mailbox = _fake_mailbox([_msg(from_="kunde@example.com")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.top_absender("info", anonymizer=_FakeAnonymizer())

    assert ergebnis["absender"][0]["von"] == "ANON[kunde@example.com]"


def test_top_absender_ohne_treffer_liefert_leere_liste_kein_crash(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.top_absender("info")

    assert ergebnis["absender"] == []
    assert ergebnis["ausgewertete_mails"] == 0
    assert "fehler" not in ergebnis


def test_top_absender_ungueltiges_datum_liefert_fehler_ohne_imap_kontakt(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox_cls = mocker.patch("src.chat_tools.MailBox")

    ergebnis = chat_tools.top_absender("info", seit="nicht-ein-datum")

    assert "fehler" in ergebnis
    assert ergebnis["absender"] == []
    mock_mailbox_cls.assert_not_called()


def test_top_absender_ist_in_anon_aware_tools():
    import src.chat_tools as chat_tools

    assert "top_absender" in chat_tools._ANON_AWARE_TOOLS


def test_top_absender_vollstaendig_registriert():
    import src.chat_tools as chat_tools

    assert "top_absender" in {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "top_absender" in chat_tools.TOOL_HANDLERS
    assert "top_absender" in chat_tools._TOOL_ACTIVITY_LABELS
