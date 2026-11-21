"""Tests für den Betreiber-Befund vom 2026-08-13: der Agent fragte bei einer Mail
ohne Textinhalt zurück ("soll ich nach Anhängen suchen?"), weil `mail_lesen` die
Existenz von Anhängen überhaupt nicht mitteilte.

Gefixt wurde das STRUKTURELL, nicht per Prompt-Formulierung: `mail_lesen` liefert
jetzt immer `anzahl_anhaenge` + `anhaenge` (Metadaten, keine Inhalte) aus dem
geteilten Helfer `_attachment_metadata`, den auch `mail_anhaenge_auflisten` nutzt.
Eine reine Regel "frag nicht" hätte das Modell stattdessen zum RATEN gebracht.

Deckt ab: Metadaten im mail_lesen-Ergebnis, leerer Body + Anhang (der gemeldete
Fall), Mail ohne Anhänge, Pseudonymisierung des Dateinamens, Gleichlauf der beiden
Aufrufer, Trace-Allowlist, sowie Schema- und Prompt-Zusagen.
"""
from __future__ import annotations

from pathlib import Path

from .test_chat_attachment_tools import (
    _fake_attachment,
    _msg_with_attachments,
    _setup_attachment_env,
)
from .test_chat_tools import _msg


def test_mail_lesen_liefert_anhang_metadaten(mocker, tmp_path, monkeypatch):
    att1 = _fake_attachment("notiz.txt", "text/plain", b"hallo")
    att2 = _fake_attachment("rechnung.pdf", "application/pdf", b"%PDF-fake")
    msg = _msg_with_attachments([att1, att2])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_lesen("info", uid="42")

    assert result["anzahl_anhaenge"] == 2
    assert result["anhaenge"] == [
        {"index": 0, "dateiname": "notiz.txt", "typ": "text/plain", "groesse": 5},
        {"index": 1, "dateiname": "rechnung.pdf", "typ": "application/pdf", "groesse": 9},
    ]
    # Keine Inhalte im Metadaten-Block — die liefert ausschliesslich mail_anhang_lesen.
    for eintrag in result["anhaenge"]:
        assert set(eintrag) == {"index", "dateiname", "typ", "groesse"}


def test_mail_lesen_leerer_body_meldet_anhang_trotzdem(mocker, tmp_path, monkeypatch):
    """Der exakt gemeldete Fall: Mail ohne Textinhalt, Inhalt steckt im Anhang.
    Vorher konnte das Modell die Existenz des Anhangs nicht wissen und fragte."""
    att = _fake_attachment("rechnung.pdf", "application/pdf", b"%PDF-fake")
    msg = _msg_with_attachments([att])
    msg.text = ""
    msg.html = ""
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_lesen("info", uid="42")

    assert result["body_redigiert"].strip() == ""
    assert result["anzahl_anhaenge"] == 1
    assert result["anhaenge"][0]["dateiname"] == "rechnung.pdf"


def test_mail_lesen_ohne_anhaenge_leere_liste_kein_fehler(mocker, tmp_path, monkeypatch):
    msg = _msg_with_attachments([])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_lesen("info", uid="42")

    assert result["anzahl_anhaenge"] == 0
    assert result["anhaenge"] == []
    assert "fehler" not in result


def test_mail_lesen_anhang_dateiname_wird_pseudonymisiert(mocker, tmp_path, monkeypatch):
    """Dateinamen können personenbezogene Daten tragen ("Rechnung_Mueller.pdf") —
    sie müssen durch denselben Anonymizer laufen wie der Body."""
    att = _fake_attachment("Rechnung_Mueller.pdf", "application/pdf", b"%PDF-fake")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    class _Anon:
        def anonymize(self, text):
            return text.replace("Mueller", "PERSON_1")

    result = chat_tools.mail_lesen("info", uid="42", anonymizer=_Anon())

    assert result["anhaenge"][0]["dateiname"] == "Rechnung_PERSON_1.pdf"
    assert "Mueller" not in result["anhaenge"][0]["dateiname"]


def test_beide_aufrufer_nutzen_denselben_helfer(mocker, tmp_path, monkeypatch):
    """Gleichlauf-Absicherung: mail_lesen und mail_anhaenge_auflisten dürfen bei
    derselben Mail nie unterschiedliche Metadaten melden."""
    att = _fake_attachment("bild.png", "image/png", b"\x89PNG\r\n\x1a\n")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    aus_lesen = chat_tools.mail_lesen("info", uid="42")["anhaenge"]
    aus_auflisten = chat_tools.mail_anhaenge_auflisten("info", uid="42")["anhaenge"]

    assert aus_lesen == aus_auflisten


def test_attachment_metadata_ohne_attachments_attribut_kein_crash():
    """Exotische Parts/Fixtures ohne `attachments` dürfen nicht crashen."""
    import src.chat_tools as chat_tools

    assert chat_tools._attachment_metadata(_msg(uid="1"), None) == []

    class _Leer:
        pass

    assert chat_tools._attachment_metadata(_Leer(), None) == []


def test_anzahl_anhaenge_in_trace_allowlist():
    """Damit ein Folge-Turn noch weiss, dass die gelesene Mail Anhänge hatte."""
    import src.chat_tools as chat_tools

    assert "anzahl_anhaenge" in chat_tools._TOOL_TRACE_FIELD_ALLOWLIST
    assert "anhaenge" in chat_tools._TOOL_TRACE_FIELD_ALLOWLIST


def test_mail_lesen_schema_nennt_anhang_felder():
    """Das Modell muss aus dem Schema erfahren, dass die Anhänge mitkommen —
    sonst fragt es weiter nach."""
    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "mail_lesen")
    beschreibung = schema["description"]

    assert "anzahl_anhaenge" in beschreibung
    assert "anhaenge" in beschreibung
    assert "mail_anhang_lesen" in beschreibung


def test_systemprompt_verbietet_die_anhang_rueckfrage():
    """Regressionswächter für die Prompt-Regel: ohne sie kam der gemeldete
    Rückfrage-Satz zurück."""
    prompt = (
        Path(__file__).resolve().parent.parent / "prompts" / "chat-system.txt"
    ).read_text(encoding="utf-8")

    assert "EINE MAIL PRÜFST DU IMMER GANZ" in prompt
    assert "KEINE echte Mehrdeutigkeit" in prompt
    assert "mail_anhang_lesen" in prompt
