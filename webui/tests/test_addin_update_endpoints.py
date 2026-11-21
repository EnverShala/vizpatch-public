"""Tests fuer den Self-Update-Trigger (260728-m8g, Stufe 2): `POST
/addin/update`, `GET /addin/update-status`, `auth.require_basic_password` und
die reinen `docker_ctrl`-Funktionen (`parse_self_layout`,
`build_updater_run_args`). Kein echtes Docker, kein echtes Netzwerk, kein
echter Hintergrund-Thread — `_run_update_job` wird per `monkeypatch` durch
einen Recorder ersetzt und `threading.Thread` synchron ausgefuehrt."""
from __future__ import annotations

import base64
import types

import pytest

from src import auth, docker_ctrl, update_status


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))
    monkeypatch.setenv("VIZPATCH_UPDATE_STATUS_FILE", str(tmp_path / "vizpatch_update.json"))


def _basic_header(user: str, password: str) -> dict:
    raw = f"{user}:{password}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


class _ImmediateThread:
    """Ersetzt `threading.Thread` in den Endpoint-Tests: `start()` ruft das
    Target SOFORT synchron auf — kein echter Thread, kein Race, aber der
    Produktionscode (der einen echten Thread startet) bleibt unangetastet."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


@pytest.fixture(autouse=True)
def _sync_thread_and_isolate_status(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from src import main

    # WICHTIG: nur den Namen "threading" IN src.main umbiegen (main.__dict__),
    # NICHT das echte stdlib-Modul mutieren — sonst reisst das slowapi's
    # eigene threading.Timer-Nutzung (Rate-Limit-Storage) mit sich.
    fake_threading = types.SimpleNamespace(Thread=_ImmediateThread)
    monkeypatch.setattr(main, "threading", fake_threading)

    # Die installierte Version deterministisch auf "unbekannt" festnageln.
    # `POST /addin/update` ermittelt `version_from` ueber
    # `update_check.resolve_installed_version()`; ohne Mock fragt das den ECHTEN
    # Docker-Daemon (`docker_ctrl.get_webui_image_tag`) und liefert auf einem
    # Rechner mit laufender Vizpatch-Instanz deren Image-Tag. Der Test hing damit
    # am lokalen Docker-Zustand: solange die Testinstanz auf `v1.11-dev` lief
    # (nicht semver-parsbar -> None) war er gruen, nach dem Update auf
    # `v1.16.0` schlug er fehl. Zusaetzlich den Modul-Cache leeren, damit kein
    # Wert aus einer anderen Testdatei durchschlaegt.
    from src import update_check

    monkeypatch.setattr(update_check, "resolve_installed_version", lambda: None)
    update_check.reset_cache()
    yield
    update_check.reset_cache()
    update_status.reset_for_tests()


@pytest.fixture
def recorder(monkeypatch):
    from src import main

    calls = []

    def _fake_run_update_job(version, version_from):
        calls.append((version, version_from))

    monkeypatch.setattr(main, "_run_update_job", _fake_run_update_job)
    return calls


# --- POST /addin/update -------------------------------------------------------


def test_start_update_success(authed_client, recorder, monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "tok")
    r = authed_client.post(
        "/addin/update",
        data={"version": "v1.14.0"},
        headers=_basic_header("admin", "pw"),
    )
    assert r.status_code == 202
    assert r.json() == {"ok": True, "started": True}
    assert recorder == [("v1.14.0", None)]

    status = update_status.read_status()
    assert status["state"] in (update_status.STATE_RUNNING,)


def test_start_update_without_basic_header_401(authed_client, recorder, monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "tok")
    r = authed_client.post("/addin/update", data={"version": "v1.14.0"})
    assert r.status_code == 401
    assert recorder == []
    status = update_status.read_status()
    assert status["state"] == update_status.STATE_IDLE


def test_start_update_wrong_basic_password_401(authed_client, recorder, monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "tok")
    r = authed_client.post(
        "/addin/update",
        data={"version": "v1.14.0"},
        headers=_basic_header("admin", "falsch"),
    )
    assert r.status_code == 401
    assert recorder == []


def test_start_update_foreign_origin_not_in_allowlist_403(authed_client, recorder, monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "tok")
    r = authed_client.post(
        "/addin/update",
        data={"version": "v1.14.0"},
        headers={**_basic_header("admin", "pw"), "Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert recorder == []


def test_start_update_addin_origin_allowed(authed_client, recorder, monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "tok")
    r = authed_client.post(
        "/addin/update",
        data={"version": "v1.14.0"},
        headers={**_basic_header("admin", "pw"), "Origin": "https://outlook.office.com"},
    )
    assert r.status_code == 202


def test_start_update_invalid_version_format_400(authed_client, recorder, monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "tok")
    r = authed_client.post(
        "/addin/update",
        data={"version": "latest"},
        headers=_basic_header("admin", "pw"),
    )
    assert r.status_code == 400
    assert recorder == []


def test_start_update_conflict_running_fresh_409(authed_client, recorder, monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "tok")
    update_status.write_status(state=update_status.STATE_RUNNING, step="download")
    before = update_status.read_status()

    r = authed_client.post(
        "/addin/update",
        data={"version": "v1.14.0"},
        headers=_basic_header("admin", "pw"),
    )
    assert r.status_code == 409
    assert recorder == []
    after = update_status.read_status()
    assert after == before


def test_start_update_stale_lock_is_taken_over_202(authed_client, recorder, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "tok")
    stale_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    update_status.write_status(state=update_status.STATE_RUNNING, step="health")
    # updated_at auf stale zuruecksetzen (write_status setzt es sonst immer neu).
    import json as _json

    path = update_status._status_path()
    data = _json.loads(path.read_text(encoding="utf-8"))
    data["updated_at"] = stale_time
    path.write_text(_json.dumps(data), encoding="utf-8")

    r = authed_client.post(
        "/addin/update",
        data={"version": "v1.14.0"},
        headers=_basic_header("admin", "pw"),
    )
    assert r.status_code == 202
    assert recorder == [("v1.14.0", None)]


def test_start_update_missing_token_503(authed_client, recorder, monkeypatch):
    monkeypatch.delenv("VIZPATCH_DIST_TOKEN", raising=False)
    from src import config_io

    monkeypatch.setattr(config_io, "read_env_raw", lambda: {})

    r = authed_client.post(
        "/addin/update",
        data={"version": "v1.14.0"},
        headers=_basic_header("admin", "pw"),
    )
    assert r.status_code == 503
    assert recorder == []
    status = update_status.read_status()
    assert status["state"] == update_status.STATE_FAILED
    assert "VIZPATCH_DIST_TOKEN" in status["message"]


def test_start_update_response_never_contains_token(authed_client, recorder, monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "ghp_supersecrettoken")
    r = authed_client.post(
        "/addin/update",
        data={"version": "v1.14.0"},
        headers=_basic_header("admin", "pw"),
    )
    assert "ghp_supersecrettoken" not in r.text


# --- GET /addin/update-status -------------------------------------------------


def test_update_status_default_idle_200(authed_client):
    r = authed_client.get("/addin/update-status")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "state", "step", "progress", "version_from", "version_to",
        "message", "recovery_hint", "stale",
    ):
        assert key in body
    assert body["state"] == update_status.STATE_IDLE
    assert body["stale"] is False


def test_update_status_no_password_required(client, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.delenv("WEBUI_PASSWORD", raising=False)
    r = client.get("/addin/update-status")
    # require_setup verlangt ein gesetztes Passwort -> 403 (Rest-Schutz bleibt),
    # aber KEIN Basic-Auth-Zwang (kein 401 mit WWW-Authenticate).
    assert r.status_code == 403


def test_update_status_stale_true_when_running_and_old(authed_client):
    from datetime import datetime, timedelta, timezone
    import json as _json

    update_status.write_status(state=update_status.STATE_RUNNING, step="health")
    path = update_status._status_path()
    data = _json.loads(path.read_text(encoding="utf-8"))
    data["updated_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    path.write_text(_json.dumps(data), encoding="utf-8")

    r = authed_client.get("/addin/update-status")
    assert r.status_code == 200
    assert r.json()["stale"] is True


def test_update_status_never_returns_500(authed_client, monkeypatch):
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(update_status, "read_status", _boom)
    r = authed_client.get("/addin/update-status")
    assert r.status_code != 500
    assert r.json()["state"] == update_status.STATE_IDLE


# --- auth.require_basic_password ---------------------------------------------


class _FakeRequest:
    """Starlette's `Request.headers` ist case-insensitiv — die einfache
    dict-Fake hier muss dasselbe Verhalten nachbilden, sonst greift
    `headers.get("authorization")` gegen ein groß geschriebenes
    `"Authorization"` nicht."""

    def __init__(self, headers: dict):
        self.headers = {k.lower(): v for k, v in headers.items()}


def test_require_basic_password_correct(monkeypatch):
    monkeypatch.setenv("WEBUI_PASSWORD", auth.hash_password("pw"))
    req = _FakeRequest(_basic_header("admin", "pw"))
    assert auth.require_basic_password(req) == "admin"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer sometoken"},
        {"Authorization": "Basic not-base64!!"},
        {"Authorization": "Basic " + base64.b64encode(b"admin-no-colon").decode()},
    ],
)
def test_require_basic_password_rejects_malformed(monkeypatch, headers):
    monkeypatch.setenv("WEBUI_PASSWORD", auth.hash_password("pw"))
    req = _FakeRequest(headers)
    with pytest.raises(Exception):
        auth.require_basic_password(req)


def test_require_basic_password_wrong_user(monkeypatch):
    monkeypatch.setenv("WEBUI_PASSWORD", auth.hash_password("pw"))
    req = _FakeRequest(_basic_header("someone-else", "pw"))
    with pytest.raises(Exception):
        auth.require_basic_password(req)


def test_require_basic_password_wrong_password(monkeypatch):
    monkeypatch.setenv("WEBUI_PASSWORD", auth.hash_password("pw"))
    req = _FakeRequest(_basic_header("admin", "wrong"))
    with pytest.raises(Exception):
        auth.require_basic_password(req)


# --- docker_ctrl.parse_self_layout / build_updater_run_args -----------------


def test_parse_self_layout_extracts_host_path_volume_and_project():
    attrs = {
        "Mounts": [
            {"Destination": "/compose", "Source": "/opt/vizpatch"},
            {"Destination": "/data", "Name": "vizpatch_agent-data"},
            {"Destination": "/config", "Source": "/opt/vizpatch/config"},
        ],
        "Config": {"Labels": {"com.docker.compose.project": "vizpatch"}},
    }
    layout = docker_ctrl.parse_self_layout(attrs)
    assert layout == {
        "host_deploy_dir": "/opt/vizpatch",
        "data_volume": "vizpatch_agent-data",
        "project": "vizpatch",
    }


def test_parse_self_layout_missing_compose_mount_returns_none():
    attrs = {"Mounts": [{"Destination": "/data", "Name": "vol"}], "Config": {"Labels": {}}}
    assert docker_ctrl.parse_self_layout(attrs) is None


def test_parse_self_layout_project_fallback_to_basename():
    attrs = {
        "Mounts": [{"Destination": "/compose", "Source": "/opt/vizpatch"}],
        "Config": {"Labels": {}},
    }
    layout = docker_ctrl.parse_self_layout(attrs)
    assert layout["project"] == "vizpatch"


def test_build_updater_run_args_mounts_identical_host_path():
    layout = {"host_deploy_dir": "/opt/vizpatch", "data_volume": "vizpatch_agent-data", "project": "vizpatch"}
    args = docker_ctrl.build_updater_run_args(
        layout, image="vizpatch-webui:v1.14.0", source_dir="/data/updates/deployment-paket-v1.14.0",
        version_to="v1.14.0", version_from="v1.13.0", docker_gid="1001",
    )
    joined = " ".join(args)
    assert "/opt/vizpatch:/opt/vizpatch:rw" in joined
    assert "vizpatch_agent-data:/data:rw" in joined
    assert "--deploy" in args and "/opt/vizpatch" in args
    assert "--project" in args and "vizpatch" in args
    assert "--rm" not in args
    assert "--restart" not in args
    assert not any("config" in a for a in args if a not in ("--source", "/data/updates/deployment-paket-v1.14.0"))


def test_build_updater_run_args_no_group_add_without_gid():
    layout = {"host_deploy_dir": "/opt/vizpatch", "data_volume": None, "project": "vizpatch"}
    args = docker_ctrl.build_updater_run_args(
        layout, image="vizpatch-webui:v1.14.0", source_dir="/tmp/x",
        version_to="v1.14.0", version_from="v1.13.0", docker_gid=None,
    )
    assert "--group-add" not in args
    assert "-v" in args
    # Kein Data-Volume-Mount, wenn keiner konfiguriert ist.
    assert not any(":/data:rw" in a for a in args)


# --- GET /addin/installer (260728-m8g Task 6, D-6 Add-in-Selfupdate) --------


def test_addin_installer_requires_basic_password(authed_client, tmp_path, monkeypatch):
    import src.main as main

    bundle = tmp_path / "addin-publish"
    bundle.mkdir()
    (bundle / "setup.exe").write_text("stub", encoding="utf-8")
    monkeypatch.setattr(main, "ADDIN_BUNDLE_DIR", str(bundle))

    r = authed_client.get("/addin/installer")
    assert r.status_code == 401


def test_addin_installer_with_basic_password_returns_zip(authed_client, tmp_path, monkeypatch):
    import src.main as main

    bundle = tmp_path / "addin-publish"
    bundle.mkdir()
    (bundle / "setup.exe").write_text("stub", encoding="utf-8")
    monkeypatch.setattr(main, "ADDIN_BUNDLE_DIR", str(bundle))

    r = authed_client.get("/addin/installer", headers=_basic_header("admin", "pw"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


def test_addin_installer_missing_bundle_404(authed_client, tmp_path, monkeypatch):
    import src.main as main

    monkeypatch.setattr(main, "ADDIN_BUNDLE_DIR", str(tmp_path / "does-not-exist"))
    r = authed_client.get("/addin/installer", headers=_basic_header("admin", "pw"))
    assert r.status_code == 404


def test_addin_installer_session_exempt_no_login_redirect(client, tmp_path, monkeypatch):
    """Reiner Session-Gate-Test: `/addin/`-Praefix ist von der Login-Session
    ausgenommen — ohne Session landet der Request in 401/403 (Basic-Auth bzw.
    require_setup), NICHT im 303-Login-Redirect wie ein normaler Browser-Pfad."""
    from src import auth as auth_module

    monkeypatch.setenv("WEBUI_PASSWORD", auth_module.hash_password("pw"))
    r = client.get("/addin/installer")
    assert r.status_code != 303
