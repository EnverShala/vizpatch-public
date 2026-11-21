"""Tests fuer den Sortierregeln-Parser (Phase 14, Plan 14-01, AUTO-02)."""
from __future__ import annotations

from src.sortierregeln import (
    AKTION_MARKIEREN,
    AKTION_VERSCHIEBEN,
    KATEGORIE_UNZUGEORDNET,
    parse_sortierregeln,
)


def test_leerer_inhalt_liefert_leere_listen():
    kategorien, warnungen = parse_sortierregeln("")
    assert kategorien == []
    assert warnungen == []


def test_none_inhalt_liefert_leere_listen():
    kategorien, warnungen = parse_sortierregeln(None)  # type: ignore[arg-type]
    assert kategorien == []
    assert warnungen == []


def test_verschieben_kategorie_mit_ziel():
    inhalt = "## Rechnungen\nAktion: verschieben\nZiel: Rechnungen\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert warnungen == []
    assert kategorien == [{"name": "Rechnungen", "aktion": AKTION_VERSCHIEBEN, "ziel": "Rechnungen"}]


def test_markieren_kategorie_ohne_ziel():
    inhalt = "## Notfälle\nAktion: markieren\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert warnungen == []
    assert kategorien == [{"name": "Notfälle", "aktion": AKTION_MARKIEREN, "ziel": ""}]


def test_markieren_kategorie_ignoriert_angegebenes_ziel():
    """Ziel-Feld bei 'markieren' wird geparst, aber bewusst NICHT übernommen
    (AUTO-02: Markieren bewegt nichts, ein Ziel wäre irreführend)."""
    inhalt = "## Notfälle\nAktion: markieren\nZiel: Irgendwas\n"
    kategorien, _ = parse_sortierregeln(inhalt)
    assert kategorien == [{"name": "Notfälle", "aktion": AKTION_MARKIEREN, "ziel": ""}]


def test_verschieben_ohne_ziel_wird_uebersprungen():
    inhalt = "## Rechnungen\nAktion: verschieben\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert kategorien == []
    assert len(warnungen) == 1
    assert "Rechnungen" in warnungen[0] and "Ziel" in warnungen[0]


def test_unbekannte_aktion_wird_uebersprungen():
    inhalt = "## Spam\nAktion: löschen\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert kategorien == []
    assert len(warnungen) == 1
    assert "unbekannt" in warnungen[0]


def test_fehlende_aktion_wird_uebersprungen():
    inhalt = "## Rechnungen\nZiel: Rechnungen\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert kategorien == []
    assert len(warnungen) == 1


def test_reservierter_name_wird_uebersprungen():
    inhalt = f"## {KATEGORIE_UNZUGEORDNET}\nAktion: verschieben\nZiel: Sonstiges\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert kategorien == []
    assert "reserviert" in warnungen[0]


def test_reservierter_name_ist_case_insensitiv():
    inhalt = "## Unzugeordnet\nAktion: markieren\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert kategorien == []
    assert "reserviert" in warnungen[0]


def test_doppelter_name_erste_zaehlt():
    inhalt = (
        "## Rechnungen\nAktion: verschieben\nZiel: Rechnungen-Neu\n\n"
        "## Rechnungen\nAktion: markieren\n"
    )
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert kategorien == [{"name": "Rechnungen", "aktion": AKTION_VERSCHIEBEN, "ziel": "Rechnungen-Neu"}]
    assert len(warnungen) == 1
    assert "mehrfach" in warnungen[0]


def test_kategorie_ohne_namen_wird_uebersprungen():
    inhalt = "##   \nAktion: verschieben\nZiel: X\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert kategorien == []
    assert len(warnungen) == 1


def test_mehrere_kategorien_gemischt():
    inhalt = (
        "## Rechnungen\n"
        "Aktion: verschieben\n"
        "Ziel: Rechnungen\n\n"
        "## Notfälle\n"
        "Aktion: markieren\n\n"
        "## Newsletter\n"
        "Aktion: verschieben\n"
        "Ziel: Newsletter\n"
    )
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert warnungen == []
    assert kategorien == [
        {"name": "Rechnungen", "aktion": AKTION_VERSCHIEBEN, "ziel": "Rechnungen"},
        {"name": "Notfälle", "aktion": AKTION_MARKIEREN, "ziel": ""},
        {"name": "Newsletter", "aktion": AKTION_VERSCHIEBEN, "ziel": "Newsletter"},
    ]


def test_bullet_prefix_vor_feldern_wird_toleriert():
    """Betreiber schreiben evtl. '- Aktion: verschieben' (Markdown-Liste)."""
    inhalt = "## Rechnungen\n- Aktion: verschieben\n- Ziel: Rechnungen\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert warnungen == []
    assert kategorien == [{"name": "Rechnungen", "aktion": AKTION_VERSCHIEBEN, "ziel": "Rechnungen"}]


def test_aktion_gross_klein_geschrieben_wird_toleriert():
    inhalt = "## Rechnungen\nAKTION: VERSCHIEBEN\nZIEL: Rechnungen\n"
    kategorien, warnungen = parse_sortierregeln(inhalt)
    assert warnungen == []
    assert kategorien[0]["aktion"] == AKTION_VERSCHIEBEN
