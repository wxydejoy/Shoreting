package cn.weiekko.dock.background

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LanAddressTest {
    @Test
    fun prefersHomeLanOverOtherIpv4() {
        assertEquals(
            "10.83.22.150",
            preferLanIpv4(listOf("172.17.0.2", "192.168.1.8", "10.83.22.150")),
        )
        assertEquals("192.168.1.8", preferLanIpv4(listOf("8.8.8.8", "192.168.1.8")))
        assertNull(preferLanIpv4(emptyList()))
    }
}
