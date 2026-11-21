"""updater.py — Sidecar-Logik fuer das Self-Update (260728-m8g, Stufe 2, D-3/D-4).

**Laeuft im Sidecar-Container, NICHT im WebUI-Prozess.** Der Worker in
`main.py` startet diesen Container per `docker run --entrypoint python3 …
-m src.updater …` — bewusst mit dem NEUEN WebUI-Image selbst als Sidecar-Image
(Entwurfsentscheidung, siehe unten), damit er den eigenen Neustart der WebUI
ueberlebt und das Umschalten unabhaengig von ihr durchfuehrt.

**Warum das neue WebUI-Image als Sidecar-Image (bewusste Abweichung von der
urspruenglichen `<specifics>`-Idee eines zusaetzlichen `docker:cli`-Tarballs):**
1. Das Image ist bereits im Paket, bereits `docker load`et und enthaelt laut
   `webui/Dockerfile` schon Docker-CLI **und** Compose-Plugin — kein
   zusaetzlicher Tarball, kein zusaetzlicher Download. "Kein Pull zur
   Update-Zeit" ist damit strenger erfuellt als mit einem weiteren Fremdimage.
2. Die Umschalt-Logik ist damit **Python** statt Shell und laeuft in derselben
   pytest-Suite mit — der Sidecar-Ablauf ist auf dem Entwicklungsrechner sonst
   nicht live testbar (kein Docker-Zugriff hier).
3. Es laeuft immer die Updater-Logik der ZIEL-Version (das neue Image wird VOR
   dem Start des Sidecars per `docker load` geladen). Ein Updater-Bug ist damit
   mit der naechsten Version behebbar statt in der Alt-Installation
   eingefroren.

**Harte Regel (D-4, T-m8g-10):** Kein Codepfad in diesem Modul ruft jemals
`docker compose down` auf (auch nicht mit `-v`) — das wuerde das
`agent-data`-Volume mit dem SQLite-State loeschen. `up -d` genuegt, um
Container auf neue Images umzuziehen; ein Quell-Waechter in
`test_updater.py` belegt das.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from . import update_status
from .logging_setup import setup_logging

logger = logging.getLogger(__name__)

# Env-Overrides existieren ausschliesslich fuer Tests — die Produktionswerte
# entsprechen den in CONTEXT.md/PLAN.md festgelegten Fristen (D-3/D-4).
AGENT_STOP_TIMEOUT_SECONDS = int(os.getenv("AGENT_STOP_TIMEOUT_SECONDS", "60"))
HEALTH_TIMEOUT_SECONDS = int(os.getenv("HEALTH_TIMEOUT_SECONDS", "120"))
ROLLBACK_HEALTH_TIMEOUT_SECONDS = int(os.getenv("ROLLBACK_HEALTH_TIMEOUT_SECONDS", "60"))
HEALTH_POLL_INTERVAL_SECONDS = 5

AGENT_CONTAINER_NAME = "vizpatch-agent"
WEBUI_CONTAINER_NAME = "vizpatch-webui"

_IMAGE_TAG_RE = re.compile(r"^\s*image:\s*(vizpatch(?:-webui)?:\S+)", re.MULTILINE)
_SEMVER_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+$")

# Allowlist statt Skip-Liste: nur GENAU diese Namen werden aus `source_dir`
# ins `deploy_dir` kopiert. `config/`, `.env`, `.secret_key` stehen bewusst
# NICHT hier — sie tragen WEBUI_PASSWORD, Agent-Konfiguration, den
# Fernet-Schluessel bzw. DOCKER_GID/den Dist-Token (T-m8g-10).
_COPY_TARGETS = ("docker-compose.yml", "addin-publish", "prompts", "deployment", "scripts", "README.md")


# --- Reine Entscheidungsfunktionen ------------------------------------------


def read_image_tags(compose_text: str) -> dict[str, str]:
    """Regex je Zeile — bewusst KEIN YAML-Parser, die Datei soll unveraendert
    zurueckschreibbar bleiben (wir lesen nur). Fehlender Tag -> der Key fehlt
    schlicht im Ergebnis-Dict (kein Crash, kein Rateversuch)."""
    tags: dict[str, str] = {}
    for match in _IMAGE_TAG_RE.finditer(compose_text):
        full = match.group(1)
        repo = full.split(":", 1)[0]
        key = "agent" if repo == "vizpatch" else "webui"
        tags[key] = full
    return tags


def verify_compose_tags(compose_text: str, version: str) -> list[str]:
    """Vorpruefung gegen ein fehlgebautes Paket: liefert die Labels
    ("agent"/"webui") der Tags, die NICHT auf `version` stehen."""
    tags = read_image_tags(compose_text)
    expected = {"agent": f"vizpatch:{version}", "webui": f"vizpatch-webui:{version}"}
    return [label for label, tag in expected.items() if tags.get(label) != tag]


def health_verdict(webui_http_ok: bool, agent_state: str, restart_delta: int) -> str:
    """`"healthy" | "pending" | "unhealthy"`. `pending` ist das entscheidende
    dritte Ergebnis — ohne es waere jeder Start-Sekundenbruchteil ein
    Fehlschlag. `unhealthy` bei `exited`/`dead` ODER einem Restart-Zuwachs
    seit dem Umschalten (Restart-Loop, D-4 Buchstabe b)."""
    if agent_state in {"exited", "dead"} or restart_delta > 0:
        return "unhealthy"
    if webui_http_ok and agent_state == "running":
        return "healthy"
    return "pending"


def images_to_prune(existing_tags: list[str], keep: list[str]) -> list[str]:
    """Behaelt die aktuelle UND die Vorgaengerversion (`keep` = Liste von
    VERSIONSSTRINGS, nicht vollen Tags — D-4-Rollback-Ziel), entfernt aeltere.
    Nicht semver-parsbare Tags (`vizpatch:latest`) bleiben grundsaetzlich
    verschont."""
    keep_versions = set(keep)
    prune: list[str] = []
    for tag in existing_tags:
        if ":" not in tag:
            continue
        version = tag.rsplit(":", 1)[-1]
        if version in keep_versions:
            continue
        if not _SEMVER_TAG_RE.match(version):
            continue
        prune.append(tag)
    return prune


def tarballs_to_prune(filenames: list[str], keep: list[str]) -> list[str]:
    """Waehlt aus Deploy-Dateinamen die Image-Tarballs (und deren `.sha256`),
    die zu KEINER der `keep`-Versionen gehoeren.

    `_copy_release` legt `vizpatch-<version>.tar` und
    `vizpatch-webui-<version>.tar` im Deploy-Verzeichnis ab (fuer manuelles
    `docker load` im USB-Modell). Alte Staende blieben bisher liegen: bei der
    v1.18.0-Abnahme lagen dort 531 MB aus drei Updates, ~177 MB je Update. Auf
    einem kleinen Kundenserver laeuft die Platte damit voll.

    `keep` sind VERSIONSSTRINGS (wie bei `images_to_prune`), normalerweise
    aktuelle + Vorgaengerversion — damit bleibt ein manuelles Zurueckgehen
    moeglich. Nicht zuordenbare Dateinamen bleiben grundsaetzlich verschont.
    """
    keep_versions = {v for v in keep if v}
    prune: list[str] = []
    for name in filenames:
        base = name
        if base.endswith(".sha256"):
            base = base[: -len(".sha256")]
        if not base.endswith(".tar"):
            continue
        stem = base[: -len(".tar")]
        for prefix in ("vizpatch-webui-", "vizpatch-"):
            if stem.startswith(prefix):
                version = stem[len(prefix):]
                # Nur eindeutige Semver-Staende anfassen, nie Fremddateien.
                if _SEMVER_TAG_RE.match(version) and version not in keep_versions:
                    prune.append(name)
                break
    return prune


def rewrite_version_env(env_text: str, version: str) -> str:
    """`update_check.resolve_installed_version()` bevorzugt die Env-Angabe vor
    dem Image-Tag — bleibt ein alter Wert stehen, meldet die Installation nach
    dem Update dauerhaft „Update verfuegbar". Ein leerer/fehlender Eintrag
    bleibt bewusst leer/fehlend (der Image-Tag ist dann die
    selbstaktualisierende Quelle)."""
    out_lines = []
    for line in env_text.splitlines(keepends=True):
        body = line.splitlines()[0] if line.splitlines() else line
        if body.startswith("VIZPATCH_VERSION="):
            value = body.split("=", 1)[1]
            if value.strip():
                newline = "\n" if line.endswith("\n") else ""
                out_lines.append(f"VIZPATCH_VERSION={version}{newline}")
                continue
        out_lines.append(line)
    return "".join(out_lines)


# --- Command-Runner-Injektion ------------------------------------------------


class Runner:
    """Produktions-Runner: kapselt `subprocess.run`. Alle Docker-Aufrufe gehen
    ausschliesslich hierueber, damit `apply_update(..., runner=...)` in Tests
    einen Ersatz-Runner (`FakeRunner`) einsetzen kann — Happy-Path UND
    Rollback-Pfad sind so deterministisch testbar, ohne Docker."""

    def run(self, args: list[str], timeout: Optional[float] = None) -> tuple[int, str]:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return result.returncode, (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired as e:
            return 1, f"timeout: {e}"
        except Exception as e:  # noqa: BLE001 — jeder Docker-/OS-Fehler landet hier
            return 1, f"error: {type(e).__name__}: {e}"


class Clock:
    """Produktions-Uhr. Tests injizieren eine Fake-Uhr, damit kein Test
    wirklich 120s wartet."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# --- Hilfsfunktionen (duenne Docker-Huelle um die reinen Funktionen) --------


