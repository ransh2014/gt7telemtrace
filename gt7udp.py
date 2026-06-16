# gt7udp.py — GT7 telemetry direct from PS4
# Decryption method and packet offsets from Bornhall's gt7telemetry
# https://github.com/Bornhall/gt7telemetry
#
# Protocol:
#   Send heartbeat to PS4_IP:33739 → GT7 streams to PC on port 33740

import math
import socket
import struct
import threading
import time

from config import PS_IP

_ps4_ip         = PS_IP    # mutable — updated by set_ip()
SEND_PORT       = 33739
RECV_PORT       = 33740
HEARTBEAT_MSG   = b"A"
HEARTBEAT_EVERY = 1.0

_latest       = {}
_lock         = threading.Lock()
_connected    = False
_source       = None
_track        = "unknown_track"
_car          = "unknown_car"

_prev_x       = None
_prev_z       = None
_prev_heading = None
_cum_dist     = 0.0

def set_track(name): global _track; _track = name
def set_car(name):   global _car;   _car   = name
def is_connected():  return _connected

def set_ip(new_ip):
    global _ps4_ip, _connected, _source, _latest
    _ps4_ip = new_ip.strip()
    with _lock:
        _connected = False
        _source    = None
        _latest    = {}

def sanitize(name):
    if not name: return "unknown"
    safe = "".join(c if c.isalnum() else "_" for c in name.lower().strip())
    while "__" in safe: safe = safe.replace("__", "_")
    return safe.strip("_")

def reset_lap():
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
        iv2  = seed ^ 0xDEADBEAF
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

        flags_8e = b(0x8E)
        flags_8f = b(0x8F)
        flags_93 = b(0x93)
        in_pit   = bool(flags_8f & 0x02)

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
            "car_id":           car_id,
            "flags_8e":         flags_8e,
            "flags_8f":         flags_8f,
            "flags_93":         flags_93,
            "time_of_day":      i(0x80),
            "est_top_speed":    h(0x8C),
        }
    except:
        return None

def _ingest(raw):
    global _connected, _latest
    dec = _decrypt(raw)
    if dec is None: return
    parsed = _parse(dec)
    if parsed is None: return
    with _lock:
        _latest    = parsed
        _connected = True

# ── Thread 1: heartbeat ────────────────────────────────────────────────────────
def _heartbeat_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"[gt7udp] Heartbeat → {_ps4_ip}:{SEND_PORT}")
    while True:
        try:
            sock.sendto(HEARTBEAT_MSG, (_ps4_ip, SEND_PORT))
        except Exception as e:
            print(f"[gt7udp] Heartbeat error: {e}")
        time.sleep(HEARTBEAT_EVERY)

# ── Thread 2: receive ──────────────────────────────────────────────────────────
def _udp_thread():
    global _source
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", RECV_PORT))
    sock.settimeout(1.0)
    print(f"[gt7udp] Listening on :{RECV_PORT}")
    while True:
        try:
            raw, _ = sock.recvfrom(4096)
            if _source is None:
                _source = "udp"
                print("[gt7udp] Source: direct UDP")
            if _source == "udp":
                _ingest(raw)
        except socket.timeout:
            pass
        except Exception as e:
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
def get(key):
    _ensure_started()
    with _lock: return _latest.get(key)

def get_snapshot():
    _ensure_started()
    with _lock: return dict(_latest)

def get_int(key):
    try: return int(get(key) or 0)
    except: return 0

def get_float(key):
    try: return float(get(key) or 0.0)
    except: return 0.0

def wait_for_connection(timeout=60):
    _ensure_started()
    print(f"[gt7udp] Heartbeat → {_ps4_ip}:{SEND_PORT}  |  Listening on :{RECV_PORT}")
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
        if dots % 10 == 0: print("  ...", end="", flush=True)
        dots += 1
        time.sleep(0.1)
    print(f"\n[gt7udp] Timeout after {timeout}s.")
    print(f"  → Check PS4 IP (currently: {_ps4_ip})")
    print(f"  → PC and PS4 must be on the same network")
    return None
