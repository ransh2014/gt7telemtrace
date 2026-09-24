"""
add_car.py -- add a missing car to car_ids.csv

The car ID -> name lookup (car_ids.csv) is a one-time snapshot from
ddm999's gt7info database. If you're driving a car GT7 added after that
snapshot was taken, it won't have a name yet and will just show as a
raw numeric ID. Run this to add it manually.

Additions go to car_ids_local.csv in TRACE's per-user settings folder
(~/.gt7telem, or next to a portable exe), not into the installed package:
that folder isn't writable under Chocolatey/system installs, and pip/WinGet
upgrades replace it. The Dashboard layers the local file on top.

Usage (any install):  gt7telem-add-car
   or:              python -m gt7telem.add_car
"""
import csv
from pathlib import Path

from . import cars

SHIPPED_CSV = Path(__file__).parent / "car_ids.csv"


def _read(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_rows():
    """Shipped rows overlaid with local ones -- what the Dashboard sees."""
    merged = {}
    for r in _read(SHIPPED_CSV) + _read(cars.local_csv_path()):
        try:
            merged[int(r["ID"])] = r
        except (KeyError, ValueError, TypeError):
            continue
    return list(merged.values())


def save_rows(rows):
    """Write only the local additions file."""
    path = cars.local_csv_path()
    rows_sorted = sorted(rows, key=lambda r: int(r["ID"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ID", "ShortName", "Maker"])
        writer.writeheader()
        writer.writerows(rows_sorted)


def prompt_int(label):
    while True:
        raw = input(f"{label}: ").strip()
        try:
            return int(raw)
        except ValueError:
            print("  please enter a whole number")


def main():
    local_path = cars.local_csv_path()
    print("=== Add a car to TRACE's car list ===")
    print(f"File: {local_path}\n")

    existing_by_id = {int(r["ID"]): r for r in load_rows()}
    local_rows = _read(local_path)

    car_id = prompt_int("Car ID (the numeric id GT7 reports in telemetry)")

    if car_id in existing_by_id:
        row = existing_by_id[car_id]
        print(f"\nCar ID {car_id} already exists: "
              f"\"{row['ShortName']}\" (Maker {row['Maker']})")
        choice = input("Overwrite it? [y/N]: ").strip().lower()
        if choice != "y":
            print("Cancelled -- nothing changed.")
            return

    maker_id = prompt_int("Manufacturer ID (numeric Maker id)")
    name = input("Car Name: ").strip()

    if not name:
        print("Car name can't be empty -- cancelled.")
        return

    local_rows = [r for r in local_rows if str(r.get("ID")) != str(car_id)]
    local_rows.append({"ID": str(car_id), "ShortName": name, "Maker": str(maker_id)})
    try:
        save_rows(local_rows)
    except OSError as e:
        print(f"\nCouldn't write {local_path}: {e}")
        return

    print(f"\nSaved. \"{name}\" is now car ID {car_id} (Maker {maker_id}).")
    print()
    print("It's saved in your own settings folder, so it survives TRACE")
    print("upgrades -- the name will show up next time you open the Dashboard.")
    print()
    print("To get the car into the official database for everyone, open an")
    print("issue with the ID and the in-game name:")
    print("  https://github.com/ransh2014/gt7telemtrace/issues")


if __name__ == "__main__":
    main()