def _preflight(source_dir: Path, version_to: str, runner: Runner) -> list[str]:
    """VOR dem Stoppen des Agenten: neue Images vorhanden? Compose-Datei
    traegt die neuen Tags? `addin-publish/` existiert? Scheitert etwas, bleibt
    der Agent unangetastet und das Deploy-Verzeichnis unberuehrt."""
    problems: list[str] = []
    for tag in (f"vizpatch:{version_to}", f"vizpatch-webui:{version_to}"):
        rc, _ = runner.run(["docker", "image", "inspect", tag])
        if rc != 0:
            problems.append(f"Image fehlt: {tag}")

    compose_path = source_dir / "docker-compose.yml"
    if not compose_path.is_file():
        problems.append("docker-compose.yml fehlt im neuen Paket")
    else:
        missing = verify_compose_tags(compose_path.read_text(encoding="utf-8"), version_to)
        if missing:
            problems.append("Compose traegt nicht die neuen Tags: " + ", ".join(missing))

    if not (source_dir / "addin-publish").is_dir():
        problems.append("addin-publish/ fehlt im neuen Paket")

    return problems


def _agent_restart_count(runner: Runner) -> int:
    rc, out = runner.run(["docker", "inspect", "-f", "{{.RestartCount}}", AGENT_CONTAINER_NAME])
    if rc != 0:
        return 0
    try:
        return int(out.strip())
    except ValueError:
        return 0


