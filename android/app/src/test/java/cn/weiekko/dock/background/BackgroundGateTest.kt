package cn.weiekko.dock.background

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BackgroundGateTest {
    @Test
    fun loginUsesTheCurrentHubToken() {
        var token = "hub-token"
        val gate = BackgroundGate { token }

        assertNull(gate.login("other"))
        assertNull(gate.login(""))
        val session = gate.login("hub-token")
        assertNotNull(session)
        assertTrue(gate.allow(session))

        token = "rotated"
        assertFalse(gate.allow(session))
        assertNotNull(gate.login("rotated"))
    }

    @Test
    fun logoutDropsTheSession() {
        val gate = BackgroundGate { "hub-token" }
        val session = gate.login("hub-token")

        gate.logout(session)

        assertFalse(gate.allow(session))
    }
}
