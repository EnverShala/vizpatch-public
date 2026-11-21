"""Tests fuer die Statusdatei-Zustandsmaschine (260728-m8g, Stufe 2, D-5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import src.update_status as update_status


@pytest.fixture(autouse=True)
def _isolate_status_file(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VIZPATCH_UPDATE_STATUS_FILE", str(tmp_path / "vizpatch_update.json")
    )
    yield
    update_status.reset_for_tests()


def test_read_status_missing_file_returns_idle_skeleton():
    status = update_status.read_status()
    assert status["state"] == update_status.STATE_IDLE
    assert status["step"] == ""
    assert status["progress"] == 0
    assert status["version_from"] is None
    assert status["version_to"] is None
    assert status["updated_at"] is None


def test_read_status_broken_json_returns_idle_skeleton(tmp_path, monkeypatch):
    path = tmp_path / "vizpatch_update.json"
    monkeypatch.setenv("VIZPATCH_UPDATE_STATUS_FILE", str(path))
    path.write_text("{not valid json", encoding="utf-8")
    status = update_status.read_status()
    assert status["state"] == update_status.STATE_IDLE


def test_read_status_non_dict_json_returns_idle_skeleton(tmp_path, monkeypatch):
    path = tmp_path / "vizpatch_update.json"
    monkeypatch.setenv("VIZPATCH_UPDATE_STATUS_FILE", str(path))
    path.write_text("[1, 2, 3]", encoding="utf-8")
    status = update_status.read_status()
    assert status["state"] == update_status.STATE_IDLE


def test_write_status_sets_updated_at_and_persists():
    result = update_status.write_status(state=update_status.STATE_RUNNING, step="preparing")
    assert result["state"] == update_status.STATE_RUNNING
    assert result["updated_at"] is not None

    reread = update_status.read_status()
    assert reread["state"] == update_status.STATE_RUNNING
    assert reread["step"] == "preparing"


def test_write_status_merges_partial_update_keeps_version_to():
    update_status.write_status(state=update_status.STATE_RUNNING, version_to="v1.14.0")
    result = update_status.write_status(step="download", progress=10)
    assert result["version_to"] == "v1.14.0"
    assert result["step"] == "download"
    assert result["progress"] == 10


def test_write_status_is_atomic_no_partial_tmp_left(tmp_path, monkeypatch):
    path = tmp_path / "vizpatch_update.json"
    monkeypatch.setenv("VIZPATCH_UPDATE_STATUS_FILE", str(path))
    update_status.write_status(state=update_status.STATE_RUNNING)
    assert path.exists()
    assert not (tmp_path / "vizpatch_update.json.tmp").exists()


def test_write_status_creates_parent_dir(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "dir" / "vizpatch_update.json"
    monkeypatch.setenv("VIZPATCH_UPDATE_STATUS_FILE", str(path))
    update_status.write_status(state=update_status.STATE_RUNNING)
    assert path.exists()


def test_write_status_swallow_errors(monkeypatch):
    """Ein kaputtgehendes Statusschreiben darf ein laufendes Update nicht
    abbrechen (Konvention wie status_writer.py)."""
    monkeypatch.setattr(
        update_status.os,
        "replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = update_status.write_status(state=update_status.STATE_RUNNING)
    assert result["state"] == update_status.STATE_RUNNING  # kein Crash


@pytest.mark.parametrize(
    "state", [update_status.STATE_IDLE, update_status.STATE_SUCCESS, update_status.STATE_FAILED]
)
def test_can_start_true_for_non_running_states(state):
    status = {"state": state, "updated_at": datetime.now(timezone.utc).isoformat()}
    ok, reason = update_status.can_start(status)
    assert ok is True
    assert reason == ""


def test_can_start_false_running_fresh():
    status = {
        "state": update_status.STATE_RUNNING,
        "step": "download",
        "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    }
    ok, reason = update_status.can_start(status)
    assert ok is False
    assert "läuft" in reason


def test_can_start_true_running_stale():
    status = {
        "state": update_status.STATE_RUNNING,
        "step": "health",
        "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
    }
    ok, reason = update_status.can_start(status)
    assert ok is True
    assert reason == ""


def test_is_stale_running_old_is_true():
    status = {
        "state": update_status.STATE_RUNNING,
        "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
    }
    assert update_status.is_stale(status) is True


def test_is_stale_success_old_is_false():
    status = {
        "state": update_status.STATE_SUCCESS,
        "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
    }
    assert update_status.is_stale(status) is False


def test_is_stale_running_fresh_is_false():
    status = {
        "state": update_status.STATE_RUNNING,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    assert update_status.is_stale(status) is False


@pytest.mark.parametrize("updated_at", [None, "", "not-a-date"])
def test_is_stale_missing_or_unparsable_updated_at_is_true(updated_at):
    status = {"state": update_status.STATE_RUNNING, "updated_at": updated_at}
    assert update_status.is_stale(status) is True


def test_progress_for_download_between_preparing_and_verify():
    preparing = update_status.progress_for("preparing")
    verify = update_status.progress_for("verify")
    download = update_status.progress_for("download", 50, 100)
    assert preparing < download < verify


def test_progress_for_done_is_100():
    assert update_status.progress_for("done") == 100


def test_progress_for_download_unknown_total_does_not_raise():
    assert update_status.progress_for("download", 0, 0) == 5


def test_progress_for_unknown_step_returns_zero():
    assert update_status.progress_for("something-unexpected") == 0
