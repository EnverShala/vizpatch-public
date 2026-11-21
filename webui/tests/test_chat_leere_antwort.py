"""Quick 260822-4ay, Befund 4 der Live-Abnahme (2026-08-20): eine LEERE
Chat-Antwort ist nie ein akzeptables Ergebnis.

Endete ein Zug ohne einen einzigen Text-Chunk — z.B. weil das Modell nach
einem Werkzeug-Fehler mit leerem Inhalt schloss —, kam beim Betreiber nichts
an: keine Antwort, keine Meldung, kein Hinweis, ob ueberhaupt etwas passiert
ist. Gerade beim IMAP-Fehler ist das der schlechteste Ausgang, weil genau dann
eine Aussage gebraucht wird.

Der Fix hat ZWEI Schichten und dieser Test prueft beide getrennt:
  1. `_run_anthropic_tool_loop` — kennt den letzten Werkzeug-Fehler und kann
     ihn benennen (agentischer Pfad).
  2. `main.py::_stream` — Netz fuer ALLE Generatoren hinter
     `run_agentic_chat`, insbesondere den werkzeuglosen Fallback-Chat.

Aufbau wie `test_chat_tool_loop_streaming.py` (dieselben Helfer aus
`test_chat_tools`, Streaming-Mock ueber `as_streaming_client`).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from .anthropic_stream_fake import as_streaming_client
from .test_chat_tools import (
    _chat_system_prompt,
    _fake_response,
    _setup_env,
    _text_block,
    _tool_use_block,
    _write_agent_env,
)


# --- Schicht 1: die Tool-Schleife selbst ------------------------------------


def test_textloser_zug_endet_mit_erklaerung_statt_stille(mocker, tmp_path, monkeypatch):
    """Das Modell schliesst mit LEEREM Inhalt (kein Text, kein tool_use) —
    vorher yieldete der Loop nichts und der Betreiber sah eine leere
    Sprechblase."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    leer = _fake_response("end_turn", [])
    leer._stream_chunks = []
    mock_client = MagicMock()
    mock_client.messages.create.return_value = leer
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    events = list(chat_tools.run_agentic_chat("info", "Zeig mir die Rechnungen"))

    texte = [e["text"] for e in events if e["type"] == "text"]
    assert texte, "ein Zug darf NIE ohne Text-Event enden"
    assert "ohne Antworttext" in texte[-1]


def test_textloser_zug_nennt_den_letzten_werkzeug_fehler(mocker, tmp_path, monkeypatch):
    """Der eigentliche Abnahme-Fall: ein Werkzeug scheitert am Postfach, das
    Modell schliesst danach ohne Text. Der Betreiber muss erfahren, WAS
    schiefging."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    runde1 = _fake_response("tool_use", [_tool_use_block("mails_suchen", {"query": "Rechnung"})])
    runde1._stream_chunks = []
    runde2 = _fake_response("end_turn", [])
    runde2._stream_chunks = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [runde1, runde2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mails_suchen",
        MagicMock(return_value={"fehler": "IMAP-Verbindung fehlgeschlagen."}),
    )

    events = list(chat_tools.run_agentic_chat("info", "Zeig mir die Rechnungen"))

    texte = [e["text"] for e in events if e["type"] == "text"]
    assert "IMAP-Verbindung fehlgeschlagen." in texte[-1]


def test_kein_zusatztext_wenn_das_modell_geantwortet_hat(mocker, tmp_path, monkeypatch):
    """Gegenprobe: der Abschluss-Text darf sich NICHT an eine normale Antwort
    haengen — sonst stuende unter jeder Antwort ein Hinweis."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    antwort = _fake_response("end_turn", [_text_block("Drei Rechnungen gefunden.")])
    antwort._stream_chunks = ["Drei Rechnungen gefunden."]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = antwort
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    events = list(chat_tools.run_agentic_chat("info", "Zeig mir die Rechnungen"))

    texte = [e["text"] for e in events if e["type"] == "text"]
    assert texte == ["Drei Rechnungen gefunden."]


