"""Intelligenz-Modus (Betreiber-Wunsch 2026-08-24).

Muster 1:1 wie `test_config_urlaub.py`. Zwei Tests tragen die Last: ein
Bestandsagent muss auf „standard" bleiben (ein Update darf niemandem still die
Kosten verdreifachen), und ein explizit gesetztes `MODEL_CLASSIFY` muss den
Modus schlagen — sonst wuerde der Schalter eine bewusst gewaehlte Modellwahl
ueberschreiben.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import (
    MODEL_DEFAULTS,
    MODEL_DEFAULTS_HOCH,
    load_agent_config,
    normalisiere_intelligenz_modus,
)


def _agent_dir(tmp_path: Path, **extra) -> Path:
    d = tmp_path / "agents" / "info"
    d.mkdir(parents=True)
    zeilen = [
        "AGENT_ENABLED=true",
        "IMAP_USER=info@ionos.de",
        "IMAP_PASSWORD=geheim",
        "LLM_API_KEY=sk-ant-test",
    ]
    zeilen += [f"{k}={v}" for k, v in extra.items()]
    (d / ".env").write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def prompts(monkeypatch):
    monkeypatch.setenv("PROMPTS_DIR", str(Path(__file__).resolve().parents[1] / "prompts"))


def test_bestandsagent_bleibt_auf_standard(tmp_path):
    """Ein Agent, dessen `.env` vor diesem Feature geschrieben wurde, kennt die
    Variable nicht — er darf nach einem Update nicht plötzlich das Dreifache
    kosten."""
    cfg = load_agent_config("info", _agent_dir(tmp_path))
    assert cfg.intelligenz_modus == "standard"
    assert cfg.model_classify == MODEL_DEFAULTS["anthropic"]["classify"]
    assert cfg.model_draft == MODEL_DEFAULTS["anthropic"]["draft"]


def test_hoch_zieht_beide_modelle_eine_stufe_hoch(tmp_path):
    cfg = load_agent_config("info", _agent_dir(tmp_path, INTELLIGENZ_MODUS="hoch"))
    assert cfg.intelligenz_modus == "hoch"
    assert cfg.model_classify == MODEL_DEFAULTS_HOCH["anthropic"]["classify"]
    assert cfg.model_draft == MODEL_DEFAULTS_HOCH["anthropic"]["draft"]
    # Beide Stufen müssen sich tatsächlich unterscheiden, sonst ist der
    # Schalter ein Placebo.
    assert cfg.model_classify != MODEL_DEFAULTS["anthropic"]["classify"]
    assert cfg.model_draft != MODEL_DEFAULTS["anthropic"]["draft"]


def test_explizites_modell_schlaegt_den_modus(tmp_path):
    """Wer `MODEL_CLASSIFY` bewusst gesetzt hat, hat eine Entscheidung
    getroffen. Der Schalter darf sie nicht überschreiben — sonst kann man ein
    Modell nach einem Feature-Update nicht mehr festhalten."""
    cfg = load_agent_config(
        "info",
        _agent_dir(
            tmp_path,
            INTELLIGENZ_MODUS="hoch",
            MODEL_CLASSIFY="claude-haiku-4-5",
            MODEL_DRAFT="claude-sonnet-4-6",
        ),
    )
    assert cfg.intelligenz_modus == "hoch"  # Anzeige bleibt ehrlich ...
    assert cfg.model_classify == "claude-haiku-4-5"  # ... die Wahl gewinnt
    assert cfg.model_draft == "claude-sonnet-4-6"


@pytest.mark.parametrize("wert", ["", "  ", "HOCH!", "sehr hoch", "true", "maximum", "1"])
def test_unbekannter_wert_faellt_auf_standard(tmp_path, wert):
    """Ein Tippfehler darf den Agenten nicht am Starten hindern — und ihn schon
    gar nicht still teurer machen."""
    cfg = load_agent_config("info", _agent_dir(tmp_path, INTELLIGENZ_MODUS=wert))
    assert cfg.intelligenz_modus == "standard"
    assert cfg.model_classify == MODEL_DEFAULTS["anthropic"]["classify"]


@pytest.mark.parametrize(
    "roh,erwartet",
    [("hoch", "hoch"), ("HOCH", "hoch"), ("  Hoch  ", "hoch"),
     ("standard", "standard"), ("", "standard"), ("quatsch", "standard")],
)
def test_normalisierung(roh, erwartet):
    assert normalisiere_intelligenz_modus(roh) == erwartet


def test_beide_tabellen_deckungsgleich():
    """Jeder Provider braucht in BEIDEN Tabellen einen Eintrag — sonst fällt der
    Modus für diesen Provider still auf Anthropic zurück."""
    assert set(MODEL_DEFAULTS) == set(MODEL_DEFAULTS_HOCH)
    for provider, paar in MODEL_DEFAULTS_HOCH.items():
        assert set(paar) == {"classify", "draft"}, provider
        assert all(paar.values()), provider


def test_entwurfsmodell_ist_aktuell():
    """Nagelt das Draft-Modell fest. Sonnet 5 kostet dasselbe wie das zuvor
    genutzte Sonnet 4.6 (3 $/15 $ pro Mio Tokens) und ist bei agentischer Arbeit
    deutlich besser — ein Downgrade zurück wäre ein stiller Qualitätsverlust bei
    gleichem Preis."""
    assert MODEL_DEFAULTS["anthropic"]["draft"] == "claude-sonnet-5"
    assert MODEL_DEFAULTS["anthropic"]["classify"] == "claude-haiku-4-5"
