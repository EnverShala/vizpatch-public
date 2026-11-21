from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from src.provider_config import InvalidDraftsFolder
from src.imap_client import ImapClient


def _make_client_with_mock_mailbox(config):
    """ImapClient mit vorinstallierter _mailbox, ohne echten IMAP-Login (Muster aus
    test_imap_client_auto_create.py)."""
    client = ImapClient.__new__(ImapClient)
    client.config = config
    client.logger = MagicMock()
    client._mailbox = MagicMock()
    return client


# Roher RFC-5322-Draft mit eigener Message-ID — die Nachkontrolle muss GENAU diese
# ID suchen, niemals eine vom Aufrufer stammende (Review 260731-s49).
_DRAFT_BYTES = (
    b"Message-ID: <draft-123@vizpatch.test>\r\n"
    b"In-Reply-To: <original-999@kunde.test>\r\n"
    b"Subject: Re: Test\r\n"
    b"\r\n"
    b"Hallo\r\n"
)


@pytest.mark.parametrize("bad_folder", ["INBOX", "", "   "])
def test_append_to_drafts_rejects_invalid_folder(mock_config, bad_folder):
    cfg = replace(mock_config, imap_drafts_folder=bad_folder)
    client = _make_client_with_mock_mailbox(cfg)

    with pytest.raises(InvalidDraftsFolder):
        client.append_to_drafts(b"raw-email-bytes")

    assert not client._mailbox.append.called
    assert not client._mailbox.folder.create.called


def test_append_to_drafts_valid_folder_calls_append_with_that_folder(mock_config):
    cfg = replace(mock_config, imap_drafts_folder="Entwürfe")
    client = _make_client_with_mock_mailbox(cfg)

    client.append_to_drafts(b"raw-email-bytes")

    assert client._mailbox.append.called
    _, kwargs = client._mailbox.append.call_args
    assert kwargs["folder"] == "Entwürfe"


def test_append_to_drafts_auto_create_never_reached_for_invalid_folder(mock_config):
    cfg = replace(mock_config, imap_drafts_folder="")
    client = _make_client_with_mock_mailbox(cfg)

    with pytest.raises(InvalidDraftsFolder):
        client.append_to_drafts(b"raw-email-bytes")

    assert not client._mailbox.folder.create.called


def test_append_to_drafts_verification_success_logs_info(mock_config):
    cfg = replace(mock_config, imap_drafts_folder="Entwürfe")
    client = _make_client_with_mock_mailbox(cfg)
    client._mailbox.fetch.return_value = iter([MagicMock()])

    client.append_to_drafts(_DRAFT_BYTES, verify=True)

    client.logger.info.assert_any_call(
        "draft_append_verified",
        extra={"folder": "Entwürfe", "message_id": "<draft-123@vizpatch.test>"},
    )
    assert not client.logger.error.called


def test_append_to_drafts_verification_uses_draft_message_id_not_original(mock_config):
    """Regression 260731-s49: die Nachkontrolle muss die Message-ID des DRAFTS aus den
    Bytes extrahieren — nicht die der Original-Mail (die der Aufrufer fürs Dedupe hält
    und die im Draft nur als In-Reply-To steht). Vor dem Fix wurde die Original-ID
    durchgereicht -> Suche im Drafts-Ordner fand nie etwas -> Dauer-Fehlalarm
    `draft_not_in_target_folder` bei jedem erzeugten Draft."""
    cfg = replace(mock_config, imap_drafts_folder="Entwürfe")
    client = _make_client_with_mock_mailbox(cfg)
    client._mailbox.fetch.return_value = iter([MagicMock()])

    client.append_to_drafts(_DRAFT_BYTES, verify=True)

    # Die gesuchte Header-ID muss die Draft-ID sein, nirgends die Original-ID.
    fetch_criteria = str(client._mailbox.fetch.call_args)
    assert "draft-123@vizpatch.test" in fetch_criteria
    assert "original-999" not in fetch_criteria


def test_append_to_drafts_verification_not_found_logs_error_no_crash(mock_config):
    cfg = replace(mock_config, imap_drafts_folder="Entwürfe")
    client = _make_client_with_mock_mailbox(cfg)
    client._mailbox.fetch.return_value = iter([])

    client.append_to_drafts(_DRAFT_BYTES, verify=True)

    client.logger.error.assert_any_call(
        "draft_not_in_target_folder",
        extra={"folder": "Entwürfe", "message_id": "<draft-123@vizpatch.test>"},
    )


def test_append_to_drafts_verification_fetch_exception_logs_warning_no_crash(mock_config):
    cfg = replace(mock_config, imap_drafts_folder="Entwürfe")
    client = _make_client_with_mock_mailbox(cfg)
    client._mailbox.fetch.side_effect = Exception("boom")

    # kein Crash — die Ablage selbst gilt weiterhin als erfolgt
    client.append_to_drafts(_DRAFT_BYTES, verify=True)

    assert client.logger.warning.called
    warning_calls = [c.args[0] for c in client.logger.warning.call_args_list]
    assert "draft_append_verification_failed" in warning_calls


def test_append_to_drafts_verification_restores_inbox_folder(mock_config):
    cfg = replace(mock_config, imap_drafts_folder="Entwürfe", imap_inbox_folder="INBOX")
    client = _make_client_with_mock_mailbox(cfg)
    client._mailbox.fetch.return_value = iter([MagicMock()])

    client.append_to_drafts(_DRAFT_BYTES, verify=True)

    # letzter folder.set-Aufruf muss der INBOX-Ordner sein (Nachkontrolle wechselt
    # zuerst in den Drafts-Ordner, dann im finally zurück)
    set_calls = [c.args[0] for c in client._mailbox.folder.set.call_args_list]
    assert set_calls[-1] == "INBOX"


def test_append_to_drafts_without_verify_skips_verification(mock_config):
    cfg = replace(mock_config, imap_drafts_folder="Entwürfe")
    client = _make_client_with_mock_mailbox(cfg)

    client.append_to_drafts(_DRAFT_BYTES)

    assert not client._mailbox.fetch.called


def test_append_to_drafts_verify_without_message_id_skips_with_warning(mock_config):
    """Bytes ohne Message-ID-Header: die Nachkontrolle wird mit Warnung übersprungen,
    die Ablage selbst läuft normal durch."""
    cfg = replace(mock_config, imap_drafts_folder="Entwürfe")
    client = _make_client_with_mock_mailbox(cfg)

    client.append_to_drafts(b"Subject: ohne id\r\n\r\nHallo\r\n", verify=True)

    assert client._mailbox.append.called
    assert not client._mailbox.fetch.called
    warning_calls = [c.args[0] for c in client.logger.warning.call_args_list]
    assert "draft_verify_skipped_no_message_id" in warning_calls
