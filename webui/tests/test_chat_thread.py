"""Tests für `thread_lesen` (Phase 13, Plan 13-06, MBOX-08).

Deckt ab:
1. Die Threading-Kette wird aus `References` UND `In-Reply-To` KOMBINIERT
   gesammelt — ein Vorgänger wird auch gefunden, wenn NUR `In-Reply-To`
   gesetzt ist (kein `References`-Header), 13-RESEARCH.md Abschnitt 7,
   Fallstrick 1.
2. Nachfolger (Antworten AUF die Ausgangsmail) werden über eine zusätzliche
   `References`-Suche gefunden, nicht nur Vorgänger.
3. Gesucht wird über mehrere Ordner (Startordner + aufgelöster Gesendet-
   Ordner), die Anzahl durchsuchter Ordner ist gedeckelt.
4. Ergebnis ist chronologisch sortiert, nennt je Mail den Ordner.
5. Alle Fetches laufen mit `mark_seen=False`.
6. Vollständige Registrierung (Schema/Handler/Anon-Aware/Activity-Label).
"""
from __future__ import annotations

import re
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

from .test_chat_tools import _msg, _setup_env, _write_agent_env


def _mid_header_value(criteria_str: str, header_name: str) -> str | None:
    """Extrahiert den Wert eines `HEADER "<name>" "<value>"`-IMAP-Kriteriums
    aus seiner String-Repräsentation (`str(AND(header=H(...)))`), damit der
    Fake-Fetch je nach gesuchtem Header/Wert unterschiedliche Treffer liefern
    kann — echtes IMAP filtert serverseitig, der Fake muss das clientseitig
    nachbilden, um die Aufrufreihenfolge des Werkzeugs realistisch zu prüfen."""
    m = re.search(rf'HEADER "{re.escape(header_name)}" "([^"]+)"', criteria_str)
    return m.group(1) if m else None


def _make_thread_mailbox(fetch_router, folder_infos=None):
    """Baut eine gemockte `MailBox` speziell für `thread_lesen`-Tests: verfolgt
    den zuletzt per `folder.set()` selektierten Ordner und routet jeden
    `fetch(criteria, ...)`-Aufruf über `fetch_router(ordner, criteria_str)` ->
    Liste von Fake-Nachrichten. Alle Aufrufe (Ordner, Kriterium, kwargs)
    werden in `mailbox._thread_test_calls` protokolliert, damit Tests z. B.
    `mark_seen=False` für JEDEN Aufruf nachweisen können."""
    mailbox = MagicMock()
    mailbox.__enter__ = MagicMock(return_value=mailbox)
    mailbox.__exit__ = MagicMock(return_value=False)
    mailbox.folder.list.return_value = folder_infos if folder_infos is not None else []

    current = {"name": None}

    def _set(name, *_a, **_kw):
        current["name"] = name

    mailbox.folder.set.side_effect = _set

    calls: list[dict] = []

    def _fetch(criteria, **kwargs):
        calls.append({"folder": current["name"], "criteria": str(criteria), "kwargs": kwargs})
        return fetch_router(current["name"], str(criteria))

    mailbox.fetch.side_effect = _fetch
    mailbox._thread_test_calls = calls
    return mailbox


def _sent_folder_info():
    """EIN Ordner mit SPECIAL-USE \\Sent, damit `_resolve_sent_folder`
    deterministisch (ohne Kandidatenraten) auf 'Gesendet' auflöst."""
    return types.SimpleNamespace(name="Gesendet", flags=("\\Sent",))


def _setup(mocker, tmp_path, monkeypatch, mailbox):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)


# --- Kette: References UND In-Reply-To -------------------------------------


