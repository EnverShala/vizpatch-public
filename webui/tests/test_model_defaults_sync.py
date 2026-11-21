"""Drift-Guard: webui/src/style_extract.py::MODEL_DRAFT_DEFAULTS MUSS mit der
Draft-Spalte von agent/src/config.py::MODEL_DEFAULTS übereinstimmen (D-55).

Kein Import von agent/src/config.py (agent-only, relative Imports/Deps die die
WebUI nicht hat) — stattdessen AST-Parse der Datei, analog zum
crypto/pii/llm-Byte-Sync-Muster, nur auf Werte statt Bytes. Skippt, wenn
agent/src/config.py nicht im Dateisystem liegt (Docker-Image-Build, nur ein
Service im Container).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_CONFIG = _REPO_ROOT / "agent" / "src" / "config.py"


def _load_agent_tabelle(name: str) -> dict:
    """Liest eine Modell-Tabelle per AST aus `agent/src/config.py`.

    Exakter Namensvergleich, nicht `startswith`: `MODEL_DEFAULTS` und
    `MODEL_DEFAULTS_HOCH` unterscheiden sich nur durch das Suffix, und ein
    Praefix-Treffer wuerde je nach Reihenfolge im Quelltext die falsche Tabelle
    liefern — der Waechter waere dann gruen und die Aussage falsch."""
    source = _AGENT_CONFIG.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} nicht in agent/src/config.py gefunden")


def _load_agent_model_defaults() -> dict:
    return _load_agent_tabelle("MODEL_DEFAULTS")


def test_model_draft_defaults_match_agent_config():
    if not _AGENT_CONFIG.exists():
        pytest.skip(
            "agent/src/config.py nicht im Dateisystem (Docker-Image-Build) — "
            "Sync-Check läuft nur im Repo-Checkout/CI."
        )
    import src.style_extract as style_extract

    agent_model_defaults = _load_agent_model_defaults()
    for provider, defaults in agent_model_defaults.items():
        webui_draft = style_extract.MODEL_DRAFT_DEFAULTS.get(provider)
        assert webui_draft == defaults["draft"], (
            f"Draft-Modell für Provider '{provider}' ist in "
            f"webui/src/style_extract.py ({webui_draft!r}) und "
            f"agent/src/config.py ({defaults['draft']!r}) unterschiedlich. "
            "MODEL_DRAFT_DEFAULTS bitte synchron halten (D-55)."
        )


def test_model_draft_defaults_hoch_match_agent_config():
    """Dieselbe Zusicherung für den Intelligenz-Modus „hoch" (2026-08-24).

    Ohne diesen Test könnte der Stil-Lauf im hohen Modus auf einem anderen
    Modell laufen als die Entwürfe — sichtbar erst an einem Stil-Profil, das
    nicht zu den Entwürfen passt, und dann kaum zurückzuverfolgen.
    """
    if not _AGENT_CONFIG.exists():
        pytest.skip(
            "agent/src/config.py nicht im Dateisystem (Docker-Image-Build) — "
            "Sync-Check läuft nur im Repo-Checkout/CI."
        )
    import src.style_extract as style_extract

    agent_hoch = _load_agent_tabelle("MODEL_DEFAULTS_HOCH")
    assert set(agent_hoch) == set(style_extract.MODEL_DRAFT_DEFAULTS_HOCH), (
        "Provider-Mengen von MODEL_DEFAULTS_HOCH (agent) und "
        "MODEL_DRAFT_DEFAULTS_HOCH (webui) unterscheiden sich."
    )
    for provider, defaults in agent_hoch.items():
        webui_draft = style_extract.MODEL_DRAFT_DEFAULTS_HOCH.get(provider)
        assert webui_draft == defaults["draft"], (
            f"Draft-Modell (Modus hoch) für Provider '{provider}' ist in "
            f"webui/src/style_extract.py ({webui_draft!r}) und "
            f"agent/src/config.py ({defaults['draft']!r}) unterschiedlich. "
            "MODEL_DRAFT_DEFAULTS_HOCH bitte synchron halten (D-55)."
        )


def test_hoch_ist_nicht_dieselbe_tabelle_wie_standard():
    """Gegenprobe: wären beide Tabellen identisch, wäre der Schalter ein
    Placebo und beide Sync-Tests trotzdem grün."""
    import src.style_extract as style_extract

    assert (
        style_extract.MODEL_DRAFT_DEFAULTS_HOCH["anthropic"]
        != style_extract.MODEL_DRAFT_DEFAULTS["anthropic"]
    )
