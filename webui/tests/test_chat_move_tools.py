"""Tests für die generalisierte Move-Mechanik (Quick 260812-hhm, Task 2):
`_move_message` (extrahiert aus `_move_to_trash`, D-hhm-03) + die zwei neuen
Werkzeuge `mail_verschieben`/`mails_verschieben`.

Die volle CR-05-Härtung (Message-ID-Residual-Suche, Ankunfts-Nachweis,
gezieltes UID EXPUNGE, kein folder-weites EXPUNGE) bleibt über die
Bestandstests von `_move_to_trash` in `test_chat_tools.py` vollständig
abgedeckt (unverändertes Verhalten nach der Extraktion) — hier werden nur die
NEUEN Aspekte getestet: `_move_message` mit einem generischen Zielordner,
sowie die beiden Werkzeuge selbst (Ordner-Auflösung, Fehlgeschlagen-Sammlung,
Batch-Pfad, kein Bestätigungs-Gate).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from .test_chat_folder_tools import _folder_info
from .test_chat_tools import _fake_mailbox, _msg, _msg_with_mid, _setup_env, _write_agent_env


# --- _move_message (generalisierte Move-Mechanik) ---------------------------


def test_move_message_moves_and_verifies_source():
    """Phase 13, Plan 13-02: `_move_message` liefert seit dieser Änderung ein
    Tupel `(target_folder, message_id)` statt nur des Zielordners — Vorarbeit
    für das Aktions-Journal (MBOX-02, Plan 13-03)."""
    import src.chat_tools as chat_tools

    mailbox = _fake_mailbox([_msg_with_mid(uid="42")])
    mailbox.client.capabilities = ("MOVE",)

    result = chat_tools._move_message(mailbox, "42", "INBOX", "INBOX.Rechnungen")

    assert result == ("INBOX.Rechnungen", "<orig-42@example.com>")
    mailbox.move.assert_called_once_with(["42"], "INBOX.Rechnungen")


def test_move_message_raises_without_arrival_proof_in_target():
    """Bleibt die Quelle stehen und ist die Ankunft im ZIEL nicht per
    Message-ID nachweisbar -> `MailboxMoveError`, kein hartes Löschen der
    womöglich einzigen Kopie."""
    import src.chat_tools as chat_tools

    mailbox = MagicMock()
    mailbox.client.capabilities = ("MOVE", "UIDPLUS")
    current_folder = {"name": None}
    mailbox.folder.set.side_effect = lambda name, *a, **kw: current_folder.__setitem__("name", name)

    def _fetch_side_effect(*_args, **_kwargs):
        if current_folder["name"] == "Kunden":
            return []
        return [_msg_with_mid()]

    mailbox.fetch.side_effect = _fetch_side_effect

    with pytest.raises(chat_tools.MailboxMoveError):
        chat_tools._move_message(mailbox, "42", "INBOX", "Kunden")

    mailbox.flag.assert_not_called()
    mailbox.client.uid.assert_not_called()
    mailbox.delete.assert_not_called()
    mailbox.expunge.assert_not_called()


def test_move_message_raises_without_uidplus_capability():
    """Bleibt die Quelle stehen, Ankunft nachgewiesen, aber kein UIDPLUS ->
    `MailboxMoveError` statt folder-weitem EXPUNGE."""
    import src.chat_tools as chat_tools

    mailbox = MagicMock()
    mailbox.client.capabilities = ("MOVE",)  # kein UIDPLUS
    mailbox.fetch.return_value = [_msg_with_mid()]

    with pytest.raises(chat_tools.MailboxMoveError):
        chat_tools._move_message(mailbox, "42", "INBOX", "Kunden")

    mailbox.flag.assert_not_called()
    mailbox.client.uid.assert_not_called()
    mailbox.delete.assert_not_called()
    mailbox.expunge.assert_not_called()


def test_move_message_rejects_uid_range_defense_in_depth():
    import src.chat_tools as chat_tools

    mailbox = MagicMock()
    with pytest.raises(chat_tools.MailboxMoveError):
        chat_tools._move_message(mailbox, "1:*", "INBOX", "Kunden")

    mailbox.move.assert_not_called()


# --- mail_verschieben ---------------------------------------------------------


def _setup_move_env(mocker, tmp_path, monkeypatch, folder_infos, messages, capabilities=("MOVE",)):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mock_mailbox = _fake_mailbox(messages)
    mock_mailbox.folder.list.return_value = folder_infos
    mock_mailbox.client.capabilities = capabilities
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    return mock_mailbox


def test_mail_verschieben_resolves_target_against_real_folder_list(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="42")])

    import src.chat_tools as chat_tools

    result = chat_tools.mail_verschieben("info", uid="42", ziel_ordner="Rechnungen")

    assert result == {
        "verschoben": True,
        "uid": "42",
        "quell_ordner": "INBOX",
        "ziel_ordner": "INBOX.Rechnungen",
        "message_id": "",
    }
    mock_mailbox.move.assert_called_once_with(["42"], "INBOX.Rechnungen")


def test_mail_verschieben_target_missing_returns_error_mentioning_ordner_erstellen(
    mocker, tmp_path, monkeypatch
):
    infos = [_folder_info("INBOX", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="42")])

    import src.chat_tools as chat_tools

    result = chat_tools.mail_verschieben("info", uid="42", ziel_ordner="Rechnungen")

    assert "fehler" in result
    assert "ordner_erstellen" in result["fehler"]
    mock_mailbox.move.assert_not_called()


def test_mail_verschieben_target_equals_source_returns_error(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="42")])

    import src.chat_tools as chat_tools

    result = chat_tools.mail_verschieben("info", uid="42", ziel_ordner="INBOX", quell_ordner="INBOX")

    assert "fehler" in result
    mock_mailbox.move.assert_not_called()


@pytest.mark.parametrize("bad_uid", ["1:*", "1,2,3", ""])
def test_mail_verschieben_rejects_invalid_uid(bad_uid):
    import src.chat_tools as chat_tools

    result = chat_tools.mail_verschieben("info", uid=bad_uid, ziel_ordner="Rechnungen")

    assert "fehler" in result


def test_mail_verschieben_rejects_injection_in_ziel_ordner(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="42")])

    import src.chat_tools as chat_tools

    result = chat_tools.mail_verschieben("info", uid="42", ziel_ordner="a\r\nb")

    assert "fehler" in result
    mock_mailbox.move.assert_not_called()


def test_mail_verschieben_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.mail_verschieben("../evil", uid="42", ziel_ordner="Rechnungen")


def test_mail_verschieben_ignores_trash_confirmation_flag(mocker, tmp_path, monkeypatch):
    """Kein Bestätigungs-Gate: bei `ENABLE_TRASH_CONFIRMATION=true` (Default)
    funktioniert das Werkzeug unverändert und liefert KEIN
    `bestaetigung_erforderlich`."""
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="1")])
    monkeypatch.setenv("ENABLE_TRASH_CONFIRMATION", "true")

    import src.chat_tools as chat_tools

    result = chat_tools.mail_verschieben("info", uid="1", ziel_ordner="Rechnungen")

    assert "bestaetigung_erforderlich" not in result
    assert result.get("verschoben") is True


# --- mails_verschieben ---------------------------------------------------------


def test_mails_verschieben_accepts_scalar_uid_and_moves(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="42")])

    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids="42", ziel_ordner="Rechnungen")

    assert result["verschoben"] == 1
    assert result["fehlgeschlagen"] == []


def test_mails_verschieben_batch_single_move_command_for_all_uids(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(
        mocker, tmp_path, monkeypatch, infos, [_msg(uid="1"), _msg(uid="2"), _msg(uid="3")]
    )

    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids=["1", "2", "3"], ziel_ordner="Rechnungen")

    assert result == {
        "verschoben": 3,
        "ziel_ordner": "INBOX.Rechnungen",
        "quell_ordner": "INBOX",
        "fehlgeschlagen": [],
        "eintraege": [
            {"uid": "1", "message_id": ""},
            {"uid": "2", "message_id": ""},
            {"uid": "3", "message_id": ""},
        ],
    }
    assert mock_mailbox.move.call_count == 1
    args, kwargs = mock_mailbox.move.call_args
    assert list(args[0]) == ["1", "2", "3"]
    assert args[1] == "INBOX.Rechnungen"
    assert kwargs.get("chunks") == chat_tools._BULK_MOVE_CHUNK_SIZE


def test_mails_verschieben_batch_reports_missing_uid_in_fehlgeschlagen(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(
        mocker, tmp_path, monkeypatch, infos, [_msg(uid="1"), _msg(uid="2")]
    )

    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids=["1", "2", "999"], ziel_ordner="Rechnungen")

    assert result["verschoben"] == 2
    assert len(result["fehlgeschlagen"]) == 1
    assert result["fehlgeschlagen"][0]["uid"] == "999"
    assert "nicht gefunden" in result["fehlgeschlagen"][0]["fehler"]


def test_mails_verschieben_invalid_uids_skip_imap_contact(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="1")])

    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids=["1", "1:*", ""], ziel_ordner="Rechnungen")

    assert result["verschoben"] == 1
    failed_uids = {f["uid"] for f in result["fehlgeschlagen"]}
    assert "1:*" in failed_uids
    assert "" in failed_uids


def test_mails_verschieben_duplicate_uids_deduped_order_preserved(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(
        mocker, tmp_path, monkeypatch, infos, [_msg(uid="1"), _msg(uid="2")]
    )

    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids=["2", "1", "2", "1"], ziel_ordner="Rechnungen")

    assert result["verschoben"] == 2
    args, _ = mock_mailbox.move.call_args
    assert list(args[0]) == ["2", "1"]


def test_mails_verschieben_too_many_uids_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    too_many = [str(i) for i in range(chat_tools.MAX_BULK_TRASH_UIDS + 1)]
    result = chat_tools.mails_verschieben("info", uids=too_many, ziel_ordner="Rechnungen")

    assert "fehler" in result


def test_mails_verschieben_rejects_non_list_uids(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids=123, ziel_ordner="Rechnungen")

    assert "fehler" in result


def test_mails_verschieben_no_move_capability_uses_single_path(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(
        mocker, tmp_path, monkeypatch, infos, [_msg(uid="1"), _msg(uid="2")], capabilities=()
    )

    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids=["1", "2"], ziel_ordner="Rechnungen")

    assert result["verschoben"] == 2
    assert mock_mailbox.move.call_count == 2


def test_mails_verschieben_target_missing_returns_error_before_any_move(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="1")])

    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids=["1"], ziel_ordner="Rechnungen")

    assert "fehler" in result
    assert "ordner_erstellen" in result["fehler"]
    mock_mailbox.move.assert_not_called()


def test_mails_verschieben_ignores_trash_confirmation_flag(mocker, tmp_path, monkeypatch):
    """Kein Bestätigungs-Gate: bei `ENABLE_TRASH_CONFIRMATION=true` (Default)
    funktioniert das Werkzeug unverändert und liefert KEIN
    `bestaetigung_erforderlich`."""
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg(uid="1")])
    monkeypatch.setenv("ENABLE_TRASH_CONFIRMATION", "true")

    import src.chat_tools as chat_tools

    result = chat_tools.mails_verschieben("info", uids=["1"], ziel_ordner="Rechnungen")

    assert "bestaetigung_erforderlich" not in result


def test_mails_verschieben_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.mails_verschieben("../evil", uids=["1"], ziel_ordner="Rechnungen")


# --- Registry -----------------------------------------------------------------


def test_move_tools_registered_everywhere():
    import src.chat_tools as chat_tools

    for name in ("mail_verschieben", "mails_verschieben"):
        assert name in chat_tools.TOOL_HANDLERS
        schema_names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
        assert name in schema_names
        assert name in chat_tools._TOOL_ACTIVITY_LABELS
        assert name not in chat_tools._ANON_AWARE_TOOLS
        assert name not in chat_tools._SESSION_SCOPED_TOOLS
        assert name not in chat_tools._REQUEST_SCOPED_TOOLS
