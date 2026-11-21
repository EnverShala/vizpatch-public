"""Tests für die Message-ID-Rückgabe der Move-/Papierkorb-Werkzeuge (Phase 13,
Plan 13-02, Vorarbeit MBOX-02 — 13-RESEARCH.md Pitfall 3).

`_move_message` liefert seit dieser Änderung `(target_folder, message_id)`
statt nur des Zielordners. Alle Aufrufer (`_move_to_trash`,
`_bulk_move_server_side`, `mail_verschieben`, `mail_in_papierkorb`,
`mails_verschieben`, `mails_in_papierkorb`, `entwurf_in_papierkorb`,
`entwurf_bearbeiten`, `ordner_loeschen`) entpacken das Tupel — keiner darf es
als String weiterverwenden. Die Batch-Werkzeuge (`mails_verschieben`/
`mails_in_papierkorb`) tragen zusätzlich ein `eintraege`-Feld mit
uid/Message-ID-Paaren je erfolgreich bewegter Nachricht — die Grundlage, auf
der Plan 13-03 das Aktions-Journal baut.
"""
from __future__ import annotations

from .test_chat_folder_tools import _folder_info
from .test_chat_move_tools import _setup_move_env
from .test_chat_tools import (
    _fake_folder_info,
    _fake_mailbox,
    _msg,
    _msg_with_mid,
    _setup_env,
    _write_agent_env,
)


def test_move_message_liefert_tupel_mit_message_id_der_quellmail():
    import src.chat_tools as chat_tools
    from unittest.mock import MagicMock

    mailbox = MagicMock()
    mailbox.client.capabilities = ("MOVE",)

    # Fetch-Reihenfolge in `_move_message` (Muster wie im Bestandstest
    # test_move_to_trash_source_left_behind_triggers_targeted_uid_expunge_then_succeeds):
    #   1. Pre-Move-Fetch -> Nachricht ist da, Message-ID wird gesichert.
    #   2. Quell-Rest-Prüfung NACH move() -> muss LEER sein, sonst greift der
    #      CR-05-Fallback und der Move gilt als nicht bestätigt.
    # Ein konstantes `return_value` würde die Nachricht auch nach dem Move noch
    # im Quell-Ordner zeigen und damit MailboxMoveError auslösen.
    fetch_calls = {"n": 0}

    def _fetch_side_effect(*_args, **_kwargs):
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            return [_msg_with_mid(uid="42", mid="<original-42@example.com>")]
        return []

    mailbox.fetch.side_effect = _fetch_side_effect

    ergebnis = chat_tools._move_message(mailbox, "42", "INBOX", "INBOX.Kunden")

    assert isinstance(ergebnis, tuple)
    assert len(ergebnis) == 2
    ziel_ordner, message_id = ergebnis
    assert ziel_ordner == "INBOX.Kunden"
    assert message_id == "<original-42@example.com>"


def test_mail_verschieben_ziel_ordner_ist_string_kein_tupel(mocker, tmp_path, monkeypatch):
    """Beweis, dass `mail_verschieben` das Tupel korrekt entpackt: `ziel_ordner`
    ist der exakte Ordnername, keine Tupel-Repräsentation."""
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg_with_mid(uid="42")])

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mail_verschieben("info", uid="42", ziel_ordner="Rechnungen")

    assert ergebnis["ziel_ordner"] == "INBOX.Rechnungen"
    assert isinstance(ergebnis["ziel_ordner"], str)
    assert ergebnis["message_id"] == "<orig-42@example.com>"


def test_mail_in_papierkorb_papierkorb_ist_string_kein_tupel(mocker, tmp_path, monkeypatch):
    """Beweis, dass `mail_in_papierkorb` das Tupel korrekt entpackt: `papierkorb`
    ist der exakte Ordnername, keine Tupel-Repräsentation."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_TRASH_CONFIRMATION", "false")

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42")])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Papierkorb", flags=("\\Trash",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mail_in_papierkorb("info", uid="42")

    assert ergebnis["papierkorb"] == "Papierkorb"
    assert isinstance(ergebnis["papierkorb"], str)
    assert ergebnis["message_id"] == "<orig-42@example.com>"


def test_entwurf_in_papierkorb_papierkorb_ist_string_kein_tupel(mocker, tmp_path, monkeypatch):
    """Beweis, dass `entwurf_in_papierkorb` das Tupel korrekt entpackt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_TRASH_CONFIRMATION", "false")

    # `_msg_with_mid` hat die Message-ID `<orig-42@example.com>` als Default —
    # bei einer abweichenden uid muss sie mitgegeben werden, sonst prueft der
    # Test die Default-ID statt der zur uid passenden.
    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="7", mid="<orig-7@example.com>")])
    mock_mailbox.folder.list.return_value = [
        _fake_folder_info("Entwürfe", flags=("\\Drafts",)),
        _fake_folder_info("Papierkorb", flags=("\\Trash",)),
    ]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.entwurf_in_papierkorb("info", uid="7")

    assert ergebnis["papierkorb"] == "Papierkorb"
    assert isinstance(ergebnis["papierkorb"], str)
    assert ergebnis["message_id"] == "<orig-7@example.com>"


