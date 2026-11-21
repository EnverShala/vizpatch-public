"""Tests fuer den automatischen Sortierlauf (Phase 14, Plan 14-01,
AUTO-01..08) — der zentrale Orchestrator dieser Phase.

Deckt alle harten Auflagen ab: Default aus, Probelauf bewegt nichts,
Kostendeckel (ein Klassifikations-Aufruf pro Zyklus, kein Aufruf ohne
Kandidaten), geschuetzte Zielordner werden ausgeschlossen, Anonymizer greift,
und beide Aktionsarten (verschieben/markieren) fuehren zu einem korrekten
Journal-Eintrag mit quelle='automatik'."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import auto_sort, state


class FakeImap:
    def __init__(self, kandidaten=None, protected=None):
        self.kandidaten = kandidaten or []
        self.protected = protected or {"inbox", "drafts", "sent"}
        self.moved: list[tuple] = []
        self.marked: list[str] = []
        self.move_result = True

    def resolve_protected_folder_names(self):
        return self.protected

    def fetch_seen_candidates(self, limit):
        return self.kandidaten[:limit]

    def move_message(self, uid, source_folder, target_folder):
        self.moved.append((uid, source_folder, target_folder))
        return self.move_result

    def mark_important(self, uid):
        self.marked.append(uid)


def _msg(uid, subject, from_="kunde@web.de", text="", message_id=None):
    mid = message_id or f"<{uid}@example.com>"
    return SimpleNamespace(
        uid=uid, subject=subject, from_=from_, text=text,
        headers={"message-id": [mid]},
    )


@pytest.fixture
def cfg(mock_config, tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    return replace(
        mock_config,
        state_db=db,
        enable_auto_sort=True,
        auto_sort_dry_run=False,
        auto_sort_max_mails=50,
        sortierregeln_md="## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n",
    )


def _patch_llm(mocker, antwort="1|Rechnungen"):
    return mocker.patch("src.auto_sort.llm.llm_call", return_value=antwort)


# --- AUTO-01: Default aus ---------------------------------------------------


def test_enable_auto_sort_false_does_nothing(mocker, mock_config, tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_aus = replace(mock_config, state_db=db, enable_auto_sort=False)
    imap = FakeImap(kandidaten=[_msg("1", "Rechnung")])
    llm_mock = mocker.patch("src.auto_sort.llm.llm_call")

    auto_sort.run_auto_sort(cfg_aus, imap, MagicMock())

    assert not llm_mock.called
    assert imap.moved == []
    assert imap.marked == []


# --- AUTO-06: Kostendeckel ---------------------------------------------------


def test_no_categories_configured_makes_no_llm_call(mocker, mock_config, tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_ohne_regeln = replace(mock_config, state_db=db, enable_auto_sort=True, sortierregeln_md="")
    imap = FakeImap(kandidaten=[_msg("1", "Rechnung")])
    llm_mock = mocker.patch("src.auto_sort.llm.llm_call")

    auto_sort.run_auto_sort(cfg_ohne_regeln, imap, MagicMock())

    assert not llm_mock.called


def test_no_candidates_makes_no_llm_call(mocker, cfg):
    imap = FakeImap(kandidaten=[])
    llm_mock = mocker.patch("src.auto_sort.llm.llm_call")

    auto_sort.run_auto_sort(cfg, imap, MagicMock())

    assert not llm_mock.called


def test_exactly_one_llm_call_per_cycle_regardless_of_mail_count(mocker, cfg):
    kandidaten = [_msg(str(i), f"Rechnung {i}") for i in range(1, 11)]
    imap = FakeImap(kandidaten=kandidaten)
    llm_mock = _patch_llm(mocker, antwort="\n".join(f"{i}|Rechnungen" for i in range(1, 11)))

    auto_sort.run_auto_sort(cfg, imap, MagicMock())

    assert llm_mock.call_count == 1


def test_log_nennt_modell_und_deckel(mocker, cfg):
    """AUTO-06 wörtlich: 'Der Deckel und das verwendete Modell stehen im Log.'"""
    imap = FakeImap(kandidaten=[_msg("1", "Rechnung")])
    _patch_llm(mocker, antwort="1|Rechnungen")
    logger = MagicMock()

    auto_sort.run_auto_sort(cfg, imap, logger)

    klassifiziert_calls = [
        call for call in logger.info.call_args_list if call.args[0] == "auto_sort_klassifiziert"
    ]
    assert len(klassifiziert_calls) == 1
    extra = klassifiziert_calls[0].kwargs["extra"]
    assert extra["modell"] == cfg.model_classify
    assert extra["deckel"] == cfg.auto_sort_max_mails


def test_candidates_capped_at_auto_sort_max_mails(mocker, mock_config, tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_gedeckelt = replace(
        mock_config, state_db=db, enable_auto_sort=True, auto_sort_dry_run=False,
        auto_sort_max_mails=2, sortierregeln_md="## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n",
    )
    kandidaten = [_msg(str(i), f"Rechnung {i}") for i in range(1, 11)]
    imap = FakeImap(kandidaten=kandidaten)
    llm_mock = _patch_llm(mocker, antwort="1|Rechnungen\n2|Rechnungen")

    auto_sort.run_auto_sort(cfg_gedeckelt, imap, MagicMock())

    prompt = llm_mock.call_args.kwargs["prompt"]
    assert prompt.count(" | Betreff: Rechnung ") == 2


def test_log_meldet_ob_der_deckel_gegriffen_hat(mocker, mock_config, tmp_path):
    """Quick 260822-4ay (Befund 3): der Betriebsfall 'es lagen mehr Mails an,
    als angesehen wurden' war aus dem Log nicht ablesbar."""
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_gedeckelt = replace(
        mock_config, state_db=db, enable_auto_sort=True, auto_sort_dry_run=False,
        auto_sort_max_mails=2, sortierregeln_md="## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n",
    )
    _patch_llm(mocker, antwort="1|Rechnungen\n2|Rechnungen")
    mocker.patch("src.action_journal.record_action", return_value="aktion-1")
    logger = MagicMock()

    auto_sort.run_auto_sort(
        cfg_gedeckelt, FakeImap(kandidaten=[_msg(str(i), f"Rechnung {i}") for i in range(1, 11)]), logger
    )

    extra = [
        c for c in logger.info.call_args_list if c.args[0] == "auto_sort_klassifiziert"
    ][0].kwargs["extra"]
    assert extra["deckel_erreicht"] is True


