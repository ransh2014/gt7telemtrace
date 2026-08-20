"""
add_car.py -- add a missing car to car_ids.csv

The car ID -> name lookup (car_ids.csv) is a one-time snapshot from
ddm999's gt7info database. If you're driving a car GT7 added after that
snapshot was taken, it won't have a name yet and will just show as a
raw numeric ID. Run this to add it manually.

Usage: python3 add_car.py
(run from the same folder as car_ids.csv)
"""
import csv
from pathlib import Path

CSV_PATH = Path(__file__).parent / "car_ids.csv"


def load_rows():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(rows):
    rows_sorted = sorted(rows, key=lambda r: int(r["ID"]))
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
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
    print("=== Add a car to car_ids.csv ===")
    print(f"File: {CSV_PATH}\n")

    rows = load_rows()
    existing_by_id = {int(r["ID"]): r for r in rows}

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

    if car_id in existing_by_id:
        rows.remove(existing_by_id[car_id])

    rows.append({"ID": str(car_id), "ShortName": name, "Maker": str(maker_id)})
    save_rows(rows)

    print(f"\nSaved. \"{name}\" is now car ID {car_id} (Maker {maker_id}).")
    print()
    print("This only updated your local car_ids.csv. To push it into the")
    print("shipped apps:")
    print("  1. Run rebuild_all.ps1 (one folder up) -- rebuilds the Windows")
    print("     .exe and the source zip automatically.")
    print("  2. For the Linux zip: open a chat with Claude and ask it to")
    print("     rebuild it -- that one only builds in Claude's sandbox.")


if __name__ == "__main__":
    main()