def test_mails_verschieben_liefert_eintraege_mit_uid_und_message_id(mocker, tmp_path, monkeypatch):
    """`mails_verschieben` mit drei Mails liefert `eintraege` mit drei Paaren
    aus uid und Message-ID."""
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    messages = [
        _msg_with_mid(uid="1", mid="<eins@example.com>"),
        _msg_with_mid(uid="2", mid="<zwei@example.com>"),
        _msg_with_mid(uid="3", mid="<drei@example.com>"),
    ]
    _setup_move_env(mocker, tmp_path, monkeypatch, infos, messages)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_verschieben("info", uids=["1", "2", "3"], ziel_ordner="Rechnungen")

    assert ergebnis["verschoben"] == 3
    assert len(ergebnis["eintraege"]) == 3
    uids = {e["uid"] for e in ergebnis["eintraege"]}
    assert uids == {"1", "2", "3"}
    message_ids = {e["message_id"] for e in ergebnis["eintraege"]}
    assert message_ids == {"<eins@example.com>", "<zwei@example.com>", "<drei@example.com>"}


def test_mails_verschieben_mail_ohne_message_id_header_bleibt_erfolgreich(
    mocker, tmp_path, monkeypatch
):
    """Eine Mail ohne `Message-ID`-Header erscheint in `eintraege` mit leerer
    `message_id` — die Aktion gilt trotzdem als erfolgreich (kein Abbruch)."""
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    # _msg() OHNE headers -> keine Message-ID.
    messages = [_msg(uid="1")]
    _setup_move_env(mocker, tmp_path, monkeypatch, infos, messages)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_verschieben("info", uids=["1"], ziel_ordner="Rechnungen")

    assert ergebnis["verschoben"] == 1
    assert ergebnis["fehlgeschlagen"] == []
    assert ergebnis["eintraege"] == [{"uid": "1", "message_id": ""}]


def test_mails_in_papierkorb_liefert_eintraege(mocker, tmp_path, monkeypatch):
    """Gegenstück für den Papierkorb-Pfad: `mails_in_papierkorb` liefert
    ebenfalls `eintraege`."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_TRASH_CONFIRMATION", "false")

    messages = [
        _msg_with_mid(uid="1", mid="<eins@example.com>"),
        _msg_with_mid(uid="2", mid="<zwei@example.com>"),
    ]
    mock_mailbox = _fake_mailbox(messages)
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Papierkorb", flags=("\\Trash",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_in_papierkorb("info", uids=["1", "2"])

    assert ergebnis["verschoben"] == 2
    assert len(ergebnis["eintraege"]) == 2
    uids = {e["uid"] for e in ergebnis["eintraege"]}
    assert uids == {"1", "2"}


def test_tool_trace_line_fuer_mails_verschieben_enthaelt_kein_eintraege(tmp_path, monkeypatch):
    """Die Tool-Trace-Zeile bleibt unter `MAX_TOOL_TRACE_CHARS` und enthält
    `eintraege` NICHT — die Allowlist ist strukturell dagegen abgesichert."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    payload = {
        "verschoben": 3,
        "ziel_ordner": "INBOX.Rechnungen",
        "quell_ordner": "INBOX",
        "fehlgeschlagen": [],
        "eintraege": [
            {"uid": "1", "message_id": "<eins@example.com>"},
            {"uid": "2", "message_id": "<zwei@example.com>"},
            {"uid": "3", "message_id": "<drei@example.com>"},
        ],
    }

    trace = chat_tools._tool_trace_line("mails_verschieben", payload)

    assert len(trace) <= chat_tools.MAX_TOOL_TRACE_CHARS
    assert "eintraege" not in trace
    assert "eins@example.com" not in trace
    assert "ziel_ordner=INBOX.Rechnungen" in trace


# --- Journal-Schreibaufrufe (Phase 13, Plan 13-03, MBOX-02) -------------------


def test_mail_verschieben_writes_journal_entry_after_successful_move(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg_with_mid(uid="42")])

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    chat_tools.mail_verschieben("info", uid="42", ziel_ordner="Rechnungen")

    letzte = action_journal.last_action("info")
    assert letzte is not None
    assert letzte["aktion"] == "verschieben"
    assert letzte["quell_ordner"] == "INBOX"
    assert letzte["ziel_ordner"] == "INBOX.Rechnungen"
    assert letzte["eintraege"] == [
        {"message_id": "<orig-42@example.com>", "uid_zum_zeitpunkt": "42", "betreff": ""}
    ]


