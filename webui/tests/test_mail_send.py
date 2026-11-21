"""Tests für den SMTP-Versand-Kern (Quick 260801-2yg, Task 1).

Deckt ab: Zero-Config-Provider-Auflösung (`resolve_smtp_config`), Versand-Ablauf
(`deliver`, STARTTLS/SSL) und Fehler-Übersetzung (`describe_smtp_error`). `smtplib.SMTP`
und `smtplib.SMTP_SSL` werden IMMER gemockt — nie eine echte Verbindung im Test.
"""
from __future__ import annotations

import smtplib

import pytest


# --- resolve_smtp_config -----------------------------------------------------


def test_resolve_smtp_config_gmx(tmp_path, monkeypatch):
    import src.mail_send as mail_send

    cfg = mail_send.resolve_smtp_config({"IMAP_USER": "info@gmx.de"})

    assert cfg == {"host": "mail.gmx.net", "port": 587, "security": "starttls", "user": "info@gmx.de"}


def test_resolve_smtp_config_gmail():
    import src.mail_send as mail_send

    cfg = mail_send.resolve_smtp_config({"IMAP_USER": "x@gmail.com"})

    assert cfg["host"] == "smtp.gmail.com"
    assert cfg["port"] == 587
    assert cfg["security"] == "starttls"


def test_resolve_smtp_config_outlook():
    import src.mail_send as mail_send

    cfg = mail_send.resolve_smtp_config({"IMAP_USER": "x@outlook.com"})

    assert cfg["host"] == "smtp.office365.com"
    assert cfg["port"] == 587
    assert cfg["security"] == "starttls"


def test_resolve_smtp_config_t_online():
    import src.mail_send as mail_send

    cfg = mail_send.resolve_smtp_config({"IMAP_USER": "x@t-online.de"})

    assert cfg["host"] == "securesmtp.t-online.de"
    assert cfg["port"] == 587
    assert cfg["security"] == "starttls"


def test_resolve_smtp_config_override_always_wins_even_for_known_domain():
    import src.mail_send as mail_send

    cfg = mail_send.resolve_smtp_config(
        {
            "IMAP_USER": "info@gmx.de",
            "SMTP_HOST": "mail.example.org",
            "SMTP_PORT": "465",
            "SMTP_SECURITY": "ssl",
        }
    )

    assert cfg == {"host": "mail.example.org", "port": 465, "security": "ssl", "user": "info@gmx.de"}


def test_resolve_smtp_config_unknown_domain_imap_host_override_heuristic():
    import src.mail_send as mail_send

    cfg = mail_send.resolve_smtp_config(
        {"IMAP_USER": "x@eigenerserver.de", "IMAP_HOST": "imap.eigenerserver.de"}
    )

    assert cfg["host"] == "smtp.eigenerserver.de"
    assert cfg["port"] == 587
    assert cfg["security"] == "starttls"


def test_resolve_smtp_config_unknown_domain_without_override_raises_runtime_error(mocker):
    import src.mail_send as mail_send

    mocker.patch(
        "src.mail_send.resolve_imap_config",
        side_effect=RuntimeError("Kann IMAP-Config nicht auto-detektieren."),
    )

    with pytest.raises(RuntimeError) as excinfo:
        mail_send.resolve_smtp_config({"IMAP_USER": "x@unbekannt-domain.test"})

    assert "SMTP_HOST" in str(excinfo.value)


def test_resolve_smtp_config_invalid_security_raises_runtime_error():
    import src.mail_send as mail_send

    with pytest.raises(RuntimeError):
        mail_send.resolve_smtp_config(
            {"IMAP_USER": "info@gmx.de", "SMTP_HOST": "mail.example.org", "SMTP_SECURITY": "plain"}
        )


# --- deliver ------------------------------------------------------------------


def _cfg(security="starttls"):
    return {"host": "smtp.example.de", "port": 587 if security == "starttls" else 465, "security": security, "user": "info@example.de"}


