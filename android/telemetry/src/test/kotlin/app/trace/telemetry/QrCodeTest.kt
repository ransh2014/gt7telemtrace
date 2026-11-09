package app.trace.telemetry

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** The phone draws the identical QR to the desktop: qr_golden.json is written by tools/gen_android_webui.py from qr.py. */
class QrCodeTest {
    @Test fun matchesDesktopGoldenMatrices() {
        val txt = QrCodeTest::class.java.getResourceAsStream("/qr_golden.json")!!.bufferedReader().readText()
        val samples = JSONObject(txt).getJSONArray("samples")
        assertTrue(samples.length() >= 10)
        for (i in 0 until samples.length()) {
            val s = samples.getJSONObject(i)
            val want = s.getJSONArray("rows").let { r -> (0 until r.length()).map { r.getString(it) } }
            val got = QrCode.encode(s.getString("text")).map { row -> row.joinToString("") { if (it) "1" else "0" } }
            assertEquals("QR for '${s.getString("text").take(30)}'", want, got)
        }
    }

    @Test fun rejectsTooLong() {
        assertEquals(213, QrCode.MAX_BYTES)
        try { QrCode.encode("x".repeat(214)); fail() } catch (_: IllegalArgumentException) {}
    }
}
