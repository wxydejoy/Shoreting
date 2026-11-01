package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EchoGuardTest {
    @Test
    fun outsideReadingNothingIsEcho() {
        // 平时她说一句，你回一句长的也是在跟她说话——不许拿字数当回声
        assertFalse(EchoGuard.isEcho(reading = false, text = "帮我看看明天北京的天气怎么样有没有雨要不要带伞"))
    }

    @Test
    fun shortBargeInDuringReadingStillGoesThrough() {
        assertFalse(EchoGuard.isEcho(reading = true, text = "停一下"))
        assertFalse(EchoGuard.isEcho(reading = true, text = "别念了，睡觉"))
    }

    @Test
    fun longNovelSentenceDuringReadingIsEcho() {
        // 2026-11-01 12:20 那批「用户输入」原文之一，71 个字
        val echo = "而瞬间杀了竟然只考了第四名在他们心里自己的学识功底你以为人家能把顶级镜头刷成白色是为了装逼"
        assertTrue(EchoGuard.isEcho(reading = true, text = echo))
    }

    @Test
    fun thresholdCountsVisibleCharsOnly() {
        val thirteen = "一二三四五六七八九十一二三"
        assertEquals(13, EchoGuard.spokenChars(thirteen))
        assertFalse(EchoGuard.isEcho(reading = true, text = "  $thirteen "))
        assertTrue(EchoGuard.isEcho(reading = true, text = "  ${thirteen}四 "))
    }

    @Test
    fun emptyTranscriptIsNotEcho() {
        assertFalse(EchoGuard.isEcho(reading = true, text = ""))
        assertFalse(EchoGuard.isEcho(reading = true, text = "   "))
    }
}
