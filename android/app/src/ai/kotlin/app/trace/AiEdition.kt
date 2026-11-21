package app.trace

import android.app.ActivityManager
import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.background
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.Color
import app.trace.telemetry.LlmGuard
import app.trace.telemetry.LocalHttp
import org.json.JSONObject
import java.io.File
import java.net.ServerSocket

/**
 * TRACE AI: the local AI wording on the phone. A pinned llama.cpp `llama-server` (built for arm64 in CI and shipped as
 * libllama_server.so) is started on 127.0.0.1 for the length of one coach report, asked to reword each rule-based tip,
 * and stopped again. The model is bundled in the APK as an asset and copied to filesDir on first use (the server needs
 * a real file). Everything the model returns goes through [LlmGuard.guard]; anything it changes is thrown away.
 * It never runs while telemetry is arriving ([TelemetryGate]).
 */
object AiWording {
    const val EDITION = true
    const val MODEL_ASSET = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
    const val MODEL_BYTES = 807_694_464L
    private const val SERVER_LIB = "libllama_server.so"
    private val lock = Any()

    private fun serverExe(ctx: Context) = File(ctx.applicationInfo.nativeLibraryDir, SERVER_LIB)
    private fun modelFile(ctx: Context) = File(ctx.filesDir, "llm/$MODEL_ASSET")

    /** True when the server binary is installed and no telemetry is arriving. */
    fun ready(ctx: Context): Boolean = !TelemetryGate.receiving && serverExe(ctx).isFile

    /** The model on disk, copied out of the APK the first time (a partial copy is never used). */
    private fun ensureModel(ctx: Context): File {
        val f = modelFile(ctx)
        if (f.isFile && f.length() == MODEL_BYTES) return f
        f.parentFile?.mkdirs()
        val tmp = File(f.parentFile, f.name + ".part")
        ctx.assets.open(MODEL_ASSET).use { i -> tmp.outputStream().use { o -> i.copyTo(o, 1 shl 20) } }
        if (tmp.length() != MODEL_BYTES) { tmp.delete(); throw java.io.IOException("the bundled model is the wrong size") }
        if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
        return f
    }

    private fun freePort(): Int = ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1")).use { it.localPort }

    /**
     * Starts the server, runs [block] with a rewriter, stops the server. Null when the AI can't run right now (telemetry
     * is arriving, no server binary, it did not start in time): the caller then keeps the rules text.
     */
    fun <T> withRewriter(ctx: Context, block: (LlmGuard.Rewriter) -> T): T? = synchronized(lock) {
        if (TelemetryGate.receiving || !serverExe(ctx).isFile) return null
        val port = freePort()
        var proc: Process? = null
        try {
            val model = ensureModel(ctx)
            val cores = Runtime.getRuntime().availableProcessors()
            proc = ProcessBuilder(serverExe(ctx).absolutePath, "-m", model.absolutePath, "--host", "127.0.0.1", "--port", "$port",
                "-c", "${LlmGuard.CONTEXT}", "-t", "${LlmGuard.nThreads(cores)}")
                .redirectErrorStream(true).redirectOutput(ProcessBuilder.Redirect.DISCARD).start()
            val deadline = System.nanoTime() + 90_000_000_000L
            var up = false
            while (!up && System.nanoTime() < deadline) {
                if (!proc.isAlive) return null
                up = try { LocalHttp.get(port, "/health", 2_000).status == 200 } catch (_: Exception) { Thread.sleep(500); false }
            }
            if (!up) return null
            val rewriter = LlmGuard.Rewriter { tip ->
                if (TelemetryGate.receiving) null      // telemetry started while we were working: stop using the CPU
                else {
                    val body = "{\"messages\":${LlmGuard.messagesJson(tip)},\"temperature\":${LlmGuard.TEMPERATURE},\"max_tokens\":${LlmGuard.MAX_TOKENS},\"stream\":false}"
                    val r = LocalHttp.postJson(port, "/v1/chat/completions", body, (LlmGuard.TIMEOUT_S * 1000).toInt())
                    if (r.status != 200) null
                    else JSONObject(r.body).getJSONArray("choices").getJSONObject(0).getJSONObject("message").getString("content")
                }
            }
            block(rewriter)
        } catch (_: Exception) {
            null
        } finally {
            try { proc?.destroy() } catch (_: Exception) {}
        }
    }
}

/** First launch of TRACE AI: what it is, what it needs, a live check of this phone, and where TRACE Lite is the better choice. */
@Composable
fun AiGate(a: AP, content: @Composable () -> Unit) {
    val ctx = LocalContext.current
    val prefs = remember { ctx.getSharedPreferences("trace", Context.MODE_PRIVATE) }
    var ack by remember { mutableStateOf(prefs.getBoolean("ai_warning_ack", false)) }
    if (ack) { content(); return }
    val mem = remember { ActivityManager.MemoryInfo().also { (ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager).getMemoryInfo(it) } }
    val rating = LlmGuard.ratePhone(mem.totalMem / (1024.0 * 1024.0 * 1024.0))
    val colour = when (rating.level) { "green" -> a.grn; "amber" -> a.yel; else -> a.acc }
    Column(Modifier.fillMaxSize().background(a.bg).statusBarsPadding().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Lbl(a, stringResource(R.string.ai_warning_title), a.cyn, 20, true)
        Lbl(a, stringResource(R.string.ai_warning_body), a.fg, 13)
        Lbl(a, "PHONE CHECK: " + rating.level.uppercase(), colour, 13, true)
        Lbl(a, rating.text, colour, 13)
        Lbl(a, stringResource(R.string.ai_warning_recommended), a.dim, 12, true)
        Lbl(a, LlmGuard.RECOMMENDED_PHONES, a.dim, 12)
        Lbl(a, "${LlmGuard.LLAMA_NOTICE}. Llama 3.2 is licensed under the Llama 3.2 Community License: ${LlmGuard.LLAMA_LICENSE_URL}", a.dim, 11)
        TBtn(a, if (rating.level == "red") stringResource(R.string.ai_warning_continue_anyway) else stringResource(R.string.ai_warning_continue), Modifier.fillMaxWidth(), on = rating.level != "red") {
            prefs.edit().putBoolean("ai_warning_ack", true).apply(); ack = true
        }
    }
}

/** The AI app's About line on the launcher. */
@Composable
fun AiAbout(a: AP) {
    Lbl(a, "TRACE AI  -  ${LlmGuard.LLAMA_NOTICE}  -  Llama 3.2 Community License: ${LlmGuard.LLAMA_LICENSE_URL}", a.dim, 10,
        modifier = Modifier.fillMaxWidth().padding(bottom = 6.dp))
}