def test_mails_verschieben_writes_journal_entries_only_for_successful_uids(
    mocker, tmp_path, monkeypatch
):
    """Fehlgeschlagene uids (hier: eine nicht existierende) landen NICHT im
    Journal — nur die tatsächlich verschobenen `eintraege`."""
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    messages = [
        _msg_with_mid(uid="1", mid="<eins@example.com>"),
        _msg_with_mid(uid="2", mid="<zwei@example.com>"),
    ]
    _setup_move_env(mocker, tmp_path, monkeypatch, infos, messages)

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_verschieben("info", uids=["1", "2", "999"], ziel_ordner="Rechnungen")

    assert ergebnis["verschoben"] == 2
    assert len(ergebnis["fehlgeschlagen"]) == 1

    letzte = action_journal.last_action("info")
    assert letzte is not None
    message_ids = {e["message_id"] for e in letzte["eintraege"]}
    assert message_ids == {"<eins@example.com>", "<zwei@example.com>"}
    assert len(letzte["eintraege"]) == 2


def test_mail_in_papierkorb_writes_journal_entry_with_betreff(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_TRASH_CONFIRMATION", "false")

    msg = _msg_with_mid(uid="42")
    msg.subject = "Rechnung Nr. 123"
    mock_mailbox = _fake_mailbox([msg])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Papierkorb", flags=("\\Trash",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    chat_tools.mail_in_papierkorb("info", uid="42")

    letzte = action_journal.last_action("info")
    assert letzte is not None
    assert letzte["aktion"] == "papierkorb"
    assert letzte["quell_ordner"] == "INBOX"
    assert letzte["ziel_ordner"] == "Papierkorb"
    assert letzte["eintraege"] == [
        {
            "message_id": "<orig-42@example.com>",
            "uid_zum_zeitpunkt": "42",
            "betreff": "Rechnung Nr. 123",
        }
    ]


def test_mails_in_papierkorb_writes_journal_entries(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_TRASH_CONFIRMATION", "false")

    messages = [
        _msg_with_mid(uid="1", mid="<eins@example.com>"),
        _msg_with_mid(uid="2", mid="<zwei@example.com>"),
    ]
    mock_mailbox = _fake_mailbox(messages)
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Papierkorb", flags=("\\Trash",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    chat_tools.mails_in_papierkorb("info", uids=["1", "2"])

    letzte = action_journal.last_action("info")
    assert letzte is not None
    assert letzte["aktion"] == "papierkorb"
    assert len(letzte["eintraege"]) == 2


def test_mails_verschieben_journal_error_does_not_fail_the_move(mocker, tmp_path, monkeypatch):
    """Ein Journal-Fehler (hier: `record_action` wirft) darf das bereits
    erfolgreich abgeschlossene Verschieben NICHT nachträglich als Fehler
    erscheinen lassen — `mails_verschieben` liefert weiterhin sein normales
    Erfolgs-Dict."""
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg_with_mid(uid="1")])
    mocker.patch("src.action_journal.record_action", side_effect=RuntimeError("db kaputt"))

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_verschieben("info", uids=["1"], ziel_ordner="Rechnungen")

    assert ergebnis["verschoben"] == 1
    assert ergebnis["fehlgeschlagen"] == []


def test_mail_verschieben_journal_error_does_not_fail_the_move(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    _setup_move_env(mocker, tmp_path, monkeypatch, infos, [_msg_with_mid(uid="42")])
    mocker.patch("src.action_journal.record_action", side_effect=RuntimeError("db kaputt"))

    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mail_verschieben("info", uid="42", ziel_ordner="Rechnungen")

    assert ergebnis["verschoben"] is True
    assert ergebnis["ziel_ordner"] == "INBOX.Rechnungen"


def test_papierkorb_leeren_marks_journal_trash_purged_not_record_action(
    mocker, tmp_path, monkeypatch
):
    """`papierkorb_leeren` schreibt KEINEN Journal-Eintrag (kein
    `record_action`-Aufruf) — es markiert offene Papierkorb-Einträge als
    unwiderruflich (`mark_trash_purged`)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    mock_mailbox = _fake_mailbox([_msg(uid="1")])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Papierkorb", flags=("\\Trash",))]
    mock_mailbox.uids.return_value = ["1"]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    record_action_spy = mocker.spy(action_journal, "record_action")
    mark_purged_spy = mocker.spy(action_journal, "mark_trash_purged")

    ergebnis = chat_tools.papierkorb_leeren("info")

    assert ergebnis["geleert"] is True
    record_action_spy.assert_not_called()
    mark_purged_spy.assert_called_once_with("info")


def test_papierkorb_leeren_after_papierkorb_move_makes_undo_unwiderruflich(
    mocker, tmp_path, monkeypatch
):
    """End-to-end-Beleg: eine per `mail_in_papierkorb` verschobene Mail landet
    im Journal; ein anschließendes `papierkorb_leeren` markiert genau diesen
    Eintrag als `unwiderruflich` (13-RESEARCH.md Abschnitt 9, Fallstrick 4)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_TRASH_CONFIRMATION", "false")

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42")])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Papierkorb", flags=("\\Trash",))]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    chat_tools.mail_in_papierkorb("info", uid="42")
    assert action_journal.last_action("info")["unwiderruflich"] is False

    mock_mailbox.uids.return_value = []
    chat_tools.papierkorb_leeren("info")

    letzte = action_journal.last_action("info")
    assert letzte["unwiderruflich"] is True
