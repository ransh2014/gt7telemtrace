"""Tests for leaderboard.py's pure helpers.

No network here -- only the two functions that shape a request or a payload.
Everything else in that module is a urllib call best exercised for real.
"""
import json

import pytest

from gt7telem import leaderboard
from gt7telem.leaderboard import _compact_samples, _eq, lap_submission_error


def test_eq_quotes_the_value():
    assert _eq("Suzuka") == 'eq."Suzuka"'


def test_eq_survives_a_comma():
    """Unquoted, PostgREST treats a comma as the end of the filter value, so
    a name containing one would silently match the wrong rows."""
    assert _eq("Foo, Bar") == 'eq."Foo, Bar"'


def test_eq_leaves_apostrophes_alone():
    """Real car names are full of these -- GT-R '17, Mine's, 911 GT3 R '22."""
    assert _eq("GT-R '17") == 'eq."GT-R \'17"'


def test_eq_escapes_double_quotes_and_backslashes():
    assert _eq('a"b') == 'eq."a\\"b"'
    assert _eq("a\\b") == 'eq."a\\\\b"'


@pytest.mark.parametrize("value", ["Suzuka", "Foo, Bar", "GT-R '17", 'a"b', "a\\b", ""])
def test_eq_always_produces_one_balanced_quoted_token(value):
    out = _eq(value)
    assert out.startswith('eq."')
    assert out.endswith('"')


KEPT = {"track_position", "speed_kmh", "throttle", "brake", "steering", "gear"}


def test_compact_samples_keeps_only_the_six_documented_fields():
    """The privacy page lists exactly these. Anything else leaking in would
    make that page wrong."""
    full = {
        "track_position": 0.5, "speed_kmh": 180.0, "throttle": 0.9,
        "brake": 0.0, "steering": -0.2, "gear": 4,
        # must NOT be uploaded:
        "world_x": 123.0, "world_z": 456.0, "rpm": 6000,
        "tyre_temp_fl": 90.0, "fuel_remaining": 42.0, "t": 12.5,
    }
    out = _compact_samples([full])
    assert set(out[0]) == KEPT


def test_compact_samples_drops_gps_coordinates():
    out = _compact_samples([{"world_x": 1.0, "world_z": 2.0, "speed_kmh": 100}])
    assert "world_x" not in out[0]
    assert "world_z" not in out[0]


def test_compact_samples_defaults_missing_fields_to_zero():
    out = _compact_samples([{}])
    assert out[0] == dict.fromkeys(KEPT, 0)


def test_compact_samples_handles_an_empty_lap():
    assert _compact_samples([]) == []


def test_compact_output_is_json_serialisable():
    out = _compact_samples([{"speed_kmh": 1.5, "gear": 3}])
    json.dumps(out)


COMPLETE_LAP = {"lap_time_s": 92.345, "samples": [{"speed_kmh": 100.0}], "incomplete": False}


def test_complete_lap_can_be_submitted():
    assert lap_submission_error(COMPLETE_LAP) is None


def test_lap_files_from_before_the_incomplete_flag_are_accepted():
    lap = {k: v for k, v in COMPLETE_LAP.items() if k != "incomplete"}
    assert lap_submission_error(lap) is None


def test_incomplete_lap_is_refused():
    """An incomplete lap's time only covers part of a lap -- submitting it
    would post a bogus record."""
    assert "incomplete" in lap_submission_error({**COMPLETE_LAP, "incomplete": True}).lower()


@pytest.mark.parametrize("bad", [
    {"samples": []}, {"lap_time_s": 0}, {"lap_time_s": None}, {"lap_time_s": "abc"},
])
def test_lap_without_samples_or_a_time_is_refused(bad):
    assert lap_submission_error({**COMPLETE_LAP, **bad})


# ── GT7 1.71 physics era ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("stamp,era", [
    ("20260819_235959", "pre-1.71"),
    ("20260820_000000", "1.71"),
    ("20260923_101500", "1.71"),
    ("20250101_120000", "pre-1.71"),
    ("", None),
    (None, None),
    ("garbage", None),
])
def test_physics_era_of(stamp, era):
    assert leaderboard.physics_era_of(stamp) == era


def test_stored_tag_wins_over_date():
    assert leaderboard.is_current_era({"physics_era": leaderboard.PHYSICS_ERA, "recorded_at": "20200101_000000"})
    assert not leaderboard.is_current_era({"physics_era": "1.60", "recorded_at": "20990101_000000"})


def test_unknown_date_counts_as_current():
    assert leaderboard.is_current_era({})


def _lap(recorded_at):
    return {"recorded_at": recorded_at, "lap_time_s": 90.0, "samples": [{"t": 0}]}


def test_pre_171_lap_cannot_be_submitted():
    msg = lap_submission_error(_lap("20260801_120000"))
    assert msg and "1.71" in msg


def test_post_171_lap_can_be_submitted():
    assert lap_submission_error(_lap("20260901_120000")) is None
