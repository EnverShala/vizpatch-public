"""Tests für das AUSLÖSEN einer Newsletter-Abmeldung (Phase 13, Plan 13-09,
MBOX-15 Teil 2).

Das ist das riskanteste Einzelstück der ganzen Phase. Die Zieladresse steht in
einer Mail, die ein Angreifer per Zusendung vollständig kontrolliert: wer eine
Mail ins Postfach bekommt, bestimmt damit, wohin dieser ausgehende Request
ginge. Deshalb prüfen die Tests hier nicht den Glücksfall, sondern **jede
einzelne Schutzschicht** getrennt:

* eigenes Abschalt-Flag (`ENABLE_UNSUBSCRIBE`),
* Zwei-Request-Freigabe-Gate wie beim Mailversand,
* Enge des Bestätigungs-Schemas (nur der Token, kein Inhalt),
* https-Zwang,
* SSRF-Sperre gegen private und lokale Ziele, inklusive erneuter Prüfung bei
  der Bestätigung (DNS-Rebinding),
* keine Weiterleitungen, hartes Zeitlimit, kein Cookie-Speicher,
* One-Click-Pflicht: ohne den Begleitheader nach RFC 8058 wird nicht
  automatisiert.

Die Gegenprobe zum Lesen-Teil steht in `test_chat_abmelden.py`: das reine
NENNEN des Abmeldewegs hängt an keinem Flag und keinem Gate, weil daran nichts
riskant ist.

OFFEN: die Header-Struktur ist in der Recherche als unverifiziert markiert und
hier gegen konstruierte Varianten getestet — die Bestätigung an einer echten
Newsletter-Mail steht noch aus (siehe Prüfliste im SUMMARY).
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import httpx
import pytest

from .test_chat_abmelden import (
    _fake_mailbox,
    _mock_public_dns,
    _msg,
    _setup_env,
    _write_agent_env,
)

_ONE_CLICK_HEADERS = {
    "list-unsubscribe": "<mailto:stop@versender.example>, <https://versender.example/u/abc>",
    "list-unsubscribe-post": "List-Unsubscribe=One-Click",
}


def _newsletter(headers=None):
    return _msg(headers=dict(_ONE_CLICK_HEADERS) if headers is None else headers)


def _vorschau(mocker, tmp_path, monkeypatch, headers=None, request_id="req-1"):
    """Erzeugt eine Abmelde-Vorschau, liefert (chat_tools, ergebnis)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    _mock_public_dns(mocker)
    mocker.patch("src.chat_tools.MailBox", return_value=_fake_mailbox([_newsletter(headers)]))
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mail_abmelden(
        "info", uid="42", session_id="sess-1", request_id=request_id
    )
    return chat_tools, ergebnis


def _http_client(mocker, status_code=200, side_effect=None):
    """Attrappe für `httpx.Client`; liefert (klasse, instanz)."""
    instanz = MagicMock()
    instanz.__enter__ = MagicMock(return_value=instanz)
    instanz.__exit__ = MagicMock(return_value=False)
    if side_effect is not None:
        instanz.post.side_effect = side_effect
    else:
        instanz.post.return_value = types.SimpleNamespace(status_code=status_code, text="ok")
    klasse = mocker.patch("src.chat_tools.httpx.Client", return_value=instanz)
    return klasse, instanz


# --- Flag ---------------------------------------------------------------------


