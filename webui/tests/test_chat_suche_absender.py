"""Tests für den Betreiber-Befund vom 2026-08-15: „lösch alle Mails VON MUSTERHANDEL"
traf auch Paketdienst-Versandmails, die Musterhandel nur ERWÄHNEN.

Ursache war keine Prompt-Schwäche, sondern eine fehlende Fähigkeit: `mails_suchen`
kannte NUR `query`, und das landete auf IMAP `TEXT` — was Kopfzeilen UND Rumpf
durchsucht. „Von X" war schlicht nicht ausdrückbar.

Gefixt über drei getrennte Felder (`_build_search_criteria`): `absender` -> FROM,
`betreff` -> SUBJECT, `query` -> TEXT. Deckt ab: Kriterienbau je Feld und
kombiniert, Rückwärtskompatibilität des Alt-Verhaltens, Durchreichen an beide
Suchpfade, sowie Schema- und Prompt-Zusagen.
"""
from __future__ import annotations

from pathlib import Path

from .test_chat_tools import _fake_mailbox, _msg, _setup_env, _write_agent_env


def _kriterium(query="", absender="", betreff=""):
    import src.chat_tools as chat_tools

    return chat_tools._build_search_criteria(query, absender, betreff)


def test_absender_erzeugt_from_kriterium():
    """Der Kern des Befunds: 'von X' darf NICHT auf TEXT landen."""
    kriterium = str(_kriterium(absender="Musterhandel"))

    assert "FROM" in kriterium
    assert "TEXT" not in kriterium


def test_betreff_erzeugt_subject_kriterium():
    kriterium = str(_kriterium(betreff="Rechnung"))

    assert "SUBJECT" in kriterium
    assert "TEXT" not in kriterium


def test_query_bleibt_volltext():
    """Das breite Netz bleibt erhalten — es ist nur nicht mehr die einzige Option."""
    kriterium = str(_kriterium(query="Musterhandel"))

    assert "TEXT" in kriterium
    assert "FROM" not in kriterium


def test_mehrere_felder_werden_und_verknuepft():
    kriterium = str(_kriterium(absender="Musterhandel", betreff="Rechnung"))

    assert "FROM" in kriterium
    assert "SUBJECT" in kriterium


def test_ohne_felder_bleibt_ALL():
    """Unverändertes Alt-Verhalten: keine Angabe = neueste Mails des Ordners."""
    assert _kriterium() == "ALL"


def test_leere_und_whitespace_felder_zaehlen_nicht():
    assert _kriterium(query="   ", absender="", betreff="\t") == "ALL"


def test_nur_query_ist_byte_gleich_zum_altverhalten():
    """Rückwärtskompatibilität: bestehende Aufrufe mit nur `query` müssen exakt
    dasselbe Kriterium erzeugen wie vor der Änderung (`AND(text=...)`)."""
    from imap_tools import AND

    assert str(_kriterium(query="Rechnung")) == str(AND(text="Rechnung"))


def test_mails_suchen_reicht_absender_an_die_mailbox_durch(mocker, tmp_path, monkeypatch):
    """Ende-zu-Ende im Werkzeug: der Absender muss als FROM bei der Suche ankommen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox([_msg(uid="7")])
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    chat_tools.mails_suchen("info", absender="Musterhandel", folder="INBOX")

    kriterium = str(mailbox.fetch.call_args[0][0])
    assert "FROM" in kriterium
    assert "TEXT" not in kriterium


def test_mails_suchen_absender_und_query_gleichzeitig(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox([_msg(uid="7")])
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    chat_tools.mails_suchen("info", absender="Musterhandel", query="Rechnung")

    kriterium = str(mailbox.fetch.call_args[0][0])
    assert "FROM" in kriterium
    assert "TEXT" in kriterium


def test_treffer_tragen_weiterhin_das_von_feld(mocker, tmp_path, monkeypatch):
    """Die Prompt-Regel verlangt eine Gegenprüfung vor dem Löschen — dafür MUSS
    jeder Treffer den Absender ausweisen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mailbox = _fake_mailbox([_msg(uid="7")])
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_suchen("info", absender="Musterhandel")

    assert ergebnis["treffer"], "Testaufbau liefert keinen Treffer"
    assert "von" in ergebnis["treffer"][0]


def test_schema_nennt_absender_und_warnt_vor_query():
    """Ohne diese Angaben im Schema wählt das Modell weiter die Volltext-Suche."""
    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "mails_suchen")
    eigenschaften = schema["input_schema"]["properties"]

    assert "absender" in eigenschaften
    assert "betreff" in eigenschaften
    assert "FROM" in eigenschaften["absender"]["description"]
    # Die Warnung gehört in die Werkzeug-Beschreibung, nicht nur ins Feld.
    assert "VON" in schema["description"]


def test_systemprompt_trennt_von_und_mit():
    prompt = (
        Path(__file__).resolve().parent.parent / "prompts" / "chat-system.txt"
    ).read_text(encoding="utf-8")

    assert "„VON" in prompt and "IST NICHT" in prompt
    assert "absender=" in prompt
    # Die Gegenprüfung vor destruktiven Aktionen muss ebenfalls verankert sein.
    assert "\"von\"" in prompt or "Feld \"von\"" in prompt
