"""Config-Laden für die Urlaubs-Autoantwort (URL-01..09).

Muster 1:1 wie `test_config_auto_sort.py`. Der wichtigste Test ist der erste:
ein BESTANDSAGENT ohne die neuen Variablen muss die Funktion AUS haben. Bei der
einzigen sendenden Funktion des Produkts wäre ein „an durch Update" der
schlimmstmögliche Default.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import load_agent_config


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
    monkeypatch.setenv(
        "PROMPTS_DIR", str(Path(__file__).resolve().parents[1] / "prompts")
    )


def test_bestandsagent_hat_die_funktion_aus(tmp_path):
    """Ein Agent, dessen `.env` vor diesem Feature geschrieben wurde, kennt
    keine der neuen Variablen — er darf nach dem Update NICHT anfangen zu
    senden."""
    cfg = load_agent_config("info", _agent_dir(tmp_path))
    assert cfg.enable_urlaubsantwort is False
    assert cfg.urlaub_dry_run is True  # sicherer Default: erst beobachten
    assert cfg.urlaub_von == ""
    assert cfg.urlaub_bis == ""


def test_flags_werden_gelesen(tmp_path):
    d = _agent_dir(
        tmp_path,
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="false",
        URLAUB_VON="2026-09-01",
        URLAUB_BIS="2026-09-14",
        URLAUB_MAX_PRO_ZYKLUS="5",
    )
    cfg = load_agent_config("info", d)
    assert cfg.enable_urlaubsantwort is True
    assert cfg.urlaub_dry_run is False
    assert cfg.urlaub_von == "2026-09-01"
    assert cfg.urlaub_bis == "2026-09-14"
    assert cfg.urlaub_max_pro_zyklus == 5


@pytest.mark.parametrize("wert", ["false", "False", "", "1", "ja", "quatsch"])
def test_nur_exakt_true_schaltet_ein(wert, tmp_path):
    """Fail-closed: alles außer `true` bedeutet aus. Ein `ENABLE_URLAUBSANTWORT=1`
    darf nicht versehentlich senden."""
    cfg = load_agent_config("info", _agent_dir(tmp_path, ENABLE_URLAUBSANTWORT=wert))
    assert cfg.enable_urlaubsantwort is False


def test_vorlagen_md_wird_mitgeladen(tmp_path):
    d = _agent_dir(tmp_path)
    (d / "vorlagen.md").write_text("## Urlaub\nIch bin weg.\n", encoding="utf-8")
    cfg = load_agent_config("info", d)
    assert "## Urlaub" in cfg.vorlagen_md


def test_fehlende_vorlagen_md_ist_kein_fehler(tmp_path):
    """Fehlend/leer heißt schlicht „keine Vorlage, kein Versand" — es darf
    nicht den Agenten-Start brechen."""
    cfg = load_agent_config("info", _agent_dir(tmp_path))
    assert cfg.vorlagen_md == ""


def test_webui_adresse_hat_einen_compose_default(tmp_path):
    cfg = load_agent_config("info", _agent_dir(tmp_path))
    assert cfg.webui_internal_url == "http://webui:8080"


def test_webui_adresse_ist_ueberschreibbar(tmp_path):
    cfg = load_agent_config(
        "info", _agent_dir(tmp_path, WEBUI_INTERNAL_URL="http://andere:9000")
    )
    assert cfg.webui_internal_url == "http://andere:9000"
