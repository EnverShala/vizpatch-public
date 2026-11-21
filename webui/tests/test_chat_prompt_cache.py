"""Cache-Budget- und Guard-Tests fuer das Prompt-Caching aus T-0or-04 (Quick
260816-0or, T-0or-05).

Ohne diese Tests waere die 400-Falle aus T-0or-04 nur ein Kommentar: die
Anthropic-API erlaubt insgesamt HOECHSTENS `MAX_CACHE_BREAKPOINTS_TOTAL` (4)
`cache_control`-Breakpoints pro Request -- ein fuenfter weist den GESAMTEN
Request ab, nicht nur den ueberzaehligen Block. Der wichtigste Test
(`test_gesamtbudget_ueberschreitet_nie_vier_breakpoints`) zaehlt deshalb ALLE
Vorkommen ueber `system` + `tools` + `messages` (rekursiv, auch verschachtelt)
in einem einzigen Request.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from .anthropic_stream_fake import as_streaming_client
from .test_chat_attachment_vision import _vision_payload
from .test_chat_tools import (
    _chat_system_prompt,
    _fake_response,
    _scan_tool_schemas_for_forbidden_send_patterns,
    _setup_env,
    _text_block,
    _tool_use_block,
    _write_agent_env,
)


def _count_cache_control_breakpoints(obj) -> int:
    """Zaehlt ALLE `cache_control`-Vorkommen rekursiv -- egal ob auf dem
    System-Textblock, dem letzten Tool-Schema oder einem verschachtelten
    `tool_result`-Content-Eintrag in `messages`."""
    count = 0
    if isinstance(obj, dict):
        if "cache_control" in obj:
            count += 1
        for value in obj.values():
            count += _count_cache_control_breakpoints(value)
    elif isinstance(obj, list):
        for item in obj:
            count += _count_cache_control_breakpoints(item)
    return count


def _fuenf_vision_anhaenge_lauf(mocker, tmp_path, monkeypatch):
    """Gemeinsamer Aufbau fuer Tests 1 + 6: fuenf `mail_anhang_lesen`-Aufrufe
    mit Vision-Bloecken in Runde 1, ein abschliessender Text in Runde 2.
    Liefert den `mock_client` zurueck, damit die Tests ihre jeweiligen
    `call_args_list`-Eintraege pruefen koennen."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    tool_blocks = [
        _tool_use_block(
            "mail_anhang_lesen",
            {"uid": "42", "dateiname_oder_index": f"foto{i}.jpg"},
            tool_id=f"toolu_{i}",
        )
        for i in range(5)
    ]
    round1 = _fake_response("tool_use", tool_blocks)
    round2 = _fake_response("end_turn", [_text_block("ok")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    def _handler(agent_id, uid, dateiname_oder_index, folder="INBOX", **_kwargs):
        return _vision_payload(vision_key=f"key-{dateiname_oder_index}")

    monkeypatch.setitem(chat_tools.TOOL_HANDLERS, "mail_anhang_lesen", _handler)

    list(chat_tools.run_agentic_chat("info", "Zeig mir alle fünf Bilder"))
    return mock_client


def test_gesamtbudget_ueberschreitet_nie_vier_breakpoints(mocker, tmp_path, monkeypatch):
    """Der wichtigste Test: fünf Vision-Anhänge (Vision-Deckel greift bei 2)
    PLUS Prompt-Präfix (System + Tool-Schemas, 2) -> die Summe über den
    GESAMTEN zweiten Request (system + tools + messages, rekursiv) ist exakt
    4 -- damit fliegt ein späterer stiller Anstieg (z.B. ein drittes
    Cache-Ziel) sofort auf, statt erst live mit einem 400er."""
    import src.chat_tools as chat_tools

    mock_client = _fuenf_vision_anhaenge_lauf(mocker, tmp_path, monkeypatch)

    second_call_kwargs = mock_client.messages.create.call_args_list[1].kwargs
    total = _count_cache_control_breakpoints(second_call_kwargs)
    assert total <= chat_tools.MAX_CACHE_BREAKPOINTS_TOTAL
    assert total == 4  # Präfix (2) + Vision-Deckel (2)


def test_system_ist_cachebarer_textblock(mocker, tmp_path, monkeypatch):
    """`system` ist eine Liste mit GENAU einem Textblock, dessen `text` dem
    rohen `build_system_prompt`-Ergebnis entspricht (D-08 -- der System-
    Prompt darf NICHT durch den Anonymizer gelaufen sein) und der
    `cache_control` trägt."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools
    from src import chat

    round1 = _fake_response("end_turn", [_text_block("Alles klar.")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = round1
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    list(chat_tools.run_agentic_chat("info", "Hallo"))

    call_kwargs = mock_client.messages.create.call_args.kwargs
    system = call_kwargs["system"]
    assert isinstance(system, list)
    assert len(system) == 1
    assert system[0]["type"] == "text"
    assert system[0]["text"] == chat.build_system_prompt("info")
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_nur_letztes_tool_schema_traegt_cache_control(mocker, tmp_path, monkeypatch):
    """In `kwargs["tools"]` trägt EXAKT der letzte Eintrag `cache_control`,
    alle anderen keinen -- alles bis einschließlich dieses Eintrags wird als
    EIN Präfix gecacht."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response("end_turn", [_text_block("Alles klar.")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = round1
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    list(chat_tools.run_agentic_chat("info", "Hallo"))

    tools = mock_client.messages.create.call_args.kwargs["tools"]
    assert len(tools) > 1
    assert all("cache_control" not in schema for schema in tools[:-1])
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}


def test_globale_tool_schemas_bleiben_unmutiert(mocker, tmp_path, monkeypatch):
    """Fängt exakt den Fall ab, dass jemand die Kopie in `_cached_tool_schemas`
    durch eine In-place-Zuweisung ersetzt: nach einem vollen Durchlauf trägt
    WEDER das Modul-globale `TOOL_SCHEMAS` NOCH `_tool_schemas_for("info")`
    irgendein `cache_control`."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response("end_turn", [_text_block("Alles klar.")])
    mock_client = MagicMock()
    mock_client.messages.create.return_value = round1
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    list(chat_tools.run_agentic_chat("info", "Hallo"))

    assert all("cache_control" not in schema for schema in chat_tools.TOOL_SCHEMAS)
    assert all("cache_control" not in schema for schema in chat_tools._tool_schemas_for("info"))


def test_cached_tool_schemas_besteht_autosend_scanner():
    """CTOOL-05-Erweiterung: der bestehende Scanner-Test deckt nur
    `_tool_schemas_for` ab; ab jetzt geht aber die GECACHTE Variante ans
    Modell -- die Guard-Abdeckung wird hier erweitert, die Allowlist bleibt
    unangetastet."""
    import src.chat_tools as chat_tools

    findings = _scan_tool_schemas_for_forbidden_send_patterns(
        chat_tools._cached_tool_schemas("info")
    )
    assert findings == []


def test_vision_deckel_zaehlt_unabhaengig_vom_praefix(mocker, tmp_path, monkeypatch):
    """Bei NULL Vision-Blöcken sind es genau `PROMPT_CACHE_BREAKPOINTS` (2)
    Breakpoints -- belegt, dass der Präfix-Cache (System + Tool-Schemas) auch
    im Normalfall (ohne Anhänge) aktiv ist, unabhängig vom Vision-Deckel."""
    import src.chat_tools as chat_tools

    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    round1 = _fake_response("tool_use", [_tool_use_block("mails_suchen", {"query": "Rechnung"})])
    round2 = _fake_response("end_turn", [_text_block("Hier ist die Antwort.")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS, "mails_suchen", MagicMock(return_value={"treffer": []})
    )

    list(chat_tools.run_agentic_chat("info", "Suche nach Rechnung"))

    first_call_kwargs = mock_client.messages.create.call_args_list[0].kwargs
    assert _count_cache_control_breakpoints(first_call_kwargs) == chat_tools.PROMPT_CACHE_BREAKPOINTS
    assert chat_tools.PROMPT_CACHE_BREAKPOINTS == 2
