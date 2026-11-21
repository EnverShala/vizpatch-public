"""Persistentes Aktions-Journal (Phase 13, Plan 13-03, MBOX-02/03).

Webui-eigene SQLite-DB unter `/data/webui/action_journal.db` — eigene Datei,
NICHT `chat_history.db` mitbenutzen (andere Lebensdauer/Zugriffsmuster, siehe
13-RESEARCH.md Abschnitt 9 / 13-PATTERNS.md Muster 7). Der Agent
(`/data/agents/<id>/state.db`) liest diese DB nie.

Zweck: jede Massen-Bewegung (`mail_verschieben`/`mails_verschieben`/
`mail_in_papierkorb`/`mails_in_papierkorb`) wird hier je Werkzeugaufruf unter
einer gemeinsamen `aktion_id` protokolliert — die Grundlage für das Werkzeug
`letzte_aktion_rueckgaengig`. Anker ist IMMER die `Message-ID`, NIE die zum
Zeitpunkt der Aktion gültige `uid` — uids sind über ein IMAP-MOVE hinweg NICHT
stabil (siehe `chat_tools._move_message`-Docstring, Live-Bug
`#gmail-draft-move`). `uid_zum_zeitpunkt` wird trotzdem mitgeführt, aber
AUSSCHLIESSLICH für die Berichterstattung — niemals als Move-Argument.

`papierkorb_leeren` (IMAP EXPUNGE) macht ein Rückgängig für ALLE noch offenen
Papierkorb-Einträge dieses Agenten unmöglich — `mark_trash_purged` markiert
sie als `unwiderruflich`, damit ein späterer Rückgängig-Versuch eine klare
Fehlermeldung liefert statt eines stillen Fehlschlags oder eines Griffs auf
eine bereits geleerte Nachricht.

Alle Funktionen sind fail-soft (Muster `chat_history.py`): jede Exception ->
`logger.warning` + neutrales Ergebnis (`""`/`None`/`0`). Ein kaputtes/nicht
beschreibbares `/data` darf ein Postfach-Werkzeug NIE zum Fehler machen — der
Journal-Schreibaufruf steht deshalb an JEDER Aufrufstelle zusätzlich in einem
eigenen `try/except` (Defense-in-Depth, siehe `chat_tools.py`).

`agent_id` läuft VOR jedem DB-Zugriff gegen `agents_io.AGENT_ID_PATTERN` —
ein ungültiger Wert führt zu einem stillen No-Op (kein Schreiben, kein
Absturz). Alle SQL-Statements sind ausschließlich parametrisiert (`?`).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .agents_io import AGENT_ID_PATTERN

logger = logging.getLogger("vizpatch.action_journal")

# Länger als die 14 Tage der Chat-History (`chat_history.RETENTION_DAYS_DEFAULT`):
# ein "mach das von letzter Woche rückgängig" darf nicht daran scheitern, dass
# der Journal-Eintrag schon weg ist, auch wenn die Chat-Sitzung längst verfallen
# ist. Bewusst EIGENE Konstante — NICHT `chat_history.CHAT_HISTORY_RETENTION_DAYS`
# wiederverwenden (13-RESEARCH.md Abschnitt 9 / 13-PATTERNS.md Muster 7).
ACTION_JOURNAL_RETENTION_DAYS = 90


def _data_root() -> Path:
    return Path(os.getenv("WEBUI_DATA_ROOT", "/data"))


def _db_path() -> Path:
    return _data_root() / "webui" / "action_journal.db"


def _valid_agent_id(agent_id: str) -> bool:
    return bool(AGENT_ID_PATTERN.match(agent_id or ""))


# MUSS Zeichen-fuer-Zeichen mit agent/src/action_journal.SCHEMA_SQL identisch
# bleiben (test_action_journal_schema_sync.py in beiden Suiten) — der Agent
# schreibt in dieselbe physische Datei wie die WebUI (dasselbe agent-data-
# Volume, siehe agent/src/action_journal.py-Docstring). `quelle` (Phase 14,
# AUTO-04) unterscheidet manuelle Chat-Aktionen ('chat', Default -- ALLE
# bestehenden Aufrufer bleiben unveraendert) von automatischen Sortierlaeufen
# ('automatik') fuer den Bericht `automatik_sortierungen`.
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

# Eigene ALTER-TABLE-Migration statt in SCHEMA_SQL (das nur bei einer NEUEN
# Datei greift, `CREATE TABLE IF NOT EXISTS` aendert kein Bestandsschema) —
# idempotent per PRAGMA-table_info-Check, MUSS ebenfalls Zeichen-fuer-Zeichen
# mit der Agent-Kopie identisch bleiben.
_QUELLE_MIGRATION_SQL = "ALTER TABLE action_journal ADD COLUMN quelle TEXT NOT NULL DEFAULT 'chat'"

# Klammer über ALLES, was zu EINER Betreiber-Anweisung gehört. Ohne sie kennt
# das Journal nur `aktion_id` = ein einzelner Werkzeugaufruf, und genau daran
# ist das Rückgängig in der Live-Abnahme vom 2026-08-19 in BEIDE Richtungen
# gescheitert: „sortier meinen Posteingang" erzeugte 12 `aktion_id`-Gruppen
# über 111 Mails, ein „mach das rückgängig" holte 9 davon zurück und meldete
# „Erledigt" — fasste man mehrfach nach, lief der Agent über den gemeinten
# Vorgang hinaus und kippte ältere, unbeteiligte Aktionen (Lesemarkierungen
# eines ganz anderen Vorgangs). Default leer, damit Bestandszeilen ohne
# Vorgang weiterhin einzeln behandelt werden (Rückwärtskompatibilität).
_VORGANG_MIGRATION_SQL = "ALTER TABLE action_journal ADD COLUMN vorgang_id TEXT NOT NULL DEFAULT ''"

# Prozess-lokaler „laufender Vorgang" — bewusst ein ContextVar statt eines
# weiteren Parameters durch alle Werkzeug-Handler: `record_action` wird aus
# einem guten Dutzend Handler gerufen, die davon nichts wissen müssen (dasselbe
# Argument, aus dem die Vorschau-Stores prozess-lokal sind).
_aktueller_vorgang: ContextVar[str] = ContextVar("vizpatch_vorgang_id", default="")


def _ensure_quelle_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(action_journal)").fetchall()}
    if "quelle" not in cols:
        conn.execute(_QUELLE_MIGRATION_SQL)


def _ensure_vorgang_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(action_journal)").fetchall()}
    if "vorgang_id" not in cols:
        conn.execute(_VORGANG_MIGRATION_SQL)


@contextmanager
def vorgang(vorgang_id: str = "") -> "Iterator[str]":
    """Klammert alle `record_action`-Aufrufe dieses Blocks zu EINEM Vorgang.

    Aufgerufen einmal pro Betreiber-Anweisung — im Chat pro Werkzeug-Runde
    (`_run_anthropic_tool_loop`), im Agenten pro Sortier-Zyklus. Verschachtelt
    gewinnt der äußere Vorgang NICHT: ein innerer Block setzt bewusst eine
    eigene Klammer und stellt den vorherigen Wert danach wieder her, damit ein
    Aufrufer nie versehentlich fremde Aktionen mit einsammelt.

    Ohne Argument wird eine frische ID erzeugt. Der Wert wird nur weiter-
    gereicht, nie geloggt oder ausgegeben.

    Zurückgesetzt wird über den GEMERKTEN Vorgängerwert, NICHT über
    `ContextVar.reset(token)`. Grund (Live-Befund Add-in, 2026-08-21):
    `run_agentic_chat` ist ein Generator, und Starlette konsumiert ihn beim
    Streamen in einem ANDEREN Context als dem, in dem er erzeugt wurde.
    `reset()` verlangt denselben Context und wirft dort
    `ValueError: Token was created in a different Context` — im Add-in
    erschien nach JEDER Antwort ein rotes „Interner Fehler bei der
    Verarbeitung", obwohl die Aktion selbst korrekt durchgelaufen war (das
    Setzen klappt, nur das Aufräumen scheiterte). `set()` ist
    kontextunabhängig gültig und erhält die Verschachtelungssemantik
    genauso."""
    vorher = _aktueller_vorgang.get()
    neu = vorgang_id or uuid.uuid4().hex
    _aktueller_vorgang.set(neu)
    try:
        yield neu
    finally:
        _aktueller_vorgang.set(vorher)


def _align_ownership(db_path: Path) -> None:
    """Übereignet Journal-Verzeichnis und -Datei dem Besitzer von `_data_root()`.

    Der webui-Container läuft als root, der agent-Container als UID 1000
    (agent/Dockerfile). Legt die WebUI die Journal-DB zuerst an — und das tut
    sie praktisch immer, weil jeder Chat sie öffnet —, gehört die Datei root
    mit 0644: der Agent darf sie dann lesen, aber NICHT beschreiben.
    `agent/src/action_journal.record_action` scheitert daraufhin still (es ist
    bewusst fail-soft) und der automatische Sortierlauf landet NIE im Journal.

    Befund der Live-Abnahme vom 2026-08-19: der Sortierlauf markierte zwei
    Mails, `automatisch_sortierungen` meldete trotzdem „keine automatischen
    Sortieraktionen", und rückgängig machen ließ sich nichts — die vierte
    Auflage von Phase 14 (alles im Journal, umkehrbar, abfragbar) war damit in
    JEDER Installation unerfüllt, ohne dass ein Test das zeigen konnte.

    Angeglichen wird an den Besitzer von `/data` statt an eine fest verdrahtete
    UID: dasselbe selbstkonfigurierende Muster, das auch bei den `.env`-Rechten
    nötig war. Das Verzeichnis muss mitwandern, weil SQLite für die
    `-journal`/`-wal`-Datei Schreibrecht im Verzeichnis braucht, nicht nur an
    der DB selbst.

    Fail-soft: fehlendes `os.chown` (Windows/Tests), fehlende Rechte oder ein
    nicht existierender Pfad -> stillschweigend nichts tun. Die Rechte sind
    dann entweder schon richtig oder von hier aus nicht korrigierbar; ein
    Fehler beim Angleichen darf den Journal-Zugriff der WebUI selbst NIE
    verhindern."""
    chown = getattr(os, "chown", None)
    if chown is None:
        return
    try:
        ziel = _data_root().stat()
        for pfad in (db_path.parent, db_path):
            if not pfad.exists():
                continue
            ist = pfad.stat()
            if (ist.st_uid, ist.st_gid) != (ziel.st_uid, ziel.st_gid):
                chown(pfad, ziel.st_uid, ziel.st_gid)
    except OSError:
        return


def _connect() -> sqlite3.Connection:
    """Öffnet die Journal-DB, legt bei Bedarf Verzeichnis + Schema an
    (idempotent — `CREATE TABLE/INDEX IF NOT EXISTS` + `quelle`-Spalten-
    Migration für bereits bestehende Datenbanken).

    Gleicht anschließend die Besitzverhältnisse an, damit der als UID 1000
    laufende Agent in dieselbe Datei schreiben kann (siehe
    `_align_ownership`)."""
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
    _align_ownership(path)
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_action(
    agent_id: str,
    aktion: str,
    quell_ordner: str,
    ziel_ordner: str,
    eintraege: list[dict],
    *,
    quelle: str = "chat",
) -> str:
    """Schreibt EINEN Journal-Eintrag pro Element von `eintraege` unter einer
    NEU erzeugten, gemeinsamen `aktion_id` (`uuid.uuid4().hex`) — die Klammer,
    mit der `letzte_aktion_rueckgaengig` später ALLE Mails EINER Massenaktion
    in einem Rutsch zurückstellt.

    `eintraege`: Liste von Dicts mit den Schlüsseln `uid`/`message_id` (Pflicht,
    aus der Rückgabe von `_move_message`/`_bulk_move_server_side`) und
    optional `betreff` (leer, wenn nicht ohnehin schon vorliegend — kein
    zusätzlicher IMAP-Roundtrip nur für das Journal). Einträge ohne
    `message_id` werden TROTZDEM geschrieben (mit leerer `message_id`) — sie
    sind später nur nicht per Message-ID wiederauffindbar, das meldet
    `letzte_aktion_rueckgaengig` explizit statt zu raten.

    `quelle` (Phase 14, AUTO-04, keyword-only): 'chat' (Default — ALLE
    bestehenden Aufrufer in `chat_tools.py` bleiben dadurch unverändert) oder
    'automatik' (der Polling-Sortierlauf, siehe `agent/src/action_journal.py`).
    Reine Berichts-/Filter-Dimension für `automatik_eintraege` — beeinflusst
    NICHT, wie `letzte_aktion_rueckgaengig` eine Aktion rückgängig macht (das
    bleibt am `aktion`-String hängen, siehe dortiger Docstring).

    Ungültiger `agent_id`, leere `eintraege` oder ein DB-Fehler -> `""`
    (kein Schreiben), KEINE Exception. Erfolg -> die neue `aktion_id`."""
    if not _valid_agent_id(agent_id):
        return ""
    if not eintraege:
        return ""

    aktion_id = uuid.uuid4().hex
    now = _now_iso()
    quelle_wert = str(quelle or "chat")
    # Leer, wenn kein `vorgang()`-Block offen ist: solche Zeilen bleiben
    # Einzelaktionen, genau wie alle Bestandszeilen vor der Migration.
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
                quelle_wert,
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
        logger.warning(
            "action_journal_record_failed", extra={"agent_id": agent_id, "error": str(e)}
        )
        return ""
    finally:
        if conn is not None:
            conn.close()

    purge_expired()
    return aktion_id


def automatik_eintraege(agent_id: str, stunden: int = 24) -> list[dict]:
    """Liefert alle Journal-GRUPPEN mit `quelle='automatik'` (Phase 14,
    AUTO-04) der letzten `stunden`, neueste zuerst — Grundlage für den
    Chat-Bericht `automatisch_sortierungen` ('was hast du heute
    weggeräumt?'). Gruppiert über `aktion_id` (wie `mails_verschieben`: ein
    Sortierlauf schreibt EINEN Eintrag je Kategorie-Batch, nicht je Mail).

    Enthält auch bereits rückgängig gemachte Gruppen (`rueckgaengig_gemacht`
    im Ergebnis-Dict) — ehrlicher Bericht statt Beschönigung. Ungültige
    `agent_id`/DB-Fehler -> `[]` (fail-soft wie alle übrigen Funktionen
    hier)."""
    if not _valid_agent_id(agent_id):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(stunden or 24)))).isoformat()
    conn = None
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT aktion_id, aktion, quell_ordner, ziel_ordner, MAX(zeit) as zeit, "
            "MAX(rueckgaengig_gemacht) as rueckgaengig_gemacht, COUNT(*) as anzahl "
            "FROM action_journal "
            "WHERE agent_id = ? AND quelle = 'automatik' AND zeit >= ? "
            "GROUP BY aktion_id ORDER BY zeit DESC",
            (agent_id, cutoff),
        ).fetchall()
    except Exception as e:
        logger.warning(
            "action_journal_automatik_eintraege_failed", extra={"agent_id": agent_id, "error": str(e)}
        )
        return []
    finally:
        if conn is not None:
            conn.close()

    return [
        {
            "aktion": r[1],
            "quell_ordner": r[2],
            "ziel_ordner": r[3],
            "zeit": r[4],
            "rueckgaengig_gemacht": bool(r[5]),
            "anzahl": r[6],
        }
        for r in rows
    ]


def last_action(agent_id: str) -> dict | None:
    """Liefert die zuletzt protokollierte, noch NICHT rückgängig gemachte
    Aktion dieses Agenten als Gruppe:
    `{"aktion_id", "aktion", "quell_ordner", "ziel_ordner", "zeit",
    "unwiderruflich", "eintraege": [{"message_id", "uid_zum_zeitpunkt",
    "betreff"}, ...]}`. `unwiderruflich=True` heißt: der Papierkorb wurde
    zwischenzeitlich endgültig geleert (`mark_trash_purged`) — die Gruppe wird
    TROTZDEM als "letzte Aktion" zurückgegeben, damit der Aufrufer eine klare
    Fehlermeldung statt eines stillen Sprungs zu einer älteren Aktion bauen
    kann. Keine offene Aktion, ungültige `agent_id` oder DB-Fehler -> `None`."""
    if not _valid_agent_id(agent_id):
        return None

    conn = None
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT aktion_id FROM action_journal "
            "WHERE agent_id = ? AND rueckgaengig_gemacht = 0 "
            "ORDER BY id DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        aktion_id = row[0]
        rows = conn.execute(
            "SELECT aktion, message_id, uid_zum_zeitpunkt, quell_ordner, "
            "ziel_ordner, betreff, zeit, unwiderruflich FROM action_journal "
            "WHERE agent_id = ? AND aktion_id = ? ORDER BY id ASC",
            (agent_id, aktion_id),
        ).fetchall()
    except Exception as e:
        logger.warning(
            "action_journal_last_action_failed", extra={"agent_id": agent_id, "error": str(e)}
        )
        return None
    finally:
        if conn is not None:
            conn.close()

    if not rows:
        return None

    aktion, _mid0, _uid0, quell_ordner, ziel_ordner, _betreff0, zeit, unwiderruflich = rows[0]
    eintraege = [
        {"message_id": r[1], "uid_zum_zeitpunkt": r[2], "betreff": r[5]} for r in rows
    ]
    return {
        "aktion_id": aktion_id,
        "aktion": aktion,
        "quell_ordner": quell_ordner,
        "ziel_ordner": ziel_ordner,
        "zeit": zeit,
        "unwiderruflich": bool(unwiderruflich),
        "eintraege": eintraege,
    }


def letzter_vorgang(agent_id: str) -> list[dict]:
    """Alle noch offenen Aktionsgruppen des zuletzt protokollierten VORGANGS —
    also alles, was zu EINER Betreiber-Anweisung gehört, im selben Format wie
    `last_action`, neueste Gruppe zuerst.

    Warum überhaupt: ein einziges „sortier meinen Posteingang" zerfällt intern
    in eine Gruppe je Zielordner und je Verarbeitungsblock. `last_action`
    liefert davon genau eine — in der Live-Abnahme vom 2026-08-19 kamen so nach
    einem 14-Mail-Lauf nur 2 Mails zurück, gemeldet als „Erledigt". Umgekehrt
    lief wiederholtes Nachfassen über den gemeinten Vorgang hinaus in ältere,
    unbeteiligte Aktionen. Beides verschwindet, wenn der Vorgang die Klammer ist.

    Reihenfolge: neueste Gruppe zuerst, damit ein Rückgängig die Aktionen in
    umgekehrter Ausführungsreihenfolge abwickelt — wurde dieselbe Mail im
    selben Vorgang zweimal bewegt, landet sie so wieder am Ausgangsort.

    Bestandszeilen ohne `vorgang_id` (vor der Migration geschrieben, oder von
    einem Aufrufer ohne `vorgang()`-Block) bleiben Einzelaktionen: dann kommt
    genau eine Gruppe zurück, exakt wie bisher. Keine offene Aktion, ungültige
    `agent_id` oder DB-Fehler -> leere Liste."""
    if not _valid_agent_id(agent_id):
        return []

    conn = None
    try:
        conn = _connect()
        kopf = conn.execute(
            "SELECT aktion_id, vorgang_id FROM action_journal "
            "WHERE agent_id = ? AND rueckgaengig_gemacht = 0 "
            "ORDER BY id DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        if kopf is None:
            return []
        letzte_aktion_id, vorgang_id = kopf[0], kopf[1]

        if not vorgang_id:
            aktion_ids = [letzte_aktion_id]
        else:
            aktion_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT aktion_id FROM action_journal "
                    "WHERE agent_id = ? AND vorgang_id = ? AND rueckgaengig_gemacht = 0 "
                    "GROUP BY aktion_id ORDER BY MAX(id) DESC",
                    (agent_id, vorgang_id),
                ).fetchall()
            ]

        gruppen = []
        for aktion_id in aktion_ids:
            rows = conn.execute(
                "SELECT aktion, message_id, uid_zum_zeitpunkt, quell_ordner, "
                "ziel_ordner, betreff, zeit, unwiderruflich FROM action_journal "
                "WHERE agent_id = ? AND aktion_id = ? ORDER BY id ASC",
                (agent_id, aktion_id),
            ).fetchall()
            if not rows:
                continue
            aktion, _mid0, _uid0, quell_ordner, ziel_ordner, _betreff0, zeit, unwiderruflich = rows[0]
            gruppen.append(
                {
                    "aktion_id": aktion_id,
                    "aktion": aktion,
                    "quell_ordner": quell_ordner,
                    "ziel_ordner": ziel_ordner,
                    "zeit": zeit,
                    "unwiderruflich": bool(unwiderruflich),
                    "eintraege": [
                        {"message_id": r[1], "uid_zum_zeitpunkt": r[2], "betreff": r[5]} for r in rows
                    ],
                }
            )
        return gruppen
    except Exception as e:
        logger.warning(
            "action_journal_letzter_vorgang_failed", extra={"agent_id": agent_id, "error": str(e)}
        )
        return []
    finally:
        if conn is not None:
            conn.close()


def mark_undone(agent_id: str, aktion_id: str) -> int:
    """Markiert ALLE Einträge von `aktion_id` als rückgängig gemacht — nach
    einem (ggf. teilweise erfolglosen) Rückgängig-Versuch, damit
    `last_action` danach die nächstältere offene Aktion liefert statt
    endlos dieselbe zurückzugeben. Ungültige `agent_id`/leere `aktion_id`
    oder DB-Fehler -> `0`, KEINE Exception. Liefert die Zahl betroffener
    Zeilen."""
    if not _valid_agent_id(agent_id) or not aktion_id:
        return 0
    conn = None
    try:
        conn = _connect()
        cur = conn.execute(
            "UPDATE action_journal SET rueckgaengig_gemacht = 1 "
            "WHERE agent_id = ? AND aktion_id = ?",
            (agent_id, aktion_id),
        )
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except Exception as e:
        logger.warning(
            "action_journal_mark_undone_failed", extra={"agent_id": agent_id, "error": str(e)}
        )
        return 0
    finally:
        if conn is not None:
            conn.close()


def mark_trash_purged(agent_id: str) -> int:
    """`papierkorb_leeren` (IMAP EXPUNGE) macht jedes noch offene
    Papierkorb-Journal dieses Agenten endgültig nicht mehr rückgängig
    machbar — markiert ALLE Zeilen mit `aktion='papierkorb'` UND
    `rueckgaengig_gemacht=0` als `unwiderruflich=1`. Bewusst NICHT als
    `rueckgaengig_gemacht` markiert: die Aktion wurde nicht rückgängig
    gemacht, sie ist nur nicht mehr möglich — `letzte_aktion_rueckgaengig`
    muss diesen Unterschied als klare Fehlermeldung ausgeben, kein stiller
    Sprung zu einer älteren Aktion. Ungültige `agent_id`/DB-Fehler -> `0`,
    KEINE Exception. Liefert die Zahl betroffener Zeilen."""
    if not _valid_agent_id(agent_id):
        return 0
    conn = None
    try:
        conn = _connect()
        cur = conn.execute(
            "UPDATE action_journal SET unwiderruflich = 1 "
            "WHERE agent_id = ? AND aktion = 'papierkorb' AND rueckgaengig_gemacht = 0",
            (agent_id,),
        )
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except Exception as e:
        logger.warning(
            "action_journal_mark_trash_purged_failed",
            extra={"agent_id": agent_id, "error": str(e)},
        )
        return 0
    finally:
        if conn is not None:
            conn.close()


def purge_expired() -> int:
    """Entfernt Zeilen mit `zeit` älter als `ACTION_JOURNAL_RETENTION_DAYS`
    (Vergleich per String gegen den UTC-ISO-8601-Cutoff — `zeit` wird
    konsistent als `datetime.now(timezone.utc).isoformat()` geschrieben,
    ISO-8601-Strings sind lexikographisch sortierbar). Fehlende DB/DB-Fehler
    -> `0`, KEINE Exception."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=ACTION_JOURNAL_RETENTION_DAYS)
    ).isoformat()
    conn = None
    try:
        conn = _connect()
        cur = conn.execute("DELETE FROM action_journal WHERE zeit < ?", (cutoff,))
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except Exception as e:
        logger.warning("action_journal_purge_failed", extra={"error": str(e)})
        return 0
    finally:
        if conn is not None:
            conn.close()
