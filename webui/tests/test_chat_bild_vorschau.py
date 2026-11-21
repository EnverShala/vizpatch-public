"""Tests für die Bild-Vorschau in der Chat-Pane (Quick 260813-wea).

Task 1 — Abschnitt "Endpoint": `_bild_vorschau_allowed`, `_preview_size_limit_bytes`,
`anhang_vorschau_bytes` (chat_tools.py) sowie `GET /chat/{agent_id}/anhang`
(main.py). Deckt jeden Statuscode aus dem `<vertrag>`-Abschnitt des Plans ab:
404 (agent_id unbekannt/Flag aus/nicht gefunden), 400 (uid/ordner/index),
413 (Deckel), 415 (kein Bild), 502 (IMAP-Fehler) sowie den 200-Erfolgsfall
mit den vorgeschriebenen Response-Headern.

Weitere Abschnitte ("Ereignis + Aushandlung" Task 2, "Browser-Strecke" Task 5)
werden in denselben Datei-Namensraum ergänzt.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from .anthropic_stream_fake import as_streaming_client
from .test_chat_attachment_tools import _fake_attachment, _msg_with_attachments
from .test_chat_tools import (
    _chat_system_prompt,
    _fake_mailbox,
    _fake_response,
    _setup_env,
    _text_block,
    _tool_use_block,
    _write_agent_env,
)

TEST_ORIGIN = "http://testserver"


def _setup_attachment_env(mocker, tmp_path, monkeypatch, msg):
    """Analog `test_chat_attachment_tools._setup_attachment_env` — hier lokal
    dupliziert, damit diese Datei keine Zusatzabhängigkeit zur Anordnung der
    Fixtures in test_chat_attachment_tools.py hat."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mock_mailbox = _fake_mailbox([msg])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    return mock_mailbox


# --- _bild_vorschau_allowed ----------------------------------------------------


