"""Statusdatei-Zustandsmaschine fuer das Self-Update (260728-m8g, Stufe 2, D-5).

Die EINE Wahrheitsquelle fuer den Update-Fortschritt. Liegt bewusst im
`agent-data`-Volume (Default `/data/vizpatch_update.json`, dasselbe Volume wie
`agent_status.json`), damit sie einen WebUI-Neustart ueberlebt — genau in der
Phase, in der die WebUI selbst gerade den Container wechselt, muss der
Betreiber (bzw. das Add-in) trotzdem noch sehen koennen, was passiert.

Schreib-Uebergabe (siehe PLAN-Interface): die WebUI schreibt die Schritte bis
`launch_updater`, danach uebernimmt der Sidecar (`updater.py`) exklusiv. Dieses
Modul kennt diese Aufteilung nicht — es ist nur die Datenstruktur + die
Lock-/Stale-Logik, die von BEIDEN Seiten benutzt wird.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_SUCCESS = "success"
STATE_ROLLED_BACK = "rolled_back"
STATE_FAILED = "failed"

TERMINAL_STATES = {STATE_SUCCESS, STATE_ROLLED_BACK, STATE_FAILED}

# Der Sidecar kann hart gekillt werden (OOM, Host-Neustart, `docker kill`) ohne
# die Chance, einen Endzustand zu schreiben. Ohne Stale-Erkennung waere die
# Installation dann DAUERHAFT fuer weitere Update-Versuche gesperrt — der
# Betreiber haette keinen Weg mehr, ohne SSH wieder herauszukommen.
STALE_AFTER_SECONDS = 900

_STEP_PROGRESS = {
    "preparing": 2,
    # "download" wird gesondert behandelt (siehe progress_for) — linear 5..55.
    "verify": 60,
    "extract": 68,
    "load_images": 80,
    "launch_updater": 85,
    "stopping_agent": 88,
    "switching": 90,
    "health": 95,
    "rolling_back": 97,
    "done": 100,
}


def _status_path() -> Path:
    return Path(os.getenv("VIZPATCH_UPDATE_STATUS_FILE", "/data/vizpatch_update.json"))


def _idle_skeleton() -> dict:
    return {
        "state": STATE_IDLE,
        "step": "",
        "progress": 0,
        "version_from": None,
        "version_to": None,
        "message": "",
        "recovery_hint": "",
        "started_at": None,
        "updated_at": None,
    }


def read_status() -> dict:
    """Liest die Statusdatei. Fehlende/kaputte Datei -> idle-Skelett, NIE eine
    Exception (Konvention wie `state_reader`/`status_writer`)."""
    path = _status_path()
    try:
        if not path.exists():
            return _idle_skeleton()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("update status file did not contain a JSON object")
        merged = _idle_skeleton()
        merged.update(raw)
        return merged
    except Exception as e:
        logger.warning(
            "update_status_read_failed", extra={"exception_type": type(e).__name__}
        )
        return _idle_skeleton()


def write_status(**fields) -> dict:
    """Mergt `fields` in den Bestand und schreibt ATOMAR (`<pfad>.tmp` +
    `os.replace`). Teil-Updates ueberschreiben nicht angegebene Felder (z. B.
    `version_to`) NICHT. Setzt `updated_at` immer selbst. Ein Schreibfehler wird
    nur geloggt — ein kaputtgehendes Statusschreiben darf ein laufendes Update
    nicht abbrechen."""
    current = read_status()
    current.update(fields)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()

    path = _status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception as e:
        logger.warning(
            "update_status_write_failed", extra={"exception_type": type(e).__name__}
        )
    return current


def _parse_updated_at(raw) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_stale(status: dict, now: datetime | None = None) -> bool:
    """Nur fuer `state == RUNNING` relevant. True wenn `updated_at` fehlt,
    unparsbar oder aelter als `STALE_AFTER_SECONDS` ist. Ein hart gekillter
    Sidecar darf die Installation nicht dauerhaft sperren."""
    if status.get("state") != STATE_RUNNING:
        return False
    dt = _parse_updated_at(status.get("updated_at"))
    if dt is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds() > STALE_AFTER_SECONDS


def can_start(status: dict, now: datetime | None = None) -> tuple[bool, str]:
    """Doppelklick-/Parallelschutz (D-5): False NUR bei `RUNNING` und NICHT
    stale. Ein abgelaufenes (stale) `running` wird uebernommen (True)."""
    if status.get("state") != STATE_RUNNING:
        return True, ""
    if is_stale(status, now):
        return True, ""
    step = status.get("step") or "unbekannt"
    return False, f"Es läuft bereits ein Update (Schritt: {step})."


def progress_for(step: str, done: int = 0, total: int = 0) -> int:
    """Feste Fortschritts-Stufen je Schritt. Innerhalb von `download` linear
    zwischen 5 und 55 interpoliert (wenn `total > 0`), sonst 5 (keine
    ZeroDivisionError bei unbekannter Gesamtgroesse)."""
    if step == "download":
        if not total or total <= 0:
            return 5
        fraction = max(0.0, min(1.0, done / total))
        return int(5 + fraction * 50)
    return _STEP_PROGRESS.get(step, 0)


def reset_for_tests() -> None:
    """Analog `update_check.reset_cache()` — loescht die Statusdatei, falls
    vorhanden, damit Tests sich nicht gegenseitig beeinflussen."""
    path = _status_path()
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(
            "update_status_reset_failed", extra={"exception_type": type(e).__name__}
        )