def test_werkzeug_fehler_mit_antwort_bekommt_keinen_zusatztext(mocker, tmp_path, monkeypatch):
    """Auch mit einem Werkzeug-Fehler im Zug: solange das Modell antwortet,
    bleibt der Abschluss-Text weg."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    runde1 = _fake_response("tool_use", [_tool_use_block("mails_suchen", {"query": "Rechnung"})])
    runde1._stream_chunks = []
    runde2 = _fake_response("end_turn", [_text_block("Das Postfach antwortet gerade nicht.")])
    runde2._stream_chunks = ["Das Postfach antwortet gerade nicht."]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [runde1, runde2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mails_suchen",
        MagicMock(return_value={"fehler": "IMAP-Verbindung fehlgeschlagen."}),
    )

    events = list(chat_tools.run_agentic_chat("info", "Zeig mir die Rechnungen"))

    texte = [e["text"] for e in events if e["type"] == "text"]
    assert texte == ["Das Postfach antwortet gerade nicht."]


def test_abschlusstext_traegt_keinen_rohen_exception_text(mocker, tmp_path, monkeypatch):
    """Review WR-06 bleibt gewahrt: crasht ein Handler hart, geht nur die
    generische Handler-Meldung nach aussen, nie der Exception-Text (der koennte
    Hostnamen oder Server-Antworten enthalten)."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    runde1 = _fake_response("tool_use", [_tool_use_block("mails_suchen", {"query": "Rechnung"})])
    runde1._stream_chunks = []
    runde2 = _fake_response("end_turn", [])
    runde2._stream_chunks = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [runde1, runde2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mails_suchen",
        MagicMock(side_effect=Exception("imap.geheim-host.example:993 sagt NO LOGIN")),
    )

    events = list(chat_tools.run_agentic_chat("info", "Zeig mir die Rechnungen"))

    texte = " ".join(e["text"] for e in events if e["type"] == "text")
    assert "geheim-host" not in texte
    assert "mails_suchen" in texte  # generische Handler-Meldung


# --- Schicht 2: das Netz an der SSE-Grenze ----------------------------------
#
# Aufbau wie `test_endpoints_chat.py` (Fixture `authed_client` aus conftest —
# echte Session plus Origin-Header, sonst weist der CSRF-Schutz den POST mit
# 403 ab).


def test_sse_stream_liefert_text_auch_wenn_der_generator_stumm_bleibt(
    authed_client, mocker, tmp_path, monkeypatch
):
    """Zweite Schicht, unabhaengig von der Tool-Schleife geprueft: liefert
    `run_agentic_chat` ueberhaupt kein Text-Event (z.B. Fallback-Chat), schickt
    `_stream` selbst eine Meldung, bevor `event: done` rausgeht."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    mocker.patch("src.main.chat_tools.run_agentic_chat", return_value=iter([]))

    antwort = authed_client.post("/chat/info/send", data={"message": "Hallo"})

    assert antwort.status_code == 200
    assert "keine Antwort zustande gekommen" in antwort.text
    assert "event: done" in antwort.text


def test_sse_stream_haengt_nichts_an_eine_echte_antwort(
    authed_client, mocker, tmp_path, monkeypatch
):
    """Gegenprobe zur zweiten Schicht."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter([{"type": "text", "text": "Alles erledigt."}]),
    )

    antwort = authed_client.post("/chat/info/send", data={"message": "Hallo"})

    assert "Alles erledigt." in antwort.text
    assert "keine Antwort zustande gekommen" not in antwort.text


def test_sse_stream_meldet_nur_werkzeug_events_nicht_als_antwort(
    authed_client, mocker, tmp_path, monkeypatch
):
    """Ein Zug, der ausschliesslich Werkzeug-Aktivitaet zeigt, ist fuer den
    Betreiber genauso stumm wie gar kein Zug — auch hier muss eine Meldung
    kommen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    mocker.patch(
        "src.main.chat_tools.run_agentic_chat",
        return_value=iter([{"type": "tool", "label": "Suche im Postfach"}]),
    )

    antwort = authed_client.post("/chat/info/send", data={"message": "Hallo"})

    assert "keine Antwort zustande gekommen" in antwort.text
