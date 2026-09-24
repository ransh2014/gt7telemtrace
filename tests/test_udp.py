"""
Round-trips a synthetic GT7 telemetry packet through the real Salsa20
decrypt + parse pipeline, without needing a live PS4/PS5.

The packet is built by encrypting a hand-crafted plaintext with the exact
same key/IV derivation `_decrypt` uses, so these tests catch regressions in
either the crypto step or the byte-offset parsing -- the two things this
whole project depends on getting right.
"""
import math
import struct

import pytest

Salsa20 = pytest.importorskip("Crypto.Cipher.Salsa20", reason="pycryptodome not installed")

from gt7telem import udp as _udp  # noqa: E402
from gt7telem.udp import _decrypt, _parse, _salsa20_pure  # noqa: E402

MAGIC = 0x47375330
KEY = b"Simulator Interface Packet GT7 ver 0.0"[:32]


def _xor_const_for(pkt_len: int) -> int:
    if pkt_len == 344:
        return 0x55FABB4F
    if pkt_len == 296:
        return 0xDEADBEAF
    return 0xDEADBEEF


def _make_encrypted_packet(seed: int, magic: int = MAGIC, length: int = 368, setters=None) -> bytes:
    """Build a ciphertext blob that `_decrypt` will accept: bytes 0x40:0x44 of
    the ciphertext must equal `seed` (read raw, before decryption, to derive
    the IV), and decrypting the whole buffer must yield `magic` at offset 0."""
    iv1 = seed
    iv2 = iv1 ^ _xor_const_for(length)
    iv = iv2.to_bytes(4, "little") + iv1.to_bytes(4, "little")

    plaintext = bytearray(length)
    struct.pack_into("<I", plaintext, 0, magic)
    if setters:
        setters(plaintext)

    # Reveal the keystream by encrypting zeros with the same key/nonce, so we
    # can pick a plaintext seed-field value that lands on the right ciphertext.
    keystream = Salsa20.new(key=KEY, nonce=iv).encrypt(bytes(length))
    seed_bytes = seed.to_bytes(4, "little")
    for i in range(4):
        plaintext[0x40 + i] = seed_bytes[i] ^ keystream[0x40 + i]

    return Salsa20.new(key=KEY, nonce=iv).encrypt(bytes(plaintext))


def test_decrypt_rejects_undersized_packet():
    assert _decrypt(b"too short") is None


def test_decrypt_rejects_bad_magic():
    raw = _make_encrypted_packet(seed=0x1, magic=0xBAADF00D)
    assert _decrypt(raw) is None


def test_decrypt_and_parse_roundtrip_extended_packet():
    def setters(pt):
        struct.pack_into("<f", pt, 0x4C, 50.0)     # speed: 50 m/s * 3.6 = 180 km/h
        struct.pack_into("<f", pt, 0x3C, 6500.0)   # rpm
        pt[0x90] = 0x54                            # gear=4, suggested_gear=5
        pt[0x91] = 204                             # throttle ~0.8
        pt[0x92] = 51                              # brake ~0.2
        struct.pack_into("<i", pt, 0x124, 1234)    # car_id

    raw = _make_encrypted_packet(seed=0xCAFEBABE, length=368, setters=setters)
    dec = _decrypt(raw)
    assert dec is not None

    parsed = _parse(dec)
    assert parsed is not None
    assert parsed["car_id"] == 1234
    assert parsed["speed_kmh"] == pytest.approx(180.0)
    assert parsed["gear"] == 4
    assert parsed["suggested_gear"] == 5
    assert parsed["throttle"] == pytest.approx(204 / 255, rel=1e-3)
    assert parsed["brake"] == pytest.approx(51 / 255, rel=1e-3)
    # Packet C (368 bytes) should populate the extended fields.
    assert parsed["wheel_rotation"] is not None


def test_parse_packet_a_has_no_extended_fields():
    raw = _make_encrypted_packet(seed=0x1234, length=296)
    dec = _decrypt(raw)
    assert dec is not None

    parsed = _parse(dec)
    assert parsed is not None
    assert parsed["wheel_rotation"] is None
    assert parsed["sway"] is None
    assert parsed["surface_type"] is None


def test_pure_python_salsa20_fallback_matches_pycryptodome():
    """Only used when pycryptodome is missing. It used to run 40 rounds and
    decrypt just the first 64 bytes, so it never produced a valid packet."""
    iv = bytes(range(8))
    data = bytes((i * 7 + 3) & 0xFF for i in range(368))
    assert _salsa20_pure(data, KEY, iv) == Salsa20.new(key=KEY, nonce=iv).decrypt(data)


# ── udp: filtered inputs + real steering ────────────────────────────────────
def _plain_packet(length, setters):
    pt = bytearray(length)
    struct.pack_into("<I", pt, 0, 0x47375330)
    setters(pt)
    return bytes(pt)


@pytest.fixture(autouse=True)
def _reset_steering_state():
    _udp._steer_lock.clear()
    _udp._steer_corr = 0.0
    _udp._prev_heading = None
    yield
    _udp._steer_lock.clear()
    _udp._steer_corr = 0.0
    _udp._prev_heading = None


def test_filtered_inputs_parsed_on_tilde_and_c():
    def s(pt):
        pt[0x13C] = 255
        pt[0x13D] = 51
    for length in (344, 368):
        p = _udp._parse(_plain_packet(length, s))
        assert p["throttle_filtered"] == pytest.approx(1.0)
        assert p["brake_filtered"] == pytest.approx(0.2)


def test_filtered_inputs_absent_on_a_and_b():
    for length in (296, 316):
        p = _udp._parse(_plain_packet(length, lambda pt: None))
        assert p["throttle_filtered"] is None and p["brake_filtered"] is None


def test_packet_a_keeps_heading_estimate():
    p = _udp._parse(_plain_packet(296, lambda pt: None))
    assert p["steering_source"] == "heading"


def test_real_steering_waits_for_sign():
    """Before any clear turn has taught the sign, the estimate is kept."""
    def s(pt):
        struct.pack_into("<f", pt, 0x160, 0.2)
        struct.pack_into("<f", pt, 0x164, 0.2)
    p = _udp._parse(_plain_packet(368, s))
    assert p["steering_source"] == "heading"


def test_packet_c_uses_front_wheel_angle():
    _udp._steer_corr = 1.0   # sign already learned
    def s(pt):
        struct.pack_into("<f", pt, 0x160, 0.2)
        struct.pack_into("<f", pt, 0x164, 0.2)
    p = _udp._parse(_plain_packet(368, s))
    assert p["steering_source"] == "wheel"
    # below the 0.35 rad lock floor, so reads as a partial turn
    assert p["steering"] == pytest.approx(0.2 / 0.35, abs=1e-6)


def test_packet_b_uses_wheel_rotation():
    _udp._steer_corr = 1.0
    def s(pt):
        struct.pack_into("<f", pt, 0x128, 3.0)
    p = _udp._parse(_plain_packet(316, s))
    assert p["steering_source"] == "rotation"
    assert p["steering"] == pytest.approx(1.0)  # 3.0 rad becomes the lock itself


def test_steering_sign_follows_heading_estimate():
    """If GT7's angle sign is opposite to the heading-rate estimate, the
    normalised value is flipped to match it."""
    for _ in range(5):
        v = _udp._normalise_steering(0.3, "wheel", heading_estimate=-0.5, car_id=1, speed_kmh=100.0)
    assert v < 0
    assert math.isfinite(v)
