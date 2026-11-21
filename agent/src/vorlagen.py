"""Lesezugriff auf `vorlagen.md` — die Textbausteine EINES Agenten.

`vorlagen.md` entstand in Phase 13 (MBOX-17) als Chat-Ressource und liegt neben
`context.md`/`style.md`/`sortierregeln.md` im Agent-Verzeichnis. Der
Polling-Agent brauchte sie bisher nie; mit der Urlaubs-Autoantwort
(`agent/src/urlaub.py`) liest er erstmals einen benannten Abschnitt daraus.

Format (Muster wie `sortierregeln.py`): je Vorlage eine `##`-Überschrift, der
Text darunter bis zur nächsten `##`-Überschrift.

    ## Urlaub
    vielen Dank für Ihre Nachricht. Ich bin bis zum 14. September nicht
    erreichbar und melde mich danach.

Eine Vorlage in diesem Textbaustein-Stil (ohne Anrede, ohne Gruss) ist der
Normalfall — beim Draft baut das LLM beides drumherum. Die Urlaubs-Autoantwort
hat kein LLM im Sendeweg, deshalb ergaenzt `als_vollstaendige_mail` (unten) eine
neutrale Anrede und Grussformel, falls sie fehlen. Bis zum 2026-08-23 tat sie
das nicht: die Vorlage ging 1:1 hinaus, und beim Empfaenger begann die Mail
mitten im Satz. Wer die vollstaendige Mail selbst schreiben will, kann das
weiterhin — dann wird nichts ergaenzt.

BEWUSST KEIN Platzhaltertext: findet dieses Modul den Abschnitt nicht, gibt es
einen leeren String zurück und `urlaub.py` sendet NICHTS. Eine Autoantwort mit
erfundenem Inhalt ginge an echte Dritte raus — das ist der eine Fall, in dem
"lieber gar nichts" eindeutig richtig ist (TODO urlaubs-autoantwort, Punkt 4).
"""
from __future__ import annotations

import re

_UEBERSCHRIFT = re.compile(r"^\s{0,3}##\s+(.+?)\s*$")

# Überschriften, die als "der Urlaubstext" gelten. Der Betreiber-Wunsch nannte
# `## urlaub`; die drei Synonyme sind reine Tippfehler-Toleranz, damit ein
# "## Abwesenheit" nicht still zu "keine Vorlage, kein Versand" führt.
URLAUB_UEBERSCHRIFTEN = ("urlaub", "urlaubsantwort", "abwesenheit", "abwesenheitsnotiz")


