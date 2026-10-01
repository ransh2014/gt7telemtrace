import os
import socket
import ssl
import sys
import urllib.error

import pytest

from gt7telem import config

_REAL_USABLE_FILE = config._usable_file


@pytest.fixture(autouse=True)
def isolated_settings_file(tmp_path, monkeypatch):
    """Point every test at a throwaway settings.json so nothing here ever
    touches the real (or a developer's) settings file on disk."""
    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")


def test_load_returns_defaults_when_file_missing():
    data = config.load()
    assert data["PS_IP"] == "192.168.1.1"
    assert data["KNOWN_IPS"] == []
    assert data["SAMPLE_RATE"] == 10


def test_save_persists_and_load_reflects_it():
    config.save(PS_IP="10.0.0.5")
    assert config._SETTINGS_FILE.exists()
    assert config.load()["PS_IP"] == "10.0.0.5"


def test_save_merges_with_existing_defaults():
    config.save(PS_IP="10.0.0.5")
    data = config.load()
    # untouched keys should still carry their defaults
    assert data["SAMPLE_RATE"] == 10


def test_remember_good_ip_adds_most_recent_first():
    result = config.remember_good_ip("1.1.1.1")
    assert result == ["1.1.1.1"]


def test_remember_good_ip_dedupes_and_moves_to_front():
    config.remember_good_ip("1.1.1.1")
    config.remember_good_ip("2.2.2.2")
    result = config.remember_good_ip("1.1.1.1")
    assert result == ["1.1.1.1", "2.2.2.2"]


def test_remember_good_ip_keeps_all_uncapped():
    ips = ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4", "5.5.5.5"]
    for ip in ips:
        result = config.remember_good_ip(ip)
    assert len(result) == len(ips)
    assert result[0] == "5.5.5.5"
    assert result[-1] == "1.1.1.1"


def test_remember_good_ip_ignores_blank():
    result = config.remember_good_ip("   ")
    assert result == []


# --- CA-certificate fallback (config.ensure_ca_bundle / explain_error) ---

class _Paths:
    def __init__(self, cafile=None, capath=None):
        self.cafile = cafile
        self.capath = capath


@pytest.fixture
def ca_env(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(config, "_usable_file", os.path.isfile)  # tests use dummy files
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


@pytest.mark.usefixtures("ca_env")
def test_leaves_user_setting_alone(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/custom.pem")
    assert config.ensure_ca_bundle() is None
    assert os.environ["SSL_CERT_FILE"] == "/custom.pem"


@pytest.mark.usefixtures("ca_env")
def test_leaves_cert_dir_alone(monkeypatch):
    monkeypatch.setenv("SSL_CERT_DIR", "/custom")
    assert config.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


@pytest.mark.usefixtures("ca_env")
def test_noop_when_default_cafile_exists(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths(str(ca), None))
    assert config.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


@pytest.mark.usefixtures("ca_env")
def test_noop_when_default_capath_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths(None, str(tmp_path)))
    assert config.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


@pytest.mark.usefixtures("ca_env")
def test_picks_first_existing_candidate(monkeypatch, tmp_path):
    good = tmp_path / "good.pem"
    good.write_text("x")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths("/nope", "/nope"))
    monkeypatch.setattr(config, "CA_CANDIDATES", (str(tmp_path / "missing.pem"), str(good)))
    assert config.ensure_ca_bundle() == str(good)
    assert os.environ["SSL_CERT_FILE"] == str(good)


@pytest.mark.usefixtures("ca_env")
def test_certifi_fallback(monkeypatch):
    pytest.importorskip("certifi")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths("/nope", "/nope"))
    monkeypatch.setattr(config, "CA_CANDIDATES", ())
    monkeypatch.setattr(config, "CA_DIR_CANDIDATES", ())
    assert config.ensure_ca_bundle() is not None
    assert os.path.isfile(os.environ["SSL_CERT_FILE"])


@pytest.mark.usefixtures("ca_env")
def test_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths("/nope", "/nope"))
    monkeypatch.setattr(config, "CA_CANDIDATES", ())
    monkeypatch.setattr(config, "CA_DIR_CANDIDATES", ())
    monkeypatch.setitem(sys.modules, "certifi", None)
    assert config.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


@pytest.mark.usefixtures("ca_env")
def test_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(ssl, "get_default_verify_paths", boom)
    assert config.ensure_ca_bundle() is None


@pytest.mark.usefixtures("ca_env")
def test_skipped_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths(None, None))
    monkeypatch.setattr(config, "CA_CANDIDATES", (str(tmp_path),))
    assert config.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


@pytest.mark.usefixtures("ca_env")
def test_uses_hashed_cert_dir_when_no_bundle(monkeypatch, tmp_path):
    (tmp_path / "abcd1234.0").write_text("x")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths("/nope", "/nope"))
    monkeypatch.setattr(config, "CA_CANDIDATES", ())
    monkeypatch.setattr(config, "CA_DIR_CANDIDATES", (str(tmp_path),))
    assert config.ensure_ca_bundle() == str(tmp_path)
    assert os.environ["SSL_CERT_DIR"] == str(tmp_path)

@pytest.mark.usefixtures("ca_env")
def test_garbage_bundle_is_skipped(monkeypatch, tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate")
    assert _REAL_USABLE_FILE(str(bad)) is False
    assert _REAL_USABLE_FILE(str(tmp_path / "missing.pem")) is False


@pytest.mark.usefixtures("ca_env")
def test_real_certifi_bundle_is_usable():
    certifi = pytest.importorskip("certifi")
    assert _REAL_USABLE_FILE(certifi.where()) is True


@pytest.mark.usefixtures("ca_env")
def test_explain_error_categories():
    assert config.explain_error(urllib.error.URLError(ssl.SSLCertVerificationError("x"))) == "certs"
    assert config.explain_error(ssl.SSLError("CERTIFICATE_VERIFY_FAILED")) == "certs"
    assert config.explain_error(urllib.error.URLError(socket.gaierror("no dns"))) == "dns"
    assert config.explain_error(socket.timeout("slow")) == "timeout"
    assert config.explain_error(urllib.error.HTTPError("u", 503, "x", {}, None)) == "http:503"
    assert config.explain_error(OSError("weird")) == "network"