def _backup(compose_path: Path, backup_compose: Path, addin_dir: Path, backup_addin: Path) -> None:
    """Byte-identische Kopie statt Tag-Regex-Rueckschreiben — weniger
    Angriffsflaeche fuer Regex-Fehler im ungemuetlichsten Moment (Rollback)."""
    if compose_path.is_file():
        shutil.copy2(compose_path, backup_compose)
    if backup_addin.exists():
        shutil.rmtree(backup_addin)
    if addin_dir.is_dir():
        shutil.copytree(addin_dir, backup_addin)


def _copy_release(source_dir: Path, deploy_dir: Path) -> None:
    """Allowlist-Copy (siehe `_COPY_TARGETS`-Docstring oben) + Image-Tarballs."""
    deploy_dir.mkdir(parents=True, exist_ok=True)
    for name in _COPY_TARGETS:
        src = source_dir / name
        if not src.exists():
            continue
        dst = deploy_dir / name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    for pattern in ("*.tar", "*.tar.sha256"):
        for src in source_dir.glob(pattern):
            shutil.copy2(src, deploy_dir / src.name)


def _rewrite_deploy_env(deploy_dir: Path, version_to: str) -> None:
    env_path = deploy_dir / ".env"
    if not env_path.is_file():
        return
    try:
        mode = stat.S_IMODE(env_path.stat().st_mode)
        text = env_path.read_text(encoding="utf-8")
        new_text = rewrite_version_env(text, version_to)
        if new_text != text:
            env_path.write_text(new_text, encoding="utf-8")
            os.chmod(env_path, mode)
    except Exception as e:  # noqa: BLE001
        logger.warning("updater_env_rewrite_failed", extra={"exception_type": type(e).__name__})


