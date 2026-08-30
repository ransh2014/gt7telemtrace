# gt7udp.py — GT7 telemetry direct from PS4
# Decryption method and packet offsets from Bornhall's gt7telemetry
# https://github.com/Bornhall/gt7telemetry
#
# Protocol:
#   Send heartbeat to PS4_IP:33739 → GT7 streams to PC on port 33740

import errno
import math
import socket
import struct
import threading
import time
from collections import deque
from typing import Any, Callable

from .config import PS_IP

__all__ = [
    "get_snapshot", "get", "get_int", "get_float",
    "set_ip", "set_car", "set_track", "is_connected", "wait_for_connection",
    "get_diagnostics", "get_last_error", "get_incidents", "register_event",
    "reset_lap",
]

_ps4_ip         = PS_IP    # mutable — updated by set_ip()
_ps4_ip_resolved     = None   # numeric IP for _ps4_ip, cached (it may be a hostname)
_ps4_ip_resolved_for = None   # which _ps4_ip value _ps4_ip_resolved is for
SEND_PORT       = 33739
RECV_PORT       = 33740
HEARTBEAT_MSG   = b"C"   # 'C' requests the extended 368-byte packet (B + ~ + C fields)
HEARTBEAT_EVERY = 1.0

_latest       = {}
_lock         = threading.Lock()
_connected    = False
_source       = None
_track        = "unknown_track"
_car          = "unknown_car"

# ── Connection diagnostics ────────────────────────────────────────────────
# Distinct counters so a stalled connection can be explained precisely
# instead of a generic "timeout": did we fail to even send a heartbeat
# (bad IP / no network), are heartbeats going out with nothing coming back
# (wrong IP / firewall / console not in a race), or are we receiving bytes
# that don't decrypt (wrong port already bound by something else, garbled
# traffic, or a console sending a packet format we don't recognise)?
_diag_lock            = threading.Lock()
_diag = {
    "heartbeats_sent":    0,
    "heartbeat_errors":   0,
    "packets_received":   0,   # raw UDP datagrams accepted (from the configured PS4/PS5) on RECV_PORT
    "unexpected_source":  0,   # datagrams on RECV_PORT from some other host on the network, ignored
    "decrypt_failures":   0,   # datagrams that failed Salsa20 decrypt / bad magic
    "parse_failures":     0,   # decrypted but failed to parse into a dict
    "last_heartbeat_error": None,
    "last_recv_error":      None,
    "bind_error":            None,   # fatal: couldn't bind RECV_PORT at all
    "last_good_packet_at":  None,
}

def _diag_update(**kwargs):
    with _diag_lock:
        _diag.update(kwargs)

def _diag_incr(key):
    with _diag_lock:
        _diag[key] = _diag.get(key, 0) + 1

def get_diagnostics() -> dict:
    """Snapshot of connection diagnostics for the GUI's log/status panel."""
    with _diag_lock:
        return dict(_diag)

def get_last_error() -> str | None:
    """Best single-line explanation of why we're not connected right now."""
    d = get_diagnostics()
    if d["bind_error"]:
        return d["bind_error"]
    if d["packets_received"] == 0:
        if d["heartbeat_errors"] and d["heartbeats_sent"] == 0:
            return f"Can't send heartbeat to {_ps4_ip}:{SEND_PORT} — {d['last_heartbeat_error']}"
        if d["heartbeat_errors"]:
            return f"Heartbeat send failing intermittently — {d['last_heartbeat_error']}"
        return (f"Sent {d['heartbeats_sent']} heartbeat(s) to {_ps4_ip}:{SEND_PORT}, "
                f"received 0 packets back. Check: PS4/PS5 IP is correct, console and "
                f"PC are on the same network/subnet, no VPN is active, and Windows/Linux "
                f"firewall allows UDP on port {RECV_PORT}.")
    if d["packets_received"] > 0 and not _connected:
        if d["decrypt_failures"] == d["packets_received"]:
            return (f"Receiving UDP packets on :{RECV_PORT} but none decrypt successfully "
                     "(wrong console/game version, or something else is sending to this port).")
        if d["parse_failures"]:
            return "Packets decrypt but fail to parse — unexpected packet format/length."
    return None

_prev_x       = None
_prev_z       = None
_prev_heading = None
_cum_dist     = 0.0

# ── Event hooks (race start/end, pause/resume) ───────────────────────────
_event_callbacks   = {}     # dict[str, list[callable]]
_prev_paused       = False
_race_active       = False
_speed_hold_start  = None   # timestamp when speed_kmh first crossed ROLLING_START_KPH

ROLLING_START_KPH   = 80.0  # sustained speed to count as a rolling-start race begin
ROLLING_START_HOLD  = 2.0   # seconds it must hold above ROLLING_START_KPH
GRID_START_KPH_EPS  = 0.5   # "stationary" tolerance for grid starts
GRID_START_ARM_WINDOW = 8.0 # seconds after a loading->grid transition during
                             # which the stationary+on_track grid-start check
                             # is even considered. Outside this window, sitting
                             # still in a menu/garage (also car_on_track=True)
                             # will NOT trigger a race start.
_prev_loading         = False
_grid_start_armed_until = 0.0

