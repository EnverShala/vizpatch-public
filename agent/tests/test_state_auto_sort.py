"""Tests fuer die auto_sorted_emails-Tabelle (Phase 14, Plan 14-01, AUTO-06)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src import state


def test_init_db_creates_auto_sorted_emails_table(tmp_db: Path):
    state.init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "auto_sorted_emails" in tables


def test_is_auto_sorted_false_for_unknown_message(tmp_db: Path):
    state.init_db(tmp_db)
    assert state.is_auto_sorted(tmp_db, "<neu@example.com>") is False


def test_mark_and_check_auto_sorted(tmp_db: Path):
    state.init_db(tmp_db)
    state.mark_auto_sorted(
        db_path=tmp_db,
        message_id="<abc@example.com>",
        uid=7,
        aktion="verschieben",
        kategorie="Rechnungen",
        ziel_ordner="Rechnungen",
    )
    assert state.is_auto_sorted(tmp_db, "<abc@example.com>") is True


def test_mark_auto_sorted_records_dry_run_flag(tmp_db: Path):
    state.init_db(tmp_db)
    state.mark_auto_sorted(
        db_path=tmp_db,
        message_id="<probe@example.com>",
        uid=1,
        aktion="verschieben",
        kategorie="Rechnungen",
        ziel_ordner="Rechnungen",
        dry_run=True,
    )
    with sqlite3.connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT dry_run FROM auto_sorted_emails WHERE message_id = ?",
            ("<probe@example.com>",),
        ).fetchone()
    assert row[0] == 1


def test_probelauf_eintrag_blockt_einen_scharfen_lauf_nicht(tmp_db: Path):
    """Quick 260822-4ay: der Probelauf darf seine eigenen Treffer nicht
    verbrauchen. Wer erst probelaufen laesst und dann scharf stellt, muss fuer
    genau diese Mails eine Aktion bekommen."""
    state.init_db(tmp_db)
    state.mark_auto_sorted(
        tmp_db, "<probe@example.com>", 1, "verschieben", "Rechnungen", "Rechnungen", dry_run=True
    )

    assert state.is_auto_sorted(tmp_db, "<probe@example.com>", dry_run_lauf=False) is False
    # Der Probelauf selbst sieht seinen Eintrag weiterhin — Kostendeckel bleibt.
    assert state.is_auto_sorted(tmp_db, "<probe@example.com>", dry_run_lauf=True) is True


def test_scharfer_eintrag_blockt_beide_laufarten(tmp_db: Path):
    """Gegenprobe: eine wirklich behandelte Mail (z.B. nur markiert, liegt also
    weiter in der INBOX) wird von KEINER Laufart erneut angefasst."""
    state.init_db(tmp_db)
    state.mark_auto_sorted(tmp_db, "<scharf@example.com>", 2, "markieren", "Notfälle", "")

    assert state.is_auto_sorted(tmp_db, "<scharf@example.com>", dry_run_lauf=False) is True
    assert state.is_auto_sorted(tmp_db, "<scharf@example.com>", dry_run_lauf=True) is True


def test_is_auto_sorted_default_ist_der_scharfe_lauf(tmp_db: Path):
    """Der Default-Parameter haelt bestehende Aufrufer unveraendert — und der
    Default ist bewusst der scharfe Lauf (die Variante, die Probelauf-Eintraege
    ignoriert)."""
    state.init_db(tmp_db)
    state.mark_auto_sorted(
        tmp_db, "<p@example.com>", 3, "verschieben", "Rechnungen", "Rechnungen", dry_run=True
    )

    assert state.is_auto_sorted(tmp_db, "<p@example.com>") is False


def test_mark_auto_sorted_is_idempotent_insert_or_replace(tmp_db: Path):
    state.init_db(tmp_db)
    state.mark_auto_sorted(tmp_db, "<x@example.com>", 1, "verschieben", "Rechnungen", "Rechnungen")
    state.mark_auto_sorted(tmp_db, "<x@example.com>", 1, "markieren", "Notfälle", "")
    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT aktion FROM auto_sorted_emails WHERE message_id = ?", ("<x@example.com>",)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "markieren"
