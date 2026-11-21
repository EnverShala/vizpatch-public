"""Tests für das Werkzeug `mails_markieren` (Phase 13, Plan 13-04, MBOX-04).

Deckt ab: der kritische Punkt dieses Plans — `mails_markieren` setzt Flags
AUSSCHLIESSLICH über `_store_flags` (rohes UID STORE), NIE über
`MailBox.flag()`, und löst deshalb NIE ein folder-weites EXPUNGE aus (ein
Fake-Mailbox-Objekt zeichnet alle IMAP-Kommandos auf und belegt das). Dazu:
Batch-Regeln wie `mails_verschieben` (Skalar->Liste, Nicht-Liste/leer ->
fehler, Deckel, ungültige uids -> fehlgeschlagen, Duplikate entfernt), der
Journal-Eintrag (MBOX-02/03) inklusive der Kodierung, die
`letzte_aktion_rueckgaengig` zum Zurücksetzen braucht, sowie die vollständige
Werkzeug-Registrierung.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from .test_chat_folder_tools import _folder_info
from .test_chat_tools import (
    _fake_folder_info,
    _fake_mailbox,
    _msg_with_mid,
    _setup_env,
    _write_agent_env,
)


# --- Kritischer Punkt: kein MailBox.flag(), kein Expunge --------------------


def test_mails_markieren_records_store_not_flag_and_never_expunges(mocker, tmp_path, monkeypatch):
    """Zeichnet ALLE über `mailbox.client.uid(...)` abgesetzten IMAP-Kommandos
    auf und belegt: nach `mails_markieren` steht `EXPUNGE` NICHT im
    aufgezeichneten Verkehr, und `mailbox.flag(...)` (der Wrapper mit dem
    impliziten folder-weiten Expunge, Plan 13-01) wird nie aufgerufen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42"], "gelesen")

    assert ergebnis["markiert"] == 1
    mock_mailbox.flag.assert_not_called()
    aufgezeichnete_befehle = [call.args[0] for call in mock_mailbox.client.uid.call_args_list]
    assert "EXPUNGE" not in aufgezeichnete_befehle
    mock_mailbox.client.uid.assert_any_call("STORE", "42", "+FLAGS", "(\\Seen)")


