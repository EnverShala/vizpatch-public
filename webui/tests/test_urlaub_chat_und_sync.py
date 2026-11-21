"""Urlaubs-Autoantwort: Chat-Einstellungen, Relay-Route und die Wächter.

Drei Blöcke:

1. **Chat-Allowlist.** Der Betreiber soll „ich bin vom 1. bis 14. September im
   Urlaub" sagen können. Änderbar sind deshalb Zeitraum und Probelauf — die
   FREIGABE (`ENABLE_URLAUBSANTWORT`) ausdrücklich NICHT, sie bleibt wie die
   übrigen Gates für Ausgehendes dem Einstellungen-Fenster vorbehalten.
2. **Route.** `/intern/urlaubsantwort/{agent_id}` übersetzt die Ablehnungen aus
   `urlaub_relay` in HTTP-Antworten, ohne dass eine Browser-Sitzung nötig wäre
   (der Aufrufer ist der Agent-Container).
3. **Sync-Wächter.** Zwei getrennte Images, kein gemeinsames Paket — die
   Konstanten, an denen beide Seiten hängen (HMAC-Zweck, Header-Namen,
   Zeitfenster, Urlaubs-Überschriften), werden hier zusammengehalten. Läuft
   eine davon auseinander, passt keine Signatur mehr, und zwar STILL.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
    }
    werte.update(extra)
    agents_io.write_env(agent_id, werte)


# ==========================================================================
# 1. Chat-Allowlist
# ==========================================================================


def test_freigabe_der_urlaubsantwort_ist_nicht_per_chat_aenderbar(tmp_path, monkeypatch):
    """Der Kern der Abgrenzung: der Chat darf sagen WANN und OB ZUR PROBE —
    aber nicht, OB überhaupt gesendet werden darf. Fällt dieser Test, könnte
    eine präparierte Mail im Postfach den Versand freischalten."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    vorher = dict(agents_io.read_env_raw("info"))
    for name in ("ENABLE_URLAUBSANTWORT", "enable_urlaubsantwort", "urlaubsantwort",
                 "urlaubsantwort_freigegeben"):
        assert "fehler" in chat_tools.einstellung_aendern("info", name, "true")
    assert agents_io.read_env_raw("info") == vorher