def _compose_up(runner: Runner, deploy_dir: Path, project: str) -> tuple[int, str]:
    """`-p <project>` ist PFLICHT — ohne den echten Projektnamen legt Compose
    einen parallelen Stack mit neuem Netzwerk und neuem `agent-data`-Volume an
    (SQLite-State weg, doppelte Entwuerfe, T-m8g-09).

    `--force-recreate` ist PFLICHT aus einem zweiten Grund: `up -d` allein
    erstellt einen Container nur neu, wenn sich Image-Tag oder Config geaendert
    haben. Steht das Ziel-Tag schon im Compose-File (Neuinstallation derselben
    Version, um eine kaputte Instanz zu heilen — oder eine Versionsmarke, die
    dem tatsaechlich laufenden Image hinterherhinkt), ist der Tag-Wechsel ein
    No-Op: Compose STARTET dann den alten Container bloss, statt ihn zu
    ersetzen. Ein Update, das den Container nie neu erstellt, ist immer ein
    falscher Zustand — die neue Version laeuft dann nachweislich nicht.

    Am 2026-08-23 hat genau das einen Update-Versuch mit "Neue Version wurde
    nicht gesund" abgebrochen: `installed` stand auf v1.27.2, im Compose-File
    stand bereits v1.30.0, also wurde nur `start` ausgefuehrt — und auf
    Docker Desktop/WSL scheitert `start` eines bestehenden Containers am
    verwaisten Bind-Mount-Cache (Exit 127). `down` bleibt weiter verboten (es
    wuerde das `agent-data`-Volume mit dem SQLite-State loeschen, siehe
    Modul-Docstring); `--force-recreate` ersetzt Container ohne Volumes
    anzutasten."""
    return runner.run(
        [
            "docker", "compose",
            "-f", str(deploy_dir / "docker-compose.yml"),
            "--project-directory", str(deploy_dir),
            "-p", project,
            "up", "-d", "--force-recreate",
        ]
    )


def _check_webui_http(runner: Runner) -> bool:
    """`docker exec` + `python3` statt curl/Host-Port — der Sidecar haengt in
    keinem Compose-Netzwerk, der veroeffentlichte Port kann abweichen, und
    `python3` ist im Image garantiert (es IST die Laufzeit), `curl` nur
    zufaellig."""
    rc, _ = runner.run(
        [
            "docker", "exec", WEBUI_CONTAINER_NAME, "python3", "-c",
            "import urllib.request,sys; "
            "sys.exit(0 if urllib.request.urlopen("
            "'http://127.0.0.1:8080/healthz', timeout=3).status==200 else 1)",
        ]
    )
    return rc == 0


def _check_agent_state(runner: Runner) -> tuple[str, int]:
    rc, out = runner.run(
        ["docker", "inspect", "-f", "{{.State.Status}} {{.RestartCount}}", AGENT_CONTAINER_NAME]
    )
    if rc != 0:
        return "unknown", 0
    parts = out.strip().split()
    state = parts[0] if parts else "unknown"
    try:
        count = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        count = 0
    return state, count


def _wait_for_health(
    runner: Runner,
    timeout_seconds: int,
    poll_interval: int,
    restart_count_before: int,
    clock: Clock,
) -> tuple[str, str]:
    """Zwei aufeinanderfolgende `healthy`-Runden sind noetig, bevor der Erfolg
    festgeschrieben wird — schuetzt gegen den Moment, in dem ein Container
    gerade noch laeuft und eine Sekunde spaeter crasht."""
    consecutive_healthy = 0
    start = clock.monotonic()
    agent_state, webui_ok = "unknown", False
    while True:
        webui_ok = _check_webui_http(runner)
        agent_state, restart_count = _check_agent_state(runner)
        restart_delta = max(0, restart_count - restart_count_before)
        verdict = health_verdict(webui_ok, agent_state, restart_delta)

        if verdict == "unhealthy":
            return "unhealthy", f"Agent-Zustand: {agent_state}, Restart-Zuwachs: {restart_delta}"
        if verdict == "healthy":
            consecutive_healthy += 1
            if consecutive_healthy >= 2:
                return "healthy", ""
        else:
            consecutive_healthy = 0

        if clock.monotonic() - start >= timeout_seconds:
            return (
                "timeout",
                f"Zeitfenster ueberschritten (Agent: {agent_state}, WebUI-HTTP-ok: {webui_ok})",
            )
        clock.sleep(poll_interval)


