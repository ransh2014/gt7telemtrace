"""Settings safety: atomic writes, corrupt/locked files, secrets, type validation."""
import json

import pytest

from gt7telem import config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_KEY_FILE", tmp_path / ".settings.key")


def test_save_returns_true_and_leaves_no_temp_file():
    assert config.save(PS_IP="10.0.0.9") is True
    assert not list(config._SETTINGS_FILE.parent.glob("*.tmp"))
    assert config.load()["PS_IP"] == "10.0.0.9"


def test_save_only_writes_what_it_was_given():
    config.save(PS_IP="10.0.0.9")
    stored = json.loads(config._SETTINGS_FILE.read_text())
    assert stored == {"PS_IP": "10.0.0.9"}      # derived defaults (laps folder) are not frozen in


def test_corrupt_file_is_moved_aside_not_overwritten_silently():
    config._SETTINGS_FILE.write_text("{ not json")
    assert config.load()["PS_IP"] == "192.168.1.1"
    assert config.save(PS_IP="1.2.3.4") is True
    assert (config._SETTINGS_FILE.parent / "settings.json.bad").read_text() == "{ not json"
    assert config.load()["PS_IP"] == "1.2.3.4"


def test_non_object_json_is_treated_as_empty():
    config._SETTINGS_FILE.write_text("[1, 2]")
    assert config.load()["SAMPLE_RATE"] == 10


def test_bom_prefixed_file_loads():
    config._SETTINGS_FILE.write_bytes(b"\xef\xbb\xbf" + json.dumps({"PS_IP": "9.9.9.9"}).encode())
    assert config.load()["PS_IP"] == "9.9.9.9"


def test_unreadable_file_is_never_overwritten(monkeypatch):
    config.save(PS_IP="5.5.5.5")
    real = config.Path.read_text

    def locked(self, *a, **k):
        if self == config._SETTINGS_FILE:
            raise PermissionError("locked")
        return real(self, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(config.Path, "read_text", locked)
        assert config.save(PS_IP="6.6.6.6") is False
    assert config.load()["PS_IP"] == "5.5.5.5"


def test_wrongly_typed_values_fall_back_to_defaults():
    config._SETTINGS_FILE.write_text(json.dumps(
        {"KNOWN_IPS": "1.1.1.1", "SAMPLE_RATE": "fast", "METRICS_PORT": "9200", "DEBUG_LOG": "yes",
         "SUPABASE_ACCESS_TOKEN": 5}))
    d = config.load()
    assert d["KNOWN_IPS"] == []
    assert d["SAMPLE_RATE"] == 10
    assert d["METRICS_PORT"] == 9200
    assert d["DEBUG_LOG"] is False
    assert d["SUPABASE_ACCESS_TOKEN"] == ""


def test_failed_decrypt_does_not_wipe_the_stored_token():
    pytest.importorskip("Crypto")
    config.save(SUPABASE_ACCESS_TOKEN="tok", SUPABASE_REFRESH_TOKEN="ref")
    stored_before = json.loads(config._SETTINGS_FILE.read_text())["SUPABASE_REFRESH_TOKEN"]
    assert stored_before.startswith("enc:v1:")
    config._KEY_FILE.write_bytes(b"x" * 32)           # key changed -> can't decrypt
    assert config.load()["SUPABASE_REFRESH_TOKEN"] == ""
    config.save(PS_IP="7.7.7.7")                       # unrelated save
    assert json.loads(config._SETTINGS_FILE.read_text())["SUPABASE_REFRESH_TOKEN"] == stored_before


def test_secrets_round_trip_and_are_not_plaintext_on_disk():
    pytest.importorskip("Crypto")
    config.save(SUPABASE_ACCESS_TOKEN="abc.def", SUPABASE_USER_ID="u-1")
    assert "abc.def" not in config._SETTINGS_FILE.read_text()
    d = config.load()
    assert d["SUPABASE_ACCESS_TOKEN"] == "abc.def" and d["SUPABASE_USER_ID"] == "u-1"


def test_odd_length_key_file_is_not_replaced():
    config._KEY_FILE.write_bytes(b"short")
    assert config._get_or_create_key() == b""
    assert config._KEY_FILE.read_bytes() == b"short"


def test_key_file_created_once_with_32_bytes():
    k1 = config._get_or_create_key()
    k2 = config._get_or_create_key()
    assert len(k1) == 32 and k1 == k2


def test_backup_laps_skips_its_own_backups_and_reports_errors(tmp_path, monkeypatch):
    laps = tmp_path / "laps"
    (laps / "Track").mkdir(parents=True)
    (laps / "Track" / "a.json").write_text("{}")
    config.save(LAPS_FOLDER=str(laps))
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    out = config.backup_laps()
    assert out is not None and out.exists() and config.last_backup_error is None
    import zipfile
    assert zipfile.ZipFile(out).namelist() == ["Track/a.json"]
    config.save(LAPS_FOLDER=str(tmp_path / "nothing-here"))
    assert config.backup_laps() is None and config.last_backup_error is None


def test_handshake_ssl_errors_are_not_blamed_on_certs():
    import ssl
    assert config.explain_error(ssl.SSLError("handshake timed out")) == "network"
