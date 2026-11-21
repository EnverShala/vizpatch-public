"""Tests fuer `webui/src/urlaub_relay.py` — die Gegenstelle zum Agenten.

Dieses Modul ist die Stelle, an der die Urlaubs-Autoantwort die eine
Invariante des Projekts durchbricht („kein Auto-Send"). Entsprechend liegt der
Schwerpunkt hier NICHT auf dem Gutfall, sondern auf den Riegeln: ohne gueltige
Signatur, ohne frischen Zeitstempel, ohne gesetztes Agenten-Flag und ausserhalb
des Urlaubszeitraums darf NICHTS zugestellt werden — und zwar so, dass
`mail_send.deliver` gar nicht erst aufgerufen wird.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import date, datetime, timedelta

import pytest


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


def _write_agent_env(agent_id="info", **extra):
    import src.agents_io as agents_io

    werte = {
        "IMAP_USER": "info@ionos.de",
        "IMAP_PASSWORD": "imap-pw",
        "LLM_API_KEY": "sk-ant-test-key",
        "ENABLE_URLAUBSANTWORT": "true",
        "URLAUB_VON": (date.today() - timedelta(days=1)).isoformat(),
        "URLAUB_BIS": (date.today() + timedelta(days=7)).isoformat(),
    }
    werte.update(extra)
    agents_io.write_env(agent_id, werte)


def _nutzlast(agent_id="info", empfaenger=None, roh=b"Subject: Re: Test\r\n\r\nWeg.\r\n"):
    return json.dumps(
        {
            "agent_id": agent_id,
            "absender": "info@ionos.de",
            "empfaenger": empfaenger if empfaenger is not None else ["kunde@web.de"],
            "rfc822_base64": base64.b64encode(roh).decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signiert(body, stempel=None):
    import src.urlaub_relay as urlaub_relay

    stempel = stempel or str(int(time.time()))
    return stempel, urlaub_relay.signatur(body, stempel)


# --- Riegel ---------------------------------------------------------------


def test_ohne_signatur_wird_nicht_gesendet(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast()
    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, str(int(time.time())), "")
    assert e.value.status == 401
    assert deliver.call_count == 0


def test_falsche_signatur_wird_nicht_gesendet(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast()
    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, str(int(time.time())), "sha256=deadbeef")
    assert e.value.status == 401
    assert deliver.call_count == 0


def test_veraenderter_body_bricht_die_signatur(tmp_path, monkeypatch, mocker):
    """Der eigentliche Zweck der Signatur: ein Mitleser darf den Empfaenger
    nicht austauschen koennen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    original = _nutzlast(empfaenger=["kunde@web.de"])
    stempel, sig = _signiert(original)
    manipuliert = _nutzlast(empfaenger=["angreifer@boese.de"])

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", manipuliert, stempel, sig)
    assert e.value.status == 401
    assert deliver.call_count == 0


def test_alter_zeitstempel_wird_abgelehnt(tmp_path, monkeypatch, mocker):
    """Replay-Schutz: eine einmal beobachtete Anfrage darf nicht dauerhaft
    gueltig bleiben."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast()
    alt = str(int(time.time()) - urlaub_relay.MAX_CLOCK_SKEW_SECONDS - 60)
    stempel, sig = _signiert(body, alt)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, stempel, sig)
    assert e.value.status == 401
    assert deliver.call_count == 0


def test_flag_aus_lehnt_ab(tmp_path, monkeypatch, mocker):
    """Fail-closed am Agenten-Flag: der Schalter wirkt auch dann, wenn ein
    (kuenftig fehlerhafter) Agent trotzdem anfragt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_URLAUBSANTWORT="false")
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast()
    stempel, sig = _signiert(body)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, stempel, sig)
    assert e.value.status == 403
    assert deliver.call_count == 0


def test_fehlendes_flag_lehnt_ab(tmp_path, monkeypatch, mocker):
    """Kein Eintrag = aus. Ein Bestandsagent ohne die neue Variable darf nicht
    versehentlich senden."""
    _setup_env(tmp_path, monkeypatch)
    import src.agents_io as agents_io

    agents_io.write_env(
        "info", {"IMAP_USER": "info@ionos.de", "IMAP_PASSWORD": "p", "LLM_API_KEY": "k"}
    )
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast()
    stempel, sig = _signiert(body)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, stempel, sig)
    assert e.value.status == 403
    assert deliver.call_count == 0


def test_ausserhalb_des_zeitraums_lehnt_ab(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(
        URLAUB_VON=(date.today() - timedelta(days=30)).isoformat(),
        URLAUB_BIS=(date.today() - timedelta(days=20)).isoformat(),
    )
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast()
    stempel, sig = _signiert(body)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, stempel, sig)
    assert e.value.status == 403
    assert deliver.call_count == 0


