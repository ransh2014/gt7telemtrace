package app.trace.telemetry

import org.junit.Assert.*
import org.junit.Test

class AutoLapTest {
    @Test fun armsAfterHoldingWhileDriving() {
        val a = AutoLap()
        assertEquals(AutoLap.Action.NONE, a.step(true, true, true, false, 0.0))
        assertEquals(AutoLap.Action.NONE, a.step(true, true, true, false, 1.0))
        assertEquals(AutoLap.Action.ARM, a.step(true, true, true, false, 1.6))
        assertTrue(a.active)
    }

    @Test fun disarmsAfterLeavingSessionAndNeverWhenUserArmed() {
        val a = AutoLap()
        a.step(true, true, true, false, 0.0); a.step(true, true, true, false, 2.0)
        assertEquals(AutoLap.Action.NONE, a.step(true, false, false, true, 3.0))
        assertEquals(AutoLap.Action.DISARM_ENDED, a.step(true, false, false, true, 6.5))
        assertFalse(a.active)
        // busy (the user armed it) -> never arms by itself
        val b = AutoLap()
        b.step(true, true, true, true, 0.0); assertEquals(AutoLap.Action.NONE, b.step(true, true, true, true, 5.0))
    }

    @Test fun userStopBlocksUntilSessionLeft() {
        val a = AutoLap()
        a.step(true, true, true, false, 0.0); a.step(true, true, true, false, 2.0)
        a.onUserStop()
        assertEquals(AutoLap.Action.NONE, a.step(true, true, true, false, 10.0))
        assertEquals(AutoLap.Action.NONE, a.step(true, true, true, false, 12.0))
        a.step(true, false, false, false, 13.0)               // leaves the session: block clears
        a.step(true, true, true, false, 14.0)
        assertEquals(AutoLap.Action.ARM, a.step(true, true, true, false, 15.6))
    }

    @Test fun turningOffDisarmsAnAutoLap() {
        val a = AutoLap()
        a.step(true, true, true, false, 0.0); a.step(true, true, true, false, 2.0)
        assertEquals(AutoLap.Action.DISARM_OFF, a.step(false, true, true, false, 3.0))
    }

    @Test fun sessionAndArmRules() {
        assertTrue(AutoLap.inSession(true, false, 0)); assertFalse(AutoLap.inSession(true, false, 3))
        assertFalse(AutoLap.inSession(false, false, 0)); assertFalse(AutoLap.inSession(true, true, 0))
        assertFalse(AutoLap.canArm(true, false, 4.9)); assertTrue(AutoLap.canArm(true, false, 5.0))
    }
}
