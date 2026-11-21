"""Schreibt automatische Sortier-Aktionen (Phase 14, AUTO-04) ins webui-eigene
Aktions-Journal (`/data/webui/action_journal.db`), damit sie über
`letzte_aktion_rueckgaengig` (webui/src/chat_tools.py, Phase 13) umkehrbar
sind und der Chat berichten kann, was automatisch einsortiert wurde
(`automatisch_sortierungen`, siehe webui/src/chat_tools.py).

KEIN Modul-Import von webui/src — die beiden Container sind getrennte Docker-
Images (dieselbe Nicht-Entscheidung wie bei `agent/src/pii.py` <->
`webui/src/pii.py`, siehe `test_pii_sync.py`). ANDERS als bei pii.py/llm.py
ist dies aber KEIN byte-identisches Duplikat von `webui/src/action_journal.py`:
jenes Modul importiert `agents_io` (Verschlüsselung, Verzeichnis-Verwaltung,
den Lesepfad für `letzte_aktion_rueckgaengig`) — Gewicht, das der Agent für
den einzigen hier benötigten Schreibpfad (`record_action`) nicht braucht und
nicht mitschleppen soll. Entscheidung dokumentiert im SUMMARY von Plan 14-01.

Stattdessen: EXAKT dasselbe Tabellenschema (`SCHEMA_SQL`/`_QUELLE_MIGRATION_SQL`,
Zeichen-für-Zeichen mit `webui/src/action_journal.py` synchron gehalten —
siehe `test_action_journal_schema_sync.py` in BEIDEN Suiten) und derselbe
physische Pfad (`AGENT_DATA_ROOT`/webui/action_journal.db bzw.
`WEBUI_DATA_ROOT`/webui/action_journal.db — beide Container mounten dasselbe
`agent-data`-Volume unter `/data`, siehe `agent/docker-compose.yml`). Der
Agent SCHREIBT ausschließlich `quelle='automatik'` — Lesen und
Rückgängigmachen bleiben ausschließlich Sache der WebUI (kein `last_action`/
`mark_undone` hier).

Fail-soft wie das Original: ungültige `agent_id`/leere `eintraege`/DB-Fehler
-> `""` (kein Schreiben), NIE eine Exception — ein kaputtes/nicht
beschreibbares `/data` darf einen Poll-Zyklus nie zum Absturz bringen (der
Sortierlauf selbst bleibt davon unberührt, siehe `auto_sort.py`: ein
fehlgeschlagener Journal-Write verhindert NICHT die eigentliche IMAP-Aktion,
er verhindert nur deren Rückgängig-Fähigkeit — geloggt, kein stiller
Datenverlust)."""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("vizpatch.action_journal")

# Identische Slug-Regel wie webui/src/agents_io.AGENT_ID_PATTERN bzw.
# agent/src/config.AGENT_SLUG_PATTERN — reine Werte-Validierung vor dem
# Schreiben in die fremde (webui-eigene) Journal-DB.
_AGENT_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")

