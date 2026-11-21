"""Tests für `ordner_umbenennen` (Quick 260813-06b, Teil A): IMAP RENAME,
Namensauflösung/Qualifizierung/Sanitizer (1:1 wiederverwendet aus
`ordner_erstellen`/`ordner_loeschen`), Schutzliste (`_protected_folder_reason`
WIEDERVERWENDET, nicht dupliziert) für Quelle UND Zielname, bewusst KEINE
Nachfahren-Prüfung (A2/T-06b-11), Post-RENAME-Verifikation über ein ZWEITES,
abweichendes `folder.list()`-Ergebnis (kein stiller Erfolg), sowie die
Registry-/Schema-Anbindung (kein Flag, `TOOL_HANDLERS`/`TOOL_SCHEMAS`/
`_TOOL_ACTIVITY_LABELS`, NICHT in `_ANON_AWARE_TOOLS`/`_SESSION_SCOPED_TOOLS`/
`_REQUEST_SCOPED_TOOLS`).

Deckt ab:
1. Erfolg ohne Kinder (genau EIN `rename`-Aufruf, Ergebnisform).
2. Unterordner wandern mit (Präfix-Ersetzung an der Delimiter-Grenze),
   weiterhin genau EIN `rename`-Aufruf.
3. Post-RENAME-Verifikation nutzt ein ZWEITES, vom ersten abweichendes
   `folder.list()`-Ergebnis — unbestätigt (alter Name noch da / neuer fehlt)
   -> `fehler` statt Erfolg.
4. Fehlender erwarteter Unterordner-Zielpfad im frischen Listing -> Erfolg
   bleibt bestehen, aber `unterordner_unbestaetigt` + `hinweis`.
5. Schutz am Quellordner: alle neun SPECIAL-USE-Rollen + namensbasiert.
6. Schutz am Zielnamen (belt-and-braces), namensbasiert.
7. Geschützter NACHFAHRE blockiert NICHT (bewusste Entscheidung, A2).
8. Zielname existiert bereits -> `fehler`, `rename` nie aufgerufen.
9. Quellordner existiert nicht -> `fehler` verweist auf `ordner_auflisten`.
10. Sanitizer lehnt CR/LF und leeren `neuer_name` ab, `rename` nie aufgerufen.
11. Alter und neuer Name lösen auf denselben Pfad auf -> "heißt bereits so".
12. `folder.rename`/`folder.list`/Login werfen -> `fehler`-Dict, kein Crash.
13. Invalider `agent_id` -> `ValueError` propagiert.
14. KEIN Flag: `ENABLE_FOLDER_DELETE=false` hat KEINE Wirkung.
15. Registry: `TOOL_HANDLERS`/`TOOL_SCHEMAS`/`_TOOL_ACTIVITY_LABELS`, NICHT in
    den anderen Sets, Schema-Properties, `_tool_schemas_for` filtert nicht.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from .test_chat_tools import _fake_mailbox, _setup_env, _write_agent_env


def _folder_info(name, delim=".", flags=()):
    """Wie `test_chat_folder_delete_tools._folder_info` — MIT `delim`-Attribut
    (die Bestandsfixture `_fake_folder_info` in `test_chat_tools.py` hat
    keins)."""
    import types

    return types.SimpleNamespace(name=name, delim=delim, flags=flags)


def _setup_ordner_umbenennen(
    mocker,
    tmp_path,
    monkeypatch,
    folder_infos,
    folder_infos_nachher=None,
    *,
    rename_raises=False,
    list_raises=False,
    list_raises_after_rename=False,
    login_raises=False,
):
    """Baut eine Mock-Mailbox, deren `folder.list()` VOR dem `rename()`-Aufruf
    `folder_infos` liefert und DANACH `folder_infos_nachher` (Default: dieselbe
    Liste MINUS des alten Namens PLUS des neuen — der Aufrufer übergibt i.d.R.
    eine explizit abweichende Liste, damit die Verifikation echt geprüft wird
    statt tautologisch zu sein). `folder.rename` markiert den Umschlagpunkt
    per Seiteneffekt — dieselbe Instanz wird dadurch VOR dem Rename mit dem
    Vorher-Zustand und NACH dem Rename mit dem Nachher-Zustand befragt, exakt
    wie ein echter IMAP-Server."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    state = {"renamed": False}

    mock_mailbox = MagicMock()
    mock_mailbox.__enter__ = MagicMock(return_value=mock_mailbox)
    mock_mailbox.__exit__ = MagicMock(return_value=False)
    if login_raises:
        mock_mailbox.login.side_effect = RuntimeError("auth failed")

    nachher = folder_infos_nachher if folder_infos_nachher is not None else folder_infos

    def _list(*_a, **_kw):
        if list_raises and not state["renamed"]:
            raise RuntimeError("list failed (vorher)")
        if list_raises_after_rename and state["renamed"]:
            raise RuntimeError("list failed (nachher)")
        return list(nachher) if state["renamed"] else list(folder_infos)

    mock_mailbox.folder.list.side_effect = _list

    def _rename(alt, neu, *_a, **_kw):
        if rename_raises:
            raise RuntimeError("rename failed")
        state["renamed"] = True

    mock_mailbox.folder.rename.side_effect = _rename

    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)
    return mock_mailbox


