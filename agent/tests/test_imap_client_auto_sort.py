"""Tests fuer die IMAP-Bausteine des automatischen Einsortierens (Phase 14,
Plan 14-01, AUTO-01..08)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from imap_tools.errors import MailboxMoveError
from src.imap_client import ImapClient


def _make_client_with_mock_mailbox(config):
    client = ImapClient.__new__(ImapClient)
    client.config = config
    client.logger = MagicMock()
    client._mailbox = MagicMock()
    return client


# --- resolve_protected_folder_names (AUTO-07) -------------------------------


def test_resolve_protected_folder_names_includes_configured_folders(mock_config):
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.folder.list.return_value = []

    names = client.resolve_protected_folder_names()

    assert "inbox" in names
    assert mock_config.imap_drafts_folder.casefold() in names
    assert mock_config.imap_sent_folder.casefold() in names


def test_resolve_protected_folder_names_includes_special_use_roles(mock_config):
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.folder.list.return_value = [
        SimpleNamespace(name="Papierkorb", flags=("\\Trash",)),
        SimpleNamespace(name="Spam", flags=("\\Junk",)),
        SimpleNamespace(name="INBOX.Kunden", flags=()),
    ]

    names = client.resolve_protected_folder_names()

    assert "papierkorb" in names
    assert "spam" in names
    assert "inbox.kunden" not in names


def test_resolve_protected_folder_names_list_failure_falls_back_to_configured(mock_config):
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.folder.list.side_effect = Exception("boom")

    names = client.resolve_protected_folder_names()

    assert "inbox" in names
    client.logger.warning.assert_called_once()


# --- fetch_seen_candidates (AUTO-03/AUTO-06/Auflage #9) ---------------------


def test_fetch_seen_candidates_uses_mark_seen_false_and_limit(mock_config):
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.fetch.return_value = iter([])

    client.fetch_seen_candidates(limit=50)

    client._mailbox.folder.set.assert_called_once_with(mock_config.imap_inbox_folder)
    _, kwargs = client._mailbox.fetch.call_args
    assert kwargs["mark_seen"] is False
    assert kwargs["limit"] == 50


def test_fetch_seen_candidates_reads_newest_first(mock_config):
    """Quick 260822-4ay: die Leserichtung ist fachlich, nicht Stil. Mit
    `reverse=False` (dem alten Wert) las der Lauf von der ältesten Mail her —
    bei 3385 Mails und `limit=50` wäre eine heute eingetroffene Mail erst nach
    ~68 Zyklen dran gewesen."""
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.fetch.return_value = iter([])

    client.fetch_seen_candidates(limit=50)

    _, kwargs = client._mailbox.fetch.call_args
    assert kwargs["reverse"] is True


def test_fetch_seen_candidates_returns_messages(mock_config):
    client = _make_client_with_mock_mailbox(mock_config)
    msgs = [SimpleNamespace(uid="1"), SimpleNamespace(uid="2")]
    client._mailbox.fetch.return_value = iter(msgs)

    result = client.fetch_seen_candidates(limit=10)

    assert result == msgs


class _FakeSeenAwareMailbox:
    """Simuliert einen IMAP-Server, der die SEEN-Suchkriterien tatsächlich
    auswertet (Gegenprobe für AUTO-03): `fetch` filtert `messages` nach
    `msg.seen`, wenn und nur wenn die übergebenen Kriterien exakt `(SEEN)`
    lauten (das ist, was `imap_tools.AND(seen=True)` erzeugt) — ruft der
    Produktivcode stattdessen ohne SEEN-Filter (oder mit einem anderen
    Kriterium) auf, liefert dieser Fake ALLE Mails zurück (auch ungelesene)
    und macht damit eine Regression sichtbar, statt sie zu verschleiern."""

    def __init__(self, messages):
        self.messages = messages
        self.folder = SimpleNamespace(set=lambda *_: None)

    def fetch(self, criteria, mark_seen=False, reverse=False, limit=None):
        if str(criteria) == "(SEEN)":
            result = [m for m in self.messages if getattr(m, "seen", False)]
        else:
            result = list(self.messages)
        # `messages` ist wie im Postfach von ALT nach NEU sortiert; `reverse`
        # wird hier bewusst mit ausgewertet, damit der Deckel-Schnitt unten die
        # ECHTE Leserichtung abbildet (Quick 260822-4ay) — ein Fake, der
        # `reverse` ignoriert, hätte den Befund nie sichtbar machen können.
        if reverse:
            result = list(reversed(result))
        return result[:limit] if limit else result


def test_fetch_seen_candidates_excludes_unread_mail_matching_category(mock_config):
    """AUTO-03 Kern-Auflage, mit Gegenprobe: eine UNGELESENE Mail, die
    perfekt auf eine aktive Kategorie passen würde, taucht in den
    Kandidaten für den automatischen Sortierlauf NICHT auf — nur weil sie
    ungelesen ist, unabhängig vom Inhalt (den `fetch_seen_candidates`
    ohnehin nicht kennt, die Beurteilung passiert erst später im
    Klassifikations-Schritt)."""
    client = ImapClient.__new__(ImapClient)
    client.config = mock_config
    client.logger = MagicMock()
    gelesene_rechnung = SimpleNamespace(uid="1", subject="Rechnung Nr. 42", seen=True)
    ungelesene_rechnung = SimpleNamespace(uid="2", subject="Rechnung Nr. 43", seen=False)
    client._mailbox = _FakeSeenAwareMailbox([gelesene_rechnung, ungelesene_rechnung])

    result = client.fetch_seen_candidates(limit=50)

    assert result == [gelesene_rechnung]
    assert ungelesene_rechnung not in result


def test_fetch_seen_candidates_deckel_schneidet_die_aeltesten_ab(mock_config):
    """Quick 260822-4ay, der eigentliche Befund: greift der Deckel, muss die
    NEUESTE Mail dabei sein und die älteste wegfallen — vorher war es genau
    umgekehrt, weshalb frische Mails in einem großen Postfach praktisch nie
    an die Reihe kamen."""
    client = ImapClient.__new__(ImapClient)
    client.config = mock_config
    client.logger = MagicMock()
    alt = SimpleNamespace(uid="1", subject="Rechnung von 2019", seen=True)
    mittel = SimpleNamespace(uid="2", subject="Rechnung von 2023", seen=True)
    neu = SimpleNamespace(uid="3", subject="Rechnung von heute", seen=True)
    client._mailbox = _FakeSeenAwareMailbox([alt, mittel, neu])

    result = client.fetch_seen_candidates(limit=2)

    assert neu in result
    assert alt not in result


# --- move_message (AUTO-02/AUTO-07) -----------------------------------------


def test_move_message_verschiebt_auch_ohne_gemeldete_move_capability(mock_config):
    """Gmail fuehrt MOVE NICHT in CAPABILITY, beherrscht `UID MOVE` aber.

    Befund der Live-Abnahme 2026-08-19: die fruehere Vorpruefung auf die
    Capability-Liste lehnte an einem Gmail-Postfach JEDE Verschiebung ab, die
    Kategorie-Aktion `verschieben` war dort vollstaendig wirkungslos."""
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.client.capabilities = ()          # kein MOVE gemeldet
    client._mailbox.client.uid.return_value = ("OK", [b""])

    moved = client.move_message("5", "INBOX", "Rechnungen")

    assert moved is True
    client._mailbox.client.uid.assert_called_once_with("MOVE", "5", b'"Rechnungen"')


def test_move_message_ruft_niemals_mailbox_move(mock_config):
    """`MailBox.move()` faellt ohne MOVE-Capability still auf copy()+delete()
    zurueck — und `delete()` haengt ein folder-weites EXPUNGE an, das FREMDE
    als geloescht markierte Mails anderer Programme endgueltig entfernt."""
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.client.capabilities = ("MOVE",)
    client._mailbox.client.uid.return_value = ("OK", [b""])

    client.move_message("5", "INBOX", "Rechnungen")

    assert not client._mailbox.move.called
    assert not client._mailbox.delete.called
    assert not client._mailbox.expunge.called


def test_move_message_moves_when_server_supports_move(mock_config):
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.client.capabilities = ("MOVE",)
    client._mailbox.client.uid.return_value = ("OK", [b""])

    moved = client.move_message("5", "INBOX", "Rechnungen")

    assert moved is True
    client._mailbox.folder.set.assert_called_once_with("INBOX")
    client._mailbox.client.uid.assert_called_once_with("MOVE", "5", b'"Rechnungen"')
    assert not client._mailbox.folder.create.called


def test_move_message_creates_missing_target_folder(mock_config):
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.client.capabilities = ("MOVE",)
    client._mailbox.client.uid.side_effect = [
        MailboxMoveError(("does not exist", [b""]), "OK"),
        ("OK", [b""]),
    ]

    moved = client.move_message("5", "INBOX", "Neu")

    assert moved is True
    client._mailbox.folder.create.assert_called_once_with("Neu")
    assert client._mailbox.client.uid.call_count == 2


def test_move_message_raises_non_missing_error(mock_config):
    """Quota/Berechtigung fliegt weiter — nur ein `BAD` gilt als 'kann kein MOVE'."""
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.client.capabilities = ("MOVE",)
    client._mailbox.client.uid.side_effect = MailboxMoveError(("quota exceeded", [b""]), "OK")

    with pytest.raises(MailboxMoveError):
        client.move_message("5", "INBOX", "Rechnungen")

    assert not client._mailbox.folder.create.called


def test_move_message_ueberspringt_bei_bad_response(mock_config):
    """Kann der Server UID MOVE wirklich nicht, antwortet er BAD — dann wird
    uebersprungen statt auf einen expunge-behafteten Ersatzweg auszuweichen."""
    client = _make_client_with_mock_mailbox(mock_config)
    client._mailbox.client.capabilities = ()
    client._mailbox.client.uid.side_effect = MailboxMoveError(
        ("BAD command unknown: MOVE", [b""]), "OK"
    )

    moved = client.move_message("5", "INBOX", "Rechnungen")

    assert moved is False
    assert not client._mailbox.move.called
    client.logger.warning.assert_called_once()
    assert client.logger.warning.call_args[0][0] == "auto_sort_move_unsupported"


# --- mark_important (AUTO-02) -----------------------------------------------


def test_mark_important_uses_raw_uid_store_not_mailbox_flag(mock_config):
    """Kritisch: NIE MailBox.flag() (haengt folder-weites EXPUNGE an,
    Phase 13, Plan 13-01)."""
    client = _make_client_with_mock_mailbox(mock_config)

    client.mark_important("7")

    client._mailbox.client.uid.assert_called_once_with("STORE", "7", "+FLAGS", "(\\Flagged)")
    assert not client._mailbox.flag.called
    assert not client._mailbox.expunge.called
