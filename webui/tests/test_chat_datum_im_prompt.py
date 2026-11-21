"""Tests für das aktuelle Datum im Chat-System-Prompt (Phase 13, Plan 13-02,
MBOX-18 technischer Teil).

Befund vor dieser Änderung (13-RESEARCH.md Abschnitt 10, am Quelltext
verifiziert): das aktuelle Datum stand NIRGENDS im Prompt — weder in
`chat-system.txt` noch in `_format_agent_status`/`build_system_prompt`. Der
Agent konnte „seit Montag" oder „älter als ein Jahr" deshalb nicht auflösen.

Zwei Eigenschaften sichern diese Tests ab:

1. **Tagesgenauigkeit.** Der GESAMTE System-Prompt hängt an einem
   `cache_control: ephemeral`-Breakpoint (`chat_tools._cached_system_blocks`).
   Ein tagesgenauer Wert entwertet den Anthropic-Prompt-Cache einmal pro
   Kalendertag, ein uhrzeitgenauer bei JEDEM Request.
2. **Zeitzone Europe/Berlin.** Der Container läuft in UTC, deshalb würde
   `date.today()` zwischen 00:00 und 02:00 deutscher Ortszeit den VORTAG
   liefern.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .test_chat_tools import _setup_env, _write_agent_env


def _prompt(tmp_path, monkeypatch) -> str:
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv(
        "WEBUI_CHAT_SYSTEM_PROMPT",
        str((__import__("pathlib").Path(__file__).parent.parent / "prompts" / "chat-system.txt")),
    )
    import src.chat as chat

    return chat.build_system_prompt("info")


def test_prompt_enthaelt_heutiges_iso_datum(tmp_path, monkeypatch):
    heute = datetime.now(ZoneInfo("Europe/Berlin")).date()
    assert heute.isoformat() in _prompt(tmp_path, monkeypatch)


def test_prompt_enthaelt_deutschen_wochentagsnamen_fuer_heute(tmp_path, monkeypatch):
    import src.chat as chat

    heute = datetime.now(ZoneInfo("Europe/Berlin")).date()
    erwartet = chat._WOCHENTAGE_DE[heute.weekday()]

    assert erwartet in _prompt(tmp_path, monkeypatch)


def test_platzhalter_ist_ersetzt_und_nicht_mehr_im_prompt(tmp_path, monkeypatch):
    prompt = _prompt(tmp_path, monkeypatch)

    assert "{heute}" not in prompt, (
        "Der Platzhalter {heute} steht noch im fertigen Prompt — die Ersetzung in "
        "build_system_prompt hat nicht gegriffen. Das Modell bekäme dann keinen "
        "Datumsbezug und könnte relative Zeitangaben nicht auflösen."
    )


def test_datumsangabe_traegt_keine_uhrzeit_cache_schutz(tmp_path, monkeypatch):
    """Der eingesetzte Datumstext darf KEINE Uhrzeit enthalten.

    Das ist der eigentliche Cache-Schutz — nicht das Verbot eines bestimmten
    Funktionsnamens. `datetime.now(...).date()` ist erlaubt und nötig (Zeitzone),
    eine Uhrzeit IM PROMPT ist es nicht: sie würde den `cache_control`-Breakpoint
    des Prompts bei jedem einzelnen Request entwerten.
    """
    import src.chat as chat

    heute_text = chat._heute_text()

    assert not re.search(r"\d{1,2}:\d{2}", heute_text), (
        f"Die Datumsangabe '{heute_text}' enthaelt eine Uhrzeit. Der gesamte "
        "System-Prompt haengt an einem cache_control-Breakpoint "
        "(chat_tools._cached_system_blocks) — eine Uhrzeit macht den Prompt bei "
        "JEDEM Request einmalig und entwertet damit den Anthropic-Prompt-Cache "
        "vollstaendig. Nur Tagesgenauigkeit ist zulaessig."
    )


def test_datum_folgt_berliner_ortszeit_nicht_utc():
    """Gegenprobe zur UTC-Falle: `today_local` liefert das Berliner Datum.

    Zwischen 00:00 und 02:00 deutscher Ortszeit weicht das UTC-Datum um einen
    Tag ab. Ein Test, der nur `date.today()` vergleicht, wuerde die Regression
    genau in diesem Zeitfenster durchlassen — deshalb wird explizit gegen die
    Zeitzone geprueft.
    """
    from src import timefmt

    erwartet = datetime.now(ZoneInfo("Europe/Berlin")).date()

    assert timefmt.today_local() == erwartet
    assert isinstance(timefmt.today_local(), date)


def test_zwei_aufrufe_liefern_bytegleiche_prompts(tmp_path, monkeypatch):
    """Cache-Stabilitaet innerhalb eines Tages: zwei unmittelbar
    aufeinanderfolgende Aufrufe muessen byte-gleich sein."""
    erster = _prompt(tmp_path, monkeypatch)
    zweiter = _prompt(tmp_path, monkeypatch)

    assert erster == zweiter
