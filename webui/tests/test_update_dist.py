"""Tests fuer den Download aus dem privaten Dist-Repo (260728-m8g, Stufe 2,
D-1/D-2). Kein echtes Netzwerk — `httpx.Client` wird ueber `monkeypatch`
gestubbt (Muster `test_addin_update_check.py`)."""
from __future__ import annotations

import zipfile

import httpx
import pytest

import src.update_dist as update_dist


# --- dist_repo / read_dist_token --------------------------------------------


def test_dist_repo_default(monkeypatch):
    monkeypatch.delenv("VIZPATCH_DIST_REPO", raising=False)
    assert update_dist.dist_repo() == "EnverShala/vizpatch-dist"


@pytest.mark.parametrize("raw", ["", "   "])
def test_dist_repo_empty_env_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setenv("VIZPATCH_DIST_REPO", raw)
    assert update_dist.dist_repo() == "EnverShala/vizpatch-dist"


def test_dist_repo_override(monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_REPO", "SomeOrg/other-dist")
    assert update_dist.dist_repo() == "SomeOrg/other-dist"


def test_read_dist_token_from_env(monkeypatch):
    monkeypatch.setenv("VIZPATCH_DIST_TOKEN", "ghp_secret")
    assert update_dist.read_dist_token() == "ghp_secret"


def test_read_dist_token_falls_back_to_config_io(monkeypatch):
    monkeypatch.delenv("VIZPATCH_DIST_TOKEN", raising=False)
    from src import config_io

    monkeypatch.setattr(config_io, "read_env_raw", lambda: {"VIZPATCH_DIST_TOKEN": "from-env-file"})
    assert update_dist.read_dist_token() == "from-env-file"


def test_read_dist_token_env_read_error_returns_empty(monkeypatch):
    monkeypatch.delenv("VIZPATCH_DIST_TOKEN", raising=False)
    from src import config_io

    def _raise():
        raise OSError("nope")

    monkeypatch.setattr(config_io, "read_env_raw", _raise)
    assert update_dist.read_dist_token() == ""


def test_read_dist_token_missing_everywhere_returns_empty(monkeypatch):
    monkeypatch.delenv("VIZPATCH_DIST_TOKEN", raising=False)
    from src import config_io

    monkeypatch.setattr(config_io, "read_env_raw", lambda: {})
    assert update_dist.read_dist_token() == ""


# --- asset_names -------------------------------------------------------------


def test_asset_names():
    zip_name, sha_name = update_dist.asset_names("v1.14.0")
    assert zip_name == "deployment-paket-v1.14.0.zip"
    assert sha_name == "deployment-paket-v1.14.0.zip.sha256"


# --- find_release_assets -----------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeGetClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.last_url = None
        self.last_headers = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        self.last_url = url
        self.last_headers = headers
        if self._exc is not None:
            raise self._exc
        return self._response


def test_find_release_assets_empty_token_no_http_call(monkeypatch):
    called = {"n": 0}

    class _ExplodingClient:
        def __enter__(self):
            called["n"] += 1
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: _ExplodingClient())
    result, status = update_dist.find_release_assets("v1.14.0", "")
    assert result is None
    assert status == "no_token"
    assert called["n"] == 0


def test_find_release_assets_ok(monkeypatch):
    payload = {
        "assets": [
            {"name": "deployment-paket-v1.14.0.zip", "id": 111, "size": 500_000_000},
            {"name": "deployment-paket-v1.14.0.zip.sha256", "id": 222, "size": 90},
            {"name": "something-else.txt", "id": 333, "size": 1},
        ]
    }
    fake = _FakeGetClient(_FakeResponse(200, payload))
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    result, status = update_dist.find_release_assets("v1.14.0", "tok")
    assert status == "ok"
    assert result == {"zip_id": 111, "zip_size": 500_000_000, "sha_id": 222}
    assert "tags/v1.14.0" in fake.last_url
    assert fake.last_headers["Authorization"] == "Bearer tok"


def test_find_release_assets_missing_zip_asset(monkeypatch):
    payload = {"assets": [{"name": "unrelated.zip", "id": 1}]}
    fake = _FakeGetClient(_FakeResponse(200, payload))
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    result, status = update_dist.find_release_assets("v1.14.0", "tok")
    assert result is None
    assert status == "asset_missing"


