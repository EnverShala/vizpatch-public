"""Tests für `ordner_loeschen` (Quick 260812-wco): Schutzliste,
Nachfahren-Ermittlung, Flag `ENABLE_FOLDER_DELETE`, Mail-Rettung in den
Papierkorb, Rekursion, fail-closed bei unvollständigem Move,
Post-DELETE-Verifikation und Registry-Anbindung.

Deckt ab (Task 1):
1. `_folder_roles`: identische defensive Rollen-Extraktion wie
   `ordner_auflisten`, kein Crash ohne `flags`-Attribut.
2. `_protected_folder_reason`: alle neun SPECIAL-USE-Rollen, case-
   insensitiver Vergleich auf dem GANZEN Rollennamen (kein Substring-Treffer
   über `HasChildren`/`HasNoChildren`/`Noselect`/`Marked`/`Subscribed`),
   namensbasierter Schutz (INBOX + aufgelöste Entwürfe-/Gesendet-/
   Papierkorb-Grenzen), kein Treffer -> `None`.
3. `_descendant_folders`: tiefste Ebene zuerst, Ziel selbst nicht enthalten,
   Delimiter-Grenze Pflicht (kein Präfix-Fehltreffer).
4. `_folder_delete_allowed`: Default an, per-Agent/global `false`,
   per-Agent gewinnt über global, andere Werte bleiben an.

(Task 2/3 ergänzen `ordner_loeschen` selbst sowie Registry-/Schema-Tests in
dieser Datei.)
"""
from __future__ import annotations

import re
import types
from unittest.mock import MagicMock

import pytest

from .test_chat_tools import _fake_mailbox, _setup_env, _write_agent_env


def _folder_info(name, delim=".", flags=()):
    """Wie `test_chat_folder_tools._folder_info` — MIT `delim`-Attribut (die
    Bestandsfixture `_fake_folder_info` in `test_chat_tools.py` hat keins)."""
    import types

    return types.SimpleNamespace(name=name, delim=delim, flags=flags)


# --- _folder_roles ------------------------------------------------------------


def test_folder_roles_extracts_backslash_flags_without_leading_backslash():
    import src.chat_tools as chat_tools

    info = _folder_info("Trash", flags=("\\Trash", "\\HasNoChildren"))
    assert chat_tools._folder_roles(info) == ["Trash", "HasNoChildren"]


def test_folder_roles_missing_flags_attribute_returns_empty_list_no_crash():
    import types

    import src.chat_tools as chat_tools

    info = types.SimpleNamespace(name="INBOX")
    assert chat_tools._folder_roles(info) == []


def test_folder_roles_ignores_non_backslash_entries():
    import src.chat_tools as chat_tools

    info = _folder_info("Sonstiges", flags=("kein-flag", "\\Sent"))
    assert chat_tools._folder_roles(info) == ["Sent"]


# --- _protected_folder_reason: neun SPECIAL-USE-Rollen ------------------------


_PROTECTED_ROLE_CASES = [
    ("Inbox", "Posteingang"),
    ("Drafts", "Entwürfe"),
    ("Sent", "Gesendet"),
    ("Trash", "Papierkorb"),
    ("Junk", "Spam"),
    ("Archive", "Archiv"),
    ("All", "alle Nachrichten"),
    ("Flagged", "markierte Nachrichten"),
    ("Important", "wichtige Nachrichten"),
]


@pytest.mark.parametrize("role,expected_snippet", _PROTECTED_ROLE_CASES)
def test_protected_folder_reason_covers_all_nine_special_use_roles(role, expected_snippet):
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "Irgendein.Ordner",
        [role],
        inbox_folder="INBOX",
        drafts_folder="Entwürfe",
        sent_folder="Gesendet",
        trash_folder="Papierkorb",
    )
    assert reason is not None
    assert "Irgendein.Ordner" in reason
    assert f"SPECIAL-USE \\{role}" in reason
    assert "nie gelöscht" in reason


@pytest.mark.parametrize("role", ["inbox", "INBOX", "InBoX"])
def test_protected_folder_reason_role_comparison_is_case_insensitive(role):
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "Egal",
        [role],
        inbox_folder="INBOX",
        drafts_folder="",
        sent_folder="",
        trash_folder="",
    )
    assert reason is not None


@pytest.mark.parametrize(
    "role",
    ["HasChildren", "HasNoChildren", "Noselect", "Marked", "Subscribed"],
)
def test_protected_folder_reason_non_special_use_role_flags_do_not_trigger(role):
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "INBOX.Kunden",
        [role],
        inbox_folder="INBOX",
        drafts_folder="",
        sent_folder="",
        trash_folder="",
    )
    assert reason is None