def test_bild_vorschau_allowed_default_true(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    assert chat_tools._bild_vorschau_allowed("info") is True


@pytest.mark.parametrize("value", ["1", "FALSCH", "yes", ""])
def test_bild_vorschau_allowed_true_for_non_false_values(value, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ENABLE_BILD_VORSCHAU", value)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    assert chat_tools._bild_vorschau_allowed("info") is True


@pytest.mark.parametrize("value", ["false", "FALSE", " false "])
def test_bild_vorschau_allowed_false_only_for_exact_false(value, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ENABLE_BILD_VORSCHAU", value)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    assert chat_tools._bild_vorschau_allowed("info") is False


def test_bild_vorschau_allowed_per_agent_wins_over_global(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ENABLE_BILD_VORSCHAU", "false")
    import src.agents_io as agents_io

    agents_io.write_env(
        "info",
        {
            "IMAP_USER": "info@ionos.de",
            "IMAP_PASSWORD": "imap-pw",
            "LLM_API_KEY": "sk-ant-test-key",
            "LLM_PROVIDER": "anthropic",
            "ENABLE_BILD_VORSCHAU": "true",
        },
    )
    import src.chat_tools as chat_tools

    assert chat_tools._bild_vorschau_allowed("info") is True


# --- _preview_size_limit_bytes -------------------------------------------------


def test_preview_size_limit_bytes_default_10mb(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    assert chat_tools._preview_size_limit_bytes() == 10 * 1024 * 1024


def test_preview_size_limit_bytes_env_override(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("MAX_PREVIEW_MB", "3")
    import src.chat_tools as chat_tools

    assert chat_tools._preview_size_limit_bytes() == 3 * 1024 * 1024


# --- anhang_vorschau_bytes ------------------------------------------------------


def test_anhang_vorschau_bytes_jpeg_success_ignores_declared_content_type(mocker, tmp_path, monkeypatch):
    """Magic Bytes sind die einzige Autorität — auch wenn die Mail
    `application/octet-stream` deklariert, liefert der Endpoint `image/jpeg`."""
    payload = b"\xff\xd8\xff" + b"x" * 40
    att = _fake_attachment("foto.jpg", "application/octet-stream", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "42", 0, "INBOX")
    assert result["status"] == 200
    assert result["bytes"] == payload
    assert result["typ"] == "image/jpeg"
    assert result["groesse"] == len(payload)


def test_anhang_vorschau_bytes_pdf_rejected_415(mocker, tmp_path, monkeypatch):
    payload = b"%PDF-1.4" + b"x" * 40
    att = _fake_attachment("scan.pdf", "application/pdf", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "42", 0, "INBOX")
    assert result["status"] == 415
    assert "bytes" not in result


@pytest.mark.parametrize(
    "filename,content_type,payload",
    [
        ("scan.tiff", "image/tiff", b"II*\x00" + b"x" * 40),
        ("foto.heic", "image/heic", b"\x00\x00\x00\x18ftypheic" + b"x" * 20),
        ("archiv.zip", "application/zip", b"PK\x03\x04" + b"x" * 40),
        ("notiz.txt", "text/plain", b"hallo welt" * 5),
    ],
)
def test_anhang_vorschau_bytes_non_image_formats_rejected_415(
    filename, content_type, payload, mocker, tmp_path, monkeypatch
):
    att = _fake_attachment(filename, content_type, payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "42", 0, "INBOX")
    assert result["status"] == 415
    assert "bytes" not in result


def test_anhang_vorschau_bytes_over_limit_413_no_bytes(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_PREVIEW_MB", "0")
    payload = b"\xff\xd8\xff" + b"x" * 40
    att = _fake_attachment("foto.jpg", "image/jpeg", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "42", 0, "INBOX")
    assert result["status"] == 413
    assert "bytes" not in result


def test_anhang_vorschau_bytes_invalid_uid_400(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "1:*", 0, "INBOX")
    assert result["status"] == 400


def test_anhang_vorschau_bytes_invalid_folder_400(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "42", 0, "bad\r\nfolder")
    assert result["status"] == 400


@pytest.mark.parametrize("bad_index", ["-1", "keine-zahl"])
def test_anhang_vorschau_bytes_invalid_index_400(bad_index, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "42", bad_index, "INBOX")
    assert result["status"] == 400


def test_anhang_vorschau_bytes_mail_not_found_404(mocker, tmp_path, monkeypatch):
    _setup_attachment_env(mocker, tmp_path, monkeypatch, _msg_with_attachments([], uid="1"))
    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "999", 0, "INBOX")
    assert result["status"] == 404


def test_anhang_vorschau_bytes_attachment_index_not_found_404(mocker, tmp_path, monkeypatch):
    msg = _msg_with_attachments([], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)
    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "42", 0, "INBOX")
    assert result["status"] == 404


def test_anhang_vorschau_bytes_imap_connect_failed_502(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mocker.patch("src.chat_tools.MailBox", side_effect=RuntimeError("connection refused"))
    import src.chat_tools as chat_tools

    result = chat_tools.anhang_vorschau_bytes("info", "42", 0, "INBOX")
    assert result["status"] == 502


# --- GET /chat/{agent_id}/anhang -----------------------------------------------


def test_chat_anhang_endpoint_success_png(pw_set_client, mocker, tmp_path, monkeypatch):
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 40
    att = _fake_attachment("bild.png", "image/png", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    response = pw_set_client.get("/chat/info/anhang", params={"uid": "42", "ordner": "INBOX", "index": "0"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == payload


def test_chat_anhang_endpoint_unknown_agent_404(pw_set_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    response = pw_set_client.get("/chat/ghost/anhang", params={"uid": "42"})
    assert response.status_code == 404


def test_chat_anhang_endpoint_flag_false_404_no_imap_connection(pw_set_client, mocker, tmp_path, monkeypatch):
    """ENABLE_BILD_VORSCHAU=false -> 404 OHNE dass eine IMAP-Verbindung geoeffnet
    wird (Mock-Assertion: open_agent_mailbox/MailBox wird nicht aufgerufen)."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ENABLE_BILD_VORSCHAU", "false")
    _write_agent_env("info")
    mock_mailbox_cls = mocker.patch("src.chat_tools.MailBox")

    response = pw_set_client.get("/chat/info/anhang", params={"uid": "42", "ordner": "INBOX", "index": "0"})
    assert response.status_code == 404
    mock_mailbox_cls.assert_not_called()


def test_chat_anhang_endpoint_invalid_uid_400(pw_set_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    response = pw_set_client.get("/chat/info/anhang", params={"uid": "1:*", "ordner": "INBOX", "index": "0"})
    assert response.status_code == 400


def test_chat_anhang_endpoint_not_found_404(mocker, pw_set_client, tmp_path, monkeypatch):
    _setup_attachment_env(mocker, tmp_path, monkeypatch, _msg_with_attachments([], uid="1"))
    response = pw_set_client.get("/chat/info/anhang", params={"uid": "999", "ordner": "INBOX", "index": "0"})
    assert response.status_code == 404


def test_chat_anhang_endpoint_over_limit_413(mocker, pw_set_client, tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_PREVIEW_MB", "0")
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 40
    att = _fake_attachment("bild.png", "image/png", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    response = pw_set_client.get("/chat/info/anhang", params={"uid": "42", "ordner": "INBOX", "index": "0"})
    assert response.status_code == 413


def test_chat_anhang_endpoint_non_image_415(mocker, pw_set_client, tmp_path, monkeypatch):
    payload = b"%PDF-1.4" + b"x" * 40
    att = _fake_attachment("scan.pdf", "application/pdf", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    response = pw_set_client.get("/chat/info/anhang", params={"uid": "42", "ordner": "INBOX", "index": "0"})
    assert response.status_code == 415


def test_chat_anhang_endpoint_imap_error_502(mocker, pw_set_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mocker.patch("src.chat_tools.MailBox", side_effect=RuntimeError("connection refused"))

    response = pw_set_client.get("/chat/info/anhang", params={"uid": "42", "ordner": "INBOX", "index": "0"})
    assert response.status_code == 502


def test_is_addin_session_exempt_path_covers_anhang():
    import src.main as main_module

    assert main_module._is_addin_session_exempt_path("/chat/default/anhang") is True


# =====================================================================================
# Task 2 — Ereignis + Aushandlung
# =====================================================================================

# --- _parse_client_features -----------------------------------------------------


def test_parse_client_features_empty_string_yields_empty_set():
    import src.main as main_module

    assert main_module._parse_client_features("") == set()


def test_parse_client_features_reads_bild_case_insensitive_with_noise():
    import src.main as main_module

    assert main_module._parse_client_features("BILD, foo") == {"bild", "foo"}


def test_parse_client_features_never_raises_on_garbage():
    import src.main as main_module

    assert main_module._parse_client_features(",,,") == set()


# --- mail_anhang_lesen setzt `_vision_preview` (Task 2a) -----------------------


def test_mail_anhang_lesen_sets_vision_preview_for_image(mocker, tmp_path, monkeypatch):
    payload = b"\xff\xd8\xff" + b"x" * 40
    att = _fake_attachment("foto.jpg", "image/jpeg", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="foto.jpg")
    assert result["modus"] == "vision"
    preview = result["_vision_preview"]
    assert preview == {
        "uid": "42",
        "ordner": "INBOX",
        "index": 0,
        "dateiname": "foto.jpg",
        "typ": "image/jpeg",
        "groesse": len(payload),
    }


def test_mail_anhang_lesen_no_vision_preview_when_flag_false(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_BILD_VORSCHAU", "false")
    payload = b"\xff\xd8\xff" + b"x" * 40
    att = _fake_attachment("foto.jpg", "image/jpeg", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="foto.jpg")
    assert result["modus"] == "vision"
    assert "_vision_preview" not in result


def test_mail_anhang_lesen_no_vision_preview_for_pdf(mocker, tmp_path, monkeypatch):
    """PDF-Seiten erzeugen NIE ein Vorschaubild (D) — auch wenn der
    Scan-Verdacht greift und der Anhang als Vision-Dokument-Block geht."""
    payload = b"%PDF-1.4" + b"x" * 300
    att = _fake_attachment("scan.pdf", "application/pdf", payload)
    msg = _msg_with_attachments([att], uid="42")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)
    # `_extract_attachment_text` gemockt (statt eines echten pypdf-Aufrufs auf
    # Fake-Bytes) -- liefert dünnen Text OHNE `fehler`, sonst würde die
    # bestehende Textextraktion (`fehler` gesetzt) den Vision-Zweig gar nicht
    # erst betreten (`elif media == "application/pdf" and not fehler`).
    mocker.patch("src.chat_tools._extract_attachment_text", return_value=("x", None))
    mocker.patch("src.chat_tools._pdf_page_count", return_value=1)
    mocker.patch("src.chat_tools._pdf_text_is_thin", return_value=True)
    mocker.patch(
        "src.chat_tools._build_vision_blocks",
        return_value=(
            [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "QQ=="}}],
            None,
        ),
    )

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="scan.pdf")
    assert result["modus"] == "vision"
    assert "_vision_preview" not in result


# --- Tool-Loop: Ereignis-Yield + Aushandlung ------------------------------------


def _vision_payload_with_preview(vision_key="42|INBOX|foto.jpg", index=0):
    return {
        "uid": "42",
        "ordner": "INBOX",
        "dateiname": "foto.jpg",
        "typ": "image/jpeg",
        "groesse": 123,
        "modus": "vision",
        "hinweis": "Anhang liegt als Bild-/Dokument-Block bei.",
        "_vision_blocks": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUFBQQ=="}}
        ],
        "_vision_key": vision_key,
        "_vision_preview": {
            "uid": "42",
            "ordner": "INBOX",
            "index": index,
            "dateiname": "foto.jpg",
            "typ": "image/jpeg",
            "groesse": 123,
        },
    }


def test_run_agentic_chat_yields_bild_event_when_vision_preview_present(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"})],
    )
    round2 = _fake_response("end_turn", [_text_block("Das Bild zeigt eine Rechnung.")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload_with_preview()),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was ist auf dem Bild?"))
    bild_events = [e for e in events if e.get("type") == "bild"]
    assert len(bild_events) == 1
    assert bild_events[0]["bild"]["dateiname"] == "foto.jpg"
    assert bild_events[0]["bild"]["typ"] == "image/jpeg"


def test_run_agentic_chat_dedup_same_attachment_yields_bild_event_once(mocker, tmp_path, monkeypatch):
    """Derselbe Bild-Anhang zweimal in EINEM Loop-Aufruf ausgewertet -> genau
    EIN `event: bild` (das Ereignis erbt den bestehenden Vision-Blocks-Dedup)."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"}, tool_id="t1")],
    )
    round2 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"}, tool_id="t2")],
    )
    round3 = _fake_response("end_turn", [_text_block("ok")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2, round3]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload_with_preview()),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was ist auf dem Bild?"))
    bild_events = [e for e in events if e.get("type") == "bild"]
    assert len(bild_events) == 1


def test_run_agentic_chat_no_bild_event_for_pdf_document_block(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    pdf_payload = {
        "uid": "42",
        "ordner": "INBOX",
        "dateiname": "scan.pdf",
        "typ": "application/pdf",
        "groesse": 999,
        "modus": "vision",
        "hinweis": "Anhang liegt als Bild-/Dokument-Block bei.",
        "_vision_blocks": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "QQ=="}}
        ],
        "_vision_key": "42|INBOX|scan.pdf",
        # bewusst KEIN _vision_preview -- PDF erzeugt nie eine Vorschau (D).
    }
    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "scan.pdf"})],
    )
    round2 = _fake_response("end_turn", [_text_block("ok")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: pdf_payload),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was steht im PDF?"))
    assert not [e for e in events if e.get("type") == "bild"]


def test_run_agentic_chat_no_bild_event_when_flag_false(mocker, tmp_path, monkeypatch):
    """`ENABLE_BILD_VORSCHAU=false` -> kein `_vision_preview` im Payload
    (Task 2a greift bereits VOR dem Tool-Loop) -> kein `event: bild`, auch
    mit angekündigter Fähigkeit weiter unten in main.py."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    payload_ohne_preview = _vision_payload_with_preview()
    del payload_ohne_preview["_vision_preview"]

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"})],
    )
    round2 = _fake_response("end_turn", [_text_block("ok")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: payload_ohne_preview),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was ist auf dem Bild?"))
    assert not [e for e in events if e.get("type") == "bild"]


# --- main.py::_stream — SSE-Filterung nach client_features ----------------------


def test_chat_send_bild_event_absent_without_client_features(authed_client, mocker, tmp_path, monkeypatch):
    """Rückwärtskompatibilitäts-Test: ohne `client_features` bleibt der Stream
    OHNE `event: bild` — auch wenn ein Bild ausgewertet wurde. Ein altes,
    installiertes Add-in schickt dieses Feld nicht."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")
    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter(
            [
                {"type": "bild", "bild": {"uid": "42", "ordner": "INBOX", "index": 0, "dateiname": "foto.jpg", "typ": "image/jpeg", "groesse": 123}},
                {"type": "text", "text": "Das Bild zeigt eine Rechnung."},
            ]
        ),
    )

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Was ist auf dem Bild?"},
    )
    assert response.status_code == 200
    assert "event: bild" not in response.text
    assert "foto.jpg" not in response.text


