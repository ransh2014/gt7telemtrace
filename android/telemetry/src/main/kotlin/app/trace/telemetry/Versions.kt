package app.trace.telemetry

/** Mirrors gt7telem/updates.py: numeric version comparison and the "current / update available" decision. */
object Versions {
    /** "v1.0.10" -> [1, 0, 10, 0]; ignores a leading v and any -rc1 / +build suffix; padded so 1.0 == 1.0.0. */
    fun parse(s: String): List<Int> {
        val core = s.trim().trimStart('v', 'V').split('-', '+', limit = 2)[0]
        val nums = Regex("[0-9]+").findAll(core).map { it.value.toIntOrNull() ?: 0 }.toList()
        return nums + List(maxOf(0, 4 - nums.size)) { 0 }
    }

    fun isNewer(candidate: String, installed: String): Boolean {
        val a = parse(candidate); val b = parse(installed)
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }; val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return false
    }

    sealed class Status {
        object Current : Status()
        data class Available(val version: String, val url: String) : Status()
    }

    const val RELEASES_URL = "https://github.com/ransh2014/gt7telemtrace/releases/latest"

    /** null = nothing to show (draft, pre-release or unusable tag). */
    fun evaluate(tag: String, htmlUrl: String, draft: Boolean, prerelease: Boolean, installed: String): Status? {
        if (draft || prerelease) return null
        val t = tag.trim().trimStart('v', 'V')
        if (t.none { it.isDigit() }) return null
        return if (isNewer(t, installed)) Status.Available(t, htmlUrl.ifEmpty { RELEASES_URL }) else Status.Current
    }
}