def test_protected_folder_reason_role_match_is_whole_name_not_substring():
    """`Trashcan` enthält die Teilzeichenkette 'Trash', ist aber keine Rolle
    '\\Trash' — anders als `_detect_trash_folder`s bewusster Substring-
    Vergleich muss dieser Vergleich auf dem GANZEN Rollennamen laufen."""
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "INBOX.Kunden",
        ["Trashcan"],
        inbox_folder="INBOX",
        drafts_folder="",
        sent_folder="",
        trash_folder="",
    )
    assert reason is None


# --- _protected_folder_reason: namensbasiert ----------------------------------


def test_protected_folder_reason_namebased_inbox_case_insensitive():
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "inbox",
        [],
        inbox_folder="INBOX",
        drafts_folder="",
        sent_folder="",
        trash_folder="",
    )
    assert reason is not None
    assert "Posteingang" in reason
    assert "SPECIAL-USE" not in reason


def test_protected_folder_reason_namebased_matches_resolved_drafts_folder():
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "  KI-Entwürfe  ",
        [],
        inbox_folder="INBOX",
        drafts_folder="KI-Entwürfe",
        sent_folder="",
        trash_folder="",
    )
    assert reason is not None
    assert "Entwürfe" in reason


def test_protected_folder_reason_namebased_matches_resolved_sent_folder():
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "Gesendete Objekte",
        [],
        inbox_folder="INBOX",
        drafts_folder="",
        sent_folder="Gesendete Objekte",
        trash_folder="",
    )
    assert reason is not None


def test_protected_folder_reason_namebased_matches_resolved_trash_folder():
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "Papierkorb",
        [],
        inbox_folder="INBOX",
        drafts_folder="",
        sent_folder="",
        trash_folder="Papierkorb",
    )
    assert reason is not None


def test_protected_folder_reason_empty_bound_values_are_skipped_not_matched():
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "",
        [],
        inbox_folder="INBOX",
        drafts_folder="",
        sent_folder="",
        trash_folder="",
    )
    assert reason is None


def test_protected_folder_reason_harmless_folder_returns_none():
    import src.chat_tools as chat_tools

    reason = chat_tools._protected_folder_reason(
        "INBOX.Kunden",
        [],
        inbox_folder="INBOX",
        drafts_folder="Entwürfe",
        sent_folder="Gesendet",
        trash_folder="Papierkorb",
    )
    assert reason is None


# --- _descendant_folders -------------------------------------------------------


def test_descendant_folders_deepest_level_first_target_not_included():
    import src.chat_tools as chat_tools

    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Kunden"),
        _folder_info("INBOX.Kunden.2025"),
        _folder_info("INBOX.Kunden.2025.Q1"),
    ]
    result = chat_tools._descendant_folders(infos, "INBOX.Kunden", ".")

    assert result == ["INBOX.Kunden.2025.Q1", "INBOX.Kunden.2025"]
    assert "INBOX.Kunden" not in result


def test_descendant_folders_requires_delimiter_boundary():
    """`INBOX.KundenAlt` teilt sich das Präfix mit `INBOX.Kunden`, ist aber
    OHNE Delimiter-Grenze KEIN Nachfahre — sonst würde ein fremder Ordner
    mitgerissen."""
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX"), _folder_info("INBOX.Kunden"), _folder_info("INBOX.KundenAlt")]
    result = chat_tools._descendant_folders(infos, "INBOX.Kunden", ".")

    assert result == []


def test_descendant_folders_no_children_returns_empty_list():
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    assert chat_tools._descendant_folders(infos, "INBOX.Kunden", ".") == []


# --- _folder_delete_allowed -----------------------------------------------------


