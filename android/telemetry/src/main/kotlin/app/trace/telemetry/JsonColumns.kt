package app.trace.telemetry

import java.io.File
import java.io.InputStream
import java.io.InputStreamReader
import java.io.Reader

/**
 * Streaming JSON reader for lap and race files. The top-level object comes back as a plain
 * map (without `samples`); the `samples` array is read straight into columns, because a race
 * can be a hundred thousand samples of seventy keys and one JSONObject per row would not fit
 * in a phone's heap.
 */
object JsonColumns {
    class Raw(val meta: LinkedHashMap<String, Any?>, val n: Int, val nums: LinkedHashMap<String, DoubleArray>,
              val strs: LinkedHashMap<String, Array<String?>>, val hadSamples: Boolean)

    fun read(file: File): Raw = file.inputStream().buffered(1 shl 16).use { read(it) }
    fun read(ins: InputStream): Raw = Parser(InputStreamReader(ins, Charsets.UTF_8)).parseFile()
    fun parse(text: String): Raw = Parser(text.reader()).parseFile()

    private class Builder {
        var nums = DoubleArray(1024); var strs: Array<String?>? = null
        var len = 0                         // rows written so far (including padding)
        fun ensure(i: Int) { if (i >= nums.size) nums = nums.copyOf(maxOf(nums.size * 2, i + 1)); strs?.let { if (i >= it.size) strs = it.copyOf(nums.size) } }
        fun padTo(row: Int) { ensure(row); while (len < row) { nums[len] = Double.NaN; len++ } }
        fun putNum(row: Int, v: Double) { padTo(row); ensure(row); nums[row] = v; len = row + 1 }
        fun putStr(row: Int, s: String) {
            padTo(row); ensure(row)
            if (strs == null) strs = arrayOfNulls(nums.size)
            strs!![row] = s
            nums[row] = s.trim().toDoubleOrNull() ?: Double.NaN
            len = row + 1
        }
    }

    private class Parser(private val r: Reader) {
        private val buf = CharArray(1 shl 15); private var pos = 0; private var lim = 0; private var eof = false
        private fun fill(): Boolean { if (eof) return false; lim = r.read(buf, 0, buf.size); pos = 0; if (lim <= 0) { eof = true; lim = 0; return false }; return true }
        private fun peek(): Int { if (pos >= lim && !fill()) return -1; return buf[pos].code }
        private fun next(): Int { val c = peek(); if (c >= 0) pos++; return c }
        private fun ws() { while (true) { val c = peek(); if (c == ' '.code || c == '\n'.code || c == '\r'.code || c == '\t'.code || c == 0xFEFF) pos++ else return } }
        private fun expect(ch: Char) { ws(); val c = next(); if (c != ch.code) throw IllegalArgumentException("Not valid JSON (expected '$ch')") }
        private fun lit(s: String) { for (ch in s) if (next() != ch.code) throw IllegalArgumentException("Not valid JSON") }

        fun parseFile(): Raw {
            ws(); expect('{')
            val meta = LinkedHashMap<String, Any?>()
            val builders = LinkedHashMap<String, Builder>()
            var rows = 0; var had = false
            ws()
            if (peek() == '}'.code) { next() } else while (true) {
                ws(); val key = readString(); expect(':'); ws()
                if (key == "samples" && peek() == '['.code) {
                    had = true; rows = parseSamples(builders)
                } else meta[key] = value()
                ws(); val c = next()
                if (c == ','.code) continue
                if (c == '}'.code) break
                throw IllegalArgumentException("Not valid JSON")
            }
            val nums = LinkedHashMap<String, DoubleArray>(); val strs = LinkedHashMap<String, Array<String?>>()
            for ((k, b) in builders) {
                b.padTo(rows)
                nums[k] = b.nums.copyOf(rows)
                b.strs?.let { s -> strs[k] = s.copyOf(rows) }
            }
            return Raw(meta, rows, nums, strs, had)
        }

