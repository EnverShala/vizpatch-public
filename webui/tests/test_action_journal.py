"""Tests für das persistente Aktions-Journal (Phase 13, Plan 13-03, MBOX-02).

Deckt ab: Schema/Gruppierung (`record_action`/`last_action`), `mark_undone`,
`mark_trash_purged` (papierkorb_leeren macht ein Rückgängig unmöglich),
Retention (`purge_expired`, eigene `ACTION_JOURNAL_RETENTION_DAYS`), Agent-ID-
Validierung VOR jedem DB-Zugriff, Fail-soft bei DB-Fehlern (Muster
`test_chat_history.py`).
"""
from __future__ import annotations


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


def test_record_action_and_last_action_group_three_entries(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    aktion_id = action_journal.record_action(
        "info",
        "verschieben",
        "INBOX",
        "INBOX.Rechnungen",
        [
            {"uid": "1", "message_id": "<eins@example.com>", "betreff": "Rechnung 1"},
            {"uid": "2", "message_id": "<zwei@example.com>", "betreff": "Rechnung 2"},
            {"uid": "3", "message_id": "<drei@example.com>", "betreff": "Rechnung 3"},
        ],
    )

    assert aktion_id != ""

    letzte = action_journal.last_action("info")
    assert letzte is not None
    assert letzte["aktion_id"] == aktion_id
    assert letzte["aktion"] == "verschieben"
    assert letzte["quell_ordner"] == "INBOX"
    assert letzte["ziel_ordner"] == "INBOX.Rechnungen"
    assert letzte["unwiderruflich"] is False
    assert len(letzte["eintraege"]) == 3
    message_ids = {e["message_id"] for e in letzte["eintraege"]}
    assert message_ids == {"<eins@example.com>", "<zwei@example.com>", "<drei@example.com>"}


def test_last_action_returns_none_when_journal_empty(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    assert action_journal.last_action("info") is None


def test_last_action_returns_most_recent_of_two_actions(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Alt", [{"uid": "1", "message_id": "<alt@example.com>"}]
    )
    neu_id = action_journal.record_action(
        "info", "papierkorb", "INBOX", "Papierkorb", [{"uid": "2", "message_id": "<neu@example.com>"}]
    )

    letzte = action_journal.last_action("info")
    assert letzte["aktion_id"] == neu_id
    assert letzte["aktion"] == "papierkorb"


def test_mark_undone_removes_action_from_last_action(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    aeltere_id = action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Alt", [{"uid": "1", "message_id": "<alt@example.com>"}]
    )
    neuere_id = action_journal.record_action(
        "info", "papierkorb", "INBOX", "Papierkorb", [{"uid": "2", "message_id": "<neu@example.com>"}]
    )

    geaendert = action_journal.mark_undone("info", neuere_id)
    assert geaendert == 1

    letzte = action_journal.last_action("info")
    assert letzte["aktion_id"] == aeltere_id


def test_record_action_missing_message_id_still_written(tmp_path, monkeypatch):
    """Eine Mail ohne Message-ID-Header wird TROTZDEM protokolliert (leere
    message_id) — die Aktion gilt weiter als erfolgreich, sie ist später nur
    nicht per Message-ID wiederauffindbar (13-RESEARCH.md Pitfall 3)."""
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Sonstiges", [{"uid": "9", "message_id": ""}]
    )

    letzte = action_journal.last_action("info")
    assert letzte["eintraege"] == [{"message_id": "", "uid_zum_zeitpunkt": "9", "betreff": ""}]


def test_mark_trash_purged_flags_open_papierkorb_entries_unwiderruflich(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "papierkorb", "INBOX", "Papierkorb", [{"uid": "1", "message_id": "<a@example.com>"}]
    )

    geaendert = action_journal.mark_trash_purged("info")
    assert geaendert == 1

    letzte = action_journal.last_action("info")
    assert letzte["unwiderruflich"] is True
    # Bewusst NICHT rueckgaengig_gemacht -- last_action liefert die Aktion
    # weiterhin (als unwiderruflich), statt sie zu verschlucken.
    assert letzte["aktion"] == "papierkorb"


def test_mark_trash_purged_does_not_affect_verschieben_actions(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Rechnungen", [{"uid": "1", "message_id": "<a@example.com>"}]
    )

    geaendert = action_journal.mark_trash_purged("info")
    assert geaendert == 0

    letzte = action_journal.last_action("info")
    assert letzte["unwiderruflich"] is False


def test_record_action_rejects_agent_id_with_path_segments(tmp_path, monkeypatch):
    """Ungültige agent_id (Pfadanteile) -> kein Schreiben, KEIN Absturz."""
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    aktion_id = action_journal.record_action(
        "../evil", "verschieben", "INBOX", "INBOX.X", [{"uid": "1", "message_id": "<a@example.com>"}]
    )

    assert aktion_id == ""
    assert action_journal.last_action("../evil") is None
    # Kein Schaden ausserhalb WEBUI_DATA_ROOT angerichtet -- die DB-Datei
    # existiert erst gar nicht, wenn nichts Gueltiges geschrieben wurde.
    assert not action_journal._db_path().exists()


def test_record_action_rejects_empty_eintraege(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    assert action_journal.record_action("info", "verschieben", "INBOX", "INBOX.X", []) == ""


def test_record_action_fail_soft_when_connect_raises(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    def _boom():
        raise RuntimeError("db kaputt")

    monkeypatch.setattr(action_journal, "_connect", _boom)

    result = action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.X", [{"uid": "1", "message_id": "<a@example.com>"}]
    )

    assert result == ""


def test_last_action_fail_soft_when_connect_raises(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.X", [{"uid": "1", "message_id": "<a@example.com>"}]
    )

    def _boom():
        raise RuntimeError("db kaputt")

    monkeypatch.setattr(action_journal, "_connect", _boom)

    assert action_journal.last_action("info") is None


def test_mark_undone_fail_soft_when_connect_raises(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    def _boom():
        raise RuntimeError("db kaputt")

    monkeypatch.setattr(action_journal, "_connect", _boom)

    assert action_journal.mark_undone("info", "irgendeine-id") == 0


def test_mark_trash_purged_fail_soft_when_connect_raises(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    def _boom():
        raise RuntimeError("db kaputt")

    monkeypatch.setattr(action_journal, "_connect", _boom)

    assert action_journal.mark_trash_purged("info") == 0


def test_purge_expired_fail_soft_when_connect_raises(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    def _boom():
        raise RuntimeError("db kaputt")

    monkeypatch.setattr(action_journal, "_connect", _boom)

    assert action_journal.purge_expired() == 0


def test_purge_expired_removes_old_keeps_recent(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    conn = action_journal._connect()
    old_ts = "2000-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO action_journal(aktion_id, agent_id, aktion, message_id, "
        "uid_zum_zeitpunkt, quell_ordner, ziel_ordner, betreff, zeit, "
        "rueckgaengig_gemacht, unwiderruflich) VALUES "
        "('alt-id', 'info', 'verschieben', '<alt@example.com>', '1', 'INBOX', "
        "'INBOX.Alt', '', ?, 0, 0)",
        (old_ts,),
    )
    conn.commit()
    conn.close()

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Neu", [{"uid": "2", "message_id": "<neu@example.com>"}]
    )

    # record_action ruft purge_expired() bereits intern auf -- der alte
    # Eintrag ist zu diesem Zeitpunkt schon weg.
    letzte = action_journal.last_action("info")
    assert letzte["ziel_ordner"] == "INBOX.Neu"

    conn = action_journal._connect()
    rest = conn.execute("SELECT aktion_id FROM action_journal").fetchall()
    conn.close()
    assert [r[0] for r in rest] == [letzte["aktion_id"]]


def test_retention_constant_is_higher_than_chat_history_default():
    import src.action_journal as action_journal
    import src.chat_history as chat_history

    assert action_journal.ACTION_JOURNAL_RETENTION_DAYS > chat_history.RETENTION_DAYS_DEFAULT


def test_db_path_uses_own_file_not_chat_history(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.X", [{"uid": "1", "message_id": "<a@example.com>"}]
    )

    assert action_journal._db_path() == tmp_path / "data" / "webui" / "action_journal.db"
    assert action_journal._db_path().exists()
    assert action_journal._db_path().name != "chat_history.db"


# --- quelle-Spalte + automatik_eintraege (Phase 14, Plan 14-01, AUTO-04) ----


def test_record_action_default_quelle_is_chat(tmp_path, monkeypatch):
    """ALLE bestehenden Aufrufer (chat_tools.py) rufen record_action OHNE
    quelle-Argument auf — das MUSS unverändert 'chat' ergeben."""
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.X", [{"uid": "1", "message_id": "<a@example.com>"}]
    )
    conn = action_journal._connect()
    row = conn.execute("SELECT quelle FROM action_journal LIMIT 1").fetchone()
    conn.close()
    assert row[0] == "chat"


def test_record_action_quelle_automatik_is_stored(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Rechnungen",
        [{"uid": "1", "message_id": "<a@example.com>"}],
        quelle="automatik",
    )
    conn = action_journal._connect()
    row = conn.execute("SELECT quelle FROM action_journal LIMIT 1").fetchone()
    conn.close()
    assert row[0] == "automatik"


def test_automatik_eintraege_returns_only_quelle_automatik(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Rechnungen",
        [{"uid": "1", "message_id": "<a@example.com>"}, {"uid": "2", "message_id": "<b@example.com>"}],
        quelle="automatik",
    )
    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Manuell",
        [{"uid": "3", "message_id": "<c@example.com>"}],
        quelle="chat",
    )

    eintraege = action_journal.automatik_eintraege("info", stunden=24)

    assert len(eintraege) == 1
    assert eintraege[0]["ziel_ordner"] == "INBOX.Rechnungen"
    assert eintraege[0]["anzahl"] == 2
    assert eintraege[0]["rueckgaengig_gemacht"] is False


def test_automatik_eintraege_respects_zeitfenster(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    conn = action_journal._connect()
    alt = (
        action_journal.datetime.now(action_journal.timezone.utc)
        - action_journal.timedelta(hours=48)
    ).isoformat()
    conn.execute(
        "INSERT INTO action_journal(aktion_id, agent_id, aktion, message_id, "
        "uid_zum_zeitpunkt, quell_ordner, ziel_ordner, betreff, zeit, quelle) "
        "VALUES ('alt', 'info', 'verschieben', '<alt@x.de>', '1', 'INBOX', 'X', '', ?, 'automatik')",
        (alt,),
    )
    conn.commit()
    conn.close()

    eintraege = action_journal.automatik_eintraege("info", stunden=24)
    assert eintraege == []


def test_automatik_eintraege_invalid_agent_id_returns_empty_list(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    assert action_journal.automatik_eintraege("../evil") == []


def test_automatik_eintraege_no_entries_returns_empty_list(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    assert action_journal.automatik_eintraege("info") == []


def test_ensure_quelle_column_migrates_pre_existing_database(tmp_path, monkeypatch):
    """Simuliert eine bereits laufende Installation VOR Phase 14 (Schema ohne
    quelle-Spalte) -- die Migration muss sie beim naechsten _connect()
    ergaenzen, ohne bestehende Zeilen zu verlieren."""
    _setup_env(tmp_path, monkeypatch)
    import sqlite3

    import src.action_journal as action_journal

    db_path = action_journal._db_path()
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
            "VALUES ('alt', 'info', 'verschieben', '<alt@x.de>', '1', 'INBOX', 'X', '', 'irgendwann')"
        )
        conn.commit()

    letzte = action_journal.last_action("info")
    assert letzte is not None
    assert letzte["aktion_id"] == "alt"


# ---------------------------------------------------------------------------
# Besitzverhältnisse der Journal-Datei (Befund der Live-Abnahme 2026-08-19)
#
# Die WebUI läuft als root, der Agent als UID 1000. Legt die WebUI die
# Journal-DB an, gehört sie root — und `agent/src/action_journal.record_action`
# scheitert dann still, weil es fail-soft ist. Ergebnis: der automatische
# Sortierlauf landete NIE im Journal, war weder umkehrbar noch berichtbar.
# ---------------------------------------------------------------------------


def test_connect_gleicht_besitzer_an_datenwurzel_an(tmp_path, monkeypatch):
    """`_connect` übereignet Verzeichnis UND Datei dem Besitzer von /data."""
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    gerufen = []

    class _Stat:
        def __init__(self, uid, gid):
            self.st_uid = uid
            self.st_gid = gid

    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    echtes_stat = type(data_root).stat

    def _fake_stat(self, *args, **kwargs):
        # /data gehoert dem Agenten (1000), alles darunter root (0)
        if self == data_root:
            return _Stat(1000, 1000)
        return _Stat(0, 0)

    monkeypatch.setattr(type(data_root), "stat", _fake_stat)
    monkeypatch.setattr(
        action_journal.os, "chown", lambda p, uid, gid: gerufen.append((str(p), uid, gid)), raising=False
    )

    conn = action_journal._connect()
    conn.close()
    monkeypatch.setattr(type(data_root), "stat", echtes_stat)

    ziele = {pfad for pfad, _, _ in gerufen}
    assert any(pfad.endswith("webui") for pfad in ziele), "Verzeichnis muss mitwandern (SQLite -journal)"
    assert any(pfad.endswith("action_journal.db") for pfad in ziele), "DB-Datei muss übereignet werden"
    assert all((uid, gid) == (1000, 1000) for _, uid, gid in gerufen)


def test_connect_ohne_chown_laeuft_normal_durch(tmp_path, monkeypatch):
    """Fehlt `os.chown` (Windows) oder scheitert es, bleibt das Journal nutzbar."""
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    def _kaputt(*args, **kwargs):
        raise PermissionError("kein root")

    monkeypatch.setattr(action_journal.os, "chown", _kaputt, raising=False)

    aktion_id = action_journal.record_action(
        "info", "verschieben", "INBOX", "Archiv",
        [{"uid": "1", "message_id": "<a@b>", "betreff": "x"}],
    )
    assert aktion_id != "", "Ein fehlgeschlagenes chown darf das Schreiben nie verhindern"


# ---------------------------------------------------------------------------
# Vorgangs-Klammer (Befund der Live-Abnahme 2026-08-19)
#
# Eine Betreiber-Anweisung zerfaellt intern in mehrere `aktion_id`-Gruppen.
# `last_action` lieferte davon eine — nach einem 14-Mail-Sortierlauf kamen so
# 2 Mails zurueck, gemeldet als "Erledigt"; wiederholtes Nachfassen lief
# umgekehrt in aeltere, unbeteiligte Aktionen hinein.
# ---------------------------------------------------------------------------


def _eintrag(nr):
    return [{"uid": str(nr), "message_id": "<m%d@example.com>" % nr, "betreff": "Betreff %d" % nr}]


def test_letzter_vorgang_fasst_alle_gruppen_einer_anweisung_zusammen(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    with action_journal.vorgang():
        action_journal.record_action("info", "verschieben", "INBOX", "Rechnungen", _eintrag(1))
        action_journal.record_action("info", "verschieben", "INBOX", "Newsletter", _eintrag(2))
        action_journal.record_action("info", r"markieren:\Flagged:gesetzt", "INBOX", "INBOX", _eintrag(3))

    gruppen = action_journal.letzter_vorgang("info")

    assert len(gruppen) == 3, "alle drei Gruppen derselben Anweisung gehoeren zum Vorgang"
    ziele = {g["ziel_ordner"] for g in gruppen}
    assert ziele == {"Rechnungen", "Newsletter", "INBOX"}
    # `last_action` sieht davon weiterhin nur eine — genau der alte Zustand
    assert action_journal.last_action("info")["ziel_ordner"] == "INBOX"


def test_letzter_vorgang_greift_nicht_auf_aeltere_vorgaenge_ueber(tmp_path, monkeypatch):
    """Die Gegenrichtung: der Rueckweg darf NICHT ueber die Anweisung hinauslaufen."""
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    with action_journal.vorgang():
        action_journal.record_action("info", r"markieren:\Seen:gesetzt", "INBOX", "INBOX", _eintrag(9))

    with action_journal.vorgang():
        action_journal.record_action("info", "verschieben", "INBOX", "Rechnungen", _eintrag(1))
        action_journal.record_action("info", "verschieben", "INBOX", "Newsletter", _eintrag(2))

    gruppen = action_journal.letzter_vorgang("info")

    assert len(gruppen) == 2
    assert all(g["aktion"] == "verschieben" for g in gruppen), (
        "die Lesemarkierung des VORIGEN Vorgangs darf nicht mitgekippt werden"
    )


def test_letzter_vorgang_ohne_klammer_bleibt_einzelaktion(tmp_path, monkeypatch):
    """Rueckwaertskompatibilitaet: Bestandszeilen ohne vorgang_id."""
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    action_journal.record_action("info", "verschieben", "INBOX", "A", _eintrag(1))
    action_journal.record_action("info", "verschieben", "INBOX", "B", _eintrag(2))

    gruppen = action_journal.letzter_vorgang("info")

    assert len(gruppen) == 1
    assert gruppen[0]["ziel_ordner"] == "B"


def test_letzter_vorgang_ueberspringt_bereits_rueckgaengige_gruppen(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    with action_journal.vorgang():
        a = action_journal.record_action("info", "verschieben", "INBOX", "A", _eintrag(1))
        action_journal.record_action("info", "verschieben", "INBOX", "B", _eintrag(2))

    action_journal.mark_undone("info", a)
    gruppen = action_journal.letzter_vorgang("info")

    assert [g["ziel_ordner"] for g in gruppen] == ["B"]


def test_vorgang_stellt_vorherigen_wert_wieder_her(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    assert action_journal._aktueller_vorgang.get() == ""
    with action_journal.vorgang() as aussen:
        assert action_journal._aktueller_vorgang.get() == aussen
        with action_journal.vorgang() as innen:
            assert innen != aussen
        assert action_journal._aktueller_vorgang.get() == aussen
    assert action_journal._aktueller_vorgang.get() == ""


def test_vorgang_ueberlebt_generator_ueber_context_grenze(tmp_path, monkeypatch):
    """Live-Befund Add-in 2026-08-21: `ContextVar.reset(token)` wirft, wenn der
    Generator in einem ANDEREN Context konsumiert wird als dem, in dem er
    erzeugt wurde — genau das tut Starlette beim Streamen von
    `run_agentic_chat`. Ergebnis war nach JEDER Antwort ein rotes
    "Interner Fehler bei der Verarbeitung", obwohl die Aktion korrekt lief.

    Dieser Test bildet die Konstellation nach: Generator hier erzeugt,
    Verbrauch in einem kopierten Context. Vor dem Fix schlug er mit
    `ValueError: Token was created in a different Context` fehl.
    """
    import contextvars

    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    gesehen = []

    def _streamt():
        with action_journal.vorgang() as vid:
            gesehen.append(vid)
            yield "chunk-1"
            yield "chunk-2"

    # Entscheidend: JEDER Iterationsschritt laeuft in einem EIGENEN Context —
    # so konsumiert Starlette einen sync-Generator beim Streamen. Ein einzelnes
    # `copy_context().run(list, gen)` reicht NICHT, das lief auch mit dem Bug
    # durch (nachgemessen).
    gen = _streamt()
    chunks = []
    try:
        while True:
            ctx = contextvars.copy_context()
            chunks.append(ctx.run(lambda: next(gen)))
    except StopIteration:
        pass

    assert chunks == ["chunk-1", "chunk-2"]
    assert len(gesehen) == 1


def test_vorgang_setzt_id_auch_ueber_generator_hinweg(tmp_path, monkeypatch):
    """Der Vorgang muss beim Schreiben tatsaechlich ankommen — sonst waere der
    Fix oben zwar leise, aber wirkungslos."""
    _setup_env(tmp_path, monkeypatch)
    import src.action_journal as action_journal

    def _streamt():
        with action_journal.vorgang():
            action_journal.record_action(
                "info", "verschieben", "INBOX", "A",
                [{"uid": "1", "message_id": "<a@b>", "betreff": "x"}])
            yield "fertig"
            action_journal.record_action(
                "info", "verschieben", "INBOX", "B",
                [{"uid": "2", "message_id": "<c@d>", "betreff": "y"}])

    list(_streamt())
    gruppen = action_journal.letzter_vorgang("info")
    assert len(gruppen) == 2, "beide Schreibvorgaenge gehoeren zum selben Vorgang"
