package app.trace.telemetry

/** Salsa20/20 stream cipher, 32-byte key, 8-byte nonce. Port of udp._salsa20_pure. */
object Salsa20 {
    private fun rotl(v: Int, n: Int) = (v shl n) or (v ushr (32 - n))
    private fun le(b: ByteArray, o: Int) =
        (b[o].toInt() and 0xFF) or ((b[o + 1].toInt() and 0xFF) shl 8) or
            ((b[o + 2].toInt() and 0xFF) shl 16) or ((b[o + 3].toInt() and 0xFF) shl 24)

    private fun block(state: IntArray, out: ByteArray, off: Int) {
        val x = state.copyOf()
        fun qr(a: Int, b: Int, c: Int, d: Int) {
            x[b] = x[b] xor rotl(x[a] + x[d], 7)
            x[c] = x[c] xor rotl(x[b] + x[a], 9)
            x[d] = x[d] xor rotl(x[c] + x[b], 13)
            x[a] = x[a] xor rotl(x[d] + x[c], 18)
        }
        repeat(10) {
            qr(0, 4, 8, 12); qr(5, 9, 13, 1); qr(10, 14, 2, 6); qr(15, 3, 7, 11)
            qr(0, 1, 2, 3); qr(5, 6, 7, 4); qr(10, 11, 8, 9); qr(15, 12, 13, 14)
        }
        for (i in 0 until 16) {
            val v = x[i] + state[i]
            out[off + i * 4] = v.toByte()
            out[off + i * 4 + 1] = (v ushr 8).toByte()
            out[off + i * 4 + 2] = (v ushr 16).toByte()
            out[off + i * 4 + 3] = (v ushr 24).toByte()
        }
    }

    fun crypt(data: ByteArray, key: ByteArray, nonce: ByteArray): ByteArray {
        require(key.size == 32 && nonce.size == 8)
        val con = "expand 32-byte k".toByteArray(Charsets.US_ASCII)
        val state = intArrayOf(
            le(con, 0), le(key, 0), le(key, 4), le(key, 8),
            le(key, 12), le(con, 4), le(nonce, 0), le(nonce, 4),
            0, 0, le(con, 8), le(key, 16),
            le(key, 20), le(key, 24), le(key, 28), le(con, 12),
        )
        val out = ByteArray(data.size)
        val ks = ByteArray(64)
        var n = 0
        var off = 0
        while (off < data.size) {
            state[8] = n
            state[9] = 0
            block(state, ks, 0)
            val end = minOf(64, data.size - off)
            for (i in 0 until end) out[off + i] = (data[off + i].toInt() xor ks[i].toInt()).toByte()
            n++; off += 64
        }
        return out
    }
}