# Debug state tracking -- fires a 'debug_state' event any time loading,
# car_on_track, or total_laps changes, so misfires can be diagnosed from
# the exact state that triggered them (shows up in the GUI log panel).
_debug_prev_state = {}

# ── Incident detection (tyre slip / body motion spikes) ───────────────────────
_incidents           = []   # list of dicts, reset each race_start
SLIP_THRESHOLD       = 1.5  # raw slip ratio >= this = wheelspin (wheel outrunning ground)
LOCKUP_THRESHOLD     = 0.5  # raw slip ratio <= this = lockup/skid (wheel under-rotating vs ground)
SWAY_THRESHOLD       = 3.0  # sway/heave/surge magnitude (m/s^2-ish, tune per car)
INCIDENT_COOLDOWN_S  = 2.0  # min seconds between logged incidents of the same type
_last_incident_t      = {}  # type -> last fire time (time.time())

# ── Fuel mixture change detection (step-change in burn rate) ─────────────────
_fuel_hist            = deque()  # (t, fuel_remaining) pairs, cleared each race_start
MIX_CHECK_WINDOW      = 5.0      # seconds per comparison window
MIX_CHANGE_RATIO      = 1.3      # recent/prior burn-rate ratio to flag (either direction)
MIX_CHANGE_COOLDOWN_S = 15.0     # don't re-flag more often than this
_last_mix_change_t    = None

def get_incidents() -> list:
    return list(_incidents)

def _log_incident(kind, parsed, value):
    now = time.time()
    last = _last_incident_t.get(kind)
    if last is not None and now - last < INCIDENT_COOLDOWN_S:
        return
    _last_incident_t[kind] = now
    _incidents.append({
        "type":           kind,
        "t":              round(now, 3),
        "lap_number":     parsed.get("lap_number"),
        "track_position": round(parsed.get("track_position", 0.0), 2),
        "value":          round(value, 4),
        "speed_kmh":      round(parsed.get("speed_kmh", 0.0), 1),
    })

def _check_incidents(parsed, now):
    """Flags big tyre-slip and body-motion (sway/heave/surge) spikes as
    incidents -- e.g. a spin, rear step-out, or big kerb strike."""
    for wheel in ("fl", "fr", "rl", "rr"):
        slip = parsed.get(f"tyre_slip_{wheel}")
        if slip is None:
            continue
        axle = "front" if wheel in ("fl", "fr") else "rear"
        if slip >= SLIP_THRESHOLD:
            _log_incident(f"{axle}_slip", parsed, slip)
        elif slip <= LOCKUP_THRESHOLD:
            _log_incident(f"{axle}_lockup", parsed, slip)

    for axis in ("sway", "heave", "surge"):
        val = parsed.get(axis)
        if val is not None and abs(val) >= SWAY_THRESHOLD:
            _log_incident(f"{axis}_spike", parsed, val)

def _check_fuel_mix(parsed, now):
    """Watches for a sustained step-change in fuel burn rate -- a rough
    signal that the driver switched fuel mixture (GT7 doesn't expose the
    mixture setting itself over telemetry)."""
    global _last_mix_change_t
    fuel = parsed.get("fuel_remaining")
    if fuel is None:
        return
    _fuel_hist.append((now, fuel))
    cutoff = now - (MIX_CHECK_WINDOW * 2)
    while _fuel_hist and _fuel_hist[0][0] < cutoff:
        _fuel_hist.popleft()
    if len(_fuel_hist) < 4:
        return

    mid = now - MIX_CHECK_WINDOW
    recent = [(t, f) for t, f in _fuel_hist if t >= mid]
    prior  = [(t, f) for t, f in _fuel_hist if t <  mid]
    if len(recent) < 2 or len(prior) < 2:
        return

    recent_rate = (recent[0][1] - recent[-1][1]) / max(1e-6, recent[-1][0] - recent[0][0])
    prior_rate  = (prior[0][1]  - prior[-1][1])  / max(1e-6, prior[-1][0]  - prior[0][0])
    # rates are %/sec fuel burned (positive = burning fuel normally)
    if prior_rate <= 0.0001 or recent_rate <= 0.0001:
        return  # avoid div-by-near-zero / refuel noise

    ratio = recent_rate / prior_rate
    if ratio >= MIX_CHANGE_RATIO or ratio <= (1.0 / MIX_CHANGE_RATIO):
        if _last_mix_change_t is not None and now - _last_mix_change_t < MIX_CHANGE_COOLDOWN_S:
            return
        _last_mix_change_t = now
        _incidents.append({
            "type":           "mix_change_suspected",
            "t":              round(now, 3),
            "lap_number":     parsed.get("lap_number"),
            "track_position": round(parsed.get("track_position", 0.0), 2),
            "prior_rate":     round(prior_rate, 5),
            "recent_rate":    round(recent_rate, 5),
            "ratio":          round(ratio, 3),
        })

