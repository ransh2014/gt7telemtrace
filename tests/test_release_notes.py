"""The release text comes from CHANGELOG.md: every released version must have a section, and the extractor
must return exactly that section."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("release_notes", ROOT / "tools" / "release_notes.py")
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

SAMPLE = "# Changelog\n\n## [Unreleased]\n\n## [1.2.0] - 2026-01-02\n- new thing\n- other\n\n## [1.1.0] - 2025-12-01\n- old\n"


def test_section_for_picks_only_that_version():
    assert release_notes.section_for("v1.2.0", SAMPLE) == "- new thing\n- other"
    assert release_notes.section_for("1.1.0", SAMPLE) == "- old"


def test_missing_or_empty_section_is_none():
    assert release_notes.section_for("v9.9.9", SAMPLE) is None
    assert release_notes.section_for("Unreleased", SAMPLE) is None


def test_current_version_has_a_changelog_section():
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    body = release_notes.section_for(f"v{version}", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert body, f"CHANGELOG.md has no section for the current version {version}"
