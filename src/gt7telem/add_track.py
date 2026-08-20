"""
add_track.py -- add a missing track to course_ids.csv

Unlike add_car.py, this doesn't need a numeric ID from you: GT7's telemetry
stream doesn't report a track/course ID at all (confirmed -- it's just not
in the packet), so course_ids.csv only exists to back the TRACK dropdown's
name list, not a live lookup. If GT7 adds a new track that isn't in the
picker yet, just type its name here.

Usage: python3 add_track.py
(run from the same folder as course_ids.csv)
"""
import csv
from pathlib import Path

CSV_PATH = Path(__file__).parent / "course_ids.csv"

FIELDNAMES = [
    "ID", "Name", "Base", "Country", "Category", "Length", "LongestStraight",
    "ElevationDiff", "Altitude", "MinTimeH", "MinTimeM", "MinTimeS",
    "MaxTimeH", "MaxTimeM", "MaxTimeS", "LayoutNumber", "IsReverse",
    "PitLaneDelta", "IsOval", "NumCorners", "NoRain",
]


def load_rows():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(rows):
    def sort_key(r):
        try:
            return int(r.get("ID", 0))
        except (TypeError, ValueError):
            return 0
    rows_sorted = sorted(rows, key=sort_key)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows_sorted)


def next_local_id(rows):
    """User-added tracks get negative IDs so they can never collide with a
    real gt7info course ID (those are always positive)."""
    local_ids = []
    for r in rows:
        try:
            v = int(r.get("ID", 0))
        except (TypeError, ValueError):
            continue
        if v < 0:
            local_ids.append(v)
    return (min(local_ids) - 1) if local_ids else -1


def main():
    print("=== Add a track to course_ids.csv ===")
    print(f"File: {CSV_PATH}\n")
    print("GT7's telemetry doesn't report a track ID, so there's nothing to")
    print("look up by number here -- this just adds a name to the TRACK")
    print("dropdown in the Live Dashboard.\n")

    rows = load_rows()
    existing_names = {(r.get("Name") or "").strip().lower() for r in rows}

    name = input("Track Name: ").strip()
    if not name:
        print("Track name can't be empty -- cancelled.")
        return

    if name.lower() in existing_names:
        print(f"\n\"{name}\" is already in course_ids.csv -- nothing to add.")
        return

    new_id = next_local_id(rows)
    row = {k: "" for k in FIELDNAMES}
    row["ID"] = str(new_id)
    row["Name"] = name
    rows.append(row)
    save_rows(rows)

    print(f"\nSaved. \"{name}\" added (local ID {new_id}) -- it'll show up")
    print("in the TRACK dropdown next time you open the Dashboard.")
    print()
    print("This only updated your local course_ids.csv. To push it into the")
    print("shipped apps:")
    print("  1. Run rebuild_all.ps1 (one folder up) -- rebuilds the Windows")
    print("     .exe, Linux binary (via WSL), and source zip.")
    print("  2. Or open a chat with Claude and ask it to rebuild.")


if __name__ == "__main__":
    main()
