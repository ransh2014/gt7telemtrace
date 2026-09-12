"""Where a frozen (standalone) build keeps settings.json and laps/.

Portable next to the executable when the user put it somewhere they own;
the per-user folders pip installs use when a package manager or the OS owns
the folder (Chocolatey, WinGet, a macOS .app) or it isn't writable. Nothing
here writes outside pytest's tmp_path.
"""
import sys
from pathlib import Path

import pytest

from gt7telem import config


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pretend to be a frozen build. Returns place(*parts), which puts the
    executable under tmp_path/<parts> and returns its folder."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    def place(*parts):
        exe = tmp_path.joinpath(*parts, "TRACE.exe")
        exe.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(sys, "executable", str(exe))
        return exe.parent.resolve()

    return place


def test_pip_and_source_installs_are_never_portable(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert config._portable_dir() is None


def test_standalone_build_in_a_folder_you_own_stays_portable(frozen):
    folder = frozen("Downloads", "TRACE")
    assert config._portable_dir() == folder
    assert config._default_laps_dir() == folder / "laps"


def test_unwritable_folder_falls_back_to_the_home_folders(frozen, monkeypatch):
    frozen("Program Files", "TRACE")
    monkeypatch.setattr(config, "_is_writable_dir", lambda d: False)
    assert config._portable_dir() is None
    assert config._default_laps_dir() == Path.home() / "TRACE" / "laps"


def test_existing_portable_install_keeps_its_data_where_it_is(frozen, monkeypatch):
    folder = frozen("TRACE")
    (folder / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "_is_writable_dir", lambda d: False)
    assert config._portable_dir() == folder


@pytest.mark.parametrize("parts", [
    ("ProgramData", "chocolatey", "lib", "gt7telem", "tools"),
    ("AppData", "Local", "Microsoft", "WinGet", "Packages", "ransh2014.TRACE_Microsoft.Winget.Source"),
    ("Applications", "TRACE.app", "Contents", "MacOS"),
])
def test_package_manager_and_app_bundle_installs_use_the_home_folders(frozen, parts):
    """Chocolatey's folder is admin-only (settings never saved, lap saves
    failed), and all three are replaced on upgrade."""
    folder = frozen(*parts)
    (folder / "settings.json").write_text("{}", encoding="utf-8")  # even with data already there
    assert config._portable_dir() is None
