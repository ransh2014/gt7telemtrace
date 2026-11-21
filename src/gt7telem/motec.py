"""MoTeC i2 export: write a saved lap as a MoTeC ``.ld`` data file.

Pure numpy/pandas (no tkinter). The layout is the one the community has documented for ``.ld`` logs (a fixed 1762-byte
header, an event block, one 124-byte meta record per channel, then each channel's samples back to back); this is an
independent implementation written from that description, with no third-party code and no runtime dependency.

All channels are resampled onto one uniform time grid at the lap's recording rate (the Dashboard records on a timer, so
the timestamps are close to uniform but not exact) and stored as little-endian float32, except ``Gear`` (int16). A
channel the lap has no data for is skipped and reported, never invented; a lap too short to plot is refused.

Not verified inside MoTeC i2 itself: the tests read the file back with a separate reference parser.
"""

from __future__ import annotations

import datetime as _dt
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# ---- file layout -----------------------------------------------------------------------------------------------
HEADER_SIZE = 1762
EVENT_SIZE = 1154            # name 64, session 64, comment 1024, venue pointer 2
CHANNEL_SIZE = 124
EVENT_POS = HEADER_SIZE
FIRST_CHANNEL_POS = HEADER_SIZE + EVENT_SIZE   # 2916
MAX_CHANNELS = 1000
MIN_SAMPLES = 2
MIN_SPAN_S = 0.1
MAX_HZ = 200
FALLBACK_HZ = 10

# Header fields in file order, with the byte offset each one starts at (zero padding in between):
#   0 marker 0x40 | 8 channel-list pointer | 12 first-data pointer | 36 event pointer | 64 static 1 | 66 static 1000000
#   70 serial | 74 device type "ADL" (8) | 82 version | 84 static 128 | 86 channel count | 94 date (16) | 126 time (16)
#   158 driver (64) | 222 vehicle (64) | 350 venue (64) | 1502 "pro logging" word | 1572 short comment (64) -> 1762 bytes
_HEADER_FMT = "<I4xII20xI24xHII8sHHI4x16s16x16s16x64s64s64x64s64x1024xI66x64s126x"

DTYPE_FLOAT = 0x07
DTYPE_INT = 0x03


@dataclass(frozen=True)
class Channel:
    name: str          # up to 32 characters
    short: str         # up to 8
    unit: str          # up to 12
    column: str        # column of the lap frame ("" = computed)
    scale: float = 1.0  # multiplied into the column (throttle 0..1 -> %)
    integer: bool = False


# the channels the export tries to write, in this order
CHANNELS: list[Channel] = [
    Channel("Speed", "Speed", "km/h", "speed_kmh"),
    Channel("Engine RPM", "RPM", "rpm", "rpm"),
    Channel("Gear", "Gear", "", "gear", integer=True),
    Channel("Throttle", "Thr", "%", "throttle", 100.0),
    Channel("Brake", "Brk", "%", "brake", 100.0),
    Channel("Steering", "Steer", "", "steering"),
    Channel("G Lat", "GLat", "g", "lat_g"),
    Channel("G Long", "GLong", "g", "long_g"),
    Channel("Tyre Temp FL", "TTempFL", "C", "tyre_temp_fl"),
    Channel("Tyre Temp FR", "TTempFR", "C", "tyre_temp_fr"),
    Channel("Tyre Temp RL", "TTempRL", "C", "tyre_temp_rl"),
    Channel("Tyre Temp RR", "TTempRR", "C", "tyre_temp_rr"),
    Channel("Tyre Slip FL", "SlipFL", "", "tyre_slip_fl"),
    Channel("Tyre Slip FR", "SlipFR", "", "tyre_slip_fr"),
    Channel("Tyre Slip RL", "SlipRL", "", "tyre_slip_rl"),
    Channel("Tyre Slip RR", "SlipRR", "", "tyre_slip_rr"),
    Channel("Track Position", "TrkPos", "m", "track_position"),
    Channel("World X", "WorldX", "m", "world_x"),
    Channel("World Z", "WorldZ", "m", "world_z"),
    Channel("Fuel", "Fuel", "", "fuel_remaining"),
    Channel("Lap Time", "LapTime", "s", ""),
]


