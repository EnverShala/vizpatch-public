"""Tests für das Werkzeug `letzte_aktion_rueckgaengig` (Phase 13, Plan 13-03,
MBOX-02/03).

Deckt ab: Anker ist die Message-ID (NIE die im Journal mitgeführte
`uid_zum_zeitpunkt`), Mehrordner-Suche (zuletzt bekannter Zielordner + INBOX-
Fallback), klare Fehlermeldung bei endgültig geleertem Papierkorb
(`unwiderruflich`), klare Fehlermeldung bei nicht mehr existierendem
Rückstell-Ziel, ehrlicher Teilerfolg-Bericht sowie die vollständige
Werkzeug-Registrierung (Schema/Handler/Aktivitäts-Label/Anonymizer-Awareness).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from .test_chat_folder_tools import _folder_info
from .test_chat_tools import _fake_mailbox, _msg_with_mid, _setup_env, _write_agent_env


def test_letzte_aktion_rueckgaengig_no_journal_entry_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert "fehler" in ergebnis


def test_letzte_aktion_rueckgaengig_uses_current_uid_never_uid_zum_zeitpunkt(
    mocker, tmp_path, monkeypatch
):
    """Kernkriterium: die im Journal gespeicherte `uid_zum_zeitpunkt` (hier
    absichtlich eine uid, die auf dem Server NICHT mehr existiert) darf NIE
    als Move-Argument verwendet werden — die tatsächlich verschobene uid ist
    die AKTUELL per Message-ID gefundene."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info",
        "verschieben",
        "INBOX",
        "INBOX.Rechnungen",
        [{"uid": "999", "message_id": "<a@example.com>", "betreff": "Rechnung"}],
    )

    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="55", mid="<a@example.com>")])
    mock_mailbox.folder.list.return_value = infos
    mock_mailbox.client.capabilities = ("MOVE",)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert ergebnis["zurueckgestellt"] == 1
    assert ergebnis["nicht_gefunden"] == []
    # Die tatsächlich per Message-ID gefundene, AKTUELLE uid ("55") wird
    # bewegt — NIE die veraltete uid_zum_zeitpunkt ("999") aus dem Journal.
    mock_mailbox.move.assert_called_once_with(["55"], "INBOX")


def test_letzte_aktion_rueckgaengig_falls_back_to_inbox_when_not_in_last_known_folder(
    mocker, tmp_path, monkeypatch
):
    """Der Betreiber kann die Mail zwischenzeitlich selbst weiterverschoben
    haben (z. B. in Outlook) — ist sie im zuletzt bekannten Zielordner nicht
    mehr auffindbar, sucht das Werkzeug zusätzlich in INBOX, bevor es
    aufgibt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info",
        "verschieben",
        "INBOX.Alt",
        "INBOX.Rechnungen",
        [{"uid": "42", "message_id": "<b@example.com>", "betreff": "Rechnung"}],
    )

    infos = [
        _folder_info("INBOX", delim="."),
        _folder_info("INBOX.Alt", delim="."),
        _folder_info("INBOX.Rechnungen", delim="."),
    ]
    mock_mailbox = _fake_mailbox(
        per_folder_messages={
            "INBOX.Rechnungen": [],
            "INBOX": [_msg_with_mid(uid="7", mid="<b@example.com>")],
        }
    )
    mock_mailbox.folder.list.return_value = infos
    mock_mailbox.client.capabilities = ("MOVE",)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert ergebnis["zurueckgestellt"] == 1
    assert ergebnis["nicht_gefunden"] == []
    mock_mailbox.move.assert_called_once_with(["7"], "INBOX.Alt")


def test_letzte_aktion_rueckgaengig_purged_trash_returns_clear_endgueltig_error(
    tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info",
        "papierkorb",
        "INBOX",
        "Papierkorb",
        [{"uid": "1", "message_id": "<a@example.com>", "betreff": "Rechnung"}],
    )
    action_journal.mark_trash_purged("info")

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert "fehler" in ergebnis
    assert "endgültig" in ergebnis["fehler"]


def test_letzte_aktion_rueckgaengig_missing_restore_target_folder_returns_clear_error(
    mocker, tmp_path, monkeypatch
):
    """Der ursprüngliche Quellordner (jetzt Rückstell-Ziel) existiert nicht
    mehr (umbenannt/gelöscht) -> verständlicher Fehler statt Traceback,
    nichts wird bewegt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info",
        "verschieben",
        "INBOX.GeloeschterOrdner",
        "INBOX.Rechnungen",
        [{"uid": "1", "message_id": "<a@example.com>", "betreff": "Rechnung"}],
    )

    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="1", mid="<a@example.com>")])
    mock_mailbox.folder.list.return_value = infos
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert "fehler" in ergebnis
    assert "existiert" in ergebnis["fehler"]
    mock_mailbox.move.assert_not_called()


