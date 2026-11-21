"""Add-in-Update-Check via oeffentlichem Versions-Index (260728-irn, Stufe 1).

Backend-Proxy fuer `GET /addin/update-check`: liest server-seitig die aktuelle
Vizpatch-Version aus einem **oeffentlichen** Versions-Index und vergleicht sie
semver-basiert mit der installierten Produktversion. Reine Logik + ein
gekapselter HTTP-Call (`fetch_latest_version`), damit der Rest ohne Netzwerk
testbar bleibt.

**Warum kein GitHub-API-Call mehr:** Das Produkt-Repo `EnverShala/vizpatch` ist
privat, ein API-Zugriff braucht also einen Token. Der einzige GitHub-Scope, der
`releases/latest` erlaubt (`contents: read`), gibt aber gleichzeitig den
kompletten Quellcode und alle Release-Assets frei — ein solcher Token darf
nicht auf Kundenhardware liegen. Fuer einen Versions-Check muss ohnehin nur die
**Versionsnummer** oeffentlich sein, nicht der Code. Deshalb liegt sie in
`EnverShala/vizpatch-releases/latest.json` (nur Version + Datum, kein Code) und
wird von `scripts/release.sh` beim Release mitgepflegt. Ergebnis: kein Secret
beim Kunden, kein Ablaufdatum, nichts zu rotieren — das Produkt-Repo bleibt
privat.

Die URL ist per `VIZPATCH_VERSION_INDEX_URL` ueberschreibbar, damit der Index
spaeter ohne Codeaenderung auf eine eigene Domain umziehen kann.

**Verzoegerung nach einem Release (kein Fehler):** `raw.githubusercontent.com`
liefert die Datei mit `Cache-Control: max-age=300` ueber ein CDN, dessen
Edge-Knoten unabhaengig voneinander ablaufen. Zwei Rechner koennen daher fuer
kurze Zeit unterschiedliche Versionen sehen — bei der v1.17.0-Abnahme gemessen:
der Host meldete bereits die neue Datei, der Container noch die alte, obwohl
dessen eigener Cache leer war. Zusammen mit `_CACHE_TTL_OK` (15 min) dauert es
also bis zu ~20 Minuten, bis alle Installationen ein neues Release sehen. Fuer
Updates unkritisch; wer sofort pruefen will, startet die WebUI neu (leert
`_cache`) und muss trotzdem die CDN-Frist abwarten.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

from . import docker_ctrl

logger = logging.getLogger(__name__)

# Oeffentlich, anonym lesbar — bewusst KEIN Auth-Header (siehe Modul-Docstring).
_DEFAULT_VERSION_INDEX_URL = (
    "https://raw.githubusercontent.com/EnverShala/vizpatch-releases/main/latest.json"
)

# In-Memory-TTL-Cache ueber dem HTTP-Call. Erfolg laenger cachen als Fehler,
# damit ein vorbeigehender Ausfall nicht 15 Minuten lang "kein Update" meldet.
_CACHE_TTL_OK = 900
_CACHE_TTL_FAIL = 60

# Modul-globaler Cache: {"version": str | None, "status": str, "expires_at": float}
_cache: dict | None = None


def reset_cache() -> None:
    """Fuer Tests: verhindert, dass der Modul-Cache zwischen Testfaellen
    durchschlaegt."""
    global _cache
    _cache = None


def index_url() -> str:
    """Erlaubt den Umzug des Versions-Index auf eine eigene Domain, ohne Code zu
    aendern. Leere/whitespace-Env faellt auf den Default zurueck (das
    `${VAR:-}`-Muster der Compose liefert einen leeren String, nicht `None` —
    dieselbe Falle wie bei `ADDIN_FRAME_ANCESTORS`)."""
    return (os.getenv("VIZPATCH_VERSION_INDEX_URL") or "").strip() or _DEFAULT_VERSION_INDEX_URL


def parse_version(raw: str | None) -> tuple[int, int, int] | None:
    """Akzeptiert `vX.Y.Z` und `X.Y.Z`, ignoriert ein Prerelease-/Build-Suffix
    nach `-`/`+`. Gibt bei allem anderen `None` zurueck — nie eine Exception."""
    if not raw:
        return None
    s = raw.strip()
    if s.lower().startswith("v"):
        s = s[1:]
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts = s.split(".")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def compare_versions(installed: str | None, latest: str | None) -> int | None:
    """Semver-Tupel-Vergleich (D-4: `v1.9.0` < `v1.15.0`, KEIN String-Vergleich).
    `-1/0/1`, `None` wenn eine Seite nicht parsebar ist."""
    a = parse_version(installed)
    b = parse_version(latest)
    if a is None or b is None:
        return None
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def resolve_installed_version() -> str | None:
    """Aufloesungsreihenfolge: (a) `VIZPATCH_VERSION`-Env falls gesetzt und
    parsebar, (b) Image-Tag des eigenen WebUI-Containers, (c) `None`.

    Der ausgelieferte Compose schreibt die Image-Tags beim Paketbau auf die
    Release-Version um (`scripts/build-deployment-package.sh`); im Dev-Baum ist
    der eingecheckte Tag dagegen veraltet — deshalb hat die explizite
    Env-Angabe Vorrang. Ein nicht-semver Dev-Tag (z. B. `v1.11-dev`) fuehrt
    bewusst zu `None` (-> `unknown_installed`) statt zu einer Falschmeldung.
    """
    env_version = os.getenv("VIZPATCH_VERSION")
    if env_version and parse_version(env_version) is not None:
        return env_version

    try:
        tag = docker_ctrl.get_webui_image_tag()
    except Exception:
        tag = None
    if tag and ":" in tag:
        candidate = tag.rsplit(":", 1)[-1]
        if parse_version(candidate) is not None:
            return candidate
    return None


def fetch_latest_version() -> tuple[str | None, str]:
    """Liest `latest.json` vom oeffentlichen Versions-Index.
    Rueckgabe `(version, status)` mit `status` in `ok|no_access|unreachable`.

    Bewusst ohne Auth-Header — der Index ist oeffentlich. TLS-Verifikation
    bleibt aktiv (httpx-Default, `verify` wird NICHT abgeschaltet). Fehler
    werden nur mit Statuscode/Exception-Typ geloggt, nie mit Response-Body.
    """
    headers = {"Accept": "application/json", "User-Agent": "vizpatch-webui"}
    try:
        with httpx.Client(timeout=6.0, follow_redirects=True) as client:
            response = client.get(index_url(), headers=headers)
    except httpx.HTTPError as e:
        logger.warning(
            "update_check_fetch_failed", extra={"exception_type": type(e).__name__}
        )
        return None, "unreachable"

    if response.status_code in (401, 403, 404):
        # Bei einem oeffentlichen Index heisst das praktisch immer: Datei fehlt
        # oder URL falsch — nicht "Zugriff verweigert" im Auth-Sinn.
        logger.warning(
            "update_check_index_missing", extra={"status_code": response.status_code}
        )
        return None, "no_access"
    if response.status_code != 200:
        logger.warning(
            "update_check_unexpected_status",
            extra={"status_code": response.status_code},
        )
        return None, "unreachable"

    try:
        data = response.json()
        version = data.get("version") if isinstance(data, dict) else None
    except Exception as e:
        logger.warning(
            "update_check_parse_failed", extra={"exception_type": type(e).__name__}
        )
        return None, "unreachable"
    if not version or not isinstance(version, str):
        return None, "unreachable"
    return version, "ok"


def build_update_status() -> dict:
    """Orchestriert den gecachten Index-Call und den Semver-Vergleich.
    Liefert IMMER ein vollstaendiges Dict
    `{"status", "installed", "latest", "update_available", "message"}` — nie
    eine Exception (der Aufrufer in `main.py` faengt dennoch defensiv ab)."""
    global _cache

    installed = resolve_installed_version()

    now = time.monotonic()
    if _cache is not None and _cache["expires_at"] > now:
        latest, fetch_status = _cache["version"], _cache["status"]
    else:
        latest, fetch_status = fetch_latest_version()
        ttl = _CACHE_TTL_OK if fetch_status == "ok" else _CACHE_TTL_FAIL
        _cache = {"version": latest, "status": fetch_status, "expires_at": now + ttl}

    if fetch_status == "no_access":
        return {
            "status": "no_access",
            "installed": installed,
            "latest": None,
            "update_available": False,
            "message": "Versions-Index nicht gefunden — Update-Prüfung übersprungen.",
        }
    if fetch_status == "unreachable":
        return {
            "status": "unreachable",
            "installed": installed,
            "latest": None,
            "update_available": False,
            "message": "Versions-Index war nicht erreichbar — Update-Prüfung übersprungen.",
        }

    if installed is None:
        return {
            "status": "unknown_installed",
            "installed": None,
            "latest": latest,
            "update_available": False,
            "message": "Installierte Version konnte nicht ermittelt werden.",
        }

    cmp = compare_versions(installed, latest)
    update_available = cmp is not None and cmp < 0
    return {
        "status": "ok",
        "installed": installed,
        "latest": latest,
        "update_available": update_available,
        "message": "",
    }