# --- Erfolg ohne Kinder -------------------------------------------------------


def test_ordner_umbenennen_success_no_children(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden 2024")]
    nachher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden 2025")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, nachher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden 2024", "Kunden 2025")

    assert result == {
        "ordner_alt": "INBOX.Kunden 2024",
        "ordner_neu": "INBOX.Kunden 2025",
        "umbenannt": True,
        "unterordner": [],
        "hinweis": None,
    }
    mock_mailbox.folder.rename.assert_called_once_with("INBOX.Kunden 2024", "INBOX.Kunden 2025")


# --- Unterordner wandern mit ---------------------------------------------------


def test_ordner_umbenennen_children_travel_with_single_rename_call(mocker, tmp_path, monkeypatch):
    vorher = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Kunden"),
        _folder_info("INBOX.Kunden.2025"),
        _folder_info("INBOX.Kunden.2025.Q1"),
    ]
    nachher = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Neu"),
        _folder_info("INBOX.Neu.2025"),
        _folder_info("INBOX.Neu.2025.Q1"),
    ]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, nachher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Neu")

    assert result["umbenannt"] is True
    assert result["ordner_alt"] == "INBOX.Kunden"
    assert result["ordner_neu"] == "INBOX.Neu"
    assert {"vorher": "INBOX.Kunden.2025", "nachher": "INBOX.Neu.2025"} in result["unterordner"]
    assert {"vorher": "INBOX.Kunden.2025.Q1", "nachher": "INBOX.Neu.2025.Q1"} in result["unterordner"]
    assert len(result["unterordner"]) == 2
    mock_mailbox.folder.rename.assert_called_once_with("INBOX.Kunden", "INBOX.Neu")


# --- Post-RENAME-Verifikation --------------------------------------------------


