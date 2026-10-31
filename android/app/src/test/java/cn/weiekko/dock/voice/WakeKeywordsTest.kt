package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeKeywordsTest {
    @Test
    fun anbaoUsesInitialFinalPinyin() {
        val line = WakeKeywords.LINES.first { it.contains("@岸宝") }
        assertTrue(line.startsWith("àn b ǎo"))
        assertTrue(line.contains("#0.12"))
    }

    @Test
    fun everyLineHasScoreThresholdAndLabel() {
        // sherpa 的行格式：音节 :boost #threshold @显示名。少一项整行被忽略，
        // 而且不会报错——表现就是「喊了没反应」。
        WakeKeywords.LINES.forEach { line ->
            assertTrue(line, line.contains(":"))
            assertTrue(line, line.contains("#"))
            assertTrue(line, line.contains("@"))
            assertTrue(line, line.endsWith("宝"))
        }
    }

    @Test
    fun streamJoinsWithSlash() {
        assertTrue(WakeKeywords.STREAM.contains("/"))
        assertTrue(WakeKeywords.STREAM.contains("@岸宝"))
        assertTrue(WakeKeywords.STREAM.contains("@嘿岸宝"))
        assertTrue(!WakeKeywords.STREAM.contains("\n"))
    }

    @Test
    fun asrDisplayStripsBpeMarks() {
        assertEquals("你好 岸宝", AsrModels.display("▁你好▁岸宝"))
        assertEquals("把灯打开", AsrModels.display("  把灯打开  "))
    }
}
