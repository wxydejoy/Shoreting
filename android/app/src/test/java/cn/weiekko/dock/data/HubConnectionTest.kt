package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HubConnectionTest {
    @Test
    fun baseUrlStripsSchemeAndPastedPort() {
        val connection = HubConnection(host = "http://10.83.22.31:17890/", port = 17890, token = "x")
        assertEquals("http://10.83.22.31:17890", connection.baseUrl())
    }

    @Test
    fun miniBaseUrlStripsPastedPort() {
        val connection = MiniConnection(host = "10.83.22.121:17891", port = 17891)
        assertEquals("http://10.83.22.121:17891", connection.baseUrl())
    }

    @Test
    fun defaultHubHostIsMini() {
        assertEquals(MiniConnection.DEFAULT_HOST, HubConnection.DEFAULT_HOST)
        val hub = HubConnection(host = HubConnection.DEFAULT_HOST, token = "x")
        assertTrue(hub.isMiniHost())
    }

}
