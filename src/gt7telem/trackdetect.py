"""Track auto-detect from the car's world position.

GT7's telemetry has no track ID, but a lap's start/finish point and the direction
you leave it in are a stable fingerprint of a layout (the same point to within a few
metres lap after lap). This module:

* learns that fingerprint whenever a lap is saved on a known layout (local file
  track_fingerprints.json in the settings folder, and optionally a shared table),
* matches a new lap's start point + heading against everything learned, and
* for layouts never seen, shortlists candidates from the lap's length using the
  course_ids.csv metadata.

It never decides silently: callers always show the guess for confirmation.
Pure logic, no Tk, so it can be tested headlessly.
"""
import csv
import json
import math
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from . import config as _cfg

STORE_NAME = "track_fingerprints.json"
SHARED_NAME = "track_fingerprints_shared.json"

POS_TOL_M = 15.0          # start point within this of a learned one
HEADING_TOL_DEG = 30.0    # leaving the line within this of the learned heading
MIN_DRIVE_M = 20.0        # distance driven before the heading is trustworthy
LEN_TOL = 0.02            # lap length vs the metadata Length column (races read ~1.6% short)
LEARN_LEN_TOL = 0.05      # don't learn from a lap that is >5% off the layout length
MAX_OBS = 8               # observations kept per layout
DEDUPE_M, DEDUPE_DEG = 5.0, 10.0

_COURSE_CSV = Path(__file__).parent / "course_ids.csv"


# ── geometry ────────────────────────────────────────────────────────────────
def _xz(s):
    return float(s.get("world_x") or 0.0), float(s.get("world_z") or 0.0)


