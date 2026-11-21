"""Regressionstest fuer Phase 13, Plan 13-01 (Befund A): `imap_tools.MailBox.flag()`
haengt laut installiertem imap-tools 1.13.0 (`mailbox.py:279-305`) nach JEDEM
`STORE` unbedingt ein folder-weites `self.expunge()` an — unabhaengig von
`value` und unabhaengig davon, ob ueberhaupt geloescht werden soll. Ein reines
Markieren wuerde damit ALLE `\\Deleted`-markierten, noch nicht expungten
Nachrichten anderer Clients im selben Ordner endgueltig loeschen.

Dieser Test deckt zwei Dinge ab:
1. Den neuen Helfer `_store_flags` (roher `UID STORE`-Befehl, kein
   Expunge-Nachlauf) isoliert.
2. Die beiden Aufrufstellen (`_move_message`-CR-05-Fallback,
   `papierkorb_leeren`), die `_store_flags` statt `MailBox.flag()` nutzen
   MUESSEN.

Ein Quell-Waechter (Test 6) verbietet die Zeichenkette `mailbox.flag(` im
Bestandscode strukturell, damit der Bug nicht unbemerkt zurueckkehrt (z.B.
durch ein neues Werkzeug wie `mails_markieren`, Welle 2)."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

from .test_chat_tools import _fake_folder_info, _fake_mailbox, _msg_with_mid, _setup_env, _write_agent_env


# --- _store_flags (isoliert) -------------------------------------------------


def test_store_flags_uses_raw_store_command_not_wrapper():
    """`_store_flags` setzt Flags per rohem `mailbox.client.uid('STORE', ...)`
    und ruft weder `mailbox.flag` noch `mailbox.expunge` auf."""
    import src.chat_tools as chat_tools

    mailbox = MagicMock()

    anzahl = chat_tools._store_flags(mailbox, ["1", "2"], "\\Deleted", True)

    assert anzahl == 2
    mailbox.client.uid.assert_called_once_with("STORE", "1,2", "+FLAGS", "(\\Deleted)")
    mailbox.flag.assert_not_called()
    mailbox.expunge.assert_not_called()


def test_store_flags_value_false_uses_minus_flags():
    """`value=False` entfernt das Flag über `-FLAGS`, nicht `+FLAGS`."""
    import src.chat_tools as chat_tools

    mailbox = MagicMock()

    anzahl = chat_tools._store_flags(mailbox, ["5"], "\\Seen", False)

    assert anzahl == 1
    mailbox.client.uid.assert_called_once_with("STORE", "5", "-FLAGS", "(\\Seen)")
    mailbox.flag.assert_not_called()
    mailbox.expunge.assert_not_called()


def test_store_flags_empty_uid_list_sends_no_command():
    """Leere uid-Liste ist ein No-Op — kein IMAP-Roundtrip, kein Kommando."""
    import src.chat_tools as chat_tools

    mailbox = MagicMock()

    anzahl = chat_tools._store_flags(mailbox, [], "\\Deleted", True)

    assert anzahl == 0
    mailbox.client.uid.assert_not_called()
    mailbox.flag.assert_not_called()
    mailbox.expunge.assert_not_called()


# --- CR-05-Fallback in _move_message -----------------------------------------


def test_move_message_cr05_fallback_uses_store_flags_not_wrapper_flag_or_folder_expunge():
    """Nachbildung des Live-IONOS-Symptoms (wie in `test_chat_tools.py`
    bereits für `_move_to_trash` gedeckt, hier für die generalisierte
    `_move_message`): `move()` hinterlässt die uid trotzdem im Quell-Ordner.
    Der Fallback setzt `\\Deleted` GEZIELT über `_store_flags` (roher STORE)
    und expunged NUR per `UID EXPUNGE <uid>` (UIDPLUS) — NIE über
    `MailBox.flag()`, NIE über ein folder-weites `mailbox.expunge()`."""
    import src.chat_tools as chat_tools

    mailbox = MagicMock()
    mailbox.client.capabilities = ("MOVE", "UIDPLUS")
    call_count = {"n": 0}

    def _fetch_side_effect(*_args, **_kwargs):
        call_count["n"] += 1
        # 1: Pre-Move-Fetch (Message-ID sichern) -> Nachricht da.
        # 2: Quell-Rest-Suche per Message-ID nach move() -> Nachricht NOCH da.
        # 3: Quell-uid-Verifikation nach move() -> uid NOCH da (IONOS-Bug).
        # 4: Zielordner-Ankunfts-Nachweis (Message-ID-Suche) -> gefunden.
        # 5+6: Quell-Verifikation nach gezieltem UID EXPUNGE (uid + Message-ID) -> leer.
        return [_msg_with_mid()] if call_count["n"] <= 4 else []

    mailbox.fetch.side_effect = _fetch_side_effect

    result = chat_tools._move_message(mailbox, "42", "INBOX", "INBOX.Kunden")

    assert result == ("INBOX.Kunden", "<orig-42@example.com>")
    mailbox.flag.assert_not_called()
    mailbox.expunge.assert_not_called()
    mailbox.client.uid.assert_any_call("STORE", "42", "+FLAGS", "(\\Deleted)")
    mailbox.client.uid.assert_any_call("EXPUNGE", "42")


# --- papierkorb_leeren --------------------------------------------------------


def test_papierkorb_leeren_calls_expunge_exactly_once_and_never_wrapper_flag(mocker, tmp_path, monkeypatch):
    """`papierkorb_leeren` markiert ALLE uids per `_store_flags` und expunged
    danach GENAU EINMAL, sichtbar und absichtlich (T-13-02, Disposition
    'accept') — `MailBox.flag()` kommt dabei nie zum Einsatz."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    mock_mailbox = _fake_mailbox([])
    mock_mailbox.folder.list.return_value = [_fake_folder_info("Papierkorb", flags=("\\Trash",))]
    mock_mailbox.uids.return_value = ["10", "11"]
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.papierkorb_leeren("info")

    assert result == {"geleert": True, "papierkorb": "Papierkorb", "anzahl": 2}
    mock_mailbox.flag.assert_not_called()
    mock_mailbox.client.uid.assert_called_once_with("STORE", "10,11", "+FLAGS", "(\\Deleted)")
    mock_mailbox.expunge.assert_called_once()


