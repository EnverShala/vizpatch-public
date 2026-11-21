import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

import docker
from docker.errors import APIError, NotFound

logger = logging.getLogger(__name__)

AGENT_CONTAINER_NAME = "vizpatch-agent"
WEBUI_CONTAINER_NAME = "vizpatch-webui"
COMPOSE_DIR = os.getenv("WEBUI_COMPOSE_DIR", "/config")

_client = None


def _get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def get_agent_status() -> dict:
    try:
        container = _get_client().containers.get(AGENT_CONTAINER_NAME)
        return {
            "state": container.status,
            "started_at": container.attrs["State"]["StartedAt"],
            "container_name": AGENT_CONTAINER_NAME,
        }
    except NotFound:
        return {"state": "not_created", "started_at": None, "container_name": AGENT_CONTAINER_NAME}
    except APIError as e:
        return {"state": "error", "started_at": None, "container_name": AGENT_CONTAINER_NAME, "error": str(e)}


def stop_and_remove_agent() -> dict:
    try:
        container = _get_client().containers.get(AGENT_CONTAINER_NAME)
    except NotFound:
        return {"ok": True, "note": "container did not exist"}
    try:
        container.stop(timeout=15)
    except APIError as e:
        logger.warning("agent_stop_failed", extra={"error": str(e)})
    try:
        container.remove(force=True)
    except APIError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "removed": AGENT_CONTAINER_NAME}


def get_webui_image_tag() -> str | None:
    """Liest das Image-Tag des EIGENEN WebUI-Containers (260728-irn, D-4). Genutzt
    von `update_check.resolve_installed_version()`, um die installierte
    Produktversion abzuleiten — der ausgelieferte Compose schreibt die Image-Tags
    beim Paketbau auf die Release-Version um (`scripts/build-deployment-package.sh`).
    Gleiche „nie propagieren"-Konvention wie `get_agent_status`: JEDER Docker-Fehler
    liefert `None`, nie eine Exception."""
    try:
        container = _get_client().containers.get(WEBUI_CONTAINER_NAME)
        image = container.attrs.get("Config", {}).get("Image")
        if image:
            return image
        tags = container.image.tags
        return tags[0] if tags else None
    except Exception:
        # NotFound/APIError/KeyError und jeder sonstige Docker-Fehler -> None,
        # nie propagieren (gleiche Konvention wie get_agent_status).
        return None


UPDATER_CONTAINER_NAME = "vizpatch-updater"


def parse_self_layout(attrs: dict) -> Optional[dict]:
    """Liest aus einem `docker inspect`-Dict (des EIGENEN WebUI-Containers) den
    Host-Pfad des `/compose`-Mounts, den Volume-NAMEN des `/data`-Mounts und
    den Compose-Projektnamen. Pure Funktion, testbar ohne Docker.

    **Das ist die gefaehrlichste Stelle des ganzen Features (260728-m8g,
    T-m8g-09):** Bind-Mount-Quellpfade werden vom Docker-Daemon auf dem HOST
    aufgeloest. Wuerde der Sidecar das Deploy-Verzeichnis unter einem anderen
    Pfad sehen (z. B. `/deploy`) und Compose von dort starten, wuerde Docker
    Container mit dem nicht existierenden Host-Pfad `/deploy/config` anlegen —
    der Stack waere zerstoert. Deshalb wird der HOST-Pfad ermittelt und der
    Sidecar spaeter auf GENAU DIESEN identischen Pfad gemountet
    (`build_updater_run_args`)."""
    mounts = attrs.get("Mounts") or []
    host_deploy_dir = None
    data_volume = None
    for mount in mounts:
        destination = mount.get("Destination")
        if destination == "/compose":
            host_deploy_dir = mount.get("Source")
        elif destination == "/data":
            data_volume = mount.get("Name") or mount.get("Source")

    if not host_deploy_dir:
        return None

    labels = ((attrs.get("Config") or {}).get("Labels")) or {}
    project = labels.get("com.docker.compose.project") or Path(host_deploy_dir).name

    return {"host_deploy_dir": host_deploy_dir, "data_volume": data_volume, "project": project}


