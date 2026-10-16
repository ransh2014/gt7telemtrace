"""Builds golden test packets for the Kotlin telemetry core.

Each packet is encrypted with the exact key/IV derivation udp._decrypt uses,
then decrypted and parsed by the REAL gt7telem.udp module, in sequence (the parser
is stateful: heading, distance, steering calibration). The ciphertext and the
parsed result are written to fixtures.json, and the Kotlin tests must reproduce
the parsed result from the ciphertext exactly.

Run from the gt7telem-package folder:  python make_fixtures.py <out.json>
"""
import json
import math
import struct
import sys

sys.path.insert(0, "src")
from gt7telem import udp  # noqa: E402

MAGIC = 0x47375330
KEY = b"Simulator Interface Packet GT7 ver 0.0"[:32]


def xor_const(n):
    return 0x55FABB4F if n == 344 else 0xDEADBEAF if n == 296 else 0xDEADBEEF


def encrypt(plain: bytearray, seed: int) -> bytes:
    n = len(plain)
    iv1 = seed
    iv2 = iv1 ^ xor_const(n)
    iv = iv2.to_bytes(4, "little") + iv1.to_bytes(4, "little")
    keystream = udp._salsa20_pure(bytes(n), KEY, iv)
    seed_bytes = seed.to_bytes(4, "little")
    for i in range(4):
        plain[0x40 + i] = seed_bytes[i] ^ keystream[0x40 + i]
    return udp._salsa20_pure(bytes(plain), KEY, iv)


def make_plain(n, t, lap, speed_ms, steer, flags, gear, seq):
    p = bytearray(n)
    pk = lambda fmt, off, *v: struct.pack_into(fmt, p, off, *v)
    pk("<I", 0, MAGIC)
    pk("<fff", 0x04, 100.0 + 12.5 * t, 3.0 + 0.1 * t, -40.0 + 7.25 * t)   # world
    pk("<fff", 0x10, 1.5, -0.2, speed_ms)                                   # velocity
    heading = 0.35 * t
    pk("<ffff", 0x1C, 0.0, math.sin(heading / 2), 0.0, math.cos(heading / 2))  # quaternion
    pk("<fff", 0x2C, 0.01 * t, -0.02, 0.003)                                # angular vel
    pk("<f", 0x38, 0.114)                                                   # ride height (m)
    pk("<f", 0x3C, 6200.0 + 150.0 * t)                                      # rpm
    pk("<f", 0x44, 41.5 - 0.3 * t)                                          # fuel
    pk("<f", 0x48, 100.0)                                                   # fuel capacity
    pk("<f", 0x4C, speed_ms)                                                # speed m/s
    pk("<f", 0x50, 1.35)                                                    # boost+1
    pk("<f", 0x54, 4.8)                                                     # oil pressure
    pk("<f", 0x58, 88.0)                                                    # water
    pk("<f", 0x5C, 96.0)                                                    # oil
    pk("<ffff", 0x60, 84.0 + t, 86.5, 79.25, 91.0)                          # tyre temps
    pk("<i", 0x70, 1000 + seq)                                              # packet id
    pk("<h", 0x74, lap)
    pk("<h", 0x76, 5)                                                       # total laps
    pk("<i", 0x78, 107120)                                                  # best lap
    pk("<i", 0x7C, 107906)                                                  # last lap
    pk("<i", 0x80, 52320000)                                                # time of day
    pk("<h", 0x84, 4)
    pk("<h", 0x86, 12)
    pk("<H", 0x88, 7200)
    pk("<H", 0x8A, 7800)
    pk("<h", 0x8C, 241)
    pk("<B", 0x8E, flags & 0xFF)
    pk("<B", 0x8F, (flags >> 8) & 0xFF)
    pk("<B", 0x90, gear)
    pk("<B", 0x91, 235)                                                     # throttle
    pk("<B", 0x92, 12)                                                      # brake
    pk("<B", 0x93, 7)
    pk("<ffff", 0xA4, 55.0, 56.0, 60.0, -61.0)                              # tyre rot
    pk("<ffff", 0xB4, 0.31, 0.31, 0.33, 0.33)                               # tyre diam
    pk("<ffff", 0xC4, 0.112, 0.110, 0.118, 0.117)                           # suspension
    pk("<f", 0xF4, 0.0)
    pk("<f", 0xF8, 1.0)
    pk("<f", 0xFC, 1200.0)
    pk("<f", 0x100, 7.5)
    pk("<8f", 0x104, 3.62, 2.19, 1.52, 1.17, 0.94, 0.0, 0.0, 0.0)
    pk("<i", 0x124, 3611)
    if n >= 316:
        pk("<fffff", 0x128, 0.6 * steer, 0.1, 0.02, 0.01, -0.04)
    if n >= 344:
        pk("<B", 0x13C, 200)
        pk("<B", 0x13D, 9)
        pk("<4f", 0x140, 1.0, 2.0, 3.0, 4.0)
        pk("<f", 0x150, 0.25)
    if n >= 368:
        p[0x158:0x15C] = b"TCDG"
        pk("<i", 0x15C, 48213)
        pk("<ff", 0x160, steer * 0.3, steer * 0.28)
        pk("<f", 0x168, 2.3)
        p[0x16C:0x170] = b"GR3\x00"
    return p


