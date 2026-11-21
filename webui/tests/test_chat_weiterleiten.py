"""Tests für `mail_weiterleiten`/`mail_weiterleiten_bestaetigen` (Phase 13,
Plan 13-08, MBOX-13).

`mail_weiterleiten` ist das ERSTE neue ausgehende Werkzeug seit `mail_senden`
(Quick 260801-2yg) — eine weitergeleitete Mail verlässt das Postfach Richtung
eines NEUEN Empfängers und läuft deshalb ZWINGEND über dasselbe Zwei-Request-
Freigabe-Gate, KEIN zweiter Gate-Mechanismus (13-PATTERNS.md Abschnitt 3).
Deckt ab: Vorschau ohne Versand, fail-closed Cross-Request-Gate, Token-
Bindung, vollständige Original-Anhänge, KEIN Threading zur Originalmail,
Größenlimit bereits in der Vorschau, `ENABLE_MAIL_SEND`-Filterung sowie
Registrierung.

`_setup_env`/`_write_agent_env`/`_fake_mailbox`/`_fake_folder_info`/`_msg` sind
lokale Kopien der etablierten Helfer (Muster `test_chat_send.py`)."""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


def _write_agent_env(agent_id, provider="anthropic", api_key="sk-ant-test-key", imap_user="info@ionos.de"):
    import src.agents_io as agents_io

    agents_io.write_env(
        agent_id,
        {
            "IMAP_USER": imap_user,
            "IMAP_PASSWORD": "imap-pw",
            "LLM_API_KEY": api_key,
            "LLM_PROVIDER": provider,
        },
    )


def _att(filename="Anhang.pdf", content_type="application/pdf", size=1024, payload=b"%PDF-1.4 x"):
    return types.SimpleNamespace(
        filename=filename,
        content_disposition="attachment",
        content_type=content_type,
        size=size,
        payload=payload,
    )


def _msg(
    subject="Wichtige Rechnung",
    text="Bitte pruefen.",
    uid="42",
    from_="kunde@example.com",
    to=("info@ionos.de",),
    headers=None,
    attachments=(),
    date=None,
):
    m = MagicMock()
    m.subject = subject
    m.text = text
    m.html = None
    m.uid = uid
    m.from_ = from_
    m.to = to
    m.date = date
    m.headers = headers or {}
    m.attachments = list(attachments)
    return m


def _fake_folder_info(name, flags=()):
    return types.SimpleNamespace(name=name, flags=flags)


def _fake_mailbox(messages=None, login_raises=False):
    """Analog `test_chat_send._fake_mailbox` — `fetch` liefert IMMER
    `messages` (Weiterleiten verschiebt/löscht nichts)."""
    mailbox = MagicMock()
    mailbox.__enter__ = MagicMock(return_value=mailbox)
    mailbox.__exit__ = MagicMock(return_value=False)
    if login_raises:
        mailbox.login.side_effect = RuntimeError("auth failed")
    msgs = list(messages or [])
    mailbox.fetch.side_effect = lambda *a, **kw: list(msgs)
    mailbox.folder.list.return_value = []
    return mailbox


# --- mail_weiterleiten: Vorschau, sendet NICHTS -------------------------------


