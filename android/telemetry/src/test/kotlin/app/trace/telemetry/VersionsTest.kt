package app.trace.telemetry

import org.junit.Assert.*
import org.junit.Test

class VersionsTest {
    @Test fun comparesNumericallyNotAsText() {
        assertTrue(Versions.isNewer("1.0.10", "1.0.9"))
        assertFalse(Versions.isNewer("1.0.9", "1.0.10"))
        assertTrue(Versions.isNewer("1.1.0", "1.0.99"))
        assertTrue(Versions.isNewer("2.0", "1.9.9"))
    }

    @Test fun ignoresLeadingVAndSuffixAndPads() {
        assertFalse(Versions.isNewer("v1.0.3", "1.0.3"))
        assertFalse(Versions.isNewer("1.0", "1.0.0"))
        assertFalse(Versions.isNewer("1.0.3-rc1", "1.0.3"))
        assertTrue(Versions.isNewer("V1.0.4+build7", "1.0.3"))
    }

    @Test fun evaluateDecidesCurrentOrAvailable() {
        assertEquals(Versions.Status.Current, Versions.evaluate("v1.0.3", "u", false, false, "1.0.3"))
        assertEquals(Versions.Status.Available("1.0.4", "u"), Versions.evaluate("v1.0.4", "u", false, false, "1.0.3"))
        assertEquals(Versions.Status.Current, Versions.evaluate("v1.0.2", "u", false, false, "1.0.3"))
    }

    @Test fun skipsDraftsPreReleasesAndJunkTags() {
        assertNull(Versions.evaluate("v9.9.9", "u", true, false, "1.0.3"))
        assertNull(Versions.evaluate("v9.9.9", "u", false, true, "1.0.3"))
        assertNull(Versions.evaluate("latest", "u", false, false, "1.0.3"))
    }
}
