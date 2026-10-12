package app.trace.telemetry

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.util.concurrent.atomic.AtomicInteger

/** Heartbeat sender + receiver. Port of udp._heartbeat_thread / _udp_thread. */
class TelemetryClient(ip: String = "192.168.1.11", private val onPacket: ((Packet) -> Unit)? = null) {
    @Volatile var ip: String = ip
    val decoder = PacketDecoder()
    @Volatile var latest: Packet = emptyMap(); private set
    @Volatile private var lastGoodMs = 0L
    @Volatile var bindError: String? = null; private set
    @Volatile var lastHeartbeatError: String? = null; private set
    val heartbeatsSent = AtomicInteger(); val heartbeatErrors = AtomicInteger()
    val packetsReceived = AtomicInteger(); val unexpectedSource = AtomicInteger()
    val decryptFailures = AtomicInteger(); val parseFailures = AtomicInteger()
    @Volatile private var running = false

    fun isConnected(): Boolean = lastGoodMs != 0L && (System.nanoTime() / 1_000_000 - lastGoodMs) <= (STALE_AFTER * 1000).toLong()

    fun start() {
        if (running) return
        running = true
        Thread({ heartbeat() }, "trace-heartbeat").apply { isDaemon = true }.start()
        Thread({ receive() }, "trace-recv").apply { isDaemon = true }.start()
    }

    fun stop() { running = false }

    private fun heartbeat() {
        val sock = DatagramSocket()
        while (running) {
            try {
                sock.send(DatagramPacket(HEARTBEAT, HEARTBEAT.size, InetAddress.getByName(ip), SEND_PORT))
                heartbeatsSent.incrementAndGet(); lastHeartbeatError = null
            } catch (e: Exception) {
                heartbeatErrors.incrementAndGet(); lastHeartbeatError = e.message ?: e.toString()
            }
            Thread.sleep((HEARTBEAT_EVERY * 1000).toLong())
        }
        sock.close()
    }

    private fun receive() {
        var sock: DatagramSocket? = null
        while (running && sock == null) {
            try { sock = DatagramSocket(RECV_PORT).apply { soTimeout = 1000 }; bindError = null }
            catch (e: Exception) { bindError = "Couldn't bind UDP port $RECV_PORT: ${e.message}"; Thread.sleep(5000) }
        }
        val buf = ByteArray(4096)
        while (running && sock != null) {
            try {
                val dp = DatagramPacket(buf, buf.size)
                sock.receive(dp)
                val expected = try { InetAddress.getByName(ip).hostAddress } catch (e: Exception) { null }
                if (expected != null && dp.address.hostAddress != expected) { unexpectedSource.incrementAndGet(); continue }
                packetsReceived.incrementAndGet()
                val raw = dp.data.copyOf(dp.length)
                val dec = decoder.decrypt(raw)
                if (dec == null) { decryptFailures.incrementAndGet(); continue }
                val p = decoder.parse(dec)
                if (p == null) { parseFailures.incrementAndGet(); continue }
                latest = p
                lastGoodMs = System.nanoTime() / 1_000_000
                onPacket?.invoke(p)
            } catch (_: SocketTimeoutException) {
            } catch (e: Exception) { Thread.sleep(1000) }
        }
        sock?.close()
    }

    companion object {
        const val SEND_PORT = 33739
        const val RECV_PORT = 33740
        const val HEARTBEAT_EVERY = 1.0
        const val STALE_AFTER = 2.0
        val HEARTBEAT = "C".toByteArray()
        private val DISCOVERY = listOf(987 to "00020020", 9302 to "00030010")

        /** Port of discover_ps_ip: broadcast PS4+PS5 queries, return first replying IP. */
        fun discoverPsIp(timeoutSec: Double = 2.0): String? {
            val sock = DatagramSocket().apply { broadcast = true }
            try {
                var sent = false
                for ((port, ver) in DISCOVERY) {
                    try {
                        val q = "SRCH * HTTP/1.1\ndevice-discovery-protocol-version:$ver\n".toByteArray(Charsets.US_ASCII)
                        sock.send(DatagramPacket(q, q.size, InetAddress.getByName("255.255.255.255"), port)); sent = true
                    } catch (_: Exception) {}
                }
                if (!sent) return null
                val deadline = System.nanoTime() + (timeoutSec * 1e9).toLong()
                val buf = ByteArray(1024)
                while (true) {
                    val rem = (deadline - System.nanoTime()) / 1_000_000
                    if (rem <= 0) return null
                    sock.soTimeout = rem.toInt()
                    val dp = DatagramPacket(buf, buf.size)
                    try { sock.receive(dp) } catch (_: SocketTimeoutException) { return null } catch (_: Exception) { continue }
                    if (String(dp.data, 0, minOf(dp.length, 5), Charsets.US_ASCII).startsWith("HTTP/")) return dp.address.hostAddress
                }
            } finally { sock.close() }
        }
    }
}
