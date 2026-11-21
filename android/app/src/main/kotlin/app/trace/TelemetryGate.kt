package app.trace

/**
 * TRACE AI only works on a lap or session that is over: the local model must never compete with the live dashboard for
 * the CPU while telemetry is arriving. LiveController flips this each poll; the AI engine refuses to run while it is set.
 * (TRACE Lite has no AI, so there it is simply never read.)
 */
object TelemetryGate {
    @Volatile var receiving = false
}
