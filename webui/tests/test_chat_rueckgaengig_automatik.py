"""Integrationsnachweis für AUTO-04 (Phase 14, Plan 14-01): eine Journal-
Zeile mit `quelle='automatik'` — so, wie sie `agent/src/action_journal.py`
schreiben würde — ist über das BESTEHENDE `letzte_aktion_rueckgaengig`
(webui/src/chat_tools.py, Phase 13) genauso rückgängig zu machen wie eine
manuelle Chat-Aktion. Das beweist die Kernaussage aus AUTO-04 ("ist über
letzte_aktion_rueckgaengig umkehrbar") konkret, statt sie nur zu behaupten.

Simuliert wird die AGENT-Schreibseite über `webui.action_journal.record_action`
mit `quelle='automatik'` (byte-gleiches Schema, siehe
test_action_journal_schema_sync.py) — ein echter Cross-Prozess-Test über
zwei Docker-Images hinweg ist hier nicht möglich, aber die Schema- und
Verhaltens-Kompatibilität ist damit vollständig abgedeckt."""
from __future__ import annotations

from unittest.mock import patch

from .test_chat_folder_tools import _folder_info
from .test_chat_tools import _fake_mailbox, _msg_with_mid, _setup_env, _write_agent_env


def test_automatischer_verschieben_eintrag_ist_rueckgaengig_machbar(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info",
        "verschieben",
        "INBOX",
        "INBOX.Rechnungen",
        [{"uid": "1", "message_id": "<a@example.com>", "betreff": ""}],
        quelle="automatik",
    )

    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="7", mid="<a@example.com>")])
    mock_mailbox.folder.list.return_value = infos
    mock_mailbox.client.capabilities = ("MOVE",)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert ergebnis["zurueckgestellt"] == 1
    assert ergebnis["nicht_gefunden"] == []
    mock_mailbox.move.assert_called_once_with(["7"], "INBOX")


def test_automatischer_markieren_eintrag_ist_rueckgaengig_machbar(tmp_path, monkeypatch):
    """Die Aktion-Kodierung 'markieren:<flag>:<richtung>' (identisch zu
    `mails_markieren`, siehe agent/src/auto_sort._markieren_aktion_string)
    hakt korrekt in den Markieren-Rückweg ein — KEIN Move-Versuch."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info",
        "markieren:\\Flagged:gesetzt",
        "INBOX",
        "INBOX",
        [{"uid": "1", "message_id": "<a@example.com>", "betreff": ""}],
        quelle="automatik",
    )

    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="7", mid="<a@example.com>")])
    mock_mailbox.folder.list.return_value = [_folder_info("INBOX", delim=".")]

    with patch("src.chat_tools.MailBox", return_value=mock_mailbox):
        ergebnis = chat_tools.letzte_aktion_rueckgaengig("info")

    assert "fehler" not in ergebnis
    assert not mock_mailbox.move.called


def test_automatischer_eintrag_erscheint_im_automatisch_sortierungen_bericht_und_danach_als_rueckgaengig(
    mocker, tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.action_journal as action_journal
    import src.chat_tools as chat_tools

    action_journal.record_action(
        "info", "verschieben", "INBOX", "INBOX.Rechnungen",
        [{"uid": "1", "message_id": "<a@example.com>", "betreff": ""}],
        quelle="automatik",
    )

    vorher = chat_tools.automatisch_sortierungen("info")
    assert len(vorher["aktionen"]) == 1
    assert vorher["aktionen"][0]["rueckgaengig_gemacht"] is False

    infos = [_folder_info("INBOX", delim="."), _folder_info("INBOX.Rechnungen", delim=".")]
    mock_mailbox = _fake_mailbox([_msg_with_mid(uid="7", mid="<a@example.com>")])
    mock_mailbox.folder.list.return_value = infos
    mock_mailbox.client.capabilities = ("MOVE",)
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    chat_tools.letzte_aktion_rueckgaengig("info")

    nachher = chat_tools.automatisch_sortierungen("info")
    assert nachher["aktionen"][0]["rueckgaengig_gemacht"] is True
