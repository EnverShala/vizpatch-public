"""Tests für den Chat-Mailversand (Quick 260801-2yg, Tasks 2+3).

Deckt ab: `ENABLE_MAIL_SEND`-Flag (`_mail_send_allowed`), `mail_senden` (Vorschau,
sendet NICHTS) + `mail_senden_verwerfen`, das fail-closed Cross-Request-Gate +
Vorschau-Token-Bindung von `mail_senden_bestaetigen`, SMTP-Versand-Inhalt, Anhang,
Sent-Kopie sowie Registrierung/Schema-Drift der drei Versand-Werkzeuge.

`_setup_env`/`_write_agent_env`/`_fake_mailbox`/`_fake_folder_info` sind lokale
Kopien der etablierten Helfer aus `test_chat_tools.py` (jede Testdatei definiert
ihre eigenen Helfer, siehe dortiges Muster).
"""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from .anthropic_stream_fake import as_streaming_client


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


def _chat_system_prompt(tmp_path):
    real_path = Path(__file__).resolve().parent.parent / "prompts" / "chat-system.txt"
    return real_path


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


def _msg(
    subject="Frage zum Angebot",
    text="Hallo, hier ist die Frage.",
    uid="42",
    from_="kunde@example.com",
    to=(),
    headers=None,
):
    m = MagicMock()
    m.subject = subject
    m.text = text
    m.html = None
    m.uid = uid
    m.from_ = from_
    m.to = to
    m.date = None
    m.headers = headers or {}
    return m


def _fake_folder_info(name, flags=()):
    return types.SimpleNamespace(name=name, flags=flags)


def _fake_mailbox(messages=None, login_raises=False):
    """Analog `test_chat_tools._fake_mailbox` — hier ohne Move-Semantik (die
    Versand-Werkzeuge verschieben nichts): `fetch` liefert IMMER `messages`."""
    mailbox = MagicMock()
    mailbox.__enter__ = MagicMock(return_value=mailbox)
    mailbox.__exit__ = MagicMock(return_value=False)
    if login_raises:
        mailbox.login.side_effect = RuntimeError("auth failed")
    msgs = list(messages or [])
    mailbox.fetch.side_effect = lambda *a, **kw: list(msgs)
    mailbox.folder.list.return_value = []
    return mailbox


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_use_block(name, tool_input, tool_id="toolu_1"):
    return types.SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def _fake_response(stop_reason, content):
    return types.SimpleNamespace(stop_reason=stop_reason, content=content)


# --- Task 2, Flag `_mail_send_allowed` ---------------------------------------


