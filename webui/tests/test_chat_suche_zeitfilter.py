"""Tests für den Zeitfilter (MBOX-01) in `mails_suchen`/`_build_search_criteria`.

Deckt ab: `_parse_imap_datum` (Formate, Typ-Falle, Fehlertext), die Off-by-one-
Korrektur bei `bis` (BEFORE ist exklusiv, der Betreiber meint inklusiv), die
UND-Verknüpfung mit den bestehenden Suchfeldern, den fehlerfreien Umgang mit
ungültigen Datumswerten in `mails_suchen` (kein Absturz, kein IMAP-Kontakt),
und die Rückwärtskompatibilität des Kriteriums ohne `seit`/`bis`.
"""
from __future__ import annotations

import datetime

import pytest

from .test_chat_tools import _fake_mailbox, _msg, _setup_env, _write_agent_env


def test_parse_imap_datum_liefert_exakten_date_typ():
    import src.chat_tools as chat_tools

    ergebnis = chat_tools._parse_imap_datum("2026-01-31")

    assert type(ergebnis) is datetime.date
    assert ergebnis == datetime.date(2026, 1, 31)


def test_parse_imap_datum_akzeptiert_iso_zeitstempel_und_deutsches_format():
    import src.chat_tools as chat_tools

    erwartet = datetime.date(2026, 1, 31)

    assert chat_tools._parse_imap_datum("2026-01-31T14:30:00") == erwartet
    assert chat_tools._parse_imap_datum("31.01.2026") == erwartet


def test_parse_imap_datum_leer_liefert_none():
    import src.chat_tools as chat_tools

    assert chat_tools._parse_imap_datum("") is None
    assert chat_tools._parse_imap_datum(None) is None


def test_parse_imap_datum_wirft_value_error_bei_unlesbarem_text():
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools._parse_imap_datum("naechste Woche")


def test_build_search_criteria_setzt_since_und_before_mit_off_by_one():
    """Beweis für die Off-by-one-Korrektur: `bis="2026-01-31"` muss zu
    `BEFORE 1-Feb-2026` führen (inklusive Interpretation des Betreiber-„bis")."""
    import src.chat_tools as chat_tools

    kriterium = str(chat_tools._build_search_criteria("", "", "", seit="2026-01-01", bis="2026-01-31"))

    assert "SINCE 1-Jan-2026" in kriterium
    assert "BEFORE 1-Feb-2026" in kriterium


def test_build_search_criteria_zeit_und_absender_werden_und_verknuepft():
    import src.chat_tools as chat_tools

    kriterium = str(
        chat_tools._build_search_criteria("", "Musterhandel", "", seit="2026-01-01", bis="")
    )

    assert "FROM" in kriterium
    assert "SINCE" in kriterium


def test_mails_suchen_liefert_fehler_bei_ungueltigem_datum_ohne_imap_kontakt(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox([_msg(uid="7")])
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", seit="Bloedsinn")

    assert "fehler" in ergebnis
    assert ergebnis["treffer"] == []
    mailbox.fetch.assert_not_called()


def test_ohne_seit_bis_ist_kriterium_byte_gleich_zum_altverhalten():
    """Regressionsschutz: bestehende Aufrufe ohne `seit`/`bis` verhalten sich
    unverändert."""
    import src.chat_tools as chat_tools

    alt = chat_tools._build_search_criteria("Rechnung", "Musterhandel", "Mahnung")
    neu = chat_tools._build_search_criteria("Rechnung", "Musterhandel", "Mahnung", seit="", bis="")

    assert str(alt) == str(neu)


def test_mails_suchen_liefert_seit_bis_im_ergebnis(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox([_msg(uid="7")])
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", seit="2026-01-01", bis="2026-01-31")

    assert ergebnis["seit"] == "2026-01-01"
    assert ergebnis["bis"] == "2026-01-31"


def test_schema_nennt_seit_und_bis():
    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "mails_suchen")
    eigenschaften = schema["input_schema"]["properties"]

    assert "seit" in eigenschaften
    assert "bis" in eigenschaften
