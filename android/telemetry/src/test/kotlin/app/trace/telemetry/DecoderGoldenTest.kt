package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

class DecoderGoldenTest {
    private fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private fun same(path: String, exp: Any?, act: Any?) {
        when {
            exp == null || exp == JSONObject.NULL -> assertNull("$path expected null", act)
            exp is JSONArray -> {
                val l = act as List<*>
                assertEquals("$path size", exp.length(), l.size)
                for (i in 0 until exp.length()) same("$path[$i]", exp.get(i), l[i])
            }
            exp is Boolean -> assertEquals(path, exp, act)
            exp is String -> assertEquals(path, exp, act)
            exp is Number -> {
                val a = (act as Number).toDouble(); val e = exp.toDouble()
                assertEquals(path, e, a, 1e-9 * maxOf(1.0, Math.abs(e)))
            }
            else -> fail("$path unexpected type ${exp::class}")
        }
    }

    @Test fun matchesPythonOutput() {
        val root = JSONObject(File("src/test/resources/fixtures.json").readText())
        val cases = root.getJSONArray("cases")
        val dec = PacketDecoder().apply { track = "Sportsland SUGO"; car = "Honda Civic SI '87" }
        for (c in 0 until cases.length()) {
            val case = cases.getJSONObject(c)
            val got = dec.decode(unhex(case.getString("hex")))
            assertNotNull("case $c decoded", got)
            val exp = case.getJSONObject("expected")
            assertEquals("case $c key set", exp.keySet().toSet(), got!!.keys)
            for (k in exp.keySet()) same("case$c.$k", exp.get(k), got[k])
        }
    }

    @Test fun rejectsBadPackets() {
        val neg = JSONObject(File("src/test/resources/fixtures.json").readText()).getJSONObject("negative")
        val d = PacketDecoder()
        assertNull(d.decode(unhex(neg.getString("short_hex"))))
        assertNull(d.decode(unhex(neg.getString("garbage_hex"))))
    }

    @Test fun salsa20EstreamVector() {
        val key = ByteArray(32).also { it[0] = 0x80.toByte() }
        val out = Salsa20.crypt(ByteArray(64), key, ByteArray(8))
        val hex = out.joinToString("") { "%02X".format(it) }
        assertEquals("E3BE8FDD8BECA2E3EA8EF9475B29A6E7003951E1097A5C38D23B7A5FAD9F6844B22C97559E2723C7CBBD3FE4FC8D9A0744652A83E72A9C461876AF4D7EF1A117", hex)
    }
}
