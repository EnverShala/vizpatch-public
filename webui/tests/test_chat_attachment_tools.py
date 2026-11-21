"""Tests für die Anhang-Werkzeuge (Quick 260812-hhm, Task 3b):
`mail_anhaenge_auflisten`/`mail_anhang_lesen` + `_extract_attachment_text`/
`_select_attachment`.

Deckt ab: Auflistung mit Metadaten, Textextraktion (Charset, PDF via
`pytest.importorskip("pypdf")`), Größenlimit, unlesbares Format, fehlendes
pypdf (Import-Stub, läuft AUCH mit installiertem pypdf), Anon-vor-Truncate,
deterministische Auswahl-Reihenfolge (Name > Index), Registry-Vollständigkeit.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from .test_chat_tools import _fake_mailbox, _msg, _setup_env, _write_agent_env


def _fake_attachment(filename, content_type, payload, charset=None):
    att = MagicMock()
    att.filename = filename
    att.content_type = content_type
    att.payload = payload
    att.size = len(payload)
    att.part = MagicMock()
    att.part.get_content_charset.return_value = charset
    return att


def _msg_with_attachments(attachments, uid="42"):
    msg = _msg(uid=uid)
    msg.attachments = attachments
    return msg


def _setup_attachment_env(mocker, tmp_path, monkeypatch, msg):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mock_mailbox = _fake_mailbox([msg])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    return mock_mailbox


# --- mail_anhaenge_auflisten --------------------------------------------------


def test_mail_anhaenge_auflisten_lists_metadata(mocker, tmp_path, monkeypatch):
    att1 = _fake_attachment("notiz.txt", "text/plain", b"hallo")
    att2 = _fake_attachment("rechnung.pdf", "application/pdf", b"%PDF-fake")
    msg = _msg_with_attachments([att1, att2])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhaenge_auflisten("info", uid="42")

    assert result["uid"] == "42"
    assert result["anzahl"] == 2
    assert result["anhaenge"] == [
        {"index": 0, "dateiname": "notiz.txt", "typ": "text/plain", "groesse": 5},
        {"index": 1, "dateiname": "rechnung.pdf", "typ": "application/pdf", "groesse": 9},
    ]


def test_mail_anhaenge_auflisten_no_attachments_returns_empty_list(mocker, tmp_path, monkeypatch):
    msg = _msg_with_attachments([])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhaenge_auflisten("info", uid="42")

    assert result["anzahl"] == 0
    assert result["anhaenge"] == []
    assert "fehler" not in result


def test_mail_anhaenge_auflisten_anonymizes_filenames(mocker, tmp_path, monkeypatch):
    # Dateiname mit erkennbarer PII (IBAN) -- der Anonymizer maskiert nur
    # Werte, die einem seiner Muster entsprechen (IBAN/EMAIL/TELEFON/...),
    # nicht beliebige Klartext-Dateinamen.
    filename = "kunde-DE89370400440532013000.txt"
    att = _fake_attachment(filename, "text/plain", b"hallo")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools
    import src.pii as pii

    anonymizer = pii.Anonymizer()
    result = chat_tools.mail_anhaenge_auflisten("info", uid="42", anonymizer=anonymizer)

    assert result["anhaenge"][0]["dateiname"] != filename
    assert "DE89370400440532013000" not in result["anhaenge"][0]["dateiname"]


def test_mail_anhaenge_auflisten_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.mail_anhaenge_auflisten("../evil", uid="42")


def test_mail_anhaenge_auflisten_unknown_uid_returns_error(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mock_mailbox = _fake_mailbox([])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhaenge_auflisten("info", uid="42")

    assert "fehler" in result


# --- mail_anhang_lesen: direkte Textformate -----------------------------------


def test_mail_anhang_lesen_reads_txt_file(mocker, tmp_path, monkeypatch):
    att = _fake_attachment("notiz.txt", "text/plain", "Hällo Wört".encode("utf-8"), charset="utf-8")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="notiz.txt")

    assert result["text_redigiert"] == "Hällo Wört"
    assert result["dateiname"] == "notiz.txt"
    assert result["typ"] == "text/plain"


def test_mail_anhang_lesen_reads_json_csv_md(mocker, tmp_path, monkeypatch):
    for filename, content_type, payload in (
        ("data.json", "application/json", b'{"a": 1}'),
        ("data.csv", "text/csv", b"a,b\n1,2"),
        ("notes.md", "text/markdown", b"# Titel"),
    ):
        att = _fake_attachment(filename, content_type, payload)
        msg = _msg_with_attachments([att])
        mock_mailbox = _fake_mailbox([msg])
        mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
        _setup_env(tmp_path, monkeypatch)
        _write_agent_env("info")

        import src.chat_tools as chat_tools

        result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index=filename)

        assert result["text_redigiert"] == payload.decode("utf-8")
        assert "fehler" not in result


def test_mail_anhang_lesen_charset_fallback_utf8_with_replace(mocker, tmp_path, monkeypatch):
    # Ungueltige Bytes fuer das angegebene Charset -> errors="replace" statt Crash.
    att = _fake_attachment("notiz.txt", "text/plain", b"\xff\xfe\x00", charset="ascii")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="notiz.txt")

    assert "fehler" not in result
    assert "�" in result["text_redigiert"] or result["text_redigiert"]


# --- mail_anhang_lesen: PDF (importorskip) ------------------------------------


def _make_mini_pdf_bytes(text: str) -> bytes:
    pypdf = pytest.importorskip("pypdf")
    import io

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_mail_anhang_lesen_pdf_extracts_text(mocker, tmp_path, monkeypatch):
    pytest.importorskip("pypdf")
    pdf_bytes = _make_mini_pdf_bytes("Rechnung Nr. 123")
    att = _fake_attachment("rechnung.pdf", "application/pdf", pdf_bytes)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="rechnung.pdf")

    assert "fehler" not in result
    assert result["typ"] == "application/pdf"
    assert isinstance(result["text_redigiert"], str)


def test_mail_anhang_lesen_pdf_missing_pypdf_returns_error(mocker, tmp_path, monkeypatch):
    """Fehlt pypdf (ImportError) -> fehler mit Metadaten, kein Crash. Über
    einen Import-Stub erzwungen, damit der Test AUCH mit installiertem pypdf
    läuft (D-hhm-06)."""
    att = _fake_attachment("rechnung.pdf", "application/pdf", b"%PDF-fake-bytes")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    monkeypatch.setitem(sys.modules, "pypdf", None)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="rechnung.pdf")

    assert "fehler" in result
    assert "pypdf" in result["fehler"]
    assert result["dateiname"] == "rechnung.pdf"
    assert result["typ"] == "application/pdf"
    assert result["groesse"] == len(b"%PDF-fake-bytes")


# --- mail_anhang_lesen: unlesbare Formate / Größenlimit -----------------------


def test_mail_anhang_lesen_unreadable_format_returns_error_with_metadata(mocker, tmp_path, monkeypatch):
    att = _fake_attachment("foto.jpg", "image/jpeg", b"\xff\xd8\xff\xe0binary")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="foto.jpg")

    assert "fehler" in result
    assert "nicht lesbar" in result["fehler"]
    assert result["dateiname"] == "foto.jpg"
    assert result["typ"] == "image/jpeg"


def test_mail_anhang_lesen_over_size_limit_returns_error_no_extraction(mocker, tmp_path, monkeypatch):
    big_payload = b"x" * 1000
    att = _fake_attachment("gross.txt", "text/plain", big_payload)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)
    monkeypatch.setenv("MAX_ATTACHMENT_MB", "0")  # -> 0 Bytes Limit, garantiert überschritten

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="gross.txt")

    assert "fehler" in result
    assert "MB" in result["fehler"]
    assert result["groesse"] == 1000


# --- Anon-vor-Truncate ----------------------------------------------------------


def test_mail_anhang_lesen_anon_before_truncate(mocker, tmp_path, monkeypatch):
    iban_text = "Bitte überweisen Sie auf DE89370400440532013000, danke."
    att = _fake_attachment("notiz.txt", "text/plain", iban_text.encode("utf-8"))
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="notiz.txt")

    assert "DE89370400440532013000" not in result["text_redigiert"]
    assert "[IBAN_REDACTED]" in result["text_redigiert"]


def test_mail_anhang_lesen_anon_with_anonymizer_pseudonymizes(mocker, tmp_path, monkeypatch):
    iban_text = "IBAN: DE89370400440532013000"
    att = _fake_attachment("notiz.txt", "text/plain", iban_text.encode("utf-8"))
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools
    import src.pii as pii

    anonymizer = pii.Anonymizer()
    result = chat_tools.mail_anhang_lesen(
        "info", uid="42", dateiname_oder_index="notiz.txt", anonymizer=anonymizer
    )

    assert "DE89370400440532013000" not in result["text_redigiert"]


# --- Auswahl-Reihenfolge (Name > Index) ----------------------------------------


def test_mail_anhang_lesen_no_match_lists_available_filenames(mocker, tmp_path, monkeypatch):
    att = _fake_attachment("notiz.txt", "text/plain", b"hallo")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="nicht-vorhanden.txt")

    assert "fehler" in result
    assert "notiz.txt" in result["fehler"]


def test_mail_anhang_lesen_selects_by_numeric_index_without_name_match(mocker, tmp_path, monkeypatch):
    att0 = _fake_attachment("a.txt", "text/plain", b"AAA")
    att1 = _fake_attachment("b.txt", "text/plain", b"BBB")
    msg = _msg_with_attachments([att0, att1])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="1")

    assert result["dateiname"] == "b.txt"
    assert result["text_redigiert"] == "BBB"


def test_mail_anhang_lesen_filename_matching_digits_wins_over_index(mocker, tmp_path, monkeypatch):
    """Eine Datei namens '12' wird ueber den Namen getroffen, nicht als
    Index 12 missverstanden (D-hhm-06)."""
    att_named_12 = _fake_attachment("12", "text/plain", b"Datei namens 12")
    msg = _msg_with_attachments([att_named_12])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="12")

    assert result["dateiname"] == "12"
    assert result["text_redigiert"] == "Datei namens 12"


def test_mail_anhang_lesen_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.mail_anhang_lesen("../evil", uid="42", dateiname_oder_index="a.txt")


def test_mail_anhang_lesen_invalid_uid_returns_error():
    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="1:*", dateiname_oder_index="a.txt")

    assert "fehler" in result


def test_mail_anhang_lesen_mark_seen_false(mocker, tmp_path, monkeypatch):
    att = _fake_attachment("notiz.txt", "text/plain", b"hallo")
    msg = _msg_with_attachments([att])
    mock_mailbox = _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="notiz.txt")

    _args, kwargs = mock_mailbox.fetch.call_args
    assert kwargs.get("mark_seen") is False


# --- Registry -----------------------------------------------------------------


def test_attachment_tools_registered_everywhere():
    import src.chat_tools as chat_tools

    for name in ("mail_anhaenge_auflisten", "mail_anhang_lesen"):
        assert name in chat_tools.TOOL_HANDLERS
        schema_names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
        assert name in schema_names
        assert name in chat_tools._TOOL_ACTIVITY_LABELS
        assert name in chat_tools._ANON_AWARE_TOOLS
