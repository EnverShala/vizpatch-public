"""Tests für die Ordner-Mechanik (Quick 260812-hhm, Task 1): Sanitizer,
Delimiter-/Namespace-Auflösung und `ordner_erstellen`.

Deckt ab:
1. `_sanitize_folder_name`: gültige Namen unverändert, injektionsfähige/zu
   lange Namen -> `ValueError` (D-hhm-04).
2. `_folder_delimiter`/`_folder_namespace_prefix`: Server-Delimiter statt
   Hartkodierung, INBOX-Namespace-Erkennung (D-hhm-02).
3. `ordner_erstellen`: CREATE mit Delimiter/Namespace, Idempotenz, Race-Fall,
   SUBSCRIBE best effort, Fehlerpfade, Registry-Vollständigkeit.
"""
from __future__ import annotations

import types

import pytest

from .test_chat_tools import _fake_mailbox, _setup_env, _write_agent_env


def _folder_info(name, delim=".", flags=()):
    """Wie `test_chat_tools._fake_folder_info`, aber MIT `delim`-Attribut —
    die Bestandsfixture hat keins (Live-Server-Kontrakt: `FolderInfo.delim`
    existiert dort, wird hier für die Delimiter-Tests nachgebildet)."""
    return types.SimpleNamespace(name=name, delim=delim, flags=flags)


# --- _sanitize_folder_name ---------------------------------------------------


def test_sanitize_folder_name_accepts_valid_segment():
    import src.chat_tools as chat_tools

    assert chat_tools._sanitize_folder_name("Rechnungen") == "Rechnungen"
    assert chat_tools._sanitize_folder_name("  Kunden 2026  ") == "Kunden 2026"


@pytest.mark.parametrize(
    "bad_segment",
    ["a\r\nb", 'a"b', "a\\b", "a\x01b", "x" * 201, "", "   "],
)
def test_sanitize_folder_name_rejects_forbidden_or_invalid(bad_segment):
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools._sanitize_folder_name(bad_segment)


# --- _folder_delimiter / _folder_namespace_prefix ----------------------------


def test_folder_delimiter_returns_first_nonempty_delim():
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Drafts", delim=".")]
    assert chat_tools._folder_delimiter(infos) == "."


def test_folder_delimiter_ignores_nil_and_falls_back_to_slash():
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX", delim="NIL"), types.SimpleNamespace(name="Drafts", flags=())]
    assert chat_tools._folder_delimiter(infos) == "/"


def test_folder_delimiter_empty_list_falls_back_to_slash():
    import src.chat_tools as chat_tools

    assert chat_tools._folder_delimiter([]) == "/"


def test_folder_namespace_prefix_detects_inbox_namespace():
    import src.chat_tools as chat_tools

    infos = [
        _folder_info("INBOX", delim="."),
        _folder_info("INBOX.Drafts", delim="."),
        _folder_info("INBOX.Sent", delim="."),
    ]
    assert chat_tools._folder_namespace_prefix(infos, ".") == "INBOX."


def test_folder_namespace_prefix_empty_without_convention():
    import src.chat_tools as chat_tools

    infos = [
        _folder_info("INBOX", delim="/"),
        _folder_info("[Gmail]/Drafts", delim="/"),
        _folder_info("Archiv", delim="/"),
    ]
    assert chat_tools._folder_namespace_prefix(infos, "/") == ""


def test_folder_namespace_prefix_empty_folder_list():
    import src.chat_tools as chat_tools

    assert chat_tools._folder_namespace_prefix([], ".") == ""


# --- ordner_erstellen ---------------------------------------------------------


def _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, folder_infos, exists_return=True):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.return_value = folder_infos
    mock_mailbox.folder.exists.return_value = exists_return
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    return mock_mailbox


def test_ordner_erstellen_creates_with_server_delimiter_and_namespace(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Drafts", delim=".")]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert result == {"ordner": "INBOX.Rechnungen", "erstellt": True, "hinweis": None}
    mock_mailbox.folder.create.assert_called_once_with("INBOX.Rechnungen")
    mock_mailbox.folder.subscribe.assert_called_once_with("INBOX.Rechnungen", True)


def test_ordner_erstellen_without_namespace_convention_uses_plain_name(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="/"), _folder_info("[Gmail]/Drafts", delim="/")]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert result == {"ordner": "Rechnungen", "erstellt": True, "hinweis": None}
    mock_mailbox.folder.create.assert_called_once_with("Rechnungen")


