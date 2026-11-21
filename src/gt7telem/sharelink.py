"""
sharelink.py -- opt-in lap-sharing links (TRACE 1.6.0).

"Share lap link" uploads ONE lap so anyone holding the link can open a read-only page (lap.html on the TRACE website).
It is the only thing in TRACE that uploads a whole lap besides the leaderboard, so:

  * nothing is sent until the caller passes confirmed=True (the front ends show CONFIRM_TEXT first);
  * the payload is small, columnar JSON of whole numbers (format v1, below), gzipped and base64'd;
  * the server (docs/supabase-shared-laps.sql) is only reachable through three RPCs: share_lap, get_shared_lap,
    delete_shared_lap.  Links are unlisted and deleted after 90 days or when the owner deletes them.

Pure module: no tkinter, stdlib only, and the HTTP call is injectable so tests never touch the network.  The Kotlin
twin (android/.../SharedLap.kt) builds byte-identical payloads; tests/test_android_ports.py checks both against the
same golden vectors, which cover the canonical (uncompressed) JSON.  gzip output itself can differ between
implementations and is not compared.

Payload v1 -- {"v":1,"car":..,"track":..,"lap_time_ms":N,"n":M,"t":[..],"x":[..],"z":[..],"s":[..],"th":[..],"br":[..]}
  t   milliseconds since the first sample          x, z  world position in decimetres (empty lists if the lap has none)
  s   speed in 0.1 km/h                            th, br  throttle / brake 0..1000
Keys are written in exactly that order, no spaces, ASCII only (non-ASCII as \\uXXXX), so Python and Kotlin agree.
At most MAX_POINTS samples are kept (evenly spaced picks, always including the first and last).
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import math
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from . import labels as L

__all__ = ["CONFIRM_TEXT", "SITE_URL", "MAX_POINTS", "MAX_GZIP_BYTES", "ShareError", "ShareResult", "build_payload", "payload_json",
           "compress", "decompress", "lap_key", "ShareStore", "link_for", "id_from", "valid_id", "sharing_problem", "share", "delete", "reason_text"]

SITE_URL = "https://gt7trace.netlify.app/lap"
SUPABASE_URL = "https://hignsvyojdqsjoidgkud.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_OdzGvcypa0GI7TVxxUkusQ_KJ_Apa8i"

CONFIRM_TEXT = L.SHARE_CONFIRM
MAX_POINTS = 3000                  # the server allows 20000; 3000 points is plenty for a lap page and keeps it tiny
MAX_GZIP_BYTES = 200 * 1024        # same cap the server enforces
MIN_LAP_MS, MAX_LAP_MS = 10_000, 3_600_000

_ID_RE = re.compile(r"^[0-9a-f]{20}$")

# (ok, reason) reasons, the same vocabulary leaderboard.submit_lap uses plus the sharing-specific ones.
REASONS = ("network", "auth", "server", "data", "limit", "toobig", "session", "unconfirmed")


class ShareError(Exception):
    """Raised by build_payload/compress for a lap that can't be shared (message is user-readable)."""


class ShareResult:
    """ok: bool; reason: "" on success else one of REASONS; id/link set on success."""

    def __init__(self, ok: bool, reason: str = "", lap_id: str = ""):
        self.ok, self.reason, self.id = ok, reason, lap_id

    @property
    def link(self) -> str:
        return link_for(self.id) if self.ok and self.id else ""

    def __repr__(self) -> str:       # pragma: no cover - debugging aid
        return f"ShareResult(ok={self.ok}, reason={self.reason!r}, id={self.id!r})"


# ── payload ──────────────────────────────────────────────────────────────────────────────────────────────────
def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _q(v: float, scale: float) -> int:
    """Quantise to a whole number the same way in Python and Kotlin: floor(v*scale + 0.5)."""
    return int(math.floor(v * scale + 0.5))


def _clamp(i: int, lo: int, hi: int) -> int:
    return lo if i < lo else hi if i > hi else i


