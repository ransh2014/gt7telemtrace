"""
leaderboard.py -- global lap leaderboard + crowdsourced car/track ID
submissions (Phase 2 of TRACE's Supabase-backed features).

Anti-cheat runs server-side (see the `validate_lap_submission` Postgres
trigger): physically-impossible times are rejected outright and never
appear anywhere; times that beat the current record by more than 20% are
held in a review queue instead of hitting the public leaderboard
immediately. Both cases still return True from submit_lap() below, since
that return value only means "the request reached Supabase successfully" --
whether it actually made the leaderboard is a separate question, answered
by calling get_top_laps() afterward if you want to check.

Like analytics.py, this module is stdlib-only (urllib), never raises to
the caller, and every function has a short network timeout so a submit
button never hangs the UI waiting on a bad connection.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

__all__ = ["submit_lap", "get_top_laps", "get_lap_samples", "get_consensus_line", "submit_car_id", "submit_track_name"]

_SUPABASE_URL = "https://hignsvyojdqsjoidgkud.supabase.co"
_SUPABASE_ANON_KEY = "sb_publishable_OdzGvcypa0GI7TVxxUkusQ_KJ_Apa8i"


def _headers(prefer=None):
    h = {
        "apikey": _SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {_SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _compact_samples(samples):
    """Strip a full recorded-lap sample list (as saved by dashboard.py, one
    dict per sample with 40+ fields) down to just what the leaderboard and
    future ghost/heatmap features need, keyed by each sample's own
    track_position -- keeps the uploaded payload small."""
    out = []
    for s in samples:
        out.append({
            "track_position": s.get("track_position", 0),
            "speed_kmh":      s.get("speed_kmh", 0),
            "throttle":       s.get("throttle", 0),
            "brake":          s.get("brake", 0),
            "steering":       s.get("steering", 0),
            "gear":           s.get("gear", 0),
        })
    return out


def submit_lap(car_name: str, track_name: str, lap_time_ms: int,
               psn_name: str, samples: list, timeout: float = 8) -> bool:
    """Submit a lap to the global leaderboard. Returns True if the request
    reached Supabase successfully -- this does NOT mean it made the public
    leaderboard, since a flagged or rejected submission also returns True
    here (both are resolved server-side, silently, by design -- see the
    anti-cheat note above). Returns False only on a genuine network/request
    failure (offline, DNS, timeout, malformed response)."""
    payload = {
        "car_name": car_name,
        "track_name": track_name,
        "lap_time_ms": int(lap_time_ms),
        "psn_name": psn_name,
        "samples": _compact_samples(samples),
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/laps",
            data=data, method="POST",
            headers=_headers(prefer="return=minimal"),
        )
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def get_top_laps(car_name: str, track_name: str, n: int = 10, timeout: float = 8) -> list:
    """Return up to `n` fastest public leaderboard laps for this exact
    car+track, sorted ascending by lap time. Each row is a dict:
    {id, car_name, track_name, lap_time_ms, psn_name, created_at}. `id` is
    included so a row can be passed straight to get_lap_samples() for a
    ghost-lap download. Returns [] on any failure (offline, nothing
    submitted yet, etc) -- callers should treat an empty list as "nothing
    to show", not an error."""
    params = urllib.parse.urlencode({
        "car_name": f"eq.{car_name}",
        "track_name": f"eq.{track_name}",
        "select": "id,car_name,track_name,lap_time_ms,psn_name,created_at",
        "order": "lap_time_ms.asc",
        "limit": str(n),
    })
    try:
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/laps?{params}",
            method="GET", headers=_headers(),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []


def get_lap_samples(lap_id: int, timeout: float = 8) -> list:
    """Return the compact sample list stored for one specific leaderboard
    lap (by the numeric `id` returned in get_top_laps() rows) -- used for
    ghost-lap download (Phase 3). Each sample only has the fields
    _compact_samples() keeps (track_position, speed_kmh, throttle, brake,
    steering, gear) -- enough for the input-trace charts and A/B diffs, but
    without world_x/world_z/t there's no GPS track map or replay for a
    downloaded ghost. Returns [] on any failure or if the lap doesn't
    exist."""
    params = urllib.parse.urlencode({
        "id": f"eq.{int(lap_id)}",
        "select": "samples",
        "limit": "1",
    })
    try:
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/laps?{params}",
            method="GET", headers=_headers(),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
            return rows[0]["samples"] if rows else []
    except Exception:
        return []


def get_consensus_line(car_name: str, track_name: str, n: int = 10, timeout: float = 8) -> list:
    """Return the server-computed consensus racing line (Phase 4): the
    average speed/throttle/brake per 10m track-position bucket across the
    top `n` laps for this car+track, computed entirely in Postgres (see
    the `get_consensus_line` SQL function). Each item: {bucket_start,
    avg_speed, avg_throttle, avg_brake, sample_count, lap_count}. Bucketed
    by track_position rather than GPS coordinates because leaderboard
    submissions don't carry world_x/world_z (see _compact_samples).
    Returns [] on any failure or if there's no data yet for this car+track."""
    payload = {"p_car_name": car_name, "p_track_name": track_name, "p_n": n}
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/rpc/get_consensus_line",
            data=data, method="POST", headers=_headers(),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []


def submit_car_id(raw_id, guessed_name: str = None, timeout: float = 8) -> bool:
    """Submit an unrecognized numeric car ID to the shared community inbox,
    instead of it just showing as a blank name until you personally notice
    and add it via add_car.py."""
    return _submit_id({"kind": "car", "raw_id": str(raw_id), "guessed_name": guessed_name}, timeout)


def submit_track_name(name: str, timeout: float = 8) -> bool:
    """Submit a track name that isn't in the local list yet. Unlike cars,
    GT7 never exposes a track ID over telemetry, so there's no numeric ID
    to submit -- `raw_id` is just the name itself."""
    return _submit_id({"kind": "track", "raw_id": name, "guessed_name": None}, timeout)


def _submit_id(payload: dict, timeout: float) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/car_track_submissions",
            data=data, method="POST",
            headers=_headers(prefer="return=minimal"),
        )
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False
