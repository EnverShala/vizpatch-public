"""Tests für die vierte Matching-Stufe in `_resolve_existing_folder`
(Phase 13, Plan 13-06, MBOX-20): case-insensitiver, EINDEUTIGER
Substring-Treffer, mit Mehrdeutigkeits-Verweigerung statt Raten.

Deckt ab:
1. Jede Stufe der Prioritätskette einzeln: exakt, präfigiert, case-
   insensitiv-exakt, eindeutiger Substring.
2. Mehrere Substring-Treffer -> `FolderAmbiguousError` mit Kandidatenliste,
   KEIN gewählter Ordner.
3. Ein exakter Treffer gewinnt IMMER gegen einen Substring-Treffer.
4. `ordner_erstellen` legt bei eindeutigem Substring-Treffer KEINEN
   zweiten Ordner an (der eigentliche Zweck von MBOX-20).
5. Die Schutzliste geschützter Ordner bleibt wirksam, auch wenn die
   Auflösung über die neue Substring-Stufe läuft.

Die Regressionsfreiheit der BESTEHENDEN Ordner-Operations-Tests
(`ordner_erstellen`, `ordner_loeschen`, `ordner_umbenennen`,
`mail_verschieben`, `mails_verschieben`) wird NICHT hier, sondern durch
Ausführung der jeweils eigenen Testdateien nachgewiesen (siehe SUMMARY.md
für die Zusammenfassung) — dieses höchste Regressionsrisiko der Phase
rechtfertigt einen eigenen Verifikationsschritt statt einer Duplizierung
hier.
"""
from __future__ import annotations

import types

import pytest

from .test_chat_tools import _fake_mailbox, _setup_env, _write_agent_env


def _folder_info(name, delim=".", flags=()):
    return types.SimpleNamespace(name=name, delim=delim, flags=flags)


# --- Prioritätskette, je eine Stufe ------------------------------------------


def test_stufe_1_exakter_treffer_gewinnt():
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX"), _folder_info("Rechnungen")]
    assert chat_tools._resolve_existing_folder(infos, "Rechnungen", ".", "") == "Rechnungen"


def test_stufe_2_praefigierter_treffer():
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX"), _folder_info("INBOX.Rechnungen")]
    assert (
        chat_tools._resolve_existing_folder(infos, "Rechnungen", ".", "INBOX.")
        == "INBOX.Rechnungen"
    )


def test_stufe_3_case_insensitiver_exakter_treffer():
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX"), _folder_info("Rechnungen")]
    assert chat_tools._resolve_existing_folder(infos, "rechnungen", ".", "") == "Rechnungen"


def test_stufe_3_case_insensitiver_treffer_auf_praefigierter_variante():
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX"), _folder_info("INBOX.Rechnungen")]
    assert (
        chat_tools._resolve_existing_folder(infos, "rechnungen", ".", "INBOX.")
        == "INBOX.Rechnungen"
    )


def test_stufe_4_eindeutiger_substring_treffer_findet_jahresordner():
    """Der zentrale MBOX-20-Fall: 'Rechnungen' findet 'Rechnungen 2026',
    obwohl kein exakter/präfigierter/case-insensitiver Treffer existiert."""
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX"), _folder_info("INBOX.Rechnungen 2026")]
    assert (
        chat_tools._resolve_existing_folder(infos, "Rechnungen", ".", "INBOX.")
        == "INBOX.Rechnungen 2026"
    )


def test_stufe_4_kein_treffer_liefert_none():
    import src.chat_tools as chat_tools

    infos = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    assert chat_tools._resolve_existing_folder(infos, "Rechnungen", ".", "INBOX.") is None


# --- Mehrdeutigkeit: kein Raten -----------------------------------------------


def test_mehrere_substring_treffer_werfen_ambiguous_mit_kandidaten():
    import src.chat_tools as chat_tools

    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Rechnungen 2025"),
        _folder_info("INBOX.Rechnungen 2026"),
    ]
    with pytest.raises(chat_tools.FolderAmbiguousError) as exc_info:
        chat_tools._resolve_existing_folder(infos, "Rechnungen", ".", "INBOX.")

    error = exc_info.value
    assert set(error.candidates) == {"INBOX.Rechnungen 2025", "INBOX.Rechnungen 2026"}
    # Beide Kandidaten muessen dem Betreiber NAMENTLICH genannt werden.
    assert "INBOX.Rechnungen 2025" in str(error)
    assert "INBOX.Rechnungen 2026" in str(error)