def _check_debug_state(parsed, now, paused, car_on_track, loading, speed, lap_number, total_laps):
    """Fires a 'debug_state' event any time one of the key race-start-relevant
    fields changes, carrying a full snapshot -- lets us see exactly what GT7
    reported at the moment of a misfire without needing a console window."""
    cur = {
        "loading": loading, "car_on_track": car_on_track,
        "total_laps": total_laps, "speed_bucket": "moving" if speed > 1 else "stopped",
        "race_active": _race_active,
    }
    if cur != _debug_prev_state:
        _debug_prev_state.update(cur)
        _fire_event("debug_state", {
            "t": round(now, 3), "loading": loading, "car_on_track": car_on_track,
            "paused": paused, "speed_kmh": round(speed, 1),
            "lap_number": lap_number, "total_laps": total_laps,
            "race_active": _race_active,
            "grid_armed": now <= _grid_start_armed_until,
        })

def register_event(name: str, fn: Callable[[dict], None]) -> None:
    """Register a callback for an event: 'race_start', 'race_end', 'pause', 'resume'.
    Callback receives the parsed telemetry dict for the packet that triggered it.
    Keep callbacks fast/non-blocking -- they run on the UDP receive thread."""
    _event_callbacks.setdefault(name, []).append(fn)

def _fire_event(name, parsed):
    for fn in _event_callbacks.get(name, []):
        try:
            fn(parsed)
        except Exception as e:
            print(f"[gt7udp] Event callback error ({name}): {e}")

def _check_events(parsed):
    """Called once per parsed packet. Detects race start/end and pause/resume
    from raw telemetry, using two independent start conditions:
      - Rolling start: speed held >= ROLLING_START_KPH for ROLLING_START_HOLD seconds
      - Grid start:    speed ~= 0 and car_on_track True (stationary on the grid)
    car_on_track alone is NOT used by itself -- it's also true while sitting in
    menus/replay, so it's only trusted here in the grid-start combo with speed."""
    global _prev_paused, _race_active, _speed_hold_start, _prev_loading, _grid_start_armed_until

    paused       = parsed.get("paused", False)
    car_on_track = parsed.get("car_on_track", False)
    loading      = parsed.get("loading", False)
    speed        = parsed.get("speed_kmh", 0.0)
    lap_number   = parsed.get("lap_number", 0) or 0
    total_laps   = parsed.get("total_laps", 0) or 0
    now          = time.time()

    # ── Pause / resume ───────────────────────────────────────────
    if paused and not _prev_paused:
        _fire_event("pause", parsed)
    elif _prev_paused and not paused:
        _fire_event("resume", parsed)
    _prev_paused = paused

    # ── Grid-start arming window ───────────────────────────────────────
    # A loading->grid transition (True->False) is when GT7 hands control back
    # to the player -- either at the actual starting grid, or after backing
    # out of a menu/replay. Only arm the grid-start check for a short window
    # right after this, so idling in a garage/menu (also car_on_track=True,
    # speed=0) doesn't fire a false race start outside that window.
    if _prev_loading and not loading:
        _grid_start_armed_until = now + GRID_START_ARM_WINDOW
    _prev_loading = loading

    _check_debug_state(parsed, now, paused, car_on_track, loading, speed, lap_number, total_laps)

    # ── Race start ──────────────────────────────────────────────────
    if not _race_active:
        # Rolling start: sustained speed above threshold
        if speed >= ROLLING_START_KPH:
            if _speed_hold_start is None:
                _speed_hold_start = now
            elif now - _speed_hold_start >= ROLLING_START_HOLD:
                _race_active = True
                _speed_hold_start = None
                _incidents.clear()
                _fuel_hist.clear()
                _last_incident_t.clear()
                _fire_event("race_start", parsed)
        else:
            _speed_hold_start = None  # speed dropped -- reset the hold timer

        # Grid start: stationary and on track, only within the arm window
        # right after a loading screen ends, AND only when total_laps is
        # already populated (>0) -- total_laps stays 0 in menus/car-select/
        # lobby screens and only becomes nonzero once an actual race session
        # is loaded, so this filters out unrelated loading blips that also
        # happen to have car_on_track=True and speed=0.
        if (not _race_active and car_on_track and speed <= GRID_START_KPH_EPS
                and now <= _grid_start_armed_until and total_laps > 0):
            _race_active = True
            _speed_hold_start = None
            _incidents.clear()
            _fuel_hist.clear()
            _last_incident_t.clear()
            _fire_event("race_start", parsed)

    # ── Incident / fuel-mix checks (only while a race is active) ─────────
    if _race_active:
        _check_incidents(parsed, now)
        _check_fuel_mix(parsed, now)

        # NOTE: car_on_track alone does NOT flip false at the finish line --
        # the car is still "on track" cruising back to pits/results after the
        # last lap. Watch lap_number cross past total_laps instead (e.g. 11/10).
        if total_laps > 0 and lap_number > total_laps:
            _race_active = False
            _fire_event("race_end", parsed)

def set_track(name: str) -> None: global _track; _track = name
def set_car(name: str) -> None:   global _car;   _car   = name
def is_connected() -> bool:  return _connected

def set_ip(new_ip: str) -> None:
    global _ps4_ip, _connected, _source, _latest
    _ps4_ip = new_ip.strip()
    with _lock:
        _connected = False
        _source    = None
        _latest    = {}

