"""SQLite-State-Layer. processed_emails (dedup) + meta (first_run_at Marker)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_emails (
  message_id TEXT PRIMARY KEY,
  uid INTEGER NOT NULL,
  from_address TEXT,
  subject TEXT,
  classification TEXT NOT NULL,
  draft_created INTEGER NOT NULL,
  error_message TEXT,
  processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_emails(processed_at);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auto_sorted_emails (
  message_id TEXT PRIMARY KEY,
  uid INTEGER NOT NULL,
  aktion TEXT NOT NULL,
  kategorie TEXT NOT NULL,
  ziel_ordner TEXT,
  dry_run INTEGER NOT NULL DEFAULT 0,
  sorted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Urlaubs-Autoantwort (URL-01..09): Merkliste "wem wurde in DIESEM
-- Urlaubszeitraum schon geantwortet". Der Schluessel ist bewusst
-- (absender, zeitraum) und NICHT die message_id: geantwortet wird EINMAL pro
-- Absender pro Zeitraum, egal wie viele Mails derselbe Mensch schreibt.
-- Ein neuer Zeitraum (anderes Von/Bis) ist ein anderer Schluessel und beginnt
-- damit von vorn — genau das ist gewollt.
CREATE TABLE IF NOT EXISTS urlaub_antworten (
  absender TEXT NOT NULL,
  zeitraum TEXT NOT NULL,
  message_id TEXT,
  dry_run INTEGER NOT NULL DEFAULT 0,
  beantwortet_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (absender, zeitraum)
);
"""


def init_db(db_path: Path | str) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def is_processed(db_path: Path | str, message_id: str) -> bool:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_emails WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return row is not None


def mark_processed(
    db_path: Path | str,
    message_id: str,
    uid: int,
    from_address: str,
    subject: str,
    classification: str,
    draft_created: bool,
    error_message: Optional[str] = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO processed_emails
                (message_id, uid, from_address, subject, classification, draft_created, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, uid, from_address, subject, classification, int(draft_created), error_message),
        )


def is_auto_sorted(db_path: Path | str, message_id: str, *, dry_run_lauf: bool = False) -> bool:
    """Phase 14 (AUTO-01..08): true, wenn diese Mail in einem früheren
    Poll-Zyklus bereits durch den automatischen Sortierlauf beurteilt wurde —
    verhindert, dass dieselbe gelesene Mail Zyklus für Zyklus erneut
    klassifiziert wird (Kostendeckel AUTO-06).

    `dry_run_lauf` sagt, WAS für ein Lauf gerade fragt (Befund der Live-Abnahme
    vom 2026-08-20, gefixt in Quick 260822-4ay):

      * **scharfer Lauf** (`False`, Default) — es blocken nur Mails, die ein
        früherer SCHARFER Lauf beurteilt hat. Ein Probelauf-Eintrag blockt
        NICHT. Vorher zählte jeder Eintrag: wer den Probelauf zum Prüfen nutzte
        und danach die Checkbox scharf stellte, bekam für genau die Mails, die
        der Probelauf als sortierbar erkannt hatte, nie eine Aktion — der
        Probelauf verbrauchte seine eigenen Treffer.
      * **Probelauf** (`True`) — es blockt JEDER Eintrag. Der Kostendeckel
        bleibt damit unangetastet: ein dauerhaft aktiver Probelauf
        klassifiziert dieselbe Mail nicht Zyklus für Zyklus neu.
    """
    sql = "SELECT 1 FROM auto_sorted_emails WHERE message_id = ?"
    if not dry_run_lauf:
        sql += " AND dry_run = 0"
    with connect(db_path) as conn:
        row = conn.execute(sql, (message_id,)).fetchone()
        return row is not None


