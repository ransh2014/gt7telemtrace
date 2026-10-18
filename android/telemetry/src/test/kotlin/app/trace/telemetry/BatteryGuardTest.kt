package app.trace.telemetry

import org.junit.Assert.*
import org.junit.Test

class BatteryGuardTest {
    @Test fun warnsOnlyWhileRecordingUnpluggedAndLow() {
        assertTrue(BatteryGuard.needsWarning(15, plugged = false, active = true))
        assertFalse(BatteryGuard.needsWarning(15, plugged = true, active = true))
        assertFalse(BatteryGuard.needsWarning(15, plugged = false, active = false))
        assertFalse(BatteryGuard.needsWarning(20, plugged = false, active = true))
        assertFalse(BatteryGuard.needsWarning(null, plugged = false, active = true))
    }

    @Test fun showsBarThenAlarmsIfIgnored() {
        val g = BatteryGuard()
        val first = g.step(18, false, true, 0.0)
        assertTrue(first.show); assertEquals(listOf(BatteryGuard.Event.WARN), first.events)
        val later = g.step(18, false, true, 10.0)
        assertTrue(later.show); assertTrue(later.events.isEmpty())
        val alarm = g.step(18, false, true, 31.0)
        assertEquals(listOf(BatteryGuard.Event.ALARM), alarm.events)
    }

    @Test fun acknowledgingHidesBarAndSilencesAlarm() {
        val g = BatteryGuard()
        g.step(18, false, true, 0.0)
        g.ack()
        val st = g.step(18, false, true, 60.0)
        assertFalse(st.show); assertTrue(st.events.isEmpty())
    }

    @Test fun criticalLevelReAlarmsOnce() {
        val g = BatteryGuard()
        g.step(18, false, true, 0.0); g.ack()
        val crit = g.step(9, false, true, 5.0)
        assertTrue(crit.show)
        assertEquals(listOf(BatteryGuard.Event.CRITICAL, BatteryGuard.Event.ALARM), crit.events)
        assertFalse(g.step(8, false, true, 7.0).events.contains(BatteryGuard.Event.CRITICAL))
    }

    @Test fun plugInClearsEverything() {
        val g = BatteryGuard()
        g.step(18, false, true, 0.0)
        assertFalse(g.step(18, true, true, 4.0).show)
        // a later dip warns again from scratch
        assertEquals(listOf(BatteryGuard.Event.WARN), g.step(17, false, true, 100.0).events)
    }
}
