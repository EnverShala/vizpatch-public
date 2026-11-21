"""Tests für `entwurf_mit_mail_anhang` (Phase 13, Plan 13-08, MBOX-14):

Ein Entwurf kann mit einem Anhang erzeugt werden, dessen Bytes aus einer per
uid benannten Quellmail stammen — OHNE Beteiligung des Pending-Upload-Stores
(der ist für Browser-Uploads gedacht, `entwurf_mit_anhang`). Die Anhang-Auswahl
läuft über `_select_attachment` (dieselbe deterministische Reihenfolge wie
`mail_anhang_lesen`), das bestehende Größenlimit `MAX_ATTACHMENT_MB` greift
unverändert, und Threading (`In-Reply-To`/`References`) bleibt bei einer
Antwort erhalten — anders als beim separaten `mail_weiterleiten`, das bewusst
KEIN Threading setzt.

`_setup_env`/`_write_agent_env`/`_fake_mailbox`/`_msg` sind die etablierten
Helfer aus `test_chat_tools.py` (13-PATTERNS.md Abschnitt 10 — jede Testdatei
importiert sie statt sie zu duplizieren).
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

from .test_chat_tools import _fake_mailbox, _msg, _setup_env, _write_agent_env


def _att(filename="Rechnung.pdf", content_type="application/pdf", size=1024, payload=b"%PDF-1.4 inhalt"):
    """Anhangs-Attrappe (Muster `test_chat_suche_anhang_groesse._att`, um
    `size`/`payload` erweitert -- hier zählt die Größenprüfung und der
    tatsächlich übernommene Byte-Inhalt, nicht die Inline/Attachment-
    Unterscheidung)."""
    return types.SimpleNamespace(
        filename=filename,
        content_disposition="attachment",
        content_type=content_type,
        size=size,
        payload=payload,
    )


def _msg_mit_anhaengen(uid="42", attachments=(), headers=None, subject="Re: Angebot", from_="kunde@example.com"):
    m = _msg(uid=uid, subject=subject, from_=from_, headers=headers)
    m.attachments = list(attachments)
    return m


# --- Grundfunktion: Anhang aus Quellmail übernehmen --------------------------


def test_entwurf_mit_mail_anhang_uebernimmt_anhang_aus_quellmail(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    quelle = _msg_mit_anhaengen(uid="7", attachments=[_att(filename="Rechnung.pdf")])
    mock_mailbox = _fake_mailbox([quelle])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.entwurf_mit_mail_anhang(
        "info",
        text="Anbei die Rechnung.",
        anhang_quelle_uid="7",
        anhang_dateiname_oder_index="Rechnung.pdf",
        betreff="Rechnung",
        an="kunde@example.com",
    )

    assert "fehler" not in result
    assert result["ok"] is True
    assert result["anhang_dateiname"] == "Rechnung.pdf"
    assert result["anhang_quelle_uid"] == "7"
    assert result["anhang_quelle_ordner"] == "INBOX"

    append_bytes = mock_mailbox.append.call_args.args[0]
    assert b"Rechnung.pdf" in append_bytes
    # Anhang-Bytes laufen durch add_attachment() und kommen deshalb
    # BASE64-kodiert im Ergebnis an, nicht als Klartext-Bytes.
    import base64

    assert base64.b64encode(b"%PDF-1.4 inhalt") in append_bytes


def test_entwurf_mit_mail_anhang_pending_upload_store_nicht_beruehrt(mocker, tmp_path, monkeypatch):
    """MBOX-14 (Kernanforderung): der Pending-Upload-Store (Browser-Uploads,
    `entwurf_mit_anhang`) wird NICHT konsultiert — die Bytes kommen live aus
    IMAP."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    quelle = _msg_mit_anhaengen(uid="7", attachments=[_att()])
    mock_mailbox = _fake_mailbox([quelle])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    consume_upload_mock = mocker.patch("src.chat_tools._consume_pending_upload")
    peek_upload_mock = mocker.patch("src.chat_tools._peek_pending_upload")

    import src.chat_tools as chat_tools

    result = chat_tools.entwurf_mit_mail_anhang(
        "info",
        text="Anbei die Rechnung.",
        anhang_quelle_uid="7",
        anhang_dateiname_oder_index="0",
        an="kunde@example.com",
    )

    assert result["ok"] is True
    consume_upload_mock.assert_not_called()
    peek_upload_mock.assert_not_called()


# --- Anhang-Auswahl: Dateiname vs. Index -------------------------------------