@dataclass
class Report:
    path: Path | None = None
    hz: int = 0
    samples: int = 0
    channels: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (channel name, why)

    def text(self) -> str:
        s = f"{len(self.channels)} channels, {self.samples} samples at {self.hz} Hz"
        if self.skipped:
            s += "; skipped: " + ", ".join(f"{n} ({why})" for n, why in self.skipped)
        return s


# ---- helpers ---------------------------------------------------------------------------------------------------
def _txt(value, n: int) -> bytes:
    """Fixed-width, NUL-padded ASCII field (anything else becomes '?')."""
    b = str(value or "").encode("ascii", "replace")[:n]
    return b + b"\0" * (n - len(b))


def _numeric(df: pd.DataFrame, col: str):
    if col not in df.columns:
        return None
    return pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).to_numpy(dtype=float)


def _fill(a: np.ndarray) -> np.ndarray | None:
    """Linear-fill NaNs from the neighbouring samples; None when nothing in the channel is a number."""
    ok = np.isfinite(a)
    if not ok.any():
        return None
    if ok.all():
        return a
    idx = np.arange(len(a))
    return np.interp(idx, idx[ok], a[ok])


def _derived(df: pd.DataFrame, t: np.ndarray) -> dict[str, np.ndarray]:
    """G channels when the frame does not carry them: lateral from yaw rate x speed, longitudinal from the speed trace."""
    out: dict[str, np.ndarray] = {}
    spd, ang_y = _numeric(df, "speed_kmh"), _numeric(df, "ang_y")
    if spd is not None and ang_y is not None and "lat_g" not in df.columns:
        out["lat_g"] = np.clip(np.nan_to_num(ang_y) * np.nan_to_num(spd) / 3.6 / 9.81, -4, 4)
    if spd is not None and "long_g" not in df.columns and len(t) > 1 and np.all(np.diff(t) >= 0):
        v = np.nan_to_num(spd) / 3.6
        dt = np.maximum(np.diff(t, prepend=t[0] - 0.05), 1e-3)
        out["long_g"] = np.clip(np.diff(v, prepend=v[0]) / dt / 9.81, -4, 4)
    return out


def _grid(t: np.ndarray | None, n_rows: int) -> tuple[np.ndarray, int]:
    """(sample times, Hz). Uses the lap's own timestamps when they are usable, else n_rows samples at FALLBACK_HZ."""
    if t is not None and np.isfinite(t).all() and n_rows >= MIN_SAMPLES:
        t = np.maximum.accumulate(t)
        span = float(t[-1] - t[0])
        if span >= MIN_SPAN_S:
            dt = np.diff(t)
            dt = dt[dt > 1e-4]
            hz = int(round(1.0 / float(np.median(dt)))) if len(dt) else FALLBACK_HZ
            hz = max(1, min(MAX_HZ, hz))
            n = int(span * hz) + 1
            return t[0] + np.arange(n) / hz, hz
    return np.arange(n_rows) / FALLBACK_HZ, FALLBACK_HZ


# ---- building the file -----------------------------------------------------------------------------------------
def _stamp(meta: dict) -> _dt.datetime:
    try:
        return _dt.datetime.strptime(str(meta.get("recorded_at")), "%Y%m%d_%H%M%S")
    except (TypeError, ValueError):
        return _dt.datetime.now()


