"""Streaming-Regressionstests fuer `_run_anthropic_tool_loop` (Quick 260816-0or,
T-0or-03). Nagelt die vier Risiken aus T-0or-02 fest, ueber die der Loop von
`client.messages.create(...)` auf `client.messages.stream(...)` umgestellt
wurde:

1. Text wird WAEHREND der Generierung geyieldet (inkrementelle Chunks), nicht
   erst nach vollstaendiger Runde.
2. Kein Doppel-Yield: der finale Text aus `get_final_message()` darf NICHT
   zusaetzlich zu den bereits gestreamten Chunks erscheinen.
3. De-Anonymisierung laeuft ueber `chat.deanonymize_stream` (chunk-
   uebergreifend) statt chunkweise `anonymizer.deanonymize` -- ein ueber zwei
   Chunks zerrissener Pseudonym-Tag darf NICHT roh durchrutschen.
4. Die Retry-Sperre nach bereits gesendetem Text (`text_emittiert`) UND die
   Gegenprobe (Retry weiterhin erlaubt, wenn der Fehler VOR dem ersten Chunk
   auftritt, also im `__enter__`).

Zusaetzlich ein Waechter, dass der Generator-Kontrakt nach aussen (nur
`text`/`tool`/`trace`/`bild`) unveraendert bleibt (VSTO-Add-in-SSE-Parser).

Aufbau analog `test_chat_attachment_vision.py`: `_setup_env`/`_write_agent_env`/
`_chat_system_prompt`/`_fake_response`/`_text_block`/`_tool_use_block` werden
aus `test_chat_tools` importiert (etabliertes Muster, jede Testdatei nutzt
dieselben Helfer statt sie zu duplizieren). Der Mock laeuft ueber
`as_streaming_client` (T-0or-01) -- `mock_client.messages.create.side_effect`
liefert weiterhin die Antwort-Objekte, `as_streaming_client` verpasst dem
Mock zusaetzlich ein streaming-faehiges `messages.stream(**kwargs)`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from .anthropic_stream_fake import as_streaming_client
from .test_chat_bild_vorschau import _vision_payload_with_preview
from .test_chat_tools import (
    _chat_system_prompt,
    _fake_response,
    _setup_env,
    _text_block,
    _tool_use_block,
    _write_agent_env,
)

IBAN = "DE89 3704 0044 0532 0130 00"


def test_tool_loop_streamt_text_inkrementell(mocker, tmp_path, monkeypatch):
    """Der eigentliche UX-Gewinn: der Betreiber sieht Text WAEHREND der
    Generierung, nicht erst nach der vollstaendigen Runde -- drei separate
    Chunks kommen als drei separate `text`-Events, in Reihenfolge."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response("end_turn", [_text_block("Ich loesche die Mails.")])
    round1._stream_chunks = ["Ich ", "loesche ", "die Mails."]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = round1
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    events = list(chat_tools.run_agentic_chat("info", "Loesche die alten Mails"))

    text_events = [e["text"] for e in events if e["type"] == "text"]
    assert text_events == ["Ich ", "loesche ", "die Mails."]
    assert "".join(text_events) == "Ich loesche die Mails."


def test_tool_loop_yieldet_finalen_text_nicht_doppelt(mocker, tmp_path, monkeypatch):
    """Traegt die Antwort SOWOHL `_stream_chunks` ALS AUCH den passenden
    `_text_block` in `content` (wie es beim echten SDK der Fall waere --
    `get_final_message()` akkumuliert dieselben Inhalte) -> der Gesamttext
    darf NUR EINMAL erscheinen, nicht zusaetzlich aus `content`."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    gesamt_text = "Ich loesche die Mails."
    round1 = _fake_response("end_turn", [_text_block(gesamt_text)])
    round1._stream_chunks = ["Ich ", "loesche ", "die Mails."]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = round1
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    events = list(chat_tools.run_agentic_chat("info", "Loesche die alten Mails"))

    zusammengesetzt = "".join(e["text"] for e in events if e["type"] == "text")
    # Vorkommen-Zaehlung statt reiner Gleichheit: belegt, dass der Text nicht
    # ein zweites Mal (z.B. angehaengt) im Gesamt-Output auftaucht.
    assert zusammengesetzt.count(gesamt_text) == 1
    assert zusammengesetzt == gesamt_text


def test_tool_loop_deanonymisiert_ueber_chunk_grenze(mocker, tmp_path, monkeypatch):
    """ENABLE_PII_REDACTION ist per Default an (CLAUDE.md). Der Pseudonym-Tag
    `[IBAN_1]` wird ueber zwei Stream-Chunks zerrissen -- im ausgelieferten
    Gesamttext MUSS die echte IBAN stehen, OHNE Rest-Fragmente `[IBAN`/`_1]`.
    Faellt garantiert um, wenn `deanonymize_stream` durch ein chunkweises
    `anonymizer.deanonymize(chunk)` ersetzt wird."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    # Die IBAN in der Betreiber-Nachricht wird beim Bau der Initial-Message
    # zu [IBAN_1] getaggt -- derselbe Anonymizer-Instanz-Tag, den das Modell
    # in seiner (gestreamten) Antwort zitiert.
    round1 = _fake_response("end_turn", [_text_block(f"Die IBAN {IBAN} stimmt.")])
    round1._stream_chunks = ["Die IBAN [IBAN", "_1] stimmt."]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = round1
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    events = list(chat_tools.run_agentic_chat("info", f"Meine IBAN ist {IBAN}, stimmt das?"))

    gesamt_text = "".join(e["text"] for e in events if e["type"] == "text")
    assert IBAN in gesamt_text
    assert "[IBAN" not in gesamt_text
    assert "_1]" not in gesamt_text


