"""Tests fuer den Updater-Sidecar (260728-m8g, Stufe 2, D-3/D-4).

Alle Docker-Aufrufe laufen ueber einen injizierten `FakeRunner` — kein echtes
Docker. Die Zeit laeuft ueber eine `FakeClock` — kein Test wartet wirklich
120s. Datei-Operationen (Backup/Copy/Rollback) laufen gegen echte `tmp_path`-
Verzeichnisse, damit die byte-identische Wiederherstellung wirklich geprueft
wird.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

import src.update_status as update_status
import src.updater as updater


@pytest.fixture(autouse=True)
def _isolate_status_file(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VIZPATCH_UPDATE_STATUS_FILE", str(tmp_path / "status" / "vizpatch_update.json")
    )
    yield
    update_status.reset_for_tests()


# --- Reine Entscheidungsfunktionen -------------------------------------------


def test_read_image_tags_happy_path():
    text = "services:\n  agent:\n    image: vizpatch:v1.13.0\n  webui:\n    image: vizpatch-webui:v1.13.0\n"
    assert updater.read_image_tags(text) == {"agent": "vizpatch:v1.13.0", "webui": "vizpatch-webui:v1.13.0"}


def test_read_image_tags_missing_tag_no_crash():
    text = "services:\n  agent:\n    image: vizpatch:v1.13.0\n"
    tags = updater.read_image_tags(text)
    assert tags == {"agent": "vizpatch:v1.13.0"}
    assert "webui" not in tags


def test_verify_compose_tags_all_present():
    text = "image: vizpatch:v1.14.0\nimage: vizpatch-webui:v1.14.0\n"
    assert updater.verify_compose_tags(text, "v1.14.0") == []


def test_verify_compose_tags_missing_lists_labels():
    text = "image: vizpatch:v1.13.0\nimage: vizpatch-webui:v1.14.0\n"
    missing = updater.verify_compose_tags(text, "v1.14.0")
    assert missing == ["agent"]


@pytest.mark.parametrize(
    "webui_ok,agent_state,restart_delta,expected",
    [
        (True, "running", 0, "healthy"),
        (False, "running", 0, "pending"),
        (True, "restarting", 0, "pending"),
        (True, "running", 2, "unhealthy"),
        (True, "exited", 0, "unhealthy"),
        (True, "dead", 0, "unhealthy"),
    ],
)
def test_health_verdict(webui_ok, agent_state, restart_delta, expected):
    assert updater.health_verdict(webui_ok, agent_state, restart_delta) == expected


def test_images_to_prune_keeps_current_and_rollback_target():
    existing = ["vizpatch:v1.12.0", "vizpatch:v1.13.0", "vizpatch:v1.14.0"]
    assert updater.images_to_prune(existing, keep=["v1.14.0", "v1.13.0"]) == ["vizpatch:v1.12.0"]


def test_images_to_prune_ignores_unparsable_tags():
    existing = ["vizpatch:latest", "vizpatch:v1.12.0"]
    result = updater.images_to_prune(existing, keep=["v1.14.0"])
    assert "vizpatch:latest" not in result
    assert result == ["vizpatch:v1.12.0"]


def test_tarballs_to_prune_keeps_current_and_previous():
    names = [
        "vizpatch-v1.16.0.tar", "vizpatch-webui-v1.16.0.tar",
        "vizpatch-v1.17.0.tar", "vizpatch-webui-v1.17.0.tar",
        "vizpatch-v1.18.0.tar", "vizpatch-webui-v1.18.0.tar",
        "vizpatch-v1.18.0.tar.sha256", "vizpatch-v1.16.0.tar.sha256",
    ]
    prune = updater.tarballs_to_prune(names, keep=["v1.18.0", "v1.17.0"])
    assert sorted(prune) == sorted([
        "vizpatch-v1.16.0.tar", "vizpatch-webui-v1.16.0.tar",
        "vizpatch-v1.16.0.tar.sha256",
    ])


def test_tarballs_to_prune_ignores_foreign_files():
    """Nur eindeutige Semver-Staende anfassen — nie Compose, .env, Fremd-Tars."""
    names = [
        "docker-compose.yml", ".env", "README.md",
        "backup.tar", "vizpatch-latest.tar", "vizpatch-webui-dev.tar",
        "something-v1.16.0.tar",
    ]
    assert updater.tarballs_to_prune(names, keep=["v1.18.0"]) == []


def test_tarballs_to_prune_empty_keep_prunes_all_semver():
    names = ["vizpatch-v1.16.0.tar", "vizpatch-webui-v1.16.0.tar"]
    assert sorted(updater.tarballs_to_prune(names, keep=[])) == sorted(names)


def test_apply_update_success_prunes_old_deploy_tarballs(tmp_path):
    """Ende-zu-Ende: nach dem Update sind Alt-Tarballs weg, aktuelle da.

    Regression aus der v1.18.0-Abnahme: im Deploy-Verzeichnis lagen 531 MB
    Image-Tarballs aus drei Updates (~177 MB je Update), weil `_copy_release`
    sie ablegt und niemand alte entfernte.
    """
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    # Alt-Staende, wie sie sich real angesammelt hatten.
    for name in ("vizpatch-v1.12.0.tar", "vizpatch-webui-v1.12.0.tar",
                 "vizpatch-v1.13.0.tar", "vizpatch-webui-v1.13.0.tar"):
        (deploy_dir / name).write_bytes(b"alt")
    (deploy_dir / "fremd.tar").write_bytes(b"nicht anfassen")

    rules = _base_rules(agent_state_provider=lambda: "running 0") + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    result = updater.apply_update(
        source_dir, deploy_dir, "vizpatch", version_to, version_from,
        FakeRunner(rules), FakeClock(),
    )

    assert result["state"] == update_status.STATE_SUCCESS
    # v1.12.0 ist zwei Staende alt -> weg.
    assert not (deploy_dir / "vizpatch-v1.12.0.tar").exists()
    assert not (deploy_dir / "vizpatch-webui-v1.12.0.tar").exists()
    # Vorgaengerversion bleibt (manuelles Zurueckgehen).
    assert (deploy_dir / "vizpatch-v1.13.0.tar").exists()
    # Neue Version wurde eingespielt und bleibt.
    assert (deploy_dir / f"vizpatch-{version_to}.tar").exists()
    # Fremddateien unangetastet.
    assert (deploy_dir / "fremd.tar").read_bytes() == b"nicht anfassen"
    assert (deploy_dir / "docker-compose.yml").exists()
    assert (deploy_dir / "config" / ".env").exists()


def test_rewrite_version_env_nonempty_gets_rewritten():
    text = "FOO=bar\nVIZPATCH_VERSION=v1.13.0\nBAZ=1\n"
    result = updater.rewrite_version_env(text, "v1.14.0")
    assert "VIZPATCH_VERSION=v1.14.0\n" in result
    assert "v1.13.0" not in result


def test_rewrite_version_env_empty_stays_empty():
    text = "VIZPATCH_VERSION=\nFOO=bar\n"
    result = updater.rewrite_version_env(text, "v1.14.0")
    assert "VIZPATCH_VERSION=\n" in result
    assert "v1.14.0" not in result


def test_rewrite_version_env_missing_stays_absent():
    text = "FOO=bar\n"
    result = updater.rewrite_version_env(text, "v1.14.0")
    assert result == text
    assert "VIZPATCH_VERSION" not in result


# --- Test-Doubles -------------------------------------------------------------


def _prefix(*prefix):
    prefix = list(prefix)
    return lambda args: args[: len(prefix)] == prefix


def _exact(*expected):
    expected = list(expected)
    return lambda args: args == expected


class FakeRunner:
    def __init__(self, rules, default=(0, "")):
        self.rules = rules
        self.default = default
        self.calls: list[list[str]] = []

    def run(self, args, timeout=None):
        self.calls.append(list(args))
        for predicate, response in self.rules:
            if predicate(args):
                return response(args) if callable(response) else response
        return self.default


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


# --- Fixtures fuer den Ablauf --------------------------------------------------


def _make_source_dir(tmp_path: Path, version_to: str) -> Path:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "docker-compose.yml").write_text(
        f"services:\n  agent:\n    image: vizpatch:{version_to}\n  webui:\n    image: vizpatch-webui:{version_to}\n",
        encoding="utf-8",
    )
    (source_dir / "addin-publish").mkdir()
    (source_dir / "addin-publish" / "setup.exe").write_text("stub", encoding="utf-8")
    (source_dir / "prompts").mkdir()
    (source_dir / "prompts" / "classify.txt").write_text("neu", encoding="utf-8")
    (source_dir / "deployment").mkdir()
    (source_dir / "deployment" / "RUNBOOK-KUNDE.md").write_text("neu", encoding="utf-8")
    (source_dir / "scripts").mkdir()
    (source_dir / "scripts" / "install-autostart.sh").write_text("neu", encoding="utf-8")
    (source_dir / "README.md").write_text("neues readme", encoding="utf-8")
    (source_dir / f"vizpatch-{version_to}.tar").write_bytes(b"tar-agent")
    (source_dir / f"vizpatch-{version_to}.tar.sha256").write_text("hash", encoding="utf-8")
    return source_dir


def _make_deploy_dir(tmp_path: Path, version_from: str) -> Path:
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    (deploy_dir / "docker-compose.yml").write_text(
        f"services:\n  agent:\n    image: vizpatch:{version_from}\n  webui:\n    image: vizpatch-webui:{version_from}\n",
        encoding="utf-8",
    )
    (deploy_dir / "addin-publish").mkdir()
    (deploy_dir / "addin-publish" / "setup.exe").write_text("alt", encoding="utf-8")
    (deploy_dir / "config").mkdir()
    (deploy_dir / "config" / ".env").write_text("WEBUI_PASSWORD=geheim", encoding="utf-8")
    (deploy_dir / ".env").write_text(f"DOCKER_GID=999\nVIZPATCH_VERSION={version_from}\n", encoding="utf-8")
    (deploy_dir / ".secret_key").write_text("supersecretkeydata", encoding="utf-8")
    return deploy_dir


def _base_rules(agent_state_provider, webui_ok_provider=lambda: True):
    """Gemeinsame Regeln fuer Image-Inspect/Stop/RestartCount — die
    healthbezogenen Regeln (webui-exec + Agent-Status) kommen ueber
    Callables, damit Tests das Verhalten je Szenario steuern koennen."""
    return [
        (_prefix("docker", "image", "inspect"), lambda args: (0, "")),
        (_prefix("docker", "stop"), lambda args: (0, "")),
        (_exact("docker", "inspect", "-f", "{{.RestartCount}}", "vizpatch-agent"), lambda args: (0, "0")),
        (
            _prefix("docker", "exec", "vizpatch-webui"),
            lambda args: (0, "") if webui_ok_provider() else (1, "connection refused"),
        ),
        (
            _exact("docker", "inspect", "-f", "{{.State.Status}} {{.RestartCount}}", "vizpatch-agent"),
            lambda args: (0, agent_state_provider()),
        ),
    ]


# --- Happy Path ----------------------------------------------------------------


def test_apply_update_happy_path_order_and_success(tmp_path):
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)
    expected_compose = (source_dir / "docker-compose.yml").read_text(encoding="utf-8")

    compose_up_calls = {"n": 0}

    def on_compose_up(args):
        compose_up_calls["n"] += 1
        return 0, ""

    rules = _base_rules(agent_state_provider=lambda: "running 0") + [
        (_prefix("docker", "compose"), on_compose_up),
        (
            _prefix("docker", "image", "ls"),
            lambda args: (0, "vizpatch:v1.12.0\nvizpatch:v1.13.0\nvizpatch:v1.14.0\nvizpatch-webui:v1.13.0\nvizpatch-webui:v1.14.0\n"),
        ),
        (_prefix("docker", "image", "rm"), lambda args: (0, "")),
    ]
    runner = FakeRunner(rules)
    clock = FakeClock()

    result = updater.apply_update(source_dir, deploy_dir, "vizpatch", version_to, version_from, runner, clock)

    assert result["state"] == update_status.STATE_SUCCESS
    assert result["progress"] == 100

    # Reihenfolge: stop -> compose up -> image rm (Prune) NACH Erfolg.
    kinds = [
        "stop" if c[:2] == ["docker", "stop"] else
        "compose_up" if c[:2] == ["docker", "compose"] else
        "rm" if c[:3] == ["docker", "image", "rm"] else
        None
        for c in runner.calls
    ]
    kinds = [k for k in kinds if k]
    assert kinds.index("stop") < kinds.index("compose_up") < kinds.index("rm")
    assert compose_up_calls["n"] == 1

    # Prune: nur die aeltere Version wird entfernt, NIE die aktuelle/Vorgaenger.
    rm_calls = [c for c in runner.calls if c[:3] == ["docker", "image", "rm"]]
    assert rm_calls == [["docker", "image", "rm", "vizpatch:v1.12.0"]]

    # Neue Version tatsaechlich eingespielt. Der Soll-Inhalt wird VOR dem
    # Update gemerkt, weil `source_dir` nach Erfolg aufgeraeumt wird
    # (_prune_release_artifacts) — sonst laeuft der Vergleich hier in einen
    # FileNotFoundError.
    assert (deploy_dir / "docker-compose.yml").read_text(encoding="utf-8") == expected_compose
    assert (deploy_dir / "addin-publish" / "setup.exe").read_text(encoding="utf-8") == "stub"


def test_apply_update_success_prunes_download_artifacts(tmp_path):
    """Nach erfolgreichem Umschalten muessen Zip + entpacktes Paket weg sein.

    Bei der v1.16.0-Abnahme lagen 425 MB im agent-data-Volume (215 MB Zip +
    entpacktes Paket) — pro Update. Das laesst kleine Kundenserver volllaufen
    und blaeht jedes Volume-Backup auf.
    """
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    # Zip + Checksumme liegen neben dem entpackten Verzeichnis (wie im Betrieb).
    zip_path = source_dir.with_name(source_dir.name + ".zip")
    sha_path = source_dir.with_name(source_dir.name + ".zip.sha256")
    zip_path.write_bytes(b"x" * 1024)
    sha_path.write_text("hash", encoding="utf-8")

    rules = _base_rules(agent_state_provider=lambda: "running 0") + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    result = updater.apply_update(
        source_dir, deploy_dir, "vizpatch", version_to, version_from,
        FakeRunner(rules), FakeClock(),
    )

    assert result["state"] == update_status.STATE_SUCCESS
    assert not source_dir.exists(), "entpacktes Paketverzeichnis nicht aufgeraeumt"
    assert not zip_path.exists(), "Zip nicht aufgeraeumt"
    assert not sha_path.exists(), "Checksumme nicht aufgeraeumt"
    # Das Deploy-Verzeichnis darf davon NICHT betroffen sein.
    assert (deploy_dir / "docker-compose.yml").exists()
    assert (deploy_dir / "config" / ".env").exists()


def test_apply_update_success_prunes_addin_backup_keeps_compose_backup(tmp_path):
    """Add-in-Backup (5,3 MB/Update) weg, Compose-Backup bleibt.

    Das Compose-Backup ist im Kundenrunbook als Fundort fuer lokale
    Compose-Anpassungen dokumentiert und darf NICHT mitgeloescht werden.
    """
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    rules = _base_rules(agent_state_provider=lambda: "running 0") + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    result = updater.apply_update(
        source_dir, deploy_dir, "vizpatch", version_to, version_from,
        FakeRunner(rules), FakeClock(),
    )

    assert result["state"] == update_status.STATE_SUCCESS
    assert not (deploy_dir / f"addin-publish.pre-{version_from}").exists(), \
        "Add-in-Backup nicht aufgeraeumt"
    assert (deploy_dir / f"docker-compose.yml.pre-{version_from}").is_file(), \
        "Compose-Backup faelschlich geloescht (im Runbook dokumentiert)"
    # Das aktive Add-in-Verzeichnis bleibt selbstverstaendlich.
    assert (deploy_dir / "addin-publish" / "setup.exe").is_file()


def test_apply_update_rollback_reverts_vizpatch_version_in_env(tmp_path):
    """Nach einem Rollback muss VIZPATCH_VERSION wieder die ALTE Version nennen.

    Regression aus der v1.17.0-Rollback-Abnahme: der Wert blieb auf der
    Zielversion stehen. Weil `resolve_installed_version()` die Env-Angabe dem
    Image-Tag vorzieht, meldete die Installation `installed == latest` und damit
    `update_available: false` — obwohl die alten Images liefen. Derselbe Versuch
    war dadurch nicht wiederholbar, die Installation hing fest, und die
    gemeldete Version war fuer Support/Diagnose falsch.
    """
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    rules = _base_rules(
        agent_state_provider=lambda: "running 0",
        webui_ok_provider=lambda: False,  # WebUI antwortet nie -> Rollback
    ) + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    result = updater.apply_update(
        source_dir, deploy_dir, "vizpatch", version_to, version_from,
        FakeRunner(rules), FakeClock(),
    )

    assert result["state"] == update_status.STATE_ROLLED_BACK
    env_text = (deploy_dir / ".env").read_text(encoding="utf-8")
    assert f"VIZPATCH_VERSION={version_from}" in env_text, \
        f"VIZPATCH_VERSION nicht zurueckgedreht: {env_text!r}"
    assert version_to not in env_text
    # Andere Eintraege bleiben unangetastet.
    assert "DOCKER_GID=999" in env_text


def test_apply_update_rollback_leaves_empty_vizpatch_version_empty(tmp_path):
    """War der Eintrag leer, bleibt er leer — dann ist der Image-Tag die
    selbstaktualisierende Quelle und darf nicht ueberschrieben werden."""
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)
    (deploy_dir / ".env").write_text("DOCKER_GID=999\nVIZPATCH_VERSION=\n", encoding="utf-8")

    rules = _base_rules(
        agent_state_provider=lambda: "running 0",
        webui_ok_provider=lambda: False,
    ) + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    updater.apply_update(
        source_dir, deploy_dir, "vizpatch", version_to, version_from,
        FakeRunner(rules), FakeClock(),
    )

    env_text = (deploy_dir / ".env").read_text(encoding="utf-8")
    assert "VIZPATCH_VERSION=\n" in env_text
    assert version_from not in env_text
    assert version_to not in env_text


def test_apply_update_rollback_keeps_addin_backup(tmp_path):
    """Bei Rollback wird das Add-in-Backup gebraucht — es muss dann bleiben
    bzw. zurueckgespielt worden sein, aber nie stillschweigend verschwinden."""
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    rules = _base_rules(
        agent_state_provider=lambda: "running 0",
        webui_ok_provider=lambda: False,
    ) + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    result = updater.apply_update(
        source_dir, deploy_dir, "vizpatch", version_to, version_from,
        FakeRunner(rules), FakeClock(),
    )

    assert result["state"] == update_status.STATE_ROLLED_BACK
    # Der alte Add-in-Stand ist wieder aktiv (Rollback hat zurueckgespielt).
    assert (deploy_dir / "addin-publish" / "setup.exe").read_text(encoding="utf-8") == "alt"


def test_apply_update_rollback_keeps_download_artifacts(tmp_path):
    """Bei Rollback bleiben die Artefakte zur Diagnose liegen — aufgeraeumt
    wird ausschliesslich nach dem `healthy`-Verdikt."""
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)
    zip_path = source_dir.with_name(source_dir.name + ".zip")
    zip_path.write_bytes(b"x" * 1024)

    # WebUI antwortet nie -> Health-Fenster laeuft ab -> Rollback.
    rules = _base_rules(
        agent_state_provider=lambda: "running 0",
        webui_ok_provider=lambda: False,
    ) + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    result = updater.apply_update(
        source_dir, deploy_dir, "vizpatch", version_to, version_from,
        FakeRunner(rules), FakeClock(),
    )

    assert result["state"] == update_status.STATE_ROLLED_BACK
    assert source_dir.exists(), "Diagnose-Artefakte wurden bei Rollback geloescht"
    assert zip_path.exists()


def test_prune_download_artifacts_survives_missing_files(tmp_path):
    """Fehlende oder nicht loeschbare Reste duerfen kein erfolgreiches Update
    zum Fehlschlag machen — die Funktion schluckt OSError."""
    missing = tmp_path / "gibtsnicht"
    updater._prune_release_artifacts(missing)  # darf nicht werfen


def test_apply_update_copy_step_preserves_config_env_secret_key(tmp_path):
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    rules = _base_rules(agent_state_provider=lambda: "running 0") + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    runner = FakeRunner(rules)
    clock = FakeClock()

    updater.apply_update(source_dir, deploy_dir, "vizpatch", version_to, version_from, runner, clock)

    # Unangetastet:
    assert (deploy_dir / "config" / ".env").read_text(encoding="utf-8") == "WEBUI_PASSWORD=geheim"
    assert (deploy_dir / ".secret_key").read_text(encoding="utf-8") == "supersecretkeydata"
    # .env WURDE angefasst (Version-Rewrite), aber DOCKER_GID bleibt erhalten.
    env_text = (deploy_dir / ".env").read_text(encoding="utf-8")
    assert "DOCKER_GID=999" in env_text
    assert f"VIZPATCH_VERSION={version_to}" in env_text


def test_apply_update_empty_vizpatch_version_stays_empty(tmp_path):
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)
    (deploy_dir / ".env").write_text("DOCKER_GID=999\nVIZPATCH_VERSION=\n", encoding="utf-8")

    rules = _base_rules(agent_state_provider=lambda: "running 0") + [
        (_prefix("docker", "compose"), lambda args: (0, "")),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
    ]
    runner = FakeRunner(rules)
    clock = FakeClock()

    updater.apply_update(source_dir, deploy_dir, "vizpatch", version_to, version_from, runner, clock)

    env_text = (deploy_dir / ".env").read_text(encoding="utf-8")
    assert "VIZPATCH_VERSION=\n" in env_text


# --- Rollback (D-4, harte Auslieferungsbedingung) ------------------------------


def test_apply_update_rollback_on_health_timeout(tmp_path):
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    compose_before = (deploy_dir / "docker-compose.yml").read_text(encoding="utf-8")
    addin_before = (deploy_dir / "addin-publish" / "setup.exe").read_text(encoding="utf-8")

    compose_up_calls = {"n": 0}

    def on_compose_up(args):
        compose_up_calls["n"] += 1
        return 0, ""

    def agent_state():
        # Vor dem zweiten "compose up" (= Rollback-Restore) haengt der Agent
        # in einer Restart-/Pending-Schleife; danach (alte Version) laeuft er.
        return "restarting 0" if compose_up_calls["n"] < 2 else "running 0"

    rules = _base_rules(agent_state_provider=agent_state) + [
        (_prefix("docker", "compose"), on_compose_up),
        (_prefix("docker", "image", "ls"), lambda args: (0, "")),
        (_prefix("docker", "image", "rm"), lambda args: (0, "")),
    ]
    runner = FakeRunner(rules)
    clock = FakeClock()

    result = updater.apply_update(source_dir, deploy_dir, "vizpatch", version_to, version_from, runner, clock)

    assert result["state"] == update_status.STATE_ROLLED_BACK
    assert version_from in result["message"]

    # compose up wurde ZWEIMAL gerufen (Umschalten + Rollback-Restore).
    assert compose_up_calls["n"] == 2

    # Compose-Datei + addin-publish byte-identisch zum Ausgangszustand.
    assert (deploy_dir / "docker-compose.yml").read_text(encoding="utf-8") == compose_before
    assert (deploy_dir / "addin-publish" / "setup.exe").read_text(encoding="utf-8") == addin_before

    # NIE ein Image entfernt beim Rollback.
    rm_calls = [c for c in runner.calls if c[:3] == ["docker", "image", "rm"]]
    assert rm_calls == []


# --- Vorpruefung schlaegt fehl --------------------------------------------------


def test_apply_update_preflight_failure_does_not_touch_anything(tmp_path):
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    compose_before = (deploy_dir / "docker-compose.yml").read_text(encoding="utf-8")

    rules = [
        (_prefix("docker", "image", "inspect"), lambda args: (1, "no such image")),
    ]
    runner = FakeRunner(rules)
    clock = FakeClock()

    result = updater.apply_update(source_dir, deploy_dir, "vizpatch", version_to, version_from, runner, clock)

    assert result["state"] == update_status.STATE_FAILED
    assert not any(c[:2] == ["docker", "stop"] for c in runner.calls)
    assert (deploy_dir / "docker-compose.yml").read_text(encoding="utf-8") == compose_before


def test_apply_update_preflight_missing_compose_tags(tmp_path):
    version_to, version_from = "v1.14.0", "v1.13.0"
    source_dir = _make_source_dir(tmp_path, version_to)
    # Kaputtes Paket: die neue Compose-Datei traegt noch den alten Tag.
    (source_dir / "docker-compose.yml").write_text(
        f"services:\n  agent:\n    image: vizpatch:{version_from}\n  webui:\n    image: vizpatch-webui:{version_to}\n",
        encoding="utf-8",
    )
    deploy_dir = _make_deploy_dir(tmp_path, version_from)

    rules = [(_prefix("docker", "image", "inspect"), lambda args: (0, ""))]
    runner = FakeRunner(rules)
    clock = FakeClock()

    result = updater.apply_update(source_dir, deploy_dir, "vizpatch", version_to, version_from, runner, clock)

    assert result["state"] == update_status.STATE_FAILED
    assert not any(c[:2] == ["docker", "stop"] for c in runner.calls)


# --- Quell-Waechter: nirgends "docker compose down" --------------------------


def test_source_guard_no_compose_down_command():
    source = Path(updater.__file__).read_text(encoding="utf-8")
    # Docstrings entfernen (Prosa erklaert bewusst "NIE docker compose down" —
    # das darf den Waechter nicht selbst invalidieren).
    stripped = re.sub(r'"""[\s\S]*?"""', "", source)
    stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
    # Vollzeilen-'#'-Kommentare entfernen.
    code_lines = [
        line for line in stripped.splitlines() if not line.strip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert "down" not in code_only


# --- Waechter: `up -d` erstellt den Container IMMER neu -----------------------


def test_compose_up_erzwingt_force_recreate(tmp_path):
    """`up -d` ohne `--force-recreate` STARTET einen bestehenden Container bloss,
    wenn Image-Tag und Config unveraendert sind — dann laeuft nach einem
    "erfolgreichen" Update nachweislich noch der alte Container. Genau so ist am
    2026-08-23 ein Update mit "Neue Version wurde nicht gesund" abgebrochen
    (Compose stand schon auf dem Ziel-Tag, der Tag-Wechsel war ein No-Op).

    `down` bleibt verboten (loescht das `agent-data`-Volume) — dafuer gibt es
    `test_source_guard_no_compose_down_command`."""
    runner = FakeRunner([])

    updater._compose_up(runner, tmp_path, "vizpatch")

    assert len(runner.calls) == 1
    aufruf = runner.calls[0]
    assert aufruf[:2] == ["docker", "compose"]
    assert "--force-recreate" in aufruf
    assert "-p" in aufruf and "vizpatch" in aufruf
    assert "down" not in aufruf
