"""Tests fuer `mail_abmeldeweg_lesen`/`mail_abmelden`/`mail_abmelden_bestaetigen`
(Phase 13, Plan 13-09, MBOX-15).

Zwei Teile mit sehr unterschiedlichem Risikoprofil (13-09-PLAN.md):

- `mail_abmeldeweg_lesen` (Task 1) liest NUR den `List-Unsubscribe`-Header
  aus einem ohnehin passierenden Fetch und NENNT den Weg — KEIN Flag, KEIN
  Gate.
- `mail_abmelden`/`mail_abmelden_bestaetigen` (Task 2) LOESEN eine Abmeldung
  tatsaechlich AUS — dasselbe Zwei-Request-Freigabe-Gate wie `mail_senden`/
  `mail_weiterleiten` PLUS ein eigenes Abschalt-Flag `ENABLE_UNSUBSCRIBE`.

WICHTIG (13-RESEARCH.md Abschnitt 5): die Header-Struktur ist dort
ausdruecklich als ASSUMED markiert — NICHT gegen die RFC-Quelle oder eine
echte Newsletter-Mail in dieser Session verifiziert. Diese Tests decken
deshalb mehrere KONSTRUIERTE Header-Varianten ab (nur mailto, nur https,
beide, mit/ohne List-Unsubscribe-Post, gefaltete Kopfzeile, kaputte
Kopfzeile) — die Bestaetigung an einer echten Newsletter-Mail bleibt dem
Betreiber vorbehalten (siehe SUMMARY).

`_setup_env`/`_write_agent_env`/`_fake_mailbox`/`_msg` sind lokale Kopien der
etablierten Helfer (Muster `test_chat_weiterleiten.py`/`test_chat_send.py`)."""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import httpx
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


