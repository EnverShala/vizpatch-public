"""Serverseitige Chat-Verlaufs-Persistenz (Quick 260812-hhm, Task 4, D-hhm-09).

Webui-eigene SQLite-DB unter `/data/webui/chat_history.db` — NICHT die
Agent-DB (`/data/agents/<id>/state.db`, die liest `state_reader` bewusst
read-only). Kein `chmod`/`chown` an bestehenden Dateien: der webui-Container
läuft als root, der Agent als UID 1000; die bekannte Rechte-Falle entsteht
genau dann, wenn root eine Datei anlegt, die der Agent lesen/schreiben muss.
Der Agent liest diese DB nie.

Alle Funktionen sind fail-soft (D-hhm-09): jede Exception -> `logger.warning`
+ neutrales Ergebnis (leere Liste / `False` / `0`). Ein kaputtes/nicht
beschreibbares `/data` darf den Chat NIE zum 500er machen.

Persistenz-Schlüssel: `(agent_id, session_id)`. `agent_id` läuft gegen
`agents_io.AGENT_ID_PATTERN`, `session_id` gegen `SESSION_ID_PATTERN` — beide
Allowlists, ein ungültiger Wert führt zu einem stillen No-Op statt eines
Fehlers (der Aufrufer bekommt trotzdem eine funktionierende, nur leere
Antwort). Alle SQL-Statements sind ausschließlich parametrisiert (`?`) — nie
String-Interpolation (T-hhm-09).
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .agents_io import AGENT_ID_PATTERN

logger = logging.getLogger("vizpatch.chat_history")

# `session_id` stammt von `crypto.randomUUID()` im Browser (chat.js) — die
# Allowlist ist bewusst etwas großzügiger als ein reines UUID-Muster (erlaubt
# auch den JS-Fallback `'sess-' + Date.now() + '-' + Math.random()...`).
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Aufbewahrung der Chat-Verläufe. Betreiber-Entscheidung 2026-08-30 (vorher 30):
# Verläufe sind Arbeitskontext, kein Archiv — die Mails selbst liegen ohnehin im
# Postfach. Zwei Wochen überbrücken auch eine Abwesenheit; alles darüber speichert
# Mailinhalte länger, als sie jemand liest. Überschreibbar per
# `CHAT_HISTORY_RETENTION_DAYS`.
RETENTION_DAYS_DEFAULT = 14
MAX_TURNS_PER_SESSION = 400
MAX_TURN_CHARS = 20_000

_VALID_ROLES = ("user", "assistant")


def _data_root() -> Path:
    return Path(os.getenv("WEBUI_DATA_ROOT", "/data"))


def _db_path() -> Path:
    return _data_root() / "webui" / "chat_history.db"


def _retention_days() -> int:
    raw = (os.getenv("CHAT_HISTORY_RETENTION_DAYS") or "").strip()
    if not raw:
        return RETENTION_DAYS_DEFAULT
    try:
        return int(raw)
    except ValueError:
        return RETENTION_DAYS_DEFAULT


def _valid_agent_id(agent_id: str) -> bool:
    return bool(AGENT_ID_PATTERN.match(agent_id or ""))


def _valid_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_PATTERN.match(session_id or ""))


def _connect() -> sqlite3.Connection:
    """Öffnet die Verlaufs-DB, legt bei Bedarf das Verzeichnis + Schema an
    (idempotent bei jedem Connect — `CREATE TABLE/INDEX IF NOT EXISTS`)."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_turns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_turns_session "
        "ON chat_turns(agent_id, session_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_turns_created ON chat_turns(created_at)"
    )
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_turns(agent_id: str, session_id: str, turns: list[dict]) -> bool:
    """Schreibt `turns` (Liste von `{"role": "user"|"assistant", "content": str}`)
    für `(agent_id, session_id)` in EINER Transaktion und ruft danach
    `purge_expired()`. Invalider `agent_id`/`session_id`/leere `turns` ->
    No-Op (`False`), KEINE Exception. `role` außerhalb `user`/`assistant`
    wird übersprungen (kein Schreiben dieses einen Turns, der Rest läuft
    weiter). `content` wird auf `MAX_TURN_CHARS` gekappt."""
    if not _valid_agent_id(agent_id) or not _valid_session_id(session_id):
        return False
    if not turns:
        return False

    rows = []
    now = _now_iso()
    for turn in turns:
        role = turn.get("role") if isinstance(turn, dict) else None
        content = turn.get("content") if isinstance(turn, dict) else None
        if role not in _VALID_ROLES or not isinstance(content, str):
            continue
        rows.append((agent_id, session_id, role, content[:MAX_TURN_CHARS], now))
    if not rows:
        return False

    conn = None
    try:
        conn = _connect()
        conn.executemany(
            "INSERT INTO chat_turns(agent_id, session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    except Exception as e:
        logger.warning(
            "chat_history_append_failed", extra={"agent_id": agent_id, "error": str(e)}
        )
        return False
    finally:
        if conn is not None:
            conn.close()

    purge_expired()
    return True


def load_history(agent_id: str, session_id: str) -> list[dict]:
    """Liefert den persistierten Verlauf für `(agent_id, session_id)` als
    `[{"role", "content"}, ...]` in AUFSTEIGENDER Reihenfolge (ältester Turn
    zuerst). Mehr als `MAX_TURNS_PER_SESSION` Turns -> die JÜNGSTEN N,
    weiterhin aufsteigend sortiert. Invalider `agent_id`/`session_id`/DB-
    Fehler/fehlende DB -> `[]`, KEINE Exception."""
    if not _valid_agent_id(agent_id) or not _valid_session_id(session_id):
        return []

    conn = None
    try:
        conn = _connect()
        cur = conn.execute(
            "SELECT role, content FROM chat_turns "
            "WHERE agent_id = ? AND session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (agent_id, session_id, MAX_TURNS_PER_SESSION),
        )
        rows = cur.fetchall()
    except Exception as e:
        logger.warning(
            "chat_history_load_failed", extra={"agent_id": agent_id, "error": str(e)}
        )
        return []
    finally:
        if conn is not None:
            conn.close()

    rows.reverse()
    return [{"role": role, "content": content} for role, content in rows]


def clear_session(agent_id: str, session_id: str) -> int:
    """Löscht den Verlauf für GENAU `(agent_id, session_id)` — andere
    Sessions/Agents bleiben unberührt. Liefert die Anzahl gelöschter Turns
    (`0` bei invaliden IDs, fehlender DB oder DB-Fehler, KEINE Exception)."""
    if not _valid_agent_id(agent_id) or not _valid_session_id(session_id):
        return 0

    conn = None
    try:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM chat_turns WHERE agent_id = ? AND session_id = ?",
            (agent_id, session_id),
        )
        conn.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    except Exception as e:
        logger.warning(
            "chat_history_clear_failed", extra={"agent_id": agent_id, "error": str(e)}
        )
        return 0
    finally:
        if conn is not None:
            conn.close()


def purge_expired() -> int:
    """Entfernt Turns mit `created_at` älter als `CHAT_HISTORY_RETENTION_DAYS`
    (Default `RETENTION_DAYS_DEFAULT` = 14) — Vergleich per String gegen den
    UTC-ISO-8601-Cutoff (created_at wird konsistent als
    `datetime.now(timezone.utc).isoformat()` geschrieben, ISO-8601-Strings
    sind lexikographisch sortierbar). Fehlende DB/DB-Fehler -> `0`, KEINE
    Exception."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_retention_days())).isoformat()
    conn = None
    try:
        conn = _connect()
        cur = conn.execute("DELETE FROM chat_turns WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    except Exception as e:
        logger.warning("chat_history_purge_failed", extra={"error": str(e)})
        return 0
    finally:
        if conn is not None:
            conn.close()