def test_log_meldet_keinen_gegriffenen_deckel_wenn_alles_reinpasst(mocker, cfg):
    logger = MagicMock()
    _patch_llm(mocker, antwort="1|Rechnungen")
    mocker.patch("src.action_journal.record_action", return_value="aktion-1")

    auto_sort.run_auto_sort(cfg, FakeImap(kandidaten=[_msg("1", "Rechnung")]), logger)

    extra = [
        c for c in logger.info.call_args_list if c.args[0] == "auto_sort_klassifiziert"
    ][0].kwargs["extra"]
    assert extra["deckel_erreicht"] is False


# --- AUTO-07: geschuetzte Zielordner ----------------------------------------


def test_category_targeting_protected_folder_is_skipped(mocker, mock_config, tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_geschuetzt = replace(
        mock_config, state_db=db, enable_auto_sort=True, auto_sort_dry_run=False,
        sortierregeln_md="## Alles\nAktion: verschieben\nZiel: INBOX\n",
    )
    imap = FakeImap(kandidaten=[_msg("1", "Rechnung")], protected={"inbox"})
    llm_mock = mocker.patch("src.auto_sort.llm.llm_call")

    auto_sort.run_auto_sort(cfg_geschuetzt, imap, MagicMock())

    assert not llm_mock.called  # keine aktive Kategorie mehr uebrig
    assert imap.moved == []


# --- Aktionsart 'verschieben' ------------------------------------------------


def test_verschieben_moves_mail_and_writes_journal(mocker, cfg):
    imap = FakeImap(kandidaten=[_msg("5", "Rechnung Nr. 1", message_id="<r1@x.de>")])
    _patch_llm(mocker, antwort="1|Rechnungen")
    journal_mock = mocker.patch("src.action_journal.record_action", return_value="aktion-123")

    auto_sort.run_auto_sort(cfg, imap, MagicMock())

    assert imap.moved == [("5", cfg.imap_inbox_folder, "Rechnungen")]
    journal_mock.assert_called_once()
    args, kwargs = journal_mock.call_args
    assert args[0] == cfg.agent_id
    assert args[1] == "verschieben"
    assert args[2] == cfg.imap_inbox_folder
    assert args[3] == "Rechnungen"
    assert args[4] == [{"uid": "5", "message_id": "<r1@x.de>"}]
    assert state.is_auto_sorted(cfg.state_db, "<r1@x.de>") is True


def test_verschieben_skipped_when_server_has_no_move_capability(mocker, cfg):
    imap = FakeImap(kandidaten=[_msg("5", "Rechnung", message_id="<r1@x.de>")])
    imap.move_result = False  # move_message meldet "uebersprungen" (kein MOVE-Capability)
    _patch_llm(mocker, antwort="1|Rechnungen")
    journal_mock = mocker.patch("src.action_journal.record_action")

    auto_sort.run_auto_sort(cfg, imap, MagicMock())

    assert not journal_mock.called
    assert state.is_auto_sorted(cfg.state_db, "<r1@x.de>") is False


def test_unzugeordnet_mail_is_not_moved(mocker, cfg):
    imap = FakeImap(kandidaten=[_msg("5", "Newsletter", message_id="<n1@x.de>")])
    _patch_llm(mocker, antwort="1|unzugeordnet")
    journal_mock = mocker.patch("src.action_journal.record_action")

    auto_sort.run_auto_sort(cfg, imap, MagicMock())

    assert imap.moved == []
    assert not journal_mock.called


# --- Aktionsart 'markieren' --------------------------------------------------


def test_markieren_flags_mail_without_moving(mocker, mock_config, tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_markieren = replace(
        mock_config, state_db=db, enable_auto_sort=True, auto_sort_dry_run=False,
        sortierregeln_md="## Notfälle\nAktion: markieren\n",
    )
    imap = FakeImap(kandidaten=[_msg("9", "DRINGEND: Serverausfall", message_id="<not@x.de>")])
    _patch_llm(mocker, antwort="1|Notfälle")
    journal_mock = mocker.patch("src.action_journal.record_action", return_value="aktion-456")

    auto_sort.run_auto_sort(cfg_markieren, imap, MagicMock())

    assert imap.marked == ["9"]
    assert imap.moved == []
    args, _ = journal_mock.call_args
    assert args[1] == "markieren:\\Flagged:gesetzt"
    assert args[2] == cfg_markieren.imap_inbox_folder
    assert args[3] == cfg_markieren.imap_inbox_folder


def test_markieren_kann_ungelesene_mail_treffen_laut_kodierung():
    """AUTO-03 Ausnahme: 'markieren' darf auch ungelesene Mails treffen —
    dieser Test dokumentiert die Ausnahme auf Ebene der Aktionskodierung
    (kein Move, kein Bewegen). Die STRUKTURELLE Durchsetzung von 'nur
    gelesene Mails kommen als Kandidaten infrage' bleibt vollständig bei
    ImapClient.fetch_seen_candidates (siehe test_imap_client_auto_sort.py) —
    'markieren' bezieht sich hier auf denselben Kandidatenstrom wie
    'verschieben', die Ausnahme in AUTO-03 beschreibt reale Postfach-
    Situationen (z. B. Chat-getriggertes Markieren), nicht den Polling-Pfad."""
    assert auto_sort._markieren_aktion_string() == "markieren:\\Flagged:gesetzt"


# --- AUTO-05: Probelauf bewegt nichts, mit Gegenprobe -----------------------


def test_dry_run_moves_nothing_and_writes_no_journal(mocker, mock_config, tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_probe = replace(
        mock_config, state_db=db, enable_auto_sort=True, auto_sort_dry_run=True,
        sortierregeln_md="## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n",
    )
    imap = FakeImap(kandidaten=[_msg("5", "Rechnung", message_id="<r1@x.de>")])
    _patch_llm(mocker, antwort="1|Rechnungen")
    journal_mock = mocker.patch("src.action_journal.record_action")

    auto_sort.run_auto_sort(cfg_probe, imap, MagicMock())

    assert imap.moved == []
    assert imap.marked == []
    assert not journal_mock.called
    # Trotzdem als beurteilt vermerkt (kein Endlos-Reklassifizieren durch einen
    # dauerhaft aktiven Probelauf), aber NICHT verschoben/markiert -- siehe
    # state.mark_auto_sorted dry_run=True. Die Frage stellt hier bewusst der
    # PROBELAUF (dry_run_lauf=True): fuer einen scharfen Lauf blockt dieser
    # Eintrag seit Quick 260822-4ay gerade NICHT, siehe naechster Test.
    assert state.is_auto_sorted(cfg_probe.state_db, "<r1@x.de>", dry_run_lauf=True) is True


def test_dry_run_dann_scharf_bewegt_die_probelauf_treffer_doch(mocker, mock_config, tmp_path):
    """Quick 260822-4ay, Befund 2 der Live-Abnahme: der Probelauf darf seine
    eigenen Treffer nicht verbrauchen.

    Dieser Test stand vorher UMGEKEHRT im Repo (`..._trotzdem_nichts_erneut`)
    und nannte das Verhalten „dokumentiert, kein Bug". Der Betreiber hat es in
    der Abnahme als Defekt gemeldet, und zu Recht: der Probelauf ist genau das
    Werkzeug, mit dem man vor dem Scharfschalten prueft — wer ihn benutzt,
    verlor damit fuer jede geprueft Mail die spaetere Aktion. Ein Test, der
    einen Defekt als Absicht festschreibt, ist schlimmer als kein Test."""
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_probe = replace(
        mock_config, state_db=db, enable_auto_sort=True, auto_sort_dry_run=True,
        sortierregeln_md="## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n",
    )
    kandidat = _msg("5", "Rechnung", message_id="<r1@x.de>")
    imap = FakeImap(kandidaten=[kandidat])
    _patch_llm(mocker, antwort="1|Rechnungen")

    auto_sort.run_auto_sort(cfg_probe, imap, MagicMock())
    assert imap.moved == []

    cfg_scharf = replace(cfg_probe, auto_sort_dry_run=False)
    imap2 = FakeImap(kandidaten=[kandidat])
    _patch_llm(mocker, antwort="1|Rechnungen")
    mocker.patch("src.action_journal.record_action", return_value="aktion-1")

    auto_sort.run_auto_sort(cfg_scharf, imap2, MagicMock())

    assert imap2.moved == [("5", cfg_scharf.imap_inbox_folder, "Rechnungen")]


def test_probelauf_klassifiziert_dieselbe_mail_nicht_jeden_zyklus_neu(mocker, mock_config, tmp_path):
    """Gegenprobe zum Fix: der Kostendeckel bleibt. Ein dauerhaft aktiver
    Probelauf schickt dieselbe Mail nicht Zyklus fuer Zyklus erneut ans
    Modell."""
    db = tmp_path / "state.db"
    state.init_db(db)
    cfg_probe = replace(
        mock_config, state_db=db, enable_auto_sort=True, auto_sort_dry_run=True,
        sortierregeln_md="## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n",
    )
    kandidat = _msg("5", "Rechnung", message_id="<r1@x.de>")
    _patch_llm(mocker, antwort="1|Rechnungen")
    auto_sort.run_auto_sort(cfg_probe, FakeImap(kandidaten=[kandidat]), MagicMock())

    llm_mock2 = _patch_llm(mocker, antwort="1|Rechnungen")
    auto_sort.run_auto_sort(cfg_probe, FakeImap(kandidaten=[kandidat]), MagicMock())

    assert not llm_mock2.called


# --- AUTO-06: keine erneute Klassifikation bereits beurteilter Mails -------


def test_already_auto_sorted_message_is_excluded_from_next_cycle(mocker, cfg):
    kandidat = _msg("5", "Rechnung", message_id="<r1@x.de>")
    imap1 = FakeImap(kandidaten=[kandidat])
    _patch_llm(mocker, antwort="1|Rechnungen")
    mocker.patch("src.action_journal.record_action", return_value="aktion-1")

    auto_sort.run_auto_sort(cfg, imap1, MagicMock())
    assert imap1.moved == [("5", cfg.imap_inbox_folder, "Rechnungen")]

    imap2 = FakeImap(kandidaten=[kandidat])
    llm_mock2 = _patch_llm(mocker, antwort="1|Rechnungen")
    auto_sort.run_auto_sort(cfg, imap2, MagicMock())

    assert not llm_mock2.called
    assert imap2.moved == []


# --- Anonymizer (D-01..D-09 Muster, hier für den Sortier-Prompt) ------------


def test_prompt_does_not_contain_raw_iban_when_pii_redaction_enabled(mocker, cfg):
    kandidat = _msg(
        "5", "Rueckerstattung", text="Bitte ueberweisen an DE89370400440532013000", message_id="<r1@x.de>"
    )
    imap = FakeImap(kandidaten=[kandidat])
    llm_mock = _patch_llm(mocker, antwort="1|Rechnungen")

    auto_sort.run_auto_sort(cfg, imap, MagicMock())

    prompt = llm_mock.call_args.kwargs["prompt"]
    assert "DE89370400440532013000" not in prompt


def test_prompt_contains_raw_iban_when_pii_redaction_disabled(mocker, cfg):
    cfg_ohne_redaction = replace(cfg, enable_pii_redaction=False)
    kandidat = _msg(
        "5", "Rueckerstattung", text="IBAN DE89370400440532013000", message_id="<r1@x.de>"
    )
    imap = FakeImap(kandidaten=[kandidat])
    llm_mock = _patch_llm(mocker, antwort="1|Rechnungen")

    auto_sort.run_auto_sort(cfg_ohne_redaction, imap, MagicMock())

    prompt = llm_mock.call_args.kwargs["prompt"]
    assert "DE89370400440532013000" in prompt


# --- LLM-Fehler bricht den Zyklus sauber ab ---------------------------------


def test_llm_failure_moves_nothing(mocker, cfg):
    imap = FakeImap(kandidaten=[_msg("5", "Rechnung", message_id="<r1@x.de>")])
    mocker.patch("src.auto_sort.llm.llm_call", side_effect=Exception("timeout"))

    auto_sort.run_auto_sort(cfg, imap, MagicMock())

    assert imap.moved == []
    assert imap.marked == []
