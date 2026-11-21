"""Der EINZIGE Weg, auf dem aus dem Polling-Agenten heraus etwas hinausgeht.

## Warum es dieses Modul gibt

`CLAUDE.md` sagt seit dem ersten Tag: „**Kein Auto-Send in der Drafts-Pipeline.**
Der Polling-Agent legt ausschliesslich Entwuerfe im IMAP-`Drafts`-Ordner ab und
versendet NIE — kein SMTP-Import in `agent/src/`, per Drift-Test abgesichert."
Die Urlaubs-Autoantwort ist die vom Betreiber ausdruecklich benannte „einzige
Ausnahme". Sie wurde NICHT so gebaut, dass der Agent selbst SMTP spricht
(Variante (a) im TODO), sondern so:

    agent  ──[fertige RFC-5322-Bytes, HMAC-signiert]──▶  webui  ──SMTP──▶  Welt

Damit bleibt die Zusicherung woertlich erhalten: `agent/src/` enthaelt
weiterhin NULL `smtplib`-Importe und NULL `SMTP(`/`sendmail(`/`send_message(`-
Aufrufe (`test_agent_quellbaum_bleibt_smtp_frei`), und der gesamte
Sendeverkehr des Produkts geht durch die eine Stelle, an der er auditiert ist:
`webui/src/mail_send.py`.

Die Trennung ist keine Formalie — sie ist der Grund, warum ein kompromittierter
Mailinhalt oder ein fehlgeleiteter Klassifikator im Agenten strukturell keine
Mail nach draussen bringen kann: der Agent hat keinen SMTP-Sprachschatz, und
die Gegenstelle nimmt ausschliesslich Urlaubsantworten an (ein einziger
Endpoint, ein einziger Zweck, fail-closed hinter `ENABLE_URLAUBSANTWORT`).

## Authentifizierung

Kein neues Secret, keine Token-Verteilung: beide Container teilen sich bereits
`/config/.secret_key` (Fernet, `crypto.load_or_create_key`) — die WebUI
verschluesselt damit, der Agent entschluesselt damit. Aus diesem Key wird ein
ZWECKGEBUNDENER HMAC-Key abgeleitet (`sha256(zweck + key)`, dasselbe Muster wie
`chat_tools._confirmation_secret`), sodass eine Relay-Signatur nirgendwo sonst
gilt und umgekehrt.

Signiert wird `"<timestamp>.<body>"`. Der Zeitstempel wandert in einen eigenen
Header und begrenzt ein Replay auf `MAX_CLOCK_SKEW_SECONDS` — ohne ihn koennte
ein Mitleser im Docker-Netz eine einmal beobachtete Autoantwort beliebig oft
erneut ausloesen.

## Abhaengigkeiten

`urllib.request` aus der stdlib, bewusst kein `httpx`/`requests`: die
Abhaengigkeitsliste des Agenten bleibt schmal, und der Waechter
`test_agent_nur_mail_relay_spricht_nach_aussen` kann eine einfache Aussage
absichern — GENAU EIN Modul in `agent/src/` importiert ueberhaupt einen
HTTP-Client.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

from . import crypto

logger = logging.getLogger("vizpatch.mail_relay")

# Zweckbindung des abgeleiteten HMAC-Keys. MUSS byte-identisch zu
# `webui/src/urlaub_relay.py` sein — sonst passt keine Signatur.
RELAY_HMAC_ZWECK = b"vizpatch-urlaub-relay:"

RELAY_PFAD = "/intern/urlaubsantwort"
RELAY_TIMEOUT_SECONDS = 30.0

# Toleranz fuer Uhrzeit-Abweichung zwischen den beiden Containern; gleichzeitig
# das Replay-Fenster. 300 s ist grosszuegig fuer zwei Container auf DEMSELBEN
# Host und trotzdem kurz genug, dass eine abgefangene Anfrage nicht dauerhaft
# gueltig bleibt.
MAX_CLOCK_SKEW_SECONDS = 300

SIGNATUR_HEADER = "X-Vizpatch-Signature"
TIMESTAMP_HEADER = "X-Vizpatch-Timestamp"


class RelayError(RuntimeError):
    """Betreiber-lesbare deutsche Meldung — nie ein Stacktrace, nie ein Secret."""


def relay_hmac_key() -> bytes:
    """Zweckgebundener HMAC-Key aus dem gemeinsamen Fernet-Key.

    Oeffentlicher Accessor `crypto.load_or_create_key` (Review IN-05) statt
    Zugriff auf das private `_load_or_create_key` — eine interne Umbenennung
    dort bricht dieses Modul nicht."""
    return hashlib.sha256(RELAY_HMAC_ZWECK + crypto.load_or_create_key()).digest()


def signatur(body: bytes, timestamp: str, key: Optional[bytes] = None) -> str:
    """`sha256=<hexdigest>` ueber `"<timestamp>.".encode() + body`.

    Der Zeitstempel ist Teil der signierten Daten (nicht nur ein Header),
    sonst koennte ein Angreifer ihn austauschen und das Replay-Fenster
    beliebig verschieben."""
    schluessel = key if key is not None else relay_hmac_key()
    nachricht = timestamp.encode("ascii", "ignore") + b"." + body
    return "sha256=" + hmac.new(schluessel, nachricht, hashlib.sha256).hexdigest()


def signatur_gueltig(body: bytes, timestamp: str, empfangen: str, key: Optional[bytes] = None) -> bool:
    """Timing-sichere Pruefung — genutzt von der Gegenstelle (`webui`), hier
    mitgeliefert, damit beide Seiten dieselbe Implementierung teilen und ein
    Test die Runde Signieren -> Pruefen in einem Stueck belegen kann."""
    if not empfangen:
        return False
    return hmac.compare_digest(signatur(body, timestamp, key), empfangen)


def timestamp_frisch(timestamp: str, jetzt: Optional[float] = None) -> bool:
    """`abs(jetzt - timestamp) <= MAX_CLOCK_SKEW_SECONDS`. Nicht-numerisch ->
    False (fail-closed)."""
    try:
        gesendet = float(timestamp)
    except (TypeError, ValueError):
        return False
    referenz = time.time() if jetzt is None else jetzt
    return abs(referenz - gesendet) <= MAX_CLOCK_SKEW_SECONDS


def baue_nutzlast(agent_id: str, empfaenger: list[str], absender: str, raw_bytes: bytes) -> bytes:
    """Kanonischer JSON-Body (sortierte Schluessel, kompakte Trenner) — beide
    Seiten muessen ueber EXAKT denselben Bytes signieren/pruefen, deshalb wird
    serverseitig ueber den ROHEN Body geprueft und nicht ueber ein
    re-serialisiertes dict."""
    return json.dumps(
        {
            "agent_id": agent_id,
            "absender": absender,
            "empfaenger": list(empfaenger),
            "rfc822_base64": base64.b64encode(raw_bytes).decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def urlaubsantwort_ausliefern(
    config,
    empfaenger: list[str],
    raw_bytes: bytes,
    log: Optional[logging.Logger] = None,
) -> None:
    """Reicht EINE fertige Urlaubsantwort an die WebUI weiter, die sie per SMTP
    zustellt.

    Wirft `RelayError` mit deutscher, betreiber-lesbarer Meldung — der Aufrufer
    (`urlaub.run_urlaubsantwort`) behandelt das als "diese eine Antwort ging
    nicht raus" und macht mit der naechsten Mail weiter. Es wird NICHTS in die
    Merkliste geschrieben, wenn hier etwas schiefgeht: ein fehlgeschlagener
    Versand darf den Absender nicht fuer den Rest des Urlaubs sperren."""
    protokoll = log or logger
    if not empfaenger:
        raise RelayError("Kein Empfänger angegeben — es wurde nichts ausgeliefert.")

    body = baue_nutzlast(config.agent_id, empfaenger, config.own_email_address, raw_bytes)
    stempel = str(int(time.time()))
    basis = (config.webui_internal_url or "").rstrip("/")
    url = f"{basis}{RELAY_PFAD}/{config.agent_id}"

    anfrage = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: stempel,
            SIGNATUR_HEADER: signatur(body, stempel),
        },
    )

    try:
        with urllib.request.urlopen(anfrage, timeout=RELAY_TIMEOUT_SECONDS) as antwort:
            status = getattr(antwort, "status", None) or antwort.getcode()
            if status != 200:
                raise RelayError(f"Der WebUI-Dienst antwortete mit Status {status}.")
    except urllib.error.HTTPError as e:
        # KEIN Server-Text in die Meldung (Muster `mail_send.describe_smtp_error`):
        # er koennte Empfaenger oder Konfigurationsdetails enthalten und landet
        # sonst im Log.
        protokoll.warning(
            "urlaub_relay_http_fehler",
            extra={"agent_id": config.agent_id, "status": e.code},
        )
        raise RelayError(
            f"Der WebUI-Dienst hat die Urlaubsantwort abgelehnt (Status {e.code})."
        ) from e
    except urllib.error.URLError as e:
        protokoll.warning("urlaub_relay_nicht_erreichbar", extra={"agent_id": config.agent_id})
        raise RelayError(
            "Der WebUI-Dienst ist nicht erreichbar — läuft der Container 'webui'?"
        ) from e
    except RelayError:
        raise
    except Exception as e:
        protokoll.warning("urlaub_relay_fehler", extra={"agent_id": config.agent_id})
        raise RelayError("Die Urlaubsantwort konnte nicht ausgeliefert werden.") from e

    protokoll.info(
        "urlaub_relay_ok",
        extra={"agent_id": config.agent_id, "empfaenger_anzahl": len(empfaenger)},
    )
