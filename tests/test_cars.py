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


# ── local car/track additions survive upgrades ─────────────────────────────
def test_local_car_overlay(tmp_path, monkeypatch):
    from gt7telem import cars, config
    monkeypatch.setattr(config, "_base_dir", lambda: tmp_path)
    (tmp_path / cars.LOCAL_CSV_NAME).write_text("ID,ShortName,Maker\n999001,Test Car '26,1\n", encoding="utf-8")
    monkeypatch.setattr(cars, "_loaded", False)
    monkeypatch.setattr(cars, "_car_names", {})
    assert cars.get_car_name(999001) == "Test Car '26"
    assert cars.get_car_name(24) == "180SX Type X '96"   # shipped list still there


def test_add_car_writes_local_file_only(tmp_path, monkeypatch):
    from gt7telem import add_car, config
    monkeypatch.setattr(config, "_base_dir", lambda: tmp_path)
    shipped_before = add_car.SHIPPED_CSV.read_bytes()
    answers = iter(["999002", "5", "Brand New '26"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    add_car.main()
    assert add_car.SHIPPED_CSV.read_bytes() == shipped_before
    assert "Brand New '26" in (tmp_path / "car_ids_local.csv").read_text(encoding="utf-8")
