"""Tests fuer `agent/src/vorlagen.py` — das Lesen des `## Urlaub`-Abschnitts.

Der wichtigste Test dieser Datei ist der letzte: fehlt der Abschnitt, kommt ein
LEERER String zurueck. `urlaub.run_urlaubsantwort` haengt genau daran seine
Entscheidung "nicht senden" auf — eine Vorlage zu erfinden hiesse, echten
Dritten einen Text zu schicken, den der Betreiber nie geschrieben hat."""
from __future__ import annotations

import pytest

from src import vorlagen


BEISPIEL = """# Vorlagen

## Terminanfrage
Vielen Dank für Ihre Anfrage. Wir haben folgenden Termin frei.

## Urlaub
vielen Dank für Ihre Nachricht.
Ich bin bis zum 14. September nicht erreichbar.

## Reklamation
Das tut uns leid.
"""


def test_urlaubstext_liest_den_abschnitt():
    text = vorlagen.urlaubstext(BEISPIEL)
    assert text.startswith("vielen Dank für Ihre Nachricht.")
    assert "Ich bin bis zum 14. September nicht erreichbar." in text


def test_urlaubstext_endet_an_der_naechsten_ueberschrift():
    """Ohne diese Grenze haenge der Reklamations-Text mit in der Autoantwort —
    der Kunde bekaeme 'Das tut uns leid' als Abwesenheitsnotiz."""
    assert "Das tut uns leid" not in vorlagen.urlaubstext(BEISPIEL)
    assert "Terminanfrage" not in vorlagen.urlaubstext(BEISPIEL)


@pytest.mark.parametrize(
    "ueberschrift",
    ["## Urlaub", "## urlaub", "##   URLAUB  ", "## Abwesenheit", "## Abwesenheitsnotiz",
     "## Urlaubsantwort"],
)
def test_ueberschrift_wird_unabhaengig_von_schreibweise_gefunden(ueberschrift):
    md = f"{ueberschrift}\nIch bin weg.\n"
    assert vorlagen.urlaubstext(md) == "Ich bin weg."


def test_fehlender_abschnitt_liefert_leer():
    md = "## Terminanfrage\nGuten Tag.\n\n## Reklamation\nSchade.\n"
    assert vorlagen.urlaubstext(md) == ""


def test_leere_datei_liefert_leer():
    assert vorlagen.urlaubstext("") == ""
    assert vorlagen.urlaubstext(None) == ""


def test_abschnitt_mit_nur_leerzeilen_gilt_als_leer():
    """Eine Ueberschrift ohne Text darf NICHT als "Vorlage vorhanden" zaehlen —
    sonst ginge eine leere Mail raus."""
    assert vorlagen.urlaubstext("## Urlaub\n\n   \n\n## Anderes\nText\n") == ""


def test_abschnittsnamen_listet_alle_ueberschriften():
    assert vorlagen.abschnittsnamen(BEISPIEL) == ["Terminanfrage", "Urlaub", "Reklamation"]


def test_einzelne_raute_ist_keine_vorlagen_ueberschrift():
    """`# Vorlagen` (eine Raute) ist die Dateiueberschrift, keine Vorlage."""
    assert "Vorlagen" not in vorlagen.abschnittsnamen(BEISPIEL)


def test_drei_rauten_zaehlen_nicht_als_vorlage():
    md = "### Urlaub\nIch bin weg.\n"
    assert vorlagen.urlaubstext(md) == ""


# --------------------------------------------------------------------------
# Anrede + Grussformel ergaenzen (Befund aus dem Live-Test 2026-08-23)
# --------------------------------------------------------------------------


def test_kernvorlage_bekommt_anrede_und_gruss():
    """Genau der Fall, der im scharfen Live-Test hinausging: die Vorlage ist nur
    der Textbaustein, beim Empfaenger begann die Mail mitten im Satz."""
    fertig = vorlagen.als_vollstaendige_mail(
        "vielen Dank für Ihre Nachricht. Ich bin derzeit nicht im Haus.",
        "Enver Shala",
    )
    zeilen = [z for z in fertig.splitlines() if z.strip()]
    assert zeilen[0] == "Guten Tag,"
    assert zeilen[1].startswith("vielen Dank")
    assert zeilen[-2] == "Freundliche Grüße"
    assert zeilen[-1] == "Enver Shala"


def test_leerzeile_zwischen_anrede_und_text():
    """Ohne Leerzeile klebte die Anrede am Text — im Mail-Programm sieht das
    nach einem Fehler aus, nicht nach einer Anrede."""
    fertig = vorlagen.als_vollstaendige_mail("Ich bin weg.", "")
    assert fertig.startswith("Guten Tag,\n\nIch bin weg.")


def test_vorlage_mit_eigener_anrede_bleibt_unberuehrt():
    """Idempotenz — sonst entsteht "Guten Tag," ueber "Sehr geehrte Damen…"."""
    eigen = "Sehr geehrte Damen und Herren,\n\nich bin im Urlaub.\n\nViele Grüße\nEnver"
    assert vorlagen.als_vollstaendige_mail(eigen, "Enver Shala") == eigen


@pytest.mark.parametrize(
    "anrede",
    ["Hallo,", "Guten Tag", "Guten Morgen!", "Moin,", "Liebe Kundin, lieber Kunde,",
     "Servus,", "Sehr geehrte Frau Müller,", "Hi,", "Grüß Gott,"],
)
def test_bekannte_anredeformen_werden_erkannt(anrede):
    text = f"{anrede}\n\nich bin im Urlaub."
    fertig = vorlagen.als_vollstaendige_mail(text, "")
    assert not fertig.startswith("Guten Tag,\n\n" + anrede)
    assert vorlagen.hat_anrede(text)


@pytest.mark.parametrize(
    "gruss",
    ["Mit freundlichen Grüßen", "Freundliche Grüße", "Viele Grüße", "Beste Grüße",
     "Herzliche Grüße", "Grüße", "MfG", "LG", "Kind regards", "Bis bald"],
)
def test_bekannte_grussformeln_werden_erkannt(gruss):
    text = f"Hallo,\n\nich bin im Urlaub.\n\n{gruss}\nEnver"
    assert vorlagen.hat_gruss(text)
    assert vorlagen.als_vollstaendige_mail(text, "Enver Shala") == text


def test_gruss_mitten_im_text_zaehlt_nicht():
    """`hat_gruss` schaut nur ans Ende. Eine Vorlage, die im Fliesstext
    "viele Grüße an das Team" erwaehnt, braucht trotzdem eine Grussformel."""
    text = (
        "Hallo,\n\nich bin im Urlaub, viele Grüße an das Team.\n"
        "Zeile drei.\nZeile vier.\nZeile fünf.\nZeile sechs."
    )
    assert not vorlagen.hat_gruss(text)
    assert vorlagen.als_vollstaendige_mail(text, "").endswith("Freundliche Grüße")


def test_ohne_absendername_kein_leerer_namensplatz():
    fertig = vorlagen.als_vollstaendige_mail("Ich bin weg.", "")
    assert fertig.endswith("Freundliche Grüße")
    assert not fertig.endswith("\n")


def test_leere_vorlage_bleibt_leer():
    """DER Riegel: aus "keine Vorlage" darf NIE eine versandfaehige Mail
    entstehen. `urlaub.run_urlaubsantwort` prueft zwar die Vorlage selbst, aber
    diese Funktion darf die Entscheidung nicht unterlaufen koennen."""
    assert vorlagen.als_vollstaendige_mail("", "Enver Shala") == ""
    assert vorlagen.als_vollstaendige_mail("   \n\n  ", "Enver Shala") == ""