def get_self_layout() -> Optional[dict]:
    """Docker-Huelle um `parse_self_layout` — jeder Docker-Fehler -> None
    (Konvention wie `get_webui_image_tag`)."""
    try:
        container = _get_client().containers.get(WEBUI_CONTAINER_NAME)
        return parse_self_layout(container.attrs)
    except Exception:
        return None


def build_updater_run_args(
    layout: dict,
    image: str,
    source_dir,
    version_to: str,
    version_from: str,
    docker_gid: Optional[str] = None,
) -> list[str]:
    """Baut die `docker run`-Argv fuer den Updater-Sidecar (pure Funktion).
    Der Host-Deploy-Pfad wird auf den IDENTISCHEN Pfad im Sidecar gemountet
    (siehe `parse_self_layout`-Docstring), das Datenvolumen per NAME auf
    `/data`. Bewusst OHNE `--rm` (damit `docker logs vizpatch-updater` nach
    dem Lauf noch auswertbar ist) und OHNE `--restart` (der Sidecar soll
    genau einmal laufen, kein Neustart bei Absturz)."""
    host_deploy_dir = layout["host_deploy_dir"]
    project = layout["project"]
    data_volume = layout.get("data_volume")

    args = [
        "docker", "run", "-d", "--name", UPDATER_CONTAINER_NAME,
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", f"{host_deploy_dir}:{host_deploy_dir}:rw",
    ]
    if data_volume:
        args += ["-v", f"{data_volume}:/data:rw"]
    if docker_gid:
        args += ["--group-add", str(docker_gid)]
    args += [
        "--entrypoint", "python3",
        image,
        "-m", "src.updater",
        "--source", str(source_dir),
        "--deploy", host_deploy_dir,
        "--project", project,
        "--to", version_to,
        "--from", version_from,
    ]
    return args


def load_image_tarball(path) -> tuple[bool, str]:
    """`docker load -i <path>` (Muster wie `control_agent`s Compose-Aufruf).
    Timeout 600s — grosse Image-Tarballs auf einem kleinen Kundenserver."""
    try:
        result = subprocess.run(
            ["docker", "load", "-i", str(path)],
            capture_output=True, text=True, timeout=600, check=False,
        )
        return result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def image_exists(tag: str) -> bool:
    """`docker image inspect` ueber das SDK; jeder Fehler -> False (Konvention)."""
    try:
        _get_client().images.get(tag)
        return True
    except Exception:
        return False


def remove_container_quietly(name: str) -> None:
    """Entfernt Vorlauf-Reste eines abgebrochenen Updates (z. B. ein
    haengengebliebener `vizpatch-updater`-Container). Fehler werden ignoriert —
    das ist ein Best-Effort-Aufraeumschritt, kein kritischer Pfad."""
    try:
        container = _get_client().containers.get(name)
        container.remove(force=True)
    except Exception:
        pass


def start_updater(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
        return result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def docker_socket_gid() -> Optional[str]:
    """Auf dem Testhost hat der Socket GID 1001, nicht 999 (bekannte Falle aus
    dem Scouting) — deshalb wird die tatsaechliche GID gelesen statt eines
    hartkodierten Defaults."""
    try:
        return str(os.stat("/var/run/docker.sock").st_gid)
    except Exception:
        return None


def control_agent(action: str) -> dict:
    if action not in {"start", "stop", "restart"}:
        raise ValueError(f"invalid action: {action}")
    try:
        container = _get_client().containers.get(AGENT_CONTAINER_NAME)
    except NotFound:
        if action == "start":
            result = subprocess.run(
                ["docker", "compose", "up", "-d", "agent"],
                cwd=COMPOSE_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
            return {"ok": result.returncode == 0, "log": result.stdout + result.stderr}
        return {"ok": False, "error": "container not found"}
    if action == "start":
        container.start()
    elif action == "stop":
        container.stop(timeout=30)
    elif action == "restart":
        container.restart(timeout=30)
    return {"ok": True, "action": action}
