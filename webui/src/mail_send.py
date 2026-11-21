"""SMTP-Versand-Kern — webui-only (Quick 260801-2yg, D-2yg-01/02).

Webui-only Modul: der Polling-Agent (`agent/src/`) bleibt strukturell SMTP-frei
(Drift-Test `test_agent_quellbaum_bleibt_smtp_frei`) — nur die WebUI, in der der
Betreiber Chat-Versand ausdrücklich freigeben kann, darf `smtplib` importieren.

NICHT in `provider_config.py`: diese Datei ist ein byte-identischer Drift-Zwilling
von `agent/src/provider_config.py` (`tests/test_provider_config_sync.py` prüft den
sha256) und wird von diesem Quick-Task NICHT angefasst. Die SMTP-Tabelle unten wird
stattdessen über den bereits von `resolve_imap_config` aufgelösten IMAP-**Host**
geschlüsselt — so erbt sie dessen komplette statische + MX-Auflösung gratis, ohne
den Zwilling zu verändern oder zu duplizieren.

Der eigentliche Versand-Aufrufer ist `webui/src/chat_tools.py::mail_senden_bestaetigen`
— dort lebt das Zweiphasen-Gate, hier ausschließlich der SMTP-Transport.
"""
from __future__ import annotations

import logging
import smtplib

from .provider_config import resolve_imap_config

logger = logging.getLogger("vizpatch.mail_send")

# Identischer Wert wie chat_tools._IMAP_TIMEOUT_SECONDS — kein hängender SMTP-
# Request soll den WebUI-Prozess blockieren.
_SMTP_TIMEOUT_SECONDS = 20.0

# IMAP-Host (Schlüssel, siehe Docstring oben) -> (SMTP-Host, Port, Security).
# Alle bekannten Provider laufen auf 587/STARTTLS.
_SMTP_BY_IMAP_HOST: dict[str, tuple[str, int, str]] = {
    "imap.gmx.net": ("mail.gmx.net", 587, "starttls"),
    "imap.web.de": ("smtp.web.de", 587, "starttls"),
    "imap.ionos.de": ("smtp.ionos.de", 587, "starttls"),
    "secureimap.t-online.de": ("securesmtp.t-online.de", 587, "starttls"),
    "imap.gmail.com": ("smtp.gmail.com", 587, "starttls"),
    "outlook.office365.com": ("smtp.office365.com", 587, "starttls"),
    "imap.mailbox.org": ("smtp.mailbox.org", 587, "starttls"),
    "imap.strato.de": ("smtp.strato.de", 587, "starttls"),
    "imap.your-server.de": ("smtp.your-server.de", 587, "starttls"),
    "imap.alfahosting.de": ("smtp.alfahosting.de", 587, "starttls"),
}

# SICHERHEIT: Klartext-SMTP ist bewusst NICHT konfigurierbar — die Zugangsdaten
# des Kunden-Postfachs gehen über diese Verbindung; ein Tippfehler im Env
# (z.B. "plain") darf nie in unverschlüsselten Versand kippen.
_ALLOWED_SECURITY = ("starttls", "ssl")


class SmtpSendError(RuntimeError):
    """Betreiber-lesbare deutsche Meldung — NIE ein Stacktrace, NIE ein Secret."""


def resolve_smtp_config(env: dict) -> dict:
    """Löst die SMTP-Konfiguration für den Agenten aus dessen Env-Werten auf.

    Reihenfolge (siehe Modul-Docstring, D-2yg-02):
    1. `SMTP_HOST`-Override (mit `SMTP_PORT`-Default 587, `SMTP_SECURITY`-Default
       "starttls") — schlägt IMMER, auch bei bekannter Domain.
    2. `_SMTP_BY_IMAP_HOST[imap_host]`, wobei `imap_host` entweder aus
       `env["IMAP_HOST"]` (falls gesetzt) oder aus
       `resolve_imap_config(IMAP_USER)["host"]` stammt.
    3. `imap.` -> `smtp.`-Heuristik auf demselben `imap_host`.
    4. `RuntimeError` mit einem Hinweis auf `SMTP_HOST` (der Betreiber weiß dann,
       was in der Agent-Konfiguration zu setzen ist).

    Gibt `{"host", "port", "security", "user"}` zurück. `user` ist der getrimmte
    `IMAP_USER` (dieselben Zugangsdaten wie IMAP, D-2yg-Vorgabe: Zero-Config)."""
    imap_user = (env.get("IMAP_USER") or "").strip()

    smtp_host_override = (env.get("SMTP_HOST") or "").strip()
    if smtp_host_override:
        security = (env.get("SMTP_SECURITY") or "starttls").strip().lower()
        if security not in _ALLOWED_SECURITY:
            raise RuntimeError(
                f"Ungültiges SMTP_SECURITY={security!r} — erlaubt sind nur "
                f"'starttls' oder 'ssl' (kein Klartext-Versand konfigurierbar)."
            )
        try:
            port = int(env.get("SMTP_PORT") or "587")
        except (TypeError, ValueError):
            port = 587
        return {"host": smtp_host_override, "port": port, "security": security, "user": imap_user}

    imap_host = (env.get("IMAP_HOST") or "").strip()
    if not imap_host:
        try:
            imap_host = (resolve_imap_config(imap_user) or {}).get("host") or ""
        except RuntimeError:
            # Unbekannte Domain, keine MX-Übereinstimmung — kein IMAP-Host
            # ermittelbar; unten greift entweder die Heuristik (falls IMAP_HOST
            # doch noch gesetzt war) oder die abschließende RuntimeError.
            imap_host = ""

    known = _SMTP_BY_IMAP_HOST.get(imap_host)
    if known:
        smtp_host, port, security = known
        return {"host": smtp_host, "port": port, "security": security, "user": imap_user}

    if imap_host.startswith("imap."):
        smtp_host = "smtp." + imap_host[len("imap."):]
        return {"host": smtp_host, "port": 587, "security": "starttls", "user": imap_user}

    raise RuntimeError(
        "Kein SMTP-Server ermittelbar — bitte SMTP_HOST (optional SMTP_PORT/"
        "SMTP_SECURITY) in der Agent-Konfiguration setzen."
    )


