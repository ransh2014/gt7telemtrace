package app.trace

import android.content.Context
import androidx.compose.runtime.Composable
import app.trace.telemetry.LlmGuard

/** TRACE Lite: the same API as the AI edition (src/ai), with nothing behind it. No model, no native code, no warning screen. */
object AiWording {
    const val EDITION = false
    fun ready(ctx: Context): Boolean = false
    fun <T> withRewriter(ctx: Context, block: (LlmGuard.Rewriter) -> T): T? = null
}

@Composable
fun AiGate(a: AP, content: @Composable () -> Unit) = content()

@Composable
fun AiAbout(a: AP) {}
