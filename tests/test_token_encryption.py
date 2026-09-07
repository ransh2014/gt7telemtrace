"""Tests for the at-rest encryption of Supabase session tokens.

Added in 0.2.9 so that sharing a settings.json (bug report, backup, cloud
sync) doesn't hand over a usable session, but shipped with no tests. The
failure mode that matters is silent: if _decrypt ever stopped round-tripping
it would just return "" and the user would look logged out, with the real
cause invisible.
"""
import importlib

import pytest


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A freshly imported config module rooted at an empty temp home, so the
    real ~/.gt7telem is never touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import gt7telem.config as c
    return importlib.reload(c)


TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"


def test_encrypt_marks_and_changes_the_value(cfg):
    enc = cfg._encrypt(TOKEN)
    assert enc.startswith("enc:v1:")
    assert TOKEN not in enc


def test_roundtrip(cfg):
    assert cfg._decrypt(cfg._encrypt(TOKEN)) == TOKEN


def test_encrypt_is_idempotent(cfg):
    """save() re-encrypts on every write; double-encrypting would corrupt."""
    once = cfg._encrypt(TOKEN)
    assert cfg._encrypt(once) == once


def test_plaintext_tokens_from_before_the_change_still_load(cfg):
    assert cfg._decrypt("legacy-plaintext-token") == "legacy-plaintext-token"


def test_empty_stays_empty(cfg):
    assert cfg._encrypt("") == ""
    assert cfg._decrypt("") == ""


def test_undecryptable_reads_as_logged_out_not_a_crash(cfg):
    """A rotated/lost key must degrade to "signed out", never raise."""
    assert cfg._decrypt("enc:v1:not-valid-base64!!!") == ""


def test_token_is_not_written_to_settings_json_in_plaintext(cfg):
    cfg.save(SUPABASE_ACCESS_TOKEN=TOKEN)
    on_disk = (cfg._base_dir() / "settings.json").read_text(encoding="utf-8")
    assert TOKEN not in on_disk
    assert "enc:v1:" in on_disk


def test_load_transparently_decrypts(cfg):
    cfg.save(SUPABASE_ACCESS_TOKEN=TOKEN)
    assert cfg.load()["SUPABASE_ACCESS_TOKEN"] == TOKEN


def test_key_lives_in_a_separate_file_from_settings(cfg):
    """The whole point: settings.json alone must not be enough to decrypt."""
    cfg.save(SUPABASE_ACCESS_TOKEN=TOKEN)
    key_file = cfg._base_dir() / ".settings.key"
    assert key_file.exists()
    assert len(key_file.read_bytes()) == 32
