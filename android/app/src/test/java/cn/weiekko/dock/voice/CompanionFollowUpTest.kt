package cn.weiekko.dock.voice

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CompanionFollowUpTest {
    @Test
    fun asksWhenQuestionMark() {
        assertTrue(CompanionFollowUp.asks("还开着吗？"))
        assertTrue(CompanionFollowUp.asks("还开着吗?"))
        assertFalse(CompanionFollowUp.asks("好。"))
        assertFalse(CompanionFollowUp.asks("  "))
    }
}