def test_chat_send_bild_event_present_with_client_features_bild(authed_client, mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")
    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter(
            [
                {"type": "bild", "bild": {"uid": "42", "ordner": "INBOX", "index": 0, "dateiname": "foto.jpg", "typ": "image/jpeg", "groesse": 123}},
                {"type": "text", "text": "Das Bild zeigt eine Rechnung."},
            ]
        ),
    )

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Was ist auf dem Bild?", "client_features": "bild"},
    )
    assert response.status_code == 200
    assert response.text.count("event: bild") == 1
    assert '"dateiname": "foto.jpg"' in response.text or '"dateiname":"foto.jpg"' in response.text


def test_chat_send_bild_event_not_persisted_in_history(authed_client, mocker, tmp_path, monkeypatch):
    """Der Ereignis-Inhalt landet NICHT in `assistant_parts` und damit nicht im
    persistierten Verlauf (T-wea-07)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")
    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter(
            [
                {"type": "bild", "bild": {"uid": "42", "ordner": "INBOX", "index": 0, "dateiname": "foto.jpg", "typ": "image/jpeg", "groesse": 123}},
                {"type": "text", "text": "Das Bild zeigt eine Rechnung."},
            ]
        ),
    )
    mock_append = mocker.patch("src.main.chat_history.append_turns")

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Was ist auf dem Bild?", "client_features": "bild", "session_id": "sess-1"},
    )
    assert response.status_code == 200
    mock_append.assert_called_once()
    turns = mock_append.call_args.args[2]
    assistant_turn = next(t for t in turns if t["role"] == "assistant")
    assert "foto.jpg" not in assistant_turn["content"]
    assert "event: bild" not in assistant_turn["content"]


# =====================================================================================
# Task 5 — Browser-Strecke (struktureller Drift-Wächter über chat.js/chat.css)
# =====================================================================================

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_CHAT_JS = _STATIC_DIR / "chat.js"
_CHAT_CSS = _STATIC_DIR / "chat.css"

_JS_COMMENT_LINE_RE = re.compile(r"^\s*(//|\*)")


def _js_code_lines(text: str) -> list[str]:
    """Filtert Kommentarzeilen (`^\\s*//`, `^\\s*\\*` — Block-Kommentar-Zeilen
    innerhalb `/* */`) heraus, damit erklärende Kommentare den Wächter nicht
    selbst invalidieren (Muster `test_addin_readonly.py`)."""
    lines = []
    in_block_comment = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue
        if _JS_COMMENT_LINE_RE.match(raw_line):
            continue
        lines.append(raw_line)
    return lines


def _bild_zweig(js_filtered_text: str) -> str:
    """Schneidet den `eventType === 'bild'`-Zweig bis zum naechsten `} else`
    heraus."""
    start = js_filtered_text.index("eventType === 'bild'")
    rest = js_filtered_text[start:]
    end_marker = rest.index("} else {\n            assistantText")
    return rest[:end_marker]


def test_chat_js_contains_client_features_bild():
    filtered = "\n".join(_js_code_lines(_CHAT_JS.read_text(encoding="utf-8")))
    assert "client_features" in filtered
    assert "'bild'" in filtered


def test_chat_js_bild_zweig_builds_anhang_url_with_textcontent_no_innerhtml():
    filtered = "\n".join(_js_code_lines(_CHAT_JS.read_text(encoding="utf-8")))
    zweig = _bild_zweig(filtered)
    assert "/anhang" in zweig
    assert "textContent" in zweig
    assert "innerHTML" not in zweig


def test_chat_css_contains_bild_vorschau_and_280px():
    text = _CHAT_CSS.read_text(encoding="utf-8")
    assert ".chat-bild-vorschau" in text
    assert "280px" in text