def _prune_images(runner: Runner, version_to: str, version_from: str) -> None:
    rc, out = runner.run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])
    if rc != 0:
        return
    existing = [
        line.strip()
        for line in out.splitlines()
        if line.strip().startswith(("vizpatch:", "vizpatch-webui:"))
    ]
    for tag in images_to_prune(existing, keep=[version_to, version_from]):
        rc, _ = runner.run(["docker", "image", "rm", tag])
        if rc != 0:
            logger.warning("updater_prune_failed", extra={"tag": tag})


def _prune_deploy_tarballs(deploy_dir, version_to: str, version_from: str) -> None:
    """Entfernt veraltete Image-Tarballs aus dem Deploy-Verzeichnis (siehe
    `tarballs_to_prune`). Behaelt aktuelle + Vorgaengerversion, damit ein
    manuelles Zurueckgehen moeglich bleibt. Fehler werden geschluckt: ein
    liegengebliebener Tarball ist ein Platzproblem, kein Grund, ein
    erfolgreiches Update als gescheitert zu melden."""
    deploy = Path(deploy_dir)
    try:
        names = [p.name for p in deploy.iterdir() if p.is_file()]
    except OSError:
        return

    for name in tarballs_to_prune(names, keep=[version_to, version_from]):
        try:
            (deploy / name).unlink()
        except OSError as e:
            logger.warning(
                "updater_tarball_prune_failed",
                extra={"target": name, "exception_type": type(e).__name__},
            )


def _prune_addin_backup(backup_addin) -> None:
    """Loescht das Add-in-Backup (`addin-publish.pre-<version>`) nach
    erfolgreichem Umschalten — 5,3 MB pro Update, bei der v1.16.0-Abnahme
    gemessen.

    Das Compose-Backup (`docker-compose.yml.pre-<version>`) bleibt bewusst
    liegen: es ist im Kundenrunbook als Fundort fuer lokale Compose-Anpassungen
    dokumentiert und misst nur ~1,4 kB. Das Add-in-Backup hat keinen solchen
    Zweck — der Rollback-Pfad ist zu diesem Zeitpunkt bereits ausgeschlossen.
    """
    backup = Path(backup_addin)
    try:
        if backup.is_dir():
            shutil.rmtree(backup)
    except OSError as e:
        logger.warning(
            "updater_addin_backup_prune_failed",
            extra={"target": str(backup), "exception_type": type(e).__name__},
        )


def _prune_release_artifacts(source_dir) -> None:
    """Loescht das heruntergeladene Zip, seine `.sha256` und das entpackte
    Paketverzeichnis nach erfolgreichem Umschalten.

    Ohne diesen Schritt bleiben pro Update ~425 MB im `agent-data`-Volume
    liegen (215 MB Zip + entpacktes Paket) — bei der Abnahme von v1.16.0
    gemessen. Auf einem kleinen Kundenserver laeuft die Platte damit nach
    wenigen Updates voll, und das naechste Update scheitert am Speicherplatz;
    ausserdem blaeht es jedes Backup des Volumes auf.

    Der Rollback braucht diese Dateien NICHT: er stellt die gesicherte
    `docker-compose.yml` wieder her und faehrt die alten Images hoch, die
    lokal weiterhin vorhanden sind (`images_to_prune` behaelt `version_from`).
    Deshalb ist Aufraeumen erst NACH dem `healthy`-Verdikt korrekt — bei
    Rollback oder Abbruch bleibt alles zur Diagnose liegen.

    Fehler werden geschluckt: ein nicht geloeschter Rest ist ein
    Platzproblem, kein Grund, ein erfolgreiches Update als gescheitert zu
    melden.
    """
    source = Path(source_dir)
    targets = [source]
    # Das Zip liegt neben dem entpackten Verzeichnis und heisst gleich.
    for suffix in (".zip", ".zip.sha256"):
        targets.append(source.with_name(source.name + suffix))

    for target in targets:
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except OSError as e:
            logger.warning(
                "updater_artifact_prune_failed",
                extra={"target": str(target), "exception_type": type(e).__name__},
            )


