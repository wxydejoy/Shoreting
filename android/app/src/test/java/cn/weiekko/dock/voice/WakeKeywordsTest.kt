package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeKeywordsTest {
    @Test
    fun anbaoUsesInitialFinalPinyin() {
        val line = WakeKeywords.LINES.first { it.contains("@小岸") }
        assertTrue(line.startsWith("x iǎo àn"))
        assertTrue(line.contains("#0.15"))
    }

    @Test
    fun streamJoinsWithSlash() {
        assertTrue(WakeKeywords.STREAM.contains("/"))
        assertTrue(WakeKeywords.STREAM.contains("@小岸"))
        assertTrue(WakeKeywords.STREAM.contains("@嘿小岸"))
        assertTrue(!WakeKeywords.STREAM.contains("\n"))
    }

    @Test
    fun asrDisplayStripsBpeMarks() {
        assertEquals("你好 小岸", AsrModels.display("▁你好▁小岸"))
        assertEquals("把灯打开", AsrModels.display("  把灯打开  "))
    }
}