@pytest.mark.parametrize("code", [401, 403, 404])
def test_find_release_assets_no_access(monkeypatch, code):
    fake = _FakeGetClient(_FakeResponse(code))
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    result, status = update_dist.find_release_assets("v1.14.0", "tok")
    assert result is None
    assert status == "no_access"


def test_find_release_assets_network_error(monkeypatch):
    fake = _FakeGetClient(exc=httpx.ConnectTimeout("timeout"))
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    result, status = update_dist.find_release_assets("v1.14.0", "tok")
    assert result is None
    assert status == "unreachable"


def test_find_release_assets_unexpected_status(monkeypatch):
    fake = _FakeGetClient(_FakeResponse(500))
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    result, status = update_dist.find_release_assets("v1.14.0", "tok")
    assert result is None
    assert status == "unreachable"


def test_find_release_assets_bad_json(monkeypatch):
    class _BadJson(_FakeResponse):
        def json(self):
            raise ValueError("nope")

    fake = _FakeGetClient(_BadJson(200))
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    result, status = update_dist.find_release_assets("v1.14.0", "tok")
    assert result is None
    assert status == "unreachable"


def test_find_release_assets_never_logs_token(monkeypatch, caplog):
    fake = _FakeGetClient(_FakeResponse(500))
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    with caplog.at_level("WARNING"):
        update_dist.find_release_assets("v1.14.0", "ghp_supersecrettoken")
    for record in caplog.records:
        assert "ghp_supersecrettoken" not in record.getMessage()
        assert "ghp_supersecrettoken" not in str(getattr(record, "__dict__", {}))


# --- download_asset (streaming + redirect-Auth-Stripping) --------------------


class _FakeStreamResponse:
    def __init__(self, status_code, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    def iter_bytes(self, chunk_size=1024 * 1024):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeStreamClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, headers=None, follow_redirects=None):
        self.requests.append(
            {"method": method, "url": url, "headers": headers, "follow_redirects": follow_redirects}
        )
        return self._responses.pop(0)


def test_download_asset_direct_200_streams_in_chunks(tmp_path, monkeypatch):
    dest = tmp_path / "out.zip"
    fake = _FakeStreamClient(
        [_FakeStreamResponse(200, headers={"content-length": "8"}, chunks=[b"1234", b"5678"])]
    )
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)

    progress_calls = []
    update_dist.download_asset(42, "tok", dest, on_progress=lambda d, t: progress_calls.append((d, t)))

    assert dest.read_bytes() == b"12345678"
    assert len(progress_calls) == 2
    assert progress_calls[-1] == (8, 8)
    assert fake.requests[0]["headers"]["Authorization"] == "Bearer tok"
    assert fake.requests[0]["follow_redirects"] is False


def test_download_asset_redirect_strips_authorization_header(tmp_path, monkeypatch):
    dest = tmp_path / "out.zip"
    fake = _FakeStreamClient(
        [
            _FakeStreamResponse(302, headers={"location": "https://signed-storage.example/x"}),
            _FakeStreamResponse(200, headers={"content-length": "4"}, chunks=[b"data"]),
        ]
    )
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)

    update_dist.download_asset(42, "tok", dest)

    assert dest.read_bytes() == b"data"
    assert len(fake.requests) == 2
    # Erster Request (an die GitHub-API) traegt den Token.
    assert fake.requests[0]["headers"]["Authorization"] == "Bearer tok"
    # Zweiter Request (an den Signed-Storage-Host) traegt KEINEN Auth-Header.
    assert fake.requests[1]["headers"] is None
    assert fake.requests[1]["url"] == "https://signed-storage.example/x"


def test_download_asset_redirect_without_location_raises(tmp_path, monkeypatch):
    dest = tmp_path / "out.zip"
    fake = _FakeStreamClient([_FakeStreamResponse(302, headers={})])
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    with pytest.raises(RuntimeError):
        update_dist.download_asset(42, "tok", dest)


def test_download_asset_unexpected_status_raises(tmp_path, monkeypatch):
    dest = tmp_path / "out.zip"
    fake = _FakeStreamClient([_FakeStreamResponse(500)])
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    with pytest.raises(RuntimeError):
        update_dist.download_asset(42, "tok", dest)


