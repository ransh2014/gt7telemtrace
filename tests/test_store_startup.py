"""The Microsoft Store build hung at startup: config probed its read-only install folder for writability with
tempfile, which on Windows retries 10,000 random names when access is denied. It must try once and never loop."""
import sys
from pathlib import Path

from gt7telem import config


def test_store_install_folder_is_never_portable(monkeypatch):
    store_exe = r"C:\Program Files\WindowsApps\ransh2014.GT7trace_1.5.2.0_x64__rddnpbedzc7se\TRACE.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", store_exe)
    probed = []
    monkeypatch.setattr(config, "_is_writable_dir", lambda d: probed.append(d) or True)
    assert config._portable_dir() is None
    assert not probed                      # the folder is recognised by its path, never probed at all


def test_writability_probe_tries_once_and_returns_false_when_denied(monkeypatch, tmp_path):
    calls = []

    def denied(*a, **k):
        calls.append(a)
        raise PermissionError("access denied")

    monkeypatch.setattr(config.os, "open", denied)
    assert config._is_writable_dir(tmp_path) is False
    assert len(calls) == 1                 # a single attempt, no retry loop


def test_writability_probe_true_for_a_normal_folder_and_leaves_nothing_behind(tmp_path):
    assert config._is_writable_dir(tmp_path) is True
    assert list(Path(tmp_path).iterdir()) == []
    assert config._is_writable_dir(tmp_path / "missing") is False