def angle_diff(a, b):
    """Smallest absolute difference between two headings, degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def fingerprint_of(samples, min_drive_m=MIN_DRIVE_M):
    """{"x","z","heading"} for the start of a lap, or None when there isn't enough
    positional data yet (no coordinates, or the car hasn't moved min_drive_m)."""
    if len(samples) < 2:
        return None
    x0, z0 = _xz(samples[0])
    if x0 == 0.0 and z0 == 0.0:
        return None
    for s in samples[1:]:
        x, z = _xz(s)
        if math.hypot(x - x0, z - z0) >= min_drive_m:
            return {"x": round(x0, 1), "z": round(z0, 1),
                    "heading": round(math.degrees(math.atan2(z - z0, x - x0)) % 360.0, 1)}
    return None


def rollover_indexes(samples, limit=3):
    """Indexes where the lap counter ticks up: the car is crossing the start line
    there, which is what lets a race (grid start) be recognised too."""
    out, prev = [], None
    for i, s in enumerate(samples):
        try:
            lap = int(s.get("lap_number"))
        except (TypeError, ValueError):
            continue
        if prev is not None and lap > prev:
            out.append(i)
            if len(out) >= limit:
                break
        prev = lap
    return out


def race_fingerprints(samples):
    """Start-line fingerprints taken at the first two line crossings of a race (the
    first can be the green flag on the grid, so both are tried)."""
    out = []
    for i in rollover_indexes(samples, 2):
        fp = fingerprint_of(samples[i:i + 1500])
        if fp:
            out.append(fp)
    return out


def race_lap_lengths(samples):
    """Measured lengths of the full laps between consecutive line crossings."""
    idx = rollover_indexes(samples, 3)
    return [lap_length(samples[a:b]) for a, b in zip(idx, idx[1:])]


def lap_length(samples):
    """Driven distance in metres from the world coordinates (0.0 if unavailable)."""
    if len(samples) < 2:
        return 0.0
    total = 0.0
    px, pz = _xz(samples[0])
    for s in samples[1:]:
        x, z = _xz(s)
        total += math.hypot(x - px, z - pz)
        px, pz = x, z
    return total


# ── layout metadata (course_ids.csv) ────────────────────────────────────────
_layouts_cache = None


def layouts():
    """{name: {"id","length","reverse","base"}} from course_ids.csv."""
    global _layouts_cache
    if _layouts_cache is not None:
        return _layouts_cache
    out = {}
    try:
        with open(_COURSE_CSV, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("Name") or "").strip()
                try:
                    length = float(row.get("Length"))
                except (TypeError, ValueError):
                    continue
                if not name or length <= 0:
                    continue
                try:
                    cid = int(row.get("ID"))
                except (TypeError, ValueError):
                    cid = None
                out[name] = {"id": cid, "length": length,
                             "reverse": (row.get("IsReverse") or "").strip() == "1",
                             "base": (row.get("Base") or "").strip()}
    except (OSError, csv.Error, UnicodeDecodeError):
        out = {}
    _layouts_cache = out
    return out


def reverse_twin(name):
    """The one layout that is `name` driven the other way (same venue and length,
    opposite direction), or None when there isn't exactly one."""
    lay = layouts().get(name)
    if not lay or not lay["base"]:
        return None
    twins = [n for n, o in layouts().items()
             if n != name and o["base"] == lay["base"] and o["reverse"] != lay["reverse"]
             and abs(o["length"] - lay["length"]) <= 0.005 * lay["length"]]
    return twins[0] if len(twins) == 1 else None


def shortlist_by_length(length_m, tol=LEN_TOL, limit=6):
    """Layout names whose official length is within tol of the measured lap,
    closest first. Works for layouts nobody has driven yet."""
    if length_m <= 0:
        return []
    hits = [(abs(o["length"] - length_m) / o["length"], n) for n, o in layouts().items()
            if abs(o["length"] - length_m) / o["length"] <= tol]
    return [n for _d, n in sorted(hits)][:limit]


# ── learned fingerprints ─────────────────────────────────────────────────────
def _base_dir():
    return _cfg._base_dir()


def _near(a, b):
    return (math.hypot(a["x"] - b["x"], a["z"] - b["z"]) <= DEDUPE_M
            and angle_diff(a["heading"], b["heading"]) <= DEDUPE_DEG)


class FingerprintStore:
    """{layout name: [observations]} saved as JSON. Thread-safe."""

    def __init__(self, path=None, shared_path=None):
        self.path = Path(path) if path else _base_dir() / STORE_NAME
        self.shared_path = Path(shared_path) if shared_path else _base_dir() / SHARED_NAME
        self._lock = threading.Lock()
        self.local = self._read(self.path)
        self.shared = self._read(self.shared_path)

    @staticmethod
    def _read(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write(p, data):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(p)
            return True
        except OSError:
            return False

    def add(self, name, fp):
        """Remember one start fingerprint for a layout. True when it was new."""
        with self._lock:
            obs = self.local.setdefault(name, [])
            if any(_near(o, fp) for o in obs):
                return False
            obs.append({"x": fp["x"], "z": fp["z"], "heading": fp["heading"]})
            del obs[:-MAX_OBS]
            self._write(self.path, self.local)
            return True

    def set_shared(self, rows):
        """rows: [{"layout_name","x","z","heading"}] from the shared table."""
        shared = {}
        for r in rows or []:
            try:
                shared.setdefault(str(r["layout_name"]), []).append(
                    {"x": float(r["x"]), "z": float(r["z"]), "heading": float(r["heading"])})
            except (KeyError, TypeError, ValueError):
                continue
        with self._lock:
            self.shared = shared
            self._write(self.shared_path, shared)

    def _all(self):
        """(name, obs) pairs, local + shared, plus the reverse-direction twin of each."""
        with self._lock:
            sources = [self.local, self.shared]
        for src in sources:
            for name, obs in src.items():
                for o in obs:
                    yield name, o
                    twin = reverse_twin(name)
                    if twin:
                        yield twin, {"x": o["x"], "z": o["z"], "heading": (o["heading"] + 180.0) % 360.0}

    def match(self, fp):
        """[(layout name, distance m)] whose learned start matches fp, best first."""
        if not fp:
            return []
        best = {}
        for name, o in self._all():
            d = math.hypot(o["x"] - fp["x"], o["z"] - fp["z"])
            if d <= POS_TOL_M and angle_diff(o["heading"], fp["heading"]) <= HEADING_TOL_DEG:
                if name not in best or d < best[name]:
                    best[name] = d
        return sorted(best.items(), key=lambda kv: kv[1])

    def known_layouts(self):
        return sorted(set(self.local) | set(self.shared))


def can_learn(name, samples):
    """A lap may teach TRACE its layout only when the layout is a real one and the
    lap's length agrees with the official length (guards against a wrong pick)."""
    lay = layouts().get(name)
    if not lay:
        return False
    n = lap_length(samples)
    return n > 0 and abs(n - lay["length"]) / lay["length"] <= LEARN_LEN_TOL


# ── shared table (Supabase) ──────────────────────────────────────────────────
_URL = "https://hignsvyojdqsjoidgkud.supabase.co"
_KEY = "sb_publishable_OdzGvcypa0GI7TVxxUkusQ_KJ_Apa8i"


def _headers():
    return {"apikey": _KEY, "Authorization": f"Bearer {_KEY}", "Content-Type": "application/json"}


def fetch_shared(timeout=6.0):
    """All shared fingerprints, or None when offline / the table isn't there."""
    try:
        q = urllib.parse.urlencode({"select": "layout_name,x,z,heading", "limit": "5000"})
        req = urllib.request.Request(f"{_URL}/rest/v1/track_fingerprints?{q}", headers=_headers())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, list) else None
    except Exception:
        return None


def submit_shared(name, fp, length_m, timeout=6.0):
    """Share one confirmed start fingerprint (layout name, start x/z, heading, length).
    No personal data. True on success; never raises."""
    try:
        lay = layouts().get(name) or {}
        body = json.dumps({"p_name": name, "p_course_id": lay.get("id"), "p_x": fp["x"],
                           "p_z": fp["z"], "p_heading": fp["heading"],
                           "p_length": round(float(length_m), 1)}).encode("utf-8")
        req = urllib.request.Request(f"{_URL}/rest/v1/rpc/submit_track_fingerprint",
                                     data=body, headers=_headers(), method="POST")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False
