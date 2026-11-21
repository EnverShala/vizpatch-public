"""Vorschau-Injektion fuer Weiterleiten und Abmelden.

Befund der Live-Abnahme vom 2026-08-19: der Server speist eine offene
Versand-Vorschau samt Token in den Folge-Request ein (Quick 260801-2yg) —
ohne das kann das Modell die per Cross-Request-Gate erzwungene Bestaetigung
strukturell nie leisten. Genau diese Injektion fehlte bei den zwei in Phase 13
gebauten Werkzeug-Paaren: es gibt drei Pending-Stores, aber nur einer wurde
eingespeist. Auf "Ja, bitte so senden" kam eine WEITERE Vorschau, der
Gesendet-Ordner blieb unveraendert.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Vorschau-Injektion in den Folge-Request (Befund der Live-Abnahme 2026-08-19)
#
# Der Fix aus Quick 260801-2yg existierte nur fuer mail_senden. Weiterleiten
# und Abmelden haengen am selben Cross-Request-Gate, bekamen ihre offene
# Vorschau aber nie eingespeist -> das Modell konnte nie bestaetigen und
# erzeugte Vorschau um Vorschau.
# ---------------------------------------------------------------------------


def test_offene_weiterleitungs_vorschau_wird_in_folge_request_eingespeist():
    import src.chat_tools as chat_tools

    messages = chat_tools._build_initial_messages(
        None,
        "Ja, bitte so senden.",
        None,
        None,
        None,
        None,
        {"an": "empfaenger@example.com", "betreff": "Fwd: Rechnung", "vorschau_token": "abc123"},
        None,
    )

    inhalt = messages[-1]["content"]
    assert "Offene Weiterleitungs-Vorschau" in inhalt
    assert "abc123" in inhalt, "ohne Token kann das Modell im Folge-Request nie bestaetigen"
    assert "mail_weiterleiten_bestaetigen" in inhalt


def test_offene_abmelde_vorschau_wird_in_folge_request_eingespeist():
    import src.chat_tools as chat_tools

    messages = chat_tools._build_initial_messages(
        None, "Ja, abmelden.", None, None, None, None, None,
        {"weg": "https", "vorschau_token": "def456"},
    )

    inhalt = messages[-1]["content"]
    assert "Offene Abmelde-Vorschau" in inhalt
    assert "def456" in inhalt
    assert "mail_abmelden_bestaetigen" in inhalt


def test_ohne_offene_vorschau_bleibt_die_nachricht_unveraendert():
    """Rueckwaertskompatibilitaet: kein Zusatzblock, wenn nichts offen ist."""
    import src.chat_tools as chat_tools

    messages = chat_tools._build_initial_messages(None, "Hallo", None, None, None, None, None, None)

    inhalt = messages[-1]["content"]
    assert "Offene Weiterleitungs-Vorschau" not in inhalt
    assert "Offene Abmelde-Vorschau" not in inhalt


def test_vorschau_ohne_token_wird_nicht_eingespeist():
    """Ein Eintrag ohne Token ist unbrauchbar — er darf nichts erzeugen."""
    import src.chat_tools as chat_tools

    messages = chat_tools._build_initial_messages(
        None, "x", None, None, None, None, {"an": "a@b", "betreff": "t", "vorschau_token": "  "}, None
    )

    assert "Offene Weiterleitungs-Vorschau" not in messages[-1]["content"]
