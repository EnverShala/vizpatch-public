"""Tests fuer den Agent-seitigen Journal-Schreib-Zwilling (Phase 14, Plan
14-01, AUTO-04)."""
from __future__ import annotations

import sqlite3

from src import action_journal


def test_record_action_writes_row_with_quelle_automatik(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DATA_ROOT", str(tmp_path))
    aktion_id = action_journal.record_action(
        "mein-agent",
        "verschieben",
        "INBOX",
        "Rechnungen",
        [{"uid": "5", "message_id": "<a@x.de>", "betreff": ""}],
    )
    assert aktion_id != ""

    db_path = tmp_path / "webui" / "action_journal.db"
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT agent_id, aktion, quell_ordner, ziel_ordner, message_id, quelle "
            "FROM action_journal WHERE aktion_id = ?",
            (aktion_id,),
        ).fetchone()
    assert row == ("mein-agent", "verschieben", "INBOX", "Rechnungen", "<a@x.de>", "automatik")


def test_record_action_invalid_agent_id_returns_empty_string(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DATA_ROOT", str(tmp_path))
    result = action_journal.record_action(
        "../evil", "verschieben", "INBOX", "Rechnungen", [{"uid": "1", "message_id": "x"}]
    )
    assert result == ""
    assert not (tmp_path / "webui" / "action_journal.db").exists()


def test_record_action_empty_eintraege_returns_empty_string(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DATA_ROOT", str(tmp_path))
    assert action_journal.record_action("mein-agent", "verschieben", "INBOX", "Rechnungen", []) == ""


def test_record_action_groups_multiple_entries_under_one_aktion_id(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DATA_ROOT", str(tmp_path))
    aktion_id = action_journal.record_action(
        "mein-agent",
        "verschieben",
        "INBOX",
        "Rechnungen",
        [
            {"uid": "1", "message_id": "<a@x.de>"},
            {"uid": "2", "message_id": "<b@x.de>"},
        ],
    )
    db_path = tmp_path / "webui" / "action_journal.db"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT message_id FROM action_journal WHERE aktion_id = ?", (aktion_id,)
        ).fetchall()
    assert {r[0] for r in rows} == {"<a@x.de>", "<b@x.de>"}


def test_record_action_db_error_is_fail_soft(tmp_path, monkeypatch):
    """Ein kaputtes /data darf einen Poll-Zyklus nie zum Absturz bringen."""
    monkeypatch.setenv("AGENT_DATA_ROOT", str(tmp_path))

    def _boom():
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(action_journal, "_connect", _boom)
    result = action_journal.record_action(
        "mein-agent", "verschieben", "INBOX", "Rechnungen", [{"uid": "1", "message_id": "x"}]
    )
    assert result == ""


def test_record_action_shares_schema_with_pre_existing_webui_row(tmp_path, monkeypatch):
    """Simuliert eine bereits vom webui-Container angelegte DB OHNE quelle-
    Spalte (Alt-Schema) — die Migration muss sie ergaenzen, bevor der Agent
    schreibt (Kompatibilitaet mit bereits laufenden Installationen)."""
    monkeypatch.setenv("AGENT_DATA_ROOT", str(tmp_path))
    db_path = tmp_path / "webui" / "action_journal.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE action_journal(
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
        )
        conn.execute(
            "INSERT INTO action_journal(aktion_id, agent_id, aktion, message_id, "
            "uid_zum_zeitpunkt, quell_ordner, ziel_ordner, betreff, zeit) "
            "VALUES ('alt', 'mein-agent', 'verschieben', '<alt@x.de>', '1', 'INBOX', 'X', '', 'irgendwann')"
        )
        conn.commit()

    aktion_id = action_journal.record_action(
        "mein-agent", "markieren:\\Flagged:gesetzt", "INBOX", "INBOX",
        [{"uid": "9", "message_id": "<neu@x.de>"}],
    )
    assert aktion_id != ""
    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(action_journal)").fetchall()}
        assert "quelle" in cols
        alt_quelle = conn.execute(
            "SELECT quelle FROM action_journal WHERE aktion_id = 'alt'"
        ).fetchone()[0]
    assert alt_quelle == "chat"  # DEFAULT der Migration fuer Bestandszeilen