def build(df: pd.DataFrame, meta: dict | None = None, driver: str = "") -> tuple[bytes, Report]:
    """The bytes of the .ld file and a Report of what went in. Raises ValueError for a lap that cannot be exported."""
    meta = meta or {}
    if df is None or len(df) < MIN_SAMPLES:
        raise ValueError("This lap has too few samples to export.")
    t_raw = _numeric(df, "t")
    grid, hz = _grid(t_raw, len(df))
    t_src = t_raw if (t_raw is not None and np.isfinite(t_raw).all() and len(grid) > 1
                      and float(np.max(t_raw) - np.min(t_raw)) >= MIN_SPAN_S) else np.arange(len(df)) / FALLBACK_HZ
    t_src = np.maximum.accumulate(t_src)
    derived = _derived(df, t_src)

    rep = Report(hz=hz, samples=len(grid))
    cols: list[tuple[Channel, np.ndarray]] = []
    for ch in CHANNELS:
        if not ch.column:                                   # Lap Time: elapsed seconds
            cols.append((ch, grid - grid[0]))
            continue
        raw = _numeric(df, ch.column)
        if raw is None:
            raw = derived.get(ch.column)
            if raw is None:
                rep.skipped.append((ch.name, "not in this lap"))
                continue
        filled = _fill(np.asarray(raw, dtype=float))
        if filled is None:
            rep.skipped.append((ch.name, "no valid values"))
            continue
        if ch.integer:                                      # nearest sample, so gears never blend
            pos = np.clip(np.searchsorted(t_src, grid, side="left"), 0, len(filled) - 1)
            vals = np.rint(filled[pos])
        else:
            vals = np.interp(grid, t_src, filled) * ch.scale
        cols.append((ch, vals))
    rep.channels = [c.name for c, _ in cols]

    n_ch = len(cols)
    data_pos = FIRST_CHANNEL_POS + CHANNEL_SIZE * n_ch
    metas, blobs = [], []
    pos = data_pos
    for i, (ch, vals) in enumerate(cols):
        if ch.integer:
            blob = np.clip(vals, -32768, 32767).astype("<i2").tobytes()
            dtype, size = DTYPE_INT, 2
        else:
            blob = vals.astype("<f4").tobytes()
            dtype, size = DTYPE_FLOAT, 4
        prev_ptr = 0 if i == 0 else FIRST_CHANNEL_POS + CHANNEL_SIZE * (i - 1)
        next_ptr = 0 if i == n_ch - 1 else FIRST_CHANNEL_POS + CHANNEL_SIZE * (i + 1)
        metas.append(struct.pack("<IIIIHHHHhhhh32s8s12s40x", prev_ptr, next_ptr, pos, len(vals), 0x2EE1 + i, dtype, size,
                                 hz, 0, 1, 1, 0, _txt(ch.name, 32), _txt(ch.short, 8), _txt(ch.unit, 12)))
        blobs.append(blob)
        pos += len(blob)

    when = _stamp(meta)
    car = meta.get("car_display") or meta.get("car") or ""
    track = meta.get("track_display") or meta.get("track") or ""
    lap_s = meta.get("lap_time_s")
    comment = "Exported from TRACE" + (f" - lap {float(lap_s):.3f} s" if isinstance(lap_s, (int, float)) and lap_s else "")
    header = struct.pack(
        _HEADER_FMT, 64, FIRST_CHANNEL_POS, data_pos, EVENT_POS, 1, 1_000_000, 12007, b"ADL", 420, 128, n_ch,
        _txt(when.strftime("%d/%m/%Y"), 16), _txt(when.strftime("%H:%M:%S"), 16), _txt(driver, 64), _txt(car, 64),
        _txt(track, 64), 0, _txt("", 64))
    event = struct.pack("<64s64s1024sH", _txt(track, 64), _txt("TRACE", 64), _txt(comment, 1024), 0)
    assert len(header) == HEADER_SIZE and len(event) == EVENT_SIZE and all(len(m) == CHANNEL_SIZE for m in metas)
    return header + event + b"".join(metas) + b"".join(blobs), rep


def write(path, df: pd.DataFrame, meta: dict | None = None, driver: str = "") -> Report:
    """Write the .ld file (atomically) and return the Report. Raises ValueError / OSError, never writes a partial file."""
    blob, rep = build(df, meta, driver)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_bytes(blob)
        tmp.replace(p)
    finally:
        tmp.unlink(missing_ok=True)
    rep.path = p
    return rep
