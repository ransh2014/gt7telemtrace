package app.trace.telemetry

import java.io.File
import java.net.InetAddress
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.LocalDate
import java.util.Base64

/**
 * webui_auth.py on the phone: the passcode that guards the WEB UI when ALLOW REMOTE (WEB UI) is on. Same rules:
 *  - each time the WEB UI is turned on, a fresh 6-digit code and a separate 32-byte urlsafe QR login token, memory only;
 *  - constant-time code compare; per client IP, 5 wrong tries lock it out for 60 s, doubling per lockout, max 10 min;
 *    a wrong code and a lockout look the same to the caller;
 *  - the QR token works once, or for 5 minutes, whichever comes first (then [qrToken] issues a new one);
 *  - sessions are random cookie values kept as SHA-256 hashes in memory; [end] / [logoutAll] forget them;
 *  - remembered devices: only a SHA-256 hash + created date per device in devices.json (30 days), so a remembered
 *    phone or tablet skips the code even after the code rotates. [logoutAll] empties the file.
 * Pure JVM (no Android classes) so WebAuthTest runs it on the build machine; [clock] is in seconds and injectable.
 */
class WebAuth(
    private val devicesFile: File,
    private val clock: () -> Double = { System.nanoTime() / 1e9 },
    private val today: () -> LocalDate = { LocalDate.now() },
) {
    companion object {
        const val SESSION_COOKIE = "trace_s"
        const val DEVICE_COOKIE = "trace_d"
        const val DEVICE_DAYS = 30
        const val QR_TTL_S = 300.0
        const val MAX_TRIES = 5
        const val LOCK_BASE_S = 60.0
        const val LOCK_MAX_S = 600.0
        private val rng = SecureRandom()

        fun sha(s: String): String = MessageDigest.getInstance("SHA-256").digest(s.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it.toInt() and 0xFF) }

        fun token(): String = ByteArray(32).also { rng.nextBytes(it) }.let { Base64.getUrlEncoder().withoutPadding().encodeToString(it) }

        /** 127.0.0.0/8, ::1 and ::ffff:127.x. */
        fun isLoopback(a: InetAddress?): Boolean = a != null && a.isLoopbackAddress

        fun parseCookies(header: String?): Map<String, String> {
            val out = LinkedHashMap<String, String>()
            for (part in (header ?: "").split(";")) {
                val i = part.indexOf('=')
                if (i <= 0) continue
                val k = part.substring(0, i).trim(); val v = part.substring(i + 1).trim().trim('"')
                if (k.isNotEmpty() && k !in out) out[k] = v
            }
            return out
        }

        private fun eq(a: String, b: String) = MessageDigest.isEqual(a.toByteArray(Charsets.UTF_8), b.toByteArray(Charsets.UTF_8))
    }

    private val lock = Any()
    @Volatile var code = ""; private set
    private var qr = ""
    private var qrAt = 0.0
    private var qrUsed = true
    private val sessions = HashSet<String>()
    private val fails = HashMap<String, DoubleArray>()   // ip -> [wrong tries, lockouts so far, locked until]

    /** WEB UI turned on: new code, new QR token, no sessions (remembered devices stay). */
    fun rotate() = synchronized(lock) {
        code = "%06d".format(rng.nextInt(1_000_000)); sessions.clear(); newQr()
    }

    /** WEB UI turned off: every session and the QR token stop working. */
    fun end() = synchronized(lock) { code = ""; sessions.clear(); qr = ""; qrUsed = true }

    /** LOG OUT ALL DEVICES. */
    fun logoutAll() = synchronized(lock) { sessions.clear(); writeDevices(emptyList()) }

    fun locked(ip: String): Boolean = synchronized(lock) { (fails[ip]?.get(2) ?: 0.0) > clock() }

    fun checkCode(ip: String, given: String?): Boolean = synchronized(lock) {
        val now = clock()
        val f = fails.getOrPut(ip) { doubleArrayOf(0.0, 0.0, 0.0) }
        if (f[2] > now) return false
        val ok = code.isNotEmpty() && eq((given ?: "").trim(), code)
        if (ok) { fails.remove(ip); return true }
        f[0] = f[0] + 1.0
        if (f[0] >= MAX_TRIES) {
            f[2] = now + minOf(LOCK_MAX_S, LOCK_BASE_S * Math.pow(2.0, f[1]))
            f[1] = f[1] + 1.0; f[0] = 0.0
        }
        false
    }

    private fun newQr() { qr = token(); qrAt = clock(); qrUsed = false }

    /** The token for the Dashboard's QR; a fresh one once the last was used or expired ("" while off). */
    fun qrToken(): String = synchronized(lock) {
        if (code.isEmpty()) return ""
        if (qrUsed || clock() - qrAt >= QR_TTL_S) newQr()
        qr
    }

    fun consumeQr(t: String?): Boolean = synchronized(lock) {
        if (t.isNullOrEmpty() || qr.isEmpty() || qrUsed || clock() - qrAt >= QR_TTL_S) return false
        if (!eq(t, qr)) return false
        qrUsed = true
        true
    }

    fun newSession(): String { val t = token(); synchronized(lock) { sessions.add(sha(t)) }; return t }

    fun rememberDevice(): String {
        val t = token()
        synchronized(lock) { writeDevices(readDevices() + (sha(t) to today())) }
        return t
    }

    fun isAuthed(cookies: Map<String, String>): Boolean = synchronized(lock) {
        if (code.isEmpty()) return false
        val s = cookies[SESSION_COOKIE]; val d = cookies[DEVICE_COOKIE]
        if (!s.isNullOrEmpty() && sha(s) in sessions) return true
        if (!d.isNullOrEmpty()) { val h = sha(d); return readDevices().any { eq(h, it.first) } }
        false
    }

    /** Valid, unexpired entries only; a damaged file counts as empty. Same JSON shape as the desktop's devices.json. */
    fun readDevices(): List<Pair<String, LocalDate>> {
        val txt = try { devicesFile.readText() } catch (_: Exception) { return emptyList() }
        val cutoff = today().minusDays(DEVICE_DAYS.toLong()); val now = today()
        return Regex("\\{[^{}]*\\}").findAll(txt).mapNotNull { m ->
            val h = Regex("\"hash\"\\s*:\\s*\"([0-9a-f]{64})\"").find(m.value)?.groupValues?.get(1) ?: return@mapNotNull null
            val c = Regex("\"created\"\\s*:\\s*\"(\\d{4}-\\d{2}-\\d{2})\"").find(m.value)?.groupValues?.get(1) ?: return@mapNotNull null
            val d = try { LocalDate.parse(c) } catch (_: Exception) { return@mapNotNull null }
            if (d.isAfter(cutoff) && !d.isAfter(now)) h to d else null
        }.toList()
    }

    private fun writeDevices(devs: List<Pair<String, LocalDate>>) {
        try {
            devicesFile.parentFile?.mkdirs()
            val body = devs.joinToString(",\n") { "  {\n   \"hash\": \"${it.first}\",\n   \"created\": \"${it.second}\"\n  }" }
            val tmp = File(devicesFile.path + ".tmp")
            tmp.writeText("{\n \"devices\": [" + (if (devs.isEmpty()) "" else "\n$body\n ") + "]\n}")
            if (!tmp.renameTo(devicesFile)) { devicesFile.delete(); tmp.renameTo(devicesFile) }
        } catch (_: Exception) {}
    }
}
