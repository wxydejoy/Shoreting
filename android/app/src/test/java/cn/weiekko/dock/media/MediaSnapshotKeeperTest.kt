package cn.weiekko.dock.media

import cn.weiekko.dock.data.MediaInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertSame
import org.junit.Test

class MediaSnapshotKeeperTest {
    private fun info(
        playing: Boolean = false,
        title: String? = "歌",
        at: String = "2026-10-30T09:00:00Z",
    ) = MediaInfo(online = true, playing = playing, title = title, app = "网易云", updatedAt = at)

    @Test
    fun repeatsTheSameInstanceSoStateFlowStaysQuiet() {
        val keeper = MediaSnapshotKeeper()
        val first = keeper.keep(info(at = "1"))
        // 内容一样、只有时间戳变了：必须原样返回上一份，否则每秒一次重组
        val again = keeper.keep(info(at = "2"))
        assertSame(first, again)
        assertEquals("1", again.updatedAt)
    }

    @Test
    fun passesThroughRealChanges() {
        val keeper = MediaSnapshotKeeper()
        keeper.keep(info(playing = false, at = "1"))
        val playing = keeper.keep(info(playing = true, at = "2"))
        assertEquals("2", playing.updatedAt)
        assertEquals(true, playing.playing)
    }

    @Test
    fun treatsTrackSwitchAsChange() {
        val keeper = MediaSnapshotKeeper()
        keeper.keep(info(title = "第一首", at = "1"))
        val next = keeper.keep(info(title = "第二首", at = "2"))
        assertNotEquals("1", next.updatedAt)
        assertEquals("第二首", next.title)
    }

    @Test
    fun clearingThePlayerIsAChange() {
        val keeper = MediaSnapshotKeeper()
        keeper.keep(info(title = "歌", at = "1"))
        val idle = keeper.keep(info(title = null, at = "2"))
        assertEquals("2", idle.updatedAt)
        assertEquals(null, idle.title)
    }
}
