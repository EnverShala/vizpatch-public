"""Drift-Guard: BEIDE Services in BEIDEN Compose-Dateien setzen `TZ`.

Die Urlaubs-Autoantwort (URL-01..10) entscheidet am **Kalendertag**, ob sie
greift. Ohne `TZ` läuft ein Container auf UTC, und dann meint er einen anderen
Tag als der Betreiber, der deutsche Daten einträgt.

**Warum beide Services, nicht nur der Agent.** Der Agent entscheidet, ob er
antwortet; die WebUI prüft denselben Zeitraum ein ZWEITES Mal, bevor sie
zustellt (`urlaub_relay.pruefe_und_sende`, fail-closed). Laufen sie in
verschiedenen Zonen, klaffen die beiden Prüfungen an der Tagesgrenze
auseinander: am ersten Urlaubstag zwischen 00:00 und 02:00 deutscher Zeit will
der Agent senden und die WebUI lehnt mit 403 ab. Das heilt sich zwar selbst
(bei Fehlschlag wird kein Merklisten-Eintrag geschrieben, der nächste Zyklus
versucht es erneut), erzeugt aber Fehler im Log für einen Zustand, der keiner
ist. Dazu käme eine Anzeige (`_urlaubslage`), die etwas anderes behauptet als
der Agent tut.

**Warum es diesen Test gibt.** Genau dieser Fehler ist beim Release v1.29.2
passiert: `TZ` war im agent-Service beider Dateien gesetzt, im webui-Service
aber nur in `agent/docker-compose.yml` — und die Kunden bekommen
`deployment/docker-compose.phase4.yml`. Aufgefallen ist es erst bei der
Verifikation des fertigen Pakets. Das ist dieselbe Familie wie die
Compose-Passthrough-Befunde aus Phase 13 (`test_compose_env_sync.py`), nur für
eine Variable, die kein `ENABLE_`/`MAX_`-Präfix trägt und deshalb von jenem
Scanner nicht erfasst wird.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_DATEIEN = (
    _REPO_ROOT / "agent" / "docker-compose.yml",
    _REPO_ROOT / "deployment" / "docker-compose.phase4.yml",
)

# Service-Kopf auf Einrückungsebene 2 ("  agent:"), bis zum nächsten.
_SERVICE_KOPF = re.compile(r"^  ([a-z][a-z0-9_-]*):\s*$", re.MULTILINE)


def _service_bloecke(text: str) -> dict[str, str]:
    """Zerlegt eine Compose-Datei in {service_name: block}. Bewusst simpel und
    ohne YAML-Parser: der Test soll auch dann laufen, wenn kein pyyaml
    installiert ist, und die Struktur dieser zwei Dateien ist flach genug."""
    treffer = list(_SERVICE_KOPF.finditer(text))
    bloecke: dict[str, str] = {}
    for i, m in enumerate(treffer):
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
        bloecke[m.group(1)] = text[m.start():ende]
    return bloecke


@pytest.mark.parametrize("pfad", _COMPOSE_DATEIEN, ids=lambda p: p.parent.name)
def test_beide_services_setzen_die_zeitzone(pfad):
    text = pfad.read_text(encoding="utf-8")
    bloecke = _service_bloecke(text)

    # Die zwei Services, die es geben MUSS — findet der Zerleger sie nicht,
    # ist die Datei umgebaut worden und dieser Test stillschweigend wertlos.
    assert {"agent", "webui"} <= set(bloecke), (
        f"{pfad.name}: agent/webui nicht gefunden — Zerleger passt nicht mehr "
        f"zur Datei (gefunden: {sorted(bloecke)})."
    )

    for service in ("agent", "webui"):
        assert "TZ:" in bloecke[service], (
            f"{pfad.name}: Service '{service}' setzt kein TZ. Ohne das laeuft er "
            "auf UTC, waehrend der Betreiber deutsche Urlaubsdaten eintraegt — "
            "beide Services muessen denselben Kalendertag meinen (URL-01..10)."
        )


@pytest.mark.parametrize("pfad", _COMPOSE_DATEIEN, ids=lambda p: p.parent.name)
def test_zeitzone_hat_denselben_default_und_bleibt_ueberschreibbar(pfad):
    """`${TZ:-Europe/Berlin}` — überschreibbar für einen Kunden in einer
    anderen Zone, aber mit dem Default, der hier gemeint ist. Zwei
    unterschiedliche Defaults in einer Datei waeren schlimmer als gar keiner."""
    text = pfad.read_text(encoding="utf-8")
    werte = set(re.findall(r"^\s*TZ:\s*(.+?)\s*$", text, re.MULTILINE))

    assert werte == {"${TZ:-Europe/Berlin}"}, (
        f"{pfad.name}: TZ-Werte weichen ab oder sind hartkodiert: {sorted(werte)}"
    )


def test_der_zerleger_ist_kein_blindgaenger():
    """Gegenprobe: eine Datei mit fehlendem TZ im webui-Service muss auffallen
    — sonst wuerde der Test oben auch eine kaputte Datei durchwinken."""
    kaputt = (
        "services:\n"
        "  agent:\n"
        "    environment:\n"
        "      TZ: ${TZ:-Europe/Berlin}\n"
        "  webui:\n"
        "    environment:\n"
        "      LOG_LEVEL: INFO\n"
    )
    bloecke = _service_bloecke(kaputt)
    assert set(bloecke) == {"agent", "webui"}
    assert "TZ:" in bloecke["agent"]
    assert "TZ:" not in bloecke["webui"]