def test_download_asset_never_logs_or_returns_token(tmp_path, monkeypatch, caplog):
    dest = tmp_path / "out.zip"
    fake = _FakeStreamClient(
        [_FakeStreamResponse(200, headers={"content-length": "2"}, chunks=[b"ok"])]
    )
    monkeypatch.setattr(update_dist.httpx, "Client", lambda **kw: fake)
    with caplog.at_level("WARNING"):
        update_dist.download_asset(1, "ghp_token_value_xyz", dest)
    for record in caplog.records:
        assert "ghp_token_value_xyz" not in record.getMessage()


# --- parse_sha256_file / sha256_of_file / verify_sha256 ----------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("abc123" + "0" * 58 + "  paket.zip\n", "abc123" + "0" * 58),
        ("abc123" + "0" * 58 + " *paket.zip\n", "abc123" + "0" * 58),
        ("abc123" + "0" * 58 + "\n", "abc123" + "0" * 58),
        ("", None),
        ("not a hash at all\n", None),
        ("   \n\n", None),
    ],
)
def test_parse_sha256_file(text, expected):
    assert update_dist.parse_sha256_file(text) == expected


def test_sha256_of_file_and_verify_sha256_roundtrip(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello world" * 1000)
    digest = update_dist.sha256_of_file(path)
    assert len(digest) == 64
    assert update_dist.verify_sha256(path, digest) is True
    assert update_dist.verify_sha256(path, digest.upper()) is True


def test_verify_sha256_mismatch_one_flipped_byte(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello world")
    digest = update_dist.sha256_of_file(path)
    wrong = ("f" if digest[0] != "f" else "0") + digest[1:]
    assert update_dist.verify_sha256(path, wrong) is False


def test_verify_sha256_empty_expected_is_false(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"x")
    assert update_dist.verify_sha256(path, None) is False
    assert update_dist.verify_sha256(path, "") is False


# --- is_safe_zip_member / extract_zip ----------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("../x", False),
        ("/etc/x", False),
        ("a/../../x", False),
        ("paket/x/y.tar", True),
        ("C:\\evil\\path", False),
        ("a\\b", False),
        ("", False),
    ],
)
def test_is_safe_zip_member(name, expected):
    assert update_dist.is_safe_zip_member(name) is expected


def test_extract_zip_happy_path(tmp_path):
    zip_path = tmp_path / "deployment-paket-v1.14.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("deployment-paket-v1.14.0/docker-compose.yml", "services: {}")
        zf.writestr("deployment-paket-v1.14.0/README.md", "hi")

    target = tmp_path / "extracted"
    root = update_dist.extract_zip(zip_path, target)
    assert root == target / "deployment-paket-v1.14.0"
    assert (root / "docker-compose.yml").exists()


def test_extract_zip_slip_raises_and_writes_nothing_outside_target(tmp_path):
    zip_path = tmp_path / "deployment-paket-v1.14.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("deployment-paket-v1.14.0/ok.txt", "fine")
        zf.writestr("../evil.txt", "pwned")

    target = tmp_path / "extracted"
    with pytest.raises(ValueError):
        update_dist.extract_zip(zip_path, target)

    # Nichts ausserhalb des Zielverzeichnisses geschrieben.
    assert not (tmp_path / "evil.txt").exists()
    assert not target.exists() or not any(target.rglob("*"))


def test_extract_zip_missing_root_dir_raises(tmp_path):
    zip_path = tmp_path / "deployment-paket-v1.14.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("unrelated-root/ok.txt", "fine")

    target = tmp_path / "extracted"
    with pytest.raises(FileNotFoundError):
        update_dist.extract_zip(zip_path, target)


# --- has_enough_space ---------------------------------------------------------


def test_has_enough_space_false_when_below_factor():
    assert update_dist.has_enough_space(free_bytes=100, asset_bytes=100) is False


def test_has_enough_space_true_when_well_above_factor():
    assert update_dist.has_enough_space(free_bytes=1000, asset_bytes=100) is True


def test_has_enough_space_zero_asset_bytes_requires_2gib():
    two_gib = 2 * 1024 * 1024 * 1024
    assert update_dist.has_enough_space(free_bytes=two_gib - 1, asset_bytes=0) is False
    assert update_dist.has_enough_space(free_bytes=two_gib + 1, asset_bytes=0) is True