def deliver(
    config: dict,
    password: str,
    envelope_from: str,
    recipients: list[str],
    raw_bytes: bytes,
) -> None:
    """Verschickt `raw_bytes` (fertige RFC-5322-Bytes) per SMTP.

    `recipients` ist bewusst eine LISTE von Einzeladressen (Envelope-Form) — SMTP
    braucht je Empfänger ein eigenes RCPT-Element; ein kommaseparierter String
    würde bei mehreren Empfängern nicht (vollständig) zustellen (T-2yg-12).

    Ablauf bei `security="starttls"`: `smtplib.SMTP(host, port, timeout=...)`,
    `starttls()`, `login(...)`, `sendmail(...)`. Bei `security="ssl"`:
    `smtplib.SMTP_SSL(...)`, KEIN `starttls()`. Verbindung wird in jedem Fall
    geschlossen (`quit()`).

    Leere `recipients` -> `SmtpSendError` VOR jedem Verbindungsaufbau (kein
    Netzwerk-Roundtrip für einen von vornherein sinnlosen Versand). Jede
    `smtplib`-/`OSError`-Ausnahme wird über `describe_smtp_error` in eine
    `SmtpSendError` mit betreiber-lesbarer deutscher Meldung übersetzt — nie
    das Passwort, nie der Server-Text (T-2yg-05)."""
    if not recipients:
        raise SmtpSendError("Kein Empfänger angegeben — es wurde nichts gesendet.")

    host = config["host"]
    port = config["port"]
    security = config["security"]
    user = config["user"]

    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=_SMTP_TIMEOUT_SECONDS)
        else:
            server = smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT_SECONDS)
        try:
            if security == "starttls":
                server.starttls()
            server.login(user, password)
            server.sendmail(envelope_from, recipients, raw_bytes)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except Exception as e:
        # T-2yg-04: NIE Passwort/Empfänger/Betreff loggen — nur Host/Port/
        # Security und das Fehlerflag.
        logger.warning(
            "smtp_send_failed",
            extra={"host": host, "port": port, "security": security},
        )
        raise SmtpSendError(describe_smtp_error(e)) from e

    logger.info("smtp_send_ok", extra={"host": host, "port": port, "security": security})


def describe_smtp_error(exc: Exception) -> str:
    """Übersetzt eine `smtplib`-/Netzwerk-Ausnahme in eine konkrete, betreiber-
    lesbare deutsche Meldung — Muster `chat_tools.describe_llm_error`.

    SICHERHEIT (T-2yg-05): die Meldung wird ausschließlich aus der Fehler-KLASSE
    gebaut, NIE aus `str(exc)` — so kann kein Passwort und kein Server-Echo
    durchsickern. Reihenfolge beachtet: alle `smtplib`-Exceptions sind (indirekt)
    `OSError`-Unterklassen — die spezifischeren Klassen (Authentication/
    Recipients/Sender) müssen deshalb VOR dem generischen `OSError`-Fall geprüft
    werden."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "Anmeldung beim Mailserver fehlgeschlagen — Zugangsdaten prüfen."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "Der Mailserver hat mindestens einen Empfänger abgelehnt."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "Der Mailserver hat den Absender abgelehnt."
    if isinstance(exc, (smtplib.SMTPConnectError, OSError)):
        return "Verbindung zum Mailserver fehlgeschlagen — Netzwerk/Firewall prüfen."
    return "Versand fehlgeschlagen (unbekannter Fehler beim Mailserver)."
