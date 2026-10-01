"""Tests for gt7telem._certs.ensure_ca_bundle."""

import os
import ssl
import sys

import pytest

from gt7telem import _certs

_REAL_USABLE_FILE = _certs._usable_file


class _Paths:
    def __init__(self, cafile=None, capath=None):
        self.cafile = cafile
        self.capath = capath


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_certs, "_usable_file", os.path.isfile)  # tests use dummy files
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def test_leaves_user_setting_alone(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/custom.pem")
    assert _certs.ensure_ca_bundle() is None
    assert os.environ["SSL_CERT_FILE"] == "/custom.pem"


def test_leaves_cert_dir_alone(monkeypatch):
    monkeypatch.setenv("SSL_CERT_DIR", "/custom")
    assert _certs.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


def test_noop_when_default_cafile_exists(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths(str(ca), None))
    assert _certs.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


def test_noop_when_default_capath_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths(None, str(tmp_path)))
    assert _certs.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


def test_picks_first_existing_candidate(monkeypatch, tmp_path):
    good = tmp_path / "good.pem"
    good.write_text("x")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths("/nope", "/nope"))
    monkeypatch.setattr(_certs, "CA_CANDIDATES", (str(tmp_path / "missing.pem"), str(good)))
    assert _certs.ensure_ca_bundle() == str(good)
    assert os.environ["SSL_CERT_FILE"] == str(good)


def test_certifi_fallback(monkeypatch):
    pytest.importorskip("certifi")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths("/nope", "/nope"))
    monkeypatch.setattr(_certs, "CA_CANDIDATES", ())
    monkeypatch.setattr(_certs, "CA_DIR_CANDIDATES", ())
    assert _certs.ensure_ca_bundle() is not None
    assert os.path.isfile(os.environ["SSL_CERT_FILE"])


def test_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths("/nope", "/nope"))
    monkeypatch.setattr(_certs, "CA_CANDIDATES", ())
    monkeypatch.setattr(_certs, "CA_DIR_CANDIDATES", ())
    monkeypatch.setitem(sys.modules, "certifi", None)
    assert _certs.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


def test_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(ssl, "get_default_verify_paths", boom)
    assert _certs.ensure_ca_bundle() is None


def test_skipped_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths(None, None))
    monkeypatch.setattr(_certs, "CA_CANDIDATES", (str(tmp_path),))
    assert _certs.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


def test_uses_hashed_cert_dir_when_no_bundle(monkeypatch, tmp_path):
    (tmp_path / "abcd1234.0").write_text("x")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _Paths("/nope", "/nope"))
    monkeypatch.setattr(_certs, "CA_CANDIDATES", ())
    monkeypatch.setattr(_certs, "CA_DIR_CANDIDATES", (str(tmp_path),))
    assert _certs.ensure_ca_bundle() == str(tmp_path)
    assert os.environ["SSL_CERT_DIR"] == str(tmp_path)

def test_garbage_bundle_is_skipped(monkeypatch, tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate")
    assert _REAL_USABLE_FILE(str(bad)) is False
    assert _REAL_USABLE_FILE(str(tmp_path / "missing.pem")) is False


def test_real_certifi_bundle_is_usable():
    certifi = pytest.importorskip("certifi")
    assert _REAL_USABLE_FILE(certifi.where()) is True


def test_explain_error_categories():
    import socket
    import urllib.error

    assert _certs.explain_error(urllib.error.URLError(ssl.SSLCertVerificationError("x"))) == "certs"
    assert _certs.explain_error(ssl.SSLError("CERTIFICATE_VERIFY_FAILED")) == "certs"
    assert _certs.explain_error(urllib.error.URLError(socket.gaierror("no dns"))) == "dns"
    assert _certs.explain_error(socket.timeout("slow")) == "timeout"
    assert _certs.explain_error(urllib.error.HTTPError("u", 503, "x", {}, None)) == "http:503"
    assert _certs.explain_error(OSError("weird")) == "network"
