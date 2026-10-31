package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class SnapshotIdentityTest {
    private fun pc(minute: String, percent: Double = 12.0) = PcStatus(
        online = true,
        updatedAt = "2026-10-30T09:$minute:00Z",
        cpu = PcCpu(percent = percent),
        memory = PcMemory(percent = 40.0, usedGb = 6.4, totalGb = 16.0),
    )

    private fun media(second: String, playing: Boolean = true) = MediaInfo(
        online = true,
        playing = playing,
        title = "夜航星",
        artist = "不才",
        app = "网易云音乐",
        updatedAt = "2026-10-30T09:00:$second" + "Z",
    )

    @Test
    fun pcStatusIgnoresSampleTime() {
        // 主屏每 2～3 秒拿到一份新样本：时间戳会前进，但数字没变。
        // 这种「假变化」不能算新状态，否则整棵界面跟着重组。
        assertEquals(pc("00"), pc("01"))
        assertEquals(pc("00").hashCode(), pc("01").hashCode())
    }

    @Test
    fun pcStatusStillNoticesRealChanges() {
        assertNotEquals(pc("00", percent = 12.0), pc("00", percent = 63.5))
        assertNotEquals(pc("00"), pc("00").copy(online = false))
    }

    @Test
    fun mediaInfoIgnoresSampleTime() {
        assertEquals(media("00"), media("30"))
        assertNotEquals(media("00", playing = true), media("00", playing = false))
    }

    @Test
    fun snapshotWithOnlyNewSampleTimeIsEqual() {
        val first = Snapshot(
            protocol = 1,
            hub = HubStatus(name = "study", mijia = "ok"),
            pc = pc("00"),
            media = media("00"),
            devices = listOf(HubDevice(id = "lamp", name = "台灯", type = "light", online = true)),
        )
        val second = first.copy(pc = pc("01"), media = media("01"))
        // StateFlow 用相等性去重：相等 → 不发新状态 → 不重组
        assertEquals(first, second)
        assertNotEquals(first, second.copy(pc = pc("01", percent = 80.0)))
    }
}