def _normalisiert(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def abschnitt(md: str, namen: tuple[str, ...] | str) -> str:
    """Text unter der ersten `##`-Überschrift, deren Name (case-/whitespace-
    normalisiert) in `namen` steht. Kein Treffer -> leerer String."""
    if isinstance(namen, str):
        namen = (namen,)
    gesucht = {_normalisiert(n) for n in namen}

    aktiv = False
    zeilen: list[str] = []
    for zeile in (md or "").splitlines():
        treffer = _UEBERSCHRIFT.match(zeile)
        if treffer:
            if aktiv:
                break  # nächste Überschrift beendet den Abschnitt
            aktiv = _normalisiert(treffer.group(1)) in gesucht
            continue
        if aktiv:
            zeilen.append(zeile)

    return "\n".join(zeilen).strip()


def urlaubstext(md: str) -> str:
    """Der Text des `## Urlaub`-Abschnitts (oder eines Synonyms), sonst ""."""
    return abschnitt(md, URLAUB_UEBERSCHRIFTEN)


def abschnittsnamen(md: str) -> list[str]:
    """Alle `##`-Überschriften in Reihenfolge — für Log-/Fehlermeldungen, damit
    der Betreiber sieht, welche Vorlagen der Agent GEFUNDEN hat, wenn `Urlaub`
    nicht dabei ist."""
    namen: list[str] = []
    for zeile in (md or "").splitlines():
        treffer = _UEBERSCHRIFT.match(zeile)
        if treffer:
            name = " ".join(treffer.group(1).split())
            if name:
                namen.append(name)
    return namen


# --------------------------------------------------------------------------
# Anrede + Grussformel ergaenzen
# --------------------------------------------------------------------------

# Neutral heisst hier: ohne Namen, ohne Geschlecht, ohne Firmenbezug. Der Name
# des Absenders ist im `From` sichtbar und wird unter den Gruss gesetzt; der
# Name des EMPFAENGERS wird bewusst NICHT verwendet — er muesste aus dem
# `From`-Header geraten werden, und "Martin Kessler" -> "Herr Kessler" raet das
# Geschlecht mit. Bei einer Mail, die ohne menschliche Kontrolle an Dritte
# hinausgeht, ist "Guten Tag," die Form, die nie falsch ist.
ANREDE_NEUTRAL = "Guten Tag,"
GRUSS_NEUTRAL = "Freundliche Grüße"

# Erste Zeile beginnt schon mit einer Anrede -> nichts ergaenzen.
_ANREDE_MUSTER = re.compile(
    r"^\s*(hallo|guten\s+(tag|morgen|abend)|sehr\s+geehrte|sehr\s+verehrte|"
    r"liebe|lieber|moin|servus|gr(ü|ue)(ß|ss)\s+(gott|dich|euch)|"
    r"hi|hey|hallihallo|dear|good\s+(morning|afternoon|day))\b",
    re.IGNORECASE,
)

# Irgendeine der letzten Zeilen ist eine Grussformel -> nichts ergaenzen.
_GRUSS_MUSTER = re.compile(
    r"^\s*((mit\s+)?(freundliche[nm]?|beste[nm]?|herzliche[nm]?|liebe[nm]?|"
    r"sonnige[nm]?|viele[nm]?)\s+gr(ü|ue)(ß|ss)e?n?|"
    r"gr(ü|ue)(ß|ss)e|mfg|lg\b|vg\b|"
    r"(kind|best)\s+regards|regards|sincerely|bis\s+(dann|bald))",
    re.IGNORECASE,
)

# Wie viele Zeilen am Ende auf eine Grussformel geprueft werden. Vier deckt
# "Freundliche Gruesse / Name / Firma / Telefon" ab, ohne bei einer langen
# Vorlage mitten im Text falsch anzuschlagen.
_GRUSS_FENSTER = 4


def hat_anrede(text: str) -> bool:
    """Beginnt `text` mit einer Anrede?"""
    for zeile in (text or "").splitlines():
        if zeile.strip():
            return bool(_ANREDE_MUSTER.match(zeile))
    return False


def hat_gruss(text: str) -> bool:
    """Endet `text` mit einer Grussformel (in den letzten Zeilen)?"""
    gefuellt = [z for z in (text or "").splitlines() if z.strip()]
    return any(_GRUSS_MUSTER.match(z) for z in gefuellt[-_GRUSS_FENSTER:])


def als_vollstaendige_mail(text: str, absendername: str = "") -> str:
    """Ergaenzt eine neutrale Anrede und Grussformel, falls die Vorlage nur den
    Textkern enthaelt.

    Hintergrund (Befund aus dem scharfen Live-Test am 2026-08-23): eine
    Urlaubs-Vorlage wie

        ## Urlaub
        vielen Dank für Ihre Nachricht. Ich bin derzeit nicht im Haus …

    ging woertlich so hinaus — beim Empfaenger begann die Mail mitten im Satz
    und endete ohne Gruss. Im Draft-Pfad baut das LLM beides; die
    Urlaubs-Autoantwort nimmt die Vorlage dagegen 1:1, weil ihr Sinn gerade
    darin liegt, NICHTS zu erfinden. Genau dieser Textbaustein-Stil ist aber der
    naheliegende — auch das Beispiel in diesem Modul-Docstring war so gebaut.

    Idempotent: bringt die Vorlage Anrede und/oder Gruss selbst mit, wird
    NICHTS ergaenzt. Wer die volle Mail schreiben will, kann das also weiterhin
    — es entsteht kein doppeltes "Guten Tag,".

    `absendername` (der Anzeigename des Postfachs) landet unter dem Gruss, wenn
    einer bekannt ist. Der Name des EMPFAENGERS bleibt aussen vor, siehe
    Kommentar an `ANREDE_NEUTRAL`.
    """
    kern = (text or "").strip()
    if not kern:
        return ""

    teile: list[str] = []
    if not hat_anrede(kern):
        teile.extend([ANREDE_NEUTRAL, ""])
    teile.append(kern)
    if not hat_gruss(kern):
        teile.extend(["", GRUSS_NEUTRAL])
        name = " ".join((absendername or "").split())
        if name:
            teile.append(name)
    return "\n".join(teile)
