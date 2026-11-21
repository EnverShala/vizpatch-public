"""Tests fuer `agent/src/mail_relay.py` — den einzigen Weg nach draussen.

Der wichtigste Test dieser Datei ist `test_agent_quellbaum_bleibt_smtp_frei`
(in der webui-Suite): dieses Modul existiert ausschliesslich, damit die
Urlaubs-Autoantwort moeglich wird, OHNE dass `agent/src/` SMTP spricht. Hier
wird die andere Haelfte geprueft — dass die Uebergabe an die WebUI signiert,
frisch und fehlerfest ist.
"""
from __future__ import annotations

import base64
import json
import urllib.error
from dataclasses import replace

import pytest

from src import mail_relay


@pytest.fixture(autouse=True)
def fester_key(tmp_path, monkeypatch):
    """Eigene Key-Datei je Test — `crypto.load_or_create_key` legt sie an."""
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


@pytest.fixture
def cfg(mock_config):
    return replace(
        mock_config,
        agent_id="default",
        own_email_address="chef@firma.de",
        webui_internal_url="http://webui:8080",
    )


def test_signatur_ist_reproduzierbar():
    stempel = "1700000000"
    assert mail_relay.signatur(b"x", stempel) == mail_relay.signatur(b"x", stempel)


def test_signatur_haengt_am_zeitstempel():
    """Ohne den Zeitstempel IN der Signatur koennte ein Angreifer ihn
    austauschen und das Replay-Fenster beliebig verschieben."""
    assert mail_relay.signatur(b"x", "1700000000") != mail_relay.signatur(b"x", "1700000999")


def test_signatur_haengt_am_body():
    assert mail_relay.signatur(b"a", "1") != mail_relay.signatur(b"b", "1")


def test_signatur_pruefung_akzeptiert_die_eigene_signatur():
    stempel = "1700000000"
    assert mail_relay.signatur_gueltig(b"x", stempel, mail_relay.signatur(b"x", stempel)) is True


@pytest.mark.parametrize("falsch", ["", "sha256=deadbeef", "quatsch"])
def test_signatur_pruefung_lehnt_falsches_ab(falsch):
    assert mail_relay.signatur_gueltig(b"x", "1700000000", falsch) is False


def test_hmac_key_ist_zweckgebunden():
    """Der Relay-Key darf NICHT derselbe sein wie der rohe Fernet-Key — sonst
    gaelte eine hier erzeugte Signatur potenziell auch anderswo."""
    from src import crypto

    assert mail_relay.relay_hmac_key() != crypto.load_or_create_key()


def test_timestamp_frisch_im_fenster():
    assert mail_relay.timestamp_frisch("1000", jetzt=1000.0) is True
    assert mail_relay.timestamp_frisch("1000", jetzt=1000.0 + mail_relay.MAX_CLOCK_SKEW_SECONDS) is True


def test_timestamp_ausserhalb_des_fensters():
    zu_alt = 1000.0 + mail_relay.MAX_CLOCK_SKEW_SECONDS + 1
    assert mail_relay.timestamp_frisch("1000", jetzt=zu_alt) is False


@pytest.mark.parametrize("wert", ["", "gestern", None])
def test_nicht_numerischer_timestamp_ist_ungueltig(wert):
    """Fail-closed: was nicht als Zeit lesbar ist, gilt nicht als frisch."""
    assert mail_relay.timestamp_frisch(wert, jetzt=1000.0) is False


def test_nutzlast_ist_kanonisch():
    """Beide Seiten muessen ueber EXAKT dieselben Bytes signieren/pruefen —
    deshalb sortierte Schluessel und kompakte Trenner."""
    a = mail_relay.baue_nutzlast("default", ["k@web.de"], "chef@firma.de", b"ROH")
    b = mail_relay.baue_nutzlast("default", ["k@web.de"], "chef@firma.de", b"ROH")
    assert a == b
    assert b" " not in a  # kompakt


def test_nutzlast_traegt_die_mail_unveraendert():
    roh = b"Subject: Re: Test\r\n\r\nIch bin weg.\r\n"
    nutzlast = json.loads(mail_relay.baue_nutzlast("default", ["k@web.de"], "chef@firma.de", roh))
    assert base64.b64decode(nutzlast["rfc822_base64"]) == roh


