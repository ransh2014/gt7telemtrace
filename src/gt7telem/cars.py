"""GT7 car ID -> name lookup.
Data sourced from the ddm999/gt7info community car database (car_ids.csv,
same folder). To refresh: download https://ddm999.github.io/gt7info/data/db/cars.csv
and overwrite car_ids.csv.

Cars added with `gt7telem-add-car` go to car_ids_local.csv in the per-user
settings folder instead (see add_car.py) and are layered on top, so they
survive upgrades -- handy for the gap between a GT7 update adding cars and
a TRACE release shipping the refreshed CSV.
"""
import csv
import threading
from pathlib import Path

__all__ = ["get_car_name"]

_CSV_PATH = Path(__file__).parent / "car_ids.csv"
LOCAL_CSV_NAME = "car_ids_local.csv"


def local_csv_path() -> Path:
    from . import config  # lazy: keeps a plain `import gt7telem.cars` side-effect free
    return config._base_dir() / LOCAL_CSV_NAME
_car_names: dict[int, str] = {}
_loaded = False


_load_lock = threading.Lock()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    with _load_lock:               # two threads must not half-fill the dict for each other
        if not _loaded:
            _read_all()
            _loaded = True


def _read_all() -> None:
    paths = [_CSV_PATH]
    try:
        paths.append(local_csv_path())
    except Exception:
        pass  # no usable settings folder -- shipped list only
    for path in paths:   # local entries override shipped ones
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    try:
                        cid = int(row["ID"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    name = (row.get("ShortName") or "").strip()
                    if name:
                        _car_names[cid] = name
        except (OSError, csv.Error, UnicodeDecodeError):
            pass


def get_car_name(car_id: int | str) -> str:
    """Return the car's short name for a given GT7 car_id, or "" if unknown."""
    _load()
    try:
        return _car_names.get(int(car_id), "")
    except (TypeError, ValueError):
        return ""
