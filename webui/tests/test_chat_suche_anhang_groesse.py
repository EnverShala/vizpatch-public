"""Tests für die Aufräum-Filter in `mails_suchen` (Phase 13, Plan 13-04, MBOX-05)
und für die Anhangs-Definition (Task 3).

Drei Eigenschaften werden hier festgeschrieben:

1. **`min_groesse_mb` ist serverseitig, `nur_mit_anhang` clientseitig.** IMAP
   kennt kein natives Kriterium für „hat Anhang" (13-RESEARCH.md Abschnitt 4) —
   `min_groesse_mb` wird zu `size_gt` in BYTES, `nur_mit_anhang` filtert NACH
   dem Fetch. Geprüft wird das gebaute Suchkriterium, nicht nur das Ergebnis:
   ein Test, der nur das Ergebnis prüft, würde eine versehentlich serverseitige
   Anhangs-Bedingung nicht bemerken.

2. **Bei aktivem `nur_mit_anhang` wird KEINE exakte Gesamtzahl behauptet.** Die
   echte Trefferzahl ist ohne Fetch über ALLE Treffer nicht ermittelbar. Eine
   erfundene Zahl wäre schlimmer als keine — der Agent würde dem Betreiber
   „12 Mails gefunden" melden, wo es 300 sind.

3. **Die Anhangs-Definition ist in allen Werkzeugen dieselbe.** `_is_real_attachment`
   entscheidet für `mails_suchen(nur_mit_anhang=True)` UND für
   `_attachment_metadata` (also `mail_lesen` und `mail_anhaenge_auflisten`).
   Liefen die auseinander, würden zwei Werkzeuge über dieselbe Mail
   widersprechen.

Zur Anhangs-Definition selbst: `imap_tools.MailMessage.attachments` zählt JEDEN
MIME-Part mit `Content-ID` als Anhang — auch ein per HTML-Signatur eingebettetes
Logo. Wäre das ungefiltert durchgereicht, wäre `nur_mit_anhang` für praktisch
jede Mail mit Signatur wahr und damit nutzlos. Umgekehrt darf NICHT auf
`content_disposition == "attachment"` geprüft werden: zwei legitime Anhangsarten
tragen einen LEEREN Wert, weil der Header ganz fehlt. Deshalb wird nur `inline`
ausgeschlossen. Beide Richtungen sind unten als Test festgehalten.

OFFEN für die Live-Abnahme: diese Tests arbeiten mit konstruierten Anhangs-
Attrappen. Dass ein echter Mail-Client (Outlook-Signatur mit eingebettetem Logo)
tatsächlich `Content-Disposition: inline` setzt, ist am Test-Postfach zu
bestätigen.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

from .test_chat_tools import _fake_mailbox, _msg, _setup_env, _write_agent_env


def _att(filename="Rechnung.pdf", content_disposition="attachment", content_type="application/pdf"):
    """Anhangs-Attrappe. `content_disposition=None` bildet den Fall ab, dass der
    Header komplett fehlt (imap-tools liefert dann einen leeren Wert)."""
    return types.SimpleNamespace(
        filename=filename,
        content_disposition=content_disposition,
        content_type=content_type,
        size=1024,
        payload=b"x",
    )


def _msg_mit_anhaengen(uid="42", attachments=()):
    m = _msg(uid=uid)
    m.attachments = list(attachments)
    return m


# --- Anhangs-Definition (Task 3) ---------------------------------------------


def test_inline_eingebettetes_bild_gilt_nicht_als_anhang():
    """Der Kernfall: ein Signatur-Logo darf `nur_mit_anhang` nicht auslösen."""
    import src.chat_tools as chat_tools

    logo = _att(filename="", content_disposition="inline", content_type="image/png")

    assert chat_tools._is_real_attachment(logo) is False


def test_inline_mit_grossschreibung_und_leerzeichen_gilt_nicht_als_anhang():
    """Header-Werte sind nicht normiert — `Inline` und ` inline ` müssen ebenso
    greifen wie `inline`, sonst hängt das Verhalten am sendenden Client."""
    import src.chat_tools as chat_tools

    assert chat_tools._is_real_attachment(_att(content_disposition="Inline")) is False
    assert chat_tools._is_real_attachment(_att(content_disposition=" inline ")) is False


def test_normaler_anhang_gilt_als_anhang():
    import src.chat_tools as chat_tools

    assert chat_tools._is_real_attachment(_att()) is True


def test_anhang_ohne_content_disposition_header_gilt_als_anhang():
    """Gegenprobe zur naheliegenden Fehlimplementierung: eine Prüfung auf
    `content_disposition == "attachment"` würde diesen LEGITIMEN Anhang
    ausschließen. Ältere Mail-Clients hängen Dateien nur über den `name=`-
    Parameter im Content-Type an, ohne eigenen Content-Disposition-Header.
    """
    import src.chat_tools as chat_tools

    alt_client_anhang = _att(filename="Scan.pdf", content_disposition=None)

    assert chat_tools._is_real_attachment(alt_client_anhang) is True


def test_weitergeleiteter_rfc822_anhang_gilt_als_anhang():
    """Zweiter Fall mit leerem Content-Disposition: eine weitergeleitete Mail
    als `message/rfc822`-Part. Für Phase 13 besonders relevant, weil Plan 13-08
    das Weiterleiten baut."""
    import src.chat_tools as chat_tools

    weitergeleitet = _att(
        filename="Weitergeleitet.eml", content_disposition=None, content_type="message/rfc822"
    )

    assert chat_tools._is_real_attachment(weitergeleitet) is True


def test_anhangsdefinition_ist_in_suche_und_auflistung_dieselbe(mocker, tmp_path, monkeypatch):
    """Konsistenz-Nachweis: eine Mail, die NUR ein Inline-Logo trägt, wird von
    `mails_suchen(nur_mit_anhang=True)` nicht geliefert UND von der Metadaten-
    Aufbereitung (Grundlage von `mail_lesen`/`mail_anhaenge_auflisten`) als
    anhanglos geführt. Liefen die beiden auseinander, würde der Agent eine Mail
    als „hat Anhang" melden, die die Suche nicht findet — oder umgekehrt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    nur_logo = _msg_mit_anhaengen(
        uid="7", attachments=[_att(filename="", content_disposition="inline")]
    )

    # Seite 1: die Metadaten-Aufbereitung führt keinen Anhang.
    assert chat_tools._attachment_metadata(nur_logo, None) == []

    # Seite 2: die Suche mit Anhangsfilter liefert die Mail nicht.
    mock_mailbox = _fake_mailbox([nur_logo])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_suchen("info", nur_mit_anhang=True)

    assert ergebnis["treffer"] == []