def test_deliver_starttls_calls_in_order(mocker):
    import src.mail_send as mail_send

    mock_smtp_cls = mocker.patch("src.mail_send.smtplib.SMTP")
    mock_server = mock_smtp_cls.return_value
    manager = mocker.Mock()
    manager.attach_mock(mock_server.starttls, "starttls")
    manager.attach_mock(mock_server.login, "login")
    manager.attach_mock(mock_server.sendmail, "sendmail")

    mail_send.deliver(_cfg("starttls"), "pw", "info@example.de", ["a@x.de"], b"RAW")

    mock_smtp_cls.assert_called_once_with("smtp.example.de", 587, timeout=mail_send._SMTP_TIMEOUT_SECONDS)
    assert [c[0] for c in manager.mock_calls] == ["starttls", "login", "sendmail"]
    mock_server.login.assert_called_once_with("info@example.de", "pw")
    mock_server.sendmail.assert_called_once_with("info@example.de", ["a@x.de"], b"RAW")
    mock_server.quit.assert_called_once()


def test_deliver_ssl_uses_smtp_ssl_and_skips_starttls(mocker):
    import src.mail_send as mail_send

    mock_smtp_ssl_cls = mocker.patch("src.mail_send.smtplib.SMTP_SSL")
    mock_smtp_cls = mocker.patch("src.mail_send.smtplib.SMTP")
    mock_server = mock_smtp_ssl_cls.return_value

    mail_send.deliver(_cfg("ssl"), "pw", "info@example.de", ["a@x.de"], b"RAW")

    mock_smtp_ssl_cls.assert_called_once_with("smtp.example.de", 465, timeout=mail_send._SMTP_TIMEOUT_SECONDS)
    mock_smtp_cls.assert_not_called()
    mock_server.starttls.assert_not_called()
    mock_server.login.assert_called_once_with("info@example.de", "pw")
    mock_server.sendmail.assert_called_once()


def test_deliver_empty_recipients_raises_without_connection(mocker):
    import src.mail_send as mail_send

    mock_smtp_cls = mocker.patch("src.mail_send.smtplib.SMTP")

    with pytest.raises(mail_send.SmtpSendError):
        mail_send.deliver(_cfg("starttls"), "pw", "info@example.de", [], b"RAW")

    mock_smtp_cls.assert_not_called()


# --- describe_smtp_error / Fehler-Übersetzung ---------------------------------


def test_describe_smtp_error_authentication():
    import src.mail_send as mail_send

    msg = mail_send.describe_smtp_error(smtplib.SMTPAuthenticationError(535, b"hunter2"))
    assert "Anmeldung" in msg or "Zugangsdaten" in msg


def test_describe_smtp_error_recipients_refused():
    import src.mail_send as mail_send

    msg = mail_send.describe_smtp_error(smtplib.SMTPRecipientsRefused({"a@x.de": (550, b"no")}))
    assert "Empfänger" in msg


def test_describe_smtp_error_sender_refused():
    import src.mail_send as mail_send

    msg = mail_send.describe_smtp_error(smtplib.SMTPSenderRefused(550, b"no", "a@x.de"))
    assert "Absender" in msg


def test_describe_smtp_error_connection_failure():
    import src.mail_send as mail_send

    msg1 = mail_send.describe_smtp_error(smtplib.SMTPConnectError(421, b"nope"))
    msg2 = mail_send.describe_smtp_error(OSError("connection refused"))
    assert "Verbindung" in msg1
    assert "Verbindung" in msg2


def test_describe_smtp_error_unknown_exception_generic_no_crash():
    import src.mail_send as mail_send

    msg = mail_send.describe_smtp_error(ValueError("irgendwas"))
    assert msg


def test_describe_smtp_error_never_leaks_password_marker():
    import src.mail_send as mail_send

    exceptions = [
        smtplib.SMTPAuthenticationError(535, b"hunter2"),
        smtplib.SMTPRecipientsRefused({"a@x.de": (550, b"hunter2")}),
        smtplib.SMTPSenderRefused(550, b"hunter2", "a@x.de"),
        smtplib.SMTPConnectError(421, b"hunter2"),
        OSError("hunter2"),
        ValueError("hunter2"),
    ]
    for exc in exceptions:
        assert "hunter2" not in mail_send.describe_smtp_error(exc)


def test_deliver_smtp_failure_translated_and_no_password_leak(mocker):
    import src.mail_send as mail_send

    mock_smtp_cls = mocker.patch("src.mail_send.smtplib.SMTP")
    mock_server = mock_smtp_cls.return_value
    mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"hunter2-secret")

    with pytest.raises(mail_send.SmtpSendError) as excinfo:
        mail_send.deliver(_cfg("starttls"), "hunter2-secret", "info@example.de", ["a@x.de"], b"RAW")

    assert "hunter2-secret" not in str(excinfo.value)