def test_entwurf_mit_mail_anhang_auswahl_per_dateiname(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    quelle = _msg_mit_anhaengen(
        uid="7", attachments=[_att(filename="Angebot.pdf"), _att(filename="Rechnung.pdf")]
    )
    mock_mailbox = _fake_mailbox([quelle])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.entwurf_mit_mail_anhang(
        "info",
        text="Text",
        anhang_quelle_uid="7",
        anhang_dateiname_oder_index="Rechnung.pdf",
        an="kunde@example.com",
    )

    assert result["anhang_dateiname"] == "Rechnung.pdf"
    append_bytes = mock_mailbox.append.call_args.args[0]
    assert b"Rechnung.pdf" in append_bytes


def test_entwurf_mit_mail_anhang_auswahl_per_index(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    quelle = _msg_mit_anhaengen(
        uid="7", attachments=[_att(filename="Angebot.pdf"), _att(filename="Rechnung.pdf")]
    )
    mock_mailbox = _fake_mailbox([quelle])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.entwurf_mit_mail_anhang(
        "info",
        text="Text",
        anhang_quelle_uid="7",
        anhang_dateiname_oder_index="1",
        an="kunde@example.com",
    )

    assert result["anhang_dateiname"] == "Rechnung.pdf"


def test_entwurf_mit_mail_anhang_unbekannter_anhang_gibt_fehler_mit_liste(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    quelle = _msg_mit_anhaengen(uid="7", attachments=[_att(filename="Angebot.pdf")])
    mock_mailbox = _fake_mailbox([quelle])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.entwurf_mit_mail_anhang(
        "info",
        text="Text",
        anhang_quelle_uid="7",
        anhang_dateiname_oder_index="Nicht-Vorhanden.pdf",
        an="kunde@example.com",
    )

    assert "fehler" in result
    assert "Angebot.pdf" in result["fehler"]
    mock_mailbox.append.assert_not_called()


# --- mark_seen=False ----------------------------------------------------------


def test_entwurf_mit_mail_anhang_quellmail_fetch_mark_seen_false(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    quelle = _msg_mit_anhaengen(uid="7", attachments=[_att()])
    mock_mailbox = _fake_mailbox([quelle])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    chat_tools.entwurf_mit_mail_anhang(
        "info",
        text="Text",
        anhang_quelle_uid="7",
        anhang_dateiname_oder_index="0",
        an="kunde@example.com",
    )

    for call in mock_mailbox.fetch.call_args_list:
        assert call.kwargs.get("mark_seen") is False


# --- Größenlimit ---------------------------------------------------------------


def test_entwurf_mit_mail_anhang_zu_grosser_anhang_wird_abgelehnt(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    zu_gross = 16 * 1024 * 1024  # > Default MAX_ATTACHMENT_MB=15
    quelle = _msg_mit_anhaengen(uid="7", attachments=[_att(filename="Riesig.pdf", size=zu_gross)])
    mock_mailbox = _fake_mailbox([quelle])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.entwurf_mit_mail_anhang(
        "info",
        text="Text",
        anhang_quelle_uid="7",
        anhang_dateiname_oder_index="Riesig.pdf",
        an="kunde@example.com",
    )

    assert "fehler" in result
    assert "MB" in result["fehler"]
    mock_mailbox.append.assert_not_called()


# --- Threading bleibt bei einer Antwort erhalten ------------------------------


def test_entwurf_mit_mail_anhang_antwort_behaelt_threading_header(mocker, tmp_path, monkeypatch):
    """MBOX-14: bei `in_reply_to_uid` bleibt die Threading-Disziplin des
    Bestandscodes erhalten (anders als beim separaten `mail_weiterleiten`)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    quelle = _msg_mit_anhaengen(
        uid="7",
        subject="Frage zum Angebot",
        from_="kunde@example.com",
        attachments=[_att(filename="Rechnung.pdf")],
        headers={"message-id": "<orig@kunde>", "references": "<prev@kunde>"},
    )
    mock_mailbox = _fake_mailbox([quelle])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.entwurf_mit_mail_anhang(
        "info",
        text="Anbei die Rechnung.",
        anhang_quelle_uid="7",
        anhang_dateiname_oder_index="Rechnung.pdf",
        in_reply_to_uid="7",
    )

    assert "fehler" not in result
    assert result["antwort_auf_uid"] == "7"
    append_bytes = mock_mailbox.append.call_args.args[0]
    assert b"In-Reply-To: <orig@kunde>" in append_bytes
    assert b"References:" in append_bytes


def test_entwurf_mit_mail_anhang_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    import pytest

    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.entwurf_mit_mail_anhang(
            "../evil", text="Text", anhang_quelle_uid="1", anhang_dateiname_oder_index="0"
        )


# --- Registrierung -------------------------------------------------------------


def test_entwurf_mit_mail_anhang_registriert_in_schemas_und_handlern():
    import src.chat_tools as chat_tools

    schema_names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "entwurf_mit_mail_anhang" in chat_tools.TOOL_HANDLERS
    assert "entwurf_mit_mail_anhang" in schema_names


def test_entwurf_mit_mail_anhang_hat_aktivitaets_label():
    import src.chat_tools as chat_tools

    assert chat_tools._tool_activity_label("entwurf_mit_mail_anhang") != "Entwurf mit mail anhang…"
    assert "Anhang" in chat_tools._tool_activity_label("entwurf_mit_mail_anhang")


def test_entwurf_mit_mail_anhang_kein_session_scoped_tool():
    """MBOX-14: kein Pending-Upload-Store beteiligt -> kein `session_id`-
    Parameter nötig, keine `_SESSION_SCOPED_TOOLS`-Mitgliedschaft."""
    import src.chat_tools as chat_tools

    assert "entwurf_mit_mail_anhang" not in chat_tools._SESSION_SCOPED_TOOLS