def test_exakter_treffer_gewinnt_immer_gegen_substring_treffer():
    """Existieren 'Rechnungen' UND 'Rechnungen 2026', waehlt die Eingabe
    'Rechnungen' den EXAKTEN Ordner — keine Mehrdeutigkeitsmeldung, obwohl
    'Rechnungen' auch ein gueltiger Substring-Treffer waere."""
    import src.chat_tools as chat_tools

    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Rechnungen"),
        _folder_info("INBOX.Rechnungen 2026"),
    ]
    result = chat_tools._resolve_existing_folder(infos, "Rechnungen", ".", "INBOX.")
    assert result == "INBOX.Rechnungen"


# --- ordner_erstellen: eindeutiger Substring-Treffer -> keine Dopplung -------


def test_ordner_erstellen_legt_bei_eindeutigem_substring_treffer_keinen_zweiten_ordner_an(
    mocker, tmp_path, monkeypatch
):
    """Der eigentliche Zweck von MBOX-20: ein Betreiber, der 'Rechnungen'
    sagt, obwohl bereits 'Rechnungen 2026' existiert, bekommt den
    vorhandenen Ordner gemeldet statt eines versehentlichen Duplikats."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    infos = [_folder_info("INBOX"), _folder_info("INBOX.Rechnungen 2026")]
    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.return_value = infos
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert result["erstellt"] is False
    assert "fehler" not in result
    assert result["ordner"] == "INBOX.Rechnungen 2026"
    mock_mailbox.folder.create.assert_not_called()


def test_ordner_erstellen_meldet_mehrdeutigkeit_statt_zu_erraten(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Rechnungen 2025"),
        _folder_info("INBOX.Rechnungen 2026"),
    ]
    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.return_value = infos
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_erstellen("info", name="Rechnungen")

    assert "fehler" in result
    assert "INBOX.Rechnungen 2025" in result["fehler"]
    assert "INBOX.Rechnungen 2026" in result["fehler"]
    mock_mailbox.folder.create.assert_not_called()


# --- Bestandsaufrufer: mail_verschieben/mails_verschieben --------------------


def test_mail_verschieben_meldet_mehrdeutigen_zielordner_statt_zu_erraten(
    mocker, tmp_path, monkeypatch
):
    """Regressionsrisiko-Kern des Plans: `_resolve_source_and_target_folders`
    (gemeinsam von `mail_verschieben`/`mails_verschieben` genutzt) darf bei
    Mehrdeutigkeit NICHT den ersten Treffer waehlen und in den falschen
    Ordner verschieben."""
    from .test_chat_tools import _msg_with_mid

    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    infos = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Rechnungen 2025"),
        _folder_info("INBOX.Rechnungen 2026"),
    ]
    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="1")])
    mock_mailbox.folder.list.return_value = infos
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_verschieben("info", uid="1", ziel_ordner="Rechnungen")

    assert "fehler" in result
    assert "INBOX.Rechnungen 2025" in result["fehler"]
    assert "INBOX.Rechnungen 2026" in result["fehler"]
    mock_mailbox.move.assert_not_called()


# --- Schutzliste bleibt wirksam ------------------------------------------------


def test_ordner_loeschen_substring_treffer_auf_geschuetzten_ordner_bleibt_geschuetzt(
    mocker, tmp_path, monkeypatch
):
    """Ein Substring-Treffer, der auf einen geschützten Ordner (hier: INBOX
    selbst, per Namensvergleich) aufgelöst wird, darf ihn NICHT löschbar
    machen — dieselbe, nicht abschaltbare Schutzliste greift unverändert
    auf dem AUFGELÖSTEN Namen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    infos = [_folder_info("INBOX")]
    mock_mailbox = _fake_mailbox()
    mock_mailbox.folder.list.return_value = infos
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    import src.chat_tools as chat_tools

    # "INB" ist ein eindeutiger Substring-Treffer auf "INBOX" (kein exakter,
    # praefigierter oder case-insensitiv-exakter Treffer existiert).
    result = chat_tools.ordner_loeschen("info", name="INB")

    assert "fehler" in result
    assert "Posteingang" in result["fehler"]
    mock_mailbox.folder.delete.assert_not_called()
