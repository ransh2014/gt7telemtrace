package app.trace.telemetry

import org.junit.Assert.*
import org.junit.Test
import java.net.ServerSocket
import kotlin.concurrent.thread

/** LocalHttp talks to a throwaway server on 127.0.0.1 that answers with the three body framings llama-server may use. */
class LocalHttpTest {
    /** Serves one canned raw response per connection, remembers the request it received. */
    private class Canned(vararg responses: String) {
        val server = ServerSocket(0, 5, java.net.InetAddress.getByName("127.0.0.1"))
        val requests = ArrayList<String>()
        private val queue = ArrayDeque(responses.toList())
        init {
            thread(isDaemon = true) {
                while (queue.isNotEmpty()) {
                    val c = try { server.accept() } catch (_: Exception) { break }
                    val inp = c.getInputStream(); val sb = StringBuilder()
                    c.soTimeout = 2000
                    try {
                        while (!sb.endsWith("\r\n\r\n")) sb.append(inp.read().toChar())
                        val len = Regex("Content-Length: (\\d+)").find(sb)?.groupValues?.get(1)?.toInt() ?: 0
                        repeat(len) { sb.append(inp.read().toChar()) }
                    } catch (_: Exception) {}
                    requests.add(sb.toString())
                    c.getOutputStream().apply { write(queue.removeFirst().toByteArray()); flush() }
                    c.close()
                }
            }
        }
        val port get() = server.localPort
    }

    @Test fun contentLengthBody() {
        val s = Canned("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{\"ok\":true}")
        val r = LocalHttp.get(s.port, "/health")
        assertEquals(200, r.status); assertEquals("{\"ok\":true}", r.body)
        assertTrue(s.requests[0].startsWith("GET /health HTTP/1.1\r\n")); assertTrue("Host: 127.0.0.1:${s.port}" in s.requests[0])
    }

    @Test fun chunkedBody() {
        val s = Canned("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
        val r = LocalHttp.postJson(s.port, "/v1/chat/completions", "{\"a\":1}")
        assertEquals("hello world", r.body)
        assertTrue(s.requests[0].startsWith("POST /v1/chat/completions HTTP/1.1")); assertTrue("Content-Length: 7" in s.requests[0]); assertTrue(s.requests[0].endsWith("{\"a\":1}"))
    }

    @Test fun bodyUntilTheConnectionCloses() {
        val s = Canned("HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nplain text until close")
        assertEquals("plain text until close", LocalHttp.get(s.port, "/x").body)
    }

    @Test fun errorStatusesAreReturnedNotThrown() {
        val s = Canned("HTTP/1.1 503 Service Unavailable\r\nContent-Length: 7\r\n\r\nloading")
        val r = LocalHttp.get(s.port, "/health")
        assertEquals(503, r.status); assertEquals("loading", r.body)
    }

    @Test fun aTruncatedBodyIsAnError() {
        val s = Canned("HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\nshort")
        try { LocalHttp.get(s.port, "/x"); fail("expected an exception") } catch (_: java.io.IOException) {}
    }

    @Test fun nothingListeningIsAnError() {
        val port = ServerSocket(0).use { it.localPort }   // a free port that is closed again
        try { LocalHttp.get(port, "/health", 500); fail("expected an exception") } catch (_: java.io.IOException) {}
    }
}