def _rollback(
    runner: Runner,
    clock: Clock,
    compose_path: Path,
    backup_compose: Path,
    addin_dir: Path,
    backup_addin: Path,
    deploy_dir: Path,
    project: str,
    version_from: str,
    cause_detail: str,
) -> dict:
    """D-4: byte-identische Wiederherstellung, erneutes `up -d`, kuerzeres
    Health-Fenster. Entfernt NIEMALS ein Image — `docker load` hat die alten
    Images nicht angetastet, genau darauf beruht der Rollback."""
    update_status.write_status(
        state=update_status.STATE_RUNNING,
        step="rolling_back",
        progress=update_status.progress_for("rolling_back"),
        message="Neue Version wurde nicht gesund — Vorversion wird wiederhergestellt …",
    )

    if backup_compose.is_file():
        shutil.copy2(backup_compose, compose_path)
    if backup_addin.is_dir():
        if addin_dir.exists():
            shutil.rmtree(addin_dir)
        shutil.copytree(backup_addin, addin_dir)

    # VIZPATCH_VERSION zurueckdrehen. Das Umschalten hat den Wert auf die
    # ZIELversion gesetzt (`_rewrite_deploy_env`); bleibt er stehen, behauptet
    # die Installation dauerhaft, auf der neuen Version zu laufen, obwohl die
    # alten Images laufen. `update_check.resolve_installed_version()` bevorzugt
    # die Env-Angabe vor dem Image-Tag -> `installed == latest` ->
    # `update_available: false`. Folge: derselbe Versuch ist nicht
    # wiederholbar, die Installation haengt nach einem Rollback fest, und die
    # gemeldete Version ist fuer Support/Diagnose falsch. In der
    # v1.17.0-Rollback-Abnahme genau so aufgetreten.
    _rewrite_deploy_env(deploy_dir, version_from)

    _compose_up(runner, deploy_dir, project)
    restart_count_before = _agent_restart_count(runner)
    verdict, _detail = _wait_for_health(
        runner, ROLLBACK_HEALTH_TIMEOUT_SECONDS, HEALTH_POLL_INTERVAL_SECONDS,
        restart_count_before, clock,
    )

    message = f"Neue Version wurde nicht gesund — {cause_detail} — Vorversion {version_from} wiederhergestellt."
    recovery_hint = ""
    if verdict != "healthy":
        recovery_hint = "Bitte Enver Shala kontaktieren, Container-Logs: docker logs vizpatch-agent"

    update_status.write_status(
        state=update_status.STATE_ROLLED_BACK,
        step="done",
        progress=update_status.progress_for("done"),
        message=message,
        recovery_hint=recovery_hint,
    )
    return update_status.read_status()


# --- Hauptablauf --------------------------------------------------------------


