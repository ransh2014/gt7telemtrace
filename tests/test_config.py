import pytest

from gt7telem import config


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


def test_remember_good_ip_caps_at_max_known_ips():
    for ip in ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"]:
        result = config.remember_good_ip(ip)
    assert len(result) == config.MAX_KNOWN_IPS
    assert result[0] == "4.4.4.4"


def test_remember_good_ip_ignores_blank():
    result = config.remember_good_ip("   ")
    assert result == []
