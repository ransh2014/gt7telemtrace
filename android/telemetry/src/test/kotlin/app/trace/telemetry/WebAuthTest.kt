package app.trace.telemetry

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.net.InetAddress
import java.nio.file.Files
import java.time.LocalDate

/** Same scenarios as tests/test_webui_auth.py, so the phone and the PC behave identically. */
class WebAuthTest {
    private var t = 1000.0
    private var day = LocalDate.of(2026, 11, 9)
    private val dir: File = Files.createTempDirectory("webauth").toFile()
    private val file = File(dir, "devices.json")
    private fun auth() = WebAuth(file, { t }, { day }).also { it.rotate() }

    @Test fun codeIsSixDigitsAndRotates() {
        val a = auth(); val seen = HashSet<String>()
        repeat(20) { a.rotate(); assertTrue(a.code.length == 6 && a.code.all { it.isDigit() }); seen.add(a.code) }
        assertTrue(seen.size > 1)
    }

    @Test fun correctAndWrongCode() {
        val a = auth()
        val wrong = "%06d".format((a.code.toInt() + 1) % 1_000_000)
        assertFalse(a.checkCode("10.0.0.2", wrong)); assertFalse(a.checkCode("10.0.0.2", null))
        assertTrue(a.checkCode("10.0.0.2", a.code))
        a.end(); assertFalse(a.checkCode("10.0.0.2", ""))
    }

    @Test fun lockoutBackoffAndCap() {
        val a = auth(); val ip = "10.0.0.9"
        repeat(4) { assertFalse(a.checkCode(ip, "x")) }
        assertFalse(a.locked(ip)); assertFalse(a.checkCode(ip, "x")); assertTrue(a.locked(ip))
        assertFalse(a.checkCode(ip, a.code))
        t += 59; assertTrue(a.locked(ip)); t += 1.5; assertFalse(a.locked(ip))
        val durations = ArrayList<Long>()
        repeat(6) {
            repeat(5) { a.checkCode(ip, "x") }
            val start = t
            while (a.locked(ip)) t += 1
            durations.add(Math.round(t - start))
        }
        assertEquals(listOf(120L, 240L, 480L, 600L, 600L, 600L), durations)
        assertTrue(a.checkCode("10.0.0.10", a.code)); assertTrue(a.checkCode(ip, a.code))
    }

    @Test fun qrTokenSingleUseAndExpiry() {
        val a = auth()
        val t1 = a.qrToken(); assertTrue(t1.length >= 43); assertEquals(t1, a.qrToken())
        assertFalse(a.consumeQr("nope")); assertTrue(a.consumeQr(t1)); assertFalse(a.consumeQr(t1))
        val t2 = a.qrToken(); assertNotEquals(t1, t2)
        t += WebAuth.QR_TTL_S; assertFalse(a.consumeQr(t2))
        val t3 = a.qrToken(); assertTrue(a.consumeQr(t3))
        a.end(); assertEquals("", a.qrToken())
    }

    @Test fun sessionsEndOnRotateEndAndLogout() {
        val a = auth(); var s = a.newSession()
        assertTrue(a.isAuthed(mapOf(WebAuth.SESSION_COOKIE to s)))
        assertFalse(a.isAuthed(mapOf(WebAuth.SESSION_COOKIE to s + "x")))
        a.rotate(); assertFalse(a.isAuthed(mapOf(WebAuth.SESSION_COOKIE to s)))
        s = a.newSession(); a.logoutAll(); assertFalse(a.isAuthed(mapOf(WebAuth.SESSION_COOKIE to s)))
    }

    @Test fun rememberedDeviceSurvivesRotationRestartAndExpires() {
        val a = auth(); val d = a.rememberDevice()
        a.rotate(); assertTrue(a.isAuthed(mapOf(WebAuth.DEVICE_COOKIE to d)))
        assertTrue(auth().isAuthed(mapOf(WebAuth.DEVICE_COOKIE to d)))          // app restart
        assertFalse(file.readText().contains(d))                                  // only the hash is stored
        assertTrue(file.readText().contains(WebAuth.sha(d)))
        day = day.plusDays(29); assertTrue(a.isAuthed(mapOf(WebAuth.DEVICE_COOKIE to d)))
        day = day.plusDays(1); assertFalse(a.isAuthed(mapOf(WebAuth.DEVICE_COOKIE to d)))
    }

    @Test fun logoutAllWipesDevices() {
        val a = auth(); val d = a.rememberDevice()
        a.logoutAll()
        assertFalse(a.isAuthed(mapOf(WebAuth.DEVICE_COOKIE to d)))
        assertTrue(a.readDevices().isEmpty())
    }

    @Test fun readsDesktopDevicesJson() {
        // the desktop writes json.dumps({"devices": [...]}, indent=1); the phone reads the same shape
        file.writeText("{\n \"devices\": [\n  {\n   \"hash\": \"${WebAuth.sha("tok")}\",\n   \"created\": \"2026-11-01\"\n  }\n ]\n}")
        assertTrue(auth().isAuthed(mapOf(WebAuth.DEVICE_COOKIE to "tok")))
        file.writeText("{not json")
        assertFalse(auth().isAuthed(mapOf(WebAuth.DEVICE_COOKIE to "tok")))
    }

    @Test fun loopbackAndCookies() {
        for (h in listOf("127.0.0.1", "127.8.9.1", "::1")) assertTrue(h, WebAuth.isLoopback(InetAddress.getByName(h)))
        for (h in listOf("192.168.1.5", "10.0.0.1")) assertFalse(h, WebAuth.isLoopback(InetAddress.getByName(h)))
        assertEquals(mapOf("a" to "1", "trace_s" to "xyz"), WebAuth.parseCookies("a=1; trace_s=xyz; b"))
    }
}