def test_ordner_umbenennen_verification_fails_if_old_name_still_listed(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Alt")]
    # Server "vergisst" das Rename im zweiten Listing (alter Name noch da).
    nachher = [_folder_info("INBOX"), _folder_info("INBOX.Alt"), _folder_info("INBOX.Neu")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, nachher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Alt", "Neu")

    assert "fehler" in result
    assert "nicht bestätigt" in result["fehler"]
    mock_mailbox.folder.rename.assert_called_once()


def test_ordner_umbenennen_verification_fails_if_new_name_missing(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Alt")]
    # Zweites Listing zeigt weder alten noch neuen Namen (Server-Inkonsistenz).
    nachher = [_folder_info("INBOX")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, nachher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Alt", "Neu")

    assert "fehler" in result
    assert "nicht bestätigt" in result["fehler"]


def test_ordner_umbenennen_second_list_raises_returns_fehler_no_crash(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Alt")]
    mock_mailbox = _setup_ordner_umbenennen(
        mocker, tmp_path, monkeypatch, vorher, list_raises_after_rename=True
    )

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Alt", "Neu")

    assert "fehler" in result
    assert "nicht bestätigt" in result["fehler"]
    mock_mailbox.folder.rename.assert_called_once()


# --- Unbestätigter Unterordner --------------------------------------------------


def test_ordner_umbenennen_unconfirmed_subfolder_reports_hinweis_but_still_success(
    mocker, tmp_path, monkeypatch
):
    vorher = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Kunden"),
        _folder_info("INBOX.Kunden.2025"),
    ]
    # Hauptordner korrekt umbenannt, aber der Unterordner fehlt im frischen
    # Listing (z. B. Server-Verzögerung) — Erfolg bleibt bestehen, mit Hinweis.
    nachher = [_folder_info("INBOX"), _folder_info("INBOX.Neu")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, nachher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Neu")

    assert result["umbenannt"] is True
    assert result["unterordner_unbestaetigt"] == ["INBOX.Neu.2025"]
    assert result["hinweis"] is not None
    assert "INBOX.Neu.2025" in result["hinweis"]
    mock_mailbox.folder.rename.assert_called_once()


# --- Schutz am Quellordner: neun Rollen ----------------------------------------


@pytest.mark.parametrize(
    "role",
    ["Inbox", "Drafts", "Sent", "Trash", "Junk", "Archive", "All", "Flagged", "Important"],
)
def test_ordner_umbenennen_refuses_every_special_use_role_as_source(role, mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Ziel", flags=(f"\\{role}",))]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Ziel", "Neu")

    assert "fehler" in result
    assert role in result["fehler"]
    mock_mailbox.folder.rename.assert_not_called()


def test_ordner_umbenennen_refuses_inbox_as_source_by_name(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "INBOX", "Neu")

    assert "fehler" in result
    assert "Posteingang" in result["fehler"]
    mock_mailbox.folder.rename.assert_not_called()


# --- Schutz am Zielnamen (belt-and-braces) -------------------------------------


def test_ordner_umbenennen_refuses_inbox_as_target_because_it_already_exists(mocker, tmp_path, monkeypatch):
    """`INBOX` existiert als Ordner IMMER real — deshalb greift hier bereits
    die generische "existiert bereits"-Prüfung (Schritt 5) VOR der
    Schutzprüfung (Schritt 7); das Ergebnis ist trotzdem ein `fehler` ohne
    `rename`-Aufruf, also identisch abgelehnt. Die eigentliche
    Belt-and-braces-Schutzprüfung für NICHT real existierende, aber
    namensgebundene Ziele deckt der folgende Test ab."""
    vorher = [_folder_info("INBOX"), _folder_info("Kunden")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "INBOX")

    assert "fehler" in result
    mock_mailbox.folder.rename.assert_not_called()


def test_ordner_umbenennen_refuses_target_matching_configured_sent_folder_even_if_not_real(
    mocker, tmp_path, monkeypatch
):
    """Belt-and-braces (A4/T-06b-02): `_write_agent_env` nutzt eine
    IONOS-Adresse, deren providerspezifischer Sent-Fallback "Gesendete
    Objekte" ist (`provider_config.STATIC_PROVIDERS["ionos.de"]["sent"]`) —
    ohne Server-Announcement (`\\Sent`) und ohne reale Ordner "Gesendete
    Objekte" in der Liste. Die Zielnamen-Schutzprüfung muss trotzdem greifen,
    OBWOHL `_resolve_existing_folder` für diesen Namen `None` liefert (der
    Ordner existiert ja noch nicht) und die generische
    "existiert bereits"-Prüfung deshalb NICHT feuert."""
    vorher = [_folder_info("INBOX"), _folder_info("Kunden")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Gesendete Objekte")

    assert "fehler" in result
    assert "Gesendet" in result["fehler"]
    mock_mailbox.folder.rename.assert_not_called()


# --- Geschützter NACHFAHRE blockiert NICHT -------------------------------------


def test_ordner_umbenennen_protected_descendant_does_not_block(mocker, tmp_path, monkeypatch):
    vorher = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Kunden"),
        _folder_info("INBOX.Kunden.Drafts", flags=("\\Drafts",)),
    ]
    nachher = [
        _folder_info("INBOX"),
        _folder_info("INBOX.Neu"),
        _folder_info("INBOX.Neu.Drafts", flags=("\\Drafts",)),
    ]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, nachher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Neu")

    assert result["umbenannt"] is True
    mock_mailbox.folder.rename.assert_called_once_with("INBOX.Kunden", "INBOX.Neu")


# --- Zielname existiert bereits ------------------------------------------------


def test_ordner_umbenennen_refuses_when_target_already_exists(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Alt"), _folder_info("INBOX.Neu")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Alt", "Neu")

    assert "fehler" in result
    assert "existiert bereits" in result["fehler"]
    mock_mailbox.folder.rename.assert_not_called()


# --- Quellordner existiert nicht -----------------------------------------------


def test_ordner_umbenennen_refuses_when_source_missing_mentions_ordner_auflisten(
    mocker, tmp_path, monkeypatch
):
    vorher = [_folder_info("INBOX")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Nichtvorhanden", "Neu")

    assert "fehler" in result
    assert "ordner_auflisten" in result["fehler"]
    mock_mailbox.folder.rename.assert_not_called()


# --- Sanitizer ------------------------------------------------------------------


def test_ordner_umbenennen_sanitizer_rejects_crlf_in_source_name(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "a\r\nb", "Neu")

    assert "fehler" in result
    mock_mailbox.folder.rename.assert_not_called()


def test_ordner_umbenennen_sanitizer_rejects_crlf_in_target_name(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "a\r\nb")

    assert "fehler" in result
    mock_mailbox.folder.rename.assert_not_called()


def test_ordner_umbenennen_sanitizer_rejects_empty_target_name(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "")

    assert "fehler" in result
    mock_mailbox.folder.rename.assert_not_called()


# --- Identischer Pfad -----------------------------------------------------------


def test_ordner_umbenennen_source_and_target_resolve_to_same_path(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Kunden")

    assert "fehler" in result
    assert "heißt bereits so" in result["fehler"]
    mock_mailbox.folder.rename.assert_not_called()


# --- Fehlerpfade: rename wirft / list wirft / Login schlägt fehl --------------


def test_ordner_umbenennen_rename_raises_returns_fehler_no_crash(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    mock_mailbox = _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, rename_raises=True)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Neu")

    assert "fehler" in result
    mock_mailbox.folder.rename.assert_called_once()


def test_ordner_umbenennen_first_list_raises_returns_fehler(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX")]
    _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, list_raises=True)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Neu")

    assert "fehler" in result


def test_ordner_umbenennen_login_failure_returns_fehler(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX")]
    _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, login_raises=True)

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Neu")

    assert "fehler" in result


def test_ordner_umbenennen_invalid_agent_id_propagates_value_error(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.ordner_umbenennen("../evil", "Kunden", "Neu")


# --- KEIN Flag -------------------------------------------------------------------


def test_ordner_umbenennen_ignores_enable_folder_delete_flag_per_agent(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    nachher = [_folder_info("INBOX"), _folder_info("INBOX.Neu")]
    _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, nachher)
    monkeypatch.setenv("ENABLE_FOLDER_DELETE", "false")

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Neu")

    assert result["umbenannt"] is True


def test_ordner_umbenennen_ignores_enable_folder_delete_flag_global(mocker, tmp_path, monkeypatch):
    vorher = [_folder_info("INBOX"), _folder_info("INBOX.Kunden")]
    nachher = [_folder_info("INBOX"), _folder_info("INBOX.Neu")]
    _setup_ordner_umbenennen(mocker, tmp_path, monkeypatch, vorher, nachher)
    monkeypatch.setenv("ENABLE_FOLDER_DELETE", "false")

    import src.chat_tools as chat_tools

    result = chat_tools.ordner_umbenennen("info", "Kunden", "Neu")

    assert result["umbenannt"] is True


# --- Registry / Schema (Task 5) -------------------------------------------------


def test_ordner_umbenennen_registered_everywhere():
    import src.chat_tools as chat_tools

    assert chat_tools.TOOL_HANDLERS["ordner_umbenennen"] is chat_tools.ordner_umbenennen
    assert chat_tools._TOOL_ACTIVITY_LABELS["ordner_umbenennen"] == "Ordner umbenennen…"
    names = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "ordner_umbenennen" in names
    assert "ordner_umbenennen" not in chat_tools._ANON_AWARE_TOOLS
    assert "ordner_umbenennen" not in chat_tools._SESSION_SCOPED_TOOLS
    assert "ordner_umbenennen" not in chat_tools._REQUEST_SCOPED_TOOLS


def test_ordner_umbenennen_schema_has_exact_two_properties():
    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "ordner_umbenennen")
    assert set(schema["input_schema"]["properties"].keys()) == {"name", "neuer_name"}
    assert schema["input_schema"]["required"] == ["name", "neuer_name"]


def test_ordner_umbenennen_present_in_tool_schemas_for_even_with_folder_delete_disabled(
    tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.chat_tools as chat_tools

    monkeypatch.setenv("ENABLE_FOLDER_DELETE", "false")
    names = {s["name"] for s in chat_tools._tool_schemas_for("info")}
    assert "ordner_umbenennen" in names


def test_mail_anhang_lesen_description_mentions_images_by_default(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools._tool_schemas_for("info") if s["name"] == "mail_anhang_lesen")
    assert "Bild" in schema["description"]


def test_mail_anhang_lesen_description_falls_back_to_old_text_when_vision_disabled_per_agent(
    tmp_path, monkeypatch
):
    import src.agents_io as agents_io

    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    agents_io.write_env("info", {"ENABLE_ATTACHMENT_VISION": "false"})

    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools._tool_schemas_for("info") if s["name"] == "mail_anhang_lesen")
    assert schema["description"] == chat_tools._ATTACHMENT_VISION_OFF_DESC["mail_anhang_lesen"]


def test_mail_anhang_lesen_description_falls_back_to_old_text_when_vision_disabled_global(
    tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_ATTACHMENT_VISION", "false")

    import src.chat_tools as chat_tools

    schema = next(s for s in chat_tools._tool_schemas_for("info") if s["name"] == "mail_anhang_lesen")
    assert schema["description"] == chat_tools._ATTACHMENT_VISION_OFF_DESC["mail_anhang_lesen"]
