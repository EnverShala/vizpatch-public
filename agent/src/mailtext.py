"""Lesbaren Text aus einer Mail gewinnen — auch wenn sie NUR HTML enthaelt.

Warum es dieses Modul gibt (Live-Befund 2026-08-21): `generate.py` und
`draft.py` riefen beide `MailMessage.html_to_text()` auf. Diese Methode
existiert in imap-tools NICHT — bei jeder Mail ohne Text-Teil (reines HTML,
bei Geschaeftsmails der Normalfall) flog ein `AttributeError`, der Poll-Zyklus
protokollierte `process_failed` und fuer diese Mail entstand KEIN Entwurf.
Weil `msg.text` bei den meisten Mails gefuellt ist, blieb der Pfad lange
unbemerkt; aufgefallen ist er erst an einer reinen HTML-Mail im Testpostfach.

Die Ersatzlogik ist bewusst dieselbe wie in `webui/src/chat_tools.py`
(`_HTML_TAG_RE`): Tags durch Leerzeichen ersetzen und Whitespace einsammeln.
Kein zusaetzliches Paket (kein bleach/html2text) — der Text geht ausschliesslich
in einen LLM-Prompt bzw. in einen Zitatblock, dafuer reicht das, und die
Abhaengigkeitsliste des Agenten bleibt schmal.
"""
from __future__ import annotations

import html as _html
import re

_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Skript- und Style-Bloecke VOR dem Tag-Strippen entfernen, sonst landet ihr
# Inhalt als vermeintlicher Fliesstext im Prompt.
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_LEERZEILEN_RE = re.compile(r"\n{3,}")


def html_zu_text(roh: str | None) -> str:
    """HTML -> grober Fliesstext. Leeres/None -> leerer String."""
    if not roh:
        return ""
    ohne_skript = _SCRIPT_STYLE_RE.sub(" ", roh)
    # Blockgrenzen in Zeilenumbrueche uebersetzen, damit Absaetze nicht
    # zu einer einzigen Zeile verschmelzen.
    mit_umbruch = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6])>", "\n", ohne_skript)
    text = _HTML_TAG_RE.sub(" ", mit_umbruch)
    text = _html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = "\n".join(zeile.strip() for zeile in text.splitlines())
    return _LEERZEILEN_RE.sub("\n\n", text).strip()


def mail_text(msg) -> str:
    """Bester verfuegbarer Klartext einer `MailMessage`: `text`, sonst aus
    `html` gewonnen, sonst leer. Ersetzt das nie existierende
    `msg.html_to_text()`."""
    direkt = (getattr(msg, "text", "") or "").strip()
    if direkt:
        return direkt
    return html_zu_text(getattr(msg, "html", "") or "")
