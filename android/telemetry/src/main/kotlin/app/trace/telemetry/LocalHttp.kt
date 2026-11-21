package app.trace.telemetry

import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket

/**
 * A tiny HTTP/1.1 client for 127.0.0.1 only, over a plain socket. TRACE AI talks to its own llama-server this way:
 * Android blocks cleartext HTTP through HttpURLConnection by policy, but a loopback socket never leaves the phone and
 * needs no extra permission. It refuses any other host by construction (the address is fixed).
 */
object LocalHttp {
    class Response(val status: Int, val body: String)

    private fun readLine(inp: InputStream): String? {
        val sb = StringBuilder()
        while (true) {
            val b = inp.read()
            if (b < 0) return if (sb.isEmpty()) null else sb.toString()
            if (b == '\n'.code) return sb.toString().trimEnd('\r')
            sb.append(b.toChar())
        }
    }

    private fun readExactly(inp: InputStream, n: Int): ByteArray {
        val out = ByteArrayOutputStream(maxOf(n, 0))
        val buf = ByteArray(8192)
        var left = n
        while (left > 0) {
            val r = inp.read(buf, 0, minOf(buf.size, left))
            if (r < 0) throw java.io.EOFException("connection closed before the whole body arrived")
            out.write(buf, 0, r); left -= r
        }
        return out.toByteArray()
    }

    private fun readChunked(inp: InputStream): ByteArray {
        val out = ByteArrayOutputStream()
        while (true) {
            val size = (readLine(inp) ?: throw java.io.EOFException("chunk size missing")).substringBefore(';').trim().toInt(16)
            if (size == 0) { while (true) { val l = readLine(inp); if (l == null || l.isEmpty()) break }; break }   // trailers
            out.write(readExactly(inp, size)); readLine(inp)   // the CRLF after the chunk
        }
        return out.toByteArray()
    }

    fun request(port: Int, method: String, path: String, body: String? = null, timeoutMs: Int = 20_000): Response {
        Socket().use { s ->
            s.connect(InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 2_000)
            s.soTimeout = timeoutMs
            val payload = body?.toByteArray(Charsets.UTF_8)
            val head = "$method $path HTTP/1.1\r\nHost: 127.0.0.1:$port\r\nConnection: close\r\nAccept: application/json\r\n" +
                (if (payload != null) "Content-Type: application/json\r\nContent-Length: ${payload.size}\r\n" else "") + "\r\n"
            val out = s.getOutputStream()
            out.write(head.toByteArray(Charsets.US_ASCII)); if (payload != null) out.write(payload); out.flush()
            val inp = BufferedInputStream(s.getInputStream())
            val status = (readLine(inp) ?: throw java.io.EOFException("no response")).split(" ").getOrNull(1)?.toIntOrNull()
                ?: throw java.io.IOException("not an HTTP response")
            var length = -1; var chunked = false
            while (true) {
                val h = readLine(inp) ?: break
                if (h.isEmpty()) break
                val k = h.substringBefore(':').trim().lowercase(); val v = h.substringAfter(':', "").trim()
                if (k == "content-length") length = v.toIntOrNull() ?: -1
                if (k == "transfer-encoding" && v.lowercase().contains("chunked")) chunked = true
            }
            val bytes = when {
                chunked -> readChunked(inp)
                length >= 0 -> readExactly(inp, length)
                else -> inp.readBytes()
            }
            return Response(status, bytes.toString(Charsets.UTF_8))
        }
    }

    fun get(port: Int, path: String, timeoutMs: Int = 20_000) = request(port, "GET", path, null, timeoutMs)
    fun postJson(port: Int, path: String, json: String, timeoutMs: Int = 20_000) = request(port, "POST", path, json, timeoutMs)
}