def test_ordner_erstellen_eltern_ordner_not_double_prefixed(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX", delim="."),
        _folder_info("INBOX.Kunden", delim="."),
    ]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen", eltern_ordner="INBOX.Kunden")

    assert result["ordner"] == "INBOX.Kunden.Rechnungen"
    assert result["erstellt"] is True
    mock_mailbox.folder.create.assert_called_once_with("INBOX.Kunden.Rechnungen")


def test_ordner_erstellen_eltern_ordner_without_prefix_gets_prefixed(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX", delim="."),
        _folder_info("INBOX.Kunden", delim="."),
    ]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen", eltern_ordner="Kunden")

    assert result["ordner"] == "INBOX.Kunden.Rechnungen"


def test_ordner_erstellen_name_with_delimiter_creates_each_missing_level(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Drafts", delim=".")]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Kunden.2026")

    assert result["ordner"] == "INBOX.Kunden.2026"
    assert result["erstellt"] is True
    assert mock_mailbox.folder.create.call_args_list == [
        mocker.call("INBOX.Kunden"),
        mocker.call("INBOX.Kunden.2026"),
    ]


def test_ordner_erstellen_idempotent_when_already_exists(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX", delim="."),
        _folder_info("INBOX.Rechnungen", delim="."),
    ]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert result["erstellt"] is False
    assert "fehler" not in result
    assert "existiert bereits" in result["hinweis"]
    mock_mailbox.folder.create.assert_not_called()


def test_ordner_erstellen_race_on_create_already_exists_is_success(mocker, tmp_path, monkeypatch):
    # Bewusst nur EIN Ordner in der Liste (keine INBOX-Namespace-Konvention
    # erkennbar) -- der volle Name bleibt hier schlicht "Rechnungen"; die
    # Namespace-Auflösung selbst ist bereits in eigenen Tests abgedeckt.
    infos = [_folder_info("INBOX", delim=".")]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos, exists_return=True)
    mock_mailbox.folder.create.side_effect = RuntimeError("Mailbox already exists")

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert result["erstellt"] is False
    assert "fehler" not in result
    assert result["ordner"] == "Rechnungen"
    mock_mailbox.folder.subscribe.assert_not_called()


def test_ordner_erstellen_subscribe_failure_still_success(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim=".")]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos)
    mock_mailbox.folder.subscribe.side_effect = RuntimeError("no SUBSCRIBE support")

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert result == {"ordner": "Rechnungen", "erstellt": True, "hinweis": None}


def test_ordner_erstellen_not_confirmed_after_create_returns_error(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim=".")]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos, exists_return=False)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert "fehler" in result


def test_ordner_erstellen_rejects_injection_attempt(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim=".")]
    mock_mailbox = _setup_ordner_erstellen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="a\r\nb")

    assert "fehler" in result
    mock_mailbox.folder.create.assert_not_called()


def test_ordner_erstellen_list_failed_returns_error_dict(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.side_effect = RuntimeError("list failed")
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert "fehler" in result


def test_ordner_erstellen_imap_connect_failure_returns_error_dict(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mock_mailbox = _fake_mailbox(login_raises=True)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert "fehler" in result


def test_ordner_erstellen_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.ordner_erstellen("../evil", name="Rechnungen")


# --- Registry -----------------------------------------------------------------


def test_ordner_erstellen_registered_everywhere():
    import src.chat_tools as chat_tools

    assert "ordner_erstellen" in chat_tools.TOOL_HANDLERS
    assert chat_tools.TOOL_HANDLERS["ordner_erstellen"] is chat_tools.ordner_erstellen
    schema_names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "ordner_erstellen" in schema_names
    assert chat_tools._TOOL_ACTIVITY_LABELS.get("ordner_erstellen") == "Ordner anlegen…"
    assert "ordner_erstellen" not in chat_tools._ANON_AWARE_TOOLS
    assert "ordner_erstellen" not in chat_tools._SESSION_SCOPED_TOOLS
    assert "ordner_erstellen" not in chat_tools._REQUEST_SCOPED_TOOLS