def sanitize(name: str) -> str:
    if not name: return "unknown"
    safe = "".join(c if c.isalnum() else "_" for c in name.lower().strip())
    while "__" in safe: safe = safe.replace("__", "_")
    return safe.strip("_")

def reset_lap() -> None:
    global _prev_x, _prev_z, _prev_heading, _cum_dist
    _prev_x = _prev_z = _prev_heading = None
    _cum_dist = 0.0
    with _lock:
        _latest.pop("track_position", None)

# ── Salsa20 decrypt — IV method from Bornhall/gt7telemetry ───────────────────
def _decrypt(data):
    if len(data) < 0x44: return None
    try:
        KEY = b'Simulator Interface Packet GT7 ver 0.0'[:32]

        seed = int.from_bytes(data[0x40:0x44], byteorder='little')
        iv1  = seed

        # XOR constant depends on packet version, which is determined by
        # packet length (per MacManley/gt7-udp): A=296B, B=316B, ~=344B, C=368B
        pkt_len = len(data)
        if pkt_len == 344:          # '~' Tilda packet uses a different constant
            xor_const = 0x55FABB4F
        elif pkt_len == 296:        # Packet A
            xor_const = 0xDEADBEAF
        else:                       # Packet B (316) and Packet C (368)
            xor_const = 0xDEADBEEF
        iv2  = iv1 ^ xor_const
        IV   = iv2.to_bytes(4, 'little') + iv1.to_bytes(4, 'little')

        try:
            from Crypto.Cipher import Salsa20
            cipher = Salsa20.new(key=KEY, nonce=IV)
            ddata  = cipher.decrypt(data)
        except ImportError:
            ddata = _salsa20_pure(data, KEY, IV)

        magic = int.from_bytes(ddata[0:4], 'little')
        if magic != 0x47375330:
            return None
        return ddata
    except:
        return None

# ── Pure Python Salsa20 fallback (no deps) ────────────────────────────────────
def _salsa20_pure(data, key, nonce):
    def _block(state):
        x = list(state)
        for _ in range(20):
            x[ 4] ^= ((x[ 0]+x[12])&0xFFFFFFFF)<<7  | ((x[ 0]+x[12])&0xFFFFFFFF)>>25
            x[ 8] ^= ((x[ 4]+x[ 0])&0xFFFFFFFF)<<9  | ((x[ 4]+x[ 0])&0xFFFFFFFF)>>23
            x[12] ^= ((x[ 8]+x[ 4])&0xFFFFFFFF)<<13 | ((x[ 8]+x[ 4])&0xFFFFFFFF)>>19
            x[ 0] ^= ((x[12]+x[ 8])&0xFFFFFFFF)<<18 | ((x[12]+x[ 8])&0xFFFFFFFF)>>14
            x[ 9] ^= ((x[ 5]+x[ 1])&0xFFFFFFFF)<<7  | ((x[ 5]+x[ 1])&0xFFFFFFFF)>>25
            x[13] ^= ((x[ 9]+x[ 5])&0xFFFFFFFF)<<9  | ((x[ 9]+x[ 5])&0xFFFFFFFF)>>23
            x[ 1] ^= ((x[13]+x[ 9])&0xFFFFFFFF)<<13 | ((x[13]+x[ 9])&0xFFFFFFFF)>>19
            x[ 5] ^= ((x[ 1]+x[13])&0xFFFFFFFF)<<18 | ((x[ 1]+x[13])&0xFFFFFFFF)>>14
            x[14] ^= ((x[10]+x[ 6])&0xFFFFFFFF)<<7  | ((x[10]+x[ 6])&0xFFFFFFFF)>>25
            x[ 2] ^= ((x[14]+x[10])&0xFFFFFFFF)<<9  | ((x[14]+x[10])&0xFFFFFFFF)>>23
            x[ 6] ^= ((x[ 2]+x[14])&0xFFFFFFFF)<<13 | ((x[ 2]+x[14])&0xFFFFFFFF)>>19
            x[10] ^= ((x[ 6]+x[ 2])&0xFFFFFFFF)<<18 | ((x[ 6]+x[ 2])&0xFFFFFFFF)>>14
            x[ 3] ^= ((x[15]+x[11])&0xFFFFFFFF)<<7  | ((x[15]+x[11])&0xFFFFFFFF)>>25
            x[ 7] ^= ((x[ 3]+x[15])&0xFFFFFFFF)<<9  | ((x[ 3]+x[15])&0xFFFFFFFF)>>23
            x[11] ^= ((x[ 7]+x[ 3])&0xFFFFFFFF)<<13 | ((x[ 7]+x[ 3])&0xFFFFFFFF)>>19
            x[15] ^= ((x[11]+x[ 7])&0xFFFFFFFF)<<18 | ((x[11]+x[ 7])&0xFFFFFFFF)>>14
            x[ 1] ^= ((x[ 0]+x[ 3])&0xFFFFFFFF)<<7  | ((x[ 0]+x[ 3])&0xFFFFFFFF)>>25
            x[ 2] ^= ((x[ 1]+x[ 0])&0xFFFFFFFF)<<9  | ((x[ 1]+x[ 0])&0xFFFFFFFF)>>23
            x[ 3] ^= ((x[ 2]+x[ 1])&0xFFFFFFFF)<<13 | ((x[ 2]+x[ 1])&0xFFFFFFFF)>>19
            x[ 0] ^= ((x[ 3]+x[ 2])&0xFFFFFFFF)<<18 | ((x[ 3]+x[ 2])&0xFFFFFFFF)>>14
            x[ 6] ^= ((x[ 5]+x[ 4])&0xFFFFFFFF)<<7  | ((x[ 5]+x[ 4])&0xFFFFFFFF)>>25
            x[ 7] ^= ((x[ 6]+x[ 5])&0xFFFFFFFF)<<9  | ((x[ 6]+x[ 5])&0xFFFFFFFF)>>23
            x[ 4] ^= ((x[ 7]+x[ 6])&0xFFFFFFFF)<<13 | ((x[ 7]+x[ 6])&0xFFFFFFFF)>>19
            x[ 5] ^= ((x[ 4]+x[ 7])&0xFFFFFFFF)<<18 | ((x[ 4]+x[ 7])&0xFFFFFFFF)>>14
            x[11] ^= ((x[10]+x[ 9])&0xFFFFFFFF)<<7  | ((x[10]+x[ 9])&0xFFFFFFFF)>>25
            x[ 8] ^= ((x[11]+x[10])&0xFFFFFFFF)<<9  | ((x[11]+x[10])&0xFFFFFFFF)>>23
            x[ 9] ^= ((x[ 8]+x[11])&0xFFFFFFFF)<<13 | ((x[ 8]+x[11])&0xFFFFFFFF)>>19
            x[10] ^= ((x[ 9]+x[ 8])&0xFFFFFFFF)<<18 | ((x[ 9]+x[ 8])&0xFFFFFFFF)>>14
            x[12] ^= ((x[15]+x[14])&0xFFFFFFFF)<<7  | ((x[15]+x[14])&0xFFFFFFFF)>>25
            x[13] ^= ((x[12]+x[15])&0xFFFFFFFF)<<9  | ((x[12]+x[15])&0xFFFFFFFF)>>23
            x[14] ^= ((x[13]+x[12])&0xFFFFFFFF)<<13 | ((x[13]+x[12])&0xFFFFFFFF)>>19
            x[15] ^= ((x[14]+x[13])&0xFFFFFFFF)<<18 | ((x[14]+x[13])&0xFFFFFFFF)>>14
        return struct.pack('<16I', *[((x[i]+state[i])&0xFFFFFFFF) for i in range(16)])

    u = lambda b, o: struct.unpack('<I', b[o:o+4])[0]
    con = b"expand 32-byte k"
    state = [
        u(con,0),  u(key,0),  u(key,4),  u(key,8),
        u(key,12), u(con,4),  u(nonce,0),u(nonce,4),
        0,         0,         u(con,8),  u(key,16),
        u(key,20), u(key,24), u(key,28), u(con,12),
    ]
    stream = _block(state)
    return bytes(a ^ b for a, b in zip(data[:0x40], stream[:0x40])) + data[0x40:]

