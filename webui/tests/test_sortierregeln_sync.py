"""Drift-Guard fuer den sortierregeln.py-Sync-Kontrakt (Phase 14, Plan 14-01).

`agent/src/sortierregeln.py` und `webui/src/sortierregeln.py` sind BEWUSST
zwei identische Kopien — dieselbe Nicht-Entscheidung wie bei `pii.py`/`llm.py`
(siehe `test_pii_sync.py`): Deployment sind zwei getrennte Docker-Images, ein
Shared-Package ist bewusst nicht gewaehlt. Die WebUI braucht den Parser fuer
das Sortierregeln-Formular (Validierungs-Vorschau/Speichern), der Agent fuer
den automatischen Sortierlauf — beide MUESSEN exakt dieselben Kategorien aus
derselben Datei lesen. Dieser Test schlaegt fehl, sobald die Kopien
auseinanderlaufen. Im Docker-Image (nur ein Service im Dateisystem) wird er
geskippt — der Guard greift im Repo-Checkout/CI.

Diese Testdatei existiert ebenfalls identisch in beiden Services."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_COPY = _REPO_ROOT / "agent" / "src" / "sortierregeln.py"
_WEBUI_COPY = _REPO_ROOT / "webui" / "src" / "sortierregeln.py"


def test_sortierregeln_module_copies_are_byte_identical():
    if not (_AGENT_COPY.exists() and _WEBUI_COPY.exists()):
        pytest.skip(
            "Schwester-Service nicht im Dateisystem (Docker-Image-Build) — "
            "Sync-Check läuft nur im Repo-Checkout/CI."
        )

    agent_hash = hashlib.sha256(_AGENT_COPY.read_bytes()).hexdigest()
    webui_hash = hashlib.sha256(_WEBUI_COPY.read_bytes()).hexdigest()
    assert agent_hash == webui_hash, (
        "agent/src/sortierregeln.py und webui/src/sortierregeln.py sind "
        "auseinandergelaufen — der Sortierregeln-Parser MUSS in beiden "
        "Services identisch bleiben. Änderung bitte in BEIDE Dateien übernehmen."
    )
