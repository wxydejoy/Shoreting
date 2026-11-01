package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationWindowTest {
    private val window = ConversationWindow()

    @Test
    fun questionOpensFifteenSecondsAfterSheFinishes() {
        // 五个字的回复：说完要 5*260+1500 = 2800ms
        val wait = window.onReply("还开着吗？", nowMs = 1_000)
        assertEquals(2_800L, wait)
        assertFalse(window.listening(1_000 + 2_800L - 1)) // 她还在说
        assertTrue(window.listening(1_000 + 2_800L))
        assertTrue(window.listening(1_000 + 2_800L + 15_000L - 1))
        assertFalse(window.listening(1_000 + 2_800L + 15_000L))
    }

    @Test
    fun statementWindowIsShorter() {
        window.onReply("好。", nowMs = 0)
        val wait = ConversationWindow.speakMs(2)
        assertTrue(window.listening(wait))
        assertFalse(window.listening(wait + ConversationWindow.STATEMENT_MS))
    }

    @Test
    fun twoMissesCloseTheWindow() {
        window.onReply("灯都关了。", nowMs = 0)
        val wait = ConversationWindow.speakMs(5)
        window.onMiss(wait + 1_000)
        assertTrue(window.listening(wait + 1_000)) // 第一轮没听清：再给一次机会
        window.onMiss(wait + 4_000)
        assertFalse(window.listening(wait + 4_000)) // 连着两轮：关
    }

    @Test
    fun inputResetsMissStreak() {
        window.onReply("还开着吗？", nowMs = 0)
        val wait = ConversationWindow.speakMs(5)
        window.onMiss(wait + 1_000)
        window.onInput(wait + 2_000)
        window.onMiss(wait + 5_000)
        assertTrue(window.listening(wait + 5_000))
    }

    @Test
    fun replyResetsStreakToo() {
        window.onReply("好。", nowMs = 0)
        val first = ConversationWindow.speakMs(2)
        window.onMiss(first + 1_000)
        window.onReply("还开着吗？", nowMs = first + 2_000)
        val second = first + 2_000 + ConversationWindow.speakMs(5)
        window.onMiss(second + 1_000)
        assertTrue(window.listening(second + 1_000))
    }

    @Test
    fun missOutsideWindowDoesNotMatter() {
        window.onReply("好。", nowMs = 0)
        window.close()
        window.onMiss(999_999)
        assertFalse(window.listening(999_999))
    }

    @Test
    fun closeWins() {
        window.onReply("还开着吗？", nowMs = 0)
        window.close()
        assertFalse(window.listening(Long.MAX_VALUE / 2))
        assertEquals(0L, window.listeningUntil())
    }

    @Test
    fun speakMsCapsAtTwentySeconds() {
        assertEquals(1_500L, ConversationWindow.speakMs(0))
        assertEquals(2_800L, ConversationWindow.speakMs(5))
        assertEquals(20_000L, ConversationWindow.speakMs(200))
    }

    @Test
    fun hubPolicyOverridesLocalHeuristic() {
        // 大脑裁决版：等多久、开多久直接照办，不再看问句正则
        val wait = window.onReplyFor(waitMs = 3_300L, openMs = 20_000L, nowMs = 1_000)
        assertEquals(3_300L, wait)
        assertFalse(window.listening(1_000 + 3_300L - 1))
        assertTrue(window.listening(1_000 + 3_300L))
        assertTrue(window.listening(1_000 + 3_300L + 20_000L - 1))
        assertFalse(window.listening(1_000 + 3_300L + 20_000L))
    }

    @Test
    fun hubPolicyShortStatementWindow() {
        window.onReplyFor(waitMs = 2_000L, openMs = 8_000L, nowMs = 0)
        assertTrue(window.listening(2_000L))
        assertFalse(window.listening(2_000L + 8_000L))
    }
}

class ConversationBusTest {
    @Test
    fun bypassOnlyWhileOpenAndNotSuppressed() {
        ConversationBus.close()
        ConversationBus.suppressUntil(5_000)
        ConversationBus.openUntil(20_000)
        assertFalse(ConversationBus.bypass(4_999)) // 她还在说话
        assertTrue(ConversationBus.bypass(5_000))
        assertTrue(ConversationBus.bypass(19_999))
        assertFalse(ConversationBus.bypass(20_000))
    }

    @Test
    fun suppressDoesNotShortenWindow() {
        ConversationBus.close()
        ConversationBus.openUntil(10_000)
        ConversationBus.suppressUntil(3_000)
        assertTrue(ConversationBus.bypass(3_000))
        assertTrue(ConversationBus.bypass(9_999))
    }

    @Test
    fun laterOpenExtends() {
        ConversationBus.close()
        ConversationBus.openUntil(10_000)
        ConversationBus.openUntil(15_000)
        assertTrue(ConversationBus.bypass(14_999))
        ConversationBus.openUntil(5_000) // 过期的开窗不往回缩
        assertTrue(ConversationBus.bypass(14_999))
    }

    @Test
    fun closeClearsEverything() {
        ConversationBus.openUntil(99_000)
        ConversationBus.suppressUntil(99_000)
        ConversationBus.close()
        assertFalse(ConversationBus.bypass(50_000))
    }

    @Test
    fun closedByDefault() {
        ConversationBus.close()
        assertFalse(ConversationBus.bypass(0))
    }

    @Test
    fun speakingCoversHerPlaybackOnly() {
        ConversationBus.close()
        ConversationBus.suppressUntil(6_000)
        ConversationBus.openUntil(30_000)
        assertTrue(ConversationBus.speaking(5_999))
        assertFalse(ConversationBus.speaking(6_000)) // 说完了，之后是窗口不是插话时段
        ConversationBus.close()
        assertFalse(ConversationBus.speaking(1_000)) // 关了就都不算
    }

    @Test
    fun storySuppressesUntilHubStopsReporting() {
        // B2：朗读期间每次快照 speaking=true 都续一段保活窗（长轮询约 25s 一圈，
        // 保活 30s），全程 kws 都不该把她自己的声音当唤醒词；停更之后窗自然过期。
        ConversationBus.close()
        var now = 0L
        repeat(3) {
            now += 25_800L // 长轮询一圈回到快照
            ConversationBus.suppressUntil(now + 30_000)
            assertTrue(ConversationBus.speaking(now + 1))
        }
        // 朗读停了：不再续窗，最后一段到点就放行
        assertFalse(ConversationBus.speaking(now + 30_000))
        ConversationBus.close()
    }
}
