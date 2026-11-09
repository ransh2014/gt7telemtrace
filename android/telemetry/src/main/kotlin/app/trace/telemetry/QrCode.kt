package app.trace.telemetry

/**
 * qr.py on the phone: a tiny QR Code encoder (byte mode, error correction level M, versions 1-10) for the WEB UI
 * login link. The project has no ZXing (or any QR library), so this ~200-line port is the lightest option: no new
 * dependency, nothing added to the APK beyond this class. Line-for-line the same algorithm as the desktop, and
 * QrCodeTest checks it against the desktop's golden matrices (tools/gen_android_webui.py writes qr_golden.json).
 *
 * encode() returns rows of booleans (true = dark), without the quiet zone.
 */
object QrCode {
    private val ECC_PER_BLOCK = intArrayOf(10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
    private val NUM_BLOCKS = intArrayOf(1, 1, 1, 2, 2, 4, 4, 4, 5, 5)
    private const val EC_FORMAT_BITS = 0   // level M
    private const val N1 = 3; private const val N2 = 3; private const val N3 = 40; private const val N4 = 10

    private fun rawModules(ver: Int): Int {
        var r = (16 * ver + 128) * ver + 64
        if (ver >= 2) {
            val n = ver / 7 + 2
            r -= (25 * n - 10) * n - 55
            if (ver >= 7) r -= 36
        }
        return r
    }

    private fun dataCodewords(ver: Int) = rawModules(ver) / 8 - ECC_PER_BLOCK[ver - 1] * NUM_BLOCKS[ver - 1]
    private fun capacity(ver: Int) = (dataCodewords(ver) * 8 - 4 - (if (ver < 10) 8 else 16)) / 8
    val MAX_BYTES = capacity(10)

    private fun gfMul(x: Int, y: Int): Int {
        var z = 0
        for (i in 7 downTo 0) {
            z = (z shl 1) xor ((z ushr 7) * 0x11D)
            z = z xor (((y ushr i) and 1) * x)
        }
        return z and 0xFF
    }

    private fun rsDivisor(degree: Int): IntArray {
        val res = IntArray(degree); res[degree - 1] = 1
        var root = 1
        repeat(degree) {
            for (j in 0 until degree) {
                res[j] = gfMul(res[j], root)
                if (j + 1 < degree) res[j] = res[j] xor res[j + 1]
            }
            root = gfMul(root, 0x02)
        }
        return res
    }

    private fun rsRemainder(data: List<Int>, div: IntArray): List<Int> {
        val res = ArrayDeque(List(div.size) { 0 })
        for (b in data) {
            val factor = b xor res.removeFirst()
            res.addLast(0)
            for (i in div.indices) res[i] = res[i] xor gfMul(div[i], factor)
        }
        return res.toList()
    }

    private fun codewords(data: ByteArray, ver: Int): IntArray {
        val cc = if (ver < 10) 8 else 16
        val bits = ArrayList<Int>()
        fun put(v: Int, n: Int) { for (i in n - 1 downTo 0) bits.add((v ushr i) and 1) }
        put(0b0100, 4); put(data.size, cc)
        for (b in data) put(b.toInt() and 0xFF, 8)
        val cap = dataCodewords(ver) * 8
        put(0, minOf(4, cap - bits.size))
        put(0, (8 - bits.size % 8) % 8)
        var pad = 0xEC
        while (bits.size < cap) { put(pad, 8); pad = pad xor (0xEC xor 0x11) }
        val words = (0 until bits.size / 8).map { i -> (0 until 8).fold(0) { a, j -> (a shl 1) or bits[i * 8 + j] } }

        val nBlocks = NUM_BLOCKS[ver - 1]; val ecc = ECC_PER_BLOCK[ver - 1]
        val raw = rawModules(ver) / 8
        val nShort = nBlocks - raw % nBlocks
        val shortLen = raw / nBlocks
        val div = rsDivisor(ecc)
        val blocks = ArrayList<MutableList<Int?>>()
        var k = 0
        for (i in 0 until nBlocks) {
            val dlen = shortLen - ecc + (if (i < nShort) 0 else 1)
            val dat = words.subList(k, k + dlen); k += dlen
            val blk: MutableList<Int?> = (dat + rsRemainder(dat, div)).toMutableList()
            if (i < nShort) blk.add(shortLen - ecc, null)   // placeholder so short and long blocks interleave in step
            blocks.add(blk)
        }
        val out = ArrayList<Int>()
        for (i in blocks[0].indices) for (blk in blocks) blk[i]?.let { out.add(it) }
        return out.toIntArray()
    }

    private fun alignPositions(ver: Int): List<Int> {
        if (ver == 1) return emptyList()
        val n = ver / 7 + 2
        val size = ver * 4 + 17
        val step = (ver * 8 + n * 3 + 5) / (n * 4 - 4) * 2
        return listOf(6) + (n - 2 downTo 0).map { size - 7 - it * step }
    }

    private class Grid(val ver: Int) {
        val size = ver * 4 + 17
        val m = Array(size) { BooleanArray(size) }
        val fn = Array(size) { BooleanArray(size) }

        init { functionPatterns() }

        fun setFn(x: Int, y: Int, dark: Boolean) { m[y][x] = dark; fn[y][x] = true }

        private fun finder(cx: Int, cy: Int) {
            for (dy in -4..4) for (dx in -4..4) {
                val x = cx + dx; val y = cy + dy
                if (x in 0 until size && y in 0 until size) {
                    val d = maxOf(Math.abs(dx), Math.abs(dy))
                    setFn(x, y, d != 2 && d != 4)
                }
            }
        }

        private fun functionPatterns() {
            for (i in 0 until size) { setFn(6, i, i % 2 == 0); setFn(i, 6, i % 2 == 0) }
            finder(3, 3); finder(size - 4, 3); finder(3, size - 4)
            val pos = alignPositions(ver); val last = pos.size - 1
            for (i in pos.indices) for (j in pos.indices) {
                if ((i == 0 && j == 0) || (i == 0 && j == last) || (i == last && j == 0)) continue
                for (dy in -2..2) for (dx in -2..2) setFn(pos[i] + dx, pos[j] + dy, maxOf(Math.abs(dx), Math.abs(dy)) != 1)
            }
            formatBits(0)
            if (ver >= 7) {
                var rem = ver
                repeat(12) { rem = (rem shl 1) xor ((rem ushr 11) * 0x1F25) }
                val bits = (ver shl 12) or rem
                for (i in 0 until 18) {
                    val bit = ((bits ushr i) and 1) == 1
                    val a = size - 11 + i % 3; val b = i / 3
                    setFn(a, b, bit); setFn(b, a, bit)
                }
            }
        }

        fun formatBits(mask: Int) {
            val data = (EC_FORMAT_BITS shl 3) or mask
            var rem = data
            repeat(10) { rem = (rem shl 1) xor ((rem ushr 9) * 0x537) }
            val bits = ((data shl 10) or rem) xor 0x5412
            fun bit(i: Int) = ((bits ushr i) and 1) == 1
            for (i in 0..5) setFn(8, i, bit(i))
            setFn(8, 7, bit(6)); setFn(8, 8, bit(7)); setFn(7, 8, bit(8))
            for (i in 9..14) setFn(14 - i, 8, bit(i))
            for (i in 0..7) setFn(size - 1 - i, 8, bit(i))
            for (i in 8..14) setFn(8, size - 15 + i, bit(i))
            setFn(8, size - 8, true)
        }

        fun place(words: IntArray) {
            var i = 0
            val total = words.size * 8
            var right = size - 1
            while (right >= 1) {
                if (right == 6) right = 5
                for (vert in 0 until size) for (j in 0..1) {
                    val x = right - j
                    val upward = ((right + 1) and 2) == 0
                    val y = if (upward) size - 1 - vert else vert
                    if (!fn[y][x] && i < total) {
                        m[y][x] = ((words[i ushr 3] ushr (7 - (i and 7))) and 1) == 1
                        i++
                    }
                }
                right -= 2
            }
        }

        fun applyMask(mask: Int) {
            for (y in 0 until size) for (x in 0 until size) {
                if (fn[y][x]) continue
                val inv = when (mask) {
                    0 -> (x + y) % 2 == 0
                    1 -> y % 2 == 0
                    2 -> x % 3 == 0
                    3 -> (x + y) % 3 == 0
                    4 -> (x / 3 + y / 2) % 2 == 0
                    5 -> x * y % 2 + x * y % 3 == 0
                    6 -> (x * y % 2 + x * y % 3) % 2 == 0
                    else -> ((x + y) % 2 + x * y % 3) % 2 == 0
                }
                if (inv) m[y][x] = !m[y][x]
            }
        }

        fun penalty(): Int {
            var score = 0
            val lines = (0 until size).map { y -> m[y].toList() } + (0 until size).map { x -> (0 until size).map { y -> m[y][x] } }
            val patA = listOf(true, false, true, true, true, false, true, false, false, false, false)
            val patB = patA.reversed()
            for (line in lines) {
                var run = 0; var prev: Boolean? = null
                for (v in line) {
                    if (v == prev) run++ else { if (run >= 5) score += N1 + run - 5; run = 1; prev = v }
                }
                if (run >= 5) score += N1 + run - 5
                for (i in 0 until size - 10) {
                    val seg = line.subList(i, i + 11)
                    if (seg == patA || seg == patB) score += N3
                }
            }
            for (y in 0 until size - 1) for (x in 0 until size - 1) {
                val c = m[y][x]
                if (c == m[y][x + 1] && c == m[y + 1][x] && c == m[y + 1][x + 1]) score += N2
            }
            val dark = m.sumOf { r -> r.count { it } }
            val total = size * size
            val k = (Math.abs(dark * 20 - total * 10) + total - 1) / total - 1
            score += maxOf(0, k) * N4
            return score
        }
    }

    /** QR matrix for [text] (UTF-8, byte mode, level M), smallest version that fits; [mask] -1 = lowest penalty. */
    fun encode(text: String, mask: Int = -1): Array<BooleanArray> {
        val data = text.toByteArray(Charsets.UTF_8)
        val ver = (1..10).firstOrNull { data.size <= capacity(it) }
            ?: throw IllegalArgumentException("QR text too long (${data.size} bytes, max $MAX_BYTES)")
        val words = codewords(data, ver)
        var best: Grid? = null; var bestScore = Int.MAX_VALUE
        for (mk in if (mask < 0) (0..7) else (mask..mask)) {
            val g = Grid(ver)
            g.place(words); g.applyMask(mk); g.formatBits(mk)
            val sc = g.penalty()
            if (sc < bestScore) { best = g; bestScore = sc }
        }
        return Array(best!!.size) { best.m[it].copyOf() }
    }
}
