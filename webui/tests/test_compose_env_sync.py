"""Drift-Guard (Phase 13, Plan 13-01, Befund B): jede globale `ENABLE_*`/`MAX_*`-
Variable, die der webui-Code per `os.getenv("NAME"` oder dem duennen Wrapper
`_int_env("NAME"` liest, MUSS im Compose-Passthrough BEIDER Compose-Dateien
(`agent/docker-compose.yml`, `deployment/docker-compose.phase4.yml`) UND in
`deployment/kunde-env.example` vorkommen — sonst erreicht ein vom Betreiber in
der Installations-`.env` gesetzter Wert den Container nie, der Code-Default
greift stillschweigend (exakt der am 2026-08-17 verifizierte Befund:
`ENABLE_MAIL_SEND`/`ENABLE_CHAT_TOOLS`/`ENABLE_TRASH_CONFIRMATION`/
`ENABLE_TRASH_PURGE` fehlten in allen drei Dateien).

Bewusst NICHT einbezogen sind Per-Agent-Flags, die AUSSCHLIESSLICH ueber
`read_env_raw(agent_id).get("NAME")` (bzw. ein bereits geladenes `env`-Dict)
aus der Agent-eigenen `config/agents/<id>/.env` gelesen werden — z.B.
`ENABLE_PII_REDACTION`, `ENABLE_DRAFT_CREATION`, `ENABLE_STYLE_ADAPTION` sowie
(Phase 14, Plan 14-01, AUTO-01/05/06) `ENABLE_AUTO_SORT`/`AUTO_SORT_DRY_RUN`/
`AUTO_SORT_MAX_MAILS`. Diese Flags sind bewusst NICHT global schaltbar (kein
`os.getenv`-Fallback dafuer im Code) und duerfen deshalb nicht in den
Compose-Passthrough-Zwang fallen. Der Scanner erkennt sie strukturell nicht
(er matcht nur `os.getenv(`/`_int_env(`, kein `dict.get(`) — diese Abgrenzung
ist ABSICHT, nicht Zufall, und soll beim naechsten Umbau nicht versehentlich
aufgeweicht werden (z.B. indem jemand `dict.get(` mit in den Scanner
aufnimmt, ohne diese Flags explizit auszunehmen).

Seit Phase 14 (Plan 14-01) scannt dieser Test ZUSAETZLICH `agent/src/*.py` —
vorher deckte er nur `webui/src/*.py` ab, obwohl der Agent-Container genauso
globale `ENABLE_*`/`MAX_*`-Variablen per `os.getenv(...)` lesen koennte (aktuell
liest er keine — alle Verhaltens-Flags laufen dort ausschliesslich ueber die
per-Agent-`.env`, siehe `agent/src/config.py`). Diese Erweiterung ist bewusst
defensiv: sie faengt eine kuenftige globale Agent-Variable ab, die sonst denselben
Befund wiederholen wuerde wie `ENABLE_MAIL_SEND` u.a. oben.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_COMPOSE = _REPO_ROOT / "agent" / "docker-compose.yml"
_PHASE4_COMPOSE = _REPO_ROOT / "deployment" / "docker-compose.phase4.yml"
_KUNDE_ENV_EXAMPLE = _REPO_ROOT / "deployment" / "kunde-env.example"
_WEBUI_SRC = _REPO_ROOT / "webui" / "src"
# Phase 14, Plan 14-01: zusaetzlich zum webui-Service gescannt (siehe
# Modul-Docstring) — deckt kuenftige globale Agent-Variablen ab.
_AGENT_SRC = _REPO_ROOT / "agent" / "src"

# Matcht `os.getenv("NAME"` und `_int_env("NAME"` (auch `chat._int_env("NAME"`,
# da nur ab der letzten Punkt-Grenze vor dem Aufruf verlangt wird) — NICHT
# `dict.get("NAME")` (das waere ein Per-Agent-Read via `read_env_raw`, siehe
# Modul-Docstring).
_ENV_VAR_CALL_PATTERN = re.compile(
    r'(?:os\.getenv|_int_env)\(\s*["\']((?:ENABLE|MAX)_[A-Z0-9_]+)["\']'
)


def find_enable_max_env_vars(text: str) -> set[str]:
    """Sammelfunktion (eigenstaendig testbar, nimmt Text statt Pfade entgegen):
    liefert alle `ENABLE_*`/`MAX_*`-Variablennamen, die im uebergebenen
    Quelltext per `os.getenv(...)` oder `_int_env(...)` gelesen werden."""
    return set(_ENV_VAR_CALL_PATTERN.findall(text))


def _collect_vars_with_sources() -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for src_dir in (_WEBUI_SRC, _AGENT_SRC):
        for path in sorted(src_dir.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for var in find_enable_max_env_vars(text):
                found.setdefault(var, []).append(path)
    return found


# --- Scanner-Selbsttest (beweist, dass er tatsaechlich greift) ---------------


def test_find_enable_max_env_vars_detects_synthetic_getenv_call():
    synthetic = 'wert = os.getenv("ENABLE_ERFUNDEN")\n'
    assert find_enable_max_env_vars(synthetic) == {"ENABLE_ERFUNDEN"}


def test_find_enable_max_env_vars_detects_int_env_wrapper_call():
    synthetic = 'n = chat._int_env("MAX_ERFUNDEN", 5)\n'
    assert find_enable_max_env_vars(synthetic) == {"MAX_ERFUNDEN"}


def test_find_enable_max_env_vars_ignores_per_agent_dict_get():
    """`read_env_raw(...).get("NAME")` ist bewusst KEIN Treffer — Per-Agent-
    Flags ohne globalen Fallback fallen nicht unter den Passthrough-Zwang."""
    synthetic = 'x = (read_env_raw(agent_id).get("ENABLE_PII_REDACTION") or "true")\n'
    assert find_enable_max_env_vars(synthetic) == set()


def test_agent_src_ist_tatsaechlich_im_scan_erfasst(tmp_path, monkeypatch):
    """Phase 14, Plan 14-01: beweist die Erweiterung um `agent/src/*.py`
    (nicht nur behaupten) — eine SYNTHETISCHE globale Variable dort, die in
    keiner der drei Dateien vorkommt, MUSS als fehlend gemeldet werden,
    genau wie eine webui-seitige es täte."""
    fake_agent_src = tmp_path / "agent_src"
    fake_agent_src.mkdir()
    (fake_agent_src / "config.py").write_text(
        'wert = os.getenv("ENABLE_ERFUNDENES_AGENT_FLAG")\n', encoding="utf-8"
    )
    leerer_webui_src = tmp_path / "leer"
    leerer_webui_src.mkdir()

    import tests.test_compose_env_sync as mod

    monkeypatch.setattr(mod, "_AGENT_SRC", fake_agent_src)
    monkeypatch.setattr(mod, "_WEBUI_SRC", leerer_webui_src)

    found = mod._collect_vars_with_sources()

    assert "ENABLE_ERFUNDENES_AGENT_FLAG" in found


# --- Der eigentliche Drift-Guard ---------------------------------------------


def test_all_enable_max_env_vars_reach_compose_passthrough_and_kunde_env_example():
    found = _collect_vars_with_sources()
    assert found, "Scanner hat keine ENABLE_*/MAX_*-os.getenv/_int_env-Aufrufe gefunden — Regex kaputt?"

    agent_text = _AGENT_COMPOSE.read_text(encoding="utf-8")
    phase4_text = _PHASE4_COMPOSE.read_text(encoding="utf-8")
    kunde_text = _KUNDE_ENV_EXAMPLE.read_text(encoding="utf-8")

    missing: list[str] = []
    for var, source_files in sorted(found.items()):
        quelle = ", ".join(p.name for p in source_files)
        for label, text in (
            ("agent/docker-compose.yml", agent_text),
            ("deployment/docker-compose.phase4.yml", phase4_text),
            ("deployment/kunde-env.example", kunde_text),
        ):
            if var not in text:
                missing.append(f"{var} (gelesen in {quelle}) fehlt in {label}")

    assert not missing, (
        "Fehlende Compose-/Kunden-Env-Passthrough-Eintraege — ein vom Betreiber "
        "in der Installations-.env gesetzter Wert erreicht den Container dann "
        "NICHT, der Code-Default greift stillschweigend (derselbe Befund wie "
        "ENABLE_MAIL_SEND/ENABLE_CHAT_TOOLS/ENABLE_TRASH_CONFIRMATION/"
        "ENABLE_TRASH_PURGE, Phase 13/Plan 13-01):\n" + "\n".join(missing)
    )