def _msg(
    subject="Unser Newsletter",
    text="Hallo, hier ist der Newsletter.",
    uid="42",
    from_="newsletter@versender.example",
    to=("info@ionos.de",),
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
    m.attachments = []
    return m


def _mock_public_dns(mocker):
    """`versender.example` (RFC 2606 reservierte Test-Domain) loest real NICHT
    auf — Tests, die einen erfolgreichen https-SSRF-Check erwarten, mocken die
    Namensauflösung auf eine oeffentliche IP, statt echtes DNS zu brauchen."""
    return mocker.patch(
        "src.chat_tools.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    )


def _fake_mailbox(messages=None, login_raises=False):
    mailbox = MagicMock()
    mailbox.__enter__ = MagicMock(return_value=mailbox)
    mailbox.__exit__ = MagicMock(return_value=False)
    if login_raises:
        mailbox.login.side_effect = RuntimeError("auth failed")
    msgs = list(messages or [])
    mailbox.fetch.side_effect = lambda *a, **kw: list(msgs)
    mailbox.folder.list.return_value = []
    return mailbox


# --- _parse_list_unsubscribe_header: konstruierte Header-Varianten ----------


def test_parse_header_nur_mailto():
    import src.chat_tools as chat_tools

    raw = "<mailto:unsubscribe@versender.example?subject=unsubscribe>"
    result = chat_tools._parse_list_unsubscribe_header(raw)
    assert result["mailto"] == "mailto:unsubscribe@versender.example?subject=unsubscribe"
    assert result["https"] == ""


def test_parse_header_nur_https():
    import src.chat_tools as chat_tools

    raw = "<https://versender.example/unsub?id=123>"
    result = chat_tools._parse_list_unsubscribe_header(raw)
    assert result["https"] == "https://versender.example/unsub?id=123"
    assert result["mailto"] == ""


def test_parse_header_beide_varianten():
    import src.chat_tools as chat_tools

    raw = "<mailto:unsubscribe@versender.example>, <https://versender.example/unsub?id=123>"
    result = chat_tools._parse_list_unsubscribe_header(raw)
    assert result["mailto"] == "mailto:unsubscribe@versender.example"
    assert result["https"] == "https://versender.example/unsub?id=123"


def test_parse_header_ignoriert_http_ohne_s():
    import src.chat_tools as chat_tools

    raw = "<http://versender.example/unsub>"
    result = chat_tools._parse_list_unsubscribe_header(raw)
    assert result["https"] == ""
    assert result["mailto"] == ""


def test_parse_header_mit_leerzeichen_und_zeilenumbruechen():
    """Gefaltete Kopfzeile (RFC-5322-Fortsetzung: eingebetteter Zeilenumbruch
    + Leerraum) darf nicht dazu fuehren, dass die URI nicht mehr erkannt wird."""
    import src.chat_tools as chat_tools

    raw = "<mailto:unsubscribe@versender.example>,\r\n <https://versender.example/unsub?id=123>"
    result = chat_tools._parse_list_unsubscribe_header(raw)
    assert result["mailto"] == "mailto:unsubscribe@versender.example"
    assert result["https"] == "https://versender.example/unsub?id=123"


def test_parse_header_kaputte_kopfzeile_kein_absturz():
    import src.chat_tools as chat_tools

    for kaputt in ("", "garbage ohne spitze klammern", "<mailto:unvollstaendig", "<>", None):
        result = chat_tools._parse_list_unsubscribe_header(kaputt)
        assert result == {"mailto": "", "https": ""}


def test_one_click_marker_erkannt_gross_klein_und_leerzeichen_tolerant():
    import src.chat_tools as chat_tools

    assert chat_tools._list_unsubscribe_is_one_click("List-Unsubscribe=One-Click") is True
    assert chat_tools._list_unsubscribe_is_one_click("  list-unsubscribe = one-click  ") is True


def test_one_click_marker_fehlt():
    import src.chat_tools as chat_tools

    assert chat_tools._list_unsubscribe_is_one_click("") is False
    assert chat_tools._list_unsubscribe_is_one_click("etwas anderes") is False


# --- mail_abmeldeweg_lesen: reines Auslesen, KEIN Flag, KEIN Gate -----------


def test_mail_abmeldeweg_lesen_findet_mailto_und_https_mit_one_click(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg(
        headers={
            "list-unsubscribe": (
                "<mailto:unsubscribe@versender.example>, "
                "<https://versender.example/unsub?id=123>"
            ),
            "list-unsubscribe-post": "List-Unsubscribe=One-Click",
        }
    )
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_abmeldeweg_lesen("info", uid="42")

    assert result["gefunden"] is True
    assert result["mailto"] == "mailto:unsubscribe@versender.example"
    assert result["https"] == "https://versender.example/unsub?id=123"
    assert result["one_click"] is True


def test_mail_abmeldeweg_lesen_fehlender_header_ist_kein_fehler(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg(headers={})
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_abmeldeweg_lesen("info", uid="42")

    assert "fehler" not in result
    assert result["gefunden"] is False


def test_mail_abmeldeweg_lesen_kaputter_header_kein_absturz(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg(headers={"list-unsubscribe": "kaputt ohne spitze klammern"})
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    result = chat_tools.mail_abmeldeweg_lesen("info", uid="42")

    assert "fehler" not in result
    assert result["gefunden"] is False


def test_mail_abmeldeweg_lesen_source_fetch_uses_mark_seen_false(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    original = _msg()
    mock_mailbox = _fake_mailbox([original])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    chat_tools.mail_abmeldeweg_lesen("info", uid="42")

    for call in mock_mailbox.fetch.call_args_list:
        assert call.kwargs.get("mark_seen") is False


def test_mail_abmeldeweg_lesen_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.mail_abmeldeweg_lesen("../evil", uid="42")


def test_mail_abmeldeweg_lesen_haengt_an_keinem_flag_und_keinem_gate(tmp_path, monkeypatch):
    """13-CONTEXT.md: Header lesen braucht kein Flag und kein Gate — belegt
    daran, dass das Werkzeug auch bei ENABLE_MAIL_SEND=false im Schema
    bleibt und keiner Gate-Registry angehoert."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_env("info", {"ENABLE_MAIL_SEND": "false"})

    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert "mail_abmeldeweg_lesen" in names
    assert "mail_abmeldeweg_lesen" not in chat_tools._SESSION_SCOPED_TOOLS
    assert "mail_abmeldeweg_lesen" not in chat_tools._REQUEST_SCOPED_TOOLS
    assert "mail_abmeldeweg_lesen" not in chat_tools._MAIL_SEND_TOOL_NAMES


# --- Registrierung -----------------------------------------------------------


def test_mail_abmeldeweg_lesen_registered_in_handlers_and_schemas():
    import src.chat_tools as chat_tools

    schema_names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "mail_abmeldeweg_lesen" in chat_tools.TOOL_HANDLERS
    assert "mail_abmeldeweg_lesen" in schema_names


def test_mail_abmeldeweg_lesen_hat_aktivitaets_label():
    import src.chat_tools as chat_tools

    assert "…" in chat_tools._tool_activity_label("mail_abmeldeweg_lesen")