def test_echter_anhang_wird_von_suche_und_auflistung_gleich_gewertet(mocker, tmp_path, monkeypatch):
    """Gegenstück: eine Mail mit echtem Anhang erscheint in BEIDEN Sichten."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mit_pdf = _msg_mit_anhaengen(uid="8", attachments=[_att(filename="Rechnung.pdf")])

    assert len(chat_tools._attachment_metadata(mit_pdf, None)) == 1

    mock_mailbox = _fake_mailbox([mit_pdf])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_suchen("info", nur_mit_anhang=True)

    assert len(ergebnis["treffer"]) == 1


def test_index_bleibt_position_in_der_ungefilterten_liste(mocker, tmp_path, monkeypatch):
    """Der `index` eines Anhangs muss die Position in der UNGEFILTERTEN Liste
    bleiben, weil `mail_anhang_lesen` weiterhin die volle Liste indiziert. Wäre
    neu ab 0 durchnummeriert, würde ein per Index gewählter Anhang nach einem
    herausgefilterten Inline-Bild auf die falsche Datei zeigen."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    msg = _msg_mit_anhaengen(
        uid="9",
        attachments=[
            _att(filename="", content_disposition="inline"),  # Index 0, gefiltert
            _att(filename="Rechnung.pdf"),  # Index 1, echt
        ],
    )

    metadaten = chat_tools._attachment_metadata(msg, None)

    assert len(metadaten) == 1
    assert metadaten[0]["index"] == 1, (
        "Der Index muss die Position in der ungefilterten msg.attachments-Liste "
        "sein — mail_anhang_lesen indiziert die volle Liste."
    )


# --- Suchkriterien: was serverseitig geht und was nicht ----------------------


def test_nur_ungelesen_erzeugt_seen_false_im_kriterium(mocker):
    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.AND", side_effect=lambda **kw: kw)

    kriterium = chat_tools._build_search_criteria("", "", "", nur_ungelesen=True)

    assert kriterium.get("seen") is False


