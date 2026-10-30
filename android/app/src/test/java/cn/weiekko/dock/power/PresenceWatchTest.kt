package cn.weiekko.dock.power

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PresenceWatchTest {

    private class Recorder {
        val present: MutableList<Long> = mutableListOf()
        var away = 0
    }

    private fun watch(rec: Recorder) = PresenceWatch(
        onPresent = { awayMs -> rec.present.add(awayMs) },
        onAway = { rec.away += 1 },
    )

    @Test
    fun `first frame with a person reports present without away gap`() {
        val rec = Recorder()
        val w = watch(rec)
        w.onFrame(true, 1_000)
        assertEquals(listOf(0L), rec.present)
        assertTrue(w.isPresent)
    }

    @Test
    fun `person leaving for less than five minutes does not count as away`() {
        val rec = Recorder()
        val w = watch(rec)
        w.onFrame(true, 0)
        // 门口取个快递：4 分 59 秒的每一帧都还是「在场」
        var t = 1_000L
        while (t < 4 * 60_000L + 59_000L) {
            w.onFrame(false, t)
            t += 1_000
        }
        assertEquals(0, rec.away)
        assertTrue(w.isPresent)
    }

    @Test
    fun `empty desk for five minutes reports away once`() {
        val rec = Recorder()
        val w = watch(rec)
        w.onFrame(true, 0)
        w.onFrame(false, 60_000)
        w.onFrame(false, PresenceWatch.AWAY_AFTER_MS)
        assertEquals(1, rec.away)
        assertFalse(w.isPresent)
        w.onFrame(false, PresenceWatch.AWAY_AFTER_MS + 200)
        assertEquals(1, rec.away) // 边沿只发一次
    }

    @Test
    fun `return after away carries the gap`() {
        val rec = Recorder()
        val w = watch(rec)
        w.onFrame(true, 0)
        w.onFrame(false, 1_000)
        w.onFrame(false, PresenceWatch.AWAY_AFTER_MS) // 空帧攒满 5 分钟，离座触发
        w.onFrame(true, PresenceWatch.AWAY_AFTER_MS + 500)
        // gap 从最后一次看到人（0s）起算
        assertEquals(listOf(0L, PresenceWatch.AWAY_AFTER_MS + 500), rec.present)
    }

    @Test
    fun `short sit down and leave again stays silent`() {
        val rec = Recorder()
        val w = watch(rec)
        w.onFrame(true, 0)
        w.onFrame(false, 2_000)
        w.onFrame(true, 4_000) // gap 2s < awayAfterMs：不发回座边沿
        assertEquals(listOf(0L), rec.present)
    }

    @Test
    fun `frames pausing does not fake an away`() {
        // 屏幕被锁、相机停走：没有 false 帧就不该误判离座
        val rec = Recorder()
        val w = watch(rec)
        w.onFrame(true, 0)
        w.onFrame(true, 10 * 60_000L) // 十分钟没有帧，直接一帧有人
        assertEquals(0, rec.away)
        assertTrue(w.isPresent)
    }

    @Test
    fun `noteActivity marks present without greeting edge`() {
        val rec = Recorder()
        val w = watch(rec)
        w.onFrame(true, 0)
        w.onFrame(false, 1_000)
        w.onFrame(false, PresenceWatch.AWAY_AFTER_MS)
        assertEquals(1, rec.away)
        // 人说话 / 解锁了：算在场，但不发回座边沿（她多半正在说话）
        w.noteActivity(PresenceWatch.AWAY_AFTER_MS + 1_000)
        assertTrue(w.isPresent)
        assertEquals(listOf(0L), rec.present)
        // 之后的空帧照常续走计时
        w.onFrame(false, PresenceWatch.AWAY_AFTER_MS + 2_000)
        assertEquals(1, rec.away) // 才隔 1 秒，不算走
    }

    @Test
    fun `awayFor measures the current gap`() {
        val w = watch(Recorder())
        w.onFrame(true, 0)
        w.onFrame(false, 1_000)
        w.onFrame(false, PresenceWatch.AWAY_AFTER_MS) // 离座触发后才算「离了多久」
        assertEquals(PresenceWatch.AWAY_AFTER_MS + 1_000, w.awayFor(PresenceWatch.AWAY_AFTER_MS + 1_000))
        assertEquals(0L, w.awayFor(0))
    }
}