        private fun parseSamples(b: LinkedHashMap<String, Builder>): Int {
            expect('['); ws()
            var row = 0
            if (peek() == ']'.code) { next(); return 0 }
            while (true) {
                ws()
                if (peek() == '{'.code) {
                    next(); ws()
                    if (peek() == '}'.code) next() else while (true) {
                        ws(); val k = readString(); expect(':'); ws()
                        val bl = b.getOrPut(k) { Builder() }
                        when (val c = peek()) {
                            '"'.code -> bl.putStr(row, readString())
                            't'.code -> { lit("true"); bl.putNum(row, 1.0) }
                            'f'.code -> { lit("false"); bl.putNum(row, 0.0) }
                            'n'.code -> { lit("null"); bl.putNum(row, Double.NaN) }
                            '{'.code, '['.code -> { value(); bl.putNum(row, Double.NaN) }
                            else -> { val d = number(); bl.putNum(row, if (d.isInfinite()) Double.NaN else d) }
                        }
                        ws(); val e = next()
                        if (e == ','.code) continue
                        if (e == '}'.code) break
                        throw IllegalArgumentException("Not valid JSON")
                    }
                    row++
                } else value()   // a non-dict sample is ignored, as the PC loader does
                ws(); val e = next()
                if (e == ','.code) continue
                if (e == ']'.code) break
                throw IllegalArgumentException("Not valid JSON")
            }
            return row
        }

        private fun value(): Any? {
            ws()
            return when (val c = peek()) {
                '"'.code -> readString()
                '{'.code -> { next(); val m = LinkedHashMap<String, Any?>(); ws()
                    if (peek() == '}'.code) { next(); m } else { while (true) { ws(); val k = readString(); expect(':'); m[k] = value(); ws(); val e = next(); if (e == ','.code) continue; if (e == '}'.code) break; throw IllegalArgumentException("Not valid JSON") }; m } }
                '['.code -> { next(); val l = ArrayList<Any?>(); ws()
                    if (peek() == ']'.code) { next(); l } else { while (true) { l.add(value()); ws(); val e = next(); if (e == ','.code) continue; if (e == ']'.code) break; throw IllegalArgumentException("Not valid JSON") }; l } }
                't'.code -> { lit("true"); true }
                'f'.code -> { lit("false"); false }
                'n'.code -> { lit("null"); null }
                else -> number()
            }
        }

        private val sb = StringBuilder()
        private fun readString(): String {
            ws(); if (next() != '"'.code) throw IllegalArgumentException("Not valid JSON (string expected)")
            sb.setLength(0)
            while (true) {
                val c = next()
                if (c < 0) throw IllegalArgumentException("Not valid JSON (unterminated string)")
                if (c == '"'.code) break
                if (c == '\\'.code) {
                    when (val e = next()) {
                        'n'.code -> sb.append('\n'); 't'.code -> sb.append('\t'); 'r'.code -> sb.append('\r')
                        'b'.code -> sb.append('\b'); 'f'.code -> sb.append('\u000c')
                        'u'.code -> { var v = 0; repeat(4) { v = v * 16 + Character.digit(next(), 16) }; sb.append(v.toChar()) }
                        else -> sb.append(e.toChar())
                    }
                } else sb.append(c.toChar())
            }
            return sb.toString()
        }

        private val POW10 = DoubleArray(23).also { var p = 1.0; for (i in it.indices) { it[i] = p; p *= 10.0 } }
        private fun number(): Double {
            var c = peek()
            if (c == 'N'.code) { lit("NaN"); return Double.NaN }
            if (c == 'I'.code) { lit("Infinity"); return Double.POSITIVE_INFINITY }
            var neg = false
            if (c == '-'.code) { next(); neg = true; if (peek() == 'I'.code) { lit("Infinity"); return Double.NEGATIVE_INFINITY } }
            sb.setLength(0)
            var mant = 0L; var digits = 0; var exp10 = 0; var simple = true
            var any = false
            while (true) {
                c = peek()
                if (c >= '0'.code && c <= '9'.code) { next(); any = true; sb.append(c.toChar()); if (digits < 18) { mant = mant * 10 + (c - '0'.code); if (mant != 0L) digits++ } else { simple = false } }
                else break
            }
            if (peek() == '.'.code) {
                next(); sb.append('.')
                while (true) {
                    c = peek()
                    if (c >= '0'.code && c <= '9'.code) { next(); any = true; sb.append(c.toChar()); if (digits < 18) { mant = mant * 10 + (c - '0'.code); if (mant != 0L) digits++; exp10-- } else simple = false }
                    else break
                }
            }
            if (!any) throw IllegalArgumentException("Not valid JSON (number expected)")
            c = peek()
            if (c == 'e'.code || c == 'E'.code) {
                next(); sb.append('e'); simple = false
                c = peek(); if (c == '+'.code || c == '-'.code) { next(); sb.append(c.toChar()) }
                while (true) { c = peek(); if (c >= '0'.code && c <= '9'.code) { next(); sb.append(c.toChar()) } else break }
            }
            val v = if (simple && digits <= 15 && -exp10 <= 22) {
                if (exp10 >= 0) mant.toDouble() else mant.toDouble() / POW10[-exp10]
            } else sb.toString().toDouble()
            return if (neg) -v else v
        }
    }
}
