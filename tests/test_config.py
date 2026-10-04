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
    (tmp_path / "abcd1234.0").write_text("x")   # an empty capath doesn't count
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

def test_mono_font_per_platform():
    assert config._mono_font("win32") == "Consolas"
    assert config._mono_font("darwin") == "Menlo"
    assert config._mono_font("linux") == "monospace"
    assert config.MONO == config._mono_font(sys.platform)


# ── notify: alert sound per platform ──────────────────────────────────────────
def test_sound_command_per_platform():
    from gt7telem import notify
    have = {"afplay", "paplay", "aplay"}
    which = lambda name: f"/usr/bin/{name}" if name in have else None  # noqa: E731
    assert notify._sound_command("win32", which) is None
    assert notify._sound_command("darwin", which) == ["afplay", "/System/Library/Sounds/Glass.aiff"]
    assert notify._sound_command("darwin", lambda name: None) is None
    # Linux: first player whose binary AND sound file exist
    assert notify._sound_command("linux", which, exists=lambda p: True) == [
        "paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"]
    only_aplay = lambda p: "alsa" in p  # noqa: E731
    assert notify._sound_command("linux", which, exists=only_aplay) == [
        "aplay", "-q", "/usr/share/sounds/alsa/Front_Center.wav"]
    assert notify._sound_command("linux", lambda name: None, exists=lambda p: True) is None
    # canberra needs no sound file
    assert notify._sound_command("linux", lambda n: n == "canberra-gtk-play", exists=lambda p: False) == [
        "canberra-gtk-play", "-i", "complete"]


def test_beep_falls_back_to_the_tk_bell(monkeypatch):
    from gt7telem import notify
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(notify, "_sound_command", lambda platform: None)
    rang = []
    notify._beep(type("Root", (), {"bell": lambda self: rang.append(1)})())
    assert rang == [1]


def test_beep_plays_the_platform_command_and_never_raises(monkeypatch):
    from gt7telem import notify
    monkeypatch.setattr(sys, "platform", "darwin")
    started = []
    monkeypatch.setattr(notify, "_spawn", lambda cmd: started.append(cmd))
    notify._beep()
    assert started == [["afplay", "/System/Library/Sounds/Glass.aiff"]] or started == []  # afplay absent on CI
    monkeypatch.setattr(notify, "_sound_command", lambda platform: ["nope"])
    monkeypatch.setattr(notify, "_spawn", lambda cmd: (_ for _ in ()).throw(FileNotFoundError("nope")))
    notify._beep(type("Root", (), {"bell": lambda self: None})())   # falls through to the bell, no exception


def test_spawn_restores_the_original_library_path_in_a_frozen_build(monkeypatch):
    from gt7telem import notify
    seen = {}
    monkeypatch.setattr(notify.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, env=kw["env"]))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIxyz")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib/real")
    notify._spawn(["paplay", "x.oga"])
    assert seen["env"]["LD_LIBRARY_PATH"] == "/usr/lib/real"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    notify._spawn(["paplay", "x.oga"])
    assert "LD_LIBRARY_PATH" not in seen["env"]


# ── macOS: label-based buttons ────────────────────────────────────────────────
def test_install_mac_buttons_is_a_noop_off_macos(monkeypatch):
    import tkinter as tk
    monkeypatch.setattr(sys, "platform", "linux")
    original = tk.Button
    assert config.install_mac_buttons() is False
    assert tk.Button is original


def test_install_mac_buttons_swaps_in_the_label_button_on_macos(monkeypatch):
    import tkinter as tk
    monkeypatch.setattr(tk, "Button", tk.Button)   # restored after the test
    monkeypatch.setattr(sys, "platform", "darwin")
    assert config.install_mac_buttons() is True
    assert issubclass(tk.Button, tk.Label) and tk.Button._trace_label_button is True
    swapped = tk.Button
    assert config.install_mac_buttons() is True and tk.Button is swapped   # idempotent


def test_mac_button_behaves_like_a_button():
    import tkinter as tk
    from types import SimpleNamespace
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    try:
        hits = []
        b = config._make_mac_button_class(tk)(
            root, text="Record Lap", bg="#16213e", fg="#ffd500", activebackground="#222",
            command=lambda: hits.append(1))
        b.pack()
        inside = SimpleNamespace(x=0, y=0)
        b._press(inside)
        b._release(inside)
        assert hits == [1]
        b.config(bg="#e94560", text="Stop")           # colours survive hover/disable repaints
        assert (b.cget("bg"), b.cget("text")) == ("#e94560", "Stop")
        b.config(state="disabled")
        b.invoke()
        assert hits == [1] and b["state"] == "disabled" and b.cget("fg") == "#777777"
        b.config(state="normal")
        assert b.cget("fg") == "#ffd500"
        b._enter(inside)
        assert b.cget("bg") == "#222"
        b._leave(inside)
        assert b.cget("bg") == "#e94560"
        b._press(inside)
        b._release(SimpleNamespace(x=999, y=999))      # released away from the button: no click
        assert hits == [1]
    finally:
        root.destroy()
