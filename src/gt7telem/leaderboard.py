"""
leaderboard.py -- global lap leaderboard + crowdsourced car/track ID
submissions (Phase 2 of TRACE's Supabase-backed features).

Anti-cheat runs server-side (see the `validate_lap_submission` Postgres
trigger): physically-impossible times are rejected outright and never
appear anywhere; times that beat the current record by more than 20% are
held in a review queue instead of hitting the public leaderboard
immediately. Both cases still return (True, "ok") from submit_lap() below,
since that success value only means "the request reached Supabase
successfully" -- whether it actually made the leaderboard is a separate
question, answered by calling get_top_laps() afterward if you want to
check.

Like analytics.py, this module is stdlib-only (urllib), never raises to
the caller, and every function has a short network timeout so a submit
button never hangs the UI waiting on a bad connection.
"""
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

__all__ = ["submit_lap", "get_top_laps", "get_top_lap_ghost", "get_lap_samples", "get_consensus_line", "submit_car_id", "submit_track_name",
           "lap_submission_error", "PHYSICS_ERA", "PHYSICS_EPOCH", "physics_era_of", "is_current_era"]

_SUPABASE_URL = "https://hignsvyojdqsjoidgkud.supabase.co"
_SUPABASE_ANON_KEY = "sb_publishable_OdzGvcypa0GI7TVxxUkusQ_KJ_Apa8i"


# ── GT7 physics era ─────────────────────────────────────────────────────────
# GT7's telemetry packet carries no game version, so anything that depends on
# which physics model a lap was driven under has to go by date instead.
#
# Update 1.71 (20 Aug 2026) overhauled the physics: the tyre model (slip,
# rolling resistance, heating and wear), steering geometry, damper curves,
# engine torque delivery, aero ranges and TCS/ABS behaviour all changed, and
# Polyphony reset every official leaderboard because of it. Lap times from
# before that date aren't comparable with ones after it, so TRACE treats them
# as a separate "physics era": they can't replace a newer reference lap or
# personal best, can't be submitted to the leaderboard, and don't show on it.
# The Supabase side (anti-cheat record check, consensus line) and the
# website's leaderboard page use the same date.
#
# When a future update changes physics again, bump PHYSICS_EPOCH and
# PHYSICS_ERA here (and in leaderboard.html + the two SQL functions).

PHYSICS_ERA = "1.71"
# Rolled out ~06:00 UTC; midnight UTC is close enough and errs toward
# treating a few pre-patch laps that day as current rather than the reverse.
PHYSICS_EPOCH = datetime(2026, 8, 20, tzinfo=timezone.utc)
PHYSICS_EPOCH_ISO = PHYSICS_EPOCH.strftime("%Y-%m-%dT%H:%M:%SZ")
_EPOCH_STAMP = PHYSICS_EPOCH.strftime("%Y%m%d_%H%M%S")  # same shape as lap files' recorded_at


def physics_era_of(recorded_at) -> str | None:
    """Era label for a saved lap/race's `recorded_at` stamp ("YYYYmmdd_HHMMSS",
    local time as the Dashboard writes it). Returns PHYSICS_ERA for laps on or
    after the epoch, "pre-" + PHYSICS_ERA before it, or None if the stamp is
    missing/unparseable (unknown -- callers treat that as current, so old
    files without a stamp aren't locked out)."""
    s = str(recorded_at or "").strip()
    if len(s) < 8 or not s[:8].isdigit():
        return None
    return PHYSICS_ERA if s >= _EPOCH_STAMP[: len(s)] else f"pre-{PHYSICS_ERA}"


def is_current_era(data: dict) -> bool:
    """True unless a lap/race dict is known to be from before PHYSICS_EPOCH.
    A stored "physics_era" tag wins over the date, so a file saved by a
    TRACE version that knew the era is taken at its word."""
    tag = data.get("physics_era")
    if tag:
        return tag == PHYSICS_ERA
    era = physics_era_of(data.get("recorded_at"))
    return era is None or era == PHYSICS_ERA