# ── Parser — all fields from Bornhall's offsets ───────────────────────────────
def _parse(data):
    global _prev_x, _prev_z, _prev_heading, _cum_dist
    try:
        f  = lambda o: struct.unpack_from('<f', data, o)[0]
        i  = lambda o: struct.unpack_from('<i', data, o)[0]
        h  = lambda o: struct.unpack_from('<h', data, o)[0]
        H  = lambda o: struct.unpack_from('<H', data, o)[0]
        b  = lambda o: struct.unpack_from('<B', data, o)[0]

        wx = f(0x04);  wy = f(0x08);  wz = f(0x0C)
        vx = f(0x10);  vy = f(0x14);  vz = f(0x18)

        qx = f(0x1C);  qy = f(0x20);  qz = f(0x24);  qw = f(0x28)
        heading = math.atan2(
            2.0 * (qw * qy + qz * qx),
            1.0 - 2.0 * (qx*qx + qy*qy)
        )

        ang_x = f(0x2C);  ang_y = f(0x30);  ang_z = f(0x34)

        steering = 0.0
        if _prev_heading is not None:
            dh = heading - _prev_heading
            while dh >  math.pi: dh -= 2 * math.pi
            while dh < -math.pi: dh += 2 * math.pi
            steering = max(-1.0, min(1.0, dh / 0.2))
        _prev_heading = heading

        speed_kmh = f(0x4C) * 3.6

        if _prev_x is not None and speed_kmh > 2.0:
            dx = wx - _prev_x
            dz = wz - _prev_z
            _cum_dist += math.sqrt(dx*dx + dz*dz)
        _prev_x, _prev_z = wx, wz

        gear_byte      = b(0x90)
        current_gear   = gear_byte & 0x0F
        suggested_gear = gear_byte >> 4
        if suggested_gear == 0x0F:  # GT7 sentinel for "no suggestion", not a real gear
            suggested_gear = 0

        fuel_remaining = f(0x44)
        fuel_capacity  = f(0x48)
        is_ev          = fuel_capacity == 0.0

        tyre_temp_fl = f(0x60);  tyre_temp_fr = f(0x64)
        tyre_temp_rl = f(0x68);  tyre_temp_rr = f(0x6C)

        tyre_diam_fl = f(0xB4);  tyre_diam_fr = f(0xB8)
        tyre_diam_rl = f(0xBC);  tyre_diam_rr = f(0xC0)
        tyre_rot_fl  = abs(f(0xA4));  tyre_rot_fr = abs(f(0xA8))
        tyre_rot_rl  = abs(f(0xAC));  tyre_rot_rr = abs(f(0xB0))
        tyre_spd_fl  = 3.6 * tyre_diam_fl * tyre_rot_fl
        tyre_spd_fr  = 3.6 * tyre_diam_fr * tyre_rot_fr
        tyre_spd_rl  = 3.6 * tyre_diam_rl * tyre_rot_rl
        tyre_spd_rr  = 3.6 * tyre_diam_rr * tyre_rot_rr
        slip_fl = (tyre_spd_fl / speed_kmh) if speed_kmh > 1 else 1.0
        slip_fr = (tyre_spd_fr / speed_kmh) if speed_kmh > 1 else 1.0
        slip_rl = (tyre_spd_rl / speed_kmh) if speed_kmh > 1 else 1.0
        slip_rr = (tyre_spd_rr / speed_kmh) if speed_kmh > 1 else 1.0

        susp_fl = f(0xC4);  susp_fr = f(0xC8)
        susp_rl = f(0xCC);  susp_rr = f(0xD0)

        best_lap_ms = i(0x78)
        last_lap_ms = i(0x7C)

        packet_id       = i(0x70)
        current_lap     = h(0x74)
        total_laps      = h(0x76)
        current_pos     = h(0x84)
        total_positions = h(0x86)

        rpm             = f(0x3C)
        rpm_warning     = H(0x88)
        rpm_limiter     = H(0x8A)
        boost           = f(0x50) - 1.0

        oil_temp        = f(0x5C)
        water_temp      = f(0x58)
        oil_pressure    = f(0x54)
        ride_height     = f(0x38) * 1000

        clutch           = f(0xF4)
        clutch_engaged   = f(0xF8)
        rpm_after_clutch = f(0xFC)

        gear_ratios = [f(0x100 + i*4) for i in range(9)]
        car_id      = i(0x124)

        # ── Extended fields (Packet B / ~ / C) ────────────────────────────
        # Only present when the PS console is sent heartbeat 'C' and replies
        # with the longer packet. Falls back to None on plain packet A.
        pkt_len = len(data)
        wheel_rotation = steering_angular_velocity = None
        sway = heave = surge = None
        torque_vectors = energy_recovery = None
        surface_type = current_lap_ms = None
        wheel_steering_angle = wheel_base = car_category = None

        if pkt_len >= 316:   # Packet B
            wheel_rotation             = f(0x128)
            steering_angular_velocity  = f(0x12C)
            sway                       = f(0x130)
            heave                      = f(0x134)
            surge                      = f(0x138)

        if pkt_len >= 344:   # Packet ~ (Tilda)
            torque_vectors  = [f(0x140 + i * 4) for i in range(4)]
            energy_recovery = f(0x150)

        if pkt_len >= 368:   # Packet C
            surface_type = tuple(
                chr(b(0x158 + i)) if b(0x158 + i) else "" for i in range(4)
            )  # T=tarmac, C=curb/kerb, D=dirt, G=grass, S/s=sand/gravel, per wheel FL/FR/RL/RR
            current_lap_ms       = i(0x15C)   # live in-progress lap time, ms
            wheel_steering_angle = (f(0x160), f(0x164))  # front-left, front-right, radians
            wheel_base           = f(0x168)  # meters
            car_category = "".join(
                chr(b(0x16C + i)) for i in range(4) if b(0x16C + i)
            )  # e.g. "GR3", "GRX"

        flags_8e = b(0x8E)
        flags_8f = b(0x8F)
        flags_93 = b(0x93)  # NOTE: this is NOT a flags byte -- documented as an
                             # unused/padding byte right after `brake` in the packet
                             # struct. Kept here for raw visibility only.

        # flags_8e/flags_8f together form ONE 16-bit little-endian "SimulatorFlags"
        # field (flags_8e = low byte, flags_8f = high byte). Bit layout per the
        # community-documented GT7 packet struct (Nenkai/PDTools, MacManley/gt7-udp):
        flags16 = flags_8e | (flags_8f << 8)
        car_on_track     = bool(flags16 & (1 << 0))
        paused           = bool(flags16 & (1 << 1))
        loading          = bool(flags16 & (1 << 2))
        in_gear          = bool(flags16 & (1 << 3))
        has_turbo        = bool(flags16 & (1 << 4))
        rev_limit_alert  = bool(flags16 & (1 << 5))
        handbrake_active = bool(flags16 & (1 << 6))
        lights_active    = bool(flags16 & (1 << 7))
        high_beams       = bool(flags16 & (1 << 8))
        low_beams        = bool(flags16 & (1 << 9))
        asm_active       = bool(flags16 & (1 << 10))
        tcs_active       = bool(flags16 & (1 << 11))

        # NOTE: there's no known "in pit" bit in flags16. `flags_8f & 0x02` is
        # bit 9 (low_beams) per the table above -- using it as an "in pit"
        # signal produces false positives any time headlights are toggled
        # (tunnels, night races, manual toggle). Disabled until a real
        # pit-lane bit is identified and verified against footage; left as
        # `False` (rather than None) so downstream bool/float consumers
        # (race_analyst.pit_flag, dashboard's PIT label) degrade safely
        # instead of crashing or misreporting.
        in_pit   = False

        return {
            "track_name":       _track,
            "car_name":         _car,
            "packet_id":        packet_id,
            "lap_number":       current_lap,
            "track_position":   _cum_dist,
            "world_x":          wx,
            "world_y":          wy,
            "world_z":          wz,
            "heading":          heading,
            "speed_kmh":        speed_kmh,
            "throttle":         b(0x91) / 255.0,
            "brake":            b(0x92) / 255.0,
            "steering":         steering,
            "gear":             current_gear,
            "suggested_gear":   suggested_gear,
            "rpm":              rpm,
            "max_rpm":          rpm_limiter,
            "rpm_warning":      rpm_warning,
            "rpm_limiter":      rpm_limiter,
            "tyre_temp_fl":     tyre_temp_fl,
            "tyre_temp_fr":     tyre_temp_fr,
            "tyre_temp_rl":     tyre_temp_rl,
            "tyre_temp_rr":     tyre_temp_rr,
            "tyre_slip_fl":     slip_fl,
            "tyre_slip_fr":     slip_fr,
            "tyre_slip_rl":     slip_rl,
            "tyre_slip_rr":     slip_rr,
            "susp_fl":          susp_fl,
            "susp_fr":          susp_fr,
            "susp_rl":          susp_rl,
            "susp_rr":          susp_rr,
            "vel_x":            vx,
            "vel_y":            vy,
            "vel_z":            vz,
            "ang_x":            ang_x,
            "ang_y":            ang_y,
            "ang_z":            ang_z,
            "fuel_remaining":   fuel_remaining,
            "fuel_capacity":    fuel_capacity,
            "is_ev":            is_ev,
            "boost":            boost,
            "oil_temp":         oil_temp,
            "water_temp":       water_temp,
            "oil_pressure":     oil_pressure,
            "ride_height_mm":   ride_height,
            "clutch":           clutch,
            "clutch_engaged":   clutch_engaged,
            "rpm_after_clutch": rpm_after_clutch,
            "gear_ratios":      gear_ratios,
            "total_laps":       total_laps,
            "current_position": current_pos,
            "total_positions":  total_positions,
            "best_lap_ms":      best_lap_ms,
            "last_lap_ms":      last_lap_ms,
            "in_pit":           in_pit,
            "car_on_track":     car_on_track,
            "paused":           paused,
            "loading":          loading,
            "in_gear":          in_gear,
            "has_turbo":        has_turbo,
            "rev_limit_alert":  rev_limit_alert,
            "handbrake_active": handbrake_active,
            "lights_active":    lights_active,
            "high_beams":       high_beams,
            "low_beams":        low_beams,
            "asm_active":       asm_active,
            "tcs_active":       tcs_active,
            "car_id":           car_id,
            "flags_8e":         flags_8e,
            "flags_8f":         flags_8f,
            "flags_93":         flags_93,
            "time_of_day":      i(0x80),
            "est_top_speed":    h(0x8C),
            # Extended (Packet B / ~ / C) -- None when console only sends packet A
            "wheel_rotation":            wheel_rotation,
            "steering_angular_velocity": steering_angular_velocity,
            "sway":                      sway,
            "heave":                     heave,
            "surge":                     surge,
            "torque_vectors":            torque_vectors,
            "energy_recovery":           energy_recovery,
            "surface_type":              surface_type,
            "current_lap_ms":            current_lap_ms,
            "wheel_steering_angle":      wheel_steering_angle,
            "wheel_base":                wheel_base,
            "car_category":              car_category,
        }
    except:
        return None

