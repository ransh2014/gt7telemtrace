package app.trace.telemetry

/**
 * llm.py on the phone (TRACE AI): the pure parts of the local AI wording. A small model only REWORDS the coach's
 * rule-based tips; this guard throws its sentence away unless every number, corner label (T4) and direction word
 * (earlier / later / sooner) survives, and the prompt, limits and phone memory bands are the desktop's. Tested against
 * the desktop's output (golden4.json.gz). No Android, no model, no network here.
 */
object LlmGuard {
    const val MAX_CHARS = 240
    const val TEMPERATURE = 0.2
    const val MAX_TOKENS = 80
    const val TIMEOUT_S = 20.0
    const val CONTEXT = 1024
    const val MAX_THREADS = 6

    const val SYSTEM_PROMPT = "You are a friendly racing coach. Rewrite the driving tip you are given as ONE short, friendly sentence of plain " +
        "English. Keep every number, every corner label such as T4, every unit and every direction word (earlier, later, " +
        "sooner, more) exactly as given. Do not add advice, numbers or new facts. Reply with the sentence only."

    const val HEADER_RULES = "Coach:"
    const val HEADER_AI = "Coach (AI wording):"
    const val LLAMA_NOTICE = "Built with Llama"
    const val LLAMA_LICENSE_URL = "https://www.llama.com/llama3_2/license/"

    val DIRECTION_WORDS = listOf("earlier", "later", "sooner", "more", "less", "early", "late", "faster", "slower", "higher", "lower")
    private val ENGLISH = setOf("the", "a", "an", "to", "you", "your", "at", "in", "on", "of", "for", "by", "is", "it", "and", "try", "with",
        "into", "through", "before", "after", "bit", "turn", "corner", "brake", "throttle", "speed", "lift", "carry",
        "get", "back", "more", "later", "earlier", "sooner", "than")
    private val CORNER = Regex("\\bT(\\d+)\\b", RegexOption.IGNORE_CASE)
    private val NUMBER = Regex("(?<![\\w.])\\d+(?:\\.\\d+)?")
    private val WORDS = Regex("[A-Za-z']+")
    private val SENTENCE_BREAK = Regex("[.!?](?:\\s+[A-Z])")

    class Facts(val numbers: Set<String>, val corners: Set<String>, val directions: Set<String>) {
        override fun equals(other: Any?) = other is Facts && numbers == other.numbers && corners == other.corners && directions == other.directions
        override fun hashCode() = numbers.hashCode() * 31 + corners.hashCode() * 7 + directions.hashCode()
    }

    private fun numKey(s: String): String {
        val f = s.toDouble()
        return if (f == Math.rint(f)) f.toLong().toString() else f.toString()
    }

    /** (numbers, corner labels, direction words) in a tip. Corner labels are removed before numbers are read. */
    fun facts(text: String): Facts {
        val corners = CORNER.findAll(text).map { "T" + it.groupValues[1] }.toSet()
        val rest = CORNER.replace(text, " ")
        val numbers = NUMBER.findAll(rest).map { numKey(it.value) }.toSet()
        val words = WORDS.findAll(rest).map { it.value.lowercase() }.toSet()
        return Facts(numbers, corners, DIRECTION_WORDS.filter { it in words }.toSet())
    }

    fun looksEnglish(text: String): Boolean {
        if (text.isEmpty() || text.count { it.code < 128 }.toDouble() / text.length < 0.97) return false
        val words = WORDS.findAll(text).map { it.value.lowercase() }.toList()
        return words.size >= 3 && words.any { it in ENGLISH }
    }

    /** (accepted wording, "") or (null, why it was rejected): only text that keeps every fact, in one short English sentence. */
    fun guard(rulesText: String, candidate: String?): Pair<String?, String> {
        if (candidate == null) return null to "no answer"
        val c = candidate.trim().trim('"', '“', '”').trim()
        if (c.isEmpty()) return null to "empty"
        if ('\n' in c || '\r' in c) return null to "more than one line"
        if (c.length > MAX_CHARS || c.length > 3 * rulesText.length + 40) return null to "too long"
        if (SENTENCE_BREAK.containsMatchIn(c)) return null to "more than one sentence"
        if (!looksEnglish(c)) return null to "not English"
        if (facts(c) != facts(rulesText)) return null to "numbers, corner labels or direction words changed"
        return c to ""
    }

    fun nThreads(cores: Int): Int = maxOf(1, minOf(MAX_THREADS, cores - 1))

    fun messagesJson(tip: String): String {
        fun q(s: String) = org.json.JSONObject.quote(s)
        return "[{\"role\":\"system\",\"content\":${q(SYSTEM_PROMPT)}},{\"role\":\"user\",\"content\":${q(tip)}}]"
    }

    // ---- the phone's memory bands (llm.rate_phone) -----------------------------------------------------------------
    const val RECOMMENDED_PHONES = "Minimum: a mid-range phone from about 2022 (Snapdragon 7-series class) with 6 GB of RAM. It works; replies take " +
        "several seconds and the phone may warm up.\n" +
        "Recommended: a Snapdragon 8-series class phone with 8 GB of RAM or more. Replies take 1-3 seconds."

    class Rating(val level: String, val text: String)

    /** red under 5 GB, amber 5 to under 7 GB, green 7 GB and up (phones report less memory than their box says). */
    fun ratePhone(ramGb: Double): Rating = when {
        ramGb < 5 -> Rating("red", "This phone reports ${fixed(ramGb, 1)} GB of RAM and TRACE AI needs about 5 GB or more. Use TRACE Lite instead: the same app without the AI.")
        ramGb < 7 -> Rating("amber", "This phone reports ${fixed(ramGb, 1)} GB of RAM. TRACE AI should work, but replies will take several seconds and the phone may warm up.")
        else -> Rating("green", "This phone reports ${fixed(ramGb, 1)} GB of RAM. Good to go.")
    }

    // ---- coach tips, reworded (llm.describe_with_ai) -------------------------------------------------------------
    fun interface Rewriter { fun reword(tip: String): String? }

    /** Coach.describe(tips) with each tip's sentence replaced by the model's wording when it passed the guard.
     *  Returns (text, any tip reworded); with no rewriter, or nothing passing, it is exactly Coach.describe(tips). */
    fun describeWithAi(tips: List<Coach.Tip>, rewriter: Rewriter?): Pair<String, Boolean> {
        val plain = Coach.describe(tips)
        if (rewriter == null || tips.isEmpty()) return plain to false
        var used = false
        val out = tips.map { t ->
            val ok = try { guard(t.text, rewriter.reword(t.text)).first } catch (_: Exception) { null }
            if (ok != null) { used = true; Coach.Tip(t.corner, t.apex, t.brakePos, t.kind, ok, t.cue, t.timeS, t.estS) } else t
        }
        return if (used) Coach.describe(out).replaceFirst(HEADER_RULES, HEADER_AI) to true else plain to false
    }
}
