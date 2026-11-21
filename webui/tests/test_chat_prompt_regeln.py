"""Regressionstests für den Chat-System-Prompt (Phase 13, Plan 13-10).

Der Prompt ist das eigentliche Verhaltensdokument des Agenten, und er ist über
Phase 13 von 301 auf 335 Zeilen gewachsen, obwohl 20 neue Anforderungen dazu
kamen — durch Verdichtung statt Anhängen. Diese Tests halten beides fest:

1. **Die vier belegten Live-Befunde bleiben wörtlich.** Sie stammen aus echten
   Fehlern im Betrieb. Eine Umformulierung „zur Verbesserung" würde genau die
   Fehler zurückholen, die sie behoben haben — deshalb sind sie hier
   zeichengenau festgeschrieben.
2. **Die Zeilenzahl hat eine Obergrenze.** Ohne sie wächst der Prompt bei jeder
   Erweiterung weiter, und lange Regel-Prompts leiden messbar: Anweisungen in
   der Mitte werden schlechter befolgt als solche am Rand, und thematisch
   überlappende Regeln konkurrieren miteinander. Wer künftig etwas hinzufügt,
   muss verdichten — dieser Test erzwingt die Entscheidung.
3. **Alle Platzhalter werden ersetzt.** Ein unersetzter Platzhalter erscheint
   dem Modell als Inhalt.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PROMPT_PFAD = Path(__file__).resolve().parent.parent / "prompts" / "chat-system.txt"

# Stand nach der Verdichtung in Plan 13-10: 335 Zeilen. Der Puffer von 5 Zeilen
# lässt eine kleine Ergänzung zu, ohne dass der Test bei jeder Änderung
# angefasst werden muss — eine größere Erweiterung MUSS verdichten.
MAX_PROMPT_ZEILEN = 340


@pytest.fixture(scope="module")
def prompt() -> str:
    return PROMPT_PFAD.read_text(encoding="utf-8")


# --- Die vier belegten Live-Befunde -------------------------------------------


def test_live_befund_von_ist_nicht_mit(prompt):
    """Live-Befund 2026-08-15: „lösch alle Mails VON MUSTERHANDEL" traf auch
    Paketdienst-Versandmails, die Musterhandel nur ERWÄHNEN."""
    assert "„VON\" IST NICHT „MIT\"" in prompt
    assert 'absender="Musterhandel"' in prompt
    assert "NIEMALS query" in prompt


def test_live_befund_firmenname_meint_den_absender(prompt):
    """Live-Befund: der Betreiber sagt den Absender meist ohne das Wort „von" —
    „lösch alle Paketdienst Mails" meint den Absender, nicht den Inhalt."""
    assert "„DIE PAKETDIENST-MAILS\" HEISST EBENFALLS „MAILS VON PAKETDIENST\"" in prompt
    assert 'absender="Paketdienst"' in prompt


def test_live_befund_mehr_als_50_treffer_durchziehen(prompt):
    """Live-Befund: der Agent fragte bei mehr als 50 Treffern nach, statt
    blockweise weiterzuarbeiten."""
    assert "MEHR ALS 50 TREFFER: EINFACH DURCHZIEHEN, NICHT FRAGEN." in prompt
    assert "weitere_vorhanden" in prompt


def test_live_befund_laufende_summe_statt_blockgroesse(prompt):
    """Teil desselben Befunds: der Fortschritt wird als laufende Summe berichtet,
    nicht als Blockgröße."""
    assert "LAUFENDEN\n  SUMME" in prompt or "LAUFENDEN SUMME" in prompt
    assert "123 von 123" in prompt


def test_live_befund_mail_immer_ganz_pruefen(prompt):
    """Live-Befund: Mails, deren Inhalt komplett im Anhang steckt, wurden als
    leer gemeldet."""
    assert "EINE MAIL PRÜFST DU IMMER GANZ" in prompt
    assert "mail_anhang_lesen" in prompt


# --- Länge ---------------------------------------------------------------------


def test_prompt_bleibt_unter_der_laengengrenze(prompt):
    zeilen = len(prompt.splitlines())

    assert zeilen <= MAX_PROMPT_ZEILEN, (
        f"Der System-Prompt hat {zeilen} Zeilen und überschreitet die Grenze von "
        f"{MAX_PROMPT_ZEILEN}. Das ist kein Formfehler: in langen Regel-Prompts werden "
        "Anweisungen in der Mitte schlechter befolgt als solche am Rand, und thematisch "
        "überlappende Regeln konkurrieren miteinander. Verdichte eine bestehende Regel, "
        "statt die Grenze anzuheben — und wenn du sie anhebst, begründe es im SUMMARY."
    )


