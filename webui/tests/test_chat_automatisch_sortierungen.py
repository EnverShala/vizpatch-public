"""Tests für das Werkzeug `automatisch_sortierungen` (Phase 14, Plan 14-01,
AUTO-04) — reine Journal-Abfrage, was der automatische Sortierlauf des
Polling-Agenten erledigt hat. KEIN IMAP-Zugriff, bewegt/ändert nichts."""
from __future__ import annotations

import pytest

from .test_chat_tools import _setup_env, _write_agent_env


def test_keine_eintraege_liefert_hinweis(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.automatisch_sortierungen("info")

    assert ergebnis["aktionen"] == []
    assert "hinweis" in ergebnis
    assert ergebnis["zeitraum_stunden"] == 24


def test_liefert_nur_automatik_eintraege_nicht_chat(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Rechnungen",
        [{"uid": "1", "message_id": "<a@example.com>"}],
        quelle="automatik",
    )
    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Manuell",
        [{"uid": "2", "message_id": "<b@example.com>"}],
    )

    ergebnis = chat_tools.automatisch_sortierungen("info")

    assert len(ergebnis["aktionen"]) == 1
    assert ergebnis["aktionen"][0]["ziel_ordner"] == "INBOX.Rechnungen"


def test_stunden_wird_begrenzt_nach_oben_und_unten(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    assert chat_tools.automatisch_sortierungen("info", stunden=0)["zeitraum_stunden"] == 1
    assert chat_tools.automatisch_sortierungen("info", stunden=999999)["zeitraum_stunden"] == 24 * 30


def test_invalider_agent_id_wirft_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.automatisch_sortierungen("../evil")


# --- Betriebsgrenzen im Bericht (Quick 260822-4ay, Befund 3) ---------------


def _write_agent_env_mit_automatik(agent_id, **extra):
    import src.agents_io as agents_io

    werte = {
        "IMAP_USER": "info@ionos.de",
        "IMAP_PASSWORD": "imap-pw",
        "LLM_API_KEY": "sk-ant-test-key",
        "LLM_PROVIDER": "anthropic",
    }
    werte.update(extra)
    agents_io.write_env(agent_id, werte)


def test_bericht_nennt_immer_den_deckel_pro_zyklus(tmp_path, monkeypatch):
    """Befund 3 der Live-Abnahme: der Bericht nannte nur die Journal-Gruppen.
    Das Modell KONNTE den Kostendeckel nicht kennen und behauptete daraufhin
    Vollständigkeit ('alle Rechnungen erfasst'), obwohl der Lauf nur 50 Mails
    angesehen hatte."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env_mit_automatik(
        "info", ENABLE_AUTO_SORT="true", AUTO_SORT_DRY_RUN="false", AUTO_SORT_MAX_MAILS="50"
    )
    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Rechnungen",
        [{"uid": "1", "message_id": "<a@example.com>"}],
        quelle="automatik",
    )

    ergebnis = chat_tools.automatisch_sortierungen("info")

    assert ergebnis["deckel_pro_zyklus"] == 50
    assert ergebnis["automatik_aktiv"] is True
    assert ergebnis["probelauf_aktiv"] is False
    assert "50" in ergebnis["hinweis"]
    assert "NICHT" in ergebnis["hinweis"]


def test_eigener_deckel_aus_der_env_wird_berichtet(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env_mit_automatik(
        "info", ENABLE_AUTO_SORT="true", AUTO_SORT_DRY_RUN="false", AUTO_SORT_MAX_MAILS="120"
    )
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.automatisch_sortierungen("info")

    assert ergebnis["deckel_pro_zyklus"] == 120
    assert "120" in ergebnis["hinweis"]


def test_unlesbarer_deckel_faellt_auf_den_default_zurueck(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env_mit_automatik("info", ENABLE_AUTO_SORT="true", AUTO_SORT_MAX_MAILS="viele")
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.automatisch_sortierungen("info")

    assert ergebnis["deckel_pro_zyklus"] == chat_tools.AUTO_SORT_MAX_MAILS_DEFAULT


def test_leere_liste_bei_aktivem_probelauf_erklaert_sich(tmp_path, monkeypatch):
    """Eine leere Liste heisst NICHT 'nichts gefunden' — bei aktivem Probelauf
    heisst sie 'absichtlich nichts bewegt'. Vorher war beides derselbe Satz."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env_mit_automatik("info", ENABLE_AUTO_SORT="true", AUTO_SORT_DRY_RUN="true")
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.automatisch_sortierungen("info")

    assert ergebnis["aktionen"] == []
    assert ergebnis["probelauf_aktiv"] is True
    assert "Probelauf" in ergebnis["hinweis"]


def test_leere_liste_bei_abgeschalteter_automatik_erklaert_sich(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env_mit_automatik("info")
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.automatisch_sortierungen("info")

    assert ergebnis["automatik_aktiv"] is False
    assert "ausgeschaltet" in ergebnis["hinweis"]


def test_deckel_default_haelt_mit_der_agent_config_schritt(tmp_path, monkeypatch):
    """Drift-Guard: `AUTO_SORT_MAX_MAILS_DEFAULT` in chat_tools ist eine Kopie
    des Defaults aus `agent/src/config.py` — der Wert entsteht dort, hier wird
    er nur berichtet. Laufen die beiden auseinander, nennt der Bericht einen
    Deckel, der nicht gilt."""
    from pathlib import Path

    import src.chat_tools as chat_tools

    agent_config = Path(__file__).resolve().parents[2] / "agent" / "src" / "config.py"
    if not agent_config.exists():
        pytest.skip(
            "Schwester-Service nicht im Dateisystem (Docker-Image-Build) — "
            "Sync-Check läuft nur im Repo-Checkout/CI."
        )

    quelle = agent_config.read_text(encoding="utf-8")
    assert f"auto_sort_max_mails: int = {chat_tools.AUTO_SORT_MAX_MAILS_DEFAULT}" in quelle, (
        "Der Default für AUTO_SORT_MAX_MAILS ist in agent/src/config.py "
        "geändert worden — AUTO_SORT_MAX_MAILS_DEFAULT in webui/src/chat_tools.py "
        "mitziehen, sonst berichtet automatisch_sortierungen einen falschen Deckel."
    )


def test_registrierung_vollstaendig(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    assert "automatisch_sortierungen" in chat_tools.TOOL_HANDLERS
    assert "automatisch_sortierungen" in {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "automatisch_sortierungen" in chat_tools._TOOL_ACTIVITY_LABELS
    assert "automatisch_sortierungen" not in chat_tools._ANON_AWARE_TOOLS
