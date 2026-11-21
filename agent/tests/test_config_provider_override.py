from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import load_config, REQUIRED_ENV_VARS


BASE_ENV = {
    "IMAP_USER": "x@ionos.de",
    "IMAP_PASSWORD": "pw",
    "OWN_EMAIL_ADDRESS": "x@ionos.de",
    "LLM_API_KEY": "sk-ant-test",
}


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_env(extra: dict, tmp_path: Path) -> dict:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "classify.txt").write_text("classify {company_name}", encoding="utf-8")
    (prompts_dir / "generate.txt").write_text(
        "generate {company_name} {context_md_full} {conversation_history} {from} {subject} {body}",
        encoding="utf-8",
    )
    context_file = tmp_path / "context.md"
    context_file.write_text("# Test", encoding="utf-8")
    env = {**BASE_ENV, **extra}
    env["PROMPTS_DIR"] = str(prompts_dir)
    env["CONTEXT_FILE"] = str(context_file)
    env["STATE_DB"] = str(tmp_path / "state.db")
    return env


def test_override_skips_auto_detect(tmp_path):
    env = _make_env({"IMAP_HOST": "imap.custom.de"}, tmp_path)
    mock_resolve = MagicMock(side_effect=AssertionError("should not be called"))
    with patch.dict(os.environ, env, clear=True):
        with patch("src.config.resolve_imap_config", mock_resolve):
            cfg = load_config()
    assert cfg.imap_host == "imap.custom.de"
    mock_resolve.assert_not_called()


def test_auto_detect_ionos(tmp_path):
    env = _make_env({}, tmp_path)
    with patch.dict(os.environ, env, clear=True):
        with patch("src.config.resolve_imap_config") as mock_resolve:
            mock_resolve.return_value = {
                "host": "imap.ionos.de", "port": 993, "ssl": True,
                "drafts": "Drafts", "sent": "Sent",
            }
            cfg = load_config()
    assert cfg.imap_host == "imap.ionos.de"
    assert cfg.imap_sent_folder == "Sent"


def test_auto_detect_drafts_override(tmp_path):
    env = _make_env({"IMAP_DRAFTS_FOLDER": "KI-Entwürfe"}, tmp_path)
    with patch.dict(os.environ, env, clear=True):
        with patch("src.config.resolve_imap_config") as mock_resolve:
            mock_resolve.return_value = {
                "host": "imap.ionos.de", "port": 993, "ssl": True,
                "drafts": "Drafts", "sent": "Sent",
            }
            cfg = load_config()
    assert cfg.imap_drafts_folder == "KI-Entwürfe"


def test_auto_detect_sent_override(tmp_path):
    env = _make_env({"IMAP_USER": "x@gmx.de", "IMAP_SENT_FOLDER": "Postausgang"}, tmp_path)
    with patch.dict(os.environ, env, clear=True):
        with patch("src.config.resolve_imap_config") as mock_resolve:
            mock_resolve.return_value = {
                "host": "imap.gmx.net", "port": 993, "ssl": True,
                "drafts": "Entwürfe", "sent": "Gesendet",
            }
            cfg = load_config()
    assert cfg.imap_sent_folder == "Postausgang"


def test_fail_fast_without_imap_user(tmp_path):
    env = _make_env({}, tmp_path)
    env.pop("IMAP_USER", None)
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="Missing required env vars"):
            load_config()


def test_unknown_domain_propagates_runtime_error(tmp_path):
    env = _make_env({"IMAP_USER": "x@voelligunbekannt-xyz.de"}, tmp_path)
    with patch.dict(os.environ, env, clear=True):
        with patch("src.config.resolve_imap_config", side_effect=RuntimeError("Bitte IMAP_HOST")):
            with pytest.raises(RuntimeError, match="Bitte IMAP_HOST"):
                load_config()


def test_explicit_drafts_folder_does_not_contaminate_next_agent(tmp_path):
    """Regression: zwei Agenten derselben Domain nacheinander laden (echte
    resolve_imap_config, KEIN Mock) — der erste mit explizitem
    IMAP_DRAFTS_FOLDER=INBOX (ungültig, wird von _build_config abgelehnt und isoliert
    seinen eigenen Agenten), der zweite ohne Override muss trotzdem den
    Provider-Default bekommen und darf NICHT vom ersten Aufruf kontaminiert sein.
    Ohne den Fix in resolve_imap_config (geteiltes Dict statt Kopie) würde der erste
    Aufruf STATIC_PROVIDERS["gmx.de"] mutieren und der zweite Aufruf bekäme "INBOX".
    """
    env1 = _make_env({"IMAP_USER": "a@gmx.de", "IMAP_DRAFTS_FOLDER": "INBOX"}, _mkdir(tmp_path / "a"))
    with patch.dict(os.environ, env1, clear=True):
        with pytest.raises(RuntimeError):
            load_config()

    env2 = _make_env({"IMAP_USER": "b@gmx.de"}, _mkdir(tmp_path / "b"))
    with patch.dict(os.environ, env2, clear=True):
        cfg2 = load_config()
    assert cfg2.imap_drafts_folder == "Entwürfe"


def test_explicit_sent_folder_override_does_not_contaminate_next_agent(tmp_path):
    """Analoge Kontaminations-Regression für IMAP_SENT_FOLDER, echte resolve_imap_config."""
    env1 = _make_env({"IMAP_USER": "a@gmx.de", "IMAP_SENT_FOLDER": "Postausgang"}, _mkdir(tmp_path / "a"))
    with patch.dict(os.environ, env1, clear=True):
        cfg1 = load_config()
    assert cfg1.imap_sent_folder == "Postausgang"

    env2 = _make_env({"IMAP_USER": "b@gmx.de"}, _mkdir(tmp_path / "b"))
    with patch.dict(os.environ, env2, clear=True):
        cfg2 = load_config()
    assert cfg2.imap_sent_folder == "Gesendet"
