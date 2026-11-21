"""Tests für die serverseitige Chat-Verlaufs-Persistenz (Quick 260812-hhm,
Task 4): `chat_history`-Modul (SQLite, fail-soft) + die zwei neuen Endpoints
`GET /chat/{id}/history` / `POST /chat/{id}/history/reset` + die
Store-gewinnt-Präzedenz in `chat_send`.

Zusätzlich (Task 5, D-hhm-07/08): Verlaufs-Budget im agentischen Pfad
(`_build_initial_messages`/`_normalize_alternating_roles`) + kompaktes
Werkzeug-Transkript (`_tool_trace_line`, `trace`-Event) + Folge-Turn-Nachweis
über zwei `/send`-Aufrufe.
"""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from .anthropic_stream_fake import as_streaming_client


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


def _write_agent(agent_id, api_key="sk-openai-test-key", provider="openai"):
    import src.agents_io as agents_io

    agents_io.write_env(agent_id, {"LLM_API_KEY": api_key, "LLM_PROVIDER": provider})


@pytest.fixture(autouse=True)
def _chat_system_prompt_path(monkeypatch):
    real_path = Path(__file__).resolve().parent.parent / "prompts" / "chat-system.txt"
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(real_path))


# --- Modul-Tests: append_turns / load_history / clear_session / purge_expired --