def test_thread_lesen_findet_vorgaenger_nur_ueber_in_reply_to_ohne_references(
    mocker, tmp_path, monkeypatch
):
    """Kritischer Fallstrick (13-RESEARCH.md Abschnitt 7): manche Clients
    setzen NUR `In-Reply-To`, kein `References`. Die Kettensammlung muss
    trotzdem den Vorgänger finden."""
    start_msg = _msg(
        uid="10",
        subject="Re: Angebot",
        from_="kunde@example.com",
        headers={
            "message-id": ("<start@example.com>",),
            "references": "",
            "in-reply-to": ("<pred@example.com>",),
        },
    )
    pred_msg = _msg(
        uid="5",
        subject="Angebot",
        from_="kunde@example.com",
        headers={"message-id": ("<pred@example.com>",)},
    )

    def router(_folder, criteria_str):
        if criteria_str.startswith("(UID"):
            return [start_msg]
        mid = _mid_header_value(criteria_str, "Message-ID")
        if mid == "<start@example.com>":
            return [start_msg]
        if mid == "<pred@example.com>":
            return [pred_msg]
        return []

    mailbox = _make_thread_mailbox(router, folder_infos=[_sent_folder_info()])
    _setup(mocker, tmp_path, monkeypatch, mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.thread_lesen("info", uid="10", folder="INBOX")

    assert "fehler" not in result
    uids = {n["uid"] for n in result["nachrichten"]}
    assert uids == {"10", "5"}


def test_thread_lesen_kombiniert_references_und_in_reply_to(mocker, tmp_path, monkeypatch):
    """Auch wenn BEIDE Header gesetzt sind, müssen alle IDs aus `references`
    (mehrere, leerzeichengetrennt) UND `in-reply-to` gesammelt werden."""
    start_msg = _msg(
        uid="10",
        headers={
            "message-id": ("<start@example.com>",),
            "references": ("<a@example.com> <b@example.com>",),
            "in-reply-to": ("<b@example.com>",),
        },
    )
    msg_a = _msg(uid="1", headers={"message-id": ("<a@example.com>",)})
    msg_b = _msg(uid="2", headers={"message-id": ("<b@example.com>",)})

    def router(_folder, criteria_str):
        if criteria_str.startswith("(UID"):
            return [start_msg]
        mid = _mid_header_value(criteria_str, "Message-ID")
        return {
            "<start@example.com>": [start_msg],
            "<a@example.com>": [msg_a],
            "<b@example.com>": [msg_b],
        }.get(mid, [])

    mailbox = _make_thread_mailbox(router, folder_infos=[_sent_folder_info()])
    _setup(mocker, tmp_path, monkeypatch, mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.thread_lesen("info", uid="10", folder="INBOX")

    uids = {n["uid"] for n in result["nachrichten"]}
    assert uids == {"10", "1", "2"}


# --- Nachfolger --------------------------------------------------------------


def test_thread_lesen_findet_nachfolger_die_auf_ausgangsmail_referenzieren(
    mocker, tmp_path, monkeypatch
):
    """Eine reine Vorgänger-Suche (References/In-Reply-To DER Ausgangsmail)
    würde Antworten AUF die Ausgangsmail verpassen — die muss zusätzlich über
    eine `References`-Suche nach der eigenen Message-ID gefunden werden."""
    start_msg = _msg(
        uid="10",
        headers={"message-id": ("<start@example.com>",), "references": "", "in-reply-to": ""},
    )
    reply_msg = _msg(
        uid="20",
        subject="Re: Angebot",
        headers={
            "message-id": ("<reply@example.com>",),
            "references": ("<start@example.com>",),
        },
    )

    def router(_folder, criteria_str):
        if criteria_str.startswith("(UID"):
            return [start_msg]
        mid = _mid_header_value(criteria_str, "Message-ID")
        if mid == "<start@example.com>":
            return [start_msg]
        ref = _mid_header_value(criteria_str, "References")
        if ref == "<start@example.com>":
            return [reply_msg]
        return []

    mailbox = _make_thread_mailbox(router, folder_infos=[_sent_folder_info()])
    _setup(mocker, tmp_path, monkeypatch, mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.thread_lesen("info", uid="10", folder="INBOX")

    uids = {n["uid"] for n in result["nachrichten"]}
    assert uids == {"10", "20"}


# --- Mehrere Ordner (INBOX + Gesendet), Deckelung ---------------------------


def test_thread_lesen_durchsucht_inbox_und_aufgeloesten_gesendet_ordner(
    mocker, tmp_path, monkeypatch
):
    """Eine Konversation ist über Posteingang UND Gesendet verteilt — sonst
    fehlt die halbe Konversation (13-RESEARCH.md Abschnitt 7)."""
    start_msg = _msg(uid="10", headers={"message-id": ("<start@example.com>",)})
    sent_reply = _msg(
        uid="99",
        headers={"message-id": ("<antwort@example.com>",), "references": ("<start@example.com>",)},
    )

    def router(folder, criteria_str):
        if criteria_str.startswith("(UID"):
            return [start_msg] if folder == "INBOX" else []
        mid = _mid_header_value(criteria_str, "Message-ID")
        if mid == "<start@example.com>" and folder == "INBOX":
            return [start_msg]
        ref = _mid_header_value(criteria_str, "References")
        if ref == "<start@example.com>" and folder == "Gesendet":
            return [sent_reply]
        return []

    mailbox = _make_thread_mailbox(router, folder_infos=[_sent_folder_info()])
    _setup(mocker, tmp_path, monkeypatch, mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.thread_lesen("info", uid="10", folder="INBOX")

    gefundene = {(n["uid"], n["ordner"]) for n in result["nachrichten"]}
    assert ("10", "INBOX") in gefundene
    assert ("99", "Gesendet") in gefundene


def test_thread_search_folders_ist_gedeckelt(mocker, tmp_path, monkeypatch):
    """`_thread_search_folders` liefert nie mehr als `MAX_THREAD_SEARCH_FOLDERS`
    Ordner — Startordner + Gesendet-Ordner + INBOX-Fallback bleiben unter der
    Grenze, aber die Deckelung selbst ist strukturell nachgewiesen (`[:N]`)."""
    import src.chat_tools as chat_tools

    mailbox = MagicMock()
    mailbox.folder.list.return_value = [_sent_folder_info()]

    env = {"IMAP_USER": "info@ionos.de"}
    folders = chat_tools._thread_search_folders(mailbox, env, "INBOX")

    assert len(folders) <= chat_tools.MAX_THREAD_SEARCH_FOLDERS
    assert "INBOX" in folders
    assert "Gesendet" in folders


# --- Sortierung + mark_seen --------------------------------------------------


def test_thread_lesen_sortiert_chronologisch_und_nennt_ordner(mocker, tmp_path, monkeypatch):
    start_msg = _msg(uid="10", headers={"message-id": ("<start@example.com>",)})
    start_msg.date = datetime(2026, 1, 5, tzinfo=timezone.utc)
    pred_msg = _msg(
        uid="5",
        headers={"message-id": ("<pred@example.com>",)},
    )
    pred_msg.date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    start_msg.headers = {
        "message-id": ("<start@example.com>",),
        "in-reply-to": ("<pred@example.com>",),
    }

    def router(_folder, criteria_str):
        if criteria_str.startswith("(UID"):
            return [start_msg]
        mid = _mid_header_value(criteria_str, "Message-ID")
        return {"<start@example.com>": [start_msg], "<pred@example.com>": [pred_msg]}.get(mid, [])

    mailbox = _make_thread_mailbox(router, folder_infos=[_sent_folder_info()])
    _setup(mocker, tmp_path, monkeypatch, mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.thread_lesen("info", uid="10", folder="INBOX")

    reihenfolge = [n["uid"] for n in result["nachrichten"]]
    assert reihenfolge == ["5", "10"]
    assert all("ordner" in n for n in result["nachrichten"])


def test_thread_lesen_alle_fetches_mit_mark_seen_false(mocker, tmp_path, monkeypatch):
    start_msg = _msg(uid="10", headers={"message-id": ("<start@example.com>",)})

    def router(_folder, criteria_str):
        if criteria_str.startswith("(UID"):
            return [start_msg]
        return []

    mailbox = _make_thread_mailbox(router, folder_infos=[_sent_folder_info()])
    _setup(mocker, tmp_path, monkeypatch, mailbox)

    import src.chat_tools as chat_tools

    chat_tools.thread_lesen("info", uid="10", folder="INBOX")

    assert mailbox._thread_test_calls, "es sollten Fetch-Aufrufe protokolliert worden sein"
    for call in mailbox._thread_test_calls:
        assert call["kwargs"].get("mark_seen") is False


# --- Fehlerpfade (analog mail_lesen) ----------------------------------------


def test_thread_lesen_ohne_uid_liefert_fehler(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    mocker.patch("src.chat_tools.MailBox", return_value=MagicMock())

    import src.chat_tools as chat_tools

    result = chat_tools.thread_lesen("info", uid="")
    assert "fehler" in result


def test_thread_lesen_unbekannte_uid_liefert_fehler(mocker, tmp_path, monkeypatch):
    def router(_folder, _criteria_str):
        return []

    mailbox = _make_thread_mailbox(router, folder_infos=[_sent_folder_info()])
    _setup(mocker, tmp_path, monkeypatch, mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.thread_lesen("info", uid="999", folder="INBOX")
    assert "fehler" in result


# --- Registrierung (Muster 1, 13-PATTERNS.md) --------------------------------


def test_thread_lesen_ist_vollstaendig_registriert():
    import src.chat_tools as chat_tools

    assert "def thread_lesen" in open(chat_tools.__file__, encoding="utf-8").read()
    assert chat_tools.TOOL_HANDLERS["thread_lesen"] is chat_tools.thread_lesen
    assert "thread_lesen" in {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "thread_lesen" in chat_tools._ANON_AWARE_TOOLS
    assert chat_tools._tool_activity_label("thread_lesen") == "Thread lesen…"