def _ingest(raw):
    global _connected, _latest
    dec = _decrypt(raw)
    if dec is None:
        _diag_incr("decrypt_failures")
        return
    parsed = _parse(dec)
    if parsed is None:
        _diag_incr("parse_failures")
        return
    with _lock:
        _latest    = parsed
        _connected = True
    _diag_update(last_good_packet_at=time.time())
    _check_events(parsed)

# ── Thread 1: heartbeat ────────────────────────────────────────────────────────
def _classify_send_error(e):
    """Turn a raw socket exception into a message someone can actually act on."""
    if isinstance(e, socket.gaierror):
        return (f"'{_ps4_ip}' doesn't look like a valid IP/hostname "
                f"({e.strerror if hasattr(e, 'strerror') else e})")
    if isinstance(e, OSError):
        code = e.errno
        if code == errno.ENETUNREACH:
            return f"network unreachable — PC has no route to {_ps4_ip} (wrong subnet / not connected to that network)"
        if code == errno.EHOSTUNREACH:
            return f"host unreachable — {_ps4_ip} isn't answering on the local network"
        if code == errno.ECONNREFUSED:
            return f"connection refused by {_ps4_ip} — is the game/console actually on and reachable?"
        if code == errno.EACCES:
            return "permission denied sending UDP (check OS firewall/antivirus rules)"
        return f"{e.strerror or e} (errno {code})"
    return str(e)

