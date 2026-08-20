"""GT7 car ID -> name lookup.
Data sourced from the ddm999/gt7info community car database (car_ids.csv,
same folder). To refresh: download https://ddm999.github.io/gt7info/data/db/cars.csv
and overwrite car_ids.csv.
"""
import csv
from pathlib import Path

__all__ = ["get_car_name"]

_CSV_PATH = Path(__file__).parent / "car_ids.csv"
_car_names = {}
_loaded = False


def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    cid = int(row["ID"])
                except (KeyError, ValueError, TypeError):
                    continue
                _car_names[cid] = (row.get("ShortName") or "").strip()
    except FileNotFoundError:
        pass


def get_car_name(car_id):
    """Return the car's short name for a given GT7 car_id, or "" if unknown."""
    _load()
    try:
        return _car_names.get(int(car_id), "")
    except (TypeError, ValueError):
        return ""
