"""Parser fuer sortierregeln.md (Phase 14, AUTO-02) — Betreiber-konfigurierte
Sortier-Kategorien fuer das automatische Einsortieren beim Polling.

BEWUSST identisch in agent/src/sortierregeln.py und webui/src/sortierregeln.py
(dieselbe Nicht-Entscheidung wie bei pii.py/llm.py, siehe crypto_sync.py-
Familie): eine reine Text-zu-Struktur-Funktion ohne I/O und ohne
service-spezifische Abhaengigkeiten (kein agents_io-Import, kein config-
Import) — genau deshalb kann sie ALS EINZIGES von den drei Bausteinen dieser
Phase (sortierregeln.py / action_journal.py / auto_sort.py) tatsaechlich
Byte-identisch bleiben; siehe agent/src/action_journal.py-Docstring fuer die
Begruendung, warum dort ein anderer Weg gewaehlt wurde. Sync-Guard:
test_sortierregeln_sync.py (in BEIDEN Suiten).

Format (Markdown, Muster wie vorlagen.md/agents_io):
    ## <Kategoriename>
    Aktion: verschieben
    Ziel: <Zielordner>

    ## <Kategoriename 2>
    Aktion: markieren

Zwei Aktionsarten (AUTO-02): 'verschieben' braucht ein nicht-leeres 'Ziel',
'markieren' NICHT (die Mail bleibt liegen und wird nur hervorgehoben — ein
Notfall soll sichtbar werden, nicht in einem Unterordner verschwinden).

Jede strukturell ungueltige Kategorie (unbekannte/fehlende Aktion, fehlendes
Ziel bei 'verschieben', reservierter oder doppelt vergebener Name) wird STILL
uebersprungen, NIE crashend — der Grund landet als deutscher Klartext-Hinweis
in der zurueckgegebenen Warnungsliste, damit der Aufrufer ihn loggen kann
(Prinzip 'keine Mail verschwindet still', 13-07-SUMMARY.md, hier uebertragen
auf 'keine kaputte Regel verschwindet still')."""
from __future__ import annotations

import re

AKTION_VERSCHIEBEN = "verschieben"
AKTION_MARKIEREN = "markieren"
GUELTIGE_AKTIONEN = (AKTION_VERSCHIEBEN, AKTION_MARKIEREN)

# Reserviert wie KATEGORIE_UNZUGEORDNET in webui/src/chat_tools.py (mails_sichten,
# Phase 13) — verhindert, dass eine Betreiber-Kategorie mit dem Auffangkorb fuer
# nicht zuordenbare Mails kollidiert. Eigene Konstante (kein Import aus
# chat_tools.py): dieses Modul darf keine service-spezifische Abhaengigkeit
# bekommen, siehe Modul-Docstring.
KATEGORIE_UNZUGEORDNET = "unzugeordnet"

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FELD_RE = re.compile(r"^\s*[-*]?\s*(Aktion|Ziel)\s*:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_sortierregeln(content: str) -> tuple[list[dict], list[str]]:
    """Parst den Inhalt von sortierregeln.md in eine Liste gueltiger
    Kategorien `{"name", "aktion", "ziel"}` plus eine Liste deutscher
    Warnungen fuer uebersprungene Eintraege.

    `ziel` ist bei `aktion == "markieren"` immer ein leerer String (das Ziel
    aus der Datei wird fuer diese Aktionsart ignoriert — Markieren bewegt
    nichts). Leerer/fehlender Inhalt -> ([], [])."""
    text = content or ""
    heading_matches = list(_HEADING_RE.finditer(text))
    kategorien: list[dict] = []
    warnungen: list[str] = []
    gesehen: set[str] = set()

    for i, m in enumerate(heading_matches):
        name = m.group(1).strip()
        start = m.end()
        end = heading_matches[i + 1].start() if i + 1 < len(heading_matches) else len(text)
        block = text[start:end]

        if not name:
            warnungen.append("Eine Kategorie ohne Namen wurde übersprungen.")
            continue

        name_key = name.casefold()
        if name_key == KATEGORIE_UNZUGEORDNET:
            warnungen.append(
                f"Kategorie '{name}' ist reserviert (Auffangkorb für nicht "
                f"zuordenbare Mails) und wurde übersprungen."
            )
            continue
        if name_key in gesehen:
            warnungen.append(f"Kategorie '{name}' ist mehrfach vergeben — nur die erste zählt.")
            continue

        felder: dict[str, str] = {}
        for feld_match in _FELD_RE.finditer(block):
            key = feld_match.group(1).strip().casefold()
            felder.setdefault(key, feld_match.group(2).strip())

        aktion = felder.get("aktion", "").strip().casefold()
        if aktion not in GUELTIGE_AKTIONEN:
            warnungen.append(
                f"Kategorie '{name}': Aktion '{felder.get('aktion', '')}' ist "
                f"unbekannt (erlaubt: {', '.join(GUELTIGE_AKTIONEN)}) — übersprungen."
            )
            continue

        ziel = felder.get("ziel", "").strip()
        if aktion == AKTION_VERSCHIEBEN and not ziel:
            warnungen.append(
                f"Kategorie '{name}': Aktion 'verschieben' braucht ein Ziel — übersprungen."
            )
            continue

        gesehen.add(name_key)
        kategorien.append(
            {"name": name, "aktion": aktion, "ziel": ziel if aktion == AKTION_VERSCHIEBEN else ""}
        )

    return kategorien, warnungen