def apply_update(
    source_dir,
    deploy_dir,
    project: str,
    version_to: str,
    version_from: str,
    runner: Optional[Runner] = None,
    clock: Optional[Clock] = None,
) -> dict:
    """Orchestriert den gesamten Umschalt-/Rollback-Ablauf (D-3/D-4). Schreibt
    laufend in die Statusdatei (`update_status`), damit `GET
    /addin/update-status` den Fortschritt auch nach einem WebUI-Neustart noch
    liefert."""
    runner = runner or Runner()
    clock = clock or Clock()
    source_dir = Path(source_dir)
    deploy_dir = Path(deploy_dir)

    compose_path = deploy_dir / "docker-compose.yml"
    backup_compose = deploy_dir / f"docker-compose.yml.pre-{version_from}"
    addin_dir = deploy_dir / "addin-publish"
    backup_addin = deploy_dir / f"addin-publish.pre-{version_from}"

    try:
        problems = _preflight(source_dir, version_to, runner)
        if problems:
            update_status.write_status(
                state=update_status.STATE_FAILED,
                step="preparing",
                message="Vorpruefung fehlgeschlagen: " + "; ".join(problems),
                recovery_hint="Bitte Enver Shala kontaktieren.",
            )
            return update_status.read_status()

        # --- Agent austrudeln lassen (D-3) ---
        update_status.write_status(
            state=update_status.STATE_RUNNING,
            step="stopping_agent",
            progress=update_status.progress_for("stopping_agent"),
            version_from=version_from,
            version_to=version_to,
            message="Postfach-Verarbeitung wird beendet …",
        )
        runner.run(["docker", "stop", "--time", str(AGENT_STOP_TIMEOUT_SECONDS), AGENT_CONTAINER_NAME])

        _backup(compose_path, backup_compose, addin_dir, backup_addin)

        # --- Umschalten ---
        update_status.write_status(
            step="switching",
            progress=update_status.progress_for("switching"),
            message="Neue Version wird eingespielt …",
        )
        _copy_release(source_dir, deploy_dir)
        _rewrite_deploy_env(deploy_dir, version_to)
        _compose_up(runner, deploy_dir, project)

        # --- Health-Fenster ---
        update_status.write_status(
            step="health",
            progress=update_status.progress_for("health"),
            message="Funktionspruefung läuft …",
        )
        restart_count_before = _agent_restart_count(runner)
        verdict, detail = _wait_for_health(
            runner, HEALTH_TIMEOUT_SECONDS, HEALTH_POLL_INTERVAL_SECONDS,
            restart_count_before, clock,
        )

        if verdict == "healthy":
            _prune_images(runner, version_to, version_from)
            _prune_release_artifacts(source_dir)
            _prune_addin_backup(backup_addin)
            _prune_deploy_tarballs(deploy_dir, version_to, version_from)
            update_status.write_status(
                state=update_status.STATE_SUCCESS,
                step="done",
                progress=update_status.progress_for("done"),
                message=f"Update auf {version_to} abgeschlossen.",
                recovery_hint="",
            )
            return update_status.read_status()

        return _rollback(
            runner, clock, compose_path, backup_compose, addin_dir, backup_addin,
            deploy_dir, project, version_from, detail,
        )
    except Exception as e:  # noqa: BLE001 — jeder unerwartete Fehler, keine Secrets
        logger.warning("updater_unexpected_error", extra={"exception_type": type(e).__name__})
        update_status.write_status(
            state=update_status.STATE_FAILED,
            step="failed",
            message=f"Unerwarteter Fehler: {type(e).__name__}",
            recovery_hint="Bitte Enver Shala kontaktieren, Container-Logs: docker logs vizpatch-agent",
        )
        try:
            return _rollback(
                runner, clock, compose_path, backup_compose, addin_dir, backup_addin,
                deploy_dir, project, version_from, f"unerwarteter Fehler ({type(e).__name__})",
            )
        except Exception as rollback_error:  # noqa: BLE001
            logger.warning(
                "updater_rollback_after_error_failed",
                extra={"exception_type": type(rollback_error).__name__},
            )
            return update_status.read_status()


def main(argv: Optional[list[str]] = None) -> int:
    """CLI-Einstieg (`python3 -m src.updater --source … --deploy … --project …
    --to … --from …`). Rueckgabecode 0 bei `success`, 2 bei `rolled_back`, 1
    bei `failed`. Logging ueber `logging_setup` auf stdout, damit `docker logs
    vizpatch-updater` als Diagnosequelle taugt."""
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(description="Vizpatch Self-Update Sidecar")
    parser.add_argument("--source", required=True)
    parser.add_argument("--deploy", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--to", required=True, dest="version_to")
    parser.add_argument("--from", required=True, dest="version_from")
    args = parser.parse_args(argv)

    result = apply_update(
        args.source, args.deploy, args.project, args.version_to, args.version_from
    )
    state = result.get("state")
    if state == update_status.STATE_SUCCESS:
        return 0
    if state == update_status.STATE_ROLLED_BACK:
        return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
