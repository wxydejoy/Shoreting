package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Test

class HubDeviceReadingTest {
    @Test
    fun knownTypesMapAndSensorStaysUnknown() {
        assertEquals(
            DeviceType.Light,
            HubDevice(id = "lamp", name = "台灯", type = "light", online = true).deviceType(),
        )
        assertEquals(
            DeviceType.Unknown,
            HubDevice(id = "desk", name = "书桌", type = "sensor", online = true).deviceType(),
        )
    }
}
