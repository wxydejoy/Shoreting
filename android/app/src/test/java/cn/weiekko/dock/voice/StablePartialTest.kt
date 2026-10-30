package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class StablePartialTest {
    @Test
    fun firstTextGoesOutRightAway() {
        val partial = StablePartial()
        assertEquals("开灯", partial.offer("开灯", 100L))
    }

    @Test
    fun identicalTextIsNotSentTwice() {
        val partial = StablePartial()
        partial.offer("开灯", 100L)
        assertNull(partial.offer("开灯", 900L))
    }

    @Test
    fun shrinkingTailIsHeldBack() {
        val partial = StablePartial()
        partial.offer("现在几点了", 0L)
        // 这一拍把尾巴改短了：先别让字幕缩回去
        assertNull(partial.offer("现在几点", 100L))
        assertEquals("现在几点了呀", partial.offer("现在几点了呀", 200L))
    }

    @Test
    fun oneMoreCharWaitsForTheGap() {
        val partial = StablePartial(minGapMs = 110L)
        partial.offer("开", 0L)
        assertNull(partial.offer("开灯", 50L))
        assertEquals("开灯", partial.offer("开灯", 120L))
    }

    @Test
    fun twoMoreCharsPassTheThrottle() {
        val partial = StablePartial(minGapMs = 110L)
        partial.offer("开", 0L)
        assertEquals("开台灯", partial.offer("开台灯", 40L))
    }

    @Test
    fun resetStartsAFreshUtterance() {
        val partial = StablePartial()
        partial.offer("关掉台灯", 0L)
        partial.reset()
        assertEquals("开", partial.offer("开", 10L))
    }
}