def test_ausloesen_haengt_am_eigenen_flag(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_UNSUBSCRIBE", "false")
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mail_abmelden(
        "info", uid="42", session_id="sess-1", request_id="req-1"
    )

    assert "fehler" in ergebnis


def test_flag_aus_entfernt_nur_die_ausloese_werkzeuge_aus_dem_schema(tmp_path, monkeypatch):
    """Bei abgeschaltetem Flag verschwinden die Auslöse-Werkzeuge, das
    Lese-Werkzeug bleibt — sonst könnte sich der Betreiber den Abmeldeweg nicht
    mehr nennen lassen, obwohl daran nichts riskant ist."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_UNSUBSCRIBE", "false")
    import src.chat_tools as chat_tools

    namen = {s["name"] for s in chat_tools._tool_schemas_for("info")}

    assert "mail_abmelden" not in namen
    assert "mail_abmelden_bestaetigen" not in namen
    assert "mail_abmeldeweg_lesen" in namen


# --- Zwei-Request-Gate ---------------------------------------------------------


def test_vorschau_loest_nichts_aus(mocker, tmp_path, monkeypatch):
    klasse, _ = _http_client(mocker)
    _, ergebnis = _vorschau(mocker, tmp_path, monkeypatch)

    assert "fehler" not in ergebnis
    assert ergebnis.get("vorschau_token")
    klasse.assert_not_called()


def test_bestaetigen_im_selben_request_wird_abgelehnt(mocker, tmp_path, monkeypatch):
    """Der Kern des Gates: eine präparierte Mail darf Vorschau und Freigabe
    nicht im selben Betreiber-Turn verketten."""
    klasse, _ = _http_client(mocker)
    chat_tools, vorschau = _vorschau(mocker, tmp_path, monkeypatch, request_id="req-gleich")

    ergebnis = chat_tools.mail_abmelden_bestaetigen(
        "info",
        vorschau_token=vorschau["vorschau_token"],
        session_id="sess-1",
        request_id="req-gleich",
    )

    assert "fehler" in ergebnis
    klasse.assert_not_called()


def test_bestaetigen_ohne_request_id_wird_abgelehnt_fail_closed(mocker, tmp_path, monkeypatch):
    """Ein Gate, das bei fehlender Request-Kennung durchlässt, ist kein Gate."""
    klasse, _ = _http_client(mocker)
    chat_tools, vorschau = _vorschau(mocker, tmp_path, monkeypatch, request_id="")

    ergebnis = chat_tools.mail_abmelden_bestaetigen(
        "info", vorschau_token=vorschau["vorschau_token"], session_id="sess-1", request_id=""
    )

    assert "fehler" in ergebnis
    klasse.assert_not_called()


def test_falscher_token_wird_abgelehnt(mocker, tmp_path, monkeypatch):
    klasse, _ = _http_client(mocker)
    chat_tools, _ = _vorschau(mocker, tmp_path, monkeypatch)

    ergebnis = chat_tools.mail_abmelden_bestaetigen(
        "info", vorschau_token="ausgedacht", session_id="sess-1", request_id="req-2"
    )

    assert "fehler" in ergebnis
    klasse.assert_not_called()


def test_bestaetigen_ohne_vorschau_wird_abgelehnt(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    klasse, _ = _http_client(mocker)
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.mail_abmelden_bestaetigen(
        "info", vorschau_token="irgendwas", session_id="sess-1", request_id="req-2"
    )

    assert "fehler" in ergebnis
    klasse.assert_not_called()


def test_bestaetigungs_schema_nimmt_nur_den_token():
    """Kein Inhalts-Parameter — die Bestätigung darf das Ziel nicht mehr ändern
    können, sonst wäre die gezeigte Vorschau bedeutungslos."""
    import src.chat_tools as chat_tools

    schema = next(
        s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "mail_abmelden_bestaetigen"
    )

    assert set(schema["input_schema"]["properties"]) == {"vorschau_token"}


def test_zweiter_bestaetigungsversuch_loest_nicht_erneut_aus(mocker, tmp_path, monkeypatch):
    """Ein verbrauchter Token darf nicht wiederverwendbar sein."""
    chat_tools, vorschau = _vorschau(mocker, tmp_path, monkeypatch, request_id="req-1")
    _, instanz = _http_client(mocker)

    erst = chat_tools.mail_abmelden_bestaetigen(
        "info", vorschau_token=vorschau["vorschau_token"], session_id="sess-1", request_id="req-2"
    )
    zweit = chat_tools.mail_abmelden_bestaetigen(
        "info", vorschau_token=vorschau["vorschau_token"], session_id="sess-1", request_id="req-3"
    )

    assert "fehler" not in erst
    assert "fehler" in zweit
    assert instanz.post.call_count == 1


# --- https-Zwang ---------------------------------------------------------------


def test_http_url_wird_abgelehnt():
    import src.chat_tools as chat_tools

    ok, grund = chat_tools._validate_unsubscribe_https_url("http://versender.example/u/abc")

    assert ok is False
    assert "https" in grund.lower()


@pytest.mark.parametrize("url", ["ftp://host/u", "file:///etc/passwd", "gopher://host/1"])
def test_fremdes_schema_wird_abgelehnt(url):
    import src.chat_tools as chat_tools

    ok, _ = chat_tools._validate_unsubscribe_https_url(url)

    assert ok is False


def test_url_ohne_host_wird_abgelehnt():
    import src.chat_tools as chat_tools

    ok, grund = chat_tools._validate_unsubscribe_https_url("https:///pfad")

    assert ok is False
    assert "Host" in grund


# --- SSRF ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "ziel",
    [
        "https://127.0.0.1/u",
        "https://10.1.2.3/u",
        "https://192.168.1.1/u",
        "https://172.16.5.4/u",
        "https://169.254.169.254/u",
        "https://[::1]/u",
        "https://0.0.0.0/u",
    ],
)
def test_private_und_lokale_ip_ziele_werden_abgelehnt(ziel):
    """`169.254.169.254` ist die Metadaten-Adresse vieler Cloud-Anbieter und
    der klassische SSRF-Angriffspfad."""
    import src.chat_tools as chat_tools

    ok, grund = chat_tools._validate_unsubscribe_https_url(ziel)

    assert ok is False, f"{ziel} haette abgelehnt werden muessen"
    assert "SSRF" in grund


def test_hostname_der_auf_localhost_zeigt_wird_abgelehnt(mocker):
    import src.chat_tools as chat_tools

    mocker.patch(
        "src.chat_tools.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
    )

    ok, grund = chat_tools._validate_unsubscribe_https_url("https://boese.example/u")

    assert ok is False
    assert "SSRF" in grund


def test_hostname_mit_privater_zweitadresse_wird_abgelehnt(mocker):
    """Geprüft wird JEDE aufgelöste Adresse, nicht nur die erste — sonst könnte
    ein DNS-Eintrag eine öffentliche Adresse vorschieben und mit einer zweiten,
    privaten ins interne Netz zeigen."""
    import src.chat_tools as chat_tools

    mocker.patch(
        "src.chat_tools.socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.5", 443)),
        ],
    )

    ok, grund = chat_tools._validate_unsubscribe_https_url("https://boese.example/u")

    assert ok is False
    assert "10.0.0.5" in grund


def test_nicht_aufloesbarer_hostname_gilt_als_nicht_erlaubt(mocker):
    """Fail-closed: was nicht aufgelöst werden kann, wird nicht angefragt."""
    import socket as _socket

    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.socket.getaddrinfo", side_effect=_socket.gaierror("nope"))

    ok, grund = chat_tools._validate_unsubscribe_https_url("https://gibtsnicht.example/u")

    assert ok is False
    assert "aufgeloest" in grund


def test_ssrf_pruefung_laeuft_erneut_bei_der_bestaetigung(mocker, tmp_path, monkeypatch):
    """Zwischen Vorschau und Bestätigung könnte sich der DNS-Eintrag ändern
    (DNS-Rebinding). Deshalb wird bei der Bestätigung erneut geprüft."""
    chat_tools, vorschau = _vorschau(mocker, tmp_path, monkeypatch, request_id="req-1")
    klasse, _ = _http_client(mocker)

    mocker.patch(
        "src.chat_tools.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("192.168.0.99", 443))],
    )

    ergebnis = chat_tools.mail_abmelden_bestaetigen(
        "info",
        vorschau_token=vorschau["vorschau_token"],
        session_id="sess-1",
        request_id="req-2",
    )

    assert "fehler" in ergebnis
    klasse.assert_not_called()


# --- Der HTTP-Aufruf selbst ----------------------------------------------------


def test_post_ohne_redirects_mit_zeitlimit_und_one_click_body(mocker, tmp_path, monkeypatch):
    """Kein Redirect-Following — ein Redirect würde die SSRF-Prüfung umgehen.
    Dazu hartes Zeitlimit, frischer Client (kein Cookie-Speicher) und der von
    RFC 8058 vorgeschriebene Body."""
    chat_tools, vorschau = _vorschau(mocker, tmp_path, monkeypatch, request_id="req-1")
    klasse, instanz = _http_client(mocker)

    ergebnis = chat_tools.mail_abmelden_bestaetigen(
        "info",
        vorschau_token=vorschau["vorschau_token"],
        session_id="sess-1",
        request_id="req-2",
    )

    assert "fehler" not in ergebnis
    kwargs = klasse.call_args.kwargs
    assert kwargs["follow_redirects"] is False
    assert kwargs["timeout"] == chat_tools._UNSUBSCRIBE_HTTP_TIMEOUT_SECONDS
    assert instanz.post.call_args.kwargs["content"] == b"List-Unsubscribe=One-Click"


def test_netzwerkfehler_liefert_verstaendliche_meldung(mocker, tmp_path, monkeypatch):
    chat_tools, vorschau = _vorschau(mocker, tmp_path, monkeypatch, request_id="req-1")
    _http_client(mocker, side_effect=httpx.ConnectError("weg"))

    ergebnis = chat_tools.mail_abmelden_bestaetigen(
        "info",
        vorschau_token=vorschau["vorschau_token"],
        session_id="sess-1",
        request_id="req-2",
    )

    assert "fehler" in ergebnis


# --- One-Click-Pflicht ---------------------------------------------------------


def test_https_ohne_one_click_header_wird_nicht_automatisiert(mocker, tmp_path, monkeypatch):
    """Ohne den One-Click-Begleitheader gibt es keine Zusicherung, dass hinter
    der URL keine Bestätigungsseite oder ein Login steht — dann wird nicht
    automatisch abgemeldet, sondern höchstens der Weg genannt."""
    klasse, _ = _http_client(mocker)
    _, ergebnis = _vorschau(
        mocker,
        tmp_path,
        monkeypatch,
        headers={"list-unsubscribe": "<https://versender.example/u/abc>"},
    )

    if "fehler" not in ergebnis:
        assert ergebnis.get("variante") != "https"
    klasse.assert_not_called()


def test_mail_ohne_abmelde_header_liefert_klare_meldung(mocker, tmp_path, monkeypatch):
    klasse, _ = _http_client(mocker)
    _, ergebnis = _vorschau(mocker, tmp_path, monkeypatch, headers={})

    assert "fehler" in ergebnis
    klasse.assert_not_called()


# --- Registrierung -------------------------------------------------------------


def test_ausloese_werkzeuge_vollstaendig_registriert():
    import src.chat_tools as chat_tools

    namen = {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    for werkzeug in ("mail_abmelden", "mail_abmelden_bestaetigen"):
        assert werkzeug in namen
        assert werkzeug in chat_tools.TOOL_HANDLERS
        assert werkzeug in chat_tools._TOOL_ACTIVITY_LABELS


def test_ausloese_werkzeuge_sind_session_und_request_scoped():
    """Ohne Request-Bindung gibt es kein Cross-Request-Gate."""
    import src.chat_tools as chat_tools

    for werkzeug in ("mail_abmelden", "mail_abmelden_bestaetigen"):
        assert werkzeug in chat_tools._SESSION_SCOPED_TOOLS
        assert werkzeug in chat_tools._REQUEST_SCOPED_TOOLS


def test_schemas_geben_session_und_request_id_nicht_nach_aussen():
    """`session_id`/`request_id` werden serverseitig injiziert — stünden sie im
    Schema, könnte das Modell sie setzen und das Gate umgehen."""
    import src.chat_tools as chat_tools

    for werkzeug in ("mail_abmelden", "mail_abmelden_bestaetigen"):
        schema = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == werkzeug)
        eigenschaften = set(schema["input_schema"]["properties"])
        assert "session_id" not in eigenschaften
        assert "request_id" not in eigenschaften
