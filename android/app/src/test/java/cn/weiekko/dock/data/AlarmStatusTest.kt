package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AlarmStatusTest {
    @Test
    fun progressGoesUpAsSecondsRunOut() {
        val start = AlarmStatus(secondsLeft = 900.0, totalSec = 900.0)
        assertEquals(0f, start.progress!!, 0.001f)
        val mid = AlarmStatus(secondsLeft = 450.0, totalSec = 900.0)
        assertEquals(0.5f, mid.progress!!, 0.001f)
        val end = AlarmStatus(secondsLeft = 1.0, totalSec = 900.0)
        assertEquals(1f - 1f / 900f, end.progress!!, 0.001f)
    }

    @Test
    fun badDataYieldsNull() {
        assertNull(AlarmStatus(secondsLeft = 0.0, totalSec = 0.0).progress)
        assertNull(AlarmStatus(secondsLeft = 10.0, totalSec = -1.0).progress)
    }
}
