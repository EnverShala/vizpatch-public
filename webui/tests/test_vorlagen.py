"""Tests für Vorlagen/Textbausteine pro Agent (Phase 13, Plan 13-09, MBOX-16).

„Antworte wie immer bei Terminanfragen" setzt voraus, dass es ein „wie immer"
gibt. Die Vorlagen bekommen denselben Platz und dieselbe Mechanik wie
`context.md` und `style.md`: pro Agent, im Zero-Config-Pfad, Klartext, im
WebUI bearbeitbar, serverseitig in den Prompt injiziert.

Drei Eigenschaften sind hier festgeschrieben:

1. **Der Slug-Guard greift.** Der Pfad läuft über `_agent_dir`, eine
   `agent_id` mit Pfadanteilen darf nichts außerhalb des Agent-Verzeichnisses
   lesen oder schreiben.
2. **Fehlende Datei ist unkritisch.** Vorlagen sind optional; ohne sie bleibt
   der System-Prompt gültig und trägt keinen unersetzten Platzhalter.
3. **Der Inhalt landet im DATEN-Bereich des Prompts**, nicht im
   Anweisungsteil — er steht hinter demselben Injektions-Anker wie `context.md`
   und `style.md`. Ein Vorlagentext in Anweisungsform darf keine Anweisung
   werden.

Weil die Injektion serverseitig passiert, wirken die Vorlagen automatisch auch
im Outlook-Add-in — sie brauchen keinen Client-State.
"""
from __future__ import annotations

import pytest

from .test_chat_tools import _setup_env, _write_agent_env


def test_read_liefert_leeren_string_ohne_datei(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    assert agents_io.read_vorlagen_md("info") == ""


def test_schreiben_und_lesen_erhaelt_den_inhalt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    inhalt = "## Terminanfrage\nGerne, wir haben Zeit am ...\n\n## Reklamation\nDas tut uns leid.\n"
    agents_io.write_vorlagen_md_atomic("info", inhalt)

    assert agents_io.read_vorlagen_md("info") == inhalt


def test_datei_liegt_im_agent_verzeichnis_neben_context_und_style(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    agents_io.write_vorlagen_md_atomic("info", "## A\ntext\n")

    pfad = agents_io._vorlagen_path("info")
    assert pfad.name == "vorlagen.md"
    assert pfad.parent == agents_io._context_path("info").parent


def test_mehrere_benannte_vorlagen_bleiben_unterscheidbar(tmp_path, monkeypatch):
    """Das Format trägt Überschriften, damit eine Vorlage beim Namen
    ansprechbar ist."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    agents_io.write_vorlagen_md_atomic(
        "info", "## Terminanfrage\nText A\n\n## Reklamation\nText B\n"
    )
    gelesen = agents_io.read_vorlagen_md("info")

    assert "## Terminanfrage" in gelesen
    assert "## Reklamation" in gelesen


@pytest.mark.parametrize("boese_id", ["../../etc", "..", "a/b", "a\\b"])
def test_slug_guard_verhindert_zugriff_ausserhalb_des_agent_verzeichnisses(
    tmp_path, monkeypatch, boese_id
):
    """Ohne diesen Guard könnte eine manipulierte Agent-Kennung Dateien
    außerhalb des Agent-Verzeichnisses schreiben."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    with pytest.raises(ValueError):
        agents_io.read_vorlagen_md(boese_id)
    with pytest.raises(ValueError):
        agents_io.write_vorlagen_md_atomic(boese_id, "egal")


def test_schreiben_hinterlaesst_keine_tmp_datei(tmp_path, monkeypatch):
    """Atomares Schreiben über tmp + `os.replace`: nach dem Schreiben darf
    keine halbe Datei liegenbleiben, die der Agent zur Laufzeit lesen würde."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    agents_io.write_vorlagen_md_atomic("info", "## A\ntext\n")

    verzeichnis = agents_io._vorlagen_path("info").parent
    assert not list(verzeichnis.glob("vorlagen.tmp"))


def test_inhalt_wird_in_den_system_prompt_injiziert(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv(
        "WEBUI_CHAT_SYSTEM_PROMPT",
        str(__import__("pathlib").Path(__file__).parent.parent / "prompts" / "chat-system.txt"),
    )
    import src.agents_io as agents_io
    import src.chat as chat

    agents_io.write_vorlagen_md_atomic("info", "## Terminanfrage\nGerne, wir haben Zeit am ...\n")
    prompt = chat.build_system_prompt("info")

    assert "## Terminanfrage" in prompt
    assert "Gerne, wir haben Zeit am" in prompt


def test_platzhalter_ist_auch_ohne_datei_ersetzt(tmp_path, monkeypatch):
    """Ein unersetzter Platzhalter im Prompt wäre ein Fehler — das Modell würde
    die geschweiften Klammern als Inhalt sehen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv(
        "WEBUI_CHAT_SYSTEM_PROMPT",
        str(__import__("pathlib").Path(__file__).parent.parent / "prompts" / "chat-system.txt"),
    )
    import src.chat as chat

    prompt = chat.build_system_prompt("info")

    assert "{vorlagen_md}" not in prompt


def test_vorlagen_stehen_im_daten_bereich_hinter_dem_injektions_anker(tmp_path, monkeypatch):
    """Der Vorlagentext ist DATEN. Stünde er vor dem Injektions-Anker, könnte
    ein Vorlagentext in Anweisungsform zur Anweisung werden."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv(
        "WEBUI_CHAT_SYSTEM_PROMPT",
        str(__import__("pathlib").Path(__file__).parent.parent / "prompts" / "chat-system.txt"),
    )
    import src.agents_io as agents_io
    import src.chat as chat

    agents_io.write_vorlagen_md_atomic("info", "MARKE-VORLAGE-XYZ")
    prompt = chat.build_system_prompt("info")

    anker = prompt.index("INJEKTIONS-ANKER")
    vorlage = prompt.index("MARKE-VORLAGE-XYZ")
    assert vorlage > anker, (
        "Der Vorlagen-Inhalt steht VOR dem Injektions-Anker — damit wäre ein "
        "Vorlagentext in Anweisungsform nicht als Daten gekennzeichnet."
    )


def test_prompt_nennt_vorlagen_im_anker_satz(tmp_path, monkeypatch):
    """Der Anker-Satz zählt die Datenblöcke auf; fehlt „Vorlagen" darin, sind
    sie nicht ausdrücklich als Daten deklariert."""
    from pathlib import Path

    inhalt = (Path(__file__).parent.parent / "prompts" / "chat-system.txt").read_text(
        encoding="utf-8"
    )

    anker_zeile = next(z for z in inhalt.splitlines() if z.startswith("Die folgenden Abschnitte"))
    assert "Vorlagen" in anker_zeile