def pick_indices(n: int, m: int = MAX_POINTS) -> list[int]:
    """Evenly spaced sample indices, at most m of them, first and last always included. Integer maths only."""
    if n <= m:
        return list(range(n))
    return [(i * (n - 1)) // (m - 1) for i in range(m)]


def sharing_problem(lap: dict) -> str | None:
    """Why this lap can't be shared (user-readable), or None if it can."""
    if lap.get("incomplete"):
        return L.SHARE_INCOMPLETE
    samples = [s for s in (lap.get("samples") or []) if isinstance(s, dict)]
    if len(samples) < 2:
        return L.SHARE_NO_SAMPLES
    ms = _q(_num(lap.get("lap_time_s")), 1000)
    if ms < MIN_LAP_MS or ms > MAX_LAP_MS:
        return L.SHARE_BAD_TIME
    return None


def build_payload(lap: dict) -> dict:
    """The v1 payload dict for a saved lap (dict with "samples", "lap_time_s", "car"/"car_display", "track"/...)."""
    problem = sharing_problem(lap)
    if problem:
        raise ShareError(problem)
    samples = [s for s in lap["samples"] if isinstance(s, dict)]
    idx = pick_indices(len(samples))
    t0 = _num(samples[0].get("t"))
    has_xz = any(_num(s.get("world_x")) != 0 or _num(s.get("world_z")) != 0 for s in samples)
    t, x, z, sp, th, br = [], [], [], [], [], []
    for i in idx:
        s = samples[i]
        t.append(max(0, _q(_num(s.get("t")) - t0, 1000)))
        if has_xz:
            x.append(_q(_num(s.get("world_x")), 10))
            z.append(_q(_num(s.get("world_z")), 10))
        sp.append(_clamp(_q(_num(s.get("speed_kmh")), 10), 0, 20000))
        th.append(_clamp(_q(_num(s.get("throttle")), 1000), 0, 1000))
        br.append(_clamp(_q(_num(s.get("brake")), 1000), 0, 1000))
    return {
        "v": 1,
        "car": str(lap.get("car_display") or lap.get("car") or "?")[:120],
        "track": str(lap.get("track_display") or lap.get("track") or "?")[:120],
        "lap_time_ms": _q(_num(lap.get("lap_time_s")), 1000),
        "n": len(idx), "t": t, "x": x, "z": z, "s": sp, "th": th, "br": br,
    }


def payload_json(payload: dict) -> bytes:
    """Canonical bytes: fixed key order (insertion order of build_payload), no spaces, ASCII only."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def compress(payload: dict) -> bytes:
    """gzip of the canonical JSON. Raises ShareError if it is over the size cap."""
    raw = gzip.compress(payload_json(payload), compresslevel=9, mtime=0)
    if len(raw) > MAX_GZIP_BYTES:
        raise ShareError(L.SHARE_TOO_BIG)
    return raw


def decompress(blob: bytes, limit: int = 4_000_000) -> dict:
    """Inverse of compress(), with a cap on the decompressed size (the same guard lap.html applies)."""
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise ShareError("payload too large once decompressed")
    return json.loads(raw.decode("utf-8"))


# ── ids and links ────────────────────────────────────────────────────────────────────────────────────────────
def valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def link_for(lap_id: str) -> str:
    if not valid_id(lap_id):
        raise ValueError("not a shared-lap id")
    return f"{SITE_URL}?id={lap_id}"


def id_from(text: str) -> str | None:
    """The id out of a pasted link (or a bare id), or None."""
    s = (text or "").strip()
    m = re.search(r"[?&]id=([0-9a-fA-F]{20})(?![0-9a-fA-F])", s)
    cand = (m.group(1) if m else s).lower()
    return cand if valid_id(cand) else None


# ── HTTP (injectable) ────────────────────────────────────────────────────────────────────────────────────────
Http = Callable[[str, str, bytes | None, str], "tuple[int, str]"]      # (method, path, body, bearer) -> (status, text)


def default_http(timeout: float = 20) -> Http:
    def call(method: str, path: str, body: bytes | None, bearer: str) -> tuple[int, str]:
        req = urllib.request.Request(SUPABASE_URL + path, data=body, method=method, headers={
            "apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {bearer or SUPABASE_ANON_KEY}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace")
            except Exception:
                return e.code, ""
    return call


def _reason_for(code: int, text: str) -> str:
    t = (text or "").lower()
    if code in (401, 403) or "28000" in t or "sign-in required" in t or "jwt" in t:
        return "auth"
    if "rate limit" in t or "54000" in t or code == 429:
        return "limit"
    if "too large" in t:
        return "toobig"
    if code == 400 and ("22023" in t or "out of range" in t or "not gzip" in t or "not base64" in t or "bad car" in t):
        return "data"
    return "server"


def share(lap: dict, access_token: str, *, confirmed: bool, http: Http | None = None) -> ShareResult:
    """Upload one lap and return the result. NOTHING is built or sent unless confirmed is True.
    reason: "unconfirmed" | "data" | "toobig" | "network" | "auth" | "limit" | "server"."""
    if confirmed is not True:
        return ShareResult(False, "unconfirmed")
    try:
        payload = build_payload(lap)
        blob = compress(payload)
    except ShareError as e:
        return ShareResult(False, "toobig" if str(e) == L.SHARE_TOO_BIG else "data")
    except Exception:
        return ShareResult(False, "data")
    body = json.dumps({
        "p_car": payload["car"], "p_track": payload["track"], "p_lap_time_ms": payload["lap_time_ms"], "p_samples": payload["n"],
        "p_payload": base64.b64encode(blob).decode("ascii"),
    }).encode("utf-8")
    try:
        code, text = (http or default_http())("POST", "/rest/v1/rpc/share_lap", body, access_token)
    except Exception:
        return ShareResult(False, "network")
    if 200 <= code < 300:
        try:
            lap_id = json.loads(text)
        except ValueError:
            return ShareResult(False, "server")
        if valid_id(lap_id):
            return ShareResult(True, "", lap_id)
        return ShareResult(False, "server")
    return ShareResult(False, _reason_for(code, text))


def delete(lap_id: str, access_token: str, *, http: Http | None = None) -> ShareResult:
    """Delete one of your own shared laps. ok=True means it is gone (or was already gone)."""
    if not valid_id(lap_id):
        return ShareResult(False, "data")
    body = json.dumps({"p_id": lap_id}).encode("utf-8")
    try:
        code, text = (http or default_http())("POST", "/rest/v1/rpc/delete_shared_lap", body, access_token)
    except Exception:
        return ShareResult(False, "network")
    if 200 <= code < 300:
        return ShareResult(True, "", lap_id)
    return ShareResult(False, _reason_for(code, text))


REASON_TEXT = {
    "unconfirmed": L.SHARE_FAIL_UNCONFIRMED,
    "data": L.SHARE_FAIL_DATA,
    "toobig": L.SHARE_TOO_BIG,
    "network": L.SHARE_FAIL_NETWORK,
    "auth": L.SHARE_FAIL_AUTH,
    "session": L.SHARE_FAIL_AUTH,
    "limit": L.SHARE_FAIL_LIMIT,
    "server": L.SHARE_FAIL_SERVER,
}


def reason_text(reason: str) -> str:
    return REASON_TEXT.get(reason, REASON_TEXT["server"])


# ── remembering which of your laps you shared (so "Delete shared lap" knows the id) ───────────────────────────────
def lap_key(lap: dict) -> str:
    """A stable key for a saved lap: when it was recorded, its time, track and car."""
    ms = _q(_num(lap.get("lap_time_s")), 1000)
    return "|".join([str(lap.get("recorded_at") or ""), str(ms), str(lap.get("track_display") or lap.get("track") or ""),
                     str(lap.get("car_display") or lap.get("car") or "")])


class ShareStore:
    """lap key -> shared id, kept in a small JSON file on this device (never uploaded). A missing or damaged file is an empty store."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: v for k, v in d.items() if isinstance(k, str) and valid_id(v)} if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(d, indent=1), encoding="utf-8")
        except OSError:
            pass

    def get(self, key: str) -> str | None:
        return self._load().get(key)

    def put(self, key: str, lap_id: str) -> None:
        d = self._load()
        d[key] = lap_id
        self._save(d)

    def drop(self, key: str) -> None:
        d = self._load()
        if d.pop(key, None) is not None:
            self._save(d)