def mark_auto_sorted(
    db_path: Path | str,
    message_id: str,
    uid: int,
    aktion: str,
    kategorie: str,
    ziel_ordner: str = "",
    dry_run: bool = False,
) -> None:
    """Merkt eine Mail als vom automatischen Sortierlauf beurteilt vor
    (Phase 14) — auch im Probelauf (AUTO-05): der Probelauf soll dieselbe
    Mail nicht Zyklus für Zyklus erneut klassifizieren, er unterscheidet sich
    von einem scharfen Lauf NUR darin, dass er nichts über IMAP bewegt/markiert
    und keinen Journal-Eintrag schreibt."""
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO auto_sorted_emails
                (message_id, uid, aktion, kategorie, ziel_ordner, dry_run)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, uid, aktion, kategorie, ziel_ordner, int(dry_run)),
        )


def hat_urlaubsantwort(
    db_path: Path | str, absender: str, zeitraum: str, *, dry_run_lauf: bool = False
) -> bool:
    """True, wenn dieser Absender in diesem Urlaubszeitraum schon eine
    Autoantwort bekommen hat (URL-05, der Kern des Schleifenschutzes).

    `dry_run_lauf` unterscheidet wie `is_auto_sorted`, WER fragt:

      * **scharfer Lauf** (`False`, Default) — es blocken nur ECHT versendete
        Antworten. Ein Probelauf-Eintrag blockt NICHT, sonst haette der
        Probelauf seine eigenen Treffer verbraucht und der Betreiber bekaeme
        nach dem Scharfschalten fuer genau die beobachteten Absender nie eine
        Antwort (derselbe Befund wie bei `is_auto_sorted`, Quick 260822-4ay).
      * **Probelauf** (`True`) — jeder Eintrag blockt, damit ein dauerhaft
        laufender Probelauf denselben Absender nicht Zyklus fuer Zyklus erneut
        meldet.
    """
    sql = "SELECT 1 FROM urlaub_antworten WHERE absender = ? AND zeitraum = ?"
    if not dry_run_lauf:
        sql += " AND dry_run = 0"
    with connect(db_path) as conn:
        row = conn.execute(sql, (_absender_schluessel(absender), zeitraum)).fetchone()
        return row is not None


def mark_urlaubsantwort(
    db_path: Path | str,
    absender: str,
    zeitraum: str,
    message_id: str = "",
    dry_run: bool = False,
) -> None:
    """Merkt eine (auch nur simulierte) Urlaubsantwort vor.

    `INSERT OR REPLACE`: ein spaeterer SCHARFER Lauf ueberschreibt den
    Probelauf-Eintrag desselben Absenders und setzt `dry_run` auf 0 — danach
    blockt der Eintrag auch fuer scharfe Laeufe."""
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO urlaub_antworten
                (absender, zeitraum, message_id, dry_run)
            VALUES (?, ?, ?, ?)
            """,
            (_absender_schluessel(absender), zeitraum, message_id, int(dry_run)),
        )


def zaehle_urlaubsantworten(db_path: Path | str, zeitraum: str, *, nur_scharf: bool = True) -> int:
    """Anzahl der Absender, die in diesem Zeitraum bereits eine Antwort haben —
    fuer das Zyklus-Log (der Betreiber will sehen, wie viele Autoantworten sein
    Urlaub insgesamt ausgeloest hat)."""
    sql = "SELECT COUNT(*) AS n FROM urlaub_antworten WHERE zeitraum = ?"
    if nur_scharf:
        sql += " AND dry_run = 0"
    with connect(db_path) as conn:
        row = conn.execute(sql, (zeitraum,)).fetchone()
        return int(row["n"]) if row else 0


def _absender_schluessel(absender: str) -> str:
    """Normalisierung des Absenders fuer die Merkliste: getrimmt + casefold.
    Ohne sie waeren "Max@Example.DE" und "max@example.de" zwei Schluessel und
    derselbe Mensch bekaeme zwei Autoantworten."""
    return (absender or "").strip().casefold()


def get_meta(db_path: Path | str, key: str) -> Optional[str]:
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(db_path: Path | str, key: str, value: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, value),
        )


def get_or_set_first_run(db_path: Path | str) -> datetime:
    """Return the first_run_at timestamp, setting it on first call."""
    existing = get_meta(db_path, "first_run_at")
    if existing:
        return datetime.fromisoformat(existing)
    now = datetime.now(timezone.utc)
    set_meta(db_path, "first_run_at", now.isoformat())
    return now
