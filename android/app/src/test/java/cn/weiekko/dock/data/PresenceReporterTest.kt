package cn.weiekko.dock.data

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 在场上报（F25）：边沿才发请求、离家满 10 分钟才补 away、Hub 挂了不算离家。
 * 时间手动拨，发送用记账 lambda。
 */
class PresenceReporterTest {
    private class Recorder {
        val sent = mutableListOf<String>()
        var allow = true

        suspend fun send(state: String): Boolean {
            if (!allow) return false
            sent.add(state)
            return true
        }
    }

    private fun reporter(recorder: Recorder): PresenceReporter = PresenceReporter(recorder::send)

    @Test
    fun `first reachable reports home once`() = runTest {
        val rec = Recorder()
        val reporter = reporter(rec)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 0)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 3_000)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 6_000)
        assertEquals(listOf("home"), rec.sent)
    }

    @Test
    fun `unreachable polls stay silent`() = runTest {
        val rec = Recorder()
        val reporter = reporter(rec)
        reporter.onPoll(onWifi = true, reachable = false, nowMs = 0)
        reporter.onPoll(onWifi = true, reachable = false, nowMs = 60_000)
        assertEquals(emptyList<String>(), rec.sent)
    }

    @Test
    fun `hub down while wifi stays on does not flush away on return`() = runTest {
        // Hub 重启两小时：Wi-Fi 一直在（每轮都在刷新时间戳），回来不补 away，
        // 只按心跳补一条 home —— Hub 那边是 unknown→home，不问候，正合适
        val rec = Recorder()
        val reporter = reporter(rec)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 0)
        assertEquals(listOf("home"), rec.sent)
        reporter.onPoll(onWifi = true, reachable = false, nowMs = 5_000)
        reporter.onPoll(onWifi = true, reachable = false, nowMs = 2 * 60 * 60_000L - 5_000)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 2 * 60 * 60_000L)
        assertEquals(listOf("home", "home"), rec.sent) // 第二条是心跳补的
    }

    @Test
    fun `heartbeat re-asserts home after fifteen minutes`() = runTest {
        val rec = Recorder()
        val reporter = reporter(rec)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 0)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 3_000)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = PresenceReporter.HEARTBEAT_MS + 1)
        assertEquals(listOf("home", "home"), rec.sent)
    }

    @Test
    fun `long away flushes away then home on return`() = runTest {
        val rec = Recorder()
        val reporter = reporter(rec)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 0)
        // 出门两小时：轮询还在跑，但 Wi-Fi 没了、Hub 也连不上
        reporter.onPoll(onWifi = false, reachable = false, nowMs = 60_000)
        reporter.onPoll(onWifi = false, reachable = false, nowMs = 2 * 60 * 60_000L)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 2 * 60 * 60_000L + 5_000)
        assertEquals(listOf("home", "away", "home"), rec.sent)
        assertEquals("home", reporter.reported)
    }

    @Test
    fun `short wifi dip under ten minutes does not greet`() = runTest {
        val rec = Recorder()
        val reporter = reporter(rec)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 0)
        // 门口取快递：Wi-Fi 断了 5 分钟
        reporter.onPoll(onWifi = false, reachable = false, nowMs = 60_000)
        reporter.onPoll(onWifi = false, reachable = false, nowMs = 5 * 60_000L)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 5 * 60_000L + 3_000)
        assertEquals(listOf("home"), rec.sent) // 只是把 home 长回来，不补 away
    }

    @Test
    fun `failed send does not mark reported`() = runTest {
        val rec = Recorder()
        rec.allow = false
        val reporter = reporter(rec)
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 0)
        assertEquals("", reporter.reported)
        // 下一轮（心跳之内）还会再试
        rec.allow = true
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 3_000)
        assertEquals(listOf("home"), rec.sent)
        assertEquals("home", reporter.reported)
    }

    @Test
    fun `reachable without wifi reports away after ten minutes on edge only`() = runTest {
        val rec = Recorder()
        val reporter = reporter(rec)
        // 先在家报过一次 home，之后走 VPN（连得上 Hub 但不走 Wi-Fi）
        reporter.onPoll(onWifi = true, reachable = true, nowMs = 0)
        reporter.onPoll(onWifi = false, reachable = true, nowMs = 60_000)
        assertEquals(listOf("home"), rec.sent) // 才断一分钟，不算
        reporter.onPoll(onWifi = false, reachable = true, nowMs = 60_000 + PresenceReporter.AWAY_AFTER_MS + 1_000)
        assertEquals(listOf("home", "away"), rec.sent)
        reporter.onPoll(onWifi = false, reachable = true, nowMs = 60_000 + PresenceReporter.AWAY_AFTER_MS + 4_000)
        assertEquals(listOf("home", "away"), rec.sent) // 边沿只报一次
    }
}
