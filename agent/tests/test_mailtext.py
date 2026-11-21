"""Tests fuer die HTML-zu-Text-Gewinnung (Live-Befund 2026-08-21).

`generate.py` und `draft.py` riefen `MailMessage.html_to_text()` auf — eine
Methode, die imap-tools NICHT hat. Bei jeder Mail ohne Text-Teil flog ein
AttributeError, der Poll-Zyklus meldete `process_failed`, und fuer die Mail
entstand KEIN Entwurf. Aufgefallen an einer reinen HTML-Mail im Testpostfach.
"""
from __future__ import annotations

from types import SimpleNamespace

from src import mailtext


def test_text_teil_gewinnt_wenn_vorhanden():
    msg = SimpleNamespace(text="Klartext hier", html="<p>ignoriert</p>")
    assert mailtext.mail_text(msg) == "Klartext hier"


def test_faellt_auf_html_zurueck_wenn_kein_text_teil():
    """Der Fall, der vorher den AttributeError ausloeste."""
    msg = SimpleNamespace(text="", html="<p>Sehr geehrte Damen und Herren,</p><p>anbei die Rechnung.</p>")
    ergebnis = mailtext.mail_text(msg)
    assert "Sehr geehrte Damen und Herren," in ergebnis
    assert "anbei die Rechnung." in ergebnis
    assert "<p>" not in ergebnis


def test_leere_mail_liefert_leerstring_statt_absturz():
    assert mailtext.mail_text(SimpleNamespace(text="", html="")) == ""
    assert mailtext.mail_text(SimpleNamespace(text=None, html=None)) == ""


def test_fehlende_attribute_brechen_nicht():
    """`getattr`-Absicherung: ein Objekt ohne .text/.html darf nicht werfen."""
    assert mailtext.mail_text(SimpleNamespace()) == ""


def test_skript_und_style_landen_nicht_im_text():
    html = "<style>.a{color:red}</style><script>var x=1;</script><p>Nur das hier.</p>"
    ergebnis = mailtext.html_zu_text(html)
    assert ergebnis == "Nur das hier."


def test_absaetze_werden_zu_zeilen_statt_zu_einer_wurst():
    html = "<p>Erste Zeile</p><p>Zweite Zeile</p>"
    assert mailtext.html_zu_text(html).splitlines() == ["Erste Zeile", "Zweite Zeile"]


def test_html_entities_werden_aufgeloest():
    assert mailtext.html_zu_text("<p>Gr&uuml;&szlig;e &amp; Dank</p>") == "Grüße & Dank"


def test_br_wird_zum_zeilenumbruch():
    assert mailtext.html_zu_text("Zeile A<br>Zeile B").splitlines() == ["Zeile A", "Zeile B"]