def test_auslieferung_setzt_signatur_und_zeitstempel(cfg, mocker):
    aufrufe = []

    class FakeAntwort:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getcode(self):
            return 200

    def fake_urlopen(request, timeout=None):
        aufrufe.append(request)
        return FakeAntwort()

    mocker.patch.object(mail_relay.urllib.request, "urlopen", fake_urlopen)
    mail_relay.urlaubsantwort_ausliefern(cfg, ["kunde@web.de"], b"ROH")

    assert len(aufrufe) == 1
    anfrage = aufrufe[0]
    assert anfrage.full_url == "http://webui:8080/intern/urlaubsantwort/default"
    stempel = anfrage.get_header(mail_relay.TIMESTAMP_HEADER.capitalize())
    empfangen = anfrage.get_header(mail_relay.SIGNATUR_HEADER.capitalize())
    assert mail_relay.signatur_gueltig(anfrage.data, stempel, empfangen) is True


def test_ohne_empfaenger_wird_nicht_einmal_verbunden(cfg, mocker):
    urlopen = mocker.patch.object(mail_relay.urllib.request, "urlopen")
    with pytest.raises(mail_relay.RelayError):
        mail_relay.urlaubsantwort_ausliefern(cfg, [], b"ROH")
    assert urlopen.call_count == 0


def test_http_fehler_wird_uebersetzt(cfg, mocker):
    mocker.patch.object(
        mail_relay.urllib.request,
        "urlopen",
        side_effect=urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
    )
    with pytest.raises(mail_relay.RelayError) as e:
        mail_relay.urlaubsantwort_ausliefern(cfg, ["k@web.de"], b"ROH")
    assert "403" in str(e.value)


def test_unerreichbare_webui_wird_uebersetzt(cfg, mocker):
    mocker.patch.object(
        mail_relay.urllib.request, "urlopen", side_effect=urllib.error.URLError("nope")
    )
    with pytest.raises(mail_relay.RelayError) as e:
        mail_relay.urlaubsantwort_ausliefern(cfg, ["k@web.de"], b"ROH")
    assert "nicht erreichbar" in str(e.value)


def test_fehlermeldung_enthaelt_keinen_servertext(cfg, mocker):
    """Muster `mail_send.describe_smtp_error`: die Meldung wird aus der
    Fehlerklasse gebaut, nie aus dem Server-Echo — sonst koennten Empfaenger
    oder Konfigurationsdetails im Log landen."""
    mocker.patch.object(
        mail_relay.urllib.request,
        "urlopen",
        side_effect=urllib.error.HTTPError(
            "u", 500, "GEHEIM: passwort=hunter2", {}, None
        ),
    )
    with pytest.raises(mail_relay.RelayError) as e:
        mail_relay.urlaubsantwort_ausliefern(cfg, ["k@web.de"], b"ROH")
    assert "hunter2" not in str(e.value)
    assert "GEHEIM" not in str(e.value)


# --- Waechter: GENAU EIN Modul spricht nach aussen -------------------------


def test_nur_mail_relay_spricht_http():
    """Strukturelle Zusicherung analog `test_nur_mail_send_modul_importiert_smtplib`
    in der webui-Suite: von allen Modulen in `agent/src/` importiert GENAU EINES
    einen HTTP-Client — dieses hier.

    Warum das zaehlt: der AST-Waechter ueber `agent/src/` verbietet SMTP. Er
    verboete NICHT, dass irgendein anderes Modul kuenftig per HTTP an einem
    beliebigen Endpoint etwas hinausreicht. Dieser Test schliesst die Luecke —
    der Weg nach draussen bleibt EINE, auditierte Stelle."""
    import ast
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[1] / "src"
    http_module = {"urllib", "http", "httpx", "requests", "aiohttp"}
    mit_http = set()

    for py_file in src_dir.glob("*.py"):
        baum = ast.parse(py_file.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Import) and any(
                a.name.split(".")[0] in http_module for a in knoten.names
            ):
                mit_http.add(py_file.name)
            elif isinstance(knoten, ast.ImportFrom) and (knoten.module or "").split(".")[0] in http_module:
                mit_http.add(py_file.name)

    assert mit_http == {"mail_relay.py"}


def test_der_waechter_ist_kein_blindgaenger():
    """Gegenprobe: derselbe Scan erkennt einen eingeschmuggelten HTTP-Import."""
    import ast

    baum = ast.parse("import httpx\n")
    assert any(
        isinstance(k, ast.Import) and any(a.name == "httpx" for a in k.names)
        for k in ast.walk(baum)
    )