# MUSS Zeichen-für-Zeichen mit webui/src/action_journal.SCHEMA_SQL identisch
# bleiben (test_action_journal_schema_sync.py in beiden Suiten).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS action_journal(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aktion_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    aktion TEXT NOT NULL,
    message_id TEXT NOT NULL,
    uid_zum_zeitpunkt TEXT NOT NULL,
    quell_ordner TEXT NOT NULL,
    ziel_ordner TEXT NOT NULL,
    betreff TEXT NOT NULL,
    zeit TEXT NOT NULL,
    rueckgaengig_gemacht INTEGER NOT NULL DEFAULT 0,
    unwiderruflich INTEGER NOT NULL DEFAULT 0
)
"""

# MUSS Zeichen-für-Zeichen mit webui/src/action_journal._QUELLE_MIGRATION_SQL
# identisch bleiben (test_action_journal_schema_sync.py in beiden Suiten).
_QUELLE_MIGRATION_SQL = "ALTER TABLE action_journal ADD COLUMN quelle TEXT NOT NULL DEFAULT 'chat'"


def _data_root() -> Path:
    return Path(os.getenv("AGENT_DATA_ROOT", "/data"))


def _db_path() -> Path:
    return _data_root() / "webui" / "action_journal.db"


def _valid_agent_id(agent_id: str) -> bool:
    return bool(_AGENT_ID_PATTERN.match(agent_id or ""))


# Klammer über ALLES, was zu EINEM Sortier-Zyklus gehört — MUSS Zeichen-für-
# Zeichen mit webui/src/action_journal._VORGANG_MIGRATION_SQL identisch bleiben
# (test_action_journal_schema_sync.py in beiden Suiten). Ohne sie kennt das
# Journal nur `aktion_id` = eine einzelne Kategorie-Bewegung, und
# `letzte_aktion_rueckgaengig` in der WebUI könnte einen Zyklus, der in mehrere
# Kategorien zerfällt, nur stückweise umkehren (Befund der Live-Abnahme vom
# 2026-08-19, siehe webui-Kopie für die ausführliche Begründung).
_VORGANG_MIGRATION_SQL = "ALTER TABLE action_journal ADD COLUMN vorgang_id TEXT NOT NULL DEFAULT ''"

# Prozess-lokaler „laufender Vorgang", identisches Muster wie in der WebUI.
_aktueller_vorgang: ContextVar[str] = ContextVar("vizpatch_vorgang_id", default="")


@contextmanager
def vorgang(vorgang_id: str = "") -> "Iterator[str]":
    """Klammert alle `record_action`-Aufrufe dieses Blocks zu EINEM Vorgang —
    im Agenten einmal je Sortier-Zyklus (`auto_sort.run_auto_sort`), damit ein
    Zyklus mit mehreren Kategorien in der WebUI als EINE Einheit rückgängig
    gemacht werden kann.

    Zurueckgesetzt wird ueber den GEMERKTEN Vorgaengerwert, NICHT ueber
    `ContextVar.reset(token)` — identisch zur webui-Kopie, siehe dort fuer den
    Live-Befund: ein Token laesst sich nur in DEMSELBEN Context zuruecksetzen,
    in dem er erzeugt wurde, und ein Generator wird nicht zwangslaeufig dort
    konsumiert, wo er entstand. Im Agenten laeuft der Sortierlauf zwar
    synchron, aber die beiden Kopien sollen sich nicht auseinanderentwickeln."""
    vorher = _aktueller_vorgang.get()
    neu = vorgang_id or uuid.uuid4().hex
    _aktueller_vorgang.set(neu)
    try:
        yield neu
    finally:
        _aktueller_vorgang.set(vorher)


def _ensure_vorgang_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(action_journal)").fetchall()}
    if "vorgang_id" not in cols:
        conn.execute(_VORGANG_MIGRATION_SQL)


def _ensure_quelle_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(action_journal)").fetchall()}
    if "quelle" not in cols:
        conn.execute(_QUELLE_MIGRATION_SQL)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(SCHEMA_SQL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_journal_agent_aktion "
        "ON action_journal(agent_id, aktion_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_journal_zeit ON action_journal(zeit)"
    )
    _ensure_quelle_column(conn)
    _ensure_vorgang_column(conn)
    conn.commit()
    return conn


def record_action(
    agent_id: str,
    aktion: str,
    quell_ordner: str,
    ziel_ordner: str,
    eintraege: list[dict],
) -> str:
    """Schmales Gegenstück zu `webui.action_journal.record_action` (siehe
    Modul-Docstring für die Begründung, warum dies KEIN Import ist) —
    schreibt EINEN Journal-Eintrag pro Element von `eintraege` unter einer
    neuen `aktion_id`, IMMER mit `quelle='automatik'` (der Agent schreibt nie
    im Namen des Chats). Fail-soft wie das Original: ungültige `agent_id`/
    leere `eintraege`/DB-Fehler -> `""`, NIE eine Exception."""
    if not _valid_agent_id(agent_id):
        return ""
    if not eintraege:
        return ""

    aktion_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    vorgang_wert = _aktueller_vorgang.get()
    rows = []
    for eintrag in eintraege:
        if not isinstance(eintrag, dict):
            continue
        uid = str(eintrag.get("uid") or "").strip()
        message_id = str(eintrag.get("message_id") or "").strip()
        betreff = str(eintrag.get("betreff") or "")
        rows.append(
            (
                aktion_id,
                agent_id,
                str(aktion or ""),
                message_id,
                uid,
                str(quell_ordner or ""),
                str(ziel_ordner or ""),
                betreff,
                now,
                0,
                0,
                "automatik",
                vorgang_wert,
            )
        )
    if not rows:
        return ""

    conn = None
    try:
        conn = _connect()
        conn.executemany(
            "INSERT INTO action_journal(aktion_id, agent_id, aktion, message_id, "
            "uid_zum_zeitpunkt, quell_ordner, ziel_ordner, betreff, zeit, "
            "rueckgaengig_gemacht, unwiderruflich, quelle, vorgang_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    except Exception as e:
        logger.warning("action_journal_record_failed", extra={"agent_id": agent_id, "error": str(e)})
        return ""
    finally:
        if conn is not None:
            conn.close()

    return aktion_id