# (length, step, lap, speed m/s, steering, flags16, gear byte)
FLAGS_RACE = (1 << 0) | (1 << 3) | (1 << 7)              # on track, in gear, lights
FLAGS_ALL = (1 << 0) | (1 << 3) | (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11)
SEQ = [
    (368, 0, 1, 55.0, 0.0, FLAGS_RACE, 0x54),
    (368, 1, 1, 56.0, 0.9, FLAGS_RACE, 0x54),
    (368, 2, 1, 57.5, -0.7, FLAGS_RACE, 0x4F),   # suggested gear 0xF -> 0
    (368, 3, 1, 58.0, 0.5, FLAGS_ALL, 0x65),
    (368, 4, 2, 52.0, 0.4, FLAGS_RACE | (1 << 1), 0x32),  # lap changes -> distance resets; paused
    (368, 5, 2, 0.5, 0.0, FLAGS_RACE | (1 << 2), 0x10),   # nearly stopped; loading
    (344, 6, 2, 40.0, 0.2, FLAGS_RACE, 0x54),
    (316, 7, 2, 41.0, 0.2, FLAGS_RACE, 0x54),
    (296, 8, 2, 42.0, 0.2, FLAGS_RACE, 0x54),
]


def clean(v):
    if isinstance(v, tuple):
        return [clean(x) for x in v]
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v


def main(out):
    udp.set_track("SUGO International Racing Course")
    udp.set_car("Honda Civic SI '87")
    cases = []
    for idx, (n, t, lap, spd, steer, flags, gear) in enumerate(SEQ):
        seed = 0x1234ABCD + idx * 977
        plain = make_plain(n, t, lap, spd, steer, flags, gear, idx)
        raw = encrypt(plain, seed)
        dec = udp._decrypt(raw)
        assert dec is not None, f"packet {idx} did not decrypt"
        parsed = udp._parse(dec)
        assert parsed is not None, f"packet {idx} did not parse"
        cases.append({"len": n, "hex": raw.hex(), "expected": {k: clean(v) for k, v in parsed.items()}})
    # negative cases
    bad_magic = bytearray(368)
    short = bytes(0x20)
    cases_neg = {"short_hex": short.hex(), "garbage_hex": bytes(range(256)) .hex() + bytes(112).hex()}
    json.dump({"cases": cases, "negative": cases_neg}, open(out, "w"), indent=1, sort_keys=True)
    print("wrote", out, len(cases), "cases")


if __name__ == "__main__":
    main(sys.argv[1])