def test_min_groesse_mb_wird_zu_size_gt_in_bytes(mocker):
    """5 MB müssen als 5242880 Bytes ankommen — IMAP `LARGER` rechnet in Bytes.
    Ein Test auf „irgendein size_gt gesetzt" würde einen Faktor-1024-Fehler
    durchlassen."""
    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.AND", side_effect=lambda **kw: kw)

    kriterium = chat_tools._build_search_criteria("", "", "", min_groesse_mb=5)

    assert kriterium.get("size_gt") == 5 * 1024 * 1024


def test_kriterienbau_kennt_nur_mit_anhang_gar_nicht():
    """IMAP kennt kein „hat Anhang" — deshalb nimmt der Kriterienbau diesen
    Parameter überhaupt nicht an. Das ist die stärkste Form der Zusicherung:
    eine spätere Änderung, die daraus versehentlich ein serverseitiges Kriterium
    macht (etwa eine `Content-Type: multipart/mixed`-Heuristik, die auch Mails
    ohne echten Anhang trifft), müsste die Signatur ändern und würde hier
    auffallen — nicht erst in einem stillschweigend zu großen Suchergebnis.
    """
    import inspect

    import src.chat_tools as chat_tools

    parameter = inspect.signature(chat_tools._build_search_criteria).parameters

    assert "nur_mit_anhang" not in parameter, (
        "_build_search_criteria nimmt nur_mit_anhang an — der Anhangsfilter "
        "gehoert NICHT in das IMAP-Suchkriterium, sondern als Nachfilter auf den "
        "bereits gefetchten Batch (13-RESEARCH.md Abschnitt 4)."
    )
    # Gegenprobe, dass die serverseitigen Filter dieses Plans sehr wohl dort sind.
    assert "nur_ungelesen" in parameter
    assert "min_groesse_mb" in parameter


def test_filter_lassen_sich_mit_absender_und_zeitraum_kombinieren(mocker):
    """Alle Bedingungen landen UND-verknüpft im selben AND-Aufruf — der Betreiber
    kann „große Mails mit Anhang von Paketdienst aus 2025" in EINEM Auftrag ausdrücken."""
    import datetime

    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.AND", side_effect=lambda **kw: kw)

    kriterium = chat_tools._build_search_criteria(
        "",
        "Paketdienst",
        "",
        seit="2025-01-01",
        bis="2025-12-31",
        nur_ungelesen=True,
        min_groesse_mb=1,
    )

    assert kriterium.get("from_") == "Paketdienst"
    assert kriterium.get("seen") is False
    assert kriterium.get("size_gt") == 1024 * 1024
    assert isinstance(kriterium.get("date_gte"), datetime.date)
    assert type(kriterium.get("date_gte")) is datetime.date, (
        "date_gte muss exakt datetime.date sein — imap_tools prüft mit "
        "`type(value) is not datetime.date`, ein datetime.datetime wirft TypeError."
    )


# --- Ehrlichkeit der Gesamtzahl ----------------------------------------------


def test_gesamt_wird_bei_anhangsfilter_nicht_behauptet(mocker, tmp_path, monkeypatch):
    """Bei aktivem `nur_mit_anhang` ist die echte Trefferzahl ohne Fetch über
    ALLE Treffer nicht ermittelbar. Das Ergebnis darf deshalb keine exakte
    `gesamt`-Zahl tragen — sonst meldet der Agent „12 gefunden", wo es 300 sind.
    """
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mit_pdf = _msg_mit_anhaengen(uid="1", attachments=[_att()])
    mock_mailbox = _fake_mailbox([mit_pdf])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_suchen("info", nur_mit_anhang=True)

    assert "gesamt" not in ergebnis, (
        "Bei aktivem Anhangsfilter darf keine exakte Gesamtzahl behauptet werden — "
        f"geliefert wurde gesamt={ergebnis.get('gesamt')!r}."
    )
    assert "weitere_vorhanden" in ergebnis


def test_gesamt_wird_ohne_anhangsfilter_weiterhin_geliefert(mocker, tmp_path, monkeypatch):
    """Gegenprobe: ohne Anhangsfilter bleibt das bestehende `gesamt`-Verhalten
    (Live-Befund 2026-08-16) unverändert — der Filter darf es nicht global
    abschalten."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mock_mailbox = _fake_mailbox([_msg_mit_anhaengen(uid="1", attachments=[_att()])])
    mock_mailbox.uids = MagicMock(return_value=["1"])
    mocker.patch("src.chat_tools.MailBox", return_value=mock_mailbox)

    ergebnis = chat_tools.mails_suchen("info")

    assert "weitere_vorhanden" in ergebnis