def _headers(prefer=None, access_token=None):
    """access_token, when given, is used as the Authorization bearer instead
    of the anon key -- needed for any write that RLS checks auth.uid()
    against (e.g. submitting a lap). apikey stays the anon key either way;
    Supabase requires it alongside a user JWT, not instead of one."""
    h = {
        "apikey": _SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {access_token or _SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _eq(value: str) -> str:
    """PostgREST `eq.` filter value, safely quoted.

    Unquoted values terminate at a comma, so a car or track name containing
    one would silently truncate the filter and match the wrong rows (or
    nothing). Double quotes make it a literal; inner quotes and backslashes
    are escaped. urlencode() then percent-encodes the result."""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'eq."{escaped}"'


def _compact_samples(samples):
    """Strip a full recorded-lap sample list (as saved by dashboard.py, one
    dict per sample with 40+ fields) down to just what the leaderboard and
    future ghost/heatmap features need, keyed by each sample's own
    track_position -- keeps the uploaded payload small."""
    def num(v):
        # JSON has no NaN/Infinity (the server rejects them with a 400), and
        # numpy scalars aren't serialisable -- coerce to a plain finite float.
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0
        return f if math.isfinite(f) else 0

    out = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        out.append({
            "track_position": num(s.get("track_position", 0)),
            "speed_kmh":      num(s.get("speed_kmh", 0)),
            "throttle":       num(s.get("throttle", 0)),
            "brake":          num(s.get("brake", 0)),
            "steering":       num(s.get("steering", 0)),
            "gear":           num(s.get("gear", 0)),
        })
    return out


def lap_submission_error(lap: dict) -> str | None:
    """Why a recorded lap (a saved lap file's dict) can't go on the
    leaderboard, or None if it can. Incomplete laps are refused: they were
    stopped before the line, or saved when the Dashboard closed, so their
    lap_time_s is only the elapsed part of a lap -- submitting half a lap
    would post a bogus "record". Lap files from before the `incomplete`
    flag existed are treated as complete."""
    if lap.get("incomplete"):
        return ("This lap is incomplete -- it was stopped before the line (or saved when "
                "the Dashboard closed), so its time only covers part of a lap. Only "
                "complete laps can be submitted.")
    if not lap.get("samples"):
        return "This lap has no samples to submit."
    if not is_current_era(lap):
        return (f"This lap was recorded before GT7 update {PHYSICS_ERA}, which changed the "
                "tyre, suspension and engine physics -- its time isn't comparable with laps driven "
                "since, so it can't go on the leaderboard. Drive a fresh lap to submit.")
    try:
        lap_time_s = float(lap.get("lap_time_s") or 0)
    except (TypeError, ValueError):
        lap_time_s = 0.0
    if lap_time_s <= 0:
        return "This lap has no valid lap time to submit."
    return None


def submit_lap(car_name: str, track_name: str, lap_time_ms: int,
               psn_name: str, samples: list, access_token: str = "",
               user_id: str = "", timeout: float = 8) -> tuple[bool, str]:
    """Submit a lap to the global leaderboard. Returns (True, "ok") if the
    request reached Supabase successfully -- this does NOT mean it made the
    public leaderboard, since a flagged or rejected submission also
    succeeds here (both are resolved server-side, silently, by design --
    see the anti-cheat note above).

    On failure, returns (False, reason) with reason one of:
      "network" -- couldn't reach Supabase at all (offline, DNS, timeout)
      "auth"    -- Supabase answered 401/403: the access token expired or was
                   rejected (refresh the session and retry once)
      "server"  -- reached Supabase but it rejected the request (malformed
                   payload, 5xx, etc.)
      "data"    -- the lap data itself couldn't be turned into a request
    The distinction matters because "server" failures aren't fixed by
    checking your internet connection -- callers should say so rather than
    pointing the user at their network for a server-side problem.

    access_token/user_id come from a signed-in Supabase session (see
    auth.py); the `laps` table's INSERT RLS policy requires
    `auth.uid() = user_id`, so a submission with no access_token will be
    rejected by RLS unless the anon key alone is still permitted. Both
    default to "" so existing callers (anon-key-only) keep working
    unchanged."""
    try:
        payload = {
            "car_name": car_name,
            "track_name": track_name,
            "lap_time_ms": int(lap_time_ms),
            "psn_name": psn_name,
            "samples": _compact_samples(samples),
        }
        if user_id:
            payload["user_id"] = user_id
        data = json.dumps(payload, allow_nan=False).encode("utf-8")
    except Exception:
        # Bad local data (unparseable time, unserialisable sample): nothing
        # was sent, so it's neither a network nor a server problem.
        return False, "data"
    try:
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/laps",
            data=data, method="POST",
            headers=_headers(prefer="return=minimal", access_token=access_token or None),
        )
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return True, "ok"
    except urllib.error.HTTPError as e:
        # 401/403 = the access token expired or was rejected: the caller can
        # try refreshing the session. Anything else (400/422 payload errors,
        # 5xx) is the server's problem and a new token won't fix it.
        return False, "auth" if e.code in (401, 403) else "server"
    except Exception:
        return False, "network"


def get_top_laps(car_name: str, track_name: str, n: int = 10, timeout: float = 8) -> list:
    """Return up to `n` fastest public leaderboard laps for this exact
    car+track, sorted ascending by lap time. Each row is a dict:
    {id, car_name, track_name, lap_time_ms, psn_name, created_at}. `id` is
    included so a row can be passed straight to get_lap_samples() for a
    ghost-lap download. Returns [] on any failure (offline, nothing
    submitted yet, etc) -- callers should treat an empty list as "nothing
    to show", not an error.

    Only laps submitted since the current GT7 physics era began
    (PHYSICS_EPOCH) are returned: times set under the old physics
    aren't comparable, the same reason Polyphony reset its own boards."""
    params = urllib.parse.urlencode({
        "car_name": _eq(car_name),
        "track_name": _eq(track_name),
        "created_at": f"gte.{PHYSICS_EPOCH_ISO}",
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


def get_top_lap_ghost(car_name: str, track_name: str, timeout: float = 8) -> dict | None:
    """The single fastest public lap for this exact car+track, samples included,
    in one read-only request -- what the Dashboard's live "vs top lap" gap needs
    (it fetches this once per car+track per run). Same era filter as
    get_top_laps. Returns {id, car_name, track_name, lap_time_ms, psn_name,
    created_at, samples} or None on any failure or when nobody has set a lap."""
    params = urllib.parse.urlencode({
        "car_name": _eq(car_name),
        "track_name": _eq(track_name),
        "created_at": f"gte.{PHYSICS_EPOCH_ISO}",
        "select": "id,car_name,track_name,lap_time_ms,psn_name,created_at,samples",
        "order": "lap_time_ms.asc",
        "limit": "1",
    })
    try:
        req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/laps?{params}",
            method="GET", headers=_headers(),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        row = rows[0] if isinstance(rows, list) and rows else None
        return row if isinstance(row, dict) and row.get("samples") else None
    except Exception:
        return None


def get_lap_samples(lap_id: int, timeout: float = 8) -> list:
    """Return the compact sample list stored for one specific leaderboard
    lap (by the numeric `id` returned in get_top_laps() rows) -- used for
    ghost-lap download (Phase 3). Each sample only has the fields
    _compact_samples() keeps (track_position, speed_kmh, throttle, brake,
    steering, gear) -- enough for the input-trace charts and A/B diffs, but
    without world_x/world_z/t there's no GPS track map or replay for a
    downloaded ghost. Returns [] on any failure or if the lap doesn't
    exist."""
    try:
        params = urllib.parse.urlencode({
            "id": f"eq.{int(lap_id)}",
            "select": "samples",
            "limit": "1",
        })
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
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return True
    except Exception:
        return False