def test_append_and_load_history_roundtrip(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    chat_history.append_turns(
        "info",
        "sess-1",
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hallo"}],
    )

    result = chat_history.load_history("info", "sess-1")

    assert result == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hallo"}]


def test_load_history_other_session_id_isolated(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    chat_history.append_turns("info", "sess-1", [{"role": "user", "content": "hi"}])

    assert chat_history.load_history("info", "sess-2") == []


def test_load_history_other_agent_id_isolated(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io
    import src.chat_history as chat_history

    agents_io.write_env("other-agent", {})
    chat_history.append_turns("info", "sess-1", [{"role": "user", "content": "hi"}])

    assert chat_history.load_history("other-agent", "sess-1") == []


def test_clear_session_removes_only_that_session(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    chat_history.append_turns("info", "sess-1", [{"role": "user", "content": "hi"}])
    chat_history.append_turns("info", "sess-2", [{"role": "user", "content": "other"}])

    geloescht = chat_history.clear_session("info", "sess-1")

    assert geloescht == 1
    assert chat_history.load_history("info", "sess-1") == []
    assert chat_history.load_history("info", "sess-2") == [{"role": "user", "content": "other"}]


def test_purge_expired_removes_old_turns_keeps_recent(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    conn = chat_history._connect()
    old_ts = "2000-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO chat_turns(agent_id, session_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("info", "sess-1", "user", "alt", old_ts),
    )
    conn.commit()
    conn.close()

    # append_turns ruft purge_expired() intern bereits auf (nach dem Schreiben
    # des neuen Turns) -- der explizite Aufruf unten prueft daher zusaetzlich,
    # dass ein direkter purge_expired()-Call idempotent 0 zurueckgibt (nichts
    # mehr uebrig zum Loeschen), waehrend der alte Turn bereits weg ist.
    chat_history.append_turns("info", "sess-1", [{"role": "assistant", "content": "neu"}])

    remaining = chat_history.load_history("info", "sess-1")
    assert remaining == [{"role": "assistant", "content": "neu"}]
    assert chat_history.purge_expired() == 0


def test_purge_expired_respects_custom_retention_days(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("CHAT_HISTORY_RETENTION_DAYS", "1")
    import src.chat_history as chat_history
    from datetime import datetime, timedelta, timezone

    conn = chat_history._connect()
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    conn.execute(
        "INSERT INTO chat_turns(agent_id, session_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("info", "sess-1", "user", "alt", two_days_ago),
    )
    conn.commit()
    conn.close()

    removed = chat_history.purge_expired()

    assert removed == 1


def test_append_turns_invalid_agent_id_no_write_no_exception(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    result = chat_history.append_turns("../evil", "sess-1", [{"role": "user", "content": "hi"}])

    assert result is False
    assert chat_history.load_history("../evil", "sess-1") == []


def test_append_turns_invalid_session_id_no_write(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    result = chat_history.append_turns("info", "sess with spaces!", [{"role": "user", "content": "hi"}])

    assert result is False


def test_load_history_invalid_ids_returns_empty_no_exception(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    assert chat_history.load_history("../evil", "sess-1") == []
    assert chat_history.load_history("info", "sess with spaces!") == []


def test_clear_session_invalid_ids_returns_zero(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    assert chat_history.clear_session("../evil", "sess-1") == 0


def test_append_turns_role_filtered_out(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    chat_history.append_turns(
        "info",
        "sess-1",
        [{"role": "system", "content": "invalid role"}, {"role": "user", "content": "ok"}],
    )

    result = chat_history.load_history("info", "sess-1")
    assert result == [{"role": "user", "content": "ok"}]


def test_append_turns_content_capped_at_max_turn_chars(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    long_content = "x" * (chat_history.MAX_TURN_CHARS + 500)
    chat_history.append_turns("info", "sess-1", [{"role": "user", "content": long_content}])

    result = chat_history.load_history("info", "sess-1")
    assert len(result[0]["content"]) == chat_history.MAX_TURN_CHARS


def test_load_history_caps_at_max_turns_per_session_keeps_newest(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    monkeypatch.setattr(chat_history, "MAX_TURNS_PER_SESSION", 3)
    for i in range(5):
        chat_history.append_turns("info", "sess-1", [{"role": "user", "content": str(i)}])

    result = chat_history.load_history("info", "sess-1")

    assert [t["content"] for t in result] == ["2", "3", "4"]


def test_chat_history_fail_soft_on_unwritable_data_root(tmp_path, monkeypatch, caplog):
    """Nicht beschreibbares WEBUI_DATA_ROOT -> logger.warning + neutrales
    Ergebnis, KEINE Exception (D-hhm-09)."""
    # Ein Pfad, dessen Elternverzeichnis eine DATEI (statt eines Verzeichnisses)
    # ist, kann nie als Verzeichnis angelegt werden -> mkdir schlaegt fehl.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(blocker))

    import src.chat_history as chat_history

    assert chat_history.load_history("info", "sess-1") == []
    assert chat_history.append_turns("info", "sess-1", [{"role": "user", "content": "hi"}]) is False
    assert chat_history.clear_session("info", "sess-1") == 0
    assert chat_history.purge_expired() == 0


def test_db_path_uses_webui_subdirectory(tmp_path, monkeypatch):
    """D-hhm-09/D-hhm-11: eigenes Unterverzeichnis /data/webui/ -- keine
    Kollision mit /data/agents/<id>/state.db (Agent-DB, UID 1000)."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_history as chat_history

    chat_history.append_turns("info", "sess-1", [{"role": "user", "content": "hi"}])

    assert chat_history._db_path() == tmp_path / "data" / "webui" / "chat_history.db"
    assert chat_history._db_path().exists()


# --- Endpoint-Tests: GET/POST /chat/{id}/history[/reset] -----------------------


def test_history_endpoint_returns_persisted_turns(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")
    import src.chat_history as chat_history

    chat_history.append_turns(
        "info", "sess-1", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hallo"}]
    )

    response = authed_client.get("/chat/info/history?session_id=sess-1", auth=("admin", "pw"))

    assert response.status_code == 200
    assert response.json() == {
        "turns": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hallo"}]
    }


def test_history_endpoint_missing_session_id_returns_empty_list(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")

    response = authed_client.get("/chat/info/history", auth=("admin", "pw"))

    assert response.status_code == 200
    assert response.json() == {"turns": []}


def test_history_endpoint_unknown_agent_returns_404(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    response = authed_client.get("/chat/ghost/history?session_id=sess-1", auth=("admin", "pw"))

    assert response.status_code == 404


def test_history_endpoint_without_session_cookie_returns_401(pw_set_client, tmp_path, monkeypatch):
    """Passwort gesetzt, aber KEINE gueltige Session: die Session-Gate-
    Middleware (`enforce_auth`) leitet volle Seiten-GETs auf /login um (303),
    HTMX-/API-Anfragen (erkennbar am `HX-Request`-Header, das Muster, das
    auch `chat.js`-Fetches ueber HTMX-Infrastruktur nutzen) erhalten 401 --
    dasselbe Verhalten wie jede andere gesicherte Route."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")

    response = pw_set_client.get(
        "/chat/info/history?session_id=sess-1", headers={"HX-Request": "true"}
    )

    assert response.status_code == 401


def test_history_reset_endpoint_deletes_and_returns_count(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")
    import src.chat_history as chat_history

    chat_history.append_turns("info", "sess-1", [{"role": "user", "content": "hi"}])

    response = authed_client.post(
        "/chat/info/history/reset", auth=("admin", "pw"), data={"session_id": "sess-1"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "geloescht": 1}
    assert chat_history.load_history("info", "sess-1") == []


def test_history_reset_endpoint_unknown_agent_returns_404(authed_client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    response = authed_client.post(
        "/chat/ghost/history/reset", auth=("admin", "pw"), data={"session_id": "sess-1"}
    )

    assert response.status_code == 404


# --- Store-gewinnt-Praezedenz + Persistenz nach fehlerfreiem Stream ------------


def test_chat_send_persists_turns_after_successful_stream(authed_client, mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")
    import src.chat_history as chat_history

    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter([{"type": "text", "text": "Antwort."}]),
    )

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Hallo", "session_id": "sess-persist"},
    )

    assert response.status_code == 200
    stored = chat_history.load_history("info", "sess-persist")
    assert stored == [
        {"role": "user", "content": "Hallo"},
        {"role": "assistant", "content": "Antwort."},
    ]


def test_chat_send_does_not_persist_on_stream_error(authed_client, mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")
    import src.chat_history as chat_history

    def _raising_gen():
        yield {"type": "text", "text": "Teil-Antwort"}
        raise RuntimeError("boom")

    mocker.patch("src.main.chat_tools.run_agentic_chat", return_value=_raising_gen())

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Hallo", "session_id": "sess-error"},
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert chat_history.load_history("info", "sess-error") == []


def test_chat_send_store_wins_over_history_formfield(authed_client, mocker, tmp_path, monkeypatch):
    """Store gewinnt gegen das history-Formfeld (Reload-Simulation: zweiter
    /send mit derselben session_id, OHNE history-Formfeld — der zweite Aufruf
    erhaelt den ersten Turn ueber den Store)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")

    mock_run = mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter([{"type": "text", "text": "Antwort 1."}]),
    )
    first = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Erste Nachricht", "session_id": "sess-reload"},
    )
    assert first.status_code == 200

    mock_run.return_value = iter([{"type": "text", "text": "Antwort 2."}])
    second = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Zweite Nachricht", "session_id": "sess-reload"},
    )
    assert second.status_code == 200

    # Zweiter Aufruf: die an run_agentic_chat uebergebene history (2. Positionsargument)
    # enthaelt den ERSTEN Turn (aus dem Store), obwohl kein history-Formfeld
    # mitgeschickt wurde.
    second_call_args = mock_run.call_args_list[1]
    passed_history = second_call_args.args[2] if len(second_call_args.args) > 2 else second_call_args.kwargs.get("history")
    assert any(t.get("content") == "Erste Nachricht" for t in passed_history)


def test_chat_send_empty_store_falls_back_to_history_formfield(authed_client, mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")
    import json

    mock_run = mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter([{"type": "text", "text": "Ok."}]),
    )
    formfield_history = json.dumps([{"role": "user", "content": "aus dem Formfeld"}])

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Hi", "session_id": "sess-no-store-yet", "history": formfield_history},
    )

    assert response.status_code == 200
    call_args = mock_run.call_args_list[0]
    passed_history = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("history")
    assert any(t.get("content") == "aus dem Formfeld" for t in passed_history)


# --- Task 5 (D-hhm-07): Verlaufs-Budget im agentischen Pfad -------------------


def test_normalize_alternating_roles_drops_leading_assistant(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    turns = [
        {"role": "assistant", "content": "verwaist (zugehöriger user-Turn wurde getrimmt)"},
        {"role": "user", "content": "Frage"},
        {"role": "assistant", "content": "Antwort"},
    ]

    result = chat_tools._normalize_alternating_roles(turns)

    assert result[0]["role"] == "user"
    assert result == [
        {"role": "user", "content": "Frage"},
        {"role": "assistant", "content": "Antwort"},
    ]


def test_normalize_alternating_roles_merges_consecutive_same_role(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    turns = [
        {"role": "user", "content": "Erste Frage"},
        {"role": "user", "content": "Zweite Frage"},
        {"role": "assistant", "content": "Antwort"},
    ]

    result = chat_tools._normalize_alternating_roles(turns)

    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "Erste Frage\n\nZweite Frage"}
    assert result[1] == {"role": "assistant", "content": "Antwort"}


def test_normalize_alternating_roles_empty_list():
    import src.chat_tools as chat_tools

    assert chat_tools._normalize_alternating_roles([]) == []


def test_build_initial_messages_trims_oldest_turns_beyond_budget(tmp_path, monkeypatch):
    """D-hhm-07: vorher wurde `history` in `_build_initial_messages` GAR NICHT
    getrimmt — mit einem kleinen Budget müssen die älteren Turns jetzt fehlen,
    der jüngste (user-)Turn und die aktuelle Nachricht bleiben. Der jüngste
    Turn ist hier bewusst ein `user`-Turn (realistischer Verlaufs-Ausschnitt,
    Turns kommen paarweise) — ein einzelner ÜBERLEBENDER `assistant`-Turn
    wäre nach der Rollen-Normalisierung (D-hhm-07, API verlangt `user` als
    ersten Turn) korrekt verworfen, siehe
    `test_normalize_alternating_roles_drops_leading_assistant`."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("CHAT_HISTORY_TOKEN_BUDGET", "10")
    import src.chat_tools as chat_tools

    history = [
        {"role": "user", "content": "x" * 400},  # alter Turn, ~100 Tokens -> muss weichen
        {"role": "assistant", "content": "y" * 400},  # muss ebenfalls weichen
        {"role": "user", "content": "kurz"},  # jüngster Turn, wenige Tokens -> bleibt
    ]

    messages = chat_tools._build_initial_messages(history, "Aktuelle Frage", None)

    joined = " ".join(str(m.get("content")) for m in messages)
    assert "x" * 400 not in joined
    assert "y" * 400 not in joined
    assert "kurz" in joined
    assert "Aktuelle Frage" in joined


def test_build_initial_messages_result_starts_with_user_after_trim(tmp_path, monkeypatch):
    """Nach der Trimmung beginnt die Message-Liste NIE mit einem
    assistant-Turn (die Anthropic-API lehnt das ab)."""
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setenv("CHAT_HISTORY_TOKEN_BUDGET", "5")
    import src.chat_tools as chat_tools

    history = [
        {"role": "user", "content": "x" * 400},
        {"role": "assistant", "content": "y" * 400},
        {"role": "user", "content": "kurz"},
    ]

    messages = chat_tools._build_initial_messages(history, "Aktuelle Frage", None)

    assert messages[0]["role"] == "user"


def test_build_initial_messages_no_history_unaffected(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    messages = chat_tools._build_initial_messages(None, "Hallo", None)

    assert messages == [{"role": "user", "content": "Hallo"}]


# --- Task 5 (D-hhm-08): Werkzeug-Transkript (_tool_trace_line) ----------------


def test_tool_trace_line_builds_compact_summary():
    import src.chat_tools as chat_tools

    line = chat_tools._tool_trace_line(
        "mail_verschieben",
        {"verschoben": True, "uid": "42", "quell_ordner": "INBOX", "ziel_ordner": "Rechnungen"},
    )

    assert line.startswith("mail_verschieben:")
    assert "uid=42" in line
    assert "quell_ordner=INBOX" in line
    assert "ziel_ordner=Rechnungen" in line
    assert "verschoben=True" in line


def test_tool_trace_line_excludes_body_and_text_fields():
    """D-hhm-08: NIE body_redigiert/text_redigiert/text im Trace — auch wenn
    das Handler-Ergebnis sie enthält."""
    import src.chat_tools as chat_tools

    payload = {
        "uid": "42",
        "betreff": "Rechnung Nr. 123",
        "body_redigiert": "Das ist der volle Mailtext, der NIE im Trace stehen darf.",
        "text_redigiert": "Ebenfalls voller Text.",
        "text": "Und noch mehr Text.",
    }

    line = chat_tools._tool_trace_line("mail_lesen", payload)

    assert "body_redigiert" not in line
    assert "text_redigiert" not in line
    assert "voller Mailtext" not in line
    assert "Ebenfalls voller Text" not in line
    assert "Und noch mehr Text" not in line
    assert "uid=42" in line


def test_tool_trace_line_treffer_list_capped_at_ten():
    import src.chat_tools as chat_tools

    treffer = [{"uid": str(i), "betreff": f"Betreff {i}"} for i in range(15)]
    line = chat_tools._tool_trace_line("mails_suchen", {"treffer": treffer, "anzahl": 15})

    assert line.count(":Betreff") <= chat_tools._TOOL_TRACE_MAX_TREFFER
    assert "anzahl=15" in line


def test_tool_trace_line_empty_for_unrecognized_payload():
    import src.chat_tools as chat_tools

    assert chat_tools._tool_trace_line("irgendwas", {"nichts_bekanntes": "x"}) == ""
    assert chat_tools._tool_trace_line("irgendwas", {}) == ""
    assert chat_tools._tool_trace_line("irgendwas", "kein dict") == ""


def test_tool_trace_line_caps_at_max_chars():
    import src.chat_tools as chat_tools

    treffer = [{"uid": str(i), "betreff": "x" * 60} for i in range(10)]
    line = chat_tools._tool_trace_line("mails_suchen", {"treffer": treffer})

    assert len(line) <= chat_tools.MAX_TOOL_TRACE_CHARS


def test_tool_trace_line_anhaenge_lists_only_filenames():
    import src.chat_tools as chat_tools

    line = chat_tools._tool_trace_line(
        "mail_anhaenge_auflisten",
        {"anzahl": 2, "anhaenge": [{"dateiname": "a.txt", "typ": "text/plain", "groesse": 5}]},
    )

    assert "a.txt" in line
    assert "text/plain" not in line


# --- Task 5: trace-Event ueber _run_anthropic_tool_loop + Persistenz ----------


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_use_block(name, tool_input, tool_id="toolu_1"):
    return types.SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def _fake_llm_response(stop_reason, content):
    return types.SimpleNamespace(stop_reason=stop_reason, content=content)


def test_run_agentic_chat_yields_trace_event_for_tool_call(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info", provider="anthropic")
    import src.chat_tools as chat_tools

    round1 = _fake_llm_response(
        "tool_use", [_tool_use_block("mails_suchen", {"query": "Rechnung"})]
    )
    round2 = _fake_llm_response("end_turn", [_text_block("Ich habe 1 Mail gefunden.")])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mails_suchen",
        MagicMock(return_value={"anzahl": 1, "treffer": [{"uid": "42", "betreff": "Rechnung"}]}),
    )

    events = list(chat_tools.run_agentic_chat("info", "Suche bitte nach Rechnung"))

    trace_events = [e for e in events if e.get("type") == "trace"]
    assert len(trace_events) == 1
    assert "42" in trace_events[0]["text"]


def test_chat_send_does_not_stream_trace_as_sse_text(authed_client, mocker, tmp_path, monkeypatch):
    """main.py::_stream MUSS trace-Events abfangen -- ohne den `elif`-Zweig
    wuerden sie als rohes SSE-`data:`-Frame in den Chat-Browser gestreamt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")

    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter(
            [
                {"type": "trace", "text": "mails_suchen: uid=42, betreff=Rechnung"},
                {"type": "text", "text": "Antwort."},
            ]
        ),
    )

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Suche", "session_id": "sess-trace"},
    )

    assert response.status_code == 200
    assert "mails_suchen: uid=42" not in response.text
    assert "data: Antwort." in response.text


def test_chat_send_persists_trace_as_werkzeug_protokoll_block(authed_client, mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")
    import src.chat_history as chat_history

    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter(
            [
                {"type": "trace", "text": "mails_suchen: uid=42, betreff=Rechnung"},
                {"type": "text", "text": "Ich habe die Mail gefunden."},
            ]
        ),
    )

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Suche", "session_id": "sess-trace-persist"},
    )
    assert response.status_code == 200

    stored = chat_history.load_history("info", "sess-trace-persist")
    assistant_turn = stored[-1]
    assert "Werkzeug-Protokoll dieses Zuges" in assistant_turn["content"]
    assert "uid=42" in assistant_turn["content"]
    assert "Ich habe die Mail gefunden." in assistant_turn["content"]


def test_chat_send_no_trace_no_werkzeug_protokoll_block(authed_client, mocker, tmp_path, monkeypatch):
    """Kein Werkzeugaufruf -> kein Werkzeug-Protokoll-Block im gespeicherten
    Turn (unveränderte Form)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")
    import src.chat_history as chat_history

    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter([{"type": "text", "text": "Einfache Antwort ohne Werkzeug."}]),
    )

    response = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Hallo", "session_id": "sess-no-trace"},
    )
    assert response.status_code == 200

    stored = chat_history.load_history("info", "sess-no-trace")
    assert "Werkzeug-Protokoll" not in stored[-1]["content"]


def test_folgeturn_knows_uid_and_ordner_from_previous_tool_call(authed_client, mocker, tmp_path, monkeypatch):
    """Folge-Turn-Nachweis: `/send` mit Werkzeugaufruf, dann zweiter `/send`
    mit derselben `session_id` — die an `run_agentic_chat` übergebene
    `history` enthält die uid/den Ordner aus dem ersten Zug (über das
    persistierte Werkzeug-Protokoll)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent("info")

    mock_run = mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter(
            [
                {"type": "trace", "text": "mails_suchen: uid=42, ordner=INBOX, betreff=Rechnung"},
                {"type": "text", "text": "Ich habe die Mail mit uid 42 gefunden."},
            ]
        ),
    )
    first = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Suche die Rechnung", "session_id": "sess-followup"},
    )
    assert first.status_code == 200

    mock_run.return_value = iter([{"type": "text", "text": "Verschoben."}])
    second = authed_client.post(
        "/chat/info/send",
        auth=("admin", "pw"),
        data={"message": "Verschieb genau diese Mail nach Rechnungen", "session_id": "sess-followup"},
    )
    assert second.status_code == 200

    second_call_args = mock_run.call_args_list[1]
    passed_history = (
        second_call_args.args[2] if len(second_call_args.args) > 2 else second_call_args.kwargs.get("history")
    )
    joined = " ".join(str(t.get("content")) for t in passed_history)
    assert "uid=42" in joined
    assert "ordner=INBOX" in joined
