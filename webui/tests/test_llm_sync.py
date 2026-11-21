"""Drift-Guard für den llm.py-Sync-Kontrakt (Phase 6, Plan 06.02).

`agent/src/llm.py` und `webui/src/llm.py` sind BEWUSST zwei identische
Kopien: Deployment sind zwei getrennte Docker-Images, ein Shared-Package ist
eine absichtliche Nicht-Entscheidung (siehe `agent/src/crypto.py` ↔
`webui/src/crypto.py` + `test_crypto_sync.py`, Review WR-06). Die WebUI
braucht den provider-agnostischen `llm_call(...)`-Adapter für die
Schreibstil-Extraktion (D-55): derselbe Anthropic/OpenAI/Google-Dispatch wie
im Agent. Dieser Test schlägt fehl, sobald die Kopien auseinanderlaufen. Im
Docker-Image (nur ein Service im Dateisystem) wird er geskippt — der Guard
greift im Repo-Checkout/CI.

Diese Testdatei existiert ebenfalls identisch in beiden Services.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_COPY = _REPO_ROOT / "agent" / "src" / "llm.py"
_WEBUI_COPY = _REPO_ROOT / "webui" / "src" / "llm.py"


def test_llm_module_copies_are_byte_identical():
    if not (_AGENT_COPY.exists() and _WEBUI_COPY.exists()):
        pytest.skip(
            "Schwester-Service nicht im Dateisystem (Docker-Image-Build) — "
            "Sync-Check läuft nur im Repo-Checkout/CI."
        )

    agent_hash = hashlib.sha256(_AGENT_COPY.read_bytes()).hexdigest()
    webui_hash = hashlib.sha256(_WEBUI_COPY.read_bytes()).hexdigest()
    assert agent_hash == webui_hash, (
        "agent/src/llm.py und webui/src/llm.py sind auseinandergelaufen — "
        "der provider-agnostische LLM-Adapter MUSS in beiden Services "
        "identisch bleiben. Änderung bitte in BEIDE Dateien übernehmen."
    )

_AGENT_KOMPAT = _REPO_ROOT / "agent" / "src" / "anthropic_kompat.py"
_WEBUI_KOMPAT = _REPO_ROOT / "webui" / "src" / "anthropic_kompat.py"


def test_anthropic_kompat_copies_are_byte_identical():
    """Zweiter Sync-Kontrakt, gleiche Begruendung wie oben: die Tabelle der
    Modelle, die `temperature` noch akzeptieren, muss in beiden Services
    dieselbe sein. Laufen die Kopien auseinander, wuerde ein Service einen
    Parameter senden, den der andere weglaesst — und der eine bekaeme 400,
    waehrend der andere laeuft. Genau dieser Bruch (Sonnet 5 / Opus 5 lehnen
    `temperature` ab) hat am 2026-08-24 die Entwuerfe stillgelegt."""
    if not (_AGENT_KOMPAT.exists() and _WEBUI_KOMPAT.exists()):
        pytest.skip(
            "Schwester-Service nicht im Dateisystem (Docker-Image-Build) — "
            "Sync-Check läuft nur im Repo-Checkout/CI."
        )

    agent_hash = hashlib.sha256(_AGENT_KOMPAT.read_bytes()).hexdigest()
    webui_hash = hashlib.sha256(_WEBUI_KOMPAT.read_bytes()).hexdigest()
    assert agent_hash == webui_hash, (
        "agent/src/anthropic_kompat.py und webui/src/anthropic_kompat.py sind "
        "auseinandergelaufen — die Modelltabelle MUSS in beiden Services "
        "identisch bleiben. Änderung bitte in BEIDE Dateien übernehmen."
    )


def test_temperature_kwargs_laesst_neue_modelle_aus():
    """Der eigentliche Schutz: Sonnet 5 / Opus 5 duerfen KEIN `temperature`
    bekommen, Haiku 4.5 schon — und ein unbekanntes (kuenftiges) Modell faellt
    auf die sichere Seite, damit der naechste Modellwechsel nicht dieselbe
    Bruchstelle erneut aufreisst."""
    import sys

    sys.path.insert(0, str(_REPO_ROOT / "webui"))
    from src import anthropic_kompat

    assert anthropic_kompat.temperature_kwargs("claude-sonnet-5", 0.3) == {}
    assert anthropic_kompat.temperature_kwargs("claude-opus-5", 0.0) == {}
    assert anthropic_kompat.temperature_kwargs("claude-haiku-4-5", 0.0) == {"temperature": 0.0}
    assert anthropic_kompat.temperature_kwargs("claude-sonnet-4-6", 0.3) == {"temperature": 0.3}
    # Unbekannt -> weglassen (fail-safe), nicht senden.
    assert anthropic_kompat.temperature_kwargs("claude-opus-6", 0.3) == {}
    assert anthropic_kompat.temperature_kwargs("", 0.3) == {}