# --- Quell-Waechter -----------------------------------------------------------


def test_source_guard_no_mailbox_flag_call_in_chat_tools():
    """Struktureller Quell-Waechter (Phase 13, Plan 13-01): `MailBox.flag()`
    haengt laut imap-tools 1.13.0 nach jedem STORE unbedingt ein
    folder-weites `expunge()` an — ein neuer Aufruf von `.flag(...)` auf einem
    Mailbox-Objekt wuerde diesen Bug zurueckbringen. Nutze `_store_flags`.

    Die Pruefung laeuft ueber den AST, NICHT ueber eine Textsuche nach
    `"mailbox.flag("`. Grund (Befund bei der Ausfuehrung von Plan 13-04): eine
    Textsuche schlaegt auch bei DOKUMENTATION an — die Docstrings von
    `_store_flags` und `mails_markieren` erklaeren ausdruecklich, warum
    `mailbox.flag()` nicht verwendet wird, und liessen den Waechter faelschlich
    rot werden. Ein Waechter, der Erklaerungen ueber einen Fehler mit dem
    Fehler selbst verwechselt, erzieht dazu, die Begruendung aus dem Code zu
    entfernen — genau das Gegenteil des Gewollten. Der AST sieht nur echte
    Aufrufe.
    """
    quelle = Path(__file__).resolve().parent.parent / "src" / "chat_tools.py"
    baum = ast.parse(quelle.read_text(encoding="utf-8"))

    treffer = [
        knoten.lineno
        for knoten in ast.walk(baum)
        if isinstance(knoten, ast.Call)
        and isinstance(knoten.func, ast.Attribute)
        and knoten.func.attr == "flag"
    ]

    assert not treffer, (
        f"Echter Aufruf von .flag(...) in chat_tools.py, Zeile(n) {treffer} — das "
        "würde laut imap-tools 1.13.0 (mailbox.py:279-305) nach dem STORE ein "
        "folder-weites expunge() auslösen und fremde \\Deleted-markierte "
        "Nachrichten anderer Clients endgültig löschen (Befund Phase 13, Plan "
        "13-01). Nutze stattdessen _store_flags(mailbox, uids, flag_name, value)."
    )