def test_agent_kennung_muss_zum_pfad_passen(tmp_path, monkeypatch, mocker):
    """Sonst koennte eine fuer Agent A gueltige Signatur ueber das Postfach von
    Agent B zugestellt werden."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    _write_agent_env("zweiter")
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast(agent_id="info")
    stempel, sig = _signiert(body)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("zweiter", body, stempel, sig)
    assert e.value.status == 400
    assert deliver.call_count == 0


def test_mehrere_empfaenger_werden_abgelehnt(tmp_path, monkeypatch, mocker):
    """Eine Urlaubsantwort geht an genau einen Menschen — alles andere waere
    ein Massenversand-Werkzeug."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast(empfaenger=["a@web.de", "b@web.de"])
    stempel, sig = _signiert(body)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, stempel, sig)
    assert e.value.status == 400
    assert deliver.call_count == 0


def test_unbekannter_agent_wird_abgelehnt(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = _nutzlast(agent_id="gibtsnicht")
    stempel, sig = _signiert(body)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("gibtsnicht", body, stempel, sig)
    assert e.value.status == 404
    assert deliver.call_count == 0


def test_zu_grosser_body_wird_abgelehnt(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende(
            "info", b"x" * (urlaub_relay.MAX_BODY_BYTES + 1), str(int(time.time())), "sig"
        )
    assert e.value.status == 413
    assert deliver.call_count == 0


def test_kaputtes_json_wird_abgelehnt(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    body = b"{kein json"
    stempel, sig = _signiert(body)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, stempel, sig)
    assert e.value.status == 400
    assert deliver.call_count == 0


# --- Gutfall --------------------------------------------------------------


def test_gueltige_anfrage_wird_zugestellt(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    roh = b"Subject: Re: Test\r\nAuto-Submitted: auto-replied\r\n\r\nWeg.\r\n"
    body = _nutzlast(roh=roh)
    stempel, sig = _signiert(body)

    ergebnis = urlaub_relay.pruefe_und_sende("info", body, stempel, sig)

    assert ergebnis["status"] == "gesendet"
    assert deliver.call_count == 1
    kwargs = deliver.call_args.kwargs
    assert kwargs["recipients"] == ["kunde@web.de"]
    assert kwargs["raw_bytes"] == roh
    assert kwargs["envelope_from"] == "info@ionos.de"


def test_mail_bytes_werden_nicht_veraendert(tmp_path, monkeypatch, mocker):
    """Der Agent baut die Mail, die WebUI stellt sie nur zu — Kopfzeilen wie
    `Auto-Submitted` duerfen unterwegs nicht verlorengehen, sonst faellt der
    Schleifenschutz."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    deliver = mocker.patch.object(mail_send, "deliver")
    roh = b"Auto-Submitted: auto-replied\r\nPrecedence: bulk\r\n\r\nText\r\n"
    body = _nutzlast(roh=roh)
    stempel, sig = _signiert(body)
    urlaub_relay.pruefe_und_sende("info", body, stempel, sig)

    assert deliver.call_args.kwargs["raw_bytes"] == roh


def test_smtp_fehler_wird_als_502_gemeldet(tmp_path, monkeypatch, mocker):
    """502 statt 400: der Agent soll es im naechsten Zyklus erneut versuchen
    duerfen (er merkt den Absender bei Fehlschlag nicht vor)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.mail_send as mail_send
    import src.urlaub_relay as urlaub_relay

    mocker.patch.object(
        mail_send, "deliver", side_effect=mail_send.SmtpSendError("Verbindung fehlgeschlagen")
    )
    body = _nutzlast()
    stempel, sig = _signiert(body)

    with pytest.raises(urlaub_relay.RelayAbgelehnt) as e:
        urlaub_relay.pruefe_und_sende("info", body, stempel, sig)
    assert e.value.status == 502


# --- Zeitraum-Zweitpruefung ----------------------------------------------


@pytest.mark.parametrize(
    "von,bis,heute,erwartet",
    [
        ("2026-09-01", "2026-09-14", date(2026, 9, 1), True),
        ("2026-09-01", "2026-09-14", date(2026, 9, 14), True),
        ("2026-09-01", "2026-09-14", date(2026, 8, 31), False),
        ("2026-09-01", "2026-09-14", date(2026, 9, 15), False),
        ("2026-09-14", "2026-09-01", date(2026, 9, 7), False),
        ("", "", date(2026, 9, 7), False),
        ("01.09.2026", "14.09.2026", date(2026, 9, 7), False),
    ],
)
def test_zeitraum_zweitpruefung(von, bis, heute, erwartet, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.urlaub_relay as urlaub_relay

    assert urlaub_relay._zeitraum_aktiv(von, bis, heute) is erwartet