def _heartbeat_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"[gt7udp] Heartbeat -> {_ps4_ip}:{SEND_PORT}")
    while True:
        try:
            sock.sendto(HEARTBEAT_MSG, (_ps4_ip, SEND_PORT))
            _diag_incr("heartbeats_sent")
        except Exception as e:
            msg = _classify_send_error(e)
            _diag_incr("heartbeat_errors")
            _diag_update(last_heartbeat_error=msg)
            print(f"[gt7udp] Heartbeat error: {msg}")
        time.sleep(HEARTBEAT_EVERY)

# ── Thread 2: receive ──────────────────────────────────────────────────────────
def _classify_bind_error(e):
    if isinstance(e, OSError):
        if e.errno == errno.EADDRINUSE:
            return (f"Port {RECV_PORT} is already in use — another copy of TRACE (or another "
                     "GT7 telemetry tool) is probably already running and listening on it. "
                     "Close it and relaunch.")
        if e.errno == errno.EACCES:
            return f"Permission denied binding UDP port {RECV_PORT} (check firewall/OS permissions)."
        return f"Couldn't bind UDP port {RECV_PORT}: {e.strerror or e} (errno {e.errno})"
    return f"Couldn't bind UDP port {RECV_PORT}: {e}"

def _expected_ps4_ip():
    """Numeric IP `_ps4_ip` should currently resolve to, cached so the
    receive loop doesn't do a DNS lookup per packet. Returns None (meaning
    "don't filter") if `_ps4_ip` has never resolved successfully yet --
    better to accept an unverified sender than drop real telemetry because
    of a transient DNS hiccup on a hostname-configured console."""
    global _ps4_ip_resolved, _ps4_ip_resolved_for
    if _ps4_ip_resolved_for != _ps4_ip:
        try:
            _ps4_ip_resolved = socket.gethostbyname(_ps4_ip)
            _ps4_ip_resolved_for = _ps4_ip
        except OSError:
            pass  # keep the last-known-good value (if any) until this succeeds
    return _ps4_ip_resolved

