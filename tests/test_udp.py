"""
Round-trips a synthetic GT7 telemetry packet through the real Salsa20
decrypt + parse pipeline, without needing a live PS4/PS5.

The packet is built by encrypting a hand-crafted plaintext with the exact
same key/IV derivation `_decrypt` uses, so these tests catch regressions in
either the crypto step or the byte-offset parsing -- the two things this
whole project depends on getting right.
"""
import struct

import pytest

Salsa20 = pytest.importorskip("Crypto.Cipher.Salsa20", reason="pycryptodome not installed")

from gt7telem.udp import _decrypt, _parse  # noqa: E402

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