# --- Platzhalter ----------------------------------------------------------------


@pytest.mark.parametrize(
    "platzhalter", ["{context_md}", "{style_md}", "{vorlagen_md}", "{agent_status}", "{heute}"]
)
def test_platzhalter_ist_im_template_vorhanden(prompt, platzhalter):
    assert platzhalter in prompt


def test_alle_platzhalter_werden_im_fertigen_prompt_ersetzt(tmp_path, monkeypatch):
    """Ein unersetzter Platzhalter erscheint dem Modell als Inhalt — etwa
    „{vorlagen_md}" als Text mitten in den Daten."""
    from .test_chat_tools import _setup_env, _write_agent_env

    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(PROMPT_PFAD))
    import src.chat as chat

    fertig = chat.build_system_prompt("info")

    for platzhalter in ("{context_md}", "{style_md}", "{vorlagen_md}", "{agent_status}", "{heute}"):
        assert platzhalter not in fertig, f"{platzhalter} wurde nicht ersetzt"


# --- Sprachverständnis-Regeln aus diesem Plan ----------------------------------


def test_zeitangaben_werden_auf_konkrete_grenzen_abgebildet(prompt):
    """MBOX-19: relative Zeitangaben müssen zu seit/bis werden."""
    assert "ZEITANGABEN" in prompt
    assert "JJJJ-MM-TT" in prompt
    assert "EINSCHLIESSLICH" in prompt
    assert "älter als ein Jahr" in prompt


def test_prompt_warnt_vor_uhrzeitgenauer_deutung(prompt):
    """Die IMAP-Suche ist tagesgenau — eine uhrzeitgenaue Zusage wäre falsch."""
    assert "tagesgenau" in prompt


def test_mengenwoerter_werden_eng_gelesen_und_die_lesart_genannt(prompt):
    """MBOX-19: dasselbe Prinzip wie bei „von" gegenüber „mit"."""
    assert "MENGEN- UND UNSCHÄRFEWÖRTER" in prompt
    assert "ENG" in prompt


def test_tagesbriefing_verlangt_gewichtung_statt_aufzaehlung(prompt):
    """MBOX-09: eine Liste aller Betreffzeilen ist kein Briefing."""
    assert "TAGESBRIEFING" in prompt
    assert "Gewichtung" in prompt


def test_fristen_werden_mit_quelle_genannt_und_nicht_in_kalender_geschrieben(prompt):
    """MBOX-17: erkennen und berichten, nicht schreiben — und nachprüfbar."""
    assert "FRISTEN UND TERMINE" in prompt
    assert "MIT Quelle" in prompt
    assert "NICHTS in einen\n  Kalender" in prompt or "NICHTS in einen Kalender" in prompt


# --- Struktur: kein Thema an zwei Stellen --------------------------------------


def test_gate_ablauf_steht_nur_einmal(prompt):
    """Der Vorschau-Freigabe-Ablauf gilt für Senden, Weiterleiten und Abmelden —
    er wird EINMAL beschrieben, nicht dreimal. Sonst konkurrieren drei fast
    gleiche Regeln miteinander."""
    assert " ".join(prompt.split()).count("DER ABLAUF IST IMMER DERSELBE") == 1


def test_uid_je_ordner_regel_steht_nur_einmal(prompt):
    """Die Falle „uids gelten nur im angegebenen Ordner" galt früher getrennt
    für Papierkorb und Verschieben. Jetzt einmal generisch für alle
    Batch-Werkzeuge.

    Geprüft wird gegen den umbruchfreien Text: der Prompt ist auf ~96 Zeichen
    umgebrochen, und eine Zeichenketten-Prüfung, die auf eine bestimmte
    Umbruchstelle angewiesen ist, bricht bei jeder Umformulierung — sie würde
    die Textbreite festschreiben statt der Regel.
    """
    ohne_umbruch = " ".join(prompt.split())

    assert ohne_umbruch.count("mische nie uids") == 1


def test_injektions_anker_deckt_alle_datenbloecke_ab(prompt):
    """Kommt ein Datenblock dazu (in dieser Phase die Vorlagen), muss der Anker
    ihn mit abdecken — sonst ist ein Block nicht als Daten deklariert."""
    anker_zeile = next(z for z in prompt.splitlines() if z.startswith("Die folgenden Abschnitte"))

    for block in ("context.md", "style.md", "Vorlagen", "Status"):
        assert block in anker_zeile, f"{block} fehlt im Anker-Satz"
