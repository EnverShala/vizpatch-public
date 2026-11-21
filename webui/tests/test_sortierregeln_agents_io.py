"""Tests für die sortierregeln.md-Lese-/Schreibfunktionen in agents_io.py
(Phase 14, Plan 14-01, AUTO-02) — Muster 1:1 wie vorlagen.md
(test_vorlagen.py), aber OHNE Prompt-Injektion: sortierregeln.md wird vom
Polling-Agenten gelesen, nicht in den Chat-System-Prompt injiziert."""
from __future__ import annotations

import pytest

from .test_chat_tools import _setup_env, _write_agent_env


def test_read_liefert_leeren_string_ohne_datei(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    assert agents_io.read_sortierregeln_md("info") == ""


def test_schreiben_und_lesen_erhaelt_den_inhalt(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    inhalt = "## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n"
    agents_io.write_sortierregeln_md_atomic("info", inhalt)

    assert agents_io.read_sortierregeln_md("info") == inhalt


def test_datei_liegt_im_agent_verzeichnis_neben_context_und_style(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    agents_io.write_sortierregeln_md_atomic("info", "## A\nAktion: markieren\n")

    pfad = agents_io._sortierregeln_path("info")
    assert pfad.name == "sortierregeln.md"
    assert pfad.parent == agents_io._context_path("info").parent


@pytest.mark.parametrize("boese_id", ["../../etc", "..", "a/b", "a\\b"])
def test_slug_guard_verhindert_zugriff_ausserhalb_des_agent_verzeichnisses(
    tmp_path, monkeypatch, boese_id
):
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    with pytest.raises(ValueError):
        agents_io.read_sortierregeln_md(boese_id)
    with pytest.raises(ValueError):
        agents_io.write_sortierregeln_md_atomic(boese_id, "egal")


def test_schreiben_hinterlaesst_keine_tmp_datei(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io

    agents_io.write_sortierregeln_md_atomic("info", "## A\nAktion: markieren\n")

    verzeichnis = agents_io._sortierregeln_path("info").parent
    assert not list(verzeichnis.glob("sortierregeln.tmp"))


def test_geschriebener_inhalt_ist_vom_agent_seitigen_parser_lesbar(tmp_path, monkeypatch):
    """Beweist das Zusammenspiel ueber die Dateigrenze hinweg: was die WebUI
    schreibt, versteht der (unabhaengig entwickelte) Agent-Parser."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.agents_io as agents_io
    import src.sortierregeln as sortierregeln

    agents_io.write_sortierregeln_md_atomic(
        "info", "## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n\n## Notfälle\nAktion: markieren\n"
    )
    gelesen = agents_io.read_sortierregeln_md("info")
    kategorien, warnungen = sortierregeln.parse_sortierregeln(gelesen)

    assert warnungen == []
    assert len(kategorien) == 2