def test_urlaubszeitraum_ist_per_chat_setzbar(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    assert "fehler" not in chat_tools.einstellung_aendern("info", "urlaub_von", "2026-09-01")
    assert "fehler" not in chat_tools.einstellung_aendern("info", "urlaub_bis", "2026-09-14")

    env = agents_io.read_env_raw("info")
    assert env["URLAUB_VON"] == "2026-09-01"
    assert env["URLAUB_BIS"] == "2026-09-14"


@pytest.mark.parametrize(
    "eingabe", ["01.09.2026", "09/01/2026", "1. September", "morgen", "2026-13-01", "2026-02-31"]
)
def test_nur_iso_datum_wird_angenommen(eingabe, tmp_path, monkeypatch):
    """Kein Raten: ein um Monate verschobener Urlaub verschickt entweder keine
    oder ganz falsche Autoantworten. Das Modell rechnet selbst um — die
    Schema-Beschreibung sagt ihm das."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.einstellung_aendern("info", "urlaub_von", eingabe)
    assert "fehler" in ergebnis
    assert "JJJJ-MM-TT" in ergebnis["fehler"]
    assert "URLAUB_VON" not in agents_io.read_env_raw("info")


def test_urlaub_laesst_sich_austragen(tmp_path, monkeypatch):
    """Leerer Wert ist gültig — sonst bliebe ein vergangener Urlaub für immer
    eingetragen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(URLAUB_VON="2026-09-01", URLAUB_BIS="2026-09-14")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    assert "fehler" not in chat_tools.einstellung_aendern("info", "urlaub_von", "")
    assert agents_io.read_env_raw("info")["URLAUB_VON"] == ""


def test_bis_vor_von_wird_abgelehnt(tmp_path, monkeypatch):
    """Sonst meldete der Agent „eingetragen" für einen Zeitraum, der nie
    greift."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(URLAUB_VON="2026-09-01")
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    ergebnis = chat_tools.einstellung_aendern("info", "urlaub_bis", "2026-08-01")
    assert "fehler" in ergebnis
    assert agents_io.read_env_raw("info").get("URLAUB_BIS", "") == ""


def test_urlaub_probelauf_ist_per_chat_schaltbar(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    assert "fehler" not in chat_tools.einstellung_aendern("info", "urlaub_probelauf", "aus")
    assert agents_io.read_env_raw("info")["URLAUB_DRY_RUN"] == "false"


def test_einstellungen_lesen_zeigt_urlaub(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(
        ENABLE_URLAUBSANTWORT="true", URLAUB_VON="2026-09-01", URLAUB_BIS="2026-09-14"
    )
    import src.chat_tools as chat_tools

    e = chat_tools.einstellungen_lesen("info")["einstellungen"]
    assert e["urlaub_von"] == "2026-09-01"
    assert e["urlaub_bis"] == "2026-09-14"
    assert e["urlaubsantwort_freigegeben"] is True


def test_hinweis_wenn_zeitraum_ohne_freigabe(tmp_path, monkeypatch):
    """Sonst trägt der Betreiber per Chat einen Urlaub ein, der Agent meldet
    Vollzug — und es passiert nie etwas."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(URLAUB_VON="2026-09-01", URLAUB_BIS="2026-09-14")
    import src.chat_tools as chat_tools

    text = chat_tools.einstellungen_lesen("info").get("achtung_urlaub", "")
    assert "nicht freigegeben" in text.lower() or "NICHT" in text


def test_hinweis_wenn_vorlage_fehlt(tmp_path, monkeypatch):
    """Zeitraum relativ zu heute: mit festen Daten wäre dieser Test ab dem
    15.09.2026 umgeschlagen (der Zeitraum wäre „vorbei", und dann ersetzt der
    Vorbei-Hinweis alle anderen — zu Recht, aber der Test hätte es als
    Regression gemeldet)."""
    from datetime import date, timedelta

    _setup_env(tmp_path, monkeypatch)
    heute = date.today()
    _write_agent_env(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="false",
        URLAUB_VON=(heute - timedelta(days=1)).isoformat(),
        URLAUB_BIS=(heute + timedelta(days=7)).isoformat(),
    )
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic("info", "## Terminanfrage\nHallo\n")
    text = chat_tools.einstellungen_lesen("info").get("achtung_urlaub", "")
    assert "## Urlaub" in text


def test_kein_vorlagen_hinweis_wenn_abschnitt_da_ist(tmp_path, monkeypatch):
    """Läuft alles, kommt KEINE Warnung — sonst stumpft sie ab.

    Der Zeitraum ist bewusst RELATIV zu heute: mit festen Daten prüfte dieser
    Test bis zum 31.08.2026 einen ZUKÜNFTIGEN Urlaub (der zu Recht den
    Start-Hinweis bekommt) und wäre danach still auf einen laufenden
    umgesprungen. Ein Test, dessen Aussage vom Kalender abhängt, prüft an
    verschiedenen Tagen Verschiedenes."""
    from datetime import date, timedelta

    _setup_env(tmp_path, monkeypatch)
    heute = date.today()
    _write_agent_env(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="false",
        URLAUB_VON=(heute - timedelta(days=1)).isoformat(),
        URLAUB_BIS=(heute + timedelta(days=7)).isoformat(),
    )
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic("info", "## Urlaub\nIch bin weg.\n")
    assert "achtung_urlaub" not in chat_tools.einstellungen_lesen("info")


def test_zukuenftiger_urlaub_behaelt_die_warnungen(tmp_path, monkeypatch):
    """Der Befund, den die Gesamtsuite aufgedeckt hat: ein noch nicht
    begonnener Urlaub bekommt den Start-Hinweis — aber Probelauf und fehlende
    Vorlage müssen TROTZDEM gemeldet werden.

    Genau vor der Abreise sind diese zwei Warnungen am meisten wert; ein
    früherer Entwurf behandelte „noch nicht begonnen" als eigenen Zweig und
    verschluckte sie damit."""
    from datetime import date, timedelta

    _setup_env(tmp_path, monkeypatch)
    heute = date.today()
    _write_agent_env(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="true",
        URLAUB_VON=(heute + timedelta(days=7)).isoformat(),
        URLAUB_BIS=(heute + timedelta(days=14)).isoformat(),
    )
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic("info", "## Terminanfrage\nHallo\n")
    text = chat_tools.einstellungen_lesen("info")["achtung_urlaub"]

    assert "beginnt erst am" in text
    assert "Probelauf ist AN" in text
    assert "## Urlaub" in text


def test_hinweis_wenn_probelauf_noch_an(tmp_path, monkeypatch):
    """Zeitraum relativ zu heute — dieselbe Zeitbomben-Falle wie bei
    `test_hinweis_wenn_vorlage_fehlt`."""
    from datetime import date, timedelta

    _setup_env(tmp_path, monkeypatch)
    heute = date.today()
    _write_agent_env(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_DRY_RUN="true",
        URLAUB_VON=(heute - timedelta(days=1)).isoformat(),
        URLAUB_BIS=(heute + timedelta(days=7)).isoformat(),
    )
    import src.agents_io as agents_io
    import src.chat_tools as chat_tools

    agents_io.write_vorlagen_md_atomic("info", "## Urlaub\nIch bin weg.\n")
    text = chat_tools.einstellungen_lesen("info").get("achtung_urlaub", "")
    assert "Probelauf" in text


# ==========================================================================
# 2. Route
# ==========================================================================


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import src.main as main

    return TestClient(main.app)


def _signierte_anfrage(agent_id="info", empfaenger=None):
    import src.urlaub_relay as urlaub_relay

    body = json.dumps(
        {
            "agent_id": agent_id,
            "absender": "info@ionos.de",
            "empfaenger": empfaenger or ["kunde@web.de"],
            "rfc822_base64": base64.b64encode(b"Subject: Re: X\r\n\r\nWeg.\r\n").decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    stempel = str(int(time.time()))
    return body, {
        urlaub_relay.TIMESTAMP_HEADER: stempel,
        urlaub_relay.SIGNATUR_HEADER: urlaub_relay.signatur(body, stempel),
        "Content-Type": "application/json",
    }


def test_route_liefert_bei_gueltiger_anfrage_200(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(
        ENABLE_URLAUBSANTWORT="true",
        URLAUB_VON=(date.today() - timedelta(days=1)).isoformat(),
        URLAUB_BIS=(date.today() + timedelta(days=7)).isoformat(),
    )
    import src.mail_send as mail_send

    deliver = mocker.patch.object(mail_send, "deliver")
    body, headers = _signierte_anfrage()

    antwort = _client(tmp_path, monkeypatch).post(
        "/intern/urlaubsantwort/info", content=body, headers=headers
    )

    assert antwort.status_code == 200
    assert antwort.json()["status"] == "gesendet"
    assert deliver.call_count == 1


def test_route_ohne_signatur_ist_401(tmp_path, monkeypatch, mocker):
    """Die Route hat bewusst KEIN `require_auth` (der Aufrufer ist kein
    Browser) — die Signatur IST die Authentifizierung. Fällt dieser Test, ist
    der Endpoint offen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_URLAUBSANTWORT="true")
    import src.mail_send as mail_send

    deliver = mocker.patch.object(mail_send, "deliver")
    body, _ = _signierte_anfrage()

    antwort = _client(tmp_path, monkeypatch).post(
        "/intern/urlaubsantwort/info", content=body, headers={"Content-Type": "application/json"}
    )

    assert antwort.status_code == 401
    assert deliver.call_count == 0


def test_route_bei_ausgeschaltetem_flag_ist_403(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_URLAUBSANTWORT="false")
    import src.mail_send as mail_send

    deliver = mocker.patch.object(mail_send, "deliver")
    body, headers = _signierte_anfrage()

    antwort = _client(tmp_path, monkeypatch).post(
        "/intern/urlaubsantwort/info", content=body, headers=headers
    )

    assert antwort.status_code == 403
    assert deliver.call_count == 0


def test_route_meldet_keinen_stacktrace(tmp_path, monkeypatch, mocker):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env(ENABLE_URLAUBSANTWORT="false")
    body, headers = _signierte_anfrage()

    antwort = _client(tmp_path, monkeypatch).post(
        "/intern/urlaubsantwort/info", content=body, headers=headers
    )

    text = antwort.text
    assert "Traceback" not in text
    assert "File \"" not in text


# ==========================================================================
# 3. Sync-Wächter zwischen den beiden Images
# ==========================================================================


def _agent_quelle(name: str) -> str:
    pfad = _REPO_ROOT / "agent" / "src" / name
    if not pfad.exists():
        pytest.skip("Schwester-Service nicht im Dateisystem (Docker-Image-Build).")
    return pfad.read_text(encoding="utf-8")


def test_hmac_zweck_ist_auf_beiden_seiten_gleich():
    """Läuft diese Konstante auseinander, passt KEINE Signatur mehr — und der
    Fehler wäre still: jede Urlaubsantwort würde mit 401 abgelehnt, während
    beide Seiten für sich genommen fehlerfrei aussehen."""
    quelle = _agent_quelle("mail_relay.py")
    import src.urlaub_relay as urlaub_relay

    zweck = urlaub_relay.RELAY_HMAC_ZWECK.decode("ascii")
    assert f'RELAY_HMAC_ZWECK = b"{zweck}"' in quelle


def test_header_namen_sind_auf_beiden_seiten_gleich():
    quelle = _agent_quelle("mail_relay.py")
    import src.urlaub_relay as urlaub_relay

    assert f'SIGNATUR_HEADER = "{urlaub_relay.SIGNATUR_HEADER}"' in quelle
    assert f'TIMESTAMP_HEADER = "{urlaub_relay.TIMESTAMP_HEADER}"' in quelle


def test_zeitfenster_ist_auf_beiden_seiten_gleich():
    """Ein größeres Fenster auf der Empfängerseite wäre eine stille
    Aufweichung des Replay-Schutzes."""
    quelle = _agent_quelle("mail_relay.py")
    import src.urlaub_relay as urlaub_relay

    assert f"MAX_CLOCK_SKEW_SECONDS = {urlaub_relay.MAX_CLOCK_SKEW_SECONDS}" in quelle


def test_urlaubs_ueberschriften_sind_auf_beiden_seiten_gleich():
    """Sonst sagt der Chat „es gibt keine Vorlage", während der Agent sie
    findet — oder umgekehrt."""
    quelle = _agent_quelle("vorlagen.py")
    import src.chat_tools as chat_tools

    for name in chat_tools._URLAUB_UEBERSCHRIFTEN:
        assert f'"{name}"' in quelle


def test_relay_pfad_stimmt_mit_der_route_ueberein():
    """Der Agent baut die URL aus `RELAY_PFAD` — benennt jemand die Route um,
    ohne die Konstante nachzuziehen, kommt keine Urlaubsantwort mehr an."""
    quelle = _agent_quelle("mail_relay.py")
    haupt = (_REPO_ROOT / "webui" / "src" / "main.py").read_text(encoding="utf-8")

    assert 'RELAY_PFAD = "/intern/urlaubsantwort"' in quelle
    assert '@app.post("/intern/urlaubsantwort/{agent_id}")' in haupt


# ==========================================================================
# 4. Die zwei Middleware-Ausnahmen bleiben eng
# ==========================================================================
#
# Der Relay-Pfad ist von Session-Gate UND CSRF-Abwehr ausgenommen (Begründung
# im Docstring von `_is_urlaub_relay_path`). Beide Ausnahmen sind bewusst auf
# EIN exaktes Pfadmuster begrenzt; eine Präfix-Ausnahme `/intern/` würde jeden
# künftigen Pfad dort still mitnehmen. Diese Tests halten das fest — sie sind
# der Grund, warum die Ausnahme vertretbar ist.


@pytest.mark.parametrize(
    "pfad",
    [
        "/intern/urlaubsantwort/info",
        "/intern/urlaubsantwort/zweiter-agent",
    ],
)
def test_ausnahme_greift_fuer_den_relay_pfad(pfad):
    import src.main as main

    assert main._is_urlaub_relay_path(pfad) is True


@pytest.mark.parametrize(
    "pfad",
    [
        "/intern/",
        "/intern/urlaubsantwort",
        "/intern/urlaubsantwort/",
        "/intern/urlaubsantwort/info/extra",
        "/intern/etwas-anderes/info",
        "/intern/urlaubsantwort/INFO",          # Slug-Muster: nur Kleinbuchstaben
        "/intern/urlaubsantwort/../../save",
        "/save",
        "/reset",
        "/chat/info/send",
    ],
)
def test_ausnahme_greift_fuer_nichts_anderes(pfad):
    """Fällt einer dieser Fälle, ist über den Umweg der Ausnahme eine andere
    Route unauthentifiziert erreichbar geworden."""
    import src.main as main

    assert main._is_urlaub_relay_path(pfad) is False


def test_andere_route_bleibt_ohne_origin_gesperrt(tmp_path, monkeypatch):
    """Gegenprobe zur CSRF-Ausnahme: ein POST OHNE Origin auf eine andere Route
    muss weiterhin mit 403 abgewiesen werden.

    Gewählt ist `/addin/verify-password`, weil dieser Pfad wie der Relay-Pfad
    am Session-Gate vorbeiläuft und deshalb tatsächlich in
    `enforce_same_origin` ankommt — an einer normalen Route (`/save`) griffe
    vorher das Session-Gate, und der Test würde die CSRF-Abwehr gar nicht
    berühren."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env()
    antwort = _client(tmp_path, monkeypatch).post(
        "/addin/verify-password", json={"passwort": "x"}
    )
    assert antwort.status_code == 403
    assert "cross-origin" in antwort.text


def test_andere_intern_route_gaebe_es_nicht():
    """Es gibt genau EINE `/intern/`-Route. Kommt eine zweite dazu, muss diese
    Datei bewusst angefasst werden — sie erbt die Ausnahme NICHT."""
    import src.main as main

    intern_routen = [
        r.path for r in main.app.routes if getattr(r, "path", "").startswith("/intern/")
    ]
    assert intern_routen == ["/intern/urlaubsantwort/{agent_id}"]
