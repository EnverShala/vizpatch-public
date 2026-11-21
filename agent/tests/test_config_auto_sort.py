"""Tests fuer die Config-Felder des automatischen Einsortierens (Phase 14,
Plan 14-01, AUTO-01/05/06). Kritischste Erwartung (AUTO-01): ein bestehender
Agent OHNE die neuen Env-Variablen verhaelt sich nach dem Update unveraendert
-- Default AUS."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import load_agent_config


def _make_prompts_dir(base: Path) -> Path:
    prompts_dir = base / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    (prompts_dir / "classify.txt").write_text("classify {company_name}", encoding="utf-8")
    (prompts_dir / "generate.txt").write_text(
        "generate {company_name} {context_md_full} {conversation_history} {from} {subject} {body}",
        encoding="utf-8",
    )
    return prompts_dir


def _make_agent_dir(agents_root: Path, agent_id: str, env: dict, sortierregeln_md: str = "") -> Path:
    agent_dir = agents_root / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in env.items()]
    (agent_dir / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (agent_dir / "context.md").write_text(f"# Kontext {agent_id}", encoding="utf-8")
    if sortierregeln_md:
        (agent_dir / "sortierregeln.md").write_text(sortierregeln_md, encoding="utf-8")
    return agent_dir


@pytest.fixture(autouse=True)
def _agent_env(tmp_path, monkeypatch):
    prompts_dir = _make_prompts_dir(tmp_path)
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    data_root = tmp_path / "data"
    data_root.mkdir()

    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("AGENTS_CONFIG_ROOT", str(agents_root))
    monkeypatch.setenv("AGENT_DATA_ROOT", str(data_root))
    monkeypatch.setenv("PROMPTS_DIR", str(prompts_dir))

    return {"tmp_path": tmp_path, "agents_root": agents_root, "data_root": data_root}


_BASE_ENV = {
    "IMAP_USER": "a@x.de",
    "IMAP_PASSWORD": "pw",
    "IMAP_HOST": "imap.x.de",
    "LLM_API_KEY": "key",
}


def test_defaults_ohne_env_variablen_sind_aus(_agent_env):
    """AUTO-01: kein ENABLE_AUTO_SORT gesetzt -> Feature bleibt AUS, ein
    bestehender Agent verhaelt sich nach dem Update unveraendert."""
    agent_dir = _make_agent_dir(_agent_env["agents_root"], "bestandsagent", dict(_BASE_ENV))
    cfg = load_agent_config("bestandsagent", agent_dir)
    assert cfg.enable_auto_sort is False
    # AUTO-05: Probelauf-Modus ist Default AN (unabhaengig davon, ob das
    # Feature ueberhaupt aktiv ist) -- sichere Grundeinstellung.
    assert cfg.auto_sort_dry_run is True
    assert cfg.auto_sort_max_mails == 50
    assert cfg.sortierregeln_md == ""


def test_enable_auto_sort_true_wird_uebernommen(_agent_env):
    env = dict(_BASE_ENV, ENABLE_AUTO_SORT="true")
    agent_dir = _make_agent_dir(_agent_env["agents_root"], "auto-an", env)
    cfg = load_agent_config("auto-an", agent_dir)
    assert cfg.enable_auto_sort is True


def test_auto_sort_dry_run_kann_explizit_ausgeschaltet_werden(_agent_env):
    env = dict(_BASE_ENV, ENABLE_AUTO_SORT="true", AUTO_SORT_DRY_RUN="false")
    agent_dir = _make_agent_dir(_agent_env["agents_root"], "live-scharf", env)
    cfg = load_agent_config("live-scharf", agent_dir)
    assert cfg.auto_sort_dry_run is False


def test_auto_sort_max_mails_ist_konfigurierbar(_agent_env):
    env = dict(_BASE_ENV, ENABLE_AUTO_SORT="true", AUTO_SORT_MAX_MAILS="10")
    agent_dir = _make_agent_dir(_agent_env["agents_root"], "gedeckelt", env)
    cfg = load_agent_config("gedeckelt", agent_dir)
    assert cfg.auto_sort_max_mails == 10


def test_sortierregeln_md_wird_geladen(_agent_env):
    inhalt = "## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n"
    agent_dir = _make_agent_dir(
        _agent_env["agents_root"], "mit-regeln", dict(_BASE_ENV, ENABLE_AUTO_SORT="true"),
        sortierregeln_md=inhalt,
    )
    cfg = load_agent_config("mit-regeln", agent_dir)
    assert cfg.sortierregeln_md == inhalt


def test_fehlende_sortierregeln_md_ist_leerer_string_kein_crash(_agent_env):
    agent_dir = _make_agent_dir(_agent_env["agents_root"], "ohne-regeln", dict(_BASE_ENV))
    cfg = load_agent_config("ohne-regeln", agent_dir)
    assert cfg.sortierregeln_md == ""