def test_mail_weiterleiten_creates_full_preview_sends_nothing(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg(attachments=[_att(filename="Rechnung.pdf")])
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_weiterleiten(
        "info", uid="42", an="steuerberater@example.com", session_id="s1"
    )

    assert result["vorschau_erstellt"] is True
    assert result["an"] == "steuerberater@example.com"
    assert result["betreff"] == "Fwd: Wichtige Rechnung"
    assert result["anzahl_anhaenge"] == 1
    assert result["anhang_dateinamen"] == ["Rechnung.pdf"]
    assert result["quelle_uid"] == "42"
    assert "vorschau_token" in result and result["vorschau_token"]

    deliver_mock.assert_not_called()
    mock_mailbox.append.assert_not_called()


def test_mail_weiterleiten_disabled_flag_returns_error_no_pending(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_env("info", {"ENABLE_MAIL_SEND": "false"})

    result = chat_tools.mail_weiterleiten("info", uid="42", an="a@x.de", session_id="s1")

    assert "fehler" in result
    assert chat_tools._peek_pending_forward("info", "s1") is None


def test_mail_weiterleiten_empty_session_id_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.mail_weiterleiten("info", uid="42", an="a@x.de", session_id="")

    assert "fehler" in result


def test_mail_weiterleiten_no_recipient_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.mail_weiterleiten("info", uid="42", an="", session_id="s1")

    assert "fehler" in result


def test_mail_weiterleiten_unknown_uid_returns_error(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_weiterleiten("info", uid="999", an="a@x.de", session_id="s1")

    assert "fehler" in result


def test_mail_weiterleiten_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.mail_weiterleiten("../evil", uid="42", an="a@x.de", session_id="s1")


def test_mail_weiterleiten_source_fetch_uses_mark_seen_false(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg()
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    chat_tools.mail_weiterleiten("info", uid="42", an="a@x.de", session_id="s1")

    for call in mock_mailbox.fetch.call_args_list:
        assert call.kwargs.get("mark_seen") is False


# --- Beide Werkzeuge im selben Request -> Ablehnung (fail-closed) -------------


def test_mail_weiterleiten_bestaetigen_same_request_id_blocks(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg()
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_weiterleiten(
        "info", uid="42", an="a@x.de", session_id="s1", request_id="R1"
    )
    result = chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R1"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()
    assert chat_tools._peek_pending_forward("info", "s1") is not None


def test_mail_weiterleiten_bestaetigen_both_empty_request_id_blocks_fail_closed(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg()
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_weiterleiten("info", uid="42", an="a@x.de", session_id="s1")
    result = chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


def test_mail_weiterleiten_bestaetigen_different_request_ids_sends(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg()
    mock_mailbox = _fake_mailbox([original])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_weiterleiten(
        "info", uid="42", an="a@x.de", session_id="s1", request_id="R1"
    )
    result = chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    assert result.get("weitergeleitet") is True
    deliver_mock.assert_called_once()
    assert chat_tools._peek_pending_forward("info", "s1") is None


# --- Token-Bindung -------------------------------------------------------------


def test_mail_weiterleiten_bestaetigen_wrong_token_returns_error(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg()
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    chat_tools.mail_weiterleiten("info", uid="42", an="a@x.de", session_id="s1", request_id="R1")
    result = chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token="erfunden", session_id="s1", request_id="R2"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


def test_mail_weiterleiten_bestaetigen_without_preview_returns_error(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")

    result = chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token="whatever", session_id="s1", request_id="R2"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


def test_mail_weiterleiten_bestaetigen_schema_has_only_vorschau_token():
    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "mail_weiterleiten_bestaetigen")

    assert set(schema["input_schema"]["properties"].keys()) == {"vorschau_token"}
    assert schema["input_schema"]["required"] == ["vorschau_token"]


# --- Original-Anhänge kommen vollständig mit ----------------------------------


def test_mail_weiterleiten_bestaetigen_includes_all_three_attachments(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg(
        attachments=[
            _att(filename="Rechnung.pdf", payload=b"pdf-inhalt"),
            _att(filename="Lieferschein.pdf", payload=b"lieferschein-inhalt"),
            _att(filename="Foto.png", content_type="image/png", payload=b"png-inhalt"),
        ]
    )
    mock_mailbox = _fake_mailbox([original])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_weiterleiten(
        "info", uid="42", an="a@x.de", session_id="s1", request_id="R1"
    )
    assert preview["anzahl_anhaenge"] == 3

    chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    _, kwargs = deliver_mock.call_args
    raw = kwargs["raw_bytes"]
    assert b"Rechnung.pdf" in raw
    assert b"Lieferschein.pdf" in raw
    assert b"Foto.png" in raw
    assert raw.count(b"Content-Disposition: attachment") == 3


def test_mail_weiterleiten_preview_lists_all_attachment_names(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg(
        attachments=[
            _att(filename="A.pdf"),
            _att(filename="B.pdf"),
            _att(filename="C.pdf"),
        ]
    )
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_weiterleiten("info", uid="42", an="a@x.de", session_id="s1")

    assert result["anhang_dateinamen"] == ["A.pdf", "B.pdf", "C.pdf"]


# --- Weiterleitungs-Kopf als Textzitat -----------------------------------------


def test_mail_weiterleiten_text_contains_forward_header_quote(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg(subject="Wichtige Rechnung", from_="kunde@example.com", text="Original-Text hier.")
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_weiterleiten("info", uid="42", an="a@x.de", session_id="s1")

    assert "Weitergeleitete Nachricht" in result["text"]
    assert "Von: kunde@example.com" in result["text"]
    assert "Betreff: Wichtige Rechnung" in result["text"]
    assert "Original-Text hier." in result["text"]


def test_mail_weiterleiten_zusatztext_erscheint_vor_dem_kopf(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg(subject="Wichtige Rechnung", from_="kunde@example.com")
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_weiterleiten(
        "info", uid="42", an="a@x.de", zusatztext="Bitte pruefen und buchen.", session_id="s1"
    )

    text = result["text"]
    assert text.index("Bitte pruefen und buchen.") < text.index("Weitergeleitete Nachricht")


# --- KEIN Threading zur Originalmail -------------------------------------------


def test_mail_weiterleiten_bestaetigen_sets_no_in_reply_to(mocker, tmp_path, monkeypatch):
    """MBOX-13, Betreiber-Entscheidung: ein Forward ist KEIN Reply."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg(headers={"message-id": "<orig@kunde>", "references": "<prev@kunde>"})
    mock_mailbox = _fake_mailbox([original])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_weiterleiten(
        "info", uid="42", an="a@x.de", session_id="s1", request_id="R1"
    )
    chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    _, kwargs = deliver_mock.call_args
    raw = kwargs["raw_bytes"]
    assert b"In-Reply-To:" not in raw
    assert b"orig@kunde" not in raw
    assert b"Message-ID:" in raw


# --- Größenlimit bereits in der Vorschau ---------------------------------------


def test_mail_weiterleiten_oversized_attachments_rejected_in_preview(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    zu_gross = 20 * 1024 * 1024  # > Default MAX_ATTACHMENT_MB=15
    original = _msg(attachments=[_att(filename="Riesig.pdf", size=zu_gross)])
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_weiterleiten("info", uid="42", an="a@x.de", session_id="s1")

    assert "fehler" in result
    assert "MB" in result["fehler"]
    assert chat_tools._peek_pending_forward("info", "s1") is None
    deliver_mock.assert_not_called()


# --- ENABLE_MAIL_SEND filtert beide Werkzeuge ----------------------------------


def test_tool_schemas_for_filters_forward_tools_when_mail_send_disabled(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_env("info", {"ENABLE_MAIL_SEND": "false"})

    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert not ({"mail_weiterleiten", "mail_weiterleiten_bestaetigen"} & names)


def test_tool_schemas_for_includes_forward_tools_by_default(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert {"mail_weiterleiten", "mail_weiterleiten_bestaetigen"} <= names


def test_mail_weiterleiten_schemas_do_not_expose_session_or_request_id():
    import src.chat_tools as chat_tools

    for name in ("mail_weiterleiten", "mail_weiterleiten_bestaetigen"):
        schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == name)
        props = schema["input_schema"]["properties"]
        assert "session_id" not in props
        assert "request_id" not in props


# --- Registrierung -------------------------------------------------------------


def test_mail_weiterleiten_tools_registered_in_handlers_and_schemas():
    import src.chat_tools as chat_tools

    schema_names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    for name in ("mail_weiterleiten", "mail_weiterleiten_bestaetigen"):
        assert name in chat_tools.TOOL_HANDLERS
        assert name in schema_names


def test_mail_weiterleiten_ist_session_und_request_scoped():
    import src.chat_tools as chat_tools

    for name in ("mail_weiterleiten", "mail_weiterleiten_bestaetigen"):
        assert name in chat_tools._SESSION_SCOPED_TOOLS
        assert name in chat_tools._REQUEST_SCOPED_TOOLS


def test_mail_weiterleiten_hat_aktivitaets_label():
    import src.chat_tools as chat_tools

    assert "…" in chat_tools._tool_activity_label("mail_weiterleiten")
    assert "…" in chat_tools._tool_activity_label("mail_weiterleiten_bestaetigen")


# --- Sent-Kopie (Muster mail_senden_bestaetigen) -------------------------------


def test_mail_weiterleiten_bestaetigen_appends_sent_copy_once(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg()
    mock_mailbox = _fake_mailbox([original])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_weiterleiten(
        "info", uid="42", an="a@x.de", session_id="s1", request_id="R1"
    )
    result = chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    mock_mailbox.append.assert_called_once()
    assert result["kopie_ordner"] == "Gesendete Objekte"


def test_mail_weiterleiten_bestaetigen_second_call_does_not_resend(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    original = _msg()
    mock_mailbox = _fake_mailbox([original])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_weiterleiten(
        "info", uid="42", an="a@x.de", session_id="s1", request_id="R1"
    )
    chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )
    result2 = chat_tools.mail_weiterleiten_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R3"
    )

    assert deliver_mock.call_count == 1
    assert "fehler" in result2
