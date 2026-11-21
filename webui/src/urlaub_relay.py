"""Gegenstelle zu `agent/src/mail_relay.py` — nimmt EINE Urlaubsantwort an.

Der Polling-Agent darf strukturell kein SMTP sprechen (CLAUDE.md, per AST-
Waechter ueber `agent/src/` erzwungen). Die Urlaubs-Autoantwort ist die eine
vom Betreiber freigegebene Ausnahme von „kein Auto-Send"; sie wurde so gebaut,
dass die Invariante woertlich stehen bleibt:

    agent  ──[fertige RFC-5322-Bytes, HMAC-signiert]──▶  HIER  ──▶  mail_send

Dieses Modul ist bewusst so eng wie moeglich:

* **Ein Zweck.** Es nimmt Urlaubsantworten an, sonst nichts. Es gibt keinen
  Betreff-, Text- oder Empfaenger-Parameter, den ein Aufrufer frei setzen
  koennte — der Agent liefert fertige Bytes, hier wird nur zugestellt.
* **Fail-closed am Flag.** Steht `ENABLE_URLAUBSANTWORT` in der `.env` DIESES
  Agenten nicht auf `true`, wird abgelehnt. Der Schalter wirkt damit an zwei
  Stellen: der Agent fragt gar nicht erst, und die WebUI liefert auch dann
  nicht aus, wenn er es doch taete.
* **Fail-closed am Zeitraum.** Ausserhalb von `URLAUB_VON`..`URLAUB_BIS` wird
  abgelehnt — auch das doppelt, aus demselben Grund.
* **Signatur + Frische.** HMAC ueber `"<timestamp>.<body>"` mit einem aus dem
  gemeinsamen Fernet-Key abgeleiteten, ZWECKGEBUNDENEN Schluessel; ein
  Zeitstempel ausserhalb von `MAX_CLOCK_SKEW_SECONDS` wird abgelehnt (Replay).
* **Empfaenger-Deckel.** Eine Urlaubsantwort geht an GENAU EINEN Menschen.
  Mehr als ein Empfaenger ist kein Betriebsfall, sondern ein Alarmzeichen.

Der eigentliche SMTP-Transport bleibt `webui/src/mail_send.py` — die einzige
Stelle des Produkts, die `smtplib` importiert (`test_nur_mail_send_modul_
importiert_smtplib`).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import time
from datetime import date, datetime
from typing import Optional

from . import agents_io, crypto, mail_send

logger = logging.getLogger("vizpatch.urlaub_relay")

# MUSS byte-identisch zu `agent/src/mail_relay.RELAY_HMAC_ZWECK` sein — sonst
# passt keine Signatur. Ein Sync-Test haelt beide Konstanten zusammen.
RELAY_HMAC_ZWECK = b"vizpatch-urlaub-relay:"

MAX_CLOCK_SKEW_SECONDS = 300
SIGNATUR_HEADER = "X-Vizpatch-Signature"
TIMESTAMP_HEADER = "X-Vizpatch-Timestamp"

# Obergrenze fuer den Anfrage-Body. Eine Abwesenheitsnotiz ist ein kurzer Text
# ohne Anhang; 256 KB sind grosszuegig und verhindern, dass ein fehlgeleiteter
# Aufruf den Prozess mit Speicher flutet.
MAX_BODY_BYTES = 256 * 1024

# Eine Urlaubsantwort hat GENAU einen Empfaenger (siehe Modul-Docstring).
MAX_EMPFAENGER = 1


class RelayAbgelehnt(RuntimeError):
    """Fachliche Ablehnung mit HTTP-Status. Die Meldung geht an den Agenten
    (der sie loggt), nie an einen Browser — trotzdem enthaelt sie bewusst kein
    Secret und keinen Server-Text."""

    def __init__(self, status: int, meldung: str):
        super().__init__(meldung)
        self.status = status
        self.meldung = meldung


def relay_hmac_key() -> bytes:
    """Zweckgebundener HMAC-Key aus dem gemeinsamen Fernet-Key
    (`/config/.secret_key`) — dieselbe Ableitung wie im Agenten."""
    return hashlib.sha256(RELAY_HMAC_ZWECK + crypto.load_or_create_key()).digest()


def signatur(body: bytes, timestamp: str, key: Optional[bytes] = None) -> str:
    schluessel = key if key is not None else relay_hmac_key()
    nachricht = timestamp.encode("ascii", "ignore") + b"." + body
    return "sha256=" + hmac.new(schluessel, nachricht, hashlib.sha256).hexdigest()


def _signatur_gueltig(body: bytes, timestamp: str, empfangen: str) -> bool:
    if not empfangen:
        return False
    return hmac.compare_digest(signatur(body, timestamp), empfangen)


def _timestamp_frisch(timestamp: str, jetzt: Optional[float] = None) -> bool:
    try:
        gesendet = float(timestamp)
    except (TypeError, ValueError):
        return False
    referenz = time.time() if jetzt is None else jetzt
    return abs(referenz - gesendet) <= MAX_CLOCK_SKEW_SECONDS


def _zeitraum_aktiv(von: str, bis: str, heute: Optional[date] = None) -> bool:
    """Zweitpruefung des Urlaubszeitraums. Bewusst eine eigene, kleine
    Implementierung statt eines Imports aus `agent/src/` — die beiden Services
    sind getrennte Images ohne gemeinsames Paket, und ein Zweitriegel, der
    denselben Code ausfuehrt, waere kein Zweitriegel."""
    try:
        von_datum = date.fromisoformat((von or "").strip())
        bis_datum = date.fromisoformat((bis or "").strip())
    except ValueError:
        return False
    if bis_datum < von_datum:
        return False
    bezug = heute or datetime.now().date()
    return von_datum <= bezug <= bis_datum


def pruefe_und_sende(agent_id: str, body: bytes, timestamp: str, empfangene_signatur: str) -> dict:
    """Der vollstaendige Annahme-Pfad: pruefen, dann ueber `mail_send.deliver`
    zustellen. Wirft `RelayAbgelehnt` mit passendem HTTP-Status; die Route in
    `main.py` uebersetzt das nur noch in eine Antwort.

    Reihenfolge ist Absicht — das Billigste und das Sicherheitskritische
    zuerst, damit ein unsignierter Aufruf nicht einmal eine Datei anfasst."""
    if len(body) > MAX_BODY_BYTES:
        raise RelayAbgelehnt(413, "Anfrage zu groß für eine Urlaubsantwort.")

    if not _timestamp_frisch(timestamp):
        logger.warning("urlaub_relay_timestamp_alt", extra={"agent_id": agent_id})
        raise RelayAbgelehnt(401, "Zeitstempel fehlt oder ist zu alt.")

    if not _signatur_gueltig(body, timestamp, empfangene_signatur):
        logger.warning("urlaub_relay_signatur_ungueltig", extra={"agent_id": agent_id})
        raise RelayAbgelehnt(401, "Signatur ungültig.")

    try:
        nutzlast = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RelayAbgelehnt(400, "Anfrage ist kein gültiges JSON.") from e
    if not isinstance(nutzlast, dict):
        raise RelayAbgelehnt(400, "Anfrage ist kein gültiges JSON-Objekt.")

    # Die agent_id aus dem Pfad ist die massgebliche — sie steht auch im
    # signierten Body, und beide muessen uebereinstimmen. Sonst koennte eine
    # fuer Agent A gueltige Signatur an Agent B's Postfach adressiert werden.
    if str(nutzlast.get("agent_id") or "") != agent_id:
        logger.warning("urlaub_relay_agent_mismatch", extra={"agent_id": agent_id})
        raise RelayAbgelehnt(400, "Agent-Kennung passt nicht zur Signatur.")

    try:
        env = agents_io.read_env_raw(agent_id)
    except ValueError as e:
        raise RelayAbgelehnt(404, "Unbekannter Agent.") from e
    if not env:
        raise RelayAbgelehnt(404, "Unbekannter Agent.")

    # Fail-closed am Flag — der Schalter, den der Betreiber im Einstellungen-
    # Fenster umlegt, wirkt HIER genauso wie im Agenten.
    if (env.get("ENABLE_URLAUBSANTWORT") or "false").strip().lower() != "true":
        logger.warning("urlaub_relay_flag_aus", extra={"agent_id": agent_id})
        raise RelayAbgelehnt(403, "Urlaubs-Autoantwort ist für diesen Agenten nicht aktiv.")

    if not _zeitraum_aktiv(env.get("URLAUB_VON") or "", env.get("URLAUB_BIS") or ""):
        logger.warning("urlaub_relay_ausserhalb_zeitraum", extra={"agent_id": agent_id})
        raise RelayAbgelehnt(403, "Heute liegt nicht im eingetragenen Urlaubszeitraum.")

    empfaenger = nutzlast.get("empfaenger")
    if not isinstance(empfaenger, list) or not empfaenger:
        raise RelayAbgelehnt(400, "Kein Empfänger angegeben.")
    empfaenger = [str(e).strip() for e in empfaenger if str(e).strip()]
    if not empfaenger:
        raise RelayAbgelehnt(400, "Kein Empfänger angegeben.")
    if len(empfaenger) > MAX_EMPFAENGER:
        # Kein Betriebsfall — laut protokollieren statt still den ersten nehmen.
        logger.warning(
            "urlaub_relay_zu_viele_empfaenger",
            extra={"agent_id": agent_id, "anzahl": len(empfaenger)},
        )
        raise RelayAbgelehnt(400, "Eine Urlaubsantwort geht an genau einen Empfänger.")

    try:
        raw_bytes = base64.b64decode(str(nutzlast.get("rfc822_base64") or ""), validate=True)
    except (binascii.Error, ValueError) as e:
        raise RelayAbgelehnt(400, "Mail-Inhalt ist nicht lesbar.") from e
    if not raw_bytes:
        raise RelayAbgelehnt(400, "Mail-Inhalt ist leer.")

    imap_user = (env.get("IMAP_USER") or "").strip()
    try:
        cfg = mail_send.resolve_smtp_config(env)
    except RuntimeError as e:
        raise RelayAbgelehnt(502, str(e)) from e

    roh_passwort = (env.get("IMAP_PASSWORD") or "").strip()
    try:
        passwort = crypto.decrypt_value(roh_passwort) if roh_passwort else ""
    except RuntimeError as e:
        raise RelayAbgelehnt(502, "Zugangsdaten konnten nicht entschlüsselt werden.") from e

    try:
        mail_send.deliver(
            cfg,
            passwort,
            envelope_from=imap_user,
            recipients=empfaenger,
            raw_bytes=raw_bytes,
        )
    except mail_send.SmtpSendError as e:
        # 502, nicht 400: der Agent soll es im naechsten Zyklus erneut
        # versuchen duerfen (er merkt den Absender bei Fehlschlag NICHT vor).
        raise RelayAbgelehnt(502, str(e)) from e

    logger.info(
        "urlaub_relay_gesendet",
        extra={"agent_id": agent_id, "host": cfg["host"], "empfaenger_anzahl": len(empfaenger)},
    )
    return {"status": "gesendet", "empfaenger_anzahl": len(empfaenger)}
