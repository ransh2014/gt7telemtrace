import csv

from gt7telem import tracks
from gt7telem.tracks import all_track_names, get_track_name


def _first_csv_row():
    with open(tracks._CSV_PATH, newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    return int(row["ID"]), (row.get("Name") or "").strip()


def test_get_track_name_matches_shipped_csv():
    track_id, name = _first_csv_row()
    assert name
    assert get_track_name(track_id) == name


def test_get_track_name_unknown_id_returns_empty_string():
    assert get_track_name(-999999) == ""


def test_get_track_name_non_numeric_id_returns_empty_string():
    assert get_track_name("not-an-id") == ""


def test_all_track_names_is_sorted_and_nonempty():
    names = all_track_names()
    assert names
    assert names == sorted(names)
    assert all(n for n in names)  # no blank entries