def test_folder_delete_allowed_default_true(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    assert chat_tools._folder_delete_allowed("info") is True


def test_folder_delete_allowed_per_agent_false(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    env = agents_io.read_env_raw("info")
    env["ENABLE_FOLDER_DELETE"] = "false"
    agents_io.write_env("info", env)

    assert chat_tools._folder_delete_allowed("info") is False


def test_folder_delete_allowed_global_false(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_FOLDER_DELETE", "false")
    import src.chat_tools as chat_tools

    assert chat_tools._folder_delete_allowed("info") is False


def test_folder_delete_allowed_per_agent_wins_over_global(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_FOLDER_DELETE", "false")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    env = agents_io.read_env_raw("info")
    env["ENABLE_FOLDER_DELETE"] = "true"
    agents_io.write_env("info", env)

    assert chat_tools._folder_delete_allowed("info") is True


@pytest.mark.parametrize("value", ["1", "yes", "FALSCH", ""])
def test_folder_delete_allowed_other_values_stay_true(value, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_FOLDER_DELETE", value)
    import src.chat_tools as chat_tools

    assert chat_tools._folder_delete_allowed("info") is True


@pytest.mark.parametrize("value", ["FALSE", " false "])
def test_folder_delete_allowed_false_case_and_whitespace_insensitive(value, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_FOLDER_DELETE", value)
    import src.chat_tools as chat_tools

    assert chat_tools._folder_delete_allowed("info") is False


# =============================================================================
# Task 2: ordner_loeschen — Mail-Rettung, Rekursion, fail-closed,
# Post-DELETE-Verifikation
# =============================================================================
#
# `_setup_ordner_loeschen` baut eine Mailbox-Fake, die die REALE Move-
# Mechanik (`_bulk_move_server_side`/`_move_message`) tatsächlich durchlaufen
# lässt statt sie zu mocken — `uids()` liefert je selektiertem Ordner die
# konfigurierten uids, `fetch(AND(uid=...))` simuliert Existenz VOR und
# Abwesenheit NACH einem erfolgreichen `move()` — je uid unabhängig (nicht
# global über `mailbox.move.called` wie `_fake_mailbox`, das würde
# Rekursions-Tests über mehrere Ordner verfälschen: der zweite Ordner würde
# sonst fälschlich schon als "verschoben" gelten, nur weil der erste es war).
# `missing_uids` simuliert bereits verschwundene uids für den fail-closed-Test
# (nie gefunden, unabhängig vom Move-Aufruf).


def _delete_msg(uid):
    return types.SimpleNamespace(uid=uid, headers={})


def _setup_ordner_loeschen(
    mocker,
    tmp_path,
    monkeypatch,
    folder_infos,
    *,
    uids_by_folder=None,
    exists_return=False,
    capabilities=("MOVE",),
    missing_uids=None,
    login_raises=False,
    list_raises=False,
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    uids_by_folder = uids_by_folder or {}
    missing_uids = {str(u) for u in (missing_uids or ())}
    moved_uids: set[str] = set()
    current_folder = {"name": None}

    mock_mailbox = MagicMock()
    mock_mailbox.__enter__ = MagicMock(return_value=mock_mailbox)
    mock_mailbox.__exit__ = MagicMock(return_value=False)
    if login_raises:
        mock_mailbox.login.side_effect = RuntimeError("auth failed")
    if list_raises:
        mock_mailbox.folder.list.side_effect = RuntimeError("list failed")
    else:
        mock_mailbox.folder.list.return_value = folder_infos
    mock_mailbox.folder.exists.return_value = exists_return
    mock_mailbox.client.capabilities = capabilities

    def _folder_set(name, *_a, **_kw):
        current_folder["name"] = name

    mock_mailbox.folder.set.side_effect = _folder_set

    def _uids(*_a, **_kw):
        return list(uids_by_folder.get(current_folder["name"], []))

    mock_mailbox.uids.side_effect = _uids

    def _move(uids, _target, **_kwargs):
        moved_uids.update(str(u) for u in uids)

    mock_mailbox.move.side_effect = _move

    def _fetch(*args, **_kwargs):
        if not args:
            return []
        qstr = str(args[0]).upper()
        if "UID" not in qstr or "HEADER" in qstr:
            # Message-ID-/Header-Suchen tragen hier keine Rolle — unsere
            # Fake-Nachrichten haben leere `headers`, `_message_id_of` liefert
            # ohnehin "" und überspringt jede Header-Suche.
            return []
        requested = re.findall(r"\d+", str(args[0]))
        present = [u for u in requested if u not in moved_uids and u not in missing_uids]
        return [_delete_msg(u) for u in present]

    mock_mailbox.fetch.side_effect = _fetch

    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    return mock_mailbox


_TRASH_ROLE = ("\\Trash",)


def test_ordner_loeschen_success_no_children_moves_mails_and_deletes(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Kunden"),
    ]
    mock_mailbox = _setup_ordner_loeschen(
        mocker, tmp_path, monkeypatch, infos, uids_by_folder={"INBOX.Kunden": ["1", "2", "3"]}
    )

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert result["geloescht"] is True
    assert result["ordner"] == "INBOX.Kunden"
    assert result["mails_in_papierkorb"] == 3
    assert result["papierkorb"] == "INBOX.Trash"
    assert result["ordner_geloescht"] == [{"ordner": "INBOX.Kunden", "mails": 3}]
    mock_mailbox.folder.delete.assert_called_once_with("INBOX.Kunden")
    mock_mailbox.folder.exists.assert_called_with("INBOX.Kunden")


def test_ordner_loeschen_recursion_deepest_first_sums_mail_count(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Kunden"),
        _folder_info("INBOX.Kunden.2025"),
        _folder_info("INBOX.Kunden.2025.Q1"),
    ]
    mock_mailbox = _setup_ordner_loeschen(
        mocker,
        tmp_path,
        monkeypatch,
        infos,
        uids_by_folder={
            "INBOX.Kunden": ["1"],
            "INBOX.Kunden.2025": ["2", "3"],
            "INBOX.Kunden.2025.Q1": ["4"],
        },
    )

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert result["geloescht"] is True
    assert result["mails_in_papierkorb"] == 4
    assert mock_mailbox.folder.delete.call_args_list == [
        mocker.call("INBOX.Kunden.2025.Q1"),
        mocker.call("INBOX.Kunden.2025"),
        mocker.call("INBOX.Kunden"),
    ]


@pytest.mark.parametrize(
    "role",
    ["Inbox", "Drafts", "Sent", "Trash", "Junk", "Archive", "All", "Flagged", "Important"],
)
def test_ordner_loeschen_refuses_every_special_use_role_as_target(role, mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Ziel", flags=(f"\\{role}",)),
    ]
    mock_mailbox = _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Ziel")

    assert "fehler" in result
    assert role in result["fehler"]
    mock_mailbox.folder.delete.assert_not_called()
    mock_mailbox.move.assert_not_called()


def test_ordner_loeschen_refuses_inbox_by_name_even_without_special_use(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX")]
    mock_mailbox = _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="INBOX")

    assert "fehler" in result
    assert "Posteingang" in result["fehler"]
    mock_mailbox.folder.delete.assert_not_called()
    mock_mailbox.move.assert_not_called()


def test_ordner_loeschen_protected_descendant_aborts_entire_operation(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Kunden"),
        _folder_info("INBOX.Kunden.Gesendet", flags=("\\Sent",)),
    ]
    mock_mailbox = _setup_ordner_loeschen(
        mocker,
        tmp_path,
        monkeypatch,
        infos,
        uids_by_folder={"INBOX.Kunden": ["1"], "INBOX.Kunden.Gesendet": ["2"]},
    )

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert "fehler" in result
    assert "Sent" in result["fehler"]
    mock_mailbox.folder.delete.assert_not_called()
    mock_mailbox.move.assert_not_called()


def test_ordner_loeschen_move_partial_failure_is_fail_closed(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Kunden"),
    ]
    mock_mailbox = _setup_ordner_loeschen(
        mocker,
        tmp_path,
        monkeypatch,
        infos,
        uids_by_folder={"INBOX.Kunden": ["1", "2", "3"]},
        missing_uids={"3"},
    )

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert "fehler" in result
    assert "2" in result["fehler"] and "3" in result["fehler"]
    mock_mailbox.folder.delete.assert_not_called()


def test_ordner_loeschen_trash_hierarchy_target_skips_move(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Trash.Alt"),
    ]
    mock_mailbox = _setup_ordner_loeschen(
        mocker, tmp_path, monkeypatch, infos, uids_by_folder={"INBOX.Trash.Alt": ["5", "6"]}
    )

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Trash.Alt")

    assert result["geloescht"] is True
    assert result["mails_in_papierkorb"] == 0
    assert result["hinweis"] is not None
    mock_mailbox.move.assert_not_called()
    mock_mailbox.folder.delete.assert_called_once_with("INBOX.Trash.Alt")


def test_ordner_loeschen_empty_folder_no_move_normal_delete(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Kunden"),
    ]
    mock_mailbox = _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos, uids_by_folder={})

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert result["geloescht"] is True
    assert result["mails_in_papierkorb"] == 0
    mock_mailbox.move.assert_not_called()
    mock_mailbox.folder.delete.assert_called_once_with("INBOX.Kunden")


def test_ordner_loeschen_post_delete_verification_failure_returns_error(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Kunden"),
    ]
    mock_mailbox = _setup_ordner_loeschen(
        mocker, tmp_path, monkeypatch, infos, uids_by_folder={}, exists_return=True
    )

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert "fehler" in result
    mock_mailbox.folder.delete.assert_called_once_with("INBOX.Kunden")


def test_ordner_loeschen_nonexistent_folder_returns_error_mentioning_ordner_auflisten(
    mocker, tmp_path, monkeypatch
):
    infos = [_folder_info("INBOX"), _folder_info("INBOX.Trash", flags=_TRASH_ROLE)]
    mock_mailbox = _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Nichtvorhanden")

    assert "fehler" in result
    assert "ordner_auflisten" in result["fehler"]
    mock_mailbox.folder.delete.assert_not_called()
    mock_mailbox.move.assert_not_called()


def test_ordner_loeschen_rejects_injection_attempt(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX"), _folder_info("INBOX.Trash", flags=_TRASH_ROLE)]
    mock_mailbox = _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="a\r\nb")

    assert "fehler" in result
    mock_mailbox.folder.delete.assert_not_called()
    mock_mailbox.move.assert_not_called()


def test_ordner_loeschen_unsubscribe_failure_still_success(mocker, tmp_path, monkeypatch):
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Trash", flags=_TRASH_ROLE),
        _folder_info("INBOX.Kunden"),
    ]
    mock_mailbox = _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos, uids_by_folder={})
    mock_mailbox.folder.subscribe.side_effect = RuntimeError("no UNSUBSCRIBE support")

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert result["geloescht"] is True


def test_ordner_loeschen_flag_off_refuses_without_any_imap_contact(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_FOLDER_DELETE", "false")
    mock_mailbox_cls = mocker.patch("src.chat_tools.MailBox")

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert "fehler" in result
    assert "ENABLE_FOLDER_DELETE" in result["fehler"]
    mock_mailbox_cls.assert_not_called()


def test_ordner_loeschen_no_trash_detected_and_target_not_in_trash_refuses(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    mock_mailbox = _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert "fehler" in result
    assert "Papierkorb" in result["fehler"]
    mock_mailbox.folder.delete.assert_not_called()
    mock_mailbox.move.assert_not_called()


def test_ordner_loeschen_list_failed_returns_error_dict_no_crash(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX")]
    _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos, list_raises=True)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert "fehler" in result


def test_ordner_loeschen_login_failure_returns_error_dict_no_crash(mocker, tmp_path, monkeypatch):
    infos = [_folder_info("INBOX")]
    _setup_ordner_loeschen(mocker, tmp_path, monkeypatch, infos, login_raises=True)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_loeschen("info", name="Kunden")

    assert "fehler" in result


def test_ordner_loeschen_invalid_agent_id_raises_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.ordner_loeschen("../evil", name="Kunden")


# =============================================================================
# Task 3d: Registry-/Filter-Tests (Muster test_ordner_erstellen_registered_everywhere)
# =============================================================================


def test_ordner_loeschen_registered_everywhere():
    import src.chat_tools as chat_tools

    assert "ordner_loeschen" in chat_tools.TOOL_HANDLERS
    assert chat_tools.TOOL_HANDLERS["ordner_loeschen"] is chat_tools.ordner_loeschen
    schema_names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "ordner_loeschen" in schema_names
    assert chat_tools._TOOL_ACTIVITY_LABELS.get("ordner_loeschen") == "Ordner löschen…"
    assert "ordner_loeschen" not in chat_tools._ANON_AWARE_TOOLS
    assert "ordner_loeschen" not in chat_tools._SESSION_SCOPED_TOOLS
    assert "ordner_loeschen" not in chat_tools._REQUEST_SCOPED_TOOLS


def test_ordner_loeschen_schema_has_only_name_property_no_confirmation():
    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "ordner_loeschen")

    assert set(schema["input_schema"]["properties"].keys()) == {"name"}
    assert schema["input_schema"]["required"] == ["name"]


def test_tool_schemas_for_contains_ordner_loeschen_by_default(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert "ordner_loeschen" in names


def test_tool_schemas_for_excludes_ordner_loeschen_when_disabled_per_agent(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    env = agents_io.read_env_raw("info")
    env["ENABLE_FOLDER_DELETE"] = "false"
    agents_io.write_env("info", env)

    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert "ordner_loeschen" not in names


def test_tool_schemas_for_excludes_ordner_loeschen_when_disabled_globally(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_FOLDER_DELETE", "false")
    import src.chat_tools as chat_tools

    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert "ordner_loeschen" not in names