def _udp_thread():
    global _source
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", RECV_PORT))
    except OSError as e:
        msg = _classify_bind_error(e)
        _diag_update(bind_error=msg)
        print(f"[gt7udp] FATAL: {msg}")
        return  # nothing more this thread can do without the socket
    sock.settimeout(1.0)
    print(f"[gt7udp] Listening on :{RECV_PORT}")
    while True:
        try:
            raw, addr = sock.recvfrom(4096)
            expected_ip = _expected_ps4_ip()
            if expected_ip is not None and addr[0] != expected_ip:
                _diag_incr("unexpected_source")
                continue  # not our configured PS4/PS5 -- ignore rather than parse/trust it
            _diag_incr("packets_received")
            if _source is None:
                _source = "udp"
                print("[gt7udp] Source: direct UDP")
            if _source == "udp":
                _ingest(raw)
        except socket.timeout:
            pass
        except Exception as e:
            _diag_update(last_recv_error=str(e))
            print(f"[gt7udp] Receive error: {e}")
            time.sleep(1.0)

# ── Start ──────────────────────────────────────────────────────────────────────
_started = False

def _ensure_started():
    global _started
    if _started: return
    _started = True
    threading.Thread(target=_heartbeat_thread, daemon=True).start()
    threading.Thread(target=_udp_thread,       daemon=True).start()

# ── Public API ─────────────────────────────────────────────────────────────────
def get(key: str) -> Any:
    _ensure_started()
    with _lock: return _latest.get(key)

def get_snapshot() -> dict:
    _ensure_started()
    with _lock: return dict(_latest)

def get_int(key: str) -> int:
    try: return int(get(key) or 0)
    except: return 0

def get_float(key: str) -> float:
    try: return float(get(key) or 0.0)
    except: return 0.0

def wait_for_connection(timeout: int = 60) -> str | None:
    _ensure_started()
    print(f"[gt7udp] Heartbeat -> {_ps4_ip}:{SEND_PORT}  |  Listening on :{RECV_PORT}")
    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        with _lock:
            ok  = _connected
            src = _source
            spd = _latest.get("speed_kmh")
            pos = (_latest.get("world_x", 0), _latest.get("world_z", 0))
        if ok:
            print(f"\n[gt7udp] Connected via {src}!  "
                  f"speed={spd:.1f} km/h  pos=({pos[0]:.0f}, {pos[1]:.0f})")
            return _track
        # Fail fast on a fatal bind error instead of burning the full timeout.
        if get_diagnostics()["bind_error"]:
            print(f"\n[gt7udp] {get_diagnostics()['bind_error']}")
            return None
        if dots % 10 == 0: print("  ...", end="", flush=True)
        dots += 1
        time.sleep(0.1)
    reason = get_last_error() or "no reason determined"
    print(f"\n[gt7udp] Timeout after {timeout}s.")
    print(f"  -> {reason}")
    return None