def test_mails_markieren_ungelesen_entfernt_seen_flag(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42"], "ungelesen")

    assert ergebnis["markiert"] == 1
    mock_mailbox.client.uid.assert_any_call("STORE", "42", "-FLAGS", "(\\Seen)")


def test_mails_markieren_wichtig_setzt_flagged(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42"], "wichtig")

    assert ergebnis["markiert"] == 1
    mock_mailbox.client.uid.assert_any_call("STORE", "42", "+FLAGS", "(\\Flagged)")


def test_mails_markieren_unwichtig_entfernt_flagged(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42"], "unwichtig")

    assert ergebnis["markiert"] == 1
    mock_mailbox.client.uid.assert_any_call("STORE", "42", "-FLAGS", "(\\Flagged)")


def test_mails_markieren_invalid_modus_returns_error_without_imap_contact(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = MagicMock()
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42"], "geloescht")

    assert "fehler" in ergebnis
    mock_mailbox.assert_not_called()


# --- Batch-Regeln (wörtlich wie mails_verschieben) ---------------------------


def test_mails_markieren_uid_scalar_normalized_to_list(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", "42", "gelesen")

    assert ergebnis["markiert"] == 1


def test_mails_markieren_uids_not_a_list_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_markieren("info", 42, "gelesen")

    assert "fehler" in ergebnis
    assert "Liste" in ergebnis["fehler"]


def test_mails_markieren_empty_uids_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mails_markieren("info", [], "gelesen")

    assert "fehler" in ergebnis


def test_mails_markieren_too_many_uids_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    viele_uids = [str(i) for i in range(chat_tools.MAX_BULK_TRASH_UIDS + 1)]
    ergebnis = chat_tools.mails_markieren("info", viele_uids, "gelesen")

    assert "fehler" in ergebnis
    assert "Zu viele" in ergebnis["fehler"]


def test_mails_markieren_invalid_uid_goes_to_fehlgeschlagen_without_imap_contact(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42", "1:*"], "gelesen")

    assert ergebnis["markiert"] == 1
    assert len(ergebnis["fehlgeschlagen"]) == 1
    assert ergebnis["fehlgeschlagen"][0]["uid"] == "1:*"


def test_mails_markieren_duplicate_uids_deduplicated(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42", "42"], "gelesen")

    assert ergebnis["markiert"] == 1


def test_mails_markieren_uid_not_found_on_server_reported_as_fehlgeschlagen(
    mocker, tmp_path, monkeypatch
):
    """Existenzprüfung VOR dem Flag-Setzen: eine uid, die der Existenz-Fetch
    nicht zurückliefert, wird als fehlgeschlagen gemeldet statt fälschlich als
    erfolgreich markiert gezählt zu werden."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42", "999"], "gelesen")

    assert ergebnis["markiert"] == 1
    assert len(ergebnis["fehlgeschlagen"]) == 1
    assert ergebnis["fehlgeschlagen"][0]["uid"] == "999"


def test_mails_markieren_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.mails_markieren("../evil", ["42"], "gelesen")


def test_mails_markieren_folder_set_failure_returns_error_no_crash(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox(folder_set_raises=True)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["42"], "gelesen", folder="Archiv")

    assert "fehler" in ergebnis


# --- Journal (MBOX-02/03) ----------------------------------------------------


def test_mails_markieren_writes_journal_entry_with_flag_and_richtung(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    chat_tools.mails_markieren("info", ["42"], "wichtig")

    letzte = action_journal.last_action("info")
    assert letzte is not None
    assert letzte["aktion"] == "markieren:\\Flagged:gesetzt"
    assert letzte["quell_ordner"] == "INBOX"
    assert letzte["ziel_ordner"] == "INBOX"
    assert letzte["eintraege"] == [
        {"message_id": "<a@example.com>", "uid_zum_zeitpunkt": "42", "betreff": ""}
    ]


def test_mails_markieren_no_journal_entry_when_nothing_succeeded(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_markieren("info", ["999"], "gelesen")

    assert ergebnis["markiert"] == 0
    assert action_journal.last_action("info") is None


# --- Rückgängig (letzte_aktion_rueckgaengig toggelt statt zu bewegen) -------


def test_letzte_aktion_rueckgaengig_toggles_flag_back_never_moves(mocker, tmp_path, monkeypatch):
    """KRITISCH: ein Rückgängig einer Markieren-Aktion darf NIE
    `mailbox.move` aufrufen — sonst wäre ein Move IN DENSELBEN Ordner
    riskiert (Duplikat/Datenverlust auf Servern ohne native MOVE-
    Capability, siehe `_rueckgaengig_markieren`-Docstring)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    # INBOX MUSS in der Ordnerliste stehen: _rueckgaengig_markieren loest den
    # Ordner aus dem Journal-Eintrag ueber _resolve_existing_folder auf und
    # verweigert das Zurueckstellen, wenn der Ordner nicht mehr existiert. Ein
    # echtes Postfach fuehrt INBOX immer (IMAP-Standard), der nackte Mock nicht.
    mock_mailbox.folder.list.return_value = [_fake_folder_info("INBOX")]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    markiert = chat_tools.mails_markieren("info", ["42"], "gelesen")
    assert markiert["markiert"] == 1

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert ergebnis["zurueckgestellt"] == 1
    assert ergebnis["nicht_gefunden"] == []
    mock_mailbox.move.assert_not_called()
    # Das GEGENTEILIGE Flag wird gesetzt: "gelesen" (=\Seen gesetzt) rückgängig
    # heisst \Seen ENTFERNEN.
    mock_mailbox.client.uid.assert_any_call("STORE", "42", "-FLAGS", "(\\Seen)")


def test_letzte_aktion_rueckgaengig_markieren_wichtig_reverses_to_unwichtig(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    # INBOX MUSS in der Ordnerliste stehen: _rueckgaengig_markieren loest den
    # Ordner aus dem Journal-Eintrag ueber _resolve_existing_folder auf und
    # verweigert das Zurueckstellen, wenn der Ordner nicht mehr existiert. Ein
    # echtes Postfach fuehrt INBOX immer (IMAP-Standard), der nackte Mock nicht.
    mock_mailbox.folder.list.return_value = [_fake_folder_info("INBOX")]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    chat_tools.mails_markieren("info", ["42"], "unwichtig")
    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert ergebnis["zurueckgestellt"] == 1
    mock_mailbox.client.uid.assert_any_call("STORE", "42", "+FLAGS", "(\\Flagged)")


def test_letzte_aktion_rueckgaengig_markieren_marks_action_undone_after_attempt(
    mocker, tmp_path, monkeypatch
):
    """Nach dem Versuch gilt die Markieren-Aktion im Journal als
    abgeschlossen — sonst würde jeder künftige Rückgängig-Versuch für immer
    an dieser einen Aktion hängen bleiben (analog dem Move-Pfad)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="42", mid="<a@example.com>")])
    # INBOX MUSS in der Ordnerliste stehen: _rueckgaengig_markieren loest den
    # Ordner aus dem Journal-Eintrag ueber _resolve_existing_folder auf und
    # verweigert das Zurueckstellen, wenn der Ordner nicht mehr existiert. Ein
    # echtes Postfach fuehrt INBOX immer (IMAP-Standard), der nackte Mock nicht.
    mock_mailbox.folder.list.return_value = [_fake_folder_info("INBOX")]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    chat_tools.mails_markieren("info", ["42"], "gelesen")
    erstes_ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")
    assert erstes_ergebnis["zurueckgestellt"] == 1

    zweites_ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")
    assert "fehler" in zweites_ergebnis


def test_letzte_aktion_rueckgaengig_markieren_reports_partial_success(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    eintraege = [
        {"uid": "1", "message_id": "<a@example.com>", "betreff": ""},
        {"uid": "2", "message_id": "<b@example.com>", "betreff": ""},
    ]
    action_journal.record_action("info", "markieren:\\Seen:gesetzt", "INBOX", "INBOX", eintraege)

    def _uids_side_effect(_mailbox, message_id, _folder):
        return ["1"] if message_id == "<a@example.com>" else []

    mocker.patch("src.chat_tools._uids_of_message_id_in_folder", side_effect=_uids_side_effect)

    mock_mailbox = MagicMock()
    mock_mailbox.__enter__ = MagicMock(return_value=mock_mailbox)
    mock_mailbox.__exit__ = MagicMock(return_value=False)
    mock_mailbox.folder.list.return_value = [_folder_info("INBOX", delim=".")]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert ergebnis["zurueckgestellt"] == 1
    assert len(ergebnis["nicht_gefunden"]) == 1
    mock_mailbox.move.assert_not_called()


def test_letzte_aktion_rueckgaengig_markieren_corrupted_aktion_string_returns_error(
    tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info", "markieren:kaputt", "INBOX", "INBOX", [{"uid": "1", "message_id": "<a@example.com>"}]
    )

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert "fehler" in ergebnis


def test_letzte_aktion_rueckgaengig_markieren_missing_folder_returns_error(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info",
        "markieren:\\Seen:gesetzt",
        "INBOX.Geloescht",
        "INBOX.Geloescht",
        [{"uid": "1", "message_id": "<a@example.com>"}],
    )

    mock_mailbox = MagicMock()
    mock_mailbox.__enter__ = MagicMock(return_value=mock_mailbox)
    mock_mailbox.__exit__ = MagicMock(return_value=False)
    mock_mailbox.folder.list.return_value = [_folder_info("INBOX", delim=".")]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert "fehler" in ergebnis
    assert "existiert" in ergebnis["fehler"]
    mock_mailbox.move.assert_not_called()


# --- Registrierung (13-PATTERNS.md Muster 1) ---------------------------------


def test_mails_markieren_is_fully_registered():
    import src.chat_tools as chat_tools

    assert "mails_markieren" in chat_tools.TOOL_HANDLERS
    assert chat_tools.TOOL_HANDLERS["mails_markieren"] is chat_tools.mails_markieren

    schema_names = {schema["name"] for schema in chat_tools.TOOL_SCHEMAS}
    assert "mails_markieren" in schema_names

    assert "mails_markieren" in chat_tools._TOOL_ACTIVITY_LABELS
    assert chat_tools._tool_activity_label("mails_markieren") != "Mails markieren…".replace(
        "…", ""
    )


def test_source_guard_no_mailbox_flag_call_stays_zero():
    """Der Waechter gegen `MailBox.flag()` gilt auch fuer `mails_markieren`.

    Die STRUKTURELLE Pruefung (kein echter `.flag(`-Aufruf irgendwo in
    `chat_tools.py`) liegt beim AST-Waechter
    `test_chat_expunge_disziplin.py::test_source_guard_no_mailbox_flag_call_in_chat_tools`
    und wird hier NICHT als Textsuche wiederholt — eine Textsuche schlaegt auch
    bei den Docstrings an, die erklaeren, warum `mailbox.flag()` gemieden wird.

    Dieser Test prueft stattdessen das VERHALTEN am Werkzeug selbst: die Funktion
    `mailbox.flag` wird beim Markieren nie aufgerufen, und im aufgezeichneten
    IMAP-Verkehr steht kein EXPUNGE.
    """
    quelle = Path(__file__).resolve().parent.parent / "src" / "chat_tools.py"
    baum = ast.parse(quelle.read_text(encoding="utf-8"))
    flag_aufrufe = [
        k.lineno
        for k in ast.walk(baum)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "flag"
    ]
    assert not flag_aufrufe, (
        f"Echter .flag(...)-Aufruf in chat_tools.py, Zeile(n) {flag_aufrufe} — "
        "MailBox.flag() haengt ein folder-weites expunge() an. Nutze _store_flags."
    )