def test_mail_send_allowed_default_true(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    assert chat_tools._mail_send_allowed("info") is True


def test_mail_send_allowed_per_agent_false(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_env("info", {"ENABLE_MAIL_SEND": "false"})

    assert chat_tools._mail_send_allowed("info") is False


def test_mail_send_allowed_global_false(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_MAIL_SEND", "false")
    import src.chat_tools as chat_tools

    assert chat_tools._mail_send_allowed("info") is False


def test_mail_send_allowed_per_agent_true_overrides_global_false(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_env("info", {"ENABLE_MAIL_SEND": "true"})
    monkeypatch.setenv("ENABLE_MAIL_SEND", "false")

    assert chat_tools._mail_send_allowed("info") is True


# --- Task 2, mail_senden (Vorschau) -------------------------------------------


def test_mail_senden_creates_full_preview(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.mail_senden(
        "info", text="Guten Tag, hier die Antwort.", betreff="Angebot", an="kunde@example.com", session_id="s1"
    )

    assert result["vorschau_erstellt"] is True
    assert result["an"] == "kunde@example.com"
    assert result["betreff"] == "Angebot"
    assert result["text"] == "Guten Tag, hier die Antwort."
    assert result["anhang_dateiname"] is None
    assert result["antwort_auf_uid"] is None


def test_mail_senden_returns_distinct_nonempty_tokens(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    r1 = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1")
    token1 = r1["vorschau_token"]
    assert token1
    pending = chat_tools._peek_pending_send("info", "s1")
    assert pending["vorschau_token"] == token1

    r2 = chat_tools.mail_senden("info", text="B", an="a@x.de", session_id="s1")
    assert r2["vorschau_token"]
    assert r2["vorschau_token"] != token1


def test_mail_senden_never_calls_deliver_or_mailbox_append(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mailbox_cls = mocker.patch("src.chat_tools.MailBox")

    chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1")

    deliver_mock.assert_not_called()
    mailbox_cls.return_value.append.assert_not_called()


def test_mail_senden_reply_resolves_recipient_subject_threading(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg(
        subject="Frage zum Angebot",
        from_="kunde@example.com",
        uid="42",
        headers={"message-id": "<orig@kunde>", "references": "<prev@kunde>"},
    )
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_senden("info", text="Antworttext", in_reply_to_uid="42", session_id="s1")

    assert result["vorschau_erstellt"] is True
    assert result["an"] == "kunde@example.com"
    assert result["betreff"] == "Re: Frage zum Angebot"
    assert result["antwort_auf_uid"] == "42"

    pending = chat_tools._peek_pending_send("info", "s1")
    assert pending["in_reply_to"] == "<orig@kunde>"
    assert "<prev@kunde>" in pending["references"]
    assert "<orig@kunde>" in pending["references"]


def test_mail_senden_invalid_uid_range_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.mail_senden("info", text="A", in_reply_to_uid="1:*", session_id="s1")

    assert "fehler" in result


def test_mail_senden_empty_text_returns_error_no_pending(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.mail_senden("info", text="   ", an="a@x.de", session_id="s1")

    assert "fehler" in result
    assert chat_tools._peek_pending_send("info", "s1") is None


def test_mail_senden_no_recipient_no_reply_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.mail_senden("info", text="A", session_id="s1")

    assert "fehler" in result


def test_mail_senden_empty_session_id_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="")

    assert "fehler" in result


def test_mail_senden_with_pending_upload_names_attachment_without_consuming(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    tmp_file = tmp_path / "upload.pdf"
    tmp_file.write_bytes(b"content")
    chat_tools.register_pending_upload("info", "s1", tmp_file, "upload.pdf", 7, "application/pdf")

    result = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1")

    assert result["anhang_dateiname"] == "upload.pdf"
    assert result["anhang_groesse"] == 7
    assert chat_tools._peek_pending_upload("info", "s1") is not None


def test_mail_senden_second_call_replaces_first(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    chat_tools.mail_senden("info", text="Erst", an="a@x.de", session_id="s1")
    chat_tools.mail_senden("info", text="Zweit", an="b@x.de", session_id="s1")

    pending = chat_tools._peek_pending_send("info", "s1")
    assert pending["text"] == "Zweit"
    assert pending["an"] == "b@x.de"


def test_mail_senden_disabled_flag_returns_error_no_pending(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_env("info", {"ENABLE_MAIL_SEND": "false"})

    result = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1")

    assert "fehler" in result
    assert chat_tools._peek_pending_send("info", "s1") is None


def test_mail_senden_crlf_injection_in_an_normalized(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    # Direkter Nachweis, dass die wiederverwendete `_sanitize_address_list`
    # (T-2yg-06) CRLF strukturell neutralisiert (kein Header-Splitting).
    sanitized = chat_tools._sanitize_address_list("a@x.de\r\nBcc: evil@x.de")
    assert "\r" not in sanitized
    assert "\n" not in sanitized

    result = chat_tools.mail_senden("info", text="A", an="a@x.de\r\nBcc: evil@x.de", session_id="s1")

    # Ob als Pending-Send akzeptiert oder (fail-safe, da nach der Sanitisierung
    # keine eindeutige Adresse mehr auflösbar ist) mit "kein verwertbarer
    # Empfänger" abgelehnt: in KEINEM Fall taucht ein rohes CRLF im Ergebnis
    # oder im Pending-Send auf.
    assert "\r" not in str(result)
    assert "\n" not in str(result)
    pending = chat_tools._peek_pending_send("info", "s1")
    if pending is not None:
        assert "\r" not in pending["an"]
        assert "\n" not in pending["an"]


def test_mail_senden_multiple_recipients_split_and_header_form(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    chat_tools.mail_senden("info", text="A", an="a@x.de, b@x.de", session_id="s1")

    pending = chat_tools._peek_pending_send("info", "s1")
    assert pending["empfaenger"] == ["a@x.de", "b@x.de"]
    assert pending["an"] == "a@x.de, b@x.de"


# --- Task 2, mail_senden_verwerfen --------------------------------------------


def test_mail_senden_verwerfen_after_preview(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1")
    result = chat_tools.mail_senden_verwerfen("info", session_id="s1")

    assert result == {"verworfen": True}
    assert chat_tools._peek_pending_send("info", "s1") is None


def test_mail_senden_verwerfen_without_preview_no_crash(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    result = chat_tools.mail_senden_verwerfen("info", session_id="s1")

    assert result["verworfen"] is False


def test_pending_send_ttl_expiry(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    base_time = 1_000_000.0
    monkeypatch.setattr(chat_tools.time, "time", lambda: base_time)
    chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1")

    monkeypatch.setattr(
        chat_tools.time, "time", lambda: base_time + chat_tools._PENDING_SEND_TTL_SECONDS + 1
    )

    assert chat_tools._peek_pending_send("info", "s1") is None


# --- Task 3: mail_senden_bestaetigen — Cross-Request-Gate (fail-closed) ------


def test_mail_senden_bestaetigen_same_request_id_blocks(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R1"
    )

    assert "fehler" in result
    assert "Bestätigung" in result["fehler"]
    deliver_mock.assert_not_called()
    assert chat_tools._peek_pending_send("info", "s1") is not None


def test_mail_senden_bestaetigen_both_empty_request_id_blocks_fail_closed(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1")
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


def test_mail_senden_bestaetigen_empty_preview_request_id_blocks(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1")
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


def test_mail_senden_bestaetigen_different_request_ids_sends(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    assert result.get("gesendet") is True
    deliver_mock.assert_called_once()
    assert chat_tools._peek_pending_send("info", "s1") is None


def test_mail_senden_bestaetigen_without_preview_returns_error(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")

    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token="whatever", session_id="s1", request_id="R2"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


def test_mail_senden_bestaetigen_after_ttl_expiry_returns_error(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    base_time = 2_000_000.0
    monkeypatch.setattr(chat_tools.time, "time", lambda: base_time)
    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")

    monkeypatch.setattr(
        chat_tools.time, "time", lambda: base_time + chat_tools._PENDING_SEND_TTL_SECONDS + 1
    )
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


# --- Task 3: Vorschau-Token-Bindung -------------------------------------------


def test_mail_senden_bestaetigen_wrong_token_returns_error_pending_remains(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")

    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token="erfunden", session_id="s1", request_id="R2"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()
    assert chat_tools._peek_pending_send("info", "s1") is not None


def test_mail_senden_bestaetigen_empty_token_returns_error(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")

    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token="", session_id="s1", request_id="R2"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


def test_mail_senden_bestaetigen_old_token_invalid_after_replace_new_token_works(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview_a = chat_tools.mail_senden("info", text="Erst", an="a@x.de", session_id="s1", request_id="R1")
    token_a = preview_a["vorschau_token"]
    preview_b = chat_tools.mail_senden("info", text="Zweit", an="b@x.de", session_id="s1", request_id="R2")
    token_b = preview_b["vorschau_token"]

    result_a = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=token_a, session_id="s1", request_id="R3"
    )
    assert "fehler" in result_a
    deliver_mock.assert_not_called()

    result_b = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=token_b, session_id="s1", request_id="R3"
    )
    assert result_b.get("gesendet") is True
    deliver_mock.assert_called_once()


# --- Task 3: Ende-zu-Ende über _run_anthropic_tool_loop -----------------------


def test_run_agentic_chat_send_confirm_same_request_blocked(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")
    import src.chat_tools as chat_tools

    monkeypatch.setattr(chat_tools.secrets, "token_hex", lambda n: "FIXEDTOKEN")
    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")

    round1 = _fake_response(
        "tool_use", [_tool_use_block("mail_senden", {"text": "A", "an": "a@x.de"}, tool_id="t1")]
    )
    round2 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_senden_bestaetigen", {"vorschau_token": "FIXEDTOKEN"}, tool_id="t2")],
    )
    round3 = _fake_response("end_turn", [_text_block("Fertig.")])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2, round3]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    list(chat_tools.run_agentic_chat("info", "Sende eine Mail", session_id="s1"))

    deliver_mock.assert_not_called()


def test_run_agentic_chat_send_confirm_across_two_calls_sends_once(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")
    import src.chat_tools as chat_tools

    monkeypatch.setattr(chat_tools.secrets, "token_hex", lambda n: "FIXEDTOKEN")
    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    round1 = _fake_response(
        "tool_use", [_tool_use_block("mail_senden", {"text": "A", "an": "a@x.de"}, tool_id="t1")]
    )
    round1b = _fake_response("end_turn", [_text_block("Vorschau erstellt.")])
    round2 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_senden_bestaetigen", {"vorschau_token": "FIXEDTOKEN"}, tool_id="t2")],
    )
    round2b = _fake_response("end_turn", [_text_block("Gesendet.")])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round1b, round2, round2b]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    list(chat_tools.run_agentic_chat("info", "Sende eine Mail", session_id="s1"))
    list(chat_tools.run_agentic_chat("info", "Ja, sende sie so.", session_id="s1"))

    deliver_mock.assert_called_once()


# --- Task 3: Versand-Inhalt ----------------------------------------------------


def test_mail_senden_bestaetigen_bytes_contain_headers_and_text(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_senden(
        "info", text="Hallo Kunde", betreff="Angebot", an="kunde@example.com", session_id="s1", request_id="R1"
    )
    chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    _, kwargs = deliver_mock.call_args
    raw = kwargs["raw_bytes"]
    assert b"To: kunde@example.com" in raw
    assert b"Subject: Angebot" in raw
    assert b"Hallo Kunde" in raw


def test_mail_senden_bestaetigen_reply_bytes_contain_threading_headers(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg(
        subject="Frage",
        from_="kunde@example.com",
        uid="42",
        headers={"message-id": "<orig@kunde>", "references": ""},
    )
    mock_mailbox = _fake_mailbox([original])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")

    preview = chat_tools.mail_senden("info", text="Antwort", in_reply_to_uid="42", session_id="s1", request_id="R1")
    chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    _, kwargs = deliver_mock.call_args
    raw = kwargs["raw_bytes"]
    assert b"In-Reply-To: <orig@kunde>" in raw
    assert b"References:" in raw


def test_mail_senden_bestaetigen_envelope_from_and_recipients(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_senden("info", text="A", an="kunde@example.com", session_id="s1", request_id="R1")
    chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    _, kwargs = deliver_mock.call_args
    assert kwargs["envelope_from"] == "info@ionos.de"
    assert kwargs["recipients"] == ["kunde@example.com"]


def test_mail_senden_bestaetigen_multi_recipients_envelope_list_header_comma(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de, b@x.de", session_id="s1", request_id="R1")
    chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    _, kwargs = deliver_mock.call_args
    assert kwargs["recipients"] == ["a@x.de", "b@x.de"]
    assert b"To: a@x.de, b@x.de" in kwargs["raw_bytes"]


def test_mail_senden_bestaetigen_second_call_does_not_resend(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")
    chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )
    result2 = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R3"
    )

    assert deliver_mock.call_count == 1
    assert "fehler" in result2


# --- Task 3: Anhang -------------------------------------------------------------


def test_mail_senden_bestaetigen_with_attachment_included_and_consumed(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    tmp_file = tmp_path / "upload.pdf"
    tmp_file.write_bytes(b"%PDF-1.4 content")
    chat_tools.register_pending_upload(
        "info", "s1", tmp_file, "upload.pdf", tmp_file.stat().st_size, "application/pdf"
    )

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")
    assert preview["anhang_dateiname"] == "upload.pdf"

    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    assert result.get("gesendet") is True
    _, kwargs = deliver_mock.call_args
    raw = kwargs["raw_bytes"]
    assert b"Content-Disposition: attachment" in raw
    assert b"upload.pdf" in raw
    assert chat_tools._peek_pending_upload("info", "s1") is None
    assert not tmp_file.exists()


def test_mail_senden_bestaetigen_attachment_missing_aborts(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    deliver_mock = mocker.patch("src.chat_tools.mail_send.deliver")

    tmp_file = tmp_path / "upload.pdf"
    tmp_file.write_bytes(b"content")
    chat_tools.register_pending_upload("info", "s1", tmp_file, "upload.pdf", 7, "application/pdf")

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")

    # Upload danach nicht mehr vorhanden (z.B. TTL/anderweitig konsumiert):
    chat_tools._consume_pending_upload("info", "s1")

    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    assert "fehler" in result
    deliver_mock.assert_not_called()


def test_mail_senden_bestaetigen_smtp_failure_keeps_pending_and_tmp(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mocker.patch(
        "src.chat_tools.mail_send.deliver",
        side_effect=chat_tools.mail_send.SmtpSendError("Anmeldung beim Mailserver fehlgeschlagen."),
    )

    tmp_file = tmp_path / "upload.pdf"
    tmp_file.write_bytes(b"content")
    chat_tools.register_pending_upload("info", "s1", tmp_file, "upload.pdf", 7, "application/pdf")

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    assert result["fehler"] == "Anmeldung beim Mailserver fehlgeschlagen."
    assert tmp_file.exists()
    assert chat_tools._peek_pending_send("info", "s1") is not None


# --- Task 3: Sent-Kopie ----------------------------------------------------------


def test_mail_senden_bestaetigen_appends_sent_copy_once(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    mock_mailbox.append.assert_called_once()
    assert result["kopie_ordner"] == "Gesendete Objekte"


def test_mail_senden_bestaetigen_gmail_skips_sent_copy(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", imap_user="info@gmail.com")
    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    mock_mailbox.append.assert_not_called()
    assert result["kopie_ordner"] is None
    assert "hinweis" in result


def test_mail_senden_bestaetigen_sent_copy_failure_still_reports_success(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.mail_send.deliver")
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Gesendete Objekte", flags=("\\Sent",))]
    mock_mailbox.append.side_effect = RuntimeError("append failed")
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    preview = chat_tools.mail_senden("info", text="A", an="a@x.de", session_id="s1", request_id="R1")
    result = chat_tools.mail_senden_bestaetigen(
        "info", vorschau_token=preview["vorschau_token"], session_id="s1", request_id="R2"
    )

    assert result["gesendet"] is True
    assert "hinweis" in result


# --- Task 3: Registrierung / Schema-Drift ----------------------------------------


def test_mail_send_tools_registered_in_handlers_and_schemas():
    import src.chat_tools as chat_tools

    schema_names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    for name in ("mail_senden", "mail_senden_bestaetigen", "mail_senden_verwerfen"):
        assert name in chat_tools.TOOL_HANDLERS
        assert name in schema_names


def test_mail_senden_bestaetigen_schema_has_only_vorschau_token():
    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "mail_senden_bestaetigen")

    assert set(schema["input_schema"]["properties"].keys()) == {"vorschau_token"}
    assert schema["input_schema"]["required"] == ["vorschau_token"]


def test_tool_schemas_for_filters_mail_send_tools_when_disabled(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_env("info", {"ENABLE_MAIL_SEND": "false"})

    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert not ({"mail_senden", "mail_senden_bestaetigen", "mail_senden_verwerfen"} & names)


def test_tool_schemas_for_includes_mail_send_tools_by_default(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert {"mail_senden", "mail_senden_bestaetigen", "mail_senden_verwerfen"} <= names


def test_mail_send_schemas_do_not_expose_session_or_request_id():
    import src.chat_tools as chat_tools

    for name in ("mail_senden", "mail_senden_bestaetigen", "mail_senden_verwerfen"):
        schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == name)
        props = schema["input_schema"]["properties"]
        assert "session_id" not in props
        assert "request_id" not in props


def test_mail_senden_bestaetigen_denied_directly_when_disabled(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_env("info", {"ENABLE_MAIL_SEND": "false"})

    result = chat_tools.mail_senden_bestaetigen("info", vorschau_token="x", session_id="s1", request_id="R2")

    assert "fehler" in result
