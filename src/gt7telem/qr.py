"""qr.py -- a tiny QR Code encoder (byte mode, error correction level M, versions 1-10).

Vendored on purpose: the Dashboard only needs one QR for the WEB UI login link, so pulling in a QR library (and,
for the PyInstaller build, its image dependencies) is not worth it. ``encode(text)`` returns a square list of rows of
bools (True = dark module) WITHOUT the quiet zone; the caller draws it with a 4-module light border.

The Android app has a line-for-line port (android/telemetry/.../QrCode.kt); tests/test_qr.py checks this module against
golden matrices that the Kotlin unit test checks too, so both platforms draw the identical code.

Algorithm per ISO/IEC 18004: data codewords -> Reed-Solomon blocks -> interleave -> zig-zag placement -> the mask
(0-7) with the lowest penalty score. Never imports tkinter (headless-safe).
"""
from __future__ import annotations

__all__ = ["encode", "MAX_BYTES"]

# Level M, versions 1..10: ECC codewords per block, number of blocks.
_ECC_PER_BLOCK = (10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
_NUM_BLOCKS = (1, 1, 1, 2, 2, 4, 4, 4, 5, 5)
_EC_FORMAT_BITS = 0          # level M is 00 in the format information
_PENALTY_N1, _PENALTY_N2, _PENALTY_N3, _PENALTY_N4 = 3, 3, 40, 10


def _raw_modules(ver: int) -> int:
    r = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        r -= (25 * n - 10) * n - 55
        if ver >= 7:
            r -= 36
    return r


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ver - 1] * _NUM_BLOCKS[ver - 1]


def _capacity(ver: int) -> int:
    """Max bytes in byte mode: 4-bit mode + count bits (8 for v1-9, 16 for v10+)."""
    cc = 8 if ver < 10 else 16
    return (_data_codewords(ver) * 8 - 4 - cc) // 8


MAX_BYTES = _capacity(10)


# ── GF(256) Reed-Solomon (polynomial 0x11D) ───────────────────────────────────
def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    res = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < degree:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 0x02)
    return res


def _rs_remainder(data: list[int], div: list[int]) -> list[int]:
    res = [0] * len(div)
    for b in data:
        factor = b ^ res.pop(0)
        res.append(0)
        for i, coef in enumerate(div):
            res[i] ^= _gf_mul(coef, factor)
    return res


# ── encoding ─────────────────────────────────────────────────────────────────
def _codewords(data: bytes, ver: int) -> list[int]:
    cc = 8 if ver < 10 else 16
    bits: list[int] = []

    def put(val: int, n: int) -> None:
        bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))

    put(0b0100, 4)
    put(len(data), cc)
    for b in data:
        put(b, 8)
    cap = _data_codewords(ver) * 8
    put(0, min(4, cap - len(bits)))
    put(0, (8 - len(bits) % 8) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    nblocks, ecc = _NUM_BLOCKS[ver - 1], _ECC_PER_BLOCK[ver - 1]
    raw = _raw_modules(ver) // 8
    n_short = nblocks - raw % nblocks
    short_len = raw // nblocks
    div = _rs_divisor(ecc)
    blocks, k = [], 0
    for i in range(nblocks):
        dlen = short_len - ecc + (0 if i < n_short else 1)
        dat = words[k:k + dlen]
        k += dlen
        blk = dat + _rs_remainder(dat, div)
        if i < n_short:
            blk.insert(short_len - ecc, None)   # placeholder so short and long blocks interleave in step
        blocks.append(blk)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if blk[i] is not None:
                out.append(blk[i])
    return out


def _align_positions(ver: int) -> list[int]:
    if ver == 1:
        return []
    n = ver // 7 + 2
    size = ver * 4 + 17
    step = (ver * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    return [6] + [size - 7 - i * step for i in range(n - 2, -1, -1)]


class _Grid:
    def __init__(self, ver: int):
        self.ver = ver
        self.size = ver * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]
        self._function_patterns()

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = dark
        self.fn[y][x] = True

    def _finder(self, cx: int, cy: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < self.size and 0 <= y < self.size:
                    d = max(abs(dx), abs(dy))
                    self.set_fn(x, y, d not in (2, 4))

    def _function_patterns(self) -> None:
        s = self.size
        for i in range(s):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        self._finder(3, 3)
        self._finder(s - 4, 3)
        self._finder(3, s - 4)
        pos = _align_positions(self.ver)
        last = len(pos) - 1
        for i, a in enumerate(pos):
            for j, b in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(a + dx, b + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)   # reserve; real bits drawn after masking
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = s - 11 + i % 3, i // 3
                self.set_fn(a, b, bit)
                self.set_fn(b, a, bit)

    def format_bits(self, mask: int) -> None:
        data = _EC_FORMAT_BITS << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        s = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1
        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(s - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, s - 15 + i, bit(i))
        self.set_fn(8, s - 8, True)   # the always-dark module

    def place(self, words: list[int]) -> None:
        s = self.size
        i = 0
        total = len(words) * 8
        right = s - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(s):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = s - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < total:
                        self.m[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if self.fn[y][x]:
                    continue
                if mask == 0:
                    inv = (x + y) % 2 == 0
                elif mask == 1:
                    inv = y % 2 == 0
                elif mask == 2:
                    inv = x % 3 == 0
                elif mask == 3:
                    inv = (x + y) % 3 == 0
                elif mask == 4:
                    inv = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    inv = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    inv = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    inv = ((x + y) % 2 + x * y % 3) % 2 == 0
                if inv:
                    self.m[y][x] = not self.m[y][x]

    def penalty(self) -> int:
        s, m = self.size, self.m
        score = 0
        lines = [m[y] for y in range(s)] + [[m[y][x] for y in range(s)] for x in range(s)]
        pat_a = [True, False, True, True, True, False, True, False, False, False, False]
        pat_b = pat_a[::-1]
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += _PENALTY_N1 + run - 5
                    run, prev = 1, v
            if run >= 5:
                score += _PENALTY_N1 + run - 5
            for i in range(s - 10):
                seg = line[i:i + 11]
                if seg == pat_a or seg == pat_b:
                    score += _PENALTY_N3
        for y in range(s - 1):
            for x in range(s - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += _PENALTY_N2
        dark = sum(v for row in m for v in row)
        total = s * s
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        score += max(0, k) * _PENALTY_N4
        return score


def encode(text: str, mask: int | None = None) -> list[list[bool]]:
    """QR matrix for `text` (UTF-8, byte mode, level M), smallest version that fits. `mask` forces a mask (tests);
    by default the lowest-penalty mask is chosen. Raises ValueError if the text is longer than MAX_BYTES."""
    data = text.encode("utf-8")
    for ver in range(1, 11):
        if len(data) <= _capacity(ver):
            break
    else:
        raise ValueError(f"QR text too long ({len(data)} bytes, max {MAX_BYTES})")
    words = _codewords(data, ver)
    best, best_score = None, None
    for mk in (range(8) if mask is None else (mask,)):
        g = _Grid(ver)
        g.place(words)
        g.apply_mask(mk)
        g.format_bits(mk)
        sc = g.penalty()
        if best_score is None or sc < best_score:
            best, best_score = g, sc
    return [row[:] for row in best.m]
