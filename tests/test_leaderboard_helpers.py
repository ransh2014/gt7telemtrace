"""Tests for leaderboard.py's pure helpers.

No network here -- only the two functions that shape a request or a payload.
Everything else in that module is a urllib call best exercised for real.
"""
import json

import pytest

from gt7telem.leaderboard import _compact_samples, _eq


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
