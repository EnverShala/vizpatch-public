"""Verdrahtung der Urlaubs-Autoantwort in den Polling-Zyklus (URL-01..09) —
Muster 1:1 wie `test_main_auto_sort.py`.

Kern-Erwartung: ein Bestandsagent verhält sich nach dem Update UNVERÄNDERT —
keine zusätzliche IMAP-Verbindung, kein Aufruf. Und ein Fehler in dieser
Zusatzfunktion darf den Agenten nicht rot färben; die Kern-Funktion
(Klassifikation + Entwurf) hat Vorrang.
"""
from __future__ import annotations

import json
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
    (agent_dir / ".env").write_text(
        "\n".join(f"{k}={v}" for k, v in env.items()) + "\n", encoding="utf-8"
    )
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


def test_bestandsagent_loest_keinen_urlaubslauf_aus(agent_env, mocker):
    """Der wichtigste Test der Datei: wer die neue Variable nicht gesetzt hat,
    bekommt nach dem Update keine einzige zusätzliche IMAP-Verbindung — und
    schon gar keine gesendete Mail."""
    _make_agent_dir(agent_env["agents_root"], "bestandsagent")

    mocker.patch("src.main._resolve_drafts_folder", side_effect=lambda c, d, s, l: (c, "provider"))
    mocker.patch("src.main._poll_once")
    imap = mocker.patch("src.main.ImapClient")
    lauf = mocker.patch("src.main.urlaub.run_urlaubsantwort")

    main_module._run_cycle(logging.getLogger("test"))

    assert not imap.called
    assert not lauf.called


def test_eingeschalteter_agent_bekommt_eigene_verbindung(agent_env, mocker):
    _make_agent_dir(
        agent_env["agents_root"], "urlaubs-agent", {"ENABLE_URLAUBSANTWORT": "true"}
    )

    mocker.patch("src.main._resolve_drafts_folder", side_effect=lambda c, d, s, l: (c, "provider"))
    mocker.patch("src.main._poll_once")
    mocker.patch("src.main.ImapClient").return_value.__enter__.return_value = mocker.MagicMock()
    lauf = mocker.patch("src.main.urlaub.run_urlaubsantwort")

    main_module._run_cycle(logging.getLogger("test"))

    assert lauf.call_count == 1
    assert lauf.call_args.args[0].agent_id == "urlaubs-agent"


def test_fehler_im_urlaubslauf_faerbt_den_agenten_nicht_rot(agent_env, mocker):
    """Die Urlaubsantwort ist eine Zusatzfunktion. Fällt sie aus, muss der
    Betreiber trotzdem sehen, dass Klassifikation und Entwürfe laufen."""
    _make_agent_dir(
        agent_env["agents_root"], "urlaubs-agent", {"ENABLE_URLAUBSANTWORT": "true"}
    )

    mocker.patch("src.main._resolve_drafts_folder", side_effect=lambda c, d, s, l: (c, "provider"))
    poll = mocker.patch("src.main._poll_once")
    mocker.patch("src.main.ImapClient", side_effect=RuntimeError("imap boom"))

    main_module._run_cycle(logging.getLogger("test"))

    assert poll.called
    status_datei = agent_env["data_root"] / "agents" / "urlaubs-agent" / "agent_status.json"
    status = json.loads(status_datei.read_text(encoding="utf-8"))
    assert not status.get("error")


def test_urlaubslauf_laeuft_nach_dem_polling(agent_env, mocker):
    """Reihenfolge zählt: erst Klassifikation/Entwürfe, dann Sortieren, dann
    die Autoantwort — die teuerste und riskanteste Funktion zuletzt."""
    _make_agent_dir(
        agent_env["agents_root"],
        "urlaubs-agent",
        {"ENABLE_URLAUBSANTWORT": "true", "ENABLE_AUTO_SORT": "true"},
    )

    reihenfolge = []
    mocker.patch("src.main._resolve_drafts_folder", side_effect=lambda c, d, s, l: (c, "provider"))
    mocker.patch("src.main._poll_once", side_effect=lambda *a: reihenfolge.append("poll"))
    mocker.patch("src.main.ImapClient").return_value.__enter__.return_value = mocker.MagicMock()
    mocker.patch(
        "src.main.auto_sort.run_auto_sort", side_effect=lambda *a: reihenfolge.append("sort")
    )
    mocker.patch(
        "src.main.urlaub.run_urlaubsantwort",
        side_effect=lambda *a: reihenfolge.append("urlaub"),
    )

    main_module._run_cycle(logging.getLogger("test"))

    assert reihenfolge == ["poll", "sort", "urlaub"]


def test_deaktivierter_agent_loest_nichts_aus(agent_env, mocker):
    _make_agent_dir(
        agent_env["agents_root"],
        "aus",
        {"AGENT_ENABLED": "false", "ENABLE_URLAUBSANTWORT": "true"},
    )

    lauf = mocker.patch("src.main.urlaub.run_urlaubsantwort")
    main_module._run_cycle(logging.getLogger("test"))
    assert not lauf.called
