"""Tests fuer die Integration des automatischen Sortierlaufs in den Polling-
Zyklus (Phase 14, Plan 14-01, AUTO-01). Kern-Erwartung: ein Bestandsagent
(ENABLE_AUTO_SORT nicht gesetzt) verhaelt sich nach dem Update unveraendert
-- keine zusaetzliche IMAP-Verbindung, kein run_auto_sort-Aufruf."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

import src.main as main_module


def _make_prompts_dir(base: Path) -> Path:
    prompts_dir = base / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    (prompts_dir / "classify.txt").write_text("classify {company_name}", encoding="utf-8")
    (prompts_dir / "generate.txt").write_text(
        "generate {company_name} {context_md_full} {conversation_history} {from} {subject} {body}",
        encoding="utf-8",
    )
    return prompts_dir


def _make_agent_dir(agents_root: Path, agent_id: str, extra_env: dict | None = None) -> Path:
    agent_dir = agents_root / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "IMAP_USER": f"{agent_id}@example.de",
        "IMAP_PASSWORD": "pw",
        "IMAP_HOST": "imap.example.de",
        "LLM_API_KEY": "key",
        "AGENT_ENABLED": "true",
    }
    env.update(extra_env or {})
    lines = [f"{k}={v}" for k, v in env.items()]
    (agent_dir / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return agent_dir


@pytest.fixture(autouse=True)
def _reset_shutdown_flag():
    main_module._shutdown = False
    main_module._drafts_cache.clear()
    yield
    main_module._shutdown = False
    main_module._drafts_cache.clear()


@pytest.fixture
def agent_env(tmp_path, monkeypatch):
    prompts_dir = _make_prompts_dir(tmp_path)
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    data_root = tmp_path / "data"
    data_root.mkdir()

    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("AGENTS_CONFIG_ROOT", str(agents_root))
    monkeypatch.setenv("AGENT_DATA_ROOT", str(data_root))
    monkeypatch.setenv("PROMPTS_DIR", str(prompts_dir))

    return {"agents_root": agents_root, "data_root": data_root}


def test_bestandsagent_ohne_flag_oeffnet_keine_zweite_imap_verbindung(agent_env, mocker):
    """AUTO-01 im Polling-Kontext: kein ENABLE_AUTO_SORT gesetzt -> gar kein
    ImapClient-Aufbau fuer den Sortierlauf, kein run_auto_sort-Aufruf."""
    agents_root = agent_env["agents_root"]
    _make_agent_dir(agents_root, "bestandsagent")

    mocker.patch("src.main._resolve_drafts_folder", side_effect=lambda cfg, d, s, l: (cfg, "provider"))
    mocker.patch("src.main._poll_once")
    imap_client_mock = mocker.patch("src.main.ImapClient")
    run_auto_sort_mock = mocker.patch("src.main.auto_sort.run_auto_sort")

    main_module._run_cycle(logging.getLogger("test"))

    assert not imap_client_mock.called
    assert not run_auto_sort_mock.called


def test_enabled_agent_calls_run_auto_sort_with_own_imap_connection(agent_env, mocker):
    agents_root = agent_env["agents_root"]
    _make_agent_dir(agents_root, "auto-agent", extra_env={"ENABLE_AUTO_SORT": "true"})

    mocker.patch("src.main._resolve_drafts_folder", side_effect=lambda cfg, d, s, l: (cfg, "provider"))
    mocker.patch("src.main._poll_once")
    mock_mailbox = mocker.MagicMock()
    mock_mailbox.client.capabilities = ()
    mocker.patch("src.main.ImapClient").return_value.__enter__.return_value = mock_mailbox
    run_auto_sort_mock = mocker.patch("src.main.auto_sort.run_auto_sort")

    main_module._run_cycle(logging.getLogger("test"))

    assert run_auto_sort_mock.call_count == 1
    called_config = run_auto_sort_mock.call_args.args[0]
    assert called_config.agent_id == "auto-agent"


def test_auto_sort_failure_does_not_isolate_the_whole_agent(agent_env, mocker):
    """Ein Fehler im optionalen Sortierlauf darf die Kern-Funktion (Draft-
    Erstellung) nicht als 'agent_cycle_failed' rot einfaerben — anders als
    ein Fehler in _poll_once selbst."""
    agents_root = agent_env["agents_root"]
    data_root = agent_env["data_root"]
    _make_agent_dir(agents_root, "auto-agent", extra_env={"ENABLE_AUTO_SORT": "true"})

    mocker.patch("src.main._resolve_drafts_folder", side_effect=lambda cfg, d, s, l: (cfg, "provider"))
    mock_poll = mocker.patch("src.main._poll_once")
    mocker.patch("src.main.ImapClient", side_effect=RuntimeError("imap boom"))

    main_module._run_cycle(logging.getLogger("test"))

    # _poll_once (die Kern-Funktion) lief erfolgreich durch, BEVOR der
    # Sortierlauf scheiterte.
    assert mock_poll.called
    status = (data_root / "agents" / "auto-agent" / "agent_status.json")
    import json
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data.get("error") is None
