"""Download aus dem privaten Dist-Repo (260728-m8g, Stufe 2, D-1/D-2).

**Warum ein SEPARATES privates Repo:** Der Kunden-Token
(`VIZPATCH_DIST_TOKEN`) gibt damit nur Zugriff auf genau die Artefakte, die
auf dem Kundenserver ohnehin schon laufen — wer ihn stiehlt, gewinnt nichts.
Ein Token auf das Quellrepo `EnverShala/vizpatch` wuerde dagegen Historie,
Planungsdokumente und ALLE vergangenen Releases oeffnen (Quellcode liegt im
Klartext in den Images, siehe CONTEXT.md Scouting-Fakt 7). Deshalb ein
zweites, ausschliesslich Release-Assets tragendes Repo (Default
`EnverShala/vizpatch-dist`), das der Kunde nie direkt ansieht.

**Der oeffentliche Versions-Index aus Stufe 1 (`update_check.py`) bleibt die
ERKENNUNGS-Quelle** und wird hier NICHT angefasst — dieses Modul ist
ausschliesslich der DOWNLOAD-Weg, nachdem der Betreiber bereits weiss, dass
und auf welche Version er updaten will.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_DIST_REPO = "EnverShala/vizpatch-dist"
_GIB = 1024 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024  # 1 MB


def dist_repo() -> str:
    """`${VAR:-}`-Falle (siehe `ADDIN_FRAME_ANCESTORS`/`update_check.index_url`):
    ein gesetzter, aber leerer/whitespace Env-Wert darf den Default nicht
    ueberschreiben."""
    return (os.getenv("VIZPATCH_DIST_REPO") or "").strip() or _DEFAULT_DIST_REPO


def read_dist_token() -> str:
    """Env hat Vorrang; Fallback auf die WebUI-Root-`.env`
    (`config_io.read_env_raw()`), damit ein beim Paketbau vorbefuellter Token
    (D-2) auch ohne Compose-Env-Passthrough gefunden wird. Jeder Fehler beim
    `.env`-Lesen -> leerer String, NIE eine Exception."""
    env_val = os.getenv("VIZPATCH_DIST_TOKEN")
    if env_val:
        return env_val
    try:
        from . import config_io

        return (config_io.read_env_raw().get("VIZPATCH_DIST_TOKEN") or "").strip()
    except Exception:
        return ""


def asset_names(version: str) -> tuple[str, str]:
    """Exakt die Namen, die `scripts/release.sh` erzeugt
    (`dist/deployment-paket-<VERSION>.zip[.sha256]`) — NICHT unabhaengig
    weiterentwickeln, sonst laeuft der Download hier ins Leere."""
    zip_name = f"deployment-paket-{version}.zip"
    return zip_name, f"{zip_name}.sha256"


def find_release_assets(version: str, token: str) -> tuple[Optional[dict], str]:
    """GET `.../releases/tags/<version>` (bewusst NICHT `latest` — die
    Zielversion kommt aus dem Versions-Index, wir wollen genau diese).
    Rueckgabe `({"zip_id","zip_size","sha_id"}, "ok")` oder
    `(None, "no_token"|"no_access"|"unreachable"|"asset_missing")`. Loggt NUR
    Statuscode/Exception-Typ — niemals Token, niemals Response-Body."""
    if not token:
        return None, "no_token"

    zip_name, sha_name = asset_names(version)
    url = f"https://api.github.com/repos/{dist_repo()}/releases/tags/{version}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vizpatch-webui",
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as e:
        logger.warning(
            "update_dist_fetch_failed", extra={"exception_type": type(e).__name__}
        )
        return None, "unreachable"

    if response.status_code in (401, 403, 404):
        logger.warning(
            "update_dist_no_access", extra={"status_code": response.status_code}
        )
        return None, "no_access"
    if response.status_code != 200:
        logger.warning(
            "update_dist_unexpected_status",
            extra={"status_code": response.status_code},
        )
        return None, "unreachable"

    try:
        data = response.json()
    except Exception as e:
        logger.warning(
            "update_dist_parse_failed", extra={"exception_type": type(e).__name__}
        )
        return None, "unreachable"

    assets = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(assets, list):
        return None, "asset_missing"

    zip_id = None
    zip_size = 0
    sha_id = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if name == zip_name:
            zip_id = asset.get("id")
            zip_size = asset.get("size") or 0
        elif name == sha_name:
            sha_id = asset.get("id")

    if zip_id is None or sha_id is None:
        return None, "asset_missing"
    return {"zip_id": zip_id, "zip_size": zip_size, "sha_id": sha_id}, "ok"


def _write_stream(response: httpx.Response, dest: Path, on_progress) -> None:
    total = 0
    try:
        total = int(response.headers.get("content-length") or 0)
    except ValueError:
        total = 0
    done = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        for chunk in response.iter_bytes(chunk_size=_CHUNK_SIZE):
            if not chunk:
                continue
            f.write(chunk)
            done += len(chunk)
            if on_progress is not None:
                on_progress(done, total)


def download_asset(
    asset_id,
    token: str,
    dest,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Zwei Schritte, bewusst explizit:
    (1) GET `.../releases/assets/<asset_id>` mit `Accept:
        application/octet-stream`, `follow_redirects=False`.
    (2) Bei 301/302/307 die `Location` OHNE `Authorization`-Header laden.

    Der Signed-Storage-Host (S3 o.ae.) lehnt Requests ab, die zusaetzlich einen
    `Authorization`-Header tragen — und der Token darf ohnehin nie an einen
    fremden Host gehen (T-m8g-04). Deshalb wird die Weiterleitung MANUELL
    aufgeloest statt `follow_redirects=True` zu vertrauen (das wuerde den
    Header stillschweigend mitschicken).

    Schreibt in 1-MB-Chunks (kein `response.content`/Full-Read), ruft
    `on_progress(bytes_done, total)` nach jedem Chunk auf (der Aufrufer darf
    drosseln). `total` aus `Content-Length`, 0 wenn unbekannt.
    """
    dest = Path(dest)
    url = f"https://api.github.com/repos/{dist_repo()}/releases/assets/{asset_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/octet-stream",
        "User-Agent": "vizpatch-webui",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=30.0) as client:
        with client.stream(
            "GET", url, headers=headers, follow_redirects=False
        ) as response:
            if response.status_code in (301, 302, 307):
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError(
                        "Asset-Download: Redirect ohne Location-Header."
                    )
                # KEIN Authorization-Header auf diesem zweiten Request — siehe
                # Docstring. Bewusst ein frischer, headerloser Aufruf.
                with client.stream("GET", location) as redirected:
                    if redirected.status_code != 200:
                        raise RuntimeError(
                            f"Asset-Download (Redirect) Status {redirected.status_code}"
                        )
                    _write_stream(redirected, dest, on_progress)
                return
            if response.status_code != 200:
                raise RuntimeError(f"Asset-Download Status {response.status_code}")
            _write_stream(response, dest, on_progress)


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def parse_sha256_file(text: Optional[str]) -> Optional[str]:
    """Erstes Token der ersten nicht-leeren Zeile, wenn es 64 Hex-Zeichen sind
    (deckt `sha256sum`-Format mit `  name`/`*name` sowie einen nackten Hash
    ab). Alles andere -> None."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        token = line.split()[0]
        token = token.lstrip("*")
        if _SHA256_RE.match(token):
            return token
        return None
    return None


def sha256_of_file(path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_sha256(path, expected: Optional[str]) -> bool:
    """Case-insensitiver Vergleich ueber `hmac.compare_digest` (Timing-sicher,
    auch wenn hier kein echtes Secret verglichen wird — Konsistenz mit dem
    Rest des Projekts)."""
    if not expected:
        return False
    actual = sha256_of_file(path)
    return hmac.compare_digest(actual.lower(), expected.strip().lower())


def is_safe_zip_member(name: str) -> bool:
    """Zip-Slip-Schutz (T-m8g-03, pure Funktion): lehnt absolute Pfade,
    Laufwerksbuchstaben, Backslashes und `..`-Segmente ab."""
    if not name:
        return False
    if name.startswith("/") or name.startswith("\\"):
        return False
    if "\\" in name:
        return False
    if ":" in name:  # Laufwerksbuchstabe, z. B. "C:\..."
        return False
    parts = name.split("/")
    if ".." in parts:
        return False
    return True


def extract_zip(zip_path, target_dir) -> Path:
    """Prueft ALLE Mitglieder VOR dem Entpacken (Zip-Slip-Schutz, T-m8g-03) —
    wirft `ValueError` beim ersten unsicheren Namen, OHNE vorher irgendetwas
    geschrieben zu haben. Entpackt danach und liefert das Wurzelverzeichnis
    (`target_dir / "deployment-paket-<version>"`, abgeleitet aus dem
    Zip-Dateinamen — siehe `asset_names`), inklusive Existenzpruefung."""
    zip_path = Path(zip_path)
    target_dir = Path(target_dir)

    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not is_safe_zip_member(name):
                raise ValueError(f"Unsicherer Zip-Eintrag (Zip-Slip): {name}")
        zf.extractall(target_dir)

    root = target_dir / zip_path.stem  # "deployment-paket-<version>"
    if not root.is_dir():
        raise FileNotFoundError(
            f"Erwartetes Wurzelverzeichnis nach dem Entpacken fehlt: {root}"
        )
    return root


def has_enough_space(free_bytes: int, asset_bytes: int) -> bool:
    """ZIP + entpackte Tarballs + `docker load` brauchen gleichzeitig Platz —
    ein Update, das mitten im `docker load` an ENOSPC scheitert, ist der
    unangenehmste Fall auf einem kleinen Kundenserver. Verlangt `free >=
    2.5 * asset_bytes`; bei unbekannter Groesse (`asset_bytes == 0`) gilt eine
    Mindestforderung von 2 GiB."""
    if asset_bytes <= 0:
        required = 2 * _GIB
    else:
        required = 2.5 * asset_bytes
    return free_bytes >= required