def test_letzte_aktion_rueckgaengig_reports_partial_success_honestly(mocker, tmp_path, monkeypatch):
    """10 Einträge, 3 davon nirgends auffindbar -> 7 zurückgestellt,
    3 nicht_gefunden — kein Abbruch, kein stiller Fehlschlag."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    eintraege = [
        {"uid": str(100 + i), "message_id": f"<msg{i}@example.com>", "betreff": f"Mail {i}"}
        for i in range(10)
    ]
    action_journal.record_action("info", "papierkorb", "INBOX", "Papierkorb", eintraege)

    findable = {f"<msg{i}@example.com>" for i in range(7)}

    def _uids_side_effect(_mailbox, message_id, _folder):
        return ["999"] if message_id in findable else []

    mocker.patch("src.chat_tools._uids_of_message_id_in_folder", side_effect=_uids_side_effect)
    mocker.patch("src.chat_tools._move_message", return_value=("INBOX", "<irrelevant@example.com>"))

    mock_mailbox = MagicMock()
    mock_mailbox.__enter__ = MagicMock(return_value=mock_mailbox)
    mock_mailbox.__exit__ = MagicMock(return_value=False)
    mock_mailbox.folder.list.return_value = [
        _folder_info("INBOX", delim="."),
        _folder_info("Papierkorb", delim="."),
    ]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert ergebnis["zurueckgestellt"] == 7
    assert len(ergebnis["nicht_gefunden"]) == 3


def test_letzte_aktion_rueckgaengig_entry_without_message_id_reported_as_not_found(
    mocker, tmp_path, monkeypatch
):
    """Ein Journal-Eintrag ohne Message-ID (Mail ohne diesen Header zum
    Zeitpunkt der Aktion) kann nicht automatisch wiedergefunden werden — das
    wird als `nicht_gefunden` gemeldet, kein Absturz, kein Raten der uid."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Rechnungen", [{"uid": "1", "message_id": ""}]
    )

    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = infos
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert ergebnis["zurueckgestellt"] == 0
    assert len(ergebnis["nicht_gefunden"]) == 1
    mock_mailbox.move.assert_not_called()


def test_letzte_aktion_rueckgaengig_marks_action_undone_after_attempt(mocker, tmp_path, monkeypatch):
    """Nach dem Versuch gilt die Aktion im Journal als abgeschlossen — ein
    erneuter Aufruf liefert 'keine Aktion' statt endlos dieselbe zu
    wiederholen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="55", mid="<a@example.com>")])
    mock_mailbox.folder.list.return_value = infos
    mock_mailbox.client.capabilities = ("MOVE",)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Rechnungen", [{"uid": "999", "message_id": "<a@example.com>"}]
    )

    erstes_ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")
    assert erstes_ergebnis["zurueckgestellt"] == 1

    zweites_ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")
    assert "fehler" in zweites_ergebnis


def test_letzte_aktion_rueckgaengig_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.letzte_aktion_rueckgaengig("../evil")


# --- Registrierung (13-PATTERNS.md Muster 1) ---------------------------------


def test_letzte_aktion_rueckgaengig_is_fully_registered():
    import src.chat_tools as chat_tools

    assert "letzte_aktion_rueckgaengig" in chat_tools.TOOL_HANDLERS
    assert chat_tools.TOOL_HANDLERS["letzte_aktion_rueckgaengig"] is chat_tools.letzte_aktion_rueckgaengig

    schema_names = {schema["name"] for schema in chat_tools.TOOL_SCHEMAS}
    assert "letzte_aktion_rueckgaengig" in schema_names

    assert "letzte_aktion_rueckgaengig" in chat_tools._ANON_AWARE_TOOLS
    assert chat_tools._tool_activity_label("letzte_aktion_rueckgaengig") != "Letzte aktion rueckgaengig…"
    assert "letzte_aktion_rueckgaengig" in chat_tools._TOOL_ACTIVITY_LABELS
