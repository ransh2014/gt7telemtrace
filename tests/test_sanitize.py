"""Tests for udp.sanitize().

This function turns free-typed car/track names into path segments, so it is
the only thing standing between user input and the filesystem. It had no
coverage at all despite that.
"""
import pytest

from gt7telem.udp import sanitize


@pytest.mark.parametrize("raw,expected", [
    ("Suzuka Circuit", "suzuka_circuit"),
    ("Spa-Francorchamps", "spa_francorchamps"),
    ("GT-R '17", "gt_r_17"),
    ("  Fuji  ", "fuji"),
    ("Le Mans 24h", "le_mans_24h"),
])
def test_normal_names(raw, expected):
    assert sanitize(raw) == expected


@pytest.mark.parametrize("attack", [
    "../../etc/passwd",
    "..\\..\\windows\\system32",
    "/etc/shadow",
    "C:/Windows/System32",
    "lap/../../x",
    "....//....//x",
])
def test_no_path_separators_or_traversal_survive(attack):
    """Every non-alphanumeric char folds to '_', so a result can never contain
    a separator or a '..' segment regardless of input."""
    out = sanitize(attack)
    assert "/" not in out
    assert "\\" not in out
    assert ".." not in out
    assert not out.startswith(".")


@pytest.mark.parametrize("blank", ["", "   ", ".", "..", "...", "___", "!!!", "\t\n"])
def test_input_that_reduces_to_nothing_returns_unknown(blank):
    """Previously these returned "" and laps landed in the laps root instead
    of a per-track folder."""
    assert sanitize(blank) == "unknown"


@pytest.mark.parametrize("reserved", ["con", "CON", "nul", "PRN", "aux", "com1", "LPT9"])
def test_windows_reserved_device_names_are_escaped(reserved):
    """mkdir() on any of these raises OSError on Windows at any path depth."""
    out = sanitize(reserved)
    assert out not in {"con", "nul", "prn", "aux", "com1", "lpt9"}
    assert out.startswith(reserved.lower())


def test_long_names_are_truncated():
    out = sanitize("a" * 500)
    assert 0 < len(out) <= 64


def test_truncation_does_not_leave_a_trailing_underscore():
    out = sanitize("x" * 63 + " tail")
    assert not out.endswith("_")


def test_result_is_always_a_usable_single_segment():
    for raw in ["", "..", "con", "a" * 300, "Nürburgring", "鈴鹿", "///"]:
        out = sanitize(raw)
        assert out, f"empty result for {raw!r}"
        assert "/" not in out and "\\" not in out