def test_tool_loop_kein_retry_nach_bereits_gesendetem_text(mocker, tmp_path, monkeypatch):
    """Vision-Bloecke koennen strukturell erst NACH einer ersten Tool-Runde in
    `messages` stehen (`_build_initial_messages` akzeptiert nur String-
    History, siehe Docstring) -- deshalb erzeugt Runde 1 hier den Vision-
    Block, Runde 2 ist die unter Test stehende Runde: sie streamt bereits
    Text UND wirft danach einen `APIStatusError` (Mitten-im-Stream-Fehler).
    Weil in DIESER Runde schon Text ausgeliefert wurde, darf KEIN Retry
    erfolgen -- `messages.create` wird fuer Runde 2 GENAU EINMAL aufgerufen
    (Gesamt-`call_count` ueber den Lauf: 2 = Runde 1 + der eine Versuch von
    Runde 2, siehe Abweichungsvermerk in SUMMARY.md)."""
    from anthropic import APIStatusError
    import httpx

    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"})],
    )
    round2 = _fake_response("end_turn", [_text_block("Text kommt durch.")])
    round2._stream_chunks = ["Text ", "kommt ", "durch."]
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(500, request=req)
    round2._stream_error = APIStatusError("mid-stream error", response=resp, body=None)

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload_with_preview()),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was zeigt das Bild?"))

    assert mock_client.messages.create.call_count == 2
    text_events = [e["text"] for e in events if e["type"] == "text"]
    assert "".join(text_events[:-1]) == "Text kommt durch."
    assert text_events[-1].startswith("[Fehler beim LLM-Aufruf")
    # Genau EIN Fehler-Event, nicht mehr.
    assert sum(1 for t in text_events if t.startswith("[Fehler beim LLM-Aufruf")) == 1


def test_tool_loop_retry_wenn_fehler_vor_erstem_chunk(mocker, tmp_path, monkeypatch):
    """Gegenprobe zu obigem Test: wirft die API den Fehler DIREKT beim
    Verbindungsaufbau (`__enter__`, vor jedem Chunk), bleibt die bestehende
    Fail-soft-Retry-Regel unveraendert aktiv -- Vision-Bloecke werden
    gestrippt, ein zweiter Versuch findet statt, KEIN Fehler-Event, KEINE
    zusaetzliche `MAX_TOOL_ROUNDS`-Runde wird verbraucht (Retry laeuft
    innerhalb derselben Runde)."""
    from anthropic import APIStatusError
    import httpx

    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"})],
    )
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req)
    api_error = APIStatusError("document block rejected", response=resp, body=None)
    round3 = _fake_response("end_turn", [_text_block("Ich konnte den Anhang nicht auswerten.")])

    mock_client = MagicMock()
    # api_error als side_effect-Eintrag: create() wirft ihn DIREKT (== der
    # reale Ablehnungsfall schlaegt beim `__enter__` zu, also VOR jedem Chunk).
    mock_client.messages.create.side_effect = [round1, api_error, round3]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload_with_preview()),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was zeigt das Bild?"))

    # Runde 1 (Tool-Use) + Runde 2 Versuch 1 (Fehler) + Runde 2 Versuch 2
    # (Retry, erfolgreich) = 3 Aufrufe insgesamt -- KEIN Fehler-Event, weil
    # der Retry noch innerhalb derselben Werkzeug-Runde erfolgt.
    assert mock_client.messages.create.call_count == 3
    text_events = [e["text"] for e in events if e["type"] == "text"]
    assert not any(t.startswith("[Fehler beim LLM-Aufruf") for t in text_events)
    assert "Ich konnte den Anhang nicht auswerten." in "".join(text_events)


def test_tool_loop_event_typen_unveraendert(mocker, tmp_path, monkeypatch):
    """Waechter gegen einen spaeteren, neuen Event-Typ (wuerde den VSTO-Add-
    in-SSE-Parser brechen, main.py::_stream). Ueber einen Durchlauf mit
    Tool-Runde + Vision-Anhang (Bild-Vorschau-Event) duerfen ausschliesslich
    die vier bekannten `type`-Werte vorkommen."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"})],
    )
    round2 = _fake_response("end_turn", [_text_block("Hier ist das Bild.")])
    round2._stream_chunks = ["Hier ", "ist ", "das Bild."]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload_with_preview()),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was zeigt das Bild?"))

    beobachtete_typen = {e["type"] for e in events}
    assert beobachtete_typen <= {"text", "tool", "trace", "bild"}
    # Mindestens EIN "bild"-Event muss aufgetreten sein, sonst waere der
    # Test ein Blindgaenger (prueft dann nur text/tool/trace).
    assert "bild" in beobachtete_typen
