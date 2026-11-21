"""Drift-Guard fuer das Aktions-Journal-Schema (Phase 14, Plan 14-01, AUTO-04).

`agent/src/action_journal.py` und `webui/src/action_journal.py` sind KEIN
byte-identisches Duplikat (anders als pii.py/llm.py, siehe deren
Modul-Docstrings fuer die Begruendung) — der Agent-Teil ist ein schmaler
Schreib-Zwilling ohne die agents_io-Abhaengigkeit des WebUI-Teils. Beide
MUESSEN aber exakt dasselbe Tabellenschema erzeugen, weil beide Container
dieselbe physische SQLite-Datei (`/data/webui/action_journal.db`, geteiltes
agent-data-Volume) beschreiben. Dieser Test vergleicht NUR die beiden
schema-tragenden Konstanten (`SCHEMA_SQL`/`_QUELLE_MIGRATION_SQL`) per Regex-
Extraktion aus dem Quelltext — kein Modul-Import, weil beide Pakete denselben
Top-Level-Namen `src` verwenden und in einem Prozess nicht gleichzeitig
importierbar sind.

Diese Testdatei existiert ebenfalls identisch in beiden Services."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_COPY = _REPO_ROOT / "agent" / "src" / "action_journal.py"
_WEBUI_COPY = _REPO_ROOT / "webui" / "src" / "action_journal.py"


def _extract_constant(text: str, name: str) -> str:
    """Extrahiert eine Konstanten-Zuweisung `NAME = ...` aus Python-Quelltext
    (triple-quoted oder einfach angefuehrt). Wirft AssertionError mit
    Klartext, wenn die Konstante fehlt — ein stiller Leerstring-Vergleich
    waere ein falsch-gruener Test."""
    triple = re.search(rf'^{re.escape(name)}\s*=\s*"""(.*?)"""', text, re.MULTILINE | re.DOTALL)
    if triple:
        return triple.group(1)
    single = re.search(rf'^{re.escape(name)}\s*=\s*"(.*?)"\s*$', text, re.MULTILINE)
    if single:
        return single.group(1)
    raise AssertionError(f"Konstante {name!r} nicht gefunden — Scanner-Regex kaputt oder Konstante umbenannt?")


@pytest.mark.parametrize("constant_name", ["SCHEMA_SQL", "_QUELLE_MIGRATION_SQL", "_VORGANG_MIGRATION_SQL"])
def test_action_journal_schema_constant_matches_between_services(constant_name):
    if not (_AGENT_COPY.exists() and _WEBUI_COPY.exists()):
        pytest.skip(
            "Schwester-Service nicht im Dateisystem (Docker-Image-Build) — "
            "Sync-Check läuft nur im Repo-Checkout/CI."
        )

    agent_text = _AGENT_COPY.read_text(encoding="utf-8")
    webui_text = _WEBUI_COPY.read_text(encoding="utf-8")

    agent_value = _extract_constant(agent_text, constant_name)
    webui_value = _extract_constant(webui_text, constant_name)

    assert agent_value == webui_value, (
        f"{constant_name} ist zwischen agent/src/action_journal.py und "
        f"webui/src/action_journal.py auseinandergelaufen — beide Container "
        f"schreiben in dieselbe physische SQLite-Datei und MÜSSEN dasselbe "
        f"Tabellenschema erzeugen. Änderung bitte in BEIDE Dateien übernehmen."
    )
