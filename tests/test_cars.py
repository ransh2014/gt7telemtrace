import csv

from gt7telem import cars
from gt7telem.cars import get_car_name


def _first_csv_row():
    with open(cars._CSV_PATH, newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    return int(row["ID"]), (row.get("ShortName") or "").strip()


def test_get_car_name_matches_shipped_csv():
    car_id, name = _first_csv_row()
    assert name  # sanity: the fixture CSV isn't empty/malformed
    assert get_car_name(car_id) == name


def test_get_car_name_accepts_string_id():
    car_id, name = _first_csv_row()
    assert get_car_name(str(car_id)) == name


def test_get_car_name_unknown_id_returns_empty_string():
    assert get_car_name(-999999) == ""


def test_get_car_name_non_numeric_id_returns_empty_string():
    assert get_car_name("not-an-id") == ""
    assert get_car_name(None) == ""
